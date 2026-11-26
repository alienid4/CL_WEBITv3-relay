"""「只涵蓋已納管主機」類頁面的共用資料狀態判定（data_coverage，同步碼 ESJE 第 2 點）。

雙向：每一種狀態都有「會落進這格」與「不會被誤落進這格」的案例——
0 筆、未納管、逾期、收集失敗（連不上／權限不足／失聯／匯入截斷）、平台不支援、排除，以及 API 權限不足（401）。
總數＝下鑽：每一格的數字＝帶 state 下鑽回來的筆數；各格相加＝適用範圍＋範圍外＝現役台數。
全部假資料（10.99.x），不連任何機器。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import data_coverage as dc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

NOW = datetime.now()


def _t(days: float) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


RHEL = "Red Hat Enterprise Linux 9"


def _hw(conn, sn, host, ip, os_=RHEL, ok=None, checked=None, err=None, **kw):
    db.insert_hardware(conn, asset_serial=sn, hostname=host, ip=ip, os=os_, asset_status="使用中",
                       collect_ok=ok, collect_checked_at=checked, collect_error=err, **kw)


def _acct(conn, ip, sn, seen):
    conn.execute("INSERT INTO host_account (ip, asset_serial, username, last_seen, first_seen) VALUES (?,?,?,?,?)",
                 (ip, sn, "root", seen, seen))


def _trace(conn, sn, ip, connected, err=None, total=0, okn=0, failed=0, at=None):
    conn.execute("INSERT INTO collect_ssh_trace (asset_serial, ip, connected, transport_error, cmd_total, cmd_ok, cmd_failed, "
                 "collected_at) VALUES (?,?,?,?,?,?,?,?)", (sn, ip, connected, err, total, okn, failed, at or _t(0)))


def _seed(conn):
    _hw(conn, "L-OK", "ok01", "10.99.3.1", ok=1, checked=_t(0))
    _acct(conn, "10.99.3.1", "L-OK", _t(1))
    _hw(conn, "L-STALE", "stale01", "10.99.3.2", ok=1, checked=_t(0))
    _acct(conn, "10.99.3.2", "L-STALE", _t(20))
    _hw(conn, "L-FAIL", "fail01", "10.99.3.3", ok=1, checked=_t(0))
    _trace(conn, "L-FAIL", "10.99.3.3", 0, err="Connection timed out")
    _hw(conn, "L-PERM", "perm01", "10.99.3.4", ok=1, checked=_t(0))
    _trace(conn, "L-PERM", "10.99.3.4", 1, total=5, okn=0, failed=5)
    _hw(conn, "L-PEND", "pend01", "10.99.3.5", ok=1, checked=_t(0))
    _hw(conn, "L-LOST", "lost01", "10.99.3.6", ok=1, checked=_t(30))
    _hw(conn, "L-UNM", "unm01", "10.99.3.7")
    _hw(conn, "L-UNM2", "unm02", "10.99.3.8", ok=0, checked=_t(0), err="Permission denied (publickey)")
    _hw(conn, "E-1", "esx01", "10.99.3.9", os_="VMware ESXi 8.0.3")
    _hw(conn, "N-1", "sw01", "10.99.3.10", os_="Cisco IOS XE")
    _hw(conn, "L-EXCL", "excl01", "10.99.3.11", ok=1, checked=_t(0))
    _acct(conn, "10.99.3.11", "L-EXCL", _t(1))
    _hw(conn, "L-OLD", "old01", "10.99.3.12", ok=1, checked=_t(0))
    _acct(conn, "10.99.3.12", "L-OLD", _t(1))
    # 已通過但又被收到資料之後才失敗：資料仍在門檻內 → 有資料（不被失敗蓋掉）
    _trace(conn, "L-OLD", "10.99.3.12", 0, err="timed out", at=_t(0))
    db.set_setting(conn, "account_exclude_serials", "L-EXCL")
    conn.commit()


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    yield c
    c.close()


def _by(r):
    return {x["serial"]: x for x in r["rows"]}


def test_每一台只落一格_五態加範圍外(conn):
    r = dc.coverage(conn, "account")
    s = {k: v["state"] for k, v in _by(r).items()}
    assert s["L-OK"] == dc.OK
    assert s["L-STALE"] == dc.STALE
    assert s["L-FAIL"] == dc.FAILED and "連不上" in _by(r)["L-FAIL"]["reason"]
    assert s["L-PERM"] == dc.FAILED and "權限不足" in _by(r)["L-PERM"]["reason"]
    assert s["L-PEND"] == dc.PENDING
    assert s["L-LOST"] == dc.FAILED and "失聯" in _by(r)["L-LOST"]["reason"]
    assert s["L-UNM"] == dc.UNMANAGED
    assert s["L-UNM2"] == dc.UNMANAGED, "沒納管成功過的試連失敗＝未納管，不是收集失敗"
    import manage_state
    assert s["E-1"] == dc.UNSUPPORTED and _by(r)["E-1"]["reason"] == manage_state.VMWARE_SKIP, "天條：VMware 設備理由要講明不收集"
    assert s["N-1"] == dc.UNSUPPORTED
    assert s["L-EXCL"] == dc.EXCLUDED and "排除" in _by(r)["L-EXCL"]["reason"]
    assert s["L-OLD"] == dc.OK, "門檻內的資料不被較晚的失敗蓋成失敗"


def test_未知不算成0或正常_只有有資料才算覆蓋(conn):
    r = dc.coverage(conn, "account")
    assert r["valid"] == 2                                      # L-OK、L-OLD
    assert r["scope_total"] == 9                                # 5 態（含 0 筆的 pending／unmanaged）
    assert r["pct"] == round(2 * 100 / 9, 1)
    for x in r["rows"]:
        if x["state"] != dc.OK:
            assert x["state_label"] != "有資料"
        # 逐筆可追：每列都有原因與來源；有資料／過期／失敗的有更新時間
        assert x["reason"] and x["source"]
        if x["state"] in (dc.OK, dc.STALE):
            assert x["updated_at"]


def test_加總等於下鑽_範圍內外相加等於現役台數(conn):
    r = dc.coverage(conn, "account")
    assert sum(r["counts"].values()) == len(r["rows"]) == 12
    assert r["scope_total"] + r["out_of_scope"] == len(r["rows"])
    for st in dc.STATES:
        d = dc.drill(conn, "account", st)
        assert len(d["rows"]) == r["counts"][st], st
    assert len(dc.drill(conn, "account", "scope")["rows"]) == r["scope_total"]
    assert len(dc.drill(conn, "account", "out")["rows"]) == r["out_of_scope"]
    assert "rows" not in dc.drill(conn, "account")             # 彙總不帶清單
    with pytest.raises(ValueError):
        dc.drill(conn, "account", "nope")
    with pytest.raises(ValueError):
        dc.coverage(conn, "nope")


def test_逾期門檻可調_雙向(conn):
    assert _by(dc.coverage(conn, "account"))["L-STALE"]["state"] == dc.STALE
    db.set_setting(conn, "collect_stale_days", "30")
    conn.commit()
    assert _by(dc.coverage(conn, "account"))["L-STALE"]["state"] == dc.OK


def test_零筆空庫_全部是0且覆蓋率不給數字(tmp_path):
    p = tmp_path / "e.db"
    db.init_db(p)
    c = db.get_connection(p)
    for k in dc.KINDS:
        r = dc.coverage(c, k)
        assert r["scope_total"] == 0 and r["pct"] is None, k     # 沒有分母＝不顯示百分比，不是 0% 也不是 100%
    c.close()


def test_其他類別_平台與範圍(conn):
    # 軟體：沒有 host_package → 已納管的是「尚無資料」不是「有資料」；排除清單只管帳號
    sw = _by(dc.coverage(conn, "software"))
    assert sw["L-OK"]["state"] == dc.PENDING and sw["L-EXCL"]["state"] == dc.PENDING
    # 效能圖表只支援 AIX：Linux 一律不支援
    pf = _by(dc.coverage(conn, "perf"))
    assert pf["L-OK"]["state"] == dc.UNSUPPORTED
    # 基線偏移：沒通過上線檢查的不在範圍
    dr = dc.coverage(conn, "drift")
    assert dr["scope_total"] == 0 and dr["counts"][dc.EXCLUDED] == 10 and dr["counts"][dc.UNSUPPORTED] == 2


def test_組態檢核_匯入截斷算失敗_未匯入不算失敗(conn):
    import config_audit
    config_audit._ensure(conn)
    conn.execute("INSERT INTO config_audit_run (imported_at, status, ip, asset_serial, completeness, checked_at) "
                 "VALUES (?, 'ready', '10.99.3.1', 'L-OK', '截斷', ?)", (_t(0), _t(0)))
    conn.execute("INSERT INTO config_audit_run (imported_at, status, ip, asset_serial, completeness, checked_at) "
                 "VALUES (?, 'ready', '10.99.3.2', 'L-STALE', '完整', ?)", (_t(1), _t(1)))
    conn.commit()
    r = _by(dc.coverage(conn, "config"))
    assert r["L-OK"]["state"] == dc.FAILED and "截斷" in r["L-OK"]["reason"]
    assert r["L-STALE"]["state"] == dc.OK
    assert r["L-PEND"]["state"] == dc.PENDING and "組態檢核報告" in r["L-PEND"]["reason"]
    assert r["L-UNM"]["state"] == dc.UNMANAGED


def test_效能圖表_AIX有NMON才算有資料(tmp_path):
    import nmon_series
    p = tmp_path / "a.db"
    db.init_db(p)
    c = db.get_connection(p)
    _hw(c, "A-1", "aix01", "10.99.4.1", os_="AIX 7.2", ok=1, checked=_t(0))
    _hw(c, "A-2", "aix02", "10.99.4.2", os_="AIX 7.2", ok=1, checked=_t(0))
    nmon_series.ensure(c)
    c.execute("INSERT INTO nmon_series (host, day, data) VALUES ('AIX01', ?, x'00')", (_t(1)[:10],))
    c.commit()
    r = _by(dc.coverage(c, "perf"))
    assert r["A-1"]["state"] == dc.OK
    assert r["A-2"]["state"] == dc.PENDING and "NMON" in r["A-2"]["reason"]
    c.close()


@pytest.fixture()
def client(tmp_path):
    p = tmp_path / "api.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    c.close()

    def _get_db():
        x = db.get_connection(p)
        try:
            yield x
        finally:
            x.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        yield TestClient(api.app, raise_server_exceptions=False)
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


def test_API_權限不足擋下_登入後可看_格子等於下鑽(client):
    assert client.get("/api/v2/coverage", params={"kind": "account"}).status_code == 401
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "v", "role": "viewer"}
    s = client.get("/api/v2/coverage", params={"kind": "account"})
    assert s.status_code == 200, s.text
    j = s.json()
    assert "rows" not in j and j["counts"]["failed"] == 3
    d = client.get("/api/v2/coverage", params={"kind": "account", "state": "failed"}).json()
    assert len(d["rows"]) == j["counts"]["failed"]
    assert client.get("/api/v2/coverage", params={"kind": "bogus"}).status_code == 400
    assert client.get("/api/v2/coverage", params={"kind": "account", "state": "bogus"}).status_code == 400


def test_基線偏移_只看通過上線檢查的主機(conn):
    conn.execute("INSERT INTO golive_check (asset_serial, status) VALUES ('L-OK', 'passed')")
    conn.execute("INSERT INTO golive_check (asset_serial, status) VALUES ('L-PEND', 'passed')")
    conn.execute("INSERT INTO golive_check (asset_serial, status) VALUES ('L-UNM', 'open')")
    conn.execute("INSERT INTO host_service (ip, asset_serial, port, proto, source, last_seen, first_seen) VALUES "
                 "('10.99.3.1', 'L-OK', 22, 'tcp', 'ssh_ss', ?, ?)", (_t(1), _t(1)))
    conn.commit()
    r = dc.coverage(conn, "drift")
    b = _by(r)
    assert b["L-OK"]["state"] == dc.OK and b["L-PEND"]["state"] == dc.PENDING
    assert b["L-UNM"]["state"] == dc.EXCLUDED, "上線檢查還沒通過＝沒有基線，不在範圍"
    assert r["scope_total"] == 2 and r["pct"] == 50.0


def test_verify_numbers_不變式_全綠(conn):
    import verify_numbers as vn
    before = vn.FAILS
    vn.check_data_coverage(conn)
    assert vn.FAILS == before, [x for x in vn.RESULTS if x["ok"] is False and "覆蓋率" in x["page"]][:3]
