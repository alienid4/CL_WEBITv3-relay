// A4 機櫃盤點表「備註」欄只放短標記（2.044.8，使用者：備註只放短標記，「另一段：36-37U」這類不印）。
// 後端 rack_layout._marks／print_sheets 產的字 → 短標記；對不到的取前 6 字。
const RULES: [RegExp, string | null][] = [
  [/^另一段/, null],
  [/^管理 IP/, '含管理口'],
  [/^推定同一台/, '疑似重複'],
  [/^疑似登記錯誤/, '疑似登記錯誤'],
  [/^管理介面・找不到本體/, '找不到本體'],
  [/^未觀察到/, '未觀察到'],
  [/^待確認/, '待確認'],
  [/^關機未下架/, '關機未下架'],
  [/^現場更正/, '現場更正'],
]

export function shortMark(m: string): string | null {
  const t = (m || '').trim()
  if (!t) return null
  for (const [re, out] of RULES) if (re.test(t)) return out
  return t.slice(0, 6)
}

/** 一列的備註：縮短、去重，用「、」接 */
export function shortMarks(marks: string[] | null | undefined): string {
  const out: string[] = []
  for (const m of marks ?? []) {
    const s = shortMark(m)
    if (s && !out.includes(s)) out.push(s)
  }
  return out.join('、')
}
