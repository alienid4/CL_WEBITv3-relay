"""S4 done_when 驗證：異常新增／異常消失／漏登記 三種異常類型都要有測試案例覆蓋。
review:true 切片——這份測試本身也是之後 code-review 要檢視的重點。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import comparison_engine  # noqa: E402
import db  # noqa: E402


def _insert_scan_row(conn, scan_time, hostname, ip, scan_ok=1):
    conn.execute(
        "INSERT INTO scan_history (scan_time, hostname, ip, device_model, is_vm, segment, scan_ok) "
        "VALUES (?, ?, ?, 'Test Model', 0, '機房A', ?)",
        (scan_time, hostname, ip, scan_ok),
    )
    conn.commit()


def _fresh_conn(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)
    return db.get_connection(db_path)


def test_first_scan_has_no_baseline_so_no_false_positive_changes():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            _insert_scan_row(conn, "2026-07-17T03:00", "host-a", "10.0.0.1")
            result = comparison_engine.detect_scan_changes(conn, "2026-07-17T03:00", None)
            assert result == {"新增": [], "消失": []}
        finally:
            conn.close()


def test_new_host_detected_as_anomaly_addition():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            _insert_scan_row(conn, "2026-07-16T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-b-new", "10.0.0.2")

            result = comparison_engine.detect_scan_changes(
                conn, "2026-07-17T03:00", "2026-07-16T03:00"
            )
            assert result == {"新增": 1, "消失": 0}

            rows = conn.execute(
                "SELECT * FROM comparison_result WHERE issue_type = '異常新增'"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["hostname"] == "host-b-new"
        finally:
            conn.close()


def test_missing_host_detected_as_anomaly_removal():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            _insert_scan_row(conn, "2026-07-16T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-16T03:00", "host-gone", "10.0.0.9")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-a", "10.0.0.1")

            result = comparison_engine.detect_scan_changes(
                conn, "2026-07-17T03:00", "2026-07-16T03:00"
            )
            assert result == {"新增": 0, "消失": 1}

            rows = conn.execute(
                "SELECT * FROM comparison_result WHERE issue_type = '異常消失'"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["hostname"] == "host-gone"
        finally:
            conn.close()


def test_rerunning_same_comparison_does_not_duplicate_unresolved_issue():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            _insert_scan_row(conn, "2026-07-16T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-b-new", "10.0.0.2")

            comparison_engine.detect_scan_changes(conn, "2026-07-17T03:00", "2026-07-16T03:00")
            # 同樣的比對再跑一次（模擬重複執行/排程重跑）
            result2 = comparison_engine.detect_scan_changes(
                conn, "2026-07-17T03:00", "2026-07-16T03:00"
            )
            assert result2 == {"新增": 0, "消失": 0}  # 未處理的問題不會重複生成

            rows = conn.execute(
                "SELECT * FROM comparison_result WHERE issue_type = '異常新增'"
            ).fetchall()
            assert len(rows) == 1  # 還是只有一筆，不是兩筆
        finally:
            conn.close()


def test_resolved_issue_can_reappear_as_new_entry():
    """問題被標記已處理後，若同類異常在同一台主機重新發生，要能再生成新的一筆
    （不能因為 dedup 邏輯而永久擋住之後真的再發生的同類問題）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            _insert_scan_row(conn, "2026-07-16T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-c", "10.0.0.3")
            comparison_engine.detect_scan_changes(conn, "2026-07-17T03:00", "2026-07-16T03:00")

            row = conn.execute(
                "SELECT id FROM comparison_result WHERE issue_type = '異常新增' AND hostname = 'host-c'"
            ).fetchone()
            assert row is not None
            db.mark_comparison_read(conn, row["id"])  # 標記已處理

            # host-c 這次消失、下次又出現（重新偷偷上線）
            _insert_scan_row(conn, "2026-07-18T03:00", "host-a", "10.0.0.1")
            comparison_engine.detect_scan_changes(conn, "2026-07-18T03:00", "2026-07-17T03:00")

            _insert_scan_row(conn, "2026-07-19T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-19T03:00", "host-c", "10.0.0.3")
            result = comparison_engine.detect_scan_changes(
                conn, "2026-07-19T03:00", "2026-07-18T03:00"
            )
            assert result["新增"] == 1  # host-c 重新出現，且舊的已處理，所以能生成新一筆

            rows = conn.execute(
                "SELECT * FROM comparison_result WHERE issue_type = '異常新增' AND hostname = 'host-c'"
            ).fetchall()
            assert len(rows) == 2  # 一筆已處理的舊紀錄 + 一筆新紀錄，歷史都留著
        finally:
            conn.close()


def test_scanned_host_not_in_ica_flagged_as_missing_registration():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            db.insert_hardware(
                conn, asset_serial="HW-0001", hostname="registered-host", ip="10.0.0.1"
            )
            _insert_scan_row(conn, "2026-07-17T03:00", "registered-host", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "unregistered-host", "10.0.0.99")

            count = comparison_engine.detect_missing_from_ica(conn, "2026-07-17T03:00")
            assert count == 1

            rows = conn.execute(
                "SELECT * FROM comparison_result WHERE issue_type = '漏登記'"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["hostname"] == "unregistered-host"
        finally:
            conn.close()


def test_scanned_host_matched_by_hostname_when_ip_changed():
    """D3 比對鑰匙：IP 為主、主機名稱為輔——IP 變了但 hostname 沒變，不該被誤判漏登記。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            db.insert_hardware(
                conn, asset_serial="HW-0002", hostname="stable-host", ip="10.0.0.5"
            )
            # 掃描到的 IP 換了，但 hostname 一樣
            _insert_scan_row(conn, "2026-07-17T03:00", "stable-host", "10.0.0.200")

            count = comparison_engine.detect_missing_from_ica(conn, "2026-07-17T03:00")
            assert count == 0  # 靠 hostname 比對到了，不算漏登記
        finally:
            conn.close()


def test_ip_change_between_scans_is_not_false_positive_appear_and_disappear():
    """review 抓到的 bug：同一台主機兩次掃描間 IP 從無到有（或改變），舊版用單一
    key（IP優先、缺值才退hostname）做差集，會把同一台誤判成「消失一台+新增一台」。
    改成「IP 或 hostname 任一相符就算同一台」後，這種情況不該產生任何異常事件。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            # 上次掃描：host-z 沒回報到 IP（只有 hostname）
            conn.execute(
                "INSERT INTO scan_history (scan_time, hostname, ip, device_model, is_vm, segment, scan_ok) "
                "VALUES ('2026-07-16T03:00', 'host-z', NULL, 'Test Model', 0, '機房A', 1)"
            )
            conn.commit()
            # 這次掃描：host-z 開始回報 IP 了，hostname 不變
            _insert_scan_row(conn, "2026-07-17T03:00", "host-z", "10.0.0.50")

            result = comparison_engine.detect_scan_changes(
                conn, "2026-07-17T03:00", "2026-07-16T03:00"
            )
            assert result == {"新增": 0, "消失": 0}  # 同一台主機，不該算新增也不該算消失
        finally:
            conn.close()


def test_scan_row_with_no_identifiable_ip_or_hostname_is_skipped():
    """review 抓到的 bug：hostname 跟 ip 都是 None 時，舊版的 OR 比對條件永遠比對不到
    任何硬體資料，會不斷產生無意義的漏登記紀錄。現在應該直接跳過。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            conn.execute(
                "INSERT INTO scan_history (scan_time, hostname, ip, device_model, is_vm, segment, scan_ok) "
                "VALUES ('2026-07-17T03:00', NULL, NULL, 'Test Model', 0, '機房A', 1)"
            )
            conn.commit()

            count = comparison_engine.detect_missing_from_ica(conn, "2026-07-17T03:00")
            assert count == 0

            rows = conn.execute(
                "SELECT * FROM comparison_result WHERE issue_type = '漏登記'"
            ).fetchall()
            assert len(rows) == 0
        finally:
            conn.close()


def test_run_comparison_aggregates_both_comparisons():
    """run_comparison() 這個對外整合入口本身也要有測試直接呼叫，不能只測它內部呼叫的子函式。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="HW-0003", hostname="host-a", ip="10.0.0.1")
            _insert_scan_row(conn, "2026-07-16T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(conn, "2026-07-17T03:00", "host-new", "10.0.0.2")

            summary = comparison_engine.run_comparison(conn, "2026-07-17T03:00", "2026-07-16T03:00")
            # 「自動結案」是後加的：主機納管後，先前的漏登記要自動結案，不然已經不成立的
            # 問題會一直掛在清單上掩蓋真正待處理的（見 test_semantic_bugs.py）。
            # 這裡沒有已納管的舊問題，所以是 0。
            assert summary == {"異常新增": 1, "異常消失": 0, "漏登記": 1, "自動結案": 0}
        finally:
            conn.close()


if __name__ == "__main__":
    test_first_scan_has_no_baseline_so_no_false_positive_changes()
    test_new_host_detected_as_anomaly_addition()
    test_missing_host_detected_as_anomaly_removal()
    test_rerunning_same_comparison_does_not_duplicate_unresolved_issue()
    test_resolved_issue_can_reappear_as_new_entry()
    test_scanned_host_not_in_ica_flagged_as_missing_registration()
    test_scanned_host_matched_by_hostname_when_ip_changed()
    test_ip_change_between_scans_is_not_false_positive_appear_and_disappear()
    test_scan_row_with_no_identifiable_ip_or_hostname_is_skipped()
    test_run_comparison_aggregates_both_comparisons()
    print("S4 test_comparison_engine.py: PASS")
