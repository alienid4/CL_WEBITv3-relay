// 5.1 機房平面圖「超過一年未盤」「從沒盤過」的下鑽清單（2.053.1，批 17 審查 P2-4：原本寫在頁面裡、只靠原始碼比對測）。
// 機房：後端 audit_stale_keys／audit_never_keys 原樣；指定機櫃：只留那一櫃在用設備（tile.keys）裡的。
// 每櫃的「逾一年未盤（台）」用 staleCountOf，跟這裡同一個判斷，所以格子數字＝點下去的筆數。
export interface AuditRoom { racks: { rack: string; keys: string[] }[]; audit_stale_keys?: string[]; audit_never_keys?: string[] }

export function auditDrillKeys(room: AuditRoom | null | undefined, kind: 'stale' | 'never', rack = ''): string[] {
  if (!room) return []
  const src = (kind === 'stale' ? room.audit_stale_keys : room.audit_never_keys) ?? []
  if (!rack) return src
  const inRack = new Set(room.racks.filter(t => t.rack === rack).flatMap(t => t.keys))
  return src.filter(k => inRack.has(k))
}

export function staleCountOf(room: AuditRoom | null | undefined, rack: string): number {
  return auditDrillKeys(room, 'stale', rack).length
}
