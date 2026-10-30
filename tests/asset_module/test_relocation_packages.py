"""搬遷包 v1（2026-10-31）：兩層成包（系統為底、連線合併附證據）、唯讀。例子 IP 一律 10.99.x。

釘住的事：同系統成包、跨機房標示、連線合併附證據、無連線僅依系統登記、未歸屬系統一包、
VMware 不入包、超門檻標示、台數加總＝成員下鑽筆數、過期連線不合併、viewer 可讀不可匯出、不改動任何既有資料。
"""
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

COLLECTOR = "10.99.200.1"
ROOM = "板橋"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(od, "_LOCAL_IPS", set())
    od.invalidate()
    yield
    od.invalidate()


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
    """S（核心：s1 s2）、T（前台：t1）、U（報表：u1）、W（獨立：w1）、一台沒系統的 n1、別的機房還有一台 S。"""
    _hw(conn, "H1", "s1", "10.99.1.1", "S-1")
    _hw(conn, "H2", "s2", "10.99.1.2", "S-1")
    _hw(conn, "H3", "t1", "10.99.2.1", "T-1")
    _hw(conn, "H4", "u1", "10.99.3.1", "U-1")
    _hw(conn, "H5", "w1", "10.99.4.1", "W-1")
    _hw(conn, "H6", "n1", "10.99.5.1", None)
    _hw(conn, "H7", "s3", "10.99.6.1", "S-1", loc="敦南")
    for api, name in (("S-1", "核心交易"), ("T-1", "下單前台"), ("U-1", "報表"), ("W-1", "獨立批次")):
        conn.execute("INSERT OR REPLACE INTO business_system (api_id, name) VALUES (?, ?)", (api, name))
    conn.commit()


def _conn_row(conn, host_ip, serial, peer_ip, days_ago=0, port=1521):
    """host 收到 peer 連進來（inbound）→ peer 的系統依賴 host 的系統。"""
    od.store(conn, host_ip, serial, [{"direction": "inbound", "local_port": port, "peer_ip": peer_ip,
                                      "peer_port": 0, "count": 3}], now=_now(days_ago))
    od.invalidate()


def _by_name(res):
    return {p["name"]: p for p in res["packages"]}


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
        assert res["total"] == 6 == sum(p["count"] for p in res["packages"])
    finally:
        conn.close()


def test_連線合併附證據(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _conn_row(conn, "10.99.1.1", "H1", "10.99.2.1", days_ago=1)       # t1 → s1
        res = rp.list_packages(conn, ROOM)
        merged = [p for p in res["packages"] if p["system_count"] == 2]
        assert len(merged) == 1 and merged[0]["count"] == 3 and merged[0]["basis"] == "連線證據"
        d = rp.package_detail(conn, ROOM, merged[0]["id"])
        m = [x for x in d["merges"] if x["type"] == "observed_conn"][0]
        assert sorted(m["apis"]) == ["S-1", "T-1"] and m["evidence_count"] == 1
        ev = m["evidence"][0]
        assert {ev["host"], ev["peer"]} == {"s1", "t1"} and ev["port"] == 1521 and ev["last_seen"]
        assert sorted(x["hostname"] for x in d["members"]) == ["s1", "s2", "t1"]
        assert len(d["members"]) == d["count"]
    finally:
        conn.close()


def test_過期連線不合併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _conn_row(conn, "10.99.1.1", "H1", "10.99.2.1", days_ago=90)
        res = rp.list_packages(conn, ROOM)
        assert all(p["system_count"] <= 1 for p in res["packages"])
    finally:
        conn.close()


def test_連線另一端不在所選機房就不合併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "H8", "t9", "10.99.7.1", "T-1", loc="敦南")
        conn.commit()
        _conn_row(conn, "10.99.1.2", "H2", "10.99.7.1", days_ago=1)       # 敦南的 t9 → s2：t9 不在板橋
        res = rp.list_packages(conn, ROOM)
        assert all(p["system_count"] <= 1 for p in res["packages"])
    finally:
        conn.close()


def test_共用同一台機器併包(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "H9", "s1", "10.99.1.1", "U-1")      # 同一台 s1 也登記在 U 系統
        res = rp.list_packages(conn, ROOM)
        m = [p for p in res["packages"] if p["system_count"] == 2]
        assert len(m) == 1 and "共用同一台機器" in m[0]["merge_summary"]
        assert res["total"] == sum(p["count"] for p in res["packages"])
    finally:
        conn.close()


def test_VMware不入包(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw(conn, "HV", "esxi01", "10.99.8.1", "W-1", os="VMware ESXi 7.0")
        res = rp.list_packages(conn, ROOM)
        assert res["total"] == 6 and res["meta"]["vmware_excluded"] >= 1
        d = rp.package_detail(conn, ROOM, _by_name(res)["獨立批次"]["id"])
        assert [m["hostname"] for m in d["members"]] == ["w1"]
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
        _conn_row(conn, "10.99.1.1", "H1", "10.99.2.1", days_ago=1)
        res = rp.list_packages(conn, ROOM)
        keys = []
        for p in res["packages"]:
            d = rp.package_detail(conn, ROOM, p["id"])
            assert len(d["members"]) == p["count"]
            keys += [m["key"] for m in d["members"]]
        assert len(keys) == len(set(keys)) == res["total"]
    finally:
        conn.close()


def test_唯讀_不改動任何既有資料(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _conn_row(conn, "10.99.1.1", "H1", "10.99.2.1", days_ago=1)

        def snap():
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                "AND name NOT LIKE 'sqlite_%'")]
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in names}
        biz = ("hardware", "host_connection", "personnel", "business_system", "app_settings")
        first = snap()
        rp.list_packages(conn, ROOM)       # 暖機：rack_layout.machines 既有的惰性建表／塞型號規格不算搬遷包寫的
        warm = snap()
        assert all(warm[t] == first[t] for t in biz)       # 業務資料一筆都沒動
        for p in rp.list_packages(conn, ROOM)["packages"]:
            rp.package_detail(conn, ROOM, p["id"])
        assert snap() == warm                              # 之後再怎麼查都不再有任何變動
    finally:
        conn.close()


def test_verify_numbers_搬遷包不變式通過(tmp_path):
    import verify_numbers as vn
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _conn_row(conn, "10.99.1.1", "H1", "10.99.2.1", days_ago=1)
        before, checks = vn.FAILS, vn.CHECKS
        vn.check_relocation_packages(conn)
        assert vn.CHECKS == checks + 3 and vn.FAILS == before
    finally:
        conn.close()


def test_權限_viewer可讀不可匯出():
    assert access_policy.level_for("GET", "/api/relocation/packages") == access_policy.VIEWER
    assert access_policy.level_for("GET", "/api/relocation/packages/{package_id}") == access_policy.VIEWER
    # 匯出走前端 V2Table（viewer 沒有鈕）；既有的搬遷盤點表匯出仍是管理員
    assert access_policy.level_for("GET", "/api/relocation/export") != access_policy.VIEWER
