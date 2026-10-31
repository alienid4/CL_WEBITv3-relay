"""verify_numbers 在 221（公司資料副本）長期紅的兩條（2026-10-28），在 fixture 上重現＋釘住。

1. 「同一台多筆登記每筆的軟體／服務／帳號數字一樣」
   a) 實測主機名校正（PRECEDENCE1）把 SRV64 校成 srv064t：A 找得到 B、B 找不到 A
      （same_machine_serials 的候選只用 IP 與主機名 LIKE），兩筆看到的範圍不同；
      而且同一台用兩個 IP 各收一次，每樣東西存兩列，加總變兩倍（221：606 vs 1212）。
   b) 資料掛在已併回的 DYN- 序號、只剩 IP 對得到：scope 只放「這一筆自己的 IP」，
      另一筆登記（另一個 IP）就看到 0（221：SECSVR018-145TT (18,22,11) vs (0,0,0)）。
2. 「首頁已納管 = 存在可信度已納管」：首頁逐筆判收集證據、代表筆取「要處理的優先」，
   同一台另一筆沒證據就整台變未納管；存在可信度是整台看（221：256 vs 257）。
例子 IP 一律 10.99.x。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import machine_identity  # noqa: E402

NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def _restore_identity_state():
    # conn 會把全域 _STATE 切成主機名口徑；不還原會讓同一行程後面假設 legacy 的測試紅
    saved = dict(machine_identity._STATE)
    yield
    machine_identity._STATE.clear()
    machine_identity._STATE.update(saved)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    p = tmp_path / "v.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('machine_key_mode', 'hostname')")
    c.commit()
    c.close()
    c = db.get_connection(p)          # 開連線時 machine_identity.refresh 讀到主機名口徑
    yield c
    c.close()


def _hw(c, serial, host, ip):
    db.insert_hardware(c, asset_serial=serial, hostname=host, ip=ip, environment="正式", asset_status="使用中")


def _collected(c, serial, ip, n_pkg=3, n_svc=2, n_acct=2):
    for i in range(n_pkg):
        c.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, first_seen, last_seen) "
                  "VALUES (?,?,?,?,?,?,?,?)", (ip, serial, f"pkg{i}", "1.0", "x86_64", "ssh_rpm", NOW, NOW))
    for i in range(n_svc):
        c.execute("INSERT INTO host_service (ip, asset_serial, proto, port, bind_addr, source, first_seen, last_seen) "
                  "VALUES (?,?,?,?,?,?,?,?)", (ip, serial, "tcp", 8000 + i, "0.0.0.0", "ssh_ss", NOW, NOW))
    for i in range(n_acct):
        c.execute("INSERT INTO host_account (ip, asset_serial, username, last_seen) VALUES (?,?,?,?)",
                  (ip, serial, f"user{i}", NOW))
    c.commit()


def _nums(c, serial):
    import asset_collect_status
    import machine_scope

    hw = c.execute("SELECT * FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
    pk = len(machine_scope.machine_packages(c, serial, hw))
    cs = asset_collect_status.compute(c, serial, hw, pk)
    return cs["software"]["count"], cs["service"]["count"], cs["account"]["count"]


def test_1a_實測主機名校正的兩筆_範圍對稱_兩個IP各收一次不算兩倍(conn, monkeypatch):
    import machine_identity
    import system_stats

    _hw(conn, "HW-A", "srv064t", "10.99.1.1")
    _hw(conn, "HW-B", "SRV64", "10.99.1.2")
    monkeypatch.setitem(machine_identity._STATE, "host_fix", {"HW-B": "srv064t"})
    assert system_stats.machine_key("srv064t", "10.99.1.1", "HW-A") == \
        system_stats.machine_key("SRV64", "10.99.1.2", "HW-B"), "前提：校正後是同一台"
    _collected(conn, "HW-A", "10.99.1.1")
    _collected(conn, "HW-B", "10.99.1.2")          # 同一台、另一個 IP 又收一次
    import manage_state
    assert sorted(manage_state.same_machine_serials(conn, "HW-A")) == ["HW-A", "HW-B"]
    assert sorted(manage_state.same_machine_serials(conn, "HW-B")) == ["HW-A", "HW-B"]
    assert _nums(conn, "HW-A") == _nums(conn, "HW-B") == (3, 2, 2)
    # 清單（分頁）跟數字同一把鍵，而且記得在哪幾個 IP 看到
    import account_inventory
    import service_inventory
    svcs = service_inventory.list_services(conn, asset_serial="HW-B")
    assert len(svcs) == 2 and all(s["seen_ips"] == ["10.99.1.1", "10.99.1.2"] for s in svcs)
    assert all(s["ip"] == "10.99.1.2" for s in svcs), "優先留這一筆自己 IP 收到的那列"
    assert len(account_inventory.list_accounts(conn, asset_serial="HW-A")) == 2


def test_1b_資料掛在已併回的序號_同一台另一筆登記也看得到(conn):
    _hw(conn, "HW-C", "srv145", "10.99.2.1")
    _hw(conn, "HW-D", "srv145", "10.99.2.2")
    _collected(conn, "DYN-10.99.2.1", "10.99.2.1", n_pkg=18, n_svc=5, n_acct=4)   # 序號已不在 hardware
    assert _nums(conn, "HW-C") == _nums(conn, "HW-D") == (18, 5, 4)


def test_1_verify_numbers那條在fixture上全過(conn, monkeypatch):
    import machine_identity
    import verify_numbers as vn

    _hw(conn, "HW-A", "srv064t", "10.99.1.1")
    _hw(conn, "HW-B", "SRV64", "10.99.1.2")
    monkeypatch.setitem(machine_identity._STATE, "host_fix", {"HW-B": "srv064t"})
    _collected(conn, "HW-A", "10.99.1.1")
    _collected(conn, "HW-B", "10.99.1.2")
    _hw(conn, "HW-C", "srv145", "10.99.2.1")
    _hw(conn, "HW-D", "srv145", "10.99.2.2")
    _collected(conn, "DYN-10.99.2.1", "10.99.2.1")
    before = vn.FAILS
    vn.check_same_machine_collect(conn)
    hit = [r for r in vn.RESULTS if "同一台多筆登記" in r["name"]][-1]
    assert hit["ok"] is True and vn.FAILS == before, hit


def test_退役登記不借別人的IP(conn):
    import machine_scope

    db.insert_hardware(conn, asset_serial="HW-R", hostname="old01", ip="10.99.3.1", asset_status="報廢")
    sc = machine_scope.scope(conn, "HW-R")
    assert sc["ips"] == [] and sc["ip"] is None


def test_2_首頁已納管跟存在可信度同一個定義_整台看收集證據(conn):
    """同一台兩筆登記，收集證據只在其中一個 IP（帳號收集，序號是已併回的 DYN-）：整台算已納管。"""
    import existence
    import manage_state

    _hw(conn, "HW-C", "srv145", "10.99.2.1")
    _hw(conn, "HW-D", "srv145", "10.99.2.2")
    conn.execute("INSERT INTO host_account (ip, asset_serial, username, last_seen) VALUES (?,?,?,?)",
                 ("10.99.2.1", "DYN-10.99.2.1", "root", NOW))
    conn.commit()
    ex = existence.compute(conn)["summary"]["onboarded"]
    m = manage_state.summarize(conn)
    assert ex == 1
    assert m["counts"][manage_state.ONBOARDED] == ex
    assert m["counts"][manage_state.NOT_ONBOARDED] == 0
    states = {it["asset_serial"]: it["state"] for it in m["items"] if it.get("asset_serial")} \
        if "items" in m else None
    if states is not None:
        assert states == {"HW-C": manage_state.ONBOARDED, "HW-D": manage_state.ONBOARDED}
