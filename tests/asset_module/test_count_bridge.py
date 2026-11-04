"""對帳橋（7 資料品質，v1.502.0，使用者 2026-10-26）＋對帳只算有涵蓋的網段。

使用者：數字看起來灌水、對不起來。拍板順序：先做對帳橋、看完分類再決定頭條。
釘住：
- 每筆現役登記剛好一種角色（互斥且窮盡），加總＝CIA 筆＋帳外筆
- 每種角色的判法（實體設備四類、LPAR、一體機、同框多筆、疑似登記錯誤、管理口掛本體／找不到本體、
  VM、ESXi、重複登記、端點、帳外待確認、無法判斷），以及兩個例外（一格多段 U 拆開、≤2U 超過 4 筆不併）
- HMC／Serial Console 雖然 os_class 判成管理介面，但它佔機櫃＝實體設備，不是管理口
- 人工指定：管理員才能改、有稽核紀錄、重匯 CIA（覆寫 hardware 欄位）不會蓋掉
- 試算三個數字＝各組成加總；每一格＝下鑽
- 對帳：一致＋搜不到＋未涵蓋＝登記台數；搜不到只算這次掃描有涵蓋的網段
- 首頁搬走的數字還在 API 上（/api/dashboard/composition、/api/dashboard/stats）
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import count_bridge as cb  # noqa: E402
import db  # noqa: E402
import scan_scope  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
LOC = "01_板橋機房"


def _seed(conn):
    def hw(serial, hostname, ip=None, **kw):
        kw.setdefault("physical_location", LOC)
        db.insert_hardware(conn, asset_serial=serial, hostname=hostname, ip=ip, **kw)

    hw("HW-SRV", "srv-a", "10.99.1.1", os="Red Hat Enterprise Linux 8", device_model="HPE DL380 Gen10",
       rack_no="M10/5-6U")
    hw("HW-STO", "sto-a", "10.99.1.2", device_model="IBM FlashSystem 5100", rack_no="M10/10U")
    hw("HW-NET", "sw-a", "10.99.1.3", os="Cisco IOS XE", device_model="Cisco C9300", rack_no="M10/20U")
    hw("HW-PC", "rackpc", "10.99.1.4", device_model="PC", rack_no="M10/30U")
    # LPAR：同框（同櫃同 U 同型號）兩筆 AIX
    hw("HW-LP1", "lpar1", "10.99.1.11", os="AIX 7.2", device_model="IBM S922", rack_no="M11/1-4U")
    hw("HW-LP2", "lpar2", "10.99.1.12", os="AIX 7.2", device_model="IBM S922", rack_no="M11/1-4U")
    # 一體機：同框兩筆 Exadata，使用者在 4-5「合併為同一台」→ 第二筆是同一個箱子的元件（見 _seed 最後）
    hw("HW-EX1", "exa1", "10.99.1.21", os="Oracle Linux 8", device_model="Oracle Exadata X10M", rack_no="M12/1-10U")
    hw("HW-EX2", "exa2", "10.99.1.22", os="Oracle Linux 8", device_model="Oracle Exadata X10M", rack_no="M12/1-10U")
    # x86、同 U 同型號、不同主機名：4-5 不併（兩台，算位置重疊）→ 對帳橋也是兩台實體伺服器
    hw("HW-FM1", "x86a", "10.99.1.31", os="Windows Server 2019", device_model="Lenovo x3650 M5", rack_no="M13/5-6U")
    hw("HW-FM2", "x86b", "10.99.1.32", os="Windows Server 2019", device_model="Lenovo x3650 M5", rack_no="M13/5-6U")
    # ≤2U 登記 5 筆 → 疑似登記錯誤
    for n in range(5):
        hw(f"HW-SUS{n}", f"sus{n}", f"10.99.1.4{n}", os="Windows Server 2019", device_model="HPE DL360 Gen10",
           rack_no="M14/7U")
    # 一格多段 U → 分開的設備，不合併
    hw("HW-MR1", "san1", "10.99.1.51", device_model="IBM San Switch", rack_no="M15/1-2U、3-4U")
    hw("HW-MR2", "san2", "10.99.1.52", device_model="IBM San Switch", rack_no="M15/1-2U、3-4U")
    # 管理口：掛在 srv-a（主機名＋機櫃）；找不到本體
    hw("HW-ILO", "srv-a-ilom", "10.99.2.1", os="iLO 5", device_model="HPE iLO", rack_no="M10/5-6U")
    hw("HW-ORP", "ghost-idrac", "10.99.2.2", os="N/A", device_model="DELL", rack_no="M16/1U")
    # HMC：os_class 判管理介面，但佔機櫃＝實體設備-其他
    hw("HW-HMC", "hmc1", "10.99.2.3", os="V10R3", device_model="IBM 7063-CR2", rack_no="M17/1U")
    # VM／ESXi
    hw("HW-VM", "vm1", "10.99.3.1", os="Windows Server 2022", is_vm="1", rack_no="(VM)")
    hw("HW-ESX", "esx1", "10.99.3.2", os="VMware ESXi 7.0", device_model="DELL R740", rack_no="M18/1-2U")
    # 重複登記：DY 空殼跟 vm1 同一台（同主機名同 IP）
    hw("DYN-VM1", "vm1", "10.99.3.1", physical_location=None)
    # 端點：沒有機櫃的 Win10
    hw("HW-PC2", "pc01", "10.99.4.1", os="Windows 10 Enterprise", physical_location=None)
    # 帳外、沒有任何證據 → 帳外待確認
    hw("DYN-X", "dyn-x", "10.99.5.1", physical_location=None)
    # v1.502.1：CIA、沒有機櫃也沒標 VM 的 Linux → 實體伺服器（缺機櫃資料），不是無法判斷
    hw("HW-LONE", "lonely", "10.99.6.1", os="Red Hat Enterprise Linux 9", physical_location=None)
    # 網路設備沒填機櫃 → 照樣是實體設備-網路（缺機櫃資料）
    hw("HW-NETNR", "sw-b", "10.99.6.2", os="Cisco IOS XE", device_model="Cisco C9200L", physical_location=None)
    # PC 級：機櫃欄填了但認不出（「網路櫃」）＝有機櫃位置 → 實體設備-其他
    hw("HW-PCU", "rackpc2", "10.99.6.3", device_model="PC", rack_no="網路櫃")
    # 帳外（DY 有、存在可信度夠）、沒有機櫃：看 VM 訊號
    hw("DYN-WIN", "dywin", "10.99.8.1", os="Windows Server 2022 Standard", physical_location=None)  # 沒訊號 → 無法判斷
    hw("DYN-MAC", "dymac", "10.99.8.2", os="Windows Server 2022 Standard", mac="00:50:56:aa:bb:cc",
       physical_location=None)                                                                         # VMware MAC
    hw("DYN-VCOS", "dyvcos", "10.99.8.3", os="Microsoft Windows Server 2022 (64-bit)", physical_location=None)
    hw("DYN-VCN", "dyvcn", "10.99.8.4", os="Windows Server 2019 Standard", physical_location=None)  # vCenter 清單有它
    hw("DYN-DL", "dydl", "10.99.8.5", os="Windows Server 2019 Standard", device_model="HPE DL380 Gen10",
       physical_location=None)                                                                         # 型號是實體機
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for serial in ("DYN-WIN", "DYN-MAC", "DYN-VCOS", "DYN-VCN", "DYN-DL"):
        hid = conn.execute("SELECT id FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()[0]
        conn.execute("INSERT INTO source_record (source, source_key, payload, collected_at, resolved_status, "
                     "resolved_hardware_id) VALUES ('dynassets', ?, '{\"is_vm\": null}', ?, 'new', ?)",
                     (f"dy-{serial}", now, hid))
    conn.execute("INSERT INTO source_record (source, source_key, payload, collected_at, resolved_status) "
                 "VALUES ('vcenter', 'vc-1', '{\"hostname\": \"DYVCN\", \"ip\": null}', ?, 'ambiguous')", (now,))
    # 退役：不在對帳橋裡
    hw("HW-OLD", "old1", "10.99.7.1", asset_status="報廢", rack_no="M10/40U")
    conn.commit()
    # 4-5 人工合併兩筆 Exadata（對帳橋不自己判同一個箱子，照 4-5 的決定）
    import overlap_queue
    import rack_layout
    exa = [m for m in rack_layout.machines(conn) if m["asset_serial"] in ("HW-EX1", "HW-EX2")]
    overlap_queue.decide(conn, exa[0]["room"], exa[0]["rack"], [m["key"] for m in exa], "merge", "tester")


def _ago(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


EXPECT = {
    "HW-SRV": "phys_server", "HW-STO": "phys_storage", "HW-NET": "phys_network", "HW-PC": "phys_other",
    "HW-LP1": "lpar", "HW-LP2": "lpar", "HW-EX1": "appliance", "HW-EX2": "appliance",
    "HW-FM1": "phys_server", "HW-FM2": "phys_server",
    **{f"HW-SUS{n}": "frame_suspect" for n in range(5)},
    "HW-MR1": "phys_storage", "HW-MR2": "phys_storage",
    "HW-ILO": "mgmt_attached", "HW-ORP": "mgmt_orphan", "HW-HMC": "phys_other",
    "HW-VM": "vm", "HW-ESX": "esxi", "DYN-VM1": "duplicate", "HW-PC2": "endpoint",
    "DYN-X": "offbook_pending", "HW-LONE": "phys_server", "HW-NETNR": "phys_network", "HW-PCU": "phys_other",
    "DYN-WIN": "unknown", "DYN-MAC": "vm", "DYN-VCOS": "vm", "DYN-VCN": "vm", "DYN-DL": "phys_server",
}
N_OFF = sum(1 for k in EXPECT if k.startswith("DYN-"))


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    _seed(conn)
    return conn, p


def _roles(conn):
    return {x["asset_serial"]: x for x in cb.compute(conn)["items"]}


def test_每種角色的判法(tmp_path):
    conn, _ = _conn(tmp_path)
    m = _roles(conn)
    assert "HW-OLD" not in m                      # 退役不在對帳橋
    assert {k: m[k]["role"] for k in EXPECT} == EXPECT
    assert m["HW-ILO"]["host_serial"] == "HW-SRV"
    assert m["HW-ORP"]["host_serial"] is None
    assert m["HW-MR1"]["frame"] != m["HW-MR2"]["frame"]          # 一格多段 U＝分開
    assert m["HW-LP1"]["frame"] == m["HW-LP2"]["frame"]
    assert m["HW-ESX"]["racked"] and not m["HW-VM"]["racked"]
    # 缺機櫃資料：只標在實體設備類，有機櫃（含認不出的機櫃欄）的不標
    assert m["HW-LONE"]["rack_missing"] and m["HW-NETNR"]["rack_missing"] and m["DYN-DL"]["rack_missing"]
    assert not m["HW-SRV"]["rack_missing"] and not m["HW-PCU"]["rack_missing"] and not m["HW-VM"]["rack_missing"]
    assert "VM 訊號" in m["DYN-MAC"]["reason"] and "vCenter" in m["DYN-VCN"]["reason"]


def test_互斥窮盡_加總與試算(tmp_path):
    conn, _ = _conn(tmp_path)
    out = cb.compute(conn)
    s = out["summary"]
    total = len(EXPECT)
    assert s["totals"] == {**s["totals"], "rows": total, "cia_rows": total - N_OFF, "offbook_rows": N_OFF}
    assert sum(r["rows"] for r in s["roles"]) == total
    assert sum(r["cia_rows"] for r in s["roles"]) == total - N_OFF and sum(r["offbook_rows"] for r in s["roles"]) == N_OFF
    by = {r["role"]: r for r in s["roles"]}
    assert (by["lpar"]["rows"], by["lpar"]["units"], by["lpar"]["frames"]) == (2, 2, 1)
    assert (by["appliance"]["rows"], by["appliance"]["units"]) == (2, 1)
    assert (by["frame_suspect"]["rows"], by["frame_suspect"]["units"]) == (5, 5)   # 4-5 各算一台
    assert by["duplicate"]["units"] == 0
    t = {x["key"]: x for x in s["trial"]}
    # 實體設備：伺服器 5（srv-a、x86a、x86b、lonely、dydl）＋儲存 3＋網路 2＋其他 3（PC、PC 網路櫃、HMC）
    #           ＋LPAR 1 箱＋一體機 1 箱＋疑似登記錯誤 5（4-5 各算一台）＋ESXi 1＋找不到本體、有 U 位的管理口 1
    p = t["physical"]
    assert p["value"] == 5 + 3 + 2 + 3 + 1 + 1 + 5 + 1 + 1
    assert (p["unparsed"], p["none"]) == (1, 3)       # 認不出：PC 網路櫃；缺機櫃：lonely、dydl、sw-b
    assert p["rack"] + p["unparsed"] + p["none"] == p["value"]
    # 主機：實體伺服器 5＋LPAR 2＋VM 4（vm1＋三筆有 VM 訊號的 DY；ESXi 不算、管理口不算）
    # ＋（L2）一體機元件裡跑 OS 的 2 筆（exa1、exa2 是 DB 節點）＋疑似登記錯誤裡伺服器 OS 的 5 筆
    assert t["hosts"]["value"] == 5 + 2 + 4 + 2 + 5
    assert len(cb.filter_items(out["items"], "phys_server", None, "none")) == 2
    # 跟 4-5 機房架構圖同一個「實體箱子」
    import rack_layout
    dc = rack_layout.datacenter(conn)
    assert p["rack"] == dc["total_racked"]
    assert by["mgmt_attached"]["rows"] == sum(x["breakdown"]["port"] for r in dc["rooms"] for x in r["racks"])
    assert any(x.get("no_rack_reason") == "機櫃欄認不出" for r in dc["rooms"] for x in r["no_rack"])
    assert t["mgmt"]["value"] == 2
    for x in s["trial"]:
        assert x["value"] == sum(p["n"] for p in x["parts"])
    # 每一格＝下鑽
    for r in s["roles"]:
        assert len(cb.filter_items(out["items"], r["role"], "CIA")) == r["cia_rows"]
        assert len(cb.filter_items(out["items"], r["role"], "帳外")) == r["offbook_rows"]


def test_人工指定後_加總仍等於下鑽_兩個角色的台數一增一減(tmp_path):
    """新版 9.2 明細表可以批次人工指定（2.007.58）：改完之後互斥窮盡、每一格＝下鑽仍要成立，
    被改的那筆從原角色的格子移到新角色的格子，其他格子不動。"""
    conn, _ = _conn(tmp_path)
    before = cb.compute(conn)
    b0 = {r["role"]: r for r in before["summary"]["roles"]}
    old_role = {x["asset_serial"]: x["role"] for x in before["items"]}["HW-PC2"]
    cb.set_override(conn, "HW-PC2", "phys_server", "現場看過是實體機", "admin")
    after = cb.compute(conn)
    s = after["summary"]
    a0 = {r["role"]: r for r in s["roles"]}
    assert sum(r["rows"] for r in s["roles"]) == s["totals"]["rows"] == len(after["items"])
    for r in s["roles"]:
        assert len(cb.filter_items(after["items"], r["role"], "CIA")) == r["cia_rows"]
        assert len(cb.filter_items(after["items"], r["role"], "帳外")) == r["offbook_rows"]
    assert a0[old_role]["rows"] == b0[old_role]["rows"] - 1
    assert a0["phys_server"]["rows"] == b0["phys_server"]["rows"] + 1
    for k in b0:
        if k not in (old_role, "phys_server"):
            assert a0[k]["rows"] == b0[k]["rows"], k
    # 清除 → 回到原樣
    cb.set_override(conn, "HW-PC2", None, None, "admin")
    again = {r["role"]: r["rows"] for r in cb.compute(conn)["summary"]["roles"]}
    assert again == {k: v["rows"] for k, v in b0.items()}


def test_人工指定_管理員_稽核_重匯不蓋掉(tmp_path):
    conn, p = _conn(tmp_path)
    conn.close()

    def _override_get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    try:
        c = db.get_connection(p)
        db.create_user(c, "admin", auth.hash_password(_STUB_CREDENTIAL))
        db.create_user(c, "tester", auth.hash_password(_STUB_CREDENTIAL))
        c.commit()
        c.close()
        user = TestClient(api.app)
        assert user.post("/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}).status_code == 200
        body = {"asset_serial": "HW-PC2", "role": "phys_server", "note": "現場看過是實體機"}
        assert user.put("/api/count-bridge/override", json=body).status_code == 403
        admin = TestClient(api.app)
        assert admin.post("/api/auth/login", json={"username": "admin", "password": _STUB_CREDENTIAL}).status_code == 200
        assert admin.put("/api/count-bridge/override", json={**body, "role": "亂寫"}).status_code == 400
        assert admin.put("/api/count-bridge/override", json={**body, "asset_serial": "NOPE"}).status_code == 404
        r = admin.put("/api/count-bridge/override", json=body)
        assert r.status_code == 200 and r.json()["auto_role"] == "endpoint"
        # 重匯 CIA 會覆寫 hardware 欄位——人工指定存自己的表，不受影響
        c = db.get_connection(p)
        db.update_hardware(c, "HW-PC2", {"os": "Windows 10 Enterprise 22H2", "rack_no": ""})
        c.commit()
        it = _roles(c)["HW-PC2"]
        assert it["role"] == "phys_server" and it["override"]
        c.close()
        b = admin.get("/api/count-bridge").json()
        assert b["totals"]["override_rows"] == 1
        d = admin.get("/api/count-bridge/items", params={"role": "phys_server"}).json()
        assert "HW-PC2" in {x["asset_serial"] for x in d["items"]} and d["total"] == len(d["items"])
        assert admin.get("/api/count-bridge/items", params={"role": "亂寫"}).status_code == 400
        a = admin.get("/api/count-bridge/override-audit").json()["items"]
        assert a[0]["asset_serial"] == "HW-PC2" and a[0]["new_role"] == "phys_server" and a[0]["by_user"].startswith("admin")
        # 清除 → 回到自動判斷，也留紀錄
        assert admin.put("/api/count-bridge/override", json={"asset_serial": "HW-PC2", "role": None}).status_code == 200
        assert admin.get("/api/count-bridge/override-audit").json()["items"][0]["new_role"] is None
        assert admin.get("/api/count-bridge").json()["totals"]["override_rows"] == 0
    finally:
        api.app.dependency_overrides.clear()


def test_對帳只算有涵蓋的網段(tmp_path):
    conn, p = _conn(tmp_path)
    t = _ago(1)
    # 這次掃描：10.99.1.0/24 有存活紀錄（srv-a 回應）；10.99.2.0/24 有涵蓋紀錄、沒人回應
    conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) VALUES (?,?,?,?,1)",
                 (t, "10.99.1.1", "srv-a", "10.99.1.0/24"))
    scan_scope._ensure(conn)
    conn.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)",
                 (t, "10.99.2.0/24", t))
    conn.commit()
    rc = cb.recon_coverage(conn)
    by = {x["asset_serial"]: x["bucket"] for x in rc["items"]}
    assert by["HW-SRV"] == "overlap"
    assert by["HW-STO"] == "missing" and by["HW-ORP"] == "missing"      # 10.99.1.x／10.99.2.x 有涵蓋、沒回應
    assert by["HW-VM"] == "uncovered" and by["HW-LONE"] == "uncovered"  # 那段這次沒掃
    c = rc["counts"]
    assert c["overlap"] + c["missing"] + c["uncovered"] == rc["total"]
    assert rc["covered"] == c["overlap"] + c["missing"] and rc["covered_segments"] == 2
    assert "DYN-X" not in by and "HW-OLD" not in by                     # 排帳外、排退役（跟首頁同一個 universe）
    assert cb.match_pct_covered(1, 4, 0) == 25.0 and cb.match_pct_covered(0, 0, 0) is None
    conn.close()

    def _override_get_db():
        cc = db.get_connection(p)
        try:
            yield cc
        finally:
            cc.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    try:
        cc = db.get_connection(p)
        db.create_user(cc, "tester", auth.hash_password(_STUB_CREDENTIAL))
        cc.commit()
        cc.close()
        client = TestClient(api.app)
        assert client.post("/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}).status_code == 200
        st = client.get("/api/dashboard/stats", params={"environment": "全部"}).json()
        assert st["covered_missing_machines"] == c["missing"] and st["uncovered_ica_machines"] == c["uncovered"]
        assert st["covered_overlap_machines"] == st["total_overlap_machines"] == c["overlap"]
        assert st["covered_ica_machines"] + st["uncovered_ica_machines"] == st["total_ica_machines"]
        for b in cb.RECON_BUCKETS:
            r = client.get("/api/count-bridge/recon", params={"bucket": b}).json()
            assert len(r["items"]) == c[b], b
        assert client.get("/api/count-bridge/recon", params={"bucket": "x"}).status_code == 400
        # 首頁搬走的「過程數字」還在 API 上（搬到 7 資料品質的對帳橋頁）
        comp = client.get("/api/dashboard/composition").json()
        for k in ("os_from_facts", "os_guessed", "by_os_class", "existence"):
            assert k in comp, k
        assert "cia_complete_pct" in comp["existence"]
    finally:
        api.app.dependency_overrides.clear()


def test_VMware平台_有VM證據算虛擬機_沒有才是ESXi_天條不受影響(tmp_path):
    """2026-10-27 決定（v2.007.6）：明確的 VM 證據（型號／機櫃欄「(VM)」、is_vm）優先——vCenter 一體機本來就是 VM、
    標 (VM) 的 ESXi 多半是巢狀 ESXi；沒有 VM 證據的 VMware 平台才是 ESXi 實體機。
    逐台標記同一個規則（VMware 的寫「虛擬機（VMware 設備）」）；納管／收集的天條看作業系統大類，不受角色影響。"""
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))
    import count_bridge
    import db as _db
    import onboard_eligibility
    import v2_hosts
    import v2_views
    import verify_numbers as vn

    p = tmp_path / "t.db"
    _db.init_db(p)
    c = _db.get_connection(p)
    try:
        _db.insert_hardware(c, asset_serial="E-1", hostname="esx67n", ip="10.99.8.11", os="VMWare ESXi 6.7",
                            device_model="(VM)", rack_no="(VM)", is_vm=1)                      # 巢狀 ESXi
        _db.insert_hardware(c, asset_serial="E-2", hostname="vcsa01", ip="10.99.8.12", os="VMware Photon OS (64-bit)", is_vm=1)
        _db.insert_hardware(c, asset_serial="E-3", hostname="esx80", ip="10.99.8.14", os="VMware ESXi 8.0.3")   # 沒有 VM 證據
        _db.insert_hardware(c, asset_serial="V-1", hostname="app01", ip="10.99.8.13", os="Red Hat Enterprise Linux 9",
                            device_model="(VM)", rack_no="(VM)", is_vm=1)
        c.commit()
        roles = {x["asset_serial"]: x["role"] for x in count_bridge.compute(c)["items"]}
        assert (roles["E-1"], roles["E-2"], roles["E-3"], roles["V-1"]) == ("vm", "vm", "esxi", "vm"), roles
        ms = {r["serial"]: r for r in v2_hosts.machines(c)}
        assert [ms[s]["badge"]["vm"] for s in ("E-1", "E-2", "E-3", "V-1")] == [True, True, False, True]
        assert ms["E-2"]["badge"]["vm_label"] == "虛擬機（VMware 設備）" and "VMware 設備" in ms["E-1"]["type"]
        assert v2_views.machine(c, "vcsa01")["badge"]["vm_label"] == "虛擬機（VMware 設備）"
        assert sum(1 for r in ms.values() if r["badge"]["vm"]) == count_bridge.measure(count_bridge.compute(c)["items"], "vm") == 3
        # 天條：角色是 vm 也照樣不納管、不收集
        for s in ("E-1", "E-2", "E-3"):
            row = c.execute("SELECT * FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
            assert onboard_eligibility.judge_for_asset(c, row)[0] == "esxi", s
        assert set(onboard_eligibility.vmware_targets(c, ["10.99.8.11", "10.99.8.12", "10.99.8.13", "10.99.8.14"])) ==             {"10.99.8.11", "10.99.8.12", "10.99.8.14"}
        vn.RESULTS.clear()
        vn.check_v2_hosts(c)
        assert all(r["ok"] for r in vn.RESULTS if "VM 台數" in r["name"])
    finally:
        c.close()


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
