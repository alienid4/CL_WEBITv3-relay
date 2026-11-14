"""Codex 1265：舊版「掃不到的網段」按「匯出 CSV（申請附件）」→ 422、下載不到。

原因：畫面沒勾時把 only_registered／include_expected 送成空字串，後端宣告成 bool，FastAPI 解析不了空字串。
契約：沒帶＝預設；空字串＝沒勾；1/0、true/false、yes/no、on/off；其他值 400。
畫面一律送 1／0；點了頁首卡片時，CSV 用同一組旗標（view）匯出卡片那批。全部假資料（IP 一律 10.99.x）。
"""
import csv
import io
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import scan_gaps  # noqa: E402
import scan_scope  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FE = ROOT / "APP" / "asset-module" / "frontend"
T = "2026-09-20 01:00:00"


def _seed(c):
    scan_scope._ensure(c)
    for cidr in ("10.99.1.0/24", "10.99.2.0/24", "10.99.3.0/24"):
        c.execute("INSERT INTO network_segment (cidr, raw_cidr, location, environment, category, imported_at) "
                  "VALUES (?,?,?,?,?,?)", (cidr, cidr, "ROOM-A", "正式", "SERVER", T))
        scan_scope.apply_scope(c, [r[0] for r in c.execute("SELECT id FROM network_segment WHERE cidr = ?", (cidr,))])
    for cidr in ("10.99.1.0/24", "10.99.2.0/24"):
        c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,?,?)", (T, cidr, 1, T))
    c.execute("INSERT INTO scan_history (scan_time, ip, segment, scan_ok) VALUES (?,?,?,1)", (T, "10.99.1.10", "10.99.1.0/24"))
    # 2 號段有登記資產卻沒掃到；3 號段從沒掃過、也沒登記
    db.insert_hardware(c, asset_serial="H-1", hostname="h1", ip="10.99.2.10", asset_status="使用中")
    c.commit()


@pytest.fixture()
def env():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        _seed(c)
        db.create_user(c, "tester", auth.hash_password("s3cure-pass!"))
        c.close()

        def _override():
            conn = db.get_connection(p)
            try:
                yield conn
            finally:
                conn.close()

        api.app.dependency_overrides[api.get_db] = _override
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "tester", "password": "s3cure-pass!"}).status_code == 200
        conn = db.get_connection(p)
        try:
            yield cl, conn
        finally:
            conn.close()
            api.app.dependency_overrides.pop(api.get_db, None)


def _cidrs(r) -> list[str]:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    col = rows[0].index("網段") if "網段" in rows[0] else 0
    return sorted(x[col] for x in rows[1:] if x)


def _expect(conn, **kw) -> list[str]:
    data = scan_gaps.report(conn, only_in_scope=True)
    return sorted(r["cidr"] for r in scan_gaps.filter_rows(data["rows"], **kw))


def test_舊畫面送空字串_不再422_而是當沒勾(env):
    cl, conn = env
    r = cl.get("/api/reports/scan-gaps/export?q=&environment=&location=&kind=&only_registered=&include_expected=")
    assert _cidrs(r) == _expect(conn, only_registered=False, include_expected=False)


def test_沒帶參數走預設(env):
    cl, conn = env
    assert _cidrs(cl.get("/api/reports/scan-gaps/export")) == _expect(conn)


def test_勾了只看有登記_真的會少(env):
    """反向：參數不是被吞掉——1 跟 0 匯出的列不一樣。"""
    cl, conn = env
    on = _cidrs(cl.get("/api/reports/scan-gaps/export?only_registered=1&include_expected=1"))
    off = _cidrs(cl.get("/api/reports/scan-gaps/export?only_registered=0&include_expected=1"))
    assert on == _expect(conn, only_registered=True, include_expected=True)
    assert off == _expect(conn, only_registered=False, include_expected=True)
    assert len(on) < len(off)
    assert _cidrs(cl.get("/api/reports/scan-gaps/export?only_registered=true")) == on


def test_亂給值要400講出參數名(env):
    cl, _ = env
    r = cl.get("/api/reports/scan-gaps/export?only_registered=maybe")
    assert r.status_code == 400 and "only_registered" in r.text


def test_舊畫面布林一律送1或0_卡片模式匯卡片那批():
    v = (FE / "pages" / "reports" / "scan-gaps.vue").read_text(encoding="utf-8")
    seg = v[v.index("function exportCsv()"):][:1600]
    assert "? '1' : ''" not in seg
    assert "only_registered: onlyRegistered.value ? '1' : '0'" in seg
    assert "include_expected: showExpected.value ? '1' : '0'" in seg
    # 卡片：firewall_first→fw；其他→gap；登記資產卡另加 only_registered
    assert "view: k === 'firewall_first' ? 'fw' : 'gap'" in seg
    assert "only_registered: k === 'gap_registered_machines' ? '1' : '0'" in seg
