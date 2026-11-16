"""機櫃「前後各一台」（2026-11-01）：兩台背對背共用同一段 U（一台在正面、一台在背面）。

守的事（危險區 D3）：
  · 決定「前後各一台」→ 重疊清單移除；未確認的同 U 兩台行為跟原本完全一樣（回歸）
  · 台數不變（machine_key、datacenter、count_bridge）；共用的 U 只算佔用一次；用電、重量各算各的
  · 可還原、會留稽核、非管理員 403、讀取路徑唯讀（不建表、不寫庫）
全部假資料（192.0.2.x、虛構主機名）。
"""
import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import count_bridge as cb  # noqa: E402
import db  # noqa: E402
import overlap_queue as oq  # noqa: E402
import rack_layout as rl  # noqa: E402
import system_stats  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"          # 測試用假密碼


def _hw(conn, serial, host, ip, rack, model, hw_serial=None):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                       rack_no=rack, device_model=model, api_id="N-940", asset_name="電力系統", hw_serial=hw_serial)
    conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = datetime('now','localtime') "
                 "WHERE asset_serial = ?", (serial,))


def _seed(conn):
    _hw(conn, "FR-1", "frfront01", "192.0.2.11", "K1/10-11U", "Dell R750")               # 正面（2800 W）
    _hw(conn, "FR-2", "frrear01", "192.0.2.12", "K1/10-11U", "Cisco C9200L-48T-4X")      # 背面
    _hw(conn, "FR-3", "solo01", "192.0.2.13", "K1/20U", "Dell R750")                     # 沒有重疊的一般設備
    conn.commit()


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    return c, p


def _room(conn):
    return rl.datacenter(conn)["rooms"][0]["room"]


def _grp(conn):
    return next(x for x in oq.groups(conn)["groups"] if x["u_lo"] == 10)


def _by_name(conn):
    return {m["name"]: m["key"] for m in rl.machines(conn) if m["kind"] == "rack"}


def _numbers(conn):
    """台數相關的全部數字：datacenter、對帳橋、machine_key 去重後的台數。"""
    dc = rl.datacenter(conn)
    tile = dc["rooms"][0]["racks"][0]
    cbs = cb.compute(conn, skip_override=True)["summary"]["totals"]["counted_machines"]
    keys = {system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
            for r in conn.execute("SELECT hostname, ip, asset_serial FROM hardware")}
    return {"total_racked": dc["total_racked"], "total_devices": dc["total_devices"], "tile_devices": tile["device_count"],
            "breakdown": tile["breakdown"], "counted": cbs, "mk": len(keys)}


# ---------------------------------------------------------------- 回歸：沒確認前跟原本一樣
def test_未確認的同U兩台_仍報位置重疊_U只算一次(tmp_path):
    conn, _ = _conn(tmp_path)
    try:
        g = oq.groups(conn)
        assert g["open"] == 1 and g["front_rear"] == []
        grp = _grp(conn)
        assert grp["cause"] == "other" and not grp["resolved"]
        d = rl.rack_detail(conn, _room(conn), "K1")
        assert d["front_rear"] == [] and all("side" not in x for x in d["devices"])
        pw = d["power"]
        assert pw["used_u"] == 3 and pw["rear_count"] == 0 and pw["front_rear_shared_u"] == 0   # 10-11 兩格＋20 一格
        assert sorted(x["overlap_with"] and 1 or 0 for x in d["devices"]) == [0, 1, 1]
    finally:
        conn.close()


# ---------------------------------------------------------------- 決定 → 清單移除、台數不變、U 一次、電各算
def test_前後各一台_清單移除_台數不變_U只算一次_用電各算各的(tmp_path):
    conn, _ = _conn(tmp_path)
    try:
        before = _numbers(conn)
        room = _room(conn)
        pw0 = rl.rack_detail(conn, room, "K1")["power"]
        names = _by_name(conn)
        grp = _grp(conn)
        res = oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", "背對背裝", target_key=names["frrear01"])
        assert res["action_label"] == "前後各一台"

        g = oq.groups(conn)
        assert g["open"] == 0 and g["resolved"] == 1
        assert g["front_rear"][0]["rear"] == [names["frrear01"]] and g["front_rear"][0]["front"] == [names["frfront01"]]
        assert _grp(conn)["decision"]["action"] == "front_rear"

        assert _numbers(conn) == before                                   # 台數、對帳橋、machine_key 完全不變
        d = rl.rack_detail(conn, room, "K1")
        pw = d["power"]
        assert pw["used_u"] == pw0["used_u"] == 3                        # 共用的 10-11U 只算一次，不是 5
        assert pw["free_u"] == pw0["free_u"] and pw["largest_free_u"] == pw0["largest_free_u"]
        assert pw["front_rear_shared_u"] == 2 and pw["rear_count"] == 1
        assert pw["active_count"] == 3 and pw["known_count"] + pw["unknown_count"] == 3
        # 用電各算各的：R750 2800 W ×2（front＋solo）＋ 背面那台自己的瓦數
        det = {x["name"]: x for x in d["devices"]}
        assert det["frrear01"]["side"] == "rear" and det["frfront01"]["side"] == "front" and "side" not in det["solo01"]
        assert det["frrear01"]["fr_with"] == [names["frfront01"]] and det["frfront01"]["fr_with"] == [names["frrear01"]]
        assert all(x["overlap_with"] == [] for x in d["devices"])
        want_w = sum(x["w"] for x in d["devices"] if x["w"] is not None)
        assert pw["w"] == round(want_w, 1) and det["frfront01"]["w"] == 2800
        assert pw["w"] == pw0["w"]                                        # 決定前後用電不變（本來就各算各的）
        assert d["front_rear"][0]["rear"] == [names["frrear01"]]

        tile = rl.datacenter(conn)["rooms"][0]["racks"][0]
        assert tile["overlap_count"] == 0 and tile["rear_keys"] == [names["frrear01"]]
        assert tile["used_u"] == 3 and tile["power"]["used_u"] == 3
        # 稽核：誰、何時、備註、背面是哪一台
        h = oq.history(conn, room)
        assert h[0]["action"] == "front_rear" and h[0]["decided_by"] == "alice" and h[0]["note"] == "背對背裝"
        assert h[0]["target_key"] == names["frrear01"]
    finally:
        conn.close()


def test_前後各一台_還原後回到原狀(tmp_path):
    conn, _ = _conn(tmp_path)
    try:
        room = _room(conn)
        base_numbers, base_g = _numbers(conn), oq.groups(conn)["open"]
        base_detail = [(x["name"], x["overlap_with"]) for x in rl.rack_detail(conn, room, "K1")["devices"]]
        grp = _grp(conn)
        r = oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", None, target_key=_by_name(conn)["frrear01"])
        assert oq.groups(conn)["open"] == 0
        oq.undo(conn, r["id"], "alice", "測試復原")
        assert oq.groups(conn)["open"] == base_g == 1 and oq.groups(conn)["front_rear"] == []
        assert _numbers(conn) == base_numbers
        d = rl.rack_detail(conn, room, "K1")
        assert [(x["name"], x["overlap_with"]) for x in d["devices"]] == base_detail
        assert d["power"]["rear_count"] == 0
        with pytest.raises(ValueError):
            oq.undo(conn, r["id"], "alice")                               # 復原過不能再復原
        assert len(oq.history(conn, room)) == 1                           # 紀錄還在（只標復原）
    finally:
        conn.close()


def test_決定的防呆(tmp_path):
    conn, _ = _conn(tmp_path)
    try:
        room = _room(conn)
        grp = _grp(conn)
        with pytest.raises(PermissionError):
            oq.decide(conn, room, "K1", grp["keys"], "front_rear", None, target_key=grp["keys"][0])
        with pytest.raises(ValueError):                                   # 沒指定背面是哪一台
            oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice")
        with pytest.raises(ValueError):                                   # 指定的不在這組
            oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", target_key="nope")
        oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", target_key=grp["keys"][0])
        with pytest.raises(ValueError):                                   # 已處理過，不能再決定一次
            oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", target_key=grp["keys"][1])
        with pytest.raises(ValueError):
            oq.decide(conn, room, "K1", grp["keys"], "merge", "alice")
    finally:
        conn.close()


def test_同一面還互相重疊_不能當前後各一台(tmp_path):
    """三台：A、B 都在正面且互相重疊，C 在背面——不能整組放過，指定 C 在背面時要被擋。"""
    conn, _ = _conn(tmp_path)
    try:
        _hw(conn, "FR-4", "frfront02", "192.0.2.14", "K1/11-12U", "HPE DL380")
        conn.commit()
        room = _room(conn)
        grp = next(x for x in oq.groups(conn)["groups"] if x["u_lo"] == 10)
        assert len(grp["keys"]) == 3
        names = _by_name(conn)
        # 11U 是 frfront01(10-11) 與 frfront02(11-12) 與 frrear01(10-11) 三方互相重疊
        with pytest.raises(ValueError):
            oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", target_key=names["frrear01"])
        # 背面指定 frfront02：正面剩 frfront01、frrear01 仍互相重疊 → 一樣擋
        with pytest.raises(ValueError):
            oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", target_key=names["frfront02"])
    finally:
        conn.close()


# ---------------------------------------------------------------- 唯讀
def _ro(p):
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c


# 寫入類語句（CREATE TABLE IF NOT EXISTS 對已存在的表是 no-op，是既有各模組的建表防呆，這裡不算；
# 真正的證明是：表不存在時唯讀連線（mode=ro＋query_only）照樣讀得出來，沒丟「attempt to write」）
_WRITE_SQL = ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "REPLACE")


def test_讀取路徑唯讀_不建表不寫庫(tmp_path):
    conn, p = _conn(tmp_path)
    room = _room(conn)
    grp = _grp(conn)
    # 情況 1：連 rack_overlap_decision 表都還不存在 → 讀取不能去建它
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE name='rack_overlap_decision'").fetchone()
    if has:
        conn.execute("DROP TABLE rack_overlap_decision")
        conn.commit()
    conn.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = _ro(p)
    stmts: list[str] = []
    ro.set_trace_callback(stmts.append)
    assert oq.groups(ro)["open"] == 1 and oq.history(ro) == []
    rl.rack_detail(ro, room, "K1")
    rl.datacenter(ro)
    ro.close()
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
    bad = [s for s in stmts if s.strip().upper().startswith(_WRITE_SQL)]
    assert not bad, bad[:3]

    # 情況 2：有前後各一台的決定 → 唯讀連線照樣讀得到
    w = db.get_connection(p)
    names = _by_name(w)
    oq.decide(w, room, "K1", grp["keys"], "front_rear", "alice", None, target_key=names["frrear01"])
    w.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = _ro(p)
    stmts = []
    ro.set_trace_callback(stmts.append)
    assert oq.groups(ro)["open"] == 0
    d = rl.rack_detail(ro, room, "K1")
    assert d["power"]["rear_count"] == 1 and d["power"]["used_u"] == 3
    assert rl.datacenter(ro)["rooms"][0]["racks"][0]["rear_keys"]
    assert not [s for s in stmts if s.strip().upper().startswith(_WRITE_SQL)]
    ro.close()
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


# ---------------------------------------------------------------- 權限（API）
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    conn, p = _conn(tmp_path)
    for name, role in (("admin", "admin"), ("alice", "user")):
        db.create_user(conn, name, auth.hash_password(_PW), role=role)
    conn.close()

    def _override_get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db

    class E:
        path = p

        def client(self, name):
            c = TestClient(api.app)
            r = c.post("/api/auth/login", json={"username": name, "password": _PW})
            assert r.status_code == 200, r.text
            c.headers[auth.CSRF_HEADER_NAME] = auth.csrf_token_for(c.cookies.get("session_token"))   # 明確帶 CSRF
            return c
    yield E()
    api.app.dependency_overrides.clear()


def test_非管理員403_管理員可決定_稽核與還原(env):
    c = db.get_connection(env.path)
    room = _room(c)
    grp = _grp(c)
    names = _by_name(c)
    c.close()
    body = {"room": room, "rack": "K1", "keys": grp["keys"], "action": "front_rear", "target_key": names["frrear01"], "note": "背對背"}
    assert env.client("alice").post("/api/racks/overlaps/decide", json=body).status_code == 403
    adm = env.client("admin")
    r = adm.post("/api/racks/overlaps/decide", json=body)
    assert r.status_code == 200, r.text
    got = adm.get("/api/racks/overlaps", params={"room": room}).json()
    assert got["open"] == 0 and got["front_rear"] and got["can_edit"]
    det = adm.get("/api/racks/detail", params={"room": room, "rack": "K1"}).json()
    assert det["front_rear"] and {x["name"]: x.get("side") for x in det["devices"]}["frrear01"] == "rear"
    hist = adm.get("/api/racks/overlaps/history", params={"room": room}).json()
    assert hist[0]["action"] == "front_rear" and hist[0]["decided_by"].startswith("admin")
    assert env.client("alice").post(f"/api/racks/overlaps/{r.json()['id']}/undo", json={}).status_code == 403
    assert adm.post(f"/api/racks/overlaps/{r.json()['id']}/undo", json={}).status_code == 200
    assert adm.get("/api/racks/overlaps", params={"room": room}).json()["open"] == 1


# ---------------------------------------------------------------- verify_numbers 不變式
def _run_vn(conn):
    import verify_numbers as vn
    vn.FAILS = 0
    vn.RESULTS.clear()
    vn.check_front_rear(conn)
    return vn.FAILS, [r for r in vn.RESULTS if r["sec"].startswith("機櫃前後各一台")]


def test_verify_numbers_前後各一台不變式_決定前後都過(tmp_path):
    conn, _ = _conn(tmp_path)
    try:
        fails, res = _run_vn(conn)
        assert fails == 0 and len(res) == 5 and all(r["ok"] for r in res)
        room = _room(conn)
        grp = _grp(conn)
        oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", None, target_key=_by_name(conn)["frrear01"])
        fails, res = _run_vn(conn)
        assert fails == 0 and all(r["ok"] for r in res)
        assert any("1 櫃有背面設備" in r["detail"] for r in res)
    finally:
        conn.close()


def _decide_fr(conn):
    room = _room(conn)
    grp = _grp(conn)
    n = _by_name(conn)
    return room, n, oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", "背對背", target_key=n["frrear01"])


def test_位置更正搬走再搬回_舊決定失效_不自動復活_需重新確認(tmp_path):
    conn, _ = _conn(tmp_path)
    try:
        base = _numbers(conn)
        room, n, d1 = _decide_fr(conn)
        assert oq.groups(conn)["open"] == 0 and rl.rack_detail(conn, room, "K1")["power"]["rear_count"] == 1
        # 沒改位置的位置更正（同一個位置）不該讓決定失效
        rl.set_position(conn, n["frrear01"], room, "K1", 10, 2, "位置沒變", "bob")
        assert oq.groups(conn)["open"] == 0 and len(oq.groups(conn)["front_rear"]) == 1
        # 把背面那台搬走 → 決定失效（寫稽核），台數不變
        rl.set_position(conn, n["frrear01"], room, "K1", 30, 2, "搬走", "bob")
        assert oq.groups(conn)["front_rear"] == []
        h = [x for x in oq.history(conn, room) if x["id"] == d1["id"]][0]
        assert h["undone_at"] and h["undo_note"].startswith(oq.STALE_NOTE) and h["undone_by"] == "bob"
        assert _numbers(conn) == base
        assert rl.rack_detail(conn, room, "K1")["power"]["rear_count"] == 0
        # 搬回原位：重疊重新出現，不自動復活，且清單標「請重新確認」
        rl.set_position(conn, n["frrear01"], room, "K1", 10, 2, "搬回", "bob")
        g = oq.groups(conn)
        assert g["open"] == 1 and g["front_rear"] == []
        grp = _grp(conn)
        assert grp["decision"] is None and grp["stale_front_rear"] == oq.STALE_TEXT
        det = {x["name"]: x for x in rl.rack_detail(conn, room, "K1")["devices"]}
        assert det["frfront01"]["overlap_with"] and "side" not in det["frfront01"]
        assert _numbers(conn) == base
        # 重新確認 → 生效，提示消失
        oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", "重新確認", target_key=n["frrear01"])
        g = oq.groups(conn)
        assert g["open"] == 0 and len(g["front_rear"]) == 1 and _grp(conn)["stale_front_rear"] is None
        # 清除位置更正（回到 CIA 位置）也算位置變動
        rl.clear_position(conn, n["frrear01"], "清除", "bob")
        assert oq.groups(conn)["front_rear"] == [] or oq.groups(conn)["open"] == 0
    finally:
        conn.close()


def test_CIA重匯入改位置_讀取時兜底失效_改成別的重疊位置也不放過(tmp_path):
    """不經位置更正、直接改 CIA 的機櫃欄（重匯入）：決定當時存的 U 範圍對不上 → 失效，重疊重新被報。"""
    conn, _ = _conn(tmp_path)
    try:
        base = _numbers(conn)
        room, n, _d = _decide_fr(conn)
        assert oq.groups(conn)["open"] == 0
        conn.execute("UPDATE hardware SET rack_no = 'K1/10U' WHERE asset_serial = 'FR-2'")      # 背面那台 CIA 改成只佔 10U
        conn.commit()
        g = oq.groups(conn)
        assert g["front_rear"] == [] and g["open"] == 1                                       # 仍重疊，但舊決定失效 → 重新出現
        grp = _grp(conn)
        assert grp["decision"] is None and grp["stale_front_rear"] == oq.STALE_TEXT
        d = rl.rack_detail(conn, room, "K1")
        assert d["power"]["rear_count"] == 0 and all("side" not in x for x in d["devices"])
        assert _numbers(conn)["total_devices"] == base["total_devices"]
        # 重新確認（以現在的位置為準）
        oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", None, target_key=n["frrear01"])
        assert oq.groups(conn)["open"] == 0 and rl.rack_detail(conn, room, "K1")["power"]["rear_count"] == 1
    finally:
        conn.close()


def test_三台群組_fr_with只放對面_同面不互為對面(tmp_path):
    """前 10U、前 11U、背 10-12U：兩台正面設備互不出現在對方的 fr_with；背面那台對面是兩台正面；
    背面比正面多佔的 12U 照樣算佔用（已用 U 不漏）。"""
    p = tmp_path / "t3.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        _hw(conn, "T-1", "tfront10", "192.0.2.21", "K1/10U", "Dell R750")
        _hw(conn, "T-2", "tfront11", "192.0.2.22", "K1/11U", "HPE DL380")
        _hw(conn, "T-3", "trear3u", "192.0.2.23", "K1/10-12U", "Cisco C9300-48P")
        conn.commit()
        room = _room(conn)
        grp = next(x for x in oq.groups(conn)["groups"] if len(x["keys"]) == 3)
        n = _by_name(conn)
        oq.decide(conn, room, "K1", grp["keys"], "front_rear", "alice", None, target_key=n["trear3u"])
        d = rl.rack_detail(conn, room, "K1")
        det = {x["name"]: x for x in d["devices"]}
        assert det["tfront10"]["fr_with"] == [n["trear3u"]]
        assert det["tfront11"]["fr_with"] == [n["trear3u"]]
        assert sorted(det["trear3u"]["fr_with"]) == sorted([n["tfront10"], n["tfront11"]])
        assert n["tfront11"] not in det["tfront10"]["fr_with"] and n["tfront10"] not in det["tfront11"]["fr_with"]
        assert (det["tfront10"]["side"], det["tfront11"]["side"], det["trear3u"]["side"]) == ("front", "front", "rear")
        assert d["power"]["used_u"] == 3 and d["power"]["front_rear_shared_u"] == 2     # 10、11 共用；12 只有背面
        assert all(x["overlap_with"] == [] for x in d["devices"])
    finally:
        conn.close()
