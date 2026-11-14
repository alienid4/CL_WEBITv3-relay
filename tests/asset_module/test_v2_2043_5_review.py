"""2.043.5：GPT 獨立審查 2.043.4 那批的 7 條（掃描缺口匯出、功能開關、各櫃進度下鑽、viewer）。

純函式的行為在前端 vitest（scanGapViews／featureRoutes／rackDiffFilter）與 test_scan_gaps.py；
這裡釘「畫面確實接上那些函式」，免得函式對了、頁面卻還用舊寫法。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
BE = ROOT / "APP" / "asset-module" / "backend"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_1_掃描缺口CSV帶頁籤給後端_畫面與匯出同一個判斷():
    v = _read(FE / "pages" / "v2" / "onboard.vue")
    assert "gapInView(r, gapView(q('gk')))" in v, "畫面列要用 scanGapViews 的判斷"
    assert "gapCsvQuery(q('gk'))" in v, "CSV 要帶同一個頁籤給後端"
    r = _read(BE / "routers" / "reports.py")
    seg = r[r.index('"/api/reports/scan-gaps/export"'):][:1500]
    assert 'view: str = ""' in seg and "view=view" in seg, "後端匯出要吃 view 並交給 filter_rows"


def test_6_匯出有VLAN畫面也要有VLAN欄():
    v = _read(FE / "pages" / "v2" / "onboard.vue")
    m = re.search(r"exportTable\('掃描缺口', \[([^\]]*)\]", v)
    assert m and "'VLAN'" in m.group(1)
    table = v[v.index('empty="沒有掃描缺口"'):][:1200]
    assert "{ k: 'vlan', label: 'VLAN'" in table, "匯出有的欄位畫面也要看得到（送出前能核對）"


def test_7_viewer看不到環境寫入按鈕():
    v = _read(FE / "pages" / "v2" / "onboard.vue")
    i = v.index("@click=\"toggleGapEnv(e)\"")
    before = v[max(0, i - 400):i]
    assert '<template v-if="!isViewer">' in before, "只能看的帳號用 v-if 隱藏，不是 disabled"


def test_3_停用提示在V2外框():
    lay = _read(FE / "layouts" / "v2.vue")
    assert "route.query.disabled" in lay and "功能已停用" in lay
    mw = _read(FE / "middleware" / "features.global.ts")
    assert "isAdmin.value" in mw, "停用導向要分管理員／非管理員"


def test_4_5_各櫃進度帶機櫃與種類_填寫與差異頁會讀():
    p = _read(FE / "components" / "v2" / "RackProgress.vue")
    assert "room: r.room, rack: r.rack" in p and "rackDiffQuery(" in p
    for dk in ("'gone'", "'bad'", "'extra'", "'open'"):
        assert f"diffUrl(r, {dk})" in p, dk
    f = _read(FE / "components" / "v2" / "RackFillSheet.vue")
    assert "route.query.room" in f and "route.query.rack" in f
    flow = _read(FE / "components" / "v2" / "InventoryFlow.vue")
    assert "inRackDiff(d, dRk.value, dDk.value)" in flow
    # 差異種類名稱要跟後端 DIFF_LABEL 一致
    labels = _read(BE / "rack_inventory.py")
    util = _read(FE / "utils" / "rackDiffFilter.ts")
    for lab in re.findall(r"'([^']+)'", util[util.index("RACK_DIFF_LABELS"):util.index("export const rackKey")]):
        if lab in ("gone", "bad", "extra"):
            continue
        assert f'"{lab}"' in labels, f"前端用的差異種類「{lab}」後端 DIFF_LABEL 沒有"


def test_2_納管作業的掃描結果與掃描缺口各歸原本的開關():
    fr = _read(FE / "utils" / "featureRoutes.ts")
    i_scan = fr.index("['/v2/onboard', 'dashboard', { key: 'tab', vals: ['scan'] }]")
    i_gaps = fr.index("['/v2/onboard', 'report_system', { key: 'tab', vals: ['gaps'] }]")
    i_all = fr.index("['/v2/onboard', 'adopt']")
    assert i_scan < i_all and i_gaps < i_all, "頁籤專屬的要排在整頁那條前面（先寫先贏）"
