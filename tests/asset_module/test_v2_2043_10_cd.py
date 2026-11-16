"""2.043.10：V2 移植清單 C 區 V62／V64／V67、D 區 V72／V73 搬到 V2（舊版入口拿掉）。
釘「V2 畫面確實接上舊頁有的那些功能／API」，免得之後有人改掉又沒人發現。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _r(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_V62_影響範圍查詢頁籤():
    idx = _r("pages/v2/incident/index.vue")
    assert "{ k: 'impact', label: '影響範圍查詢' }" in idx and "<V2ImpactDetail" in idx and "'/api/blast/systems'" in idx
    assert "/blast?legacy=1" not in idx
    d = _r("components/v2/ImpactDetail.vue")
    for s in ("/api/blast/impact/csv", "/api/blast/snapshot/${viewing.value.id}/csv", "'/api/blast/snapshots'", "'/api/blast/snapshot'",
              "'/api/blast/observed-rows'", "admin_ssh_auto", "ambiguous", "excluded", "notify"):
        assert s in d, s
    assert 'v-if="!isViewer && !viewing"' in d, "存快照只給能寫的人"


def test_V64_補登分派():
    v = _r("pages/v2/datafix.vue")
    for s in ("df.value?.systems", "q('sys')", "trendPts", "&export=1", "dfExport("):
        assert s in v, s
    assert "/data-fix?legacy=1" not in v


def test_V67_單一事件():
    v = _r("pages/v2/incident/[id].vue")
    for s in ("printFind", "M_FILTERS", "'single'", "'psu_unknown'", "vm_unplaced", "'core'", "<RackElevation", "/api/incidents/${eid.value}/runs",
              "/api/incidents/runs/", "npOnly"):
        assert s in v, s
    assert "?legacy=1" not in v


def test_V72_生命週期():
    o = _r("pages/v2/onboard.vue")
    assert "{ k: 'life', label: '新設備生命週期' }" in o and "<V2Lifecycle" in o and "/lifecycle?legacy=1" not in o
    c = _r("components/v2/Lifecycle.vue")
    assert "'/api/lifecycle'" in c and "stuck_days" in c and "export-name" in c


def test_V73_功能查詢():
    m = _r("pages/v2/manual.vue")
    assert "<V2Features" in m and "view: 'features'" in m and "/features?legacy=1" not in m
    u = _r("utils/v2LegacyMap.ts")
    assert "'/lifecycle': () => to('/v2/onboard', { tab: 'life' })" in u and "'/features': q => to('/v2/manual'" in u
    assert "NEVER_REDIRECT = ['/login']" in u      # 2.047.2：/print/labels、/print/rack-sheet 搬到 /v2/print/，改成轉
