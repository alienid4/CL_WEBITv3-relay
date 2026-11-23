"""OS 大類：全站唯一一份規則（OS-CLASS-AUDIT，2026-10-18）。

背景：221 唯讀分析（machine_key_trial 舊關鍵字）非退役有 1106 筆掉「其他」——網路設備、
儲存、CoreOS、ESXi、iDRAC／ATEN、IBM i 全混在裡面；首頁長條又是另一份前端分組。
這裡守住：
  1. 各類代表樣本判對（含 7.2.10 這種 FortiOS 裸版本號不能當 AIX）
  2. 試算腳本內嵌的規則＝設定檔、判出來的結果＝正式程式（漂移守門）
  3. 首頁長條加總＝有效資產、長條數字＝點進去 /api/assets?os_class= 的筆數
IP 一律 10.99.x（範例），主機名是假的。
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
SCRIPT = ROOT / "APP" / "asset-module" / "scripts" / "machine_key_trial.py"
sys.path.insert(0, str(BACKEND))

import os_class  # noqa: E402

_spec = importlib.util.spec_from_file_location("machine_key_trial_oc", SCRIPT)
trial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trial)

# (os, device_model, group_name, asset_purpose, guess) → 期望大類
CASES = [
    (("CoreOS4.20", None, None, None, None), "Linux"),
    (("RHCOS 4", None, None, None, None), "Linux"),
    (("Coreos", "(VM)", None, None, None), "Linux"),
    (("Red Hat Enterprise Linux 8.10", None, None, None, None), "Linux"),
    (("Windows Server 2022", None, None, None, None), "Windows"),
    (("AIX 7.2", "IBM S922", None, None, None), "AIX"),
    # FortiOS 裸版本號：只看版本會被誤認 AIX 7.2，要靠機型／群組
    (("7.2.10", "Fortinet FortiGate 101F", None, None, None), "網路設備"),
    (("7.2.11", None, "H6-一般網路設備", None, None), "網路設備"),
    (("7.2.10", None, None, None, None), "其他"),
    (("17.03.04b", "Cisco C9200L-48P-4X", None, None, None), "網路設備"),
    (("cisco 15.2(4)E7", None, None, None, None), "網路設備"),
    (("APIC 5.2(8h)", None, None, None, None), "網路設備"),
    (("12.3R9.4", "Juniper EX2200-24T-4G", None, None, None), "網路設備"),
    (("WC.16.10.0009", "Aruba 2930F", None, None, None), "網路設備"),
    (("網路設備", None, None, None, None), "網路設備"),
    # CIA 群組寫網路設備但 OS 是 CentOS（Palo Alto VM）→ 群組優先
    (("CentOS 7 (64-bit)", "VM-Palo Alto VM-100", "H6-一般網路設備", None, None), "網路設備"),
    (("儲存設備", None, None, None, None), "儲存設備"),
    (("Avamar - 客製OS", "(VM)", None, None, None), "儲存設備"),
    (("FOS v9", "EMC SAN Switch", None, None, None), "儲存設備"),
    # SAN Switch 屬儲存（使用者 2026-09-10），就算群組寫網路設備也一樣
    (("N/A", "HP SAN Switch", "H6-一般網路設備", None, None), "儲存設備"),
    (("", "IBM FlashSystem 5100", None, None, None), "儲存設備"),
    (("VMWare ESXi 8.0.3", None, None, None, None), "虛擬化平台"),
    (("VMware ESXi, 8.0.3", None, None, None, None), "虛擬化平台"),
    # vCenter appliance（Photon OS）不是可納管的 Linux（2026-10-18 小總管拍板）
    (("VMware Photon OS (64-bit)", None, None, None, None), "虛擬化平台"),
    (("VM VC", "(VM)", None, None, None), "虛擬化平台"),
    (("Idrac 7.10.70.00", "DELL R750", None, None, None), "管理介面"),
    (("iDRAC 9", None, None, None, None), "管理介面"),
    (("ATEN v1.5.141", None, None, None, None), "管理介面"),
    (("V10R3", "IBM 7063-CR2", None, None, None), "管理介面"),     # HMC，不是 IBM i
    (("", "HPE DL380 Gen10", None, "分公司主機 (iLo遠端管理)", None), "管理介面"),
    (("V7R3", "IBM AS400", None, None, None), "IBM i"),
    (("V7R5", None, None, None, None), "IBM i"),
    (("", "IBM AS400", None, None, None), "IBM i"),
    (("客製化系統", None, None, None, None), "其他"),
    (("MySQL 8.0.29", "(VM)", None, None, None), "其他"),
    (("", None, None, None, None), "未知"),
    (("N/A", "(VM)", None, None, None), "未知"),
    (("N/A", None, None, None, "Windows（RDP 3389）"), "Windows"),   # 沒 OS 才用掃描推測
    (("客製化系統", None, None, None, "Linux/Unix（TTL≈64）"), "其他"),  # 有 OS 不拿推測蓋
]


def test_各類代表樣本判對():
    bad = [(args, want, os_class.classify(*args)) for args, want in CASES if os_class.classify(*args) != want]
    assert not bad, bad


def test_判出的類別都在設定檔類別清單內():
    classes = set(os_class.class_order())
    assert {os_class.classify(*a) for a, _ in CASES} <= classes
    assert {r["class"] for r in os_class.load_rules()["rules"]} <= classes


def test_試算腳本內嵌規則_逐字等於設定檔():
    cfg = json.loads((BACKEND / "os_class_groups.json").read_text(encoding="utf-8"))
    assert trial.OS_CLASS_RULES == {k: v for k, v in cfg.items() if not k.startswith("_")}
    assert trial.OS_ORDER == cfg["classes"]


def test_試算腳本判法_逐筆等於正式程式():
    extra = [("Ubuntu 22.04", None, None, None, None), ("Juniper", "", "", "", ""),
             ("  WINDOWS 10 ", None, None, None, None), ("Cisco\nC9200L", None, None, None, None),
             (None, "Cisco\nC9200L-48T-4X-E", None, None, None)]
    for args in [a for a, _ in CASES] + extra:
        assert trial.os_class(*args) == os_class.classify(*args), args


def test_試算腳本只用標準函式庫():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "import os_class" not in src and "from os_class" not in src


def _client(tmp):
    import api
    import auth
    import db
    from fastapi.testclient import TestClient

    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    conn = db.get_connection(p)
    rows = [
        dict(asset_serial="O-1", hostname="lx01", ip="10.99.0.1", os="CoreOS 4.18"),
        dict(asset_serial="O-2", hostname="fw01", ip="10.99.0.2", os="7.2.10",
             device_model="Fortinet FG-61F", group_name="H6-一般網路設備"),
        dict(asset_serial="O-3", hostname="sw01", ip="10.99.0.3", os="17.03.04b", device_model="Cisco C9200L"),
        dict(asset_serial="O-4", hostname="san01", ip="10.99.0.4", os="N/A", device_model="HP SAN Switch"),
        dict(asset_serial="O-5", hostname="esx01", ip="10.99.0.5", os="VMWare ESXi 8.0.3"),
        dict(asset_serial="O-6", hostname="drac01", ip="10.99.0.6", os="Idrac 7.10"),
        dict(asset_serial="O-7", hostname="as01", ip="10.99.0.7", os="V7R3", device_model="IBM AS400"),
        dict(asset_serial="O-8", hostname="win01", ip="10.99.0.8", os="Windows Server 2019"),
        dict(asset_serial="O-9", hostname="x01", ip="10.99.0.9", os="客製化系統"),
        dict(asset_serial="O-10", hostname="old", ip="10.99.0.10", os="Windows Server 2012", asset_status="報廢"),
        dict(asset_serial="O-11", hostname="u01", ip="10.99.0.11"),
    ]
    try:
        db.create_user(conn, "tester", auth.hash_password("test-password-123"))
        for r in rows:
            db.insert_hardware(conn, **r)
        conn.commit()
    finally:
        conn.close()
    client = TestClient(api.app)
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": "test-password-123"}).status_code == 200
    return client, p


def test_首頁大類長條_加總等於有效資產_且等於點進去的筆數():
    import api
    import db
    import manage_state

    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            conn = db.get_connection(p)
            try:
                c = manage_state.composition(conn)
            finally:
                conn.close()
            assert sum(c["by_os_class"].values()) == c["total"] == 10   # 報廢那筆不算
            assert c["by_os_class"] == {"Linux": 1, "網路設備": 2, "儲存設備": 1, "虛擬化平台": 1,
                                        "管理介面": 1, "IBM i": 1, "Windows": 1, "其他": 1, "未知": 1}
            for k, pm in c["by_os_class_platform"].items():
                assert sum(pm.values()) == c["by_os_class"][k], k
            for k, n in c["by_os_class"].items():
                got = client.get("/api/assets", params={"os_class": k}).json()
                assert len(got) == n, (k, n, [g["asset_serial"] for g in got])
        finally:
            api.app.dependency_overrides.clear()


def test_月報表1照大類決定列():
    import system_report

    assert system_report._report_platform("Linux(其他)", "Red Hat CoreOS 4.12", "Linux") == "Linux"
    assert system_report._report_platform("未知", None, "網路設備") is None
    assert system_report._report_platform("未知", None, "虛擬化平台") == "VMware ESXi"
    # Photon（vCenter）留在 VMware 列，不搬去 Linux
    assert system_report._report_platform("VMware ESXi", None, "虛擬化平台") == "VMware ESXi"
    assert system_report._report_platform("未知", "Windows Server 2019", "Windows") == "Windows Server"
    assert system_report._report_platform("網路設備", None, "儲存設備") is None
