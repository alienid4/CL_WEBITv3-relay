// 搬遷包畫面的純函式（vitest 直接測）：機房切換競態、台數一致性、證據方向顯示。

/** 「只認最新一次」：每次發請求先 begin() 拿 token，回應時 isLatest(token) 為 false 就丟棄。
 * 例：切機房 A→B，A 的慢回應晚到時不可蓋掉 B 的畫面。 */
export function createLatest() {
  let seq = 0
  return {
    begin(): number { return ++seq },
    isLatest(token: number): boolean { return token === seq },
    cancel(): void { seq++ },
  }
}

/** 回應是否仍屬於目前畫面：機房一致、而且是最新一次請求。 */
export function accept(latest: ReturnType<typeof createLatest>, token: number, requestRoom: string, currentRoom: string): boolean {
  return latest.isLatest(token) && requestRoom === currentRoom
}

/** 明細標題台數與成員筆數必須一致；不一致回錯誤字串（畫面顯示錯誤，不顯示兩個數字）。 */
export function countMismatch(detail: { count?: number; members?: unknown[] } | null | undefined): string {
  if (!detail) return ''
  const n = detail.members?.length ?? 0
  return detail.count === n ? '' : `資料不一致：包台數 ${detail.count ?? '—'} 與成員筆數 ${n} 對不上，請重新整理；若持續發生請回報。`
}

/** 證據的方向欄：只用系統→系統的真實流向（caller→callee），不用「host → peer」冒充。 */
export function flowText(ev: { flow?: string } | null | undefined, direction?: string | null): string {
  if (ev?.flow) return ev.flow
  return direction || '—'
}
