"""W221-016：分公司各分點的同名櫃（A1）不再共用機櫃鍵；舊鍵（room='分公司'）資料不改寫、不當空閒、標待確認。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import blast_radius  # noqa: E402
import db  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, loc, rack, ip):
    db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(), ip=ip, physical_location=loc,
                       rack_no=rack, device_model="Dell R750", api_id="N-920", asset_name="分點系統")


def _seed(conn):
    _hw(conn, "TC01", "分公司-台中", "A1/10-11U", "10.99.1.1")
    _hw(conn, "KH01", "分公司-高雄", "A1/10-11U", "10.99.1.2")
    _hw(conn, "BQ01", "01_板橋機房", "A1/10-11U", "10.99.1.3")
    _hw(conn, "BR01", "分公司", "A1/20U", "10.99.1.4")        # 只寫「分公司」：分點不明
    conn.commit()


def _by_room(conn):
    return {(m["room"], m["rack"]): m for m in rl.machines(conn) if m["kind"] == "rack"}


def test_rack_room_只改分公司_其他機房同map_room():
    for loc in ("01_板橋機房", "敦南機房", "02_內湖機房", "", None, "某未歸類機房", "分公司"):
        assert blast_radius.rack_room(loc) == blast_radius.map_room(loc)
    assert blast_radius.rack_room("分公司-台中") == "分公司-台中"
    assert blast_radius.rack_room("分公司 - 高雄") == "分公司-高雄"
    assert blast_radius.map_room("分公司-台中") == "分公司"       # 顯示分組不變


def test_兩分點同名櫃_不共用佔用與容量(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ms = _by_room(conn)
        assert {("分公司-台中", "A1"), ("分公司-高雄", "A1"), ("板橋機房", "A1"), ("分公司", "A1")} <= set(ms)
        assert ms[("分公司-台中", "A1")]["name"] == "tc01"
        # 台中 A1 的 10-11U 不影響高雄：兩邊各自一台、各自佔 2U
        r = rl.placement(conn, u=2, qty=1, rooms=["分公司-台中"])
        tc = next(x for x in r["racks"] if x["rack"] == "A1")
        assert tc["occupied"] == [10, 11]
        det = rl.rack_detail(conn, "分公司-高雄", "A1")
        assert [d["name"] for d in det["devices"]] == ["kh01"]
        # 在台中預留 → 高雄看不到
        rl.reserve(conn, "分公司-台中", "A1", 30, 2, "新機", "2099-01-01", "t")
        kh = next(x for x in rl.placement(conn, u=2, qty=1, rooms=["分公司-高雄"])["racks"] if x["rack"] == "A1")
        assert 30 not in kh["occupied"] and kh["reserved_u"] == 0
        tc2 = next(x for x in rl.placement(conn, u=2, qty=1, rooms=["分公司-台中"])["racks"] if x["rack"] == "A1")
        assert 30 in tc2["occupied"]
    finally:
        conn.close()


def test_一般機房結果不變(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        det = rl.rack_detail(conn, "板橋機房", "A1")
        assert [d["name"] for d in det["devices"]] == ["bq01"]
        assert det["legacy_reservations"] == [] and det["key_warnings"] == []
        r = next(x for x in rl.placement(conn, u=2, qty=1, rooms=["板橋機房"])["racks"] if x["rack"] == "A1")
        assert r["legacy_reserved_count"] == 0 and not r["site_pending"] and r["occupied"] == [10, 11]
    finally:
        conn.close()


def test_舊鍵預留_不當空閒_標待確認(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        rl.reserve(conn, "分公司", "A1", 30, 2, "舊預留", "2099-01-01", "t")     # 舊鍵：分點不明
        before = conn.execute("SELECT room, rack, u_start FROM rack_reservation").fetchall()
        det = rl.rack_detail(conn, "分公司-台中", "A1")
        assert len(det["legacy_reservations"]) == 1 and det["legacy_reservations"][0]["legacy_key"]
        assert any("待確認" in w for w in det["key_warnings"])
        assert det["reservations"] == []                           # 不歸給分點
        for room in ("分公司-台中", "分公司-高雄"):
            x = next(x for x in rl.placement(conn, u=2, qty=1, rooms=[room])["racks"] if x["rack"] == "A1")
            assert 30 in x["occupied"] and x["legacy_reserved_count"] == 1
            assert any("舊鍵預留" in s for s in x["reasons"])
        # 新預留撞到舊預留要警告
        res = rl.reserve(conn, "分公司-台中", "A1", 30, 1, "新", "2099-01-01", "t")
        assert any("舊預留" in c["name"] for c in res["collisions"])
        # 板橋同櫃號不受舊鍵影響
        b = next(x for x in rl.placement(conn, u=2, qty=1, rooms=["板橋機房"])["racks"] if x["rack"] == "A1")
        assert 30 not in b["occupied"]
        # 沒有被改寫
        after = conn.execute("SELECT room, rack, u_start FROM rack_reservation ORDER BY id").fetchall()
        assert [tuple(r) for r in after][:len(before)] == [tuple(r) for r in before]
    finally:
        conn.close()


def test_舊鍵盤點快照_可查不改寫(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        import rack_inventory
        rack_inventory.ensure_tables(conn)
        conn.execute("INSERT INTO rack_audit_rack (audit_id, room, rack, snapshot_json, book_count) VALUES (1,'分公司','A1','[]',3)")
        conn.commit()
        det = rl.rack_detail(conn, "分公司-台中", "A1")
        assert det["legacy_audits"] == [{"audit_id": 1, "book_count": 3, "legacy_key": True,
                                         "legacy_note": "舊鍵資料（分點不明，待確認）"}]
        assert conn.execute("SELECT COUNT(*) FROM rack_audit_rack WHERE room='分公司'").fetchone()[0] == 1
        assert rl.rack_detail(conn, "板橋機房", "A1")["legacy_audits"] == []
    finally:
        conn.close()


def test_映射不明_只寫分公司_標待確認不猜(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        det = rl.rack_detail(conn, "分公司", "A1")
        assert [d["name"] for d in det["devices"]] == ["br01"]
        assert any("分點不明" in w for w in det["key_warnings"])
        x = next(x for x in rl.placement(conn, u=2, qty=1, rooms=["分公司"])["racks"] if x["rack"] == "A1")
        assert x["site_pending"] and any("分點不明" in s for s in x["reasons"])
    finally:
        conn.close()


def test_彙總頁分公司台數不變(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        locs = [r[0] for r in conn.execute("SELECT physical_location FROM hardware")]
        grouped = [blast_radius.map_room(l) for l in locs]
        assert grouped.count("分公司") == 3                       # 彙總仍把三種寫法併成「分公司」
    finally:
        conn.close()


# ===== 審查補強（F2）=====
def test_分點判定收窄_正規表示式():
    rr = blast_radius.rack_room
    assert rr("分公司-台中") == "分公司-台中"
    assert rr("分公司－高雄") == "分公司-高雄"              # 全形連字號 → 同一把鍵
    assert rr("分公司 台中") == "分公司"            # 只有空白、沒有連字號：不當分點（審查：「分公司 A01」「分公司 本部」會被誤認）
    assert rr("分公司　本部") == "分公司" and rr("分公司 A01") == "分公司"
    assert rr("分公司 － 台中") == "分公司-台中"
    assert rr("分公司") == "分公司"
    assert rr("分公司本部") == "分公司"                    # 沒有分隔符與分點名：舊鍵，待確認
    assert rr("分公司A01") == "分公司"
    assert rr("01_板橋機房") == "板橋機房" and rr("") == "未填"
    assert rl.is_branch_site_room("分公司-台中") and not rl.is_branch_site_room("分公司")
    assert not rl.is_branch_site_room("分公司本部") and not rl.is_branch_site_room("板橋機房")


def test_分公司本部_舊鍵標待確認(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "HQ01", "分公司本部", "A1/5U", "10.99.2.1")
        conn.commit()
        x = next(x for x in rl.placement(conn, u=2, qty=1, rooms=["分公司"])["racks"] if x["rack"] == "A1")
        assert x["site_pending"]
    finally:
        conn.close()


def test_前端舊鍵警告文字存在():
    fe = ROOT / "APP" / "asset-module" / "frontend"
    el = (fe / "components" / "RackElevation.vue").read_text(encoding="utf-8")
    for needle in ("legacy_reservations", "legacy_audits", "key_warnings", "舊鍵資料（分點不明，待確認）", "舊鍵預留", "舊鍵盤點快照"):
        assert needle in el
    assert 'v-if="!compact && (det.legacy_reservations?.length' in el        # 沒有舊鍵資料時整塊不出現
    for page in ("pages/datacenter.vue", "pages/v2/datacenter.vue"):
        t = (fe / page).read_text(encoding="utf-8")
        assert "已保守扣除舊鍵預留" in t and "site_pending" in t


def test_機櫃新增設備_分點櫃可用(tmp_path):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = tmp_path / "a.db"
    db.init_db(p)

    def _ov():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _ov
    c = TestClient(api.app)
    conn = db.get_connection(p)
    db.create_user(conn, "admin", auth.hash_password("test-password-123"))
    _hw(conn, "TC01", "分公司-台中", "A1/10-11U", "10.99.1.1")
    _hw(conn, "OLD1", "分公司", "B1/10-11U", "10.99.1.9")
    _hw(conn, "BQ01", "01_板橋機房", "A1/10-11U", "10.99.1.3")
    conn.commit()
    conn.close()
    assert c.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
    for room, rack, loc, serial in (("分公司-台中", "A1", "分公司-台中", "S-N1"), ("分公司", "B1", "分公司", "S-N2"),
                                    ("板橋機房", "A1", "01_板橋機房", "S-N3")):
        r = c.post("/api/racks/add-device", json={"room": room, "rack": rack, "u_start": 30, "u_end": 31,
                                                 "asset_serial": serial, "device_model": "IBM S922", "asset_name": "新"})
        assert r.status_code == 200, (room, r.text)
        assert r.json()["hardware"]["physical_location"] == loc


def test_事故範圍_舊分公司涵蓋所有分點(tmp_path):
    import incident as inc
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        w = inc.World(conn)

        def names(s):
            return {m["name"] for m in inc.expand(w, s)["machines"]}
        assert names({"rooms": ["分公司"], "machines": []}) >= {"tc01", "kh01", "br01"}   # 舊值涵蓋所有分點＋舊鍵
        assert names({"rooms": ["分公司-台中"], "machines": []}) == {"tc01"}
        assert names({"racks": [{"room": "分公司", "rack": "A1"}], "machines": []}) >= {"tc01", "kh01", "br01"}
        assert "bq01" not in names({"rooms": ["分公司"], "machines": []})
    finally:
        conn.close()


def test_生命週期頁_房名與機櫃同一把鍵(tmp_path):
    import device_lifecycle as dl
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "NR01", "分公司-新竹", "", "10.99.1.7")        # 沒機櫃：舊寫法會回「分公司」
        conn.commit()
        rooms = {r["serial"]: r["room"] for r in dl.compute(conn)["rows"]}
        assert rooms.get("TC01") == "分公司-台中" and rooms.get("NR01") == "分公司-新竹"
    finally:
        conn.close()
