import { describe, expect, it } from 'vitest'
import { coverageOf, coverageText } from '../utils/healthCoverage'

describe('體檢涵蓋文案', () => {
  it('舊 API 沒有 check_states 時不默認全驗', () => {
    const t = coverageText({ checks_total: 10 })
    expect(t).toBe('資料不足，無法判斷涵蓋')
    expect(t).not.toMatch(/已驗通過 10/)
    expect(coverageText(null)).toContain('資料不足')
    expect(coverageText({ checks_total: 10, check_states: {} })).toContain('資料不足')
  })
  it('四態加總等於共幾項，未驗與不適用逐項帶原因', () => {
    const h: any = { check_states: {
      lost: { state: 'passed', reason: '' }, eos: { state: 'found', reason: 'x' },
      account: { state: 'not_checked', reason: '這台尚無帳號盤點' },
      drift: { state: 'not_applicable', reason: '尚未通過上線前檢查，無基準可比' },
    } }
    const c = coverageOf(h)
    expect(c.passed + c.found + c.notChecked + c.notApplicable).toBe(c.total)
    expect(c.gaps.map(g => g.key).sort()).toEqual(['account', 'drift'])
    expect(coverageText(h)).toBe('已驗通過 1／已驗有發現 1／未驗 1／不適用 1（共 4 項）')
  })
  it('不認得的狀態當未驗', () => {
    const c = coverageOf({ check_states: { lost: { state: 'weird' as any, reason: '' } } })
    expect(c.notChecked).toBe(1)
    expect(c.passed).toBe(0)
  })
})
