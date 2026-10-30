/**
 * 2-1 待納管搜尋只在目前頁簽、其他頁簽另外算（v1.469.0）。
 * 使用者：「我都勾選 SSH 了，為什麼 Push Agent、只能匯入…都在裡面？」。例子 IP 一律 10.99.x。
 */
import { describe, expect, it } from 'vitest'
import { matchesAll, otherTabHits, searchTerms } from '../composables/adoptSearch'

interface R { ip: string; tab: string; host: string }
const rows: R[] = [
  { ip: '10.99.1.1', tab: 'ssh', host: 'web01' },
  { ip: '10.99.1.2', tab: 'ssh', host: 'web02' },
  { ip: '10.99.1.3', tab: 'agent', host: 'web03' },
  { ip: '10.99.1.4', tab: 'import', host: 'web04' },
  { ip: '10.99.1.5', tab: 'import', host: 'web05' },
  { ip: '10.99.1.6', tab: 'import', host: 'db01' },
]
const text = (r: R) => `${r.ip} ${r.host}`

describe('adoptSearch', () => {
  it('空白隔開＝每個字都要有，不分大小寫', () => {
    expect(searchTerms('  WEB  01 ')).toEqual(['web', '01'])
    expect(matchesAll('10.99.1.1 web01', ['web', '01'])).toBe(true)
    expect(matchesAll('10.99.1.1 web01', ['web', 'db'])).toBe(false)
  })

  it('目前在 SSH 頁簽：其他頁簽的符合數依頁簽分組，不混進目前頁簽', () => {
    const scope = new Set(['10.99.1.1', '10.99.1.2'])
    const visible = new Set(['10.99.1.1', '10.99.1.2'])
    const h = otherTabHits(rows, scope, visible, ['web'], text)
    expect(h.tabs).toEqual([{ tab: 'import', n: 2 }, { tab: 'agent', n: 1 }])
    expect(h.total).toBe(3)
    expect(h.hiddenHere).toBe(0)
  })

  it('目前頁簽被篩選收起來的另外算（hiddenHere），不算成其他頁簽', () => {
    const scope = new Set(['10.99.1.1', '10.99.1.2'])
    const visible = new Set(['10.99.1.1'])          // web02 被「隱藏已納管」收起來
    const h = otherTabHits(rows, scope, visible, ['web0'], text)
    expect(h.hiddenHere).toBe(1)
    expect(h.total).toBe(3)
  })

  it('沒有搜尋字就不算', () => {
    expect(otherTabHits(rows, new Set(), new Set(), [], text)).toEqual({ tabs: [], total: 0, hiddenHere: 0 })
  })
})
