<script setup lang="ts">
// S1 實體機補登（2.061.2）：9.4「實體機」頁籤。上方三態（實體／無法判定／非實體）＋排除，數字＝下面清單列數（同一份計算）。
// 實體的一台一案（共用案件清單 V2CiaCases，可批次確認、撤回、標例外）；無法判定、非實體只列出，不進補登。
// 證據帶日期、機櫃圖 Excel 與彙整表位置並列，對不上標「對不上」且不預填（機櫃以 Excel 為準）。
const { apiFetch } = useApi()
const route = useRoute()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())
const u = (pst?: string) => `/v2/ciareg?tab=phys${pst ? `&pst=${pst}` : ''}`
const ov = ref<any>(null); const list = ref<any[] | null>(null); const err = ref('')
const view = computed(() => (['unknown', 'virtual'].includes(q('pst')) ? q('pst') : 'cases'))
async function load() {
  try {
    // 清單頁一次拿清單＋上方數字（後端同一次計算）；案件頁只要上方數字
    if (view.value === 'cases') { ov.value = await apiFetch<any>('/api/cia-case/physical/overview'); list.value = null }
    else { const r = await apiFetch<any>('/api/cia-case/physical/list', { query: { state: view.value } }); ov.value = r.overview; list.value = r.items }
    err.value = ''
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)
watch(view, load)
const evText = (ev: any[]) => (ev ?? []).map((e: any) => `${e.source}${e.date ? ` ${e.date}` : ''}：${e.what}`).join('；') || '—'
const caseCols = [
  { k: 'ip', label: 'IP', mono: true }, { k: 'level', label: '證據' }, { k: 'evs', label: '證據明細' },
  { k: 'rack_loc', label: '機櫃圖位置' }, { k: 'sheet_loc', label: '彙整表位置' }, { k: 'conf', label: '位置' },
  { k: 'pre', label: '預填機房／機櫃' }, { k: 'apid', label: 'APID 建議' },
]
const caseRow = (x: any) => {
  const d = x.detail ?? {}
  return { ip: d.ip ?? '—', level: d.evidence_level ?? '—', evs: evText(d.evidence), rack_loc: d.rack_loc || '—', sheet_loc: d.sheet_loc || '—',
    conf: d.conflict ? '對不上（不預填）' : '—', pre: d.fill?.room ? `${d.fill.room} ${d.fill.rack ?? ''}`.trim() : '—',
    apid: d.apid_suggest ? `${d.apid_suggest} ${d.apid_name ?? ''}（${d.fill?.api_id ? '預填' : '只當參考'}）` : '無法判定' }
}
const rows = computed(() => (list.value ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, evs: evText(x.evidence), last: (x.last_seen || '').slice(0, 10) || '—' })))
const cols = [{ k: 'hostname', label: '主機名稱', link: (r: any) => (r.serial ? `/v2/machine/${encodeURIComponent(r.serial)}` : null) }, { k: 'ip', label: 'IP', mono: true },
  { k: 'os', label: '作業系統' }, { k: 'state_why', label: '為什麼' }, { k: 'evidence_level', label: '證據' }, { k: 'evs', label: '證據明細' },
  { k: 'status_label', label: '偵測狀態' }, { k: 'last', label: '最後看到', mono: true }]
const chips = computed(() => [
  { k: 'cases', label: '實體（進補登）', v: ov.value?.physical }, { k: 'unknown', label: '無法判定', v: ov.value?.unknown }, { k: 'virtual', label: '非實體', v: ov.value?.virtual },
])
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <div v-if="ov" class="bar">
      <NuxtLink v-for="c in chips" :key="c.k" class="chip" :class="{ on: view === c.k }" :to="u(c.k === 'cases' ? undefined : c.k)">{{ c.label }} <b class="mono">{{ n(c.v) }}</b></NuxtLink>
      <span class="muted">排除：虛擬機 {{ n(ov.excluded?.vm) }}・VMware 設備 {{ n(ov.excluded?.vmware) }}</span>
      <span class="muted">APID 回測 {{ ov.backtest?.samples ? `${n(ov.backtest.hits)}／${n(ov.backtest.samples)}` : '沒有樣本' }}{{ ov.backtest?.ok ? '（過門檻，預填）' : '（沒過門檻，不預填）' }}</span>
      <InfoNote>母體＝2.1 未登錄 CIA，扣掉虛擬機（走 9.4 VM 補登）與 VMware 設備（ESXi／vCenter 不當補登候選）。實體＝有硬體序號而且型號像伺服器，或機櫃圖 Excel 有這台；
        非實體＝型號／作業系統／主機名像虛擬或容器；其餘＝無法判定（另列，不進補登）。證據依強弱：伺服器資料彙整表（{{ ov.sheet_date ?? '未匯入' }}）、機櫃圖 Excel、存活掃描；
        兩個證據日期相差超過 30 天不算互相佐證。機房／機櫃以機櫃圖 Excel 為準，跟彙整表對不上就不預填。APID 只在用 CIA 已登記機器回測準確率夠高時才預填。
        「實體」數字是現在算的；下面的案件是上次「重新比對」寫進來的，按一下就同步。</InfoNote>
    </div>
    <V2CiaCases v-if="view === 'cases'" kind="physical" prefix="P" tab="phys" page="/v2/ciareg" scan-path="/api/cia-case/physical/scan"
                bulk-confirm :extra-cols="caseCols" :map-row="caseRow" />
    <V2Table v-else :rows="rows" :cols="cols" :prefix="view === 'unknown' ? 'U' : 'V'" row-key="machine_key" unit="台" :empty="view === 'unknown' ? '沒有無法判定的' : '沒有非實體的'" />
  </div>
</template>

<style scoped>
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
</style>
