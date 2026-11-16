"""既有 DB 的 host_account 欄位遷移。

背景：CREATE TABLE IF NOT EXISTS 不會替**既有表**補欄位。這個坑 db.py 開頭就寫過，
但加 login_source/os_* 時仍然漏做，結果 221 正式庫一跑就 no such column。
用測試釘住：模擬「舊版建的表」再跑 init_db，欄位要自動補上。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import db  # noqa: E402

# 0.15.x 當時的 host_account（沒有 login_source / os_*）
OLD_TABLE = """
CREATE TABLE host_account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    asset_serial TEXT,
    username TEXT NOT NULL,
    uid INTEGER,
    gid INTEGER,
    gecos TEXT,
    home TEXT,
    shell TEXT,
    can_login INTEGER,
    kind TEXT,
    last_login TEXT,
    never_logged_in INTEGER,
    pw_status TEXT,
    pw_last_change TEXT,
    pw_expires TEXT,
    pw_max_days TEXT,
    acct_expires TEXT,
    is_sudoer INTEGER,
    sudo_nopasswd INTEGER,
    priv_groups TEXT,
    authorized_keys INTEGER,
    source TEXT,
    first_seen TEXT,
    last_seen TEXT,
    gone_at TEXT,
    UNIQUE(ip, username)
);
"""


def test_舊版host_account會自動補欄位():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "old.db"
        conn = sqlite3.connect(p)
        conn.executescript(OLD_TABLE)
        conn.execute("INSERT INTO host_account (ip, username, uid) VALUES ('203.0.113.9','alice',1000)")
        conn.commit()
        conn.close()

        db.init_db(p)          # 應該自動補欄位而不是炸掉

        conn = db.get_connection(p)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(host_account)")}
            for col in ("login_source", "os_family", "os_id", "os_version"):
                assert col in cols, f"遷移沒補上 {col}"
            # 既有資料不能掉
            row = conn.execute("SELECT username, uid FROM host_account").fetchone()
            assert row["username"] == "alice" and row["uid"] == 1000
        finally:
            conn.close()


def test_遷移可重複執行():
    """init_db 每次啟動都會跑，重複執行不能出錯。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "x.db"
        db.init_db(p)
        db.init_db(p)
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(host_account)")}
            assert "login_source" in cols
        finally:
            conn.close()
