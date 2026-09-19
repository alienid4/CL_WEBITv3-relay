"""[B-08] 整台操作：預覽會改到哪幾筆＋全成或全不做＋區分整台／單一服務（2026-09-18）。

下線（cia_pending.batch_set_status）、豁免（onboard_exempt.add）以前都只寫單一筆：
畫面顯示的是「一台」，動作只改了其中一筆——按了下線，同一台其他筆仍是使用中。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import cia_pending  # noqa: E402
import db  # noqa: E402
import manage_state as ms  # noqa: E402
import onboard_exempt  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 一台三筆使用中（三個服務）＋一筆早就報廢的上一台＋另一台
    for sn, h, st in (("A-1", "web1", "使用中"), ("A-2", "web1", "使用中"), ("A-3", "web1", "使用中"),
                      ("A-9", "web1", "報廢"), ("B-1", "web2", "使用中")):
        db.insert_hardware(c, asset_serial=sn, hostname=h, ip="192.0.2.10" if sn != "B-1" else "192.0.2.20",
                           asset_status=st)
    c.commit()
    return c


def _status(c):
    return {r[0]: r[1] for r in c.execute("SELECT asset_serial, asset_status FROM hardware")}


def test_預覽_整台展開_單筆不展開(tmp_path):
    c = _conn(tmp_path)
    assert ms.expand_to_machines(c, ["A-2"]) == ["A-1", "A-2", "A-3"], "報廢那筆是上一台，不算"
    rows = ms.preview_rows(c, ms.expand_to_machines(c, ["A-2"]))
    assert [r["asset_serial"] for r in rows] == ["A-1", "A-2", "A-3"]


def test_整台下線_同一台所有使用中登記都改(tmp_path):
    c = _conn(tmp_path)
    r = cia_pending.batch_set_status(c, ["A-2"], "停用", "現場確認已下線", "t", scope="machine")
    assert sorted(r["updated"]) == ["A-1", "A-2", "A-3"]
    s = _status(c)
    assert s["A-1"] == s["A-2"] == s["A-3"] == "停用" and s["B-1"] == "使用中"


def test_只下線某個服務_只改那一筆_機器仍在(tmp_path):
    c = _conn(tmp_path)
    r = cia_pending.batch_set_status(c, ["A-2"], "停用", "這個服務收掉", "t", scope="single")
    assert r["updated"] == ["A-2"]
    s = _status(c)
    assert s["A-2"] == "停用" and s["A-1"] == "使用中" and s["A-3"] == "使用中"


def test_中途失敗_一筆都不改(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    calls = {"n": 0}
    real = cia_pending.record

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模擬第 2 筆寫紀錄時失敗")
        return real(*a, **k)

    monkeypatch.setattr(cia_pending, "record", boom)
    with pytest.raises(RuntimeError):
        cia_pending.batch_set_status(c, ["A-1"], "停用", "下線", "t", scope="machine")
    s = _status(c)
    assert s["A-1"] == s["A-2"] == s["A-3"] == "使用中", "全成功或全不做：第 1 筆也要復原"


def test_豁免_本來就是整台(tmp_path):
    c = _conn(tmp_path)
    r = onboard_exempt.add(c, ["A-3"], "客製化系統", None, "t")
    assert sorted(r["added"]) == ["A-1", "A-2", "A-3"]
