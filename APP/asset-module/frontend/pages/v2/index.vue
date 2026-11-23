<script setup lang="ts">
// 1.1 今日概況（2.044.0，需求 R9，使用者 2026-11-04：「今日概況應該是哪台主機有報修、有報告警…這才是今日；保固到期每天都 30 台，
// 一年才做完，永遠放首頁不恰當」）。只放台北時間 00:00 到現在的事；長期待辦搬到 1.4 追蹤處理，這裡只留一行入口＋今天的變化。
// 資料＝/api/v2/today（v2_today.today，組裝既有計算）：
//   現在的告警＝wug_alert 告警事件（還開著）；今天的事件＝4.1 同一張表；今天的資產異動＝1.2 每日異動同一份比對；
//   開盤尖峰（2.045.0）＝跳板機收盤後送來的 Aria ESXi 檔（V2OpeningPeak）；今天還沒收到只寫「尚未匯入」，不放假數字。
// 0 件和「未收集／未啟用／資料中斷」分開顯示：n 是 null＝來源沒資料（寫原因），n 是 0＝真的 0 件。
// 舊網址：?tab=todo（原待處理事項）→ 1.4 追蹤處理；?tab=week（原本週異動）→ 1.2 異動與異常「本週異動」頁籤。
// 畫面重看（AI/畫面重看標準_2026-11-15.md，樣板 1.3）：頁首一行結論 →「要你處理」（沒事寫沒事）→ 可點的數字卡 →
// 表格每格一個粗體主值＋一行灰字；聯絡五欄、IP、分級收進「點監控項目看明細」與匯出；逐來源資料時間收進「資料更新於」。
import { NuxtLink } from '#components'
import { ALERT_CONTACT_COLS } from '~/utils/alertContact'
// Q9（2.066.x）：告警預設只列跟 Teams 同一個主機組政策範圍（?all=1 看全部）；同一分鐘＋同一來源收成一列，點件數（?ag=）列出那幾筆
import { scopeOf, toggleScopeQuery, groupQuery, groupRows, groupTitle, bucketQuery, attentionCount, attentionBlind } from '~/utils/alertScope'
// 2.066.x（使用者 11-13）：「需要注意」只列斷滿門檻、還沒恢復、不在例行時段的；其他收進摺疊的「例行／已自動恢復」（?ab= 下鑽）
import type { AlertGroup } from '~/utils/alertScope'
import { tierTone, sortFocus, focusTone as calcFocusTone, focusChipText, isStaleSource, EXPECTED_SOURCE_STATES, teamsStatus, relayAsSources } from '~/utils/todayFocus'
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
if (route.query.tab === 'todo') await navigateTo('/v2/track', { replace: true })
else if (route.query.tab === 'week') await navigateTo('/v2/changes?tab=week', { replace: true })

interface Block { status: string; label?: string | null; rows: any[]; n: number | null; as_of: string | null; [k: string]: any }
interface Today {
  now: string; since: string; date: string
  alerts: Block; incidents: Block; changes: Block; opening: Block
  track: { status: string; label?: string; total: number | null; high?: number; as_of: string | null; nodata?: string[]; changes?: { prev_day: string | null; new: number | null; resolved: number | null; reason: string | null } }
  sources: { key: string; label: string; as_of: string | null; state: string | null }[]
  can_notify?: boolean
}
const { apiFetch } = useApi()
const data = ref<Today | null>(null)
const err = ref('')
let loadSeq = 0
const lastLoadAt = ref(Date.now())
// 必須宣告在 await load() 之前：load() 成功時會寫 nowDay，宣告在後面會在瀏覽器端噴
// 「Cannot access '…' before initialization」（2.070.1 修，2.067.0 起的錯）
const nowDay = ref(new Date().toLocaleDateString('sv-SE'))
async function load() {
  const my = ++loadSeq
  try {
    const r = await apiFetch<Today>('/api/v2/today', { query: { scope: scopeOf(route.query.all) } })
    if (my !== loadSeq) return
    data.value = r; err.value = ''; lastLoadAt.value = Date.now(); nowDay.value = new Date().toLocaleDateString('sv-SE')
  } catch (e: any) {
    if (my !== loadSeq) return
    err.value = `今日資料載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}${data.value ? `；畫面上是 ${String(data.value.now).slice(0, 16)} 的舊資料，不是現在的狀態` : ''}`
  }
}
await load()
watch(() => route.query.all, () => { load() })
// 1.5 每日檢查今天的狀態一行；讀不到就寫原因，不擋首頁
const dcheck = ref<{ state: string; text: string; prev?: any; lp_alert?: string; bad?: number; unconfirmed?: number } | null>(null)
const dcErr = ref('')
let refreshTimer: ReturnType<typeof setInterval> | null = null
const stale5 = () => Date.now() - lastLoadAt.value > 5 * 60 * 1000
const onVisible = () => { if (document.visibilityState === 'visible' && stale5()) { load(); loadDcheck(); loadAux() } }
onMounted(() => {
  document.addEventListener('visibilitychange', onVisible)
  refreshTimer = setInterval(() => { if (document.visibilityState === 'visible' && stale5()) { load(); loadDcheck(); loadAux() } }, 60 * 1000)
  loadAux()
})
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', onVisible); if (refreshTimer) clearInterval(refreshTimer) })
async function loadAux() {
  try { teams.value = await apiFetch<any>('/api/teams-notify/settings'); teamsErr.value = '' } catch (e: any) { teamsErr.value = `Teams 狀態讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
  try { relay.value = await apiFetch<any>('/api/v2/relay-import'); relayErr.value = '' } catch (e: any) { relayErr.value = `中繼匯入狀態讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
async function loadDcheck() { try { dcheck.value = await apiFetch<any>('/api/daily-check/today'); dcErr.value = '' } catch (e: any) { dcErr.value = `讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } }
onMounted(async () => { try { dcheck.value = await apiFetch<any>('/api/daily-check/today') } catch (e: any) { dcErr.value = `讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } })

const hm = (t: string | null | undefined) => (t ? String(t).slice(11, 16) || String(t) : '')
const srcText = (s: Today['sources'][number]) => (s.state ? s.state : (s.as_of ? (s.as_of.slice(0, 10) === data.value?.date ? hm(s.as_of) : s.as_of.slice(0, 16)) : '—'))
// Teams 通知與中繼匯入的狀態（H12／H14：原本只有進設定頁才看得到）
const teams = ref<any>(null)
const teamsErr = ref('')
const relay = ref<any>(null)
const relayErr = ref('')
const allSources = computed(() => [...(data.value?.sources ?? []), ...relayAsSources(relayErr.value ? null : relay.value), ...(relayErr.value ? [{ key: 'relay', label: '中繼匯入', as_of: null, state: '讀不到' }] : [])])
const teamsView = computed(() => teamsStatus(teams.value, teamsErr.value))
const teamsInTodo = computed(() => !!teamsView.value.todo)
const staleSrc = computed(() => allSources.value.filter(isStaleSource))
/** 區塊標題旁的件數：null＝來源沒資料（寫原因的短字），0＝真的 0 件 */
const countText = (b: Block | undefined) => (!b ? '—' : b.n == null ? ({ disabled: '未啟用', uncollected: '未收集', error: '無法計算', stale: '資料中斷' } as Record<string, string>)[b.status] || '未收集' : `${b.n} 件`)
/** 2.066.1：資產異動那行裡的「收集中斷 M 台」做成連結（到 1.2 每日異動的收集中斷分類），其餘文字照原樣 */
const gapParts = (b: any): { t: string; link?: string }[] => {
  const label = String(b?.label || '')
  const g = b?.collect_gap
  const hit = g?.n && g?.link ? `收集中斷 ${g.n} 台` : ''
  const i = hit ? label.indexOf(hit) : -1
  if (i < 0) return [{ t: label }]
  return [{ t: label.slice(0, i) }, { t: hit, link: g.link }, { t: label.slice(i + hit.length) }].filter(p => p.t)
}
const dur = (m: number | null | undefined) => { if (m == null || (m as any) === '' || Number.isNaN(Number(m))) return '時間未提供'; const v = Number(m); return v < 120 ? `${v} 分` : `${Math.floor(v / 60)} 小時 ${v % 60} 分` }
const mlink = (r: any) => (r.machine_key ? `/v2/machine/${encodeURIComponent(r.machine_key)}` : r.ip ? `/v2/hosts?q=${encodeURIComponent(r.ip)}` : null)
// 告警兩段（2.044.3，GPT A2＋B1）：現在仍在告警（不管哪天開始，昨天開始的標「昨天起」）／今天發生過（今天 00:00 後開始，含已恢復）
const shape = (r: any) => ({ ...r, dur_text: dur(r.duration_min), start_text: String(r.started_at || '').slice(5, 16), end_text: r.ended_at ? String(r.ended_at).slice(5, 16) : '未恢復',
  sys_text: r.api_id ? `${r.api_id} ${r.system_name || ''}`.trim() : '未登記業務系統', since_text: r.since_note ? String(r.since_note).replace('昨日起', '昨天起') : '今天' })
const scope = computed(() => data.value?.alerts.scope)
const showAll = computed(() => scopeOf(route.query.all) === 'all')
function setScope(all: boolean) { router.replace({ query: toggleScopeQuery(route.query as any, all) }) }
const agKey = computed(() => String(route.query.ag ?? ''))
const att = computed(() => data.value?.alerts.attention ?? null)
const attGroups = computed<AlertGroup[]>(() => (att.value?.groups ?? []).map(shape))
const attRows = computed(() => (att.value?.rows ?? []).map(shape))
const agGroup = computed(() => attGroups.value.find(g => g.group === agKey.value))
const abKey = computed(() => String(route.query.ab ?? ''))
const bucketLink = (k: string) => router.resolve({ path: route.path, query: { ...bucketQuery(route.query as any, k), tab: 'routine' }, hash: '#alerts' }).fullPath
const abBucket = computed(() => (att.value?.buckets ?? []).find((b: any) => b.key === abKey.value && b.key !== 'longterm'))
// 使用者 11-14：斷超過 N 小時還沒恢復的移出需要注意，另收一區（摺疊、可下鑽）
const longB = computed(() => (att.value?.buckets ?? []).find((b: any) => b.key === 'longterm'))
const longRows = computed(() => (longB.value?.rows ?? []).map(shape))
const routineBuckets = computed(() => (att.value?.buckets ?? []).filter((b: any) => b.key !== 'longterm'))
const routineN = computed(() => routineBuckets.value.reduce((s: number, b: any) => s + b.n, 0))
const abRows = computed(() => (abBucket.value?.rows ?? []).map(shape))
const flapRows = computed(() => att.value?.flapping ?? [])
const sugRows = ref<any[] | null>(null)
const sugErr = ref('')
watch(abKey, async (k) => {
  if (k !== 'suggest' || sugRows.value) return
  try { sugRows.value = (await apiFetch<{ rows: any[] }>('/api/v2/routine/suggestions')).rows } catch (e: any) { sugErr.value = `讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}, { immediate: true })
const hostLinkOf = (r: any) => (String(r.host_key || '').startsWith('ip:') ? `/v2/hosts?q=${encodeURIComponent(String(r.host_key).slice(3))}` : r.host_key && !String(r.host_key).startsWith('wug:') ? `/v2/machine/${encodeURIComponent(r.host_key)}?tab=ops` : null)
const attCountText = computed(() => attentionCount(data.value?.alerts as any))
const agRows = computed(() => groupRows(attRows.value, attGroups.value, agKey.value))
const groupLink = (g: AlertGroup) => router.resolve({ path: route.path, query: groupQuery(route.query as any, g) }).fullPath
const closeDrill = computed(() => { const { ag: _g, ...rest } = route.query; return { path: route.path, query: rest } })
const todayAlertRows = computed(() => (data.value?.alerts.today?.rows ?? []).map(shape))

// ===== 表格欄位：畫面上只留主值；IP、業務系統是主機下面的灰字，分級與聯絡五欄在明細與匯出 =====
const hostText = (r: any) => [r.who, r.ip].filter(Boolean).join(' ')       // 排序＋搜尋（可以直接搜 IP）
const HOST_COL = { k: 'who', label: '主機', text: hostText }
const MON_COL = { k: 'monitor', label: '監控項目' }
const REASON_COL = { k: 'judge_reason', label: '判斷理由', min: 160 }
const START_COL = { k: 'start_text', label: '開始', mono: true }
const END_COL = { k: 'end_text', label: '恢復', mono: true }
const NOTIFY_COL = { k: 'notify', label: '通知', text: (r: any) => r.notified_text || '' }   // R16：按了先預覽收件人與內容，確認才送
const X_IP = { k: 'ip', label: 'IP' }
const X_TIER = { k: 'tier_label', label: '分級' }
const X_SINCE = { k: 'since_text', label: '從哪天開始' }
const X_DUR = { k: 'dur_text', label: '持續' }
const X_SYS = { k: 'sys_text', label: '業務系統' }
const X_CONTACT = ALERT_CONTACT_COLS                                             // R15：告警聯絡五欄（沒有的寫「未提供」）
// 需要注意（一組一列）：件數可點（下鑽＝這一組的那幾筆）；只有一筆的組照樣能直接按「通知」
const ATT_COLS = [HOST_COL, MON_COL, REASON_COL, { k: 'n', label: '件數', mono: true, link: groupLink }, START_COL, NOTIFY_COL]
const ATT_X = [X_IP, X_TIER, X_SINCE, X_DUR, X_SYS, ...X_CONTACT]
const AG_COLS = [HOST_COL, MON_COL, REASON_COL, START_COL, NOTIFY_COL]
const BUCKET_COLS = [HOST_COL, MON_COL, REASON_COL, START_COL, END_COL]
const BUCKET_X = [X_IP, X_DUR, X_SYS]
const TODAY_COLS = [HOST_COL, MON_COL, START_COL, END_COL, NOTIFY_COL]
const TODAY_X = [X_IP, X_TIER, X_DUR, X_SYS, ...X_CONTACT]
const FLAP_COLS = [{ k: 'who', label: '主機', text: hostText }, { k: 'n', label: '今天斷了幾次', mono: true }, { k: 'events', label: '告警筆數', mono: true }, { k: 'reason', label: '判斷理由', min: 160 }]
const SUG_COLS = [{ k: 'who', label: '主機', link: hostLinkOf }, { k: 'ip', label: 'IP', mono: true }, { k: 'start_hhmm', label: '建議開始', mono: true },
  { k: 'end_hhmm', label: '建議結束', mono: true }, { k: 'days', label: '14 天內幾天', mono: true }, { k: 'reason', label: '理由', min: 160 }]
const TWO_SLOTS = ['cell-who', 'cell-monitor', 'cell-start_text', 'cell-end_text']

// 一格＝粗體主值＋一行灰字（V2Table 的 cell-<欄> 插槽共用；監控項目可點，展開這一列的明細）
const Cell2 = (p: { row: any; k: string }, { emit }: { emit: (e: 'open') => void }) => {
  const r = p.row
  const sub = (t: string) => (t ? h('span', { class: 't11-sub' }, t) : null)
  if (p.k === 'who') {
    const to = mlink(r)
    const name = h('b', r.who || r.ip || '未知主機')
    return h('span', { class: 't11-c2' }, [to ? h(NuxtLink, { to }, () => name) : name, sub([r.ip, r.sys_text].filter(Boolean).join('・'))])
  }
  if (p.k === 'monitor') return h('button', { type: 'button', class: 't11-lb', title: '看明細', onClick: () => emit('open') }, r.monitor || '看明細')
  if (p.k === 'start_text') {
    return h('span', { class: 't11-c2' }, [h('b', [r.start_text || '—', r.since_note ? h('span', { class: 't11-tag' }, r.since_text) : null]),
      sub(r.ended_at ? '' : `已持續 ${r.dur_text}`)])
  }
  return h('span', { class: 't11-c2' }, [h('b', r.end_text), sub(r.ended_at ? `持續 ${r.dur_text}` : `已持續 ${r.dur_text}`)])
}
Cell2.props = ['row', 'k']
Cell2.emits = ['open']

// 點監控項目＝明細（判斷依據、聯絡人、下一步按鈕）；同一列再點一次收起
const detail = ref<{ at: string; row: any } | null>(null)
function openDetail(at: string, row: any) { detail.value = detail.value?.at === at && detail.value.row?.id === row?.id ? null : { at, row } }
watch(() => route.fullPath, () => { detail.value = null })
const hasContact = (r: any) => ALERT_CONTACT_COLS.some(c => r[c.k] && r[c.k] !== '未提供')
const detailItems = (r: any): [string, string][] => ([
  ['監控項目', r.monitor], ['IP', r.ip], ['分級', r.tier_label], ['開始', r.start_text ? `${r.start_text}${r.since_note ? `・${r.since_text}` : ''}` : ''],
  ['恢復', r.end_text], ['持續', r.dur_text], ['判斷理由', r.judge_reason], ['業務系統', r.sys_text],
  ...(hasContact(r) ? ALERT_CONTACT_COLS.map(c => [c.label, r[c.k] || '未提供'] as [string, string]) : [['聯絡資料', '沒有登記'] as [string, string]]),
  ['通知', r.notified_text || '還沒通知'],
] as [string, string][]).filter(([, v]) => v != null && v !== '')

const notifyId = ref<number | null>(null)
const track = computed(() => data.value?.track)

// ===== 頁首一行結論＋要你處理（1.1 v3 規格）=====
const headline = computed(() => {
  const d = data.value
  if (!d) return ''
  const a = att.value
  const parts: string[] = []
  if (!a) parts.push(`告警${attCountText.value}`)
  else if (a.n) parts.push(`需要注意 ${a.n} 件${a.blind ? '，監控資料中斷' : ''}`)
  else if (a.blind) parts.push('監控看不到告警資料，0 件不代表沒事')
  else if (d.alerts.status !== 'ok') parts.push(`告警${attCountText.value}，無法確認有沒有需要注意的`)
  else parts.push('沒有需要注意的告警')
  if (d.alerts.today?.n != null) parts.push(`今天告警 ${d.alerts.today.n} 件`)
  const dc = dcheck.value
  if (dc?.bad) parts.push(`每日檢查異常 ${dc.bad} 列`)
  return parts.join('・')
})
const anchor = (id: string) => ({ path: route.path, query: route.query, hash: `#${id}` })
interface Todo { text: string; to: any; label: string }
const todos = computed<Todo[]>(() => {
  const d = data.value
  if (!d) return []
  const out: Todo[] = []
  const a = att.value
  const dc = dcheck.value
  if (teamsView.value.todo) out.push({ text: teamsView.value.todo, to: '/v2/settings?st=notify&sub=teams#log', label: '看投遞紀錄' })
  if (dcErr.value) out.push({ text: `每日檢查讀不到：${dcErr.value.replace('讀取失敗：', '')}`, to: '/v2/dailycheck', label: '去看' })
  if (dc?.state === 'open' && dc.unconfirmed) out.push({ text: `每日檢查還有 ${dc.unconfirmed} 列待確認`, to: '/v2/dailycheck', label: '去確認' })
  else if (dc?.bad) out.push({ text: `每日檢查有 ${dc.bad} 列異常`, to: '/v2/dailycheck', label: '看是哪幾台' })
  else if (dc?.state === 'none') out.push({ text: '今天的每日檢查還沒產生', to: '/v2/dailycheck', label: '去看' })
  if (dc?.lp_alert) out.push({ text: 'WhatsUp 輪循資料可能中斷，每日檢查的數字可能不完整', to: '/v2/dailycheck', label: '看原因' })
  const g = d.changes.collect_gap
  if (g?.n && g?.link) out.push({ text: `${g.n} 台收集中斷，今天的資產異動可能不完整`, to: g.link, label: '看是哪幾台' })
  if (d.opening.status === 'ok' && d.opening.hot) out.push({ text: `開盤尖峰 ${d.opening.hot} 台 ESXi 的 CPU 超過 ${d.opening.hot_pct ?? 80}%`, to: anchor('opening'), label: '看是哪幾台' })
  return out
})
// 數字卡：每張＝大數字＋一行灰字解讀；點了跳到對應頁籤或區塊
const cards = computed(() => {
  const d = data.value
  if (!d) return []
  const a = att.value
  const num = (n: number | null | undefined, unit: string, fallback: string) => (n == null ? { num: fallback, unit: '' } : { num: String(n), unit })
  const att0 = a ? { num: String(a.n), unit: '件' } : { num: attCountText.value, unit: '' }
  const tdy = d.alerts.today?.n == null ? { num: countText(d.alerts), unit: '' } : { num: String(d.alerts.today.n), unit: '件' }
  const inc = d.incidents.n == null ? { num: countText(d.incidents), unit: '' } : { num: String(d.incidents.n), unit: '件' }
  const chg = num(d.changes.n, '筆', countText(d.changes))
  const hol = d.opening.status === 'holiday'
  const op = d.opening.status === 'ok' ? { num: String(d.opening.hot ?? 0), unit: '台' } : { num: hol ? '今日休市' : d.opening.status === 'pending' ? '尚未匯入' : '無法計算', unit: '' }
  return [
    { k: 'att', label: '需要注意', ...att0, sub: a?.n ? `斷超過 ${a.min ?? 10} 分還沒恢復` : a ? (a.blind ? '監控看不到，0 不代表沒事' : '查過了，沒有') : '', tone: a?.n || a?.blind ? 'bad' : '', to: tabLink('att') },
    { k: 'today', label: '今天的告警', ...tdy, sub: d.alerts.today?.n == null ? '' : `仍未恢復 ${d.alerts.today.still_open}・已恢復 ${d.alerts.today.recovered}`, tone: '', to: tabLink('today') },
    { k: 'incidents', label: '今天的事件', ...inc, sub: d.incidents.n === 0 ? (staleSrc.value.length ? '有來源收集不完整，0 不代表沒事' : '查過了，沒有') : '', tone: d.incidents.n ? 'warn' : '', to: anchor('incidents') },
    { k: 'changes', label: '今天的資產異動', ...chg, sub: d.changes.prev_date ? `與 ${d.changes.prev_label || d.changes.prev_date} 比較` : '', tone: '', to: anchor('changes') },
    { k: 'opening', label: '開盤尖峰', ...op, sub: d.opening.status === 'ok' ? `CPU 超過 ${d.opening.hot_pct ?? 80}%` : hol ? '只影響開盤尖峰，監控照常' : d.opening.status === 'pending' ? '收盤後才有資料' : '請看開盤尖峰區', tone: hol || d.opening.status !== 'ok' ? 'dim' : d.opening.hot ? 'warn' : '', to: anchor('opening') },
  ]
})
// ===== 焦點卡：需要注意的告警最多 3 件。排序：分級（嚴重＞注意）→ 第一類系統優先 → 斷線久（規格 v3 第 3 點）=====
const focusAll = computed(() => sortFocus(attGroups.value as any[]))
const focus = computed(() => focusAll.value.slice(0, 3))
const focusMore = computed(() => Math.max(0, focusAll.value.length - 3))
const alertsOk = computed(() => data.value?.alerts.status === 'ok' && !!att.value)
const focusTone = computed(() => calcFocusTone(focusAll.value, data.value?.alerts.status, att.value))
const focusChip = computed(() => focusChipText(focusAll.value.length, att.value?.n))
const auxPending = computed(() => (!teams.value && !teamsErr.value) || (!relay.value && !relayErr.value))
const dcPending = computed(() => (!dcheck.value && !dcErr.value) || auxPending.value)
const dcInTodo = computed(() => todos.value.some(t => t.to === '/v2/dailycheck'))
const ownerText = (r: any) => { const v = r[ALERT_CONTACT_COLS[0].k]; return v && v !== '未提供' ? v : '負責人未提供' }
const showPrevOpening = ref(false)
// 資料日跟「現在」差一天以上＝隔夜沒更新（用每次載入後算的台北日期，不用頁面開啟當下的日期）
const isOldData = computed(() => !!data.value && data.value.date !== nowDay.value)
const okSrc = computed(() => allSources.value.filter(s => !s.state || EXPECTED_SOURCE_STATES.includes(s.state)).length)
const staleNames = computed(() => staleSrc.value.map(s => s.label).join('、'))
const cleanLabel = (t: any) => String(t || '').replace(/（[A-Za-z_]*(?:Error|Exception)）/g, '')
// ===== 告警頁籤（網址 ?tab= 記住；舊的 ?ab=／?ag= 下鑽照舊，會自動落在對應頁籤）=====
type TabKey = 'att' | 'today' | 'long' | 'routine'
const tab = computed<TabKey>(() => {
  const t = String(route.query.tab || '')
  if (!att.value) return 'att'
  if (t === 'att' || t === 'today' || t === 'long' || t === 'routine') return t
  if (abKey.value === 'longterm') return 'long'
  if (abKey.value) return 'routine'
  return 'att'
})
const tabLink = (k: TabKey) => { const { ab: _b, ag: _g, ...rest } = route.query as any; return router.resolve({ path: route.path, query: { ...rest, tab: k }, hash: '#alerts' }).fullPath }
function setTab(k: TabKey) { router.replace(tabLink(k)) }
const TAB_ORDER: TabKey[] = ['att', 'today', 'long', 'routine']
function tabKey(e: KeyboardEvent) {
  const i = TAB_ORDER.indexOf(tab.value)
  const to = e.key === 'ArrowRight' ? TAB_ORDER[(i + 1) % 4] : e.key === 'ArrowLeft' ? TAB_ORDER[(i + 3) % 4] : e.key === 'Home' ? TAB_ORDER[0] : e.key === 'End' ? TAB_ORDER[3] : null
  if (to) { e.preventDefault(); setTab(to); nextTick(() => document.getElementById(`alert-tab-${to}`)?.focus()) }
}
const showOpeningFull = ref(false)
const openingBars = computed(() => ((data.value?.opening as any)?.top?.rows ?? []).filter((r: any) => r.has_data).slice(0, 5))
const shortHost = (h: string) => String(h || '').split('.')[0]
const alertTabs = computed(() => [
  { k: 'att' as TabKey, label: '需要注意', n: att.value ? att.value.n : '—' },
  { k: 'today' as TabKey, label: '今天發生過', n: data.value?.alerts.today?.n ?? '—' },
  { k: 'long' as TabKey, label: '長期未恢復', n: longB.value ? longB.value.n : '—' },
  { k: 'routine' as TabKey, label: '例行與已自動恢復', n: routineN.value },
])
const INCIDENT_COLS = [{ k: 'title', label: '事件', link: (r: any) => r.link, min: 160 }, { k: 'state', label: '狀態' }, { k: 'started_at', label: '開始', text: (r: any) => String(r.started_at || '').slice(5, 16), mono: true }]
const INCIDENT_X = [{ k: 'type', label: '類型' }, { k: 'mode_label', label: '正式／演練' }, { k: 'what', label: '今天的變化', text: (r: any) => String(r.what || '').replace(/今日/g, '今天') }]
</script>

<template>
  <V2Page>
    <template #title><h1 class="h1">1.1 今日概況</h1></template>
    <template #conclusion>
      <span v-if="data" data-testid="today-headline">{{ headline }}
        <InfoNote>只放今天（台北時間 00:00 到現在）發生或變化的事。保固、單點風險、逾期未重開機、容量預警這類長期待辦在「追蹤處理」。<br>「0 件」＝來源有資料、今天真的沒有；「未收集」「未啟用」「資料中斷」＝來源沒有資料，不代表沒事。</InfoNote>
      </span>
    </template>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="data">
      <details class="srcs" data-testid="today-sources">
        <summary>資料更新於 <b class="mono">{{ isOldData ? String(data.now).slice(0, 16) : hm(data.now) }}</b><span v-if="isOldData" class="tag warn">不是今天的資料</span>・資料來源 <b class="mono">{{ okSrc }}／{{ allSources.length }}</b> 正常<span v-if="staleSrc.length" class="tag warn">{{ staleSrc.length }} 個來源沒更新：{{ staleNames }}</span>
          <NuxtLink v-if="staleSrc.length" class="lk" to="/v2/dailycheck?tab=sources">看哪一個</NuxtLink></summary>
        <div class="srcl">
          <span v-for="s in allSources" :key="s.key" class="src" :class="{ warn: isStaleSource(s) }">{{ s.label }} <b class="mono">{{ srcText(s) }}</b></span>
        </div>
      </details>

      <div class="top">
        <section class="focus" :class="focusTone" data-testid="today-focus">
          <div class="fh"><b>需要注意</b>
            <span v-if="focusAll.length" class="chip" data-testid="focus-chip">{{ focusChip }}</span>
          </div>
          <template v-if="focus.length">
            <p v-if="att?.blind" class="blind" role="alert" data-testid="focus-blind">{{ att.blind }}；下面是中斷前的狀態</p>
            <div v-for="r in focus" :key="r.id ?? r.group" class="fi">
              <b class="big mono" :class="tierTone(r)">{{ r.dur_text }}</b>
              <div class="fm">
                <div><b>{{ r.who || r.ip || '未知主機' }}</b> <span class="ip">{{ r.ip }}</span></div>
                <div>{{ r.monitor }}<span v-if="r.tier_label" class="tg" :class="tierTone(r)">{{ r.tier_label }}</span><span v-if="r.n > 1" class="tg">共 {{ r.n }} 筆</span></div>
                <div class="sub" :title="`${r.sys_text}・${ownerText(r)}`">{{ r.sys_text }}・{{ ownerText(r) }}</div>
              </div>
              <div class="fa">
                <button v-if="data.can_notify && r.n === 1" type="button" class="btn" @click="notifyId = r.id">{{ r.notified_n ? '重送通知' : '通知負責人' }}</button>
                <NuxtLink v-if="r.n > 1" class="btn" :to="groupLink(r)">看這 {{ r.n }} 筆</NuxtLink>
                <NuxtLink v-if="mlink(r)" class="btn ghost" :to="mlink(r)!">看主機</NuxtLink>
              </div>
            </div>
            <NuxtLink v-if="focusMore" class="more2" :to="tabLink('att')" data-testid="focus-more">另有 {{ focusMore }} 件，看全部 ›</NuxtLink>
          </template>
          <template v-else-if="att?.blind || !alertsOk">
            <p class="blind" role="alert" data-testid="focus-blind">{{ att?.blind || `告警資料目前無法確認${cleanLabel(data.alerts.label) && cleanLabel(data.alerts.label) !== attCountText ? `：${cleanLabel(data.alerts.label)}` : ''}` }}</p>
            <NuxtLink class="btn ghost fit" to="/v2/incident?tab=wug">看告警事件</NuxtLink>
          </template>
          <template v-else>
            <p class="okmsg" data-testid="focus-ok">沒有需要注意的告警</p>
            <details class="evid" data-testid="focus-evidence">
              <summary>看查了什麼</summary>
              <p>查了 WhatsUp 告警：斷線超過 {{ att?.min ?? 10 }} 分（開盤時段 {{ att?.open_min ?? 5 }} 分）、還沒恢復、不在例行時段內的。資料時間 {{ hm(data.alerts.as_of ?? data.now) }}。</p>
            </details>
          </template>
        </section>

        <section class="todo" :class="{ ok: !todos.length && !dcPending }" data-testid="today-todo">
          <div class="fh"><b>要你處理</b><span v-if="todos.length" class="chip">{{ todos.length }} 件</span></div>
          <p v-if="dcPending && !todos.length" class="none">確認中…</p>
          <p v-else-if="!todos.length" class="none">今天沒有新增要處理的事</p>
          <div v-for="(t, i) in todos" :key="i" class="it">
            <span>{{ t.text }}</span><span class="sp" />
            <NuxtLink class="btn ghost" :to="t.to">{{ t.label }}</NuxtLink>
          </div>
          <div class="thin">
            <NuxtLink to="/v2/track" class="tl">
              <span>長期待辦 <template v-if="track?.total != null">{{ track.total }} 項<template v-if="track.high">・高 {{ track.high }}</template></template><span v-else class="warnt">{{ track?.label || '無法計算' }}</span></span>
              <span class="go">看 ›</span>
            </NuxtLink>
            <NuxtLink v-if="track?.changes?.resolved" to="/v2/track?view=resolved" class="tl"><span>今天解除 {{ track.changes.resolved }} 件</span><span class="go">看 ›</span></NuxtLink>
            <div v-else-if="track?.changes" class="tl"><span>今天解除 {{ track.changes.resolved == null ? '無法計算' : '0 件' }}</span></div>
            <NuxtLink v-if="!teamsInTodo" to="/v2/settings?st=notify&sub=teams" class="tl" data-testid="teams-line">
              <span>Teams 通知 <span :class="{ warnt: teamsView.tone === 'warn' || teamsView.tone === 'bad', dimt: teamsView.tone === 'dim' }">{{ teamsView.text }}</span></span>
              <span class="go">看 ›</span>
            </NuxtLink>
            <NuxtLink v-if="!dcInTodo" to="/v2/dailycheck" class="tl" data-testid="dc-line">
              <span>每日檢查 <span :class="{ warnt: dcheck?.state === 'none' || !!dcErr }">{{ dcErr || dcheck?.text || '讀取中…' }}</span>
                <span v-if="!dcErr && dcheck?.state === 'none' && dcheck?.prev" class="prevnote" data-testid="dc-prev">{{ dcheck.prev.note }}：{{ dcheck.prev.text }}</span>
                <span v-if="!dcErr && dcheck?.lp_alert" class="badt" data-testid="dc-lp-alert">{{ dcheck.lp_alert }}</span>
                <InfoNote>各項主機當天的狀態（系統判定）；異常、無資料點進去看是哪幾台。</InfoNote></span>
              <span class="go">看 ›</span>
            </NuxtLink>
          </div>
        </section>
      </div>

      <div class="cards">
        <NuxtLink v-for="c in cards" :key="c.k" :to="c.to" class="card" :class="c.tone" :data-testid="`today-card-${c.k}`">
          <b class="mono">{{ c.num }}<small v-if="c.unit">{{ c.unit }}</small></b><span>{{ c.label }}</span><em v-if="c.sub">{{ c.sub }}</em></NuxtLink>
      </div>

      <section id="alerts" class="blk" data-testid="alerts-card">
        <div class="tabs" role="tablist" aria-label="今日告警" @keydown="tabKey">
          <button v-for="t in alertTabs" :id="`alert-tab-${t.k}`" :key="t.k" type="button" role="tab" class="tb" :class="{ on: tab === t.k }" :aria-selected="tab === t.k" :aria-controls="`alert-panel`" :tabindex="tab === t.k ? 0 : -1" :data-testid="`alert-tab-${t.k}`" @click="setTab(t.k)">{{ t.label }} <b class="mono">{{ t.n }}</b></button>
          <span class="sp" />
          <NuxtLink to="/v2/incident?tab=wug" class="more">全部告警 ›</NuxtLink>
        </div>

        <section v-if="tab === 'att'" id="alert-panel" role="tabpanel" aria-labelledby="alert-tab-att">
          <div class="bh"><span v-if="!att" class="cnt na" data-testid="att-count">{{ attCountText }}</span>
            <span v-if="attentionBlind(data.alerts as any)" class="tag bad" data-testid="att-blind-tag">監控看不到</span>
            <InfoNote>還沒恢復、斷線超過 {{ att?.min ?? 10 }} 分（開盤時段 {{ att?.open_min ?? 5 }} 分），而且不在這台例行時段內的告警；進來時推一次 Email／Teams，恢復時再推一次。其他的收在「例行與已自動恢復」。點「監控項目」看判斷依據與聯絡人；門檻與例行時段在主機頁「事件與維運紀錄」設定。</InfoNote>
          </div>
          <p v-if="!att?.blind && data.alerts.label" class="na-line">{{ cleanLabel(data.alerts.label) }}</p>
          <div v-if="scope" class="scope" data-testid="alert-scope">
            <span>{{ scope.mode === 'policy' ? '範圍：Teams 通知範圍' : '範圍：全部' }}</span>
            <span v-if="scope.mode === 'policy' && scope.hidden" class="sub">另有 {{ scope.hidden }} 件不在通知範圍</span>
            <button v-if="!showAll && scope.keywords.length" type="button" class="mini" data-testid="scope-all" @click="setScope(true)">看全部</button>
            <button v-else-if="showAll && scope.keywords.length" type="button" class="mini" data-testid="scope-policy" @click="setScope(false)">只看通知範圍</button>
            <InfoNote>{{ scope.label }}。通知範圍＝事件應變 › 告警事件 › 設定 的「只推這些動作政策」，Teams 只推這些；沒記錄有沒有掛政策的照樣算在範圍內（寧可多列）。「看全部」會把不在範圍的也列出來。</InfoNote>
          </div>
          <V2Table v-if="att" :rows="attGroups" prefix="A" :max="10" :empty="att.blind ? '監控看不到告警資料，0 件不代表沒事' : '目前沒有需要注意的告警'" :cols="ATT_COLS" :export-cols="ATT_X" export-name="今日概況_需要注意">
            <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('att', row)" /></template>
            <template #cell-notify="{ row }">
              <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify && row.n === 1" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
            </template>
          </V2Table>
          <div v-if="detail?.at === 'att'" class="pv" data-testid="alert-detail">
            <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
            <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
            <div class="acts">
              <button v-if="data.can_notify && detail.row.n === 1" type="button" class="btn" @click="notifyId = detail.row.id">{{ detail.row.notified_n ? '重送通知' : '通知負責人' }}</button>
              <NuxtLink v-if="detail.row.n > 1" class="btn ghost" :to="groupLink(detail.row)">看這 {{ detail.row.n }} 筆</NuxtLink>
              <NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink>
            </div>
          </div>
          <div v-if="agKey" class="drill" data-testid="alert-group-drill">
            <div class="bh"><b>{{ agGroup ? groupTitle(agGroup) : '這一組已經不在需要注意裡' }}</b>
              <NuxtLink :to="closeDrill" class="more">收起</NuxtLink></div>
            <V2Table v-if="agGroup" :rows="agRows" prefix="R" :max="20" empty="這一組目前沒有告警" :cols="AG_COLS" :export-cols="ATT_X" export-name="今日概況_同批告警">
              <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('ag', row)" /></template>
              <template #cell-notify="{ row }">
                <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
              </template>
            </V2Table>
            <div v-if="detail?.at === 'ag'" class="pv">
              <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
              <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
              <div class="acts">
                <button v-if="data.can_notify" type="button" class="btn" @click="notifyId = detail.row.id">{{ detail.row.notified_n ? '重送通知' : '通知負責人' }}</button>
                <NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink>
              </div>
            </div>
          </div>
        </section>

        <section v-else-if="tab === 'today'" id="alert-panel" role="tabpanel" aria-labelledby="alert-tab-today">
          <div class="bh"><span v-if="data.alerts.today?.n == null" class="cnt na">{{ countText(data.alerts) }}</span>
            <span v-if="data.alerts.today?.n != null" class="sub">仍未恢復 {{ data.alerts.today.still_open }}・已恢復 {{ data.alerts.today.recovered }}</span>
            <InfoNote>今天 00:00 以後開始的告警，含已經恢復的。件數＝下面的列數。點「監控項目」看分級與聯絡人。</InfoNote>
          </div>
          <p v-if="data.alerts.today?.label" class="na-line">{{ cleanLabel(data.alerts.today.label) }}</p>
          <V2Table v-if="data.alerts.today?.n != null" :rows="todayAlertRows" prefix="D" :max="10" empty="今天沒有發生告警" :cols="TODAY_COLS" :export-cols="TODAY_X">
            <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('today', row)" /></template>
            <template #cell-notify="{ row }">
              <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
            </template>
          </V2Table>
          <div v-if="detail?.at === 'today'" class="pv">
            <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
            <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
            <div class="acts">
              <button v-if="data.can_notify" type="button" class="btn" @click="notifyId = detail.row.id">{{ detail.row.notified_n ? '重送通知' : '通知負責人' }}</button>
              <NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink>
            </div>
          </div>
        </section>

        <section v-else-if="tab === 'long'" id="alert-panel" role="tabpanel" aria-labelledby="alert-tab-long" data-testid="att-longterm">
          <div class="bh"><span class="sub" data-testid="att-long-n">斷超過 {{ att?.long_h }} 小時還沒恢復，不算在需要注意</span>
            <InfoNote>斷超過 {{ att?.long_h }} 小時還沒恢復的移到這裡，不算在需要注意、不再推；推過「發生」的恢復時照樣推恢復。每日彙整只寫一行「長期未恢復 N 台」。小時數由管理員在主機頁「事件與維運紀錄」設定。</InfoNote></div>
          <V2Table :rows="longRows" prefix="L" :max="20" empty="沒有長期未恢復的告警" :cols="BUCKET_COLS" :export-cols="BUCKET_X" export-name="今日概況_長期未恢復">
            <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('long', row)" /></template>
          </V2Table>
          <div v-if="detail?.at === 'long'" class="pv">
            <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
            <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
            <div class="acts"><NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink></div>
          </div>
        </section>

        <section v-else id="alert-panel" role="tabpanel" aria-labelledby="alert-tab-routine" data-testid="att-routine">
          <div class="bh"><span class="sub">反覆斷線 {{ flapRows.length }} 台<template v-if="att?.suggestions">・待確認的例行時段 {{ att.suggestions }} 筆</template></span></div>
          <div class="tiles">
            <NuxtLink v-for="b in routineBuckets" :key="b.key" :to="bucketLink(b.key)" class="tile" :class="{ on: abKey === b.key }" :data-testid="`att-b-${b.key}`">
              <b class="mono">{{ b.n }}</b><span>{{ b.label }}</span></NuxtLink>
            <NuxtLink :to="bucketLink('flap')" class="tile" :class="{ on: abKey === 'flap' }" data-testid="att-b-flap"><b class="mono">{{ flapRows.length }} 台</b><span>反覆斷線</span></NuxtLink>
            <NuxtLink :to="bucketLink('suggest')" class="tile" :class="{ on: abKey === 'suggest' }" data-testid="att-b-suggest"><b class="mono">{{ att?.suggestions == null ? '—' : `${att.suggestions} 筆` }}</b><span>待確認的例行時段</span></NuxtLink>
            <InfoNote>未滿門檻仍斷：還在等，滿 {{ att?.min }} 分沒恢復就會列進需要注意。監控明細讀不到：收集問題，不是設備故障。例行時段內：管理員登記（或確認建議）的每天固定時段，時段結束還沒恢復就列進需要注意。門檻內自動恢復、例行時段內恢復、曾需注意已恢復：今天恢復的。反覆斷線：今天同一台斷了 3 次以上，不列需要注意，每日彙整寫一行「今天斷了 N 次」。待確認的例行時段：最近 14 天同一台同一時刻（前後 15 分）有 10 天以上斷過，管理員確認才生效，到主機頁確認或拒絕。</InfoNote>
          </div>
          <template v-if="abBucket">
            <V2Table :rows="abRows" prefix="B" :max="20" empty="這一類目前沒有告警" :cols="BUCKET_COLS" :export-cols="BUCKET_X" :export-name="`今日概況_${abBucket.label}`">
              <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('bucket', row)" /></template>
            </V2Table>
            <div v-if="detail?.at === 'bucket'" class="pv">
              <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
              <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
              <div class="acts"><NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink></div>
            </div>
          </template>
          <V2Table v-else-if="abKey === 'flap'" :rows="flapRows" prefix="F" :max="20" empty="今天沒有反覆斷線的主機" :cols="FLAP_COLS" :export-cols="[X_IP]" export-name="今日概況_反覆斷線">
            <template #cell-who="{ row }"><span class="c2"><b>{{ row.who || row.ip }}</b><span class="sub">{{ row.ip }}</span></span></template>
          </V2Table>
          <template v-else-if="abKey === 'suggest'">
            <p v-if="sugErr" class="err">{{ sugErr }}</p>
            <V2Table v-else-if="sugRows" :rows="sugRows" prefix="S" :max="20" empty="目前沒有待確認的例行時段" :cols="SUG_COLS" export-name="今日概況_待確認的例行時段" />
            <p v-else class="sub">讀取中…</p>
          </template>
        </section>
        <V2AlertNotify :event-id="notifyId" @close="notifyId = null" @sent="load" />
      </section>

      <div class="row4">
        <section id="incidents" class="blk">
          <div class="bh"><h2>今天的事件</h2><span class="cnt" :class="{ na: data.incidents.n == null }">{{ countText(data.incidents) }}</span>
            <NuxtLink to="/v2/incident" class="more">看全部 ›</NuxtLink>
            <InfoNote>事件應變裡今天開立或今天結案的事件。還沒結案的舊事件在「追蹤處理」的「進行中事件」。</InfoNote>
          </div>
          <p v-if="data.incidents.label" class="na-line">{{ cleanLabel(data.incidents.label) }}</p>
          <p v-if="data.incidents.n === 0 && staleSrc.length" class="na-line" data-testid="inc-zero-warn">{{ staleNames }} 收集不完整，0 不代表沒事</p>
          <V2Table v-if="data.incidents.n != null && data.incidents.n > 0" :rows="data.incidents.rows" prefix="E" :max="5" empty="今天沒有事件" :cols="INCIDENT_COLS" :export-cols="INCIDENT_X" export-name="今日概況_今天的事件" />
          <details v-else-if="data.incidents.n === 0" class="evid" data-testid="inc-zero">
            <summary>今天沒有事件・看查了什麼</summary>
            <p>查了 4.1 事件應變：今天 00:00 起開立或結案的事件；資料時間 {{ hm(data.incidents.as_of ?? data.now) }}。<NuxtLink to="/v2/incident">事件應變 ›</NuxtLink></p>
          </details>
        </section>

        <section id="changes" class="blk">
          <div class="bh"><h2>今天的資產異動</h2><span class="cnt" :class="{ na: data.changes.n == null || data.changes.status === 'stale' }">{{ countText(data.changes) }}</span>
            <NuxtLink to="/v2/changes?tab=daily" class="more">看全部 ›</NuxtLink>
            <InfoNote>今天那份每日快照跟前一個工作日快照的比對（週末、休市日跳過；前一個工作日沒有快照就不比，寫明是哪一天沒有。跟「異動與異常」的每日異動同一份計算，點分類看明細）。今天還沒產生快照寫「未收集」；兩份快照之間掃描、收集、匯入都沒有新資料時，比出來的數字不代表沒有異動，會標出來。<template v-if="data.changes.as_of">快照 {{ data.changes.as_of }}<template v-if="data.changes.prev_date">，與 {{ data.changes.prev_label || data.changes.prev_date }} 比較</template>。</template></InfoNote>
          </div>
          <p v-if="data.changes.compare?.holiday_note" class="na-line" data-testid="chg-holiday-note">{{ data.changes.compare.holiday_note }}</p>
          <p v-if="data.changes.label" class="na-line"><template v-for="(p, i) in gapParts(data.changes)" :key="i"><NuxtLink v-if="p.link" :to="p.link" data-testid="chg-gap-link">{{ p.t }}</NuxtLink><template v-else>{{ p.t }}</template></template></p>
          <V2Table v-if="data.changes.n != null" :rows="data.changes.rows" prefix="C" :max="5" empty="今天沒有資產異動"
                   :cols="[{ k: 'label', label: '分類', link: (r: any) => r.link }, { k: 'n', label: '筆數', mono: true, link: (r: any) => r.link }]" />
        </section>

        <section id="opening" class="blk">
          <div class="bh"><h2>{{ data.opening.status !== 'ok' && data.opening.status !== 'holiday' && data.opening.prev ? `開盤尖峰 ${data.opening.prev.day}` : '開盤尖峰 09:00–09:30' }}</h2>
            <span v-if="data.opening.status !== 'ok'" class="cnt na">{{ data.opening.status === 'pending' ? '尚未匯入' : data.opening.status === 'holiday' ? '今日休市' : '無法計算' }}</span>
            <span v-else class="sub">收盤後才有資料</span>
            <InfoNote>ESXi 主機開盤時段（09:00～09:30）的 CPU 使用率最高／平均／最低，依最高排序，與該主機近 20 個工作日比較。資料由跳板機收盤後從 Aria Operations 匯出送進來，所以收盤前這一區不放數字。休市日只影響開盤尖峰，監控照常。<template v-if="data.opening.last_day">最近一次收到的是 {{ data.opening.last_day }}。</template></InfoNote>
          </div>
          <template v-if="data.opening.status === 'holiday'">
            <p class="zero" data-testid="op-holiday">今日休市，只影響開盤尖峰，監控照常
              <button v-if="data.opening.prev" type="button" class="lb" data-testid="op-prev-btn" @click="showPrevOpening = !showPrevOpening">{{ showPrevOpening ? '收起' : '看上一個開盤日' }}</button></p>
            <template v-if="data.opening.prev && showPrevOpening">
              <p class="prevnote" data-testid="op-prev">這是 {{ data.opening.prev.day }} 的資料，收到 {{ String(data.opening.prev.collected_at || '').slice(0, 16) || '—' }}</p>
              <V2OpeningPeak :data="data.opening.prev" />
            </template>
          </template>
          <template v-else-if="data.opening.status !== 'ok' && data.opening.prev">
            <p class="prevnote" data-testid="op-prev">這是 {{ data.opening.prev.day }} 的資料，收到 {{ String(data.opening.prev.collected_at || '').slice(0, 16) || '—' }}；{{ data.opening.status === 'pending' ? '今日尚未匯入' : '今日無法計算' }}</p>
            <V2OpeningPeak :data="data.opening.prev" />
          </template>
          <template v-else>
            <div v-if="openingBars.length" class="bars" data-testid="op-bars">
              <div v-for="r in openingBars" :key="r.host" class="bar" :title="`${r.host} 最高 ${r.max}%（${r.max_at}）`">
                <span class="bn">{{ shortHost(r.host) }}</span>
                <span class="bt"><i :class="{ hot: r.max > (data.opening.hot_pct ?? 80) }" :style="{ width: `${Math.min(100, Number(r.max))}%` }" /></span>
                <b class="mono" :class="{ hotn: r.max > (data.opening.hot_pct ?? 80) }">{{ r.max }}%</b>
              </div>
            </div>
            <p v-else class="zero">這一批沒有可用的 CPU 資料</p>
            <button type="button" class="lb" data-testid="op-full-btn" @click="showOpeningFull = !showOpeningFull">{{ showOpeningFull ? '收起明細' : '看全部明細' }}</button>
            <V2OpeningPeak v-if="showOpeningFull" :data="data.opening" />
          </template>
        </section>
      </div>

      <div class="quick">
        <NuxtLink to="/v2/incident" class="qb danger"><b>事件應變</b><span>停機影響評估、通報聯絡、檢核清單</span></NuxtLink>
        <NuxtLink to="/v2/onboard" class="qb"><b>納管作業</b><span>新設備納管與收集</span></NuxtLink>
        <NuxtLink to="/v2/datafix" class="qb"><b>待補資料</b><span>CIA 缺漏與待更正</span></NuxtLink>
      </div>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.err { color: var(--bad); }
.mono { font-family: var(--disp); }
.srcs { font-size: 13px; color: var(--ink-soft); margin-top: -8px; }
.srcs summary { cursor: pointer; color: var(--ink-aux); }
.srcs .lk { margin-left: 8px; color: var(--link); }
.srcl { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; margin-top: 6px; }
.src.warn b { color: var(--warn-text); }
.tag { display: inline-block; margin-left: 8px; padding: 0 8px; border-radius: 999px; font-size: 12px; white-space: nowrap; }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.tag.bad { background: var(--bad-soft); color: var(--bad); margin-left: 0; }
.sp { flex: 1; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 14px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; display: inline-block; white-space: nowrap; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.btn.fit { align-self: flex-start; }
/* 第一列：焦點卡＋要你處理 */
.top { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; align-items: stretch; }
@media (max-width: 1100px) { .top { grid-template-columns: 1fr; } }
.focus, .todo { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 18px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.focus { border-left: 5px solid var(--bad); }
.focus.warn { border-left-color: var(--warn); }
.focus.ok { border-left-color: var(--good); background: var(--good-soft); }
.fh { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.fh b { font-size: 16px; }
.chip { font-size: 13px; color: var(--ink-aux); }
.fi { display: grid; grid-template-columns: 150px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 8px 0; border-top: 1px solid var(--line); }
.fi:first-of-type { border-top: 0; }
.fi .big { font-size: 24px; font-weight: 700; color: var(--ink-soft); line-height: 1.1; }
.fi .big.bad { color: var(--bad); }
.fi .big.warn { color: var(--warn-text); }
.fm { display: flex; flex-direction: column; gap: 2px; min-width: 0; font-size: 14px; }
.fm .ip { font-size: 12px; color: var(--ink-aux); }
.fm .sub { font-size: 12.5px; color: var(--ink-aux); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tg { display: inline-block; margin-left: 8px; padding: 0 8px; border-radius: 999px; font-size: 12px; background: var(--sub); color: var(--ink-soft); }
.tg.bad { background: var(--bad-soft); color: var(--bad); }
.tg.warn { background: var(--warn-soft); color: var(--warn-text); }
.fa { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.more2 { font-size: 13px; color: var(--link); text-decoration: underline; }
.okmsg { margin: 0; font-size: 18px; font-weight: 700; color: var(--good); }
.evid { font-size: 13px; color: var(--ink-soft); }
.evid summary { cursor: pointer; color: var(--link); }
.evid p { margin: 6px 0 0; }
.todo { border-left: 5px solid var(--warn); }
.todo.ok { border-left-color: var(--good); }
.todo .none { margin: 0; font-size: 14px; color: var(--ink-soft); }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 6px 0; border-top: 1px solid var(--line); font-size: 14px; }
.thin { margin-top: auto; display: flex; flex-direction: column; gap: 0; }
.tl { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 7px 0; border-top: 1px dashed var(--line); color: var(--ink); text-decoration: none; font-size: 13.5px; }
.tl:hover .go { text-decoration: underline; }
.tl .go { color: var(--link); white-space: nowrap; }
.warnt { color: var(--warn-text); }
.dimt { color: var(--muted); }
.badt { color: var(--bad); font-size: 13px; margin-left: 8px; }
.prevnote { margin: 0 0 6px; color: var(--warn-text); font-size: 13px; }
/* 第二列：數字卡 */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.card { display: flex; flex-direction: column; gap: 2px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px; color: var(--ink); text-decoration: none; }
.card:hover { border-color: var(--brand); }
.card b { font-size: 26px; line-height: 1.1; }
.card b small { font-size: 14px; font-weight: 600; margin-left: 3px; color: var(--ink-soft); }
.card span { font-size: 13.5px; color: var(--ink); }
.card em { font-style: normal; font-size: 12.5px; color: var(--ink-aux); }
.card.bad b { color: var(--bad); }
.card.warn b { color: var(--warn-text); }
.card.dim b { color: var(--ink-aux); font-size: 18px; }
/* 第三列：告警頁籤卡 */
.blk { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 18px; display: flex; flex-direction: column; gap: 10px; min-width: 0; scroll-margin-top: 16px; }
.tabs { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; border-bottom: 1px solid var(--line); }
.tb { background: none; border: 0; border-bottom: 3px solid transparent; padding: 8px 14px; font: inherit; font-size: 14px; color: var(--ink-soft); cursor: pointer; margin-bottom: -1px; }
.tb b { margin-left: 4px; }
.tb.on { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 700; }
.bh { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.bh h2 { margin: 0; font-size: 16px; }
.cnt { font-family: var(--disp); font-weight: 700; color: var(--brand-dark); }
.cnt.na { color: var(--warn-text); font-weight: 600; font-family: inherit; }
.sub { font-size: 13px; color: var(--ink-aux); }
.more { margin-left: auto; font-size: 13px; color: var(--link); text-decoration: none; }
.na-line { margin: 0; padding: 8px 10px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.blind { margin: 0; padding: 8px 10px; border-radius: 8px; background: var(--bad-soft); color: var(--bad); font-weight: 600; font-size: 14px; }
.zero { margin: 0; font-size: 13.5px; color: var(--ink-soft); }
.zero a { margin-left: 8px; color: var(--link); }
.tiles { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }
.tile { display: flex; flex-direction: column; align-items: center; min-width: 96px; padding: 6px 12px; border-radius: 12px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink); text-decoration: none; font-size: 13px; }
.tile b { font-size: 20px; }
.tile.on { outline: 2px solid var(--brand); }
.scope { margin: 0; display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; font-size: 13px; color: var(--ink-soft); }
.drill { border-top: 1px dashed var(--line); padding-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.nt { display: inline-flex; gap: 6px; align-items: center; }
.mini { font-family: inherit; font-size: 12px; padding: 1px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.c2 { display: flex; flex-direction: column; }
.c2 b { color: var(--ink); }
.pv { border: 1px solid var(--brand); border-radius: 12px; padding: 10px 14px; background: var(--card); display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.pvh { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.pv dl { display: grid; grid-template-columns: 110px 1fr; gap: 4px 12px; margin: 0; font-size: 13px; }
.pv dt { color: var(--ink-aux); }
.pv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font: inherit; font-size: 13px; padding: 0; text-decoration: underline; }
/* 第四列 */
.row4 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; align-items: start; }
@media (max-width: 1280px) { .row4 { grid-template-columns: 1fr; } }
.quick { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 760px) { .quick { grid-template-columns: 1fr; } }
.qb { text-decoration: none; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; color: var(--ink); }
.qb b { font-size: 16px; }
.qb span { font-size: 13px; color: var(--ink-aux); }
.qb:hover { border-color: var(--brand); }
.qb.danger { border-left: 4px solid var(--v2-danger); }
.bars { display: flex; flex-direction: column; gap: 8px; }
.bar { display: grid; grid-template-columns: 110px minmax(0, 1fr) 52px; gap: 10px; align-items: center; font-size: 13px; }
.bar .bn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
.bar .bt { height: 10px; border-radius: 999px; background: var(--sub); overflow: hidden; }
.bar .bt i { display: block; height: 100%; border-radius: 999px; background: var(--good); }
.bar .bt i.hot { background: var(--bad); }
.bar b { text-align: right; }
.bar b.hotn { color: var(--bad); }
</style>

<style>
/* Cell2（render function）不吃 scoped 屬性，類名前綴 t11- 避免外溢 */
.t11-c2 { display: flex; flex-direction: column; gap: 1px; }
.t11-c2 b { color: var(--ink); font-weight: 700; }
.t11-sub { font-size: 12px; color: var(--ink-aux); }
.t11-tag { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 11.5px; font-weight: 600; }
.t11-lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-align: left; text-decoration: underline dotted; }
</style>
