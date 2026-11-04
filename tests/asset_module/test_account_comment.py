"""收集帳號的備註（GECOS）是設定值，不是寫死在程式碼裡的字串。

使用者 2026-09-03 曾想把 `/etc/passwd` 的備註改成「員工編號-姓名_部門_系統」——
裡面有**員工編號與真實姓名**。2026-10-18 決定：預設中性「WEBIT3 collector」，
值存 app_settings、在 6 系統設定可改；備註出現在每一台主機的 /etc/passwd，不放個資。

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

REAL = "09990001-王小明_資訊部_webit3"   # 虛構示意值（格式驗證用，不是真人）


# ---------------------------------------------------------------------------
# 個資不進程式碼
# ---------------------------------------------------------------------------

def test_預設值是中性的_不含個資():
    """預設值會進公開 relay、也會出現在每一台主機的 /etc/passwd。"""
    import re
    d = eng.DEFAULT_ACCOUNT_COMMENT
    assert d == "WEBIT3 collector"
    assert not re.search(r"\d{5,}", d), "預設備註不可含員編這類長數字"
    assert not re.search(r"[一-鿿]", d), "預設備註不可含中文姓名"


def test_產生的腳本不含個資樣式():
    """沒設定時，Linux／AIX 腳本與一鍵包裡的備註都是中性預設，沒有員編樣式的數字。"""
    import re
    import onboard_bundle
    for s in (eng.build_linux_script("k", "10.99.0.221"),
              eng.build_aix_script("k", "10.99.0.221"),
              onboard_bundle.build_bundle("k", "10.99.0.221", "webit3sc", None)):
        m = re.search(r"^COMMENT='([^']*)'$", s, re.M)
        assert m and m.group(1) == "WEBIT3 collector"
        assert not re.search(r"\d{8}-", s), "腳本裡出現員編樣式字串"


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
    "a:b",          # /etc/passwd 欄位分隔字元，會把整行切歪
    "a'b",          # 腳本用 COMMENT='…' 單引號賦值
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
    s = eng.build_linux_script("k", "10.99.0.221", comment=REAL)
    assert f"COMMENT='{REAL}'" in s, "設定了卻沒帶進腳本，等於白設"
    assert 'useradd -m -s /bin/bash -c "$COMMENT"' in s
    a = eng.build_aix_script("k", "10.99.0.221", comment=REAL)
    assert f"COMMENT='{REAL}'" in a and 'gecos="$COMMENT"' in a


def test_沒設定時腳本用中性預設():
    s = eng.build_linux_script("k", "10.99.0.221")
    assert f"COMMENT='{eng.DEFAULT_ACCOUNT_COMMENT}'" in s


def test_重新納管會把已存在帳號的備註對齊_且一樣就不動():
    """設定改了，已納管主機重跑一次就跟上；一樣不動（冪等），改了要寫進主機紀錄檔（say）。"""
    s = eng.build_linux_script("k", "10.99.0.221", comment=REAL)
    assert 'if [ "$OLD_GECOS" != "$COMMENT" ]; then' in s
    assert 'usermod -c "$COMMENT" "$ACCOUNT"' in s
    assert 'say "[+] 帳號備註已更新' in s, "備註變更沒寫進主機端紀錄檔"
    a = eng.build_aix_script("k", "10.99.0.221", comment=REAL)
    assert 'chuser gecos="$COMMENT" "$ACCOUNT"' in a
    assert 'say "[+] 帳號備註已更新' in a


def test_改設定值_一鍵包的sha256跟著變():
    import onboard_bundle
    b1 = onboard_bundle.build_bundle("k", "10.99.0.221", "webit3scan", "WEBIT3 collector")
    b2 = onboard_bundle.build_bundle("k", "10.99.0.221", "webit3scan", "WEBIT3 collector DR")
    assert onboard_bundle.sha256_of(b1) != onboard_bundle.sha256_of(b2)
    assert "COMMENT='WEBIT3 collector DR'" in b2


def test_設定值從資料庫讀進一鍵包():
    import onboard_bundle
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.set_setting(conn, eng.ACCOUNT_COMMENT_SETTING, "WEBIT3 collector X")
            assert eng.resolve_account_comment(conn) == "WEBIT3 collector X"
        finally:
            conn.close()


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


# ---------------------------------------------------------------------------
# 批次納管也要帶備註
# ---------------------------------------------------------------------------

def test_批次納管也要把備註帶進去():
    """單台納管有帶、批次漏了——而**批次正是一次建幾百個帳號的地方**。

    那個備註存在的理由是「別人在那台看到這個帳號時知道是誰佈的」。
    幾百台全寫預設值，等於這個功能對最需要它的場景失效，
    而且事後要一台一台 usermod 改回來。

    2026-09-08 確認 Debian 走 root 可以批次納管之後、真的按下去之前發現的。
    """
    seen = {}

    def fake_probe(targets, username, passwords, runner=None):
        return [{"ip": t["ip"], "matched_password_index": 0,
                 "platform": "linux", "error": None} for t in targets]

    def fake_exec(**kw):
        seen["script"] = kw["script"]
        return eng.OnboardResult(True, "execute", "ok", "")

    orig = eng.batch_probe_credentials
    eng.batch_probe_credentials = fake_probe
    try:
        eng.batch_auto_onboard(
            [{"ip": "10.0.0.9"}], username="root", passwords=["pw"],
            collector_ip="10.99.0.221", account_of=lambda p: "webit3scan",
            pubkey="ssh-ed25519 AAAA k", comment=REAL,
            onboard_executor=fake_exec)
    finally:
        eng.batch_probe_credentials = orig

    assert f"COMMENT='{REAL}'" in seen["script"], (
        "批次納管沒把設定的帳號備註帶進腳本——幾百台會全部建成預設值")
