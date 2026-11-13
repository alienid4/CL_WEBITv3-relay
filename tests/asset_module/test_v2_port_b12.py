"""V2 移植清單第 12 批起（2.048.23 起）：V30 EOS、V21 新增資產、V20 資產明細……改 V2 原生（純搬移、行為不變）。
說明見 docs/ui-v2/V2移植_B區_批12_2026-11-05.md。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
BE = ROOT / "APP" / "asset-module" / "backend"
POL = (BE / "access_policy.py").read_text(encoding="utf-8")


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def _code(rel: str) -> str:
    return "\n".join(l for l in _src(rel).splitlines() if not l.lstrip().startswith("//"))


def _ported() -> str:
    m = _src("utils/v2LegacyMap.ts")
    return m[m.index("export const PORTED"):m.index("export function isPorted")]


def _set(name: str) -> str:
    s = POL[POL.index(f"{name}: frozenset"):]
    return s[:s.index("\n)\n")]


ADM_EXP = POL[POL.index("_ADMIN_EXPORTS = ["):POL.index("_USER_EXPORTS = [")]
USR_EXP = POL[POL.index("_USER_EXPORTS = ["):POL.index("EXPORT_LEVEL: dict")]


# ===== 第 12 批（2.048.23）：V30 EOS =====

def test_V30_改V2原生_不再嵌入舊頁():
    p = _src("pages/v2/eos.vue")
    assert '<ClientOnly v-else><V2EosLife :key="tab" :view="(tab as any)" /></ClientOnly>' in p
    assert "pages/eos.vue" not in p and "V2Embed" not in p
    assert "'/eos'" in _ported()
    src = _src("components/v2/EosLife.vue")
    assert "<V2Table" in src and "<table" not in src and "~/pages/" not in src and "<InventoryHosts" not in src
    for x in ["'/api/eos/summary'", "'/api/inventory/eos/hosts'", "'/api/assets'", "'/api/eos/category-override'",
              "/api/eos/category-override/${encodeURIComponent(item.name)}", "'/api/normalize/canonical-override'",
              "'/api/normalize/alias'", "'/api/normalize/pending/import'", "/api/normalize/pending/export", "/api/eos/export",
              "/api/inventory/eos/hosts/export", "selectIcsRows", "useLatest"]:
        assert x in src, x


def test_V30_權限跟後端一致():
    usr, adm = _set("USER_WRITE"), _set("ADMIN_WRITE")
    for p in ['"/api/eos/category-override"', '"/api/eos/category-override/{canonical}"',
              '"/api/normalize/canonical-override"', '"/api/normalize/alias"']:
        assert p in usr, p
    assert '"/api/normalize/pending/import"' in adm
    assert '("GET", "/api/eos/export")' in USR_EXP and '("GET", "/api/normalize/pending/export")' in USR_EXP
    assert '("GET", "/api/inventory/{kind}/hosts/export")' in ADM_EXP
    c = _code("components/v2/EosLife.vue")
    assert "...(canWrite.value ? [{ k: 'rename'" in c and "...(canWrite.value ? [{ k: 'cat'" in c      # 改名、分類欄只給一般帳號以上
    assert '<button v-if="canWrite" type="button" class="lnk" @click="startEdit(row)">' in c
    assert '<button v-if="canWrite" type="button" class="lnk" :disabled="suggestBusy[row.name]"' in c
    assert '<label v-if="isAdmin" class="btn2 file"' in c and '<span v-else class="muted">匯入限管理員</span>' in c
    assert '<a v-if="canWrite" class="btn2" :href="`${apiBase}/api/eos/export`"' in c
    assert '<a v-if="canWrite" class="btn2" :href="`${apiBase}/api/normalize/pending/export`"' in c
    assert '<a v-if="isAdmin" class="btn2" :href="`${apiBase}/api/inventory/eos/hosts/export`"' in c


def test_V30_確認步驟照舊():
    c = _code("components/v2/EosLife.vue")
    assert "if (!window.confirm(`把「${item.name}」歸類改成「${CATEGORY_LABEL[category]}」？（技術判斷不變，只是改變由誰維護）`)) return" in c
    assert "if (!window.confirm(`即將匯入「${file.name}」，把有填「標準名稱」的列補進對應表，空白的列會跳過。確定執行嗎？`)) { input.value = ''; return }" in c
    # 合併列不能改名／補對應／移分類（name 不是真實 canonical）
    assert c.count("!row.merged_versions?.length") >= 4


def test_V30_審查修正_盤點主機匯出限管理員_ICS不給只能看的帳號():
    c = _code("components/v2/EosLife.vue")
    assert 'export-name="EOS_盤點到的主機" :no-export="!isAdmin"' in c
    assert '<button v-if="canWrite" type="button" class="btn2" @click="exportIcs">' in c


# ===== 第 13 批（2.048.25）：V21 新增資產 =====

def test_V21_改V2原生_權限跟後端一致():
    h = _src("pages/v2/hosts.vue")
    assert "<ClientOnly><V2AssetNew /></ClientOnly>" in h and "pages/assets/new.vue" not in h
    assert """<NuxtLink v-if="isAdmin" :to="{ query: { ...route.query, new: '1' } }" class="btn">新增主機</NuxtLink>""" in h
    assert "'/assets/new'" in _ported()
    adm = _set("ADMIN_WRITE")
    for p in ['"/api/assets/manual"', '"/api/assets/scan/discover"', '"/api/assets/scan/import"']:
        assert p in adm, p
    c = _code("components/v2/AssetNew.vue")
    assert "<V2Table" in c and "<table" not in c and "~/pages/" not in c
    assert '<p v-if="!isAdmin" class="muted">新增主機限管理員' in c and "if (a && !loaded) { loaded = true; loadAll() }" in c
    for x in ["'/api/import/field-mapping'", "'/api/assets/manual/field-options'", "'/api/os-catalog'", "'/api/assets/manual/field-groups'",
              "'/api/segments/tree'", "'/api/segments/ips'", "'/api/assets/manual'", "/provision-attachment`", "'/api/assets/scan/discover'",
              "'/api/assets/scan/import'", "useLatest"]:
        assert x in c, x
    assert "manualError.value = '資產序號必填'" in c and "manualError.value = '依申請單轉錄時，單據編號必填'" in c
    assert "const VM_HIDDEN = ['rack_no', 'hardware_no']" in c and "fields.is_vm = manualIsVm.value ? '1' : '0'" in c


def test_V21_審查修正_輔助選單分開載_掃描一頁列完():
    c = _code("components/v2/AssetNew.vue")
    assert "Promise.allSettled([" in c and "部分選單讀不到" in c
    assert ':max="Math.max(100, scanRows.length)"' in c
