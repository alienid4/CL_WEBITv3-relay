<script setup lang="ts">
import { GROUP_BUCKET, groupSegments } from '~/utils/overviewSegments'
// 10.1「主機系統總覽」（V50，2.048.6 起 V2 原生；取代嵌入的舊頁 pages/reports/system-overview.vue）。同一批 API：
// /api/reports/system-overview 與 /drill（加總與清單同一份後端計算，格子數字＝點進去的筆數），這裡不另算台數。
// 圓環中心＝分段加總（含未分類，utils/overviewSegments，W221-014）。
// 下鑽在網址：?tab=overview&ob=<core|noncore|test|uncategorized>&or=<機房>（只帶 or＝那個機房全環境）。
const { apiFetch } = useApi()
const route = useRoute()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())

const data = ref<any>(null)
const err = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  try { const r = await apiFetch<any>('/api/reports/system-overview'); if (!isLatest()) return; data.value = r; err.value = '' } catch (e: any) { if (isLatest()) err.value = `主機系統總覽載入失敗：${errMsg(e)}` }
}
onMounted(load)

const u = (extra: Record<string, string | undefined> = {}) =>
  ({ path: '/v2/report', query: Object.fromEntries(Object.entries({ tab: 'overview', ...extra }).filter(([, v]) => v !== undefined && v !== '')) })
const segs = computed(() => groupSegments(data.value))
const pct = (x: number) => (data.value?.total ? (x / data.value.total * 100).toFixed(1) : '0.0')
const platform = computed(() => (data.value ? [
  { name: 'Virtual Machine', count: data.value.vm, color: 'var(--brand-dark)' },
  { name: 'Physical Server', count: data.value.physical, color: 'var(--chart-gray)' },
] : []))
const BUCKET_LABEL: Record<string, string> = { core: '核心交易', noncore: '非核心', test: '測試', uncategorized: '尚未分類' }
const segRows = computed(() => segs.value.map((s, i) => ({ ...s, __no: i + 1, bucket: GROUP_BUCKET[s.name], pct_t: `${pct(s.count)}%` })))
const roomRows = computed(() => (data.value?.rooms ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1 })))
const cellLink = (bucket?: string) => (r: any) => ((bucket ? r[bucket] : r.total) ? u({ or: r.room, ob: bucket }) : null)

// ===== 下鑽 =====
const drillQ = computed(() => (q('ob') || q('or') ? { bucket: q('ob'), room: q('or') } : null))
const drillTitle = computed(() => {
  const d = drillQ.value
  if (!d) return ''
  const b = d.bucket ? BUCKET_LABEL[d.bucket] ?? d.bucket : '全環境'
  return d.room ? `${d.room}・${b}` : b
})
const drillRows = ref<any[]>([])
const drillErr = ref('')
const drillLoading = ref(false)
const beginDrill = useLatest()
watch(drillQ, async (d) => {
  const isLatest = beginDrill()
  drillRows.value = []
  drillErr.value = ''
  drillLoading.value = false
  if (!d) return
  drillLoading.value = true
  const query: Record<string, string> = {}
  if (d.bucket) query.bucket = d.bucket
  if (d.room) query.room = d.room
  try {
    const r = await apiFetch<any[]>('/api/reports/system-overview/drill', { query })
    if (!isLatest()) return
    drillRows.value = r.map((x: any, i: number) => ({ ...x, __no: i + 1 }))
    drillErr.value = ''
  } catch (e: any) { if (isLatest()) drillErr.value = `明細載入失敗：${errMsg(e)}` } finally { if (isLatest()) drillLoading.value = false }
}, { immediate: true })
const drillCols = [
  { k: 'hostname', label: '主機名', text: (r: any) => r.hostname || r.asset_serial, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` },
  { k: 'ip', label: 'IP', mono: true }, { k: 'api_id', label: '業務系統', mono: true, text: (r: any) => r.api_id || '（無）' },
  { k: 'environment', label: '環境別', text: (r: any) => r.environment || '（空）' }, { k: 'os_canonical', label: 'OS', text: (r: any) => r.os_canonical || '認不出' },
  { k: 'reason', label: '判定依據' }]
</script>

<template>
  <div>
    <template v-if="drillQ">
      <div class="bar"><NuxtLink :to="u()">‹ 返回</NuxtLink><b>{{ drillTitle }}</b><span class="muted">{{ drillLoading ? '載入中…' : `${drillRows.length.toLocaleString()} 台` }}</span></div>
      <p v-if="drillErr" class="err">{{ drillErr }}</p>
      <V2Table v-else :rows="drillRows" prefix="D" row-key="asset_serial" unit="台" :max="200" :cols="drillCols"
               :export-name="`主機系統總覽_${drillTitle}`" :empty="drillLoading ? '載入中…' : '沒有符合的主機'" />
    </template>
    <template v-else>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-else-if="!data" class="muted">載入中…</p>
    <template v-else>
      <div class="bar">
        <span>全環境設備 <b class="mono">{{ n(data.total) }}</b> 台・系統虛擬化率 <b class="mono">{{ data.virtualization_rate }}%</b></span>
        <InfoNote>{{ data.category_note }}</InfoNote>
        <button type="button" class="btn2" @click="load">重新整理</button>
      </div>
      <div class="top2">
        <div class="card">
          <b>全環境系統架構分布</b>
          <DonutChart :segments="segs" :size="196" :stroke-width="22" center-label="全環境設備" @segment-click="(name: string) => navigateTo(u({ ob: GROUP_BUCKET[name] }))" />
        </div>
        <div class="card">
          <b>運算平台概況</b>
          <DonutChart :segments="platform" :size="150" :stroke-width="16" center-label="總設備數" @segment-click="() => {}" />
          <span class="muted">Virtual Machine {{ n(data.vm) }}（{{ pct(data.vm) }}%）・Physical Server {{ n(data.physical) }}（{{ pct(data.physical) }}%）</span>
        </div>
      </div>
      <h3 class="h3">系統架構分組（台）</h3>
      <V2Table :rows="segRows" prefix="G" row-key="name" unit="組" export-name="主機系統總覽_分組"
               :cols="[{ k: 'name', label: '分組' }, { k: 'count', label: '台數', mono: true, link: (r: any) => (r.count ? u({ ob: r.bucket }) : null) }, { k: 'pct_t', label: '占全環境' }]" />
      <p v-if="data.uncategorized" class="warn">其中 {{ n(data.uncategorized) }} 台還沒有歸入核心／非核心／測試任何一類
        <InfoNote>不看環境別，所有環境都算。到 3.1 業務系統的「業務系統排行」頁籤補上對照表就會自動歸類。</InfoNote>
        <NuxtLink :to="u({ ob: 'uncategorized' })">看是哪幾台</NuxtLink>・<NuxtLink to="/v2/systems?tab=rank">去補對照表</NuxtLink></p>
      <h3 class="h3">各機房系統資源分布（實體機＋虛擬機）<InfoNote>{{ data.room_note }}</InfoNote></h3>
      <V2Table :rows="roomRows" prefix="R" row-key="room" unit="個機房" export-name="主機系統總覽_各機房"
               :cols="[{ k: 'room', label: '機房' },
                       { k: 'core', label: '核心交易', mono: true, link: cellLink('core') },
                       { k: 'noncore', label: '非核心', mono: true, link: cellLink('noncore') },
                       { k: 'test', label: '測試', mono: true, link: cellLink('test') },
                       { k: 'total', label: '全環境', mono: true, link: cellLink() }]" />
    </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 14px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.top2 { display: flex; gap: 14px; flex-wrap: wrap; }
.card { flex: 1 1 300px; display: flex; flex-direction: column; gap: 8px; align-items: center; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.warn a { color: var(--link); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
