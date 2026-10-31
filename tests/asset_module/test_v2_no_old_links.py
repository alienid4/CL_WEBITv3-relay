"""v2 頁面不可以悄悄連回已經有新版的舊頁（2.006.3，找碴 C10）。

半新半舊期間每點幾下就換一次外框、換一套選單，新人會迷路。規則：
  * 舊頁已經有 v2 對應頁 → 頁內連結一律連 v2；
  * 真的要用舊頁才有的功能（例如 Push Agent 安裝包、網頁填寫盤點）可以連，但同一行一定要標「舊版」，
    讓使用者點之前就知道會換外框。
這支掃 pages/v2、components/v2、layouts/v2.vue 每一行的路徑字串。
"""
import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"

#: 舊路由 → 它的 v2 對應頁（有對應頁的才列；沒有 v2 的舊頁，如 /existence、/pipeline，不在這裡）
OLD_WITH_V2 = {
    "/topology": "/v2/systems、/v2/system/<代號>",
    "/architecture": "/v2/arch",
    "/datacenter": "/v2/datacenter",
    "/storage": "/v2/storage",
    "/adopt": "/v2/onboard",
    "/data-fix": "/v2/datafix",
    "/changes": "/v2/changes",
    "/risk": "/v2/risk",
    "/hosts": "/v2/hosts",
    "/assets": "/v2/hosts、/v2/machine/<代號>",
    "/incident": "/v2/incident",
    "/rack-audit": "/v2/inventory/rack",
    "/reports/scan-gaps": "/v2/onboard?tab=gaps",
    "/reports/classify": "/v2/classify",
    "/blast": "主機履歷／系統履歷「影響範圍」",
    "/san": "/v2/collect",
    "/hmc": "/v2/collect",
    # 第四批（2.007.0）
    "/architecture": "/v2/arch",
    "/patch": "/v2/patch",
    "/config-audit": "/v2/audit",
    "/drift": "/v2/audit?tab=drift",
    "/eos": "/v2/eos",
    "/reports/monthly": "/v2/report",
    "/reports/system-group": "/v2/report?tab=group",
    "/distribution": "/v2/dist",
    "/settings": "/v2/settings",
    "/import": "/v2/settings?st=import",
    "/contacts": "/v2/settings?st=contacts",
    "/model-specs": "/v2/settings?st=specs",
    "/segments": "/v2/settings?st=segments",
    "/activity": "/v2/settings?st=activity",
    "/nav-editor": "/v2/settings?st=nav",
    "/reset": "/v2/settings?st=reset",
    # 2.007.12
    "/count-bridge": "/v2/reconcile",
    "/machine-key": "/v2/reconcile?tab=rule",
    # 2.010.x 舊版翻新第七批（a）
    "/existence": "/v2/reconcile?tab=existence",
    "/manage-state-detail": "/v2/reconcile?tab=state",
    "/number-check": "/v2/reconcile?tab=numcheck",
    "/pipeline": "/v2/onboard?tab=funnel",
    "/scan-results": "/v2/onboard?tab=scan",
    "/ocp": "/v2/arch?tab=ocpdetail",
    "/data-quality": "/v2/datafix?tab=quality",
    "/corrections": "/v2/datafix?tab=mismatch",
    "/reports/business-systems": "/v2/systems?tab=rank",
    "/golive": "/v2/audit?tab=golive、/v2/golive/<資產編號>",
    # 2.019.1 補登記（原本漏列，V2 頁面連到這些舊路由時沒被掃到）
    "/accounts": "/v2/inventory/account",
    "/account-ops": "/v2/inventory/account?tab=identity",
    "/account-matrix": "/v2/inventory/matrix",
    "/anomalies": "/v2/changes?tab=anomaly",
    "/issues": "/v2/changes?tab=issues",
    "/oncall": "/v2/night",
    "/services": "/v2/inventory/service",
    "/software": "/v2/inventory/software",
    "/health-check": "/v2/incident?tab=health",
    "/documents": "/v2/settings?st=documents",
    "/collect-account": "/v2/settings?st=identity",
    "/rack-diagram": "/v2/inventory/rack?tab=photo",
    "/relocation": "/v2/inventory/rack?tab=relocate",
    "/reports/physical-distribution": "/v2/dist?tab=phys",
    "/reports/host-sources": "/v2/report?tab=sources",
    "/reports/system-overview": "/v2/report?tab=overview",
    "/scan-shells": "/v2/datafix?tab=shells",
    # /lifecycle、/features 目前沒有確定的 V2 對應頁，不登記
}
#: 舊頁上還沒有 v2 對應的子頁
NO_V2: set[str] = set()
#: 列印輸出（layout:false 的純列印頁，不是舊版 UI），V2 可以直接連、不用標「舊版」
PRINT_OK = ("/print/",)

_PATH = re.compile(r"""["'`](/(?!api/|v2\b|_nuxt)[a-z][a-z0-9\-/]*)""")


def _files():
    yield from (FE / "pages" / "v2").rglob("*.vue")
    yield from (FE / "components" / "v2").rglob("*.vue")
    yield FE / "layouts" / "v2.vue"


def _old_target(path: str):
    if path in NO_V2 or path.startswith(PRINT_OK):
        return None
    for old in OLD_WITH_V2:
        if path == old or path.startswith(old + "/") or path.startswith(old + "?"):
            return old
    return None


def test_v2頁面連到已有新版的舊頁時一定標舊版():
    bad = []
    for f in _files():
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in _PATH.finditer(line):
                old = _old_target(m.group(1))
                if old and "舊版" not in line:
                    bad.append(f"{f.relative_to(FE)}:{i} 連到 {m.group(1)}（新版：{OLD_WITH_V2[old]}）")
    assert not bad, "v2 頁面連回舊頁、又沒有標「舊版」：\n" + "\n".join(bad)


def test_掃描器抓得到():
    """防呆：掃描規則本身要能抓到典型寫法（不然上面那支永遠綠）。"""
    samples = ["<NuxtLink to=\"/topology\">", ":to=\"`/datacenter?room=${r}`\"", "navigateTo({ path: '/adopt' })",
               "link: (r) => `/topology?system=${r.api_id}`"]
    for s in samples:
        m = _PATH.search(s)
        assert m and _old_target(m.group(1)), s
    assert _old_target("/print/rack-sheet") is None and _old_target("/print/labels") is None   # 列印輸出，允許
    assert _old_target("/software") == "/software" and _old_target("/accounts?x=1") == "/accounts"
