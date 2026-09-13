"""測試階段可以關掉登入——但要**難開、易看見、預設關**。

使用者 2026-09-10：「我現在測試階段，用不到登入。」

⚠️ 這是關掉存取控制，不是介面調整：開著時任何連得到那個埠的人都能撈走全部資產與
人員姓名電話。在金融業這是稽核會直接開缺失的項目。所以這個檔案守的不是「能不能
關掉」，是**關掉這件事夠不夠難、夠不夠明顯**：

1. 預設一定是關的（沒設定就要照常擋 401）
2. 只能從伺服器端打開——不可以有「未登入就能關掉登入」的路徑，那是後門不是開關
3. 開著時 `/api/version` 要說出來，前端才掛得出橫幅
4. 操作紀錄要看得出「這筆是在沒登入的狀態下做的」
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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


def test_預設一定要擋():
    """沒有任何設定時照常 401——**這條紅了就是預設值被改成開著**。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert api.auth_disabled() is False
            assert client.get("/api/dashboard/stats").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_環境變數打開之後不需要登入(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
            assert api.auth_disabled() is True
            assert client.get("/api/dashboard/stats").status_code == 200
        finally:
            api.app.dependency_overrides.clear()


def test_系統設定也能打開():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, '1')",
                         (api.AUTH_DISABLED_SETTING,))
            conn.commit()
            assert api.auth_disabled(conn) is True
            conn.close()
            assert client.get("/api/dashboard/stats").status_code == 200
        finally:
            api.app.dependency_overrides.clear()


def test_開著時版本端點要說出來(monkeypatch):
    """前端靠這個掛橫幅。不說的話，關掉登入這件事在畫面上完全看不出來。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/version").json().get("auth_disabled") is False
            monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
            assert client.get("/api/version").json().get("auth_disabled") is True
        finally:
            api.app.dependency_overrides.clear()


def test_沒登入時的使用者名稱要看得出是沒登入():
    """操作紀錄裡如果寫成 admin，事後查稽核軌跡會以為真的有人登入過。"""
    name = api._anonymous_session()["username"]
    assert "admin" not in name
    assert "未登入" in name or "停用" in name


def test_關掉登入不可以只靠前端(monkeypatch):
    """前端守衛擋得住畫面，擋不住直接打 API。

    這條是防止有人「改前端讓它不跳登入頁」就當作做完了——那樣 API 還是 401，
    畫面會變成一堆錯誤訊息；反過來說，真正生效的地方只有後端這一處。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
            # 隨便挑幾支不同模組的端點，確認是整體生效不是只有某一支
            for path in ("/api/dashboard/stats", "/api/assets", "/api/onboard/audit"):
                assert client.get(path).status_code != 401, path
        finally:
            api.app.dependency_overrides.clear()
