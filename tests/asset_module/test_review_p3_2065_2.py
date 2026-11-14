"""審查延後的 P3（2.065.2）。

1. 欄位對應存檔鎖：過期鎖清除改成「先 rename 到唯一檔名再刪」，不會刪到別人剛建的新鎖。⚠ D4 周邊。
2. 同一把鎖：os.write 拋錯時 fd 一定關掉、鎖檔一定刪掉。
3. 資料品質「作業系統一致」：沒記序號的證據，時間早於這台資產登記時間（hardware.created_at）→ 不採用。⚠ D3。
全假資料、隔離暫存庫。
"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402


def _imports_mod():
    import routers.imports as m
    return m


def _setup_lock(tmp_path):
    target = tmp_path / "field_mapping.json"
    target.write_text("{}", encoding="utf-8")
    return target, tmp_path / "field_mapping.json.lock"


def _age(p: Path, seconds: float):
    old = time.time() - seconds
    os.utime(p, (old, old))


# ---------------------------------------------------------------- 1. 過期鎖競態
def test_過期鎖_清除前被別人換成新鎖_不可刪到新鎖(tmp_path, monkeypatch):
    """模擬 B 看到鎖過期（stat 回舊時間）後、rename 之前，A 已清掉舊鎖並建了新鎖。
    B 搬到的是 A 的新鎖 → 必須放回去，A 的新鎖仍在，B 繼續等（最後 409，不進臨界區）。"""
    m = _imports_mod()
    target, lock = _setup_lock(tmp_path)
    lock.write_text("A-new", encoding="utf-8")       # A 的新鎖（mtime＝現在）
    monkeypatch.setattr(m, "MAPPING_LOCK_WAIT_S", 0.3)

    real_stat = Path.stat
    calls = {"n": 0}

    def fake_stat(self, *a, **k):
        st = real_stat(self, *a, **k)
        if self == lock and calls["n"] == 0:          # 只有第一次讓 B「看到」舊時間
            calls["n"] += 1
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid,
                                   st.st_size, st.st_atime, time.time() - m.MAPPING_LOCK_STALE_S - 100,
                                   st.st_ctime))
        return st
    monkeypatch.setattr(Path, "stat", fake_stat)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        with m._mapping_file_lock(target):
            pytest.fail("A 還拿著鎖，B 不可以進臨界區")
    assert ei.value.status_code == 409
    assert lock.exists() and lock.read_text(encoding="utf-8") == "A-new", "A 的新鎖被刪了／被換了"
    assert not list(tmp_path.glob("*.stale.*")), "暫存的 .stale 檔要清掉"


def test_過期鎖_rename失敗代表別人已處理_回頭重搶(tmp_path, monkeypatch):
    m = _imports_mod()
    target, lock = _setup_lock(tmp_path)
    lock.write_text("old", encoding="utf-8")
    _age(lock, m.MAPPING_LOCK_STALE_S + 10)

    real_rename = os.rename
    state = {"n": 0}

    def racing_rename(src, dst):
        if state["n"] == 0 and str(src) == str(lock):
            state["n"] += 1
            os.remove(src)                            # 別人搶先把過期鎖清掉了
            raise FileNotFoundError(src)
        return real_rename(src, dst)
    monkeypatch.setattr(os, "rename", racing_rename)

    entered = False
    with m._mapping_file_lock(target):
        entered = True
        assert lock.exists()
    assert entered and not lock.exists()
    assert state["n"] == 1
    assert not list(tmp_path.glob("*.stale.*"))


def test_過期鎖_真的過期_搬走刪掉後拿到鎖(tmp_path):
    m = _imports_mod()
    target, lock = _setup_lock(tmp_path)
    lock.write_text("dead", encoding="utf-8")
    _age(lock, m.MAPPING_LOCK_STALE_S + 10)
    with m._mapping_file_lock(target):
        assert lock.read_text(encoding="utf-8") == str(os.getpid())
    assert not lock.exists()
    assert not list(tmp_path.glob("*.stale.*"))


# ---------------------------------------------------------------- 2. os.write 拋錯
def test_os_write拋錯_fd已關_鎖檔已刪(tmp_path, monkeypatch):
    m = _imports_mod()
    target, lock = _setup_lock(tmp_path)
    opened, closed = [], []
    real_open, real_close = os.open, os.close

    def spy_open(*a, **k):
        fd = real_open(*a, **k)
        opened.append(fd)
        return fd

    def spy_close(fd):
        closed.append(fd)
        return real_close(fd)

    def boom(fd, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(os, "write", boom)

    with pytest.raises(OSError):
        with m._mapping_file_lock(target):
            pytest.fail("寫鎖失敗不可以進臨界區")
    assert opened and opened[0] in closed, "fd 沒關"
    assert not lock.exists(), "鎖檔沒刪"


# ---------------------------------------------------------------- 3. NULL 序號證據 vs 登記時間
def _ts(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _acct(db_path, ip, os_id, ver, seen, user="root"):
    c = db.get_connection(db_path)
    try:
        c.execute("INSERT INTO host_account (ip, asset_serial, username, os_id, os_version, last_seen) "
                  "VALUES (?, NULL, ?, ?, ?, ?)", (ip, user, os_id, ver, seen))
        c.commit()
    finally:
        c.close()


def _set_created(db_path, serial, ts):
    c = db.get_connection(db_path)
    try:
        c.execute("UPDATE hardware SET created_at = ? WHERE asset_serial = ?", (ts, serial))
        c.commit()
    finally:
        c.close()


def _os_dim(db_path):
    import data_quality
    c = db.get_connection(db_path)
    try:
        dims = {d["key"]: d for d in data_quality.measure(c)["dimensions"]}
        return dims["os_match"], data_quality.list_offenders(c, "os_match")
    finally:
        c.close()


def test_NULL序號證據早於登記時間_不採用(tmp_path):
    """舊機器退役（不算台數）、新機器剛登記到同一 IP：舊機器 10 天前的帳號資料不可拿來判新機 OS 不一致。"""
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "R-OLD", "oldbox", "10.99.8.1", asset_status="報廢", os="RHEL 8.6")
    _insert_hardware(db_path, "R-NEW", "newbox", "10.99.8.1", asset_status="使用中", os="Ubuntu 22.04")
    _set_created(db_path, "R-NEW", _ts(2))
    _acct(db_path, "10.99.8.1", "rhel", "8.6", _ts(10))
    d, rows = _os_dim(db_path)
    assert d["checked"] == 0 and rows == []


def test_NULL序號證據晚於登記時間_照常採用(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "R-OLD2", "oldbox", "10.99.8.2", asset_status="報廢", os="RHEL 8.6")
    _insert_hardware(db_path, "R-NEW2", "newbox", "10.99.8.2", asset_status="使用中", os="Ubuntu 22.04")
    _set_created(db_path, "R-NEW2", _ts(10))
    _acct(db_path, "10.99.8.2", "rhel", "8.6", _ts(1))
    d, rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (1, 0)
    assert [r["asset_serial"] for r in rows] == ["R-NEW2"]


def test_NULL序號_新舊兩筆證據_略過舊的採用登記後那筆(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "R-3", "box3", "10.99.8.3", asset_status="使用中", os="Ubuntu 22.04")
    _set_created(db_path, "R-3", _ts(5))
    _acct(db_path, "10.99.8.3", "rhel", "8.6", _ts(20))      # 登記前：舊機器的
    _acct(db_path, "10.99.8.3", "ubuntu", "22.04", _ts(1), user="ops")   # 登記後：這台的
    d, rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (1, 1) and rows == []


def test_有序號的證據不受登記時間限制(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "R-4", "box4", "10.99.8.4", asset_status="使用中", os="Ubuntu 22.04")
    _set_created(db_path, "R-4", _ts(1))
    c = db.get_connection(db_path)
    c.execute("INSERT INTO host_account (ip, asset_serial, username, os_id, os_version, last_seen) "
              "VALUES ('10.99.8.4', 'R-4', 'root', 'rhel', '9', ?)", (_ts(30),))
    c.commit()
    c.close()
    d, rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (1, 0)


def test_登記時間空白_照原規則採用(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "R-5", "box5", "10.99.8.5", asset_status="使用中", os="Ubuntu 22.04")
    _set_created(db_path, "R-5", None)
    _acct(db_path, "10.99.8.5", "rhel", "8.6", _ts(30))
    d, _rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (1, 0)


def test_證據沒有時間_比不出先後_照原規則採用(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "R-6", "box6", "10.99.8.6", asset_status="使用中", os="Ubuntu 22.04")
    _set_created(db_path, "R-6", _ts(1))
    _acct(db_path, "10.99.8.6", "rhel", "8.6", None)
    d, _rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (1, 0)
