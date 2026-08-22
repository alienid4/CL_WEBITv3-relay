"""儀表板每個數字都可以點進去看細項——磚塊上的數字必須等於點進去的筆數。

這是這組測試唯一在乎的事：數字對不上的下鑽比不能點更糟（使用者會不信任整個畫面）。
所以每個 case 都直接拿 /api/dashboard/stats 的數字去比對應清單端點回傳的長度。

也涵蓋一個原本的計算瑕疵：舊版「相符」是從掃描側數（幾筆掃描結果對得上 ICA），
再用 ica_count - overlap 反推「登記卻掃不到」。一台資產被兩筆掃描結果對到時
（同機多 IP，或 IP 與主機名分別命中不同筆），那個減法會少算，極端情況甚至變負數。
現已改成資產側/掃描側各自獨立計算。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
SCAN_TIME = "2026-07-18 10:00:00"


def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_STUB_CREDENTIAL))
        conn.commit()
    finally:
        conn.close()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}
    ).status_code == 200
    return client, db_path


def _seed(db_path, hardware, scanned):
    conn = db.get_connection(db_path)
    try:
        for hw in hardware:
            db.insert_hardware(conn, environment="正式", **hw)
        for s in scanned:
            conn.execute(
                "INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) "
                "VALUES (?,?,?,?,1)",
                (SCAN_TIME, s.get("ip"), s.get("hostname"), s.get("segment", "10.0.0.0/24")),
            )
        conn.commit()
    finally:
        conn.close()


def test_三塊數字加總與下鑽筆數完全一致():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed(
                db_path,
                hardware=[
                    {"asset_serial": "A1", "hostname": "h1", "ip": "10.0.0.1"},  # 掃得到
                    {"asset_serial": "A2", "hostname": "h2", "ip": "10.0.0.2"},  # 掃得到
                    {"asset_serial": "A3", "hostname": "h3", "ip": "10.0.0.3"},  # 掃不到
                    {"asset_serial": "A4", "hostname": "h4", "ip": "10.0.0.4"},  # 掃不到
                ],
                scanned=[
                    {"ip": "10.0.0.1", "hostname": "h1"},
                    {"ip": "10.0.0.2", "hostname": "h2"},
                    {"ip": "10.0.0.9", "hostname": "stranger"},  # 掃到但沒登記
                ],
            )
            stats = client.get("/api/dashboard/stats").json()

            assert stats["ica_count"] == 4
            assert stats["overlap_count"] == 2
            assert stats["ica_only_count"] == 2
            assert stats["scan_only_count"] == 1
            # 兩邊相符 + 登記卻掃不到 必須等於 ICA 總數，否則畫面自己就矛盾
            assert stats["overlap_count"] + stats["ica_only_count"] == stats["ica_count"]

            overlap = client.get("/api/assets", params={"scan_status": "overlap"}).json()
            ica_only = client.get("/api/assets", params={"scan_status": "ica_only"}).json()
            unreg = client.get("/api/scan/unregistered").json()

            assert len(overlap) == stats["overlap_count"], "「兩邊相符」磚塊數字與清單筆數不一致"
            assert len(ica_only) == stats["ica_only_count"], "「登記卻掃不到」磚塊數字與清單筆數不一致"
            assert len(unreg) == stats["scan_only_count"], "「掃到卻沒登記」磚塊數字與清單筆數不一致"

            assert {r["asset_serial"] for r in overlap} == {"A1", "A2"}
            assert {r["asset_serial"] for r in ica_only} == {"A3", "A4"}
        finally:
            api.app.dependency_overrides.clear()


def test_一台資產被多筆掃描結果對到也不會算錯():
    """舊算法的破口：overlap 從掃描側數會變 2，ica_count(1) - 2 = -1 台「登記卻掃不到」。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed(
                db_path,
                hardware=[{"asset_serial": "A1", "hostname": "h1", "ip": "10.0.0.1"}],
                scanned=[
                    {"ip": "10.0.0.1", "hostname": "other-name"},  # IP 命中
                    {"ip": "10.0.0.77", "hostname": "h1"},         # 主機名命中（同一台資產）
                ],
            )
            stats = client.get("/api/dashboard/stats").json()

            assert stats["ica_count"] == 1
            assert stats["overlap_count"] == 1, "同一台資產被兩筆掃描對到，仍然只該算一台"
            assert stats["ica_only_count"] == 0, "不該出現負數或灌水的「登記卻掃不到」"
            assert stats["ica_only_count"] >= 0

            overlap = client.get("/api/assets", params={"scan_status": "overlap"}).json()
            assert len(overlap) == stats["overlap_count"]
        finally:
            api.app.dependency_overrides.clear()


def test_掃描結果清單筆數等於本次掃到存活():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed(
                db_path,
                hardware=[{"asset_serial": "A1", "hostname": "h1", "ip": "10.0.0.1"}],
                scanned=[
                    {"ip": "10.0.0.1", "hostname": "h1"},
                    {"ip": "10.0.0.9", "hostname": "stranger"},
                ],
            )
            stats = client.get("/api/dashboard/stats").json()
            results = client.get("/api/scan/results").json()

            assert len(results["items"]) == stats["scanned_count"], (
                "「本次掃到存活」磚塊數字與掃描結果清單筆數不一致"
            )
            assert results["scan_time"] == SCAN_TIME

            reg = [i for i in results["items"] if i["registered"]]
            unreg = [i for i in results["items"] if not i["registered"]]
            assert len(reg) == 1 and reg[0]["asset_serial"] == "A1"
            assert len(unreg) == stats["scan_only_count"]

            # registered 篩選要跟不篩選時的分組一致
            assert len(client.get("/api/scan/results", params={"registered": "yes"}).json()["items"]) == 1
            assert len(client.get("/api/scan/results", params={"registered": "no"}).json()["items"]) == 1
        finally:
            api.app.dependency_overrides.clear()


def test_問題磚塊數字與問題清單一致():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                db.insert_comparison_result(conn, "h1", "10.0.0.1", "漏登記")
                db.insert_comparison_result(conn, "h2", "10.0.0.2", "漏登記")
                db.insert_comparison_result(conn, "h3", "10.0.0.3", "異常新增")
            finally:
                conn.close()

            stats = client.get("/api/dashboard/stats").json()
            for issue_type in ("漏登記", "異常新增", "異常消失"):
                listed = client.get(
                    "/api/issues", params={"issue_type": issue_type, "is_read": False}
                ).json()
                assert len(listed) == stats["issue_counts"][issue_type], (
                    f"「{issue_type}」磚塊數字與清單筆數不一致"
                )
        finally:
            api.app.dependency_overrides.clear()


def test_scan_status_參數擋掉亂填的值():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            r = client.get("/api/assets", params={"scan_status": "亂填"})
            assert r.status_code == 400
            r = client.get("/api/scan/results", params={"registered": "亂填"})
            assert r.status_code == 400
        finally:
            api.app.dependency_overrides.clear()
