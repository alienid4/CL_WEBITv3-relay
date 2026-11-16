"""資產詳細頁效能修正（2026-10-21）：只改「怎麼算」，不改「算出什麼」。

1. 體檢（health_check.evaluate_all）：別名／改名對照表整次只查一次，不再每台各查兩張表。
   守：快取前後輸出逐字相同；SQL 條數不再隨台數成長；範圍外照舊每次重查（字典一補立刻生效）。
2. 兄弟節點（arch.ocp_siblings）：不是 OCP 節點的先便宜判斷直接回 is_ocp False。
   守：每一台的結果跟「一律算整座拓撲」完全相同（含人工指定成 OpenShift 節點的那種）。
3. 詳細頁回 san_collected：前端只在收過 SAN 的 IP 才打 /api/san/switches/{ip}。
"""
import contextlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import arch  # noqa: E402
import db  # noqa: E402
import health_check  # noqa: E402
import normalize  # noqa: E402
import os_override  # noqa: E402


@contextlib.contextmanager
def _conn():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


# 混合一批：會被別名救回來的、會被改名覆寫的、過保的、一年內過保的、認不出來的、退役的、OCP 節點
_HOSTS = [
    ("P1", "web01", "10.99.1.1", "Red Hat Enterprise Linux 7.9", "使用中"),
    ("P2", "web02", "10.99.1.2", "怪字串OS-本來認不出來", "使用中"),       # 靠別名字典
    ("P3", "web03", "10.99.1.3", "Windows Server 2012 R2", "使用中"),       # 靠改名覆寫
    ("P4", "web04", "10.99.1.4", "Windows Server 2016", "使用中"),
    ("P5", "web05", "10.99.1.5", "Rocky Linux 9.7 (Blue Onyx)", "使用中"),
    ("P6", "web06", "10.99.1.6", "N/A", "使用中"),
    ("P7", "web07", "10.99.1.7", None, "使用中"),
    ("P8", "web08", "10.99.1.8", "Red Hat Enterprise Linux 6.10", "報廢"),
    ("P9", "n1.paas-a.ocp.example.com", "10.99.2.1", "CoreOS 4.12", "使用中"),
    ("P10", "n2.paas-a.ocp.example.com", "10.99.2.2", "RHCOS 4.12", "使用中"),
    ("P11", "box11", "10.99.3.1", "Rocky Linux 9.7", "使用中"),            # 等一下人工指定成 OpenShift 節點
    ("P12", "web01", "10.99.1.1", "Red Hat Enterprise Linux 7.9", "使用中"),  # 同一台重複登記
]


def _seed(conn):
    for sn, host, ip, os_, status in _HOSTS:
        db.insert_hardware(conn, asset_serial=sn, hostname=host, ip=ip, os=os_,
                           asset_status=status, asset_purpose="OCP Worker" if sn in ("P9", "P10") else "AP")
    conn.execute("INSERT INTO normalize_alias (kind, raw_value, canonical, note) VALUES (?,?,?,?)",
                 (normalize.KIND_OS, "怪字串os-本來認不出來", "Red Hat Enterprise Linux 6", "test"))
    conn.execute("INSERT INTO normalize_canonical_override (kind, old_canonical, new_canonical) VALUES (?,?,?)",
                 (normalize.KIND_OS, "Windows Server 2012", "Windows Server 2016"))
    conn.commit()


def _evaluate_without_cache(conn, monkeypatch):
    """舊行為：沒有 lookup_cache，每台各自查表。"""
    monkeypatch.setattr(normalize, "lookup_cache", contextlib.nullcontext)
    try:
        return health_check.evaluate_all(conn)
    finally:
        monkeypatch.undo()


def test_體檢輸出_快取前後逐字相同(monkeypatch):
    with _conn() as conn:
        _seed(conn)
        before = _evaluate_without_cache(conn, monkeypatch)
        after = health_check.evaluate_all(conn)
        assert after == before
        # 確認案例真的有走到別名與改名覆寫（不然「相同」沒有意義）
        eos_details = {hid: [i["detail"] for i in v["issues"] if i["key"] == "eos"] for hid, v in after.items()}
        flat = " ".join(d for ds in eos_details.values() for d in ds)
        assert "Red Hat Enterprise Linux 6" in flat, "別名字典那台應該被判成 RHEL 6 過保"
        assert "Windows Server 2012" not in flat, "改名覆寫後不該再出現 2012"


def test_體檢的SQL條數不隨台數成長():
    with _conn() as conn:
        _seed(conn)
        n = {"q": 0}
        conn.set_trace_callback(lambda _s: n.__setitem__("q", n["q"] + 1))
        health_check.evaluate_all(conn)
        small = n["q"]
        for i in range(200):
            db.insert_hardware(conn, asset_serial=f"X{i}", hostname=f"x{i}", ip=f"10.99.9.{i % 250}",
                               os="Red Hat Enterprise Linux 8.6", asset_status="使用中")
        conn.commit()
        n["q"] = 0
        health_check.evaluate_all(conn)
        conn.set_trace_callback(None)
        # 舊寫法每台 2 條（別名＋改名覆寫）→ 多 200 台至少多 400 條
        assert n["q"] - small < 50, (small, n["q"])


def test_快取範圍內_OS與型號兩種正規化結果都跟範圍外相同():
    with _conn() as conn:
        _seed(conn)
        conn.execute("INSERT INTO normalize_alias (kind, raw_value, canonical) VALUES (?,?,?)",
                     (normalize.KIND_MODEL, "怪型號x1", "HPE ProLiant DL380 Gen10"))
        conn.execute("INSERT INTO normalize_canonical_override (kind, old_canonical, new_canonical) VALUES (?,?,?)",
                     (normalize.KIND_MODEL, "HPE ProLiant DL360 Gen10", "HPE ProLiant DL360 Gen10 Plus"))
        conn.commit()
        oses = [h[3] for h in _HOSTS] + ["怪型號x1", "Windows Server 2012"]
        models = ["怪型號x1", "HPE DL360 Gen10", "Windows Server 2012", "(VM)", None, "怪字串OS-本來認不出來"]
        plain = ([normalize.normalize_os(o, conn) for o in oses],
                 [normalize.normalize_model(m, conn) for m in models])
        with normalize.lookup_cache():
            cached = ([normalize.normalize_os(o, conn) for o in oses],
                      [normalize.normalize_model(m, conn) for m in models])
        assert cached == plain
        assert any(r["method"] == "alias" for r in plain[1]) and any(r["method"] == "alias" for r in plain[0])


def test_範圍外照舊每次重查_字典一補立刻生效():
    with _conn() as conn:
        assert normalize.normalize_os("某個新字串", conn)["matched"] is False
        conn.execute("INSERT INTO normalize_alias (kind, raw_value, canonical) VALUES (?,?,?)",
                     (normalize.KIND_OS, "某個新字串", "Rocky Linux 9"))
        conn.commit()
        assert normalize.normalize_os("某個新字串", conn)["canonical"] == "Rocky Linux 9"


def test_快取範圍可巢狀_離開後不殘留():
    with _conn() as conn:
        with normalize.lookup_cache():
            with normalize.lookup_cache():
                normalize.normalize_os("Rocky Linux 9.7", conn)
            assert normalize._LOOKUP_CACHE.get(), "內層離開不可以清掉外層那份"
        assert normalize._LOOKUP_CACHE.get() is None


def _all_siblings(conn, monkeypatch, fast: bool) -> dict:
    if not fast:
        monkeypatch.setattr(arch, "_maybe_ocp_node", lambda _c, _s: True)
    try:
        serials = [r[0] for r in conn.execute("SELECT asset_serial FROM hardware")] + ["沒這台"]
        return {s: arch.ocp_siblings(conn, s) for s in serials}
    finally:
        monkeypatch.undo()


def test_兄弟節點_先判斷再算_每一台結果都跟全算相同(monkeypatch):
    with _conn() as conn:
        _seed(conn)
        full = _all_siblings(conn, monkeypatch, fast=False)
        fast = _all_siblings(conn, monkeypatch, fast=True)
        assert fast == full
        assert fast["P9"]["is_ocp"] and fast["P10"]["is_ocp"]
        assert fast["P1"] == {"is_ocp": False}


def test_兄弟節點_人工指定成OpenShift節點的也不可以漏(monkeypatch):
    with _conn() as conn:
        _seed(conn)
        import pipeline
        os_override.set_override(conn, "box11", "10.99.3.1", "P11", pipeline.OS_OPENSHIFT,
                                 pipeline.OS_LINUX, "平台組確認是 OCP 節點", "tester")
        full = _all_siblings(conn, monkeypatch, fast=False)
        assert full["P11"]["is_ocp"], "前提：人工指定後整座拓撲裡有這台"
        fast = _all_siblings(conn, monkeypatch, fast=True)
        assert fast == full


def test_非OCP節點不跑全站漏斗(monkeypatch):
    with _conn() as conn:
        _seed(conn)
        called = {"n": 0}
        real = arch.ocp_topology

        def spy(c):
            called["n"] += 1
            return real(c)
        monkeypatch.setattr(arch, "ocp_topology", spy)
        assert arch.ocp_siblings(conn, "P1") == {"is_ocp": False}
        assert called["n"] == 0
        assert arch.ocp_siblings(conn, "P9")["is_ocp"]
        assert called["n"] == 1


def test_詳細頁回san_collected():
    import api
    import auth
    from fastapi.testclient import TestClient

    with _conn() as conn:
        db.insert_hardware(conn, asset_serial="S1", hostname="san01", ip="10.99.5.1", os="FOS v9")
        db.insert_hardware(conn, asset_serial="S2", hostname="web01", ip="10.99.5.2", os="Rocky Linux 9")
        conn.execute("INSERT INTO san_switch (ip, data_json) VALUES ('10.99.5.1', '{}')")
        db.create_user(conn, "t", auth.hash_password("pw-for-test-123"))
        conn.commit()

        def _override():
            yield conn

        api.app.dependency_overrides[api.get_db] = _override
        client = TestClient(api.app)
        try:
            assert client.post("/api/auth/login",
                               json={"username": "t", "password": "pw-for-test-123"}).status_code == 200
            assert client.get("/api/assets/S1").json()["san_collected"] is True
            assert client.get("/api/assets/S2").json()["san_collected"] is False
        finally:
            api.app.dependency_overrides.clear()
