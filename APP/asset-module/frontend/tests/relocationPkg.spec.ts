import { describe, expect, it } from 'vitest'
import { accept, countMismatch, createLatest, flowText } from '../utils/relocationPkg'

describe('機房切換競態（搬遷包）', () => {
  it('慢回應晚到：舊請求被丟棄，只有最新一次被接受', () => {
    const l = createLatest()
    const a = l.begin()           // 選機房 A
    const b = l.begin()           // 馬上改選 B
    expect(accept(l, a, 'A', 'B')).toBe(false)
    expect(accept(l, b, 'B', 'B')).toBe(true)
  })
  it('token 是最新但機房已經變了：也丟棄（明細請求帶發出當下的機房）', () => {
    const l = createLatest()
    const t = l.begin()
    expect(accept(l, t, 'A', 'B')).toBe(false)
  })
  it('cancel 之後任何舊回應都不接受', () => {
    const l = createLatest()
    const t = l.begin()
    l.cancel()
    expect(accept(l, t, 'A', 'A')).toBe(false)
  })
})

describe('台數一致性', () => {
  it('一致：沒有錯誤', () => { expect(countMismatch({ count: 2, members: [1, 2] })).toBe('') })
  it('不一致：給錯誤字串而不是兩個數字', () => {
    expect(countMismatch({ count: 3, members: [1, 2] })).toContain('對不上')
  })
  it('沒有明細：空字串', () => { expect(countMismatch(null)).toBe('') })
})

describe('證據方向', () => {
  it('用系統→系統的流向', () => { expect(flowText({ flow: 'T-1→S-1' }, '雙向')).toBe('T-1→S-1') })
  it('沒有 flow 時退回整體方向', () => { expect(flowText({}, '雙向')).toBe('雙向') })
  it('都沒有：—', () => { expect(flowText(null, null)).toBe('—') })
})
