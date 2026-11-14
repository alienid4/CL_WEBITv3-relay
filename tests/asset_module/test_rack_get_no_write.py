"""Codex 71／001：機房架構／上架助手／機櫃立面／預留／型號規格的 GET 讀取路徑不得寫庫。
用 test_count_bridge 的完整假庫，以 mode=ro 唯讀連線重開同一個庫（庫檔 SHA256 前後相同），並與可寫路徑的內容比對。"""
import hashlib
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(HERE))

import model_specs  # noqa: E402
import rack_layout  # noqa: E402
import test_count_bridge as tcb  # noqa: E402


def _ro(p):
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c


def test_機櫃GET在唯讀連線下可用且庫檔不變(tmp_path):
    w, p = tcb._conn(tmp_path)
    w.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = _ro(p)
    dc = rack_layout.datacenter(ro)
    assert dc["rooms"]
    pl = rack_layout.placement(ro, 2, 1)
    assert pl["racks"] is not None
    assert rack_layout.reservations(ro) == []                       # 表還沒建＝沒有預留（不建表）
    room = next(r for r in dc["rooms"] if r["racks"])
    rk = room["racks"][0]["rack"]
    assert rack_layout.rack_detail(ro, room["room"], rk)["rack"] == rk
    assert model_specs.all_specs_readonly(ro)                        # 種子檔在記憶體組
    assert model_specs.coverage(ro)["total"] >= 1
    ro.close()
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_唯讀路徑內容等同可寫路徑(tmp_path):
    w, p = tcb._conn(tmp_path)
    writable = rack_layout._machines(w)                              # 不經快取、可寫路徑
    ro = _ro(p)
    readonly = rack_layout._machines(ro, read_only=True, with_specs=True, with_existence=True)
    assert len(readonly) == len(writable)
    wk = {m["key"]: m for m in writable}
    for m in readonly:
        a, b = wk[m["key"]], m
        for f in ("room", "rack", "kind", "state", "w", "kg", "system_level", "spec_model", "u_start", "u_end",
                  "exist_level", "unobserved", "retired"):
            assert a.get(f) == b.get(f), (m["key"], f, a.get(f), b.get(f))
    ro.close()


def test_預留寫入入口仍會建表(tmp_path):
    w, p = tcb._conn(tmp_path)
    assert rack_layout.reservations(w) == []
    rack_layout.ensure_reservation_table(w)
    assert w.execute("SELECT 1 FROM sqlite_master WHERE name='rack_reservation'").fetchone()


def test_全新庫缺所有機櫃表_GET在唯讀連線與無任何非SELECT語句下可用(tmp_path):
    """獨立審查補強：fixture 先用可寫路徑把表都建好了，CREATE IF NOT EXISTS 是空操作，抓不到回歸。
    這裡把機櫃／型號規格／掃描涵蓋相關表全部丟掉，再以 mode=ro 唯讀連線＋trace 斷言沒有任何非 SELECT 語句。"""
    w_, p = tcb._conn(tmp_path)
    for (name,) in w_.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'rack_%' OR name LIKE 'model_spec%' "
                              "OR name LIKE 'scan_coverage%' OR name LIKE 'collect_dispatch%')").fetchall():
        w_.execute(f'DROP TABLE IF EXISTS "{name}"')
    w_.commit()
    w_.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = _ro(p)
    stmts: list[str] = []
    ro.set_trace_callback(stmts.append)
    dc = rack_layout.datacenter(ro)
    rack_layout.placement(ro, 2, 1)
    assert rack_layout.reservations(ro) == []
    room = next(r for r in dc["rooms"] if r["racks"])
    rack_layout.rack_detail(ro, room["room"], room["racks"][0]["rack"])
    model_specs.coverage(ro)
    ro.close()
    bad = [x for x in stmts if not x.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "BEGIN", "COMMIT"))]
    assert not bad, bad[:5]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
