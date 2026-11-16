"""7.3 收集涵蓋：有現役套件但時間未知的主機，不可讓整頁 500（Codex 343）。

時間未知的仍算「有清單」（collected_at=None）；最早／最晚只用已知時間，全未知就是 None，不填 0、不猜日期。"""
import sqlite3

import software_inventory as si
import v2_hosts


def _conn(rows):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE host_package (ip TEXT, asset_serial TEXT, last_seen TEXT, gone_at TEXT)")
    c.execute("CREATE TABLE software_collect_runs (id INTEGER PRIMARY KEY, started_at TEXT)")
    c.executemany("INSERT INTO host_package VALUES (?,?,?,NULL)", rows)
    return c


def _machines(*ms):
    return [{"key": k, "name": k, "ip": ip, "serial": sn, "onboarded": True, "badge": {"os_class": "Linux"}} for k, ip, sn in ms]


def test_混合已知與未知時間_不出錯_未知仍算有清單(monkeypatch):
    c = _conn([("192.0.2.1", "A", "2026-11-01 10:00:00"), ("192.0.2.2", "B", ""), ("192.0.2.3", "C", None)])
    monkeypatch.setattr(v2_hosts, "machines", lambda conn: _machines(("a", "192.0.2.1", "A"), ("b", "192.0.2.2", "B"), ("c", "192.0.2.3", "C"), ("d", "192.0.2.4", "D")))
    out = si.coverage(c)
    row = out["rows"][0]
    assert row["n_with"] == 3 and row["n_without"] == 1
    assert {x["name"]: x["collected_at"] for x in row["with"]} == {"a": "2026-11-01 10:00:00", "b": None, "c": None}
    assert out["oldest"] == out["newest"] == "2026-11-01 10:00:00"


def test_全部時間未知_最早最晚保留未知(monkeypatch):
    c = _conn([("192.0.2.2", "B", "")])
    monkeypatch.setattr(v2_hosts, "machines", lambda conn: _machines(("b", "192.0.2.2", "B")))
    out = si.coverage(c)
    assert out["rows"][0]["n_with"] == 1 and out["oldest"] is None and out["newest"] is None
