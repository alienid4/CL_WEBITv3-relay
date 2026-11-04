"""V2 移植清單第 5 批（2.048.8 起）：V29 基線偏移、V35 HMC 收集……改 V2 原生（純搬移、行為不變）。
說明見 docs/ui-v2/V2移植_B區_批5_2026-11-05.md。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
POL = (ROOT / "APP" / "asset-module" / "backend" / "access_policy.py").read_text(encoding="utf-8")


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def _ported() -> str:
    m = _src("utils/v2LegacyMap.ts")
    return m[m.index("export const PORTED"):m.index("export function isPorted")]


def _set(name: str) -> str:
    s = POL[POL.index(f"{name}: frozenset"):]
    return s[:s.index("\n)\n")]


def test_V29_V35_改V2原生_不再嵌入舊頁():
    a = _src("pages/v2/audit.vue")
    assert "<V2Drift />" in a and "pages/drift.vue" not in a
    c = _src("pages/v2/collect.vue")
    assert "<V2Hmc v-else />" in c and "pages/hmc.vue" not in c
    for p in ["'/drift'", "'/hmc'"]:
        assert p in _ported(), p
    for comp, apis in (("components/v2/Drift.vue", ["/api/drift'", "/api/drift/summary", "/api/drift/recheck", "/disposition"]),
                       ("components/v2/Hmc.vue", ["/api/hmc/consoles'", "/api/hmc/consoles/${", "/api/hmc/collect", "/api/credentials"])):
        s = _src(comp)
        assert "<V2Table" in s and "<table" not in s and "useLatest" in s and "~/pages/" not in s, comp
        for x in apis:
            assert x in s, (comp, x)


def test_V29_V35_寫入權限跟後端一致():
    d = _src("components/v2/Drift.vue")
    assert '"/api/drift/recheck"' in _set("ADMIN_WRITE") and '"/api/drift/{drift_id}/disposition"' in _set("USER_WRITE")
    assert '<button v-if="isAdmin" type="button" class="btn2" :disabled="rechecking" @click="recheck">' in d
    assert '<template v-if="canWrite">' in d and "decide(row, 'ack')" in d
    h = _src("components/v2/Hmc.vue")
    assert '"/api/hmc/collect"' in _set("ADMIN_WRITE")
    assert '<template v-if="isAdmin">' in h and '<button v-if="isAdmin" type="button" class="lb" :disabled="running" @click="ask(row.ip)">' in h


def test_V35_帳密不留存_只在記憶體():
    """D2／A 版安全模式：帳密當場打、收完即清；不寫進網址、不存瀏覽器。"""
    h = _src("components/v2/Hmc.vue")
    code = "\n".join(l for l in h.splitlines() if not l.lstrip().startswith("//"))
    assert "cPass.value = ''; running.value = false" in code          # 收完即清
    assert "function cancelCred() { credOpen.value = false; cPass.value = '' }" in code
    for bad in ["localStorage", "sessionStorage", "query: { pass", "cookie"]:
        assert bad not in code, bad
    assert 'type="password" autocomplete="off"' in code
    assert not re.search(r"hd=.*cPass", code)


def test_V44_V54_改V2原生_寫入限管理員():
    r = _src("pages/v2/inventory/rack.vue")
    assert "<V2Relocation v-else-if" in r and "pages/relocation.vue" not in r
    d = _src("pages/v2/datafix.vue")
    assert "<V2ScanShells" in d and "pages/scan-shells.vue" not in d
    for p in ["'/relocation'", "'/scan-shells'"]:
        assert p in _ported(), p
    adm = _set("ADMIN_WRITE")
    for api in ['"/api/scan-shells/apply"', '"/api/scan-shells/restore/{audit_id}"', '"/api/relocation/import"', '"/api/host-spec/collect"']:
        assert api in adm, api
    assert '("GET", "/api/relocation/export")' in POL[POL.index("_ADMIN_EXPORTS = ["):]
    s = _src("components/v2/ScanShells.vue")
    assert "<V2Table" in s and "<table" not in s and "useLatest" in s
    assert ':selectable="sk === \'mergeable\' && isAdmin"' in s
    assert 'v-if="confirming && sk === \'mergeable\' && isAdmin"' in s           # 先列確認清單，按「確認併回」才送
    assert "window.confirm(`還原" in s                                            # 還原前確認
    assert "emit('changed')" in s
    rl = _src("components/v2/Relocation.vue")
    assert "<V2Table" in rl and "<table" not in rl
    assert '<a v-if="isAdmin" class="btn" :href="exportUrl">' in rl and '<label v-if="isAdmin" class="btn">' in rl
    assert '<button v-if="isAdmin" type="button" class="btn2" :disabled="specBusy" @click="collectSpec">' in rl
