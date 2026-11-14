"""V2 移植清單 B 區第 2 批（2.047.2 起）：V28 單據檔案室、V55 網段配置、V57 實體機分布、V59 標籤貼紙、V60 機櫃盤點表——
改 V2 原生（V2Table：可排序、項次、匯出、下鑽），不再嵌入舊頁；舊網址轉到 V2、已移植的不吃 legacy=1。
說明見 docs/ui-v2/V2移植_B區_批2_2026-11-04.md。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


# 新元件 → (掛在哪一頁, 那一頁不可以再出現的舊頁／舊元件, 必須打的 API)
PORTS = {
    "components/v2/Documents.vue": ("pages/v2/settings.vue", "import('~/pages/documents.vue')",
                                    ["/api/documents", "/api/documents/import", "/review", "/bind", "/download"]),
    "components/v2/Segments.vue": ("pages/v2/settings.vue", "import('~/pages/segments.vue')", ["/api/segments", "/api/segments/ips"]),
    "components/v2/PhysDist.vue": ("pages/v2/dist.vue", "<PhysicalDist", ["/api/reports/physical-distribution", "/api/reports/physical-distribution/drill"]),
}


def test_每一頁都改成V2原生_不再嵌入舊頁_同一批API():
    for comp, (host, old, apis) in PORTS.items():
        c = _src(comp)
        assert "<V2Table" in c, f"{comp}：要用 V2Table（排序、項次、匯出）"
        for a in apis:
            assert a in c, f"{comp}：舊頁的功能（{a}）要在"
        assert old not in _src(host), f"{host} 還在嵌入舊的 {old}"
        assert "~/pages/" not in c


def test_列印頁搬到v2_連結跟著改():
    for p in ("pages/v2/print/labels.vue", "pages/v2/print/rack-sheet.vue"):
        assert (FE / p).exists(), p
        assert "layout: false" in _src(p), f"{p}：純列印頁沒有外框"
    assert "<V2Table" in _src("pages/v2/print/labels.vue") and "selectable" in _src("pages/v2/print/labels.vue"), "挑選清單可排序、勾選"
    for p in ("pages/v2/inventory/rack.vue", "components/RackElevation.vue", "components/SystemMap.vue"):
        s = _src(p)
        assert "`/print/" not in s and "`/v2/print/" in s, p
    m = _src("utils/v2LegacyMap.ts")
    assert "NEVER_REDIRECT = ['/login']" in m
    assert "'/print/labels': q => to('/v2/print/labels', flat(q))" in m
    assert "['/v2/print/', 'topology']" in _src("utils/featureRoutes.ts"), "功能開關也要管到新網址"


def test_已移植的加進PORTED():
    m = _src("utils/v2LegacyMap.ts")
    block = m[m.index("export const PORTED"):m.index("export function isPorted")]
    for x in ("'/segments'", "'/documents'", "'/reports/physical-distribution'", "'/print/labels'", "'/print/rack-sheet'"):
        assert x in block, x


def test_沒有V1網址():
    v1 = re.compile(r"""(?:to|href)=["'`]/(?!v2|api)[a-z]""")
    for comp in PORTS:
        assert not v1.search(_src(comp)), comp


def test_寫入只給能寫的人():
    d = _src("components/v2/Documents.vue")
    assert '<label v-if="isAdmin" class="upload">' in d and 'data-testid="doc-import-admin-only"' in d and "guardedFetch(`/api/documents/${d.id}/review`" in d
    assert '<template v-if="!isViewer">' in d, "改綁只給能寫的人"


def test_數字卡下鑽_數字等於筆數():
    s = _src("components/v2/Segments.vue")
    for x in ("only({ ss: 'exclude' })", "only({ sw: 'expanded' })", "only({ sw: 'range' })", "only({ sw: 'unparsed' })"):
        assert x in s, x
    assert "sq: undefined, sr: undefined, se: undefined, su: undefined, ss: undefined, sw: undefined" in s
    assert "setQ({ cidr:" in s, "已登記台數點下去看是哪幾個 IP"
    assert "openDrill('room'" in _src("components/v2/PhysDist.vue")


def test_條件一改就重抓的都只套最新那一次():
    for comp in ("components/v2/ModelSpecs.vue", "components/v2/Activity.vue", "components/v2/Segments.vue",
                 "components/v2/PhysDist.vue", "components/v2/Documents.vue"):
        c = _src(comp)
        assert "useLatest()" in c and "if (!isLatest()) return" in c, comp


# ---- 第 2 批後半（2.047.3）：V18 異常報告、V19 架構圖、V26 聯絡資訊、V40 OCP 逐台明細
PORTS_B = {
    "components/v2/Anomalies.vue": ("pages/v2/changes.vue", "import('~/pages/anomalies.vue')",
                                    ["/api/dashboard/stats", "/api/issues", "/api/dashboard/composition", "/api/health/summary", "/api/manage-state"]),
    "components/v2/ArchDiagram.vue": ("pages/v2/arch.vue", "import('~/pages/architecture.vue')", ["/api/arch/vcenter", "/api/arch/ocp", "/api/arch/ftp"]),
    "components/v2/OcpDetail.vue": ("pages/v2/arch.vue", "import('~/pages/ocp.vue')", ["/api/arch/ocp", "/api/arch/ocp/role", "/api/arch/ocp/cluster"]),
    "components/v2/Contacts.vue": ("pages/v2/settings.vue", "import('~/pages/contacts.vue')",
                                   ["/api/contacts/todo", "/api/contacts/entries", "/api/contacts/entry", "/api/contacts/oncall", "/api/contacts/audit",
                                    "/api/contracts", "/api/contracts/preview", "/api/contracts/import", "/api/contacts/roster/preview",
                                    "/api/contacts/roster/import", "/api/contacts/roster/template", "/api/night/settings"]),
}


def test_後半_每一頁都改成V2原生_同一批API():
    for comp, (host, old, apis) in PORTS_B.items():
        c = _src(comp)
        assert "<V2Table" in c, comp
        for a in apis:
            assert a in c, f"{comp}：舊頁的功能（{a}）要在"
        assert old not in _src(host), f"{host} 還在嵌入 {old}"
        assert "~/pages/" not in c
    assert "<V2Embed" not in _src("pages/v2/arch.vue"), "架構圖頁不再嵌入舊頁"
    m = _src("utils/v2LegacyMap.ts")
    block = m[m.index("export const PORTED"):m.index("export function isPorted")]
    for x in ("'/anomalies'", "'/architecture'", "'/ocp'", "'/contacts'"):
        assert x in block, x


def test_後半_沒有V1網址():
    v1 = re.compile(r"""(?:to|href)=["'`]/(?!v2|api)[a-z]""")
    for comp in PORTS_B:
        s = _src(comp)
        assert not v1.search(s), comp
        assert "`/assets/" not in s and "'/ocp'" not in s and "`/topology" not in s, comp


def test_後半_寫入只給能寫的人():
    o = _src("components/v2/OcpDetail.vue")
    assert o.count('v-if="!isViewer && row.asset_serial"') == 2 and "guardedFetch(url, { method: 'PUT'" in o
    c = _src("components/v2/Contacts.vue")
    assert "const canEdit = computed(() => canEditApi.value && !isViewer.value)" in c
    assert c.count('v-if="canEdit"') >= 4 and "window.confirm(" in c and "apiFetch('/api/contacts/entry'" not in c, "寫入走 guardedFetch、刪除與匯入前確認"


def test_後半_數字可下鑽():
    a = _src("components/v2/Anomalies.vue")
    assert "openGroup(ag === row.key ? undefined : row.key)" in a and "drillRows" in a, "待處理台數點下去列出是哪幾台"
    assert "V2.adopt" in a and "V2.osUnknown" in a, "舊頁連 V1 的地方都換成 V2 對應頁"
    d = _src("components/v2/ArchDiagram.vue")
    assert "openEsxi(vcc === row.cluster ? undefined : row.cluster)" in d and "ocpLink({ cluster: c.cluster, role: String(role) })" in d
    c = _src("components/v2/Contacts.vue")
    assert "toggleRow(row.rk)" in c and 'data-testid="contact-machines"' in c


def test_2047_4_異常報告三個數字本頁下鑽_同一份計算():
    a = _src("components/v2/Anomalies.vue")
    assert "'/api/dashboard/vcenter-verified'" in a and "'/api/health/drill'" in a
    assert "ad: 'vc' }" in a and "openAd('health')" in a and "openAd(`issue:${row.k}`)" in a
    assert "/v2/hosts?tab=vm" not in a, "vCenter 校正不再丟到虛擬機清單（那不是同一份）"
    assert "if (!isLatest()) return" in a[a.index("watch(ad,"):]


def test_2047_4_資料匯入頁有合約與值班名冊兩列():
    s = _src("pages/import.vue")
    for tid, to in (("import-contracts-row", "/v2/settings?st=contacts&tab=import"), ("import-roster-row", "/v2/settings?st=contacts&tab=oncall")):
        i = s.index(f'data-testid="{tid}"')
        row = s[i:s.index("</tr>", i)]
        assert f'to="{to}"' in row and "last_imported_at" in row, tid
    assert "'/api/contacts/import-status'" in s


def test_資料匯入頁有單據那一列():
    s = _src("pages/import.vue")
    i = s.index('data-testid="import-docs-row"')
    row = s[i:s.index("</tr>", i)]
    assert 'to="/v2/settings?st=documents"' in row and "docSum?.last_imported_at" in row
    assert "'/api/documents/summary'" in s
