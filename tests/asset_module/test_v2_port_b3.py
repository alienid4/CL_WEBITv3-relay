"""V2 移植清單 B 區第 3 批（2.048.0 起）：V13＋V15 帳號合規矩陣／帳號儀表板、V58 系統主機部署圖。
改 V2 原生（V2Table：可排序、項次、匯出、下鑽），不再嵌入舊頁；舊網址轉到 V2、已移植的不吃 legacy=1。
說明見 docs/ui-v2/V2移植_B區_批3_2026-11-04.md。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def _ported() -> str:
    m = _src("utils/v2LegacyMap.ts")
    return m[m.index("export const PORTED"):m.index("export function isPorted")]


def test_帳號合規矩陣_V2原生_不再嵌入舊頁():
    page = _src("pages/v2/inventory/matrix.vue")
    assert "<V2AccountMatrix />" in page and "~/pages/" not in page
    c = _src("components/v2/AccountMatrix.vue")
    assert "<V2Table" in c and "~/pages/" not in c and "<table" not in c
    # 舊頁的功能都在：三種寫入、四種匯出、反查、涵蓋率、0 筆解釋
    for api in ["/api/accounts/findings/disposition", "/api/accounts/note", "/api/accounts/kind",
                "/api/accounts/export", "/api/accounts/matrix/export", "/api/accounts/findings/export",
                "/api/accounts/by-username", "/api/accounts/coverage", "/api/accounts/search-explain",
                "/api/accounts/inventoried-hosts"]:
        assert api in c, api
    for tab in ["'dashboard'", "'matrix'", "'findings'", "'accounts'"]:
        assert tab in c, tab
    assert "<V2AccountDashboard" in c


def test_帳號儀表板_V2原生_卡片數字等於下鑽():
    d = _src("components/v2/AccountDashboard.vue")
    assert "<V2Table" in d and "<table" not in d
    assert "dashboardChecks" in d and "privBreakdown" in d          # 跟合規表共用同一份判定
    assert "useLatest" in d                                          # 換 OS 分頁重抓只採用最新那一次
    assert "/account-matrix" not in d and "const AM = '/v2/inventory/matrix'" in d
    m = _src("components/v2/AccountMatrix.vue")
    assert "composables/accountCells" in m and "MATRIX_COLS.every(c => !colVal(c.key)" in m
    assert "qs('col') === key ? qs('val')" in m                      # 儀表板中招卡的 col/val 也認


def test_寫入只給有權限的人_匯出不給只能看的帳號():
    c = _src("components/v2/AccountMatrix.vue")
    assert "const { canWrite, isAdmin } = useAuth()" in c
    # 每一個處置下拉、類型下拉都要包在 canWrite 裡
    n = 0
    for m in re.finditer(r'@change="(onStatusChange|saveKind)', c):
        head = c[c.rfind("<select", 0, m.start()):m.start()]
        assert 'v-if="canWrite"' in head, head
        n += 1
    assert n >= 5, n
    # 備註編輯框也是
    for m in re.finditer(r'@keyup\.enter="saveNote', c):
        head = c[c.rfind("<template", 0, m.start()):m.start()]
        assert "canWrite" in head, head
    # 後端匯出 access_policy 是 admin-only（_ADMIN_EXPORTS）：前端也只給管理員，其他人看到「限管理員」
    for comp, apis in (("components/v2/AccountMatrix.vue", ["/api/accounts/findings/export", "/api/accounts/by-username/export"]),
                       ("components/v2/AccountDashboard.vue", ["/api/accounts/inventoried-hosts/export"])):
        s = _src(comp)
        assert "isViewer" not in s, comp
        for api in apis:
            i = s.index(api, s.index("<template>"))
            assert 'v-if="isAdmin' in s[s.rfind("<a", 0, i):i], api
        assert "限管理員" in s, comp
    assert '<span v-if="!isAdmin" class="muted" data-test="admin-only">標準／全匯出／自選欄位限管理員</span>' in c
    pol = (ROOT / "APP" / "asset-module" / "backend" / "access_policy.py").read_text(encoding="utf-8")
    admin = pol[pol.index("_ADMIN_EXPORTS = ["):]
    admin = admin[:admin.index("]\n")]
    for api in ["/api/accounts/export", "/api/accounts/matrix/export", "/api/accounts/findings/export",
                "/api/accounts/by-username/export", "/api/accounts/inventoried-hosts/export"]:
        assert f'"{api}"' in admin, api


def test_條件一改就重抓的地方用useLatest():
    c = _src("components/v2/AccountMatrix.vue")
    assert c.count("useLatest()") >= 4                                # 主清單、反查、0 筆解釋、帳號明細
    assert "if (!isLatest()) return" in c


def test_舊網址轉到V2_加進PORTED():
    block = _ported()
    for x in ("'/account-matrix'", "'/accounts'"):
        assert x in block, x
    m = _src("utils/v2LegacyMap.ts")
    assert "'/accounts': q => to('/v2/inventory/matrix', { tab: 'dashboard', ...pick(q, ['os']) })" in m      # os 照帶


def test_系統主機部署圖_圖照舊_圖下加V2Table清單_列印不印():
    page = _src("pages/v2/system/[id].vue")
    assert "<V2SystemMap " in page and "<V2Embed><SystemMap" not in page
    c = _src("components/v2/SystemMap.vue")
    assert "<table" not in c and "SortTh" not in c and "useSort(" not in c     # 表格全換 V2Table
    assert c.count("<V2Table") >= 9                                          # 圖下清單＋停機＋主機三張＋ESXi＋DS＋上下游＋證據
    assert 'class="glist no-print"' in c and "@media print { .no-print { display: none !important; } }" in c
    for api in ["/api/blast/systems", "/api/blast/system-map", "/api/blast/host-systems", "/api/blast/observed-edge"]:
        assert api in c, api
    assert c.count("useLatest()") >= 3                                       # 換系統、主機搜尋、連線證據
    # 圖下清單的台數下鑽跟圖上同一份主機鍵（筆數＝台數）
    assert "showKeys(`${row.room}／${row.label}`, row.hosts)" in c and "hosts: g.hosts" in c
    assert "'/topology'" in _ported()


def test_沒有V1網址():
    v1 = re.compile(r"""(?:to|href)=["'`]/(?!v2|api)[a-z]""")
    for comp in ("components/v2/AccountMatrix.vue", "components/v2/AccountDashboard.vue", "components/v2/SystemMap.vue"):
        s = _src(comp)
        assert not v1.search(s), comp
        assert "`/assets/" not in s and "'/pipeline'" not in s, comp
