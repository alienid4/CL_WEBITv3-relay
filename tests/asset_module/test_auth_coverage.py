"""權限守門：走過 FastAPI 路由表上的每一個 /api 端點，未登入一律要被擋。

為什麼要有這支：曾經有 16 個端點沒掛 require_auth——前端每頁都有登入守衛，看起來很安全，
但 API 本身是全裸的，任何碰得到後端埠的人 curl 就能撈走全部資產與人員姓名電話，甚至
PUT 改欄位對應、PATCH 把問題標成已處理。UI 有登入牆不等於 API 有。

這支測試的價值在「自動涵蓋新端點」：以後有人加了路由卻忘了掛 require_auth，這裡直接紅，
不用靠人記得。要開放新的公開端點，必須明確加進 PUBLIC 白名單（強迫做一次決定）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# 刻意公開、不需登入的端點。加東西進這份白名單＝一個資安決定，要想清楚再加。
PUBLIC = {
    ("POST", "/api/auth/login"),    # 還沒登入當然不能要求登入
    ("POST", "/api/auth/logout"),   # 沒session時登出視為no-op，不該噴401
    ("GET", "/api/version"),        # 部署驗證/健康檢查用（deploy.sh 打的就是這支），不含業務資料
    # ONBOARD-FLOW1（2026-10-18）：主機上的 curl 沒有登入狀態，憑權杖（綁那批主機 IP、30 分鐘）下載 webit3_onboard.sh。
    # 只換得到這支固定腳本（內容只有公鑰與收集器位址，無機密、無業務資料）；權杖不對一律 404。
    ("GET", "/api/onboard/fetch/{token}"),
}

# path 參數用的替代值（值本身不重要，反正應該在碰到資料前就被擋掉）
PATH_PARAM_STUB = {
    "{issue_id}": "1",
    "{asset_serial}": "STUB",
    "{connection_id}": "1",
    "{module_key}": "topology",
    "{system_id}": "STUB",
    "{dep_id}": "1",
    "{snapshot_id}": "1",
    # 匯入來源（2026-09-09 的匯出端點）：要用真的存在的 key，
    # 不然測到的是「400 不認得的來源」而不是「有沒有擋未登入」
    "{source}": "dynassets",
    # 1-4 分佈統計的下鑽：要用對照表裡真的有的代碼才測得到 401，
    # 不然測到的是 404
    "{api_id}": "N-107",
    # SAN 原文存檔下載（2026-09-15）：整數參數，給字面值會先 422
    "{archive_id}": "1",
    # 檔案傳送（Q94）：整數參數，給字面值會先 422
    "{file_id}": "1",
    "{job_id}": "1",
    "{log_id}": "1",
}


def _anonymous_client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    return TestClient(api.app)


def _api_endpoints():
    found = []
    for route in api.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            found.append((method, path))
    return found


def test_每個api端點未登入都被擋():
    """401/403 才算擋住。特別注意 422 不算——那代表 request body 驗證先跑了，
    也就是未登入的人已經摸到這支端點，只是參數不合而已。"""
    with tempfile.TemporaryDirectory() as tmp:
        client = _anonymous_client(tmp)
        try:
            endpoints = _api_endpoints()
            assert endpoints, "抓不到任何 /api 路由，測試本身壞了"

            unprotected = []
            for method, path in endpoints:
                if (method, path) in PUBLIC:
                    continue
                url = path
                for token, stub in PATH_PARAM_STUB.items():
                    url = url.replace(token, stub)
                if method in ("POST", "PUT", "PATCH"):
                    resp = client.request(method, url, json={})
                else:
                    resp = client.request(method, url)
                if resp.status_code not in (401, 403):
                    unprotected.append(f"{method} {path} -> {resp.status_code}")

            assert not unprotected, (
                "以下端點未登入就能存取，請掛上 require_auth（或確認要公開後加進 PUBLIC 白名單）：\n"
                + "\n".join(unprotected)
            )
        finally:
            api.app.dependency_overrides.clear()


def test_白名單端點未登入可用():
    """反向確認：白名單那幾支不能被誤擋，不然登入頁/部署驗證會壞掉。"""
    with tempfile.TemporaryDirectory() as tmp:
        client = _anonymous_client(tmp)
        try:
            assert client.get("/api/version").status_code == 200
            # 帳密錯誤要回 401（是「認證失敗」不是「需要先登入」），代表端點本身有讓匿名者叫到
            resp = client.post(
                "/api/auth/login", json={"username": "nobody", "password": "x" * 12}
            )
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_白名單沒有偷偷長大():
    """白名單本身要有人看著。改動這個數字＝你正在開放新的公開端點，請說明理由。"""
    assert len(PUBLIC) == 4, (
        f"公開端點數量從 4 變成 {len(PUBLIC)}。開放公開端點是資安決定，"
        "請確認新增的那支不含任何業務/個資，並更新這個斷言。"
    )


def test_登入被關掉時_version要誠實回報(tmp_path, monkeypatch):
    """2026-09-21 在 221 抓到：app_settings.auth_disabled='1'（登入是關的、未登入
    curl /api/assets 拿得到全部資產），但 /api/version 一直回 auth_disabled=false，
    所以**防忘記關的那條紅色橫幅從來沒出現過**。

    保險本身壞掉比沒有保險更危險：看到畫面沒橫幅的人會以為登入是開的。
    原因是 version_endpoint() 沒帶 conn，auth_disabled() 只看得到環境變數。
    """
    import sqlite3

    import api
    import db
    from fastapi.testclient import TestClient

    p = tmp_path / "t.db"
    db.init_db(p)
    monkeypatch.setenv("ASSET_DB_PATH", str(p))
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('auth_disabled','1')")
    conn.commit()
    conn.close()

    with TestClient(api.app) as c:
        body = c.get("/api/version").json()
    assert body["auth_disabled"] is True, (
        "登入關著就要回 true，否則畫面不會掛橫幅，沒人會發現登入是關的")


# ===================================================================== Q91：權限等級登記
# 上面守的是「有沒有登入」；下面守的是「登入了能做什麼」（access_policy 登記簿）。
# 跟上面同一個精神：新端點忘了做決定，這裡直接紅，不用靠人記得。

def _registry_routes():
    out = {}
    for route in api.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/"):
            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
                out[(method, path)] = route
    return out


def test_Q91_每支非GET端點都有明確的權限分級():
    import access_policy as ap

    routes = _registry_routes()
    nonget = {k for k in routes if k[0] != "GET"}
    known = ap.all_registered_writes() | ap.PUBLIC_ROUTES | ap.AGENT_ROUTES | set(ap.EXPORT_LEVEL)
    missing = sorted(nonget - known)
    assert not missing, (
        "以下非 GET 端點沒有在 access_policy.py 登記等級（ADMIN_WRITE／USER_WRITE／VIEWER_WRITE，"
        "或是匯出就登記 EXPORT_LEVEL）。沒登記的執行期會被當成 admin 才能呼叫：\n"
        + "\n".join(f"{m} {p}" for m, p in missing))
    stale = sorted((ap.all_registered_writes() | set(ap.EXPORT_LEVEL) | ap.AGENT_ROUTES) - set(routes))
    assert not stale, f"access_policy 登記了不存在的端點（改名／刪除後要一起改）：{stale}"


def test_Q91_每支端點在路由層都掛了對應等級的依賴():
    import access_policy as ap

    bad = []
    for (method, path), route in _registry_routes().items():
        want = ap.level_for(method, path)
        names = [getattr(d.dependency, "__name__", "") for d in route.dependencies]
        have = [n.replace("require_level_", "") for n in names if n.startswith("require_level_")]
        if want is None:
            if have:
                bad.append(f"{method} {path}: 不該有等級依賴卻有 {have}")
        elif have != [want]:
            bad.append(f"{method} {path}: 要 {want}，實際 {have}")
    assert not bad, "\n".join(bad)


def test_Q91_端點內自帶管理員檢查的_一定登記成admin():
    """原本零散的 `_can_edit_nav`／`_writer`／transfer 閘門是「限管理員」；登記簿不可以把它登成比較鬆的等級，
    否則 UI 看起來開放、點下去才被端點自己擋（或反過來被誤放行）。"""
    import inspect

    import access_policy as ap

    bad = []
    for (method, path), route in _registry_routes().items():
        if method == "GET":
            continue
        try:
            src = inspect.getsource(route.endpoint)
        except (OSError, TypeError):
            continue
        if (method, path) in ap.EXPORT_LEVEL:
            continue           # 匯出端點的等級另有規則（例如畫面表格匯出：大量／含帳號欄位才要 admin）
        if any(k in src for k in ("_can_edit_nav(", "_writer(", "transfer_gate", "vrops_write_gate",
                                   "whatsup_write_gate", "is_admin(")):
            if ap.level_for(method, path) not in ("admin",):
                bad.append(f"{method} {path} -> {ap.level_for(method, path)}")
    assert not bad, "端點內有管理員檢查、登記簿卻不是 admin：\n" + "\n".join(bad)


def test_Q91_會回檔案的端點_一定登記在EXPORT_LEVEL():
    """回應是檔案下載（Content-Disposition／FileResponse）的端點，必須在 EXPORT_LEVEL 決定等級
    （viewer 一律不能匯出）。靜態掃端點原始碼；經由 helper 才回檔案的端點，由 api.py 的下載中介層兜底
    （回應帶 attachment 時 viewer 一樣被擋，見 test_access_levels）。"""
    import inspect

    import access_policy as ap

    miss = []
    for (method, path), route in _registry_routes().items():
        try:
            src = inspect.getsource(route.endpoint)
        except (OSError, TypeError):
            continue
        low = src.lower()
        returns_file = ("content-disposition" in low or "fileresponse(" in low or "streamingresponse(" in low
                        or "_xlsx_response(" in src or "spreadsheetml" in low or "text/csv" in low
                        or ("xlsx" in low and "media_type" in low))
        if returns_file and (method, path) not in ap.EXPORT_LEVEL \
                and (method, path) not in ap.PUBLIC_ROUTES and ap.level_for(method, path) != "admin":
            # （已登記成 admin 的寫入端點順便回檔案報告的，viewer／一般本來就進不來，不必重複登記）
            miss.append(f"{method} {path}")
    assert not miss, "這些端點會回檔案，卻沒登記匯出等級（access_policy.EXPORT_LEVEL）：\n" + "\n".join(miss)
