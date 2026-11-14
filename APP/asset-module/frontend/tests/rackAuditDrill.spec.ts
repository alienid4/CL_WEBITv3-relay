// 2.053.1（批 17 審查 P2-4）：5.1「超過一年未盤／從沒盤過」下鑽清單＝格子數字（機房與每一櫃）。
import { describe, expect, it } from 'vitest'
import { auditDrillKeys, staleCountOf } from '../utils/rackAuditDrill'

const room = {
  racks: [{ rack: 'B1', keys: ['a', 'b', 'c'] }, { rack: 'B2', keys: ['d', 'e'] }, { rack: 'B3', keys: ['f'] }],
  audit_stale_keys: ['a', 'c', 'd'],
  audit_never_keys: ['c'],
}

describe('auditDrillKeys', () => {
  it('機房：後端清單原樣', () => {
    expect(auditDrillKeys(room, 'stale')).toEqual(['a', 'c', 'd'])
    expect(auditDrillKeys(room, 'never')).toEqual(['c'])
  })
  it('指定櫃：只留那一櫃在用的', () => {
    expect(auditDrillKeys(room, 'stale', 'B1')).toEqual(['a', 'c'])
    expect(auditDrillKeys(room, 'stale', 'B2')).toEqual(['d'])
    expect(auditDrillKeys(room, 'stale', 'B3')).toEqual([])
    expect(auditDrillKeys(room, 'never', 'B2')).toEqual([])
    expect(auditDrillKeys(room, 'never', 'B1')).toEqual(['c'])
  })
  it('每櫃格子數字＝點下去的筆數，各櫃加總＝機房數字', () => {
    const per = room.racks.map(t => staleCountOf(room, t.rack))
    expect(per).toEqual([2, 1, 0])
    room.racks.forEach(t => expect(staleCountOf(room, t.rack)).toBe(auditDrillKeys(room, 'stale', t.rack).length))
    expect(per.reduce((a, b) => a + b, 0)).toBe(auditDrillKeys(room, 'stale').length)
  })
  it('沒有機房或沒有欄位：空清單', () => {
    expect(auditDrillKeys(null, 'stale')).toEqual([])
    expect(auditDrillKeys({ racks: [] }, 'never')).toEqual([])
  })
})
