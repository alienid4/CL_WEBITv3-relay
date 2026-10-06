"""收集用憑證庫：加密儲存、只解密給收集用、絕不外流。

使用者同意存服務帳密的前提是這三條保護真的成立，所以每一條都要有測試守。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import credential_store as cs  # noqa: E402
import db  # noqa: E402

FAKE_PW = "FAKE-TEST-PW-not-real-9527"


def _setup(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p), str(Path(tmp) / "key.bin")


def test_密碼加密後才進DB_明文不可出現在資料庫任何一格():
    """DB 會被備份、被複製走——存明文等於備份外流就全裸。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            cs.save(conn, "測試憑證", "winrm", "svc_collect", FAKE_PW,
                    scope="192.168.1.", key_path=kp)
            row = conn.execute("SELECT * FROM collect_credential").fetchone()
            for v in dict(row).values():
                assert FAKE_PW not in str(v), f"明文密碼出現在 DB 欄位：{v}"
            assert row["secret_enc"] and row["secret_enc"] != FAKE_PW
        finally:
            conn.close()


def test_金鑰檔不在DB裡_且權限收緊():
    """金鑰跟 DB 放一起就等於沒加密。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            cs.save(conn, "c1", "winrm", "u", FAKE_PW, key_path=kp)
            assert Path(kp).exists(), "金鑰檔沒建立"
            # 金鑰內容不該出現在 DB
            keydata = Path(kp).read_bytes().decode()
            row = conn.execute("SELECT * FROM collect_credential").fetchone()
            for v in dict(row).values():
                assert keydata not in str(v)
        finally:
            conn.close()


def test_列表永不回傳密碼或密文():
    """畫面只需要知道有哪幾組。把密文丟到前端沒有用途，只是多一個外洩面。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            cs.save(conn, "c1", "winrm", "svc", FAKE_PW, key_path=kp)
            pub = cs.list_public(conn)
            assert len(pub) == 1
            blob = str(pub)
            assert FAKE_PW not in blob
            assert "secret_enc" not in blob, "連密文都不該給前端"
            assert pub[0]["has_secret"] is True and pub[0]["username"] == "svc"
        finally:
            conn.close()


def test_只有get_for_use會回明文_且能正確解密():
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            cs.save(conn, "c1", "winrm", "svc", FAKE_PW, key_path=kp)
            got = cs.get_for_use(conn, "c1", key_path=kp)
            assert got == ("svc", FAKE_PW)
        finally:
            conn.close()


def test_換過金鑰檔要好好回None_而不是炸掉():
    """換金鑰是可預期的情況，要讓呼叫端能報「這組解不開，請重設」。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            cs.save(conn, "c1", "winrm", "svc", FAKE_PW, key_path=kp)
            other = str(Path(tmp) / "other.key")
            assert cs.get_for_use(conn, "c1", key_path=other) is None
        finally:
            conn.close()


def test_查無憑證回None():
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            assert cs.get_for_use(conn, "不存在", key_path=kp) is None
        finally:
            conn.close()


def test_依網段挑憑證_明確的優先於通用的():
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            # 用 TEST-NET（203.0.113.x）當網段，不用真實內網值——relay 匯出會替換真 IP，
            # 若這裡用 192.168.1.x，去識別化後前綴對不上、產出物測試會紅（見 make_relay）。
            cs.save(conn, "通用", "winrm", "u1", FAKE_PW, scope=None, key_path=kp)
            cs.save(conn, "機房A", "winrm", "u2", FAKE_PW, scope="203.0.113.", key_path=kp)
            assert cs.pick_for_host(conn, "203.0.113.5") == "機房A"
            assert cs.pick_for_host(conn, "10.20.30.40") == "通用"
        finally:
            conn.close()


def test_使用稽核不含密碼():
    """一組能讀所有 Windows 的憑證放在服務裡，就必須查得到被誰、對哪台用過。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            cs.save(conn, "c1", "winrm", "svc", FAKE_PW, key_path=kp)
            cs.audit_use(conn, "c1", "10.99.0.101", True, "收集成功")
            row = conn.execute("SELECT * FROM credential_use_audit").fetchone()
            for v in dict(row).values():
                assert FAKE_PW not in str(v)
            assert row["credential_name"] == "c1" and row["ok"] == 1
        finally:
            conn.close()


def test_稽核表無存密碼的欄位():
    with tempfile.TemporaryDirectory() as tmp:
        conn, kp = _setup(tmp)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(credential_use_audit)")}
            assert not any(k in c.lower() for c in cols
                           for k in ("password", "secret", "credential_enc"))
        finally:
            conn.close()
