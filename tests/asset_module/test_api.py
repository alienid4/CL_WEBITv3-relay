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
        api._COLUMN_CACHE.clear()
