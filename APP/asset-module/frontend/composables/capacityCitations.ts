// 10.3 容量分析「封存引用」純函式（2.042.06）。資料＝/api/v2/capacity/archive-citations（後端 capacity_archive.citations，唯讀）。
// 狀態磚的數字＝同一份 rows 依狀態計數，點磚＝同一份 rows 篩出來（加總＝下鑽）。
export type CiteStatus = 'active' | 'superseded' | 'voided'
export interface CiteResolved { ref: string; rows: number; archive_ids: number[]; label: string }
export interface CiteRow {
  id: number; root_id: number; chain: string; version: number; supersedes_id: number | null; title: string; purpose: string; note: string
  refs: string[]; resolved: CiteResolved[]; ref_rows: number; created_by: string; created_at: string
  status: CiteStatus; status_label: string; is_latest: boolean; voided_at: string | null; voided_by: string | null; void_reason: string | null
}
export interface CiteData { rows: CiteRow[]; counts: Record<CiteStatus, number>; chains: number }

export function citeCounts(rows: CiteRow[]): Record<CiteStatus | 'all', number> {
  const c = { all: rows.length, active: 0, superseded: 0, voided: 0 }
  for (const r of rows) c[r.status] += 1
  return c
}
export function citeFilter(rows: CiteRow[], s: CiteStatus | 'all'): CiteRow[] {
  return s === 'all' ? rows : rows.filter(r => r.status === s)
}
/** 每個引用一段：原文（對到幾筆、哪幾筆）。 */
export function citeRefsText(r: CiteRow): string {
  return (r.resolved ?? []).map(x => `${x.ref}（${x.rows} 筆${x.label ? `：${x.label}` : ''}）`).join('；') || r.refs.join('；')
}
/** 表單的多行輸入 → 引用清單（去空白、去重，保留順序；格式與存在性由後端驗證）。 */
export function parseRefsInput(text: string): string[] {
  const out: string[] = []
  for (const s of (text || '').split(/[\r\n,;]+/).map(x => x.trim()).filter(Boolean)) if (!out.includes(s)) out.push(s)
  return out
}
