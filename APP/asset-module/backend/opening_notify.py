"""開盤尖峰 → Teams 摘要（2.047.5）。

流程：中繼匯入的 aria_host 批次**成功寫入**後（relay_import.import_batch 提交之後），把「那一天」的開盤 09:00～09:30
結果組成一則 Teams 訊息，走 teams_notify 的既有佇列（啟用開關、Workflow 網址、對外送出已確認、登入停用 fail-closed、
重試／不確定、投遞紀錄全部沿用）。Teams 通知沒啟用時只記錄（歷史可查），不送。

內容（數字一律取 opening_peak.rows ＝首頁同一份計算，不另算）：
  * 涵蓋幾台（opening_peak.coverage，同首頁）
  * CPU 或記憶體 ≥ 門檻（預設 90%）的主機，一台一行
  * CPU 最高前 10 名：最高（幾點）／平均／記憶體最高
同一天只送一次（opening_notify_seen）；同一天後來又匯入較新的批次不重送（首頁會更新，Teams 不洗版）。

盤中靜默窗：這裡只把訊息送到 Teams，不連任何主機，不受 08:30–15:00 靜默窗限制（匯入本身由 relay_import.tick 守門）。
evaluate 自己 commit（獨立於匯入交易，失敗不影響匯入結果）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

SETTING_KEY = "teams_opening_peak"
DEFAULTS = {"enabled": True, "threshold": 90}
TOP_N = 10
log = logging.getLogger("opening_notify")

SQL = [
    """CREATE TABLE IF NOT EXISTS opening_notify_seen (
        day TEXT PRIMARY KEY, batch_id TEXT, at TEXT NOT NULL,
        status TEXT NOT NULL,               -- queued（已排進 Teams 佇列）／recorded（Teams 未啟用，只記錄）
        delivery_key TEXT, summary TEXT)""",
]


def ensure(conn: sqlite3.Connection) -> None:
    for s in SQL:
        conn.execute(s)


def load_settings(conn) -> dict:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    cfg = dict(DEFAULTS)
    if row and row[0]:
        try:
            saved = json.loads(row[0]) or {}
        except (ValueError, TypeError) as exc:
            raise ValueError(f"開盤 Teams 設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}") from exc
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return cfg


def _short(host: str) -> str:
    return (host or "").split(".")[0]


def _pct(v) -> str:
    return "—" if v is None else f"{v:g}%"


def build(conn, day: str, threshold: float) -> tuple[str, list[str], str] | None:
    """組（標題, 行, 註）。那天沒有資料回 None。數字全部取 opening_peak.rows（首頁同一份）。"""
    import opening_peak as op
    rs = op.rows(conn, day, with_vms=False)
    if not rs:
        return None
    b = conn.execute("SELECT scope, data_until, collected_at FROM opening_peak_batch WHERE day = ?", (day,)).fetchone()
    cov = op.coverage(conn, day, rs, b["scope"] if b else None)
    with_data = [r for r in rs if r["has_data"]]
    hot = [r for r in rs if (r["max"] is not None and r["max"] >= threshold) or (r["mem_max"] is not None and r["mem_max"] >= threshold)]
    avg_all = round(sum(r["avg"] for r in with_data if r["avg"] is not None) / len(with_data), 1) if with_data else None
    title = f"{day} 開盤 09:00～09:30 尖峰：≥{threshold:g}% {len(hot)} 台"
    lines = [f"涵蓋 {cov['covered']}／{cov['expected']} 台有資料；全部主機 CPU 平均 {_pct(avg_all)}"]
    if hot:
        lines.append(f"■ CPU 或記憶體 ≥{threshold:g}%：")
        lines += [f"{r['host']}｜CPU 最高 {_pct(r['max'])}（{r['max_at'] or '—'}）｜記憶體最高 {_pct(r['mem_max'])}" for r in hot]
    else:
        lines.append(f"■ 沒有主機 CPU 或記憶體 ≥{threshold:g}%")
    lines.append(f"■ CPU 最高前 {TOP_N} 名（最高／平均／記憶體最高）：")
    lines += [f"{i}. {_short(r['host'])}｜{_pct(r['max'])}（{r['max_at'] or '—'}）｜{_pct(r['avg'])}｜{_pct(r['mem_max'])}"
              for i, r in enumerate(with_data[:TOP_N], 1)]
    if cov.get("missing"):
        lines.append(f"■ 沒有資料：{len(cov['missing'])} 台")
    note = "資料來源：Aria Operations（vCOps），5 分鐘一點；缺的點不算 0。明細看系統首頁「今日開盤尖峰」。"
    return title, lines, note


def evaluate(conn, day: str, batch_id: str, now: datetime | None = None) -> dict:
    import disk_alert
    import teams_notify as tn
    cfg = load_settings(conn)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    ensure(conn)
    if conn.execute("SELECT 1 FROM opening_notify_seen WHERE day = ?", (day,)).fetchone():
        return {"status": "already"}
    built = build(conn, day, float(cfg["threshold"]))
    if built is None:
        return {"status": "none"}
    title, lines, note = built
    now = now or tn.now_taipei()
    now_s = tn._ts(now)
    summary = "\n".join([title] + lines)
    try:
        teams_on = tn.load_settings(conn)["enabled"]
    except tn.SettingsCorrupt:
        teams_on = False
    if not teams_on:
        conn.execute("INSERT INTO opening_notify_seen (day, batch_id, at, status, delivery_key, summary) VALUES (?,?,?,?,?,?)",
                     (day, batch_id, now_s, "recorded", None, summary[:4000]))
        conn.commit()
        return {"status": "recorded"}
    tn.ensure(conn)
    card = disk_alert.build_card(title, lines, 0, note)
    key = f"opening:{day}"
    tn._enqueue(conn, key, "opening_peak", "opening", "info", title, card, now)
    conn.execute("INSERT INTO opening_notify_seen (day, batch_id, at, status, delivery_key, summary) VALUES (?,?,?,?,?,?)",
                 (day, batch_id, now_s, "queued", key, summary[:4000]))
    conn.commit()
    return {"status": "queued", "key": key}


def after_import(conn, result: dict) -> None:
    """relay_import 匯入成功（已 commit）後的掛勾：只處理 aria_host。錯誤只記日誌，不影響匯入結果。"""
    if result.get("status") != "imported" or result.get("source") != "aria_host":
        return
    try:
        r = conn.execute("SELECT day FROM opening_peak_batch WHERE batch_id = ?", (result["batch_id"],)).fetchone()
        if r is None:
            return                     # 這批被同一天較新的批次蓋過（skipped_existing），不送
        evaluate(conn, r[0], result["batch_id"])
    except Exception as exc:  # noqa: BLE001 - Teams 摘要失敗不可以讓匯入變失敗；記日誌（不含資料內容）
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        log.error("開盤 Teams 摘要失敗（批次 %s）：%s", result.get("batch_id"), type(exc).__name__)
