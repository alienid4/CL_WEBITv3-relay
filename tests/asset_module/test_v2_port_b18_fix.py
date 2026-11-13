"""第 18 批（納管漏斗，⚠D1）獨立審查修正（2.054.1）：釘住元件的接線——送進依序納管視窗與偵測存活的 IP 一定是
utils/funnel.ipsToSend（＝目前篩選結果裡已勾的），能勾的一定從目前篩選結果算；規則本身由 vitest funnel.spec.ts 驗。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
F = (ROOT / "APP" / "asset-module" / "frontend" / "components" / "v2" / "Funnel.vue").read_text(encoding="utf-8")


def test_送出的IP只有一個來源_就是目前篩選結果裡已勾的():
    assert "const selIps = computed(() => ipsToSend(selected.value, filtered.value as FunnelRow[]))" in F
    assert re.search(r'<BatchOnboardModal v-if="showBatchOnboard" :ips="selIps"', F)
    assert "body: { ips: selIps.value, scope: '納管漏斗勾選' }" in F
    # 不准有別的地方直接拿 filtered／items 的 IP 去送
    assert F.count(":ips=") == 1 and F.count("apiFetch<any>('/api/tools/alive-check/batch'") == 1


def test_能勾的與勾選這關都從目前篩選結果算():
    assert "const selectable = computed(() => selectableRows(filtered.value))" in F
    assert "selected.value = selectAll(selected.value, filtered.value as FunnelRow[])" in F
    assert "`勾選這關 ${selectable.value.length} 台並依序納管`" in F


def test_換篩選丟掉看不到的勾選_表頭取消清全部():
    assert "watch(filtered, (f) => { selected.value = pruneSelection(selected.value, f as FunnelRow[]) })" in F
    assert '@update:selected="onSelect"' in F and "v-model:selected" not in F
    assert "pruneSelection(next, filtered.value as FunnelRow[])" in F and "header-clears-all" in F      # 2.055.1：表頭取消＝全部清（V2Table 明確做）


def test_篩選走utils_下鑽關卡組有帶():
    assert "const filtered = computed<Row[]>(() => filterFunnel(items.value, filt.value) as Row[])" in F
    assert "stageSet: stageSet.value" in F


def test_點不在待處理的關卡會關掉只看待處理_下鑽OS可清掉也會保留():
    assert "!o.todoStages.includes(o.k) ? { ft: '0' } : {}" in (ROOT / 'APP/asset-module/frontend/utils/funnel.ts').read_text(encoding='utf-8')
    assert "route.query.os_type === undefined ? drillOs.value : qList('os_type')" in F
    assert "v.join(',') || (drillKey.value ? '' : undefined)" in F
    assert "setQ(pickStagePatch({ current: stageFilter.value, k, osInUrl: route.query.os_type !== undefined, drillOs: drillOs.value, todoStages: todoStages.value }))" in F   # 規則在 utils（vitest 驗 keepOs）
