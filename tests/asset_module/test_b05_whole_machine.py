"""[B-05] 納管成功要寫整台（與失敗對稱）（2026-09-18）。

以前成功只寫 `WHERE asset_serial = ?` 一筆；一台多筆登記時，沒寫到的兄弟筆仍是「進不去」，
漏斗合併後可能勝出 → 納管成功的機器顯示成未納管。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    for sn, h, st in (("A-1", "web1", "使用中"), ("A-2", "WEB1.corp.example.com", "使用中"),
                      ("A-3", "web1", "報廢"), ("B-1", "other", "使用中")):
        db.insert_hardware(c, asset_serial=sn, hostname=h, ip="192.0.2.10" if sn != "B-1" else "192.0.2.10",
                           asset_status=st)
    c.execute("UPDATE hardware SET collect_ok = 0")
    c.commit()
    return c


def test_一筆成功_同一台的使用中登記都寫成功_報廢與別台不動(tmp_path):
    c = _conn(tmp_path)
    got = ms.mark_collect_ok(c, "A-1", "2026-09-18 10:00:00")
    c.commit()
    assert sorted(got) == ["A-1", "A-2"], "帶網域的同名同 IP 也是同一台（B-03）"
    ok = {r[0]: r[1] for r in c.execute("SELECT asset_serial, collect_ok FROM hardware")}
    assert ok == {"A-1": 1, "A-2": 1, "A-3": 0, "B-1": 0}, "報廢那筆是上一台、other 是別台（同 IP 不同名）"


def test_缺主機名只寫自己(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="C-1", hostname=None, ip="192.0.2.10", asset_status="使用中")
    c.commit()
    assert ms.mark_collect_ok(c, "C-1") == ["C-1"]
