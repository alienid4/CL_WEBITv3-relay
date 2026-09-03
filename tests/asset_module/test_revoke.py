"""取消納管：佈得出去，也要收得回來。

使用者 2026-08-28：「納管成功 就有 取消納管，需要打 YES 才能取消納管」。

在此之前系統只有「怎麼佈出去」——`webit3scan` 是持有全機隊金鑰的帳號，
而撤銷只存在於 playbook 的一句註解裡，沒有程式也沒有按鈕。
稽核對特權帳號的標準問法是「怎麼建、怎麼撤、誰批准」，第三個答不出來。

這裡守四件事：
1. **只刪我們放進去的那一行金鑰**——帳號本來就存在的機器，檔案裡有別人的金鑰
2. **順序**：金鑰 → sudoers → 帳號。先斷存取權，後面失敗也已經收回
3. **要打 YES**：這個動作在目標機上刪東西，打字比按鈕難按錯
4. **撤銷後的狀態不是「連不上」**：兩者在畫面上都是「收不到」，但一個是刻意的
   結果、一個是待辦。分不出來的話，會有人跑去「修」一台剛撤銷的機器
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import onboard_engine as eng  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PUBKEY = "ssh-ed25519 AAAACOLLECTOR webit3-collector"
OTHER_KEY = "ssh-rsa AAAASOMEONEELSE alice@corp"
IP = "10.0.0.60"


# ---------------------------------------------------------------------------
# 腳本本身
# ---------------------------------------------------------------------------

def test_只刪我們那一行_不刪整個檔案():
    """帳號**本來就存在**時（納管會印 `[=] 帳號已存在`），我們是 append 到既有的
    authorized_keys。撤銷若 `rm` 整個檔案，會把別人的金鑰一起刪掉——那不是撤銷，
    是破壞，而且目標機上沒有備份機制，還原不回來。

    （使用者 2026-08-28 問「取消納管前有備份嗎」才發現第一版寫錯。）
    """
    s = eng.build_linux_revoke_script(PUBKEY)
    assert 'rm -f "$AUTH"' not in s, "還在刪整個 authorized_keys"
    assert "grep -vF" in s, "沒有用比對的方式只刪一行"
    assert "$PUBKEY" in s, "沒有帶公鑰進去，無從比對該刪哪一行"


def test_動之前要備份():
    s = eng.build_linux_revoke_script(PUBKEY)
    assert "webit3-revoke-" in s, "沒有留備份——撤錯了還原不回來"
    assert s.count("cp -p") >= 2, "authorized_keys 與 sudoers 都要備份"


def test_剩下別人的金鑰要講出來():
    """「這台的帳號不是我們獨佔的」是人需要知道的事實——代表這台之後
    還有別人進得來，撤銷沒有把門關死。"""
    s = eng.build_linux_revoke_script(PUBKEY)
    assert "還有" in s and "沒有動它們" in s


def test_三步順序_金鑰要最先():
    """先斷金鑰：就算後面兩步失敗，該收回的權限已經收回。
    反過來（先 userdel）失敗的話權限還在——那才是要命的失敗模式。"""
    s = eng.build_linux_revoke_script(PUBKEY)
    i_key = s.index("移除收集公鑰")
    i_sudo = s.index("移除唯讀 sudo")
    i_user = s.index("刪除帳號")
    assert i_key < i_sudo < i_user, "順序錯了——金鑰必須最先斷"


def test_冪等_本來就沒有不算失敗():
    """撤銷兩次要安全（可能有人先手動清掉了）。每一步分別回報
    「移除了」與「本來就沒有」——這兩件事對人的意義不同。"""
    s = eng.build_linux_revoke_script(PUBKEY)
    assert s.count("[=]") >= 3, "沒有把「本來就沒有」跟「移除了」分開講"


def test_AIX與Windows明確擋掉():
    """AIX 是 rmuser、Windows 是 Remove-LocalUser，指令不同，沒實作也沒實測。
    硬套 Linux 那份會失敗在中途、留下半套狀態（2026-08-16 的教訓）。"""
    for plat in ("aix", "windows"):
        r = eng.revoke(IP, plat, "root", "pw", pubkey=PUBKEY)
        assert not r.ok
        assert "只支援 Linux" in r.message
        assert "authorized_keys" in r.message, "沒告訴人手動怎麼先斷存取權"


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
    return TestClient(api.app), db_path


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
        db.insert_hardware(conn, asset_serial="R1", ip=IP, collect_ok=1)
        conn.commit()
    finally:
        conn.close()
    r = client.post("/api/auth/login",
                    json={"username": "tester", "password": "s3cure-pass!"})
    assert r.status_code == 200


def _body(confirm="YES"):
    return {"ip": IP, "platform": "linux", "username": "root",
            "password": "pw", "confirm": confirm}


def test_沒打YES就不准撤(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            for bad in ("", "yes", "Yes", "確定"):
                r = client.post("/api/onboard/revoke", json=_body(bad))
                assert r.status_code == 400, f"{bad!r} 竟然放行了"
        finally:
            api.app.dependency_overrides.clear()


def test_撤銷成功後狀態要分得出是人撤的不是連不上(monkeypatch):
    """兩者在畫面上都是「收不到」。不分開的話，之後有人會跑去「修」
    一台你剛撤銷的機器。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            monkeypatch.setattr(
                eng, "revoke",
                lambda **kw: eng.OnboardResult(True, "execute", "完成", "out"))

            r = client.post("/api/onboard/revoke", json=_body())
            assert r.status_code == 200 and r.json()["ok"] is True

            conn = db.get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT collect_ok, collect_error FROM hardware WHERE ip = ?",
                    (IP,)).fetchone()
                assert row["collect_ok"] == 0
                assert "取消納管" in (row["collect_error"] or ""), (
                    f"撤銷後看起來像連線失敗：{row['collect_error']!r}")
                assert "tester" in (row["collect_error"] or ""), "沒記是誰撤的"

                audit = conn.execute(
                    "SELECT trigger, triggered_by, ok FROM onboard_audit "
                    "WHERE target_ip = ? ORDER BY id DESC LIMIT 1", (IP,)).fetchone()
                assert audit["trigger"] == "revoke", "稽核分不出這筆是撤銷還是納管"
                assert audit["triggered_by"] == "tester"
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


def test_撤銷失敗不可以把狀態改成已撤銷(monkeypatch):
    """撤不掉就是撤不掉。標成已撤銷等於製造假證據——
    人會以為那台的金鑰收回來了，實際上還在。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            monkeypatch.setattr(
                eng, "revoke",
                lambda **kw: eng.OnboardResult(False, "connect", "連不上", ""))

            r = client.post("/api/onboard/revoke", json=_body())
            assert r.json()["ok"] is False

            conn = db.get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT collect_ok, collect_error FROM hardware WHERE ip = ?",
                    (IP,)).fetchone()
                assert row["collect_ok"] == 1, "撤銷失敗卻把這台標成不再納管"
                assert "取消納管" not in (row["collect_error"] or "")
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 清單篩選
# ---------------------------------------------------------------------------

def test_查得出哪些主機納管成功():
    """使用者 2026-08-28：「我會查哪些主機是納管成功的。」

    而且「收不到」要拆得開：revoked 是刻意的結果、failed 才是待辦、
    never 是還沒開始——混在一起會讓待辦數字灌水。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                db.insert_hardware(conn, asset_serial="OK1", ip="10.0.0.1", collect_ok=1)
                db.insert_hardware(conn, asset_serial="REV1", ip="10.0.0.2", collect_ok=0,
                                   collect_error="已由 tester 取消納管（不是連線失敗）")
                db.insert_hardware(conn, asset_serial="FAIL1", ip="10.0.0.3", collect_ok=0,
                                   collect_error="Permission denied")
                db.insert_hardware(conn, asset_serial="NEW1", ip="10.0.0.4")
                conn.commit()
            finally:
                conn.close()

            def serials(collect):
                r = client.get("/api/assets", params={"collect": collect})
                assert r.status_code == 200, r.text
                return {a["asset_serial"] for a in r.json()}

            assert "OK1" in serials("ok") and "REV1" not in serials("ok")
            assert serials("revoked") == {"REV1"}
            assert serials("failed") == {"FAIL1"}, "撤銷的被當成連線失敗了"
            assert serials("never") == {"NEW1"}, "從沒試連過的被混進失敗"

            assert client.get("/api/assets", params={"collect": "亂填"}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()
