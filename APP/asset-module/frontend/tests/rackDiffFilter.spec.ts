/** 各櫃進度 → 差異處理的下鑽（2.043.5，GPT 審查 4／5）。 */
import { describe, expect, it } from 'vitest'
import { inRackDiff, rackDiffQuery, rackDiffStatus, rackKey } from '../utils/rackDiffFilter'

const diffs = [
  { host: 'A 01', kind: '不在', status: '待處理' },
  { host: 'A 01', kind: '型號不符', status: '已處理' },
  { host: 'A 01', kind: '櫃上有但沒登記', status: '待處理' },
  { host: 'A 02', kind: '不在', status: '待處理' },
]
const hit = (rk: string, dk: string) => diffs.filter(d => (!rackDiffStatus(dk) || d.status === rackDiffStatus(dk)) && inRackDiff(d, rk, dk))

describe('rackDiff', () => {
  it('網址帶這一櫃＋這一類', () => {
    expect(rackDiffQuery(7, 'A', '01', 'gone')).toEqual({ tab: 'diffs', task: '7', rk: 'A 01', dk: 'gone' })
    expect(rackKey('A', '01')).toBe('A 01')
  })
  it('不在／不符／沒登記只列這一櫃那一類（不分處理狀態，跟格子同口徑）', () => {
    expect(hit('A 01', 'gone')).toHaveLength(1)
    expect(hit('A 01', 'bad').map(d => d.kind)).toEqual(['型號不符'])
    expect(hit('A 01', 'extra')).toHaveLength(1)
  })
  it('差異未處理＝這一櫃待處理的全部種類', () => {
    expect(hit('A 01', 'open')).toHaveLength(2)
  })
  it('沒帶參數＝不篩、預設待處理', () => {
    expect(inRackDiff(diffs[3], '', '')).toBe(true)
    expect(rackDiffStatus('')).toBe('待處理')
  })
})
