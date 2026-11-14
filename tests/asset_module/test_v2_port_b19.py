"""V2 移植第 19 批（2.054.0）：V20 資產明細第 3 批——編輯改 V2 原生（components/v2/AssetEdit.vue）。⚠D4 純搬移：
寫入只有舊頁原本的 PUT /api/assets/{序號}，body 規則同一份；舊頁的編輯入口改連 V2，寫入欄位只有一條路。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
OLD = "pages/assets/[serial].vue"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _calls(s):
    """(method, 路徑樣板)：路徑裡的 ${…} 一律換成 {x}；沒寫 method＝GET。"""
    out = set()
    for m in re.finditer(r"apiFetch(?:<[^>]*>)?\(\s*[`']([^`']+)[`'](?:\s*,\s*\{([^}]*))?", s):
        path = re.sub(r"\$\{[^}]*\}", "{x}", m.group(1)).split("?")[0]
        mm = re.search(r"method:\s*'(\w+)'", m.group(2) or "")
        out.add((mm.group(1) if mm else "GET", path))
    return out


def test_編輯的端點集合跟舊頁一致_沒有新的寫入():
    new = _calls(_src("components/v2/AssetEdit.vue"))
    assert new == {("GET", "/api/assets/{x}"), ("GET", "/api/assets/field-groups"), ("GET", "/api/field-meta"),
                   ("PUT", "/api/assets/{x}")}, new
    old = _calls(_src(OLD))
    assert {c for c in new} <= old, new - old                      # 每一支舊頁都有
    writes_new = {c for c in new if c[0] != "GET"}
    assert writes_new == {("PUT", "/api/assets/{x}")}


def test_送出規則沿用同一份_跟舊頁的寫法一致():
    u = _src("utils/assetEdit.ts")
    o = _src(OLD)
    # 舊頁 saveEdit 的資產狀態規則，原樣在 util
    rule = "if ('asset_status' in f && (f.asset_status || '') === (hw.pending_status || hw.asset_status || '')) delete f.asset_status"
    assert rule in u
    assert "if ('asset_status' in f && (f.asset_status || '') === (hw.pending_status || hw.asset_status || '')) delete f.asset_status" in o
    assert "new Set(['asset_serial', 'id', 'created_at', 'updated_at'])" in u and "new Set(['asset_serial', 'id', 'created_at', 'updated_at'])" in o
    e = _src("components/v2/AssetEdit.vue")
    assert "body: { fields: saveFields(draft, hw.value) }" in e
    assert "isViewer" in e and "編輯限一般帳號以上" in e                          # 後端 USER_WRITE，畫面跟著


def test_舊頁編輯入口改連V2_V2頁edit等於1用新表單():
    o = _src(OLD)
    assert '@click="startEdit">✎ 編輯' not in o
    assert "?edit=1`\">✎ 編輯</NuxtLink>" in o
    p = _src("pages/v2/asset/[serial].vue")
    assert "<V2AssetEdit" in p and "route.query.edit === '1'" in p and "<V2AssetView" in p      # 2.059.2：檢視改 V2 原生（不再嵌入舊頁）


def test_舊頁頁內連結都改成V2網址():
    o = _src(OLD)
    for old in ('to="/assets"', 'to="/adopt"', 'to="/data-quality"', "path: '/existence'", 'to="/scan-shells"',
                'to="/config-audit"', 'to="/architecture"', "`/ocp?", 'to="/corrections"', 'to="/eos"', ':to="`/assets/'):
        assert old not in o, old
    for new in ('to="/v2/audit?tab=config"', 'to="/v2/eos"', 'to="/v2/onboard"', 'to="/v2/datafix?tab=shells"', 'to="/v2/hosts"',
                ":to=\"`/v2/machine/${"):
        assert new in o, new
