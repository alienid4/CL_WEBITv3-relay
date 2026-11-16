"""系統地圖（/api/blast/system-map）：選一個 APID，看它跑在哪些主機、掛哪台 ESXi、上下游。

釘住的事：
- 台數走 machine_key（同名同 IP 的多筆登記＝一台），退役不算台數
- 同一台的另一筆登記（RVTools 建的 VC- 那筆）掛著 runs_on，ESXi 也要找得到
- system_deps 整張是空的 → deps_available=False（尚無資料），不能講成「沒有上下游」
- 有登記上下游時方向正確（src 依賴 dst）
- 查無此系統 → ValueError（API 回 404，不是 500）
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import blast_radius as br  # noqa: E402
import ci_graph  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    # 系統 N-901：app01 兩筆登記（同名同 IP＝一台）、db01 一台實體、old01 退役
    db.insert_hardware(conn, asset_serial="HW-1", hostname="app01", ip="10.99.1.11",
                       environment="正式", api_id="N-901", asset_name="測試交易系統", availability=3)
    db.insert_hardware(conn, asset_serial="HW-2", hostname="app01", ip="10.99.1.11",
                       environment="正式", api_id="N-901", asset_name="測試交易系統")
    db.insert_hardware(conn, asset_serial="HW-3", hostname="db01", ip="10.99.1.12",
                       environment="正式", api_id="N-901", asset_name="測試交易系統")
    db.insert_hardware(conn, asset_serial="HW-4", hostname="old01", ip="10.99.1.13",
                       environment="正式", api_id="N-901", asset_name="測試交易系統")
    conn.execute("UPDATE hardware SET asset_status='報廢' WHERE asset_serial='HW-4'")
    # app01 的 RVTools 那筆（沒有 api_id），vCenter 指到 esxi-a / cluster-a / ds-a
    vm_id = db.insert_hardware(conn, asset_serial="VC-app01", hostname="app01", ip="10.99.1.11")
    conn.execute(
        "INSERT INTO source_record (source, source_key, payload, resolved_status, "
        "resolved_hardware_id, resolved_rule, resolved_confidence, collected_at) "
        "VALUES ('vcenter', 'vm-app01', ?, 'matched', ?, 'vm_uuid', 1.0, '2026-10-01 09:00:00')",
        (json.dumps({"esxi_host": "esxi-a.example.local", "cluster": "CL-A", "datastore": "DS-A"}),
         vm_id),
    )
    # 另一個系統，給上下游用
    db.insert_hardware(conn, asset_serial="HW-9", hostname="gw01", ip="10.99.2.1",
                       api_id="N-902", asset_name="測試閘道系統")
    conn.commit()
    ci_graph.rebuild(conn)


def test_台數走machine_key_退役不算(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        m = br.system_map(conn, "N-901")
        assert m["name"] == "測試交易系統"
        assert m["severity"] == "重大"
        names = sorted(h["hostname"] for h in m["hosts"])
        assert names == ["app01", "db01"]
        assert m["summary"]["hosts"] == 2
        assert m["summary"]["records"] == 3
        assert m["summary"]["retired_records"] == 1
        assert [r["asset_serial"] for r in m["retired"]] == ["HW-4"]   # 卡片＝下鑽
        app = next(h for h in m["hosts"] if h["hostname"] == "app01")
        assert app["record_count"] == 2
        assert app["ips"] == ["10.99.1.11"]
    finally:
        conn.close()


def test_同一台的RVTools登記掛著ESXi也要找得到(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        m = br.system_map(conn, "bizsys:N-901")  # 接受 node_id 寫法
        app = next(h for h in m["hosts"] if h["hostname"] == "app01")
        assert "VC-app01" in app["serials"]
        assert app["on_esxi"] is True
        assert app["esxi"] == ["esxi:ESXI-A"]
        assert app["datastores"] == ["ds:DS-A"]
        assert m["summary"]["on_esxi"] == 1
        assert m["summary"]["no_esxi"] == 1
        assert m["summary"]["esxi"] == 1
        assert m["esxi"][0]["clusters"] == ["cluster:CL-A"]
        assert m["clusters"][0]["esxi"] == ["esxi:ESXI-A"]
        assert m["datastores"][0]["host_count"] == 1
        # 卡片＝下鑽：on_esxi + no_esxi = hosts；ESXi 掛的台數加總不超過有 ESXi 的台數
        s = m["summary"]
        assert s["on_esxi"] + s["no_esxi"] == s["hosts"] == len(m["hosts"])
    finally:
        conn.close()


def test_上下游表是空的要標尚無資料不是沒有(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        m = br.system_map(conn, "N-901")
        assert m["deps_available"] is False
        assert m["depends_on"] == [] and m["depended_by"] == []
    finally:
        conn.close()


def test_有登記上下游時方向正確(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        # src 依賴 dst：N-901 依賴 N-902；N-903 依賴 N-901（system_deps 有 FK 到 systems）
        for sid, label in (("N-901", "交易"), ("N-902", "閘道"), ("N-903", "外部報送系統")):
            conn.execute("INSERT INTO systems (id, label, health) VALUES (?,?,'ok')", (sid, label))
        conn.execute("INSERT INTO system_deps (source, target, dep_type) VALUES ('N-901','N-902','API')")
        conn.execute("INSERT INTO system_deps (source, target, dep_type) VALUES ('N-903','N-901','檔案')")
        conn.commit()
        m = br.system_map(conn, "N-901")
        assert m["deps_available"] is True
        assert [(d["api_id"], d["name"], d["has_hosts"]) for d in m["depends_on"]] == [
            ("N-902", "測試閘道系統", True)]
        # 沒有主機、對照表也沒有 → 名稱退回 systems.label
        assert [(d["api_id"], d["name"], d["has_hosts"]) for d in m["depended_by"]] == [
            ("N-903", "外部報送系統", False)]
        assert m["summary"]["depends_on"] == 1 and m["summary"]["depended_by"] == 1
    finally:
        conn.close()


def test_查無此系統拋ValueError(tmp_path):
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError):
            br.system_map(conn, "N-000")
        with pytest.raises(ValueError):
            br.system_map(conn, "  ")
    finally:
        conn.close()


def _seed_rooms(conn):
    """N-950：甲機房 正式3（2 台 VM 在 CL-A、1 台實體）＋備援1；乙機房 正式1＋UAT1；分公司兩間各 1 台正式。"""
    rows = [
        ("R-1", "p1", "10.99.5.1", "01_甲機房", "正式"),
        ("R-2", "p2", "10.99.5.2", "01_甲機房", "正式"),
        ("R-3", "p3", "10.99.5.3", "01_甲機房", "正式"),
        ("R-4", "d1", "10.99.5.4", "01_甲機房", "備援"),
        ("R-5", "q1", "10.99.6.1", "02_乙機房", "正式"),
        ("R-6", "u1", "10.99.6.2", "02_乙機房", "使用者測試(UAT)"),
        ("R-7", "b1", "10.99.7.1", "分公司-東區", "正式"),
        ("R-8", "b2", "10.99.7.2", "分公司-西區", "正式"),
    ]
    ids = {}
    for serial, host, ip, loc, env in rows:
        ids[serial] = db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, environment=env,
                                         physical_location=loc, api_id="N-950", asset_name="測試機房系統")
    for serial in ("R-1", "R-2"):
        conn.execute(
            "INSERT INTO source_record (source, source_key, payload, resolved_status, "
            "resolved_hardware_id, resolved_rule, resolved_confidence, collected_at) "
            "VALUES ('vcenter', ?, ?, 'matched', ?, 'vm_uuid', 1.0, '2026-10-01 09:00:00')",
            (f"vm-{serial}", json.dumps({"esxi_host": "esxi-r1", "cluster": "CL-A"}), ids[serial]),
        )
    # 同叢集：一台完全沒登記 APID 的 VM（orph1），以及 p1 的 RVTools 那筆（同一台，不可算進未登記）
    for serial, host, ip in (("VC-orph1", "orph1", "10.99.5.99"), ("VC-p1", "p1", "10.99.5.1")):
        vid = db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip)
        conn.execute(
            "INSERT INTO source_record (source, source_key, payload, resolved_status, "
            "resolved_hardware_id, resolved_rule, resolved_confidence, collected_at) "
            "VALUES ('vcenter', ?, ?, 'matched', ?, 'vm_uuid', 1.0, '2026-10-01 09:00:00')",
            (f"vm-{serial}", json.dumps({"esxi_host": "esxi-r1", "cluster": "CL-A"}), vid),
        )
    conn.commit()
    ci_graph.rebuild(conn)


def test_機房分組_分公司併一格_列加總等於台數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_rooms(conn)
        m = br.system_map(conn, "N-950")
        rooms = {r["room"]: r for r in m["rooms"]}
        assert list(rooms) == ["甲機房", "乙機房", "分公司"]       # 台數多的在前
        assert sum(r["host_count"] for r in m["rooms"]) == m["summary"]["hosts"] == 8
        a = rooms["甲機房"]
        assert a["env"] == {"正式": 3, "備援": 1, "測試": 0, "其他": 0}
        assert [(g["kind"], g["label"], g["host_count"]) for g in a["groups"]] == [
            ("cluster", "CL-A", 2), ("physical", "實體／未對應", 2)]
        assert a["groups"][0]["esxi"] == ["esxi:ESXI-R1"]
        assert rooms["乙機房"]["env"]["測試"] == 1                  # UAT 算測試
        for r in m["rooms"]:
            assert sum(r["env"].values()) == r["host_count"]
            assert sum(g["host_count"] for g in r["groups"]) == r["host_count"]
    finally:
        conn.close()


def test_機房停機影響_判讀規則與下鑽(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_rooms(conn)
        m = br.system_map(conn, "N-950")
        o = {r["room"]: r for r in m["outage"]}
        a = o["甲機房"]
        assert (a["here_prod"], a["here_dr"], a["else_prod"], a["else_dr"]) == (3, 1, 3, 0)
        assert a["verdict"] == "備援不足（0/3）"
        b = o["乙機房"]
        assert (b["here_prod"], b["here_dr"], b["else_prod"], b["else_dr"]) == (1, 0, 5, 1)
        assert b["verdict"] == "備援足夠"
        for r in m["outage"]:
            assert r["here_prod"] + r["else_prod"] == 6       # 這裡＋別處＝全系統正式
            assert r["here_dr"] + r["else_dr"] == 1
            assert all(len(r["keys"][k]) == r[k] for k in ("here_prod", "here_dr", "else_prod", "else_dr"))
    finally:
        conn.close()


def test_判讀規則邊界():
    assert br.room_verdict(0, 0, 0) == ("low", "影響小")
    assert br.room_verdict(5, 0, 0) == ("down", "整套停擺")
    assert br.room_verdict(5, 3, 2) == ("short", "備援不足（2/5）")
    assert br.room_verdict(5, 0, 5) == ("ok", "備援足夠")


def test_同叢集未登記VM_不算進本系統台數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_rooms(conn)
        m = br.system_map(conn, "N-950")
        assert m["summary"]["hosts"] == 8                       # orph1 不算
        cl = next(c for c in m["clusters"] if c["label"] == "CL-A")
        assert [v["hostname"] for v in cl["unregistered_vms"]] == ["orph1"]
        assert cl["unregistered_count"] == 1
        assert cl["unregistered_vms"][0]["esxi"] == ["esxi-r1"]
    finally:
        conn.close()


def test_看得到加只有登記等於台數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_rooms(conn)
        m = br.system_map(conn, "N-950")
        s = m["summary"]
        assert s["seen"] + s["registered_only"] == s["hosts"]
        assert s["seen"] == sum(1 for h in m["hosts"] if h["seen"])
        for h in m["hosts"]:
            assert h["exist_label"]                               # 每台都有可信度標籤
            assert h["seen"] == (h["exist_score"] is not None and h["exist_score"] >= m["exist_threshold"])
        for r in m["rooms"]:
            assert r["seen"] + r["registered_only"] == r["host_count"]
            assert len(r["seen_hosts"]) == r["seen"] and len(r["registered_only_hosts"]) == r["registered_only"]
        # 有 vCenter 證據的 p1／p2 看得到；只有 CIA 的 b1 只有登記
        by = {h["hostname"]: h for h in m["hosts"]}
        assert by["p1"]["seen"] is True and by["p1"]["exist_level"] == "inventory"
        assert by["b1"]["seen"] is False and by["b1"]["exist_level"] == "cia_only"
        assert s["seen"] == 2 and s["registered_only"] == 6
    finally:
        conn.close()


def test_主機名查所屬系統_忽略大小寫空白連字號(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="T-1", hostname="test-1.corp.example", ip="10.99.9.1",
                           api_id="N-971", asset_name="甲系統")
        db.insert_hardware(conn, asset_serial="T-2", hostname="TEST-1", ip="10.99.9.1",
                           api_id="N-972", asset_name="乙系統")               # 同一台，另一個系統
        db.insert_hardware(conn, asset_serial="T-3", hostname="other9", ip="10.99.9.2", api_id="N-971",
                           asset_name="甲系統")
        db.insert_hardware(conn, asset_serial="T-4", hostname="test-1", ip="10.99.9.3", api_id="N-973",
                           asset_name="丙系統")
        conn.execute("UPDATE hardware SET asset_status='報廢' WHERE asset_serial='T-4'")  # 退役不列
        conn.commit()
        for q in ("Test 1", "test1", "TEST-1"):
            hits = br.host_systems(conn, q)
            assert len(hits) == 1, q
            assert [s["api_id"] for s in hits[0]["systems"]] == ["N-971", "N-972"]
            assert hits[0]["systems"][1]["name"] == "乙系統"
        ip_hits = br.host_systems(conn, "10.99.9.2")
        assert [h["hostname"] for h in ip_hits] == ["other9"]
        assert br.host_systems(conn, "t") == []                                # 太短不查
    finally:
        conn.close()


def test_主機帶OS大類(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="O-1", hostname="lx1", ip="10.99.8.1", os="Red Hat Enterprise Linux 8",
                           api_id="N-980", asset_name="OS系統")
        db.insert_hardware(conn, asset_serial="O-2", hostname="win1", ip="10.99.8.2", os="Windows Server 2019",
                           api_id="N-980", asset_name="OS系統")
        db.insert_hardware(conn, asset_serial="O-3", hostname="sw1", ip="10.99.8.3", os="",
                           api_id="N-980", asset_name="OS系統")
        conn.commit()
        m = br.system_map(conn, "N-980")
        fam = {h["hostname"]: h["os_family"] for h in m["hosts"]}
        assert fam == {"lx1": "Linux", "win1": "Windows", "sw1": "其他"}
        assert all(h["asset_names"] == ["OS系統"] for h in m["hosts"])
    finally:
        conn.close()


def test_系統分級分組():
    assert br.class_group("S1-第一類系統軟體（核心）") == "第一類"
    assert br.class_group("第三類") == "第三類"
    assert br.class_group("") == "未分類"
    assert br.class_group(None) == "未分類"
    assert br.class_group("核心") == "未分類"


def test_系統清單帶分級(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="C-1", hostname="c1", ip="10.99.4.1", api_id="N-990", asset_name="分級系統")
        db.insert_hardware(conn, asset_serial="C-2", hostname="c2", ip="10.99.4.2", api_id="N-991", asset_name="未分級系統")
        conn.execute("INSERT INTO business_system (api_id, name, sys_class) VALUES ('N-990', '分級系統', 'S2-第二類一般')")
        conn.commit()
        rows = {r["api_id"]: r for r in br.list_business_systems(conn)}
        assert rows["N-990"]["class_group"] == "第二類"
        assert rows["N-991"]["class_group"] == "未分類"
    finally:
        conn.close()


def test_影響查詢帶上下游資料旗標(tmp_path):
    """系統之間沒有任何依賴邊時，impact 要回 deps_available=False，畫面才講得出「不完整」。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        nid = "hw:HW-3"   # db01，只掛 N-901 一個系統
        assert br.impact(conn, nid)["deps_available"] is False
        for sid in ("N-901", "N-902"):
            conn.execute("INSERT INTO systems (id, label, health) VALUES (?,?,'ok')", (sid, sid))
        conn.execute("INSERT INTO system_deps (source, target, dep_type) VALUES ('N-901','N-902','API')")
        conn.commit()
        ci_graph.rebuild(conn)
        assert br.impact(conn, nid)["deps_available"] is True
    finally:
        conn.close()


# ---------------------------------------------------------------- #50 上下游：總數＝下鑽、缺口清單、沒有關聯 vs 查不到資料（2026-11-12）
def _deps_seed(conn):
    _seed(conn)                       # N-901（app01、db01）、N-902（gw01）
    db.insert_hardware(conn, asset_serial="HW-7", hostname="rpt01", ip="10.99.3.1", api_id="N-907", asset_name="測試報表系統")
    db.insert_hardware(conn, asset_serial="HW-8", hostname="arc01", ip="10.99.3.2", api_id="N-908", asset_name="測試歸檔系統")
    for sid in ("N-901", "N-902", "N-903"):                      # system_deps 有 FK 到 systems；N-903 沒有主機
        conn.execute("INSERT INTO systems (id, label, health) VALUES (?,?,'ok')", (sid, sid))
    conn.execute("INSERT INTO system_deps (source, target, dep_type) VALUES ('N-901','N-902','API')")
    conn.execute("INSERT INTO system_deps (source, target, dep_type) VALUES ('N-901','N-903','檔案')")
    conn.commit()


def _observe(conn, ip, serial):
    import observed_deps as od
    od.store(conn, ip, serial, [{"direction": "outbound", "local_port": 0, "peer_ip": "10.99.250.9",
                                 "peer_port": 443, "count": 1}])
    od.invalidate()


def test_上下游狀態三格_清單筆數等於系統履歷下鑽(tmp_path):
    """案例表：N-901 人工登記依賴 N-902、N-903→有關聯（下游 2）；N-902 被依賴 1 筆→有關聯（上游 1）；
    N-907 主機有連線觀察、但連到的不是任何系統→沒有關聯；N-908 沒登記、主機沒觀察→查不到資料。"""
    import observed_deps as od
    import v2_systems
    conn = _conn(tmp_path)
    try:
        od.invalidate()
        _deps_seed(conn)
        _observe(conn, "10.99.3.1", "HW-7")
        lst = v2_systems.listing(conn)
        by = {r["api_id"]: r for r in lst["rows"]}
        assert {a: by[a]["deps_state"] for a in ("N-901", "N-902", "N-907", "N-908")} == {
            "N-901": "has", "N-902": "has", "N-907": "none", "N-908": "no_data"}
        assert (by["N-901"]["deps_down"], by["N-901"]["deps_up"]) == (2, 0)
        assert (by["N-902"]["deps_down"], by["N-902"]["deps_up"]) == (0, 1)
        for a, x in by.items():                                    # 總數＝下鑽：每個系統跟系統履歷同一個數
            m = br.system_map(conn, a)
            assert (x["deps_down"], x["deps_up"]) == (len(m["depends_on"]), len(m["depended_by"])), a
            assert x["deps_state"] == m["deps_state"] and x["deps_observed_hosts"] == m["deps_observed_hosts"], a
            assert m["deps_observed_hosts"] + len(m["deps_unobserved_hosts"]) == m["summary"]["hosts"], a
        assert sum(lst["deps_by_state"].values()) == lst["total"]
        assert lst["deps_by_state"]["no_data"] == sum(1 for r in lst["rows"] if r["deps_state"] == "no_data")
        m908 = br.system_map(conn, "N-908")
        assert m908["deps_state_label"] == "查不到資料" and [h["hostname"] for h in m908["deps_unobserved_hosts"]] == ["arc01"]
        assert br.system_map(conn, "N-907")["deps_state_label"] == "沒有關聯"
    finally:
        conn.close()
        od.invalidate()


def test_上下游雙向_補觀察變沒有關聯_刪登記退回(tmp_path):
    import observed_deps as od
    import v2_systems
    conn = _conn(tmp_path)
    try:
        od.invalidate()
        _deps_seed(conn)
        st = lambda a: {r["api_id"]: r for r in v2_systems.listing(conn)["rows"]}[a]["deps_state"]   # noqa: E731
        assert st("N-908") == "no_data"
        _observe(conn, "10.99.3.2", "HW-8")                        # 補上連線觀察 → 查過了、沒看到 → 沒有關聯
        assert st("N-908") == "none"
        conn.execute("DELETE FROM system_deps")
        conn.commit()
        od.invalidate()
        assert st("N-902") == "no_data", "登記刪掉、主機也沒觀察 → 回到查不到資料，不是沒有關聯"
        assert v2_systems.listing(conn) == v2_systems.listing(conn), "重跑結果一樣"
    finally:
        conn.close()
        od.invalidate()


def test_上下游_空庫零筆(tmp_path):
    import observed_deps as od
    import v2_systems
    conn = _conn(tmp_path)
    try:
        od.invalidate()
        lst = v2_systems.listing(conn)
        assert lst["total"] == 0 and lst["deps_by_state"] == {"has": 0, "none": 0, "partial": 0, "no_data": 0}
        assert br.deps_counts(conn) == {}
        assert br.deps_state(0, 0, 2) == "no_data" and br.deps_state(0, 2, 2) == "none"
        assert br.deps_state(0, 1, 2) == "partial" and br.deps_state(1, 0, 2) == "has" and br.deps_state(0, 0, 0) == "no_data"
    finally:
        conn.close()


def test_上下游_部分主機沒有觀察_不可判沒有關聯_雙向(tmp_path):
    """審查 P2：N-907 兩台、只有一台有連線觀察 → 部分查不到（不是沒有關聯），未觀察 1 台可下鑽；
    補上第二台觀察 → 沒有關聯。"""
    import observed_deps as od
    import v2_systems
    conn = _conn(tmp_path)
    try:
        od.invalidate()
        _deps_seed(conn)
        db.insert_hardware(conn, asset_serial="HW-7b", hostname="rpt02", ip="10.99.3.3", api_id="N-907", asset_name="測試報表系統")
        conn.commit()
        _observe(conn, "10.99.3.1", "HW-7")
        row = lambda: {r["api_id"]: r for r in v2_systems.listing(conn)["rows"]}["N-907"]   # noqa: E731
        r = row()
        assert (r["deps_state"], r["deps_state_label"], r["deps_observed_hosts"], r["deps_unobserved_count"]) == (
            "partial", "部分查不到", 1, 1)
        m = br.system_map(conn, "N-907")
        assert m["deps_state"] == "partial" and [h["hostname"] for h in m["deps_unobserved_hosts"]] == ["rpt02"]
        lst = v2_systems.listing(conn)
        assert lst["deps_by_state"]["partial"] == sum(1 for x in lst["rows"] if x["deps_state"] == "partial") >= 1
        assert sum(lst["deps_by_state"].values()) == lst["total"]
        _observe(conn, "10.99.3.3", "HW-7b")
        assert row()["deps_state"] == "none" and row()["deps_unobserved_count"] == 0
    finally:
        conn.close()
        od.invalidate()
