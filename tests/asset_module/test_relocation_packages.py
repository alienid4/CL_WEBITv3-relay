"""搬遷包 v1（2026-10-31）：兩層成包（系統為底、強證據才併包、附證據與方向）、完全唯讀。例子 IP 一律 10.99.x。

釘住的事：同系統成包、跨機房標示、強證據合併附證據與方向、單條單向不合併（只列弱證據）、雙向合併、忽略埠、
無連線僅依系統登記、未歸屬一包、VMware（含 vCenter、名稱辨識、判定出錯）不入包、超門檻標示、
台數加總＝成員下鑽筆數、過期連線不合併、唯讀連線（mode=ro）也能算、60 秒快取（清單與明細只算一次、資料變動失效）、
room 必須是既有機房、viewer 可讀不可匯出。
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import access_policy  # noqa: E402
import db  # noqa: E402
import observed_deps as od  # noqa: E402
import relocation_packages as rp  # noqa: E402

ROLE_AWARE_TESTS = True      # 末段的 HTTP 測試要明確指定帳號等級
COLLECTOR = "10.99.200.1"
ROOM = "板橋"
_PW = "s3cure-pass!"          # 測試用假密碼


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(od, "_LOCAL_IPS", set())
    od.invalidate()
    rp.invalidate()
    yield
    od.invalidate()
    rp.invalidate()


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.set_setting(c, "collector_ip", COLLECTOR)
    return c


def _now(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _hw(conn, serial, host, ip, api=None, loc=ROOM, **kw):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, api_id=api, asset_name=host,
                       physical_location=loc, environment=kw.pop("environment", "正式"),
                       asset_status=kw.pop("asset_status", "使用中"), **kw)


def _seed(conn):
    """S（核心：s1 s2）、T（前台：t1 t2）、U（報表：u1）、W（獨立：w1）、一台沒系統的 n1、別的機房還有一台 S。"""
    _hw(conn, "H1", "s1", "10.99.1.1", "S-1")
    _hw(conn, "H2", "s2", "10.99.1.2", "S-1")
    _hw(conn, "H3", "t1", "10.99.2.1", "T-1")
    _hw(conn, "H3B", "t2", "10.99.2.2", "T-1")
    _hw(conn, "H4", "u1", "10.99.3.1", "U-1")
    _hw(conn, "H5", "w1", "10.99.4.1", "W-1")
    _hw(conn, "H6", "n1", "10.99.5.1", None)
    _hw(conn, "H7", "s3", "10.99.6.1", "S-1", loc="敦南")
    for api, name in (("S-1", "核心交易"), ("T-1", "下單前台"), ("U-1", "報表"), ("W-1", "獨立批次")):
        conn.execute("INSERT OR REPLACE INTO business_system (api_id, name) VALUES (?, ?)", (api, name))
    conn.commit()


def _row(conn, host_ip, serial, peer_ip, days_ago=0, port=1521, direction="inbound"):
    od.store(conn, host_ip, serial, [{"direction": direction, "local_port": port if direction == "inbound" else 0,
                                      "peer_ip": peer_ip, "peer_port": 0 if direction == "inbound" else port,
                                      "count": 3}], now=_now(days_ago))
    od.invalidate()
    rp.invalidate()


def _strong(conn):
    """t1、t2 都連進 s1（共 3 條、2 台發起端）＝強證據。"""
    _row(conn, "10.99.1.1", "H1", "10.99.2.1", 1)
    _row(conn, "10.99.1.1", "H1", "10.99.2.1", 1, port=1522)
    _row(conn, "10.99.1.1", "H1", "10.99.2.2", 1)


def _by_name(res):
    return {p["name"]: p for p in res["packages"]}


def _merged(res):
    return [p for p in res["packages"] if p["system_count"] == 2]


def test_同系統成包_跨機房標示_未歸屬一包(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        res = rp.list_packages(conn, ROOM)
        pk = _by_name(res)
        assert pk["核心交易"]["count"] == 2 and pk["核心交易"]["api_ids"] == ["S-1"]
        assert pk["核心交易"]["cross_room"] == 1
        assert pk["核心交易"]["other_rooms"] == [{"room": "敦南", "count": 1}]
        assert pk["核心交易"]["basis"] == "僅依系統登記"
        assert pk[rp.UNASSIGNED]["count"] == 1 and pk[rp.UNASSIGNED]["basis"] == "未歸屬系統"
        assert res["total"] == 7 == sum(p["count"] for p in res["packages"])
    finally:
        conn.close()


def test_強證據才併包_附證據與方向(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _strong(conn)
        res = rp.list_packages(conn, ROOM)
        m = _merged(res)
        assert len(m) == 1 and m[0]["count"] == 4 and m[0]["basis"] == "連線證據"
        d = rp.package_detail(conn, ROOM, m[0]["id"])
        e = [x for x in d["merges"] if x["type"] == "observed_conn"][0]
        assert e["direction"] == "T-1→S-1" and e["evidence_count"] == 3 and e["callers"] == 2
        ev = e["evidence"][0]
        assert ev["flow"] == "T-1→S-1" and ev["from"] in ("t1", "t2") and ev["to"] == "s1" and ev["last_seen"]
        assert sorted(x["hostname"] for x in d["members"]) == ["s1", "s2", "t1", "t2"]
        assert len(d["members"]) == d["count"]
    finally:
        conn.close()


def test_單條單向連線不合併_只列弱證據(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _row(conn, "10.99.1.1", "H1", "10.99.2.1", 1)
        res = rp.list_packages(conn, ROOM)
        assert _merged(res) == []
        core = _by_name(res)["核心交易"]
        assert core["weak_count"] == 1 and "弱證據" in core["merge_summary"]
        d = rp.package_detail(conn, ROOM, core["id"])
        assert d["merges"] == [] and d["weak"][0]["type"] == "weak_conn" and "建議人工確認" in d["weak"][0]["label"]
    finally:
        conn.close()


def test_條數夠但只有一台發起端也不併包(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        for p in (1521, 1522, 1523):
            _row(conn, "10.99.1.1", "H1", "10.99.2.1", 1, port=p)
        assert _merged(rp.list_packages(conn, ROOM)) == []
    finally:
        conn.close()


def test_達標才合併_門檻可調(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _row(conn, "10.99.1.1", "H1", "10.99.2.1", 1)
        _row(conn, "10.99.1.1", "H1", "10.99.2.2", 1)           # 2 條、2 台發起端：預設需 3 條
        assert _merged(rp.list_packages(conn, ROOM)) == []
        db.set_setting(conn, rp.MERGE_MIN_KEY, "2")
        assert len(_merged(rp.list_packages(conn, ROOM))) == 1
        db.set_setting(conn, rp.MERGE_MIN_KEY, "xx")
        assert rp.merge_min(conn) == rp.DEFAULT_MERGE_MIN
    finally:
        conn.close()


def test_雙向都觀察到就合併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _row(conn, "10.99.1.1", "H1", "10.99.2.1", 1)                          # T → S
        _row(conn, "10.99.1.2", "H2", "10.99.2.1", 1, port=8080, direction="outbound")   # S → T
        res = rp.list_packages(conn, ROOM)
        m = _merged(res)
        assert len(m) == 1
        d = rp.package_detail(conn, ROOM, m[0]["id"])
        e = [x for x in d["merges"] if x["type"] == "observed_conn"][0]
        assert e["direction"] == "雙向" and {x["flow"] for x in e["evidence"]} == {"T-1→S-1", "S-1→T-1"}
    finally:
        conn.close()


def test_忽略埠不當證據(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _strong(conn)
        assert len(_merged(rp.list_packages(conn, ROOM))) == 1
        db.set_setting(conn, rp.IGNORE_PORTS_KEY, "1521，1522")
        assert rp.ignore_ports(conn) == {1521, 1522}
        res = rp.list_packages(conn, ROOM)
        assert _merged(res) == [] and res["meta"]["ignore_ports"] == [1521, 1522]
    finally:
        conn.close()


def test_過期連線不合併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        for ip, port, peer in (("10.99.1.1", 1521, "10.99.2.1"), ("10.99.1.1", 1522, "10.99.2.1"), ("10.99.1.1", 1521, "10.99.2.2")):
            _row(conn, ip, "H1", peer, 90, port=port)
        assert _merged(rp.list_packages(conn, ROOM)) == []
    finally:
        conn.close()


def test_連線另一端不在所選機房就不合併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "H8", "t9", "10.99.7.1", "T-1", loc="敦南")
        conn.commit()
        for port in (1521, 1522, 1523):
            _row(conn, "10.99.1.2", "H2", "10.99.7.1", 1, port=port)
        assert _merged(rp.list_packages(conn, ROOM)) == []
    finally:
        conn.close()


def test_共用同一台機器併包(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "H9", "s1", "10.99.1.1", "U-1")      # 同一台 s1 也登記在 U 系統
        res = rp.list_packages(conn, ROOM)
        m = _merged(res)
        assert len(m) == 1 and "共用同一台機器" in m[0]["merge_summary"]
        assert res["total"] == sum(p["count"] for p in res["packages"])
    finally:
        conn.close()


def _hosts(conn, res, name):
    return [m["hostname"] for m in rp.package_detail(conn, ROOM, _by_name(res)[name]["id"])["members"]]


def test_VMware不入包_ESXi與vCenter(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "HV", "esxi01", "10.99.8.1", "W-1", os="VMware ESXi 7.0")
        _hw(conn, "HVC", "vc01", "10.99.8.2", "W-1", os="VMware vCenter Server Appliance (Photon OS)")
        res = rp.list_packages(conn, ROOM)
        assert res["total"] == 7 and res["meta"]["vmware_excluded"] >= 2
        assert _hosts(conn, res, "獨立批次") == ["w1"]
    finally:
        conn.close()


def test_VMware以名稱辨識的ESXi不入包(tmp_path, monkeypatch):
    import onboard_eligibility
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "HN", "hostx", "10.99.8.5", "W-1")            # 登記上看不出是 ESXi，但 vCenter 名單有它
        monkeypatch.setattr(onboard_eligibility, "esxi_host_names", lambda c: frozenset({"HOSTX"}))
        res = rp.list_packages(conn, ROOM)
        assert _hosts(conn, res, "獨立批次") == ["w1"]
    finally:
        conn.close()


def test_VMware判定_沒有可回連序號的機器也看機器層_判定出錯偏保守(tmp_path, monkeypatch):
    import onboard_eligibility
    monkeypatch.setattr(onboard_eligibility, "esxi_host_names", lambda c: frozenset({"GHOST"}))
    conn = _conn(tmp_path)
    try:
        m = {"key": "k1", "serials": ["NO-SUCH-SERIAL"], "name": "ghost", "ip": "10.99.9.9", "_oses": [], "asset_names": [],
             "model": None}
        ok = {"key": "k2", "serials": [], "name": "plain", "ip": "10.99.9.8", "_oses": ["Red Hat Linux"], "asset_names": [], "model": None}
        assert rp._vmware_keys(conn, [m, ok]) == {"k1"}
        monkeypatch.setattr(onboard_eligibility, "judge_for_asset", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert rp._vmware_keys(conn, [m, ok]) == {"k1", "k2"}      # 判定自己出錯：寧可排除
    finally:
        conn.close()


def test_超過門檻標示_門檻可設定(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        assert not any(p["over_threshold"] for p in rp.list_packages(conn, ROOM)["packages"])
        db.set_setting(conn, rp.MAX_KEY, "1")
        res = rp.list_packages(conn, ROOM)
        assert _by_name(res)["核心交易"]["over_threshold"] is True and res["threshold"] == 1
        assert _by_name(res)["獨立批次"]["over_threshold"] is False
        db.set_setting(conn, rp.MAX_KEY, "abc")
        assert rp.threshold(conn) == rp.DEFAULT_MAX
    finally:
        conn.close()


def test_台數加總等於成員下鑽筆數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _strong(conn)
        res = rp.list_packages(conn, ROOM)
        keys = []
        for p in res["packages"]:
            d = rp.package_detail(conn, ROOM, p["id"])
            assert len(d["members"]) == p["count"] and all(m["room"] == ROOM for m in d["members"])
            keys += [m["key"] for m in d["members"]]
        assert len(keys) == len(set(keys)) == res["total"]
        assert set(keys) == rp.active_machine_keys(conn, ROOM)
    finally:
        conn.close()


def test_唯讀連線_mode_ro也能算_且不寫庫(tmp_path):
    """真的沒寫入：資料庫以唯讀開（mode=ro），build 仍成功；連 query_only 的寫入都會被擋。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _strong(conn)
    finally:
        conn.close()
    ro = sqlite3.connect(f"file:{(tmp_path / 't.db').as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("CREATE TABLE zz (a)")                      # 證明這條連線真的寫不進去
        res = rp.list_packages(ro, ROOM)
        assert res["total"] == 7 and len(_merged(res)) == 1
        for p in res["packages"]:
            assert rp.package_detail(ro, ROOM, p["id"]) is not None
    finally:
        ro.close()


def test_既有惰性寫入沒被搬遷包觸發_machines_readonly不建表(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        names = lambda: {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}  # noqa: E731
        import rack_layout
        before = names()
        rack_layout.machines_readonly(conn)
        assert names() == before
        assert not any(r[0] for r in conn.execute("SELECT 1 FROM sqlite_master WHERE name='model_spec'")) or "model_spec" in before
    finally:
        conn.close()


def test_快取_清單與明細只算一次_資料變動後失效(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        w0 = rp.STATS["world"]
        res = rp.list_packages(conn, ROOM)
        for p in res["packages"]:
            rp.package_detail(conn, ROOM, p["id"])
        rp.list_packages(conn, ROOM)
        assert rp.STATS["world"] == w0 + 1
        _hw(conn, "HNEW", "w2", "10.99.4.2", "W-1")             # 資料變動
        res2 = rp.list_packages(conn, ROOM)
        assert rp.STATS["world"] == w0 + 2 and res2["total"] == res["total"] + 1
    finally:
        conn.close()


def test_快取_超過60秒重算(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        rp.list_packages(conn, ROOM)
        w = rp.STATS["world"]
        t = rp.time.monotonic() + rp.CACHE_TTL_S + 1
        monkeypatch.setattr(rp.time, "monotonic", lambda: t)
        rp.list_packages(conn, ROOM)
        assert rp.STATS["world"] == w + 1
    finally:
        conn.close()


def test_room必須是既有機房(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        assert rp.rooms(conn) == sorted({"板橋", "敦南"})
        with pytest.raises(rp.UnknownRoom):
            rp.list_packages(conn, "不存在")
        with pytest.raises(rp.UnknownRoom):
            rp.package_detail(conn, "不存在", "PK-x")
    finally:
        conn.close()


def test_verify_numbers_搬遷包不變式通過(tmp_path):
    import verify_numbers as vn
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _strong(conn)
        before, checks = vn.FAILS, vn.CHECKS
        vn.check_relocation_packages(conn)
        assert vn.CHECKS == checks + 4 and vn.FAILS == before
    finally:
        conn.close()


def test_權限_viewer可讀不可匯出():
    assert access_policy.level_for("GET", "/api/relocation/packages") == access_policy.VIEWER
    assert access_policy.level_for("GET", "/api/relocation/packages/{package_id}") == access_policy.VIEWER
    assert access_policy.level_for("GET", "/api/relocation/export") != access_policy.VIEWER


def test_HTTP_viewer可讀_機房不存在404白話_沒給room400(tmp_path):
    import api
    import auth
    from fastapi.testclient import TestClient

    p = tmp_path / "h.db"
    db.init_db(p)

    def _override():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    c = db.get_connection(p)
    try:
        db.create_user(c, "vera", auth.hash_password(_PW), role="viewer")
        _seed(c)
    finally:
        c.close()
    try:
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "vera", "password": _PW}).status_code == 200
        ok = cl.get("/api/relocation/packages", params={"room": ROOM})
        assert ok.status_code == 200 and ok.json()["total"] == 7
        pid = ok.json()["packages"][0]["id"]
        assert cl.get(f"/api/relocation/packages/{pid}", params={"room": ROOM}).status_code == 200
        bad = cl.get("/api/relocation/packages", params={"room": "不存在"})
        assert bad.status_code == 404 and "查無機房" in bad.json()["detail"]
        assert cl.get(f"/api/relocation/packages/{pid}", params={"room": "不存在"}).status_code == 404
        assert cl.get("/api/relocation/packages").status_code == 400
        assert cl.get("/api/relocation/packages/PK-nope", params={"room": ROOM}).status_code == 404
    finally:
        api.app.dependency_overrides.clear()
