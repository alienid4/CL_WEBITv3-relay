"""全測試共用：每個測試開始前清掉全機隊計算的跨請求快取（fleet_cache）。

快取是行程層級的；測試之間如果在「資料庫檔案沒變」的情況下換掉內部函式（monkeypatch），
可能拿到前一個測試算好的結果——那是假綠燈（2026-10-21 獨立審查 #4）。
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "APP" / "asset-module" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture(autouse=True)
def _clear_fleet_cache():
    try:
        import fleet_cache
    except Exception:  # noqa: BLE001 - 不是資產模組的測試環境：沒有快取可清
        yield
        return
    fleet_cache.clear()
    yield
    fleet_cache.clear()


# ===== Q91（權限分級＋CSRF）給「Q91 之前寫的」測試的相容層 =====
# 1. 預設角色：db.create_user 沒指定 role 時，正式行為是 admin 帳號→admin、其餘→一般(user)。
#    Q91 之前的測試用 "tester" 之類的帳號登入後呼叫各種寫入／收集端點，它們測的是端點本身的行為、不是權限；
#    所以測試環境預設把「沒指定 role 的帳號」建成 admin。
#    要測權限的測試檔：模組層加 `ROLE_AWARE_TESTS = True`（取消這個預設，回到正式行為），或建帳號時明確傳 role=。
# 2. CSRF：瀏覽器的前端會自動把 csrf cookie 抄進 X-CSRF-Token。TestClient 沒有前端，這裡模擬同一件事：
#    帶著 session cookie 的非 GET 請求，自動補上正確的 X-CSRF-Token。
#    要測 CSRF 本身的測試檔：模組層加 `CSRF_RAW_CLIENT = True`（不自動補），或自己在 headers 傳 X-CSRF-Token（包含空字串）。
@pytest.fixture(autouse=True)
def _q91_test_compat(request, monkeypatch):
    mod = request.module
    try:
        import db as _db
    except Exception:  # noqa: BLE001 - 不是資產模組的測試環境
        yield
        return
    if not getattr(mod, "ROLE_AWARE_TESTS", False):
        _orig_create_user = _db.create_user

        def _create_user(conn, username, password_hash, role=None):
            return _orig_create_user(conn, username, password_hash, role or "admin")

        monkeypatch.setattr(_db, "create_user", _create_user)
    if not getattr(mod, "CSRF_RAW_CLIENT", False):
        from starlette.testclient import TestClient

        _orig_request = TestClient.request

        def _request(self, method, url, *args, **kwargs):
            if str(method).upper() not in ("GET", "HEAD", "OPTIONS"):
                headers = dict(kwargs.get("headers") or {})
                if not any(k.lower() == "x-csrf-token" for k in headers):
                    try:
                        tok = self.cookies.get("session_token")
                    except Exception:  # noqa: BLE001 - 多個同名 cookie 等：不補，讓測試自己處理
                        tok = None
                    if tok:
                        import auth as _auth

                        headers["X-CSRF-Token"] = _auth.csrf_token_for(tok)
                        kwargs["headers"] = headers
            return _orig_request(self, method, url, *args, **kwargs)

        monkeypatch.setattr(TestClient, "request", _request)
    yield
