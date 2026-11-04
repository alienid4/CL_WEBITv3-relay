"""2.020.x 第九批：Codex 驗收——被 V2 嵌入的舊頁，只能看的帳號不顯示寫入按鈕；舊表格補排序／匯出／項次。
後端本來就擋 403，這裡只釘前端不讓人白按。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _script(s):
    return s.split("</script>")[0]


def _line_has(s, mark, needle="isViewer"):
    lines = s.splitlines()
    idx = [i for i, ln in enumerate(lines) if mark in ln]
    assert idx, mark
    # 按鈕標籤可能折成兩行：本行或前一行要有 isViewer
    assert any(needle in lines[i] or (i and needle in lines[i - 1]) for i in idx), (mark, "沒有 isViewer")


# 寫入入口（該行必須帶 isViewer）
HIDE = {
    "pages/data-quality.vue": ['@click="doMergeApply"'],
    "pages/health-check.vue": ['@click="run"', '@click="baselineRun"'],
    "pages/collect-account.vue": ['@click="probe"', '@click="switchOne(d)"', '@click="revertOne(d)"'],
    "pages/golive/[serial].vue": ['v-else-if="isViewer"'],
    "pages/ocp.vue": [':disabled="isViewer || savingRole', ':disabled="isViewer || movingNode'],
    "pages/drift.vue": ['@click="recheck"', 'class="btn-row"'],
    "pages/scan-shells.vue": ['@click="confirming = true"', '@click="doRestore(h)"'],
    "pages/reset.vue": ['v-if="!isViewer"'],
    "pages/hmc.vue": ['@click="askManual"', '@click="askSingle(c.ip)"'],
    "pages/san.vue": ['@click="healEnv"', '@click="openImport"', '@click="askBatch"', '@click="askSingle(s.ip)"'],
    "pages/relocation.vue": ['@click="collectSpec"', 'class="btn primary imp"'],
    "pages/config-audit.vue": ['accept=".txt,text/plain"'],
    "pages/reports/classify.vue": ['@click="applyBatch(false)"', '@click="applyBatch(true)"', 'accept=".xlsx"'],
    "pages/reports/system-group.vue": ['@click="saveSnapshot"', '@click="!isViewer && startEdit('],
}


def test_被嵌入的舊頁只能看的帳號不顯示寫入按鈕():
    for path, marks in HIDE.items():
        s = _src(path)
        assert "useAuth()" in _script(s) and "isViewer" in _script(s), path
        for m in marks:
            _line_has(s, m)


def test_上線檢查單台頁勾選與通過只給能寫的人_並有項次與排序():
    s = _src("pages/golive/[serial].vue")
    assert s.count("v-if=\"!isPassed && !isViewer\"") == 1       # 通過鈕
    assert 'v-else-if="isViewer"' in s                          # 勾選鈕整排換成「只能看」
    assert s.index('v-else-if="isViewer"') < s.index("@click=\"setVerdict(it, 'pass')\"")
    assert '<SortTh k="no"' in s and "{{ it.no }}" in s
    # 勾選／填寫型互動表格：不做匯出（逐項勾選是操作而不是報表），見回報說明
    assert "exportTable" not in s


def test_收集帳號遷移兩張表有排序_匯出_項次():
    s = _src("pages/collect-account.vue")
    assert "useTableExport()" in s and "exportPlan" in s and "exportDetail" in s
    assert '<SortTh k="no" :active="pSortKey"' in s and "pSorted" in s     # 移除計畫表
    assert '<SortTh k="no" :active="sortKey"' in s                         # 切換明細表
    assert "{{ i.no }}" in s and "{{ d.no }}" in s


def test_業務系統排行表有排序_匯出_項次():
    s = _src("pages/reports/business-systems.vue")
    assert "useTableExport()" in s and "exportSystems" in s and "exportDrill" in s
    assert '<SortTh k="no"' in s and "{{ s.no }}" in s and "{{ r.no }}" in s
    assert 'v-if="showAll && !isViewer"' in s


def test_ocp與掃描結果有排序_匯出_項次():
    o = _src("pages/ocp.vue")
    assert "useTableExport()" in o and "exportList" in o and "exportCross" in o and "exportNodes" in o
    for k, d in (("xKey", "xDir"), ("lKey", "lDir"), ("nKey", "nDir")):
        assert f'<SortTh k="no" :active="{k}"' in o
    assert "{{ n.no }}" in o and "{{ r.no }}" in o
    sc = _src("pages/scan-results.vue")
    assert "useTableExport()" in sc and "exportScan" in sc
    assert '<SortTh k="no"' in sc and "{{ row.no }}" in sc and "isViewer" in _script(sc)
