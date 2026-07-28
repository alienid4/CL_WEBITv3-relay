"""髒資料正規化：同一個東西的各種寫法要收斂成同一個標準名。

測試資料刻意用**這個資料庫裡真實存在的值**（2026-07-19），不是我自己捏的乾淨資料——
自己捏的資料只會驗到自己的假設。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import normalize as nz  # noqa: E402


# ===== 核心：同物異名要收斂 =====

def test_真實資料裡的同物異名要收斂成同一個標準名():
    """這兩個字串真的同時存在於資料庫（Excel 匯入 vs facts 收集），
    不收斂就統計不出「我有幾台 Rocky 9.7」。"""
    a = nz.normalize_os("Rocky Linux 9.7")
    b = nz.normalize_os("Rocky Linux 9.7 (Blue Onyx)")
    assert a["canonical"] == b["canonical"] == "Rocky Linux 9.7"
    assert a["matched"] and b["matched"]


def test_機型同物異名要收斂():
    """VMware VM（人填）與 VMware Virtual Platform（DMI 讀的）是同一件事。"""
    a = nz.normalize_model("VMware VM")
    b = nz.normalize_model("VMware Virtual Platform")
    assert a["canonical"] == b["canonical"] == "VMware Virtual Machine"


def test_版本要抓對_代號不影響():
    r = nz.normalize_os("Debian GNU/Linux 13 (trixie)")
    assert r["canonical"] == "Debian 13"
    assert r["version"] == "13"
    r2 = nz.normalize_os("Ubuntu 22.04 LTS")
    assert r2["canonical"] == "Ubuntu 22.04"


def test_Windows_Server_不可被當成一般Windows():
    """規則順序有意義：先具體後籠統。搞反了所有 Server 都會變成桌面版。"""
    assert nz.normalize_os("Windows Server 2022")["product"] == "Windows Server"
    assert nz.normalize_os("Windows 11")["product"] == "Windows"


# ===== 原值永不改 =====

def test_原值一定原樣保留():
    """正規化不是改掉原值。規則錯了要能重跑；當初覆蓋掉就永遠救不回來。"""
    raw = "Rocky Linux 9.7 (Blue Onyx)"
    r = nz.normalize_os(raw)
    assert r["raw"] == raw
    assert r["canonical"] != raw          # 有正規化
    assert r["raw"] is raw or r["raw"] == raw


# ===== 認不出來的不亂猜 =====

def test_認不出來要標成未對應_而不是硬猜一個():
    """靜默猜錯比留白更糟：留白看得出來，猜錯看不出來。"""
    r = nz.normalize_os("某個沒聽過的作業系統 X9")
    assert r["matched"] is False
    assert r["method"] == "unmatched"
    assert r["canonical"] == "某個沒聽過的作業系統 X9"   # 原值原樣，不亂改


def test_空值不當成未對應的髒資料():
    for v in (None, "", "   "):
        r = nz.normalize_os(v)
        assert r["method"] == "empty" and r["canonical"] is None


# ===== 別名字典：規則橋不了的字差 =====

def test_別名字典可以覆蓋規則_且人補完立刻生效():
    """使用者舉的例子：人寫「Windows 11」，正確應該是「Microsoft Windows 11」。
    這種是真實字差，規則解析橋不了，要靠人補一次字典。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            # 補字典前：規則認得出是 Windows，但標準名沒有廠商前綴
            before = nz.normalize_os("Windows 11", conn)
            assert before["canonical"] == "Windows 11"

            conn.execute(
                "INSERT INTO normalize_alias (kind, raw_value, canonical) VALUES (?,?,?)",
                (nz.KIND_OS, "Windows 11", "Microsoft Windows 11"))
            conn.commit()

            # 補完立刻生效，不用重跑任何批次（這就是不存成欄位的理由）
            after = nz.normalize_os("Windows 11", conn)
            assert after["canonical"] == "Microsoft Windows 11"
            assert after["method"] == "alias"
        finally:
            conn.close()


def test_別名比對不分大小寫與前後空白():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            conn.execute(
                "INSERT INTO normalize_alias (kind, raw_value, canonical) VALUES (?,?,?)",
                (nz.KIND_OS, "windows 11", "Microsoft Windows 11"))
            conn.commit()
            assert nz.normalize_os("  Windows 11  ", conn)["canonical"] == "Microsoft Windows 11"
        finally:
            conn.close()


# ===== 待對應清單 =====

def test_待對應清單只列認不出來的_且補完字典就消失():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            db.insert_hardware(conn, asset_serial="N-1", os="Rocky Linux 9.7",
                               device_model="VMware VM", environment="正式")
            db.insert_hardware(conn, asset_serial="N-2", os="怪怪作業系統",
                               device_model="怪怪機型", environment="正式")
            conn.commit()

            pend = nz.pending_values(conn)
            os_raws = {x["raw_value"] for x in pend[nz.KIND_OS]}
            assert "怪怪作業系統" in os_raws
            assert "Rocky Linux 9.7" not in os_raws, "規則認得出來的不該進待辦"

            # 人補一次字典後，它就不該再出現在待對應清單
            conn.execute(
                "INSERT INTO normalize_alias (kind, raw_value, canonical) VALUES (?,?,?)",
                (nz.KIND_OS, "怪怪作業系統", "Custom OS"))
            conn.commit()
            pend2 = nz.pending_values(conn)
            assert "怪怪作業系統" not in {x["raw_value"] for x in pend2[nz.KIND_OS]}
        finally:
            conn.close()
