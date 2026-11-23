// 夜間速查（今晚通知清單 N3／緊急檢查 N4／月報夜間段 N6）的型別與純函式：排序、狀態判斷、畫面文字、API 呼叫。
// 全部不依賴 Nuxt 自動匯入（apiFetch 由呼叫端傳入），vitest 可以直接測。
// 介面依「夜間速查 介面約定」：時間都是台北時間字串 YYYY-MM-DD HH:MM:SS；查不到＝null（附原因），[]／0＝查過、真的沒有。

import type { ApiFetch } from './useNightLive'

// ===== 1. 今晚通知清單 =====
export interface NightSystem { api_id: string; name: string | null; tier: string | null; env: string | null }
export interface NightHandling { id: number; by: string; at: string; result: string | null; done_at: string | null }
export interface NotifiedItem {
  id: string
  kind: string
  notified_at: string
  title: string
  detail: string | null
  machine_key: string | null
  name: string | null
  ip: string | null
  platform: string | null
  open: boolean
  recovered_at: string | null
  times_tonight: number
  system: NightSystem | null
  impact: { hosts: number | null; systems: number | null; big: boolean; reason: string | null } | null
  handling: NightHandling | null
}
export interface NotifiedData {
  now: string
  hours: number
  data_at: string | null
  summary: { total: number; open: number; tier1: number; big_impact: number }
  items: NotifiedItem[]
  older: { count: number; link: string } | null
}

export const isTier1 = (s: NightSystem | null | undefined) => !!s && /第一類|^1$|一類/.test(String(s.tier ?? ''))

/** 預設排序：未恢復在上，再依第一類（交易系統）、影響範圍大、通知時間新到舊。項次照這個順序編、之後固定。 */
export function sortNotified(items: NotifiedItem[]): NotifiedItem[] {
  const rank = (x: NotifiedItem) => [x.open ? 0 : 1, isTier1(x.system) ? 0 : 1, x.impact?.big ? 0 : 1]
  return [...items].sort((a, b) => {
    const ra = rank(a); const rb = rank(b)
    for (let i = 0; i < ra.length; i++) if (ra[i] !== rb[i]) return ra[i] - rb[i]
    return String(b.notified_at).localeCompare(String(a.notified_at))
  })
}

/** 頁首一句結論。0 件寫「今晚沒有通知你」。 */
export function headline(s: NotifiedData['summary'] | null | undefined): string {
  if (!s || !s.total) return '今晚沒有通知你'
  return `今晚通知你 ${s.total} 件・交易系統 ${s.tier1} 件・影響範圍大 ${s.big_impact} 件`
}

/** 「HH:MM」；跨日（不是 now 那天）就帶月日。 */
export function hhmm(t: string | null | undefined, now?: string | null): string {
  if (!t) return '—'
  const s = String(t)
  const time = s.slice(11, 16)
  if (now && s.slice(0, 10) !== String(now).slice(0, 10)) return `${s.slice(5, 10)} ${time}`
  return time || s
}

const PLATFORM: Record<string, string> = { windows: 'Windows', linux: 'Linux', aix: 'AIX', esxi: 'ESXi', vcenter: 'vCenter', hpux: 'HP-UX', solaris: 'Solaris' }
export const platformLabel = (p: string | null | undefined) => (p ? PLATFORM[String(p).toLowerCase()] ?? String(p) : '平台不明')

/** VMware 設備（ESXi／vCenter）：天條，不做即時檢測，連按鈕都不出。 */
export const isVmwarePlatform = (p: string | null | undefined) => /esxi|vcenter|vmware|vcsa/i.test(String(p ?? ''))

/** 「它現在」：還沒恢復／已恢復幾點，加今晚第幾次。 */
export function nowText(x: NotifiedItem, now?: string | null): { tag: string; tone: 'bad' | 'good'; sub: string } {
  const times = x.times_tonight > 1 ? `今晚 ${x.times_tonight} 次` : ''
  if (x.open) return { tag: '還沒恢復', tone: 'bad', sub: times }
  return { tag: '已恢復', tone: 'good', sub: [x.recovered_at ? `${hhmm(x.recovered_at, now)} 恢復` : '', times].filter(Boolean).join('・') }
}

/** 「影響」：大範圍寫台數＋要通知主管；否則寫系統與分級；都沒有就照實說。 */
export function impactText(x: NotifiedItem): { main: string; sub: string; big: boolean } {
  const im = x.impact
  if (im?.big) return { main: `${im.hosts ?? '—'} 台・${im.systems ?? '—'} 個系統`, sub: '要通知主管', big: true }
  if (x.system) return { main: x.system.name || x.system.api_id, sub: [x.system.tier, x.system.env].filter(Boolean).join('・'), big: false }
  if (im && im.reason) return { main: '算不出', sub: im.reason, big: false }
  return { main: '沒有登記業務系統', sub: '', big: false }
}

export type HandleState = 'none' | 'doing' | 'done'
export function handleState(h: NightHandling | null | undefined): HandleState {
  if (!h) return 'none'
  return h.done_at ? 'done' : 'doing'
}
export function handleText(h: NightHandling | null | undefined, now?: string | null): { main: string; sub: string } {
  const st = handleState(h)
  if (st === 'none' || !h) return { main: '還沒有人處理', sub: '' }
  if (st === 'done') return { main: `${h.by} 已處理`, sub: h.result ? h.result : `${hhmm(h.done_at, now)} 結案` }
  return { main: `${h.by} 處理中`, sub: `${hhmm(h.at, now)} 起` }
}

/** 要你處理：還沒恢復、也還沒有人接手的。 */
export const needsYou = (items: NotifiedItem[]) => items.filter(x => x.open && handleState(x.handling) === 'none')

/** 這台今晚的通知（緊急檢查頁頂端用）：machine_key 或 IP 對得上就算。 */
export function itemsOfHost(items: NotifiedItem[], key: string): NotifiedItem[] {
  const k = String(key ?? '').trim()
  if (!k) return []
  return sortNotified(items.filter(x => x.machine_key === k || x.ip === k))
}

// ===== 2. 緊急檢查 =====
export type AliveState = 'alive' | 'rebooted' | 'unreachable' | 'unknown'
export interface BaselineChange { level: 'red' | 'amber'; what: string; since: string | null }
export interface Baseline { status: 'ok' | 'insufficient' | 'none' | 'error'; days: number | null; reason: string | null; changes: BaselineChange[]; same_count: number | null; same_brief: string | null }
export interface NightContact { role: string; name: string | null; phone: string | null; state: 'ok' | 'blank' | 'no_source' }
export interface HistoryDay { day: string; state: string; items: { name: string; what: string }[] }
export interface HostCheck {
  key: string; name: string | null; ip: string | null; platform: string | null
  alive: { state: AliveState; uptime_days: number | null; booted_at: string | null; source: string | null; at: string | null; text: string | null } | null
  baseline: Baseline | null
  impact: { hosts: number | null; systems: NightSystem[]; big: boolean; vms: { name: string; api_id: string | null; power: string | null }[]; vm_data_at: string | null; reason: string | null } | null
  contacts: NightContact[] | null
  history: HistoryDay[] | null
  events_tonight: { at: string; what: string; recovered_at: string | null }[] | null
}

/** 結論列：活著／剛重開／連不上／不知道。文字以後端為準，後端沒給才用預設詞；不知道就說不知道，不猜。 */
export function verdict(a: HostCheck['alive']): { tone: 'ok' | 'boot' | 'down' | 'unknown'; icon: string; text: string; src: string } {
  const src = a ? [a.at ? hhmm(a.at) : '', a.source ?? ''].filter(Boolean).join('・') : ''
  if (!a) return { tone: 'unknown', icon: '❔', text: '不知道它現在的狀態：沒有檢測或監控資料', src }
  const def: Record<AliveState, string> = { alive: '活著', rebooted: '剛重開機', unreachable: '連不上', unknown: '不知道它現在的狀態' }
  const tone = ({ alive: 'ok', rebooted: 'boot', unreachable: 'down', unknown: 'unknown' } as const)[a.state] ?? 'unknown'
  const icon = ({ ok: '✅', boot: '⚠', down: '❌', unknown: '❔' } as const)[tone]
  return { tone, icon, text: a.text || def[a.state] || def.unknown, src }
}

/** 跟平常比：ok 才列變化；基準不足／沒有基準／比對失敗都寫原因，不猜。紅色排在琥珀前面。 */
export function baselineView(b: Baseline | null | undefined): { kind: 'ok' | 'same' | 'short' | 'fail'; msg: string; changes: BaselineChange[]; sameCount: number; sameBrief: string } {
  if (!b) return { kind: 'fail', msg: '比對失敗：沒有拿到比對結果', changes: [], sameCount: 0, sameBrief: '' }
  const sameCount = b.same_count ?? 0
  const sameBrief = b.same_brief ?? ''
  if (b.status === 'insufficient' || b.status === 'none') return { kind: 'short', msg: `基準不足：${b.reason || (b.status === 'none' ? '沒有平常的資料可以比' : '平常的資料不夠')}`, changes: [], sameCount, sameBrief }
  if (b.status === 'error') return { kind: 'fail', msg: `比對失敗：${b.reason || '原因不明'}`, changes: [], sameCount, sameBrief }
  const changes = [...(b.changes ?? [])].sort((x, y) => (x.level === y.level ? 0 : x.level === 'red' ? -1 : 1))
  if (!changes.length) return { kind: 'same', msg: '跟平常一樣，沒有變化', changes, sameCount, sameBrief }
  return { kind: 'ok', msg: '', changes, sameCount, sameBrief }
}

/** 找誰：可撥的電話、或照實寫「查不到」「有登記但沒填」。 */
export function contactView(c: NightContact): { name: string; phone: string; tel: string; note: string } {
  if (c.state === 'no_source') return { name: c.name || '', phone: '', tel: '', note: '查不到' }
  if (c.state === 'blank' || !c.phone || !/\d{3,}/.test(c.phone)) return { name: c.name || '', phone: '', tel: '', note: '有登記但沒填' }
  return { name: c.name || '', phone: c.phone, tel: 'tel:' + c.phone.replace(/[^0-9+#*,;]/g, ''), note: '' }
}

const DAY_TONE: Record<string, 'ok' | 'warn' | 'bad' | 'gray'> = { 正常: 'ok', 異常: 'bad', 沒收到資料: 'gray', 檢查失敗: 'warn' }
export const dayTone = (state: string) => DAY_TONE[state] ?? 'gray'

function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}
/** 台北時間的今天（YYYY-MM-DD）。 */
export function taipeiToday(at: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' }).format(at)
}
/** 最近 N 天每天一格（舊到新），後端沒給的那天＝沒收到資料，不留空白。 */
export function fillHistory(h: HistoryDay[] | null | undefined, today: string, days = 7): (HistoryDay & { tone: string; brief: string })[] {
  const by = new Map((h ?? []).map(x => [x.day, x]))
  const out: (HistoryDay & { tone: string; brief: string })[] = []
  for (let i = days - 1; i >= 0; i--) {
    const day = addDays(today, -i)
    const x = by.get(day) ?? { day, state: '沒收到資料', items: [] }
    const brief = x.state === '異常' && x.items?.length ? x.items.map(it => it.what || it.name).join('、') : x.state
    out.push({ ...x, tone: dayTone(x.state), brief })
  }
  return out
}
/** 老毛病：最近幾天裡有幾天異常。 */
export const badDays = (rows: { state: string }[]) => rows.filter(r => r.state === '異常').length

/** 即時檢測回來挑哪一筆：連得到的優先，否則第一筆。 */
export const pickCheckItem = (items: any[] | null | undefined) => (items ?? []).find((x: any) => x?.reachable) ?? (items ?? [])[0] ?? null

// ===== 3. API =====
export const fetchNotified = (apiFetch: ApiFetch, hours?: number) =>
  apiFetch<NotifiedData>('/api/v2/night/notified', { query: hours ? { hours } : {} })
export const fetchHostCheck = (apiFetch: ApiFetch, key: string) =>
  apiFetch<HostCheck>('/api/v2/night/host', { query: { key } })
export const postSnapshot = (apiFetch: ApiFetch, key: string, result: any) =>
  apiFetch<{ id: number; baseline: Baseline }>('/api/v2/night/check-snapshot', { method: 'POST', body: { key, result } })
export const postHandle = (apiFetch: ApiFetch, itemId: string, key: string) =>
  apiFetch<{ id: number; by: string; at: string }>('/api/v2/night/handle', { method: 'POST', body: { item_id: itemId, key } })
export const postHandleResult = (apiFetch: ApiFetch, id: number, result: string) =>
  apiFetch<any>(`/api/v2/night/handle/${id}/result`, { method: 'POST', body: { result } })

/** 寫入失敗的白話：登入停用＝403。 */
export function writeErr(e: any): string {
  const s = e?.statusCode ?? e?.response?.status
  const d = e?.data?.detail
  if (s === 403 && !d) return '沒有權限（登入停用時不能寫入）'
  return typeof d === 'string' ? d : s ? `伺服器回 ${s}` : (e?.message ?? '未知錯誤')
}

// ===== 4. 月報「本月夜間通知與處理」 =====
export interface MonthNight { count: number | null; reason?: string | null; hosts: { name: string | null; ip: string | null; times: number; first_handle_min: number | null; handled?: boolean; result: string | null }[]; unhandled: number }
export function minText(m: number | null | undefined, handled = false): string {
  if (m === null || m === undefined) return handled ? '有人處理，時間算不出' : '沒人處理'
  if (m < 60) return `${Math.round(m)} 分`
  const h = Math.floor(m / 60); const r = Math.round(m % 60)
  return r ? `${h} 小時 ${r} 分` : `${h} 小時`
}
export function monthNightView(n: MonthNight | null | undefined): { state: 'missing' | 'empty' | 'ok'; msg: string; rows: any[] } {
  if (!n) return { state: 'missing', msg: '本月夜間通知讀不到', rows: [] }
  if (n.count === null || n.count === undefined) return { state: 'missing', msg: n.reason ? `本月夜間通知讀不到：${n.reason}` : '本月夜間通知讀不到', rows: [] }
  if (n.count === 0) return { state: 'empty', msg: '本月沒有夜間通知', rows: [] }
  const rows = (n.hosts ?? []).map((h, i) => ({ ...h, __no: i + 1, name_t: h.name || h.ip || '—', handle_t: minText(h.first_handle_min, h.handled === true), result_t: h.result || (h.handled === true || h.first_handle_min != null ? '沒寫處理結果' : '沒人處理') }))
  return { state: 'ok', msg: '', rows }
}
