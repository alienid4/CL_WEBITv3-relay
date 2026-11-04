// 憑證盤點（7.5）清單的條件過濾：網址 query ⇄ 篩選條件 ⇄ 後端參數。純函式，方便 vitest 測。
// 篩選一律放網址（可分享、重新整理不掉、從詳情頁返回仍在）；後端 /api/certs/items 用同一組參數名。

export const CERT_FILTER_KEYS = ['q', 'status', 'env', 'mount', 'unit', 'risk', 'within', 'due_from', 'due_to', 'rs', 'announce',
  'has_expiry', 'active', 'plan_from', 'plan_to', 'has_plan', 'pending'] as const
export type CertFilterKey = (typeof CERT_FILTER_KEYS)[number]

/** 空白值（欄位沒填）的篩選代號，跟後端 certs.BLANK 一致 */
export const CERT_BLANK = '_none'

type Q = Record<string, any>
const one = (v: any): string => (Array.isArray(v) ? String(v[0] ?? '') : v == null ? '' : String(v))

/** 從網址 query 取出篩選參數（只收認得的鍵、空值丟掉）。送後端與組網址都用這份。 */
export function certFilterParams(query: Q): Record<string, string> {
  const out: Record<string, string> = {}
  for (const k of CERT_FILTER_KEYS) {
    const v = one(query[k]).trim()
    if (v) out[k] = v
  }
  return out
}

/** 目前有幾個條件在作用（「是否停用」預設啟用中不算條件） */
export function certFilterCount(query: Q): number {
  const p = certFilterParams(query)
  delete p.active
  if (query.active && one(query.active) !== 'on') p.active = one(query.active)
  return Object.keys(p).length
}

const SEG_LABEL: Record<string, string> = { expired: '已過期', d7: '7 天內', d30: '8–30 天', d60: '31–60 天', ok: '正常（60 天以上）', none: '無到期資料' }
const RISK_LABEL: Record<string, string> = { high: '高', mid: '中', low: '低', expired: '已過期', pending: '待補到期日', none: '無' }
const ANN_LABEL: Record<string, string> = { yes: '需公告', no: '不需公告', blank: '未填' }
const ACT_LABEL: Record<string, string> = { on: '啟用中', off: '已停用', all: '全部（含停用）' }

/** 目前條件的白話標籤（畫面上一顆顆可單獨移除的小標籤） */
export function certFilterChips(query: Q): { key: CertFilterKey[]; text: string }[] {
  const p = certFilterParams(query)
  const out: { key: CertFilterKey[]; text: string }[] = []
  const blank = (v: string) => (v === CERT_BLANK ? '（未填）' : v)
  if (p.q) out.push({ key: ['q'], text: `搜尋：${p.q}` })
  if (p.status) out.push({ key: ['status'], text: `到期分段：${SEG_LABEL[p.status] ?? p.status}` })
  if (p.within) out.push({ key: ['within'], text: `${p.within} 天內到期` })
  if (p.due_from || p.due_to) out.push({ key: ['due_from', 'due_to'], text: `到期日：${p.due_from || '…'} ～ ${p.due_to || '…'}` })
  if (p.env) out.push({ key: ['env'], text: `環境：${blank(p.env)}` })
  if (p.mount) out.push({ key: ['mount'], text: `掛載型態：${blank(p.mount)}` })
  if (p.unit) out.push({ key: ['unit'], text: `申請單位：${blank(p.unit)}` })
  if (p.risk) out.push({ key: ['risk'], text: `風險等級：${RISK_LABEL[p.risk] ?? p.risk}` })
  if (p.rs) out.push({ key: ['rs'], text: `更換進度：${p.rs === CERT_BLANK ? '（未設定）' : p.rs}` })
  if (p.announce) out.push({ key: ['announce'], text: `需公告：${ANN_LABEL[p.announce] ?? p.announce}` })
  if (p.has_expiry) out.push({ key: ['has_expiry'], text: p.has_expiry === '1' ? '有到期日' : '無到期日' })
  if (p.plan_from || p.plan_to) out.push({ key: ['plan_from', 'plan_to'], text: `預排日期：${p.plan_from || '…'} ～ ${p.plan_to || '…'}` })
  if (p.pending) out.push({ key: ['pending'], text: p.pending === '1' ? '已更換待補到期日' : '不是待補到期日' })
  if (p.has_plan) out.push({ key: ['has_plan'], text: p.has_plan === '1' ? '有預排日期' : '無預排日期' })
  if (p.active && p.active !== 'on') out.push({ key: ['active'], text: `狀態：${ACT_LABEL[p.active] ?? p.active}` })
  return out
}

const pad = (n: number) => String(n).padStart(2, '0')
const ymd = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`

/** UTC+8 的「今天」（年、月[1-12]、日）。now 可注入，測試用。 */
export function certToday(now: Date = new Date()): { y: number; m: number; d: number } {
  const t = new Date(now.getTime() + 8 * 3600 * 1000)
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() }
}

export type CertDuePreset = 'd7' | 'this_week' | 'next_week' | 'this_month' | 'next_month' | 'd30' | 'd60' | 'd90'
export const CERT_DUE_PRESETS: CertDuePreset[] = ['d7', 'this_week', 'next_week', 'this_month', 'next_month', 'd30', 'd60', 'd90']

/** 到期區間快捷鍵 → 後端參數。本月／下個月＝UTC+8 日曆月的第一天到最後一天（含）；N 天內＝尚未過期且剩餘 ≤ N 天。 */
export function certDuePreset(kind: CertDuePreset, now: Date = new Date()): Record<string, string> {
  const { y, m, d } = certToday(now)
  const month = (yy: number, mm: number) => {
    const ny = mm > 12 ? yy + 1 : yy
    const nm = mm > 12 ? mm - 12 : mm
    const last = new Date(Date.UTC(ny, nm, 0)).getUTCDate()
    return { due_from: ymd(ny, nm, 1), due_to: ymd(ny, nm, last) }
  }
  if (kind === 'this_week' || kind === 'next_week') {          // 本週／下週＝UTC+8 的週一到週日（含）；下週＝下個週一到下個週日
    const t = new Date(Date.UTC(y, m - 1, d))
    const mon = new Date(t.getTime() - ((t.getUTCDay() + 6) % 7) * 86400000 + (kind === 'next_week' ? 7 * 86400000 : 0))
    const sun = new Date(mon.getTime() + 6 * 86400000)
    const f = (x: Date) => ymd(x.getUTCFullYear(), x.getUTCMonth() + 1, x.getUTCDate())
    return { due_from: f(mon), due_to: f(sun) }
  }
  if (kind === 'this_month') return month(y, m)
  if (kind === 'next_month') return month(y, m + 1)
  return { within: kind === 'd7' ? '7' : kind === 'd30' ? '30' : kind === 'd60' ? '60' : '90' }
}

/** 哪個快捷鍵目前是亮的（網址參數剛好等於該快捷鍵的結果） */
export function certDuePresetOn(query: Q, now: Date = new Date()): CertDuePreset | '' {
  const p = certFilterParams(query)
  for (const k of CERT_DUE_PRESETS) {
    const want = certDuePreset(k, now)
    const keys = ['within', 'due_from', 'due_to']
    if (keys.every(x => (want[x] || '') === (p[x] || ''))) return k
  }
  return ''
}

/** 清單 → 詳情頁的連結：把目前清單的網址參數整串帶過去（ret），詳情頁的「返回」才回得到同樣的篩選／排序／頁次 */
export function certDetailLink(id: string, query: Q): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    const s = one(v)
    if (s && k !== 'new') sp.set(k, s)
  }
  const ret = sp.toString()
  return `/v2/certs/${id}${ret ? `?ret=${encodeURIComponent(ret)}` : ''}`
}

/** 詳情頁「返回」的目標：ret 還原成清單網址；沒有 ret 就回清單首頁 */
export function certBackLink(ret: any): string {
  const s = one(ret)
  return `/v2/certs${s ? `?${s}` : ''}`
}

/** 表頭點的欄位 → 後端排序鍵（「項次」固定編號對應 seq） */
export const certSortKey = (k: string): string => (k === '__no' ? 'seq' : k)
