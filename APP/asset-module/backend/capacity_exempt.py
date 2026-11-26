"""已知例外（不提醒）：容量／磁碟「要處理」的單一例外機制（2026-11-14 使用者＋GPT 審查定案）。

## 適用
  * kind='ds'：VMware 資料存放區（Datastore），身分鍵＝storage_capacity.identity（URL／vCenter＋Object ID／名稱＋類型）。
  * kind='disk'：主機磁碟（Unix／Windows／AIX 檔案系統或磁碟機），身分鍵＝machine_key + '|' + norm_mount(掛載點／磁碟機)。
    WhatsUp 的磁碟名稱用同一個 norm_mount 對：去掉 Label／Serial／容量尾巴，保留完整掛載路徑
    （「C:\\ Label:…」→「C:」、「D:\\SQLData\\Log\\ Label:…」→「D:\\sqldata\\log」、「/var 50GB」→「/var」）；
    IP 對不到資產（或多重對應）＝對不到，**不套例外**，另計「對不到的磁碟」。
    同一台、同一批資料裡有兩個以上原始名稱正規化成同一個鍵＝分不出是哪一顆，**不套例外**（照常提醒）。
  * VMware 1.5（vROps）對 RVTools 卷宗：vCenter（vROps vcid＝RVTools VI SDK UUID）＋名稱優先；RVTools 沒有 UUID 才只看名稱；
    兩邊任一邊同名不唯一＝不套例外。
所有判斷只有一支：check()。消費端（5.2 容量預警／VMware Disk／實體主機磁碟、1.5 VMware 與磁碟列、Teams 磁碟告警、
1.4、首頁、每日異動）都呼叫它；check 失敗（讀不到表、例外）＝**不算例外**（照常提醒），錯誤寫日誌並放在 load() 的 error。

## 規則
  * 只能管理員設定／移除；原因必填；期限 30／90（預設）／180／365 天，或「到 EOS 日」（只有主機磁碟、主機查得到 EOS 才能選，
    最多仍是今天＋365 天）。沒有永久。到期日當天起恢復提醒（expires＝第一個恢復的日子）。
  * 設定時記基準：用量 %（一位小數）、剩餘 GB、容量 GB（來源給得出的才記）。
  * 失效（例外立即不再套用，並記一筆 void）：
      - 用量 ≥ 基準＋exempt_growth_pp（app_settings，預設 2 個百分點）**連續 2 個樣本**（同一個資料來源、同一個原始項目、
        不同資料時間才算下一個樣本）；
      - 剩餘 GB ≤ 基準剩餘 × 0.9（掉了 10% 以上）；
      - 容量比基準小（縮小＝失效；變大＝照樣有效）。
    基準**分來源**：設定時那個來源用設定當下的值；其他來源（例如 Teams 用的 WhatsUp 每日、1.5 用的 vROps）第一次取樣的值當它自己的基準，
    只跟同一來源比（不同系統量的用量有小差異，混著比會誤判）。
  * 到期／失效：在 1.4 追蹤處理出現一次（「例外已到期」「例外失效」），管理員按「知道了」（ack）或重新設定才消失。
  * 例外本身管理員可以編輯（原因、期限〔同樣的上限〕、備註），每次編輯在稽核表加一筆（誰、何時、舊→新）。
  * 稽核表 capacity_exempt_log 只增不改（set／edit／remove／expire／void／ack），沒有任何刪改 API。

## 讀寫分工（讀取路徑不寫資料）
check() 是純函式：用已存的狀態＋這次的樣本算。樣本計數、狀態轉換（到期、失效）與稽核由 tick() 寫（排程每 10 分鐘，
v2_today.tick 呼叫），所以頁面、驗算（verify_numbers 用正式庫）看到的是同一個結果。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta

log = logging.getLogger("capacity_exempt")

KIND_DS, KIND_DISK = "ds", "disk"
KIND_LABEL = {KIND_DS: "Datastore", KIND_DISK: "主機磁碟"}
PRESETS = (30, 90, 180, 365)
DEFAULT_DAYS = 90
MAX_DAYS = 365
GROWTH_KEY = "exempt_growth_pp"
DEFAULT_GROWTH_PP = 2.0
FREE_DROP = 0.9            # 剩餘 ≤ 基準 × 0.9 ＝ 掉了 10% 以上
CONSEC = 2                 # 用量上升要連續幾個樣本才失效
TICK_MIN = 10

SQL = (
    """CREATE TABLE IF NOT EXISTS capacity_exempt (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,                  -- ds／disk
        key TEXT NOT NULL,                   -- 身分鍵（見模組說明）
        label TEXT,                          -- 顯示名稱（設定當下）
        reason TEXT NOT NULL,
        duration TEXT NOT NULL,              -- 30／90／180／365／eos
        expires TEXT NOT NULL,               -- YYYY-MM-DD：這一天起恢復提醒
        eos_date TEXT, eos_source TEXT,
        base_used_pct REAL, base_free_gb REAL, base_cap_gb REAL, base_source TEXT,
        created_by TEXT, created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',   -- active／removed／expired／void
        ended_at TEXT, ended_by TEXT, end_note TEXT,
        acked_at TEXT, acked_by TEXT,
        note TEXT)                           -- 備註（可編輯）""",
    "CREATE INDEX IF NOT EXISTS ix_capacity_exempt_key ON capacity_exempt(kind, key, status)",
    """CREATE TABLE IF NOT EXISTS capacity_exempt_sample (
        exempt_id INTEGER NOT NULL, source TEXT NOT NULL, sample_key TEXT, over_count INTEGER NOT NULL DEFAULT 0,
        used_pct REAL, at TEXT, PRIMARY KEY (exempt_id, source))""",
    # 2026-11-14 審查 P2-1／P2-3：樣本分（例外, 來源, 原始項目），各自的基準；舊表 capacity_exempt_sample 保留不用
    """CREATE TABLE IF NOT EXISTS capacity_exempt_obs (
        exempt_id INTEGER NOT NULL, source TEXT NOT NULL, item TEXT NOT NULL DEFAULT '',
        base_used_pct REAL, base_free_gb REAL, base_cap_gb REAL,
        sample_key TEXT, over_count INTEGER NOT NULL DEFAULT 0, used_pct REAL, at TEXT,
        PRIMARY KEY (exempt_id, source, item))""",
    """CREATE TABLE IF NOT EXISTS capacity_exempt_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, exempt_id INTEGER, kind TEXT, key TEXT,
        action TEXT NOT NULL,                -- set／edit／remove／expire／void／ack
        actor TEXT, detail TEXT)""",
)


def ensure(conn: sqlite3.Connection) -> None:
    """建表（啟動時由 db.init_db 呼叫；不 commit）。只新增表，不動既有表。
    同一項只能有一筆生效中（部分唯一索引；審查 P3-1）。既有資料若已重複，索引建不起來：寫日誌，set_exempt 仍會先查再寫。"""
    for s in SQL:
        conn.execute(s)
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_capacity_exempt_active ON capacity_exempt(kind, key) WHERE status = 'active'")
    except sqlite3.IntegrityError as exc:
        log.error("已知例外有重複的生效中資料，唯一索引沒建：%s", exc)


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


# ===== 身分鍵 =====

_DRIVE = re.compile(r"^([A-Za-z]):(.*)$")
# 原始名稱後面的說明尾巴（WhatsUp：「 Label:xxx」「 Serial Number xxx」「 50GB」「 - 50 GB」）：從這裡切掉
_TAIL = re.compile(r"\s+(?:-\s*)?(?:label\s*:|serial\s+number\b|\d+(?:\.\d+)?\s*[kmgtp]i?b\b).*$", re.I)


def norm_mount(name) -> str | None:
    """掛載點／磁碟機正規化（審查 P2-1）：先切掉 Label／Serial Number／容量這類尾巴，再——
    Windows：磁碟機大寫＋冒號；只有磁碟機（「C:」「C:\\」）＝「C:」，掛載到資料夾的保留完整路徑（反斜線、小寫、去結尾 \\），
    例「D:\\SQLData\\Log\\」→「D:\\sqldata\\log」，跟 D: 本身是不同的鍵；
    Unix／AIX：完整路徑（去結尾 /，根目錄留 /）；其他（例如 VG 名稱）去空白後小寫。空白＝None。"""
    s = unicodedata.normalize("NFKC", str(name or "")).strip()
    s = _TAIL.sub("", s).strip().rstrip("-").strip()
    if not s:
        return None
    m = _DRIVE.match(s)
    if m:
        path = m.group(2).replace("/", "\\").strip().strip("\\").strip()
        return m.group(1).upper() + ":" + (("\\" + path.lower()) if path else "")
    if s.startswith("/"):
        return s.rstrip("/") or "/"
    return "".join(s.split()).lower()


def unique_keys(pairs) -> dict:
    """[(原始項目, 鍵)] → {鍵: 原始項目}，只留「同一批只有一個原始項目」的鍵；兩個以上對到同一鍵＝分不出來，不收（照常提醒）。"""
    seen: dict = {}
    for raw, k in pairs:
        if k:
            seen.setdefault(k, set()).add(str(raw))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def disk_key(mk: str | None, mount) -> str | None:
    m = norm_mount(mount)
    return f"{mk}::{m}" if mk and m else None   # 用 :: 不用 |（| 保留給 machine_key 守門）


def mk_of_serial(conn, serial: str | None, cache: dict | None = None) -> str | None:
    if not serial:
        return None
    cache = cache if cache is not None else {}
    ck = ("s", serial)
    if ck not in cache:
        import system_stats
        r = conn.execute("SELECT hostname, ip FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
        cache[ck] = system_stats.machine_key(r[0], r[1], serial) if r is not None else None
    return cache[ck]


def mk_of_ip(conn, ip: str | None, cache: dict | None = None) -> str | None:
    """IP → machine_key（跟 Teams 磁碟告警去重同一個對法：relay_import.match_asset 唯一對到才算）；對不到＝None。"""
    if not ip:
        return None
    cache = cache if cache is not None else {}
    ck = ("i", ip)
    if ck not in cache:
        import relay_import
        try:
            res, _n, serial = relay_import.match_asset(conn, str(ip).strip())
        except sqlite3.Error:
            res, serial = None, None
        cache[ck] = mk_of_serial(conn, serial, cache) if res == "matched" else None
    return cache[ck]


# ===== 狀態載入與判斷（純讀） =====

class State:
    def __init__(self, active: dict, obs: dict, growth_pp: float, today: date, error: str | None = None):
        self.active, self.obs, self.growth_pp, self.today, self.error = active, obs, growth_pp, today, error


def growth_pp(conn) -> float:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (GROWTH_KEY,)).fetchone()
        v = float(json.loads(r[0])) if r and r[0] else DEFAULT_GROWTH_PP
        return v if 0 < v <= 50 else DEFAULT_GROWTH_PP
    except (sqlite3.Error, ValueError, TypeError):
        return DEFAULT_GROWTH_PP


def load(conn, now: datetime | None = None) -> State:
    """讀生效中的例外。任何錯誤＝空狀態（全部不算例外、照常提醒），錯誤寫日誌並放在 error。"""
    today = (now or _now()).date()
    try:
        if not _has(conn, "capacity_exempt"):
            return State({}, {}, DEFAULT_GROWTH_PP, today)
        act = {}
        for r in conn.execute("SELECT * FROM capacity_exempt WHERE status = 'active'"):
            act[(r["kind"], r["key"])] = dict(r)
        obs = {}
        if _has(conn, "capacity_exempt_obs"):
            for r in conn.execute("SELECT * FROM capacity_exempt_obs"):
                obs[(r["exempt_id"], r["source"], r["item"] or "")] = dict(r)
        return State(act, obs, growth_pp(conn), today)
    except Exception as exc:  # noqa: BLE001 - 例外讀不到不能讓提醒消失：當成沒有例外，並記錄
        log.error("讀取已知例外失敗，全部照常提醒：%s: %s", type(exc).__name__, exc)
        return State({}, {}, DEFAULT_GROWTH_PP, today, error=f"讀取已知例外失敗（{type(exc).__name__}），全部照常提醒")


def _r1(v):
    return None if v is None else round(float(v), 1)


def baseline(st: State, row: dict, source: str | None, item) -> tuple:
    """這個來源＋原始項目的基準 (用量, 剩餘, 容量)：tick 記過的優先；沒記過且就是設定時的來源＝設定當下的值；
    其他來源第一次出現＝還沒有基準（None，tick 會把這一筆記成基準）。"""
    o = st.obs.get((row["id"], source or "", str(item or "")))
    if o:
        return o.get("base_used_pct"), o.get("base_free_gb"), o.get("base_cap_gb")
    if source and source == row.get("base_source"):
        return row.get("base_used_pct"), row.get("base_free_gb"), row.get("base_cap_gb")
    return None, None, None


def over_count(st: State, row: dict, used_pct, source: str | None, sample_key, item=None) -> int:
    """這個樣本算進去後，用量「≥ 同來源基準＋門檻」連續了幾個樣本（同一來源、同一原始項目、不同資料時間才算下一個）。"""
    bu = baseline(st, row, source, item)[0]
    over = used_pct is not None and bu is not None and _r1(used_pct) >= round(bu + st.growth_pp, 1)
    prev = st.obs.get((row["id"], source or "", str(item or ""))) if source else None
    if prev and sample_key is not None and prev.get("sample_key") == str(sample_key):
        return int(prev.get("over_count") or 0)                 # 這個樣本 tick 已經算過
    if not over:
        return 0
    return (int(prev.get("over_count") or 0) if prev else 0) + 1


def check(st: State, kind: str, key: str | None, used_pct=None, free_gb=None, cap_gb=None,
          source: str | None = None, sample_key=None, item=None) -> dict | None:
    """單一判斷。回 None＝沒有例外；否則 {"exempt": bool, "id", "state": 例外／已到期／失效, "text", "reason", "expires"}。
    exempt=False 時照常提醒（到期、失效）。"""
    if not key or st.error:
        return None
    try:
        row = st.active.get((kind, key))
        if row is None:
            return None
        base = {"id": row["id"], "reason": row["reason"], "expires": row["expires"], "label": row.get("label")}
        if st.today.isoformat() >= row["expires"]:
            return {**base, "exempt": False, "state": "已到期", "text": f"例外已到期（{row['expires']}）"}
        bu, bf, bc = baseline(st, row, source, item)
        if cap_gb is not None and bc and float(cap_gb) < bc * 0.999:
            return {**base, "exempt": False, "state": "失效", "text": f"例外失效：容量從 {bc:,.0f} GB 降到 {float(cap_gb):,.0f} GB"}
        if free_gb is not None and bf and float(free_gb) <= bf * FREE_DROP:
            return {**base, "exempt": False, "state": "失效", "text": f"例外失效：剩餘從 {bf:,.0f} GB 降到 {float(free_gb):,.0f} GB（少 10% 以上）"}
        if over_count(st, row, used_pct, source, sample_key, item) >= CONSEC:
            return {**base, "exempt": False, "state": "失效", "text": f"例外失效：用量從 {bu:g}% 升到 {_r1(used_pct):g}%"}
        return {**base, "exempt": True, "state": "例外", "text": f"已知例外：{row['reason']}（到 {row['expires']}）"}
    except Exception as exc:  # noqa: BLE001 - 判斷出錯＝不算例外（照常提醒）
        log.error("已知例外判斷失敗（%s %s），照常提醒：%s", kind, key, exc)
        return None


# ===== 寫入（管理員） =====

def _log(conn, now: datetime, eid, kind, key, action, actor, detail) -> None:
    conn.execute("INSERT INTO capacity_exempt_log (at, exempt_id, kind, key, action, actor, detail) VALUES (?,?,?,?,?,?,?)",
                 (_ts(now), eid, kind, key, action, actor, detail))


def host_eos(conn, serial: str | None) -> tuple[str | None, str | None]:
    """主機的 EOS 日（作業系統與硬體取較早、只用可信的官方日期；只有年月的用該月 1 日）。回 (日期, 來源欄位說明)。"""
    if not serial:
        return None, None
    try:
        import eos
        import normalize
        r = conn.execute("SELECT os, device_model FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
        if r is None:
            return None, None
        best = (None, None)
        for kind, val in (("作業系統", r["os"]), ("硬體", r["device_model"])):
            if not val:
                continue
            inf = normalize.normalize_os(val, conn, r["device_model"]) if kind == "作業系統" else normalize.normalize_model(val, conn)
            hit = eos.lookup_os_eos(inf["canonical"]) if kind == "作業系統" else eos.lookup_hardware_eos(inf["canonical"])
            if not hit or not hit.get("eos_date") or not eos.is_trusted(hit):
                continue
            txt = str(hit["eos_date"])[:10]
            txt = txt + "-01" if re.fullmatch(r"\d{4}-\d{2}", txt) else txt
            try:
                date.fromisoformat(txt)
            except ValueError:
                continue
            if best[0] is None or txt < best[0]:
                best = (txt, f"{kind} EOS（{inf['canonical']}）")
        return best
    except Exception as exc:  # noqa: BLE001 - 查不到 EOS＝不能選「到 EOS 日」
        log.warning("查主機 EOS 失敗（%s）：%s", serial, exc)
        return None, None


def expires_for(duration, today: date, eos_date: str | None = None) -> tuple[str, str]:
    """回 (到期日, duration 字串)。不合法 → ValueError。到期日一定 > 今天、≤ 今天＋365。"""
    cap = today + timedelta(days=MAX_DAYS)
    if str(duration) == "eos":
        if not eos_date:
            raise ValueError("這一項查不到 EOS 日，不能選「到 EOS 日」")
        d = date.fromisoformat(eos_date)
        if d <= today:
            raise ValueError(f"EOS 日 {eos_date} 已過，不能選「到 EOS 日」")
        return min(d, cap).isoformat(), "eos"
    try:
        n = int(duration)
    except (TypeError, ValueError):
        raise ValueError("期限只能選 30／90／180／365 天或到 EOS 日") from None
    if n not in PRESETS:
        raise ValueError("期限只能選 30／90／180／365 天或到 EOS 日（最多 365 天，沒有永久）")
    return (today + timedelta(days=n)).isoformat(), str(n)


def set_exempt(conn, cand: dict, reason: str, duration, actor: str | None, now: datetime | None = None) -> int:
    """設定例外（不 commit）。cand＝candidates() 的一筆（伺服器算的基準，不收前端數字）。同一項已有生效中的＝錯誤。"""
    now = now or _now()
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("原因必填")
    if len(reason) > 500:
        raise ValueError("原因最多 500 字")
    exp, dur = expires_for(duration if duration not in (None, "") else DEFAULT_DAYS, now.date(), cand.get("eos_date"))
    if conn.execute("SELECT 1 FROM capacity_exempt WHERE kind = ? AND key = ? AND status = 'active'", (cand["kind"], cand["key"])).fetchone():
        raise ValueError("這一項已經是已知例外（要改請先移除再設定）")
    try:
        cur = conn.execute(
            "INSERT INTO capacity_exempt (kind, key, label, reason, duration, expires, eos_date, eos_source, base_used_pct, base_free_gb, "
            "base_cap_gb, base_source, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cand["kind"], cand["key"], cand.get("label"), reason, dur, exp, cand.get("eos_date") if dur == "eos" else None,
             cand.get("eos_source") if dur == "eos" else None, _r1(cand.get("used_pct")), cand.get("free_gb"), cand.get("cap_gb"),
             cand.get("source"), actor, _ts(now)))
    except sqlite3.IntegrityError:
        raise ValueError("這一項已經是已知例外（要改請先移除再設定）") from None
    eid = cur.lastrowid
    # 同一項之前到期／失效還沒「知道了」的：重新設定＝已處理
    conn.execute("UPDATE capacity_exempt SET acked_at = ?, acked_by = ? WHERE kind = ? AND key = ? AND status IN ('expired','void') "
                 "AND acked_at IS NULL", (_ts(now), actor, cand["kind"], cand["key"]))
    _log(conn, now, eid, cand["kind"], cand["key"], "set", actor,
         json.dumps({"reason": reason, "duration": dur, "expires": exp, "used_pct": _r1(cand.get("used_pct")),
                     "free_gb": cand.get("free_gb"), "cap_gb": cand.get("cap_gb"), "source": cand.get("source"),
                     "eos_date": cand.get("eos_date"), "eos_source": cand.get("eos_source")}, ensure_ascii=False))
    return eid


def edit(conn, eid: int, actor: str | None, reason=None, duration=None, note=None, eos: tuple | None = None,
         now: datetime | None = None) -> dict:
    """編輯生效中的例外（不 commit）：原因、期限（從今天重新算，同樣 30／90／180／365／到 EOS 日，最多 365 天）、備註。
    回 {欄位: [舊, 新]}；沒有變動＝空。每次有變動寫一筆 edit 稽核。"""
    now = now or _now()
    r = conn.execute("SELECT * FROM capacity_exempt WHERE id = ?", (eid,)).fetchone()
    if r is None:
        raise LookupError("找不到這筆例外")
    if r["status"] != "active":
        raise ValueError("只有生效中的例外可以編輯")
    ch: dict = {}
    if reason is not None:
        rs = str(reason).strip()
        if not rs:
            raise ValueError("原因必填")
        if len(rs) > 500:
            raise ValueError("原因最多 500 字")
        if rs != r["reason"]:
            ch["reason"] = [r["reason"], rs]
    if duration not in (None, ""):
        ed, es = (eos or (r["eos_date"], r["eos_source"]))
        exp, dur = expires_for(duration, now.date(), ed)
        if exp != r["expires"] or dur != r["duration"]:
            ch["expires"] = [r["expires"], exp]
            ch["duration"] = [r["duration"], dur]
            ch["eos_date"] = [r["eos_date"], ed if dur == "eos" else None]
            ch["eos_source"] = [r["eos_source"], es if dur == "eos" else None]
    if note is not None:
        nt = str(note).strip()[:500] or None
        if nt != r["note"]:
            ch["note"] = [r["note"], nt]
    if ch:
        conn.execute(f"UPDATE capacity_exempt SET {', '.join(k + ' = ?' for k in ch)} WHERE id = ?", (*[v[1] for v in ch.values()], eid))
        _log(conn, now, eid, r["kind"], r["key"], "edit", actor, json.dumps(ch, ensure_ascii=False))
    return ch


def _end(conn, eid: int, status: str, actor, note, now: datetime, action: str) -> dict:
    r = conn.execute("SELECT * FROM capacity_exempt WHERE id = ?", (eid,)).fetchone()
    if r is None:
        raise LookupError("找不到這筆例外")
    if r["status"] != "active":
        raise ValueError("這筆例外已經不是生效中")
    conn.execute("UPDATE capacity_exempt SET status = ?, ended_at = ?, ended_by = ?, end_note = ? WHERE id = ?",
                 (status, _ts(now), actor, note, eid))
    _log(conn, now, eid, r["kind"], r["key"], action, actor, note)
    return dict(r)


def remove(conn, eid: int, actor: str | None, note: str | None = None, now: datetime | None = None) -> None:
    """移除（不 commit）：隨時可以；移除後照常提醒，不進 1.4（是人自己決定的）。"""
    now = now or _now()
    _end(conn, eid, "removed", actor, (note or "").strip()[:500] or "管理員移除", now, "remove")
    conn.execute("UPDATE capacity_exempt SET acked_at = ?, acked_by = ? WHERE id = ?", (_ts(now), actor, eid))


def ack(conn, eid: int, actor: str | None, now: datetime | None = None) -> None:
    """到期／失效的「知道了」（不 commit）：1.4 不再列。"""
    now = now or _now()
    r = conn.execute("SELECT kind, key, status, acked_at FROM capacity_exempt WHERE id = ?", (eid,)).fetchone()
    if r is None:
        raise LookupError("找不到這筆例外")
    if r["status"] not in ("expired", "void") or r["acked_at"]:
        raise ValueError("只有到期或失效、還沒確認的例外可以按「知道了」")
    conn.execute("UPDATE capacity_exempt SET acked_at = ?, acked_by = ? WHERE id = ?", (_ts(now), actor, eid))
    _log(conn, now, eid, r["kind"], r["key"], "ack", actor, None)


# ===== 樣本（現在的值）：tick 與候選清單共用 =====

def samples(conn, cache: dict | None = None) -> list[dict]:
    """目前各來源的值：一筆＝{kind, key, label, used_pct, free_gb, cap_gb, source, sample_key, host_serial}。
    Datastore 來自 RVTools；主機磁碟來自已收集的檔案系統（host_spec，by=mount）與 WhatsUp 上次輪循最新完整批次。
    對不到主機的 WhatsUp 磁碟另外回在 unmapped（不套例外）。"""
    import storage_capacity as sc
    cache = cache if cache is not None else {}
    out: list[dict] = []
    ds = sc.datastores(conn, exempt_state=False)
    for v in ds["volumes"]:
        cap = v["capacity_mib"] / 1024
        out.append({"kind": KIND_DS, "key": v["id"], "label": v["name"], "used_pct": _r1(v["in_use_mib"] * 100 / v["capacity_mib"]) if v["capacity_mib"] else None,
                    "free_gb": round(v["free_mib"] / 1024, 1), "cap_gb": round(cap, 1), "source": "rvtools", "sample_key": v.get("collected_at"),
                    "item": v["id"], "room": v.get("room"), "flag": v.get("lun_why")})
    per_host: dict = {}
    for s in sc.mount_samples(conn):
        per_host.setdefault(s["asset_serial"], []).append(s)
    for serial, ms in per_host.items():
        mk = mk_of_serial(conn, serial, cache)
        ok = unique_keys((m["mount"], disk_key(mk, m["mount"])) for m in ms)
        for m in ms:
            k = disk_key(mk, m["mount"])
            if k and ok.get(k) == str(m["mount"]):
                out.append({"kind": KIND_DISK, "key": k, "label": f"{m['host']} {m['mount']}", "used_pct": m["used_pct"], "free_gb": m["free_gb"],
                            "cap_gb": m["cap_gb"], "source": "spec", "sample_key": m["collected_at"], "item": m["mount"],
                            "host_serial": serial, "ip": m["ip"]})
    lp, _unmapped = lp_samples(conn, cache)
    out.extend(lp)
    out.extend(wug_daily_samples(conn, cache))
    out.extend(vrops_samples(conn))
    return out


def wug_daily_samples(conn, cache: dict | None = None) -> list[dict]:
    """Teams 磁碟告警用的 WhatsUp 每日磁碟（relay_disk_util 最新一天的當天最高值）。對不到主機、同鍵多項的不收。"""
    cache = cache if cache is not None else {}
    try:
        import disk_alert
        raw = disk_alert._raw(conn, latest_day=True)
    except (sqlite3.Error, ImportError, AttributeError) as exc:
        log.warning("讀 WhatsUp 每日磁碟失敗（不取樣）：%s", exc)
        return []
    grp: dict = {}
    for host, day, disk, blob in raw:
        grp.setdefault((host, day), []).append((disk, blob))
    out = []
    for (host, day), items in grp.items():
        mk = mk_of_ip(conn, host, cache)
        ok = unique_keys((d, disk_key(mk, d)) for d, _b in items)
        for d, blob in items:
            k = disk_key(mk, d)
            if k and ok.get(k) == str(d):
                out.append({"kind": KIND_DISK, "key": k, "label": f"{host} {d}", "used_pct": _r1(disk_alert.day_peak(blob)), "free_gb": None,
                            "cap_gb": None, "source": "wug_daily", "sample_key": day, "item": d, "ip": host})
    return out


def vrops_keys(conn, rows: list[dict]) -> dict:
    """vROps 資料存放區（aria_dc_ds 列）→ RVTools 卷宗身分（審查 P2-2）。回 {res_id: 鍵}，對不到或不唯一的不在裡面。
    vCenter（vcid＝RVTools VI SDK UUID）＋名稱優先；RVTools 沒有 UUID 欄才退回只看名稱；兩邊任一邊同名不唯一＝不收。"""
    import storage_capacity as sc
    by_vc: dict = {}
    by_name: dict = {}
    has_uuid = False
    for v in sc.datastores(conn, exempt_state=False)["volumes"]:
        by_name.setdefault(v["name"], set()).add(v["id"])
        for u in v.get("vc_uuids") or []:
            has_uuid = True
            by_vc.setdefault((u.lower(), v["name"]), set()).add(v["id"])
    vcount: dict = {}
    ncount: dict = {}
    for d in rows:
        vcount[((d.get("vcid") or "").lower(), d.get("name"))] = vcount.get(((d.get("vcid") or "").lower(), d.get("name")), 0) + 1
        ncount[d.get("name")] = ncount.get(d.get("name"), 0) + 1
    out = {}
    for d in rows:
        nm, vc = d.get("name"), (d.get("vcid") or "").lower()
        if has_uuid and vc:
            ids = by_vc.get((vc, nm)) or set()
            if len(ids) == 1 and vcount[(vc, nm)] == 1:
                out[d["res_id"]] = next(iter(ids))
        else:
            ids = by_name.get(nm) or set()
            if len(ids) == 1 and ncount[nm] == 1:
                out[d["res_id"]] = next(iter(ids))
    return out


def vrops_samples(conn) -> list[dict]:
    """1.5 VMware 段用的 vROps 資料存放區（最新完整批次）。對不到 RVTools 身分的不收。"""
    try:
        import aria_dailycheck as ad
        b = ad.latest_complete(conn)
        if not b or not _has(conn, "aria_dc_ds"):
            return []
        rows = [dict(r) for r in conn.execute("SELECT * FROM aria_dc_ds WHERE batch_id = ?", (b["batch_id"],))]
    except (sqlite3.Error, ImportError, AttributeError) as exc:
        log.warning("讀 vROps 資料存放區失敗（不取樣）：%s", exc)
        return []
    import daily_check
    keys = vrops_keys(conn, rows)
    return [{"kind": KIND_DS, "key": keys[d["res_id"]], "label": d.get("name"), "used_pct": daily_check.used_pct_of(d.get("free_gb"), d.get("cap_gb")),
             "free_gb": d.get("free_gb"), "cap_gb": d.get("cap_gb"), "source": "vrops", "sample_key": b["batch_id"], "item": d["res_id"]}
            for d in rows if d["res_id"] in keys]


def lp_samples(conn, cache: dict | None = None) -> tuple[list[dict], list[dict]]:
    """WhatsUp 上次輪循最新完整批次的磁碟（所有群組）。回 (對得到主機的樣本, 對不到的)。"""
    cache = cache if cache is not None else {}
    try:
        import wug_lastpoll as lp
        b = lp.latest_complete(conn)
        if not b or not _has(conn, "wug_lp_value"):
            return [], []
        rows = conn.execute("SELECT DISTINCT ip, name, item, pct FROM wug_lp_value WHERE batch_id = ? AND metric = 'disk'",
                            (b["batch_id"],)).fetchall()
    except sqlite3.Error:
        return [], []
    ok, bad, seen = [], [], set()
    uniq: dict = {}
    by_ip: dict = {}
    for r in rows:
        by_ip.setdefault(r["ip"], set()).add(r["item"])
    for ip, items in by_ip.items():
        mk = mk_of_ip(conn, ip, cache)
        uniq[ip] = unique_keys((it, disk_key(mk, it)) for it in items)
    for r in rows:
        if (r["ip"], r["item"]) in seen:
            continue
        seen.add((r["ip"], r["item"]))
        k = disk_key(mk_of_ip(conn, r["ip"], cache), r["item"])
        x = {"kind": KIND_DISK, "key": k, "label": f"{r['name'] or r['ip']} {r['item']}", "used_pct": _r1(r["pct"]), "free_gb": None,
             "cap_gb": None, "source": "whatsup", "sample_key": b["batch_id"], "ip": r["ip"], "item": r["item"]}
        if k is None:
            bad.append(x)
        elif uniq[r["ip"]].get(k) == str(r["item"]):
            ok.append(x)
    return ok, bad


def candidates(conn, now: datetime | None = None) -> dict:
    """設定畫面用：可以設例外的項目（伺服器算的基準）＋對不到主機的 WhatsUp 磁碟。主機磁碟附 EOS 日與來源欄位。"""
    cache: dict = {}
    smp = samples(conn, cache)
    _ok, unmapped = lp_samples(conn, cache)
    seen, out = set(), []
    eos_cache: dict = {}
    serial_of_mk: dict = {}
    for s in smp:
        if s.get("host_serial"):
            serial_of_mk[s["key"].split("::", 1)[0]] = s["host_serial"]
    for s in smp:
        if (s["kind"], s["key"]) in seen:          # 同一顆碟有兩個來源：先列的（已收集檔案系統，有剩餘／容量）當基準
            continue
        seen.add((s["kind"], s["key"]))
        if s["kind"] == KIND_DISK:
            serial = s.get("host_serial") or serial_of_mk.get(s["key"].split("::", 1)[0]) or _serial_of_ip(conn, s.get("ip"))
            if serial not in eos_cache:
                eos_cache[serial] = host_eos(conn, serial)
            s = {**s, "eos_date": eos_cache[serial][0], "eos_source": eos_cache[serial][1]}
        out.append(s)
    return {"rows": out, "unmapped": unmapped}


def _serial_of_ip(conn, ip) -> str | None:
    if not ip:
        return None
    import relay_import
    try:
        res, _n, serial = relay_import.match_asset(conn, str(ip).strip())
    except sqlite3.Error:
        return None
    return serial if res == "matched" else None


# ===== tick：記樣本、到期／失效轉狀態、寫稽核 =====

_last_tick: dict = {}


def tick(conn, now: datetime | None = None, force: bool = False) -> dict:
    """排程（v2_today.tick 每分鐘叫，這裡每 10 分鐘才真的跑）：自己 commit。表不在就不動作。"""
    now = now or _now()
    if not force and _last_tick.get("at") and (now - _last_tick["at"]).total_seconds() < TICK_MIN * 60:
        return {"status": "idle"}
    _last_tick["at"] = now
    if not _has(conn, "capacity_exempt"):
        return {"status": "no_table"}
    st = load(conn, now)
    if st.error or not st.active:
        return {"status": "none"}
    by_key: dict = {}
    for s in samples(conn):
        by_key.setdefault((s["kind"], s["key"]), []).append(s)
    n_exp = n_void = 0
    for (kind, key), row in st.active.items():
        try:
            if now.date().isoformat() >= row["expires"]:
                _end(conn, row["id"], "expired", "系統", f"到期（{row['expires']}）", now, "expire")
                n_exp += 1
                continue
            voided = None
            for s in by_key.get((kind, key), []):
                item = str(s.get("item") or "")
                r = check(st, kind, key, s["used_pct"], s["free_gb"], s["cap_gb"], s["source"], s["sample_key"], item)
                prev = st.obs.get((row["id"], s["source"], item))
                if not (prev and s["sample_key"] is not None and prev.get("sample_key") == str(s["sample_key"])):
                    cnt = over_count(st, row, s["used_pct"], s["source"], s["sample_key"], item)
                    bu, bf, bc = baseline(st, row, s["source"], item)
                    if bu is None and bf is None and bc is None:      # 這個來源第一次：這一筆就是它的基準
                        bu, bf, bc, cnt = s["used_pct"], s["free_gb"], s["cap_gb"], 0
                    conn.execute(
                        "INSERT INTO capacity_exempt_obs (exempt_id, source, item, base_used_pct, base_free_gb, base_cap_gb, sample_key, over_count, "
                        "used_pct, at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(exempt_id, source, item) DO UPDATE SET "
                        "sample_key = excluded.sample_key, over_count = excluded.over_count, used_pct = excluded.used_pct, at = excluded.at",
                        (row["id"], s["source"], item, bu, bf, bc, None if s["sample_key"] is None else str(s["sample_key"]), cnt,
                         s["used_pct"], _ts(now)))
                if r and r["state"] == "失效" and voided is None:
                    voided = r["text"]
            if voided:
                _end(conn, row["id"], "void", "系統", voided, now, "void")
                n_void += 1
        except (ValueError, LookupError) as exc:     # 審查 P3-5：同時被人移除／改狀態：跳過這筆，不整批回滾
            log.info("已知例外 %s 狀態已變（略過）：%s", row["id"], exc)
    conn.commit()
    return {"status": "done", "expired": n_exp, "void": n_void}


# ===== 畫面 =====

def notices(conn) -> list[dict]:
    """1.4 追蹤處理要列一次的：到期或失效、還沒有人按「知道了」或重新設定。"""
    try:
        if not _has(conn, "capacity_exempt"):
            return []
        return [dict(r) for r in conn.execute("SELECT * FROM capacity_exempt WHERE status IN ('expired','void') AND acked_at IS NULL ORDER BY id")]
    except sqlite3.Error as exc:
        log.error("讀取已到期／失效例外失敗：%s", exc)
        return []


def legacy(conn) -> list[dict]:
    """1.5 舊的「單台磁碟門檻調整」（daily_check disk_exceptions）：照舊生效，這裡只列出來讓管理員自己決定要不要改成例外。"""
    try:
        import daily_check
        return [{**x, "type": "舊門檻調整"} for x in daily_check.settings(conn).get("disk_exceptions") or []]
    except Exception as exc:  # noqa: BLE001 - 列不出來不影響判斷
        log.warning("讀 1.5 舊門檻調整失敗：%s", exc)
        return []


def overview(conn, now: datetime | None = None) -> dict:
    now = now or _now()
    st = load(conn, now)
    rows, logs = [], []
    if _has(conn, "capacity_exempt"):
        rows = [dict(r) for r in conn.execute("SELECT * FROM capacity_exempt ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id DESC LIMIT 500")]
        logs = [dict(r) for r in conn.execute("SELECT * FROM capacity_exempt_log ORDER BY id DESC LIMIT 300")]
    cand = candidates(conn, now)
    return {"rows": rows, "log": logs, "legacy": legacy(conn), "candidates": cand["rows"], "unmapped": cand["unmapped"],
            "presets": list(PRESETS), "default_days": DEFAULT_DAYS, "max_days": MAX_DAYS, "growth_pp": st.growth_pp,
            "free_drop_pct": round((1 - FREE_DROP) * 100), "consec": CONSEC, "error": st.error, "today": now.date().isoformat()}
