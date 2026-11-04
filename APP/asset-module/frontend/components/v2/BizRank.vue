<script setup lang="ts">
// 3.1 業務系統「排行與分類匯入」頁籤（V45，2.048.6 起 V2 原生；取代嵌入的舊頁 pages/reports/business-systems.vue）。
// 同一支 /api/reports/system-overview（跟 10.1 主機系統總覽同一份 report_baseline，數字永遠對得上）與 /drill；這裡不另算台數。
// 下鑽在網址：?tab=rank&bb=<core|noncore|test|uncategorized|all>&ba=<API ID>&bc=<分類>（bb=all＝全環境）。
// 業務系統分類對照表：範本下載（一般帳號以上）、上傳（管理員；後端 ADMIN_WRITE），行為照舊。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const rt = useRuntimeConfig()
const { canWrite, isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())

const data = ref<any>(null)
const err = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  try { const r = await apiFetch<any>('/api/reports/system-overview'); if (!isLatest()) return; data.value = r; err.value = '' } catch (e: any) { if (isLatest()) err.value = `業務系統排行載入失敗：${errMsg(e)}` }
}
onMounted(load)

const u = (extra: Record<string, string | undefined> = {}) =>
  ({ path: '/v2/systems', query: Object.fromEntries(Object.entries({ tab: 'rank', ...extra }).filter(([, v]) => v !== undefined && v !== '')) })
const pct = (x: number) => (data.value?.total ? (x / data.value.total * 100).toFixed(1) : '0.0')
const BUCKET_LABEL: Record<string, string> = { core: '核心交易', noncore: '非核心', test: '測試環境', uncategorized: '未分類', all: '全環境' }
const strip = computed(() => {
  const d = data.value
  if (!d) return []
  return [
    { b: 'core', label: '核心交易服務', n: d.core }, { b: 'noncore', label: '非核心服務', n: d.noncore }, { b: 'test', label: '測試環境', n: d.test },
    { b: 'uncategorized', label: '未分類', n: d.uncategorized ?? 0 }, { b: 'all', label: '全環境設備', n: d.total },
  ].map(x => ({ ...x, pct: x.b === 'all' ? '100' : pct(x.n) }))
})
const top5Rows = computed(() => (data.value?.core_top5 ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1, pct_t: `${(data.value?.core_top5_pct?.[i] ?? 0).toFixed(1)}%` })))
const catRows = (k: 'core_categories' | 'noncore_categories') => (data.value?.[k] ?? []).map((c: any, i: number) => ({ ...c, __no: i + 1, pct_t: `${(c.pct ?? 0).toFixed(1)}%` }))
// 項次＝後端排行順序（依台數），排序後同一個系統仍是同一號
const sysRows = computed(() => (data.value?.all_systems ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1, cat_t: s.category || '未分類' })))

// ===== 下鑽 =====
const drillQ = computed(() => (q('bb') || q('ba') || q('bc') ? { bucket: q('bb'), api_id: q('ba'), category: q('bc') } : null))
const drillTitle = computed(() => {
  const d = drillQ.value
  if (!d) return ''
  if (d.api_id) return (data.value?.all_systems ?? []).find((s: any) => s.api_id === d.api_id)?.name ?? d.api_id
  if (d.category) return d.category
  return BUCKET_LABEL[d.bucket] ?? d.bucket
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
  if (d.bucket && d.bucket !== 'all') query.bucket = d.bucket
  if (d.api_id) query.api_id = d.api_id
  if (d.category) query.category = d.category
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

// ===== 業務系統分類對照表 =====
const tplBusy = ref(false)
async function downloadTemplate() {
  tplBusy.value = true
  try {
    const res = await fetch(`${rt.public.apiBase}/api/reports/system-category/template`, { credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const blob = await res.blob()
    const m = (res.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/)
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href; a.download = m ? decodeURIComponent(m[1]) : 'system_category_template.xlsx'; a.click()
    URL.revokeObjectURL(href)
  } catch (e: any) { showToast(`下載失敗：${errMsg(e)}`, 'error') } finally { tplBusy.value = false }
}
const uploading = ref(false)
const uploadMsg = ref('')
async function uploadCategory(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true; uploadMsg.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    const r = await apiFetch<any>('/api/reports/system-category/import', { method: 'POST', body: fd })
    uploadMsg.value = r.rejected
      ? `已套用 ${r.accepted} 筆；${r.rejected} 筆分類名稱看不懂（只認：${(r.valid_categories ?? []).join('／')}），未套用`
      : `已套用 ${r.accepted} 筆分類`
    showToast(uploadMsg.value, r.rejected ? 'warn' : 'success')
    await load()
  } catch (e2: any) { uploadMsg.value = errMsg(e2); showToast(uploadMsg.value, 'error') } finally { uploading.value = false; input.value = '' }
}
</script>

<template>
  <div>
    <template v-if="drillQ">
      <div class="bar"><NuxtLink :to="u()">‹ 返回</NuxtLink><b>{{ drillTitle }}</b><span class="muted">{{ drillLoading ? '載入中…' : `${drillRows.length.toLocaleString()} 台` }}</span></div>
      <p v-if="drillErr" class="err">{{ drillErr }}</p>
      <V2Table v-else :rows="drillRows" prefix="D" row-key="asset_serial" unit="台" :max="200" :cols="drillCols"
               :export-name="`業務系統明細_${drillTitle}`" :empty="drillLoading ? '載入中…' : '沒有符合的主機'" />
    </template>
    <template v-else>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-else-if="!data" class="muted">載入中…</p>
    <template v-else>
      <div class="bar">
        <span class="muted">全環境系統組成</span>
        <InfoNote>{{ data.category_note }}　跟 10.1「主機系統總覽」同一份資料，數字對得上。</InfoNote>
        <button type="button" class="btn2" @click="load">重新整理</button>
      </div>
      <div class="cards">
        <NuxtLink v-for="c in strip" :key="c.b" class="kc" :to="u({ bb: c.b })">
          <span class="kl">{{ c.label }}</span><b class="kv mono">{{ n(c.n) }}<small> 台</small></b><span class="muted">{{ c.pct }}%</span>
        </NuxtLink>
      </div>
      <div class="two">
        <div>
          <h3 class="h3">核心交易服務 Top 5<span class="muted">共 {{ n(data.core) }} 台・與台股交易熱度呈高度正相關成長</span></h3>
          <V2Table :rows="top5Rows" prefix="T" row-key="api_id" unit="個系統" export-name="業務系統排行_核心Top5" empty="核心交易服務目前還沒有系統被分類"
                   :cols="[{ k: 'name', label: '系統' }, { k: 'count', label: '台數', mono: true, link: (r: any) => u({ ba: r.api_id, bb: 'core' }) }, { k: 'pct_t', label: '占核心' }]" />
          <p v-if="data.core_other_count" class="muted">核心交易服務其他系統（小計）<b class="mono">{{ n(data.core_other_count) }}</b>
            <InfoNote>小計是 Top 5 以外的核心系統台數。沒有單獨的清單（後端下鑽沒有「排除 Top 5」的條件），要看是哪幾台請點上面「核心交易服務」卡片、或下面完整系統清單逐個系統點。</InfoNote></p>
          <h3 class="h3">其他重要系統（核心交易服務）</h3>
          <V2Table :rows="catRows('core_categories')" prefix="C" row-key="name" unit="類" export-name="業務系統排行_核心分類"
                   :cols="[{ k: 'name', label: '分類' }, { k: 'count', label: '台數', mono: true, link: (r: any) => (r.count ? u({ bc: r.name }) : null) }, { k: 'pct_t', label: '占核心' }]" />
        </div>
        <div>
          <h3 class="h3">非核心服務<span class="muted">共 {{ n(data.noncore) }} 台・與開戶人數、公司規模呈高度相關成長</span></h3>
          <V2Table :rows="catRows('noncore_categories')" prefix="N" row-key="name" unit="類" export-name="業務系統排行_非核心分類" empty="非核心服務目前還沒有系統被分類"
                   :cols="[{ k: 'name', label: '分類' }, { k: 'count', label: '台數', mono: true, link: (r: any) => (r.count ? u({ bc: r.name }) : null) }, { k: 'pct_t', label: '占非核心' }]" />
        </div>
      </div>
      <h3 class="h3">完整系統清單（依台數排序）<InfoNote>所有業務系統的原始排行，不受上面分類影響。項次＝排行，重新排序後同一個系統仍是同一號。</InfoNote></h3>
      <V2Table :rows="sysRows" prefix="S" row-key="api_id" unit="個系統" :max="200" export-name="業務系統完整清單"
               :cols="[{ k: 'name', label: '系統名稱', link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'api_id', label: 'API ID', mono: true },
                       { k: 'count', label: '台數', mono: true, link: (r: any) => u({ ba: r.api_id }) }, { k: 'cat_t', label: '分類' }]" />
      <h3 class="h3">業務系統分類對照表
        <InfoNote>下載範本填好分類欄再上傳（可用分類見範本第二分頁），這裡與 10.1 主機系統總覽都會立刻反映。
          這張表是按業務系統（APID）分類的預設值，真正決定報表數字的是逐台的分類：一個 APID 一類，只當「那台主機還沒逐台分類時的預設值」，
          同一個業務系統的機器不見得同一類。要一次帶入大量逐台分類，請到 9.1 待補資料「主機分類」頁籤。</InfoNote></h3>
      <div class="bar">
        <span>{{ n(data.systems_without_category) }} / {{ n(data.all_systems.length) }} 個系統尚未分類</span>
        <span class="warn">⚠ 這是按業務系統（APID）的預設值，報表數字以逐台分類為準</span>
      </div>
      <div class="bar">
        <button v-if="canWrite" type="button" class="btn2" :disabled="tplBusy" @click="downloadTemplate">{{ tplBusy ? '準備中…' : '下載空白範本（依台數排序）' }}</button>
        <label v-if="isAdmin" class="btn2">{{ uploading ? '上傳中…' : '上傳填好的對照表' }}
          <input type="file" accept=".xlsx" :disabled="uploading" hidden @change="uploadCategory"></label>
        <span v-else class="muted" data-test="admin-only">上傳對照表限管理員</span>
        <span v-if="uploadMsg" class="muted">{{ uploadMsg }}</span>
      </div>
    </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 14px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.h3 .muted { font-weight: 400; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
.kc { display: flex; flex-direction: column; gap: 2px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; color: var(--ink); text-decoration: none; }
.kc:hover { border-color: var(--brand); }
.kl { font-size: 12.5px; color: var(--ink-soft); }
.kv { font-size: 20px; color: var(--brand-dark); }
.kv small { font-size: 12px; color: var(--muted); }
.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1000px) { .two { grid-template-columns: 1fr; } }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; display: inline-flex; align-items: center; }
.btn2:disabled { opacity: .5; cursor: default; }
.warn { color: var(--warn-text); font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
