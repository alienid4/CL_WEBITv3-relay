"""win_rebuild_collector：Windows OS 重建資料（假輸出，不連任何真機）。

重點：
  * 跟 Linux 同一套項目模型（group／id／necessity／state／label），資產頁同一套畫法
  * 主要查詢被拒但替代來源有資料 → 已收集＋附註；替代來源也沒有 → 權限不足（需系統管理員），不是此機未使用
  * 非 Server 版沒有角色與功能 → 此機未使用
  * 祕密遮蔽（環境變數）、憑證不含私鑰本體、啟用不收金鑰
  * run_collection 對 Windows 不再略過，結果存進 host_rebuild
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import os_rebuild_collector as L  # noqa: E402
import win_rebuild_collector as W  # noqa: E402


def _r(*cols):
    return "R\t" + "\t".join(str(c) for c in cols)


FULL = "\n".join([
    "SRC=win_rebuild",
    "@@SEC os", _r("caption", "Microsoft Windows Server 2019 Standard"), _r("build", "17763"),
    _r("edition", "ServerStandard"), _r("product_type", "3"),
    "@@SEC domain", _r("domain", "corp.example"), _r("part_of_domain", "True"),
    "@@SEC tz", _r("id", "Taipei Standard Time"),
    "@@SEC locale", _r("system_locale", "zh-TW"),
    "@@SEC activation", _r("Windows(R), ServerStandard edition", "1", "VOLUME_KMSCLIENT channel"),
    "@@SEC disks", _r(0, "VMware Virtual disk", "", 107374182400, "GPT", "Online", "SAS", "True", "True"),
    "@@SEC partitions", _r(0, 2, "C", 106300440576, 135266304, "Basic", "C:\\"),
    "@@SEC volumes", _r("C", "OS", "NTFS", 106300440576, 53150220288, 4096, "Fixed"),
    "@@SEC mountpoints", _r("D:\\Data\\Mnt1\\", "", "data1", "NTFS", 53687091200, 4096),
    "@@SEC pagefile", _r("auto_managed", "", "False", ""), _r("setting", "C:\\pagefile.sys", 4096, 8192),
    "@@SEC adapters", _r("Ethernet0", "vmxnet3 Ethernet Adapter", "00-50-56-00-00-01", "Up", "10 Gbps", 4),
    "@@SEC ipaddr", _r("Ethernet0", "IPv4", "10.99.1.10", 24, "Manual"),
    "@@SEC ipconf", _r("vmxnet3 Ethernet Adapter", "00:50:56:00:00:01", "10.99.1.10", "255.255.255.0",
                       "10.99.1.254", "False"),
    "@@SEC dns", _r("vmxnet3 Ethernet Adapter", "10.99.1.1,10.99.1.2", "corp.example", "corp.example"),
    "@@SEC routes", _r("live", "10.99.50.0/24", "10.99.1.253", "Ethernet0", 256),
    _r("persistent", "10.99.50.0", "10.99.1.253", "255.255.255.0", "1"),
    "@@SEC hosts", _r("10.99.1.20 appdb01"),
    "@@SEC teaming", "NA",
    "@@SEC firewall", _r("Domain", "True", "NotConfigured", "NotConfigured"),
    "@@SEC features", _r("Web-Server", "Web Server (IIS)", "Role"),
    "@@SEC services",
    _r("W32Time", "Windows Time", "Running", "NT AUTHORITY\\LocalService",
       "C:\\Windows\\system32\\svchost.exe -k LocalService", "False"),
    _r("AppSvc", "Our App", "Running", "CORP\\svc_app", "\"D:\\App\\app.exe\" --db_password=hunter2", "True"),
    "@@SEC groups", _r("Administrators", "S-1-5-32-544", "HOST\\Administrator, CORP\\Domain Admins", "", "admins"),
    "@@SEC hotfix", _r("KB5005112", "Security Update", "8/20/2021", "NT AUTHORITY\\SYSTEM"),
    "@@SEC certs", _r("CN=web01.corp.example", "AB" * 20, "2027-01-01 00:00:00", "CN=Corp CA", "True", ""),
    "@@SEC env", _r("Path", "%SystemRoot%\\system32;D:\\App"), _r("APP_DB_PASSWORD", "hunter2"),
    _r("API_TOKEN", "tok-123"),
    "@@SEC shares", _r("Data", "D:\\Data", "", "Everyone:Allow:Read"),
    "@@SEC w32time", _r("NtpServer", "ntp1.corp.example,0x9"), _r("Type", "NTP"),
    "@@END",
])


def _items(data):
    return {it["id"]: it for g in data["groups"] for it in g["items"]}


def test_same_item_model_as_linux_and_all_collected():
    data = W.build_rebuild_data(W.parse_output(FULL))
    items = _items(data)
    assert set(items) == {i["id"] for i in W.ITEMS}
    for it in items.values():
        assert {"id", "group", "title", "necessity", "state", "label"} <= set(it)
        assert it["necessity"] in ("A", "B")
    assert items["win.net.teaming"]["state"] == "empty"            # NA → 此機未使用
    assert all(it["state"] == "collected" for k, it in items.items() if k != "win.net.teaming"), \
        {k: v["state"] for k, v in items.items()}
    s = data["summary"]
    assert s["total"] == len(W.ITEMS) and s["a_collected"] == s["total_a"]
    assert set(s) >= {"total", "total_a", "total_b", "a_collected", "by_state", "collected", "denied"}
    # 分組名稱是畫面要的那幾組
    assert [g["group"] for g in data["groups"]][:3] == ["系統基本", "儲存", "網路"]


def test_sizes_shown_in_gb_and_mountpoints_listed():
    items = _items(W.build_rebuild_data(W.parse_output(FULL)))
    vol = items["win.storage.volumes"]["text"]
    assert "99.0 GB" in vol and "D:\\Data\\Mnt1\\" in vol
    assert "100.0 GB" in items["win.storage.disks"]["text"]


def test_services_only_non_default_and_secret_masked():
    t = _items(W.build_rebuild_data(W.parse_output(FULL)))["win.svc.auto"]["text"]
    assert "AppSvc" in t and "CORP\\svc_app" in t
    assert "W32Time" not in t                       # 內建服務＋內建帳號 → 不算非預設
    assert "hunter2" not in t


def test_env_secrets_masked_path_kept():
    t = _items(W.build_rebuild_data(W.parse_output(FULL)))["win.env.system"]["text"]
    assert "%SystemRoot%\\system32;D:\\App" in t
    assert "hunter2" not in t and "tok-123" not in t


def test_cert_no_private_key_material_in_script():
    # 只讀 HasPrivateKey 布林值；不得碰 PrivateKey 屬性或匯出
    assert ".PrivateKey" not in W.REBUILD_PS and "Export" not in W.REBUILD_PS
    # 啟用狀態：不輸出產品金鑰片段
    assert "$l.PartialProductKey" not in W.REBUILD_PS and "OA3xOriginalProductKey" not in W.REBUILD_PS


def test_primary_denied_but_fallback_has_rows_is_collected_with_note():
    out = "\n".join(["@@SEC disks", "ERR\tdisks\tDENIED\tAccess denied",
                     _r(0, "Disk", "", 1073741824, "", "OK", "SCSI", "", ""), "@@END"])
    it = _items(W.build_rebuild_data(W.parse_output(out)))["win.storage.disks"]
    assert it["state"] == "collected" and "權限不足" in it["note"]


def test_denied_with_no_fallback_is_admin_denied_not_empty():
    out = "\n".join(["@@SEC disks", "ERR\tdisks\tDENIED\tAccess denied",
                     "ERR\tdisks_cim\tDENIED\t0x80041003", "@@END"])
    data = W.build_rebuild_data(W.parse_output(out))
    it = _items(data)["win.storage.disks"]
    assert it["state"] == "denied" and it["deny_kind"] == "admin"
    assert it["label"] == W.STATE_LABEL_ADMIN
    assert data["summary"]["denied_by_kind"] == {"admin": 1}
    # 沒跑到的段落 → not_run，不是此機未使用
    assert _items(data)["win.net.hosts"]["state"] == "not_run"


def test_fail_is_unknown_and_workstation_features_empty():
    out = "\n".join(["@@SEC hotfix", "ERR\thotfix\tFAIL\tRPC server unavailable",
                     "@@SEC features", "NA", "@@END"])
    items = _items(W.build_rebuild_data(W.parse_output(out)))
    assert items["win.patch.hotfix"]["state"] == "unknown" and "RPC" in items["win.patch.hotfix"]["note"]
    assert items["win.role.features"]["state"] == "empty"
    assert "非 Server" in items["win.role.features"]["note"]


def test_all_builtin_services_note_explains_empty():
    out = "\n".join(["@@SEC services", _r("W32Time", "Windows Time", "Running", "LocalSystem",
                                           "C:\\Windows\\system32\\svchost.exe -k x", "False"), "@@END"])
    it = _items(W.build_rebuild_data(W.parse_output(out)))["win.svc.auto"]
    assert it["state"] == "empty" and "內建" in it["note"]


def test_collect_rebuild_uses_injected_runner_and_masks_raw():
    seen = {}

    def runner(host, script):
        seen["script"] = script
        return FULL

    res = W.collect_rebuild("10.99.1.10", "u", "p", runner=runner)
    assert seen["script"] is W.REBUILD_PS
    assert res["platform"] == "windows" and res["raw"]["complete"] is True
    assert "hunter2" not in json.dumps(res, ensure_ascii=False)


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def test_run_collection_windows_not_skipped_and_stored(conn, monkeypatch):
    db.insert_hardware(conn, asset_serial="WIN-1", hostname="win01", ip="10.99.1.10",
                       os="Microsoft Windows Server 2019 Standard")
    conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'WIN-1'")
    conn.commit()
    monkeypatch.setattr(W, "collect_for",
                        lambda c, serial, ip, runner=None: (W.collect_rebuild(ip, "", "", runner=lambda h, s: FULL),
                                                            None))
    r = L.run_collection(conn, only_serial="WIN-1")
    assert r["ok"] == 1 and r["unsupported"] == 0, r
    row = conn.execute("SELECT platform, data_json FROM host_rebuild WHERE asset_serial='WIN-1'").fetchone()
    assert row["platform"] == "windows"
    assert json.loads(row["data_json"])["summary"]["collected"] > 20


def test_run_collection_windows_failure_reason_reported(conn, monkeypatch):
    db.insert_hardware(conn, asset_serial="WIN-2", hostname="win02", ip="10.99.1.11",
                       os="Microsoft Windows Server 2022")
    conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'WIN-2'")
    conn.commit()
    monkeypatch.setattr(W, "collect_for", lambda c, s, ip, runner=None: (None, "沒有可用的收集憑證"))
    r = L.run_collection(conn, only_serial="WIN-2")
    assert r["failed"] == 1 and "憑證" in r["fail_detail"][0]["error"]


# ---- 獨立審查補強（2026-10-20）----
def test_never_empty_sections_zero_rows_without_error_is_denied_not_unused():
    out = "\n".join(["@@SEC disks", "@@SEC certs", "@@END"])
    items = _items(W.build_rebuild_data(W.parse_output(out)))
    assert items["win.storage.disks"]["state"] == "denied"
    assert items["win.cert.my"]["state"] == "empty"         # 沒有憑證是正常的


def test_service_space_separated_password_flag_masked():
    out = "\n".join(["@@SEC services",
                     _r("X", "x", "Running", "CORP\\svc", "\"D:\\x.exe\" -password S3cr3t /pw:abc --token tk9", "False"),
                     "@@END"])
    t = _items(W.build_rebuild_data(W.parse_output(out)))["win.svc.auto"]["text"]
    assert "S3cr3t" not in t and "abc" not in t and "tk9" not in t and "D:\\x.exe" in t


def test_env_key_and_access_key_masked():
    out = "\n".join(["@@SEC env", _r("AWS_ACCESS_KEY_ID", "AKIAX"), _r("STORAGE_SAS", "sv=1&sig=zz"),
                     _r("Path", "C:\\x"), "@@END"])
    t = _items(W.build_rebuild_data(W.parse_output(out)))["win.env.system"]["text"]
    assert "AKIAX" not in t and "sig=zz" not in t and "C:\\x" in t


def test_builtin_only_services_with_registry_fallback_is_empty_not_denied():
    out = "\n".join(["@@SEC services", "ERR\tservices\tEMPTY\tWin32_Service returned no rows",
                     _r("W32Time", "", "", "NT AUTHORITY\\LocalService",
                        "C:\\Windows\\system32\\svchost.exe -k LocalService", "False"), "@@END"])
    it = _items(W.build_rebuild_data(W.parse_output(out)))["win.svc.auto"]
    assert it["state"] == "empty"


def test_incomplete_output_marks_note():
    out = "\n".join(["@@SEC os", _r("caption", "Windows Server 2019")])   # 沒有 @@END
    it = _items(W.build_rebuild_data(W.parse_output(out)))["win.sys.os"]
    assert it["state"] == "collected" and "沒有跑完" in it["note"]


def test_all_denied_host_is_stored_but_reported_failed(conn, monkeypatch):
    db.insert_hardware(conn, asset_serial="WIN-3", hostname="win03", ip="10.99.1.12",
                       os="Microsoft Windows Server 2019")
    conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'WIN-3'")
    conn.commit()
    denied = "\n".join(["@@SEC os", "ERR\tos\tDENIED\tAccess denied", "@@SEC locale", _r("culture", "zh-TW"),
                        "@@END"])
    monkeypatch.setattr(W, "collect_for", lambda c, s, ip, runner=None:
                        (W.collect_rebuild(ip, "", "", runner=lambda h, sc: denied), None))
    r = L.run_collection(conn, only_serial="WIN-3")
    assert r["failed"] == 1 and r["ok"] == 0
    row = conn.execute("SELECT data_json FROM host_rebuild WHERE asset_serial='WIN-3'").fetchone()
    assert row and json.loads(row["data_json"])["summary"]["denied"] >= 1
