"""Codex 1027／1029／1031 回歸（固定 209 驗出）：HMC／SAN 必要查詢失敗不可蓋掉上次結果；SAN target 不跨 fabric。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import hmc_collector  # noqa: E402
import onboard_engine  # noqa: E402
import san_collector  # noqa: E402


def _fake_ssh(monkeypatch, fn):
    monkeypatch.setattr(onboard_engine, "_ssh_exec", lambda ip, u, p, cmd, timeout=40: fn(cmd))


# ---------------------------------------------------------------- 1027 HMC
def test_1027_HMC系統清單查詢失敗_丟錯不回空清單(monkeypatch):
    _fake_ssh(monkeypatch, lambda cmd: (1, "Permission denied") if cmd.startswith("lssyscfg -r sys") else (0, 'version= V10'))
    with pytest.raises(RuntimeError, match="保留上次結果"):
        hmc_collector.collect("10.99.0.5", "u", "p")


def test_1027_HMC查詢成功但沒有系統_仍視為成功空清單(monkeypatch):
    _fake_ssh(monkeypatch, lambda cmd: (0, "" if cmd.startswith("lssyscfg") else "version= V10"))
    inv = hmc_collector.collect("10.99.0.5", "u", "p")
    assert inv.get("system_count", 0) == 0


@pytest.mark.parametrize("rc", [0, 1])
def test_1027_HMC回No_results_were_found_不論退出碼都算真空清單(monkeypatch, rc):
    _fake_ssh(monkeypatch, lambda cmd: (rc, "No results were found.") if cmd.startswith("lssyscfg") else (0, "version= V10"))
    inv = hmc_collector.collect("10.99.0.5", "u", "p")
    assert inv.get("system_count", 0) == 0


# ---------------------------------------------------------------- 1029 SAN
def test_1029_SAN必要指令全部失敗_丟錯不入庫(monkeypatch):
    _fake_ssh(monkeypatch, lambda cmd: (1, "rbash: permission denied"))
    with pytest.raises(RuntimeError, match="保留上次結果"):
        san_collector.collect("10.99.0.6", "u", "p")


def test_1029_SAN部分指令失敗_照常回結果(monkeypatch):
    _fake_ssh(monkeypatch, lambda cmd: (1, "denied") if cmd == "alishow" else (0, ""))
    inv = san_collector.collect("10.99.0.6", "u", "p")
    assert isinstance(inv, dict) and "rows" in inv


# ---------------------------------------------------------------- 1031 SAN 跨 fabric
HOST = "10:00:00:00:c9:00:00:01"
TGT_A = "50:00:00:00:00:00:00:0a"
TGT_B = "50:00:00:00:00:00:00:0b"


def _sw(conn, ip, name, sw_wwn, rows):
    data = {"switch": name, "switch_wwn": sw_wwn, "fabric": [{"wwn": sw_wwn, "name": name}], "rows": rows}
    conn.execute("INSERT INTO san_switch (ip, switch_name, switch_wwn, data_json) VALUES (?,?,?,?)",
                 (ip, name, sw_wwn, json.dumps(data)))


def _targets(conn):
    return conn.execute("SELECT target_wwpn FROM asset_hba WHERE asset_serial = 'S1'").fetchone()[0]


def test_1031_兩個fabric同名zone_target不混入另一個fabric(tmp_path):
    p = tmp_path / "a.db"
    db.init_db(p)
    conn = db.get_connection(p)
    conn.execute("INSERT INTO asset_hba (asset_serial, wwpn) VALUES ('S1', ?)", (HOST,))
    _sw(conn, "10.99.1.1", "SWA", "10:00:00:05:1e:00:00:aa",
        [{"wwpn": HOST, "switch": "SWA", "port": "1", "zones": ["Z1"]}, {"wwpn": TGT_A, "switch": "SWA", "port": "2", "zones": ["Z1"]}])
    _sw(conn, "10.99.1.2", "SWB", "10:00:00:05:1e:00:00:bb",
        [{"wwpn": TGT_B, "switch": "SWB", "port": "2", "zones": ["Z1"]}])
    conn.commit()
    san_collector.reconcile_hba_san(conn)
    t = _targets(conn)
    assert san_collector._norm_wwpn(TGT_A) in t and san_collector._norm_wwpn(TGT_B) not in (t or "")


def test_1031_同一fabric兩台交換器_同名zone照常接上target(tmp_path):
    p = tmp_path / "a.db"
    db.init_db(p)
    conn = db.get_connection(p)
    conn.execute("INSERT INTO asset_hba (asset_serial, wwpn) VALUES ('S1', ?)", (HOST,))
    a, b = "10:00:00:05:1e:00:00:aa", "10:00:00:05:1e:00:00:bb"
    for ip, name, own, rows in (("10.99.1.1", "SWA", a, [{"wwpn": HOST, "switch": "SWA", "port": "1", "zones": ["Z1"]}]),
                                ("10.99.1.2", "SWB", b, [{"wwpn": TGT_B, "switch": "SWB", "port": "2", "zones": ["Z1"]}])):
        data = {"switch": name, "switch_wwn": own, "fabric": [{"wwn": a, "name": "SWA"}, {"wwn": b, "name": "SWB"}], "rows": rows}
        conn.execute("INSERT INTO san_switch (ip, switch_name, switch_wwn, data_json) VALUES (?,?,?,?)", (ip, name, own, json.dumps(data)))
    conn.commit()
    san_collector.reconcile_hba_san(conn)
    assert san_collector._norm_wwpn(TGT_B) in (_targets(conn) or "")
