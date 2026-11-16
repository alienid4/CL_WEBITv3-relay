"""V2 移植第 16 批（2.051.0）：V51 清空盤點資料下架、V66 分布統計補「機房×環境」「快速查詢」（只讀）。全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    db.insert_hardware(conn, asset_serial="A-1", hostname="aix01", ip="10.99.1.18", physical_location="01_甲機房",
                       environment="正式", os="AIX 7.2")
    db.insert_hardware(conn, asset_serial="A-1b", hostname="aix01", ip="10.99.1.18", physical_location="01_甲機房",
                       environment="正式", os="AIX 7.2")                                   # 同一台第二筆登記
    db.insert_hardware(conn, asset_serial="W-1", hostname="win01", ip="10.99.2.1", physical_location="01_甲機房",
                       environment="測試", os="Windows Server 2019")
    db.insert_hardware(conn, asset_serial="L-1", hostname="lnx01", ip="10.99.3.3", physical_location="02_乙機房",
                       environment="正式", os="Red Hat Enterprise Linux 9")
    db.insert_hardware(conn, asset_serial="L-2", hostname="lnx02", ip="10.99.3.4", os="Red Hat Enterprise Linux 9")
    db.insert_hardware(conn, asset_serial="L-3", hostname="lnx03", ip="10.99.3.5", physical_location="02_乙機房",
                       environment="  ", os="Red Hat Enterprise Linux 9")                 # 環境欄只有空白＝未填
    db.insert_hardware(conn, asset_serial="R-1", hostname="ret01", ip="10.99.1.99", physical_location="01_甲機房",
                       environment="正式", os="AIX 7.2", asset_status="報廢")             # 退役：任何台數都不算
    conn.commit()


# ---------- V66 機房×環境 ----------

def test_機房x環境_格子列合計欄合計都等於下鑽(tmp_path):
    import v2_dist
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = v2_dist.summary(conn)
        mx = d["matrix"]
        assert mx["total"] == d["total"] == 5          # aix01 兩筆登記算一台；退役 ret01 不算
        site_all = {r["value"]: r["all"] for r in d["dims"]["site"]}
        env_all = {r["value"]: r["all"] for r in d["dims"]["env"]}
        for r in mx["rows"]:
            assert r["all"] == site_all[r["site"]] == sum(r["by_env"].values())
            for e, n in r["by_env"].items():
                assert len(v2_dist.drill(conn, "site", r["site"], "all", env=e)["rows"]) == n
        for e in mx["envs"]:
            assert mx["env_totals"][e] == env_all[e]
        assert mx["envs"][-1] == "未填" and mx["rows"][-1]["site"] == "未填"      # 未填排最後
    finally:
        conn.close()


def test_機房x環境_退役不算_空白環境歸未填(tmp_path):
    """2.052.1（批 16 審查 P2-3）：退役機器不進交叉表也不進下鑽；環境欄只有空白的歸「未填」。"""
    import v2_dist
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        mx = v2_dist.summary(conn)["matrix"]
        cells = {(r["site"], e): n for r in mx["rows"] for e, n in r["by_env"].items()}
        assert cells[("甲機房", "正式")] == 1                      # aix01；ret01 退役不算
        assert cells[("乙機房", "未填")] == 1                      # lnx03 環境只有空白
        names = {x["name"] for r in mx["rows"] for e in r["by_env"]
                 for x in v2_dist.drill(conn, "site", r["site"], "all", env=e)["rows"]}
        assert "ret01" not in names and "lnx03" in names
        for e in mx["envs"]:                                       # 欄合計（環境別維度）的下鑽也不含退役
            assert "ret01" not in {x["name"] for x in v2_dist.drill(conn, "env", e, "all")["rows"]}
    finally:
        conn.close()


def test_機房x環境_被餵進退役機器會被verify_numbers抓到(tmp_path, monkeypatch):
    import v2_dist
    import v2_hosts
    import verify_numbers as vn
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        real = v2_dist.matrix
        monkeypatch.setattr(v2_dist, "matrix", lambda ms: real(v2_hosts.machines(conn, include_retired=True)))
        vn.RESULTS.clear()
        vn.check_v2_dist(conn)
        bad = [r["name"] for r in vn.RESULTS if r["page"] == "2.x 分布統計" and not r["ok"]]
        assert any("機房×環境：總和" in n for n in bad), bad
    finally:
        conn.close()


def test_機房x環境_env只能配所在地(tmp_path):
    import pytest
    import v2_dist
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError):
            v2_dist.drill(conn, "os", "AIX", "all", env="正式")
    finally:
        conn.close()


def test_機房x環境_格子灌水會被verify_numbers抓到(tmp_path, monkeypatch):
    import v2_dist
    import verify_numbers as vn
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        vn.RESULTS.clear()
        vn.check_v2_dist(conn)
        assert all(r["ok"] for r in vn.RESULTS if r["page"] == "2.x 分布統計")
        real = v2_dist.matrix

        def bumped(ms):
            m = real(ms)
            r0 = m["rows"][0]
            e0 = next(iter(r0["by_env"]))
            r0["by_env"][e0] += 1
            return m
        monkeypatch.setattr(v2_dist, "matrix", bumped)
        vn.RESULTS.clear()
        vn.check_v2_dist(conn)
        bad = [r["name"] for r in vn.RESULTS if r["page"] == "2.x 分布統計" and not r["ok"]]
        assert any("機房×環境：每一格" in n for n in bad) and any("列合計" in n for n in bad), bad
    finally:
        conn.close()


# ---------- V66 快速查詢 ----------

def test_快速查詢_一台一列_值跟下鑽同一個判斷(tmp_path):
    import v2_dist
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = v2_dist.lookup(conn, "aix01")
        assert r["total"] == 1 and len(r["rows"]) == 1       # 兩筆登記仍是一台
        row = r["rows"][0]
        assert row["site"] == "甲機房" and row["env"] == "正式" and row["os"] == "AIX"
        keys = {x["key"] for x in v2_dist.drill(conn, "site", row["site"], "all", env=row["env"])["rows"]}
        assert row["key"] in keys
        assert v2_dist.lookup(conn, "10.99.3")["total"] == 3           # IP 部分比對
        assert v2_dist.lookup(conn, "ret01")["total"] == 0             # 退役不列
        assert v2_dist.lookup(conn, "  ")["total"] == 0
        assert v2_dist.lookup(conn, "LNX0", limit=1)["total"] == 3 and len(v2_dist.lookup(conn, "lnx0", limit=1)["rows"]) == 1
    finally:
        conn.close()


def test_快速查詢與機房x環境下鑽_API(tmp_path):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = tmp_path / "t.db"
    db.init_db(p)

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        c = db.get_connection(p)
        _seed(c)
        db.create_user(c, "tester", auth.hash_password("test-password-123"))
        c.close()
        cl = TestClient(api.app)
        assert cl.get("/api/v2/dist/lookup", params={"q": "aix"}).status_code == 401
        assert cl.post("/api/auth/login", json={"username": "tester", "password": "test-password-123"}).status_code == 200
        r = cl.get("/api/v2/dist/lookup", params={"q": "aix"})
        assert r.status_code == 200 and r.json()["total"] == 1
        r = cl.get("/api/v2/dist/drill", params={"dim": "site", "value": "甲機房", "kind": "all", "env": "測試"})
        assert r.status_code == 200 and len(r.json()["rows"]) == 1
        assert cl.get("/api/v2/dist/drill", params={"dim": "os", "value": "AIX", "env": "正式"}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()


def test_分布統計頁_新頁籤與網址參數():
    d = _src("pages/v2/dist.vue")
    assert "{ k: 'matrix', label: '機房×環境' }" in d and "{ k: 'lookup', label: '快速查詢' }" in d
    assert "'/api/v2/dist/lookup'" in d and "env: q('me')" in d and "q('lq')" in d
    assert d.count("<V2Table") >= 5                    # 每張表都是共用表格（排序、項次、匯出）
    assert "機房×環境、OS×環境" not in d and "快速查詢、匯出）舊版" not in d     # 舊版連結不再說這兩項只在舊版


# ---------- V51 清空盤點資料下架 ----------

def test_清空資料_入口拿掉_舊網址轉備份與還原():
    s = _src("pages/v2/settings.vue")
    assert "{ k: 'reset'" not in s and "pages/reset.vue" not in s
    assert "retiredReset" in s and "已下架" in s
    assert "q('st') === 'reset' ? 'backup'" in s                       # 舊書籤 ?st=reset 開備份與還原（2.052.1）
    lm = _src("utils/v2LegacyMap.ts")
    assert "'/reset': () => to('/v2/settings', { st: 'backup', retired: 'reset' })" in lm
    assert "  '/reset'," in lm.split("export function isPorted")[0]           # PORTED：legacy=1 也不開
    nav = _src("composables/useNav.ts")
    assert "/reset'" not in nav                                         # 選單、搜尋說明、舊群組都拿掉（2.052.1）
    r = _src("pages/reset.vue")
    assert "apiFetch" not in r and "reset-inventory" not in r            # 舊頁不再有寫入


def test_前端不再呼叫清空API():
    hits = [p for p in FE.rglob("*") if p.suffix in (".vue", ".ts") and "node_modules" not in p.parts
            and ".nuxt" not in p.parts and ".output" not in p.parts and "reset-inventory" in p.read_text(encoding="utf-8", errors="ignore")]
    assert not hits, hits
