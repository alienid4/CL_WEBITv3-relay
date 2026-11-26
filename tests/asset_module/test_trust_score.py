"""資料可信度評分：盯住使用者 2026-09-11 親口定的錨點，配分不可以被悄悄改掉。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import trust_score as ts  # noqa: E402

ALL3 = {"dynassets": True, "rvtools": True, "cia": True}


def test_使用者給的三個錨點():
    assert ts.score_one("vm", ALL3, alive=True, managed=True)["score"] == 100
    assert ts.score_one("vm", ALL3, alive=True, managed=False)["score"] == 90
    no_cia = {"dynassets": True, "rvtools": True, "cia": False}
    assert ts.score_one("vm", no_cia, alive=True, managed=False)["score"] == 70


def test_機器證據取最高不疊加():
    """已納管一定也網路通，兩個加起來會超過 100，而且等於同一件事算兩次。"""
    assert ts.score_one("vm", ALL3, alive=True, managed=True)["score"] == 70 + 30


def test_實體機不被RVTools拖累():
    """使用者抓到的思考 BUG：實體機不會出現在 RVTools，固定配分永遠拿不到滿分。"""
    phys = {"dynassets": True, "rvtools": False, "cia": True}
    r = ts.score_one("physical", phys, alive=False, managed=True)
    assert r["score"] == 100
    assert r["sources"]["rvtools"] is None, "實體機的 RVTools 要標「不適用」，不是「沒有」"


def test_型態不明照VM算():
    only_cia = {"dynassets": False, "rvtools": False, "cia": True}
    assert ts.score_one("unknown", only_cia, False, False)["score"] == \
        ts.score_one("vm", only_cia, False, False)["score"] == 20


def test_只有網路看得到是10分():
    none = {"dynassets": False, "rvtools": False, "cia": False}
    assert ts.score_one("vm", none, alive=True, managed=False)["score"] == 10
    assert ts.score_one("vm", none, alive=False, managed=False)["score"] == 0


def test_型態判斷不能只看is_vm():
    """221 實測：is_vm=0 裡有 1158 台機型寫「(VM)」。"""
    assert ts.classify_kind(0, "(VM)", "h1", set()) == "vm"
    assert ts.classify_kind(0, "VMware Virtual Platform", "h1", set()) == "vm"
    assert ts.classify_kind(0, "DELL R750", "h1", set()) == "physical"
    assert ts.classify_kind(0, "", "h1", set()) == "unknown"
    assert ts.classify_kind(1, "", "h1", set()) == "vm"
    assert ts.classify_kind(0, "DELL R750", "esx01.corp.local", {"esx01"}) == "esxi"


def test_dynassets存活清單也算網路通而且講依據():
    """使用者 2026-09-11 拍板：dynassets 是存活探測清單，列在上面＝網路通 +20。
    三個來源都有的主機因此是 90（不是 70），而依據要記成 dynassets、不能冒充成我們掃到的。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            conn.execute("INSERT INTO hardware (asset_serial, ip, is_vm, device_model) "
                         "VALUES ('HW-9','192.0.2.9',1,'(VM)')")
            hid = conn.execute("SELECT id FROM hardware WHERE asset_serial='HW-9'").fetchone()[0]
            for src, key in (("dynassets", "d1"), ("vcenter", "v1")):
                conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_hardware_id, "
                             "collected_at) VALUES (?,?, '{}', ?, '2026-09-09 08:41:37')", (src, key, hid))
            conn.commit()
            r = ts.compute_all(conn)["HW-9"]
            assert r["score"] == 90, r
            assert r["evidence"] == "alive" and r["alive_basis"] == "dynassets"
            assert r["dynassets_imported_at"] == "2026-09-09 08:41:37"
        finally:
            conn.close()


def test_分布與下鑽對得起來():
    """分布說 100 分有 N 台，下鑽就要剛好 N 台（鐵規則：數字能點、點進去對得上）。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            conn.execute("INSERT INTO hardware (asset_serial, ip, is_vm, device_model, collect_ok) "
                         "VALUES ('HW-1','192.0.2.1',1,'(VM)',1)")
            conn.execute("INSERT INTO hardware (asset_serial, ip, is_vm, device_model) "
                         "VALUES ('DYN-X','192.0.2.2',0,'')")
            conn.commit()
            d = ts.distribution(conn)
            assert d["total"] == 2
            for b in d["by_score"]:
                assert len(ts.hosts_with(conn, score=b["score"])) == b["count"]
                # 「分數怎麼來的」各組合台數加總＝這個分數的台數（說明跟數字不能對不上）
                assert sum(x["count"] for x in b["reasons"]) == b["count"]
                assert all(x["text"] for x in b["reasons"])
            for c in d["by_combo"]:
                assert len(ts.hosts_with(conn, combo=c["combo"])) == c["count"]
        finally:
            conn.close()
