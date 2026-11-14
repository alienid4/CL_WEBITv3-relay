// 主機履歷「收集摘要」的純函式（codex 207）。資料＝/api/v2/machine/collect-summary（後端 v2_collect_summary，只讀既有欄位）。
// 三種狀態不能混：正常（門檻內）／資料已過期（超過門檻）／未取得（從未成功）。
export type CollectState = 'fresh' | 'stale' | 'never'
export interface CollectRow { __no: number; kind: string; label: string; state: CollectState; state_label: string; last: string | null; days: number | null }
export interface CollectData {
  key: string; stale_days: number; setting_key: string; applicable: boolean; reason: string | null
  rows: CollectRow[]; counts?: Record<CollectState, number>; targeted?: boolean
}
export const STATE_LABEL: Record<CollectState, string> = { fresh: '正常', stale: '資料已過期', never: '未取得' }
export const STATE_TONE: Record<CollectState, string> = { fresh: 'prod', stale: 'test', never: 'none' }

export function filterRows(rows: CollectRow[], f: CollectState | 'all'): CollectRow[] {
  return f === 'all' ? rows : rows.filter(r => r.state === f)
}
export function countsOf(rows: CollectRow[]): Record<CollectState, number> {
  const c: Record<CollectState, number> = { fresh: 0, stale: 0, never: 0 }
  for (const r of rows) c[r.state] += 1
  return c
}
/** 距今文字：null＝—（沒有成功過就沒有「距今」），0＝今天。 */
export function daysText(days: number | null): string {
  return days === null || days === undefined ? '—' : days === 0 ? '今天' : `${days} 天前`
}
