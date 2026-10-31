"""2.007.58 舊版翻新第三批（一）：9.2 明細表人工指定角色、9.1「重複登記合併」直接嵌入既有畫面。
釘住：不再連回舊頁、寫入只走既有 API（後端的管理員／稽核／可還原邏輯不在這裡重寫）、批次上限與確認。"""
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def test_9_2_人工指定在新版明細表():
    r = _src("pages/v2/reconcile.vue")
    assert "/api/count-bridge/override" in r and "method: 'PUT'" in r
    assert 'to="/count-bridge"' not in r and "舊版 7-8" not in r
    assert "br.can_edit" in r                       # 只有管理員看得到（後端另外擋）
    assert "PICK_MAX = 20" in r and "window.confirm" in r
    assert "指定角色要填原因" in r                   # 指定時原因必填，清除不用
    assert "itemsKey.value = ''" in r               # 存完要重抓明細，不然畫面是舊的
    assert "[401, 403, 428, 429]" in r              # 權限／密碼錯誤就停，不連問 20 次管理密碼
    assert "br.value = await apiFetch" in r         # 存完要重抓對帳總表的數字


def test_9_1_重複登記合併嵌入既有畫面():
    d = _src("pages/v2/datafix.vue")
    assert "import('~/pages/scan-shells.vue')" in d and "<ScanShells embedded />" in d
    assert 'to="/scan-shells"' not in d
    assert "/api/scan-shells/apply" not in d        # 併回的寫入留在既有元件，這裡不自己打


def test_7_2_收集身分設定嵌入頁籤():
    a = _src("pages/v2/inventory/account.vue")
    assert "label: '收集身分'" in a and "import('~/pages/account-ops.vue')" in a and "<AccountOps />" in a
    assert 'to="/account-ops"' not in a


def test_2_1_新增主機嵌入主機查詢頁():
    h = _src("pages/v2/hosts.vue")
    assert "import('~/pages/assets/new.vue')" in h and "<NewAsset />" in h
    assert 'to="/assets/new"' not in h
    assert "route.query.new === '1'" in h


def test_新版左上角要看得到版本號與更版時間():
    """使用者 2026-10-29：「新版的現在都沒有版本號在上面了，這是我判斷有沒有升版很重要的一個過程」。
    放在左上品牌區（右上角在窄視窗會被擠出畫面）。"""
    v = _src("layouts/v2.vue")
    assert 'class="verline"' in v and "started_at" in v and "更版" in v
    assert v.index('class="verline"') < v.index('v-for="g in V2_NAV"')      # 在選單上方，不是在最底


def test_系統設定開發工具不再有連線架構圖頁籤():
    """使用者 2026-10-29：「架構圖蠻爛的，簡直是廢話」——一個中心連 175 個網段只是一團毛球，資訊都在「連線設定」表格裡。"""
    s = _src("pages/v2/settings.vue")
    assert "label: '架構圖'" not in s
    assert "label: '連線設定'" in s and "label: '功能模組管理'" in s


def test_6_1_開始收集與PushAgent在新版畫面():
    o = _src("pages/v2/onboard.vue")
    assert "label: '開始收集'" in o and "import('~/pages/adopt.vue')" in o and '<AdoptCollect only="collect" />' in o
    assert 'to="/adopt"' not in o
    assert "/api/collect/agent-package" in o and "pkgOpen" in o
    a = _src("pages/adopt.vue")
    assert "defineProps<{ only?: 'collect' }>()" in a and "!selected && !only" in a


def test_舊版清單批1_唯讀頁嵌入新版():
    """2.008.8：異常報告→1.2、數字健檢→9.2、主機系統總覽／報表列印→10.1（只讀，直接嵌入既有畫面）。"""
    c = _src("pages/v2/changes.vue")
    assert "label: '異常報告'" in c and "import('~/pages/anomalies.vue')" in c
    r = _src("pages/v2/reconcile.vue")
    assert "label: '數字健檢'" in r and "import('~/pages/number-check.vue')" in r
    p = _src("pages/v2/report.vue")
    assert "label: '主機系統總覽'" in p and "label: '報表列印'" in p
    assert "import('~/pages/reports/system-overview.vue')" in p and "import('~/pages/reports/host-sources.vue')" in p
