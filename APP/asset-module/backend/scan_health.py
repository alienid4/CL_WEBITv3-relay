"""掃描「全部 0 台」＝掃描器壞了，不是網段都空了（2026-10-28）。

## 為什麼要有

一輪排程／手動掃描涵蓋好幾個網段、加起來 0 台存活，以前記成 status=ok、found_count=0。
在公司網路上這代表掃描器連不出去（防火牆、網卡、路由），但沒有任何人會發現——
存在可信度的「掃描」證據就這樣安靜地停了。

## 規則（跟 count_bridge 的「小的 0 台補掃」同一把尺）

· 這輪涵蓋的網段數 > count_bridge 規則 `empty_run_small_max_segments`（預設 2，所以 ≥3 段）
  而且加起來 0 台 → scan_runs 記 **failed**（沿用既有的失敗狀態，不另造新值），
  error＝「掃描 N 個網段全部無回應，掃描器可能無法連線」，並開一筆 scan_alert。
· 1～2 段的 0 台：照舊記 ok——那是 count_bridge 的「小的補掃」，由它看前後 24 小時判斷
  是整段斷線還是掃描器問題（它讀 scan_coverage／scan_history，不讀 scan_runs，這裡不動它）。

## 會出現在哪

· 1.2 異常事件（/api/issues）：scan_alert 跟比對結果併在一起列，類型「掃描無回應」，
  id 用負數（-scan_alert.id）跟 comparison_result 分開；標記已處理走同一支 API。
· 1.1 待處理事項（/api/v2/home）：有未處理的掃描無回應就列第一項。
"""
from __future__ import annotations

import sqlite3

ISSUE_TYPE = "掃描無回應"
DEFAULT_SMALL_MAX = 2

_DDL = """CREATE TABLE IF NOT EXISTS scan_alert (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    detected_at TEXT,
    segments INTEGER,
    message TEXT,
    is_read INTEGER DEFAULT 0,
    handled_at TEXT
)"""


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)


def small_max() -> int:
    """count_bridge 的「小的 0 台補掃」上限（同一份規則檔，不另訂數字）。"""
    try:
        import count_bridge

        return int(count_bridge.load_rules().get("empty_run_small_max_segments", DEFAULT_SMALL_MAX))
    except Exception:  # noqa: BLE001 - 規則檔讀不到：用預設 2（跟 count_bridge 的預設一樣）
        return DEFAULT_SMALL_MAX


def empty_failure(n_segments: int, total_found: int) -> str | None:
    """這輪算不算「掃描器壞了」：是就回要記在 scan_runs.error 的那句話，不是回 None。"""
    if total_found == 0 and n_segments > small_max():
        return f"掃描 {n_segments} 個網段全部無回應，掃描器可能無法連線"
    return None


def raise_alert(conn: sqlite3.Connection, run_id: int | None, n_segments: int, message: str) -> int:
    from db import _now_local

    _ensure(conn)
    cur = conn.execute(
        "INSERT INTO scan_alert (run_id, detected_at, segments, message) VALUES (?,?,?,?)",
        (run_id, _now_local(), n_segments, message))
    conn.commit()
    return cur.lastrowid


def issues(conn: sqlite3.Connection, is_read: bool | None = None) -> list[dict]:
    """給 /api/issues 的列（跟 comparison_result 同欄位；id 用負數，hostname 放那句話）。"""
    _ensure(conn)
    sql = "SELECT * FROM scan_alert"
    params: list = []
    if is_read is not None:
        sql += " WHERE is_read = ?"
        params.append(int(is_read))
    return [{"id": -r["id"], "detected_at": r["detected_at"], "hostname": r["message"], "ip": None,
             "issue_type": ISSUE_TYPE, "is_read": r["is_read"], "handled_at": r["handled_at"],
             "source": "scan", "segments": r["segments"], "run_id": r["run_id"]}
            for r in conn.execute(sql + " ORDER BY id", params)]


def mark_read(conn: sqlite3.Connection, issue_id: int) -> bool:
    """issue_id 是 /api/issues 給的負數 id。回有沒有標到。"""
    from db import _now_local

    _ensure(conn)
    cur = conn.execute("UPDATE scan_alert SET is_read = 1, handled_at = ? WHERE id = ?",
                       (_now_local(), -int(issue_id)))
    conn.commit()
    return bool(cur.rowcount)


def open_count(conn: sqlite3.Connection) -> int:
    _ensure(conn)
    return conn.execute("SELECT COUNT(*) FROM scan_alert WHERE is_read = 0").fetchone()[0]
