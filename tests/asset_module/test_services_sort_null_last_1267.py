"""Codex 1267：服務清單點「曝露」欄降序，未知（NULL）跑到第一列；程式註解寫「空值一律排最後，不隨 asc/desc 翻面」。

契約（跟 v2_hosts.page_rows、eos_evidence.sort_rows 同一套）：有值的照方向排，空值／空字串一律接在最後。
全部假資料（IP 一律 10.99.x）。
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "s3cure-pass!"
# port → exposure（D＝未知）
ROWS = [(1001, "all"), (1002, "localhost"), (1003, "specific"), (1004, None), (1005, "")]


@pytest.fixture()
def client():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        db.insert_hardware(c, asset_serial="S-1", hostname="svc-host", ip="10.99.4.1", asset_status="使用中")
        for port, exp in ROWS:
            c.execute("INSERT INTO host_service (ip, asset_serial, proto, port, bind_addr, exposure, process, source, "
                      "first_seen, last_seen) VALUES ('10.99.4.1','S-1','tcp',?,'0.0.0.0',?,?,'test',"
                      "'2026-01-01','2026-01-01')", (port, exp, f"p{port}"))
        db.create_user(c, "tester", auth.hash_password(_PW))
        c.commit()
        c.close()

        def _override():
            conn = db.get_connection(p)
            try:
                yield conn
            finally:
                conn.close()

        api.app.dependency_overrides[api.get_db] = _override
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
        try:
            yield cl
        finally:
            api.app.dependency_overrides.pop(api.get_db, None)


def _exp(cl, order):
    r = cl.get("/api/services", params={"sort_by": "exposure", "order": order})
    assert r.status_code == 200, r.text
    return [x["exposure"] for x in r.json()["items"] if x["port"] in dict(ROWS)]


def test_升冪_有值照字母_空值在最後(client):
    got = _exp(client, "asc")
    assert got[:3] == ["all", "localhost", "specific"]
    assert set(got[3:]) <= {None, ""} and len(got) == 5


def test_降冪_有值反過來_空值仍在最後不移到第一(client):
    got = _exp(client, "desc")
    assert got[:3] == ["specific", "localhost", "all"]
    assert got[0] not in (None, "")
    assert set(got[3:]) <= {None, ""} and len(got) == 5


def test_其他欄降冪也一樣_port照數字(client):
    r = client.get("/api/services", params={"sort_by": "port", "order": "desc"})
    ports = [x["port"] for x in r.json()["items"]]
    assert ports == sorted(ports, reverse=True)
