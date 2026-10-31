"""2.012.x 第七批（三）：Q91 權限三級的前端後續＋三個舊頁嵌入新版。
釘住：嵌入（不連回舊頁、不改舊頁邏輯、不新增後端）、版型顯示帳號與登出、只能看的帳號不顯示寫入按鈕、
同一個失敗不連跳兩則提示。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def test_收集帳號遷移嵌入系統設定收集身分():
    s = _src("pages/v2/settings.vue")
    assert "import('~/pages/collect-account.vue')" in s
    assert "{ k: 'migration', label: '收集帳號遷移' }" in s
    assert "tab === 'identity' && sub === 'migration'" in s
    # 舊頁不能被改寫：仍是它自己的探測／切換邏輯，這一批沒有在新版重寫任何寫入
    assert "/api/collect-account/migration" not in s
    # 收集身分頁籤本來就是 admin 才看得到，遷移（切換收集帳號）不能放寬
    assert "'identity'" in s.split("const ADMIN_ONLY")[1].split("\n")[0]


def test_資產單台頁與上線檢查單台頁嵌入新版():
    a = _src("pages/v2/asset/[serial].vue")
    assert "import('~/pages/assets/[serial].vue')" in a and "<V2Embed>" in a
    g = _src("pages/v2/golive/[serial].vue")
    assert "import('~/pages/golive/[serial].vue')" in g and "<V2Embed>" in g
    assert "/api/" not in a and "/api/" not in g       # 只嵌入，不在新版頁自己打 API
    nav = _src("composables/useNavV2.ts")
    assert "'/v2/asset'" in nav and "'/v2/golive'" in nav   # 選單反白
    assert "/v2/asset/" in _src("pages/v2/machine/[key].vue")
    assert "/v2/golive/" in _src("pages/v2/onboard.vue")


def test_上線檢查清單在新版內連到新版單台頁():
    i = _src("pages/golive/index.vue")
    assert "startsWith('/v2')" in i and "'/v2/asset'" in i and "'/v2/golive'" in i
    assert "`${assetBase}/${r.asset_serial}`" in i and "`${goliveBase}/${r.asset_serial}`" in i


def test_新版版型顯示帳號等級與登出():
    v = _src("layouts/v2.vue")
    assert "useAuth()" in v and "useServerInfo()" in v
    assert "未登入（登入已停用）" in v
    assert 'v-if="!authDisabled && user"' in v and "handleLogout" in v      # 登入停用時不顯示登出
    assert "只能看" in v and "管理員" in v and "一般" in v
    assert "navigateTo('/login')" in v


# 每個 V2 頁／元件裡的寫入入口，只能看的帳號不該看到（後端另外擋；這裡只是別讓人白按）
HIDE = {
    "pages/v2/machine/[key].vue": ["showPos = true", "showOffline = true", "@click=\"runCheck\""],
    "pages/v2/hosts.vue": ["showOnboard = true", "showOffline = true", "@click=\"addAudit\""],
    "pages/v2/changes.vue": ["@click=\"markRead\""],
    "pages/v2/datafix.vue": ["@click=\"markSynced\"", "@click=\"openReq\""],
    "pages/v2/incident/index.vue": ["@click=\"creating"],
    "pages/v2/inventory/account.vue": ["@submit.prevent=\"applyDisp\""],
    "pages/v2/inventory/rack.vue": ["上傳盤點檔"],
    "pages/v2/inventory/service.vue": ["@click=\"collect\""],
    "pages/v2/inventory/software.vue": ["@click=\"collectSw\""],
    "pages/v2/night.vue": ["@click=\"liveCheck(card!)\""],
    "pages/v2/onboard.vue": ["@click=\"recheck\"", "@submit.prevent=\"saveGapNote\""],
    "pages/v2/risk.vue": ["@click=\"openSet\"", "@click=\"startScan\""],
    "components/v2/HostMonthly.vue": ["上傳 NMON 檔", "@click=\"editing = true\""],
}


def test_只能看的帳號不顯示寫入按鈕():
    for path, marks in HIDE.items():
        s = _src(path)
        assert "isViewer" in s.split("</script>")[0], path
        for m in marks:
            hits = [ln for ln in s.splitlines() if m in ln]
            assert hits, (path, m)
            assert any("isViewer" in ln for ln in hits), (path, m, "該按鈕那一行沒有 !isViewer")


def test_事件單台頁只能看的帳號不能改():
    s = _src("pages/v2/incident/[id].vue")
    assert s.count("isViewer") >= 9
    for m in ['@click="runCheck"', '@click="makeReport"', '@click="addScope({}, true)"']:
        assert any(m in ln and "isViewer" in ln for ln in s.splitlines()), m
    assert s.count(":disabled=\"!open || isViewer\"") == 3        # 檢核清單三個欄位
    assert "<template v-if=\"open && !isViewer\">" in s           # 範圍界定
    # 新增紀錄／結案整塊
    assert s.index("<template v-if=\"!isViewer\">\n          <h3>新增紀錄</h3>") < s.index("@click=\"closeEvent\"")


def test_同一個失敗不連跳兩則提示():
    """apiFetch 對權限不足先跳一則，呼叫端 catch 又跳「XX失敗：同一句」→ 使用者看到兩次。"""
    t = _src("composables/useToast.ts")
    assert "nested" in t and "state.items.splice(state.items.indexOf(dup), 1)" in t
    assert "Math.min(a.length, b.length) >= 8" in t           # 太短的字串不算內含（避免「成功」吃掉別的提示）


def test_新增帳號_已授權_10_30_只有管理員頁籤內且後端限admin():
    """10-30 使用者授權技術決定交 AI／Codex：POST /api/access/users（admin 限定，只能建 user／viewer）。
    表單在 AccessAdmin（設定頁的「權限與下載紀錄」頁籤，本來就只有 admin 看得到）；其餘行為見 test_create_user.py。"""
    backend = ROOT / "APP" / "asset-module" / "backend"
    acc = (backend / "routers" / "access.py").read_text(encoding="utf-8")
    assert '@router.post("/api/access/users")' in acc
    assert acc.count("@router.post") == 1
    ac = _src("components/v2/AccessAdmin.vue")
    assert "新增帳號" in ac and 'type="password"' in ac
    st = _src("pages/v2/settings.vue")
    assert "ADMIN_ONLY" in st and "'access'" in st
