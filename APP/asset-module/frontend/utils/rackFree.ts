// 機櫃「可用 U」的顯示字（W221-001，Codex 29 號）：有未定位設備或 U 位未登錄時，只能給上限，不可顯示確定值。
// 總覽表、摘要、電力表共用，避免三處語意漂移；純函式，vitest 直接測。
export function freeUText(power: { free_u: number; unplaced_count?: number }, uNone = false): string {
  const n = power.unplaced_count ?? 0
  if (uNone) return `≤${power.free_u}（U 位未登錄，待確認）`
  return n > 0 ? `≤${power.free_u}（另 ${n} 台未定位）` : String(power.free_u)
}
