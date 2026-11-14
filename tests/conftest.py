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


@pytest.fixture(autouse=True)
def _quiet_window_pinned_outside_market():
    """AUD-02：全站盤中靜默窗讓「系統自己發動」的動作在平日 08:30–15:00 一律跳過。
    其他測試測的是各功能本身，不該因為『跑測試的現實時間剛好是盤中』就紅綠不定，
    所以預設把靜默窗的時鐘釘在一個週六；要測靜默窗的測試自己用 quiet_window.set_clock／傳 now 覆蓋。"""
    try:
        import datetime as _dt

        import quiet_window
    except Exception:  # noqa: BLE001 - 不是資產模組的測試環境
        yield
        return
    quiet_window.set_clock(lambda: _dt.datetime(2026, 11, 7, 10, 0))
    yield
    quiet_window.set_clock(None)
    try:      # 測試把傳送 spawn 換成計數器時，_active_jobs 不會被 wrap 清掉：每個測試後清空，避免擋到別的測試的同編號工作
        import transfer_service
        transfer_service._active_jobs.clear()
    except Exception:  # noqa: BLE001 - 非資產模組環境
        pass


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


# ---- 公開中繼產出物沒有 docs/（決策 G1）：讀 docs/ 的測試在那裡一律略過，別讓中繼同步被這種測試卡住 ----
_DOCS = Path(__file__).resolve().parents[1] / "docs"


def pytest_collection_modifyitems(config, items):
    if _DOCS.exists():
        return
    import inspect
    skip = pytest.mark.skip(reason="docs/ 不在公開中繼產出物裡（決策 G1）")
    for item in items:
        fn = getattr(item, "function", None)
        try:
            src = inspect.getsource(fn) if fn else ""
        except (OSError, TypeError):
            continue
        if '"docs"' in src or "'docs'" in src or "docs/" in src:
            item.add_marker(skip)
