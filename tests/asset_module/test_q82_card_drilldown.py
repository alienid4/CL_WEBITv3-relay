"""Q82（2026-10-17）盤點頁數字卡一律可下鑽，而且下鑽跟卡片是同一份計算。

服務盤點：卡片條件只有一份（service_inventory.CARD_WHERE），summary 用它數、list_services(card=)
用它篩。掃不到的網段：每列帶 is_gap／firewall_first 旗標，summary 用同一組旗標加總。
影響範圍：evidence_ids 跟 evidence_breakdown 同一個迴圈記。verify_numbers 另有正式資料的不變式。
IP 一律 10.99.x（範例）。
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(ROOT / "tests" / "asset_module"))

import db  # noqa: E402
import service_inventory as si  # noqa: E402
import software_inventory as sw  # noqa: E402
import verify_numbers  # noqa: E402

SS = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*     users:(("sshd",pid=1,fd=3))
LISTEN 0      511          0.0.0.0:80         0.0.0.0:*     users:(("nginx",pid=2,fd=6))
LISTEN 0      70         127.0.0.1:3306            *:*      users:(("mysqld",pid=3,fd=2))
"""
SS_B = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*     users:(("sshd",pid=1,fd=3))
"""


def _svc_conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="A-1", hostname="web1", ip="10.99.1.1", collect_ok=1)
    db.insert_hardware(conn, asset_serial="A-2", hostname="web2", ip="10.99.1.2", collect_ok=1)
    texts = {"10.99.1.1": SS, "10.99.1.2": SS}
    si.collect_services(conn, runner=lambda host, cmd: "" if "systemctl" in cmd else texts[host])
    texts["10.99.1.2"] = SS_B                     # 第二輪：web2 的 80／3306 消失
    si.collect_services(conn, runner=lambda host, cmd: "" if "systemctl" in cmd else texts[host])
    return conn


def test_服務盤點_每張卡等於下鑽():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _svc_conn(tmp)
        try:
            s = si.service_summary(conn)
            assert s["gone"] == 2 and s["live"] == 4 and s["hosts"] == 2
            for k in ("total", "live", "gone", "exposed", "confirmed"):
                assert s[k] == len(si.list_services(conn, card=k)), k
            assert s["hosts"] == len({r["ip"] for r in si.list_services(conn, card="hosts")})
            # 卡片下鑽忽略 include_gone／include_infra（不然「消失 2」點進去會是 0）
            assert len(si.list_services(conn, card="gone", include_gone=False, include_infra=False)) == 2
            with pytest.raises(ValueError):
                si.list_services(conn, card="nope")
        finally:
            conn.close()


def test_服務盤點_API_card參數():
    from fastapi.testclient import TestClient
    import api
    import auth

    with tempfile.TemporaryDirectory() as tmp:
        conn = _svc_conn(tmp)
        conn.close()
        p = Path(tmp) / "t.db"

        def _override():
            c = db.get_connection(p)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = _override
        try:
            c = db.get_connection(p)
            db.create_user(c, "tester", auth.hash_password("s3cure-pass!"))
            c.close()
            client = TestClient(api.app)
            assert client.post("/api/auth/login", json={"username": "tester", "password": "s3cure-pass!"}
                               ).status_code == 200
            r = client.get("/api/services", params={"card": "exposed"}).json()
            assert len(r["items"]) == r["summary"]["exposed"]
            assert client.get("/api/services", params={"card": "x"}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_掃不到的網段_旗標加總等於卡片(tmp_path):
    import test_scan_gaps as tsg
    import scan_gaps as sg

    rep = sg.report(tsg._conn(tmp_path))
    rows, sm = rep["rows"], rep["summary"]
    gaps = [r for r in rows if r["is_gap"]]
    assert sm["gap_segments"] == len(gaps) and sm["firewall_first"] == sum(r["firewall_first"] for r in rows)
    assert sm["gap_addresses"] == sum(r["addresses"] for r in gaps)
    assert sm["gap_registered_machines"] == sum(r["registered_machines"] for r in gaps)
    assert all(r["is_gap"] and r["registered_machines"] > 0 for r in rows if r["firewall_first"])


def test_影響範圍_證據名單跟計數同一份(tmp_path):
    import blast_radius as br

    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        for sn, host, ip in (("HW-A", "app01", "10.99.0.1"), ("HW-B", "db01", "10.99.0.2"),
                             ("HW-C", "web01", "10.99.0.3")):
            db.insert_hardware(conn, asset_serial=sn, hostname=host, ip=ip, environment="正式")
            conn.execute("INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
                         "VALUES (?,'host',?,?,'derive:hardware','t','t')", (f"hw:{sn}", host, sn))
        conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                     "VALUES ('hw:HW-A','hw:HW-B','depends_on','證據','manual','t')")
        conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                     "VALUES ('hw:HW-C','hw:HW-B','depends_on','未驗證','manual','t')")
        conn.commit()
        s = br.impact(conn, "hw:HW-B", depth=6)["summary"]
        for k, v in s["evidence_breakdown"].items():
            assert v == len(s["evidence_ids"].get(k, [])), k
        assert s["evidence_ids"]["證據"] == ["hw:HW-A"] and s["evidence_ids"]["未驗證"] == ["hw:HW-C"]
    finally:
        conn.close()


def test_verify_numbers有這段而且會跑():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _svc_conn(tmp)
        try:
            before = verify_numbers.FAILS
            verify_numbers.check_card_drilldowns(conn)
            assert verify_numbers.FAILS == before
            assert any(r["sec"].startswith("盤點頁數字卡") for r in verify_numbers.RESULTS)
        finally:
            conn.close()
    assert sw.software_summary  # 軟體盤點的四張卡由 verify_numbers 那段釘住
