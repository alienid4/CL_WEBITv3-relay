<script setup lang="ts">
// 主機頁 → 概要 →「來源對照表」（2.060.21，S11）：用途／主機名／作業系統／環境別／負責人／業務系統／機房機櫃，
// 四種來源（CIA、伺服器資料彙整表、系統實掃、納管回報）各寫什麼、採用哪個、為什麼。對不上又分不出新舊＝不猜、標黃。
// 規則在 backend/field_trust.json（不寫死）；只讀，要改走 CIA 修正（只寫回 CIA）。
const props = defineProps<{ mkey: string }>()
const { apiFetch } = useApi()
const data = ref<any>(null)
const err = ref('')
async function load() {
  err.value = ''
  try { data.value = await apiFetch<any>('/api/v2/machine/sources', { query: { key: props.mkey } }) } catch (e: any) { err.value = `來源對照表載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
watch(() => props.mkey, load)
const SRC = ['cia', 'sheet', 'scan', 'onboard'] as const
const dateOf = (s: string) => (data.value?.dates?.[s] ? String(data.value.dates[s]).slice(0, 10) : '')
const rows = computed(() => (data.value?.rows ?? []).map((r: any, i: number) => ({
  ...r, __no: i + 1, cia: r.values.cia || '—', sheet: r.values.sheet || '—', scan: r.values.scan || '—', onboard: r.values.onboard || '—',
  used: r.status === 'conflict' ? '待確認（不猜）' : r.status === 'none' ? '無資料' : `${r.pick_value}（${r.pick === 'rack' ? '機櫃圖' : data.value.sources[r.pick]}）`,
})))
const cols = computed(() => [
  { k: 'label', label: '欄位' },
  ...SRC.map(s => ({ k: s, label: `${data.value?.sources?.[s] ?? s}${dateOf(s) ? `（${dateOf(s)}）` : ''}` })),
  { k: 'used', label: '採用', tone: (r: any) => (r.status === 'conflict' ? 'test' : '') },
  { k: 'why', label: '為什麼' },
])
</script>

<template>
  <section class="sc" data-testid="source-compare">
    <div class="hd"><b>來源對照表</b>
      <span v-if="data?.conflicts" class="warnt">{{ data.conflicts }} 個欄位對不上、待確認</span>
      <span v-else-if="data" class="muted">{{ data.disagree ? `${data.disagree} 個欄位來源不同，已依規則採用` : '各來源一致' }}</span>
      <NuxtLink v-if="data?.conflicts || data?.disagree" class="lnk" to="/v2/ciareg?tab=srv&sk=diff">9.4 彙整表比對</NuxtLink>
      <InfoNote>四種來源：CIA（正式帳）、伺服器資料彙整表（主機組，有「標記時間」）、系統實掃（vCenter／RVTools）、納管回報（系統實際收到的）。
        信任規則：機器自己知道的（主機名、作業系統）信納管＞實掃；人決定的（用途、負責人、業務系統、環境別）信彙整表與 CIA 較新那筆；實體位置信機櫃圖（人工對過的 Excel）。
        對不上又分不出哪個新＝不猜，標「待確認」，到 9.4 彙整表比對用 Excel 批次確認；確認後只寫回 CIA。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <V2Table v-if="data" :rows="rows" :cols="cols" prefix="F" row-key="__no" unit="欄" :search="false" export-name="來源對照表" />
  </section>
</template>

<style scoped>
.sc { margin-top: 14px; }
.hd { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; font-size: 13px; }
.muted { color: var(--muted); }
.warnt { color: var(--warn-text); }
.err { color: var(--bad); }
.lnk { color: var(--link); }
:deep(.warncell) { background: var(--warn-soft); color: var(--warn-text); }
</style>
