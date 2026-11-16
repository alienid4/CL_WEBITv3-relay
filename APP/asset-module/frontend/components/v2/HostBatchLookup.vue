<script setup lang="ts">
// 2.1 主機查詢「批次查詢」頁籤（2.041.84，舊版缺口 230 第 2 項）：貼多筆主機名／IP／資產編號／APID，一次查。
// 唯讀：POST /api/v2/hosts/batch-lookup 不寫庫、不連目標機、不留查詢紀錄。比對規則＝值班卡與首頁搜尋同一套（oncall.resolve）。
// 預設精確比對；勾「含部分比對」才做部分字串比對，且結果標「部分命中」，不算「找到」。查無的每一筆都有一列，可匯出。
import { LOOKUP_LABEL, LOOKUP_TONE, lookupCounts, lookupFilter, parseTerms } from '~/composables/hostLookup'
import type { LookupData, LookupRow, LookupStatus } from '~/composables/hostLookup'

const { apiFetch } = useApi()
const text = ref('')
const partial = ref(false)
const data = ref<LookupData | null>(null)
const err = ref('')
const busy = ref(false)
const filter = ref<LookupStatus | 'all'>('all')
let seq = 0     // 連按查詢時，較舊的回應不得蓋掉較新的結果
const termCount = computed(() => parseTerms(text.value).length)
async function run() {
  // 原始貼入量在前端先檢查（切割後才送後端，後端看不到原始空白行與分隔字元）：與後端上限一致
  const rawLines = (text.value || '').split(/\r?\n/).length
  if (rawLines > 2000 || (text.value || '').length > 60000) { err.value = `貼入內容過大（原始 ${rawLines} 行、${(text.value || '').length} 字元）：一次最多 2000 行、60000 字元，請分批查詢`; return }
  const terms = parseTerms(text.value)
  if (!terms.length) { err.value = '請先貼上要查的主機名、IP、資產編號或 APID'; return }
  const mine = ++seq
  busy.value = true; err.value = ''
  try {
    const r = await apiFetch<LookupData>('/api/v2/hosts/batch-lookup', { method: 'POST', body: { terms, partial: partial.value } })
    if (mine !== seq) return
    data.value = r; filter.value = 'all'
  } catch (e: any) {
    if (mine === seq) err.value = `批次查詢失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  } finally { if (mine === seq) busy.value = false }
}
const counts = computed(() => lookupCounts(data.value?.rows ?? []))
const shown = computed(() => lookupFilter(data.value?.rows ?? [], filter.value).map(r => ({ ...r, __no: r.no })))
const tiles = computed<[LookupStatus, string][]>(() => (['one', 'many', ...(data.value?.mode === 'partial' ? ['partial'] : []), 'none'] as LookupStatus[]).map(s => [s, LOOKUP_LABEL[s]]))
function pick(s: LookupStatus) { filter.value = filter.value === s ? 'all' : s }
const link = (r: LookupRow) => (r.key ? `/v2/machine/${encodeURIComponent(r.key)}` : null)
const cols = [
  { k: 'term', label: '輸入值', mono: true },
  { k: 'status_label', label: '結果', tone: (r: LookupRow) => LOOKUP_TONE[r.status] },
  { k: 'name', label: '主機名稱', link, text: (r: LookupRow) => r.name || (r.count ? `${r.count} 台候選` : '—') },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'system', label: '業務系統' },
  // 部分命中要看得出是怎麼比到的（比對方式欄已移到匯出）：說明欄補上比對方式
  { k: 'hint', label: '說明', text: (r: LookupRow) => r.hint || (r.status === 'partial' ? r.matched_by : null) },
]
// 2026-11-15 畫面重看：6 欄；比對方式、類型、位置、候選主機、輸入次數只在匯出
const xcols = [{ k: 'matched_by', label: '比對方式' }, { k: 'type', label: '類型' }, { k: 'where', label: '位置' },
  { k: 'candidates_text', label: '候選主機' }, { k: 'input_count', label: '輸入次數' }]
</script>

<template>
  <section class="bl">
    <div class="bar">
      <b>貼多筆批次查詢</b>
      <InfoNote>每行一筆，可貼主機名、IP、資產編號、APID，完全相同才算找到；勾「含部分比對」的結果另標「部分命中」。一次最多 {{ data?.limits.max_unique ?? 500 }} 筆。只查詢，不改資料。</InfoNote>
    </div>
    <textarea v-model="text" class="ta" rows="6" placeholder="每行一筆：主機名、IP、資產編號或 APID" aria-label="批次查詢輸入" />
    <div class="bar">
      <button type="button" class="btn2 pri" :disabled="busy || !termCount" @click="run">{{ busy ? '查詢中…' : '查詢' }}</button>
      <label class="chk"><input v-model="partial" type="checkbox"> 含部分比對</label>
      <span class="muted">目前輸入 {{ termCount.toLocaleString() }} 項</span>
    </div>
    <p v-if="err" class="err" role="alert">{{ err }}</p>
    <template v-if="data">
      <div class="tiles">
        <button type="button" class="tile" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 <b>{{ data.rows.length }}</b></button>
        <button v-for="[s, lb] in tiles" :key="s" type="button" class="tile" :class="[s, { on: filter === s }]" @click="pick(s)">{{ lb }} <b>{{ counts[s] }}</b></button>
        <span v-if="data.mode === 'partial'" class="muted">{{ data.mode_label }}</span>
      </div>
      <V2Table :rows="shown" :cols="cols" :export-cols="xcols" prefix="L" unit="項" empty="這個結果沒有項目" :max="500">
        <template #cell-name="{ row }">
          <NuxtLink v-if="link(row as any)" :to="link(row as any)!"><b>{{ row.name }}</b></NuxtLink>
          <b v-else>{{ row.name || (row.count ? `${row.count} 台候選` : '—') }}</b>
          <div v-if="row.candidates_text && !row.name" class="sm" :title="row.candidates_text">{{ String(row.candidates_text).slice(0, 40) }}{{ String(row.candidates_text).length > 40 ? '…' : '' }}</div>
          <div v-else-if="row.type" class="sm">{{ row.type }}</div>
        </template>
      </V2Table>
    </template>
  </section>
</template>

<style scoped>
.bl { display: flex; flex-direction: column; gap: 8px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.ta { font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; padding: 8px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: 100%; max-width: 640px; box-sizing: border-box; background: var(--card); color: var(--ink); }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 14px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2.pri { border-color: var(--brand); background: var(--brand); color: var(--type-on); }
.btn2:disabled { opacity: .5; cursor: default; }
.chk { display: inline-flex; gap: 4px; align-items: center; }
.tiles { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.tile { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.tile.on { border-color: var(--brand); background: var(--mint); }
.tile.none b, .tile.partial b { color: var(--warn-text); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; margin: 0; }
.sm { color: var(--ink-aux); font-size: 12px; }
.mono { font-family: var(--disp); }
</style>
