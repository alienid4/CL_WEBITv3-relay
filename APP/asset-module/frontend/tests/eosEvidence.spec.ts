import { describe, expect, it } from 'vitest'
import { BUCKET_LABEL, TRUST_HINT, TRUST_LABEL, completeMissing, derivedPrecision, saveMissing, trustTone, validDate } from '../utils/eosEvidence'
import { selectIcsRows } from '../utils/eosIcs'

const TODAY = '2026-11-02'

describe('EOS 批二：可信度用詞統一', () => {
  it('只有三個詞：已確認／推定／未知，每個都有提示', () => {
    expect(Object.values(TRUST_LABEL)).toEqual(['已確認', '推定', '未知'])
    expect(Object.keys(TRUST_HINT)).toEqual(['confirmed', 'presumed', 'unknown'])
    expect(trustTone('confirmed')).toBe('prod')
    expect(trustTone('presumed')).toBe('test')
    expect(trustTone('unknown')).toBe('none')
    expect(trustTone(undefined)).toBe('none')
  })
  it('覆蓋率格名不含「核准」一詞', () => {
    for (const v of [...Object.values(BUCKET_LABEL), ...Object.values(TRUST_HINT)]) expect(v).not.toContain('核准')
  })
  it('ICS 列帶可信度用詞（舊 API 沒帶 trust_label 一律寫已確認；能進 ICS 的本來就只有可信的）', () => {
    const base = { name: 'X', count: 2, status: 'expired', eos_date: '2000-01-01', trusted: true } as any
    expect(selectIcsRows([[base]]).rows[0].trustLabel).toBe('已確認')
    expect(selectIcsRows([[{ ...base, trust_label: '已確認' }]]).rows[0].trustLabel).toBe('已確認')
    expect(selectIcsRows([[{ ...base, trusted: false, trust_label: '推定' }]]).rows).toEqual([])      // 推定不進行事曆
  })
})

describe('EOS 批二：證據表單提示（後端另有同規則，這裡只做即時提示）', () => {
  it('日期有效性與精度', () => {
    expect(validDate('2026-02-30')).toBe(false)
    expect(validDate('2028-02-29')).toBe(true)
    expect(derivedPrecision('2027-03-01')).toBe('day')
    expect(derivedPrecision('2029-07')).toBe('month')
    expect(derivedPrecision(null)).toBe('none')
    expect(derivedPrecision('2027-13')).toBe('none')
  })
  it('存檔至少要查證日期＋官方原文引用；日期不能在未來；零寬字元視為空', () => {
    expect(saveMissing({}, TODAY)).toEqual(['查證日期', '官方原文引用'])
    expect(saveMissing({ verified_on: '2026-11-03', quote_text: 'x' }, TODAY)).toEqual(['查證日期（不能在未來）'])
    expect(saveMissing({ verified_on: TODAY, quote_text: '​ ​' }, TODAY)).toEqual(['官方原文引用'])
    expect(saveMissing({ verified_on: TODAY, quote_text: '官方原文' }, TODAY)).toEqual([])
  })
  it('證據完整度：缺查證人／適用範圍／精度；精度要與表內日期一致；來源網址表內有就不用填', () => {
    const f = { verified_on: '2026-10-30', quote_text: 'q' }
    expect(completeMissing(f, '2027-03-01', 'https://example.invalid/x', TODAY)).toEqual(['查證人', '適用範圍', '日期精度'])
    expect(completeMissing({ ...f, verified_by: 'a', scope_text: 's', date_precision: 'month' }, '2027-03-01', 'https://e.invalid/', TODAY)[0]).toContain('日期精度（表內日期是')
    expect(completeMissing({ ...f, verified_by: 'a', scope_text: 's', date_precision: 'day' }, '2027-03-01', 'https://e.invalid/', TODAY)).toEqual([])
    expect(completeMissing({ ...f, verified_by: 'a', scope_text: 's', date_precision: 'day' }, '2027-03-01', null, TODAY)).toEqual(['來源網址'])
    expect(completeMissing({ ...f, verified_by: 'a', scope_text: 's', date_precision: 'day', source_url: 'https://e.invalid/' }, '2027-03-01', null, TODAY)).toEqual([])
  })
})
