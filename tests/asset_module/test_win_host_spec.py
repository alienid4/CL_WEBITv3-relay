"""Windows 主機規格（WinRM／CIM，win_host_spec，2026-10-21）。

公司 Windows Server 2022：硬體資訊分頁「尚未收集主機規格」、按「點此立即收集」回「Windows 待補 WinRM」。
守的重點（全部假 CIM 輸出，不連任何主機）：
1. 解析：CPU（插槽／核心／邏輯）、記憶體總量＋DIMM、BIOS、機型／序號／UUID、磁碟、網卡、FC HBA。
2. VM（Hyper-V／VMware）→ HBA「此機未使用」；實體機兩條 HBA 查詢都被拒 → 「權限不足」，不是「沒有」。
3. run_collection 對 Windows 不再回 unsupported，寫進同一張 host_spec／asset_nic／asset_hba。
4. 納管後「硬體規格」那一樣：收到明細就不再寫「Windows 尚未支援」。
5. 系統校正：host_spec 的 CPU／記憶體照既有比對；登記 MAC 不在實測網卡上 → 疑似不符。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_spec_collector  # noqa: E402
import manage_state  # noqa: E402
import measured_hw  # noqa: E402
import post_onboard  # noqa: E402
import win_host_spec as W  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _windows_opted_in(monkeypatch):
    """這支測的是 Windows 收集本身（解析、寫庫、講原因），主機一律當成「已納管」。
    「沒納管就不登入」那道閘另在 test_win_onboard.py 測（2026-10-22 Windows 勾選才收）。"""
    import win_onboard

    monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
    monkeypatch.setattr(win_onboard, "is_opted_in", lambda *a, **k: True)

T = "\t"


def _line(*c):
    return T.join(str(x) for x in c)


PHYS = "\n".join([
    "@@BEGIN",
    _line("CS", "HPE", "ProLiant DL380 Gen10", 274877906944, 2, 64),
    _line("CSP", "HPE", "ProLiant DL380 Gen10", "SGH999TEST", "4C4C4544-0000-1111-2222-333344445555"),
    _line("BIOS", "HPE", "U30", "2023-05-11", "SGH999TEST"),
    _line("OS", "Microsoft Windows Server 2022 Standard", "10.0.20348", "2026-10-01 08:00:00", "2026-10-21 10:30:00"),
    _line("CPU", "Proc 1", "Intel(R) Xeon(R) Gold 6226R CPU @ 2.90GHz", 16, 32, 2900),
    _line("CPU", "Proc 2", "Intel(R) Xeon(R) Gold 6226R CPU @ 2.90GHz", 16, 32, 2900),
] + [_line("DIMM", f"PROC 1 DIMM {i}", "", 34359738368, 2933, 2933, "HPE", "P00924-B21", 26) for i in range(1, 9)]
  + [_line("SLOTS", 24),
     _line("DISK", 0, "HPE LOGICAL VOLUME", 960197124096, "SCSI", "PDNLH0BRH9F2RF", "1.98", "Fixed hard disk media", "OK"),
     _line("PDISK", 0, "HPE LOGICAL VOLUME", 4, 8, 0, 960197124096),
     _line("VOL", "C:", 214748364800, 107374182400, "NTFS"),
     _line("VOL", "D:", 536870912000, 268435456000, "NTFS"),
     _line("NIC", 1, "Ethernet 1", "HPE Ethernet 10Gb 2-port 562SFP+ Adapter", "AA:BB:CC:00:11:22", 10000000000, 2, "i40ea", "True"),
     _line("NIC", 2, "Ethernet 2", "HPE Ethernet 10Gb 2-port 562SFP+ Adapter #2", "AA:BB:CC:00:11:23", 9223372036854775807, 7, "i40ea", "True"),
     _line("NIC", 9, "vEthernet (Mgmt)", "Hyper-V Virtual Ethernet Adapter", "00:15:5D:AA:00:01", 10000000000, 2, "VMSNP", "False"),
     _line("NIC", 10, "", "WAN Miniport (IP)", "50:50:54:50:30:30", "", "", "", "False"),
     _line("NCFG", 1, "10.99.10.21,fe80::1", "255.255.255.0,64", "10.99.10.254"),
     _line("NCFG", 9, "10.99.20.5", "255.255.255.0", ""),
     _line("NETA", "Ethernet 1", "HPE Ethernet 10Gb", "1.12.3.0", 120),
     _line("IPORT", "PCI\\VEN_10DF&DEV_F100\\4&1_0", "10000090fa000001", "20000090fa000001", 1, "2"),
     _line("IPORT", "ROOT\\ISCSIPRT\\0000_0", "iqn.1991-05.com.microsoft:demo", "", 2, "2"),
     "ERR\tmsfc\tDENIED\tAccess denied",
     "ERR\tteam\tFAIL\tInvalid class",
     "@@END"])

HYPERV = "\n".join([
    _line("CS", "Microsoft Corporation", "Virtual Machine", 17179869184, 1, 4),
    _line("CSP", "Microsoft Corporation", "Virtual Machine", "0000-0001-2345", "AAAA-BBBB"),
    _line("BIOS", "Microsoft Corporation", "Hyper-V UEFI Release v4.1", "2023-01-01", "0000-0001-2345"),
    _line("CPU", "None", "Intel(R) Xeon(R) Silver 4214 CPU @ 2.20GHz", 4, 4, 2200),
    _line("DISK", 0, "Microsoft Virtual Disk", 136365211648, "SCSI", "", "1.0", "Fixed hard disk media", "OK"),
    _line("VOL", "C:", 135838822400, 50000000000, "NTFS"),
    _line("NIC", 3, "Ethernet", "Microsoft Hyper-V Network Adapter", "00:15:5D:01:02:03", 10000000000, 2, "netvsc", "True"),
    "ERR\tiport\tDENIED\tAccess denied", "ERR\tmsfc\tDENIED\tAccess denied",
    "@@END"])

PHYS_DENIED = "\n".join([
    _line("CS", "Dell Inc.", "PowerEdge R740", 68719476736, 2, 40),
    _line("CPU", "CPU1", "Intel Xeon", 10, 20, 2400), _line("CPU", "CPU2", "Intel Xeon", 10, 20, 2400),
    _line("NIC", 1, "NIC1", "Broadcom", "AA:BB:CC:00:00:01", 1000000000, 2, "b57nd60a", "True"),
    "ERR\tiport\tDENIED\tAccess denied", "ERR\tmsfc\tDENIED\t存取被拒", "ERR\tdimm\tEMPTY\t",
    "@@END"])


# ===== 1. 解析 =====

def test_實體機_CPU記憶體BIOS序號磁碟網卡HBA():
    r = W.build(PHYS)
    s, x = r["spec"], r["spec_json"]
    assert s["cpu"] == "Intel(R) Xeon(R) Gold 6226R CPU @ 2.90GHz"
    assert s["cores"] == 64 and x["cpu_sockets"] == 2 and x["cpu_cores_per_socket"] == 16
    assert x["cpu_threads_per_core"] == 2 and x["cpu_mhz"] == 2900
    assert s["mem_mb"] == 262144
    assert s["model"] == "ProLiant DL380 Gen10" and s["vendor"] == "HPE" and s["serial"] == "SGH999TEST"
    assert x["uuid"].startswith("4C4C4544")
    assert x["dimm"]["count"] == 8 and x["dimm"]["slots_total"] == 24
    m0 = x["dimm"]["modules"][0]
    assert m0["size"] == "32 GB" and m0["speed"] == "2933 MT/s" and m0["part_number"] == "P00924-B21"
    assert m0["type"] == "DDR4" and m0["locator"] == "PROC 1 DIMM 1"
    assert x["bios"] == {"vendor": "HPE", "version": "U30", "date": "2023-05-11"}
    d = x["disks"][0]
    assert d["kind"] == "SSD" and d["bus"] == "RAID" and d["health"] == "Healthy" and d["capacity"] == "960 GB"
    assert r["storage"]["by"] == "mount" and r["storage"]["total_gb"] == 700
    assert x["kernel"].startswith("Microsoft Windows Server 2022") and x["uptime"].startswith("up 20 days")
    assert "virt" not in x and r["is_vm"] is False
    nics = {n["nic_name"]: n for n in r["nics"]}
    assert nics["Ethernet 1"]["ip"] == "10.99.10.21" and nics["Ethernet 1"]["subnet"] == "24"
    assert nics["Ethernet 1"]["speed_cur"] == 10000 and nics["Ethernet 1"]["vlan"] == "120"
    assert nics["Ethernet 1"]["mac"] == "aa:bb:cc:00:11:22" and nics["Ethernet 1"]["gateway"] == "10.99.10.254"
    assert nics["Ethernet 2"]["state"] == "down" and nics["Ethernet 2"]["speed_cur"] is None
    assert len(r["hbas"]) == 1, "iSCSI 啟動器不算 FC HBA"
    h = r["hbas"][0]
    assert h["wwpn"] == "10:00:00:90:fa:00:00:01" and h["port_state"] == "Online"
    # 選用項目（LBFO team 不存在）不算缺；msfc 被拒但已經有 InitiatorPort → 不寫進一般 notes
    assert "notes" not in x


def test_HyperV_VM_HBA_此機未使用():
    r = W.build(HYPERV)
    assert r["is_vm"] and r["spec_json"]["virt"] == "Hyper-V"
    assert r["hbas"] == [] and r["net_notes"]["hbas"].startswith("此機未使用")
    assert r["spec"]["cores"] == 4 and r["spec"]["mem_mb"] == 16384


def test_VMware_判定():
    assert W.virt_of("VMware, Inc.", "VMware7,1") == "VMware"
    assert W.virt_of("HPE", "ProLiant DL380 Gen10") is None


def test_實體機HBA兩條都被拒_講權限不足不是沒有():
    r = W.build(PHYS_DENIED)
    assert r["hbas"] == []
    assert "權限不足" in r["net_notes"]["hbas"] and "此機未使用" not in r["net_notes"]["hbas"]
    assert any("記憶體插槽" in n for n in r["spec_json"]["notes"]), r["spec_json"]


def test_實體機FC類別不存在_此機未使用():
    t = PHYS_DENIED.replace("ERR\tmsfc\tDENIED\t存取被拒", "ERR\tmsfc\tFAIL\tInvalid class")
    t = t.replace("ERR\tiport\tDENIED\tAccess denied", "")
    assert W.build(t)["net_notes"]["hbas"].startswith("此機未使用")


def test_一個欄位都沒有不寫空殼():
    r = W.build("ERR\tcs\tDENIED\tAccess denied\nERR\tcpu\tDENIED\tAccess denied\n@@END")
    assert not W.has_data(r)


# ===== 2. 收集與寫入 =====

def _conn(tmp, **hw):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    base = dict(asset_serial="W-1", hostname="demo-win01", ip="10.99.10.21",
                os="Microsoft Windows Server 2022 Standard", environment="測試", asset_status="使用中")
    base.update(hw)
    db.insert_hardware(c, **base)
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = ?", (base["asset_serial"],))
    c.commit()
    return c


def test_run_collection_Windows寫進host_spec(monkeypatch):
    monkeypatch.setattr(W, "fetch", lambda conn, ip, runner=None, timeout=90: PHYS)
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "windows")
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            r = host_spec_collector.run_collection(c, only_serial="W-1")
            assert r["ok"] == 1 and r["unsupported"] == 0, r
            hs = c.execute("SELECT * FROM host_spec WHERE asset_serial='W-1'").fetchone()
            assert hs["platform"] == "windows" and int(hs["cores"]) == 64 and int(hs["mem_mb"]) == 262144
            assert "win_spec" in json.loads(hs["raw_json"])
            assert c.execute("SELECT COUNT(*) FROM asset_nic WHERE asset_serial='W-1'").fetchone()[0] == 3
            assert c.execute("SELECT wwpn FROM asset_hba WHERE asset_serial='W-1'").fetchone()[0] \
                == "10:00:00:90:fa:00:00:01"
        finally:
            c.close()


def test_run_collection_WinRM失敗講原因(monkeypatch):
    def boom(conn, ip, runner=None, timeout=90):
        raise RuntimeError("沒有可用的收集憑證——請先在系統設定新增 WinRM 服務帳號")
    monkeypatch.setattr(W, "fetch", boom)
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "windows")
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            r = host_spec_collector.run_collection(c, only_serial="W-1")
            assert r["ok"] == 0 and r["unsupported"] == 0
            assert "收集憑證" in r["fail_detail"][0]["error"]
        finally:
            c.close()


def test_納管後硬體規格_Windows不再寫尚未支援(monkeypatch):
    monkeypatch.setattr(W, "fetch", lambda conn, ip, runner=None, timeout=90: HYPERV)
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "windows")
    monkeypatch.setattr(manage_state, "collect_facts_into_assets", lambda conn, **k: {"updated": 1})
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            ok, msg = post_onboard._collect_one(c, "spec", "W-1")
            assert ok is True and "尚未支援" not in msg, msg
            assert "CPU 4 緒" in msg and "HBA 此機未使用" in msg
        finally:
            c.close()


# ===== 3. 系統校正：MAC =====

def _rec(hw, key, cia, measured, src, at, via, kind, cfg):
    return {"asset_serial": hw["asset_serial"], "field_key": key, "cia": cia, "measured": measured,
            "kind": kind}


def _mac_rows(c):
    cfg = {"fields": [{"key": "mac", "correct_by": ["collect"]}]}
    hw = [dict(r) for r in c.execute("SELECT * FROM hardware")]
    return [r for r in measured_hw.rows(c, hw, cfg, _rec) if r["field_key"] == "mac"]


def test_全部MAC都記錄_含虛擬_標類型(monkeypatch):
    monkeypatch.setattr(W, "fetch", lambda conn, ip, runner=None, timeout=90: PHYS)
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "windows")
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            host_spec_collector.run_collection(c, only_serial="W-1")
            got = {r["nic_name"]: (r["nic_type"], r["ips"]) for r in
                   c.execute("SELECT * FROM asset_nic WHERE asset_serial='W-1'")}
            assert got["vEthernet (Mgmt)"] == ("虛擬", "10.99.20.5")
            assert got["Ethernet 1"] == ("實體", "10.99.10.21,fe80::1")
            assert len(got) == 3, "WAN Miniport 不是網路介面，不收"
        finally:
            c.close()


def test_系統校正_MAC_對得上任一實體卡就不校正_否則校正成帶登記IP的實體卡(monkeypatch):
    monkeypatch.setattr(W, "fetch", lambda conn, ip, runner=None, timeout=90: PHYS)
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "windows")
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            host_spec_collector.run_collection(c, only_serial="W-1")
            # 1) 等於第二張實體卡（沒帶 IP）→ 對得上，不校正（格式不同也算）
            c.execute("UPDATE hardware SET mac = 'AA-BB-CC-00-11-23' WHERE asset_serial='W-1'")
            assert _mac_rows(c) == []
            # 2) 只等於虛擬卡 → 不算對得上，校正成帶登記 IP（10.99.10.21）的 Ethernet 1
            c.execute("UPDATE hardware SET mac = '00155DAA0001' WHERE asset_serial='W-1'")
            out = _mac_rows(c)
            assert len(out) == 1 and out[0]["kind"] == "corrected" and out[0]["measured"] == "aa:bb:cc:00:11:22"
            assert "vEthernet (Mgmt)" in out[0]["other_macs"] and "aa:bb:cc:00:11:22" not in out[0]["other_macs"]
            hw = dict(c.execute("SELECT * FROM hardware WHERE asset_serial='W-1'").fetchone())
            assert measured_hw.mac_view(c, hw)["measured"] == "aa:bb:cc:00:11:22"
        finally:
            c.close()


def test_IP在team上_校正成team的實體成員():
    nics = [{"nic_name": "Team1", "mac": "aa:aa:aa:00:00:01", "nic_type": "teaming", "ips": "10.99.1.5", "ip": "10.99.1.5"},
            {"nic_name": "NIC2", "mac": "aa:aa:aa:00:00:03", "nic_type": "實體", "bond": None, "ip": "10.99.9.9"},
            {"nic_name": "NIC1", "mac": "aa:aa:aa:00:00:02", "nic_type": "實體", "bond": "Team1"}]
    for n in nics:
        n["_mac"] = measured_hw._norm_mac(n["mac"])
    v = measured_hw._mac_judge({"mac": "11:22:33:44:55:66", "ip": "10.99.1.5"}, nics)
    assert v["measured"] == "aa:aa:aa:00:00:02"


def test_Linux舊資料沒有類型_docker_bond不算實體():
    assert not measured_hw._is_physical({"nic_name": "docker0"})
    assert not measured_hw._is_physical({"nic_name": "bond0"})
    assert measured_hw._is_physical({"nic_name": "ens192"})


def test_CIA回報表_MAC列有其他MAC欄():
    import measured_mismatch
    assert measured_mismatch.REPORT_HEADERS[-1] == "其他 MAC" and measured_mismatch._ACTION["mac"] == "改 MAC"


def test_收集不洗掉CIA登記的MAC():
    import dyn_shell
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            c.execute("UPDATE hardware SET mac = 'aa:bb:cc:dd:ee:ff' WHERE asset_serial='W-1'")
            assert "mac" not in dyn_shell.cia_guard_collected(c, "W-1", {"mac": "11:22:33:44:55:66"})
        finally:
            c.close()
