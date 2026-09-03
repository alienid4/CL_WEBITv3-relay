"""收集帳號的備註（GECOS）是設定值，不是寫死在程式碼裡的字串。

使用者 2026-08-28 要把 `/etc/passwd` 的備註改成
`01003385-李泰益_資訊架構部_webit3`——裡面有**員工編號與真實姓名**。

`onboard_engine.py` 會進 relay（公開 repo）：
  · 寫死等於把個資推上公開程式庫
  · 去識別化替換表會把姓名換掉，公司主機收到的會是被改過的字串
    （分類清單就是這樣踩過一次的，決策：真實名稱存 app_settings 不進版控）

而且這個值會被放進 `useradd -c "…"`——引號、`$`、反引號、換行都能改變
整行指令的語意。那是命令注入，不是格式問題。
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import api  # noqa: E402
import db  # noqa: E402
import onboard_engine as eng  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

REAL = "01003385-李泰益_資訊架構部_webit3"   # 使用者要的格式（這裡是示意值）


# ---------------------------------------------------------------------------
# 個資不進程式碼
# ---------------------------------------------------------------------------

def test_原始碼裡不可以出現實際備註值():
    """這是這支測試存在的主要理由。哪天有人為了省事把值寫死，這裡就紅。

    `onboard_engine.py` 會被打包進公開 relay——真實姓名寫死在裡面就出去了。
    """
    src = (ROOT / "APP" / "asset-module" / "backend" / "onboard_engine.py").read_text(
        encoding="utf-8")
    assert "李泰益" not in src, "真實姓名被寫進會進公開 repo 的原始碼"
    assert "01003385" not in src, "員工編號被寫進會進公開 repo 的原始碼"
    assert eng.DEFAULT_ACCOUNT_COMMENT == "webit3 唯讀收集", "預設值應該是中性的"


# ---------------------------------------------------------------------------
# 過濾：這個值會被放進 useradd -c
# ---------------------------------------------------------------------------

def test_使用者要的格式要放行():
    assert eng.sanitize_account_comment(REAL) == REAL


def test_空值退回中性預設():
    for empty in (None, "", "   "):
        assert eng.sanitize_account_comment(empty) == eng.DEFAULT_ACCOUNT_COMMENT


@pytest.mark.parametrize("bad", [
    'a"b',          # 提前關掉 useradd -c 的引號
    "a`b",          # 反引號＝指令替換
    "a$b",          # $ 展開
    "a\nb",         # 換行＝多一行指令（第一版用 \\s 放行了它，自我驗證抓到）
    "a\rb",
    "a;rm -rf /",
    "x" * 200,      # 超長
])
def test_會改變指令語意的字元一律擋掉(bad):
    with pytest.raises(ValueError):
        eng.sanitize_account_comment(bad)


def test_不合規是報錯不是靜默改掉():
    """使用者填了什麼卻被系統偷偷換成別的，比直接說「這個字不能用」糟得多——
    他會以為設定生效了，直到某天在主機上看到不是自己填的字串。"""
    with pytest.raises(ValueError) as exc:
        eng.sanitize_account_comment('壞"值')
    assert "useradd" in str(exc.value), "沒講清楚為什麼不能用，人不知道怎麼改"


# ---------------------------------------------------------------------------
# 一路接到腳本
# ---------------------------------------------------------------------------

def test_設定值會出現在納管腳本裡():
    s = eng.build_linux_script("k", "10.0.0.221", comment=REAL)
    assert f'-c "{REAL}"' in s, "設定了卻沒帶進腳本，等於白設"


def test_沒設定時腳本用中性預設():
    s = eng.build_linux_script("k", "10.0.0.221")
    assert f'-c "{eng.DEFAULT_ACCOUNT_COMMENT}"' in s


# ---------------------------------------------------------------------------
# 端點
# ---------------------------------------------------------------------------

def _client(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)

    def _override():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": "s3cure-pass!"}
                       ).status_code == 200
    return client, db_path


def test_管理者可以自己填而且讀得回來():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            r = client.put("/api/accounts/collect-config",
                           json={"account": "webit3scan", "account_comment": REAL})
            assert r.status_code == 200, r.text
            assert r.json()["account_comment"] == REAL

            got = client.get("/api/accounts/collect-config")
            assert got.json()["account_comment"] == REAL
        finally:
            api.app.dependency_overrides.clear()


def test_端點也要擋不合規的值():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            r = client.put("/api/accounts/collect-config",
                           json={"account": "webit3scan", "account_comment": 'x"y'})
            assert r.status_code == 400
            assert "useradd" in r.json()["detail"]
        finally:
            api.app.dependency_overrides.clear()


def test_留空是清掉退回預設不是存成空字串():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            client.put("/api/accounts/collect-config",
                       json={"account": "webit3scan", "account_comment": REAL})
            r = client.put("/api/accounts/collect-config",
                           json={"account": "webit3scan", "account_comment": "  "})
            assert r.json()["account_comment"] == eng.DEFAULT_ACCOUNT_COMMENT, (
                "留空之後帳號備註變成空的——那樣建出來的帳號在 /etc/passwd 沒有任何說明")
        finally:
            api.app.dependency_overrides.clear()
