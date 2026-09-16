"""匯出端點一律加密、還原端點吃得下加密檔（決策 SEC5）。

上一支測試（test_export_crypto）驗的是「加解密本身對不對」，
這支驗的是**接線有沒有接對**——最容易出事的是「還有一條路繞過加密」。

原本 `fmt=sql & split_mb=0` 走串流，那條路不經過加密。少擋那一條，
使用者以為自己匯出的是加密檔，實際上寄出去的是純文字 SQL——
而且畫面完全看不出差別。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import export_crypto as ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


_ORIG_GET_DB_PATH = api.get_db_path


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
    # ⚠️ get_db_path 是普通函式不是 Depends，dependency_overrides 蓋不到它——
    # 蓋不到的結果是匯出的其實是**真的那顆資料庫**，測試看起來會過但驗錯了東西。
    api.get_db_path = lambda: db_path
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
        db.insert_hardware(conn, asset_serial="SECRET-ASSET-1", ip="10.0.0.9",
                           hostname="veryspecialhostname")
        conn.commit()
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": "s3cure-pass!"}
                       ).status_code == 200
    return client, db_path


def _set_recipient(client, tmp):
    """在來源端設定一把收件人公鑰，回 (私鑰路徑, 公鑰 raw)。"""
    priv = str(Path(tmp) / "recipient.key")
    info = ec.generate_keypair(priv)
    r = client.put("/api/export-key/recipient", json={"public_key": info["public_key"]})
    assert r.status_code == 200, r.text
    return priv, ec.parse_public_key(info["public_key"])


# ---------------------------------------------------------------------------
# 沒設收件人就不准匯出
# ---------------------------------------------------------------------------

def test_沒設收件人公鑰不准匯出():
    """寧可擋下來也不要給一份沒有保護的檔案——那份會被 email 寄出去。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            r = client.get("/api/backup/dump")
            assert r.status_code == 400
            assert "公鑰" in r.json()["detail"]
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


# ---------------------------------------------------------------------------
# 每一條匯出路徑都要加密——重點是「沒有漏網的那條」
# ---------------------------------------------------------------------------

def test_binary匯出是加密的():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            priv, _ = _set_recipient(client, tmp)
            r = client.get("/api/backup/dump")
            assert r.status_code == 200
            assert ec.looks_encrypted(r.content), "binary 匯出沒有加密"
            assert b"veryspecialhostname" not in r.content, "主機名直接出現在匯出檔裡"
            assert ".enc" in r.headers["content-disposition"]
            # 解得開，而且解出來真的是那份資料庫
            plain = ec.decrypt(r.content, priv)
            assert plain[:15] == b"SQLite format 3"
            assert b"veryspecialhostname" in plain
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


def test_sql匯出也要加密_不可以有繞過的捷徑():
    """這條是最容易漏的：原本 fmt=sql 且不分割時走串流，**不經過加密**。
    漏掉它，使用者以為匯出是加密的，實際寄出去的是純文字 SQL，
    而且畫面完全看不出差別。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            priv, _ = _set_recipient(client, tmp)
            r = client.get("/api/backup/dump", params={"fmt": "sql"})
            assert r.status_code == 200
            assert ec.looks_encrypted(r.content), "SQL 匯出繞過了加密"
            assert b"CREATE TABLE" not in r.content, "純文字 SQL 直接出現在匯出檔裡"
            plain = ec.decrypt(r.content, priv)
            assert b"CREATE TABLE" in plain
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


def test_分割匯出也是加密的():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            _set_recipient(client, tmp)
            r = client.get("/api/backup/dump", params={"split_mb": 1})
            assert r.status_code == 200
            # 分割會打包成 zip；zip 裡的分片必須是加密內容的分片
            import io
            import zipfile
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            parts = sorted(n for n in zf.namelist() if not n.endswith(".txt"))
            joined = b"".join(zf.read(n) for n in parts)
            assert ec.looks_encrypted(joined), "分割匯出的內容不是加密的"
            assert b"veryspecialhostname" not in joined
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


# ---------------------------------------------------------------------------
# 還原
# ---------------------------------------------------------------------------

def test_還原吃得下加密檔():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            priv, pub = _set_recipient(client, tmp)
            dump = client.get("/api/backup/dump").content

            # 還原端要有私鑰才解得開——用環境變數把私鑰指到測試用的那把
            import os
            os.environ["WEBIT3_EXPORT_KEY"] = priv
            try:
                r = client.post("/api/backup/restore",
                                files={"file": ("asset_dump.db.enc", dump,
                                                "application/octet-stream")})
                assert r.status_code == 200, r.text
            finally:
                os.environ.pop("WEBIT3_EXPORT_KEY", None)
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


def test_還原_別台的加密檔要講不是給這台的():
    """「拿錯檔案」跟「檔案壞了」要做的事完全不同。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            _set_recipient(client, tmp)
            dump = client.get("/api/backup/dump").content

            other = str(Path(tmp) / "other.key")
            ec.generate_keypair(other)
            import os
            os.environ["WEBIT3_EXPORT_KEY"] = other
            try:
                r = client.post("/api/backup/restore",
                                files={"file": ("x.db.enc", dump,
                                                "application/octet-stream")})
                assert r.status_code == 400
                assert "不是加密給這台" in r.json()["detail"]
            finally:
                os.environ.pop("WEBIT3_EXPORT_KEY", None)
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


def test_還原仍然收未加密的_db():
    """本機／異地備份不加密（決策 SEC5），那些檔案還是要還原得回來。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            plain = Path(db_path).read_bytes()
            r = client.post("/api/backup/restore",
                            files={"file": ("plain.db", plain,
                                            "application/octet-stream")})
            assert r.status_code == 200, r.text
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


# ---------------------------------------------------------------------------
# 金鑰管理端點
# ---------------------------------------------------------------------------

def test_狀態端點分得出來源端與收件端():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            st = client.get("/api/export-key").json()
            assert st["can_export"] is False, "還沒設公鑰卻說可以匯出"
            assert st["recipient_fingerprint"] is None

            _, pub = _set_recipient(client, tmp)
            st = client.get("/api/export-key").json()
            assert st["can_export"] is True
            assert st["recipient_fingerprint"] == ec.fingerprint_hex(pub)
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH


def test_貼錯公鑰端點要擋():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            r = client.put("/api/export-key/recipient",
                           json={"public_key": "ssh-ed25519 AAAAC3Nz user@host"})
            assert r.status_code == 400
            assert "公鑰" in r.json()["detail"]
        finally:
            api.app.dependency_overrides.clear()
            api.get_db_path = _ORIG_GET_DB_PATH
