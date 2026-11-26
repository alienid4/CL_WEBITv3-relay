// 2.1 主機查詢「逐筆登記」頁籤（V61，2.057.1）用的純函式：網址 → /api/assets 的篩選參數。
// 跟舊 /assets 資產查詢 loadAll() 同一組參數、同樣的檢查（scan_status、virtual 只收固定值；filter_value 只跟著 filter_field）。
// 舊網址 /assets?… 轉到 /v2/hosts?tab=records 時也用這份清單決定要帶哪些參數（v2LegacyMap）。

/** 送給 /api/assets 的篩選參數（網址上同名） */
export const RECORD_FILTER_KEYS = ['scan_status', 'q', 'environment', 'filter_field', 'filter_value', 'virtual', 'os_class', 'os_source', 'platform',
  'os_type', 'os_detail', 'role', 'canonical_os', 'canonical_model', 'location_group', 'environment_group', 'collect', 'source',
  'person', 'software'] as const   // N20：人員（CIA 人員表）、軟體（CIA 軟體表）部分比對

export const SCAN_STATUS_LABEL: Record<string, string> = { overlap: '兩邊相符（已登記且本次掃得到）', ica_only: '登記卻掃不到（本次掃描沒回應）' }

type Q = Record<string, unknown>
const str = (q: Q, k: string) => (typeof q[k] === 'string' ? String(q[k]) : '')

/** 網址 query → /api/assets 的篩選參數（不含分頁與排序） */
export function recordFilterQuery(q: Q): Record<string, string> {
  const o: Record<string, string> = {}
  for (const k of RECORD_FILTER_KEYS) {
    const v = str(q, k)
    if (k === 'filter_value') { if (str(q, 'filter_field')) o[k] = v; continue }       // 空值也是一種值（「（空值）」）
    if (k === 'scan_status' && !(v in SCAN_STATUS_LABEL)) continue
    if (k === 'virtual' && v !== 'yes' && v !== 'no') continue
    if (v) o[k] = v
  }
  return o
}

/** 複選籌碼：切換一個值，回傳照固定順序排好的新值（一個都沒選＝undefined，不留空字串在網址） */
export function toggleMulti(current: string, value: string, order: readonly string[]): string | undefined {
  const cur = new Set(current.split(',').filter(Boolean))
  cur.has(value) ? cur.delete(value) : cur.add(value)
  const vals = order.filter(x => cur.has(x))
  return vals.length ? vals.join(',') : undefined
}

/** 伺服器排不了的欄（不是資料表欄位）：點了不動，不送會被後端拒絕的 sort_by */
export const NOT_SORTABLE = new Set(['guess', 'ops', '__no'])

/** 勾選只留目前這一頁還看得到的登記（2.058.2，批 22 審查 P2）：重新載入後離開條件的不能被送出 */
export function pruneSelected(sel: Record<string, boolean>, rows: { asset_serial?: string | null }[]): Record<string, boolean> {
  const keep = new Set(rows.map(r => r.asset_serial).filter(Boolean) as string[])
  const out: Record<string, boolean> = {}
  for (const [k, v] of Object.entries(sel)) if (v && keep.has(k)) out[k] = true
  return out
}
