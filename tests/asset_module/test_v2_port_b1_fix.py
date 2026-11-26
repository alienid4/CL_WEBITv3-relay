"""V2 移植 B 區第 1 批：GPT 審查 8 條的修正（2.047.1）。每條都有一個「修法被拿掉就會紅」的測試。"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACK = ROOT / "APP" / "asset-module" / "backend"
FRONT = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(BACK))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
import access_policy  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from test_api import _insert_hardware  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "test-password-123"


def _src(rel: str) -> str:
    return (FRONT / rel).read_text(encoding="utf-8")


def _client(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _get():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get
    c = db.get_connection(p)
    db.create_user(c, "admin1", auth.hash_password(_PW), role="admin")
    db.create_user(c, "viewer1", auth.hash_password(_PW), role="viewer")
    c.close()
    return TestClient(api.app), p


def _login(cl, u):
    assert cl.post("/api/auth/login", json={"username": u, "password": _PW}).status_code == 200


def _n_checks(p):
    c = db.get_connection(p)
    try:
        return c.execute("SELECT COUNT(*) AS n FROM golive_check").fetchone()["n"]
    finally:
        c.close()


# ---- P1-1：只能看的帳號開單台上線檢查不寫庫；重新判定是 POST、一般帳號以上
def test_P1_1_GET單台上線檢查不寫庫_重新判定要POST():
    with tempfile.TemporaryDirectory() as tmp:
        cl, p = _client(tmp)
        try:
            _insert_hardware(p, "GL-1", "web01", "10.99.1.1")
            _login(cl, "viewer1")
            r = cl.get("/api/golive/GL-1")
            assert r.status_code == 200, r.text
            assert r.json()["items"], "沒有檢查表也要回項目定義（還沒開始）"
            assert _n_checks(p) == 0, "GET 建了檢查表＝只能看的人開頁面就寫庫"
            assert cl.get("/api/golive").status_code == 200
            assert _n_checks(p) == 0, "清單 GET 也不能建檢查表"
            assert cl.post("/api/golive/GL-1/refresh").status_code == 403, "只能看的帳號不能重新判定"
            _login(cl, "admin1")
            r = cl.post("/api/golive/GL-1/refresh")
            assert r.status_code == 200, r.text
            assert _n_checks(p) == 1, "重新判定才建檢查表"
            assert cl.post("/api/golive/NOPE/refresh").status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_P1_1_重新判定分級與畫面():
    assert access_policy.classification("POST", "/api/golive/{asset_serial}/refresh") == "user"
    g = _src("components/v2/GoliveOne.vue")
    assert "'/refresh'" in g or "/refresh`" in g, "重新判定走 POST /refresh"
    i = g.index("重新判定</button>") if "重新判定</button>" in g else g.index("重新判定")
    tag = g[g.rfind("<button", 0, i):i]
    assert 'v-if="!isViewer"' in tag, "只能看的人看不到重新判定"


# ---- P1-2：型號台數下鑽＝同一份計算
def test_P1_2_型號台數下鑽筆數等於台數(monkeypatch):
    import rack_layout
    fake = [
        {"kind": "rack", "model": "PowerEdge R740", "key": "k1", "name": "a1", "ip": "10.99.0.1", "asset_serial": "S1", "room": "R1", "rack": "A01", "u_start": 3, "u_end": 4},
        {"kind": "rack", "model": "PowerEdge R740", "key": "k2", "name": "a2", "ip": "10.99.0.2", "asset_serial": "S2", "room": "R1", "rack": "A01", "u_start": None, "u_end": None},
        {"kind": "rack", "model": "", "key": "k3", "name": "a3", "ip": None, "asset_serial": "S3", "room": "R1", "rack": "A02", "u_start": 1, "u_end": 1},
        {"kind": "vm", "model": "PowerEdge R740", "key": "k4", "name": "vm1", "ip": "10.99.0.4", "asset_serial": "S4", "room": None, "rack": None, "u_start": None, "u_end": None},
    ]
    monkeypatch.setattr(rack_layout, "machines_readonly_full", lambda conn: [dict(m) for m in fake])
    with tempfile.TemporaryDirectory() as tmp:
        cl, p = _client(tmp)
        try:
            _login(cl, "viewer1")
            rows = cl.get("/api/model-specs").json()
            cov = rows.get("coverage", rows)
            cov_rows = cov["rows"] if isinstance(cov, dict) and "rows" in cov else None
            import model_specs
            c = db.get_connection(p)
            try:
                cov_rows = cov_rows or model_specs.coverage(c)["rows"]
            finally:
                c.close()
            assert cov_rows
            for r in cov_rows:
                d = cl.get("/api/model-specs/coverage-hosts", params={"device_model": r["device_model"]})
                assert d.status_code == 200, d.text
                assert len(d.json()["items"]) == r["count"], r["device_model"]
            names = {x["name"] for x in cl.get("/api/model-specs/coverage-hosts", params={"device_model": "PowerEdge R740"}).json()["items"]}
            assert names == {"a1", "a2"}, "VM 不算機櫃設備"
            assert cl.get("/api/model-specs/coverage-hosts", params={"device_model": "不存在"}).json()["items"] == []
        finally:
            api.app.dependency_overrides.clear()
    m = _src("components/v2/ModelSpecs.vue")
    assert "/api/model-specs/coverage-hosts" in m and "/v2/hosts?q=" not in m, "不再丟到主機清單用關鍵字找"


# ---- P1-3：操作紀錄清單跟卡片同一個期間
def test_P1_3_操作紀錄清單帶days_筆數等於卡片():
    with tempfile.TemporaryDirectory() as tmp:
        cl, p = _client(tmp)
        try:
            c = db.get_connection(p)
            old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
            new = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            for at in (old, old, new):
                c.execute("INSERT INTO activity_log (at, username, ip, method, path, status, duration_ms, action) "
                          "VALUES (?, 'u9', '10.99.0.9', 'POST', '/api/x', 200, 5, 'change')", (at,))
            c.commit()
            c.close()
            _login(cl, "admin1")
            s = cl.get("/api/activity/summary?days=7").json()
            n = next(u["n"] for u in s["by_user"] if u["username"] == "u9")
            lst = cl.get("/api/activity", params={"username": "u9", "days": 7}).json()
            assert lst["total"] == n == 1, "卡片 1 次、清單也要 1 筆（不能把 20 天前的也列出來）"
            assert cl.get("/api/activity", params={"days": 0}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()
    a = _src("components/v2/Activity.vue")
    assert "days: String(days.value)" in a, "清單網址要帶 days"
    assert "u.username, aa: undefined" in a, "點帳號卡清掉動作篩選（卡片次數是全部動作）"


# ---- P1-4：已移植的頁 legacy=1 也不開舊頁
def test_P1_4_已移植頁不吃legacy():
    mw = _src("middleware/v2-legacy.global.ts")
    i, j = mw.index("isPorted(to.path)"), mw.index("to.query[LEGACY_BYPASS] === '1'")
    assert i < j, "要先判斷已移植，再看 legacy=1"
    m = _src("utils/v2LegacyMap.ts")
    for x in ("'/activity'", "'/model-specs'", "'/reports/host-sources'", "'/reports/monthly'", "'/scan-results'", "'/golive'", "'/golive/*'", "'/blast/checklist/*'"):
        assert x in m[m.index("export const PORTED"):m.index("export function isPorted")], x


# ---- P2-5：匯出照畫面目前的排序
def test_P2_5_匯出照目前排序():
    t = _src("components/v2/Table.vue")
    body = t[t.index("async function doExport"):t.index("const head = ['項次'")]
    assert "sortRows(list, sortKey.value, sortDir.value)" in body


# ---- P2-6/7/8：數字卡下鑽先清掉同類的其他條件
def test_P2_678_數字卡清掉同類條件():
    ck = _src("components/v2/Checklist.vue")
    for x in ("setQ({ cs: 'todo', cst: undefined })", "setQ({ cs: 'doing', cst: undefined })", "setQ({ cs: 'done', cst: undefined })",
              "setQ({ cst: s, cs: undefined })"):
        assert x in ck, x
    assert "hq: undefined, src: undefined" in _src("components/v2/HostSources.vue")
    g = _src("components/v2/GoliveOne.vue")
    assert "gopen: fOpen ? undefined : '1', gc: undefined" in g and "setQ({ gc: String(c), gopen: undefined })" in g
