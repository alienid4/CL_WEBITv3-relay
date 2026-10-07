"""S13 done_when 驗證：checks 全 PASS。
覆蓋D28功能模組開關：清單/切換，以及「系統設定」自己刻意不列入避免自鎖死。
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
    return TestClient(api.app), db_path


def _login(client, db_path, username="tester", password=_STUB_CREDENTIAL):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, username, auth.hash_password(password))
    finally:
        conn.close()
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200


def test_feature_flag_endpoints_require_login():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            assert client.get("/api/feature-flags").status_code == 401
            assert client.put("/api/feature-flags/dashboard", json={"enabled": False}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_list_seeds_default_modules_all_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            resp = client.get("/api/feature-flags")
            assert resp.status_code == 200
            rows = resp.json()
            keys = {r["module_key"] for r in rows}
            # 這條刻意寫死完整清單，當「新功能忘了加開關」的守門測試：
            # 新做一個模組卻沒進 feature_flags，等於在系統設定裡關不掉
            # （2026-08-15 就是這樣，一個多月新增的 6 個模組全都關不掉）。
            assert keys == {
                "dashboard", "assets", "import", "topology", "services", "accounts",
                "golive", "documents", "segments", "data_quality", "eos", "adopt",
                "pipeline", "blast", "restore", "report_system", "activity",
            }
            # 預設關閉的模組，各有各的理由：
            #   blast（MICS切片2）2026-08-18 拍板：等使用者驗證過再手動開
            #   restore（資料庫還原）2026-08-19 拍板：平時就該關著，要用才開、用完關回去
            #   report_system（系統組報告）2026-08-21：照新規則「新模組一律預設關閉」，
            #     由使用者驗過內容再自己開，不要一部署就出現在同事的選單上
            # activity（在線人數與操作紀錄）刻意**不在** DEFAULT_OFF：它不是
            # 「多一個功能頁」而是「這台系統有沒有在留痕」。預設關掉的話，出事要查
            # 的時候才發現沒開，那段時間的紀錄永遠補不回來。
            DEFAULT_OFF = {"blast", "restore", "report_system"}
            enabled_map = {r["module_key"]: r["enabled"] for r in rows}
            for k in DEFAULT_OFF:
                assert enabled_map[k] == 0, k
            assert all(v == 1 for k, v in enabled_map.items() if k not in DEFAULT_OFF)
        finally:
            api.app.dependency_overrides.clear()


def test_settings_module_is_not_toggleable():
    """D28精神延伸：系統設定是切換開關的畫面本身，不能被自己鎖死，所以根本不在清單裡，
    連嘗試關閉都要404，不是「關閉了但沒效果」這種容易誤用的設計。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            resp = client.put("/api/feature-flags/settings", json={"enabled": False})
            assert resp.status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_toggle_module_off_and_back_on():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)

            off_resp = client.put("/api/feature-flags/import", json={"enabled": False})
            assert off_resp.status_code == 200
            assert off_resp.json()["enabled"] == 0

            listed = client.get("/api/feature-flags").json()
            import_flag = next(r for r in listed if r["module_key"] == "import")
            assert import_flag["enabled"] == 0

            on_resp = client.put("/api/feature-flags/import", json={"enabled": True})
            assert on_resp.json()["enabled"] == 1
        finally:
            api.app.dependency_overrides.clear()


def test_toggle_unknown_module_is_404():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            resp = client.put("/api/feature-flags/no-such-module", json={"enabled": False})
            assert resp.status_code == 404
        finally:
            api.app.dependency_overrides.clear()


if __name__ == "__main__":
    test_feature_flag_endpoints_require_login()
    test_list_seeds_default_modules_all_enabled()
    test_settings_module_is_not_toggleable()
    test_toggle_module_off_and_back_on()
    test_toggle_unknown_module_is_404()
    print("S13 test_feature_flags_api.py: PASS")
