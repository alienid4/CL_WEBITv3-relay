"""MICS 切片2：影響範圍查詢引擎。

三條最重要的路徑（plan 驗證方式 §2）：
(a) 已登記 IP 回完整資產＋dependents
(b) 未登記 IP 回 segment fallback 且標「未驗證」，不回 404
(c) 帶未採集過的 port 回「未驗證」而非「沒有服務」——這兩句話意思完全不同
另外斷言：沒有 personnel 也沒有 api_id 的資產，出現在 unknown_owner[]。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import blast_radius as br  # noqa: E402
import ci_graph  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_已登記IP解析成功並回完整dependents(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(
            conn, asset_serial="HW-A", hostname="app01", ip="10.2.0.1", environment="正式",
            api_id="APID-1", availability=3, physical_location="板橋機房",
        )
        db.insert_hardware(
            conn, asset_serial="HW-B", hostname="db01", ip="10.2.0.2", environment="正式",
        )
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES ('hw:HW-A','host','app01','HW-A','derive:hardware','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES ('hw:HW-B','host','db01','HW-B','derive:hardware','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:HW-A','hw:HW-B','depends_on','證據','manual','t')"
        )
        conn.commit()

        resolved = br.resolve(conn, "10.2.0.2")
        assert resolved["status"] == "resolved"
        assert resolved["node_id"] == "hw:HW-B"

        result = br.impact(conn, "hw:HW-B", depth=6)
        dep_ids = {d["node_id"] for d in result["dependents"]}
        assert "hw:HW-A" in dep_ids
    finally:
        conn.close()


def test_未登記IP回segment_fallback且標未驗證(tmp_path):
    conn = _conn(tmp_path)
    try:
        conn.execute(
            "INSERT INTO network_segment (raw_cidr, net_start, net_end, location, environment, "
            "purpose_desc) VALUES ('10.9.0.0/24', ?, ?, '板橋機房', '正式', 'SERVER網段')",
            (int.from_bytes(bytes([10, 9, 0, 0]), "big"), int.from_bytes(bytes([10, 9, 0, 255]), "big")),
        )
        conn.commit()

        result = br.resolve(conn, "10.9.0.55")
        assert result["status"] == "unregistered"
        assert result["confidence"] == "未驗證"
        assert result["segment"]["location"] == "板橋機房"
    finally:
        conn.close()


def test_未登記IP且查不到網段時segment是None不報錯(tmp_path):
    conn = _conn(tmp_path)
    try:
        result = br.resolve(conn, "192.0.2.1")
        assert result["status"] == "unregistered"
        assert result["segment"] is None
    finally:
        conn.close()


def test_帶port查詢_未採集過回未驗證不是沒有服務(tmp_path):
    conn = _conn(tmp_path)
    try:
        result = br.resolve(conn, "10.9.0.99", port=22)
        assert result["status"] == "unregistered"
        assert result["service"]["known"] is False
        assert "未驗證" in result["service"]["note"]
    finally:
        conn.close()


def test_帶port查詢_已採集過回真實服務資訊(tmp_path):
    conn = _conn(tmp_path)
    try:
        conn.execute(
            "INSERT INTO host_service (ip, proto, port, process, service_guess, source, last_seen) "
            "VALUES ('10.9.0.99','tcp',22,'sshd','SSH','ssh_ss','2026-08-18 10:00:00')"
        )
        conn.commit()
        result = br.resolve(conn, "10.9.0.99", port=22)
        assert result["service"]["known"] is True
        assert result["service"]["process"] == "sshd"
    finally:
        conn.close()


def test_沒有負責人的資產出現在unknown_owner(tmp_path):
    conn = _conn(tmp_path)
    try:
        # HW-A 依賴 HW-C，HW-C 沒有 api_id 也沒有 personnel/user_name/custodian
        db.insert_hardware(conn, asset_serial="HW-A", hostname="app01", ip="10.2.0.1", environment="正式")
        db.insert_hardware(conn, asset_serial="HW-C", hostname="orphan01", ip="10.2.0.3", environment="正式")
        for nid, label in (("hw:HW-A", "app01"), ("hw:HW-C", "orphan01")):
            conn.execute(
                "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (nid, "host", label, nid.split(":")[1], "derive:hardware", "t", "t"),
            )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:HW-A','hw:HW-C','depends_on','證據','manual','t')"
        )
        conn.commit()

        result = br.impact(conn, "hw:HW-C", depth=6)
        unknown_serials = {u["asset_serial"] for u in result["summary"]["unknown_owner"]}
        assert "HW-A" in unknown_serials
    finally:
        conn.close()


def test_查無此節點拋ValueError(tmp_path):
    conn = _conn(tmp_path)
    try:
        try:
            br.impact(conn, "hw:NOT-EXIST")
            assert False, "應該要拋 ValueError"
        except ValueError:
            pass
    finally:
        conn.close()


def test_probe模式同時回dependencies(tmp_path):
    conn = _conn(tmp_path)
    try:
        for nid in ("hw:X", "hw:Y", "hw:Z"):
            conn.execute(
                "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)", (nid, "host", nid, "manual", "t", "t"),
            )
        # X 依賴 Y（Y 死了會害到 X）；Z 依賴 X（X 死了會害到 Z）
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:X','hw:Y','depends_on','證據','manual','t')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:Z','hw:X','depends_on','證據','manual','t')"
        )
        conn.commit()

        result = br.impact(conn, "hw:X", mode="probe")
        assert {d["node_id"] for d in result["dependents"]} == {"hw:Z"}     # 誰依賴 X
        assert {d["node_id"] for d in result["dependencies"]} == {"hw:Y"}   # X 依賴誰（可能肇因）
    finally:
        conn.close()


def test_graph_elements_direction_both含下游節點label(tmp_path):
    """probe 模式的「它依賴誰」要看得到 label，不能只有 node_id——
    direction=dependents（預設）不含下游節點，direction=both 才含。"""
    conn = _conn(tmp_path)
    try:
        for nid in ("hw:X", "hw:Y", "hw:Z"):
            conn.execute(
                "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)", (nid, "host", nid + "-label", "manual", "t", "t"),
            )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:X','hw:Y','depends_on','證據','manual','t')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:Z','hw:X','depends_on','證據','manual','t')"
        )
        conn.commit()

        default = br.graph_elements(conn, "hw:X")
        default_ids = {n["data"]["id"] for n in default["elements"]["nodes"]}
        assert default_ids == {"hw:X", "hw:Z"}  # 沒有 hw:Y（下游）

        both = br.graph_elements(conn, "hw:X", direction="both")
        both_ids = {n["data"]["id"] for n in both["elements"]["nodes"]}
        assert both_ids == {"hw:X", "hw:Y", "hw:Z"}
        y_node = next(n for n in both["elements"]["nodes"] if n["data"]["id"] == "hw:Y")
        assert y_node["data"]["label"] == "hw:Y-label"
    finally:
        conn.close()


def test_rebuild後真的能查到爆炸半徑_端到端(tmp_path):
    """跟 ci_graph.rebuild() 串起來測一次，不是只測 blast_radius 自己灌的假資料。"""
    conn = _conn(tmp_path)
    try:
        vm_id = db.insert_hardware(
            conn, asset_serial="VC-vm-x", hostname="web01", ip="10.3.0.1", environment="正式",
        )
        # 刻意不預先登記 ESXI-X 這台實體資產——這裡要測的是 resolve() 用查詢字串找到
        # esxi 節點本身，跟「hw: 節點 label 撞名」是不同的測試（見上面已登記IP那個案例）
        import json
        conn.execute(
            "INSERT INTO source_record (source, source_key, payload, resolved_status, "
            "resolved_hardware_id, resolved_rule, resolved_confidence, collected_at) "
            "VALUES ('vcenter', 'vm-x', ?, 'matched', ?, 'vm_uuid', 1.0, '2026-08-18 09:00:00')",
            (json.dumps({"esxi_host": "esxi-x"}), vm_id),
        )
        conn.commit()
        ci_graph.rebuild(conn)

        resolved = br.resolve(conn, "esxi-x")
        assert resolved["status"] == "resolved"
        result = br.impact(conn, resolved["node_id"], mode="incident")
        assert "hw:VC-vm-x" in {d["node_id"] for d in result["dependents"]}
    finally:
        conn.close()


def test_ESXi雙身分_查IP要選有VM依賴的esxi節點不是hw節點(tmp_path):
    """2026-08-19 使用者實測踩到：ESXi主機同時有 hw:（資產登記本身）跟 esxi:
    （vCenter推出來的角色節點，asset_serial有回填）兩個ci_node共用同一個
    asset_serial。真正掛了VM依賴邊的是esxi:節點，resolve()沒有排序時
    SQLite給哪筆看插入順序，選到hw:節點就等於看不到VM影響範圍（只看得到
    一條無關緊要的推論邊）。"""
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(
            conn, asset_serial="HW-ESX1", hostname="ESXI-A", ip="10.4.0.1", environment="正式",
        )
        # 故意先插入 hw: 節點（模擬 rebuild() 的實際插入順序：hardware 迴圈先跑）
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES ('hw:HW-ESX1','host','ESXI-A','HW-ESX1','derive:hardware','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES ('esxi:ESXI-A','esxi','esxi-a.company.com','HW-ESX1','derive:vcenter','t','t')"
        )
        # 5 台 VM 依賴這台 ESXi（掛在 esxi: 節點上，不是 hw: 節點）
        for i in range(5):
            vm_id = f"hw:VM-{i}"
            conn.execute(
                "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)", (vm_id, "host", f"vm-{i}", "derive:vcenter", "t", "t"),
            )
            conn.execute(
                "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                "VALUES (?,?,?,?,?,?)", (vm_id, "esxi:ESXI-A", "runs_on", "證據", "derive:vcenter", "t"),
            )
        conn.commit()

        resolved = br.resolve(conn, "10.4.0.1")
        assert resolved["status"] == "resolved"
        assert resolved["node_id"] == "esxi:ESXI-A"  # 不是 hw:HW-ESX1

        result = br.impact(conn, resolved["node_id"])
        assert len(result["dependents"]) == 5
    finally:
        conn.close()


def test_存快照_原封存證整包impact結果(tmp_path):
    """切片3：存快照要存當下完整的 impact() 結果，不是只存幾個欄位事後重算。"""
    conn = _conn(tmp_path)
    try:
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
            "VALUES ('hw:X','host','X','manual','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
            "VALUES ('hw:Y','host','Y','manual','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:Y','hw:X','depends_on','證據','manual','t')"
        )
        conn.commit()

        snap = br.save_snapshot(conn, "hw:X", "planned", "測試性停機", "tester")
        assert snap["id"] > 0
        assert snap["result"]["dependents"][0]["node_id"] == "hw:Y"

        fetched = br.get_snapshot(conn, snap["id"])
        assert fetched["reason"] == "測試性停機"
        assert fetched["asked_by"] == "tester"
        assert fetched["result"]["dependents"][0]["node_id"] == "hw:Y"

        listed = br.list_snapshots(conn, node_id="hw:X")
        assert len(listed) == 1
        assert listed[0]["id"] == snap["id"]
        assert "result_json" not in listed[0]  # 列表不帶大欄位

        assert br.get_snapshot(conn, 999999) is None
    finally:
        conn.close()


# ===== 業務系統的資產明細與負責人（2026-08-20 使用者要求）=====
#
# 使用者原話：「超音樹有三台，但我點進去不知道是哪三台，聯絡人／部門又是誰」。
# 表上寫「3」卻答不出是哪三台、要打給誰，事故當下等於沒有用——那三個數字
# 正是要拿來分派盤點任務的。

def _seed_bizsys(conn):
    """一個業務系統 N-009 底下兩台，其中一台有登記負責人、一台沒有。"""
    db.insert_hardware(conn, asset_serial="HW-S1", hostname="sonic01", ip="10.2.0.11",
                       environment="正式", physical_location="01_板橋機房",
                       api_id="N-009", asset_name="超音樹", availability=3,
                       user_name="王小明", usage_unit="證券資訊部")
    db.insert_hardware(conn, asset_serial="HW-S2", hostname="sonic02", ip="10.2.0.12",
                       environment="正式", physical_location="02_內湖機房",
                       api_id="N-009", asset_name="超音樹", availability=3)
    db.insert_hardware(conn, asset_serial="HW-ESXI", hostname="esxi01", ip="10.2.0.1")
    for serial, host in (("HW-S1", "sonic01"), ("HW-S2", "sonic02"), ("HW-ESXI", "esxi01")):
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"hw:{serial}", "host", host, serial, "derive:hardware", "t", "t"),
        )
    for serial in ("HW-S1", "HW-S2"):
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES (?, 'hw:HW-ESXI','runs_on','證據','manual','t')", (f"hw:{serial}",),
        )
    conn.commit()


def test_業務系統要列出是哪幾台而不只是數量(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        biz = br.impact(conn, "hw:HW-ESXI", depth=6)["summary"]["by_biz_system"]
        row = next(b for b in biz if b["api_id"] == "N-009")

        assert len(row["assets"]) == 2
        # 每一台都要答得出「是哪一台」——只有序號答不了，要有主機名/IP/位置
        serials = {a["asset_serial"] for a in row["assets"]}
        assert serials == {"HW-S1", "HW-S2"}
        by_serial = {a["asset_serial"]: a for a in row["assets"]}
        assert by_serial["HW-S1"]["hostname"] == "sonic01"
        assert by_serial["HW-S1"]["ip"] == "10.2.0.11"
        assert by_serial["HW-S1"]["location"] == "01_板橋機房"
    finally:
        conn.close()


def test_業務系統明細要帶關係資訊_隔幾層什麼關係多可信(tmp_path):
    """2026-08-20 使用者問「這三台跟這台主機有關係嗎」——資料一直都在 hit 裡
    （BFS算出來的depth/edge_type/confidence），組 by_biz_system 明細時漏接，
    使用者得自己跑去另一張表用主機名對一次。這裡鎖住不能再漏。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        biz = br.impact(conn, "hw:HW-ESXI", depth=6)["summary"]["by_biz_system"]
        by_serial = {a["asset_serial"]: a
                     for b in biz if b["api_id"] == "N-009" for a in b["assets"]}

        s1 = by_serial["HW-S1"]
        assert s1["depth"] == 1
        assert s1["edge_type"] == "runs_on"
        assert s1["confidence"] == "證據"
    finally:
        conn.close()


def test_每一台都帶負責人_查不到時是空陣列不是缺欄位(tmp_path):
    """「沒有負責人」是事故當下要立刻知道的事，必須明確表達成空陣列，
    不能讓欄位不存在——前端分不出「沒人」跟「還沒載完」。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        biz = br.impact(conn, "hw:HW-ESXI", depth=6)["summary"]["by_biz_system"]
        by_serial = {a["asset_serial"]: a
                     for b in biz if b["api_id"] == "N-009" for a in b["assets"]}

        owners = by_serial["HW-S1"]["owners"]
        assert [o["name"] for o in owners] == ["王小明"]
        assert owners[0]["department"] == "證券資訊部"   # 沒部門就不知道找哪個單位對口
        assert owners[0]["role"] == "AP User"

        # 沒登記的那台：欄位要在、且是空的
        assert by_serial["HW-S2"]["owners"] == []
    finally:
        conn.close()


def test_每台的負責人與全域notify清單一致(tmp_path):
    """同一份資料算兩次一定會漂走。notify（去重後的聯絡清單）與每台的 owners
    必須來自同一次計算，否則「照 notify 通知」跟「照明細通知」會通知到不同的人。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        summary = br.impact(conn, "hw:HW-ESXI", depth=6)["summary"]
        per_asset = {o["name"]
                     for b in summary["by_biz_system"] for a in b["assets"] for o in a["owners"]}
        assert per_asset <= {n["name"] for n in summary["notify"]}
        assert "王小明" in per_asset
    finally:
        conn.close()


def test_機房與環境別的每一桶都列得出是哪幾筆(tmp_path):
    """2026-08-20 使用者：「查到 unknown 又是哪幾台？要能點進去看」。
    「有 2 台位置不明」這句話沒有可執行性——要知道是哪 2 台才有辦法去查、去補。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        summary = br.impact(conn, "hw:HW-ESXI", depth=6)["summary"]

        for bucket, key in (("by_location", "location"), ("by_environment", "environment")):
            for row in summary[bucket]:
                # count 必須由 items 長度算出來。兩者各自累加遲早會出現
                # 「寫 3 台、點開只有 2 台」這種讓人對系統失去信任的畫面。
                assert row["count"] == len(row["items"]), f"{bucket} {row[key]}"
                assert row["items"], f"{bucket} {row[key]} 不該是空的"

        locs = {r["location"]: r for r in summary["by_location"]}
        assert {i["hostname"] for i in locs["01_板橋機房"]["items"]} == {"sonic01"}
        assert {i["hostname"] for i in locs["02_內湖機房"]["items"]} == {"sonic02"}
    finally:
        conn.close()


def test_未填桶裡分得出非資產節點(tmp_path):
    """環境別的「未填」桶裡有一部分根本不是資產（ESXi/cluster/網段這類節點沒有
    環境別可言），跟「是資產但欄位沒填」是兩回事。都叫 unknown 會有人跑去
    「補」一個根本不存在的欄位。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        # 真實會發生的情境：RVTools 報了一台 ESXi，但它沒登記在資產庫
        # （ci_graph 照樣建節點並標「未登記於資產庫」，不靜默丟掉）。
        # 這種節點沒有 asset_serial，也就沒有機房／環境別可言——它會進環境別的
        # 「未填」桶、卻不會進機房桶，這正是使用者看到「環境 unknown=9、
        # 機房 unknown=2」兩個數字對不起來的原因。
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
            "VALUES ('cluster:CL01','cluster','CL01','derive:rvtools','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
            "VALUES ('esxi:UNREG','esxi','esxi-unregistered','derive:rvtools','t','t')"
        )
        for src in ("hw:HW-ESXI", "esxi:UNREG"):
            conn.execute(
                "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                "VALUES (?, 'cluster:CL01','member_of','證據','manual','t')", (src,),
            )
        conn.commit()

        summary = br.impact(conn, "cluster:CL01", depth=6)["summary"]
        envs = {r["environment"]: r for r in summary["by_environment"]}
        assert "unknown" not in envs, "統一用「未填」，不要中英夾雜"

        unfilled = envs.get("未填")
        assert unfilled is not None
        # 非資產節點的 asset_serial 是 None，前端據此標「非資產節點」
        assert any(i["asset_serial"] is None and i["node_type"] == "esxi"
                   for i in unfilled["items"])

        # 而且它不該出現在機房桶裡（沒有機房可言），兩桶總數本來就會不一樣
        loc_ids = {i["node_id"] for r in summary["by_location"] for i in r["items"]}
        assert "esxi:UNREG" not in loc_ids
    finally:
        conn.close()


# ===== 涵蓋範圍聲明（2026-08-20 使用者：「我是 AIX 怎知道有沒有影響？」）=====
#
# 查一台儲存設備時，AIX／實體主機不會出現在結果裡——因為儲存關聯是靠 RVTools 建的，
# 而 RVTools 只看得到 vCenter 裡的虛擬機。使用者會把「沒出現」讀成「沒影響」，
# 實際是「根本沒查」。這兩件事在畫面上長得一模一樣，是這類工具最危險的錯。

def test_影響結果一定帶涵蓋範圍聲明(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        result = br.impact(conn, "hw:HW-ESXI", depth=6)
        cov = result["coverage"]
        names = [d["name"] for d in cov["dimensions"]]
        assert "實體主機 → 儲存設備" in names
        assert "SAN／Switch／VLAN" in names
        assert cov["total"] == len(cov["dimensions"])
    finally:
        conn.close()


def test_實體主機缺儲存關聯要給出實際台數而不是寫死的警語(tmp_path):
    """寫死的警語會在資料補齊後繼續嚇人，久了就被當成背景雜訊忽略。
    數字必須是算出來的，補齊後要自己降下來。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)   # 3 台實體機，都沒有 stores_on 邊
        d = next(x for x in br.coverage(conn)["dimensions"] if x["name"] == "實體主機 → 儲存設備")
        assert "3 / 3" in d["detail"]
        assert d["status"] == "none"

        # 幫其中一台補上儲存關聯，缺口數要跟著降
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
            "VALUES ('ds:DS_A','datastore','DS_A','derive:rvtools','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:HW-S1','ds:DS_A','stores_on','證據','manual','t')"
        )
        conn.commit()
        d2 = next(x for x in br.coverage(conn)["dimensions"] if x["name"] == "實體主機 → 儲存設備")
        assert "2 / 3" in d2["detail"]
        assert d2["status"] == "partial"
    finally:
        conn.close()


def test_沒匯過RVTools時要說是空的不是說有資料(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        d = next(x for x in br.coverage(conn)["dimensions"] if x["name"].startswith("虛擬機"))
        assert d["status"] == "none"
        assert "尚未匯入" in d["detail"]
    finally:
        conn.close()


def test_資料太舊不可算通過且要講出匯出日期(tmp_path):
    """VM 每天都在搬。三週前的快照拿來算爆炸半徑，得到的是**錯的**答案，
    不只是舊的答案——所以狀態不能標成 ok。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        conn.execute(
            "INSERT INTO source_record (source, source_key, payload, resolved_status, collected_at) "
            "VALUES ('vcenter','vm-1','{}','matched','2026-08-20 11:53:00')"
        )
        conn.execute(
            "INSERT INTO import_log (imported_by, hardware_count, personnel_count, "
            "software_count, error_count, imported_at, source, file_name, exported_at) "
            "VALUES ('admin',1,0,0,0,'2026-08-20 11:53:00','rvtools',"
            "'RVTools_export_all_2020-01-01_10.00.00.xlsx','2020-01-01 10:00:00')"
        )
        conn.commit()

        d = next(x for x in br.coverage(conn)["dimensions"] if x["name"].startswith("虛擬機"))
        assert d["status"] == "partial", "資料過期不可以標成 ok"
        assert "2020-01-01" in d["detail"]      # 講得出是哪天匯出的
        assert "距今" in d["detail"]            # 也講得出隔多久
    finally:
        conn.close()


def test_認不出匯出日期時不可宣稱資料是新的(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        conn.execute(
            "INSERT INTO source_record (source, source_key, payload, resolved_status, collected_at) "
            "VALUES ('vcenter','vm-1','{}','matched','2026-08-20 11:53:00')"
        )
        conn.execute(
            "INSERT INTO import_log (imported_by, hardware_count, personnel_count, "
            "software_count, error_count, imported_at, source, file_name, exported_at) "
            "VALUES ('admin',1,0,0,0,'2026-08-20 11:53:00','rvtools','rvtools.xlsx',NULL)"
        )
        conn.commit()

        d = next(x for x in br.coverage(conn)["dimensions"] if x["name"].startswith("虛擬機"))
        assert d["status"] == "partial"
        assert "認不出" in d["detail"] and "未驗證" in d["detail"]
    finally:
        conn.close()


def test_負責人一定帶email與分機欄位且說明為什麼是空的(tmp_path):
    """2026-08-20 使用者：email／分機先空白，以後補。但**留白不等於沒有**——
    空白會被讀成「這個人沒有分機」，實際是「我們還沒拿到這份資料」。
    事故當下前者代表不用再找、後者代表要趕快去問，讀錯會浪費時間。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        biz = br.impact(conn, "hw:HW-ESXI", depth=6)["summary"]["by_biz_system"]
        owner = next(o for b in biz for a in b["assets"] for o in a["owners"])

        # 欄位一定要在（前端據此排版），值是 None
        assert "email" in owner and owner["email"] is None
        assert "phone" in owner and owner["phone"] is None
        # 而且要講得出為什麼是空的
        assert "AD" in owner["email_note"]
        assert "personnel" in owner["phone_note"]
        # 有資料的欄位照常
        assert owner["name"] == "王小明"
        assert owner["department"] == "證券資訊部"
        assert owner["role"] == "AP User"
    finally:
        conn.close()


def test_SP保管者不列進聯絡清單_我們自己就是SP不用聯絡自己(tmp_path):
    """2026-08-20 使用者看了221正式資料的檢查清單後：「合約給過，但SP保管者
    拿掉。我們自己就是SP，所以不用聯絡自己，全部都是聯絡AP管理者就可以了」。"""
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-CUST", hostname="custhost", ip="10.3.0.1",
                           api_id="N-777", asset_name="有保管者的系統", availability=3,
                           user_name="AP負責人", custodian="內部保管者")
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES ('hw:HW-CUST','host','custhost','HW-CUST','derive:hardware','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES ('hw:HW-ESXI-CUST','host','esxi-cust',NULL,'manual','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:HW-CUST','hw:HW-ESXI-CUST','runs_on','證據','manual','t')"
        )
        conn.commit()

        biz = br.impact(conn, "hw:HW-ESXI-CUST", depth=6)["summary"]["by_biz_system"]
        asset = next(a for b in biz for a in b["assets"] if a["asset_serial"] == "HW-CUST")
        roles = {o["role"] for o in asset["owners"]}
        assert roles == {"AP User"}
        assert "SP 保管者" not in roles
    finally:
        conn.close()


# ===== 檢查清單（2026-08-20 拍板方案A）=====
# 使用者原話：「今天是很緊急的狀況，怎麼很快速地把全部的資訊列出來，給每一位
# 同事開始幫忙做檢查、幫忙做聯絡？聯絡完的時候，旁邊要寫備註」。

def test_建立檢查清單_攤平成主機聯絡人配對(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        snap = br.save_snapshot(conn, "hw:HW-ESXI", "incident", None, "tester", depth=6)

        items = br.create_checklist(conn, snap["id"])
        # HW-S1 有一個負責人(王小明) -> 1列；HW-S2 沒有負責人 -> 也要出現1列（查不到聯絡人）
        assert len(items) == 2
        by_host = {i["hostname"]: i for i in items}
        assert by_host["sonic01"]["contact_name"] == "王小明"
        assert by_host["sonic01"]["biz_system"] == "超音樹"
        assert by_host["sonic01"]["status"] == "未聯絡"
        assert by_host["sonic02"]["contact_name"] is None  # 沒有負責人也要有這一列

        # 2026-08-20 使用者：現有姓名/角色/電話/代理人/部門/部門主管是通訊錄視角，
        # 事故當下要看的是「這台機器多重要、在哪」——機器身分欄位要進清單，
        # 且是建立當下就存住的值，不是每次查詢重算。
        assert by_host["sonic01"]["environment"] == "正式"
        assert by_host["sonic01"]["physical_location"] == "01_板橋機房"
        assert by_host["sonic01"]["severity"] == "重大"  # availability=3
        assert by_host["sonic01"]["sort_depth"] == 1

        # 冪等：再建一次不會變成4列
        again = br.create_checklist(conn, snap["id"])
        assert len(again) == 2

        listed = br.list_checklist(conn, snap["id"])
        assert len(listed) == 2
    finally:
        conn.close()


def test_更新檢查清單項目_狀態與備註(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)
        snap = br.save_snapshot(conn, "hw:HW-ESXI", "incident", None, "tester", depth=6)
        items = br.create_checklist(conn, snap["id"])
        item_id = items[0]["id"]

        updated = br.update_checklist_item(conn, item_id, "已確認正常", "已電話確認，系統正常", "checker01")
        assert updated["status"] == "已確認正常"
        assert updated["note"] == "已電話確認，系統正常"
        assert updated["updated_by"] == "checker01"
        assert updated["updated_at"]

        # 只改備註不動狀態
        again = br.update_checklist_item(conn, item_id, None, "補充：已通知主管", "checker02")
        assert again["status"] == "已確認正常"  # 沒被改動
        assert again["note"] == "補充：已通知主管"
        assert again["updated_by"] == "checker02"

        try:
            br.update_checklist_item(conn, item_id, "不存在的狀態", None, "checker01")
            assert False, "應該要拋 ValueError"
        except ValueError:
            pass

        try:
            br.update_checklist_item(conn, 999999, "已確認正常", None, "checker01")
            assert False, "應該要拋 ValueError"
        except ValueError:
            pass
    finally:
        conn.close()


def test_建立檢查清單_查無快照拋ValueError(tmp_path):
    conn = _conn(tmp_path)
    try:
        try:
            br.create_checklist(conn, 999999)
            assert False, "應該要拋 ValueError"
        except ValueError:
            pass
    finally:
        conn.close()


def test_檢查清單排序_重大優先_同嚴重度隔越近越前面(tmp_path):
    """2026-08-20 拍板方案A：不給人工排優先級，直接照『重大先於一般、隔越近越
    前面』排好——事故當下開清單就能照順序打，不用自己判斷先打誰。"""
    conn = _conn(tmp_path)
    try:
        _seed_bizsys(conn)  # HW-S1/HW-S2：業務系統N-009，availability=3(重大)，隔1層
        # 加一台隔2層、掛在一個一般(非重大)業務系統下的資產，驗證重大優先於一般，
        # 即使一般那台其實隔得更近也一樣（嚴重度是第一排序鍵）。
        db.insert_hardware(conn, asset_serial="HW-S3", hostname="normal01", ip="10.2.0.13",
                           api_id="N-010", asset_name="一般系統", availability=1)
        conn.execute(
            "INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
            "VALUES ('hw:HW-S3','host','normal01','HW-S3','derive:hardware','t','t')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
            "VALUES ('hw:HW-S3','hw:HW-ESXI','runs_on','證據','manual','t')"
        )
        conn.commit()

        snap = br.save_snapshot(conn, "hw:HW-ESXI", "incident", None, "tester", depth=6)
        items = br.create_checklist(conn, snap["id"])

        severities = [i["severity"] for i in items]
        # 所有'重大'必須排在所有'一般'之前，不管資料庫寫入順序或距離
        first_normal = severities.index("一般")
        assert all(s == "重大" for s in severities[:first_normal])
        assert all(s == "一般" for s in severities[first_normal:])
    finally:
        conn.close()
