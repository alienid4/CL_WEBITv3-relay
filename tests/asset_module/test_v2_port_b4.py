"""V2 移植清單第 4 批（2.048.3 起）：V68 機櫃盤點「盤點期間異動」註記（N11）、X3 共用元件舊連結改 V2。
說明見 docs/ui-v2/V2移植_B區_批4_2026-11-05.md。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_V68_差異帶出盤點期間異動(tmp_path, monkeypatch):
    """N11：舊 /rack-audit 在差異上標「盤點期間異動」（基準之後系統裡有人改過這台位置／狀態）；V2 差異 API 不可吃掉。"""
    import inventory_flow as inv
    import rack_inventory as ri
    from test_rack_inventory import _conn, _seed
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        aid = ri.start_audit(conn, "甲機房", None, "alice")["id"]
        sh = ri.rack_sheet(conn, aid, "甲機房", "B2")
        first = sh["rows"][0]["key"]
        ri.submit_rack(conn, aid, "甲機房", "B2", {first: {"result": "不在"}}, [], "王小明", "2026-11-01", "alice")
        monkeypatch.setattr(ri, "_changes_after", lambda c, a, b, keys: ["位置更正（bob 2026-11-02 10:00）"] if first in keys else [])
        rows = inv.diffs(conn, "rack", aid)["rows"]
        hit = [r for r in rows if r["key"] == first]
        assert hit and hit[0]["during_change"] == ["位置更正（bob 2026-11-02 10:00）"]
        assert all(r["during_change"] == [] for r in rows if r["key"] != first)
    finally:
        conn.close()


def test_V68_畫面與匯出都有註記_舊版入口拿掉():
    c = _src("components/v2/InventoryFlow.vue")
    assert 'data-test="during-change"' in c and ":title=\"d.during_change.join('；')\"" in c
    assert "'盤點期間異動'] : []" in c and "(d.during_change || []).join('；')" in c
    assert "/rack-audit?legacy=1" not in _src("pages/v2/inventory/rack.vue")


def test_X3_所有共用元件都沒有V1網址():
    """X3：不只 V2 直接用到的共用元件，components/ 底下全部（舊頁嵌在 V2 裡時也會用到）都直接寫 V2 網址。"""
    import test_v2_no_v1_links as t
    bad = []
    for f in sorted((FE / "components").rglob("*.vue")):
        for i, line, path in t._scan(f.read_text(encoding="utf-8")):
            if path.startswith(t.FE_OK) or "legacy=1" in line:
                continue
            bad.append(f"{f.relative_to(FE).as_posix()}:{i} → {path}")
    assert not bad, "\n".join(bad)


def test_X3_資料格與型號整理走同一條轉址規則():
    d = _src("components/DataCell.vue")
    assert "assetFilterTarget(props.k, String(props.value))" in d and "/v2/machine/" in d
    m = _src("utils/v2LegacyMap.ts")
    assert "export function assetFilterTarget" in m and "SIMPLE['/assets']({ filter_field: field, filter_value: value })" in m
    assert "/v2/hosts?q=" in _src("components/ModelCleanup.vue")
    assert "tab: 'funnel'" in _src("components/OnboardMatrix.vue")
    assert 'to="/v2/reconcile?tab=numcheck"' in _src("components/KeyNumbers.vue")


def _ported() -> str:
    m = _src("utils/v2LegacyMap.ts")
    return m[m.index("export const PORTED"):m.index("export function isPorted")]


def test_V31_V37_V39_對帳頁三個頁籤改V2原生():
    r = _src("pages/v2/reconcile.vue")
    for old in ["~/pages/existence.vue", "~/pages/number-check.vue", "~/pages/manage-state-detail.vue", "<V2Embed>"]:
        assert old not in r, old
    for tag in ["<V2NumberCheck", "<V2Existence", "<V2StateDetail"]:
        assert tag in r, tag
    for p in ["'/existence'", "'/number-check'", "'/manage-state-detail'"]:
        assert p in _ported(), p
    for comp, apis in (("components/v2/Existence.vue", ["/api/existence/summary", "/api/existence/machines", "/api/existence/cia-pending/export"]),
                       ("components/v2/NumberCheck.vue", ["/api/number-proof", "/api/number-proof/diff"]),
                       ("components/v2/StateDetail.vue", ["/api/manage-state", "partial_gaps", "已退役:breakdown"])):
        c = _src(comp)
        assert "<V2Table" in c and "<table" not in c and "useLatest" in c and "~/pages/" not in c, comp
        for a in apis:
            assert a in c, (comp, a)


def test_V31_V37_V39_不另算數字_匯出權限跟後端一致():
    e = _src("components/v2/Existence.vue")
    assert 'v-if="isAdmin" class="btn2" :href="exportHref"' in e and "限管理員" in e       # _ADMIN_EXPORTS
    nc = _src("components/v2/NumberCheck.vue")
    assert 'v-if="canWrite" type="button" class="btn2" :disabled="exporting" @click="exportDiff"' in nc   # USER_READ_EXACT
    assert "/v2/onboard?tab=funnel&drill=" in nc
    s = _src("components/v2/StateDetail.vue")
    assert 'to="/v2/datafix?tab=shells"' in s and 'to="/v2/datafix?tab=cia"' in s
    # 前端只排序與篩選，不加總台數：卡片數字直接用後端欄位
    for k in ["c.machines", "data.also_active", "data.multi_row", "data.retired_alive"]:
        assert k in s, k
    pol = (ROOT / "APP" / "asset-module" / "backend" / "access_policy.py").read_text(encoding="utf-8")
    assert '("GET", "/api/existence/cia-pending/export")' in pol and '"/api/number-proof/diff",' in pol


def test_V50_V45_主機系統總覽與業務系統排行改V2原生():
    r = _src("pages/v2/report.vue")
    assert "<V2SystemOverview />" in r and "system-overview.vue" not in r
    s = _src("pages/v2/systems.vue")
    assert "<V2BizRank />" in s and "business-systems.vue" not in s and "<V2Embed" not in s
    for p in ["'/reports/system-overview'", "'/reports/business-systems'"]:
        assert p in _ported(), p
    for comp in ["components/v2/SystemOverview.vue", "components/v2/BizRank.vue"]:
        c = _src(comp)
        assert "<V2Table" in c and "<table" not in c and "useLatest" in c and "~/pages/" not in c, comp
        assert "/api/reports/system-overview'" in c and "/api/reports/system-overview/drill" in c, comp
    o = _src("components/v2/SystemOverview.vue")
    assert "groupSegments(data.value)" in o and "GROUP_BUCKET" in o           # 圓環含未分類（W221-014），同一份分段
    assert "不看環境別" in o
    b = _src("components/v2/BizRank.vue")
    assert "{ b: 'uncategorized', label: '未分類'" in b                        # W221-014 頂列第 5 格
    # 分類對照表：2.055.0（待決定 3）上傳與範本搬到 ⚙ 資料匯入；這裡只留資料來源、最後匯入時間、去匯入（限管理員）
    assert "system-category/import" not in b and "system-category/template" not in b and 'type="file"' not in b
    assert 'to="/v2/settings?st=import">去匯入</NuxtLink>' in b and "匯入對照表限管理員" in b and "/api/reports/system-category/status" in b
    imp = _src("pages/import.vue")
    assert "'/api/reports/system-category/import'" in imp and "'/api/reports/system-category/template'" in imp
    pol = (ROOT / "APP" / "asset-module" / "backend" / "access_policy.py").read_text(encoding="utf-8")
    admin_w = pol[pol.index("ADMIN_WRITE: frozenset"):]
    admin_w = admin_w[:admin_w.index("\n)\n")] if "\n)\n" in admin_w else admin_w
    assert '"/api/reports/system-category/import"' in admin_w
    assert '("GET", "/api/reports/system-category/template")' in pol[pol.index("_USER_EXPORTS = ["):]
