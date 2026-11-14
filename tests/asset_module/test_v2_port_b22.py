"""V2 移植第 22 批（2.057.x）：V61 舊 /assets 資產查詢搬到 2.1 主機查詢（⚠D4 標記下線、D1 每列一鍵納管——都沿用既有元件與端點）。
純搬移：逐筆登記頁籤用同一支 /api/assets（同一組篩選參數）、掃到未登記同一支 /api/scan/unregistered、
「不是重複」同一支 /api/assets/duplicates/dismiss。人員／軟體不搬（待決定 6），只連到 7.2／7.3。全部假資料。"""
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PW = "test-password-123"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _calls(s):
    out = set()
    for m in re.finditer(r"apiFetch(?:WithHeaders)?(?:<[^(]*>)?\(\s*[`']([^`']+)[`'](?:\s*,\s*\{([^}]*))?", s):
        path = re.sub(r"\$\{[^}]*\}", "{x}", m.group(1)).split("?")[0]
        mm = re.search(r"method:\s*'(\w+)'", m.group(2) or "")
        out.add((mm.group(1) if mm else "GET", path))
    return out


OLD = "pages/assets/index.vue"


def test_逐筆登記_端點都是舊頁原本那幾支():
    new = _calls(_src("components/v2/AssetRecords.vue"))
    assert new == {("GET", "/api/assets"), ("GET", "/api/assets/field-groups"), ("GET", "/api/field-meta"), ("GET", "/api/assets/facets")}
    assert new <= _calls(_src(OLD))
    u = _calls(_src("components/v2/HostUnregistered.vue"))
    # 2.058.4（批 23）：加了舊 /adopt 未登記主機頁的「上次收集」欄（只讀）；寫入在 V2AdoptRegister／共用納管元件，見 test_v2_port_b23
    assert u == {("GET", "/api/scan/unregistered"), ("GET", "/api/collect/dispatch/latest")}
    # 寫入沿用既有元件：標記下線＝OfflineMarkModal（batch-status）、每列動作＝RowActions（同舊頁）
    r = _src("components/v2/AssetRecords.vue")
    assert "<OfflineMarkModal" in r and "<RowActions" in r and "<RowActions" in _src(OLD)


def test_篩選參數跟舊頁送的一樣_後端都收():
    ts = _src("utils/assetRecords.ts")
    keys = re.search(r"RECORD_FILTER_KEYS = \[([^\]]*)\]", ts, re.S).group(1)
    keys = re.findall(r"'(\w+)'", keys)
    old = _src(OLD)
    old_sent = set(re.findall(r"assetQuery\.(\w+) =", old)) | {"filter_value"}
    old_sent -= {"limit", "offset", "sort_by", "order"}
    assert old_sent <= set(keys), old_sent - set(keys)            # 舊頁送的每一個都還在
    import routers.assets as ra
    params = set(inspect.signature(ra.list_assets).parameters)
    assert set(keys) <= params, set(keys) - params                # 新增的（os_source、collect、source）後端本來就收


def test_不是重複_同一支端點同一個body同一句確認():
    n, o = _src("components/v2/HostDuplicates.vue"), _src(OLD)
    for frag in ("await apiFetch('/api/assets/duplicates/dismiss', {", "body: { hostname: g.hostname, ip: g.ip },",
                 "確定要把「${g.hostname} / ${g.ip}」這組標記為「不是重複登記」嗎？"):
        assert frag in n and frag in o, frag
    fn = n[n.index("async function dismissGroup"):]
    assert fn.index("if (!ok) return") < fn.index("/api/assets/duplicates/dismiss")


def test_主機查詢接線_頁籤_連結_舊版入口拿掉():
    s = _src("pages/v2/hosts.vue")
    assert "{ k: 'records', label: '逐筆登記' }" in s and "{ k: 'unreg', label: '掃到未登記' }" in s
    assert '<V2AssetRecords v-else-if="extraTab === \'records\'" @alive="goAliveSerials" />' in s
    assert "<V2HostUnregistered v-else-if=\"extraTab === 'unreg'\" />" in s
    assert 'to="/v2/inventory/account"' in s and 'to="/v2/inventory/software"' in s   # 待決定 6：只放連結
    assert "/assets?legacy=1" not in s
    m = _src("utils/v2LegacyMap.ts")
    assert "  '/assets',  " in m.split("export const PORTED")[1].split("]")[0]
    assert "tab: 'records', ...rf" in m


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)

    def _dep():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _dep
    c = db.get_connection(p)
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, name, auth.hash_password(PW), role=role)
    for i in range(7):
        db.insert_hardware(c, asset_serial=f"T-{i}", hostname=f"h{i}", ip=f"10.99.5.{i + 1}", device_model="DL380" if i % 2 else "R740")
    c.commit()
    c.close()

    def login(n):
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": n, "password": PW}).status_code == 200
        return cl
    yield login
    api.app.dependency_overrides.clear()


def test_api_assets_分頁總數與匯出全部是同一份(env):
    cl = env("alice")
    r = cl.get("/api/assets", params={"filter_field": "device_model", "filter_value": "DL380", "limit": 2, "offset": 0})
    assert r.status_code == 200 and len(r.json()) == 2
    total = int(r.headers["X-Total-Count"])
    a = cl.get("/api/assets", params={"filter_field": "device_model", "filter_value": "DL380"})
    assert len(a.json()) == total == 3                           # 匯出（不帶 limit）＝畫面上的總筆數
    assert cl.get("/api/assets", params={"sort_by": "guess"}).status_code == 400   # 所以畫面上不送這種 sort_by
    for path in ("/api/assets/field-groups", "/api/field-meta", "/api/assets/facets", "/api/scan/unregistered"):
        assert cl.get(path).status_code == 200, path


def test_不是重複_唯讀帳號擋下(env):
    assert env("vera").post("/api/assets/duplicates/dismiss", json={"hostname": "h1", "ip": "10.99.5.2"}).status_code == 403
