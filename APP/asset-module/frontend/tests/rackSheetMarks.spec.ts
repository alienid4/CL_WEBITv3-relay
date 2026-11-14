/** A4 機櫃盤點表備註只放短標記（2.044.8） */
import { describe, expect, it } from 'vitest'
import { shortMark, shortMarks } from '../utils/rackSheetMarks'

describe('rackSheetMarks', () => {
  it('後端的長字縮成短標記，「另一段」不印', () => {
    expect(shortMarks(['疑似登記錯誤（同一段 U 太多筆）', '另一段：36-37U', '另一段：34-35U'])).toBe('疑似登記錯誤')
    expect(shortMarks(['關機未下架', '未觀察到（只有登記）'])).toBe('關機未下架、未觀察到')
    expect(shortMarks(['待確認', '待確認（只有 DY）'])).toBe('待確認')
    expect(shortMark('推定同一台，另 2 筆：a（管理卡）、b（本體）')).toBe('疑似重複')
    expect(shortMark('管理 IP：10.99.1.1（iDRAC）')).toBe('含管理口')
    expect(shortMark('管理介面・找不到本體')).toBe('找不到本體')
  })
  it('對不到的取前 6 字；空的不印', () => {
    expect(shortMark('已下線待CIA異動處理')).toBe('已下線待CI')
    expect(shortMarks([])).toBe('')
    expect(shortMarks(undefined)).toBe('')
  })
})
