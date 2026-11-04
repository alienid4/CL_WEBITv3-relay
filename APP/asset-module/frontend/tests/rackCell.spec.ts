/** 機櫃格子重排（2.043.19，R20／R21）：第 1 格主機名稱／廠牌、第 6 格系統｜管理口、說明列六格。 */
import { describe, expect, it } from 'vitest'
import { CELL_LEGEND, firstCellText, isRealHostName, mgmtShort, sysCellText } from '../utils/rackCell'

describe('rackCell', () => {
  it('沒有真的主機名稱（空、等於機型、DYN-）就只放廠牌', () => {
    expect(isRealHostName('web01', 'Dell R760')).toBe(true)
    expect(isRealHostName('', 'Dell R760')).toBe(false)
    expect(isRealHostName('dell r760', 'Dell R760')).toBe(false)
    expect(isRealHostName('DYN-10.99.1.5', null)).toBe(false)
    expect(firstCellText({ name: 'web01', model: 'R760', vendor: 'Dell' })).toBe('web01')
    expect(firstCellText({ name: 'R760', model: 'R760', vendor: 'Dell' })).toBe('Dell')
    expect(firstCellText({ name: 'DYN-10.99.1.5', model: '', vendor: 'HPE', remark: '待確認' })).toBe('HPE｜待確認')
    expect(firstCellText({ name: 'web01', model: 'R760', vendor: 'Dell', remark: '前一台' })).toBe('web01｜前一台')
  })
  it('第 6 格：系統｜管理口', () => {
    expect(mgmtShort({ ip: '10.99.1.5', kind: 'iDRAC' })).toBe('iDRAC 10.99.1.5')
    expect(sysCellText(['網銀'], { ip: '10.99.1.5', kind: 'iDRAC' })).toBe('網銀｜iDRAC 10.99.1.5')
    expect(sysCellText([], { ip: null, kind: null })).toBe('OOB 無 IP')
    expect(sysCellText(['A', 'B'], null)).toBe('A、B')
    expect(sysCellText([], null)).toBe('')
  })
  it('格子說明六格，編號 1 2 3 5 6 7', () => {
    expect(CELL_LEGEND.map(x => x.n)).toEqual([1, 2, 3, 5, 6, 7])
    expect(CELL_LEGEND.map(x => x.label)).toEqual(['1 主機名稱／廠牌', '2 機型', '3 IP', '5 序號', '6 系統｜管理口', '7 狀態圖示'])
  })
})
