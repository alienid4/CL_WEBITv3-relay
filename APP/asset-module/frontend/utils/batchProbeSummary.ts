// 批次試密碼的結果摘要（2.058.14 審查 P2-2）：不是 IPv4 的、VMware 擋下的都沒有登入，
// 不能算進「兩組都不對」——全部都是無效輸入時，摘要原本會寫「N 台兩組都不對」。
export interface ProbeSummaryRow {
  matched_password_index: number | null
  invalid_ip?: boolean
  vmware?: boolean
}

export interface ProbeSummary { total: number; matched: number; unmatched: number; invalid: number; vmware: number }

export function probeSummary(rows: ProbeSummaryRow[]): ProbeSummary {
  const invalid = rows.filter(r => r.invalid_ip).length
  const vmware = rows.filter(r => !r.invalid_ip && r.vmware).length
  const matched = rows.filter(r => r.matched_password_index !== null && r.matched_password_index !== undefined).length
  return { total: rows.length, matched, invalid, vmware, unmatched: rows.length - matched - invalid - vmware }
}

export function probeLabel(r: ProbeSummaryRow): string {
  if (r.invalid_ip) return '只接受 IPv4'
  if (r.vmware) return 'VMware 不登入'
  if (r.matched_password_index === 0) return '密碼 A'
  if (r.matched_password_index === 1) return '密碼 B'
  return '都不對'
}
