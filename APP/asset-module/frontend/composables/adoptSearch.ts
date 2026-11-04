/**
 * 2-1 待納管表的搜尋（v1.468.4）。
 *
 * 2026-10-19 使用者（公司機）：「我都勾選 SSH 了，為什麼 Push Agent、只能匯入、目前不收這些
 * 奇奇怪怪的都在裡面？」——v1.466.0 起搜尋一輸入就跨全部頁簽，不管選了哪個頁簽。
 *
 * 新規則：搜尋只在**目前頁簽**（含結果篩選、隱藏已納管）裡找；別的頁簽也有符合的，
 * 另外講「其他頁簽還有 N 筆符合：Push Agent 1、只能匯入 3」，每段點得下去（跳過去、搜尋字保留）。
 */
export function searchTerms(q: string): string[] {
  return (q || '').trim().toLowerCase().split(/\s+/).filter(Boolean)
}

export function matchesAll(text: string, terms: string[]): boolean {
  const t = (text || '').toLowerCase()
  return terms.every((k) => t.includes(k))
}

export interface TabHit { tab: string; n: number }

/**
 * 目前畫面以外、也符合搜尋的列，依頁簽分組（數量多的在前）。
 * `all`：全部的列；`visibleIps`：目前頁簽（套完篩選）看得到的列；`textOf`：一列的可搜尋文字。
 * `hiddenHere`：在目前頁簽、但被結果篩選／隱藏已納管收起來的（另外算，不混進其他頁簽）。
 */
export function otherTabHits<T extends { ip: string; tab?: string }>(
  all: T[], inScopeIps: Set<string>, visibleIps: Set<string>, terms: string[],
  textOf: (r: T) => string,
): { tabs: TabHit[]; total: number; hiddenHere: number } {
  if (!terms.length) return { tabs: [], total: 0, hiddenHere: 0 }
  const c: Record<string, number> = {}
  let hiddenHere = 0
  for (const r of all) {
    if (visibleIps.has(r.ip) || !matchesAll(textOf(r), terms)) continue
    if (inScopeIps.has(r.ip)) { hiddenHere++; continue }
    const t = r.tab || ''
    c[t] = (c[t] ?? 0) + 1
  }
  const tabs = Object.entries(c).map(([tab, n]) => ({ tab, n })).sort((a, b) => b.n - a.n || a.tab.localeCompare(b.tab))
  return { tabs, total: tabs.reduce((s, x) => s + x.n, 0), hiddenHere }
}
