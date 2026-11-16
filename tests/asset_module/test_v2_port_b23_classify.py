"""V2 移植第 23 批 第 5 片（2.058.5）：舊 /adopt 的「改分類」與「加標籤」搬到 6.1「改分類與標籤」＋納管失敗頁籤。
純搬移：同一支 /api/adopt/move-tab（body 一字不改）、/api/adopt/move-choices、共用 TagBatchModal；不會建帳號、沒有納管入口。"""
from test_v2_port_b23 import _api_calls, _src

MOVE = "components/v2/AdoptMove.vue"
CLS = "components/v2/AdoptClassify.vue"


def test_改分類_端點與body跟舊頁一樣():
    old = _src("pages/adopt.vue")
    assert _api_calls(_src(MOVE)) == {("GET", "/api/adopt/move-choices"), ("POST", "/api/adopt/move-tab")}
    assert _api_calls(_src(MOVE)) <= _api_calls(old)
    u = _src("utils/adoptCollect.ts")
    # body：舊頁 submitMove 的四個欄位
    for frag in ("ip: r.ip, hostname: r.hostname, asset_serial: r.asset_serial",
                 "tab: restore ? null : ", "os_type: restore ? null : (", "reason: "):
        assert frag in u, frag
    assert "tab: restore ? null : moveTab.value.split('|')[0]" in old
    assert "os_type: restore ? null : (moveTab.value.split('|')[1] || null)" in old
    assert "tab: restore ? null : choice.split('|')[0]" in u and "os_type: restore ? null : (choice.split('|')[1] || null)" in u
    assert "reason: moveReason.value.trim() || null" in old and "reason: reason.trim() || null" in u
    # 認不出是哪一台的不搬（同舊頁 canMove）
    assert "return Boolean((r.hostname && r.ip) || r.asset_serial)" in old
    assert "Boolean((r.hostname && r.ip) || r.asset_serial)" in u
    assert "const moveRows = computed(() => props.rows.filter(canMove))" in _src(MOVE)


def test_改分類與標籤頁_只讀載入_沒有納管入口():
    s = _src(CLS)
    calls = _api_calls(s)
    assert calls == {("GET", "/api/collect/dispatch/latest"), ("GET", "/api/adopt/move-choices"), ("POST", "/api/asset-tags/by-serial")}
    assert calls <= _api_calls(_src("pages/adopt.vue"))       # by-serial 舊頁載入時也這樣查（只讀）
    for bad in ("OnboardModal", "BatchOnboardModal", "WinOnboardModal", "PasteOnboardModal", "/api/onboard"):
        assert bad not in s, bad
    assert "<V2AdoptMove" in s and '<TagBatchModal v-if="tagRows" :rows="tagRows"' in s
    assert "watch(shown, (s) => { sel.value = pruneIps(sel.value, s) })" in s and "header-clears-all" in s
    assert "<table" not in s


def test_6_1有新頁籤_納管失敗有改分類():
    o = _src("pages/v2/onboard.vue")
    assert "{ k: 'classify', label: '改分類與標籤' }" in o and "<V2AdoptClassify />" in o
    # 審查 P2（2.058.7）：每列一顆、用的是那一列；不跟「重新納管」共用勾選
    assert '<template #cell-op="{ row }"><button type="button" class="lb" data-test="fail-move" @click="failMove = [failMoveRow(row)]">改分類</button></template>' in o
    assert "failMoveRow(failIps" not in o and "failMove = [failMoveRow(f" not in o
    fail_tbl = o[o.index('<V2Table v-model:selected="failPicked"'):o.index("</V2Table>", o.index('<V2Table v-model:selected="failPicked"'))]
    assert "failMoveRow(row)" in fail_tbl
    old = _src("pages/adopt.vue")
    assert "const FAIL_CLASS_TAB: Record<string, string> = { Linux: 'ssh', Windows: 'winrm', AIX: 'mainframe' }" in old
    assert "export const FAIL_CLASS_TAB: Record<string, string> = { Linux: 'ssh', Windows: 'winrm', AIX: 'mainframe' }" in _src("utils/adoptCollect.ts")
