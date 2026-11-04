"""V2 移植清單 B 區第 1 批（2.047.0）：V16 操作紀錄、V23 MICS 檢查清單、V32 單台上線檢查、V33 上線檢查清單、
V38 型號規格、V47 報表列印、V48 月報匯出、V53 掃描結果——改 V2 原生（V2Table：可排序、項次、匯出、下鑽），不再嵌入舊頁。
說明見 docs/ui-v2/V2移植_B區_批1_2026-11-04.md。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


# 新元件 → (掛在哪一頁, 那一頁不可以再出現的舊頁 import, 必須打的 API)
PORTS = {
    "components/v2/Activity.vue": ("pages/v2/settings.vue", "pages/activity.vue", ["/api/activity/summary", "/api/activity?"]),
    "components/v2/ModelSpecs.vue": ("pages/v2/settings.vue", "pages/model-specs.vue", ["/api/model-specs"]),
    "components/v2/HostSources.vue": ("pages/v2/report.vue", "pages/reports/host-sources.vue",
                                      ["/api/reports/host-sources", "/api/reports/host-sources/export?", "/api/reports/hardware-master/export"]),
    "components/v2/MonthlyExport.vue": ("pages/v2/report.vue", "pages/reports/monthly.vue",
                                        ["/api/monthly-report/preview", "/api/monthly-report/snapshots", "/api/monthly-report/export"]),
    "components/v2/GoliveList.vue": ("pages/v2/audit.vue", "pages/golive/index.vue", ["/api/golive"]),
    "components/v2/GoliveOne.vue": ("pages/v2/golive/[serial].vue", "pages/golive/[serial].vue", ["/api/golive/", "/item", "/pass"]),
    "components/v2/ScanResults.vue": ("pages/v2/onboard.vue", "pages/scan-results.vue", ["/api/scan/results"]),
    "components/v2/Checklist.vue": ("pages/v2/checklist/[id].vue", "pages/blast/checklist/[id].vue",
                                    ["/api/blast/snapshot/", "/api/blast/checklist/", "/api/blast/checklist/item/"]),
}


def test_每一頁都改成V2原生_不再嵌入舊頁_同一批API():
    for comp, (host, old, apis) in PORTS.items():
        c = _src(comp)
        assert "<V2Table" in c, f"{comp}：要用 V2Table（排序、項次、匯出）"
        for a in apis:
            assert a in c, f"{comp}：舊頁的功能（{a}）要在"
        h = _src(host)
        assert f"import('~/{old}')" not in h, f"{host} 還在嵌入舊頁 {old}"
        assert "~/pages/" not in c, f"{comp} 不可以再 import 舊頁"


def test_沒有V1網址():
    v1 = re.compile(r"""(?:to|href)=["'`]/(?!v2|api|print)[a-z]""")
    for comp in PORTS:
        assert not v1.search(_src(comp)), comp


def test_寫入只給能寫的人():
    g = _src("components/v2/GoliveOne.vue")
    assert 'v-if="!isViewer"' in g and 'v-else-if="isViewer"' in g and "window.confirm(" in g, "通過前確認、只能看的帳號沒有通過與勾選"
    m = _src("components/v2/ModelSpecs.vue")
    assert "r.can_edit && !isViewer.value" in m
    c = _src("components/v2/Checklist.vue")
    assert ':disabled="isViewer' in c
    for comp in ("components/v2/HostSources.vue", "components/v2/MonthlyExport.vue"):
        assert 'v-if="!isViewer"' in _src(comp), f"{comp}：只能看的帳號不能匯出（後端另擋）"


def test_數字可下鑽_網址帶篩選():
    a = _src("components/v2/Activity.vue")
    assert "set({ au: fUser === u.username" in a and "set({ aa:" in a and "exportAll" in a, "帳號卡＝篩選；匯出全部符合條件"
    assert "set({ aa: 'login_failed'" in a, "登入失敗次數點下去看是哪幾次"
    assert "/api/model-specs/coverage-hosts" in _src("components/v2/ModelSpecs.vue"), "台數點下去看是哪幾台（2.047.1 改成同一份計算）"
    assert "setQ({ both:" in _src("components/v2/HostSources.vue")
    assert "router.push(" in _src("components/v2/ScanResults.vue"), "改篩選要能按上一頁回去（舊頁的教訓）"
    assert "setQ({ gopen:" in _src("components/v2/GoliveOne.vue")
    ck = _src("components/v2/Checklist.vue")
    assert "setQ({ cs: 'todo'" in ck and "setQ({ cst: s" in ck, "系統單位、列單位都能點下去看"
    assert "ms: k" in _src("components/v2/MonthlyExport.vue")


def test_清單打勾():
    lst = (ROOT / "docs" / "V2移植清單.md").read_text(encoding="utf-8")
    for v in ("V16", "V23", "V32", "V33", "V38", "V47", "V48", "V53"):
        assert re.search(rf"^- \[x\] \*\*{v}\*\*", lst, re.M), v
