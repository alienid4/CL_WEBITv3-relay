import { describe, expect, it } from 'vitest'
import { MANUAL_SECTIONS, manualBehind, pendingItems, pickSection, sectionTexts, validateSections } from '../content/manual'

describe('使用手冊資料結構', () => {
  it('現有內容通過結構檢查', () => {
    expect(validateSections(MANUAL_SECTIONS)).toEqual([])
  })
  it('至少九節，編號連續', () => {
    expect(MANUAL_SECTIONS.length).toBeGreaterThanOrEqual(9)
    MANUAL_SECTIONS.forEach((s, i) => expect(s.no).toBe(i + 1))
  })
  it('缺欄位會被抓到', () => {
    const bad = [{ ...MANUAL_SECTIONS[0], steps: [], notes: [] }]
    const r = validateSections(bad as any)
    expect(r.some(x => x.includes('怎麼做'))).toBe(true)
    expect(r.some(x => x.includes('注意事項'))).toBe(true)
  })
  it('圖沒有 svg 也沒有 src 會被抓到', () => {
    const s = { ...MANUAL_SECTIONS[0], figures: [{ id: 'x', caption: 'c', alt: 'a' }] }
    expect(validateSections([s] as any).some(x => x.includes('svg'))).toBe(true)
  })
  it('文字內不含真實網段或公司識別字', () => {
    const banned = [/10\.9\d\./, new RegExp('cat' + 'hay', 'i'), /示範/, /demo-host/i]
    for (const s of MANUAL_SECTIONS) for (const t of sectionTexts(s)) for (const b of banned) expect(t).not.toMatch(b)
    for (const s of MANUAL_SECTIONS) for (const f of s.figures) expect(f.svg ?? '').not.toMatch(/10\.9\d\./)
  })
})

describe('使用手冊小工具', () => {
  it('pickSection：找不到回第一節', () => {
    expect(pickSection(MANUAL_SECTIONS, 'nope').id).toBe(MANUAL_SECTIONS[0].id)
    expect(pickSection(MANUAL_SECTIONS, MANUAL_SECTIONS[2].id).no).toBe(3)
  })
  it('manualBehind：只在手冊版本較舊時為真，讀不到不誤報', () => {
    expect(manualBehind('2.041.19', '2.042.0')).toBe(true)
    expect(manualBehind('2.041.19', '2.041.19')).toBe(false)
    expect(manualBehind('2.041.19', '2.041.3')).toBe(false)
    expect(manualBehind('2.041.19', '')).toBe(false)
  })
  it('沒有未解的待確認項目', () => {
    expect(pendingItems(MANUAL_SECTIONS)).toEqual([])
  })
  it('不再出現審查指出的不精確說法', () => {
    const all = MANUAL_SECTIONS.flatMap(sectionTexts).join('|')
    for (const w of ['清冊每一筆', '任何一張表格', '一定比', '上架助手']) expect(all).not.toContain(w)
  })
})
