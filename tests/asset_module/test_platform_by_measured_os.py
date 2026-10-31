"""收集／略過判斷一律照實測 OS（2026-10-21 使用者：「組態檢核／略過判斷一律照實測 OS」）。

公司實例（IP 換成 10.99.x）：SECSVR194-068T，CIA 登記 Windows Server 2019、IP .168；
實際從 .68 SSH 納管、機器講 RHEL 9.4（記在同一台的帳外那筆 DYN-.68）。
舊行為：post_onboard／組態檢核／OS 重建照 CIA 的 Windows 判 → 組態檢核、OS 重建走 Windows 那條。
新行為：實測優先；沒有實測才照 CIA，訊息講「依 CIA 登記判斷」；ESXi／vCenter 照舊六樣全略過。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collected_os  # noqa: E402
import config_audit as ca  # noqa: E402
import db  # noqa: E402
import machine_identity  # noqa: E402
import manage_state  # noqa: E402
import os_rebuild_collector as R  # noqa: E402
import post_onboard  # noqa: E402

CIA = "SECSVR194-068T"
DYN = "DYN-10.99.194.68"


@pytest.fixture(autouse=True)
def _restore_identity_state():
    saved = dict(machine_identity._STATE)
    yield
    machine_identity._STATE.clear()
    machine_identity._STATE.update(saved)


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        machine_identity._set_mode(c, "hostname")
        c.commit()
        try:
            yield c
        finally:
            c.close()


def _hw(c, serial, ip, os_text, hostname, collect_ok=1, **kw):
    db.insert_hardware(c, asset_serial=serial, ip=ip, os=os_text, hostname=hostname,
                       environment="測試", asset_status="使用中", **kw)
    c.execute("UPDATE hardware SET collect_ok = ?, collect_checked_at = ? WHERE asset_serial = ?",
              (collect_ok, "2026-10-20 10:00:00" if collect_ok else None, serial))
    c.commit()


def _company(c):
    _hw(c, CIA, "10.99.194.168", "Windows Server 2019", "SECSVR194-068T", asset_purpose="AP")
    _hw(c, DYN, "10.99.194.68", "Red Hat Enterprise Linux 9.4 (Plow)", "secsvr194-068t")
    machine_identity.refresh(c)


def _no_probe(monkeypatch):
    monkeypatch.setattr(manage_state, "probe_uname", lambda *a, **k: None)


# ===== 1. 判斷本身 =====

def test_實測RHEL_CIA寫Windows_判Linux且講出不同(conn, monkeypatch):
    _no_probe(monkeypatch)
    _company(conn)
    info = manage_state.collect_platform_explain(conn, "10.99.194.168", "Windows Server 2019",
                                                 asset_serial=CIA)
    assert info["platform"] == "linux"
    assert info["basis"] == manage_state.BASIS_MEASURED
    assert "實測" in info["note"] and "Windows Server 2019" in info["note"] and "不同" in info["note"]
    # collect_platform_of（所有收集器共用）同一個答案
    assert manage_state.collect_platform_of(conn, "10.99.194.168", "Windows Server 2019",
                                            asset_serial=CIA) == "linux"
    # CIA 值不動
    assert conn.execute("SELECT os FROM hardware WHERE asset_serial=?", (CIA,)).fetchone()[0] \
        == "Windows Server 2019"


def test_單台查實測_跟全庫measured_map同一個答案(conn):
    _company(conn)
    full = collected_os.measured_map(conn)
    for s in (CIA, DYN):
        one = collected_os.measured_for(conn, s)
        assert one is not None and one["platform"] == full[s]["platform"] == "linux"
        assert one["via_serial"] == full[s]["via_serial"] == DYN


def test_單台查實測_各種情境都跟全庫版一致_差分(conn):
    """measured_for（單台，收集判斷用）vs measured_map 去掉 RVTools（全庫，分類用）：逐筆一致。"""
    _company(conn)
    # AIX：facts 寫成 oslevel -s，CIA 原值搬到 os_cia（strong）
    _hw(conn, "A-1", "10.99.50.1", "7200-05-09-2446", "aix01", os_cia="AIX 7.2")
    # 帳外那筆單獨一台（strong）
    _hw(conn, "DYN-10.99.50.2", "10.99.50.2", "Rocky Linux 9.3", "rocky01")
    # 已納管但分不出是不是實測（weak）
    _hw(conn, "W-1", "10.99.50.3", "Windows Server 2022", "win01")
    # 只有帳號收集順手記的 os-release（account-only）：登記空白、沒納管
    _hw(conn, "L-9", "10.99.50.4", "", "acct01", collect_ok=0)
    conn.execute("INSERT INTO host_account (asset_serial, ip, username, os_family, os_id, os_version, "
                 "last_seen) VALUES ('L-9', '10.99.50.4', 'root', 'linux', 'rhel', '8.10', "
                 "'2026-10-20 09:00:00')")
    # 退役那筆不跟現役同一台
    _hw(conn, "R-1", "10.99.194.68", "Windows Server 2012", "secsvr194-068t", collect_ok=0)
    conn.execute("UPDATE hardware SET asset_status='已報廢' WHERE asset_serial='R-1'")
    # 沒有任何證據
    _hw(conn, "N-1", "10.99.50.5", "Windows Server 2019", "none01", collect_ok=0)
    conn.commit()
    machine_identity.refresh(conn)
    full = {s: m for s, m in collected_os._measured_map(conn).items() if m.get("source") == "collect"}
    serials = [r[0] for r in conn.execute("SELECT asset_serial FROM hardware")]
    for s in serials:
        one = collected_os.measured_for(conn, s)
        want = full.get(s)
        assert (one is None) == (want is None), s
        if one is not None:
            assert (one["platform"], one["via_serial"], one["strong"]) == \
                (want["platform"], want["via_serial"], want["strong"]), s
    assert full["A-1"]["platform"] == "aix" and full["L-9"]["platform"] == "linux"
    assert full["W-1"]["strong"] is False and "N-1" not in full


def test_沒有實測_CIA寫Windows_照Windows並講依CIA登記(conn, monkeypatch):
    _no_probe(monkeypatch)
    _hw(conn, "W-1", "10.99.30.5", "Windows Server 2019", "winsvr01", collect_ok=0)
    machine_identity.refresh(conn)
    info = manage_state.collect_platform_explain(conn, "10.99.30.5", "Windows Server 2019",
                                                 asset_serial="W-1")
    assert info["platform"] == "windows" and info["basis"] == manage_state.BASIS_REGISTRY
    assert info["note"] == "依 CIA 登記判斷（尚未實測）"


def test_已納管但os欄分不出是不是實測_照CIA並講出來(conn, monkeypatch):
    _no_probe(monkeypatch)
    _hw(conn, "W-1", "10.99.30.5", "Windows Server 2019", "winsvr01", collect_ok=1)
    machine_identity.refresh(conn)
    info = manage_state.collect_platform_explain(conn, "10.99.30.5", "Windows Server 2019",
                                                 asset_serial="W-1")
    assert info["platform"] == "windows"
    assert "依 CIA 登記判斷" in info["note"]


def test_作業系統不知道_不靜默_講是猜的(conn, monkeypatch):
    _no_probe(monkeypatch)
    _hw(conn, "U-1", "10.99.30.9", "", "unknown01", collect_ok=0)
    info = manage_state.collect_platform_explain(conn, "10.99.30.9", None, asset_serial="U-1")
    assert info["basis"] == manage_state.BASIS_GUESS
    assert "不知道" in info["note"] and "推測" in info["note"]


def test_實測跟CIA一致_不多講話(conn, monkeypatch):
    _no_probe(monkeypatch)
    _hw(conn, "L-1", "10.99.30.7", "Red Hat Enterprise Linux 9.4 (Plow)", "rhel01",
        os_cia="RHEL 9")
    machine_identity.refresh(conn)
    info = manage_state.collect_platform_explain(conn, "10.99.30.7", None, asset_serial="L-1")
    assert info["platform"] == "linux" and info["basis"] == manage_state.BASIS_MEASURED
    assert info["note"] is None


# ===== 2. 納管後自動收集（post_onboard） =====

def _stub_jobs(monkeypatch, called):
    import account_inventory
    import health_probe
    import host_spec_collector
    import schedule_inventory
    import service_inventory
    import software_inventory

    def mk(job):
        def fake(conn, *a, **k):
            called.append(job)
            return {"ok": 1}
        return fake
    monkeypatch.setattr(host_spec_collector, "run_collection", mk("spec"))
    monkeypatch.setattr(service_inventory, "collect_services", mk("service"))
    monkeypatch.setattr(software_inventory, "collect_software", mk("software"))
    monkeypatch.setattr(account_inventory, "collect_accounts", mk("account"))
    monkeypatch.setattr(R, "run_collection", mk("rebuild"))
    monkeypatch.setattr(schedule_inventory, "collect_windows", mk("schedule"))
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, *a, **k: (called.append("spec_facts"), {"updated": 1})[1])

    def fake_ca(conn, serial, runner=None, by=None):
        called.append("config_audit")
        return {"total": 61, "counts": {"Non-Compliant": 2, "Error": 0}}
    monkeypatch.setattr(ca, "collect_from_host", fake_ca)
    monkeypatch.setattr(health_probe, "check_one", lambda *a, **k: {})
    _no_probe(monkeypatch)


def test_納管後_實測RHEL_CIA寫Windows_組態檢核與OS重建都跑_排程照Linux略過(conn, monkeypatch):
    _company(conn)
    called = []
    _stub_jobs(monkeypatch, called)
    res = {r["job"]: r for r in post_onboard.run_now(conn, CIA)}
    assert "config_audit" in called and res["config_audit"]["ok"] is True
    assert "rebuild" in called and res["rebuild"]["ok"] is True
    # 不再出現舊的「Windows 尚無組態檢核腳本」「OS 重建只做 Linux」
    for r in res.values():
        assert "尚無組態檢核腳本" not in r["message"] and "只做 Linux" not in r["message"]
    # 規格走 SSH（Linux），不是 Windows 的 WinRM 主機事實
    assert "spec" in called and "spec_facts" not in called
    # 排程：Linux 讀 OS 重建資料、不另收（照實測，不是照 CIA 去跑 Windows 排程）
    assert "schedule" not in called and res["schedule"]["skipped"]
    assert "排程讀 OS 重建資料" in res["schedule"]["message"]
    # 實測跟 CIA 不同 → 步驟訊息講一句
    assert "與實測不同" in res["config_audit"]["message"]
    assert "與實測不同" in res["rebuild"]["message"]


def test_納管後_沒有實測_CIA寫Windows_走Windows並講依CIA登記(conn, monkeypatch):
    _hw(conn, "W-1", "10.99.30.5", "Windows Server 2019", "winsvr01", collect_ok=0)
    machine_identity.refresh(conn)
    called = []
    _stub_jobs(monkeypatch, called)
    res = {r["job"]: r for r in post_onboard.run_now(conn, "W-1")}
    assert "spec_facts" in called                       # Windows：WinRM 主機事實
    assert "schedule" in called and res["schedule"]["ok"] is True
    assert "依 CIA 登記判斷" in res["config_audit"]["message"]
    assert "依 CIA 登記判斷" in res["rebuild"]["message"]


@pytest.mark.parametrize("os_text", ["VMware ESXi 8.0", "VMware vCenter Server 8.0 (Photon)"])
def test_納管後_ESXi_vCenter_六樣仍一律略過_即使同台有Linux實測(conn, monkeypatch, os_text):
    # 登記成 VMware 設備的那筆，就算同一台另一筆收到 Linux 字樣，也不能被翻成 Linux（fail-closed）
    _hw(conn, "V-1", "10.99.40.1", os_text, "esx01")
    _hw(conn, "DYN-10.99.40.1", "10.99.40.1", "VMware Photon OS/Linux", "esx01")
    machine_identity.refresh(conn)
    called = []
    _stub_jobs(monkeypatch, called)
    res = post_onboard.run_now(conn, "V-1")
    assert called == []
    assert all(r["skipped"] and "VMware" in r["message"] for r in res)


# ===== 3. 呼叫端：組態檢核分派、OS 重建 =====

def test_組態檢核_實測RHEL_走Linux腳本不走Windows(conn, monkeypatch):
    _no_probe(monkeypatch)
    _company(conn)
    monkeypatch.setattr(ca, "_collect_windows",
                        lambda *a, **k: pytest.fail("實測是 RHEL，不可以走 Windows 那條"))
    sent = []

    def runner(ip, cmd):
        sent.append((ip, cmd))
        return ""            # 空輸出 → 後面會報「收不到檢核輸出」，這裡只看分派
    with pytest.raises(ValueError, match="收不到檢核輸出"):
        ca.collect_from_host(conn, CIA, runner=runner)
    assert len(sent) == 1                       # SSH 送出 Linux 檢核腳本（不是 AIX、不是 WinRM）
    assert ca._remote_cmd("linux", ca._latest_fcb_rhel().read_text(encoding="utf-8")) == sent[0][1]


def test_組態檢核_沒有實測_CIA寫Windows_走Windows(conn, monkeypatch):
    _no_probe(monkeypatch)
    _hw(conn, "W-1", "10.99.30.5", "Windows Server 2019", "winsvr01")
    machine_identity.refresh(conn)
    went = []
    monkeypatch.setattr(ca, "_collect_windows", lambda *a, **k: went.append(1) or {"total": 0})
    ca.collect_from_host(conn, "W-1", runner=lambda ip, cmd: pytest.fail("Windows 不送 SSH 腳本"))
    assert went == [1]


def test_OS重建_實測RHEL_整台都走Linux那條(conn, monkeypatch):
    _no_probe(monkeypatch)
    _company(conn)
    import win_rebuild_collector
    monkeypatch.setattr(win_rebuild_collector, "collect_for",
                        lambda *a, **k: pytest.fail("實測是 RHEL，不可以走 Windows 那條"))
    plats = []
    monkeypatch.setattr(manage_state, "_runner_for", lambda *a, **k: object())
    monkeypatch.setattr(R, "collect_rebuild", lambda runner, ip, platform: plats.append(platform) or {})
    monkeypatch.setattr(R, "_any_rebuild", lambda res: False)
    monkeypatch.setattr(R, "_save_trace", lambda *a, **k: None)
    monkeypatch.setattr(R, "_why_empty", lambda *a, **k: "測試：沒收到")
    R.run_collection(conn, trigger="test", only_serial=CIA)
    assert plats and set(plats) == {"linux"}
