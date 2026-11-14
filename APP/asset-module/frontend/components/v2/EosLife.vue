<script setup lang="ts">
// 8.3 生命週期 EOS「作業系統／硬體／軟體」頁籤（V30，2.048.23 起 V2 原生；取代嵌入的舊頁 pages/eos.vue）。同一批 API，行為不變：
// GET /api/eos/summary（六桶：host_os／firmware／software／insufficient／other／hardware）、GET /api/inventory/eos/hosts（盤點到的主機）、
// GET /api/assets?canonical_os|canonical_model（「N 台」下鑽，跟舊頁點「N 台 →」同一支查詢）。
// 寫入（後端 USER_WRITE＝一般帳號以上）：POST/DELETE /api/eos/category-override（移分類／還原自動）、POST /api/normalize/canonical-override（改名）、
// POST /api/normalize/alias（補對應、採用建議）。POST /api/normalize/pending/import 是 ADMIN_WRITE（只給管理員）。
// 匯出：/api/eos/export、/api/normalize/pending/export 是 _USER_EXPORTS（只能看的帳號不顯示）；/api/inventory/eos/hosts/export 是 _ADMIN_EXPORTS。
// 確認步驟照舊：移分類要確認、匯入對應表要確認；其餘照舊不問。合併列（merged_versions）不能改名、補對應、移分類（目標不是真實 canonical）。
// 網址：?tab=os|hw|sw（外層）＋ es=子分頁、est=狀態快篩、ef=第二層、esf=第三層、eq=搜尋（有字就跨全部分頁找）；下鑽 edk=os|model＋edv=名稱。
import { layerText } from '~/utils/eosLayers'
import { selectIcsRows, type IcsItem } from '~/utils/eosIcs'
import { TRUST_HINT, type Trust } from '~/utils/eosEvidence'
import { upText } from '~/utils/upText'

interface EosItem {
  name: string; status: 'expired' | 'upcoming' | 'ok' | 'unknown'; eos_date: string | null; source_url: string | null
  confidence: string | null; note: string | null; count: number; layers?: Record<string, number>; eos_kind?: string | null
  reason?: string | null; reason_label?: string | null; trusted?: boolean; trust?: Trust; trust_label?: string
  downgraded?: boolean; entitlement_pending?: boolean; device_models?: string[]; family?: string; linux_distro?: string | null
  vendor?: string | null; overridden?: boolean; merged_versions?: string[]; suggested?: string | null; model_confirmed?: boolean
  kind?: 'os' | 'device_model'
}
interface EosGroup { by_status: Record<string, number>; items: EosItem[] }
interface Summary { host_os: EosGroup; firmware: EosGroup; software: EosGroup; insufficient: EosGroup; other: EosGroup; hardware: EosGroup; os_order?: string[] }
type ItemOrigin = 'host_os' | 'firmware' | 'software' | 'insufficient' | 'other' | 'hardware'
type UiTabKey = 'host_os' | 'network' | 'hardware' | 'vm' | 'software' | 'insufficient' | 'other'
type Row = EosItem & { _origin: ItemOrigin; _tabLabel?: string; _tabKey?: UiTabKey }

const props = defineProps<{ view?: 'os' | 'hw' | 'sw' }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin, canWrite } = useAuth()
const route = useRoute()
const router = useRouter()
const apiBase = useRuntimeConfig().public.apiBase
const errMsg = (e: any, fb: string) => { const d = e?.data?.detail; return (typeof d === 'string' ? d : d?.message) ?? fb }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const view = computed(() => props.view ?? 'os')
// 換子分頁／篩選＝改網址（replace，不塞歷史）；外層 tab 永遠帶著
function setQ(patch: Record<string, string | null>) {
  const query: Record<string, string> = {}
  for (const [k, v] of Object.entries(route.query)) if (typeof v === 'string') query[k] = v
  for (const [k, v] of Object.entries(patch)) { if (v) query[k] = v; else delete query[k] }
  query.tab = view.value
  router.replace({ path: route.path, query })
}

const summary = ref<Summary | null>(null)
const loading = ref(true)
const errorMessage = ref('')
async function load() {
  loading.value = true; errorMessage.value = ''
  try { summary.value = await apiFetch<Summary>('/api/eos/summary') } catch { errorMessage.value = 'EOS 資料載入失敗，請稍後再試' } finally { loading.value = false }
}
onMounted(load)

const STATUS_LABEL: Record<string, string> = { expired: '已過 EOS', upcoming: '一年內到期', ok: '尚在支援期', unknown: '未公佈' }
const STATUS_RANK: Record<string, number> = { expired: 0, upcoming: 1, ok: 2, unknown: 3 }
const CONFIDENCE_LABEL: Record<string, string> = {
  confirmed: '已核對官方頁面', confirmed_via_attachment: '官方Excel附件核對', unverified: '來源官方但未能重新核對',
  partial_mismatch: '來源僅涵蓋部分型號', no_official_date: '官方未公佈', no_single_date: '無單一日期(依版本而異)', source_rejected: '僅論壇來源，不採信',
}
const TABS: { key: UiTabKey; label: string }[] = [
  { key: 'host_os', label: '作業系統' }, { key: 'network', label: '網路設備' }, { key: 'hardware', label: '硬體型號' }, { key: 'vm', label: 'VM' },
  { key: 'software', label: '軟體' }, { key: 'insufficient', label: '資訊不足' }, { key: 'other', label: '未分類' }]
const VIEW_TABS: Record<string, UiTabKey[]> = { os: ['host_os', 'vm', 'insufficient', 'other'], hw: ['hardware', 'network'], sw: ['software'] }
const shownTabs = computed(() => TABS.filter(t => VIEW_TABS[view.value].includes(t.key)))
const activeTab = computed<UiTabKey>(() => (VIEW_TABS[view.value].includes(q('es') as UiTabKey) ? (q('es') as UiTabKey) : VIEW_TABS[view.value][0]))
const statusFilter = computed(() => (STATUS_LABEL[q('est')] ? q('est') : ''))
const familyFilter = computed(() => q('ef') || null)
const subFilter = computed(() => q('esf') || null)
const searchQuery = ref(q('eq'))
// 打字中（計時器還沒送出）不拿網址值蓋回輸入框，只有外部改網址（上一頁、點分頁）才同步
watch(() => route.query.eq, (v) => { if (searchTimer) return; const s = typeof v === 'string' ? v : ''; if (s !== searchQuery.value) searchQuery.value = s })
let searchTimer: ReturnType<typeof setTimeout> | null = null
function onSearch(v: string) {
  searchQuery.value = v
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { searchTimer = null; setQ({ eq: v.trim() ? v : null }) }, 300)
}
onBeforeUnmount(() => { if (searchTimer) clearTimeout(searchTimer) })
const togStatus = (s: string) => setQ({ est: statusFilter.value === s ? null : s })
const goTab = (k: UiTabKey) => { cancelEdit(); setQ({ es: k, ef: null, esf: null }) }
// 搜尋結果點「分頁」＝跳到那個分頁並清掉搜尋（打字中還沒寫進網址的也一起取消）
function goFromSearch(k: UiTabKey) {
  if (searchTimer) { clearTimeout(searchTimer); searchTimer = null }
  searchQuery.value = ''
  // 那個分頁可能在另一個外層頁籤（例：在「作業系統」搜到硬體型號）→ 外層 tab 一起換
  const v = Object.keys(VIEW_TABS).find(x => VIEW_TABS[x].includes(k)) ?? view.value
  router.replace({ path: route.path, query: { tab: v, es: k, ...(statusFilter.value ? { est: statusFilter.value } : {}) } })   // 狀態快篩照舊留著
}

// 上方四格：後端六桶加總（逐台），跟怎麼分頁顯示無關
const BUCKETS: (keyof Summary)[] = ['host_os', 'firmware', 'software', 'insufficient', 'other', 'hardware']
function statusCount(s: string) {
  if (!summary.value) return 0
  return BUCKETS.reduce((sum, k) => sum + ((summary.value![k] as EosGroup).by_status[s] ?? 0), 0)
}
const tag = (list: EosItem[] | undefined, o: ItemOrigin): Row[] => (list ?? []).map(it => ({ ...it, _origin: o }))
const TAB_ITEMS = computed<Record<UiTabKey, Row[]>>(() => {
  const s = summary.value
  const hw = tag(s?.hardware.items, 'hardware')
  return {
    host_os: tag(s?.host_os.items, 'host_os'),
    network: [...tag(s?.firmware.items, 'firmware'), ...hw.filter(it => it.family === '網路設備')],
    hardware: hw.filter(it => it.family !== '網路設備' && it.family !== '虛擬化'),
    vm: hw.filter(it => it.family === '虛擬化'),
    software: tag(s?.software.items, 'software'),
    insufficient: tag(s?.insufficient.items, 'insufficient'),
    other: tag(s?.other.items, 'other'),
  }
})
const byStatus = <T extends { status: string }>(list: T[]) => (statusFilter.value ? list.filter(x => x.status === statusFilter.value) : list)
const tabItemCount = (k: UiTabKey) => byStatus(TAB_ITEMS.value[k]).length

// 第二／三層：作業系統依全站 OS 類型（後端 os_order），Linux 底下再分發行版；硬體型號依類別，主機設備底下再分廠牌；網路設備直接依廠牌
const GROUP_FAMILY_ORDER = computed<Partial<Record<UiTabKey, string[]>>>(() => ({
  host_os: summary.value?.os_order ?? ['Linux', 'Windows', 'AIX', 'IBM i', 'VMware', '其他'], hardware: ['主機設備', '儲存設備', '其他'] }))
const SUB_GROUP: Partial<Record<UiTabKey, { family: string; field: 'linux_distro' | 'vendor' }>> = {
  host_os: { family: 'Linux', field: 'linux_distro' }, hardware: { family: '主機設備', field: 'vendor' } }
const countBy = (items: Row[], f: (it: Row) => string) => { const m = new Map<string, number>(); for (const it of items) m.set(f(it), (m.get(f(it)) ?? 0) + 1); return m }
const familyChips = computed(() => {
  const items = byStatus(TAB_ITEMS.value[activeTab.value])
  if (activeTab.value === 'network') return [...countBy(items, it => it.vendor ?? '其他').entries()].sort((a, b) => b[1] - a[1]).map(([key, n]) => ({ key, n }))
  const order = GROUP_FAMILY_ORDER.value[activeTab.value]
  if (!order) return []
  const c = countBy(items, it => it.family ?? '其他')
  return order.filter(f => c.has(f)).map(f => ({ key: f, n: c.get(f)! }))
})
const subChips = computed(() => {
  const sub = SUB_GROUP[activeTab.value]
  if (!sub || familyFilter.value !== sub.family) return []
  const c = countBy(TAB_ITEMS.value[activeTab.value].filter(it => it.family === sub.family), it => (it[sub.field] ?? '其他') as string)
  return [...c.entries()].sort((a, b) => b[1] - a[1]).map(([key, n]) => ({ key, n }))
})

// 項次：照預設排序（狀態 已過→一年內→支援中→未公佈，再名稱）在篩選前編好，篩選／排序都不重編
const byDefault = (a: Row, b: Row) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) || a.name.localeCompare(b.name, 'zh-Hant', { numeric: true })
const decorate = (list: Row[]) => [...list].sort(byDefault).map((it, i) => ({ ...it, __no: i + 1, __k: `${it._tabKey ?? ''}|${it._origin}|${it.name}`,
  st_r: STATUS_RANK[it.status] ?? 9, rename: it.name, alias: it.suggested ?? null, cat: it.overridden ? '人工覆寫' : null, st_t: it.status === 'unknown' && it.reason_label ? it.reason_label : STATUS_LABEL[it.status],
  dm_t: it.device_models?.length ? it.device_models.join('、') : (it.vendor ?? null),
  trust_t: [it.trust_label, it.confidence ? (CONFIDENCE_LABEL[it.confidence] || it.confidence) : ''].filter(Boolean).join('・') || null }))
const isGlobalSearch = computed(() => searchQuery.value.trim().length > 0)
const numberedTab = computed(() => decorate(TAB_ITEMS.value[activeTab.value]))
const numberedAll = computed(() => decorate(TABS.flatMap(t => TAB_ITEMS.value[t.key].map(it => ({ ...it, _tabLabel: t.label, _tabKey: t.key })))))
const rows = computed(() => {
  const s = searchQuery.value.trim().toLowerCase()
  if (isGlobalSearch.value) {
    // 搜尋跨全部分頁找（比對名稱、掛在哪些機型上、廠牌），每列標明屬於哪個分頁
    return byStatus(numberedAll.value.filter(it => it.name.toLowerCase().includes(s) || (it.device_models ?? []).some(m => m.toLowerCase().includes(s))
      || (it.vendor ?? '').toLowerCase().includes(s)))
  }
  let list = numberedTab.value
  if (activeTab.value === 'network') {
    if (familyFilter.value) list = list.filter(it => (it.vendor ?? '其他') === familyFilter.value)
  } else if (GROUP_FAMILY_ORDER.value[activeTab.value]) {
    const sub = SUB_GROUP[activeTab.value]
    if (familyFilter.value) list = list.filter(it => (it.family ?? '其他') === familyFilter.value)
    if (sub && familyFilter.value === sub.family && subFilter.value) list = list.filter(it => (it[sub.field] ?? '其他') === subFilter.value)
  }
  return byStatus(list)
})
const showDeviceModelsCol = computed(() => !isGlobalSearch.value && activeTab.value !== 'hardware' && activeTab.value !== 'vm')
const showAliasCol = computed(() => activeTab.value === 'other' || isGlobalSearch.value)
// 機型欄只列前 2 種＋「等 N 種」，全部清單在滑鼠提示與匯出（匯出走 dm_t 全文）
const dmShort = (r: any) => {
  const ms: string[] = r.device_models?.length ? r.device_models : []
  if (!ms.length) return r.dm_t || '—'
  return ms.length <= 2 ? ms.join('、') : `${ms.slice(0, 2).join('、')} 等 ${ms.length} 種`
}
const cols = computed(() => [
  { k: 'st_r', label: '狀態', text: (r: any) => r.st_t },
  ...(isGlobalSearch.value ? [{ k: '_tabLabel', label: '分頁' }] : []),
  { k: 'name', label: activeTab.value === 'hardware' && !isGlobalSearch.value ? '型號' : '版本', mono: true },
  ...(canWrite.value ? [{ k: 'rename', label: '確認／修正名稱', note: '系統目前的答案（確認或推測），可以直接打字改，按 Enter 或點掉就存檔，之後永遠照你打的為準。' }] : []),
  ...(showDeviceModelsCol.value ? [{ k: 'dm_t', label: '掛在哪些機型上' }] : []),
  { k: 'eos_date', label: 'EOS 日期', mono: true },
  { k: 'trust_t', label: '可信度' },
  { k: 'count', label: '資產數', mono: true, text: (r: any) => `${r.count} 台` },
  { k: 'source_url', label: '來源' },
  ...(showAliasCol.value ? [{ k: 'alias', label: '補對應' }] : []),
  ...(canWrite.value ? [{ k: 'cat', label: '分類' }] : []),
])

// ===== 寫入（一般帳號以上，後端 USER_WRITE）=====
const CATEGORY_LABEL: Record<string, string> = {
  host_os: '作業系統', firmware: '韌體', software: '軟體', insufficient: '資訊不足', other: '未分類',
  '網路設備': '網路設備', '主機設備': '主機設備', '儲存設備': '儲存設備', '虛擬化': '虛擬化', '其他': '其他' }
const HW_CATEGORIES = ['網路設備', '主機設備', '儲存設備', '虛擬化', '其他']
function moveOptions(item: Row): string[] {
  if (item._origin === 'hardware') return HW_CATEGORIES.filter(c => c !== (item.family ?? '其他'))
  return (['host_os', 'firmware', 'software', 'insufficient', 'other'] as const).filter(c => c !== item._origin)
}
const moveBusy = ref<Record<string, boolean>>({})
async function moveCategory(item: Row, category: string) {
  if (!window.confirm(`把「${item.name}」歸類改成「${CATEGORY_LABEL[category]}」？（技術判斷不變，只是改變由誰維護）`)) return
  moveBusy.value[item.name] = true
  try {
    await apiFetch('/api/eos/category-override', { method: 'POST', body: { canonical: item.name, category } })
    showToast(`已改歸類為「${CATEGORY_LABEL[category]}」`, 'success')
    await load()
  } catch (e: any) { showToast(errMsg(e, '改分類失敗，請稍後再試'), 'error') } finally { moveBusy.value[item.name] = false }
}
async function revertCategory(item: Row) {
  moveBusy.value[item.name] = true
  try {
    await apiFetch(`/api/eos/category-override/${encodeURIComponent(item.name)}`, { method: 'DELETE' })
    showToast('已改回系統自動判斷', 'success')
    await load()
  } catch (e: any) { showToast(errMsg(e, '還原失敗，請稍後再試'), 'error') } finally { moveBusy.value[item.name] = false }
}
// 改名：輸入框常駐，預填系統目前的答案；按 Enter 或點掉就存（跟舊頁一樣）。種類用後端明講的 kind，沒有才看顯示在哪個分頁
const renameDraft = ref<Record<string, string>>({})
const renameBusy = ref<Record<string, boolean>>({})
async function saveRename(item: Row) {
  if (renameBusy.value[item.name]) return          // Enter 存檔中又失焦，不重送
  const newName = (renameDraft.value[item.name] ?? item.name).trim()
  if (!newName || newName === item.name) { delete renameDraft.value[item.name]; return }
  renameBusy.value[item.name] = true
  try {
    await apiFetch('/api/normalize/canonical-override', { method: 'POST',
      body: { kind: item.kind ?? (item._origin === 'hardware' ? 'device_model' : 'os'), old_canonical: item.name, new_canonical: newName } })
    showToast('已改名', 'success')
    delete renameDraft.value[item.name]
    await load()
  } catch (e: any) { showToast(errMsg(e, '改名失敗，請稍後再試'), 'error') } finally { renameBusy.value[item.name] = false }
}
// 補對應（只對「未分類」的原始值）：表格上方開一列輸入標準名稱
const editingRaw = ref<string | null>(null)
const editingCanonical = ref('')
const editBusy = ref(false)
function startEdit(item: Row) { editingRaw.value = item.name; editingCanonical.value = '' }
function cancelEdit() { editingRaw.value = null; editingCanonical.value = '' }
async function saveEdit() {
  if (!editingRaw.value || !editingCanonical.value.trim()) { showToast('請填標準名稱', 'error'); return }
  editBusy.value = true
  try {
    await apiFetch('/api/normalize/alias', { method: 'POST', body: { kind: 'os', raw_value: editingRaw.value, canonical: editingCanonical.value.trim() } })
    showToast('已補上對應', 'success')
    cancelEdit()
    await load()
  } catch (e: any) { showToast(errMsg(e, '補對應失敗，請稍後再試'), 'error') } finally { editBusy.value = false }
}
const suggestBusy = ref<Record<string, boolean>>({})
async function adoptSuggestion(item: Row) {
  if (!item.suggested) return
  suggestBusy.value[item.name] = true
  try {
    await apiFetch('/api/normalize/alias', { method: 'POST', body: { kind: 'os', raw_value: item.name, canonical: item.suggested } })
    showToast('已採用建議', 'success')
    await load()
  } catch (e: any) { showToast(errMsg(e, '採用失敗，請稍後再試'), 'error') } finally { suggestBusy.value[item.name] = false }
}
// 匯入補好的待補清單（管理員，後端 ADMIN_WRITE）：照舊先確認
const importBusy = ref(false)
async function onImportFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!window.confirm(`即將匯入「${file.name}」，把有填「標準名稱」的列補進對應表，空白的列會跳過。確定執行嗎？`)) { input.value = ''; return }
  importBusy.value = true
  try {
    const form = new FormData(); form.append('file', file)
    const r = await apiFetch<{ applied: number; skipped: number; errors: string[] }>('/api/normalize/pending/import', { method: 'POST', body: form })
    showToast(`已補上 ${r.applied} 筆，${r.skipped} 筆留空跳過${r.errors.length ? `，${r.errors.length} 筆格式有問題` : ''}`, r.errors.length ? 'warn' : 'success')
    await load()
  } catch (err: any) { showToast(errMsg(err, '匯入失敗，請稍後再試'), 'error') } finally { importBusy.value = false; input.value = '' }
}

// ===== 到期提醒 ICS（只含「已確認」的官方完整日期；規則在 utils/eosIcs.ts）=====
function icsEsc(s: string) { return String(s).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n') }
function exportIcs() {
  if (!summary.value) return
  const { rows: ics, skippedUntrusted, skippedNoDate } = selectIcsRows(BUCKETS.map(b => (summary.value![b] as EosGroup).items as IcsItem[]))
  const skippedAll = skippedUntrusted + skippedNoDate
  if (!ics.length) { showToast(`沒有可匯出的到期項目（要有「已確認」的官方完整日期）${skippedAll ? `；另有 ${skippedAll} 筆「推定」或「未知」（來源未核對、被拒或只有月份）未納入` : ''}`, 'warn'); return }
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '')
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//CL WEBIT//EOS//TW', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH']
  ics.forEach((r, i) => {
    const label = r.status === 'expired' ? '已過 EOS' : 'EOS 一年內到期'
    lines.push('BEGIN:VEVENT', `UID:eos-${i}-${r.date}@webit`, `DTSTAMP:${stamp}`, `DTSTART;VALUE=DATE:${r.date}`,
      `SUMMARY:${icsEsc(`${label}：${r.name}（${r.count} 台）`)}`,
      `DESCRIPTION:${icsEsc(`${r.name} 官方終止支援日 ${r.date}（可信度：${r.trustLabel}），目前 ${r.count} 台。請排換代／延支。`)}`,
      'BEGIN:VALARM', 'TRIGGER:-P30D', 'ACTION:DISPLAY', 'DESCRIPTION:EOS 前 30 天提醒', 'END:VALARM', 'END:VEVENT')
  })
  lines.push('END:VCALENDAR')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([lines.join('\r\n')], { type: 'text/calendar;charset=utf-8' }))
  a.download = `EOS到期提醒_${new Date().toISOString().slice(0, 10)}.ics`
  a.click()
  URL.revokeObjectURL(a.href)
  showToast(`已匯出 ${ics.length} 筆到期提醒（ICS 行事曆，只含「已確認」的官方日期）${skippedAll ? `；${skippedAll} 筆「推定」或「未知」（來源未核對、被拒或只有月份）未納入` : ''}`, 'success')
}

// ===== 「N 台」下鑽：硬體側用 canonical_model，其他用 canonical_os（跟舊頁「N 台 →」同一個規則、同一支 /api/assets）=====
const drillKind = computed(() => (q('edk') === 'os' || q('edk') === 'model' ? q('edk') : ''))
const drillName = computed(() => (drillKind.value ? q('edv') : ''))
const drillLink = (r: Row) => ({ path: route.path, query: { ...Object.fromEntries(Object.entries(route.query).filter(([, v]) => typeof v === 'string')) as Record<string, string>,
  tab: view.value, edk: r._origin === 'hardware' ? 'model' : 'os', edv: r.name } })
const drillBack = computed(() => { const query: Record<string, string> = {}
  for (const [k, v] of Object.entries(route.query)) if (typeof v === 'string' && k !== 'edk' && k !== 'edv') query[k] = v
  return { path: route.path, query: { ...query, tab: view.value } } })
const drillRows = ref<any[]>([])
const drillErr = ref('')
const drillLoading = ref(false)
const beginDrill = useLatest()
watch([drillKind, drillName], async ([k, n]) => {
  const isLatest = beginDrill()
  drillRows.value = []; drillErr.value = ''; drillLoading.value = false
  if (!k || !n) return
  drillLoading.value = true
  try {
    const r = await apiFetch<any[]>('/api/assets', { query: k === 'model' ? { canonical_model: n } : { canonical_os: n } })
    if (!isLatest()) return
    drillRows.value = (Array.isArray(r) ? r : []).map((x, i) => ({ ...x, __no: i + 1 }))
  } catch (e: any) { if (isLatest()) drillErr.value = `讀不到資產清單：${errMsg(e, '未知錯誤')}` } finally { if (isLatest()) drillLoading.value = false }
}, { immediate: true })
const machine = (s: string | null) => (s ? `/v2/machine/${encodeURIComponent(s)}` : null)
const drillCols = [{ k: 'hostname', label: '主機名稱', link: (r: any) => machine(r.asset_serial) }, { k: 'ip', label: 'IP', mono: true },
  { k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => machine(r.asset_serial) }, { k: 'os', label: '作業系統' },
  { k: 'device_model', label: '設備型號' }, { k: 'environment', label: '環境' }, { k: 'physical_location', label: '機房' }]

// ===== 盤點到的主機（查得到 EOS 日期的主機；預設收合）=====
const hostsOpen = ref(false)
const hosts = ref<any[]>([])
const hostsLoaded = ref(false)
const hostsErr = ref('')
const hostKw = ref('')
async function loadHosts() {
  try { hosts.value = ((await apiFetch<any>('/api/inventory/eos/hosts')).items ?? []); hostsErr.value = '' } catch (e: any) { hosts.value = []; hostsErr.value = `盤點主機清單載入失敗：${errMsg(e, '未知錯誤')}` } finally { hostsLoaded.value = true }
}
onMounted(loadHosts)
const ipNum = (ip: string) => (/^\d{1,3}(\.\d{1,3}){3}$/.test(ip || '') ? ip.split('.').reduce((n, p) => n * 256 + Number(p), 0) : Number.MAX_SAFE_INTEGER)
const hostRows = computed(() => {
  const all = [...hosts.value].sort((a, b) => ipNum(a.ip) - ipNum(b.ip) || String(a.ip).localeCompare(String(b.ip))).map((h, i) => ({ ...h, __no: i + 1, dm_t: upText(h.device_model) || null }))
  const s = hostKw.value.trim().toLowerCase()
  if (!s) return all
  return all.filter(h => [h.ip, h.hostname, h.asset_serial, h.os, h.environment, h.physical_location, h.device_model].some(v => (v || '').toString().toLowerCase().includes(s)))
})
const tone = (s?: string | null) => (!s ? '' : s.includes('已過') || s === 'eol' ? 'bad' : s.includes('即將') || s === 'soon' ? 'warn' : 'ok')
const hostCols = [{ k: 'ip', label: 'IP', mono: true }, { k: 'hostname', label: '主機名稱', link: (h: any) => machine(h.asset_serial) },
  { k: 'physical_location', label: '機房' }, { k: 'environment', label: '環境' }, { k: 'os', label: '作業系統' },
  { k: 'os_eos', label: 'OS EOS', mono: true }, { k: 'dm_t', label: '設備型號' }, { k: 'hw_eos', label: '硬體 EOS', mono: true }]
</script>

<template>
  <div>
    <p class="muted">硬體型號與作業系統版本的官方終止支援（EOS／EOL）日期
      <InfoNote>「資產數」每一台各算一台、各自查自己的 EOS，不會把整套設備的日期複製到元件。底下的「其中…」是這些機器的構成（整機／元件／管理口），由機櫃位置規則與人工合併決定推定，判斷不出的標「未能判斷」。同一台會同時出現在作業系統與硬體兩個分頁，請各頁分開看，不要跨頁相加。只查「種類」——型號跟版本種類比資產少得多，查一次全部台數都對得上。日期一律來自官方公告，查不到的標「未公佈」，不會用猜的。</InfoNote></p>

    <!-- 「N 台」下鑽 -->
    <template v-if="drillKind && drillName">
      <div class="bar"><NuxtLink :to="drillBack" replace>‹ 返回</NuxtLink><b>{{ drillKind === 'model' ? '型號' : '版本' }}：{{ drillName }}</b>
        <span class="muted">{{ drillLoading ? '讀取中…' : drillErr ? '—' : `${drillRows.length} 筆` }}</span></div>
      <p v-if="drillErr" class="err">{{ drillErr }}</p>
      <V2Table v-else :rows="drillRows" prefix="A" row-key="__no" unit="筆" :cols="drillCols" :export-name="`EOS_${drillName}`"
               :empty="drillLoading ? '讀取中…' : '沒有符合的資產（0 筆）'" />
    </template>

    <template v-else>
      <section class="ih">
        <button type="button" class="tg" data-test="eos-hosts-toggle" @click="hostsOpen = !hostsOpen">{{ hostsOpen ? '▾' : '▸' }} 盤點到的主機 <b>{{ hosts.length }}</b> 台</button>
        <InfoNote>查得到 EOS 日期的主機。<b>查不到的不列</b>——「沒有資料」不等於「還在支援」，混在一起會讓人以為已經查過了。</InfoNote>
        <template v-if="hostsOpen">
          <input v-model="hostKw" class="kw" placeholder="搜 IP／主機名／機房／OS" aria-label="搜尋盤點到的主機">
          <a v-if="isAdmin" class="btn2" :href="`${apiBase}/api/inventory/eos/hosts/export`">匯出 Excel</a>
        </template>
      </section>
      <template v-if="hostsOpen">
        <p v-if="hostsErr" class="err">{{ hostsErr }}</p>
        <V2Table v-else :rows="hostRows" prefix="H" row-key="ip" unit="台" :cols="hostCols" export-name="EOS_盤點到的主機" :no-export="!isAdmin"
                 :empty="!hostsLoaded ? '載入中…' : hostKw ? `沒有符合「${hostKw}」的主機` : '目前沒有任何主機查得到 EOS 日期'">
          <template #cell-ip="{ row }">{{ row.ip }}<span v-if="row.registered === false" class="tag bad">未登記<InfoNote>收到資料，但資產庫查不到這個 IP。</InfoNote></span></template>
          <template #cell-os_eos="{ row }"><span v-if="row.os_eos" class="tag" :class="tone(row.os_status)">{{ row.os_eos }}</span><template v-else>—</template></template>
          <template #cell-hw_eos="{ row }"><span v-if="row.hw_eos" class="tag" :class="tone(row.hw_status)">{{ row.hw_eos }}</span><template v-else>—</template></template>
        </V2Table>
      </template>

      <p v-if="errorMessage" class="err">{{ errorMessage }}</p>
      <p v-else-if="loading && !summary" class="muted">載入中…</p>
      <template v-else-if="summary">
        <div class="cards">
          <button type="button" class="scard bad" :class="{ on: statusFilter === 'expired' }" data-test="eos-st-expired" @click="togStatus('expired')">
            <span class="sv">{{ statusCount('expired') }}</span><span class="sl">已過 EOS 的台數<InfoNote>作業系統＋韌體＋硬體合計，逐台（同一台多筆登記只算一次）。下面分頁的數字是種類數（版本／型號有幾種），兩者本來就不一樣：一種 Windows Server 2012 可能掛著幾百台。點一下只看這些。</InfoNote></span></button>
          <button type="button" class="scard warn" :class="{ on: statusFilter === 'upcoming' }" data-test="eos-st-upcoming" @click="togStatus('upcoming')">
            <span class="sv">{{ statusCount('upcoming') }}</span><span class="sl">一年內到期（台）<InfoNote>該排換代／延支了。逐台。點一下只看這些。</InfoNote></span></button>
          <button type="button" class="scard" :class="{ on: statusFilter === 'ok' }" data-test="eos-st-ok" @click="togStatus('ok')">
            <span class="sv">{{ statusCount('ok') }}</span><span class="sl">尚在官方支援期（台）</span></button>
          <button type="button" class="scard" :class="{ on: statusFilter === 'unknown' }" data-test="eos-st-unknown" @click="togStatus('unknown')">
            <span class="sv">{{ statusCount('unknown') }}</span><span class="sl">查不到官方日期（台）<InfoNote>未公佈或還沒查。逐台。</InfoNote></span></button>
        </div>
        <p v-if="statusFilter" class="muted">只看「{{ STATUS_LABEL[statusFilter] }}」・<button type="button" class="lnk" @click="setQ({ est: null })">清除狀態篩選</button></p>

        <div class="chips">
          <button v-for="t in shownTabs" :key="t.key" type="button" class="chip" :class="{ on: activeTab === t.key && !isGlobalSearch }" :data-test="`eos-tab-${t.key}`" @click="goTab(t.key)">{{ t.label }} {{ tabItemCount(t.key) }}</button>
        </div>
        <div class="bar">
          <input :value="searchQuery" type="text" class="kw wide" placeholder="搜尋版本／型號／掛在哪些機型上／廠牌…（跨全部分頁找）" aria-label="搜尋 EOS 項目"
                 @input="onSearch(($event.target as HTMLInputElement).value)">
          <a v-if="canWrite" class="btn2" :href="`${apiBase}/api/eos/export`" target="_blank" rel="noopener">匯出全部清單（Excel）</a>
          <button v-if="canWrite" type="button" class="btn2" @click="exportIcs">匯出到期提醒（行事曆 ICS）</button>
        </div>
        <div v-if="familyChips.length && !isGlobalSearch" class="chips">
          <button v-for="f in familyChips" :key="f.key" type="button" class="chip" :class="{ on: familyFilter === f.key }"
                  @click="setQ({ ef: familyFilter === f.key ? null : f.key, esf: null })">{{ f.key }} <i class="cnt">{{ f.n }}</i></button>
          <template v-if="subChips.length">
            <span class="muted">／</span>
            <button v-for="d in subChips" :key="d.key" type="button" class="chip sub" :class="{ on: subFilter === d.key }"
                    @click="setQ({ esf: subFilter === d.key ? null : d.key })">{{ d.key }} <i class="cnt">{{ d.n }}</i></button>
          </template>
        </div>

        <div v-if="activeTab === 'other'" class="maintbox">
          <b>待補對應</b><InfoNote>這些原始值規則跟別名字典都認不出來，是查不到 EOS 的大宗原因（資料本身太籠統，不是系統問題）。逐列補一筆標準名稱即可，或整批匯出 Excel 離線查證後匯入回來。</InfoNote>
          <a v-if="canWrite" class="btn2" :href="`${apiBase}/api/normalize/pending/export`" target="_blank" rel="noopener">匯出待補清單（Excel）</a>
          <label v-if="isAdmin" class="btn2 file" :class="{ disabled: importBusy }">{{ importBusy ? '匯入中…' : '匯入補好的清單' }}
            <input type="file" accept=".xlsx" class="hidden-file" :disabled="importBusy" aria-label="匯入補好的清單" @change="onImportFile"></label>
          <span v-else class="muted">匯入限管理員</span>
        </div>

        <div v-if="editingRaw && (activeTab === 'other' || isGlobalSearch)" class="bar edit">
          <span class="muted">「{{ editingRaw }}」的標準名稱：</span>
          <input v-model="editingCanonical" type="text" class="kw" placeholder="例：Cisco Network OS 15.7(3)M8" :disabled="editBusy" aria-label="標準名稱" @keyup.enter="saveEdit">
          <button type="button" class="btn2" :disabled="editBusy" @click="saveEdit">{{ editBusy ? '儲存中…' : '儲存' }}</button>
          <button type="button" class="lnk" :disabled="editBusy" @click="cancelEdit">取消</button>
        </div>

        <V2Table :rows="rows" prefix="E" row-key="__k" unit="種" :cols="cols" :export-name="isGlobalSearch ? 'EOS_搜尋結果' : `EOS_${TABS.find(t => t.key === activeTab)?.label}`" empty="沒有資料">
          <template #cell-st_r="{ row }"><span class="dot" :class="'st-' + row.status">{{ row.st_t }}</span></template>
          <template #cell-_tabLabel="{ row }"><button type="button" class="lnk" @click="goFromSearch(row._tabKey)">{{ row._tabLabel }}</button></template>
          <template #cell-name="{ row }">{{ row.name }}
            <span v-if="row.merged_versions?.length" class="muted small">（合併 {{ row.merged_versions.length }} 個小版本）<InfoNote>查到的 EOS 結果相同，合併顯示：{{ row.merged_versions.join('、') }}</InfoNote></span>
            <span v-if="row.model_confirmed === false" class="guess">（型號：推測）<InfoNote>這個型號名稱是靠資產名稱／資產用途欄猜出來的，不是設備機型欄位本身寫得清楚，僅供參考。</InfoNote></span></template>
          <template #cell-rename="{ row }">
            <input v-if="!row.merged_versions?.length" class="rename" type="text" :value="renameDraft[row.name] ?? row.name" :disabled="renameBusy[row.name]" aria-label="確認／修正名稱"
                   @input="renameDraft[row.name] = ($event.target as HTMLInputElement).value" @keyup.enter="saveRename(row)" @blur="saveRename(row)">
            <template v-else>—</template></template>
          <!-- 2.059.17（使用者 11-10：「顯示重點就好，其他算備註」「本來可以一頁看全部」）：日期旁的說明收進 ⓘ -->
          <template #cell-eos_date="{ row }">{{ row.eos_date || '—' }}<InfoNote v-if="row.eos_kind || (row.eos_date && row.reason === 'unverified') || row.downgraded || row.entitlement_pending">
              <div v-if="row.eos_kind">{{ row.eos_kind }}</div>
              <div v-if="row.eos_date && row.reason === 'unverified'">推定：來源未核對（不單獨視為過保）</div>
              <div v-if="row.downgraded">已登記有效的延伸維護權益，非必然須更換</div>
              <div v-else-if="row.entitlement_pending">官方有付費延伸方案；本資產是否持有尚未登記（權益待確認）</div></InfoNote></template>
          <template #cell-dm_t="{ row }"><span :title="row.dm_t || ''">{{ dmShort(row) }}</span></template>
          <template #cell-trust_t="{ row }">
            <span v-if="row.trust_label" class="conf" :class="{ weak: row.trust !== 'confirmed' }" :title="TRUST_HINT[row.trust as Trust] + (row.confidence ? '\n' + (CONFIDENCE_LABEL[row.confidence] || row.confidence) : '') + (row.note ? '\n' + row.note : '')">{{ row.trust_label }}</span>
            <span v-else-if="row.confidence" class="muted small">{{ CONFIDENCE_LABEL[row.confidence] || row.confidence }}</span>
            <template v-else>—</template></template>
          <template #cell-count="{ row }">
            <NuxtLink v-if="!row.merged_versions?.length" :to="drillLink(row)">{{ row.count }} 台</NuxtLink>
            <template v-else>{{ row.count }} 台<InfoNote>合併列沒有單一版本可篩選，請用搜尋找各小版本。</InfoNote></template>
            <InfoNote v-if="layerText(row.layers)">其中 {{ layerText(row.layers) }}</InfoNote></template>
          <template #cell-source_url="{ row }"><a v-if="row.source_url" :href="row.source_url" target="_blank" rel="noopener">官方頁面 ↗</a><template v-else>—</template></template>
          <template #cell-alias="{ row }">
            <template v-if="row._origin === 'other' && !row.merged_versions?.length">
              <span v-if="row.suggested" class="suggest">建議：{{ row.suggested }}
                <button v-if="canWrite" type="button" class="lnk" :disabled="suggestBusy[row.name]" @click="adoptSuggestion(row)">{{ suggestBusy[row.name] ? '採用中…' : '採用 →' }}</button></span>
              <button v-if="canWrite" type="button" class="lnk" @click="startEdit(row)">補對應 →</button>
            </template>
            <span v-else-if="row._origin === 'other'" class="muted small">合併列不可補對應</span>
            <template v-else>—</template></template>
          <template #cell-cat="{ row }">
            <div v-if="!row.merged_versions?.length" class="acts">
              <button v-for="c in moveOptions(row)" :key="c" type="button" class="lnk" :disabled="moveBusy[row.name]" @click="moveCategory(row, c)">→ {{ CATEGORY_LABEL[c] }}</button>
              <button v-if="row.overridden" type="button" class="lnk warn" :disabled="moveBusy[row.name]" title="改回系統自動判斷的分類" @click="revertCategory(row)">還原自動</button>
            </div>
            <template v-else>—</template></template>
        </V2Table>
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.bar.edit { padding: 8px 10px; border-radius: 8px; background: var(--sub); }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 4px 0 10px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); font-weight: 600; }
.chip.sub { font-size: 11px; }
.cnt { font-style: normal; opacity: .75; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin: 4px 0 10px; }
.scard { font-family: inherit; text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 2px; min-width: 150px; padding: 10px 14px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card); color: var(--ink); }
.scard:hover { border-color: var(--brand); }
.scard.on { outline: 2px solid var(--brand); outline-offset: -2px; }
.scard .sv { font-family: var(--disp); font-size: 22px; font-weight: 700; }
.scard .sl { font-size: 12px; color: var(--muted); display: flex; gap: 4px; align-items: center; }
.scard.bad .sv { color: var(--bad); }
.scard.warn .sv { color: var(--warn-text); }
.ih { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 4px 0 8px; }
.tg { background: none; border: none; padding: 0; cursor: pointer; font-family: inherit; font-size: 14px; font-weight: 600; color: var(--ink); }
.kw { font-family: inherit; font-size: 13px; padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 200px; }
.kw.wide { min-width: 320px; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); text-decoration: none; cursor: pointer; }
.btn2:disabled, .btn2.disabled { opacity: .45; cursor: default; }
.btn2.file { position: relative; }
.hidden-file { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
.maintbox { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 12px; margin: 0 0 10px; border-radius: 8px; border: 1px solid var(--warn); background: var(--warn-soft); font-size: 13px; }
.lnk { background: none; border: none; color: var(--link); text-decoration: underline; cursor: pointer; font-size: 12.5px; font-family: inherit; padding: 0; }
.lnk:disabled { opacity: .5; cursor: not-allowed; }
.lnk.warn { color: var(--warn-text); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; }
.rename { font-family: inherit; font-size: 12.5px; padding: 3px 8px; min-width: 180px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.rename:focus { border-color: var(--brand); outline: none; }
.dot { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; white-space: nowrap; }
.dot::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: var(--muted); display: inline-block; }
.dot.st-expired::before { background: var(--bad); }
.dot.st-upcoming::before { background: var(--warn); }
.dot.st-ok::before { background: var(--good); }
.conf { font-size: 12px; color: var(--brand-dark); }
.conf.weak { color: var(--warn-text); }
.guess { font-size: 11px; color: var(--brand-dark); margin-left: 4px; display: inline-flex; gap: 2px; align-items: center; }
.suggest { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.small { font-size: 11px; white-space: normal; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: var(--sub); color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; margin-left: 4px; }
.tag.bad { background: var(--bad-soft); color: var(--bad); }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.tag.ok { background: var(--good-soft); color: var(--good); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
