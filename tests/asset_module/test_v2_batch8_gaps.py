"""2.019.1 V2 還連回舊版／缺功能的缺口（盤點第 8 批）：每項一個斷言。"""
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def test_1_實體機分布嵌入_分布統計頁籤():
    d = _src("pages/v2/dist.vue")
    assert "{ k: 'phys', label: '實體機分布' }" in d and "<PhysicalDist />" in d and "tab === 'phys'" in d
    assert (FE / "components/PhysicalDist.vue").exists() and (FE / "pages/reports/physical-distribution.vue").exists()


def test_2_軟體盤點有收集紀錄頁籤_說明不再指舊版():
    s = _src("pages/v2/inventory/software.vue")
    assert "{ k: 'collectlog', label: '收集紀錄' }" in s and "import('~/components/CollectLog.vue')" in s
    assert "<CollectLogPanel" in s and "tab === 'collectlog'" in s
    assert "失敗原因在舊版軟體頁" not in s


def test_3_必填項目調整不再要使用者回舊版():
    d = _src("pages/v2/datafix.vue")
    assert "必填項目可在舊版頁調整" not in d
    assert "/api/data-fix/required" in d and "設定必填項目" in d   # V2 自己就能存


def test_4_連線推論明細仍是標明舊版的連結():
    # 舊 /blast 頁會 router.replace({query:{}}) 清掉查詢字串，嵌進頁籤會被踢出頁籤，所以維持標「舊版」的連結
    o = _src("pages/v2/onboard.vue")
    line = next(x for x in o.splitlines() if 'to="/blast"' in x)
    assert "舊版" in line


def test_5_列印頁連結標明列印頁():
    r = _src("pages/v2/inventory/rack.vue")
    for line in r.splitlines():
        if "/print/rack-sheet" in line or "/print/labels" in line:
            assert "列印頁" in line and 'target="_blank"' in line, line


def test_6_舊路由對照表補齊_列印頁允許():
    import importlib.util
    spec = importlib.util.spec_from_file_location("nol", Path(__file__).with_name("test_v2_no_old_links.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for old in ["/accounts", "/account-ops", "/account-matrix", "/anomalies", "/issues", "/oncall", "/services", "/software",
                "/health-check", "/documents", "/collect-account", "/rack-diagram", "/relocation",
                "/reports/physical-distribution", "/reports/host-sources", "/reports/system-overview", "/scan-shells"]:
        assert old in m.OLD_WITH_V2, old
    assert "/assets/new" not in m.NO_V2
    assert m._old_target("/print/rack-sheet") is None and m._old_target("/print/labels") is None
