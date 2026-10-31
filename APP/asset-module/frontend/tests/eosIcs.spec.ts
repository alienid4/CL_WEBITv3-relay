import { describe, expect, it } from 'vitest'
import { selectIcsRows } from '../utils/eosIcs'

const item = (over: Record<string, unknown> = {}) => ({ name: 'Fixture Model', count: 1, status: 'expired', eos_date: '2000-01-01', trusted: true, ...over }) as any

describe('EOS 行事曆挑選（W221-006）', () => {
  it('已核對的完整日期：納入', () => {
    const r = selectIcsRows([[item()]])
    expect(r.rows).toEqual([{ name: 'Fixture Model', date: '20000101', count: 1, status: 'expired' }])
    expect(r.skippedUntrusted).toBe(0)
  })
  it('來源未核對／被拒（trusted=false）：不納入並計數', () => {
    const r = selectIcsRows([[item({ trusted: false })], [item({ name: 'B', trusted: false })]])
    expect(r.rows).toEqual([])
    expect(r.skippedUntrusted).toBe(2)
  })
  it('舊 API 沒有 trusted 欄位：不納入（不冒稱官方）', () => {
    const r = selectIcsRows([[item({ trusted: undefined })]])
    expect(r.rows).toEqual([])
    expect(r.skippedUntrusted).toBe(1)
  })
  it('月精度（只有年月）與格式錯：沒有完整日，不納入並計數', () => {
    const r = selectIcsRows([[item({ eos_date: '2030-04' }), item({ name: 'C', eos_date: 'not-a-date' })]])
    expect(r.rows).toEqual([])
    expect(r.skippedNoDate).toBe(2)
  })
  it('不是已過／一年內到期：忽略；同名同日只留一筆', () => {
    const r = selectIcsRows([[item({ status: 'ok' }), item(), item()]])
    expect(r.rows.length).toBe(1)
  })
})
