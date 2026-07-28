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
}

# path 參數用的替代值（值本身不重要，反正應該在碰到資料前就被擋掉）
PATH_PARAM_STUB = {
    "{issue_id}": "1",
    "{asset_serial}": "STUB",
    "{connection_id}": "1",
    "{module_key}": "topology",
    "{system_id}": "STUB",
    "{dep_id}": "1",
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
    assert len(PUBLIC) == 3, (
        f"公開端點數量從 3 變成 {len(PUBLIC)}。開放公開端點是資安決定，"
        "請確認新增的那支不含任何業務/個資，並更新這個斷言。"
    )
