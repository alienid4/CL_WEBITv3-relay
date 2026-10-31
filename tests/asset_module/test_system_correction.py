"""系統校正（v1.473.0）：已納管主機以實測 OS／IP 為準，CIA 值不動、標「⟲ 系統校正」、列清單。

公司實例（IP 換成 10.99.x）：CIA 登記 068T＝.168＋Windows Server 2019；實際從 .68 SSH 納管、
機器講 RHEL 9.4、主機名 068T；.168 其實是另一台 168T（Windows，掃描建的 DYN-）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import asset_classify  # noqa: E402
import collected_os  # noqa: E402
import db  # noqa: E402
import machine_identity  # noqa: E402
import manage_state  # noqa: E402
import measured_mismatch  # noqa: E402


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_identity_state():
    """這裡切成主機名口徑；跑完還原，別讓全域口徑漏到後面的測試。"""
    saved = dict(machine_identity._STATE)
    yield
    machine_identity._STATE.clear()
    machine_identity._STATE.update(saved)


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    machine_identity._set_mode(conn, "hostname")
    conn.commit()
    return conn


def _hw(conn, serial, ip, os_text, hostname, collect_ok=0, **kw):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os_text, hostname=hostname, **kw)
    conn.execute("UPDATE hardware SET collect_ok = ?, collect_checked_at = ? WHERE asset_serial = ?",
                 (collect_ok, "2026-10-20 10:00:00" if collect_ok else None, serial))
    conn.commit()


def _company(conn):
    _hw(conn, "SECSVR194-068T", "10.99.194.168", "Windows Server 2019", "SECSVR194-068T",
        collect_ok=1, environment="測試", asset_status="使用中", asset_purpose="AP")
    _hw(conn, "DYN-10.99.194.68", "10.99.194.68", "Red Hat Enterprise Linux 9.4 (Plow)",
        "secsvr194-068t", collect_ok=1)
    _hw(conn, "DYN-10.99.194.168", "10.99.194.168", "Windows Server 2019", "SECSVR194-168T")
    machine_identity.refresh(conn)


def test_公司實例_分類與顯示用實測_CIA值不動():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _company(conn)
        c = asset_classify.classify_one(conn, "SECSVR194-068T")
        # 舊：Windows（只看自己這筆的 os）；新：同一台的實測 RHEL
        assert c["os_class"] == "Linux"
        assert c["os_type"] == "Linux"
        assert c["os_detail"] == "RHEL"
        hw = dict(conn.execute("SELECT * FROM hardware WHERE asset_serial='SECSVR194-068T'").fetchone())
        assert hw["os"] == "Windows Server 2019" and hw["ip"] == "10.99.194.168"   # CIA 值不動
        m = collected_os.measured_map(conn)["SECSVR194-068T"]
        ov = measured_mismatch.os_view(hw, m)
        assert ov["basis"] == "measured" and ov["corrected"] and "Red Hat" in ov["effective"]
        iv = measured_mismatch.ip_view(hw, m)
        assert iv == {"measured": "10.99.194.68", "cia": "10.99.194.168",
                      "via_serial": "DYN-10.99.194.68", "collected_at": "2026-10-20 10:00:00"}
        # 168T 是另一台（不同主機名），不被併進 068T、自己的分類不受影響
        assert "DYN-10.99.194.168" not in manage_state.same_machine_serials(conn, "SECSVR194-068T")
        assert asset_classify.classify_one(conn, "DYN-10.99.194.168")["os_class"] == "Windows"
        conn.close()


def test_首頁長條_點進去_一致():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _company(conn)
        comp = manage_state.composition(conn)
        cls = asset_classify.classify_all(conn)
        from collections import Counter
        drill = Counter(v["os_class"] for v in cls.values())
        assert comp["by_os_class"] == dict(drill)
        assert comp["by_os_class"].get("Linux") == 2      # 068T 正本＋.68 那筆
        conn.close()


def test_系統校正清單_與歷程_CIA已更正():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _company(conn)
        items = measured_mismatch.corrections(conn)
        got = {(r["asset_serial"], r["field_key"]): r for r in items}
        assert set(got) == {("SECSVR194-068T", "os"), ("SECSVR194-068T", "ip")}
        assert got[("SECSVR194-068T", "os")]["confidence"] == "確定"
        assert [r["no"] for r in measured_mismatch.rows(conn)] == list(range(1, len(measured_mismatch.rows(conn)) + 1))
        measured_mismatch.sync_log(conn, now="2026-10-20 11:00:00")
        # CIA 重匯：作業系統改對了 → 那筆移到「CIA 已更正」，IP 仍待更正
        conn.execute("UPDATE hardware SET os='RedHat 9.4', os_cia='RedHat 9.4' WHERE asset_serial='SECSVR194-068T'")
        conn.commit()
        r = measured_mismatch.sync_log(conn, now="2026-10-21 09:00:00")
        assert r["closed"] == 1
        hist = measured_mismatch.history(conn)
        assert [(h["field_key"], h["kind"]) for h in hist] == [("os", "cia_fixed")]
        assert {x["field_key"] for x in measured_mismatch.corrections(conn)} == {"ip"}
        conn.close()


def test_VMware_fail_closed_兄弟筆實測不翻設備類():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "VC-01", "10.99.5.10", "VMware vCenter Server Appliance 8", "vcsa-01",
            asset_status="使用中")
        _hw(conn, "DYN-10.99.5.11", "10.99.5.11", "Linux 5.x", "vcsa-01", collect_ok=1)
        machine_identity.refresh(conn)
        assert "VC-01" not in collected_os.measured_map(conn)
        assert asset_classify.classify_one(conn, "VC-01")["os_class"] == "虛擬化平台"
        conn.close()


def test_只有掃描講不一樣_不自動校正():
    cfg = measured_mismatch.config()
    # 掃描只有「觀察到的主機名」可以校正；OS／IP 是推測或只提示（PRECEDENCE1）
    assert not any("scan" in f.get("correct_by", []) for f in cfg["fields"] if f["key"] in ("os", "ip"))
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-1", "10.99.6.1", "Windows Server 2019", "host-1", asset_status="使用中")
        hid = conn.execute("SELECT id FROM hardware WHERE asset_serial='S-1'").fetchone()["id"]
        conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_hardware_id) "
                     "VALUES ('dynassets', 'k1', ?, ?)",
                     (json.dumps({"os": "Red Hat Enterprise Linux 9.4", "ip": "10.99.6.1"}), hid))
        conn.commit()
        machine_identity.refresh(conn)
        assert asset_classify.classify_one(conn, "S-1")["os_class"] == "Windows"   # 不翻
        rows = measured_mismatch.rows(conn)
        assert [(r["field_key"], r["kind"]) for r in rows] == [("os", "suspect")]
        assert measured_mismatch.corrections(conn) == []
        conn.close()


def test_RVTools第三證人_用VM名對到CIA正本():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-2", "10.99.7.2", "Windows Server 2019", "app-02", asset_status="使用中")
        conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('vcenter', 'u2', ?)",
                     (json.dumps({"vm_name": "APP-02", "hostname": "app-02.corp.example",
                                  "os": "Red Hat Enterprise Linux 9 (64-bit)", "ip": "10.99.7.2"}),))
        conn.commit()
        machine_identity.refresh(conn)
        rv = collected_os.rvtools_map(conn)["S-2"]
        assert rv["vm_name"] == "APP-02" and rv["linked"] is False
        # PRECEDENCE1：RVTools > CIA，但作業系統只在平台家族不同時才蓋（Windows vs Linux → 蓋）
        assert collected_os.measured_map(conn)["S-2"]["source"] == "rvtools"
        assert asset_classify.classify_one(conn, "S-2")["os_class"] == "Linux"
        rows = measured_mismatch.rows(conn)
        assert [(r["field_key"], r["kind"], r["source_key"]) for r in rows] == [("os", "corrected", "rvtools")]
        conn.close()


def test_RVTools_CIA寫了認不出的OS_不蓋():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-4", "10.99.7.4", "CoreOS4.20", "ocp-04", asset_status="使用中")
        conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('vcenter', 'u4', ?)",
                     (json.dumps({"vm_name": "ocp-04", "hostname": "ocp-04",
                                  "os": "Red Hat Enterprise Linux 8 (64-bit)", "ip": "10.99.7.4"}),))
        conn.commit()
        machine_identity.refresh(conn)
        assert "S-4" not in collected_os.measured_map(conn)
        conn.close()


def test_實測主機名校正_CIA打錯字_變同一台_可關掉(monkeypatch):
    """公司 HW-00010439：CIA 寫 SECSVR018-145TT @.142；.142 WinRM 納管讀到 SECSVR018-142T。"""
    import system_stats
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "HW-00010439", "10.99.18.142", "Windows Server 2019", "SECSVR018-145TT",
            environment="正式", asset_status="使用中", asset_purpose="AP")
        _hw(conn, "DYN-10.99.18.142", "10.99.18.142", "Microsoft Windows Server 2019 Standard",
            "SECSVR018-142T", collect_ok=1)
        machine_identity.refresh(conn)
        same = manage_state.same_machine_serials(conn, "HW-00010439")
        assert set(same) == {"HW-00010439", "DYN-10.99.18.142"}           # 舊：只有自己
        assert manage_state.same_machine_serials(conn, "DYN-10.99.18.142") == same or             set(manage_state.same_machine_serials(conn, "DYN-10.99.18.142")) == set(same)
        hw = dict(conn.execute("SELECT * FROM hardware WHERE asset_serial='HW-00010439'").fetchone())
        assert hw["hostname"] == "SECSVR018-145TT"                         # CIA 值不動
        st = measured_mismatch.hostname_fix_state(conn)
        hv = measured_mismatch.hostname_view(hw, st)
        assert hv["measured"] == "SECSVR018-142T" and hv["tier"] == "collect"
        got = [(r["field_key"], r["cia"], r["measured"], r["kind"]) for r in measured_mismatch.corrections(conn)]
        assert ("hostname", "SECSVR018-145TT", "SECSVR018-142T", "corrected") in got
        keys = {system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
                for r in conn.execute("SELECT hostname, ip, asset_serial FROM hardware")}
        assert len(keys) == 1
        # 可逆：關掉就回到兩台，資料沒動
        monkeypatch.setenv("WEBIT_HOSTNAME_FIX", "off")
        machine_identity.refresh(conn)
        keys = {system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
                for r in conn.execute("SELECT hostname, ip, asset_serial FROM hardware")}
        assert len(keys) == 2
        conn.close()


def test_實測主機名不同但CIA名字在別處活著_判IP填錯_不校正():
    """068T／168T：CIA 068T@.168；.168 上實測 168T；但 068T 在 .68 收集過 → 不校正主機名、標 IP 填錯。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _company(conn)
        conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = '2026-10-20 10:05:00' "
                     "WHERE asset_serial = 'DYN-10.99.194.168'")
        conn.commit()
        machine_identity.refresh(conn)
        hw = dict(conn.execute("SELECT * FROM hardware WHERE asset_serial='SECSVR194-068T'").fetchone())
        st = measured_mismatch.hostname_fix_state(conn)
        assert measured_mismatch.hostname_view(hw, st) is None
        ie = measured_mismatch.ip_error_view(hw, st)
        assert ie["elsewhere_ip"] == "10.99.194.68" and ie["measured_hostname"] == "SECSVR194-168T"
        assert "DYN-10.99.194.168" not in manage_state.same_machine_serials(conn, "SECSVR194-068T")
        assert all(r["field_key"] != "hostname" for r in measured_mismatch.corrections(conn))
        conn.close()


def test_RVTools版本差_不列():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-3", "10.99.7.3", "Windows Server 2019", "app-03", asset_status="使用中")
        conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('vcenter', 'u3', ?)",
                     (json.dumps({"vm_name": "app-03", "hostname": 3.0,
                                  "os": "Microsoft Windows Server 2016 or later (64-bit)", "ip": "10.99.7.3"}),))
        conn.commit()
        machine_identity.refresh(conn)
        assert measured_mismatch.rows(conn) == []      # 同平台、版本是設定檔類型：不列；數字格子不炸
        conn.close()


def test_RVTools主機名_只認DNS_Name_VM名不算():
    import hostname_fix
    assert hostname_fix.dns_name({"hostname": "APP-01", "vm_name": "APP-01"}) is None      # 分不出：退路的 VM 名
    assert hostname_fix.dns_name({"hostname": "app-01.corp", "vm_name": "APP-01 (old)"}) == "app-01.corp"
    assert hostname_fix.dns_name({"dns_name": None, "hostname": "x", "vm_name": "y"}) is None   # 新匯入：DNS 空
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-20", "10.99.20.1", "Windows Server 2019", "SECSVR020-001", asset_status="使用中")
        conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('vcenter', 'v20', ?)",
                     (json.dumps({"vm_name": "SECSVR020-001T", "hostname": "SECSVR020-001T", "ip": "10.99.20.1"}),))
        conn.commit()
        machine_identity.refresh(conn)
        assert measured_mismatch.hostname_fix_state(conn)["fixes"] == {}
        conn.close()


def test_主機名只差補0_要列給CIA改成真實名字():
    """公司 HW-00011545：CIA SECSVR161-22，機器實際 SECSVR161-022。v1.482.2 曾當同一個名字不列；
    使用者 2026-10-21 推翻：「主機名稱以真實主機名稱為主」「這就是要抓的重點」→ 校正、需 CIA 改、應改為真實短名。"""
    import hostname_fix
    assert not hostname_fix.same_name("secsvr161-22", "secsvr161-022")
    assert hostname_fix.same_name("secsvr161-022", "secsvr161-022")
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "HW-00011545", "10.99.161.22", "Red Hat Enterprise Linux 9.4", "SECSVR161-22",
            asset_status="使用中")
        _hw(conn, "DYN-10.99.161.22", "10.99.161.22", "Red Hat Enterprise Linux 9.4", "SECSVR161-022",
            collect_ok=1)
        conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('vcenter', 'v161', ?)",
                     (json.dumps({"vm_name": "SECSVR161-22", "hostname": "SECSVR161-022.corp.example",
                                  "dns_name": "SECSVR161-022.corp.example", "ip": "10.99.161.22"}),))
        conn.commit()
        machine_identity.refresh(conn)
        st = measured_mismatch.hostname_fix_state(conn)
        assert st["ip_errors"] == []
        assert st["fixes"]["HW-00011545"]["measured"] == "SECSVR161-022"
        got = [r for r in measured_mismatch.corrections(conn) if r["field_key"] == "hostname"]
        assert [(r["asset_serial"], r["cia"], r["measured"], r["needs"], r["action"]) for r in got] == [
            ("HW-00011545", "SECSVR161-22", "SECSVR161-022", "CIA", "修改主機名稱")]
        rep = [r for r in measured_mismatch.cia_report(conn)["fix"] if r[2] == "主機名"]
        assert [(r[0], r[3], r[4]) for r in rep] == [("HW-00011545", "SECSVR161-22", "SECSVR161-022")]
        conn.close()


def test_主機名只差大小寫或網域_不列():
    """大小寫、網域不算不符（使用者「我們預設不會加 domain」）；命名規範不拿來校正（名字不合規範也不列）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "HW-1", "10.99.30.5", "Red Hat Enterprise Linux 9.4", "SECSVR030-005", asset_status="使用中")
        _hw(conn, "DYN-10.99.30.5", "10.99.30.5", "Red Hat Enterprise Linux 9.4",
            "secsvr030-005.corp.example", collect_ok=1)
        _hw(conn, "HW-2", "10.99.31.7", "Red Hat Enterprise Linux 9.4", "appdb-7", asset_status="使用中")
        _hw(conn, "DYN-10.99.31.7", "10.99.31.7", "Red Hat Enterprise Linux 9.4", "APPDB-7", collect_ok=1)
        machine_identity.refresh(conn)
        assert measured_mismatch.hostname_fix_state(conn)["fixes"] == {}
        assert all(r["field_key"] != "hostname" for r in measured_mismatch.rows(conn))
        conn.close()


def _spec(conn, serial, cores, mem_mb, model=None):
    conn.execute("INSERT INTO host_spec (asset_serial, cores, mem_mb, model, collected_at) VALUES (?,?,?,?,?)",
                 (serial, str(cores), mem_mb, model, "2026-10-20 21:00:00"))


def _rv_spec(conn, vm, cpus, mem_mib):
    conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('vcenter_extra:vCPU', ?, ?)",
                 (vm + "#c", json.dumps({"VM": vm, "CPUs": cpus})))
    conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('vcenter_extra:vMemory', ?, ?)",
                 (vm + "#m", json.dumps({"VM": vm, "Size MiB": mem_mib})))


def test_硬體_實測RAM8G_RVTools16G_實測贏_容差內不列():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-9", "10.99.9.9", "Red Hat Enterprise Linux 9.4", "app-09", collect_ok=1,
            asset_status="使用中", is_vm=1)
        _spec(conn, "S-9", 4, 7649)               # OS 看到的 MemTotal（8G 配置）
        _rv_spec(conn, "APP-09", 8, 16384)        # vCenter 那邊還是改大小之前
        _hw(conn, "S-10", "10.99.9.10", "Red Hat Enterprise Linux 9.4", "app-10", collect_ok=1,
            asset_status="使用中", is_vm=1)
        _spec(conn, "S-10", 4, 7649)
        _rv_spec(conn, "app-10", 4, 8192)         # 7649 vs 8192：容差內，不列
        conn.commit()
        machine_identity.refresh(conn)
        got = {(r["asset_serial"], r["field_key"]): r for r in measured_mismatch.corrections(conn)}
        assert set(got) == {("S-9", "cpu"), ("S-9", "mem")}
        assert got[("S-9", "mem")]["cia"] == "16.0 GB" and got[("S-9", "mem")]["measured"] == "7.5 GB"
        assert got[("S-9", "mem")]["against"] == "RVTools"
        # CIA 回報表不收 RVTools 舊值（那是 vCenter 要更新，不是 CIA）
        assert measured_mismatch.cia_report(conn)["fix"] == []
        conn.close()


def test_CIA回報表_每資產每欄一列_有建議動作():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _company(conn)
        rep = measured_mismatch.cia_report(conn)
        acts = sorted((r[0], r[2], r[6]) for r in rep["fix"])
        assert acts == [("SECSVR194-068T", "IP", "改 IP"), ("SECSVR194-068T", "作業系統", "改作業系統")]
        assert all(len(r) == len(measured_mismatch.REPORT_HEADERS) - 1 for r in rep["fix"])
        conn.close()


# ---- 3-15 欄位對齊＋要修改什麼（v1.482.3，使用者 2026-10-21「需誰處理怎是時間，再加一欄，要修改什麼」）----

def test_匯出_表頭與每格同一個順序_需誰處理與要修改什麼對得上():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _company(conn)
        items = measured_mismatch.rows(conn)
        table = measured_mismatch.as_table(items)
        H = measured_mismatch.HEADERS
        assert table and all(len(t) == len(H) for t in table)
        for r, t in zip(items, table):
            assert t[H.index("需誰處理")] == r["needs"]
            assert t[H.index("要修改什麼")] == r["action"]
            assert t[H.index("最後確認")] == (r.get("at") or "")
        conn.close()


def test_要修改什麼_白話動作():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _company(conn)
        got = {r["field_key"]: (r["action"], r["needs"]) for r in measured_mismatch.corrections(conn)}
        assert got == {"os": ("修改作業系統", "CIA"), "ip": ("修改 IP", "CIA")}
        # CIA 重匯改好 → 歷程那筆講「不用改」
        measured_mismatch.sync_log(conn, now="2026-10-20 11:00:00")
        conn.execute("UPDATE hardware SET os='RedHat 9.4', os_cia='RedHat 9.4' WHERE asset_serial='SECSVR194-068T'")
        conn.commit()
        measured_mismatch.sync_log(conn, now="2026-10-21 09:00:00")
        assert [h["action"] for h in measured_mismatch.history(conn)] == ["不用改（CIA 已改好）"]
        conn.close()
    with tempfile.TemporaryDirectory() as tmp:     # 主機名打錯字（HW-00010439 那型）
        conn = _conn(tmp)
        _hw(conn, "HW-00010439", "10.99.18.142", "Windows Server 2019", "SECSVR018-145TT",
            environment="正式", asset_status="使用中", asset_purpose="AP")
        _hw(conn, "DYN-10.99.18.142", "10.99.18.142", "Microsoft Windows Server 2019 Standard",
            "SECSVR018-142T", collect_ok=1)
        machine_identity.refresh(conn)
        r = next(x for x in measured_mismatch.corrections(conn) if x["field_key"] == "hostname")
        assert (r["action"], r["needs"]) == ("修改主機名稱", "CIA")
        conn.close()
    a = measured_mismatch.action
    assert a({"field_key": "mem", "field": "記憶體", "kind": "corrected", "against": "RVTools", "needs": ""}) \
        == "不用改 CIA（vCenter 舊值）"
    assert a({"field_key": "os", "field": "作業系統", "kind": "suspect", "needs": ""}) == "待確認作業系統"
    assert a({"field_key": "ip", "field": "IP", "kind": "suspect", "needs": "CIA"}) == "修改 IP"   # CIA IP 填錯
    assert a({"field_key": "mac", "field": "MAC", "kind": "corrected", "needs": "CIA"}) == "修改 MAC"
    assert a({"field_key": "_error", "field": "硬體比對失敗", "kind": "suspect"}) == "不知道"
    assert a({"field_key": "", "field": "", "kind": "suspect"}) == "不知道"
    assert a({"field_key": "gone", "field": "", "kind": "gone"}) == "不知道"     # 認不得的欄位：不知道，不留白
