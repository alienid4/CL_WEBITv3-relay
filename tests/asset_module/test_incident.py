"""事件應變 v1：告警抽取、共同點排名、範圍展開（含 ESXi 上的 VM、單電源）、回報文字、結案唯讀。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import incident as inc  # noqa: E402
import model_specs  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    def hw(serial, host, ip, rack, model=None, api=None, user=None, vm=False):
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房" if rack else None,
                           rack_no=rack, device_model=model, api_id=api, user_name=user, is_vm=1 if vm else 0)
    hw("P-1", "esx01", "10.99.1.1", "M10/30-31U", "Mystery Box 9", "I-900")
    hw("P-2", "esx02", "10.99.1.2", "M11/30-31U", "Mystery Box 9", "I-900")
    hw("P-3", "db01", "10.99.1.3", "M12/10U", "OneSupply X1", "N-1", "王小明")
    hw("P-4", "far01", "10.99.9.4", "B2/10U", "DELL R750", "N-2", "李小華")
    hw("V-1", "APPSVR01", "10.99.2.11", "(VM)", "(VM)", "N-1", "王小明", vm=True)
    hw("V-2", "APPSVR02", "10.99.2.12", "(VM)", "(VM)", "N-3", "陳大文", vm=True)
    hw("V-3", "APPSVR03", "10.99.2.13", "(VM)", "(VM)", "N-3", "陳大文", vm=True)
    db.insert_hardware(conn, asset_serial="V-9", hostname="LOOSEVM", ip="10.99.2.19", physical_location="01_甲機房",
                       rack_no="(VM)", device_model="(VM)", api_id="N-3")   # CIA 寫 VM、vCenter 沒對到
    for nid, serial in (("esxi:E1", "P-1"), ("esxi:E2", "P-2")):
        conn.execute("INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
                     "VALUES (?,?,?,?,'t','t','t')", (nid, "esxi", nid[5:].lower(), serial))
    conn.execute("INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) VALUES ('cluster:C1','cluster','CL-1','t','t','t')")
    for vm, esx in (("V-1", "esxi:E1"), ("V-2", "esxi:E1"), ("V-3", "esxi:E2")):
        conn.execute("INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
                     "VALUES (?, 'host', ?, ?, 't','t','t')", (f"hw:{vm}", vm, vm))
        conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                     "VALUES (?,?, 'runs_on','證據','t','t')", (f"hw:{vm}", esx))
    for esx in ("esxi:E1", "esxi:E2"):
        conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                     "VALUES (?, 'cluster:C1', 'member_of','證據','t','t')", (esx,))
    model_specs.update(conn, "OneSupply X1", {"psu_count": 1, "rack_u": 1}, None, "t")
    conn.execute("INSERT INTO business_system (api_id, name, sys_class) VALUES ('N-1','核心系統甲','S1-第一類系統軟體（核心）')")
    conn.execute("INSERT INTO system_oncall (api_id, phone, updated_at) VALUES ('N-1','0912-000-000','t')")
    conn.commit()


def test_抽取_IP與主機名_告警雜字不算():
    t = "16:25 Signaal DOWN 10.99.2.11 / host APPSVR02 ping fail；3FM4 UPS 異常 10.99.2.11"
    ex = inc.extract(t)
    assert ex["ips"] == ["10.99.2.11"]
    assert "APPSVR02" in ex["hosts"] and "UPS" not in ex["hosts"]


def test_共同點排名_同ESXi排最前_一鍵變範圍(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        w = inc.World(conn)
        r = inc.resolve(w, "10.99.2.11\nAPPSVR02\n10.99.2.13\n10.99.7.7")
        assert {m["asset_serial"] for m in r["machines"]} == {"V-1", "V-2", "V-3"}
        assert [t["status"] for t in r["tokens"] if t["token"] == "10.99.7.7"] == ["none"]   # 對不到照實列
        com = inc.correlate(w, r["machines"])
        top = com[0]
        assert top["count"] == 3 and top["text"] == "3 台中 3 台在 甲機房 M 排"   # 3 台的 ESXi 都在 M 排（比叢集具體）
        esxi = next(c for c in com if c["dim"] == "esxi")
        assert esxi["count"] == 2 and esxi["text"] == "3 台中 2 台跑在 ESXi e1"
        assert esxi["scope"] == {"esxi": ["esxi:E1"]}
        # 同樣 3 台時，叢集（較具體）排在機房前面
        dims3 = [c["dim"] for c in com if c["count"] == 3]
        assert dims3.index("cluster") < dims3.index("room")
    finally:
        conn.close()


def test_範圍展開_ESXi帶出VM_單電源_系統與值班電話(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ev = inc.create_event(conn, "電力", "UPS 異常", None, None, "tester", mode="formal")
        exp = inc.set_scope(conn, ev["id"], {"racks": [{"room": "甲機房", "rack": "M10"}, {"room": "甲機房", "rack": "M12"}]}, "tester")
        names = {m["name"] for m in exp["machines"]}
        assert names == {"esx01", "db01", "APPSVR01", "APPSVR02"}           # M10 的 ESXi 帶出它上面的 2 台 VM
        exp2 = inc.expand(inc.World(conn), {"rooms": ["甲機房"], "machines": []})
        assert "LOOSEVM" not in {m["name"] for m in exp2["machines"]}      # 機房範圍不會把登記在機房的 VM 全拉進來
        w = inc.World(conn)
        assert inc._brief(w, next(m for m in w.ms if m["name"] == "LOOSEVM"))["psu"] == "vm"
        db1 = next(m for m in exp["machines"] if m["name"] == "db01")
        assert db1["psu"] == "single" and exp["counts"]["single_psu"] == 1
        assert next(m for m in exp["machines"] if m["name"] == "esx01")["psu"] == "unknown"   # 規格沒有＝不明
        assert exp["machines"][0]["sys_level"] == "core"                  # 核心排最前
        sysm = {s["api_id"]: s for s in exp["systems"]}
        assert sysm["N-1"]["oncall_phone"] == "0912-000-000" and sysm["N-3"]["oncall_phone"] is None
        assert exp["counts"]["no_phone"] == len(exp["systems"]) - 1
        tasks = inc.list_tasks(conn, ev["id"])
        assert {t["ref"] for t in tasks if t["level"] == "system"} == {"N-1", "N-3", "I-900"}
    finally:
        conn.close()


def test_測一輪_回報文字_結案唯讀(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ev = inc.create_event(conn, "電力", "UPS 異常", "2026-10-23 16:22", None, "tester", mode="formal")
        inc.set_scope(conn, ev["id"], {"racks": [{"room": "甲機房", "rack": "M12"}]}, "tester")
        run = inc.run_check(conn, ev["id"], "tester", runner=lambda cmd, t: (1, "0 received"), prober=lambda ip: None)
        assert run["down"] == 1 and run["items"][0]["result_label"] == "不通"
        t = inc.list_tasks(conn, ev["id"])[0]
        inc.update_task(conn, t["id"], "tester", team="DBA", status="異常", note="DB 起不來")
        view = inc.event_view(conn, ev["id"])
        assert view["first"][0]["name"] == "db01" and view["first"][0]["why"] == ["核心系統", "不通", "單電源"]
        txt = inc.report_text(view, datetime(2026, 10, 23, 17, 40))
        lines = txt.split("\n")
        assert lines[0] == "【電力事件】UPS 異常" and "開始 10-23 16:22（已 1h18m）" in lines
        assert "異常 1" in lines and any(l.startswith("・") and "N-1" in l for l in lines)
        assert "下次回報 18:10" in lines and max(len(l) for l in lines) <= 30   # 手機群組貼得下
        inc.close_event(conn, ev["id"], "全部恢復", "tester")
        with pytest.raises(PermissionError):
            inc.update_task(conn, t["id"], "tester", status="OK")
        kinds = [x["kind"] for x in inc.timeline(conn, ev["id"])]
        assert kinds[:3] == ["open", "scope", "check"] and "task" in kinds and kinds[-1] == "close"
        assert "全部恢復" in inc.export_csv(conn, ev["id"])
    finally:
        conn.close()


def test_聯絡_只列受影響那台自己的AP與SP_亂碼標出來(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        db.insert_hardware(conn, asset_serial="P-5", hostname="db02", ip="10.99.1.5", physical_location="01_甲機房",
                           rack_no="M12/12U", api_id="N-1", user_name="別台的人")         # 同系統、不在範圍
        conn.execute("UPDATE hardware SET custodian = '張保管+V4:ab' WHERE asset_serial = 'P-3'")
        conn.commit()
        exp = inc.expand(inc.World(conn), {"racks": [{"room": "甲機房", "rack": "M12"}], "machines": []})
        db1 = next(m for m in exp["machines"] if m["name"] == "db01")
        assert db1["ap"] == ["王小明"] and db1["sp"] == ["張保管+V4:ab"]
        assert db1["people_garbled"] == ["張保管+V4:ab"]
        assert "別台的人" not in db1["ap"]
        assert inc.is_garbled_name("王小明") is False and inc.is_garbled_name("AB:CD") is True
    finally:
        conn.close()


def test_推論樹_同一台ESXi掉多台_疑似單台主機(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        w = inc.World(conn)
        r = inc.resolve(w, "10.99.2.11 10.99.2.12")
        t = inc.reason_tree(w, r["machines"])
        assert t["hosts"][0]["name"] == "esx01" and t["hosts"][0]["count"] == 2
        assert t["verdicts"][0]["kind"] == "single_host" and "esx01" in t["verdicts"][0]["text"]
        assert t["racks"][0]["rack"] == "M10" and t["rows"][0]["row"] == "M" and t["rooms"][0]["count"] == 2
    finally:
        conn.close()


def test_推論樹_同排兩台實體_單電源多_雙電源沒告警_疑似單一迴路(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        model_specs.update(conn, "TwoFeed Z2", {"psu_count": 2}, None, "t")
        db.insert_hardware(conn, asset_serial="P-6", hostname="db03", ip="10.99.1.6", physical_location="01_甲機房",
                           rack_no="M13/10U", device_model="OneSupply X1", api_id="N-1")
        db.insert_hardware(conn, asset_serial="P-7", hostname="app07", ip="10.99.1.7", physical_location="01_甲機房",
                           rack_no="M13/20U", device_model="TwoFeed Z2", api_id="N-2")
        w = inc.World(conn)
        r = inc.resolve(w, "10.99.1.3 10.99.1.6")      # db01（M12）、db03（M13），都是單電源
        t = inc.reason_tree(w, r["machines"])
        kinds = [v["kind"] for v in t["verdicts"]]
        assert kinds[0] == "same_row" and "one_feed" in kinds
        chk = next(v for v in t["verdicts"] if v["kind"] == "check_feed")
        assert "app07" in chk["text"]
    finally:
        conn.close()


def test_推論樹_ESXi沒有CIA登記_仍算一台主機_不說成對不到(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        conn.execute("UPDATE ci_node SET asset_serial = NULL WHERE node_id = 'esxi:E1'")   # E1 在 vCenter 有、CIA 沒有
        conn.commit()
        w = inc.World(conn)
        r = inc.resolve(w, "10.99.2.11 10.99.2.12 10.99.2.13")
        t = inc.reason_tree(w, r["machines"])
        assert t["unplaced"] == []
        top = t["hosts"][0]
        assert top["key"] == "esxi:E1" and top["count"] == 2 and top["no_cia"] is True
        assert t["verdicts"][0]["kind"] == "single_host" and "沒有 CIA 登記" in t["verdicts"][0]["text"]
    finally:
        conn.close()
