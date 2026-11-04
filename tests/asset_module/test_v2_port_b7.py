"""V2 移植清單第 7 批起（2.048.12 起）：V52 SAN 收集、V24 收集帳號遷移、V14 收集身分、V22 一鍵批次自動納管……改 V2 原生（純搬移、行為不變）。
說明見 docs/ui-v2/V2移植_B區_批7_2026-11-05.md。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
POL = (ROOT / "APP" / "asset-module" / "backend" / "access_policy.py").read_text(encoding="utf-8")


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


def test_V52_V24_改V2原生_不再嵌入舊頁():
    c = _src("pages/v2/collect.vue")
    assert "<V2San v-if=\"tab === 'san'\" />" in c and "pages/san.vue" not in c
    s = _src("pages/v2/settings.vue")
    assert "<V2CollectAccount v-else-if=\"tab === 'identity' && sub === 'migration'\"" in s and "pages/collect-account.vue" not in s
    for p in ["'/san'", "'/collect-account'"]:
        assert p in _ported(), p
    for comp, apis in (("components/v2/San.vue", ["/api/san/switches'", "/api/san/switches/${", "/api/san/collect", "/api/san/import",
                                                  "/api/san/offline-guide", "/api/system/collector-selfcheck", "/api/system/collector-selfheal",
                                                  "/api/credentials", "<OfflineMarkModal", "<AliveCheck", "<CollectLog"]),
                       ("components/v2/CollectAccount.vue", ["/api/collect-account/migration'", "/migration/detail?state=", "/migration/probe",
                                                             "/migration/${kind}?asset_serial=", "/migration/removal-plan", "/migration/removal-script"])):
        src = _src(comp)
        assert "<V2Table" in src and "<table" not in src and "useLatest" in src and "~/pages/" not in src, comp
        for x in apis:
            assert x in src, (comp, x)


def test_V52_V24_寫入權限跟後端一致():
    adm, usr = _set("ADMIN_WRITE"), _set("USER_WRITE")
    for api in ['"/api/san/collect"', '"/api/san/import"', '"/api/system/collector-selfheal"',
                '"/api/collect-account/migration/probe"', '"/api/collect-account/migration/switch"', '"/api/collect-account/migration/revert"']:
        assert api in adm, api
    for api in ['"/api/assets/batch-status"', '"/api/tools/alive-check"']:
        assert api in usr, api
    s = _code("components/v2/San.vue")
    assert '<div v-if="isAdmin" class="bar"><button type="button" class="btn" :disabled="healing" @click="healEnv">' in s
    assert ':selectable="isAdmin"' in s
    assert '<button v-if="isAdmin" type="button" class="lb" :disabled="running" @click="ask([row.ip])">收集</button>' in s
    assert '<AliveCheck v-if="canWrite"' in s and '<button v-if="canWrite" type="button" class="lb" :disabled="!row.asset_serial"' in s
    assert "onMounted(() => { if (isAdmin.value) loadCreds() })" in s
    ca = _code("components/v2/CollectAccount.vue")
    assert "<button v-if=\"isAdmin\" type=\"button\" class=\"btn\" :disabled=\"busy\" @click=\"act('probe')\">" in ca
    assert "v-if=\"isAdmin && row.state === '可切換'\"" in ca and "v-else-if=\"isAdmin && row.new_ok === 1\"" in ca
    assert "if (kind === 'switch' && !window.confirm(" in ca          # 切換先確認（舊頁同一句）


def test_V52_帳密不留存_只在記憶體():
    """D2／A 版安全模式：帳密當場打、收完即清；不寫進網址、不存瀏覽器。"""
    s = _code("components/v2/San.vue")
    assert "cPass.value = ''; running.value = false" in s
    assert "function cancelCred() { credOpen.value = false; cPass.value = '' }" in s
    for bad in ["localStorage", "sessionStorage", "query: { pass", "cookie"]:
        assert bad not in s, bad
    assert 'type="password" autocomplete="off"' in s
    assert not re.search(r"sd=.*cPass|se=.*cPass", s)
    # 離線匯入：IP 對不上（409）先問人，確定才帶 force
    assert "if (status === 409 && !force)" in s and "return runImport(true)" in s


def test_V14_V22_改V2原生_不再嵌入舊頁():
    a = _src("pages/v2/inventory/account.vue")
    assert "<V2AccountOps />" in a and "pages/account-ops.vue" not in a
    o = _src("pages/v2/onboard.vue")
    assert "<V2BatchAuto />" in o and "pages/batch-onboard.vue" not in o
    assert '<button v-if="isAdmin" type="button" class="btn2" @click="showAuto = !showAuto">' in o     # 入口仍只給管理員
    for p in ["'/account-ops'", "'/batch-onboard'"]:
        assert p in _ported(), p
    for comp, apis in (("components/v2/AccountOps.vue", ["/api/accounts/findings", "/api/accounts/hosts", "/api/accounts/collect-config", "/api/accounts/collect'",
                                                         "/api/accounts/exclude", "/api/accounts/sudo-rules", "/api/accounts/diagnose"]),
                       ("components/v2/BatchAuto.vue", ["/api/onboard/batch-auto/preview", "/api/onboard/batch-auto'", "/api/onboard/batch-auto/status",
                                                        "/api/onboard/batch-auto/export"])):
        src = _src(comp)
        assert "<V2Table" in src and "<table" not in src and "~/pages/" not in src, comp
        for x in apis:
            assert x in src, (comp, x)


def test_V14_權限跟後端一致():
    adm = _set("ADMIN_WRITE")
    for api in ['"/api/accounts/collect"', '"/api/accounts/collect-config"', '"/api/accounts/exclude"', '"/api/onboard/batch-auto"', '"/api/onboard/batch-auto/preview"']:
        assert api in adm, api
    assert '"/api/accounts/collect-config"' in POL[POL.index("ADMIN_READ_EXACT"):POL.index("USER_READ_EXACT")]
    s = _code("components/v2/AccountOps.vue")
    assert "if (isAdmin.value) loadCfg()" in s                                   # 一般帳號不打 ADMIN_READ
    assert '<button v-if="isAdmin" type="button" class="btn" :disabled="collecting" @click="collect">' in s
    assert '<button v-if="isAdmin" type="button" class="lb" @click="toggleExclude(row.asset_serial, !row.excluded)">' in s


def test_V22_安全步驟照舊():
    """D1：只跑測試機、未登記要人勾、統一密碼要打 YES、密碼用完即清、中斷不自動接。"""
    s = _code("components/v2/BatchAuto.vue")
    assert "if (form.unify && form.confirm !== 'YES')" in s
    assert "form.passwordA = ''; form.passwordB = ''; form.confirm = ''" in s
    assert "include_ips: overrideIps ? overrideIps : pickedUnknown.value" in s
    assert "unify: false" in s                                                   # 預設不勾
    for bad in ["localStorage", "sessionStorage", "cookie", "router.replace", "query: { pass"]:
        assert bad not in s, bad
    assert s.count('type="password" autocomplete="new-password"') == 2
    assert "setInterval(pollStatus, 2000)" in s and "clearInterval(poller)" in s
    assert "selectable" not in s                                               # 未登記逐台勾，不給表格全選（審查 P1）
