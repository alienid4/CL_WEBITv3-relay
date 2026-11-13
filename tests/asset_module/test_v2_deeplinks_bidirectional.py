"""V2 入口不回落 V1：每個入口雙向釘住（Codex 1349 第 3 點、1353；原表 #194／#6、#52、#106／#109）。

每一組都驗兩個方向：
  新：程式現在產生的連結是 V2 網址，日期／分類／系統／篩選都在網址上。
  舊：已經寄出去的信、書籤裡的舊網址，經 v2LegacyTarget（middleware 用的同一支）轉到 V2 同一頁、同一組參數；
      而且「舊網址轉過去的目標」＝「新連結」，兩邊不會各走各的。
  另外確認 V2 落點頁真的讀這些參數（不然網址帶了也沒用）。

前端純函式用 node 直接載入 .ts（node 22.6+；沒有就跳過）。.ts 之間的 import 沒寫副檔名，
用一個 resolve hook 補 .ts（只在測試裡，不影響 Nuxt 建置）。
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest as dd  # noqa: E402

_HOOK = ("data:text/javascript,export async function resolve(s,c,n){try{return await n(s,c)}catch(e){"
         "if((s.startsWith('.')||s.startsWith('file:'))&&!/\\.[cm]?[jt]s$/.test(s))return n(s+'.ts',c);throw e}}")


def _node(module: str, expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("沒有 node")
    v = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")
    if (int(v[0]), int(v[1])) < (22, 6):
        pytest.skip(f"node {'.'.join(v)} 不能直接載入 .ts")
    src = (FE / module).as_uri()
    code = (f"import {{ register }} from 'node:module'; register({json.dumps(_HOOK)});"
            f"const m = await import('{src}'); console.log(JSON.stringify({expr}))")
    r = subprocess.run([node, "--no-warnings", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _legacy(path: str, query: dict):
    return _node("utils/v2LegacyMap.ts", f"m.v2LegacyTarget({json.dumps(path)}, {json.dumps(query)})")


def _split(url: str) -> tuple[str, dict]:
    u = urlsplit(url)
    return u.path, dict(parse_qsl(u.query))


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


# ── #194／#6（N16）：摘要信的低容量（及其他分類）連結 ──────────────────────────────

DAY = "2026-11-12"
TODAY = {"date": DAY, "prev_date": "2026-11-11", "counts": {"storage_low": 2, "added": 1},
         "snapshot": {"storage_threshold": 10}, "segments": []}


def _digest_links() -> dict[str, tuple[str, dict]]:
    cfg = {**dd.DEFAULTS, "base_url": "https://webit.example"}
    _subj, body = dd.compose(cfg, TODAY, None)
    out = {}
    for u in re.findall(r"https://webit\.example(\S+)", body):
        p, q = _split(u)
        out[q.get("cat", "")] = (p, q)
    return out


def test_N16_新_摘要信每個分類都連V2同日同分類():
    links = _digest_links()
    assert "storage_low" in links, "摘要信沒有低容量連結"
    expect_cats = {k for k, _ in dd.CATEGORIES}
    assert set(links) == expect_cats, set(links) ^ expect_cats
    for cat, (p, q) in links.items():
        assert p == "/v2/changes", (cat, p)
        assert q == {"tab": "daily", "date": DAY, "cat": cat}, (cat, q)


def test_N16_舊_摘要信舊網址轉到V2同日同分類且等於新連結():
    links = _digest_links()
    for cat, (p, q) in links.items():
        t = _legacy("/changes", {"date": DAY, "tab": cat})
        assert t is not None, f"/changes?tab={cat} 轉址回 null（Codex N16）"
        assert t == {"path": p, "query": q}, (cat, t)
    # 沒帶分類＝總覽，留在每日異動同一天
    assert _legacy("/changes", {"date": DAY}) == {"path": "/v2/changes", "query": {"tab": "daily", "date": DAY}}


def test_N16_V2落點讀日期與分類():
    s = _src("pages/v2/changes.vue")
    assert "route.query.date" in s and "route.query.cat" in s
    assert "query: { date: day.value }" in s          # 日期真的送到 /api/changes


# ── #52（N27）：系統履歷「聯絡資訊」卡 → 聯絡資訊待補，保留系統 ─────────────────────

def test_N27_新_聯絡卡連V2聯絡資訊待補並帶系統():
    s = _src("components/ContactCard.vue")
    hrefs = re.findall(r':to="`([^`]*)`"|to="([^"]*)"', s)
    links = [a or b for a, b in hrefs]
    assert links, "抓不到 ContactCard 的連結"
    for u in links:
        assert u.startswith("/v2/"), u
    todo = [u for u in links if "tab=todo" in u]
    assert todo and todo[0].startswith("/v2/settings?st=contacts&tab=todo&api=${"), todo


def test_N27_舊_聯絡頁舊網址轉到V2同一頁籤同一系統():
    t = _legacy("/contacts", {"tab": "todo", "api": "DEMO-S"})
    assert t["path"] == "/v2/settings"
    assert t["query"] == {"st": "contacts", "tab": "todo", "api": "DEMO-S"}
    # 跟新連結（api 代入 DEMO-S）是同一組參數
    new_p, new_q = _split("/v2/settings?st=contacts&tab=todo&api=DEMO-S")
    assert (t["path"], t["query"]) == (new_p, new_q)


def test_N27_V2落點讀頁籤與系統():
    s = _src("components/v2/Contacts.vue")
    assert "qs('tab')" in s and "qs('api')" in s
    assert "contacts: defineAsyncComponent(() => import('~/components/v2/Contacts.vue'))" in _src("pages/v2/settings.vue")


# ── #106／#109（N26）：帳號儀表板特權帳號卡 → 合規矩陣篩選 ─────────────────────────

def test_N26_新_特權帳號卡與檢核卡都連V2合規矩陣並帶篩選():
    priv = _node("composables/accountCells.ts", "m.privBreakdown([], 'linux')")
    assert priv
    for it in priv:
        p, q = _split(it["to"])
        assert p == "/v2/inventory/matrix", it
        assert q.get("os") == "linux", it
    uid0 = next(it for it in priv if it["label"] == "UID 0")
    assert _split(uid0["to"])[1] == {"col": "sudo", "val": "uid0", "os": "linux"}
    checks = _node("composables/accountCells.ts", "m.dashboardChecks([], 'linux')")
    assert checks
    for c in checks:
        for k in ("to", "unknownTo"):
            p, q = _split(c[k])
            assert p == "/v2/inventory/matrix" and q.get("col") and q.get("val") and q.get("os") == "linux", c
    # 儀表板元件自己組的卡片連結：不在 V2 頁時一律用 V2 合規矩陣
    assert "const AM = route.path.startsWith('/v2/') ? route.path : '/v2/inventory/matrix'" in _src("components/AccountDashboard.vue")


def test_N26_舊_帳號矩陣舊網址轉到V2且篩選一筆不少():
    q = {"col": "uid0", "val": "yes", "os": "linux"}
    assert _legacy("/account-matrix", q) == {"path": "/v2/inventory/matrix", "query": q}
    checks = _node("composables/accountCells.ts", "m.dashboardChecks([], 'linux')")
    for c in checks:                      # 每張卡：舊網址轉過去＝新連結
        p, nq = _split(c["to"])
        assert _legacy("/account-matrix", nq) == {"path": p, "query": nq}
    assert _legacy("/accounts", {"os": "linux"}) == {"path": "/v2/inventory/matrix", "query": {"tab": "dashboard", "os": "linux"}}


def test_N26_V2落點讀col_val():
    s = _src("components/v2/AccountMatrix.vue")
    assert "qs('col')" in s and "qs('val')" in s and "qs('os')" in s


# ── 舊版外框（layouts/default.vue）殘留兩個 V1 連結：台數口徑明細、操作紀錄 ─────────

@pytest.mark.parametrize("old, new", [
    ("/machine-key", "/v2/reconcile?tab=rule"),
    ("/activity", "/v2/settings?st=activity"),
])
def test_舊版外框連結改V2且舊網址轉到同一頁(old, new):
    s = _src("layouts/default.vue")
    assert f'to="{new}"' in s, new
    assert f'to="{old}"' not in s, old
    p, q = _split(new)
    assert _legacy(old, {}) == {"path": p, "query": q}
