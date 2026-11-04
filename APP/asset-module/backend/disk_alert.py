"""WhatsUp 磁碟使用率 → Teams 告警（中繼匯入每日資料用）。

流程：中繼匯入的 WhatsUp 批次**成功寫入**後（relay_import.import_batch 提交之後），評估這一批寫到 relay_disk_util 的每一台＋每顆磁碟＋每一天：
  * 取**當天最高值**（max 序列的最大值；沒有 max 才用 avg），≥ 門檻（預設 90%，管理員可改 50–100）就列為告警候選。
  * 同一台＋同一顆磁碟＋同一天只告警一次（disk_alert_seen）。
  * 每次評估最多送**一則** Teams 訊息，一台一行（多顆超標時寫最高那顆＋「等 N 顆」），最多 30 行，其餘寫「另有 N 台」。
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
from datetime import datetime

SETTING_KEY = "teams_disk_alert"
import disk_level  # noqa: E402 - 2.044.0（R17）：預設門檻＝全站磁碟一把尺的「高」那一級（90%），Teams 只推高；80～90% 的「處理」在 1.4 追蹤處理看

DEFAULTS = {"enabled": False, "threshold": int(disk_level.HIGH_PCT)}
MAX_LINES = 30
HISTORY_KEEP = 200
log = logging.getLogger("disk_alert")

SQL = [
    """CREATE TABLE IF NOT EXISTS disk_alert_seen (
        host TEXT NOT NULL, disk TEXT NOT NULL, day TEXT NOT NULL,      -- host＝去重身分：'mk:<machine_key>' 或 'ip:<IP>'（見 ident_of）
        pct REAL, batch_id TEXT, first_at TEXT NOT NULL,
        PRIMARY KEY (host, disk, day))""",
    """CREATE TABLE IF NOT EXISTS disk_alert_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,                 -- alert（匯入後評估）／sample（人工範例）
        batch_id TEXT,
        threshold REAL,
        hosts INTEGER, items INTEGER,
        status TEXT NOT NULL,               -- queued（已排進 Teams 佇列）／recorded（Teams 未啟用，只記錄）／sample
        delivery_key TEXT,                  -- teams_notify_delivery.event_key（可查送出結果）
        summary TEXT)""",
]


def ensure(conn: sqlite3.Connection) -> None:
    for s in SQL:
        conn.execute(s)


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


# ── 設定 ───────────────────────────────────────────────────────────────
def load_settings(conn) -> dict:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    cfg = dict(DEFAULTS)
    if row and row[0]:
        try:
            saved = json.loads(row[0]) or {}
        except (ValueError, TypeError) as exc:
            raise ValueError(f"磁碟告警設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}") from exc
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return cfg


def validate(cfg: dict) -> dict:
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    t = out["threshold"]
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        raise ValueError("門檻要是 50～100 的數字")
    if not 50 <= float(t) <= 100:
        raise ValueError("門檻要在 50～100 之間")
    out["threshold"] = int(t) if float(t).is_integer() else round(float(t), 1)
    out["enabled"] = bool(out["enabled"])
    return out


def save_settings(conn, cfg: dict) -> dict:
    """存設定。**不 commit**（路由與稽核同一交易）。"""
    out = validate(cfg)
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SETTING_KEY, json.dumps(out, ensure_ascii=False)))
    return out


# ── 資料 ───────────────────────────────────────────────────────────────
def day_peak(blob) -> float | None:
    """relay_disk_util 一列的當天最高值：max 序列最大；沒有 max 才用 avg。壞資料回 None（不猜）。"""
    try:
        d = json.loads(gzip.decompress(blob).decode())
    except (OSError, ValueError, TypeError):
        return None
    vals = [v for v in (d.get("max") or []) if isinstance(v, (int, float))]
    if not vals:
        vals = [v for v in (d.get("avg") or []) if isinstance(v, (int, float))]
    return round(max(vals), 1) if vals else None


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


def _rows(conn, batch_id: str | None = None, latest_day: bool = False) -> list[tuple[str, str, str, float]]:
    if not _has(conn, "relay_disk_util"):
        return []
    if latest_day:
        d = conn.execute("SELECT MAX(day) FROM relay_disk_util").fetchone()[0]
        if not d:
            return []
        q, args = "SELECT host, day, disk, data FROM relay_disk_util WHERE day = ?", (d,)
    else:
        q, args = "SELECT host, day, disk, data FROM relay_disk_util WHERE batch_id = ?", (batch_id,)
    out = []
    for r in conn.execute(q, args).fetchall():
        p = day_peak(r[3])
        if p is not None:
            out.append((r[0], r[2], r[1], p))
    return out


def build_lines(conn, items: list[tuple[str, str, str, float]]) -> tuple[list[str], int]:
    """一台一行（最高那顆＋等 N 顆），依最高值由大到小；最多 MAX_LINES 行。回 (行, 超出的台數)。"""
    by: dict[str, list] = {}
    for host, disk, day, pct in items:
        by.setdefault(host, []).append((pct, disk, day))
    order = sorted(by.items(), key=lambda kv: (-max(x[0] for x in kv[1]), kv[0]))
    lines = []
    for host, ds in order[:MAX_LINES]:
        ds.sort(reverse=True)
        pct, disk, day = ds[0]
        more = f"，等 {len(ds)} 顆" if len(ds) > 1 else ""
        lines.append(f"{host_label(conn, host)}｜{disk}｜{pct:g}%｜{day}{more}")
    return lines, max(0, len(order) - MAX_LINES)


def build_card(title: str, lines: list[str], extra: int, note: str) -> dict:
    """自己組卡片（每行一個 TextBlock，文字一律經 teams_notify.md_escape），因為 FactSet 只放得下 25 列。"""
    import teams_notify as tn
    body = [{"type": "TextBlock", "text": tn.md_escape(title)[:240], "weight": "Bolder", "size": "Medium", "color": "Warning", "wrap": True},
            {"type": "TextBlock", "text": tn.md_escape(note)[:400], "isSubtle": True, "wrap": True}]
    body += [{"type": "TextBlock", "text": tn.md_escape(x)[:300], "wrap": True, "spacing": "None"} for x in lines]
    if extra:
        body.append({"type": "TextBlock", "text": tn.md_escape(f"另有 {extra} 台"), "wrap": True})
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": body}}]}


def _history(conn, now_s: str, kind: str, batch_id, thr, hosts: int, items: int, status: str, key, summary: str) -> None:
    conn.execute("INSERT INTO disk_alert_history (at, kind, batch_id, threshold, hosts, items, status, delivery_key, summary) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now_s, kind, batch_id, thr, hosts, items, status, key, summary[:2000]))
    conn.execute(f"DELETE FROM disk_alert_history WHERE id NOT IN (SELECT id FROM disk_alert_history ORDER BY id DESC LIMIT {HISTORY_KEEP})")


# ── 評估（匯入成功後呼叫）─────────────────────────────────────────────
def evaluate(conn, batch_id: str, now: datetime | None = None) -> dict:
    """評估這一批寫進 relay_disk_util 的資料。關閉時不動作。自己 commit；任何錯誤由呼叫端吞掉並記日誌（不影響匯入）。"""
    import teams_notify as tn
    cfg = load_settings(conn)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    now = now or tn.now_taipei()
    now_s = tn._ts(now)
    thr = float(cfg["threshold"])
    cands = [x for x in _rows(conn, batch_id) if x[3] >= thr]
    ensure(conn)
    new = []
    for host, disk, day, pct in sorted(cands, key=lambda x: -x[3]):
        # 去重鍵用資產的 machine_key（同一台以不同 IP 匯入也不重發）；訊息行仍顯示主機名／IP
        cur = conn.execute("INSERT OR IGNORE INTO disk_alert_seen (host, disk, day, pct, batch_id, first_at) VALUES (?,?,?,?,?,?)",
                           (ident_of(conn, host), disk, day, pct, batch_id, now_s))
        if cur.rowcount == 1:
            new.append((host, disk, day, pct))
    if not new:
        conn.commit()
        return {"status": "none", "candidates": len(cands)}
    lines, extra = build_lines(conn, new)
    hosts = len({x[0] for x in new})
    title = f"磁碟使用率 ≥ {cfg['threshold']}%：{hosts} 台"
    note = "依 WhatsUp 每日資料，取當天最高值。主機名對不到資產時顯示 IP。"
    card = build_card(title, lines, extra, note)
    summary = "\n".join(lines + ([f"另有 {extra} 台"] if extra else []))
    try:
        teams_on = tn.load_settings(conn)["enabled"]
    except tn.SettingsCorrupt:
        teams_on = False
    if not teams_on:
        _history(conn, now_s, "alert", batch_id, thr, hosts, len(new), "recorded", None, summary)
        conn.commit()
        return {"status": "recorded", "hosts": hosts, "items": len(new)}
    tn.ensure(conn)
    key = f"disk:{batch_id}:{now.strftime('%Y%m%d%H%M%S%f')}"
    tn._enqueue(conn, key, "disk_alert", "disk", "warning", title, card, now)
    _history(conn, now_s, "alert", batch_id, thr, hosts, len(new), "queued", key, summary)
    conn.commit()
    return {"status": "queued", "hosts": hosts, "items": len(new), "key": key}


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
def sample_card(conn) -> tuple[dict, str] | None:
    """用最近一天匯入的 WhatsUp 磁碟資料組一則**測試**訊息（不套門檻、不記去重）。沒有資料回 None。"""
    cfg = load_settings(conn)
    rows = _rows(conn, latest_day=True)
    if not rows:
        return None
    lines, extra = build_lines(conn, rows)
    title = f"【測試】磁碟使用率範例（未套門檻；正式告警門檻 {cfg['threshold']}%）"
    note = f"這是測試訊息：用最近一天（{rows[0][2]}）匯入的 WhatsUp 資料組成，取當天最高值，不代表已超標。"
    return build_card(title, lines, extra, note), title


def history(conn, limit: int = HISTORY_KEEP) -> list[dict]:
    if not _has(conn, "disk_alert_history"):
        return []
    has_d = _has(conn, "teams_notify_delivery")
    q = ("SELECT h.*, d.status AS delivery_status FROM disk_alert_history h LEFT JOIN teams_notify_delivery d ON d.event_key = h.delivery_key "
         if has_d else "SELECT h.*, NULL AS delivery_status FROM disk_alert_history h ")
    rows = conn.execute(q + "ORDER BY h.id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "__no": r["id"]} for r in rows]
