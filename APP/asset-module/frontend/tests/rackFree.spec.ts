import { describe, expect, it } from 'vitest'
import { freeUText } from '../utils/rackFree'

describe('freeUText（機櫃可用 U 顯示字，W221-001）', () => {
  it('沒有未定位：與原本完全相同（純數字）', () => {
    expect(freeUText({ free_u: 42 })).toBe('42')
    expect(freeUText({ free_u: 42, unplaced_count: 0 })).toBe('42')
  })
  it('部分定位（有一台以上未定位）：給上限與未定位數', () => {
    expect(freeUText({ free_u: 42, unplaced_count: 1 })).toBe('≤42（另 1 台未定位）')
    expect(freeUText({ free_u: 40, unplaced_count: 3 })).toBe('≤40（另 3 台未定位）')
  })
  it('全部未定位（已用 U 無資料）：待確認，不顯示確定的 42', () => {
    const t = freeUText({ free_u: 42, unplaced_count: 2 }, true)
    expect(t).toBe('≤42（U 位未登錄，待確認）')
    expect(t).not.toBe('42')
  })
  it('U 位未登錄時不論 unplaced_count 為何，都不是確定值', () => {
    expect(freeUText({ free_u: 42 }, true)).toContain('待確認')
  })
})
