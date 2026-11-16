import { describe, expect, it } from 'vitest'
import { groupSegments, segmentsSum, GROUP_BUCKET } from '../utils/overviewSegments'

describe('系統總覽圓環分段（W221-014）', () => {
  it('全部未分類：中心＝全環境總量，不是 0', () => {
    const s = groupSegments({ total: 2535, core: 0, noncore: 0, test: 0, uncategorized: 2535 })
    expect(segmentsSum(s)).toBe(2535)
    expect(s.find(x => x.name === '未分類')?.count).toBe(2535)
  })
  it('部分未分類：四段加總＝總量', () => {
    const s = groupSegments({ total: 100, core: 40, noncore: 30, test: 10, uncategorized: 20 })
    expect(segmentsSum(s)).toBe(100)
  })
  it('全部已分類：未分類段為 0、加總＝總量', () => {
    const s = groupSegments({ total: 80, core: 50, noncore: 20, test: 10, uncategorized: 0 })
    expect(segmentsSum(s)).toBe(80)
    expect(s.find(x => x.name === '未分類')?.count).toBe(0)
  })
  it('總量 0：全部 0，不當機', () => {
    const s = groupSegments({ total: 0, core: 0, noncore: 0, test: 0, uncategorized: 0 })
    expect(segmentsSum(s)).toBe(0)
    expect(groupSegments(null)).toEqual([])
  })
  it('未分類可下鑽（bucket=uncategorized）', () => {
    expect(GROUP_BUCKET['未分類']).toBe('uncategorized')
  })
})
