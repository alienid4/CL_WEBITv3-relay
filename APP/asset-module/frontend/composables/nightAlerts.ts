// 夜間速查「目前告警中」（AUD-03）的純函式：篩選、文字、盤中判斷、人為更新流程、示範資料。
// 全部不依賴 Nuxt 自動匯入，vitest 可直接測。

export interface NightAlertRow {
  __no: number
  device_id: string
  name: string
  ip: string
  state: string
  severity: '嚴重' | '警告'
  problem: string
  since: string | null
  duration_min: number | null
  categories: string[]
  partitions: string | null
  truncated: boolean
  match: 'matched' | 'pending' | 'none'
  match_label: string
  match_note: string
  machine_key: string | null
  machine_name: string | null
  candidates: number
}
export type NightAlertStatus = 'not_enabled' | 'never' | 'stale' | 'clock' | 'unconfirmed' | 'ok'
export interface NightAlertCounts { total: number; critical: number; warning: number; unmapped: number }
export interface NightAlertData {
  status: NightAlertStatus
  status_label: string
  message: string
  rows: NightAlertRow[]
  counts: NightAlertCounts
  data_time: string | null
  age_min: number | null
  fresh_min: number
  last_failed: boolean
  /** Q15：中繼那一份（4.1 告警事件）時有範圍說明；kind='relay' */
  scope?: { mode: 'policy' | 'all'; keywords: string[]; n_all: number; hidden: number; label: string } | null
  source: { kind?: 'relay'; label?: string; enabled: boolean; module_enabled: boolean; polling_enabled: boolean; interval_min: number | null; last_run_at: string | null; last_run_status: string | null; last_run_message: string | null }
  can_refresh?: boolean
  can_edit_settings?: boolean
  demo?: boolean
}
export type AlertFilter = 'all' | 'critical' | 'warning' | 'unmapped'

export const FILTER_LABEL: Record<AlertFilter, string> = { all: '全部', critical: '嚴重', warning: '警告', unmapped: '未對應' }

/** 下鑽用的篩選：跟後端 counts_of 用同一組欄位（severity／match），數字磚與清單才不會對不上。 */
export function filterRows(rows: NightAlertRow[], f: AlertFilter): NightAlertRow[] {
  if (f === 'critical') return rows.filter(r => r.severity === '嚴重')
  if (f === 'warning') return rows.filter(r => r.severity === '警告')
  if (f === 'unmapped') return rows.filter(r => r.match !== 'matched')
  return rows
}

export function countsOf(rows: NightAlertRow[]): NightAlertCounts {
  return { total: rows.length, critical: filterRows(rows, 'critical').length, warning: filterRows(rows, 'warning').length, unmapped: filterRows(rows, 'unmapped').length }
}

export function durationText(min: number | null | undefined): string {
  if (min === null || min === undefined) return '未取得'
  if (min < 60) return `${min} 分鐘`
  if (min < 1440) return `${Math.floor(min / 60)} 小時 ${min % 60} 分`
  return `${Math.floor(min / 1440)} 天 ${Math.floor((min % 1440) / 60)} 小時`
}

/** 平日 08:30–15:00（瀏覽器本地時間；不知道國定假日，假日也當平日提醒——寧可多問一次）。 */
export function isTradingWindow(d: Date): boolean {
  const wd = d.getDay()
  if (wd === 0 || wd === 6) return false
  const m = d.getHours() * 60 + d.getMinutes()
  return m >= 8 * 60 + 30 && m < 15 * 60
}

export const IN_WINDOW_CONFIRM = '現在是盤中，確定要人為執行一次查詢嗎？'

/** 給畫面用的資料狀態：有沒有資料可信。unavailable＝未取得（不能顯示成沒有告警）。 */
export function viewKind(d: NightAlertData | null): 'loading' | 'unavailable' | 'stale' | 'empty' | 'rows' {
  if (!d) return 'loading'
  if (d.status === 'not_enabled' || d.status === 'never') return 'unavailable'
  if (d.status !== 'ok') return 'stale'     // 過舊／時間異常／最近輪詢失敗：都不是可信的現況
  return d.rows.length ? 'rows' : 'empty'
}

/** 人為「更新」：只是呼叫既有的 WhatsUp 輪詢端點（本頁不自己查）。盤中先問，確認才送，並帶 confirm_in_window=true。
 *  回 { sent:false } 表示使用者取消，沒有任何呼叫。 */
export async function refreshByHand(apiFetch: (u: string, o?: any) => Promise<any>, now: Date, confirmFn: (msg: string) => boolean) {
  const inWin = isTradingWindow(now)
  if (inWin && !confirmFn(IN_WINDOW_CONFIRM)) return { sent: false as const }
  const url = '/api/integrations/whatsup/poll' + (inWin ? '?confirm_in_window=true' : '')
  const res = await apiFetch(url, { method: 'POST' })
  return { sent: true as const, res }
}

/** 示範資料：前端產生、不入庫；網段 192.0.2.x、名稱 DEMO-。 */
export function demoData(): NightAlertData {
  const mk = (n: number, name: string, ip: string, state: string, sev: '嚴重' | '警告', problem: string, cats: string[], part: string | null, mins: number, match: NightAlertRow['match'], key: string | null): NightAlertRow => ({
    __no: n, device_id: `demo-${n}`, name, ip, state, severity: sev, problem, since: '示範', duration_min: mins, categories: cats, partitions: part, truncated: false,
    match, match_label: match === 'matched' ? '已對應' : match === 'pending' ? '待核（不確定是哪一台）' : '未登記（資產庫對不到）', match_note: match === 'pending' ? '這個 IP 登記在多台主機（示範）' : '',
    machine_key: key, machine_name: key ? name : null, candidates: match === 'pending' ? 2 : 0,
  })
  const rows = [
    mk(1, 'DEMO-DB01', '192.0.2.11', '當機', '嚴重', 'Disk Free Space /data', ['磁碟'], '/data', 185, 'matched', 'demo:DEMO-DB01'),
    mk(2, 'DEMO-APP02', '192.0.2.12', '警告', '警告', 'Memory', ['記憶體'], null, 95, 'matched', 'demo:DEMO-APP02'),
    mk(3, 'DEMO-SHARED', '192.0.2.50', '警告', '警告', 'Ping', ['其他告警'], null, 42, 'pending', null),
    mk(4, 'DEMO-UNKNOWN', '192.0.2.99', '無回應', '嚴重', 'Ping', ['其他告警'], null, 15, 'none', null),
  ]
  return {
    status: 'ok', status_label: '資料可用', message: '示範資料：不是真的告警', rows, counts: countsOf(rows), data_time: null, age_min: null, fresh_min: 30, last_failed: false,
    source: { enabled: true, module_enabled: true, polling_enabled: true, interval_min: 5, last_run_at: null, last_run_status: null, last_run_message: null },
    can_refresh: false, can_edit_settings: false, demo: true,
  }
}
