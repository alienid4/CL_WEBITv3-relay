// 憑證盤點條件過濾的純函式：網址參數、區間快捷鍵（UTC+8 日曆月）、標籤、詳情頁返回連結。
import { describe, expect, it } from 'vitest'
import {
  CERT_BLANK, certBackLink, certDetailLink, certDuePreset, certDuePresetOn, certFilterChips, certFilterCount, certFilterParams, certSortKey, certToday,
} from '../composables/certFilters'

describe('certFilterParams', () => {
  it('只收認得的鍵、丟掉空值與別的頁面參數（tab／sort／page）', () => {
    expect(certFilterParams({ env: '正式', mount: '', tab: 'list', sort: 'name', page: '2', q: ' abc ' })).toEqual({ env: '正式', q: 'abc' })
  })
  it('query 值是陣列時取第一個', () => {
    expect(certFilterParams({ env: ['測試', '正式'] })).toEqual({ env: '測試' })
  })
})

describe('certFilterCount', () => {
  it('預設啟用中不算條件；選了已停用才算', () => {
    expect(certFilterCount({})).toBe(0)
    expect(certFilterCount({ active: 'on' })).toBe(0)
    expect(certFilterCount({ active: 'off', env: '正式' })).toBe(2)
  })
})

describe('certFilterChips', () => {
  it('每個條件一顆白話標籤；區間兩個鍵合成一顆、移除時一起清', () => {
    const c = certFilterChips({ env: '正式', due_from: '2026-12-01', due_to: '2026-12-31', rs: CERT_BLANK, risk: 'high', has_expiry: '0' })
    expect(c.map(x => x.text)).toEqual(['到期日：2026-12-01 ～ 2026-12-31', '環境：正式', '風險等級：高', '更換進度：（未設定）', '無到期日'])
    expect(c[0].key).toEqual(['due_from', 'due_to'])
  })
  it('沒有條件就沒有標籤', () => { expect(certFilterChips({})).toEqual([]) })
})

describe('區間快捷鍵（UTC+8 日曆月）', () => {
  // 2026-11-30 17:00 UTC 已經是 UTC+8 的 12-01 01:00 → 「本月」要算十二月
  const edge = new Date('2026-11-30T17:00:00Z')
  it('certToday 用 UTC+8 的日期', () => { expect(certToday(edge)).toEqual({ y: 2026, m: 12, d: 1 }) })
  it('本月／下個月：第一天到最後一天（含），跨年、閏年', () => {
    expect(certDuePreset('this_month', new Date('2026-11-15T00:00:00Z'))).toEqual({ due_from: '2026-11-01', due_to: '2026-11-30' })
    expect(certDuePreset('next_month', new Date('2026-11-15T00:00:00Z'))).toEqual({ due_from: '2026-12-01', due_to: '2026-12-31' })
    expect(certDuePreset('next_month', new Date('2026-12-15T00:00:00Z'))).toEqual({ due_from: '2027-01-01', due_to: '2027-01-31' })
    expect(certDuePreset('next_month', new Date('2028-01-10T00:00:00Z'))).toEqual({ due_from: '2028-02-01', due_to: '2028-02-29' })
    expect(certDuePreset('this_month', edge)).toEqual({ due_from: '2026-12-01', due_to: '2026-12-31' })
  })
  it('N 天內（含 7 天內）', () => { expect(certDuePreset('d30')).toEqual({ within: '30' }); expect(certDuePreset('d7')).toEqual({ within: '7' }) })
  it('本週＝UTC+8 週一到週日（含）；週日、週一、跨月邊界', () => {
    expect(certDuePreset('this_week', new Date('2026-11-01T00:00:00Z'))).toEqual({ due_from: '2026-10-26', due_to: '2026-11-01' })   // 週日
    expect(certDuePreset('this_week', new Date('2026-11-02T00:00:00Z'))).toEqual({ due_from: '2026-11-02', due_to: '2026-11-08' })   // 週一
    expect(certDuePreset('this_week', new Date('2026-11-01T17:00:00Z'))).toEqual({ due_from: '2026-11-02', due_to: '2026-11-08' })   // UTC+8 已是週一
    expect(certDuePresetOn({ due_from: '2026-11-02', due_to: '2026-11-08' }, new Date('2026-11-04T00:00:00Z'))).toBe('this_week')
  })
  it('下週＝下個週一到下個週日（含週日當天、跨月、跨年邊界）', () => {
    expect(certDuePreset('next_week', new Date('2026-11-01T00:00:00Z'))).toEqual({ due_from: '2026-11-02', due_to: '2026-11-08' })   // 今天是週日 → 下週從明天起
    expect(certDuePreset('next_week', new Date('2026-11-02T00:00:00Z'))).toEqual({ due_from: '2026-11-09', due_to: '2026-11-15' })   // 週一
    expect(certDuePreset('next_week', new Date('2026-12-30T00:00:00Z'))).toEqual({ due_from: '2027-01-04', due_to: '2027-01-10' })   // 跨年
    expect(certDuePreset('next_week', new Date('2026-11-01T17:00:00Z'))).toEqual({ due_from: '2026-11-09', due_to: '2026-11-15' })   // UTC+8 已是週一
    expect(certDuePresetOn({ due_from: '2026-11-09', due_to: '2026-11-15' }, new Date('2026-11-04T00:00:00Z'))).toBe('next_week')
  })
  it('哪個快捷鍵亮：網址參數剛好等於它的結果', () => {
    const now = new Date('2026-11-15T00:00:00Z')
    expect(certDuePresetOn({ due_from: '2026-12-01', due_to: '2026-12-31' }, now)).toBe('next_month')
    expect(certDuePresetOn({ within: '60' }, now)).toBe('d60')
    expect(certDuePresetOn({ due_from: '2026-12-01' }, now)).toBe('')           // 只有起日＝自訂，不亮快捷鍵
    expect(certDuePresetOn({}, now)).toBe('')
  })
})

describe('詳情頁往返保留清單脈絡', () => {
  it('詳情連結帶 ret（篩選、排序、頁次），返回連結原樣還原', () => {
    const link = certDetailLink('abc', { env: '正式', sort: 'name', desc: '1', page: '2', new: '1' })
    expect(link.startsWith('/v2/certs/abc?ret=')).toBe(true)
    const ret = decodeURIComponent(link.split('ret=')[1])
    expect(ret).toBe('env=%E6%AD%A3%E5%BC%8F&sort=name&desc=1&page=2')
    expect(certBackLink(ret)).toBe(`/v2/certs?${ret}`)
    expect(new URLSearchParams(ret).get('env')).toBe('正式')
  })
  it('沒有條件就不帶 ret；沒有 ret 就回清單首頁', () => {
    expect(certDetailLink('abc', {})).toBe('/v2/certs/abc')
    expect(certBackLink(undefined)).toBe('/v2/certs')
  })
})

describe('排序鍵', () => { it('項次欄對應 seq', () => { expect(certSortKey('__no')).toBe('seq'); expect(certSortKey('name')).toBe('name') }) })

describe('待補新到期日（已更換、稍後補登）', () => {
  it('篩選鍵 pending 進網址參數與標籤；風險等級篩選有「待補到期日」', () => {
    expect(certFilterParams({ pending: '1', env: '正式' })).toEqual({ pending: '1', env: '正式' })
    expect(certFilterChips({ pending: '1' }).map(c => c.text)).toEqual(['已更換待補到期日'])
    expect(certFilterChips({ risk: 'pending' }).map(c => c.text)).toEqual(['風險等級：待補到期日'])
    expect(certFilterCount({ pending: '1' })).toBe(1)
  })
})
