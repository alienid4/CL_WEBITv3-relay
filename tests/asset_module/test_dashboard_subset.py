"""首頁：選了環境的數字不可以比全部大（子集≤全體）（2026-09-18）。

221 實測「正式環境一致 1」＞「全部一致 0」：221 自己的登記是 AUTO- 帳外，選環境時算進去、
總數沒算（v1.208 只修了總數那半邊）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_選環境的一致與登記數_不大於全部(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="AUTO-192.0.2.9", hostname="selfhost", ip="192.0.2.9",
                       environment="正式", asset_status="使用中")          # 帳外、掃得到
    db.insert_hardware(c, asset_serial="A-1", hostname="h1", ip="192.0.2.1",
                       environment="正式", asset_status="使用中")          # 在管、掃得到
    db.insert_hardware(c, asset_serial="A-2", hostname="h2", ip="192.0.2.2",
                       environment="正式", asset_status="報廢")            # 退役、掃得到
    for ip, h in (("192.0.2.9", "selfhost"), ("192.0.2.1", "h1"), ("192.0.2.2", "h2")):
        c.execute("INSERT INTO scan_history (hostname, ip, scan_ok, scan_time, open_ports) "
                  "VALUES (?,?,1,'2026-09-18 10:00:00','22')", (h, ip))
    c.commit()
    c.close()

    def _get_db():
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
    try:
        cl = TestClient(api.app)
        for env in ("正式", "全部"):
            r = cl.get("/api/dashboard/stats", params={"environment": env})
            if r.status_code == 400:
                continue                                   # 該環境名稱不在 preset，略過
            s = r.json()
            assert s["overlap_count"] <= s["total_overlap_count"], f"{env}：一致 {s['overlap_count']} > 全部 {s['total_overlap_count']}"
            assert s["ica_count"] <= s["total_ica_count"], f"{env}：登記 {s['ica_count']} > 全部 {s['total_ica_count']}"
        s = cl.get("/api/dashboard/stats", params={"environment": "正式"}).json()
        assert s["ica_count"] == 1 and s["overlap_count"] == 1, "帳外與退役都不算在管"
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)
