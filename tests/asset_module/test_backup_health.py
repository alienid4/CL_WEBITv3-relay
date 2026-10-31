"""S14：備份健康儀表 + 安全快照。

這組測試最在乎的一件事：**備份必須真的能還原**。
「有跑過備份」跟「備份能用」是兩件事，而且壞掉當下不會有任何錯誤訊息——
要等到真的拿去還原才發現，那時候已經來不及了。所以這裡不只驗流程有沒有跑完，
而是實際打開產出的備份檔、讀裡面的資料、跑 integrity_check。

也涵蓋改用 VACUUM INTO 的理由：原本是 shutil.copy2 直接複製活動中的 .db 檔，
寫入交易進行中複製會拿到破損或缺資料的檔案。
"""
import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import backup  # noqa: E402
import db  # noqa: E402


def _make_db(tmp: Path, rows: int = 50) -> Path:
    db_path = tmp / "asset.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        for i in range(rows):
            db.insert_hardware(
                conn, asset_serial=f"HW-{i:04d}", hostname=f"host-{i}",
                ip=f"10.0.{i // 256}.{i % 256}", environment="正式",
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_備份檔真的打得開而且資料完整():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=30)
        result = backup.run_backup(db_path, tmp / "backups", datetime.now())

        assert result.ok, result.error
        assert result.integrity_ok, result.integrity_detail
        assert result.path and result.path.exists()

        # 真的開起來讀——這才是「備份能用」的證據
        conn = sqlite3.connect(result.path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0]
            sample = conn.execute(
                "SELECT hostname FROM hardware WHERE asset_serial = 'HW-0007'"
            ).fetchone()
        finally:
            conn.close()
        assert n == 30, f"備份裡的筆數不對：{n}"
        assert sample and sample[0] == "host-7"


def test_有人同時寫入時快照仍然完整():
    """VACUUM INTO 的重點。用 shutil.copy2 時這種情境可能複製到寫到一半的頁面。

    測法：開一條背景執行緒持續寫入，同時做備份，然後驗證備份檔的完整性與可讀性。
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=20)

        stop = threading.Event()
        errors: list[str] = []

        def writer():
            conn = db.get_connection(db_path)
            try:
                i = 1000
                while not stop.is_set():
                    conn.execute(
                        "INSERT INTO hardware (asset_serial, hostname, environment) VALUES (?,?,?)",
                        (f"HW-{i}", f"busy-{i}", "正式"),
                    )
                    conn.commit()
                    i += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                conn.close()

        th = threading.Thread(target=writer, daemon=True)
        th.start()
        try:
            result = backup.run_backup(db_path, tmp / "backups", datetime.now())
        finally:
            stop.set()
            th.join(timeout=5)

        assert not errors, f"寫入端出錯：{errors}"
        assert result.ok, result.error
        assert result.integrity_ok, f"併發寫入下的快照完整性失敗：{result.integrity_detail}"

        conn = sqlite3.connect(result.path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0]
        finally:
            conn.close()
        assert n >= 20, "備份裡的資料比備份前還少"


def test_不會覆蓋既有備份檔():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=5)
        bdir = tmp / "backups"
        now = datetime(2026, 7, 18, 10, 0, 0)

        first = backup.run_backup(db_path, bdir, now)
        assert first.ok
        # 同一秒再備一次：檔名相同，應該失敗而不是默默蓋掉別人的備份
        second = backup.run_backup(db_path, bdir, now)
        assert not second.ok
        assert "不覆蓋" in second.error or "FileExists" in second.error


def test_保留天數會清掉過期備份():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=3)
        bdir = tmp / "backups"
        bdir.mkdir()

        old = bdir / f"{backup.BACKUP_FILENAME_PREFIX}20260101_000000.db"
        old.write_bytes(b"stale")
        stale_ts = (datetime.now() - timedelta(days=backup.RETENTION_DAYS + 2)).timestamp()
        import os
        os.utime(old, (stale_ts, stale_ts))

        result = backup.run_backup(db_path, bdir, datetime.now())
        assert result.ok
        assert not old.exists(), "過期備份沒有被清掉"
        assert len(backup.list_backups(bdir)) == 1


def test_異地備份會多複製一份():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=4)
        offsite = tmp / "offsite"
        result = backup.run_backup(db_path, tmp / "backups", datetime.now(), offsite)

        assert result.ok
        assert result.offsite_path and result.offsite_path.exists()
        assert not result.offsite_error
        assert len(backup.list_backups(offsite)) == 1


def test_燈號_全部正常是綠燈():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=5)
        bdir = tmp / "backups"
        offsite = tmp / "offsite"
        backup.run_backup(db_path, bdir, datetime.now(), offsite)

        h = backup.health(db_path, bdir, offsite, datetime.now())
        assert h["status"] == "green", h["reasons"]
        assert h["db"]["integrity_ok"] is True
        assert h["last_backup"]["integrity_ok"] is True
        assert h["offsite"]["configured"] is True
        assert h["offsite"]["count"] == 1


def test_燈號_沒設異地是黃燈():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=5)
        bdir = tmp / "backups"
        backup.run_backup(db_path, bdir, datetime.now())

        h = backup.health(db_path, bdir, None, datetime.now())
        assert h["status"] == "yellow", h["reasons"]
        assert any("異地" in r for r in h["reasons"])


def test_燈號_備份逾時是黃燈():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=5)
        bdir = tmp / "backups"
        offsite = tmp / "offsite"
        backup.run_backup(db_path, bdir, datetime.now(), offsite)

        # 把時鐘往後撥超過容許時數
        later = datetime.now() + timedelta(hours=backup.BACKUP_MAX_AGE_HOURS + 5)
        h = backup.health(db_path, bdir, offsite, later)
        assert h["status"] == "yellow", h["reasons"]
        assert any("小時前" in r for r in h["reasons"])


def test_燈號_完全沒備份是紅燈():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=5)
        h = backup.health(db_path, tmp / "backups", None, datetime.now())
        assert h["status"] == "red", h["reasons"]
        assert any("沒有任何備份" in r for r in h["reasons"])


def test_燈號_備份檔壞掉是紅燈():
    """最重要的一種紅燈：檔案在、時間也新，但內容是壞的。
    只看「有沒有檔案」的儀表板會顯示綠燈，那正是最危險的假象。"""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        db_path = _make_db(tmp, rows=5)
        bdir = tmp / "backups"
        result = backup.run_backup(db_path, bdir, datetime.now())
        assert result.ok

        # 把備份檔內容毀掉（模擬磁碟壞軌／複製中斷）
        with open(result.path, "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 512)

        h = backup.health(db_path, bdir, None, datetime.now())
        assert h["status"] == "red", f"壞掉的備份沒被抓出來：{h}"
        assert any("完整性" in r for r in h["reasons"])


def test_燈號_主庫讀不到是紅燈():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        h = backup.health(tmp / "nonexistent.db", tmp / "backups", None, datetime.now())
        assert h["status"] == "red"
        assert any("資料庫" in r for r in h["reasons"])


def test_主庫啟用了_WAL():
    """WAL 讓讀不被寫擋住，也是 VACUUM INTO 能安全併發的前提（backlog S14 要求）。"""
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "asset.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        assert mode.lower() == "wal", f"journal_mode 是 {mode}，不是 wal"


# ===== 異地備份路徑可由畫面設定（app_settings 優先，退回環境變數）=====

def test_異地路徑_app設定優先於環境變數(monkeypatch):
    """畫面設的異地路徑要蓋過環境變數，讓使用者不用改 systemd 就能改。"""
    monkeypatch.setenv("ASSET_BACKUP_OFFSITE_DIR", "/env/offsite")
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_make_db(Path(tmp)))
        try:
            from db import set_setting
            set_setting(conn, backup.OFFSITE_SETTING_KEY, "/ui/offsite")
            assert str(backup.get_offsite_dir(conn)) == str(Path("/ui/offsite"))
        finally:
            conn.close()


def test_異地路徑_沒設app時退回環境變數(monkeypatch):
    monkeypatch.setenv("ASSET_BACKUP_OFFSITE_DIR", "/env/offsite")
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_make_db(Path(tmp)))
        try:
            # 沒在 app_settings 設 → 用環境變數
            assert str(backup.get_offsite_dir(conn)) == str(Path("/env/offsite"))
        finally:
            conn.close()


def test_異地路徑_app設空字串代表明確清除_不退回環境變數(monkeypatch):
    """存空字串＝使用者明確清掉異地，這時不該又退回環境變數（否則清不掉）。"""
    monkeypatch.setenv("ASSET_BACKUP_OFFSITE_DIR", "/env/offsite")
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_make_db(Path(tmp)))
        try:
            from db import set_setting
            set_setting(conn, backup.OFFSITE_SETTING_KEY, "")
            assert backup.get_offsite_dir(conn) is None
        finally:
            conn.close()


def test_健康狀態_設了異地後不再報未設定():
    """設了異地路徑，health 的 offsite.configured 要變 True（燈號才可能轉綠）。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmpp = Path(tmp)
        db_path = _make_db(tmpp)
        conn = db.get_connection(db_path)
        try:
            from db import set_setting
            offsite = tmpp / "offsite"
            offsite.mkdir()
            set_setting(conn, backup.OFFSITE_SETTING_KEY, str(offsite))
            h = backup.health(db_path=db_path, backup_dir=tmpp / "backups", conn=conn)
            assert h["offsite"]["configured"] is True
            assert h["offsite"]["dir"] == str(offsite)
        finally:
            conn.close()


def test_還原_合法檔案覆蓋成功且先留一份後悔藥(tmp_path):
    """方案B：整檔覆蓋成功後，資料要真的變成上傳那份，而且現有正式庫要先被存證一份
    （唯一的後悔藥——還原本身不可逆，但這步讓「還原錯了」還能救回來）。"""
    live_path = tmp_path / "live" / "asset.db"
    live_path.parent.mkdir()
    db.init_db(live_path)
    conn = db.get_connection(live_path)
    try:
        db.insert_hardware(conn, asset_serial="OLD-1", hostname="old-host", ip="10.9.0.1")
        conn.commit()
    finally:
        conn.close()

    upload_path = tmp_path / "upload.db"
    db.init_db(upload_path)
    conn = db.get_connection(upload_path)
    try:
        db.insert_hardware(conn, asset_serial="NEW-1", hostname="new-host", ip="10.9.0.2")
        conn.commit()
    finally:
        conn.close()
    uploaded_bytes = upload_path.read_bytes()

    result = backup.restore(uploaded_bytes, live_path)
    assert result["ok"] is True
    assert result["pre_restore_backup"] is not None
    assert Path(result["pre_restore_backup"]).exists()

    conn = db.get_connection(live_path)
    try:
        rows = {r["asset_serial"] for r in conn.execute("SELECT asset_serial FROM hardware")}
        assert rows == {"NEW-1"}  # 舊資料真的被換掉了
    finally:
        conn.close()

    backup_conn = db.get_connection(Path(result["pre_restore_backup"]))
    try:
        old_rows = {r["asset_serial"] for r in backup_conn.execute("SELECT asset_serial FROM hardware")}
        assert old_rows == {"OLD-1"}  # 後悔藥裡是換掉「之前」的內容
    finally:
        backup_conn.close()


def test_還原_不是合法SQLite檔案拒絕且不動到正本(tmp_path):
    live_path = tmp_path / "asset.db"
    db.init_db(live_path)
    conn = db.get_connection(live_path)
    try:
        db.insert_hardware(conn, asset_serial="KEEP-1", hostname="keep-host", ip="10.9.0.9")
        conn.commit()
    finally:
        conn.close()

    try:
        backup.restore(b"this is not a sqlite database at all", live_path)
        assert False, "應該要拋 ValueError"
    except ValueError as exc:
        assert "完整性檢查" in str(exc) or "資料庫" in str(exc)

    conn = db.get_connection(live_path)
    try:
        rows = {r["asset_serial"] for r in conn.execute("SELECT asset_serial FROM hardware")}
        assert rows == {"KEEP-1"}  # 正本完全沒被動到
    finally:
        conn.close()


def test_還原_合法SQLite但不是本系統資料庫拒絕(tmp_path):
    live_path = tmp_path / "asset.db"
    db.init_db(live_path)

    other_path = tmp_path / "other.db"
    other_conn = sqlite3.connect(other_path)
    other_conn.execute("CREATE TABLE unrelated_stuff (id INTEGER)")
    other_conn.commit()
    other_conn.close()

    try:
        backup.restore(other_path.read_bytes(), live_path)
        assert False, "應該要拋 ValueError"
    except ValueError as exc:
        assert "hardware" in str(exc)


# ===== 還原失敗時要講清楚原因與資料狀態（2026-08-19 使用者回報「失敗沒寫原因」）=====
#
# 還原是不可逆操作。失敗時使用者最需要知道的不是「失敗了」，而是
# **哪一步失敗、正式資料到底有沒有被動到**。以前這些路徑通通穿出去變成 FastAPI 的
# 裸 500，前端只拿得到 `Internal Server Error`。

def _make_valid_upload(tmp_path):
    """做一份「內容合法、能通過驗證」的上傳檔，好讓測試打到驗證之後的階段。"""
    upload_path = tmp_path / "upload.db"
    db.init_db(upload_path)
    conn = db.get_connection(upload_path)
    try:
        db.insert_hardware(conn, asset_serial="NEW-1", hostname="new-host", ip="10.9.0.2")
        conn.commit()
    finally:
        conn.close()
    return upload_path.read_bytes()


def _make_live(tmp_path):
    live_path = tmp_path / "live" / "asset.db"
    live_path.parent.mkdir()
    db.init_db(live_path)
    conn = db.get_connection(live_path)
    try:
        db.insert_hardware(conn, asset_serial="OLD-1", hostname="old-host", ip="10.9.0.1")
        conn.commit()
    finally:
        conn.close()
    return live_path


def test_還原_存證備份失敗就中止且明講正式資料沒被動到(tmp_path, monkeypatch):
    """存證備份是唯一的後悔藥。做不出來還硬覆蓋，就是把「不可逆」變成
    「不可逆且救不回來」——必須中止，而且要講明資料沒被動到。"""
    live_path = _make_live(tmp_path)
    uploaded = _make_valid_upload(tmp_path)

    def boom(*a, **kw):
        raise PermissionError("Permission denied: backups/")

    monkeypatch.setattr(backup, "snapshot", boom)

    with pytest.raises(backup.RestoreFailed) as exc:
        backup.restore(uploaded, live_path)

    msg = str(exc.value)
    assert "存證備份失敗" in msg
    assert "PermissionError" in msg          # 講得出是哪一種錯，不是只有「失敗」
    assert "正式資料庫未被更動" in msg        # 使用者最需要先知道的一句
    assert "已中止" in msg

    # 而且要真的沒動到
    conn = db.get_connection(live_path)
    try:
        rows = {r["asset_serial"] for r in conn.execute("SELECT asset_serial FROM hardware")}
        assert rows == {"OLD-1"}
    finally:
        conn.close()


def test_還原_寫暫存檔失敗要指出目錄與可能原因(tmp_path, monkeypatch):
    live_path = _make_live(tmp_path)
    uploaded = _make_valid_upload(tmp_path)

    def boom(self, *a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom)

    with pytest.raises(backup.RestoreFailed) as exc:
        backup.restore(uploaded, live_path)

    msg = str(exc.value)
    assert "暫存檔" in msg
    assert "磁碟空間" in msg or "寫入權限" in msg   # 給得出下一步往哪查
    assert "正式資料庫未被更動" in msg


def test_還原_覆蓋途中失敗不可宣稱資料沒被動到(tmp_path, monkeypatch):
    """這一步是唯一「可能已經寫到一半」的階段。這時候說「資料沒被動到」是騙人的，
    要明講可能不完整，並指向存證備份。"""
    live_path = _make_live(tmp_path)
    uploaded = _make_valid_upload(tmp_path)

    class _FailingBackup:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def backup(self, *a, **kw):
            raise sqlite3.OperationalError("disk I/O error")

    # 直接讓來源連線（上傳的暫存檔）的 backup() 失敗
    orig = sqlite3.connect

    def patched(path, *a, **kw):
        conn = orig(path, *a, **kw)
        if str(path).endswith(".restore_upload_%d.db" % os.getpid()):
            return _FailingBackup(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", patched)

    with pytest.raises(backup.RestoreFailed) as exc:
        backup.restore(uploaded, live_path)

    msg = str(exc.value)
    assert "寫入正式資料庫時失敗" in msg
    assert "disk I/O error" in msg
    assert "不完整" in msg
    # 這種情況**不可以**出現「未被更動」這種保證
    assert "正式資料庫未被更動" not in msg
    assert "pre_restore_" in msg  # 要指得出後悔藥在哪
