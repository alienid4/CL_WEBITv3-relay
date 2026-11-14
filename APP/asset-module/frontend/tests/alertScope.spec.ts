// Q9（2.066.x）：今日概況「現在仍在告警」的範圍切換（?all=1）與同分鐘同來源收合的下鑽（?ag=）。純函式，假資料（192.0.2.x）。
import { describe, expect, it } from 'vitest'
import { attentionCount, bucketQuery, groupQuery, groupRows, groupsTotal, groupTitle, scopeOf, toggleScopeQuery } from '../utils/alertScope'

const rows = [1, 2, 3, 4].map(id => ({ id, monitor: `P${id}.exe` }))
const groups = [
  { group: '2026-11-04 08:00|7', n: 3, ids: [1, 2, 3], started_at: '2026-11-04 08:00:00', who: 'TEST-AP01' },
  { group: '2026-11-04 08:01|8', n: 1, ids: [4], started_at: '2026-11-04 08:01:00', ip: '192.0.2.11' },
]

describe('範圍', () => {
  it('網址 all=1 才是看全部，其餘一律通知範圍', () => {
    expect(scopeOf('1')).toBe('all')
    expect(scopeOf(undefined)).toBe('policy')
    expect(scopeOf('0')).toBe('policy')
  })
  it('切換保留其他參數、收起下鑽', () => {
    expect(toggleScopeQuery({ x: 'a', ag: 'g' }, true)).toEqual({ x: 'a', all: '1' })
    expect(toggleScopeQuery({ x: 'a', all: '1', ag: 'g' }, false)).toEqual({ x: 'a' })
  })
})

describe('收合與下鑽', () => {
  it('件數加總＝列數（加總＝下鑽）', () => {
    expect(groupsTotal(groups)).toBe(rows.length)
  })
  it('點件數＝這一組的那幾筆；再點一次收起', () => {
    expect(groupRows(rows, groups, groups[0].group).map(r => r.id)).toEqual([1, 2, 3])
    expect(groupQuery({ all: '1' }, groups[0])).toEqual({ all: '1', ag: groups[0].group })
    expect(groupQuery({ ag: groups[0].group }, groups[0])).toEqual({})
  })
  it('組已經不在（恢復了）＝空清單，不亂列', () => {
    expect(groupRows(rows, groups, 'nope')).toEqual([])
  })
  it('標題寫開始時間、來源、件數', () => {
    expect(groupTitle(groups[0])).toBe('11-04 08:00 開始・TEST-AP01・3 件')
    expect(groupTitle(groups[1])).toContain('192.0.2.11')
  })
})

describe('需要注意（2.066.x）', () => {
  it('摺疊區下鑽：?ab=類別，再點一次收起，切換時收起組下鑽', () => {
    expect(bucketQuery({ all: '1', ag: 'g' }, 'young')).toEqual({ all: '1', ab: 'young' })
    expect(bucketQuery({ ab: 'young' }, 'young')).toEqual({})
  })
  it('件數：沒資料寫原因、心跳壞了標監控盲區，0 不會被讀成沒事', () => {
    expect(attentionCount({ status: 'disabled' })).toBe('未啟用')
    expect(attentionCount({ status: 'uncollected', attention: null })).toBe('未收集')
    expect(attentionCount({ status: 'ok', attention: { n: 2, blind: null } })).toBe('2 件')
    expect(attentionCount({ status: 'stale', attention: { n: 0, blind: '監控盲區：…' } })).toBe('0 件（監控盲區）')
  })
})
