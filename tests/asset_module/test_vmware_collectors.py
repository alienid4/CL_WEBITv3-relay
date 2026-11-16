"""天條：排程／批次收集與收集狀態刷新不碰 VMware（2026-10-27 終審 F1／F2／F3）。

天條前就納管的 ESXi 仍是 collect_ok=1；同 IP 也可能有一筆 os 空白的 AUTO 登記。下面每一條路徑都用假連線
記下「對哪個 IP 發了指令」，VMware 的 IP 一次都不可以出現。
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state  # noqa: E402

ESX_IP, STUB_IP, LNX_IP = "10.99.5.11", "10.99.5.12", "10.99.5.21"


@pytest.fixture()
def env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        rows = [("E-1", "esx51", ESX_IP, "VMware ESXi 7.0", 1),            # 天條前已納管的 ESXi
                ("C-2", "esx52", STUB_IP, "VMware ESXi 8.0", 0),            # CIA 寫 ESXi
                (f"AUTO-{STUB_IP}", None, STUB_IP, None, 1),                # 同 IP、os 空白的 AUTO（逐列判看不出來）
                ("L-1", "lnx51", LNX_IP, "Red Hat Enterprise Linux 9", 1)]
        for s, h, ip, os_, ok in rows:
            c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, collect_ok) VALUES (?,?,?,?,?)", (s, h, ip, os_, ok))
        c.commit()
        sent: list[str] = []

        def fake_runner_for(ip, *a, **k):
            def run(host, cmd):
                sent.append(host)
                return ""
            return run
        monkeypatch.setattr(manage_state, "_runner_for", fake_runner_for)
        monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "linux")
        monkeypatch.setattr(manage_state, "collect_platform_for", lambda *a, **k: "linux")
        monkeypatch.setattr(manage_state, "_save_trace", lambda *a, **k: None)
        try:
            yield c, sent
        finally:
            c.close()


def _no_vmware(sent):
    assert ESX_IP not in sent and STUB_IP not in sent, f"對 VMware 發了指令：{sorted(set(sent))}"


def test_F1_收集狀態刷新不試連VMware_也不標回已納管(env):
    conn, sent = env
    probed = []
    r = manage_state.refresh_collect_status(conn, runner=lambda ip: (probed.append(ip), (True, None))[1])
    _no_vmware(probed)
    assert LNX_IP in probed and r["skipped_vmware"] == 3          # E-1、C-2、AUTO 那筆
    ok = {row["asset_serial"]: (row["collect_ok"], row["collect_error"]) for row in conn.execute(
        "SELECT asset_serial, collect_ok, collect_error FROM hardware")}
    assert ok["E-1"][0] == 0 and "VMware" in ok["E-1"][1] and "待移除" in ok["E-1"][1]
    assert ok["C-2"] == (0, None)                                   # 從沒納管過的 ESXi 不動
    assert ok[f"AUTO-{STUB_IP}"][0] == 0
    assert ok["L-1"][0] == 1
    # 只刷新一台（納管後）也一樣
    probed.clear()
    manage_state.refresh_collect_status(conn, runner=lambda ip: (probed.append(ip), (True, None))[1], only_ip=ESX_IP)
    assert probed == []


def test_F2_Windows不送sh守門句():
    import facts_collector
    import onboard_engine

    sent = []
    facts_collector.collect(lambda h, c: sent.append(c) or "", "10.99.5.31", "windows")
    assert not any(onboard_engine.VMWARE_REFUSED_MARKER in c for c in sent)
    sent.clear()
    facts_collector.collect(lambda h, c: sent.append(c) or "", "10.99.5.32", "linux")
    assert onboard_engine.VMWARE_REFUSED_MARKER in sent[0]


def test_F3_硬體規格收集濾掉VMware(env):
    import host_spec_collector

    conn, sent = env
    r = host_spec_collector.run_collection(conn)
    _no_vmware(sent)
    assert LNX_IP in sent and r["unsupported"] >= 2


def test_F3_OS重建收集_同IP另一筆是VMware也不收(env):
    import os_rebuild_collector

    conn, sent = env
    r = os_rebuild_collector.run_collection(conn)
    _no_vmware(sent)
    unsupported = {u["asset_serial"] for u in r.get("unsupported_detail") or r.get("unsupported") or []} \
        if isinstance(r.get("unsupported"), list) else None
    if unsupported is not None:
        assert f"AUTO-{STUB_IP}" in unsupported


def test_F3_Windows排程_同IP另一筆是VMware列為不支援(env):
    import schedule_inventory

    conn, _sent = env
    r = schedule_inventory.collect_windows(conn)
    un = {u["asset_serial"]: u["reason"] for u in r["unsupported_detail"]}
    assert "VMware" in un["E-1"] and "VMware" in un[f"AUTO-{STUB_IP}"], un      # 是因為 VMware 擋下，不是因為「不是 Windows」


def test_F3_收集帳號換新探測不試連VMware(env, monkeypatch):
    import collect_account_migration as cam
    from routers import accounts

    conn, _sent = env
    probed = []
    monkeypatch.setattr(cam, "probe_one", lambda c, serial, ip, plat: probed.append(ip) or {"asset_serial": serial})
    accounts.collect_account_migration_probe(None, 200, None, conn)
    assert ESX_IP not in probed and STUB_IP not in probed and LNX_IP in probed


def test_R1_大量交錯目標_判定與逐台單獨判完全一樣(monkeypatch):
    """審查 R1：判定快取用 id() 當鍵，物件回收後 id 重用→拿到別的目標的判定（漏擋 ESXi、誤擋一般主機）。
    300 個目標：vCenter 名單上的 ESXi（資產庫沒登記）與一般未登記 IP 交錯，一次判完＝逐台各自判。"""
    import json
    import random
    import socket

    import onboard_eligibility as oe

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror()))
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        try:
            for i in range(150):
                c.execute("INSERT INTO source_record (source, payload) VALUES ('vcenter', ?)",
                          (json.dumps({"esxi_host": f"10.99.3.{i}"}),))
            c.commit()
            targets = [f"10.99.3.{i}" for i in range(150)] + [f"10.99.4.{i}" for i in range(150)]
            random.Random(7).shuffle(targets)
            batch = oe.vmware_targets(c, targets)
            ref = {t for t in targets if oe.vmware_targets(c, [t])}
            assert set(batch) == ref == {f"10.99.3.{i}" for i in range(150)}
        finally:
            c.close()


def test_刷新後_天條前納管的ESXi仍在待移除清單(env):
    """審查 2.006.25：刷新把 VMware 列寫成 collect_ok=0 之後，onboarded_esxi 與主機履歷「待移除」旗標不可以消失。"""
    import onboard_eligibility as oe
    import v2_views

    conn, _sent = env
    before = {x["asset_serial"] for x in oe.onboarded_esxi(conn)}
    assert "E-1" in before
    manage_state.refresh_collect_status(conn, runner=lambda ip: (True, None))
    assert conn.execute("SELECT collect_ok FROM hardware WHERE asset_serial='E-1'").fetchone()[0] == 0
    after = {x["asset_serial"] for x in oe.onboarded_esxi(conn)}
    assert "E-1" in after, "刷新後天條前納管的 ESXi 從待移除清單消失了"
    assert v2_views.machine(conn, "esx51")["vmware_legacy"] is True
    assert "C-2" not in after, "從沒納管過的 ESXi 被當成待移除"


def _audit_db(tmp, audits, created="2026-10-01 00:00:00"):
    p = Path(tmp) / "a.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, collect_ok, created_at) VALUES "
              "('E-9','esx99','10.99.5.99','VMware ESXi 7.0',0,?)", (created,))
    for ok, trig, at in audits:
        c.execute("INSERT INTO onboard_audit (target_ip, ok, trigger, created_at) VALUES ('10.99.5.99',?,?,?)", (ok, trig, at))
    c.commit()
    return c


@pytest.mark.parametrize("audits,want", [
    ([(1, "manual", "2026-10-05 00:00:00")], True),                                            # 登記後成功納管 → 待移除
    ([(1, "manual", "2026-09-01 00:00:00")], False),                                           # IP 重用前別台的成功紀錄
    ([(1, "manual", "2026-10-05 00:00:00"), (1, "revoke", "2026-10-06 00:00:00")], False),     # 最新一筆是撤銷＝已清掉
])
def test_納管紀錄證據_只看最新一筆_撤銷與IP重用不算(audits, want):
    import onboard_eligibility as oe

    with tempfile.TemporaryDirectory() as tmp:
        c = _audit_db(tmp, audits)
        try:
            assert ("E-9" in {x["asset_serial"] for x in oe.onboarded_esxi(c)}) is want
        finally:
            c.close()
