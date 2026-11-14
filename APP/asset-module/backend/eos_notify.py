"""Q95 EOS 到期自動通知（硬體／作業系統）。

仿 vuln_notify.py（B-19 弱點到期寄信）的整套做法，只換「資料來源」「門檻」「收件人欄位」：

  * 門檻只有兩級：**180 天（半年）／365 天（一年）**，存設定、畫面可改（預設值只是設定沒填時的退路）
  * 資料來源：eos.py 的官方 EOS 對照（os_eos.json／hardware_eos.json），分兩軌各算「剩幾天」
      - 硬體軌：hardware.device_model → normalize_model → eos.lookup_hardware_eos
      - 作業系統軌：hardware.os → normalize_os（**canonical_os 不是欄位，是這裡算出來的**）→ eos.lookup_os_eos
  * 收件人：硬體 EOS → hardware.owner（＝SP）；OS EOS → software.owner（＝AP，同一台掛的每一筆軟體登記）。
    名字一律查 B-23 匯入的 ad_person 轉成信箱（沿用 vuln_notify.resolve_recipient）
  * SMTP 主機／埠／寄件者**沿用 vuln_notify 那一份設定**（跟每日摘要信同一個做法，不另填第二份真相）

設計上最重要的幾件（跟弱點通知同一組，做錯會比沒有更糟）：

1. 門檻只決定「這一筆什麼時候第一次出現」，不當觸發條件。每筆記 last_bucket，跨進更急的一級才再出現。
2. 只有寄成功才更新 last_bucket。寧可重複寄，也不可以假裝寄過。
3. 找不到收件人（沒填、AD 查無此人、同名疑慮、帳號停用、沒信箱、軟體表沒登記）一律進「沒人收」那一籃，
   畫面看得到；不可以靜靜跳過。同名疑慮不可以亂猜寄給其中一個。
4. 本期 0 筆也寄一封（讓人知道系統還活著）。
5. 預覽（dry_run）永遠不寄、不寫紀錄、不動 last_bucket。

排程：每月 1 號（EOS 是以年計的事，每週寄只會變噪音）；時間點存設定；預設關閉。

不納入的（誠實講）：查不到官方 EOS 日期的項目——那是「不知道」不是「沒問題」，信的結尾會寫出來。
退役（停用／報廢／閒置）的資產備查不通知。虛擬機型號（VMware 之類）沒有硬體 EOS 可查，只走 OS 軌。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

import vuln_notify as vn

#: 「今天」一律是 Asia/Taipei（固定 +8，無日光節約），不看伺服器時區；通知引擎與追蹤頁共用這一份。
TPE = timezone(timedelta(hours=8))


def now_tpe(now_utc: datetime | None = None) -> datetime:
    base = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(TPE).replace(tzinfo=None)


def today_tpe(now_utc: datetime | None = None) -> date:
    return now_tpe(now_utc).date()

#: 兩級門檻（天）。**只是設定沒填時的退路**；實際值存 app_settings、畫面可改。
DEFAULT_BUCKETS = (180, 365)

SETTING_KEY = "eos_notify"
TRIAL_MAX = 30          # 試寄最多幾封（全部改寄給試寄信箱，不寄給真正的收件人）

TRACK_HW = "hw"
TRACK_OS = "os"
TRACK_LABEL = {TRACK_HW: "硬體", TRACK_OS: "作業系統"}
OWNER_LABEL = {TRACK_HW: "SP", TRACK_OS: "AP"}

# 找不到收件人的原因（AD 那幾種沿用 vuln_notify 的文字，這裡是 EOS 自己多出來的）
NO_OWNER_HW = "資產登記沒有填擁有者（SP）"
NO_OWNER_SW = "軟體登記有這台但沒有填擁有者（AP）"
NO_SOFTWARE = "軟體登記裡沒有這台機器（查不到 AP 負責人）"
OUT_OF_SCOPE = "不在通知範圍內（單位／人選設定）"

SettingsCorrupt = vn.SettingsCorrupt

STATE_SQL = """
CREATE TABLE IF NOT EXISTS eos_notify_state (
    state_key    TEXT PRIMARY KEY,     -- 軌別|machine_key|負責人名字|項目（OS／型號）
    last_bucket  INTEGER,              -- 上次通知用的門檻（180/365），已過 EOS 用 0
    last_sent_at TEXT,
    last_recipient TEXT                -- 上次實際寄給的信箱（收件地址變更後，舊地址的成功不能算新地址已通知）
)
"""

LOG_SQL = """
CREATE TABLE IF NOT EXISTS eos_notify_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at     TEXT NOT NULL,
    schedule    TEXT,
    recipient   TEXT,
    cc          TEXT,
    owner_name  TEXT,
    subject     TEXT,
    item_count  INTEGER,
    hw_count    INTEGER,
    os_count    INTEGER,
    status      TEXT,                  -- sent / failed
    detail      TEXT,
    smtp_host   TEXT,
    mail_from   TEXT
)
"""

RUN_SQL = """
CREATE TABLE IF NOT EXISTS eos_notify_run (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date   TEXT NOT NULL,
    schedule   TEXT NOT NULL,          -- monthly / manual
    started_at TEXT NOT NULL,
    config_ok  INTEGER,                -- 「跑得起來」，不是「寄到了」，要看 failed
    sent       INTEGER,
    failed     INTEGER,
    reason     TEXT
)
"""


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(STATE_SQL)
    if not any(r[1] == "last_recipient" for r in conn.execute("PRAGMA table_info(eos_notify_state)")):
        conn.execute("ALTER TABLE eos_notify_state ADD COLUMN last_recipient TEXT")      # 舊庫補欄位
    conn.execute(LOG_SQL)
    conn.execute(RUN_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eoslog_at ON eos_notify_log(sent_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eosrun_date ON eos_notify_run(run_date, schedule)")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 設定 ───────────────────────────────────────────────────────────────
def load_settings(conn: sqlite3.Connection) -> dict:
    """EOS 通知自己的設定（開關、時間、門檻、代收信箱…）。沒設定＝空；壞掉要丟 SettingsCorrupt，不吞。"""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    if not row:
        return {}
    raw = row["value"] if hasattr(row, "keys") else row[0]
    try:
        return json.loads(raw) or {}
    except (ValueError, TypeError) as exc:
        raise SettingsCorrupt(
            f"EOS 通知設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}: {exc}") from exc


def save_settings(conn: sqlite3.Connection, cfg: dict) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SETTING_KEY, json.dumps(cfg, ensure_ascii=False)))
    conn.commit()


def validate_buckets(raw) -> tuple[int, ...]:
    """門檻只能是「兩個不同的正整數」。不合法就丟 ValueError（API 轉成 400，不默默改成別的值）。"""
    try:
        vals = sorted({int(x) for x in (raw or [])})
    except (ValueError, TypeError) as exc:
        raise ValueError("門檻必須是整數天數") from exc
    if len(vals) != 2 or vals[0] <= 0:
        raise ValueError("門檻要剛好兩級（例如 180 與 365 天），而且都要大於 0")
    return tuple(vals)


def buckets_of(cfg: dict) -> tuple[int, ...]:
    try:
        return validate_buckets(cfg.get("buckets") or DEFAULT_BUCKETS)
    except ValueError:
        return DEFAULT_BUCKETS


def smtp_settings(conn: sqlite3.Connection) -> dict:
    """SMTP 主機／埠／寄件者沿用弱點通知那一份（SettingsCorrupt 往上丟）。"""
    return vn.load_settings(conn)


def mail_missing(conn: sqlite3.Connection, cfg: dict | None = None) -> list[str]:
    """寄信還缺什麼（指名）。SMTP 三項沿用弱點通知；代收信箱是 EOS 自己的。"""
    cfg = cfg if cfg is not None else load_settings(conn)
    miss = [f"SMTP：{x}" for x in vn.check_settings(smtp_settings(conn))]
    return miss


# ── 班表 ───────────────────────────────────────────────────────────────
def schedules_due(today: date) -> list[str]:
    """每月 1 號。EOS 以年計，每週寄只會變成沒人看的噪音。"""
    return ["monthly"] if today.day == 1 else []


def is_month_precision(text: str | None) -> bool:
    """官方原文只有年月（2030-04）：通知內的日期是『月初保守換算』，不能宣稱是官方精確日（Codex 39 號）。"""
    return bool(re.fullmatch(r"\d{4}-\d{2}", (text or "").strip()[:10]))


def bucket_for(days_left: int | None, buckets: tuple[int, ...]) -> int | None:
    """落在哪一級：已過 EOS＝0；剩 ≤180＝180；≤365＝365；更遠＝None（這期不提）。"""
    return vn.bucket_for(days_left, buckets)


def parse_eos_date(text: str | None) -> date | None:
    """官方公告有的只到月份（2029-07），補成月初——跟 eos.eos_status 同一個保守口徑。"""
    t = (text or "").strip()[:10]
    if not t:
        return None
    if re.fullmatch(r"\d{4}-\d{2}", t):
        t += "-01"
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


# ── 收件人 ─────────────────────────────────────────────────────────────
_SPLIT = re.compile(r"[、,，;；/／]+")


DEPT_AMBIGUOUS = "擁有者是部門名稱，但在 AD 對到不只一個部門"
DEPT_NO_HEAD = "擁有者是部門名稱，但從 AD 判不出這個部門的主管"
_UNIT_END = set("處部室組科課中心司院所局")


def dept_head(conn: sqlite3.Connection, dept: str) -> tuple[dict | None, str | None]:
    """擁有者欄填的是**部門名稱**（公司機實況 11-09：hardware.owner＝「資訊架構部」）→ 寄給該部門主管。

    使用者 11-09：「欄位沒對上……不是 Excel 要改，就是程式要改」——擁有者是部門，就要拿 AD 的「部門」欄對，
    不是拿姓名欄。部門主管從 AD 匯報線推：同部門成員裡，**主管不在本部門的那一位**＝部門的頂端。
    （不用「最多人掛的主管」——有分組時組長會贏過部主管。）頂端剛好一人才算數；0 人或多人
    （例如部主管登記在上一層單位）→ 回 DEPT_NO_HEAD 進「沒人收」，不猜。

    回 (人, 原因)。AD 完全對不到這個部門 → (None, None)，由呼叫端保留原本的「查無此人」。
    """
    d = (dept or "").strip()
    if not d:
        return None, None
    try:
        # 在 Python 比對，不用 LIKE（擁有者文字裡的 % _ 會變萬用字元）。AD 名單幾千筆，全撈沒負擔。
        allrows = conn.execute("SELECT * FROM ad_person WHERE dept_name IS NOT NULL").fetchall()
    except sqlite3.Error:
        return None, None
    # 完全相符優先：「資訊部」不該因為「證券資訊部」也結尾相符就被判成對到多個部門。
    rows = [r for r in allrows if (r["dept_name"] or "").strip() == d]
    if not rows:
        # 結尾相符只認「前一個字是單位結尾」：AD 部門常是全路徑「資訊管理處資訊架構部」→ 對得到「資訊架構部」；
        # 但「管理部」不能對到「資產管理部」（寄錯人是最壞的結果）。
        rows = [r for r in allrows
                if (n := (r["dept_name"] or "").strip()).endswith(d) and len(n) > len(d)
                and n[-len(d) - 1] in _UNIT_END]
    if not rows:
        return None, None
    if len({(r["dept_code"] or r["dept_name"] or "").strip() for r in rows}) > 1:
        return None, DEPT_AMBIGUOUS
    ids = {r["employee_id"] for r in rows}
    tops = [r for r in rows if not r["manager_id"] or r["manager_id"] not in ids]
    if len(tops) != 1:
        return None, DEPT_NO_HEAD
    p = dict(tops[0])
    if p.get("enabled") == 0:
        return None, vn.DISABLED
    if not (p.get("mail") or "").strip():
        return None, vn.NO_MAIL
    return p, None


def resolve_owner(conn: sqlite3.Connection, name: str) -> tuple[dict | None, str | None, str | None]:
    """擁有者 → 收件人。先當**人名**查（vuln_notify 同一套，同名不亂猜）；AD 查無此人時再當**部門名稱**查部門主管。
    回 (人, 不能寄的原因, 經由哪個部門——直接對到人時為 None)。"""
    person, why = vn.resolve_recipient(conn, name)
    if person is not None or why != vn.NOT_IN_AD:
        return person, why, None
    head, why2 = dept_head(conn, name)
    if head is None:
        return None, why2 or why, None
    return head, None, name.strip()


def effective_mail(conn: sqlite3.Connection, name: str | None, dept: str | None, memo: dict,
                   eid: str | None = None) -> str | None:
    """通知引擎實際會寄到的信箱（小寫；不含通知範圍設定）：人名 → 部門名的部門主管 → 這台登記部門的主管。
    追蹤頁（eos_follow）與數字核對（verify_numbers）都用這一份，才會跟寄出去的對得上。memo 由呼叫端給（同一次計算重用）。
    eid（2.059.17）：指定收件人規則的人＝用員編重查（同 collect），不用名字、不改寄部門主管。"""
    if eid:
        import eos_assign
        k = "e:" + eid
        if k not in memo:
            memo[k] = eos_assign.current_person(conn, eid)
        p = memo[k][0]
        return (p.get("mail") or "").strip().lower() if p else None
    p = None
    if name:
        k = "n:" + name
        if k not in memo:
            memo[k] = resolve_owner(conn, name)
        p = memo[k][0]
    if p is None and dept:
        k = "d:" + dept
        if k not in memo:
            memo[k] = dept_head(conn, dept)
        p = memo[k][0]
    return (p.get("mail") or "").strip().lower() if p else None


def split_owners(raw: str | None) -> list[str]:
    """一格可能填多個人（頓號／逗號／斜線分隔）；一個一個查。不拆空白（西式姓名有空白）。"""
    out, seen = [], set()
    for part in _SPLIT.split(raw or ""):
        n = part.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ── 組批次 ─────────────────────────────────────────────────────────────
def _hint(r) -> str | None:
    joined = " ".join(p for p in (r["asset_name"], r["asset_purpose"]) if p)
    return joined or None


def _note_unverified(stats: dict, base: dict, track: str, item: str, d: date, today: date) -> None:
    """W221-006：日期來源未核對（confidence=unverified）→ 不進通知，另分組記錄。"""
    stats["unverified"] += 1
    if len(stats["unverified_list"]) < 200:
        stats["unverified_list"].append({"track": track, "hostname": base.get("hostname"), "ip": base.get("ip"),
                                         "item": item, "eos_date": d.isoformat(), "days_left": (d - today).days})


def _pick_row(group, col: str):
    """該欄有值的第一列（沿用通知引擎原本的取法）。回 (列, 不同值清單)。
    同一台有多筆不同的非空值＝登記矛盾，值清單長度 > 1，由呼叫端標待核；取值仍只來自被選中的那一列，不拼接。"""
    cand = [g for g in group if (g[col] or "").strip()]
    if not cand:
        return None, []
    # 取值＝群組順序下第一個有值的列（與改版前通知引擎完全相同，不改變既有通知輸出）；矛盾時由 values 長度 > 1 標待核
    return cand[0], sorted({(g[col] or "").strip() for g in cand})


def resolve_machines(conn: sqlite3.Connection) -> list[dict]:
    """EOS 候選母體與 OS／硬體取值的**唯一一份**：通知引擎（eos_candidates）與覆蓋率（eos_evidence.coverage_rows）都呼叫它。

    母體＝非退役資產依 machine_key 分組；每台各取 OS 一列、硬體一列（同欄位第一個有值的列，見 _pick_row），
    正規化後查 EOS 表。回傳每台一個 dict：mk/group/rep/os/hw；os／hw 為 None（該欄沒填）或
    {row, raw, canonical, hit, values, conflict[, family]}。唯讀。
    """
    import eos
    import normalize
    import system_stats
    from lifecycle import retired_sql_list

    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, environment, os, device_model, asset_name, asset_purpose, "
        "asset_status, owner, inventory_department, usage_unit, api_id FROM hardware WHERE COALESCE(asset_status,'') NOT IN "
        + retired_sql_list()).fetchall()
    groups = system_stats.collapse_machines(rows)
    os_memo: dict[tuple, dict] = {}
    hw_memo: dict[tuple, dict] = {}
    out: list[dict] = []
    for mk, group in groups.items():
        rep = group[0]
        hrow, hvals = _pick_row(group, "device_model")
        hw = None
        if hrow is not None:
            mkey = (hrow["device_model"], _hint(hrow))
            if mkey not in hw_memo:
                info = normalize.normalize_model(hrow["device_model"], conn, _hint(hrow))
                fam, _v = normalize.hardware_family(info["vendor"], info["canonical"])
                hw_memo[mkey] = {"canonical": info["canonical"], "family": fam,
                                 "hit": None if fam == "虛擬化" else eos.lookup_hardware_eos(info["canonical"])}
            hw = {"row": hrow, "raw": hrow["device_model"], **hw_memo[mkey], "values": hvals, "conflict": len(hvals) > 1}
        orow, ovals = _pick_row(group, "os")
        osr = None
        if orow is not None:
            okey = (orow["os"], orow["device_model"])
            if okey not in os_memo:
                info = normalize.normalize_os(orow["os"], conn, orow["device_model"])
                os_memo[okey] = {"canonical": info["canonical"], "hit": eos.lookup_os_eos(info["canonical"])}
            osr = {"row": orow, "raw": orow["os"], **os_memo[okey], "values": ovals, "conflict": len(ovals) > 1}
        out.append({"mk": mk, "group": group, "rep": rep, "os": osr, "hw": hw})
    return out


def _machine_dept(group) -> str | None:
    """這台登記的部門：盤點部門優先，沒填用使用單位（同一台多筆取第一個有值的）。給「負責人查不到→部門主管」用。"""
    for col in ("inventory_department", "usage_unit"):
        for r in group:
            v = (r[col] or "").strip() if col in r.keys() else ""
            if v:
                return v
    return None


def eos_candidates(conn: sqlite3.Connection, today: date) -> tuple[list[dict], dict]:
    """每台（machine_key 一把尺）各軌算出「有官方 EOS 日期」的項目，還沒套門檻、還沒找收件人。

    回 (candidates, stats)。candidate: track/mk/hostname/ip/environment/item/eos_date/days_left
    和 owners（名字清單；空＝沒填／沒登記，原因放 owner_why）。母體與取值來自 resolve_machines（與覆蓋率共用）。
    """
    import eos

    resolved = resolve_machines(conn)
    sw_owner: dict[str, list[str]] = {}
    sw_present: set[str] = set()
    for r in conn.execute("SELECT asset_serial, owner FROM software"):
        sw_present.add(r["asset_serial"])
        sw_owner.setdefault(r["asset_serial"], []).extend(split_owners(r["owner"]))

    # 11-10 使用者：OS 軌的 AP 先看「業務系統對照表」的 AP 負責人（具名）。公司機實況：軟體登記的擁有者
    # 幾乎都填部門名「資訊架構部」，全部變成寄給同一位部主管（881 筆）。
    biz_ap: dict[str, list[str]] = {}
    try:
        for r in conn.execute("SELECT api_id, ap_owner FROM business_system WHERE TRIM(COALESCE(ap_owner,'')) <> ''"):
            biz_ap[(r["api_id"] or "").strip()] = split_owners(r["ap_owner"])
    except sqlite3.Error:
        biz_ap = {}
    sys_name: dict[str, str] = {}
    try:
        for r in conn.execute("SELECT api_id, name FROM business_system"):
            sys_name[(r["api_id"] or "").strip()] = (r["name"] or "").strip()
    except sqlite3.Error:
        sys_name = {}
    import os_class
    out: list[dict] = []
    stats = {"machines": len(resolved), "hw_with_date": 0, "os_with_date": 0,
             "unverified": 0, "unverified_list": []}

    for m in resolved:
        mk, group, rep = m["mk"], m["group"], m["rep"]
        api_ids: list[str] = []
        for g in group:
            a = ((g["api_id"] if "api_id" in g.keys() else None) or "").strip()
            if a and a not in api_ids:
                api_ids.append(a)
        base = {"mk": mk, "hostname": rep["hostname"], "ip": rep["ip"], "environment": rep["environment"],
                "asset_serial": rep["asset_serial"], "dept": _machine_dept(group),
                # 2.059.17 指定收件人規則用：業務系統（APID）與設備類別（os_class，用該軌取值那一列判，全站同一把尺）
                "api_ids": api_ids, "systems": [sys_name.get(a) or a for a in api_ids]}

        # ── 硬體軌：擁有者＝hardware.owner（SP）──
        if m["hw"] is not None:
            canonical, hit = m["hw"]["canonical"], m["hw"]["hit"]
            d = parse_eos_date((hit or {}).get("eos_date"))
            if d is not None and not eos.is_trusted(hit):
                _note_unverified(stats, base, TRACK_HW, canonical, d, today)
                d = None
            if d is not None:
                stats["hw_with_date"] += 1
                owners: list[str] = []
                for g in group:
                    for n in split_owners(g["owner"]):
                        if n not in owners:
                            owners.append(n)
                out.append({**base, "track": TRACK_HW, "devclass": os_class.classify_row(m["hw"]["row"]),
                            "item": canonical, "eos_date": d.isoformat(),
                            "eos_date_text": (hit or {}).get("eos_date"),
                            "date_precision": "month" if is_month_precision((hit or {}).get("eos_date")) else "day",
                            "days_left": (d - today).days, "owners": owners, "owner_why": NO_OWNER_HW,
                            "extendable": bool((hit or {}).get("extendable"))})

        # ── 作業系統軌：AP＝業務系統對照表的 AP 負責人，沒有才用 software.owner（這台掛的每一筆軟體登記）──
        if m["os"] is not None:
            canonical, hit = m["os"]["canonical"], m["os"]["hit"]
            d = parse_eos_date((hit or {}).get("eos_date"))
            if d is not None and not eos.is_trusted(hit):
                _note_unverified(stats, base, TRACK_OS, canonical, d, today)
                d = None
            if d is not None:
                stats["os_with_date"] += 1
                owners = []
                # ① 業務系統對照表的 AP 負責人（這台掛的每個 APID；一台多系統＝每個系統的 AP 都收）
                for g in group:
                    for n in biz_ap.get(((g["api_id"] if "api_id" in g.keys() else None) or "").strip(), []):
                        if n not in owners:
                            owners.append(n)
                ap_source = "業務系統" if owners else None
                # ② 沒有才用軟體登記的擁有者
                if not owners:
                    for g in group:
                        for n in sw_owner.get(g["asset_serial"], []):
                            if n not in owners:
                                owners.append(n)
                    ap_source = "軟體登記" if owners else None
                has_sw = any(g["asset_serial"] in sw_present for g in group)
                # 11-09：查不到 AP（軟體登記沒有這台／沒填擁有者）→ 改寄這台硬體的擁有者（SP），不讓整筆掉進沒人收。
                # 公司機實況：「沒人收」大宗就是這種（Windows Server 2016 一整批）。信裡會註明是代收。
                ap_fallback = False
                if not owners:
                    for g in group:
                        for n in split_owners(g["owner"]):
                            if n not in owners:
                                owners.append(n)
                    ap_fallback = bool(owners)
                out.append({**base, "track": TRACK_OS, "devclass": os_class.classify_row(m["os"]["row"]),
                            "item": canonical, "eos_date": d.isoformat(),
                            "ap_fallback": ap_fallback, "ap_source": ap_source,
                            "eos_date_text": (hit or {}).get("eos_date"),
                            "date_precision": "month" if is_month_precision((hit or {}).get("eos_date")) else "day",
                            "days_left": (d - today).days, "owners": owners,
                            "owner_why": NO_OWNER_SW if has_sw else NO_SOFTWARE,
                            "extendable": bool((hit or {}).get("extendable"))})
    # 2.059.17 指定收件人規則（主機＞業務系統＞設備類別，eos_assign.Matcher）：命中的那筆 owners 換成規則指定的人，
    # 原本推算的放 inferred_owners（畫面「原本會寄給誰」）。在這裡套＝通知、追蹤（eos_follow）、核數（verify_numbers）同一份。
    import eos_assign
    matcher = eos_assign.Matcher(eos_assign.load_rules(conn))
    for c in out:
        mt = matcher.match(c)
        if mt is None:
            continue
        people, hits = mt
        # 審查 P3：兩個人顯示名相同（員編不同）時名字加員編區分，不可以合併成一個人（c["assign"] 以名字為鍵）
        dup = {p["name"] for p in people if sum(1 for x in people if x["name"] == p["name"]) > 1}
        named = [(f"{p['name']}（{p['employee_id']}）" if p["name"] in dup else p["name"], p["employee_id"]) for p in people]
        c["inferred_owners"] = c["owners"]
        c["owners"] = [n for n, _e in named]
        c["assign"] = dict(named)
        c["assign_text"] = eos_assign.rule_text(hits)
        c["assign_rule_ids"] = [r["id"] for r in hits]
    return out, stats


def collect(conn: sqlite3.Connection, today: date | None = None, schedule: str = "monthly",
            ignore_state: bool = False, now_utc: datetime | None = None) -> dict:
    """挑出這一期要提的項目，分成「有收件人」與「沒人收」。

    ignore_state=True：畫面上看「現在到底有哪些沒人收／會寄給誰」用，不看寄過沒有。
    """
    _ensure(conn)
    today = today or today_tpe(now_utc)
    cfg = load_settings(conn)
    buckets = buckets_of(cfg)
    state = {r["state_key"]: (r["last_bucket"], (r["last_recipient"] if "last_recipient" in r.keys() else None))
             for r in conn.execute("SELECT * FROM eos_notify_state")}

    cands, stats = eos_candidates(conn, today)
    import eos_assign
    by_person: dict[str, dict] = {}
    orphans: list[dict] = []
    memo: dict[str, tuple[dict | None, str | None]] = {}
    in_window = 0

    for c in cands:
        b = bucket_for(c["days_left"], buckets)
        if b is None:
            continue                                   # 還很遠，這期不用提
        in_window += 1
        # 2.059.17：指定收件人規則（主機＞業務系統＞設備類別）命中 → 只寄規則指定的人，不走推算、不改寄部門主管；
        # 指定的人寄的當下已停用／不在 AD／沒信箱 → 進沒人收（原因寫明）。不套「通知範圍」：那是推算用的篩選，
        # 管理員明確指定的人不該被它默默擋掉。
        # 規則在 eos_candidates 就套好（owners＝規則指定的人，追蹤／核數跟這裡同一份）；這裡用員編重查當下狀態
        assigned: dict[str, tuple] = {}
        for nm, eid in (c.get("assign") or {}).items():
            assigned[nm] = eos_assign.current_person(conn, eid) + (c.get("assign_text"),)
        names = c["owners"] or [None]
        for name in names:
            key = f"{c['track']}|{c['mk']}|{name or ''}|{c['item']}"
            prev = state.get(key)
            assign_text = None
            via_dept = None
            if name in assigned:
                person, why, assign_text = assigned[name]
            elif name is None:
                person, why = None, c["owner_why"]
            else:
                if name not in memo:
                    memo[name] = resolve_owner(conn, name)
                person, why, via_dept = memo[name]
                if person is not None and not vn.in_scope(cfg, person):
                    person, why = None, OUT_OF_SCOPE
            # 11-09 使用者：負責人（SP／AP）都查不到 →「改寄這台登記部門的主管」，主管也判不出來才進沒人收（寄代收）。
            # 不在通知範圍內的不改寄（那是使用者刻意排除的）。部門主管推算規則同 dept_head（判不出不猜）。
            fb_dept = fb_why = None
            if person is None and assign_text is None and why != OUT_OF_SCOPE and c.get("dept"):
                dk = "dept:" + c["dept"]
                if dk not in memo:
                    memo[dk] = dept_head(conn, c["dept"]) + (None,)
                head = memo[dk][0]
                if head is not None and vn.in_scope(cfg, head):
                    person, fb_dept, fb_why = head, c["dept"], why
            if not ignore_state and prev is not None and b >= prev[0]:
                # 同一級不重複提；跨進更急的才再出現。但收件地址換了：舊地址的成功不能算新地址已通知
                # （舊資料 last_recipient 為 NULL＝相容，不因此重寄）。收件人現在解析不出來（查無此人／無信箱）、
                # 而 state 記著舊地址：不沿用舊成功，改進「沒人收」讓人核對。
                resolved0 = (assigned[name][0] if name in assigned
                             else memo[name][0] if name is not None else None)
                if person is not None:
                    moved = (prev[1] is not None
                             and (person.get("mail") or "").strip().lower() != str(prev[1]).strip().lower())
                else:
                    moved = bool(name is not None and resolved0 is None and prev[1] is not None)
                if not moved:
                    continue
            it = {**c, "_bucket": b, "_key": key, "_owner": name or "",
                  "_via_dept": via_dept,
                  "_fb_dept": fb_dept, "_fb_why": fb_why if fb_dept else None, "_assign": assign_text}
            if person is None:
                it["_why"] = why
                orphans.append(it)
            else:
                grp = by_person.setdefault(person["employee_id"], {"person": person, "items": []})
                grp["items"].append(it)

    stats["in_window"] = in_window
    return {"ready": True, "by_owner": by_person, "orphans": orphans, "buckets": buckets,
            "schedule": schedule, "stats": stats, "today": today.isoformat()}


# ── 信的內容 ───────────────────────────────────────────────────────────
def _subject(cfg: dict, name: str, n: int, today: date) -> str:
    prefix = (cfg.get("subject_prefix") or "").strip()
    return f"{prefix}[EOS 到期提醒] {name} 共 {n} 筆（{today:%Y-%m-%d}）"


def _tier_label(b: int, buckets: tuple[int, ...]) -> str:
    if b == 0:
        return "已過 EOS"
    return f"{b} 天內到期"


def _footer(stats: dict | None) -> list[str]:
    out = ["", "─" * 30]
    if stats:
        out.append(f"本期評估 {stats.get('machines', 0)} 台；查得到官方 EOS 日期的：硬體 "
                   f"{stats.get('hw_with_date', 0)} 台、作業系統 {stats.get('os_with_date', 0)} 台。")
    # 可信度語意與畫面／Excel／ICS 一致（eos.TRUST_LABEL）：信內只列「已確認」；「推定」與「未知」不列
    out.append("可信度：本信所列日期皆為「已確認」（官方頁面已核對）。")
    if stats and stats.get("unverified"):
        out.append(f"另有 {stats['unverified']} 筆日期來源尚未核對（推定，分組標示「來源未核對」），未列入通知。")
    out.append("查不到官方 EOS 日期的項目不在這封信裡——那是「不知道」（未知），不代表沒問題。")
    return out


def _date_phrase(it: dict) -> str:
    """日精度：直接寫日期；月精度：寫官方原文年月，並明講換算日只是內部月初保守口徑（不是官方精確日）。"""
    if it.get("date_precision") == "month":
        return f"{(it.get('eos_date_text') or '')[:7]}（官方只公告到月份；提醒以月初 {it['eos_date']} 保守換算）"
    return str(it["eos_date"])


def _item_line(it: dict) -> str:
    left = it["days_left"]
    when = f"已過 EOS {-left} 天" if left < 0 else f"剩 {left} 天"
    return (f"· [{TRACK_LABEL[it['track']]}] {it.get('hostname') or '(未知主機)'}"
            f"｜{it.get('ip') or '—'}｜官方公告 {it['item']} 於 {_date_phrase(it)} 結束（{when}）{'；官方另有付費延伸方案，本資產是否持有尚未登記（權益待確認）' if it.get('extendable') else ''}"
            f"{'；擁有者登記為部門「' + it['_via_dept'] + '」，寄給部門主管' if it.get('_via_dept') else ''}"
            f"{'；軟體登記查不到 AP，改寄這台的擁有者（SP）代收' if it.get('ap_fallback') else ''}"
            f"{'；負責人查不到（' + str(it.get('_fb_why') or '') + '），改寄這台登記部門「' + it['_fb_dept'] + '」的主管' if it.get('_fb_dept') else ''}"
            f"{'；指定：' + it['_assign'] if it.get('_assign') else ''}")


def _body(items: list[dict], buckets: tuple[int, ...], today: date, stats: dict | None) -> str:
    lines = [f"官方公告以下硬體或作業系統即將結束（EOS）或已結束支援，請確認維護權益並評估是否升級（{today:%Y-%m-%d}）：", ""]
    for b in sorted({it["_bucket"] for it in items}):
        grp = [it for it in items if it["_bucket"] == b]
        lines.append(f"【{_tier_label(b, buckets)}】{len(grp)} 筆")
        shown: set[tuple] = set()
        for it in sorted(grp, key=lambda x: (x["days_left"], x["track"], x.get("hostname") or "")):
            k = (it["track"], it["mk"], it["item"])
            if k in shown:          # 兩個負責人都改寄同一位主管：同一筆只列一次
                continue
            shown.add(k)
            lines.append(_item_line(it))
        lines.append("")
    lines += ["—— 這封信由資訊戰情室自動寄出。硬體寄給資產登記的擁有者（SP），"
              "作業系統寄給軟體登記的擁有者（AP）；EOS 日期取自官方公告。"]
    lines += _footer(stats)
    return "\n".join(lines)


def _zero_body(today: date, buckets: tuple[int, ...], stats: dict | None) -> str:
    lines = [f"EOS 到期提醒（{today:%Y-%m-%d}）：本期 0 筆。", "",
             f"這一期沒有任何項目落在 {buckets[0]} 天／{buckets[1]} 天的提醒門檻內，也沒有新跨進更急一級的。",
             "這封信是刻意寄的——沒有信不代表沒事，也可能是排程沒跑起來，",
             "所以就算 0 筆也會寄一封，讓你知道系統這個月有跑。"]
    lines += _footer(stats)
    return "\n".join(lines)


def _orphan_body(orphans: list[dict], today: date, stats: dict | None) -> str:
    lines = [f"以下 {len(orphans)} 筆 EOS 項目找不到收件人，需要有人指派：", ""]
    by_why: dict[str, list] = {}
    for o in orphans:
        by_why.setdefault(o["_why"], []).append(o)
    for why, its in by_why.items():
        lines.append(f"【{why}】{len(its)} 筆")
        for it in its[:20]:
            lines.append(f"  {_item_line(it)}｜{OWNER_LABEL[it['track']]}「{it['_owner'] or '(空白)'}」")
        if len(its) > 20:
            lines.append(f"  …其餘 {len(its) - 20} 筆請到系統查看")
        lines.append("")
    lines += _footer(stats)
    return "\n".join(lines)


# ── 寄送 ───────────────────────────────────────────────────────────────
def _send_smtp(cfg: dict, to: str, cc: list[str], subject: str, body: str) -> tuple[bool, str]:
    """實際寄一封：直接用弱點通知那支（免認證內部 relay、部分收件人被退＝失敗）。測試換掉這個。"""
    return vn._send_smtp(cfg, to, cc, subject, body)


def _log(conn, sched, to, cc, owner_name, subject, n, hw, osn, ok, detail, smtp) -> None:
    conn.execute(
        "INSERT INTO eos_notify_log (sent_at, schedule, recipient, cc, owner_name, subject, item_count, "
        "hw_count, os_count, status, detail, smtp_host, mail_from) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_now(), sched, to, ", ".join(cc), owner_name, subject, n, hw, osn,
         "sent" if ok else "failed", detail, smtp["smtp_host"], smtp["mail_from"]))


def _public_item(it: dict) -> dict:
    return {"track": it["track"], "track_label": TRACK_LABEL[it["track"]], "hostname": it.get("hostname"), "mk": it.get("mk"),
            "ip": it.get("ip"), "environment": it.get("environment"), "item": it["item"],
            "eos_date": it["eos_date"], "days_left": it["days_left"],
            "tier": _tier_label(it["_bucket"], ()), "bucket": it["_bucket"],
            "owner": it.get("_owner") or "", "owner_role": OWNER_LABEL[it["track"]],
            "why": it.get("_why"), "via_dept": it.get("_via_dept"),
            "fallback_dept": it.get("_fb_dept"), "fallback_why": it.get("_fb_why"),
            "assign": it.get("_assign"), "systems": it.get("systems") or [], "api_ids": it.get("api_ids") or [],
            "devclass": it.get("devclass")}


def preview(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """畫面「預覽」與「沒人收」用：會寄給誰、各幾筆、誰沒人收。**不寄、不寫紀錄、不動 last_bucket。**

    不要求 SMTP 填好（先看清單再去填設定也應該行），但 AD 沒匯入時直接講，
    不然全部會被歸成「查無此人」，看起來像資料有問題。
    """
    _ensure(conn)
    today = today or today_tpe()
    try:
        cfg = load_settings(conn)
    except SettingsCorrupt as exc:
        return {"ok": False, "reason": str(exc)}
    if not vn.ad_ready(conn):
        return {"ok": False, "reason": "AD 名單還沒匯入，查不到任何人的信箱。"
                                       "請先到「6 資料匯入」（/import）匯入 AD 名單。"}
    import eos_assign
    try:
        data = collect(conn, today, "monthly", ignore_state=True)
    except eos_assign.RulesCorrupt as exc:          # 審查 P3：規則壞掉要講出來，不是讓整頁 500
        return {"ok": False, "reason": str(exc)}
    buckets = data["buckets"]
    groups = []
    for _eid, grp in data["by_owner"].items():
        p = grp["person"]
        items = grp["items"]
        groups.append({"to": p["mail"], "name": p.get("display_name"), "dept": p.get("dept_name"),
                       "count": len(items), "hw": sum(1 for i in items if i["track"] == TRACK_HW),
                       "os": sum(1 for i in items if i["track"] == TRACK_OS),
                       "items": [_public_item(i) for i in items]})
    for g in groups:
        for i in g["items"]:
            i["tier"] = _tier_label(i["bucket"], buckets)
    orphans = [_public_item(o) for o in data["orphans"]]
    for o in orphans:
        o["tier"] = _tier_label(o["bucket"], buckets)
    return {"ok": True, "today": today.isoformat(), "buckets": list(buckets), "stats": data["stats"],
            "groups": sorted(groups, key=lambda g: (-g["count"], g["name"] or "")),
            "orphans": orphans, "fallback_to": (cfg.get("fallback_to") or "")}


def run(conn: sqlite3.Connection, today: date | None = None, schedule: str | None = None,
        dry_run: bool = False, sender=None) -> dict:
    """跑一期。dry_run=True 只組信不寄、不寫任何紀錄。sender(cfg,to,cc,subject,body)->(ok,detail) 給測試換掉。

    設定不齊／AD 沒匯入就整個不動並指名；不可以「先寄能寄的」。
    """
    _ensure(conn)
    today = today or today_tpe()
    real_send = sender or _send_smtp
    try:
        cfg = load_settings(conn)
        smtp = smtp_settings(conn)
    except SettingsCorrupt as exc:
        return {"ok": False, "reason": str(exc), "sent": 0, "failed": 0}
    # 試寄模式（11-09，正式寄前必經）：設了 trial_to，**每一封**都改寄這個信箱（主旨與內文標出原本的收件人／副本），
    # 副本一律拿掉、最多 TRIAL_MAX 封；不寫寄送紀錄、不動 last_bucket（正式開寄時照樣會寄）。唯一的寄信出口就是 send()。
    trial_to = (cfg.get("trial_to") or "").strip()
    trial_n = [0]

    def send(smtp_, to, cc, subject, body):
        if not trial_to:
            return real_send(smtp_, to, cc, subject, body)
        if trial_n[0] >= TRIAL_MAX:
            return False, f"試寄只寄前 {TRIAL_MAX} 封，這封略過"
        trial_n[0] += 1
        head = f"（試寄：這封原本寄給 {to}；副本 {'、'.join(cc) or '無'}。正式開寄前不會寄給他們。）"
        return real_send(smtp_, trial_to, [], f"[試寄｜原收件人 {to}] {subject}", head + chr(10) * 2 + body)

    missing = vn.check_settings(smtp)
    if missing:
        return {"ok": False, "reason": f"寄信設定還缺：{'、'.join(missing)}。"
                                       "SMTP 沿用「系統設定 → 通知設定 → 寄信設定」那一份，請先填好。",
                "sent": 0, "failed": 0}
    if not vn.ad_ready(conn):
        return {"ok": False, "reason": "AD 名單還沒匯入，查不到任何人的信箱。"
                                       "請先到「6 資料匯入」（/import）匯入 AD 名單。",
                "sent": 0, "failed": 0}

    todo = [schedule] if schedule else schedules_due(today)
    if not todo:
        return {"ok": True, "reason": "今天不是寄送日（每月 1 號）", "sent": 0, "failed": 0}

    result = {"ok": True, "sent": 0, "failed": 0, "orphan_items": 0, "details": []}
    for sched in todo:
        data = collect(conn, today, sched)
        buckets, stats = data["buckets"], data["stats"]
        fb = (cfg.get("fallback_to") or "").strip()
        occ = vn.always_cc(cfg)

        for _eid, grp in data["by_owner"].items():
            p, items = grp["person"], grp["items"]
            to = p["mail"]
            cc = list(occ)
            if cfg.get("manager_cc", True) and (p.get("manager_mail") or "").strip():
                mgr = p["manager_mail"].strip()
                if mgr.lower() not in {c.lower() for c in cc}:
                    cc.append(mgr)
            hw_n = sum(1 for i in items if i["track"] == TRACK_HW)
            os_n = len(items) - hw_n
            subject = _subject(cfg, p.get("display_name") or to, len(items), today)
            body = _body(items, buckets, today, stats)
            if dry_run:
                result["details"].append({"to": to, "cc": cc, "subject": subject, "count": len(items),
                                          "status": "預覽"})
                continue
            ok, detail = send(smtp, to, cc, subject, body)
            if trial_to:
                result["details"].append({"to": to, "cc": cc, "subject": subject, "count": len(items),
                                          "status": ("試寄→" + trial_to) if ok else "試寄失敗", "detail": detail})
                result["sent" if ok else "failed"] += 1
                continue
            _log(conn, sched, to, cc, p.get("display_name"), subject, len(items), hw_n, os_n, ok, detail, smtp)
            if ok:
                result["sent"] += 1
                # 只有寄成功才更新 bucket：寧可重複寄也不可以假裝寄過
                for it in items:
                    conn.execute(
                        "INSERT INTO eos_notify_state (state_key, last_bucket, last_sent_at, last_recipient) VALUES (?,?,?,?) "
                        "ON CONFLICT(state_key) DO UPDATE SET last_bucket = excluded.last_bucket, "
                        "last_sent_at = excluded.last_sent_at, last_recipient = excluded.last_recipient",
                        (it["_key"], it["_bucket"], _now(), to))
            else:
                result["failed"] += 1
            result["details"].append({"to": to, "cc": cc, "subject": subject, "count": len(items),
                                      "status": "sent" if ok else "failed", "detail": detail})

        orphans = data["orphans"]
        result["orphan_items"] = len(orphans)
        if orphans and fb and not dry_run:
            subject = _subject(cfg, "無法決定收件人", len(orphans), today)
            ok, detail = send(smtp, fb, occ, subject, _orphan_body(orphans, today, stats))
            if not trial_to:
                _log(conn, sched, fb, occ, "(沒人收)", subject, len(orphans),
                 sum(1 for o in orphans if o["track"] == TRACK_HW),
                 sum(1 for o in orphans if o["track"] == TRACK_OS), ok, detail, smtp)
            result["sent" if ok else "failed"] += 1
            result["details"].append({"to": fb, "cc": occ, "subject": subject, "count": len(orphans),
                                      "status": "sent" if ok else "failed", "detail": detail})
        elif orphans and not fb:
            # 沒設代收信箱不可以靜靜吞掉：那批會永遠沒人知道
            result["details"].append({
                "to": "(未設定)", "cc": [], "count": len(orphans), "status": "failed",
                "subject": "沒人收的 EOS 項目",
                "detail": "有找不到收件人的 EOS 項目，但沒有設定「查無收件人時轉寄給誰」，"
                          "這批沒有寄出去。請到設定頁補上（畫面「沒人收」也看得到這批）。"})
            result["failed"] += 1
        elif orphans and dry_run:
            result["details"].append({"to": fb, "cc": occ, "subject": "沒人收的 EOS 項目",
                                      "count": len(orphans), "status": "預覽"})

        # 本期 0 筆也寄一封：沉默證明不了系統還活著
        if not data["by_owner"] and not orphans:
            result["zero"] = True
            subject = _subject(cfg, "本期 0 筆", 0, today)
            body = _zero_body(today, buckets, stats)
            if dry_run:
                result["details"].append({"to": fb or "(未設定)", "cc": [], "subject": subject,
                                          "count": 0, "status": "預覽"})
            elif fb:
                ok, detail = send(smtp, fb, occ, subject, body)
                if not trial_to:
                    _log(conn, sched, fb, occ, "(本期 0 筆)", subject, 0, 0, 0, ok, detail, smtp)
                result["sent" if ok else "failed"] += 1
                result["details"].append({"to": fb, "cc": occ, "subject": subject, "count": 0,
                                          "status": "sent" if ok else "failed", "detail": detail})
            else:
                result["details"].append({
                    "to": "(未設定)", "cc": [], "count": 0, "status": "failed", "subject": subject,
                    "detail": "本期 0 筆的通知沒有寄出去——還沒設定「查無收件人時轉寄給誰」。"
                              "沒有這封信，沒收到信的人分不出是真的沒事還是排程死了。"})
                result["failed"] += 1
        result["stats"] = stats
    if trial_to:
        result["trial_to"] = trial_to
    if not dry_run and not trial_to:
        conn.commit()
    return result


def logs(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM eos_notify_log ORDER BY id DESC LIMIT ?", (limit,))]


# ── 排程：到點自己跑 ───────────────────────────────────────────────────
DEFAULT_SEND_TIME = "08:00"
RETRY_MINUTES = 60
RETRY_UNTIL_HOUR = 18


def send_time_of(cfg: dict) -> tuple[int, int]:
    return vn.send_time_of({"send_time": cfg.get("send_time") or DEFAULT_SEND_TIME})


def _attempts(conn, run_date: str, schedule: str) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM eos_notify_run WHERE run_date = ? AND schedule = ? ORDER BY id",
        (run_date, schedule))]


def mark_run(conn, run_date: str, schedule: str, result: dict, now: datetime | None = None) -> None:
    """每一次嘗試都記，成功失敗都記（「今天寄了嗎」出事當下要答得出來）。"""
    _ensure(conn)
    conn.execute(
        "INSERT INTO eos_notify_run (run_date, schedule, started_at, config_ok, sent, failed, reason) "
        "VALUES (?,?,?,?,?,?,?)",
        (run_date, schedule, (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
         1 if result.get("ok") else 0, int(result.get("sent") or 0), int(result.get("failed") or 0),
         result.get("reason")))
    conn.commit()


def is_due(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """現在該跑哪些班表。關著（沒設定過＝關）、沒到點、已成功跑過、還在冷卻 → 空。"""
    try:
        cfg = load_settings(conn)
    except SettingsCorrupt:
        # 設定壞掉不是「到點」：排程不拿壞設定去試；手動觸發 run() 時會把壞掉的原因講清楚
        return []
    if not cfg.get("enabled"):
        return []
    h, m = send_time_of(cfg)
    if (now.hour, now.minute) < (h, m) or now.hour >= RETRY_UNTIL_HOUR:
        return []
    run_date = now.strftime("%Y-%m-%d")
    due = []
    for s in schedules_due(now.date()):
        tries = _attempts(conn, run_date, s)
        # 「跑完而且沒有任何一封失敗」才算這期做完；config_ok 只代表跑得完
        if any(t["config_ok"] and not t["failed"] for t in tries):
            continue
        if tries:
            last = datetime.strptime(tries[-1]["started_at"], "%Y-%m-%d %H:%M:%S")
            if now - last < timedelta(minutes=RETRY_MINUTES):
                continue
        due.append(s)
    return due


def tick(conn: sqlite3.Connection, now: datetime | None = None, sender=None) -> list[dict]:
    now = now or datetime.now()
    out = []
    for s in is_due(conn, now):
        r = run(conn, now.date(), s, sender=sender)
        mark_run(conn, now.strftime("%Y-%m-%d"), s, r, now)
        out.append({"schedule": s, **r})
    return out


def last_runs(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM eos_notify_run ORDER BY id DESC LIMIT ?", (limit,))]


# ── 依業務系統看 EOS（2.059.17，使用者 11-10：「點大洲系統看它全部 EOS」）────────────────────
NO_SYSTEM = "（沒對到業務系統）"


def by_system(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """每個業務系統一列：台數、門檻內的硬體／OS EOS 筆數、現在寄給誰。**口徑＝通知預覽同一份**（collect ignore_state）。

    - 「筆」＝（軌別, 這台, 項目）一筆；一筆寄給多人仍算一筆（不乘人數）。
    - 一台掛多個業務系統 → 每個系統各算一次，所以各列加總可以大於總筆數（total_items 才是不重複的總數）。
    - 沒填 APID 的歸「沒對到業務系統」一列。
    - 軟體 EOS：沒有逐台軟體版本資料，不算（畫面標不知道，不寫 0）。
    """
    import system_stats
    from lifecycle import retired_sql_list
    _ensure(conn)
    today = today or today_tpe()
    if not vn.ad_ready(conn):
        return {"ok": False, "reason": "AD 名單還沒匯入，查不到任何人的信箱。請先到「6 資料匯入」（/import）匯入 AD 名單。"}
    import eos_assign
    try:
        data = collect(conn, today, "monthly", ignore_state=True)
    except eos_assign.RulesCorrupt as exc:
        return {"ok": False, "reason": str(exc)}
    buckets = data["buckets"]
    items: dict[tuple, dict] = {}
    for _eid, grp in data["by_owner"].items():
        nm = grp["person"].get("display_name") or grp["person"].get("mail")
        for it in grp["items"]:
            k = (it["track"], it["mk"], it["item"])
            e = items.setdefault(k, {**_public_item(it), "to": []})
            if nm not in e["to"]:
                e["to"].append(nm)
    for it in data["orphans"]:
        k = (it["track"], it["mk"], it["item"])
        items.setdefault(k, {**_public_item(it), "to": []})
    for e in items.values():
        e["tier"] = _tier_label(e["bucket"], buckets)
        e["to_text"] = "、".join(e["to"]) if e["to"] else "沒人收"

    names: dict[str, tuple[str, str]] = {}
    try:
        for r in conn.execute("SELECT api_id, name, ap_owner FROM business_system"):
            names[(r["api_id"] or "").strip()] = ((r["name"] or "").strip(), (r["ap_owner"] or "").strip())
    except sqlite3.Error:
        names = {}
    hw_rows = conn.execute("SELECT asset_serial, hostname, ip, environment, os, device_model, asset_name, asset_purpose, "
                           "asset_status, owner, inventory_department, usage_unit, api_id FROM hardware "
                           "WHERE COALESCE(asset_status,'') NOT IN " + retired_sql_list()).fetchall()
    hosts: dict[str, set] = {}
    for mk, group in system_stats.collapse_machines(hw_rows).items():
        aids = {((g["api_id"] or "").strip()) for g in group} - {""}
        for a in (aids or {""}):
            hosts.setdefault(a, set()).add(mk)

    rows: dict[str, dict] = {}

    def row(a: str) -> dict:
        if a not in rows:
            nm, ap = names.get(a, ("", ""))
            rows[a] = {"api_id": a, "name": (nm or a) if a else NO_SYSTEM, "ap_owner": ap,
                       "hosts": len(hosts.get(a, ())), "hw": 0, "os": 0, "orphan": 0, "items": [], "_to": {}}
        return rows[a]
    for a in hosts:
        row(a)
    for e in items.values():
        for a in (e.get("api_ids") or [""]):
            r = row(a)
            r[e["track"]] += 1
            r["items"].append(e)
            if not e["to"]:
                r["orphan"] += 1
            for n in e["to"]:
                r["_to"][n] = r["_to"].get(n, 0) + 1
    out = []
    for r in rows.values():
        to = r.pop("_to")
        r["recipients"] = [{"name": n, "count": c} for n, c in sorted(to.items(), key=lambda x: (-x[1], x[0]))]
        r["to_text"] = "、".join(f"{x['name']} {x['count']}" for x in r["recipients"]) or ("—" if not r["items"] else "")
        r["count"] = r["hw"] + r["os"]
        r["items"].sort(key=lambda e: (e["days_left"], e["hostname"] or ""))
        out.append(r)
    out.sort(key=lambda r: (r["api_id"] == "", -r["count"], r["name"]))
    return {"ok": True, "today": today.isoformat(), "buckets": list(buckets), "rows": out,
            "total_items": len(items), "software_known": False}
