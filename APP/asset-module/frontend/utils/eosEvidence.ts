// EOS 批二（2.041.73）的畫面小工具。規則的正本在後端 eos.py（可信度語意）與 eos_evidence.py（證據完整度）；
// 這裡只負責「表單即時提示缺什麼」與顯示用的文字，不做任何放行判斷（硬規則由後端強制）。
export type Trust = 'confirmed' | 'presumed' | 'unknown'

/** 全站統一的可信度用詞（與後端 eos.TRUST_LABEL 相同；API 也會直接帶 trust_label）。 */
export const TRUST_LABEL: Record<Trust, string> = { confirmed: '已確認', presumed: '推定', unknown: '未知' }

/** 可信度說明（滑鼠移上去的提示；畫面上只放三個詞）。 */
export const TRUST_HINT: Record<Trust, string> = {
  confirmed: '有完整日期，且官方頁面已核對',
  presumed: '有日期但來源尚未核對完，只供參考，不單獨觸發紅燈與通知',
  unknown: '沒有可用的日期（官方未公告、無單一日期、來源被拒、格式錯或表內查無）',
}

export function trustTone(t?: string | null): 'prod' | 'test' | 'none' {
  return t === 'confirmed' ? 'prod' : t === 'presumed' ? 'test' : 'none'
}

export const PRECISION_OPTS = [
  { v: 'day', label: '日（完整日期）' }, { v: 'month', label: '月（官方只到年月）' }, { v: 'none', label: '無日期' },
] as const

export const BUCKET_LABEL: Record<string, string> = {
  confirmed: '已確認', presumed: '推定', unknown: '未知（表內有、無可用日期）',
  unmatched: '未匹配（EOS 表查無）', no_value: '未填（資產沒有這個欄位）', not_applicable: '不適用（虛擬機）',
}

const DATE = /^\d{4}-\d{2}-\d{2}$/
export function validDate(s?: string | null): boolean {
  if (!s || !DATE.test(s)) return false
  const [y, m, d] = s.split('-').map(Number)
  const t = new Date(Date.UTC(y, m - 1, d))
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d
}

export function derivedPrecision(eosDate?: string | null): 'day' | 'month' | 'none' {
  const t = (eosDate || '').trim()
  if (/^\d{4}-\d{2}-\d{2}$/.test(t) && validDate(t)) return 'day'
  if (/^\d{4}-\d{2}$/.test(t) && validDate(`${t}-01`)) return 'month'
  return 'none'
}

export interface EvidenceForm {
  verified_by?: string | null; verified_on?: string | null; quote_text?: string | null
  scope_text?: string | null; date_precision?: string | null; source_url?: string | null
}

const ZW = /[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad\u180e\u3164\uffa0]/g
const blank = (v?: string | null) => !(v || '').replace(ZW, '').trim()

/** 存檔至少要有：查證日期＋官方原文引用（後端同規則）。回缺的欄位名。 */
export function saveMissing(f: EvidenceForm, today: string): string[] {
  const miss: string[] = []
  if (!validDate(f.verified_on)) miss.push('查證日期')
  else if ((f.verified_on as string) > today) miss.push('查證日期（不能在未來）')
  if (blank(f.quote_text)) miss.push('官方原文引用')
  return miss
}

/** 表單目前還缺哪些才算「證據完整」（完整度提示，不擋存檔）。 */
export function completeMissing(f: EvidenceForm, eosDate: string | null | undefined, entrySource: string | null | undefined, today: string): string[] {
  const miss = saveMissing(f, today)
  if (blank(f.verified_by)) miss.push('查證人')
  if (blank(f.scope_text)) miss.push('適用範圍')
  const want = derivedPrecision(eosDate)
  if (!f.date_precision) miss.push('日期精度')
  else if (f.date_precision !== want) miss.push(`日期精度（表內日期是「${PRECISION_OPTS.find((o) => o.v === want)?.label}」）`)
  if (blank(f.source_url) && blank(entrySource)) miss.push('來源網址')
  return miss
}

// ---- 覆蓋率下鑽：伺服器端排序／分頁（2.041.80）----
/** 畫面欄位 → 後端排序鍵（後端白名單 eos_evidence.SORT_KEYS）。項次欄（__no）＝預設順序，不送 sort。 */
export const COV_SORT_KEY: Record<string, string> = {
  hostname: 'hostname', ip: 'ip', environment: 'environment', raw_value: 'raw_value', canonical: 'canonical',
  matched_name: 'matched_name', trust_label: 'trust', bucket_label: 'bucket',
}

export interface CovDrillSel { track: string; bucket: string; item?: string; review?: boolean }

/** 組下鑽 API 的查詢參數；size=0＝全部（匯出）。 */
export function covDrillQuery(sel: CovDrillSel, page: number, size: number, sortKey: string, sortDir: 'asc' | 'desc'): Record<string, string> {
  const q: Record<string, string> = { page: String(page), size: String(size) }
  if (sel.track) q.track = sel.track
  if (sel.bucket) q.bucket = sel.bucket
  if (sel.item !== undefined) q.item = sel.item
  if (sel.review) q.review = 'true'
  const k = COV_SORT_KEY[sortKey]
  if (k) { q.sort = k; q.dir = sortDir }
  return q
}

// ---- 合約影子比對（2.041.80）----
export const CONTRACT_TYPE_LABEL: Record<string, string> = {
  unlinked: '合約列未對到資產',
  model_mismatch: '合約型號與資產型號不一致',
  contract_after_eos: '合約到期日晚於官方 EOS 日期',
  eos_only_in_contract: '合約型號有 EOS 日期、資產型號查不到',
}
