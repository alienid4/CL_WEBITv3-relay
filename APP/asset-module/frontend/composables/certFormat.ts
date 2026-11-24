// 憑證盤點（7.5）前端用到的純函式。狀態判斷以後端為準（距到期瞬間剩餘秒數）；這裡只做顯示與匯入預覽的選擇邏輯，
// 好讓 vitest 能測。時間：資料庫存 UTC，畫面一律 UTC+8 並明示時區。

export const CERT_STATUS_KEYS = ['expired', 'd7', 'd30', 'd60', 'ok', 'none'] as const
export type CertStatus = (typeof CERT_STATUS_KEYS)[number]

export const certStatusRank = (k: string): number => {
  const i = (CERT_STATUS_KEYS as readonly string[]).indexOf(k)
  return i < 0 ? CERT_STATUS_KEYS.length : i
}

/** 卡片／標籤顏色語意：bad=已過期與 7 天內、warn=30／60 天內、ok=正常、none=無資料 */
export const certStatusTone = (k: string): 'bad' | 'warn' | 'ok' | 'none' =>
  k === 'expired' || k === 'd7' ? 'bad' : k === 'd30' || k === 'd60' ? 'warn' : k === 'ok' ? 'ok' : 'none'

const pad = (n: number) => String(n).padStart(2, '0')

/** UTC ISO → 'YYYY-MM-DD HH:mm (UTC+8)'；空值或看不懂回 ''。 */
export function certFmtUtc8(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const d = new Date(t + 8 * 3600 * 1000)
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} (UTC+8)`
}

/** <input type="datetime-local"> 的值（'2026-11-30T23:59'）→ 送後端的 '2026-11-30 23:59'（後端視為 UTC+8）。 */
export const certLocalToApi = (v: string): string => (v || '').trim().replace('T', ' ')

export const CERT_FIELD_LABEL: Record<string, string> = {
  name: '憑證名稱', environment: '環境', endpoint: '端點', port: '埠', sni: 'SNI', mount_type: '掛載型態', host_ips: '主機 IP',
  f5_bigip: 'F5 BIG IP', f5_mounted: 'F5 是否掛憑證', os_type: 'OS／站台類型', req_unit: '申請單位', req_contact: '申請窗口',
  ap_contact: 'AP 窗口', infra_owner: 'Infra 執行人員', ap_tester: 'AP 驗測人員', planned_text: '預排更換時間',
  planned_at_utc: '預排更換時間（結構化）', announce_needed: '需公告', planned_date: '預排日期', replace_status: '更換進度',
  replace_method: '更換方式', note: '備註',
}

export interface CertChange { field: string; old: any; new: any }
/** 匯入預覽的「變更」欄：人名欄位只顯示『有更新』不顯示內容（預覽不外洩可識別資訊給一般畫面）。 */
export function certChangeSummary(changes: CertChange[] | undefined, contactFields: string[] = ['req_contact', 'ap_contact', 'infra_owner', 'ap_tester']): string {
  return (changes ?? []).filter(c => c.field !== 'planned_at_utc').map((c) => {
    const lab = CERT_FIELD_LABEL[c.field] ?? c.field
    if (contactFields.includes(c.field)) return `${lab}：有更新`
    const show = (v: any) => (v === null || v === undefined || v === '' ? '（空）' : String(v))
    return `${lab}：${show(c.old)} → ${show(c.new)}`
  }).join('；')
}

export interface CertPlanRow { row_no: number; status: string; candidates?: { id: string }[] }
/** 衝突列且還沒選的筆數（這些列確認時不會寫入）。 */
export const certUnresolved = (rows: CertPlanRow[], picks: Record<string, string>): number =>
  rows.filter(r => r.status === 'conflict' && !picks[String(r.row_no)]).length

/** 送後端的 decisions：只收衝突列、且選項必須是 skip／new／該列的候選 id（其他值丟掉，不亂送）。 */
export function certDecisions(rows: CertPlanRow[], picks: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const r of rows) {
    if (r.status !== 'conflict') continue
    const p = picks[String(r.row_no)]
    if (!p) continue
    if (p === 'skip' || p === 'new' || (r.candidates ?? []).some(c => c.id === p)) out[String(r.row_no)] = p
  }
  return out
}
