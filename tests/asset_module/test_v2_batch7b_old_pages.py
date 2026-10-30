"""2.011.x 舊版清單批 7b：剩下的舊頁搬進 V2（能嵌入就嵌入，只動連結不動邏輯）。
釘住：每個頁都從 V2 頁籤／頁面找得到、不再連回舊版、批次健檢（D2）只嵌入舊頁——不碰任何下指令的程式。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def test_機房網段篩選在新版平面圖():
    d = _src("pages/v2/datacenter.vue")
    assert "const segPick = ref('')" in d and "segOptions" in d and "segKeys" in d
    assert 'aria-label="網段"' in d and "segPick = ''" in d          # 有下拉、有清除
    assert "網段篩選仍在舊版頁" not in d
    assert "devOf(k)?.ips" in d and "x.cidr" in d                    # 每台全部 IP 對網段（同舊版口徑）


def test_批次健檢嵌入事件應變_只嵌入不改邏輯():
    i = _src("pages/v2/incident/index.vue")
    assert "import('~/pages/health-check.vue')" in i and "{ k: 'health', label: '批次健檢' }" in i
    assert "<HealthCheck />" in i and "route.query.tab === 'health'" in i
    # D2 危險區：新版只是外框，不得出現任何自己下收集指令的呼叫
    assert "/api/health-check" not in i
    h = _src("pages/health-check.vue")
    assert "/api/health-check/run" in h                              # 舊頁邏輯原封不動


def test_收集失敗紀錄頁籤():
    o = _src("pages/v2/onboard.vue")
    assert "{ k: 'collectlog', label: '收集紀錄' }" in o and "import('~/components/CollectLog.vue')" in o
    assert "tab === 'collectlog'" in o


def test_單據檔案室在系統設定():
    s = _src("pages/v2/settings.vue")
    assert "import('~/pages/documents.vue')" in s and "{ k: 'documents', label: '單據檔案室' }" in s
    assert "BASE_TABS.slice(0, 11)" in s and "BASE_TABS.slice(11)" in s      # 插了一個頁籤，切點要跟著動，不然「開發工具」跑到外部整合前面


def test_帳號合規矩陣獨立頁_頁內連結留在新版():
    a = _src("pages/v2/inventory/account.vue")
    assert "{ k: 'matrix', label: '合規矩陣' }" in a and "/v2/inventory/matrix" in a
    m = _src("pages/v2/inventory/matrix.vue")
    assert "import('~/pages/account-matrix.vue')" in m and "<AccountMatrix />" in m
    am = _src("pages/account-matrix.vue")
    assert "path: '/account-matrix'" not in am and "const AM = route.path.startsWith('/v2/')" in am
    dash = _src("components/AccountDashboard.vue")
    assert "`/account-matrix?" not in dash and "const AM = route.path.startsWith('/v2/')" in dash
    assert "'/v2/inventory/matrix'" in _src("composables/useNavV2.ts")      # 選單亮 7.2


def test_MICS檢查清單嵌入新版():
    p = _src("pages/v2/checklist/[id].vue")
    assert "import('~/pages/blast/checklist/[id].vue')" in p and "<Checklist />" in p
    old = _src("pages/blast/checklist/[id].vue")
    assert "route.path.startsWith('/v2/')" in old and "hostLink(item.asset_serial)" in old
    assert "/api/blast/checklist/item/" in old                              # 就地存的 API 不變


def test_已有新版取代的舊頁_維持橫幅指向新版():
    """生命週期→6.1 新設備上線、系統校正清單→9.1 實測不符、功能查詢→全域搜尋、列印頁→7.2 列印頁籤：不重複搬。"""
    assert '<V2Banner to="/v2/onboard?tab=newdev"' in _src("pages/lifecycle.vue")
    assert '<V2Banner to="/v2/datafix?tab=mismatch"' in _src("pages/corrections.vue")
    assert "/print/rack-sheet" in _src("pages/v2/inventory/rack.vue") and "/print/labels" in _src("pages/v2/inventory/rack.vue")
    assert "EXTRA_FEATURES" in _src("components/v2/Search.vue")


def test_全域搜尋找得到新搬的頁():
    s = _src("components/v2/Search.vue")
    for to in ["/v2/incident?tab=health", "/v2/onboard?tab=collectlog", "/v2/inventory/matrix?tab=matrix", "/v2/settings?st=documents"]:
        assert to in s, to
