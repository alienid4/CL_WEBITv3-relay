"""S3：掃描邏輯——跑每個 ScanSource，寫入 scan_history。

驗收標準第 3 條（契約）：「掃描健康度：能標示本次掃描是否成功完整；若有網段連不到／掃描
失敗，需明確呈現，不可讓『掃描失敗』被誤讀為『無異常』」——所以這裡每個來源獨立 try/except，
一個來源掛掉不會讓其他來源的結果也不見，而且會把失敗的網段明確記錄下來（scan_ok=0），
不是靜默略過。
"""
from __future__ import annotations

import sqlite3

from db import _now_local
from scan_sources import ScanSource


def run_scan(sources: list[ScanSource], conn: sqlite3.Connection) -> dict:
    """跑完所有來源，回傳本次掃描摘要：
    {"total_found": int, "failed_segments": [{"source": str, "reason": str}]}
    """
    total_found = 0
    failed_segments: list[dict] = []
    # 整批共用同一個時間戳：同一次掃描的所有列必須有完全相同的 scan_time，
    # 否則「取最新一次掃描」會只撈到最後那幾筆。也明確寫本地時間，不靠資料表 DEFAULT
    # （既有資料庫的 DEFAULT 還是舊的 UTC，改 schema 對它無效）。
    scan_stamp = _now_local()

    for source in sources:
        try:
            results = source.scan()
        except Exception as exc:  # noqa: BLE001 - 掃描來源錯誤型態不一，統一攔截後如實記錄
            failed_segments.append({"source": source.name, "reason": str(exc)})
            conn.execute(
                "INSERT INTO scan_history (hostname, ip, device_model, is_vm, segment, "
                "scan_ok, scan_time) VALUES (?, ?, ?, ?, ?, 0, ?)",
                (None, None, None, 0, source.name, scan_stamp),
            )
            continue

        for r in results:
            conn.execute(
                "INSERT INTO scan_history (hostname, ip, device_model, is_vm, segment, "
                "scan_ok, scan_time, mac, mac_vendor, open_ports, ttl, os_guess) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (r.hostname, r.ip, r.device_model, int(r.is_vm), r.segment, scan_stamp,
                 r.mac, r.mac_vendor, r.open_ports, r.ttl, r.os_guess),
            )
            total_found += 1

    conn.commit()
    return {"total_found": total_found, "failed_segments": failed_segments}


def latest_scan_results(conn: sqlite3.Connection, scan_time: str | None = None) -> list[sqlite3.Row]:
    """取最新一次掃描的成功結果（scan_ok=1），供 S4 比對引擎使用。"""
    if scan_time is None:
        row = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
        scan_time = row["t"] if row else None
    if scan_time is None:
        return []
    return conn.execute(
        "SELECT * FROM scan_history WHERE scan_time = ? AND scan_ok = 1", (scan_time,)
    ).fetchall()


if __name__ == "__main__":
    from db import get_connection, init_db
    from scan_sources import MockSNMPSource, MockVCenterSource

    init_db()
    connection = get_connection()
    try:
        summary = run_scan([MockVCenterSource(), MockSNMPSource()], connection)
        print(summary)
    finally:
        connection.close()
