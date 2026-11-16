// 共用 IP／VIP 待核清單的批次判定：純函式（選取、篩選、上限、預覽文字）。
export const BATCH_MAX = 500

export interface BatchRow { item_key: string; who: string; why: string; mach: string; signals: { code: string }[] }
export interface BatchFilter { text: string; signal: string }

/** 依文字（IP／原因／涉及主機，不分大小寫）與訊號類型篩選；訊號空字串＝不限。 */
export function filterBatchRows<T extends BatchRow>(rows: T[], f: BatchFilter): T[] {
  const q = (f.text || '').trim().toLowerCase()
  return rows.filter((r) => {
    if (f.signal && !r.signals.some(s => s.code === f.signal)) return false
    if (!q) return true
    return `${r.who} ${r.why} ${r.mach}`.toLowerCase().includes(q)
  })
}

/** 「全選目前篩選結果」：是篩選後的全部（不只這一頁），回傳勾選表。 */
export function selectAllFiltered<T extends { item_key: string }>(rows: T[], prev: Record<string, boolean> = {}): Record<string, boolean> {
  const n = { ...prev }
  for (const r of rows) n[r.item_key] = true
  return n
}

/** 只留下仍在清單內且有勾的 key（清單重載後舊勾選不外漏）。 */
export function selectedKeys(selected: Record<string, boolean>, rows: { item_key: string }[]): string[] {
  const live = new Set(rows.map(r => r.item_key))
  return Object.keys(selected).filter(k => selected[k] && live.has(k))
}

/** 超過上限回提示字串，沒超過回 ''。 */
export function overLimitText(n: number, max = BATCH_MAX): string {
  return n > max ? `一次最多 ${max} 筆（目前 ${n} 筆），請縮小篩選或取消部分勾選` : ''
}

export interface Preview {
  will_apply: number; will_overwrite: number; reopened: number; skipped: number
  skipped_by_reason: Record<string, number>; skipped_reason_label: Record<string, string>
  will_overwrite_by_verdict: Record<string, number>
}

/** 預覽摘要：一行一項，給畫面直接顯示。 */
export function previewLines(p: Preview): string[] {
  const out = [`將判定 ${p.will_apply} 筆`]
  if (p.will_overwrite) {
    const by = Object.entries(p.will_overwrite_by_verdict).map(([k, v]) => `${k} ${v}`).join('、')
    out.push(`其中覆蓋已判定 ${p.will_overwrite} 筆（${by}）`)
  }
  if (p.reopened) out.push(`其中重新出現的 ${p.reopened} 筆`)
  for (const [k, v] of Object.entries(p.skipped_by_reason)) out.push(`略過 ${p.skipped_reason_label[k] ?? k} ${v} 筆`)
  return out
}
