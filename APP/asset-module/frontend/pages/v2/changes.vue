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
const Anomalies = defineAsyncComponent(() => import('~/components/v2/Anomalies.vue'))      // 2.047.3 V18：V2 原生
const TABS = [{ k: 'daily', label: '每日異動' }, { k: 'week', label: '本週異動' }, { k: 'issues', label: '掃描異常' }, { k: 'anomaly', label: '異常報告' }]
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
// V63（2.043.4）：舊 /changes 的「立即寫一份快照」搬過來（同一天重寫＝覆蓋；只能看的帳號不顯示，後端另外擋）
const snapping = ref(false)
async function snapNow() {
  snapping.value = true
  try {
    const r = await guardedFetch<{ snap_date: string; machines: number }>('/api/changes/snapshot', { method: 'POST' })
    showToast(`已寫入 ${r.snap_date} 快照：${r.machines.toLocaleString()} 台`, 'success')
    if (day.value) router.replace({ query: { ...route.query, date: undefined, cat: undefined } })
    else await loadChanges()
  } catch (e: any) { showToast(`寫快照失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { snapping.value = false }
}
// 找碴 C16：沒有前一份快照可比、或看的是最新一份但今天沒收集、或資料來源沒更新→不顯示 0，寫無資料
const noData = computed(() => !ch.value?.date || !ch.value?.prev_date || (!day.value && !!ch.value?.freshness && (!ch.value.freshness.today || ch.value.freshness.no_new_input)))
const catRows = computed(() => (ch.value?.categories ?? []).map(c => ({ key: c.key, label: c.label, n: ch.value?.counts?.[c.key] ?? 0, n_text: noData.value && !(ch.value?.counts?.[c.key]) ? '無資料' : (ch.value?.counts?.[c.key] ?? 0) })))
const catLabel = computed(() => ch.value?.categories.find(c => c.key === cat.value)?.label || cat.value)
const itemRows = computed(() => (ch.value?.items?.[cat.value] ?? []).map((x: any) => ({
  ...x, name: x.hostname || x.serial || x.key, ip_text: Array.isArray(x.ips) ? x.ips.join('、') : x.ips,
  before_text: Array.isArray(x.before) ? x.before.join('、') : x.before, after_text: Array.isArray(x.after) ? x.after.join('、') : x.after })))
// 本週異動（2.044.0 從 1.1 首頁搬來；舊網址 /v2?tab=week 會轉到這裡）：/api/v2/home/week，每天＝「每日異動」同一份比對
const week = ref<any>(null)
const weekErr = ref('')
async function loadWeek() { if (week.value) return; try { week.value = await apiFetch('/api/v2/home/week'); weekErr.value = '' } catch (e: any) { weekErr.value = `本週異動載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } }
if (tab.value === 'week') await loadWeek()
watch(tab, t => { if (t === 'week') loadWeek() })
const weekRows = computed(() => (week.value?.rows ?? []).map((r: any) => ({ ...r, n_text: r.n == null ? '無資料' : r.n })))
function openCat(k: string) { router.replace({ query: { ...route.query, tab: 'daily', cat: k } }) }
function pickDate(e: Event) { router.replace({ query: { ...route.query, date: (e.target as HTMLSelectElement).value || undefined, cat: undefined } }) }

// 異常事件（舊問題清單／異常報告的清單部分）
interface Issue { id: number; detected_at: string; hostname: string | null; ip: string | null; issue_type: string; is_read: number; handled_at: string | null }
const issues = ref<Issue[]>([])
const issueErr = ref('')
const issueLoaded = ref(false) // 曾成功取得（含成功但為空）；false＋有錯誤＝來源未取得，不可顯示 0
// 舊 /issues?show=all（含已處理）轉過來時直接停在「全部」（N23）
const issueFilter = ref<'open' | 'all'>(route.query.show === 'all' ? 'all' : 'open')
const SCAN_ALERT = '掃描無回應'
async function loadIssues() {
  try { issues.value = await apiFetch<Issue[]>('/api/issues', { query: { sort_by: 'detected_at', order: 'desc' } }); issueErr.value = ''; issueLoaded.value = true } catch (e: any) { issueErr.value = `掃描異常載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(loadIssues)
// 項次在篩選前編好（找碴 C19：切「未處理／全部」不重新編號）
const issueRows = computed(() => issues.value.map((i, n) => ({ i, n: n + 1 })).filter(({ i }) => issueFilter.value === 'all' || !i.is_read).filter(({ i }) => !issueType.value || i.issue_type === issueType.value)
  .map(({ i, n }) => {
    // 掃描無回應（scan_health）不是單一主機：hostname 欄放的是那句說明，不可當主機連到主機履歷
    const scan = i.issue_type === SCAN_ALERT
    return { ...i, __no: n, scan, name: scan ? '（網段掃描）' : (i.hostname || i.ip || '—'), note: scan ? i.hostname : '',
      state: i.is_read ? `已處理 ${i.handled_at?.slice(0, 16) || ''}` : '未處理' }
  }))
const issueUnknown = computed(() => !!issueErr.value && !issueLoaded.value)
const openCount = computed(() => issues.value.filter(i => !i.is_read).length)
// 類型篩選（舊 /issues 的「異常消失／異常新增／漏登記」；也讀 ?type= 深連結）。類型清單＝固定三類＋資料裡實際出現的其他類型
const ISSUE_TYPES = ['異常消失', '異常新增', '漏登記']
const TYPE_HINT: Record<string, string> = { 異常消失: '之前掃得到、這次掃不到。可能是關機、換 IP、或真的下線了。', 異常新增: '之前沒看過、這次突然出現在網段上的主機。', 漏登記: '掃描掃得到，但 CIA 沒有這台的登記資料。' }
const issueType = computed(() => (route.query.type ? String(route.query.type) : ''))      // 跟著網址走（上一頁／下一頁也同步）
const typeList = computed(() => [...new Set([...ISSUE_TYPES, ...issues.value.map(i => i.issue_type)])])
const typeCount = (t: string) => issues.value.filter(i => (!t || i.issue_type === t) && (issueFilter.value === 'all' || !i.is_read)).length
function pickType(t: string) { picked.value = {}; router.replace({ query: { ...route.query, tab: 'issues', type: t || undefined } }) }
const picked = ref<Record<string, boolean>>({})
const pickedIds = computed(() => Object.keys(picked.value).filter(k => picked.value[k]).map(Number))
async function markIds(ids: number[]) {
  try {
    const r = await guardedFetch<{ marked: number }>('/api/issues/mark-read', { method: 'POST', body: { ids } })
    showToast(`已標記 ${r.marked} 筆為已處理`, 'success')
    picked.value = {}
    await loadIssues()
  } catch (e: any) { showToast(`標記失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
}
async function markRead() {
  if (!pickedIds.value.length) return
  if (!window.confirm(`將選取的 ${pickedIds.value.length} 筆標記為已處理？`)) return
  await markIds(pickedIds.value)
}
// 單筆標已處理（舊 /issues 逐筆「標記已處理」）：同一支 mark-read、只送這一筆
async function markOne(r: { id: number; name: string; issue_type: string }) {
  if (!window.confirm(`將「${r.name}」（${r.issue_type}）標記為已處理？`)) return
  await markIds([r.id])
}
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">1.2 異動與異常</h1></template>
    <template #conclusion>
      <span v-if="ch">每日異動：{{ ch.date || '尚無快照' }} 對比 {{ ch.prev_date || '—' }}，<template v-if="noData">無資料</template><template v-else>共 {{ catRows.reduce((a, c) => a + c.n, 0) }} 筆</template>・<template v-if="issueUnknown">掃描異常未取得（讀取失敗）</template><template v-else>掃描異常未處理 {{ openCount }} 筆<template v-if="issueErr">（刷新失敗，顯示最後成功資料）</template></template></span>
      <span class="aside">帳實比對已移至 <NuxtLink to="/v2/reconcile?tab=recon">9.2 數量對帳</NuxtLink></span>
    </template>
    <template #tab>
      <ClientOnly v-if="tab === 'anomaly'"><Anomalies /></ClientOnly>
      <template v-else-if="tab === 'daily'">
        <p v-if="chErr" class="err">{{ chErr }}</p>
        <div class="bar">
          <label>日期 <select :value="ch?.date || ''" @change="pickDate"><option v-for="d in ch?.dates ?? []" :key="d" :value="d">{{ d }}</option></select></label>
          <span v-if="ch?.reason" class="muted">{{ ch.reason }}</span>
          <span v-if="!day && ch?.freshness?.reason" class="stale">{{ ch.freshness.reason }}</span>
          <InfoNote>每晚 05:30 留一份每台主機的快照，與前一份比對。位置記的是 CIA 登記的位置（機櫃名用畫面上的櫃名），不套用機櫃圖上的「位置更正」——現場更正過的設備，這裡跟機櫃圖、主機詳細頁會不一樣，直到 CIA 改成跟現場一致。點筆數看明細；摘要信設定在 <NuxtLink to="/v2/settings?st=notify">⚙ 系統設定・通知設定</NuxtLink>。</InfoNote>
          <button v-if="!isViewer" type="button" class="btn small" :disabled="snapping" @click="snapNow">{{ snapping ? '寫入中…' : '立即寫一份快照' }}</button>
        </div>
        <V2Table v-if="!cat" :rows="catRows" prefix="C" :cols="[{ k: 'label', label: '分類' }, { k: 'n', label: '筆數', mono: true, text: (r: any) => r.n_text, link: (r: any) => r.n ? `/v2/changes?tab=daily&cat=${r.key}${ch?.date ? '&date=' + ch.date : ''}` : null }]" />
        <template v-else>
          <div class="bar"><button type="button" class="lb" @click="router.replace({ query: { ...route.query, cat: undefined } })">‹ 全部分類</button><b>{{ catLabel }}</b><span class="muted">{{ itemRows.length }} 筆</span></div>
          <V2Table :rows="itemRows" prefix="D" empty="此分類沒有異動"
                   :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip_text', label: 'IP', mono: true }, { k: 'before_text', label: '異動前' }, { k: 'after_text', label: '異動後' }, { k: 'detail', label: '說明' }]" />
        </template>
      </template>
      <template v-else-if="tab === 'week'">
        <p v-if="weekErr" class="err">{{ weekErr }}</p>
        <p v-if="week?.freshness?.reason" class="stale">{{ week.freshness.reason }}</p>
        <V2Table v-if="week" :rows="weekRows" prefix="W" empty="無資料"
                 :cols="[{ k: 'date', label: '日期', mono: true, link: (r: any) => r.link }, { k: 'n', label: '異動筆數', mono: true, text: (r: any) => r.n_text, link: (r: any) => r.link }, { k: 'status', label: '比對' }, { k: 'detail', label: '分類' }]" />
        <p v-else-if="!weekErr" class="muted">載入中…</p>
        <InfoNote>最近 7 天每天的異動筆數＝「每日異動」同一份比對（點日期看明細）。那天沒有快照寫「無資料（未收集）」；兩份快照之間掃描、收集、匯入都沒有新資料又比出 0 筆，寫「無資料（資料來源未更新）」，不當成沒有異動。</InfoNote>
      </template>
      <template v-else>
        <p v-if="issueErr" class="err">{{ issueErr }}</p>
        <div class="bar">
          <button type="button" class="chip" :class="{ on: issueFilter === 'open' }" @click="issueFilter = 'open'">未處理 {{ issueUnknown ? '—' : openCount }}</button>
          <button type="button" class="chip" :class="{ on: issueFilter === 'all' }" @click="issueFilter = 'all'">全部 {{ issueUnknown ? '—' : issues.length }}</button>
          <span class="sep" />
          <button v-for="t in ['', ...typeList]" :key="t" type="button" class="chip" :class="{ on: issueType === t }" :title="TYPE_HINT[t] || ''" @click="pickType(t)">{{ t || '全部類型' }} {{ issueUnknown ? '—' : typeCount(t) }}</button>
          <button v-if="!isViewer" type="button" class="btn" :disabled="!pickedIds.length" @click="markRead">標記已處理（{{ pickedIds.length }}）</button>
        </div>
        <p v-if="issueType && TYPE_HINT[issueType]" class="muted">{{ TYPE_HINT[issueType] }}</p>
        <p v-if="issueType && issueLoaded && !typeList.includes(issueType)" class="err">網址指定的類型「{{ issueType }}」不在目前資料中；下表 0 筆是這個篩選造成的，不代表沒有異常，請改選「全部類型」。</p>
        <V2Table v-model:selected="picked" :rows="issueRows" prefix="E" empty="沒有掃描異常" selectable row-key="id" :can-select="(r: any) => !r.is_read" :cols="[{ k: 'detected_at', label: '偵測時間', mono: true }, { k: 'name', label: '主機名稱', link: (r: any) => (r.scan ? null : `/v2/machine/${encodeURIComponent(r.ip || r.hostname || '')}`) }, { k: 'ip', label: 'IP', mono: true }, { k: 'issue_type', label: '類型' }, { k: 'note', label: '說明', link: (r: any) => (r.scan ? '/v2/onboard?tab=gaps' : null) }, { k: 'state', label: '處理狀態' }, ...(isViewer ? [] : [{ k: 'act', label: '操作', text: (r: any) => (r.is_read ? '' : '標已處理') }])]">
          <template #cell-act="{ row }"><button v-if="!isViewer && !row.is_read" type="button" class="lb" @click="markOne(row)">標已處理</button></template>
        </V2Table>
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
.sep { width: 1px; height: 18px; background: var(--border); }
.mono { font-family: var(--disp); }
.err { color: var(--bad); }
.stale { padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); }
</style>
