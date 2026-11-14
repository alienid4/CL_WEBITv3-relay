"""身分解析：多來源匯入時「這筆是不是我已經有的那一台」。

這是多來源合併最容易出事的一步——合併錯了不會噴錯，只會安靜地把兩台不同機器
變成一台。所以每一種危險情境都要有測試守著，而且**判不準時必須是 ambiguous，
不可以是 matched 或 new**。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import identity as idt  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _add(conn, **kw):
    return db.insert_hardware(conn, environment="正式", **kw)


# ===== 強識別碼：相符即定案 =====

def test_序號相符直接定案_即使IP和主機名都不同():
    """硬體序號是強識別碼——換 IP、改主機名都還是同一台機器。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            hid = _add(conn, asset_serial="A-1", hostname="old-name", ip="10.0.0.1",
                       hw_serial="VMware-56 4d 04")
            r = idt.resolve(conn, {"hostname": "new-name", "ip": "10.0.0.99",
                                   "hw_serial": "VMware-56 4d 04"})
            assert r.status == idt.MATCHED and r.hardware_id == hid
            assert r.rule.startswith("strong:")
        finally:
            conn.close()


def test_vm_uuid_是最強識別碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            hid = _add(conn, asset_serial="A-1", hostname="h", ip="10.0.0.1")
            conn.execute("UPDATE hardware SET vm_uuid='42011a-abc' WHERE id=?", (hid,))
            conn.commit()
            r = idt.resolve(conn, {"vm_uuid": "42011A-ABC", "ip": "10.9.9.9"})
            assert r.status == idt.MATCHED and r.hardware_id == hid   # 大小寫不影響
        finally:
            conn.close()


# ===== 危險情境：舊做法會判錯的，現在必須是 ambiguous =====

def test_IP被回收_不可判成同一台():
    """DHCP 回收 IP：舊機器下線、新機器拿到同一個 IP。
    舊的「IP 相符 or 主機名相符」會直接判成同一台 —— 那就把兩台機器合併了。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _add(conn, asset_serial="OLD", hostname="old-host", ip="10.0.0.5",
                 hw_serial="SERIAL-OLD")
            r = idt.resolve(conn, {"hostname": "brand-new-host", "ip": "10.0.0.5",
                                   "hw_serial": "SERIAL-NEW"})
            assert r.status == idt.AMBIGUOUS, "序號不同卻共用 IP，絕不可自動合併"
            assert r.candidates and "序號" in r.reason or "hw_serial" in r.reason
        finally:
            conn.close()


def test_同名主機_只有主機名相符不足以斷定():
    """dev/prod 同名是常態。只有主機名相符就合併，會把兩套環境的機器混成一台。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _add(conn, asset_serial="PROD", hostname="app01", ip="10.1.0.1")
            r = idt.resolve(conn, {"hostname": "app01", "ip": "10.2.0.1"})
            assert r.status == idt.AMBIGUOUS
            assert r.rule == "weak:single"
        finally:
            conn.close()


def test_只有IP相符也不足以斷定():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _add(conn, asset_serial="A", hostname="host-a", ip="10.0.0.7")
            r = idt.resolve(conn, {"hostname": "host-b", "ip": "10.0.0.7"})
            assert r.status == idt.AMBIGUOUS
        finally:
            conn.close()


def test_同一個序號對到多筆_是資料本身有問題_不可挑一個():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _add(conn, asset_serial="A-1", hostname="h1", ip="10.0.0.1", hw_serial="DUP")
            _add(conn, asset_serial="A-2", hostname="h2", ip="10.0.0.2", hw_serial="DUP")
            r = idt.resolve(conn, {"hw_serial": "DUP", "ip": "10.0.0.3"})
            assert r.status == idt.AMBIGUOUS
            assert len(r.candidates) == 2
        finally:
            conn.close()


# ===== 弱識別碼要兩個同時相符才算 =====

def test_主機名與IP同時相符才判定同一台():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            hid = _add(conn, asset_serial="A", hostname="app01", ip="10.0.0.10")
            r = idt.resolve(conn, {"hostname": "app01", "ip": "10.0.0.10"})
            assert r.status == idt.MATCHED and r.hardware_id == hid
            assert r.confidence < 1.0, "弱識別碼的信心度不該跟強識別碼一樣"
        finally:
            conn.close()


def test_完全沒相符就是新的一台():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _add(conn, asset_serial="A", hostname="app01", ip="10.0.0.10")
            r = idt.resolve(conn, {"hostname": "zzz", "ip": "192.168.99.99"})
            assert r.status == idt.NEW
        finally:
            conn.close()


def test_MAC相符可定案_但分隔符不影響():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            hid = _add(conn, asset_serial="A", hostname="h", ip="10.0.0.1",
                       mac="00:0c:29:11:22:33")
            r = idt.resolve(conn, {"mac": "00-0C-29-11-22-33", "ip": "10.0.0.88"})
            assert r.status == idt.MATCHED and r.hardware_id == hid
        finally:
            conn.close()


# ===== staging / 審核佇列的表要在 =====

def test_多來源地基的表都建起來了():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"source_record", "merge_review"} <= tables
            cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
            assert "vm_uuid" in cols
        finally:
            conn.close()
