"""V2 移植第 18 批（2.053.0）：V41 納管漏斗改 V2 原生（components/v2/Funnel.vue）。⚠D1 D4：純搬移——
寫入只能是舊頁原本那幾支、同樣的元件與確認；不准多出新的寫入。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _api_calls(s):
    """(method, path) 集合：apiFetch('/api/..', { method: 'X' })；沒寫 method＝GET。"""
    out = set()
    for m in re.finditer(r"apiFetch(?:<[^>]*>)?\(\s*'(/api/[^']+)'(?:\s*,\s*\{([^}]*))?", s):
        mm = re.search(r"method:\s*'(\w+)'", m.group(2) or "")
        out.add((mm.group(1) if mm else "GET", m.group(1)))
    return out


def test_寫入端點跟舊頁一模一樣_沒有新的():
    old = _api_calls(_src("pages/pipeline.vue"))
    new = _api_calls(_src("components/v2/Funnel.vue"))
    assert new == old, (new - old, old - new)
    assert ("DELETE", "/api/os-type-overrides") in new and ("PUT", "/api/os-type-overrides") in new
    assert ("POST", "/api/tools/alive-check/batch") in new and ("GET", "/api/pipeline") in new


def test_寫入沿用原元件_判準不變():
    f = _src("components/v2/Funnel.vue")
    assert "<BatchOnboardModal" in f and "<RowActions :row=\"row\"" in f      # 單台納管／下線／非納管＝RowActions
    u = _src("utils/funnel.ts")                                             # 2.054.1：判準與送出規則抽到 utils/funnel（vitest 釘住）
    assert "return !!r.ip && r.collect_ok !== 1 && !r.onboard_block && !r.onboard_exempt && r.stage !== 'retired'" in u
    assert ":can-select=\"canBatchOnboard\"" in f
    # 恢復自動＝DELETE 帶同樣三個欄位；改類別 body 同舊頁
    assert "params: { hostname: r.hostname, ip: r.ip, asset_serial: r.asset_serial }" in f
    assert "os_type: value, auto_os_type: r.os_type_auto ?? r.os_type" in f
    assert "scope: '納管漏斗勾選'" in f


def test_表格是V2Table_篩選在網址_沒有V1連結():
    f = _src("components/v2/Funnel.vue")
    assert "<V2Table" in f and "<table" not in f
    for k in ("stage", "drill", "env_group", "loc", "os_type", "fq", "ft"):
        assert f"'{k}'" in f, k
    for old in ("'/adopt'", "'/assets'", "'/services'", "'/accounts'", "'/issues'", "`/assets/"):
        assert old not in f, old


def test_納管作業頁用V2原生_舊網址轉過來():
    o = _src("pages/v2/onboard.vue")
    assert "<V2Funnel />" in o and "pages/pipeline.vue" not in o
    lm = _src("utils/v2LegacyMap.ts")
    assert "  '/pipeline'," in lm.split("export function isPorted")[0]
