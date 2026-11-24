"""WhatsUp 磁碟使用率 → Teams 告警（中繼匯入每日資料用）。

流程：中繼匯入的 WhatsUp 批次**成功寫入**後（relay_import.import_batch 提交之後），評估這一批寫到 relay_disk_util 的每一台＋每顆磁碟＋每一天：
  * 兩級（2.049.0，使用者 2026-11-05 定）：取**當天最高值**（max 序列的最大值；沒有 max 才用 avg），
    ≥ 緊急門檻（預設 90）列「緊急」、≥ 準備門檻（預設 80）列「準備」。兩個門檻都 50–100、準備 < 緊急。
    舊設定只有單一 `threshold`：當成緊急門檻，準備用預設 80（緊急 ≤ 80 時退成「緊急 − 5」，不低於 50）。
  * 暴增：同一份序列（max 有就用 max，否則 avg，與當天最高值同一個選法），任一段 ≤ N 小時（預設 5）內上升 ≥ P 個百分點（預設 15），
    且上升終點的值 ≥ 下限（預設 85）就列「暴增」。為了不漏掉跨 UTC 午夜（台北 08:00）的上升，會接上同台同碟前一天的點，
    但只算「終點落在這一天」的上升（避免同一段上升算兩天）。
  * 去重（disk_alert_seen）：同一台＋同一顆磁碟＋同一天＋同一級只告警一次。當天已發過「準備」、之後同一天升到「緊急」會再發一次（緊急）；
    已發過緊急的不再補發準備。暴增另一個級別、同台同碟同天一次。
  * 每次評估每一級最多送**一則** Teams 訊息，順序：緊急（severity critical）→ 準備（warning）→ 暴增（warning；終點 ≥ 緊急門檻就 critical）。
    緊急／準備一台一行（多顆超標時寫最高那顆＋「等 N 顆」）；暴增一顆一行；最多 30 行，其餘寫「另有 N 台／顆」。
  * 送出走 teams_notify 的既有佇列（啟用開關、Workflow 網址、對外送出已確認、登入停用 fail-closed、重試／不確定、投遞紀錄全部沿用）。
    Teams 通知沒啟用時只記錄（歷史可查），不送。
  * 主機名：用 IP 對資產（同一台以 machine_key 判斷，沿用 relay_import.match_asset）；對不到就顯示 IP。
CPU／記憶體告警這次不做。

盤中靜默窗：這裡只把訊息送到 Teams，不連任何主機、不跑任何收集，所以**不受**平日 08:30–15:00 靜默窗限制
（匯入本身由 relay_import.tick 守門；人工「範例」按鈕也只是送一則 Teams 訊息）。

資料函式不自行 commit 的：save_settings（路由與稽核同一交易）。evaluate 由匯入後的掛勾呼叫，自己 commit（獨立於匯入交易，失敗不影響匯入結果）。
"""
from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from collections import deque
from datetime import date, datetime, timedelta, timezone

SETTING_KEY = "teams_disk_alert"
import disk_level  # noqa: E402 - 預設門檻＝全站磁碟一把尺：「處理」80%＝準備、「高」90%＝緊急

DEFAULTS = {"enabled": False, "warn": int(disk_level.HANDLE_PCT), "critical": int(disk_level.HIGH_PCT),
            "surge_hours": 5, "surge_rise": 15, "surge_floor": 85,
            # 2.060.19（S6）：假磁碟排除（磁碟名稱含任一段文字就不算、不告警）。交換器的 devfs／套件映像永遠 100%
            "exclude": ["devfs", "/packages/mnt/"]}
CRITICAL, WARN, SURGE = "critical", "warn", "surge"
TIER_LABEL = {CRITICAL: "緊急", WARN: "準備", SURGE: "暴增"}
MAX_LINES = 30
HISTORY_KEEP = 200
#: 只有「資料日期 ≥ 今天（台北）− STALE_DAYS」才發 Teams；更早的（補匯入的舊批次）照樣記進去重表、不發。
#: 起因 2026-11-11：11-10 傍晚補傳約 25 天的舊批次，每天各發一則，5 分鐘內洗了 26 則。
#: 2 天＝WhatsUp 每日批次的資料日期是 UTC，台北 15:30 收的那批常落在「昨天」，留一天餘裕。
STALE_DAYS = 2
TPE = timezone(timedelta(hours=8))
log = logging.getLogger("disk_alert")

SQL = [
    """CREATE TABLE IF NOT EXISTS disk_alert_seen (
        host TEXT NOT NULL, disk TEXT NOT NULL, day TEXT NOT NULL,      -- host＝去重身分：'mk:<machine_key>' 或 'ip:<IP>'（見 ident_of）
        tier TEXT NOT NULL DEFAULT 'critical',                          -- critical／warn／surge（2.049.0）
        pct REAL, batch_id TEXT, first_at TEXT NOT NULL,
        PRIMARY KEY (host, disk, day, tier))""",
    """CREATE TABLE IF NOT EXISTS disk_alert_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,                 -- alert（匯入後評估）／sample（人工範例）／stale（補匯入的舊資料，只記不發，2.060.17）
        batch_id TEXT,
        threshold REAL,
        hosts INTEGER, items INTEGER,
        status TEXT NOT NULL,               -- queued（已排進 Teams 佇列）／recorded（Teams 未啟用，只記錄）／sample
        delivery_key TEXT,                  -- teams_notify_delivery.event_key（可查送出結果）
        summary TEXT,
        tier TEXT)                          -- critical／warn／surge；範例為 NULL（2.049.0）""",
]


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def ensure(conn: sqlite3.Connection) -> None:
    """建表＋把 2.048 以前的表升級（不 commit；呼叫端的交易一起提交）。
    舊 disk_alert_seen 的主鍵沒有級別：改名→建新表→舊列一律當「緊急」搬過去（舊版只有一個門檻、預設 90）→丟掉舊表。"""
    if _has(conn, "disk_alert_seen") and "tier" not in _cols(conn, "disk_alert_seen"):
        conn.execute("ALTER TABLE disk_alert_seen RENAME TO disk_alert_seen_v1")
        conn.execute(SQL[0])
        conn.execute("INSERT OR IGNORE INTO disk_alert_seen (host, disk, day, tier, pct, batch_id, first_at) "
                     "SELECT host, disk, day, 'critical', pct, batch_id, first_at FROM disk_alert_seen_v1")
        conn.execute("DROP TABLE disk_alert_seen_v1")
    for s in SQL:
        conn.execute(s)
    if "tier" not in _cols(conn, "disk_alert_history"):
        conn.execute("ALTER TABLE disk_alert_history ADD COLUMN tier TEXT")


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


# ── 設定 ───────────────────────────────────────────────────────────────
def _legacy_warn(critical) -> float:
    """舊單一門檻升級時的準備門檻：預設 80；緊急 ≤ 80 時退成緊急 − 5（不低於 50）。"""
    try:
        c = float(critical)
    except (TypeError, ValueError):
        return DEFAULTS["warn"]
    w = DEFAULTS["warn"] if c > DEFAULTS["warn"] else max(50.0, c - 5)
    return int(w) if float(w).is_integer() else w


def _upgrade(saved: dict) -> dict:
    """舊設定（只有 threshold）→ 新鍵。新鍵已經有的不動。"""
    out = dict(saved)
    if "critical" not in out and "threshold" in out:
        out["critical"] = out["threshold"]
        out.setdefault("warn", _legacy_warn(out["threshold"]))
    out.pop("threshold", None)
    return out


def load_settings(conn) -> dict:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    cfg = dict(DEFAULTS)
    if row and row[0]:
        try:
            saved = json.loads(row[0]) or {}
        except (ValueError, TypeError) as exc:
            raise ValueError(f"磁碟告警設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}") from exc
        if not isinstance(saved, dict):
            raise ValueError("磁碟告警設定讀不出來（內容壞掉，不是沒設定）：格式不對")
        cfg.update({k: v for k, v in _upgrade(saved).items() if k in DEFAULTS})
    return cfg


_RANGES = {   # 鍵: (畫面名稱, 下限, 上限)
    "warn": ("準備門檻", 50, 100),
    "critical": ("緊急門檻", 50, 100),
    "surge_hours": ("暴增時間窗（小時）", 0.5, 24),
    "surge_rise": ("暴增上升幅度（百分點）", 1, 100),
    "surge_floor": ("暴增下限", 50, 100),
}


def _num(key: str, v):
    name, lo, hi = _RANGES[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise ValueError(f"{name}要是 {lo:g}～{hi:g} 的數字")
    if not lo <= float(v) <= hi:
        raise ValueError(f"{name}要在 {lo:g}～{hi:g} 之間")
    return int(v) if float(v).is_integer() else round(float(v), 1)


def validate(cfg: dict) -> dict:
    cfg = _upgrade(cfg)
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    for k in _RANGES:
        out[k] = _num(k, out[k])
    if not float(out["warn"]) < float(out["critical"]):
        raise ValueError("準備門檻要小於緊急門檻")
    ex = out["exclude"]
    if isinstance(ex, str):
        ex = ex.splitlines()
    if not isinstance(ex, list) or any(not isinstance(x, str) for x in ex):
        raise ValueError("排除清單要是一行一段文字")
    ex = list(dict.fromkeys(x.strip() for x in ex if x and x.strip()))
    if len(ex) > 50 or any(len(x) > 80 for x in ex):
        raise ValueError("排除清單最多 50 行、每行 80 字以內")
    out["exclude"] = ex
    out["enabled"] = bool(out["enabled"])
    return out


def save_settings(conn, cfg: dict) -> dict:
    """存設定。**不 commit**（路由與稽核同一交易）。"""
    out = validate(cfg)
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SETTING_KEY, json.dumps(out, ensure_ascii=False)))
    return out


# ── 資料 ───────────────────────────────────────────────────────────────
def _decode(blob) -> dict | None:
    try:
        d = json.loads(gzip.decompress(blob).decode())
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def _pick(d: dict) -> list:
    """max 序列有數值就用 max，否則用 avg（day_peak 與暴增同一個選法）。"""
    mx = d.get("max") or []
    if any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in mx):
        return mx
    return d.get("avg") or []


def day_peak(blob) -> float | None:
    """relay_disk_util 一列的當天最高值：max 序列最大；沒有 max 才用 avg。壞資料回 None（不猜）。"""
    d = _decode(blob)
    if d is None:
        return None
    vals = [v for v in _pick(d) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(max(vals), 1) if vals else None


def series(blob, day: str) -> list[tuple[datetime, float]]:
    """一列的時間序列 [(UTC 時間, 值)]，時間排序；壞資料回 []。ts 是 UTC 的 HH:MM:SS，day 是 UTC 日期（relay_import 的存法）。"""
    d = _decode(blob)
    if d is None:
        return []
    vals = _pick(d)
    out = []
    for i, t in enumerate(d.get("ts") or []):
        v = vals[i] if i < len(vals) else None
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        try:
            at = datetime.fromisoformat(f"{day}T{t}").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        out.append((at, float(v)))
    out.sort(key=lambda x: x[0])
    return out


def detect_surge(points: list[tuple[datetime, float]], hours: float, rise: float, floor: float,
                 since: datetime | None = None) -> dict | None:
    """任一段 ≤ hours 小時內，後面的點比前面的點高 ≥ rise 個百分點、且後面那點 ≥ floor → 回上升最大的那段（同幅度取最早）。
    since：只算終點 ≥ since 的上升（接了前一天的點時，避免同一段上升算兩天）。滑動視窗最小值，O(n)。"""
    win = timedelta(hours=float(hours))
    q: deque[int] = deque()        # 視窗內的索引，值遞增（隊首＝視窗內最低點）
    best = None
    for j, (tj, vj) in enumerate(points):
        while q and tj - points[q[0]][0] > win:
            q.popleft()
        if q and (since is None or tj >= since) and round(vj, 6) >= floor:
            i = q[0]
            r = round(vj - points[i][1], 6)
            if r >= rise and (best is None or r > best["rise"]):
                best = {"from_t": points[i][0], "from_v": points[i][1], "to_t": tj, "to_v": vj, "rise": r}
        while q and points[q[-1]][1] >= vj:
            q.pop()
        q.append(j)
    return best


def host_label(conn, ip: str) -> str:
    """IP → 資產主機名（同一台以 machine_key 判斷）；對不到或多重對應就回 IP。"""
    import relay_import
    try:
        res, _n, serial = relay_import.match_asset(conn, ip)
        if res == "matched" and serial:
            r = conn.execute("SELECT hostname FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
            if r and (r[0] or "").strip():
                return f"{r[0].strip()}（{ip}）"
    except sqlite3.Error:
        pass                # 查不到主機名就顯示 IP，不影響告警
    return ip


def ident_of(conn, ip: str) -> str:
    """去重用的「同一台」身分：IP 對得到資產就用該資產的 machine_key（'mk:<key>'，同一台換了 IP 仍是同一台）；
    對不到或多重對應就用 'ip:<IP>'。"""
    import relay_import
    import system_stats
    try:
        res, _n, serial = relay_import.match_asset(conn, ip)
        if res == "matched" and serial:
            r = conn.execute("SELECT hostname, ip FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
            if r is not None:
                return "mk:" + system_stats.machine_key(r[0], r[1], serial)
    except sqlite3.Error:
        pass                # 查不到就退回 IP（寧可多告警一次，也不要漏）
    return "ip:" + ip


def excluded(disk, pats: list[str]) -> bool:
    """磁碟名稱含排除清單任一段（不分大小寫）＝假磁碟（例：交換器 devfs）。"""
    d = str(disk or "").lower()
    return any(p.lower() in d for p in pats or [])


def exclude_list(conn) -> list[str]:
    try:
        return list(load_settings(conn).get("exclude") or [])
    except ValueError:
        return list(DEFAULTS["exclude"])   # 設定壞掉時仍排除預設的假磁碟（不讓 devfs 100% 洗版）


def _raw(conn, batch_id: str | None = None, latest_day: bool = False) -> list[tuple]:
    """(host, day, disk, data) 列。排除清單裡的假磁碟不回。"""
    if not _has(conn, "relay_disk_util"):
        return []
    if latest_day:
        d = conn.execute("SELECT MAX(day) FROM relay_disk_util").fetchone()[0]
        if not d:
            return []
        rs = conn.execute("SELECT host, day, disk, data FROM relay_disk_util WHERE day = ?", (d,)).fetchall()
    else:
        rs = conn.execute("SELECT host, day, disk, data FROM relay_disk_util WHERE batch_id = ?", (batch_id,)).fetchall()
    pats = exclude_list(conn)
    return [r for r in rs if not excluded(r[2], pats)]


def _rows(conn, batch_id: str | None = None, latest_day: bool = False) -> list[tuple[str, str, str, float]]:
    out = []
    for r in _raw(conn, batch_id, latest_day):
        p = day_peak(r[3])
        if p is not None:
            out.append((r[0], r[2], r[1], p))
    return out


def surges(conn, raw: list[tuple], cfg: dict) -> list[dict]:
    """這幾列裡的暴增（每台每碟每天最多一筆）。接同台同碟前一天的點，只算終點在當天的上升。"""
    out = []
    for host, day, disk, blob in raw:
        pts = series(blob, day)
        if not pts:
            continue
        try:
            prev_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        except ValueError:
            continue
        prev = conn.execute("SELECT data FROM relay_disk_util WHERE host = ? AND day = ? AND disk = ?", (host, prev_day, disk)).fetchone()
        since = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        allp = (series(prev[0], prev_day) if prev else []) + pts
        hit = detect_surge(allp, cfg["surge_hours"], cfg["surge_rise"], cfg["surge_floor"], since=since)
        if hit:
            out.append({"host": host, "disk": disk, "day": day, **hit})
    return out


def _tpe(t: datetime, fmt: str) -> str:
    return t.astimezone(TPE).strftime(fmt)


def surge_lines(conn, items: list[dict], limit: int | None = MAX_LINES) -> tuple[list[str], int]:
    """暴增一顆一行，上升幅度大的在上；時間以台北時間顯示。回 (行, 超出的顆數)。limit=None＝全部。"""
    order = sorted(items, key=lambda x: (-x["rise"], x["host"], x["disk"]))
    lines = []
    for x in order[:limit]:
        same = _tpe(x["from_t"], "%Y-%m-%d") == _tpe(x["to_t"], "%Y-%m-%d")
        when = f"{_tpe(x['from_t'], '%m-%d %H:%M')}→{_tpe(x['to_t'], '%H:%M' if same else '%m-%d %H:%M')}"
        lines.append(f"{host_label(conn, x['host'])}｜{x['disk']}｜{round(x['from_v'], 1):g}%→{round(x['to_v'], 1):g}%｜{when}")
    return lines, max(0, len(order) - (limit or len(order)))


def _person(conn, serials: list[str], name: str | None) -> str:
    """「姓名 電話 email」：電話取 CIA 人員表（同一台、同名）、email 取 AD（同名不猜）；查不到寫「未登記」。"""
    n = (name or "").strip()
    if not n:
        return "未登記"
    phone = None
    try:
        ph = ",".join("?" for _ in serials)
        r = conn.execute(f"SELECT phone FROM personnel WHERE asset_serial IN ({ph}) AND TRIM(person_name) = ? "
                         "AND TRIM(COALESCE(phone,'')) <> '' LIMIT 1", (*serials, n)).fetchone()
        phone = r[0].strip() if r else None
    except sqlite3.Error:
        phone = None
    mail = None
    try:
        import eos_notify as en
        p, _why, _via = en.resolve_owner(conn, n)
        mail = (p.get("mail") or "").strip() if p else None
    except sqlite3.Error:
        mail = None
    return f"{n} {phone or '電話未登記'} {mail or 'email 未登記'}"


def env_of(conn, ip: str) -> str | None:
    """IP 對到資產的環境別（同一台每筆登記取第一個有填的，正規化後），對不到或沒填回 None。
    使用者 11-12：測試機、備援機的磁碟緊急不要立即推（改進 08:00 彙整）；查不到環境的照當正式推，不藏告警。"""
    import relay_import
    import system_stats
    import manage_state
    try:
        res, _n, serial = relay_import.match_asset(conn, ip)
        if res != "matched" or not serial:
            return None
        sers = manage_state.same_machine_serials(conn, serial) or [serial]
        ph = ",".join("?" for _ in sers)
        for r in conn.execute(f"SELECT environment FROM hardware WHERE asset_serial IN ({ph}) ORDER BY asset_serial = ? DESC",
                              [*sers, serial]):
            v = (r["environment"] or "").strip()
            if v:
                return system_stats.normalize_env(v)
    except sqlite3.Error:
        return None
    return None


def is_prod(conn, ip: str) -> bool:
    """非正式＝環境明確登記且不是「正式」（測試、備援…）。沒登記、對不到 → 當正式（寧可多推，不藏）。"""
    e = env_of(conn, ip)
    return e is None or e in ("正式", "未填")


def contact_text(conn, ip: str) -> str:
    """使用者 11-09：磁碟告警要帶系統名稱、AP／SP 負責人（電話、email）、正式或測試、機房。
    WhatsUp 只有主機名與用量，這些都從本系統的資產資料查（IP → 資產；對不到就明講，不猜）。"""
    import relay_import
    import system_stats
    try:
        res, _n, serial = relay_import.match_asset(conn, ip)
        if res != "matched" or not serial:
            return "資產對不到這個 IP，系統與負責人無從查起"
        hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
        if hw is None:
            return "資產對不到這個 IP，系統與負責人無從查起"
        # 同一台（machine_key）的每一筆登記都看：系統／負責人可能登在另一筆（審查 P2）。對到的那筆優先。
        import manage_state
        sers = manage_state.same_machine_serials(conn, serial) or [serial]
        ph = ",".join("?" for _ in sers)
        others = [r for r in conn.execute(f"SELECT * FROM hardware WHERE asset_serial IN ({ph})", sers)
                  if r["asset_serial"] != serial]
        rows = [hw] + others
        serials = [r["asset_serial"] for r in rows]

        def first(col):
            return next(((r[col] or "").strip() for r in rows if (r[col] or "").strip()), "")
        apis = sorted({(r["api_id"] or "").strip() for r in rows if (r["api_id"] or "").strip()})
        names, ap_owner = [], None
        for a in apis:
            b = conn.execute("SELECT name, ap_owner FROM business_system WHERE api_id = ?", (a,)).fetchone()
            names.append(f"{a} {b['name']}" if b and b["name"] else a)
            ap_owner = ap_owner or (b["ap_owner"] if b else None)
        env = system_stats.normalize_env(first("environment")) or "環境未登記"
        room = system_stats.normalize_location(first("physical_location")) or "機房未登記"
        user = first("user_name")
        ap_txt = _person(conn, serials, ap_owner) if ap_owner else (f"未登記（使用者 {user}）" if user else "未登記")
        return (f"系統：{'、'.join(names) or '未登記'}｜{env}｜{room}｜"
                f"AP：{ap_txt}｜SP：{_person(conn, serials, first('owner'))}")
    except sqlite3.Error:
        return "聯絡資訊查詢失敗（告警照發）"


def build_lines(conn, items: list[tuple[str, str, str, float]], limit: int | None = MAX_LINES) -> tuple[list[str], int]:
    """一台一行（最高那顆＋等 N 顆），依最高值由大到小；最多 limit 行（None＝全部）。回 (行, 超出的台數)。"""
    by: dict[str, list] = {}
    for host, disk, day, pct in items:
        by.setdefault(host, []).append((pct, disk, day))
    order = sorted(by.items(), key=lambda kv: (-max(x[0] for x in kv[1]), kv[0]))
    lines = []
    for host, ds in order[:limit]:
        ds.sort(reverse=True)
        pct, disk, day = ds[0]
        more = f"，等 {len(ds)} 顆" if len(ds) > 1 else ""
        # 一台仍是一行（摘要／歷史一行一台），聯絡資訊接在同一行後面
        lines.append(f"{host_label(conn, host)}｜{disk}｜{pct:g}%｜{day}{more}｜{contact_text(conn, host)}")
    return lines, max(0, len(order) - (limit or len(order)))


def build_card(title: str, lines: list[str], extra: int, note: str, color: str = "Warning",
               sections: list[tuple[str, list[str], str]] | None = None, extra_unit: str = "台") -> dict:
    """自己組卡片（每行一個 TextBlock，文字一律經 teams_notify.md_escape），因為 FactSet 只放得下 25 列。
    sections：[(小標, 行, 結尾「另有…」或空字串)]，接在 lines 之後（範例卡片用）。"""
    import teams_notify as tn
    body = [{"type": "TextBlock", "text": tn.md_escape(title)[:240], "weight": "Bolder", "size": "Medium", "color": color, "wrap": True},
            {"type": "TextBlock", "text": tn.md_escape(note)[:400], "isSubtle": True, "wrap": True}]
    body += [{"type": "TextBlock", "text": tn.md_escape(x)[:600], "wrap": True, "spacing": "None"} for x in lines]
    if extra:
        body.append({"type": "TextBlock", "text": tn.md_escape(f"另有 {extra} {extra_unit}"), "wrap": True})
    for head, ls, tail in sections or []:
        body.append({"type": "TextBlock", "text": tn.md_escape(head)[:240], "weight": "Bolder", "wrap": True})
        body += [{"type": "TextBlock", "text": tn.md_escape(x)[:300], "wrap": True, "spacing": "None"} for x in ls]
        if tail:
            body.append({"type": "TextBlock", "text": tn.md_escape(tail), "wrap": True})
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": body}}]}


def _history(conn, now_s: str, kind: str, batch_id, thr, hosts: int, items: int, status: str, key, summary: str, tier: str | None = None) -> None:
    conn.execute("INSERT INTO disk_alert_history (at, kind, batch_id, threshold, hosts, items, status, delivery_key, summary, tier) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (now_s, kind, batch_id, thr, hosts, items, status, key, summary[:2000], tier))
    conn.execute(f"DELETE FROM disk_alert_history WHERE id NOT IN (SELECT id FROM disk_alert_history ORDER BY id DESC LIMIT {HISTORY_KEEP})")


def _mark(conn, ident: str, disk: str, day: str, tier: str, pct, batch_id: str, now_s: str) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO disk_alert_seen (host, disk, day, tier, pct, batch_id, first_at) VALUES (?,?,?,?,?,?,?)",
                       (ident, disk, day, tier, pct, batch_id, now_s))
    return cur.rowcount == 1


def _seen(conn, ident: str, disk: str, day: str, tier: str) -> bool:
    return conn.execute("SELECT 1 FROM disk_alert_seen WHERE host=? AND disk=? AND day=? AND tier=?", (ident, disk, day, tier)).fetchone() is not None


# ── 評估（匯入成功後呼叫）─────────────────────────────────────────────
def evaluate(conn, batch_id: str, now: datetime | None = None) -> dict:
    """評估這一批寫進 relay_disk_util 的資料。關閉時不動作。自己 commit；任何錯誤由呼叫端吞掉並記日誌（不影響匯入）。
    回 {"status": none／recorded／queued, "cards": [{tier, hosts, items, key}]}。"""
    import teams_notify as tn
    cfg = load_settings(conn)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    now = now or tn.now_taipei()
    now_s = tn._ts(now)
    crit, warn = float(cfg["critical"]), float(cfg["warn"])
    ensure(conn)
    raw = _raw(conn, batch_id)
    cutoff = (now.date() - timedelta(days=STALE_DAYS)).isoformat()
    new: dict[str, list] = {CRITICAL: [], WARN: []}
    stale = 0
    # 已知例外（capacity_exempt，2026-11-14）：machine_key（IP 唯一對到資產）＋磁碟名稱；生效中的不發、不記去重。對不到／讀不到＝照發
    import capacity_exempt as ce
    ex_st, mkc = ce.load(conn), {}

    _items: dict = {}
    for h_, d_, k_, _b in raw:
        _items.setdefault((h_, d_), set()).add(k_)
    _uniq: dict = {}

    def _exempt(ip, dk, pct, day) -> bool:
        if not ex_st.active:
            return False
        mk = ce.mk_of_ip(conn, ip, mkc)
        if (ip, day) not in _uniq:          # 同一台同一天兩顆碟正規化成同一鍵＝分不出來，不套（審查 P2-1）
            _uniq[(ip, day)] = ce.unique_keys((x, ce.disk_key(mk, x)) for x in _items.get((ip, day), {dk}))
        k = ce.disk_key(mk, dk)
        k = k if k and _uniq[(ip, day)].get(k) == str(dk) else None
        ex = ce.check(ex_st, ce.KIND_DISK, k, pct, None, None, "wug_daily", day, dk)
        return bool(ex and ex["exempt"])
    n_exempt = 0
    for host, day, disk, blob in sorted(raw, key=lambda r: -(day_peak(r[3]) or 0)):
        pct = day_peak(blob)
        if pct is None or pct < warn:
            continue
        if _exempt(host, disk, pct, day):
            n_exempt += 1
            continue
        ident = ident_of(conn, host)
        fresh = str(day) >= cutoff   # 舊資料照樣記去重（之後不會再發），只是不進這次的訊息
        if pct >= crit:
            # 去重鍵用資產的 machine_key（同一台以不同 IP 匯入也不重發）；訊息行仍顯示主機名／IP。當天發過準備的，升到緊急會再發
            if _mark(conn, ident, disk, day, CRITICAL, pct, batch_id, now_s):
                if fresh:
                    new[CRITICAL].append((host, disk, day, pct))
                else:
                    stale += 1
        elif not _seen(conn, ident, disk, day, CRITICAL) and _mark(conn, ident, disk, day, WARN, pct, batch_id, now_s):
            if fresh:
                new[WARN].append((host, disk, day, pct))
            else:
                stale += 1
    new_surge = []
    for x in surges(conn, raw, cfg):
        if _exempt(x["host"], x["disk"], x["to_v"], x["day"]):
            n_exempt += 1
            continue
        if _mark(conn, ident_of(conn, x["host"]), x["disk"], x["day"], SURGE, round(x["to_v"], 1), batch_id, now_s):
            if str(x["day"]) >= cutoff:
                new_surge.append(x)
            else:
                stale += 1
    if stale:
        _history(conn, now_s, "stale", batch_id, None, 0, stale, "stale", None,
                 f"資料日期早於 {cutoff}（補匯入的舊批次）：{stale} 筆超標只記錄、不發 Teams")

    # 2.060.x 新格式（notice_fmt）：第一行燈號＋級別＋什麼事＋台數，一台一行最多 5 行＋「另有 N 台」，不放資料來源說明
    import notice_fmt as nf
    link = nf.link_of(conn, "/v2/storage")
    cards = []
    for tier, thr, sev, color in ((CRITICAL, cfg["critical"], "critical", "Attention"), (WARN, cfg["warn"], "warning", "Warning")):
        items = new[tier]
        if not items:
            continue
        all_lines, _ = build_lines(conn, items, None)
        lines = nf.cap(all_lines)
        hosts = len({x[0] for x in items})
        title = nf.head(nf.EMOJI[sev], TIER_LABEL[tier], f"磁碟使用率 ≥{thr}%", hosts)
        cards.append((tier, float(thr), sev, title, nf.card(title, lines, link, color=color), lines, hosts, len(items), all_lines))
        if tier == CRITICAL:
            # 2.061.13（使用者 11-12）：測試／備援等非正式機的緊急不立即推，跟正式機分開、改進 08:00 彙整
            np_items = [x for x in items if not is_prod(conn, x[0])]
            if np_items:
                cards.pop()
                for part, psev in (([x for x in items if x not in np_items], sev), (np_items, "warning")):
                    if not part:
                        continue
                    pl, _ = build_lines(conn, part, None)
                    ph = len({x[0] for x in part})
                    lab = TIER_LABEL[tier] if psev == sev else f"{TIER_LABEL[tier]}（非正式機）"
                    pt = nf.head(nf.EMOJI[psev], lab, f"磁碟使用率 ≥{thr}%", ph)
                    cards.append((tier, float(thr), psev, pt, nf.card(pt, nf.cap(pl), link, color=color if psev == sev else "Warning"),
                                  nf.cap(pl), ph, len(part), pl))
    if new_surge:
        all_lines, _ = surge_lines(conn, new_surge, None)
        lines = nf.cap(all_lines, unit="顆")
        hosts = len({x["host"] for x in new_surge})
        sev = "critical" if any(x["to_v"] >= crit for x in new_surge) else "warning"
        title = nf.head(nf.EMOJI[sev], TIER_LABEL[SURGE], f"磁碟 {cfg['surge_hours']:g} 小時內上升 ≥{cfg['surge_rise']:g} 個百分點", hosts)
        card = nf.card(title, lines, link, color="Attention" if sev == "critical" else "Warning")
        cards.append((SURGE, float(cfg["surge_floor"]), sev, title, card, lines, hosts, len(new_surge), all_lines))

    if not cards:
        conn.commit()
        return {"status": "none", "stale": stale, "exempt": n_exempt}
    try:
        teams_on = tn.load_settings(conn)["enabled"]
    except tn.SettingsCorrupt:
        teams_on = False
    out = []
    if teams_on:
        tn.ensure(conn)
    stamp = now.strftime("%Y%m%d%H%M%S%f")
    import notice_digest as nd
    for tier, thr, sev, title, card, summary, hosts, items, all_lines in cards:
        key = None
        # 2.060.19（S6）：非緊急（準備級、終點沒到緊急的暴增）存進每天 08:00 彙整，不立即發；緊急照舊立即發
        if sev != "critical" and nd.add(conn, {WARN: "disk", CRITICAL: "disk_np"}.get(tier, "surge"), all_lines, now):
            _history(conn, now_s, "alert", batch_id, thr, hosts, items, "digest", None, "\n".join(summary), tier)
            out.append({"tier": tier, "hosts": hosts, "items": items, "key": None, "digest": True})
            continue
        if teams_on:
            key = f"disk:{batch_id}:{tier}:{stamp}"
            tn._enqueue(conn, key, "disk_alert", "disk", sev, title, card, now)
        _history(conn, now_s, "alert", batch_id, thr, hosts, items, "queued" if teams_on else "recorded", key, "\n".join(summary), tier)
        out.append({"tier": tier, "hosts": hosts, "items": items, "key": key})
    conn.commit()
    return {"status": "queued" if teams_on else "recorded", "cards": out, "exempt": n_exempt}


def after_import(conn, result: dict) -> None:
    """relay_import 匯入成功（已 commit）後的掛勾：只處理 WhatsUp。錯誤只記日誌，不影響匯入結果。"""
    if result.get("status") != "imported" or result.get("source") != "whatsup":
        return
    try:
        evaluate(conn, result["batch_id"])
    except Exception as exc:  # noqa: BLE001 - 告警評估失敗不可以讓匯入變失敗；記日誌（不含資料內容）
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        log.error("WhatsUp 磁碟告警評估失敗（批次 %s）：%s", result.get("batch_id"), type(exc).__name__)


# ── 範例（人工按鈕）────────────────────────────────────────────────────
def sample_card(conn) -> tuple[dict, str, str, int] | None:
    """用最近一天匯入的 WhatsUp 磁碟資料組一則**測試**訊息（不記去重）：有緊急／準備／暴增就分段列，
    其餘列在「未達門檻」段（只為了看格式）。沒有資料回 None。回 (卡片, 標題, 摘要, 台數)。"""
    cfg = load_settings(conn)
    raw = _raw(conn, latest_day=True)
    rows = _rows(conn, latest_day=True)
    if not rows:
        return None
    crit, warn = float(cfg["critical"]), float(cfg["warn"])
    groups = [(f"緊急（≥{cfg['critical']}%）", [r for r in rows if r[3] >= crit]),
              (f"準備（≥{cfg['warn']}%）", [r for r in rows if warn <= r[3] < crit]),
              ("未達門檻（只為了看格式）", [r for r in rows if r[3] < warn])]
    sections, summary = [], []
    for head, items in groups:
        if not items:
            continue
        ls, extra = build_lines(conn, items)
        tail = f"另有 {extra} 台" if extra else ""
        sections.append((head, ls, tail))
        summary += [f"■ {head}"] + ls + ([tail] if tail else [])
    sg = surges(conn, raw, cfg)
    if sg:
        ls, extra = surge_lines(conn, sg)
        tail = f"另有 {extra} 顆" if extra else ""
        sections.append(("暴增", ls, tail))
        summary += ["■ 暴增"] + ls + ([tail] if tail else [])
    title = f"【測試】磁碟使用率範例（正式告警：緊急 ≥{cfg['critical']}%、準備 ≥{cfg['warn']}%）"
    note = f"這是測試訊息：用最近一天（{rows[0][2]}）匯入的 WhatsUp 資料組成，取當天最高值；未套門檻也列出，只為了看格式。"
    return build_card(title, [], 0, note, sections=sections), title, "\n".join(summary), len({r[0] for r in rows})


def history(conn, limit: int = HISTORY_KEEP) -> list[dict]:
    if not _has(conn, "disk_alert_history"):
        return []
    has_d = _has(conn, "teams_notify_delivery")
    q = ("SELECT h.*, d.status AS delivery_status FROM disk_alert_history h LEFT JOIN teams_notify_delivery d ON d.event_key = h.delivery_key "
         if has_d else "SELECT h.*, NULL AS delivery_status FROM disk_alert_history h ")
    rows = conn.execute(q + "ORDER BY h.id DESC LIMIT ?", (limit,)).fetchall()
    return [{"tier": None, **dict(r), "__no": r["id"]} for r in rows]
