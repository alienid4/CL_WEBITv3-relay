"""S5 done_when 驗證：checks 全 PASS，API有基本測試。
覆蓋儀表板統計、問題清單＋標記已處理、資產查詢＋排序搜尋、常用/進階欄位分層、主機詳細頁。
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

# 測試用假密碼抽成常數（同 test_import_api.py 的理由：避免被 checks.py 密鑰掃描誤判）
_STUB_CREDENTIAL = "test-password-123"


def _client(tmp, login=True):
    """預設回傳「已登入」的 client——資產/儀表板等端點現在一律需登入，
    絕大多數測試要驗的是業務邏輯而不是權限，所以預設就帶 session。
    要測未登入行為的傳 login=False（權限本身由 test_auth.py 與
    test_api.py::test_端點未登入一律401 負責）。"""
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
    if login:
        conn = db.get_connection(db_path)
        try:
            db.create_user(conn, "tester", auth.hash_password(_STUB_CREDENTIAL))
        finally:
            conn.close()
        assert client.post(
            "/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}
        ).status_code == 200
    return client, db_path


def _insert_hardware(db_path, asset_serial, hostname, ip, environment="正式", **extra):
    conn = db.get_connection(db_path)
    try:
        fields = {
            "asset_serial": asset_serial,
            "hostname": hostname,
            "ip": ip,
            "environment": environment,
            **extra,
        }
        db.insert_hardware(conn, **fields)
    finally:
        conn.close()


def _insert_scan_row(db_path, scan_time, hostname, ip, segment="機房A", scan_ok=1):
    conn = db.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO scan_history (scan_time, hostname, ip, device_model, is_vm, segment, scan_ok) "
            "VALUES (?, ?, ?, 'Test Model', 0, ?, ?)",
            (scan_time, hostname, ip, segment, scan_ok),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_issue(db_path, hostname, ip, issue_type, is_read=0):
    conn = db.get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO comparison_result (hostname, ip, issue_type, is_read) VALUES (?, ?, ?, ?)",
            (hostname, ip, issue_type, is_read),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_dashboard_stats_default_environment_is_正式():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "host-a", "10.0.0.1", environment="正式")
            _insert_hardware(db_path, "A002", "host-b", "10.0.0.2", environment="測試")
            _insert_scan_row(db_path, "2026-07-17T03:00", "host-a", "10.0.0.1")
            _insert_scan_row(db_path, "2026-07-17T03:00", "host-c", "10.0.0.9")  # 掃到但ICA沒登記

            resp = client.get("/api/dashboard/stats")
            assert resp.status_code == 200
            body = resp.json()
            assert body["environment"] == "正式"
            assert body["ica_count"] == 1  # 只算正式，測試環境的host-b不算
            assert body["scanned_count"] == 2
            assert body["overlap_count"] == 1  # host-a 重疊
            assert body["scan_only_count"] == 1  # host-c 僅掃描到
            assert body["ica_only_count"] == 0
            assert body["last_scan_ok"] is True
            assert body["failed_segments"] == []
        finally:
            api.app.dependency_overrides.clear()


def test_dashboard_stats_supports_combined_and_all_environment_filter():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "host-a", "10.0.0.1", environment="正式")
            _insert_hardware(db_path, "A002", "host-b", "10.0.0.2", environment="測試")
            _insert_hardware(db_path, "A003", "host-c", "10.0.0.3", environment="備援")

            combined = client.get("/api/dashboard/stats", params={"environment": "正式+測試"})
            assert combined.status_code == 200
            assert combined.json()["ica_count"] == 2  # 正式+測試，不含備援

            all_env = client.get("/api/dashboard/stats", params={"environment": "全部"})
            assert all_env.status_code == 200
            assert all_env.json()["ica_count"] == 3  # 全部含備援

            bad = client.get("/api/dashboard/stats", params={"environment": "亂填"})
            assert bad.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_dashboard_stats_reports_failed_segments():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_scan_row(db_path, "2026-07-17T03:00", None, None, segment="機房B", scan_ok=0)

            resp = client.get("/api/dashboard/stats")
            body = resp.json()
            assert body["last_scan_ok"] is False
            assert body["failed_segments"] == ["機房B"]
        finally:
            api.app.dependency_overrides.clear()


def test_list_issues_filters_by_type_and_unread():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_issue(db_path, "host-a", "10.0.0.1", "異常新增")
            _insert_issue(db_path, "host-b", "10.0.0.2", "漏登記")
            _insert_issue(db_path, "host-c", "10.0.0.3", "異常新增", is_read=1)

            resp = client.get("/api/issues", params={"issue_type": "異常新增", "is_read": "false"})
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["hostname"] == "host-a"
        finally:
            api.app.dependency_overrides.clear()


def test_mark_issue_as_read():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            issue_id = _insert_issue(db_path, "host-a", "10.0.0.1", "異常新增")

            resp = client.patch(f"/api/issues/{issue_id}", json={"is_read": True})
            assert resp.status_code == 200
            body = resp.json()
            assert body["is_read"] == 1
            assert body["handled_at"] is not None

            resp_missing = client.patch("/api/issues/99999", json={"is_read": True})
            assert resp_missing.status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_list_assets_excludes_retired_by_default_and_via_drilldown_filters():
    """/api/assets 的口徑要跟 composition()/eos_summary() 一致（都排除退役），
    不然頭條數字比下鑽出來的清單筆數少，使用者會以為算錯了。
    例外：filter_field=asset_status 明確要看某狀態時（含退役徽章本身的連結），
    不能反過來被這條規則擋掉。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "active-1", "10.30.1.1",
                              asset_status="使用中", device_model="Dell R740", os="Rocky Linux 9.7")
            _insert_hardware(db_path, "A002", "retired-1", "10.30.1.2", asset_status="停用")

            # 預設（無篩選）不該看到退役
            resp = client.get("/api/assets")
            assert resp.status_code == 200
            assert {r["asset_serial"] for r in resp.json()} == {"A001"}

            # 明確篩 asset_status=停用 要看得到（退役徽章連結靠這個）
            resp = client.get("/api/assets", params={"filter_field": "asset_status", "filter_value": "停用"})
            assert {r["asset_serial"] for r in resp.json()} == {"A002"}
        finally:
            api.app.dependency_overrides.clear()


def test_list_assets_filter_value_comma_only_multivalue_for_asset_status():
    """自由文字欄位（如備註）剛好含逗號時，要當完整字串比對，不能被誤拆成多值查詢。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "a1", "10.30.2.1", remark="主機房,備援")

            resp = client.get(
                "/api/assets", params={"filter_field": "remark", "filter_value": "主機房,備援"})
            assert resp.status_code == 200
            assert {r["asset_serial"] for r in resp.json()} == {"A001"}
        finally:
            api.app.dependency_overrides.clear()


def test_list_assets_search_and_sort():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "db-app-07", "10.20.30.41")
            _insert_hardware(db_path, "A002", "web-app-01", "10.20.30.10")

            resp = client.get("/api/assets", params={"q": "db-app"})
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["hostname"] == "db-app-07"

            resp_sorted = client.get("/api/assets", params={"sort_by": "ip", "order": "asc"})
            ips = [r["ip"] for r in resp_sorted.json()]
            assert ips == sorted(ips)
        finally:
            api.app.dependency_overrides.clear()


def test_asset_duplicates_excludes_retired_assets():
    """停用/報廢/閒置＝退役資產，不該跟使用中的同 IP 舊料一起被判成「重複登記」——
    那其實是機器汰換的正常歷史，不是重複。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            # 真重複：兩筆都「使用中」，主機名/IP 一樣 → 該抓出來
            _insert_hardware(db_path, "A001", "dup-host", "10.20.30.1", asset_status="使用中")
            _insert_hardware(db_path, "A002", "dup-host", "10.20.30.1", asset_status="使用中")
            # 假重複：舊料已「停用」，新料「使用中」承接同 IP → 不該抓
            _insert_hardware(db_path, "A003", "retired-host", "10.20.30.2", asset_status="停用")
            _insert_hardware(db_path, "A004", "retired-host", "10.20.30.2", asset_status="使用中")
            # 兩筆都退役 → 也不該抓
            _insert_hardware(db_path, "A005", "both-retired", "10.20.30.3", asset_status="報廢")
            _insert_hardware(db_path, "A006", "both-retired", "10.20.30.3", asset_status="閒置")

            resp = client.get("/api/assets/duplicates")
            assert resp.status_code == 200
            data = resp.json()
            hostnames = {g["hostname"] for g in data["groups"]}
            assert hostnames == {"dup-host"}
            assert data["extra_rows"] == 1
        finally:
            api.app.dependency_overrides.clear()


def test_list_assets_filters_by_canonical_os():
    """平台下鑽點某個版本（如「Rocky Linux 9.7」）要能篩到對應資產——
    原始寫法可能是「Rocky Linux 9.7 (Blue Onyx)」，篩選要走正規化後的值。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "db-1", "10.20.30.1",
                              os="Rocky Linux 9.7 (Blue Onyx)")
            _insert_hardware(db_path, "A002", "db-2", "10.20.30.2",
                              os="Rocky Linux 9.7")
            _insert_hardware(db_path, "A003", "db-3", "10.20.30.3",
                              os="Windows Server 2022 Standard")

            resp = client.get("/api/assets", params={"canonical_os": "Rocky Linux 9.7"})
            assert resp.status_code == 200
            rows = resp.json()
            assert {r["asset_serial"] for r in rows} == {"A001", "A002"}
        finally:
            api.app.dependency_overrides.clear()


def test_list_assets_filters_by_canonical_model():
    """EOS 頁點某個硬體型號要能篩到對應資產——道理跟 canonical_os 一樣。
    ⚠️ 2026-08-13 更新：型號規則後來補了 Dell R 系列動態規則（DELL R740 →
    Dell PowerEdge R740），「Dell R740」不再是 unmatched，改用規則算出來的
    canonical 去篩，這樣才是真實用法（畫面上點的本來就是算好的 canonical，
    不是原始 device_model 字串）。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "srv-1", "10.20.30.1", device_model="Dell R740")
            _insert_hardware(db_path, "A002", "srv-2", "10.20.30.2", device_model="Dell R740")
            _insert_hardware(db_path, "A003", "srv-3", "10.20.30.3", device_model="HPE DL360 Gen10")

            resp = client.get("/api/assets", params={"canonical_model": "Dell PowerEdge R740"})
            assert resp.status_code == 200
            rows = resp.json()
            assert {r["asset_serial"] for r in rows} == {"A001", "A002"}
        finally:
            api.app.dependency_overrides.clear()


def test_batch_lookup_matches_exact_hostname_or_ip_only():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "db-app-07", "10.20.30.41")
            _insert_hardware(db_path, "A002", "web-app-01", "10.20.30.10")

            resp = client.post(
                "/api/assets/batch-lookup",
                json={"terms": ["db-app-07", "10.20.30.10", "no-such-host"]},
            )
            assert resp.status_code == 200
            hostnames = {r["hostname"] for r in resp.json()}
            assert hostnames == {"db-app-07", "web-app-01"}

            # 部分比對不該命中（跟list_assets的q模糊搜尋不同，batch-lookup要求精確）
            partial = client.post("/api/assets/batch-lookup", json={"terms": ["db-app"]})
            assert partial.json() == []

            empty = client.post("/api/assets/batch-lookup", json={"terms": []})
            assert empty.status_code == 200
            assert empty.json() == []
        finally:
            api.app.dependency_overrides.clear()


def test_list_personnel_search_and_sort():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "host-a", "10.0.0.1")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO personnel (asset_serial, person_name) VALUES (?, ?)",
                    ("A001", "王小明"),
                )
                conn.execute(
                    "INSERT INTO personnel (asset_serial, person_name) VALUES (?, ?)",
                    ("A001", "陳小華"),
                )
                conn.commit()
            finally:
                conn.close()

            resp = client.get("/api/personnel", params={"q": "王"})
            assert resp.status_code == 200
            names = [r["person_name"] for r in resp.json()]
            assert names == ["王小明"]

            all_resp = client.get("/api/personnel", params={"sort_by": "person_name", "order": "asc"})
            all_names = [r["person_name"] for r in all_resp.json()]
            assert all_names == sorted(all_names)
        finally:
            api.app.dependency_overrides.clear()


def test_list_software_search_and_rejects_bad_sort():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "db-app-07", "10.20.30.41")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO software (asset_serial, asset_name, db_software) VALUES (?, ?, ?)",
                    ("A001", "核心交易DB", "PostgreSQL"),
                )
                conn.commit()
            finally:
                conn.close()

            # q只比對asset_name/hostname/ip，db_software欄位不在搜尋範圍內，查不到是預期行為
            resp = client.get("/api/software", params={"q": "PostgreSQL"})
            assert resp.status_code == 200
            assert resp.json() == []

            resp2 = client.get("/api/software", params={"q": "核心交易"})
            assert resp2.status_code == 200
            assert len(resp2.json()) == 1

            bad_sort = client.get("/api/software", params={"sort_by": "not_a_column"})
            assert bad_sort.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_list_assets_rejects_unknown_sort_field():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            resp = client.get("/api/assets", params={"sort_by": "id; DROP TABLE hardware;--"})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_asset_field_groups_returns_common_and_advanced():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            resp = client.get("/api/assets/field-groups")
            assert resp.status_code == 200
            body = resp.json()
            assert "hostname" in body["hardware"]["common"]
            assert "confidentiality" in body["hardware"]["advanced"]
        finally:
            api.app.dependency_overrides.clear()


def test_asset_detail_includes_personnel_software_history():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "db-app-07", "10.20.30.41")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO personnel (asset_serial, person_name) VALUES (?, ?)",
                    ("A001", "王小明"),
                )
                conn.execute(
                    "INSERT INTO software (asset_serial, db_software) VALUES (?, ?)",
                    ("A001", "PostgreSQL"),
                )
                conn.commit()
            finally:
                conn.close()
            _insert_issue(db_path, "db-app-07", "10.20.30.41", "異常新增")

            resp = client.get("/api/assets/A001")
            assert resp.status_code == 200
            body = resp.json()
            assert body["hardware"]["hostname"] == "db-app-07"
            assert len(body["personnel"]) == 1
            assert body["personnel"][0]["person_name"] == "王小明"
            assert len(body["software"]) == 1
            assert len(body["history"]) == 1

            resp_missing = client.get("/api/assets/NOPE")
            assert resp_missing.status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_asset_detail_includes_eos_when_known(monkeypatch):
    """主機詳細頁要能看到這台的軟硬體 EOS 狀態；查不到的要是 null 不是假資料。"""
    import eos

    monkeypatch.setattr(eos, "_os_table", [
        {"name": "Rocky Linux 9", "eos_date": "2020-01-01", "source_url": "https://x", "note": ""},
    ])
    monkeypatch.setattr(eos, "_hw_table", [])
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "db-app-07", "10.20.30.41",
                              os="Rocky Linux 9.7", device_model="沒收錄的型號")
            resp = client.get("/api/assets/A001")
            assert resp.status_code == 200
            body = resp.json()
            assert body["os_eos"]["eos_date"] == "2020-01-01"
            assert body["os_eos"]["status"] == "expired"   # 2020 早就過了
            assert body["hardware_eos"] is None
        finally:
            api.app.dependency_overrides.clear()


def test_eos_summary_排除退役且分三態統計(monkeypatch):
    import eos

    monkeypatch.setattr(eos, "_os_table", [
        {"name": "Rocky Linux 9", "eos_date": "2020-01-01", "source_url": "https://x", "note": ""},
        {"name": "Windows Server 2022", "eos_date": "2099-01-01", "source_url": "https://y", "note": ""},
    ])
    monkeypatch.setattr(eos, "_hw_table", [])
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "A001", "a1", "10.0.0.1",
                              os="Rocky Linux 9.7", asset_status="使用中")   # expired
            _insert_hardware(db_path, "A002", "a2", "10.0.0.2",
                              os="Windows Server 2022 Standard", asset_status="使用中")   # ok
            _insert_hardware(db_path, "A003", "a3", "10.0.0.3",
                              os="某冷門系統", asset_status="使用中")   # unknown
            # 退役的不該計入
            _insert_hardware(db_path, "A004", "a4", "10.0.0.4",
                              os="Rocky Linux 9.7", asset_status="停用")

            resp = client.get("/api/eos/summary")
            assert resp.status_code == 200
            body = resp.json()
            # ⚠️ 2026-08-13 更新：eos_summary() 回應早就沒有籠統的頂層 "os" 鍵，
            # 分成 host_os/firmware/software/insufficient/other/hardware 六桶
            # （2026-08-12 的分桶需求）——這三筆分別落在 host_os（Rocky／Windows）
            # 跟 other（某冷門系統認不出來）。
            host_os = body["host_os"]["by_status"]
            assert host_os.get("expired", 0) == 1
            assert host_os.get("ok", 0) == 1
            names = {item["name"]: item["count"] for item in body["host_os"]["items"]}
            assert names["Rocky Linux 9.7"] == 1   # 只算 A001，A004 退役排除
            other_names = {item["name"]: item["count"] for item in body["other"]["items"]}
            assert other_names.get("某冷門系統") == 1
        finally:
            api.app.dependency_overrides.clear()


if __name__ == "__main__":
    test_dashboard_stats_default_environment_is_正式()
    test_dashboard_stats_supports_combined_and_all_environment_filter()
    test_dashboard_stats_reports_failed_segments()
    test_list_issues_filters_by_type_and_unread()
    test_mark_issue_as_read()
    test_list_assets_search_and_sort()
    test_batch_lookup_matches_exact_hostname_or_ip_only()
    test_list_personnel_search_and_sort()
    test_list_software_search_and_rejects_bad_sort()
    test_list_assets_rejects_unknown_sort_field()
    test_asset_field_groups_returns_common_and_advanced()
    test_asset_detail_includes_personnel_software_history()
    print("S5 test_api.py: PASS")


def test_排序白名單涵蓋所有實際欄位_但仍擋注入(tmp_path, monkeypatch):
    """天條：表格每一欄都要能排。畫面欄位是 field_meta 動態決定的（數十個進階欄位），
    寫死 12 欄的白名單會讓大部分欄位點下去回 400 → 畫面顯示「載入失敗」，比不能排更糟。

    這支守兩件事同時成立：
    ① 資料表真實存在的欄位都可以排（含 os/owner/remark/hw_serial 這些原本不在白名單的）
    ② 不存在的欄位／注入字串仍然被擋（白名單機制沒有被放寬成什麼都收）
    """
    import api
    import auth
    import db as _db
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    _db.init_db()
    api._COLUMN_CACHE.clear()          # 換 DB 要清快取，否則沿用上一顆的欄位
    conn = _db.get_connection()
    try:
        _db.create_user(conn, "u", auth.hash_password("test-password-123"))
        _db.insert_hardware(conn, asset_serial="A-1", ip="10.0.0.1", environment="正式")
        conn.commit()
    finally:
        conn.close()

    def _override():
        c = _db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        client = TestClient(api.app)
        client.post("/api/auth/login", json={"username": "u", "password": "test-password-123"})

        # ① 原本不在寫死白名單、但資料表真的有的欄位，要能排
        for col in ("os", "owner", "remark", "physical_location", "hw_serial", "subnet"):
            r = client.get("/api/assets", params={"sort_by": col, "order": "desc"})
            assert r.status_code == 200, f"欄位 {col} 應可排序，卻回 {r.status_code}"

        # ② 不存在的欄位／注入字串仍要被擋
        for bad in ("1;DROP TABLE hardware", "no_such_column", "ip; --"):
            r = client.get("/api/assets", params={"sort_by": bad})
            assert r.status_code == 400, f"{bad!r} 應被擋，卻回 {r.status_code}"
    finally:
        api.app.dependency_overrides.clear()
        api._COLUMN_CACHE.clear()


def test_同值篩選_任何欄位點下去都看得到關聯資產(tmp_path, monkeypatch):
    """天條二：畫面上任何一個資料點，點下去都要能看到跟它有關的資料。
    類別型的值（設備機型／群組名稱／環境別…）＝列出所有同值的資產。

    守三件事：① 真實欄位都能篩 ② 空值也能篩（「哪些機器這欄沒填」是有用的盤點問題）
    ③ 不存在的欄位／注入字串仍被擋。
    """
    import api
    import auth
    import db as _db
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    _db.init_db()
    api._COLUMN_CACHE.clear()
    conn = _db.get_connection()
    try:
        _db.create_user(conn, "u", auth.hash_password("test-password-123"))
        _db.insert_hardware(conn, asset_serial="A-1", hostname="a1", device_model="HP DL380",
                            group_name="數位通路", environment="正式")
        _db.insert_hardware(conn, asset_serial="A-2", hostname="a2", device_model="HP DL380",
                            group_name="核心系統", environment="正式")
        _db.insert_hardware(conn, asset_serial="A-3", hostname="a3", device_model=None,
                            group_name="數位通路", environment="測試")
        _db.insert_hardware(conn, asset_serial="A-4", hostname="a4", device_model="Dell R740",
                            group_name="資訊架構", environment="正式", asset_status="停用")
        _db.insert_hardware(conn, asset_serial="A-5", hostname="a5", device_model="Dell R740",
                            group_name="資訊架構", environment="正式", asset_status="報廢")
        conn.commit()
    finally:
        conn.close()

    def _override():
        c = _db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        client = TestClient(api.app)
        client.post("/api/auth/login", json={"username": "u", "password": "test-password-123"})

        # ① 點「HP DL380」要看到兩台
        r = client.get("/api/assets", params={"filter_field": "device_model", "filter_value": "HP DL380"})
        assert r.status_code == 200 and len(r.json()) == 2

        # 點「數位通路」這個群組，要看到該群組下的兩台
        r = client.get("/api/assets", params={"filter_field": "group_name", "filter_value": "數位通路"})
        assert len(r.json()) == 2

        # ② 空值也要能篩：哪台沒填設備機型
        r = client.get("/api/assets", params={"filter_field": "device_model", "filter_value": ""})
        assert len(r.json()) == 1 and r.json()[0]["asset_serial"] == "A-3"

        # ②.5 多值篩選（逗號分隔）：「退役」＝停用,報廢,閒置 三種狀態合看
        r = client.get(
            "/api/assets",
            params={"filter_field": "asset_status", "filter_value": "停用,報廢"},
        )
        assert r.status_code == 200
        rows = r.json()
        assert {row["asset_serial"] for row in rows} == {"A-4", "A-5"}
        assert {row["asset_status"] for row in rows} == {"停用", "報廢"}

        # ③ 注入／不存在欄位仍被擋
        for bad in ("1;DROP TABLE hardware", "no_such_col"):
            assert client.get("/api/assets", params={"filter_field": bad}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()
        api._COLUMN_CACHE.clear()


def test_資產可編輯_主鍵不可改_亂欄位被擋(tmp_path, monkeypatch):
    """在這之前系統完全沒有編輯功能——資料進來只能靠重新匯入 Excel 覆蓋，
    連打錯一個字都要重跑匯入。盤點資料本來就會持續被修正，這是必要缺口。

    守四件事：① 改得動 ② 空字串存成 NULL（不然「空」會有 '' 和 NULL 兩種寫法，
    篩選排序就分岔）③ asset_serial 主鍵不給改 ④ 不存在的欄位被擋。
    """
    import api
    import auth
    import db as _db
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    _db.init_db()
    api._COLUMN_CACHE.clear()
    conn = _db.get_connection()
    try:
        _db.create_user(conn, "u", auth.hash_password("test-password-123"))
        _db.insert_hardware(conn, asset_serial="E-1", hostname="old-name",
                            ip="10.9.9.9", custodian="舊保管者", environment="正式")
        conn.commit()
    finally:
        conn.close()

    def _override():
        c = _db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        client = TestClient(api.app)
        client.post("/api/auth/login", json={"username": "u", "password": "test-password-123"})

        # ① 改得動
        r = client.put("/api/assets/E-1", json={"fields": {"hostname": "new-name", "custodian": "新保管者"}})
        assert r.status_code == 200, r.text
        assert r.json()["hardware"]["hostname"] == "new-name"

        # ② 空字串 → NULL
        r = client.put("/api/assets/E-1", json={"fields": {"custodian": ""}})
        assert r.json()["hardware"]["custodian"] is None

        # ③ 主鍵不可改（送了也不會生效，序號仍查得到原本那筆）
        client.put("/api/assets/E-1", json={"fields": {"asset_serial": "HACKED"}})
        assert client.get("/api/assets/E-1").status_code == 200
        assert client.get("/api/assets/HACKED").status_code == 404

        # ④ 亂欄位被擋
        r = client.put("/api/assets/E-1", json={"fields": {"no_such_col": "x"}})
        assert r.status_code == 400

        # 查無此資產
        assert client.put("/api/assets/NOPE", json={"fields": {"hostname": "x"}}).status_code == 404
    finally:
        api.app.dependency_overrides.clear()
        api._COLUMN_CACHE.clear()


def test_虛擬實體篩選_容忍混形態(tmp_path, monkeypatch):
    """使用者要「點虛擬機看是哪 7 台」。is_vm 在資料裡混了 0/1 與 'VM' 字串
    （納管表單存字串），不能直接 filter_field=is_vm 比值，要專門的 virtual 參數。"""
    import api
    import auth
    import db as _db
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    _db.init_db()
    api._COLUMN_CACHE.clear()
    conn = _db.get_connection()
    try:
        _db.create_user(conn, "u", auth.hash_password("test-password-123"))
        _db.insert_hardware(conn, asset_serial="V-1", ip="10.0.0.1", is_vm=1, environment="正式")
        _db.insert_hardware(conn, asset_serial="V-2", ip="10.0.0.2", is_vm="VM", environment="正式")
        _db.insert_hardware(conn, asset_serial="P-1", ip="10.0.0.3", is_vm=0, environment="正式")
        conn.commit()
    finally:
        conn.close()

    def _override():
        c = _db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        client = TestClient(api.app)
        client.post("/api/auth/login", json={"username": "u", "password": "test-password-123"})
        vm = client.get("/api/assets", params={"virtual": "yes"}).json()
        phys = client.get("/api/assets", params={"virtual": "no"}).json()
        # 1 與 'VM' 都算虛擬機
        assert {r["asset_serial"] for r in vm} == {"V-1", "V-2"}
        assert {r["asset_serial"] for r in phys} == {"P-1"}
    finally:
        api.app.dependency_overrides.clear()


def test_手動新增資產():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/manual", json={"fields": {
                "asset_serial": "MANUAL-001", "hostname": "test-host",
                "ip": "YOUR_CLIENT_IP", "os": "Rocky Linux 9.7", "environment": "測試",
            }})
            assert resp.status_code == 200
            body = resp.json()["hardware"]
            assert body["asset_serial"] == "MANUAL-001"
            assert body["hostname"] == "test-host"
            assert body["environment"] == "測試"

            listed = client.get("/api/assets").json()
            assert "MANUAL-001" in {r["asset_serial"] for r in listed}
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_序號必填():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/manual", json={"fields": {"hostname": "no-serial"}})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_序號重複擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "DUP-001", "existing", "10.0.0.1")
            resp = client.post("/api/assets/manual", json={"fields": {"asset_serial": "DUP-001"}})
            assert resp.status_code == 409
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_不支援欄位擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/manual", json={"fields": {
                "asset_serial": "BAD-001", "not_a_real_column": "x",
            }})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_欄位選單依現有資料種類數判斷():
    """種類數少（<=30）的欄位才給選單；種類多（近乎每筆不同）的欄位不給，維持自由輸入。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            envs = ["正式", "測試", "備援"]
            for i in range(35):
                _insert_hardware(
                    db_path, f"OPT-{i}", f"host-{i}", f"10.0.0.{i}",
                    environment=envs[i % 3],
                    asset_purpose=f"用途說明第{i}種完全不重複的文字",
                )

            resp = client.get("/api/assets/manual/field-options")
            assert resp.status_code == 200
            options = resp.json()
            assert set(options["environment"]) == set(envs)
            assert "asset_purpose" not in options
            assert "asset_serial" not in options
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_保管者使用者選單來自人員維護不是hardware歷史值():
    """保管者/使用者的選單來源要是 personnel.person_name（人員維護的真相來源），
    不是 hardware 表自己的歷史值——避免同一個人被打成好幾種錯字寫法都能選。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "P-001", "h1", "10.0.1.1", custodian="王小明ERROR")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO personnel (asset_serial, person_name) VALUES (?, ?)",
                    ("P-001", "王小明"),
                )
                conn.execute(
                    "INSERT INTO personnel (asset_serial, person_name) VALUES (?, ?)",
                    ("P-001", "陳大華"),
                )
                conn.commit()
            finally:
                conn.close()

            options = client.get("/api/assets/manual/field-options").json()
            assert set(options["custodian"]) == {"王小明", "陳大華"}
            assert set(options["user_name"]) == {"王小明", "陳大華"}
            assert "王小明ERROR" not in options["custodian"]
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_欄位分組回傳且欄位名都對得上資料表():
    """分組設定檔裡打錯欄位名不會噴錯，只會讓那欄悄悄掉進「其他」組——
    是安靜故障，所以用測試擋：每個列到的欄位都必須是 hardware 的真欄位。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.get("/api/assets/manual/field-groups")
            assert resp.status_code == 200
            groups = resp.json()["groups"]
            assert len(groups) >= 1

            conn = db.get_connection(db_path)
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
            finally:
                conn.close()

            listed = [f for g in groups for f in g["fields"]]
            assert not [f for f in listed if f not in cols], "分組設定檔有不存在的欄位名"
            assert len(listed) == len(set(listed)), "同一個欄位被分到兩組"
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_匯入對應表的欄位都有分到組():
    """field_mapping.json 加了新欄位卻忘了分組時，畫面會把它丟到最後的「其他」組。
    不會壞，但那欄等於沒人排過版——用測試提醒補上，不要放著長期積欠。"""
    import json as _json
    from pathlib import Path as _Path

    backend = _Path(api.__file__).parent
    mapped = set(
        _json.loads((backend / "field_mapping.json").read_text(encoding="utf-8"))["硬體"].values()
    )
    grouped = {
        f
        for g in _json.loads(
            (backend / "manual_form_groups.json").read_text(encoding="utf-8")
        )["groups"]
        for f in g["fields"]
    }
    assert not (mapped - grouped), f"這些欄位還沒分組：{sorted(mapped - grouped)}"


def test_手動新增資產_IP衝突擋下_只擋使用中():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "IPCONF-001", "existing-host", "10.0.2.5", asset_status="使用中")
            resp = client.post("/api/assets/manual", json={"fields": {
                "asset_serial": "IPCONF-002", "ip": "10.0.2.5",
            }})
            assert resp.status_code == 409
            assert "IPCONF-001" in resp.json()["detail"]
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增資產_IP衝突不擋已停用資產():
    """撞號的資產已停用/報廢，IP 合法可能被重新分配，不該連這種正常情境都擋死。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "IPCONF-003", "retired-host", "10.0.2.6", asset_status="報廢")
            resp = client.post("/api/assets/manual", json={"fields": {
                "asset_serial": "IPCONF-004", "ip": "10.0.2.6",
            }})
            assert resp.status_code == 200
        finally:
            api.app.dependency_overrides.clear()


def test_os目錄_分層依家族發行版分組():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "OS-1", "h1", "10.0.3.1", os="Rocky Linux 9.7")
            _insert_hardware(db_path, "OS-2", "h2", "10.0.3.2", os="Red Hat Enterprise Linux 8.10")
            _insert_hardware(db_path, "OS-3", "h3", "10.0.3.3", os="Windows Server 2022 Standard")
            _insert_hardware(db_path, "OS-4", "h4", "10.0.3.4", os="這是一個完全認不出來的怪字串")

            catalog = client.get("/api/os-catalog").json()
            assert "Rocky Linux" in catalog["Linux"]
            assert "Rocky Linux 9.7" in catalog["Linux"]["Rocky Linux"]
            assert "RHEL" in catalog["Linux"]
            assert "Red Hat Enterprise Linux 8.10" in catalog["Linux"]["RHEL"]
            assert "Windows" in catalog
            # 認不出來的不會憑空消失，歸進其他/未分類，原字串還在
            all_values = [v for distros in catalog.values() for vs in distros.values() for v in vs]
            assert "這是一個完全認不出來的怪字串" in all_values
        finally:
            api.app.dependency_overrides.clear()


def test_網段掃描匯入_建立DYN資產():
    """/api/assets/scan/import 借道 dynassets 匯入管道，只給機器事實就能建資產。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/scan/import", json={"hosts": [
                {"ip": "YOUR_CLIENT_IP", "hostname": "demo-client1"},
                {"ip": "YOUR_CLIENT_IP2", "hostname": None},
            ]})
            assert resp.status_code == 200
            summary = resp.json()
            assert summary["inserted"] == 2

            listed = client.get("/api/assets").json()
            by_ip = {r["ip"]: r for r in listed}
            assert by_ip["YOUR_CLIENT_IP"]["hostname"] == "demo-client1"
            assert by_ip["YOUR_CLIENT_IP"]["asset_serial"].startswith("DYN-")
        finally:
            api.app.dependency_overrides.clear()


def test_網段掃描匯入_空清單擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/scan/import", json={"hosts": []})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_網段掃描發現_擋公網網段():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/scan/discover", json={"cidr": "8.8.8.0/24"})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_網段掃描發現_擋超大網段():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/scan/discover", json={"cidr": "10.0.0.0/16"})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()
        api._COLUMN_CACHE.clear()


def test_功能開關_新模組都在清單裡且可停用():
    """使用者 2026-08-15：「現在沒有帳號、沒有服務盤點，可以在系統上把功能 disable 嗎」。
    機制本來就有，但這一個多月新做的模組都沒進清單＝在系統設定裡根本關不掉。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            flags = {f["module_key"]: f for f in client.get("/api/feature-flags").json()}
            for key in ("golive", "documents", "segments", "data_quality", "eos", "adopt",
                        "accounts", "services", "topology", "assets", "import", "dashboard"):
                assert key in flags, f"{key} 沒有功能開關，等於關不掉"

            r = client.put("/api/feature-flags/documents", json={"enabled": False})
            assert r.status_code == 200 and r.json()["enabled"] == 0
            assert client.get("/api/feature-flags").json()
            again = {f["module_key"]: f for f in client.get("/api/feature-flags").json()}
            assert again["documents"]["enabled"] == 0
        finally:
            api.app.dependency_overrides.clear()


def _dump_test_client(tmp_path, monkeypatch):
    """跟 test_虛擬實體篩選_容忍混形態 同一套：/api/backup/dump 用的是 get_db_path()
    （直接讀 ASSET_DB_PATH 環境變數），不是 get_db 依賴注入，_client() 那套 override
    對它沒用——要用 monkeypatch 設環境變數，backup.snapshot() 才會對到測試用的 DB。"""
    import auth
    import db as _db
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    _db.init_db()
    api._COLUMN_CACHE.clear()
    conn = _db.get_connection()
    try:
        _db.create_user(conn, "u", auth.hash_password("test-password-123"))
        conn.commit()
    finally:
        conn.close()

    def _override():
        c = _db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    assert client.post("/api/auth/login", json={"username": "u", "password": "test-password-123"}).status_code == 200
    return client


def test_匯出全部資料_二進位格式可以重新打開且資料完整(tmp_path, monkeypatch):
    import sqlite3
    import db as _db

    client = _dump_test_client(tmp_path, monkeypatch)
    try:
        conn = _db.get_connection()
        try:
            _db.insert_hardware(conn, asset_serial="DUMP-001", hostname="dump-host-1", ip="10.5.0.1", environment="正式")
            _db.insert_hardware(conn, asset_serial="DUMP-002", hostname="dump-host-2", ip="10.5.0.2", environment="正式")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/backup/dump", params={"fmt": "binary"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert ".db" in resp.headers["content-disposition"]

        out_path = tmp_path / "downloaded.db"
        out_path.write_bytes(resp.content)
        out_conn = sqlite3.connect(out_path)
        try:
            integrity = out_conn.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok"
            count = out_conn.execute(
                "SELECT COUNT(*) FROM hardware WHERE asset_serial IN ('DUMP-001','DUMP-002')"
            ).fetchone()[0]
            assert count == 2
        finally:
            out_conn.close()
    finally:
        api.app.dependency_overrides.clear()


def test_匯出全部資料_文字格式含完整SQL(tmp_path, monkeypatch):
    import db as _db

    client = _dump_test_client(tmp_path, monkeypatch)
    try:
        conn = _db.get_connection()
        try:
            _db.insert_hardware(conn, asset_serial="DUMP-SQL-001", hostname="dump-sql-host", ip="10.5.0.3", environment="正式")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/backup/dump", params={"fmt": "sql"})
        assert resp.status_code == 200
        assert ".sql" in resp.headers["content-disposition"]
        text = resp.text
        assert "CREATE TABLE" in text
        assert "DUMP-SQL-001" in text
    finally:
        api.app.dependency_overrides.clear()


def test_匯出全部資料_不支援格式擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.get("/api/backup/dump", params={"fmt": "csv"})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_匯出全部資料_未登入擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp, login=False)
        try:
            resp = client.get("/api/backup/dump")
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_資料庫還原_合法檔案覆蓋成功(tmp_path, monkeypatch):
    import db as _db

    client = _dump_test_client(tmp_path, monkeypatch)
    try:
        conn = _db.get_connection()
        try:
            _db.insert_hardware(conn, asset_serial="RESTORE-OLD", hostname="old", ip="10.6.0.1")
            conn.commit()
        finally:
            conn.close()

        # 準備一份「上傳檔」：另一個獨立的資料庫，內容跟正本不一樣
        upload_path = tmp_path / "upload.db"
        _db.init_db(upload_path)
        up_conn = _db.get_connection(upload_path)
        try:
            _db.insert_hardware(up_conn, asset_serial="RESTORE-NEW", hostname="new", ip="10.6.0.2")
            up_conn.commit()
        finally:
            up_conn.close()

        resp = client.post(
            "/api/backup/restore",
            files={"file": ("upload.db", upload_path.read_bytes(), "application/octet-stream")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["pre_restore_backup"]

        conn = _db.get_connection()
        try:
            rows = {r["asset_serial"] for r in conn.execute("SELECT asset_serial FROM hardware")}
            assert rows == {"RESTORE-NEW"}
        finally:
            conn.close()
    finally:
        api.app.dependency_overrides.clear()


def test_資料庫還原_拒絕非db副檔名():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post(
                "/api/backup/restore",
                files={"file": ("upload.txt", b"not a db", "text/plain")},
            )
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_資料庫還原_拒絕非法檔案內容():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post(
                "/api/backup/restore",
                files={"file": ("upload.db", b"garbage not sqlite", "application/octet-stream")},
            )
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_資料庫還原_未登入擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp, login=False)
        try:
            resp = client.post(
                "/api/backup/restore",
                files={"file": ("upload.db", b"x", "application/octet-stream")},
            )
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_匯入紀錄端點_可依來源篩選且未登入擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                db.create_import_log(
                    conn, imported_by="devuser", hardware_count=10, personnel_count=0,
                    software_count=0, error_count=0, source="rvtools", file_name="機房A.xlsx",
                )
                db.create_import_log(
                    conn, imported_by="devuser", hardware_count=100, personnel_count=0,
                    software_count=0, error_count=0, source="cia_excel", file_name="全公司.xlsx",
                )
            finally:
                conn.close()

            resp = client.get("/api/import/log", params={"source": "rvtools"})
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["file_name"] == "機房A.xlsx"

            all_resp = client.get("/api/import/log").json()
            assert len(all_resp) == 2
        finally:
            api.app.dependency_overrides.clear()

    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp, login=False)
        try:
            assert client.get("/api/import/log").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_CI圖譜重建端點():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "CI-001", "ci-host-1", "10.7.0.1")

            resp = client.post("/api/ci/rebuild")
            assert resp.status_code == 200
            body = resp.json()
            assert body["node_count"] >= 1

            status = client.get("/api/ci/rebuild/status")
            assert status.status_code == 200
            assert status.json()["status"] == "done"
        finally:
            api.app.dependency_overrides.clear()


def test_CI圖譜重建端點_未登入擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp, login=False)
        try:
            assert client.post("/api/ci/rebuild").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_影響範圍查詢_resolve與impact端點():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "BLAST-A", "blast-host-a", "10.8.0.1")
            _insert_hardware(db_path, "BLAST-B", "blast-host-b", "10.8.0.2")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:BLAST-A','host','blast-host-a',"
                    "'BLAST-A','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:BLAST-B','host','blast-host-b',"
                    "'BLAST-B','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,"
                    "source,created_at) VALUES ('hw:BLAST-A','hw:BLAST-B','depends_on',"
                    "'證據','manual','t')"
                )
                conn.commit()
            finally:
                conn.close()

            resolved = client.get("/api/blast/resolve", params={"q": "10.8.0.2"})
            assert resolved.status_code == 200
            assert resolved.json()["status"] == "resolved"

            impact = client.get("/api/blast/impact", params={"node_id": "hw:BLAST-B"})
            assert impact.status_code == 200
            assert "hw:BLAST-A" in {d["node_id"] for d in impact.json()["dependents"]}

            graph = client.get("/api/blast/graph", params={"node_id": "hw:BLAST-B"})
            assert graph.status_code == 200
            assert len(graph.json()["elements"]["nodes"]) >= 2

            missing = client.get("/api/blast/impact", params={"node_id": "hw:NOT-EXIST"})
            assert missing.status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_影響範圍查詢端點_未登入擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp, login=False)
        try:
            assert client.get("/api/blast/resolve", params={"q": "x"}).status_code == 401
            assert client.get("/api/blast/impact", params={"node_id": "x"}).status_code == 401
            assert client.get("/api/blast/graph", params={"node_id": "x"}).status_code == 401
            assert client.post("/api/blast/snapshot", json={"node_id": "x"}).status_code == 401
            assert client.get("/api/blast/snapshots").status_code == 401
            assert client.get("/api/blast/snapshot/1").status_code == 401
            assert client.get("/api/blast/snapshot/1/csv").status_code == 401
            assert client.get("/api/blast/systems").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_業務系統瀏覽清單端點():
    """2026-08-19 使用者拍板：不做全部關聯圖（5148節點畫一張圖是毛球），
    做瀏覽清單當入口。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(
                db_path, "SYS-1", "sys-host-1", "10.9.9.1",
                api_id="N-999", asset_name="測試業務系統", usage_unit="測試部門",
                custodian="測試保管者", availability=3,
            )
            _insert_hardware(
                db_path, "SYS-2", "sys-host-2", "10.9.9.2",
                api_id="N-999", asset_name="測試業務系統", availability=1,
            )

            resp = client.get("/api/blast/systems")
            assert resp.status_code == 200
            rows = resp.json()
            found = next(r for r in rows if r["api_id"] == "N-999")
            assert found["node_id"] == "bizsys:N-999"
            assert found["name"] == "測試業務系統"
            assert found["usage_unit"] == "測試部門"
            assert found["severity"] == "重大"  # max(availability)=3
            assert found["asset_count"] == 2
        finally:
            api.app.dependency_overrides.clear()


def test_計畫性停機快照_存讀清單CSV端點():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "SNAP-A", "snap-host-a", "10.9.0.1")
            _insert_hardware(db_path, "SNAP-B", "snap-host-b", "10.9.0.2")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:SNAP-A','host','snap-host-a',"
                    "'SNAP-A','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:SNAP-B','host','snap-host-b',"
                    "'SNAP-B','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,"
                    "source,created_at) VALUES ('hw:SNAP-B','hw:SNAP-A','depends_on',"
                    "'證據','manual','t')"
                )
                conn.commit()
            finally:
                conn.close()

            created = client.post(
                "/api/blast/snapshot",
                json={"node_id": "hw:SNAP-A", "reason": "換機測試"},
            )
            assert created.status_code == 200
            snap_id = created.json()["id"]
            assert created.json()["result"]["dependents"][0]["node_id"] == "hw:SNAP-B"

            listed = client.get("/api/blast/snapshots", params={"node_id": "hw:SNAP-A"})
            assert listed.status_code == 200
            assert len(listed.json()) == 1
            assert listed.json()[0]["id"] == snap_id

            got = client.get(f"/api/blast/snapshot/{snap_id}")
            assert got.status_code == 200
            assert got.json()["reason"] == "換機測試"

            csv_resp = client.get(f"/api/blast/snapshot/{snap_id}/csv")
            assert csv_resp.status_code == 200
            assert csv_resp.headers["content-type"].startswith("text/csv")
            # CSV 給人看的，要顯示主機名（人看得懂）不是內部 node_id
            assert "snap-host-b" in csv_resp.text
            assert "hw:SNAP-B" not in csv_resp.text

            missing = client.get("/api/blast/snapshot/999999")
            assert missing.status_code == 404

            not_found = client.post("/api/blast/snapshot", json={"node_id": "hw:NOT-EXIST"})
            assert not_found.status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_檢查清單_建立更新與未登入擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(
                db_path, "CHK-A", "chk-host-a", "10.9.2.1",
                api_id="N-500", asset_name="檢查清單測試系統",
                user_name="測試負責人", usage_unit="測試部門",
            )
            _insert_hardware(db_path, "CHK-B", "chk-host-b", "10.9.2.2")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:CHK-A','host','chk-host-a',"
                    "'CHK-A','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:CHK-B','host','chk-host-b',"
                    "'CHK-B','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,"
                    "source,created_at) VALUES ('hw:CHK-A','hw:CHK-B','depends_on',"
                    "'證據','manual','t')"
                )
                conn.commit()
            finally:
                conn.close()

            snap = client.post(
                "/api/blast/snapshot", json={"node_id": "hw:CHK-B", "mode": "incident"},
            )
            assert snap.status_code == 200
            snap_id = snap.json()["id"]
            assert snap.json()["mode"] == "incident"

            created = client.post("/api/blast/checklist", json={"snapshot_id": snap_id})
            assert created.status_code == 200
            items = created.json()
            assert len(items) == 1
            assert items[0]["hostname"] == "chk-host-a"
            assert items[0]["contact_name"] == "測試負責人"
            assert items[0]["status"] == "未聯絡"
            item_id = items[0]["id"]

            # 冪等
            again = client.post("/api/blast/checklist", json={"snapshot_id": snap_id})
            assert len(again.json()) == 1

            listed = client.get(f"/api/blast/checklist/{snap_id}")
            assert listed.status_code == 200
            assert len(listed.json()) == 1

            updated = client.put(
                f"/api/blast/checklist/item/{item_id}",
                json={"status": "已確認正常", "note": "電話確認過了"},
            )
            assert updated.status_code == 200
            assert updated.json()["status"] == "已確認正常"
            assert updated.json()["note"] == "電話確認過了"
            assert updated.json()["updated_by"] == "tester"

            bad_status = client.put(
                f"/api/blast/checklist/item/{item_id}", json={"status": "亂寫的狀態"},
            )
            assert bad_status.status_code == 404

            missing_item = client.put(
                "/api/blast/checklist/item/999999", json={"status": "已確認正常"},
            )
            assert missing_item.status_code == 404

            missing_snap = client.post("/api/blast/checklist", json={"snapshot_id": 999999})
            assert missing_snap.status_code == 404
        finally:
            api.app.dependency_overrides.clear()

    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp, login=False)
        try:
            assert client.post("/api/blast/checklist", json={"snapshot_id": 1}).status_code == 401
            assert client.get("/api/blast/checklist/1").status_code == 401
            assert client.put("/api/blast/checklist/item/1", json={"status": "未聯絡"}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_即時查詢直接匯出CSV不用先存快照():
    """2026-08-19 使用者原話：拿到影響範圍圖第一件事就是匯出聯絡資料發給每個人
    去盤點——事故當下不該要求「先存快照才能匯出」，直接查直接匯出。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "CSV-A", "csv-host-a", "10.9.1.1")
            _insert_hardware(db_path, "CSV-B", "csv-host-b", "10.9.1.2")
            conn = db.get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO personnel (asset_serial, person_name, phone, "
                    "belong_division, belong_department) VALUES (?,?,?,?,?)",
                    ("CSV-B", "王小明", "0912-345-678", "資訊部", "平台組"),
                )
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:CSV-A','host','csv-host-a',"
                    "'CSV-A','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,"
                    "created_at,updated_at) VALUES ('hw:CSV-B','host','csv-host-b',"
                    "'CSV-B','derive:hardware','t','t')"
                )
                conn.execute(
                    "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,"
                    "source,created_at) VALUES ('hw:CSV-B','hw:CSV-A','depends_on',"
                    "'證據','manual','t')"
                )
                conn.commit()
            finally:
                conn.close()

            resp = client.get("/api/blast/impact/csv", params={"node_id": "hw:CSV-A"})
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/csv")
            assert "csv-host-b" in resp.text  # 受影響節點顯示名字不是node_id
            assert "hw:CSV-B" not in resp.text
            assert "王小明" in resp.text
            assert "資訊部 平台組" in resp.text  # 部門
            assert "0912-345-678" in resp.text

            missing = client.get("/api/blast/impact/csv", params={"node_id": "hw:NOT-EXIST"})
            assert missing.status_code == 404
        finally:
            api.app.dependency_overrides.clear()
