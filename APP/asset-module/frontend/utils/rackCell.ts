// 機櫃正面圖每台的格子內容（2.043.19，需求表 R20／R21；使用者已確認排法）。
// 第 1 列：〔1 主機名稱／廠牌〕〔2 機型（兩格寬，放不下換行）〕〔3 IP〕
// 第 2 列：〔5 序號〕〔6 系統｜管理口（兩格寬）〕〔7 狀態圖示〕（2.044.8：1U 的格子只畫第 1 列）
export const CELL_LEGEND = [
  { n: 1, k: 'name', label: '1 主機名稱／廠牌' }, { n: 2, k: 'model', label: '2 機型' }, { n: 3, k: 'ip', label: '3 IP' },
  { n: 5, k: 'serial', label: '5 序號' }, { n: 6, k: 'sys', label: '6 系統｜管理口' }, { n: 7, k: 'status', label: '7 狀態圖示' },
] as const

/** 這個名稱是不是「真的主機名稱」：空的、跟機型一樣、DYN- 開頭（掃描產生、用 IP 命名的空殼）都不算 */
export function isRealHostName(name?: string | null, model?: string | null): boolean {
  const n = (name ?? '').trim()
  if (!n) return false
  if (/^DYN-/i.test(n)) return false
  const m = (model ?? '').trim()
  if (m && n.toUpperCase() === m.toUpperCase()) return false
  return true
}

/** 第 1 格：有真的主機名稱就放主機名稱（有備註接「｜備註」），沒有就只放廠牌 */
export function firstCellText(d: { name?: string | null; model?: string | null; vendor?: string | null; remark?: string | null }): string {
  const head = isRealHostName(d.name, d.model) ? (d.name ?? '').trim() : (d.vendor ?? '')
  return [head, d.remark ?? ''].filter(Boolean).join('｜')
}

/** 管理口的短寫法：「iDRAC 10.99.1.5」 */
export function mgmtShort(g?: { ip?: string | null; kind?: string | null } | null): string {
  if (!g) return ''
  return `${g.kind || 'OOB'} ${g.ip || '無 IP'}`
}

/** 第 6 格：「系統｜管理口」，兩邊都沒有就空白 */
export function sysCellText(systems: string[], mgmt?: { ip?: string | null; kind?: string | null } | null, more = 0): string {
  const m = mgmtShort(mgmt) + (more > 0 ? ` 等 ${more + 1} 個` : '')
  return [systems.join('、'), m.trim()].filter(Boolean).join('｜')
}
