import { describe, expect, it } from 'vitest'
import { icsDate, selectIcsRows } from '../utils/eosIcs'

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
  it('不存在的曆日、尾巴垃圾一律不輸出（Codex 62）', () => {
    for (const bad of ['2030-99-99', '2030-02-30', '2030-04-31', '2030-13-01', '2030-00-10', '2030-01-00', '2030-04-01junk', '2030-04-0', '2030-4-1']) {
      expect(icsDate(bad)).toBeNull()
      const r = selectIcsRows([[item({ eos_date: bad })]])
      expect(r.rows).toEqual([])
      expect(r.skippedNoDate).toBe(1)
    }
  })
  it('真實曆日含閏年與時間尾巴', () => {
    expect(icsDate('2028-02-29')).toBe('20280229')
    expect(icsDate('2000-02-29')).toBe('20000229')
    expect(icsDate('1900-02-29')).toBeNull()
    expect(icsDate('2029-02-29')).toBeNull()
    expect(icsDate('2030-04-30')).toBe('20300430')
    expect(icsDate('2030-04-01T08:00:00')).toBe('20300401')
    expect(icsDate('2030-04-01 08:00')).toBe('20300401')
  })
})
