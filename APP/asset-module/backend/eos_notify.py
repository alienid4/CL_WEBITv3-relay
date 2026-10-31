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
from datetime import date, datetime, timedelta

import vuln_notify as vn

#: 兩級門檻（天）。**只是設定沒填時的退路**；實際值存 app_settings、畫面可改。
DEFAULT_BUCKETS = (180, 365)

SETTING_KEY = "eos_notify"

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
    last_sent_at TEXT
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


def eos_candidates(conn: sqlite3.Connection, today: date) -> tuple[list[dict], dict]:
    """每台（machine_key 一把尺）各軌算出「有官方 EOS 日期」的項目，還沒套門檻、還沒找收件人。

    回 (candidates, stats)。candidate: track/mk/hostname/ip/environment/item/eos_date/days_left
    和 owners（名字清單；空＝沒填／沒登記，原因放 owner_why）。
    """
    import eos
    import normalize
    import system_stats
    from lifecycle import retired_sql_list

    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, environment, os, device_model, asset_name, asset_purpose, "
        "asset_status, owner FROM hardware WHERE COALESCE(asset_status,'') NOT IN "
        + retired_sql_list()).fetchall()
    groups = system_stats.collapse_machines(rows)

    sw_owner: dict[str, list[str]] = {}
    sw_present: set[str] = set()
    for r in conn.execute("SELECT asset_serial, owner FROM software"):
        sw_present.add(r["asset_serial"])
        sw_owner.setdefault(r["asset_serial"], []).extend(split_owners(r["owner"]))

    os_memo: dict[tuple, dict | None] = {}
    hw_memo: dict[tuple, tuple[str, dict | None]] = {}
    out: list[dict] = []
    stats = {"machines": len(groups), "hw_with_date": 0, "os_with_date": 0,
             "unverified": 0, "unverified_list": []}

    for mk, group in groups.items():
        rep = group[0]
        base = {"mk": mk, "hostname": rep["hostname"], "ip": rep["ip"], "environment": rep["environment"],
                "asset_serial": rep["asset_serial"]}

        # ── 硬體軌：擁有者＝hardware.owner（SP）──
        hrow = next((g for g in group if (g["device_model"] or "").strip()), None)
        if hrow is not None:
            mkey = (hrow["device_model"], _hint(hrow))
            if mkey not in hw_memo:
                info = normalize.normalize_model(hrow["device_model"], conn, _hint(hrow))
                fam, _v = normalize.hardware_family(info["vendor"], info["canonical"])
                hit = None if fam == "虛擬化" else eos.lookup_hardware_eos(info["canonical"])
                hw_memo[mkey] = (info["canonical"], hit)
            canonical, hit = hw_memo[mkey]
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
                out.append({**base, "track": TRACK_HW, "item": canonical, "eos_date": d.isoformat(),
                            "eos_date_text": (hit or {}).get("eos_date"),
                            "date_precision": "month" if is_month_precision((hit or {}).get("eos_date")) else "day",
                            "days_left": (d - today).days, "owners": owners, "owner_why": NO_OWNER_HW,
                            "extendable": bool((hit or {}).get("extendable"))})

        # ── 作業系統軌：擁有者＝software.owner（AP），這台掛的每一筆軟體登記 ──
        orow = next((g for g in group if (g["os"] or "").strip()), None)
        if orow is not None:
            okey = (orow["os"], orow["device_model"])
            if okey not in os_memo:
                info = normalize.normalize_os(orow["os"], conn, orow["device_model"])
                os_memo[okey] = {"canonical": info["canonical"], "hit": eos.lookup_os_eos(info["canonical"])}
            canonical, hit = os_memo[okey]["canonical"], os_memo[okey]["hit"]
            d = parse_eos_date((hit or {}).get("eos_date"))
            if d is not None and not eos.is_trusted(hit):
                _note_unverified(stats, base, TRACK_OS, canonical, d, today)
                d = None
            if d is not None:
                stats["os_with_date"] += 1
                owners = []
                for g in group:
                    for n in sw_owner.get(g["asset_serial"], []):
                        if n not in owners:
                            owners.append(n)
                has_sw = any(g["asset_serial"] in sw_present for g in group)
                out.append({**base, "track": TRACK_OS, "item": canonical, "eos_date": d.isoformat(),
                            "eos_date_text": (hit or {}).get("eos_date"),
                            "date_precision": "month" if is_month_precision((hit or {}).get("eos_date")) else "day",
                            "days_left": (d - today).days, "owners": owners,
                            "owner_why": NO_OWNER_SW if has_sw else NO_SOFTWARE,
                            "extendable": bool((hit or {}).get("extendable"))})
    return out, stats


def collect(conn: sqlite3.Connection, today: date | None = None, schedule: str = "monthly",
            ignore_state: bool = False) -> dict:
    """挑出這一期要提的項目，分成「有收件人」與「沒人收」。

    ignore_state=True：畫面上看「現在到底有哪些沒人收／會寄給誰」用，不看寄過沒有。
    """
    _ensure(conn)
    today = today or date.today()
    cfg = load_settings(conn)
    buckets = buckets_of(cfg)
    state = {r["state_key"]: r["last_bucket"] for r in conn.execute("SELECT * FROM eos_notify_state")}

    cands, stats = eos_candidates(conn, today)
    by_person: dict[str, dict] = {}
    orphans: list[dict] = []
    memo: dict[str, tuple[dict | None, str | None]] = {}
    in_window = 0

    for c in cands:
        b = bucket_for(c["days_left"], buckets)
        if b is None:
            continue                                   # 還很遠，這期不用提
        in_window += 1
        names = c["owners"] or [None]
        for name in names:
            key = f"{c['track']}|{c['mk']}|{name or ''}|{c['item']}"
            prev = state.get(key)
            if not ignore_state and prev is not None and b >= prev:
                continue                               # 同一級不重複提；跨進更急的才再出現
            it = {**c, "_bucket": b, "_key": key, "_owner": name or ""}
            if name is None:
                person, why = None, c["owner_why"]
            else:
                if name not in memo:
                    memo[name] = vn.resolve_recipient(conn, name)
                person, why = memo[name]
                if person is not None and not vn.in_scope(cfg, person):
                    person, why = None, OUT_OF_SCOPE
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
    if stats and stats.get("unverified"):
        out.append(f"另有 {stats['unverified']} 筆日期來源尚未核對（分組標示「來源未核對」），未列入通知。")
    out.append("查不到官方 EOS 日期的項目不在這封信裡——那是「不知道」，不代表沒問題。")
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
            f"｜{it.get('ip') or '—'}｜官方公告 {it['item']} 於 {_date_phrase(it)} 結束（{when}）{'；官方另有付費延伸方案，本資產是否持有尚未登記（權益待確認）' if it.get('extendable') else ''}")


def _body(items: list[dict], buckets: tuple[int, ...], today: date, stats: dict | None) -> str:
    lines = [f"官方公告以下硬體或作業系統即將結束（EOS）或已結束支援，請確認維護權益並評估是否升級（{today:%Y-%m-%d}）：", ""]
    for b in sorted({it["_bucket"] for it in items}):
        grp = [it for it in items if it["_bucket"] == b]
        lines.append(f"【{_tier_label(b, buckets)}】{len(grp)} 筆")
        for it in sorted(grp, key=lambda x: (x["days_left"], x["track"], x.get("hostname") or "")):
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
    return {"track": it["track"], "track_label": TRACK_LABEL[it["track"]], "hostname": it.get("hostname"),
            "ip": it.get("ip"), "environment": it.get("environment"), "item": it["item"],
            "eos_date": it["eos_date"], "days_left": it["days_left"],
            "tier": _tier_label(it["_bucket"], ()), "bucket": it["_bucket"],
            "owner": it.get("_owner") or "", "owner_role": OWNER_LABEL[it["track"]],
            "why": it.get("_why")}


def preview(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """畫面「預覽」與「沒人收」用：會寄給誰、各幾筆、誰沒人收。**不寄、不寫紀錄、不動 last_bucket。**

    不要求 SMTP 填好（先看清單再去填設定也應該行），但 AD 沒匯入時直接講，
    不然全部會被歸成「查無此人」，看起來像資料有問題。
    """
    _ensure(conn)
    today = today or date.today()
    try:
        cfg = load_settings(conn)
    except SettingsCorrupt as exc:
        return {"ok": False, "reason": str(exc)}
    if not vn.ad_ready(conn):
        return {"ok": False, "reason": "AD 名單還沒匯入，查不到任何人的信箱。"
                                       "請先到「6 資料匯入」（/import）匯入 AD 名單。"}
    data = collect(conn, today, "monthly", ignore_state=True)
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
    today = today or date.today()
    send = sender or _send_smtp
    try:
        cfg = load_settings(conn)
        smtp = smtp_settings(conn)
    except SettingsCorrupt as exc:
        return {"ok": False, "reason": str(exc), "sent": 0, "failed": 0}

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
            _log(conn, sched, to, cc, p.get("display_name"), subject, len(items), hw_n, os_n, ok, detail, smtp)
            if ok:
                result["sent"] += 1
                # 只有寄成功才更新 bucket：寧可重複寄也不可以假裝寄過
                for it in items:
                    conn.execute(
                        "INSERT INTO eos_notify_state (state_key, last_bucket, last_sent_at) VALUES (?,?,?) "
                        "ON CONFLICT(state_key) DO UPDATE SET last_bucket = excluded.last_bucket, "
                        "last_sent_at = excluded.last_sent_at", (it["_key"], it["_bucket"], _now()))
            else:
                result["failed"] += 1
            result["details"].append({"to": to, "cc": cc, "subject": subject, "count": len(items),
                                      "status": "sent" if ok else "failed", "detail": detail})

        orphans = data["orphans"]
        result["orphan_items"] = len(orphans)
        if orphans and fb and not dry_run:
            subject = _subject(cfg, "無法決定收件人", len(orphans), today)
            ok, detail = send(smtp, fb, occ, subject, _orphan_body(orphans, today, stats))
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
    if not dry_run:
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
