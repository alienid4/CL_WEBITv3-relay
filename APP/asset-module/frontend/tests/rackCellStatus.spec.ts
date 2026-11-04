/** 機櫃正面圖每一格的狀態標示（2.043.13）：一組固定的字，系統設備與 Excel 層共用。 */
import { describe, expect, it } from 'vitest'
import { CELL_ICON, CELL_TEXT, FLAG_ICON, ICON_LEGEND, cellIcons, sysCellStatus, xlCellStatus } from '../utils/rackCellStatus'

describe('rackCellStatus', () => {
  it('規格裡的六種字都有', () => {
    for (const t of ['一致', 'Excel 有・系統無', '系統有・Excel 無', 'Excel 尚未更新', '下架待確認', '關機']) expect(Object.values(CELL_TEXT)).toContain(t)
  })
  it('系統設備：關機／下架待確認優先，再看跟 Excel 對的結果', () => {
    expect(sysCellStatus('off', 'OK')).toEqual({ code: 'off', label: '關機' })
    expect(sysCellStatus('pending', 'ABSENT')).toEqual({ code: 'pending', label: '下架待確認' })
    expect(sysCellStatus('active', 'OK').label).toBe('一致')
    expect(sysCellStatus('active', 'ABSENT').label).toBe('系統有・Excel 無')
    expect(sysCellStatus('active', '')).toEqual({ code: '', label: '' })   // 沒有 Excel 對照：不寫
  })
  it('Excel 層', () => {
    expect(xlCellStatus('MISSING').label).toBe('Excel 有・系統無')
    expect(xlCellStatus('STALE').label).toBe('Excel 尚未更新')
    expect(xlCellStatus('WEIRD').label).toBe('WEIRD')
  })
})

describe('rackCellStatus 圖示（2.044.8）', () => {
  it('每個狀態碼都有圖示，標記在前', () => {
    for (const k of Object.keys(CELL_TEXT)) expect(CELL_ICON[k]).toBeTruthy()
    expect(cellIcons('', 'OK')).toBe('✓')
    expect(cellIcons('疑似登記錯誤', 'MISSING')).toBe('!✕')
    expect(cellIcons('現場更正', '')).toBe('✎')
    expect(cellIcons('', '')).toBe('')
    expect(cellIcons('', 'pending')).toBe('?')
  })
  it('圖示說明涵蓋所有用到的圖示', () => {
    const used = new Set([...Object.values(CELL_ICON), ...Object.values(FLAG_ICON)])
    const shown = new Set(ICON_LEGEND.map(x => x.icon))
    for (const i of used) expect(shown.has(i)).toBe(true)
  })
})
