<script setup lang="ts">
// 7.5 憑證盤點（原 9.3 憑證管理，使用者 11-01：憑證管理算盤點）。第二批（2026-11-01）：從「名冊」變「工作台」——
// 找出要換的憑證（條件過濾，可組合、網址可分享）→ 分派／排程（更換進度、預排日期，可批次）→ 追蹤（批次紀錄可整批復原）→ 登錄新到期日（詳情頁）。
// 只做管理與查詢：沒有探測、沒有更換動作、不碰私鑰；更換一律人工，「更換進度」只是登記，不驅動任何自動動作。
// 頁籤：憑證清單｜批次紀錄｜設定（管理員：風險等級門檻、掛載型態、含人名匯出）。匯入統一走「資料匯入」頁（這裡只顯示資料來源與最後匯入時間）。
// 資料：/api/certs/*（certs.py）。清單是伺服器端篩選／排序／分頁；人名：清單完全不含；詳情對非管理員遮罩。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋）
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const cfg = useRuntimeConfig()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
if (route.query.tab === 'import') await navigateTo('/v2/settings?st=import')    // 舊書籤：匯入已搬到「資料匯入」頁

interface Card { key: string; label: string; count: number }
interface ImportInfo { import_items: number; manual_items: number; last_import_at: string | null; last_import_local: string | null; last_import_file: string | null }
interface Summary { cards: Card[]; total_active: number; inactive: number; thresholds: number[]; now_utc: string; ready: boolean; can_see_contacts: boolean; import_info: ImportInfo }
interface Opts {
  mount_types: { name: string; active: boolean; builtin: boolean; in_use: number }[]; replace_statuses: string[]; environments: string[]
  deactivate_reasons: { code: string; label: string }[]; units: string[]; batch_max: number; search_fields: string[]
}
const FALLBACK_SEARCH = ['憑證名稱', '端點', 'SNI', '申請單位', 'F5 BIG-IP', '主機 IP', 'SAN', '序號']
const summary = ref<Summary | null>(null)
const opts = ref<Opts | null>(null)
const items = ref<any[]>([])
const matched = ref(0)
const total = ref(0)
const err = ref('')
const loading = ref(false)
const isAdmin = computed(() => !!summary.value?.can_see_contacts)
const batchMax = computed(() => opts.value?.batch_max ?? 500)

// ---- 網址即篩選條件 ----
const fp = computed(() => certFilterParams(route.query))
const sortKey = computed(() => String(route.query.sort || 'seq'))
const sortDir = computed<'asc' | 'desc'>(() => (route.query.desc === '1' ? 'desc' : 'asc'))
const page = computed(() => Number(route.query.page) || 1)
const size = computed(() => Number(route.query.size) || 100)
const listQuery = (extra: Record<string, any> = {}) => ({ ...fp.value, sort: certSortKey(sortKey.value), desc: sortDir.value === 'desc', page: page.value, size: size.value, ...extra })
async function loadSummary() {
  const [s, o] = await Promise.all([apiFetch<Summary>('/api/certs/summary'), apiFetch<Opts>('/api/certs/options')])
  summary.value = s; opts.value = o
}
async function loadList() {
  loading.value = true
  try {
    const l = await apiFetch<{ items: any[]; matched: number; total: number }>('/api/certs/items', { query: listQuery() })
    items.value = l.items; matched.value = l.matched; total.value = l.total
    err.value = ''
  } catch (e: any) { err.value = `憑證清單載入失敗：${errMsg(e)}` } finally { loading.value = false }
}
// 兩個請求同時發出（Nuxt 的 composable 只能在 setup 同步段取得 instance：第一個 await 之後才呼叫 apiFetch 會報 "called outside of a plugin"）
async function load() {
  await Promise.all([loadSummary().catch((e: any) => { err.value = `憑證清單載入失敗：${errMsg(e)}` }), loadList()])
}
watch(() => JSON.stringify([fp.value, route.query.sort, route.query.desc, route.query.page, route.query.size]), loadList)
watch(() => JSON.stringify(fp.value), () => { selected.value = {} })

function setQ(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch, page: undefined } }) }
const setF = (k: string, v: string) => setQ({ [k]: v || undefined })
function clearKeys(keys: string[]) { setQ(Object.fromEntries(keys.map(k => [k, undefined]))) }
function clearAll() { setQ(Object.fromEntries(CERT_FILTER_KEYS.map(k => [k, undefined]))); q.value = '' }
function pick(k: string) { setF('status', k && k !== fp.value.status ? k : '') }
function onSort(k: string) {
  const key = certSortKey(k)
  const desc = key === certSortKey(sortKey.value) && sortDir.value === 'asc'
  router.replace({ query: { ...route.query, sort: key === 'seq' ? undefined : key, desc: desc ? '1' : undefined, page: undefined } })
}
function onPage(p: number, s: number) { router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined, size: s !== 100 ? String(s) : undefined } }) }
const server = computed(() => ({ total: matched.value, page: page.value, size: size.value, sortKey: certSortKey(sortKey.value) === 'seq' ? '__no' : certSortKey(sortKey.value), sortDir: sortDir.value }))

const q = ref(String(route.query.q || ''))
function search() { setF('q', q.value.trim()) }
const chips = computed(() => certFilterChips(route.query))
const nFilters = computed(() => chips.value.length)     // 以標籤數計（到期區間的起迄是同一個條件）
const duePreset = computed(() => certDuePresetOn(route.query))
const PRESET_LABEL: Record<CertDuePreset, string> = { d7: '7 天內', this_week: '本週', next_week: '下週', this_month: '本月', next_month: '下個月', d30: '30 天內', d60: '60 天內', d90: '90 天內' }
function setPreset(k: CertDuePreset) {
  if (duePreset.value === k) { clearKeys(['within', 'due_from', 'due_to']); return }
  setQ({ within: undefined, due_from: undefined, due_to: undefined, status: undefined, ...certDuePreset(k) })
}
const moreOpen = ref(['announce', 'has_expiry', 'plan_from', 'plan_to', 'has_plan', 'pending'].some(k => fp.value[k]))
const searchHint = computed(() => `搜尋：${(opts.value?.search_fields ?? FALLBACK_SEARCH).join('、')}`)
const scopeText = computed(() => ({ on: '啟用中', off: '已停用', all: '全部（含停用）' } as Record<string, string>)[fp.value.active || 'on'])
const activeMounts = computed(() => (opts.value?.mount_types ?? []).filter(m => m.active))

const TABS = computed(() => [
  { k: 'list', label: '憑證清單' },
  { k: 'batches', label: '批次紀錄' },
  ...(isAdmin.value ? [{ k: 'settings', label: '設定' }] : []),
])
const tab = computed({ get: () => (TABS.value.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'list'), set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })

const rows = computed(() => items.value.map(r => ({ ...r, __no: r.seq, ep: `${r.endpoint || '—'}:${r.port}`, status_rank: certStatusRank(r.status),
  src: r.source_label, act: r.active ? '' : `已停用（${r.deactivate_reason_label || '未記錄原因'}）` })))
const exportAllRows = async () => (await apiFetch<{ items: any[] }>('/api/certs/items', { query: listQuery({ page: 1, size: 0 }) })).items
  .map(r => ({ ...r, __no: r.seq, ep: `${r.endpoint || '—'}:${r.port}`, status_rank: certStatusRank(r.status), src: r.source_label,
    act: r.active ? '' : `已停用（${r.deactivate_reason_label || '未記錄原因'}）` }))
const envTone = (r: any) => (r.environment === '正式' ? 'prod' : r.environment === '測試' ? 'test' : 'none')
const detailLink = (r: any) => certDetailLink(r.id, route.query)
const cols = computed(() => [
  { k: 'name', label: '憑證名稱', min: 200, link: detailLink },
  { k: 'environment', label: '環境', tone: envTone },
  { k: 'ep', label: '端點:埠', min: 110, mono: true },
  { k: 'sni', label: 'SNI', min: 190, mono: true },
  { k: 'mount_type', label: '掛載' },
  { k: 'req_unit', label: '申請單位' },
  { k: 'not_after_local', label: '到期時間', mono: true },
  { k: 'remain_hours', label: '剩餘', min: 100, text: (r: any) => r.remain_text },
  { k: 'risk', label: '風險等級' },
  { k: 'status_rank', label: '到期分段', min: 100, text: (r: any) => r.status_label },
  { k: 'planned_date', label: '預排日期（僅登記）', mono: true },
  { k: 'announce_needed', label: '需公告' },
  { k: 'replace_status', label: '更換進度（僅登記）' },
  { k: 'src', label: '來源' },
  { k: 'data_updated', label: '資料最後更新', mono: true, text: (r: any) => r.data_updated_local },
  { k: 'probe_at', label: '最後探測', mono: true, text: (r: any) => r.probe_at_local },
  { k: 'probe_diff', label: '探測比對', min: 120, text: probeText, tone: (r: any) => (r.probe_differs ? 'test' : '') },
  ...(isViewer.value ? [] : [{ k: 'ops', label: '動作' }]),
  ...(fp.value.active && fp.value.active !== 'on' ? [{ k: 'act', label: '停用' }] : []),     // 預設只看啟用中，這欄一定是空的，不顯示
])
// 清單上直接改更換進度（下拉）；只存、不驅動任何動作
const dlg = ref<{ mode: 'replaced' | 'schedule'; id: string; name: string } | null>(null)
async function dlgDone() { dlg.value = null; await load(); await loadBatches() }
async function saveRs(r: any, v: string) {
  if (v === '已更換') { dlg.value = { mode: 'replaced', id: r.id, name: r.name }; await loadList(); return }      // 已更換要一起登錄新到期日（或稍後補登）
  if (v === '已排程' && !r.planned_date) { dlg.value = { mode: 'schedule', id: r.id, name: r.name }; await loadList(); return }    // 已排程要有預排日期
  try {
    await guardedFetch(`/api/certs/items/${r.id}`, { method: 'PUT', body: { replace_status: v } })
    r.replace_status = v; showToast(`已改為「${v || '未設定'}」`, 'success')
    await loadList()
  } catch (e: any) { showToast(`更換進度沒存成功：${errMsg(e)}`, 'error'); await loadList() }
}

// ---- 勾選／批次 ----
const selected = ref<Record<string, boolean>>({})
const selIds = computed(() => Object.entries(selected.value).filter(([, v]) => v).map(([k]) => k))
const showBatch = ref(false)
const selecting = ref(false)
async function selectAllMatched() {
  if (matched.value > batchMax.value) { showToast(`符合 ${matched.value} 筆，超過一次上限 ${batchMax.value} 筆；請再縮小條件`, 'warn'); return }
  selecting.value = true
  try {
    const l = await apiFetch<{ items: any[] }>('/api/certs/items', { query: listQuery({ page: 1, size: 0 }) })
    selected.value = Object.fromEntries(l.items.map(r => [r.id, true]))
  } catch (e: any) { showToast(`全選失敗：${errMsg(e)}`, 'error') } finally { selecting.value = false }
}
async function batchDone() { showBatch.value = false; selected.value = {}; await load(); await loadBatches() }

// 「匯出所選」：只匯出勾選的列（跟表格右上「匯出」＝目前篩選結果全部頁 區分）；去識別、欄位與畫面表格相同
const { exportTable } = useTableExport()
const exportName = useV2ExportName()
const exportingSel = ref(false)
async function exportSelected() {
  exportingSel.value = true
  try {
    const want = new Set(selIds.value)
    const all = await exportAllRows()
    const list = all.filter(r => want.has(r.id))
    const cs = cols.value.filter(c => c.k !== 'ops')
    const head = ['項次', ...cs.map(c => c.label)]
    const body = list.map(r => [`C${r.__no}`, ...cs.map((c: any) => { const v = c.text ? c.text(r) : r[c.k]; return v === null || v === undefined ? '' : v })])
    await exportTable(`${exportName()}_所選${list.length}筆`, head, body)
  } catch (e: any) { showToast(`匯出失敗：${errMsg(e)}`, 'error') } finally { exportingSel.value = false }
}

// ---- 手動新增 ----
const adding = ref(false)
const busy = ref(false)
const blank = () => ({ name: '', environment: '', endpoint: '', port: 443, sni: '', mount_type: '', req_unit: '', req_contact: '', ap_contact: '', infra_owner: '', ap_tester: '',
  planned_text: '', planned_date: '', announce_needed: '', note: '', not_after: '', issuer_dn: '' })
const form = reactive(blank())
async function create() {
  if (!form.name.trim()) { showToast('憑證名稱必填', 'warn'); return }
  busy.value = true
  try {
    const { not_after, issuer_dn, ...body } = form
    const r = await guardedFetch<{ id: string }>('/api/certs/items', { method: 'POST', body })
    let noExpiry = !not_after
    if (not_after) {
      try { await guardedFetch(`/api/certs/items/${r.id}/observations`, { method: 'POST', body: { not_after: certLocalToApi(not_after), issuer_dn } }) } catch (e: any) { noExpiry = true; showToast(`項目已建立，但到期時間沒存成功：${errMsg(e)}`, 'error') }
    }
    if (noExpiry) showToast('已新增。此筆尚無到期日，不會出現在到期風險卡片，請補登（到「無到期資料」清單找它）。', 'warn', 9000)
    else showToast('已新增', 'success')
    Object.assign(form, blank()); adding.value = false
    await load()
  } catch (e: any) { showToast(`新增失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}

// ---- 批次紀錄 ----
const batches = ref<any[]>([])
const conflicts = ref<{ id: string; seq: number | null; name: string; fields: string[] }[]>([])
const conflictOpen = ref(false)
const conflictSkipped = ref<string[]>([])
const conflictRows = computed(() => conflicts.value.map((c, i) => ({ ...c, __no: c.seq ?? i + 1, fields_text: c.fields.map(f => CERT_FIELD_LABEL[f] ?? f).join('、'), name_text: c.name })))
async function loadBatches() {
  try { batches.value = (await apiFetch<{ batches: any[] }>('/api/certs/batches')).batches } catch (e: any) { err.value = `批次紀錄載入失敗：${errMsg(e)}` }
}
const STATE_LABEL: Record<string, string> = { active: '已套用', partial: '部分復原', undone: '已復原' }
const batchRows = computed(() => batches.value.map((b, i) => ({ ...b, __no: batches.value.length - i, state_label: STATE_LABEL[b.state] ?? b.state, fields_text: (b.fields || []).join('、'), seqs_text: (b.item_seqs || []).map((n: number) => `C${n}`).join('、') })))
async function undo(b: any) {
  if (!window.confirm(`整批復原批次 ${b.short}（${b.item_count} 筆：${b.fields_text}）？\n批次之後又被改過的項目會列為衝突、不會被覆蓋。`)) return
  busy.value = true
  try {
    const r = await guardedFetch<{ restored: number; conflicts: any[]; skipped_fields?: string[] }>(`/api/certs/batch/${b.id}/undo`, { method: 'POST' })
    conflicts.value = r.conflicts
    conflictSkipped.value = r.skipped_fields ?? []
    conflictOpen.value = !!(r.conflicts.length || r.skipped_fields?.length)
    showToast(`已復原 ${r.restored} 筆${r.conflicts.length ? `；${r.conflicts.length} 筆在批次後被改過，沒有覆蓋` : ''}${r.skipped_fields?.length ? `；「${r.skipped_fields.join('、')}」是人名欄位，只有管理員能復原，已略過` : ''}`, r.conflicts.length || r.skipped_fields?.length ? 'warn' : 'success', 8000)
    await load(); await loadBatches()
  } catch (e: any) { showToast(`復原失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}

// ---- 設定（管理員）----
const th = reactive<number[]>([...(summary.value?.thresholds ?? [45, 30, 14])])
watch(() => summary.value?.thresholds, (v) => { if (v) th.splice(0, 3, ...v) })
async function saveTh() {
  busy.value = true
  try {
    await guardedFetch('/api/certs/settings', { method: 'PUT', body: { thresholds: th.map(Number) } })
    showToast('已儲存風險等級門檻', 'success'); await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
const newMount = ref('')
async function mountCall(path: string, body: Record<string, any>, ok: string) {
  busy.value = true
  try {
    const r = await guardedFetch<{ mount_types: Opts['mount_types'] }>(`/api/certs/mount-types/${path}`, { method: 'POST', body })
    if (opts.value) opts.value.mount_types = r.mount_types
    showToast(ok, 'success'); newMount.value = ''
  } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
const addMount = () => (newMount.value.trim() ? mountCall('add', { name: newMount.value.trim() }, `已新增型態「${newMount.value.trim()}」`) : showToast('請輸入型態名稱', 'warn'))
const toggleMount = (m: any) => mountCall('set-active', { name: m.name, active: !m.active }, m.active ? `已停用「${m.name}」` : `已啟用「${m.name}」`)
const removeMount = (m: any) => (window.confirm(`刪除型態「${m.name}」？（沒有項目使用才刪得掉）`) ? mountCall('remove', { name: m.name }, `已刪除「${m.name}」`) : undefined)
const mountRows = computed(() => (opts.value?.mount_types ?? []).map((m, i) => ({ ...m, __no: i + 1, state: m.active ? '啟用' : '停用', kind: m.builtin ? '內建' : '自訂' })))
// 中繼探測（2.042.02）：探測值只比對、不覆蓋人工到期日
function probeText(r: any) {
  if (!r.probe_diff) return '未探測'
  if (r.probe_diff === 'differs' || r.probe_diff === 'probe_only') return `${r.probe_diff_label}（探測 ${r.probe_not_after_local ?? '—'}）`
  if (r.probe_diff === 'failed') return `探測失敗：${r.probe_error || '—'}`
  return r.probe_diff_label
}
async function exportProbeList() {
  busy.value = true
  try {
    const res = await fetch(`${cfg.public.apiBase}/api/certs/probe-list.txt`, { credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const n = res.headers.get('X-Probe-Lines') ?? '?'
    const sk = (res.headers.get('X-Probe-Skipped') || '').split(',').filter(Boolean)
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a'); a.href = url; a.download = 'cert_probe_list.txt'; a.click()
    URL.revokeObjectURL(url)
    showToast(`已匯出探測清單 ${n} 行${sk.length ? `；略過 ${sk.length} 筆（端點不是主機名或 IP：${sk.slice(0, 10).join('、')}）` : ''}`, 'success')
  } catch (e: any) { showToast(`匯出探測清單失敗：${e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
const purposeCode = ref('')
const otherNote = ref('')
async function exportIdentified() {
  if (!purposeCode.value) { showToast('請先選匯出用途', 'warn'); return }
  if (purposeCode.value === 'other' && otherNote.value.trim().length < 4) { showToast('選「其他」請另外填說明（至少 4 個字）', 'warn'); return }
  if (!window.confirm(`將匯出符合目前篩選的 ${matched.value} 筆（含人名）。會寫入稽核紀錄（誰、何時、用途分類、列數）。確定匯出？`)) return
  busy.value = true
  try {
    const res = await fetch(`${cfg.public.apiBase}/api/certs/export-identified`, { method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders('POST') }, body: JSON.stringify({ purpose_code: purposeCode.value, other_note: otherNote.value.trim(), filter: fp.value }) })
    if (!res.ok) throw new Error(await httpReason(res))
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a'); a.href = url; a.download = `憑證管理_含人名_${new Date().toISOString().slice(0, 10)}.xlsx`; a.click()
    URL.revokeObjectURL(url)
    showToast('已匯出並寫入稽核紀錄', 'success'); purposeCode.value = ''; otherNote.value = ''
  } catch (e: any) { showToast(`匯出失敗：${e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
const isEmptyAll = computed(() => !!summary.value?.ready && summary.value.total_active + summary.value.inactive === 0)
// 初次載入：請求同時在 setup 同步段發出（放在最後，所有 const 都已初始化，不會踩到暫時性死區）
await Promise.all([load(), loadBatches()])
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">7.5 憑證盤點</h1></template>
    <template #conclusion>
      <span class="warn">本系統不寄送通知、不建立待辦、不保證即時探測；請依既有作業追蹤更換。</span>
      <InfoNote>範圍是 CA 簽發的 TLS 憑證（網站／F5／IIS／Nginx／APP），做清單、到期查詢與更換進度登記；到期時間由人工登錄或「資料匯入」頁的憑證調查表提供；另可由公司中繼 PC 定期做只讀 TLS 握手探測（本系統只讀它輸出的檔案、不自己連線），探測結果只列在「最後探測」「探測比對」兩欄，不會覆蓋人工值。更換一律人工，系統永遠不接觸私鑰；「更換進度」只是登記，不會觸發任何動作。到期時間存 UTC、畫面顯示 UTC+8；狀態以「距到期瞬間的剩餘時間」判斷，不是日期相減。「風險等級」依門檻（預設 45／30／14 天）只在畫面標示，不會通知任何人。上方卡片是全部啟用中項目的統計，不受下方篩選影響；點卡片會把該分段加進篩選。</InfoNote>
    </template>
    <template v-if="!isViewer" #actions>
      <button type="button" class="pbtn" @click="adding = !adding; tab = 'list'">{{ adding ? '取消新增' : '＋ 新增憑證項目' }}</button>
    </template>

    <div v-if="summary" class="src">
      資料來源：憑證調查表匯入 <b>{{ summary.import_info.import_items }}</b> 筆・手動新增 <b>{{ summary.import_info.manual_items }}</b> 筆・最後匯入
      <b>{{ summary.import_info.last_import_local || '尚未匯入' }}</b><template v-if="summary.import_info.last_import_file">（{{ summary.import_info.last_import_file }}）</template>
      <NuxtLink to="/v2/settings?st=import" class="lnk">到資料匯入 ›</NuxtLink>
    </div>
    <div v-if="summary && !isAdmin" class="mask-line">窗口資料已遮罩，請洽管理員</div>

    <div v-if="summary" class="cards">
      <button v-for="c in summary.cards" :key="c.key" type="button" class="card" :class="[certStatusTone(c.key), { on: fp.status === c.key, todo: c.key === 'none' }]" :title="`點一下列出這 ${c.count} 筆`" @click="pick(c.key)">
        <span class="n">{{ c.count }}</span><span class="l">{{ c.label }}</span><span v-if="c.key === 'none'" class="todo-l">待補登到期日</span>
      </button>
      <button type="button" class="card all" :class="{ on: !fp.status }" title="取消到期分段篩選" @click="pick('')">
        <span class="n">{{ summary.total_active }}</span><span class="l">全部（啟用中）</span>
      </button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <template #tab>
      <template v-if="tab === 'list'">
        <div v-if="isEmptyAll" class="empty">
          <b>還沒有任何憑證項目。</b>下一步：
          <button v-if="!isViewer" type="button" class="pbtn on" @click="adding = true">＋ 新增憑證項目</button>
          <NuxtLink to="/v2/settings?st=import" class="pbtn on">到資料匯入（匯入憑證調查表）</NuxtLink>
        </div>
        <form v-if="adding && !isViewer" class="frm" @submit.prevent="create">
          <h3 class="frm-t">新增憑證項目<span class="req-note">　標 <span class="req">*</span> 為必填，其餘可先空著、之後補登</span></h3>
          <label>憑證名稱 <span class="req">*</span><input v-model="form.name" required></label>
          <label>環境<select v-model="form.environment"><option value="">（未填）</option><option v-for="e in (opts?.environments ?? ['正式', '測試'])" :key="e">{{ e }}</option></select></label>
          <label>端點（F5 VIP 或主機位址）<input v-model="form.endpoint"></label>
          <label>埠<input v-model.number="form.port" type="number" min="1" max="65535"></label>
          <label>SNI 主機名<input v-model="form.sni"></label>
          <label>掛載型態<select v-model="form.mount_type"><option value="">（未填）</option><option v-for="m in activeMounts" :key="m.name">{{ m.name }}</option></select></label>
          <label>申請單位<input v-model="form.req_unit"></label>
          <label>預排日期<span class="hint">僅登記，不會通知任何人</span><input v-model="form.planned_date" type="date"></label>
          <label>需公告<select v-model="form.announce_needed"><option value="">（未填）</option><option value="是">是</option><option value="否">否</option></select></label>
          <template v-if="isAdmin">
            <label>申請窗口<input v-model="form.req_contact"></label>
            <label>AP 窗口<input v-model="form.ap_contact"></label>
            <label>Infra 執行人員<input v-model="form.infra_owner"></label>
            <label>AP 驗測人員<input v-model="form.ap_tester"></label>
          </template>
          <label>到期時間（UTC+8，可先不填）<input v-model="form.not_after" type="datetime-local"></label>
          <label>簽發者（選填）<input v-model="form.issuer_dn"></label>
          <label class="wide">備註<input v-model="form.note"></label>
          <div class="wide"><button type="submit" class="pbtn on" :disabled="busy">{{ busy ? '儲存中…' : '新增' }}</button><InfoNote>唯一條件是「環境＋端點＋埠＋SNI＋掛載型態」，不是憑證名稱；同名的正式／測試、同一個 VIP 不同 SNI 都可以並存。沒填到期時間也能新增，但這筆不會出現在到期風險卡片，會算在「無到期資料」待補清單。人名欄位只有管理員能填。掛載型態的選項由管理員在「設定」頁籤維護。</InfoNote></div>
        </form>

        <template v-if="!isEmptyAll">
        <!-- 條件過濾：全部放網址（可分享、重新整理不掉、從詳情頁返回仍在） -->
        <div class="fbar">
          <input v-model="q" class="srch" :placeholder="searchHint" @keyup.enter="search">
          <button type="button" class="pbtn" @click="search">搜尋</button>
          <span class="seg-l">到期：</span>
          <button v-for="p in CERT_DUE_PRESETS" :key="p" type="button" class="chip btn" :class="{ on: duePreset === p }" @click="setPreset(p)">{{ PRESET_LABEL[p] }}</button>
          <label class="dt">自訂 <input type="date" :value="fp.due_from || ''" @change="setQ({ within: undefined, due_from: ($event.target as HTMLInputElement).value || undefined })"> ～ <input type="date" :value="fp.due_to || ''" @change="setQ({ within: undefined, due_to: ($event.target as HTMLInputElement).value || undefined })"></label>
        </div>
        <div class="fbar">
          <label class="f">環境<select :value="fp.env || ''" @change="setF('env', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option v-for="e in (opts?.environments ?? [])" :key="e">{{ e }}</option><option value="_none">（未填）</option></select></label>
          <label class="f">掛載型態<select :value="fp.mount || ''" @change="setF('mount', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option v-for="m in (opts?.mount_types ?? [])" :key="m.name" :value="m.name">{{ m.name }}{{ m.active ? '' : '（已停用）' }}</option><option value="_none">（未填）</option></select></label>
          <label class="f">申請單位<select :value="fp.unit || ''" @change="setF('unit', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option v-for="u in (opts?.units ?? [])" :key="u">{{ u }}</option><option value="_none">（未填）</option></select></label>
          <label class="f">風險等級<select :value="fp.risk || ''" @change="setF('risk', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option value="high">高</option><option value="mid">中</option><option value="low">低</option><option value="expired">已過期</option><option value="pending">待補到期日</option><option value="none">無</option></select></label>
          <label class="f">到期分段<select :value="fp.status || ''" @change="setF('status', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option v-for="c in (summary?.cards ?? [])" :key="c.key" :value="c.key">{{ c.label }}</option></select></label>
          <label class="f">更換進度<select :value="fp.rs || ''" @change="setF('rs', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option v-for="s in (opts?.replace_statuses ?? [])" :key="s">{{ s }}</option><option value="_none">（未設定）</option></select></label>
          <label class="f">是否停用<select :value="fp.active || 'on'" @change="setF('active', ($event.target as HTMLSelectElement).value === 'on' ? '' : ($event.target as HTMLSelectElement).value)"><option value="on">啟用中</option><option value="off">已停用</option><option value="all">全部</option></select></label>
          <button type="button" class="pbtn" @click="moreOpen = !moreOpen">{{ moreOpen ? '收起更多條件' : '更多條件' }}</button>
        </div>
        <div v-if="moreOpen" class="fbar">
          <label class="f">需公告<select :value="fp.announce || ''" @change="setF('announce', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option value="yes">需公告</option><option value="no">不需公告</option><option value="blank">未填</option></select></label>
          <label class="f">到期日<select :value="fp.has_expiry || ''" @change="setF('has_expiry', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option value="1">有到期日</option><option value="0">無到期日</option></select></label>
          <label class="f">待補新到期日<select :value="fp.pending || ''" @change="setF('pending', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option value="1">已更換待補到期日</option><option value="0">不是</option></select></label>
          <label class="f">預排日期<select :value="fp.has_plan || ''" @change="setF('has_plan', ($event.target as HTMLSelectElement).value)"><option value="">全部</option><option value="1">已排</option><option value="0">未排</option></select></label>
          <label class="dt">預排區間 <input type="date" :value="fp.plan_from || ''" @change="setF('plan_from', ($event.target as HTMLInputElement).value)"> ～ <input type="date" :value="fp.plan_to || ''" @change="setF('plan_to', ($event.target as HTMLInputElement).value)"></label>
        </div>
        <div class="fbar">
          <b class="cnt">符合 {{ matched.toLocaleString() }} 筆／共 {{ total.toLocaleString() }} 筆（{{ scopeText }}）</b>
          <span v-if="loading" class="muted">載入中…</span>
          <span v-for="c in chips" :key="c.text" class="chip">{{ c.text }} <button type="button" class="x" :title="`移除這個條件`" @click="clearKeys(c.key)">✕</button></span>
          <button v-if="nFilters" type="button" class="pbtn" @click="clearAll">清除篩選（{{ nFilters }}）</button>
          <InfoNote>欄位定義：風險等級＝依設定頁的門檻（預設 45／30／14 天）把剩餘時間標成 高／中／低，已過期單獨標「已過期」，沒有到期日不標；已更換但選了「稍後補登」的項目標「待補到期日」（舊到期日不再拿來判斷，歸在「無到期資料」，可用「待補新到期日」篩選找出來）。狀態＝依剩餘時間分段（已過期、7 天內、8–30 天、31–60 天、正常、無到期資料），上方卡片與下鑽用同一份計算。更換進度：待排程＝還沒安排時間；已排程＝已排定更換時間；已更換＝已換上新憑證（記得到詳情登錄新到期日）；已驗測＝換完並驗測通過。可以回頭改（含改回前一個），有編輯權限的帳號都能改，只是登記、不觸發任何動作。預排日期與更換進度要一致：已排程必須有預排日期，有預排日期不能停在待排程；清單每列的「標記已更換」「排定更換」會一次把相關欄位設好。</InfoNote>
          <InfoNote>篩選條件可以任意組合，全部記在網址裡：複製網址給同事就是同一份清單，從詳情頁返回也會回到同樣的篩選與排序。「N 天內」是尚未過期且剩餘不超過 N 天；到期區間以 UTC+8 日期計、含起訖兩天。搜尋框可找{{ (opts?.search_fields ?? FALLBACK_SEARCH).join('、') }}（序號與 SAN 含歷史觀測，已作廢的不算）。備註、預排更換時間、申請單位是自由文字，會出現在清單與去識別匯出，請勿填人名。清單不含任何人名欄；表格右上「匯出」匯出的是<b>目前篩選結果</b>（全部頁），且去識別；唯讀帳號沒有匯出鈕。要看或匯出窗口／人員，進單筆詳情（人名遮罩，管理員看完整）或到「設定」頁籤做含人名匯出（需填用途、會寫稽核）。項次 C 開頭、固定不變。「設定」頁籤（風險等級門檻、掛載型態、含人名匯出）只有管理員看得到。</InfoNote>
        </div>
        <div v-if="!loading && !matched && nFilters" class="warnbox">
          <b>目前條件下沒有符合的憑證。</b>套用中的條件：{{ chips.map(c => c.text).join('；') }}
          <button type="button" class="pbtn" @click="clearAll">一鍵清除篩選</button>
        </div>
        <div v-if="!isViewer" class="fbar">
          <span class="muted">已選 {{ selIds.length }} 筆</span>
          <button type="button" class="pbtn" :disabled="selecting || !matched" @click="selectAllMatched">{{ selecting ? '選取中…' : `全選目前篩選結果（${matched} 筆）` }}</button>
          <button type="button" class="pbtn on" :disabled="!selIds.length || selIds.length > batchMax" @click="showBatch = true">批次更新（{{ selIds.length }} 筆）</button>
          <button v-if="selIds.length" type="button" class="pbtn" :disabled="exportingSel" @click="exportSelected">{{ exportingSel ? '匯出中…' : `匯出所選（${selIds.length} 筆）` }}</button>
          <button v-if="selIds.length" type="button" class="pbtn" @click="selected = {}">清除選取</button>
          <span v-if="matched > batchMax" class="muted">符合筆數超過一次上限 {{ batchMax }} 筆，要全選請再縮小條件</span>
          <span v-else-if="selIds.length > batchMax" class="warn">一次最多 {{ batchMax }} 筆</span>
        </div>
        <div v-else class="fbar"><span class="muted">您的帳號是唯讀：只能查詢，不能編輯、批次更新、新增或匯出。</span></div>
        <V2Table v-model:selected="selected" :rows="rows" :cols="cols" prefix="C" :selectable="!isViewer" row-key="id" :server="server" unit="筆"
                 :export-all="exportAllRows" empty="沒有符合的憑證項目（可放寬條件，或按「清除篩選」）" @sort="onSort" @page="onPage">
          <template #cell-ops="{ row }">
            <template v-if="row.active">
              <button type="button" class="pbtn" @click="dlg = { mode: 'replaced', id: row.id, name: row.name }">標記已更換</button>
              <button type="button" class="pbtn" @click="dlg = { mode: 'schedule', id: row.id, name: row.name }">排定更換</button>
            </template>
            <span v-else>—</span>
          </template>
          <template #cell-replace_status="{ row }">
            <select v-if="!isViewer && row.active" :value="row.replace_status" class="sel" @change="saveRs(row, ($event.target as HTMLSelectElement).value)">
              <option value="">（未設定）</option><option v-for="s in (opts?.replace_statuses ?? [])" :key="s">{{ s }}</option>
            </select>
            <span v-else>{{ row.replace_status || '—' }}</span>
          </template>
        </V2Table>
        </template>
      </template>

      <template v-else-if="tab === 'batches'">
        <p class="note">批次更新的每一次操作都在這裡：誰、何時、改了幾筆、改了什麼。
          <InfoNote>每個批次是單一交易，要嘛全部套用、要嘛全部沒有。「復原」會把這批改過的欄位還原成批次前的值；若某個項目在批次之後又被改過（任何人、任何方式），會列為衝突、保持現狀，不會被蓋掉。已全部復原的批次不能再復原。摘要不含人名（執行人欄位只記有改，不記內容）。</InfoNote></p>
        <V2Table :rows="batchRows" prefix="B" empty="還沒有批次更新紀錄（在「憑證清單」勾選項目後按「批次更新」）"
                 :cols="[{ k: 'short', label: '批次', mono: true }, { k: 'created_at', label: '時間', mono: true, text: (r: any) => r.created_local }, ...(isAdmin ? [{ k: 'created_by', label: '操作者' }] : []), { k: 'item_count', label: '筆數' }, { k: 'fields_text', label: '改的欄位' }, { k: 'summary', label: '內容', brief: (r: any) => (r.summary || '').slice(0, 24) }, { k: 'seqs_text', label: '影響項次', brief: (r: any) => (r.seqs_text || '').slice(0, 24) }, { k: 'state_label', label: '狀態' }, ...(isViewer ? [] : [{ k: 'ops', label: '動作' }])]">
          <template #cell-ops="{ row }">
            <button v-if="row.remaining > 0" type="button" class="pbtn" :disabled="busy" @click="undo(row)">{{ row.state === 'partial' ? `再試復原剩下 ${row.remaining} 筆` : '整批復原' }}</button>
            <span v-else>—</span>
          </template>
        </V2Table>
        <button v-if="conflicts.length" type="button" class="pbtn" @click="conflictOpen = true">看上次復原的衝突清單（{{ conflicts.length }} 筆）</button>
      </template>

      <template v-else-if="tab === 'settings' && isAdmin">
        <h3 class="h3">到期風險等級門檻（天）<InfoNote>剩餘時間 ≤ 門檻就在清單「風險等級」欄標出：≤ 高 → 高、≤ 中 → 中、≤ 低 → 低；已過期標「已過期」。只影響畫面標示，不寄信、不建立待辦。3 個數字都要 1～3650 天且不可重複，預設 45／30／14。</InfoNote></h3>
        <form class="inl" @submit.prevent="saveTh">
          <label>低（天）<input v-model.number="th[0]" type="number" min="1" max="3650"></label>
          <label>中（天）<input v-model.number="th[1]" type="number" min="1" max="3650"></label>
          <label>高（天）<input v-model.number="th[2]" type="number" min="1" max="3650"></label>
          <button type="submit" class="pbtn on" :disabled="busy">儲存</button>
        </form>
        <h3 class="h3">掛載型態<InfoNote>新增、編輯、篩選的「掛載型態」下拉都用這份清單。內建 5 個（F5／IIS／Nginx／APP／其他）不能刪、「其他」不能停用；自訂型態沒有項目使用時才能刪，使用中的只能停用（停用後既有項目保留原值，但不能再選）。匯入調查表遇到沒登記的型態（例如 Tomcat）時，預覽會提示並讓你選新增或歸「其他」。</InfoNote></h3>
        <form class="inl" @submit.prevent="addMount">
          <label>新增型態<input v-model="newMount" placeholder="例：Tomcat" maxlength="30"></label>
          <button type="submit" class="pbtn on" :disabled="busy">新增</button>
        </form>
        <V2Table :rows="mountRows" prefix="M" empty="沒有型態"
                 :cols="[{ k: 'name', label: '型態' }, { k: 'kind', label: '種類' }, { k: 'state', label: '狀態' }, { k: 'in_use', label: '使用中的項目數' }, { k: 'ops', label: '動作' }]">
          <template #cell-ops="{ row }">
            <button v-if="row.name !== '其他'" type="button" class="pbtn" :disabled="busy" @click="toggleMount(row)">{{ row.active ? '停用' : '啟用' }}</button>
            <button v-if="!row.builtin && !row.in_use" type="button" class="pbtn" :disabled="busy" @click="removeMount(row)">刪除</button>
          </template>
        </V2Table>
        <h3 class="h3">到期探測清單（管理員）<InfoNote>自動到期檢查由公司中繼 PC 執行（開發協作頁 D5 的 cert_collect.ps1）：它讀這份清單，對每個端點做一次只讀 TLS 握手、只讀憑證的公開資訊（到期時間、簽發者、序號、指紋、SAN），輸出檔案到中繼匯入的共用資料夾；本系統只讀檔，不會自己連任何端點，也不接觸私鑰。清單只含啟用中的項目，每行「端點:埠,SNI」；端點不是主機名或 IP 的項目會略過並提示項次。探測到的到期日不會覆蓋人工登錄的值，清單「探測比對」欄只標出不一致，由你決定要不要改。</InfoNote></h3>
        <div class="inl"><button type="button" class="pbtn" :disabled="busy" @click="exportProbeList">匯出探測清單</button></div>
        <h3 class="h3">含人名匯出（管理員）<InfoNote>一般匯出（清單表格的「匯出」）預設去識別、不含人名。這裡會把申請窗口、AP 窗口、Infra 執行人員、AP 驗測人員一起匯出：必須選用途分類（選「其他」要另填說明但不會寫入紀錄），系統只把誰、何時、用途分類代號、列數寫進操作紀錄與匯出紀錄，不記任何自由文字。</InfoNote></h3>
        <form class="inl" @submit.prevent="exportIdentified">
          <label>用途<select v-model="purposeCode"><option value="">（請選）</option><option value="audit">稽核查核</option><option value="replace">更換作業</option><option value="inventory">內部盤點</option><option value="other">其他</option></select></label>
          <label v-if="purposeCode === 'other'">說明（不寫入稽核）<input v-model="otherNote" style="width: 240px"></label>
          <button type="submit" class="pbtn" :disabled="busy">匯出含人名（{{ matched }} 筆）</button>
        </form>
        <p class="note">沿用「憑證清單」目前的篩選：{{ chips.length ? chips.map(c => c.text).join('；') : '沒有篩選條件（全部啟用中）' }}；共 <b>{{ matched }}</b> 筆。要改範圍請先回清單調整篩選。</p>
      </template>
    </template>
    <div v-if="conflictOpen" class="mask">
      <div class="box">
        <h3 class="h3">復原結果</h3>
        <p v-if="conflicts.length"><b>這 {{ conflicts.length }} 筆在批次之後又被改過，沒有覆蓋（維持現狀）。</b>可用表格右上「匯出」存下清單，逐筆處理。</p>
        <p v-if="conflictSkipped.length" class="muted">「{{ conflictSkipped.join('、') }}」是人名欄位，只有管理員能復原，已略過。</p>
        <V2Table v-if="conflicts.length" :rows="conflictRows" prefix="C" empty="沒有衝突"
                 :cols="[{ k: 'name_text', label: '憑證名稱', link: (r: any) => `/v2/certs/${r.id}` }, { k: 'fields_text', label: '涉及欄位' }]" />
        <div class="acts"><button type="button" class="pbtn on" @click="conflictOpen = false">關閉</button></div>
      </div>
    </div>
    <CertReplaceModal v-if="dlg" :mode="dlg.mode" :id="dlg.id" :name="dlg.name" @close="dlg = null" @done="dlgDone" />
    <CertBatchModal v-if="showBatch" :ids="selIds" :is-admin="isAdmin" :statuses="opts?.replace_statuses ?? []" :max="batchMax" @close="showBatch = false" @done="batchDone" />
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.warn { color: var(--warn-text); font-size: 14px; }
.err { color: var(--bad); }
.note { margin: 0 0 8px; font-size: 13px; color: var(--ink-aux); }
.muted { color: var(--muted); font-size: 13px; }
.hint { font-size: 11px; color: var(--muted); margin-left: 6px; }
.mask-line { font-size: 13px; color: var(--warn-text); }
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: flex; align-items: center; justify-content: center; z-index: 60; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 18px 20px; width: min(720px, 94vw); max-height: 92vh; overflow: auto; }
.acts { display: flex; justify-content: flex-end; margin-top: 10px; }
.h3 { margin: 14px 0 6px; font-size: 15px; font-weight: 700; }
.src { font-size: 13px; color: var(--ink-soft); display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.lnk { color: var(--link); text-decoration: none; margin-left: 6px; }
.empty { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 14px; margin-bottom: 12px; background: var(--mint); border: 1px dashed var(--brand); border-radius: var(--radius); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 12px 14px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card); cursor: pointer; font-family: inherit; text-align: left; color: var(--ink); }
.card .n { font-family: var(--disp); font-size: 28px; font-weight: 700; line-height: 1.1; }
.card .l { font-size: 13px; color: var(--ink-soft); }
.card.bad .n { color: var(--bad); } .card.bad { background: var(--bad-soft); }
.card.warn .n { color: var(--warn-text); } .card.warn { background: var(--warn-soft); }
.card.ok .n { color: var(--good); }
.card.todo { border: 2px dashed var(--brand); background: var(--mint); }
.card.todo .n { color: var(--brand-dark); }
.todo-l { font-size: 12px; font-weight: 700; color: var(--brand-dark); }
.card.on { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.fbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.f { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--ink-aux); }
.f select, .dt input { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; font-size: 13px; background: var(--card); color: var(--ink); }
.dt { font-size: 13px; color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; }
.seg-l { font-size: 13px; color: var(--ink-soft); margin-left: 6px; }
.cnt { font-size: 14px; }
.srch { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; min-width: 340px; }
.chip { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--mint); color: var(--brand-dark); }
.chip.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; font-family: inherit; }
.chip.btn.on { background: var(--brand); color: var(--type-on); border-color: var(--brand); }
.chip .x { border: 0; background: none; cursor: pointer; color: inherit; }
.pbtn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; text-decoration: none; display: inline-block; }
.pbtn.on { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.pbtn:disabled { opacity: .5; cursor: not-allowed; }
.warnbox { background: var(--warn-soft); color: var(--warn-text); border-radius: 8px; padding: 8px 12px; margin-top: 10px; font-size: 13px; }
.frm { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px 14px; margin-bottom: 14px; padding: 12px; background: var(--sub); border: 1px solid var(--border); border-radius: var(--radius); }
.frm-t { grid-column: 1 / -1; margin: 0; font-size: 15px; }
.req { color: var(--bad); font-weight: 700; }
.req-note { font-size: 12px; font-weight: 400; color: var(--ink-aux); }
.frm label, .inl label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--ink-aux); }
.frm input, .frm select, .inl input, .inl select, .sel { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; font-size: 13px; background: var(--card); color: var(--ink); }
.frm .wide { grid-column: 1 / -1; }
.inl { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
</style>
