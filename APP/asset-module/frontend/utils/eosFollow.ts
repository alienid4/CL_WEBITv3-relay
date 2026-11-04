// EOS 通知與回覆追蹤（批次一）的畫面小工具。規則的正本在後端 eos_follow.py（硬規則由後端強制）；
// 這裡只負責「表單即時提示缺什麼」與顯示用的文字，不做任何放行判斷。
export interface ReasonClass { name: string; need_note: boolean }
export interface HoldForm {
  hold_reason_class?: string | null; hold_reason_note?: string | null; hold_basis_text?: string | null
  hold_evidence_ref?: string | null; hold_review_date?: string | null
}

const DATE = /^\d{4}-\d{2}-\d{2}$/
export function validDate(s?: string | null): boolean {
  if (!s || !DATE.test(s)) return false
  const [y, m, d] = s.split('-').map(Number)
  const t = new Date(Date.UTC(y, m - 1, d))
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d
}

/** 擬保留缺哪些依據（空陣列＝齊全，會成為「保留（已附依據）」）。記錄人／日期由伺服器在存檔時自動記，表單不用填。 */
export function holdMissing(f: HoldForm, classes: ReasonClass[]): string[] {
  const miss: string[] = []
  const cls = (f.hold_reason_class || '').trim()
  const meta = classes.find((c) => c.name === cls)
  if (!cls) miss.push('阻礙來源類別')
  else if (!meta) miss.push(`阻礙來源類別「${cls}」不在類別清單`)
  else if (meta.need_note && !(f.hold_reason_note || '').trim()) miss.push(`「${cls}」需填說明（單位／原因）`)
  if (!(f.hold_basis_text || '').trim()) miss.push('依據說明')
  if (!(f.hold_evidence_ref || '').trim()) miss.push('證據參照')
  if (!validDate(f.hold_review_date)) miss.push('重新檢視日')
  return miss
}

/** 重新檢視日的「建議值」：今天起算 N 個月（預設 6，可改）。只是表單預填，不是系統規則。 */
export function suggestReviewDate(today: string, months: number): string {
  const [y, m, d] = today.split('-').map(Number)
  const t = new Date(Date.UTC(y, m - 1 + months, 1))
  const last = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth() + 1, 0)).getUTCDate()
  t.setUTCDate(Math.min(d, last))
  return t.toISOString().slice(0, 10)
}

export const REPLY_OPTS = ['待回覆', '規劃更換', '擬保留'] as const
export const DONE_OPTS = ['未完成', '更換完成'] as const

/** 狀態標籤的語氣（只用站內既有 tone：prod 綠／test 橘／none 灰；不用警示紅黃配色堆疊）。 */
export function statusTone(flags: string[]): 'prod' | 'test' | 'none' {
  if (flags.includes('done')) return 'prod'
  if (flags.includes('voided')) return 'none'
  const todo = ['unnotified', 'unassigned', 'delivery_failed', 'unreplied', 'plan_overdue', 'hold_no_basis', 'hold_expired']
  if (flags.some((f) => todo.includes(f))) return 'test'
  return 'none'
}

/** 負責人欄：待指派要講清楚「沒人可通知」，不是空白。 */
export function ownerText(r: { owner_ref: string; owner_role?: string }): string {
  return r.owner_ref === '待指派' ? '待指派（登記沒有負責人）' : `${r.owner_ref}${r.owner_role ? `（${r.owner_role}）` : ''}`
}

/** 通知欄：已通知／寄送失敗／尚未通知／無法對應／收件地址無法確認分開寫，不混成一個「已通知」；失敗可與已通知並存。 */
export function notifyText(r: { notified: boolean; notified_at?: string | null; last_send_failed?: boolean; in_window?: boolean; notify_map?: string; recipient_unrecorded?: boolean }): string {
  const failed = r.last_send_failed ? '（最近一次寄送失敗）' : ''
  if (r.notified) return `已通知 ${(r.notified_at || '').slice(0, 10)}${failed}${r.recipient_unrecorded ? '・收件地址未記錄（舊資料）' : ''}`
  if (r.notify_map === 'recipient_unverified') return '收件地址無法確認（待核對）'
  if (r.last_send_failed) return '最近一次寄送失敗，尚未通知成功'
  if (r.notify_map === 'unmapped') return '無法對應通知紀錄'
  if (r.in_window === false) return '未到通知期'
  return '尚未通知'
}
