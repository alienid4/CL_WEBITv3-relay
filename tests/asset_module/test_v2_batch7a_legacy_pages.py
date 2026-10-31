"""2.010.x 舊版翻新第七批（a）：資料品質、業務系統排行、存在可信度、OCP 逐台明細、對帳明細、納管漏斗、掃描結果、
上線檢查單台、資產明細 全部進 V2 頁籤／V2 路由。
釘住：嵌入關係（舊頁檔案不搬、寫入邏輯不動）、V2 頁面不殘留舊網址、舊網址→V2 對照表與 middleware、選單入口。"""
import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def test_被嵌入的舊頁檔案都還在原處():
    for p in ["pages/data-quality.vue", "pages/reports/business-systems.vue", "pages/existence.vue", "pages/ocp.vue",
              "pages/manage-state-detail.vue", "pages/pipeline.vue", "pages/scan-results.vue",
              "pages/golive/[serial].vue", "pages/assets/[serial].vue"]:
        assert (FE / p).exists(), p
    for p in ["pages/v2/golive/[serial].vue", "pages/v2/asset/[serial].vue", "utils/v2LegacyMap.ts", "middleware/v2-legacy.global.ts"]:
        assert (FE / p).exists(), p


def test_資料品質嵌入_待補資料頁籤_不碰寫入():
    d = _src("pages/v2/datafix.vue")
    assert "import('~/pages/data-quality.vue')" in d and "<DataQuality />" in d
    assert "{ k: 'quality', label: '資料品質' }" in d
    # 人員合併等寫入全留在舊畫面：新版頁不得自己打資料品質的寫入端點
    assert "/api/data-quality" not in d and "/api/quality" not in d and "name-merge" not in d and "/merge" not in d


def test_業務系統排行嵌入_業務系統頁籤_不碰匯入():
    s = _src("pages/v2/systems.vue")
    assert "import('~/pages/reports/business-systems.vue')" in s and "<Rank />" in s
    assert "{ k: 'rank'" in s and "tab === 'rank'" in s
    assert "system-category/import" not in s and "method: 'POST'" not in s


def test_存在可信度與納管狀態明細在對帳頁():
    r = _src("pages/v2/reconcile.vue")
    assert "import('~/pages/existence.vue')" in r and "<Existence />" in r
    assert "import('~/pages/manage-state-detail.vue')" in r and "<StateDetail />" in r
    assert "{ k: 'existence', label: '存在可信度' }" in r and "{ k: 'state', label: '納管狀態明細' }" in r
    # 對帳頁數字卡不再連舊頁
    for old in ["'/manage-state-detail", "'/pipeline", "'/adopt'"]:
        assert old not in r, old
    assert "/v2/reconcile?tab=state&state=" in r and "/v2/onboard?tab=funnel&stage=" in r
    assert "tab: 'state', state: '已納管'" in r        # 直接點頁籤沒帶狀態時有預設，不顯示「沒有指定狀態」


def test_納管漏斗與掃描結果在納管作業頁():
    o = _src("pages/v2/onboard.vue")
    assert "import('~/pages/pipeline.vue')" in o and "<Funnel />" in o
    assert "import('~/pages/scan-results.vue')" in o and "<ScanResults />" in o
    assert "{ k: 'funnel', label: '納管漏斗' }" in o and "{ k: 'scan', label: '掃描結果' }" in o
    assert "`/golive/" not in o and "`/v2/golive/${encodeURIComponent(r.serial)}`" in o


def test_OCP逐台明細在架構圖頁():
    a = _src("pages/v2/arch.vue")
    assert "import('~/pages/ocp.vue')" in a and "<Ocp v-if=\"tab === 'ocpdetail'\" />" in a
    assert "{ k: 'ocpdetail', label: 'OCP 逐台明細' }" in a
    assert "仍在舊版 OCP" not in a


def test_上線檢查單台與資產明細有V2路由():
    g = _src("pages/v2/golive/[serial].vue")
    assert "import('~/pages/golive/[serial].vue')" in g and "layout: 'v2'" in g and "<V2Embed>" in g
    assert "/v2/audit?tab=golive" in g
    a = _src("pages/v2/asset/[serial].vue")
    assert "import('~/pages/assets/[serial].vue')" in a and "layout: 'v2'" in a and "<V2Embed>" in a
    m = _src("pages/v2/machine/[key].vue")
    assert "舊版資產頁" not in m and "/v2/asset/" in m and 'to="`/assets/' not in m


def test_選單有入口():
    n = _src("composables/useNavV2.ts")
    assert "'/v2/asset'" in n and "'/v2/golive'" in n          # 子路由要讓所屬選單項亮起來
    for tab_owner, key in [("pages/v2/datafix.vue", "quality"), ("pages/v2/systems.vue", "rank"), ("pages/v2/reconcile.vue", "existence"),
                           ("pages/v2/reconcile.vue", "state"), ("pages/v2/onboard.vue", "funnel"), ("pages/v2/onboard.vue", "scan"),
                           ("pages/v2/arch.vue", "ocpdetail")]:
        assert f"k: '{key}'" in _src(tab_owner), (tab_owner, key)
    for to in ["/v2/datafix", "/v2/systems", "/v2/reconcile", "/v2/onboard", "/v2/arch", "/v2/audit"]:
        assert f"to: '{to}'" in n, to


# ===== 舊網址 → V2 對照表 + middleware =====
def test_對照表涵蓋所有搬進來的舊網址():
    m = _src("utils/v2LegacyMap.ts")
    for old, new in [("/existence", "/v2/reconcile"), ("/manage-state-detail", "/v2/reconcile"), ("/number-check", "/v2/reconcile"),
                     ("/pipeline", "/v2/onboard"), ("/scan-results", "/v2/onboard"), ("/ocp", "/v2/arch"), ("/architecture", "/v2/arch"),
                     ("/data-quality", "/v2/datafix"), ("/corrections", "/v2/datafix"), ("/reports/business-systems", "/v2/systems"),
                     ("/golive", "/v2/audit")]:
        assert re.search(rf"p === '{re.escape(old)}'\) return \{{ path: '{re.escape(new)}'", m), old
    assert "/v2/golive/${m[1]}" in m and "/v2/machine/${m[1]}" in m and "/v2/asset/${m[1]}" in m
    assert "m[1] !== 'new'" in m                                # /assets/new（新增主機）不轉


def test_middleware只從V2出發才轉():
    w = _src("middleware/v2-legacy.global.ts")
    assert "from.path.startsWith('/v2')" in w and "to.path.startsWith('/v2')" in w      # 直接開舊網址、舊版互點不受影響
    assert "v2LegacyTarget" in w and "navigateTo" in w


def test_V2頁面沒有殘留會直接帶出新版的舊網址():
    """已有 V2 對應頁的舊網址不准直接寫在 V2 頁面裡（舊頁裡的連結由 middleware 轉；V2 頁自己的一律寫 V2 網址）。"""
    pat = re.compile(r"""["'`]/(existence|manage-state-detail|pipeline|scan-results|ocp|data-quality|corrections|golive|reports/business-systems|number-check|count-bridge)\b""")
    bad = []
    for f in list((FE / "pages" / "v2").rglob("*.vue")) + list((FE / "components" / "v2").rglob("*.vue")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and not line.lstrip().startswith("//"):
                bad.append(f"{f.relative_to(FE)}:{i}")
    assert not bad, bad
