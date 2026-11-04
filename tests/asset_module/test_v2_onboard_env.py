"""2026-10-29 6.1 待納管每列帶環境別（使用者：「環境可選 正式 測試」）。全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import v2_onboard  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seg(conn, cidr, env):
    import ipaddress
    n = ipaddress.ip_network(cidr)
    conn.execute("INSERT INTO network_segment (raw_cidr, cidr, net_start, net_end, environment) VALUES (?,?,?,?,?)",
                 (cidr, cidr, int(n.network_address), int(n.broadcast_address), env))


def test_環境別_資產登記優先_沒登記看網段_小段優先_都沒有是未分類(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seg(conn, "10.99.0.0/16", "正式")
        _seg(conn, "10.99.3.0/24", "UAT")                      # 小段優先；UAT 併進「測試」（environment_groups.json）
        db.insert_hardware(conn, asset_serial="E-1", hostname="a1", ip="10.99.9.1", environment="測試")
        conn.commit()
        rows = [{"ip": "10.99.9.1"}, {"ip": "10.99.3.7"}, {"ip": "10.99.8.8"}, {"ip": "172.16.0.1"}, {"ip": "bad"}, {"ip": None}]
        m = v2_onboard.environment_map(conn, rows)
        assert m["10.99.9.1"] == "測試"                          # 資產登記的環境（雖然網段是正式）
        assert m["10.99.3.7"] == "測試"                          # 小段 /24（UAT→測試），不是 /16 的正式
        assert m["10.99.8.8"] == "正式"                          # 只落在 /16
        assert m["172.16.0.1"] == "未分類" and m["bad"] == "未分類"
        assert v2_onboard.environment_map(conn, []) == {}
    finally:
        conn.close()


def test_沒有網段配置表資料_也不炸(tmp_path):
    conn = _conn(tmp_path)
    try:
        conn.execute("DROP TABLE network_segment")
        assert v2_onboard.environment_map(conn, [{"ip": "10.99.1.1"}]) == {"10.99.1.1": "未分類"}
    finally:
        conn.close()


def test_機房_資產登記優先_沒登記看網段_兩邊都沒有是未知(tmp_path):
    conn = _conn(tmp_path)
    try:
        import ipaddress
        n = ipaddress.ip_network("10.99.5.0/24")
        conn.execute("INSERT INTO network_segment (raw_cidr, cidr, net_start, net_end, environment, location) VALUES (?,?,?,?,?,?)",
                     ("10.99.5.0/24", "10.99.5.0/24", int(n.network_address), int(n.broadcast_address), "正式", "01_甲機房"))
        db.insert_hardware(conn, asset_serial="R-1", hostname="r1", ip="10.99.9.9", physical_location="02_乙機房")
        conn.commit()
        rows = [{"ip": "10.99.5.7"}, {"ip": "10.99.9.9"}, {"ip": "172.16.0.9"}]
        m = v2_onboard.room_map(conn, rows)
        assert m["10.99.5.7"] == "甲機房"                        # 沒登記：看網段（去掉 01_ 編號）
        assert m["10.99.9.9"] == "乙機房"                        # 資產登記的機房
        assert m["172.16.0.9"] == "未知"
        assert v2_onboard.environment_map(conn, rows)["10.99.5.7"] == "正式"
    finally:
        conn.close()
