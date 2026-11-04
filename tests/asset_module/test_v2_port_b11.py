"""V2 移植清單第 11 批起（2.048.21 起）：V25 組態檢核、V34 批次健檢……改 V2 原生（純搬移、行為不變）。
說明見 docs/ui-v2/V2移植_B區_批11_2026-11-05.md。"""
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


EXP = POL[POL.index("_ADMIN_EXPORTS"):POL.index("_USER_EXPORTS")]


# ===== 第 11 批（2.048.21）：V25 組態檢核、V34 批次健檢 =====

def test_V25_V34_改V2原生_不再嵌入舊頁():
    a = _src("pages/v2/audit.vue")
    assert "<ClientOnly v-else><V2ConfigAudit /></ClientOnly>" in a and "pages/config-audit.vue" not in a
    i = _src("pages/v2/incident/index.vue")
    assert "<ClientOnly v-else-if=\"tab === 'health'\"><V2HealthCheck /></ClientOnly>" in i and "pages/health-check.vue" not in i
    for p in ["'/config-audit'", "'/health-check'"]:
        assert p in _ported(), p
    for comp, apis in (("components/v2/ConfigAudit.vue", ["'/api/config-audit'", "'/api/config-audit/items'", "/api/config-audit/${id}",
                                                          "'/api/config-audit/import'", "/api/config-audit/export", "'/api/scripts'", "/download`"]),
                       ("components/v2/HealthCheck.vue", ["'/api/health-check/targets'", "'/api/health-check/run'", "'/api/health-check/baseline/run'", "<V2HealthDetail"]),
                       ("components/v2/HealthDetail.vue", ["copyCmd", "portBuckets", "sel.coverage.missing", "sel.kernel_errors"])):
        src = _src(comp)
        assert "<V2Table" in src and "<table" not in src and "~/pages/" not in src, comp
        for x in apis:
            assert x in src, (comp, x)
    assert "useLatest" in _src("components/v2/ConfigAudit.vue")


def test_V25_V34_權限跟後端一致():
    adm = _set("ADMIN_WRITE")
    for p in ['"/api/config-audit/import"', '"/api/health-check/run"', '"/api/health-check/baseline/run"']:
        assert p in adm, p
    assert '("GET", "/api/config-audit/export")' in EXP and '("GET", "/api/scripts/{name}/download")' in EXP
    ca = _code("components/v2/ConfigAudit.vue")
    assert '<div v-if="isAdmin" class="bar">' in ca and 'accept=".txt,text/plain"' in ca          # 匯入＋匯出同一塊、限管理員
    assert ca.index('<div v-if="isAdmin" class="bar">') < ca.index('accept=".txt,text/plain"') < ca.index("/api/config-audit/export")
    assert '<button v-if="isAdmin" type="button" class="btn2" :disabled="dlBusy === row.name"' in ca
    hc = _code("components/v2/HealthCheck.vue")
    t = hc[hc.index("<template v-if=\"isAdmin\">"):]
    assert t.index('@click="run"') < t.index("</template>") and t.index('@click="baselineRun"') < t.index("</template>")


def test_V34_確認步驟照舊_只讀不執行指令():
    hc = _code("components/v2/HealthCheck.vue")
    assert "if (!window.confirm(`這段有 ${r.count} 台已登記的主機，要全部帶入健檢清單嗎？`)) return" in hc
    assert "if (!window.confirm('立即對所有已納管主機收一份「健康基準」？平常每週六會自動收，這是手動補一份。')) return" in hc
    assert "body: { ips, concurrency: concurrency.value }" in hc and 'max="16"' in hc and "const concurrency = ref(8)" in hc
    hd = _code("components/v2/HealthDetail.vue")
    assert "v-html" not in hd and "apiFetch" not in hd                                  # 明細只顯示＋複製，不打任何 API
    assert "navigator.clipboard.writeText(cmd)" in hd


def test_V25_未查到自己一態_算不出來顯示未知():
    ca = _code("components/v2/ConfigAudit.vue")
    assert "s === 'Compliant' || s === 'Non-Compliant' || s === 'Not-Applicable' ? s : 'Error'" in ca
    assert "v === null || v === undefined ? '未知'" in ca
