<script setup lang="ts">
// 1.2 異動與異常（2.x 藍圖）：頁籤「每日異動｜掃描異常」（2.006.6 找碴 C14：「異常事件」只留給 4.1 事件應變）。合併舊 1-2 今日變化、1-4 問題清單、1-5 異常報告；
// 帳實比對（過程數字）移到 9.2 數量對帳（任務走查 8）。資料：/api/changes（每晚快照比對）、/api/issues（異常事件）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
interface Cat { key: string; label: string }
interface Changes { dates: string[]; categories: Cat[]; date: string | null; prev_date: string | null; items: Record<string, any[]>; counts: Record<string, number>; snapshot?: { created_at: string } | null; reason?: string | null
  freshness?: { today: boolean; no_new_input: boolean; reason: string | null; latest_input: string | null } }
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
// 異常報告（2.008.8，舊版 /anomalies）：直接嵌入既有畫面，只讀
const Anomalies = defineAsyncComponent(() => import('~/pages/anomalies.vue'))
const TABS = [{ k: 'daily', label: '每日異動' }, { k: 'issues', label: '掃描異常' }, { k: 'anomaly', label: '異常報告' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'daily'), set: (v: string) => router.replace({ query: { ...route.query, tab: v, cat: undefined } }) })
const day = computed(() => (route.query.date ? String(route.query.date) : undefined))
const cat = computed(() => (route.query.cat ? String(route.query.cat) : ''))
const ch = ref<Changes | null>(null)
const chErr = ref('')
async function loadChanges() {
  try { ch.value = await apiFetch<Changes>('/api/changes', { query: { date: day.value } }); chErr.value = '' } catch (e: any) { chErr.value = `每日異動載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
await loadChanges()
watch(day, loadChanges)
// 找碴 C16：沒有前一份快照可比、或看的是最新一份但今天沒收集、或資料來源沒更新→不顯示 0，寫無資料
const noData = computed(() => !ch.value?.date || !ch.value?.prev_date || (!day.value && !!ch.value?.freshness && (!ch.value.freshness.today || ch.value.freshness.no_new_input)))
const catRows = computed(() => (ch.value?.categories ?? []).map(c => ({ key: c.key, label: c.label, n: ch.value?.counts?.[c.key] ?? 0, n_text: noData.value && !(ch.value?.counts?.[c.key]) ? '無資料' : (ch.value?.counts?.[c.key] ?? 0) })))
const catLabel = computed(() => ch.value?.categories.find(c => c.key === cat.value)?.label || cat.value)
const itemRows = computed(() => (ch.value?.items?.[cat.value] ?? []).map((x: any) => ({
  ...x, name: x.hostname || x.serial || x.key, ip_text: Array.isArray(x.ips) ? x.ips.join('、') : x.ips,
  before_text: Array.isArray(x.before) ? x.before.join('、') : x.before, after_text: Array.isArray(x.after) ? x.after.join('、') : x.after })))
function openCat(k: string) { router.replace({ query: { ...route.query, tab: 'daily', cat: k } }) }
function pickDate(e: Event) { router.replace({ query: { ...route.query, date: (e.target as HTMLSelectElement).value || undefined, cat: undefined } }) }

// 異常事件（舊問題清單／異常報告的清單部分）
interface Issue { id: number; detected_at: string; hostname: string | null; ip: string | null; issue_type: string; is_read: number; handled_at: string | null }
const issues = ref<Issue[]>([])
const issueErr = ref('')
const issueFilter = ref<'open' | 'all'>('open')
const SCAN_ALERT = '掃描無回應'
async function loadIssues() {
  try { issues.value = await apiFetch<Issue[]>('/api/issues', { query: { sort_by: 'detected_at', order: 'desc' } }); issueErr.value = '' } catch (e: any) { issueErr.value = `掃描異常載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(loadIssues)
// 項次在篩選前編好（找碴 C19：切「未處理／全部」不重新編號）
const issueRows = computed(() => issues.value.map((i, n) => ({ i, n: n + 1 })).filter(({ i }) => issueFilter.value === 'all' || !i.is_read)
  .map(({ i, n }) => {
    // 掃描無回應（scan_health）不是單一主機：hostname 欄放的是那句說明，不可當主機連到主機履歷
    const scan = i.issue_type === SCAN_ALERT
    return { ...i, __no: n, scan, name: scan ? '（網段掃描）' : (i.hostname || i.ip || '—'), note: scan ? i.hostname : '',
      state: i.is_read ? `已處理 ${i.handled_at?.slice(0, 16) || ''}` : '未處理' }
  }))
const openCount = computed(() => issues.value.filter(i => !i.is_read).length)
const picked = ref<Record<string, boolean>>({})
const pickedIds = computed(() => Object.keys(picked.value).filter(k => picked.value[k]).map(Number))
async function markRead() {
  if (!pickedIds.value.length) return
  if (!window.confirm(`將選取的 ${pickedIds.value.length} 筆標記為已處理？`)) return
  try {
    const r = await guardedFetch<{ marked: number }>('/api/issues/mark-read', { method: 'POST', body: { ids: pickedIds.value } })
    showToast(`已標記 ${r.marked} 筆為已處理`, 'success')
    picked.value = {}
    await loadIssues()
  } catch (e: any) { showToast(`標記失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
}
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">1.2 異動與異常</h1></template>
    <template #conclusion>
      <span v-if="ch">每日異動：{{ ch.date || '尚無快照' }} 對比 {{ ch.prev_date || '—' }}，<template v-if="noData">無資料</template><template v-else>共 {{ catRows.reduce((a, c) => a + c.n, 0) }} 筆</template>・掃描異常未處理 {{ openCount }} 筆</span>
      <span class="aside">帳實比對已移至 <NuxtLink to="/v2/reconcile?tab=recon">9.2 數量對帳</NuxtLink></span>
    </template>
    <template #tab>
      <ClientOnly v-if="tab === 'anomaly'"><V2Embed><Anomalies /></V2Embed></ClientOnly>
      <template v-else-if="tab === 'daily'">
        <p v-if="chErr" class="err">{{ chErr }}</p>
        <div class="bar">
          <label>日期 <select :value="ch?.date || ''" @change="pickDate"><option v-for="d in ch?.dates ?? []" :key="d" :value="d">{{ d }}</option></select></label>
          <span v-if="ch?.reason" class="muted">{{ ch.reason }}</span>
          <span v-if="!day && ch?.freshness?.reason" class="stale">{{ ch.freshness.reason }}</span>
          <InfoNote>每晚 05:30 留一份每台主機的快照，與前一份比對。點筆數看明細；摘要信設定在 <NuxtLink to="/v2/settings?st=notify">⚙ 系統設定・通知設定</NuxtLink>。</InfoNote>
        </div>
        <V2Table v-if="!cat" :rows="catRows" prefix="C" :cols="[{ k: 'label', label: '分類' }, { k: 'n', label: '筆數', mono: true, text: (r: any) => r.n_text, link: (r: any) => r.n ? `/v2/changes?tab=daily&cat=${r.key}${ch?.date ? '&date=' + ch.date : ''}` : null }]" />
        <template v-else>
          <div class="bar"><button type="button" class="lb" @click="router.replace({ query: { ...route.query, cat: undefined } })">‹ 全部分類</button><b>{{ catLabel }}</b><span class="muted">{{ itemRows.length }} 筆</span></div>
          <V2Table :rows="itemRows" prefix="D" empty="此分類沒有異動"
                   :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip_text', label: 'IP', mono: true }, { k: 'before_text', label: '異動前' }, { k: 'after_text', label: '異動後' }, { k: 'detail', label: '說明' }]" />
        </template>
      </template>
      <template v-else>
        <p v-if="issueErr" class="err">{{ issueErr }}</p>
        <div class="bar">
          <button type="button" class="chip" :class="{ on: issueFilter === 'open' }" @click="issueFilter = 'open'">未處理 {{ openCount }}</button>
          <button type="button" class="chip" :class="{ on: issueFilter === 'all' }" @click="issueFilter = 'all'">全部 {{ issues.length }}</button>
          <button v-if="!isViewer" type="button" class="btn" :disabled="!pickedIds.length" @click="markRead">標記已處理（{{ pickedIds.length }}）</button>
        </div>
        <V2Table v-model:selected="picked" :rows="issueRows" prefix="E" empty="沒有掃描異常" selectable row-key="id" :can-select="(r: any) => !r.is_read" :cols="[{ k: 'detected_at', label: '偵測時間', mono: true }, { k: 'name', label: '主機名稱', link: (r: any) => (r.scan ? null : `/v2/machine/${encodeURIComponent(r.ip || r.hostname || '')}`) }, { k: 'ip', label: 'IP', mono: true }, { k: 'issue_type', label: '類型' }, { k: 'note', label: '說明', link: (r: any) => (r.scan ? '/v2/onboard?tab=gaps' : null) }, { k: 'state', label: '處理狀態' }]" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.aside { margin-left: 16px; font-size: 13px; color: var(--ink-aux); }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font: inherit; padding: 0; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn:disabled { opacity: .5; cursor: default; }
.muted { color: var(--muted); }
.mono { font-family: var(--disp); }
.err { color: var(--bad); }
.stale { padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); }
</style>
