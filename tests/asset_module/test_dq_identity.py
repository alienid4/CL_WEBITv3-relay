"""資料品質：主機名與 IP 都有才認得出是哪一台（2026-09-18 十項盤點第 8 條）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import data_quality as dq  # noqa: E402
import db  # noqa: E402


def test_識別不完整的列得出來且原因分得清(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    for sn, h, ip in (("A-1", "h1", "192.0.2.1"), ("A-2", "h2", None), ("A-3", None, "192.0.2.3"),
                      ("A-4", "h4", "0.0.0.0")):
        db.insert_hardware(c, asset_serial=sn, hostname=h, ip=ip, asset_status="使用中")
    c.commit()
    dim = next(d for d in dq.measure(c)["dimensions"] if d["key"] == "identity")
    assert dim["kind"] == "filled", "缺資料不是資料錯，不併入可信度分數"
    assert dim["ok"] == 1 and dim["bad"] == 3
    why = {r["asset_serial"]: r["reason"] for r in dq.list_offenders(c, "identity")}
    assert set(why) == {"A-2", "A-3", "A-4"}
    assert "IP" in why["A-2"] and "主機名" in why["A-3"] and "0.0.0.0" in why["A-4"]


def test_同名不同IP_列出來給人確認(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    for sn, h, ip in (("A-1", "db01", "192.0.2.11"), ("A-2", "DB01.corp.example.com", "192.0.2.12"),
                      ("B-1", "web1", "192.0.2.20")):
        db.insert_hardware(c, asset_serial=sn, hostname=h, ip=ip, asset_status="使用中")
    c.commit()
    dim = next(d for d in dq.measure(c)["dimensions"] if d["key"] == "same_name_multi_ip")
    assert dim["bad"] == 2, "db01 與 DB01.corp.example.com 去網域後同名、兩個 IP"
    rows = dq.list_offenders(c, "same_name_multi_ip")
    assert {r["asset_serial"] for r in rows} == {"A-1", "A-2"}
    assert "192.0.2.12" in next(r["reason"] for r in rows if r["asset_serial"] == "A-1")
