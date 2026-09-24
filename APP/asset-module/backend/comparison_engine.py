"""S4：比對引擎（review:true — 這片是核心業務邏輯，正確性直接決定使用者信不信任這個系統）。

契約「架構」＋「怎樣算對」定義的兩種比對：
  比對①　本次掃描 vs 上次掃描 → 異常新增（偷偷上線）／異常消失（偷偷下線）
  比對②　掃描結果 vs CIA 表   → 漏登記（掃描到但 CIA 未登記）

比對鑰匙（契約已定案，D3）：主要用 IP，輔以主機名稱。

D24 邊界：這裡只碰「可比對層」欄位（ip／hostname／device_model／is_vm），不去動
「業務性/僅顯示層」欄位（保管者、資產用途…）——比對引擎不應該也不需要知道那些欄位。
"""
from __future__ import annotations

import sqlite3

from db import insert_comparison_result


def _existing_unresolved(
    conn: sqlite3.Connection, ip: str | None, hostname: str | None, issue_type: str
) -> bool:
    """同一台主機、同一種異常，若還有未處理的紀錄，就不重複插入
    （契約：問題清單「標記已處理」後不再重複跳出；但這裡是說「同一個未結案的問題」不要一天生一筆，
    不是說歷史紀錄要被覆蓋——真的被標記已處理後，若問題重新出現，會是新的一筆）。

    ⚠️ 這裡原本只比對 hostname，是實際踩到的 bug：網段掃描抓不到反解名稱時 hostname 是
    **空字串**（不是 NULL），於是所有「叫不出名字」的主機在 `hostname = ''` 這個條件下
    全部長得一樣，第二台之後就被當成「已經記過了」而跳過。實測有 3 台未登記主機因此
    從來沒進過問題清單，畫面上只看得到 1 筆。

    改成以 IP 為主鍵、主機名稱為輔——這也才符合契約 D3 訂的比對鑰匙順序。
    """
    if ip:
        row = conn.execute(
            "SELECT 1 FROM comparison_result WHERE ip = ? AND issue_type = ? AND is_read = 0",
            (ip, issue_type),
        ).fetchone()
        return row is not None
    if hostname:
        row = conn.execute(
            "SELECT 1 FROM comparison_result WHERE hostname = ? AND issue_type = ? AND is_read = 0",
            (hostname, issue_type),
        ).fetchone()
        return row is not None
    # IP 跟主機名稱都沒有：認不出是哪一台，無從判斷重複，交給呼叫端決定要不要記
    return False


def resolve_issues_for_registered_hosts(conn: sqlite3.Connection) -> int:
    """把「該台其實已經登記了」的漏登記自動結案，回傳結案筆數。

    為什麼需要：主機被納入管理之後，先前產生的「漏登記」不會自己消失，會一直掛在
    問題清單上。實測畫面上那 2 筆待處理的漏登記，指的兩台其實早就登記好了——
    使用者看到的是已經不成立的問題，反而掩蓋掉真正還沒處理的。

    標成已處理（而不是刪掉）是為了保留歷史：這件事真的發生過，只是已經解決了。
    """
    rows = conn.execute(
        "SELECT id FROM comparison_result WHERE is_read = 0 AND issue_type = '漏登記' "
        "AND EXISTS (SELECT 1 FROM hardware h "
        "            WHERE (h.ip IS NOT NULL AND h.ip <> '' AND h.ip = comparison_result.ip) "
        "               OR (h.hostname IS NOT NULL AND h.hostname <> '' "
        "                   AND h.hostname = comparison_result.hostname))"
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE comparison_result SET is_read = 1, handled_at = datetime('now','localtime') "
            "WHERE id = ?",
            (r["id"],),
        )
    conn.commit()
    return len(rows)


def detect_scan_changes(
    conn: sqlite3.Connection, current_scan_time: str, previous_scan_time: str | None
) -> dict:
    """比對①：本次 vs 上次掃描。回傳 {"新增": [...], "消失": [...]}"""
    current_rows = conn.execute(
        "SELECT * FROM scan_history WHERE scan_time = ? AND scan_ok = 1", (current_scan_time,)
    ).fetchall()

    if previous_scan_time is None:
        # 沒有上一次掃描可比（第一次跑），視為都是新的，不判定為「異常」新增
        return {"新增": [], "消失": []}

    previous_rows = conn.execute(
        "SELECT * FROM scan_history WHERE scan_time = ? AND scan_ok = 1", (previous_scan_time,)
    ).fetchall()

    # D3 比對鑰匙：IP 或主機名稱任一相符就算同一台。用「IP 或 hostname 各自取一個key」
    # 硬切割會有問題——同一台主機如果上次掃描沒回報 IP（只有 hostname key）、這次開始回報
    # IP 了（換成 IP key），兩把 key 對不上，會被誤判成「消失一台、新增一台」，其實只是
    # 同一台補上了 IP 欄位。改用「集合成員測試」：只要 IP 或 hostname 任一邊出現在對方
    # 集合裡，就視為同一台，不再各自單獨取一把 key 做差集。
    def _matches_any(row: sqlite3.Row, ip_set: set, hostname_set: set) -> bool:
        if row["ip"] and row["ip"] in ip_set:
            return True
        if row["hostname"] and row["hostname"] in hostname_set:
            return True
        return False

    previous_ips = {r["ip"] for r in previous_rows if r["ip"]}
    previous_hostnames = {r["hostname"] for r in previous_rows if r["hostname"]}
    current_ips = {r["ip"] for r in current_rows if r["ip"]}
    current_hostnames = {r["hostname"] for r in current_rows if r["hostname"]}

    appeared = [
        r for r in current_rows if not _matches_any(r, previous_ips, previous_hostnames)
    ]
    disappeared = [
        r for r in previous_rows if not _matches_any(r, current_ips, current_hostnames)
    ]

    new_count = 0
    for row in appeared:
        if not _existing_unresolved(conn, row["ip"], row["hostname"], "異常新增"):
            insert_comparison_result(conn, row["hostname"], row["ip"], "異常新增")
            new_count += 1

    gone_count = 0
    for row in disappeared:
        if not _existing_unresolved(conn, row["ip"], row["hostname"], "異常消失"):
            insert_comparison_result(conn, row["hostname"], row["ip"], "異常消失")
            gone_count += 1

    return {"新增": new_count, "消失": gone_count}


def detect_missing_from_ica(conn: sqlite3.Connection, scan_time: str) -> int:
    """比對②：掃描到、但 CIA（hardware 表）沒登記 → 漏登記。回傳新增的漏登記筆數。"""
    scanned = conn.execute(
        "SELECT * FROM scan_history WHERE scan_time = ? AND scan_ok = 1", (scan_time,)
    ).fetchall()

    missing_count = 0
    for row in scanned:
        ip = row["ip"]
        hostname = row["hostname"]
        if not ip and not hostname:
            # 掃描結果連 IP、主機名稱都沒有，無法識別是哪一台，跳過不產生無意義的漏登記紀錄
            continue
        # 空字串要排除：掃不到反解名稱時 hostname 是 ''，若 hardware 裡也有空 hostname 的列，
        # `hostname = ''` 會把兩台毫不相干的機器判定成同一台（同一個空字串 bug 的另一面）
        matched = conn.execute(
            "SELECT 1 FROM hardware WHERE (? <> '' AND ip IS NOT NULL AND ip = ?) "
            "OR (? <> '' AND hostname IS NOT NULL AND hostname = ?)",
            (ip or "", ip, hostname or "", hostname),
        ).fetchone()
        if matched is None and not _existing_unresolved(conn, ip, hostname, "漏登記"):
            insert_comparison_result(conn, hostname, ip, "漏登記")
            missing_count += 1

    return missing_count


def run_comparison(
    conn: sqlite3.Connection, current_scan_time: str, previous_scan_time: str | None
) -> dict:
    # 先把已經不成立的舊問題結案，再產生新的——順序反過來的話，剛結案的又會被當成
    # 「已存在未處理」而擋掉新紀錄
    resolved = resolve_issues_for_registered_hosts(conn)
    change_summary = detect_scan_changes(conn, current_scan_time, previous_scan_time)
    missing_count = detect_missing_from_ica(conn, current_scan_time)
    return {
        "異常新增": change_summary["新增"],
        "異常消失": change_summary["消失"],
        "漏登記": missing_count,
        "自動結案": resolved,
    }
