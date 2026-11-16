"""主機關聯探索（工作包 B）：只讀鄰居 API 的資料正規化、分頁 snapshot、權限與旗標。

對應規格第 10 節：P0 01～09、11～13、16（UI／動畫／模擬的 P0 10、14、15 在工作包 C／D）。
全部是虛構資料：主機 host-a／gw01／db-a／mq-a，IP 一律 10.99.x。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_relations as hr  # noqa: E402
import observed_deps as od  # noqa: E402

COLLECTOR = "10.99.200.1"
A, GW, DBA, MQ, UNK, AMB = "10.99.1.10", "10.99.1.1", "10.99.1.20", "10.99.1.30", "10.99.2.20", "10.99.3.3"


def _now(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _hw(conn, serial, host, ip, api=None):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, api_id=api, environment="正式")


def _out(peer, port, n=1):
    return {"direction": "outbound", "local_port": 0, "peer_ip": peer, "peer_port": port, "count": n}


def _in(peer, port, n=1):
    return {"direction": "inbound", "local_port": port, "peer_ip": peer, "peer_port": 0, "count": n}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(od, "_LOCAL_IPS", set())
    od.invalidate()
    hr._reset_snapshots()
    yield
    od.invalidate()
    hr._reset_snapshots()


def _conn(tmp_path, flag=True):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.set_setting(c, "collector_ip", COLLECTOR)
    if flag:
        c.execute("UPDATE feature_flags SET enabled = 1 WHERE module_key = ?", (hr.FLAG_KEY,))
    c.commit()
    return c


def _seed(conn):
    _hw(conn, "HW-A", "host-a", A, "S-1")
    _hw(conn, "HW-G", "gw01", GW, "S-2")
    _hw(conn, "HW-D", "db-a", DBA, "S-3")
    _hw(conn, "HW-M", "mq-a", MQ, "S-4")
    _hw(conn, "HW-X1", "hostx1", AMB, "S-5")      # 同一個 IP 登記在兩台、APID 不同 → 模稜兩可
    _hw(conn, "HW-X2", "hostx2", AMB, "S-6")
    conn.commit()
    now = _now()
    od.store(conn, A, "HW-A", [
        _out(GW, 443, 3), _out(DBA, 1521, 2), _out(MQ, 5672), _out(MQ, 5671), _out(UNK, 8443),
        _in(AMB, 8080), _in("127.0.0.1", 22), _in(COLLECTOR, 22), _out(GW, 53)], now=now)
    od.store(conn, DBA, "HW-D", [_in(A, 1521, 4)], now=now)
    od.store(conn, GW, "HW-G", [_out(MQ, 5672), _out(A, 9000)], now=now)


def _key(conn, serial):
    import system_stats
    r = conn.execute("SELECT hostname, ip, asset_serial, asset_status FROM hardware WHERE asset_serial = ?",
                     (serial,)).fetchone()
    return hr.host_node_id(system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]))


def _edges(res):
    return {(e["source_id"], e["target_id"], e["destination_port"]): e for e in res["edges"]}


# ═══ 旗標 ═════════════════════════════════════════════════════════════

def test_旗標預設關_關著就擋(tmp_path):
    conn = _conn(tmp_path, flag=False)
    _seed(conn)
    row = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = ?", (hr.FLAG_KEY,)).fetchone()
    assert row is not None and row[0] == 0
    with pytest.raises(hr.NotEnabled):
        hr.neighbors(conn, _key(conn, "HW-A"))


# ═══ P0 01／05：一跳、Port、多埠、兩端各觀測 ════════════════════════════

def test_P0_01_出站一跳_鄰居與Port(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a = _key(conn, "HW-A")
    res = hr.neighbors(conn, a, "outbound")
    e = _edges(res)
    g, d, m = _key(conn, "HW-G"), _key(conn, "HW-D"), _key(conn, "HW-M")
    assert set(e) == {(a, g, 443), (a, d, 1521), (a, m, 5672), (a, m, 5671),
                      (a, hr.endpoint_node_id(UNK), 8443)}
    assert all(x["source_id"] == a for x in res["edges"])          # 只有一跳、都從起點出去
    assert all(x["destination_port"] for x in res["edges"])        # 沒有 TCP/0
    assert e[(a, g, 443)]["protocol"] == "tcp" and e[(a, g, 443)]["direction_status"] == "inferred"
    assert res["root_node_id"] == a and res["completeness"]["status"] == "complete"


def test_P0_05_同端點多埠不遺失_兩端觀測合併為筆次(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a, d, m = _key(conn, "HW-A"), _key(conn, "HW-D"), _key(conn, "HW-M")
    e = _edges(hr.neighbors(conn, a, "outbound"))
    assert (a, m, 5672) in e and (a, m, 5671) in e
    ad = e[(a, d, 1521)]
    assert ad["observation_sides"] == "both"
    assert ad["observations"] == 2 + 4                               # 兩端各算，不去重
    assert {r["collector_ip"] for r in ad["source_refs"]} == {A, DBA}
    assert e[(a, m, 5672)]["observation_sides"] == "single"


# ═══ P0 03：入站目的埠取 local_port ═══════════════════════════════════

def test_P0_03_入站取local_port_出站取peer_port(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a, d = _key(conn, "HW-A"), _key(conn, "HW-D")
    inb = _edges(hr.neighbors(conn, d, "inbound"))
    assert set(inb) == {(a, d, 1521)}
    out = _edges(hr.neighbors(conn, d, "outbound"))
    assert out == {}                                                  # 回應封包不會被當成反向的邊


def test_P0_02_共用節點與循環_各自是有向邊(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a, g, m = _key(conn, "HW-A"), _key(conn, "HW-G"), _key(conn, "HW-M")
    both = _edges(hr.neighbors(conn, g, "both"))
    assert (a, g, 443) in both and (g, a, 9000) in both and (g, m, 5672) in both
    node_ids = [n["node_id"] for n in hr.neighbors(conn, g, "both")["nodes"]]
    assert len(node_ids) == len(set(node_ids))


# ═══ P0 04：0 哨兵 ═══════════════════════════════════════════════════

def test_P0_04_目的埠0_方向未知_只在雙向回(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    conn.execute("INSERT INTO host_connection (host_ip, asset_serial, direction, local_port, peer_ip, peer_port, "
                 "count, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                 (A, "HW-A", "outbound", 0, "10.99.4.4", 0, 1, _now(), _now()))
    conn.commit()
    a = _key(conn, "HW-A")
    assert all(e["destination_port"] for e in hr.neighbors(conn, a, "outbound")["edges"])
    hr._reset_snapshots()
    unk = [e for e in hr.neighbors(conn, a, "both")["edges"] if e["direction_status"] == "uncertain"]
    assert len(unk) == 1 and unk[0]["destination_port"] is None and "~" in unk[0]["edge_id"]


# ═══ P0 06／07／08：身分、未納管、沒有下一層 ═══════════════════════════

def test_P0_06_同IP多台_保留endpoint不合併(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a = _key(conn, "HW-A")
    res = hr.neighbors(conn, a, "inbound")
    nodes = {n["node_id"]: n for n in res["nodes"]}
    amb = nodes[hr.endpoint_node_id(AMB)]
    assert amb["entity_type"] == "endpoint" and amb["identity"] == {"status": "ambiguous", "candidate_count": 2}
    assert amb["management"]["status"] == "unknown"
    assert _key(conn, "HW-X1") not in nodes and _key(conn, "HW-X2") not in nodes


def test_P0_07_未納管端點_虛線語意與原因(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    res = hr.neighbors(conn, _key(conn, "HW-A"), "outbound")
    n = {x["node_id"]: x for x in res["nodes"]}[hr.endpoint_node_id(UNK)]
    assert n["entity_type"] == "endpoint" and n["machine_key"] is None
    assert n["management"] == {"status": "unmanaged", "reason_code": "not_in_inventory", "source_ref": "inventory",
                               "checked_at": None, "detail": None}
    assert n["health"] is None


def test_P0_08_展開未納管端點_只回既有證據_不寫庫不連線(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed(conn)
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("不可以連線或執行指令")
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    before = conn.total_changes
    res = hr.neighbors(conn, hr.endpoint_node_id(UNK), "outbound")
    assert res["edges"] == [] and res["completeness"]["reasons"] == ["endpoint_one_sided"]
    hr._reset_snapshots()
    both = hr.neighbors(conn, hr.endpoint_node_id(UNK), "both")
    assert [e["source_id"] for e in both["edges"]] == [_key(conn, "HW-A")]
    assert conn.total_changes == before


def test_身分確認後提示改用主機節點(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    res = hr.neighbors(conn, hr.endpoint_node_id(DBA), "both")
    assert "identity_resolved" in res["warnings"] and res["resolved_as"] == _key(conn, "HW-D")


# ═══ P0 09／11：未收集、截斷、排除統計 ═══════════════════════════════════

def test_P0_09_起點沒有收集過_partial不是空(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    res = hr.neighbors(conn, _key(conn, "HW-M"), "outbound")
    assert res["edges"] == []
    assert res["completeness"]["status"] == "partial" and "root_not_collected" in res["completeness"]["reasons"]
    assert res["coverage"]["collection_status"] == "not_collected"


def test_P0_09_有收集而且真的沒有_是完整的空(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    res = hr.neighbors(conn, _key(conn, "HW-D"), "outbound")
    assert res["edges"] == [] and res["completeness"]["status"] == "complete"


def test_P0_11_到上限_標partial與截斷(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed(conn)
    monkeypatch.setattr(od, "MAX_ROWS_PER_HOST", 3)
    res = hr.neighbors(conn, _key(conn, "HW-A"), "outbound")
    assert res["completeness"]["truncated"] is True and "source_truncated" in res["completeness"]["reasons"]
    assert res["coverage"]["truncated"] is True


def test_P0_11_排除原因都有計數_加總等於範圍內列數(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a = _key(conn, "HW-A")
    for d in hr.DIRECTIONS:
        hr._reset_snapshots()
        s = hr.neighbors(conn, a, d)["excluded_summary"]
        assert s["shown_rows"] + sum(s[k] for k in hr.EXCLUDE_REASONS) == s["scope_rows"]
    hr._reset_snapshots()
    s = hr.neighbors(conn, a, "both")["excluded_summary"]
    assert s["ignored"] == 1 and s["collector"] == 1 and s["infra"] == 1      # 127.0.0.1、收集器、DNS:53


# ═══ P0 12／13：分頁、snapshot、游標 ══════════════════════════════════

def test_P0_13_分頁不重不漏_游標綁定條件(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a = _key(conn, "HW-A")
    p1 = hr.neighbors(conn, a, "outbound", limit=2)
    assert p1["has_more"] and len(p1["edges"]) == 2 and p1["total_edges"] == 5
    seen = [e["edge_id"] for e in p1["edges"]]
    cur = p1["next_cursor"]
    while cur:
        p = hr.neighbors(conn, a, "outbound", limit=2, cursor=cur)
        assert p["snapshot_id"] == p1["snapshot_id"]
        seen += [e["edge_id"] for e in p["edges"]]
        cur = p["next_cursor"]
    assert len(seen) == len(set(seen)) == 5
    with pytest.raises(hr.BadCursor):
        hr.neighbors(conn, a, "inbound", limit=2, cursor=p1["next_cursor"])


def test_P0_16_游標竄改_拒絕(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a = _key(conn, "HW-A")
    cur = hr.neighbors(conn, a, "outbound", limit=1)["next_cursor"]
    body, sig = cur.split(".")
    for bad in (body + "x." + sig, body + "." + sig[:-1] + ("A" if sig[-1] != "A" else "B"), "abc", "a.b.c"):
        with pytest.raises(hr.BadCursor):
            hr.neighbors(conn, a, "outbound", limit=1, cursor=bad)


def test_P0_13_資料變動_同版本分頁照舊_查新節點要求重讀(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a, g = _key(conn, "HW-A"), _key(conn, "HW-G")
    p1 = hr.neighbors(conn, a, "outbound", limit=2)
    od.store(conn, A, "HW-A", [_out("10.99.7.7", 8080)], now=_now())
    p2 = hr.neighbors(conn, a, "outbound", limit=2, cursor=p1["next_cursor"])
    assert p2["total_edges"] == 5                                      # 同一版本，不混新資料
    with pytest.raises(hr.SnapshotStale):
        hr.neighbors(conn, g, "outbound", snapshot_id=p1["snapshot_id"])
    fresh = hr.neighbors(conn, a, "outbound")
    assert fresh["total_edges"] == 6 and fresh["snapshot_id"] != p1["snapshot_id"]


def test_P0_13_snapshot逾時_回expired(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed(conn)
    a = _key(conn, "HW-A")
    p1 = hr.neighbors(conn, a, "outbound", limit=2)
    t = hr._now_mono()
    monkeypatch.setattr(hr, "_now_mono", lambda: t + hr.SNAPSHOT_TTL_SEC + 1)
    with pytest.raises(hr.SnapshotExpired):
        hr.neighbors(conn, a, "outbound", limit=2, cursor=p1["next_cursor"])


# ═══ P0 16：參數與節點 ID ═════════════════════════════════════════════

@pytest.mark.parametrize("bad", ["", "x", "h:", "e:default:4:10.99.1", "e:other:4:10.99.1.1", "e:default:6:10.99.1.1",
                                 "e:default:4:010.99.1.1", "<script>alert(1)</script>", "h:" + "x" * 400])
def test_P0_16_節點ID格式不對(tmp_path, bad):
    conn = _conn(tmp_path)
    with pytest.raises(hr.BadParam):
        hr.neighbors(conn, bad)


def test_不存在的主機_404(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    with pytest.raises(hr.NodeNotFound):
        hr.neighbors(conn, "h:no-such-machine")


def test_參數驗證(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    a = _key(conn, "HW-A")
    for kw in ({"direction": "up"}, {"limit": 0}, {"limit": 51}):
        with pytest.raises(hr.BadParam):
            hr.neighbors(conn, a, **kw)
    with pytest.raises(hr.BadParam):
        hr.normalize_filters(protocol="udp")
    with pytest.raises(hr.BadParam):
        hr.normalize_filters(port=[0])
    res = hr.neighbors(conn, a, "outbound", filters=hr.normalize_filters(port=[5672, 5671]))
    assert {e["destination_port"] for e in res["edges"]} == {5671, 5672}


# ═══ 時間與納管狀態 ═══════════════════════════════════════════════════

def test_時間_台北時間才換算UTC_舊資料不猜():
    assert hr.ts_view("2026-11-05 10:00:00") == {"raw": "2026-11-05 10:00:00", "utc": "2026-11-05T02:00:00Z",
                                                  "time_quality": "taipei"}
    assert hr.ts_view("2026-10-25 10:00:00") == {"raw": "2026-10-25 10:00:00", "utc": None, "time_quality": "unknown"}
    assert hr.ts_view("2026-10-25 10:00:00", "Asia/Taipei")["utc"] == "2026-10-25T02:00:00Z"
    assert hr.ts_view(None) is None


def test_納管狀態算不出來_標未知不偽裝(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed(conn)
    import manage_state

    def _fail(_c):
        raise RuntimeError("boom")
    monkeypatch.setattr(manage_state, "summarize", _fail)
    res = hr.neighbors(conn, _key(conn, "HW-A"), "outbound")
    root = {n["node_id"]: n for n in res["nodes"]}[_key(conn, "HW-A")]
    assert root["management"]["status"] == "unknown"
    assert "management_unavailable" in res["warnings"] and res["completeness"]["status"] == "partial"


def test_資料偏舊_標stale_關聯保留(tmp_path):
    conn = _conn(tmp_path)
    _hw(conn, "HW-A", "host-a", A, "S-1")
    _hw(conn, "HW-G", "gw01", GW, "S-2")
    conn.commit()
    od.store(conn, A, "HW-A", [_out(GW, 443)], now=_now(20))
    res = hr.neighbors(conn, _key(conn, "HW-A"), "outbound")
    assert len(res["edges"]) == 1 and res["edges"][0]["freshness"] == "stale" and "stale_data" in res["warnings"]
    assert res["edges"][0]["source_refs"][0]["observations"] == 1


# ═══ API：權限、旗標、參數白名單 ═══════════════════════════════════════

@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    import api
    import auth
    from fastapi.testclient import TestClient

    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    db_path = tmp_path / "t.db"
    db.init_db(db_path)

    def _override_get_db():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    c = db.get_connection(db_path)
    db.set_setting(c, "collector_ip", COLLECTOR)
    db.create_user(c, "vera", auth.hash_password("s3cure-pass!"), role="viewer")
    _seed(c)
    key = _key(c, "HW-A")
    c.close()

    class E:
        anon = TestClient(api.app)

        def viewer(self):
            cl = TestClient(api.app)
            assert cl.post("/api/auth/login", json={"username": "vera", "password": "s3cure-pass!"}).status_code == 200
            return cl

        def flag(self, on):
            cc = db.get_connection(db_path)
            cc.execute("UPDATE feature_flags SET enabled = ? WHERE module_key = ?", (1 if on else 0, hr.FLAG_KEY))
            cc.commit()
            cc.close()

    e = E()
    e.key = key
    yield e
    api.app.dependency_overrides.clear()


URL = "/api/host-relations/neighbors"


def test_API_未登入401_旗標關404_開了viewer可讀(api_env):
    assert api_env.anon.get(URL, params={"node_id": api_env.key}).status_code == 401
    v = api_env.viewer()
    r = v.get(URL, params={"node_id": api_env.key})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_enabled"
    api_env.flag(True)
    r = v.get(URL, params={"node_id": api_env.key, "direction": "outbound"})
    assert r.status_code == 200, r.text
    assert r.json()["total_edges"] == 5 and r.json()["request_id"]


def test_API_不認得的參數與壞參數_422_不反射輸入(api_env):
    api_env.flag(True)
    v = api_env.viewer()
    r = v.get(URL, params={"node_id": api_env.key, "evil": "1"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_param"
    r = v.get(URL, params={"node_id": "<img src=x onerror=1>"})
    assert r.status_code == 422 and "<img" not in r.text
    r = v.get(URL, params={"node_id": api_env.key, "limit": "abc"})
    assert r.status_code == 422
    r = v.get(URL, params={"node_id": api_env.key, "cursor": "a.b"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_cursor"


# ═══ verify_numbers 不變式（部署前用正式庫跑） ═════════════════════════

def test_verify_numbers_主機關聯不變式_種子資料全綠(tmp_path):
    import verify_numbers as vn

    conn = _conn(tmp_path, flag=False)          # 旗標關著也要驗得到
    _seed(conn)
    fails0, checks0 = vn.FAILS, vn.CHECKS
    vn.check_host_relations(conn)
    assert vn.CHECKS - checks0 == 7 and vn.FAILS == fails0


# ═══ 審查補測：每條排除規則、退役、共用 IP、服務名稱 ═══════════════════════

def _base(tmp_path):
    conn = _conn(tmp_path)
    _hw(conn, "HW-A", "host-a", A, "S-1")
    _hw(conn, "HW-G", "gw01", GW, "S-2")
    conn.commit()
    return conn


def test_排除_跳板SSH_admin_peers(tmp_path):
    conn = _base(tmp_path)
    db.set_setting(conn, od.ADMIN_PEERS_KEY, "10.99.8.0/24")
    od.store(conn, A, "HW-A", [_in("10.99.8.8", 22), _out(GW, 443)], now=_now())
    s = hr.neighbors(conn, _key(conn, "HW-A"), "both")["excluded_summary"]
    assert s["admin_ssh"] == 1 and s["shown_rows"] == 1


def test_排除_排除清單網段(tmp_path):
    conn = _base(tmp_path)
    db.set_setting(conn, od.EXCLUDE_KEY, "10.99.9.0/24")
    od.store(conn, A, "HW-A", [_out("10.99.9.9", 8080), _out(GW, 443)], now=_now())
    res = hr.neighbors(conn, _key(conn, "HW-A"), "outbound")
    assert res["excluded_summary"]["excluded"] == 1 and len(res["edges"]) == 1


def test_排除_自動判定管理SSH(tmp_path, monkeypatch):
    conn = _base(tmp_path)
    od.store(conn, A, "HW-A", [_in("10.99.8.9", 22), _out(GW, 443)], now=_now())
    real = od.infer

    def _fake(c, use_cache=True):
        r = dict(real(c, use_cache))
        r["auto_admin_ssh"] = [{"ip": "10.99.8.9"}]
        return r
    monkeypatch.setattr(od, "infer", _fake)
    s = hr.neighbors(conn, _key(conn, "HW-A"), "both")["excluded_summary"]
    assert s["admin_ssh_auto"] == 1 and s["shown_rows"] == 1


def test_排除_同一台_次要IP(tmp_path):
    conn = _base(tmp_path)
    conn.execute("INSERT INTO asset_nic (asset_serial, ip) VALUES (?, ?)", ("HW-A", "10.99.1.11"))
    conn.commit()
    od.store(conn, A, "HW-A", [_out("10.99.1.11", 8080), _out(GW, 443)], now=_now())
    s = hr.neighbors(conn, _key(conn, "HW-A"), "outbound")["excluded_summary"]
    assert s["same_machine"] == 1 and s["shown_rows"] == 1


def test_退役機器收的列_不算到接手同IP的新機器(tmp_path):
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="HW-OLD", hostname="oldbox", ip="10.99.5.5", api_id="S-7",
                       environment="正式", asset_status="停用")
    _hw(conn, "HW-NEW", "newbox", "10.99.5.5", "S-8")
    _hw(conn, "HW-P", "peer1", "10.99.5.6", "S-9")
    conn.commit()
    od.store(conn, "10.99.5.5", "HW-OLD", [_out("10.99.5.6", 1521)], now=_now())
    res = hr.neighbors(conn, _key(conn, "HW-NEW"), "outbound")
    assert res["edges"] == [] and res["excluded_summary"]["retired_collector"] == 1


def test_共用IP解析到別台_計入other_node不安靜消失(tmp_path):
    conn = _conn(tmp_path)
    _hw(conn, "HW-HA", "ha", "10.99.6.1", "S-1")
    _hw(conn, "HW-HB", "hb", "10.99.6.2", "S-1")
    _hw(conn, "HW-HC", "hc", "10.99.6.3", "S-2")
    conn.commit()
    for s_ in ("HW-HA", "HW-HB"):
        conn.execute("INSERT INTO asset_nic (asset_serial, ip) VALUES (?, ?)", (s_, "10.99.6.100"))
    conn.commit()
    od.store(conn, "10.99.6.3", "HW-HC", [_out("10.99.6.100", 443)], now=_now())
    od.store(conn, "10.99.6.1", "HW-HA", [_out("10.99.6.3", 8080)], now=_now())
    s = hr.neighbors(conn, _key(conn, "HW-HA"), "both")["excluded_summary"]
    assert s["fetched_rows"] == 2 and s["other_node"] == 1 and s["shown_rows"] == 1
    assert s["scope_rows"] + s["retired_collector"] + s["other_node"] + s["other_direction"] + s["port_filtered"] \
        == s["fetched_rows"]


def test_服務名稱_有行程名用行程名_沒有用常見埠號_都沒有標未知(tmp_path):
    conn = _base(tmp_path)
    conn.execute("INSERT INTO host_service (ip, asset_serial, proto, port, process, source, last_seen) "
                 "VALUES (?,?,?,?,?,?,?)", (GW, "HW-G", "tcp", 443, "nginx", "ssh_ss", _now()))
    conn.commit()
    od.store(conn, A, "HW-A", [_out(GW, 443), _out(GW, 21), _out(GW, 47123)], now=_now())
    e = {x["destination_port"]: x["service"] for x in hr.neighbors(conn, _key(conn, "HW-A"), "outbound")["edges"]}
    assert e[443]["name"] == "nginx" and e[443]["basis"] == "listening_process"
    assert e[21] == {"name": "FTP", "basis": "common_port"}
    assert e[47123] == {"name": None, "basis": None}


def test_證據列有上限_總數照實(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed(conn)
    monkeypatch.setattr(hr, "MAX_REFS_PER_EDGE", 1)
    e = _edges(hr.neighbors(conn, _key(conn, "HW-A"), "outbound"))
    ad = e[(_key(conn, "HW-A"), _key(conn, "HW-D"), 1521)]
    assert len(ad["source_refs"]) == 1 and ad["source_refs_total"] == 2 and ad["observations"] == 6


def test_身分口徑改了_同一個snapshot查新節點要求重讀(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed(conn)
    p1 = hr.neighbors(conn, _key(conn, "HW-A"), "outbound")
    monkeypatch.setattr(od, "_identity_state", lambda: ("changed",))
    with pytest.raises(hr.SnapshotStale):
        hr.neighbors(conn, _key(conn, "HW-G"), "outbound", snapshot_id=p1["snapshot_id"])
