"""S12：掃描紀錄清除排程（D7：掃描紀錄保留政策，90天自動清除）。

範圍只碰scan_history（原始掃描結果）。comparison_result（問題清單）走的是
「標記已處理」生命週期（契約已定案功能），不是這裡的清除範圍——兩者是不同的
資料保留邏輯，不能混在一起清。

排程觸發方式（部署環境）：cron／Windows工作排程器每天呼叫一次
`python cleanup.py`，理由同backup.py——D7沒有要求做管理畫面。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from db import get_connection

RETENTION_DAYS = 90


def run_cleanup(conn: sqlite3.Connection, now: datetime) -> int:
    """刪除scan_time早於(now - RETENTION_DAYS天)的scan_history紀錄，回傳刪除筆數。
    now當參數傳入，理由同run_backup——方便測試注入固定時間。
    """
    cutoff = (now - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("DELETE FROM scan_history WHERE scan_time < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


if __name__ == "__main__":
    connection = get_connection()
    try:
        removed_count = run_cleanup(connection, datetime.now())
        print(f"已清除 {removed_count} 筆超過 {RETENTION_DAYS} 天的掃描紀錄")
    finally:
        connection.close()
