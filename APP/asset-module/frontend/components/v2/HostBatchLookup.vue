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
  { k: 'matched_by', label: '比對方式', text: (r: LookupRow) => r.matched_by || '—' },
  { k: 'name', label: '主機名稱', link, text: (r: LookupRow) => r.name || (r.count ? `${r.count} 台候選` : '—') },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'type', label: '類型' },
  { k: 'system', label: '業務系統' },
  { k: 'where', label: '位置' },
  { k: 'candidates_text', label: '候選主機', brief: (r: LookupRow) => (r.candidates_text.length > 40 ? r.candidates_text.slice(0, 40) : '') },
  { k: 'input_count', label: '輸入次數', mono: true },
  { k: 'hint', label: '說明' },
]
</script>

<template>
  <section class="bl">
    <div class="bar">
      <b>貼多筆批次查詢</b>
      <InfoNote>每行一筆（也可用逗號、空白分隔），可貼主機名、IP、資產編號、APID。預設精確比對：資產編號、IP、主機名（不分大小寫，FQDN 與短名視為同名）、APID，與值班卡、首頁搜尋同一套規則。
        勾選「含部分比對」才會比對部分字串，結果標示為「部分命中」，不計入「找到」。查無的每一筆都會列出。不支援以機器代號直接查詢。
        上限：原始 {{ data?.limits.max_raw_lines ?? 2000 }} 行、每項 {{ data?.limits.max_term_len ?? 128 }} 字元、去除重複後 {{ data?.limits.max_unique ?? 500 }} 筆。此功能只讀取，不寫入資料、不連線目標主機。</InfoNote>
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
        <span class="muted">{{ data.mode_label }}；輸入 {{ data.summary.raw_lines }} 行，空白 {{ data.summary.blank }}，重複合併 {{ data.summary.duplicates_merged }}，實際查詢 {{ data.summary.unique }} 項</span>
      </div>
      <V2Table :rows="shown" :cols="cols" prefix="L" unit="項" empty="這個結果沒有項目" :max="500" />
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
</style>
