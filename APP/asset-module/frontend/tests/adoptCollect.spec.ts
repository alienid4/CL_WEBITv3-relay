// 批 23（V17）：6.1「開始收集」V2 原生——舊頁 pages/adopt.vue 抽出來的規則（utils/adoptCollect）。
import { describe, expect, it } from 'vitest'
import { addSegments, asCovOnly, covRows, dispatchGate, lastUnresolvedFail, pqPrune, pqToSend, runSumLine, runSumRows, targetSplit } from '../utils/adoptCollect'

describe('targetSplit（舊頁同一套粗估）', () => {
  it('CIDR、範圍、單一 IP、/32 分開算', () => {
    expect(targetSplit('10.99.1.0/24')).toEqual({ ranges: 254, singles: 0 })
    expect(targetSplit('10.99.2.10-20')).toEqual({ ranges: 11, singles: 0 })
    expect(targetSplit('10.99.3.5\n10.99.3.6/32')).toEqual({ ranges: 0, singles: 2 })
    expect(targetSplit('10.99.1.0/24, 10.99.3.5; 10.99.2.10-12')).toEqual({ ranges: 257, singles: 1 })
    expect(targetSplit('  ')).toEqual({ ranges: 0, singles: 0 })
  })
})

describe('addSegments', () => {
  it('接在後面、已經有的不重複', () => {
    expect(addSegments('10.99.1.0/24', ['10.99.1.0/24', '10.99.2.0/24'])).toEqual({ text: '10.99.1.0/24\n10.99.2.0/24', added: 1 })
    expect(addSegments('', ['10.99.5.0/24'])).toEqual({ text: '10.99.5.0/24', added: 1 })
  })
})

describe('lastUnresolvedFail', () => {
  it('之後同一台成功過就不算（新到舊）', () => {
    const a = [{ id: 3, target_ip: '10.99.0.1', ok: 1 }, { id: 2, target_ip: '10.99.0.1', ok: 0 }, { id: 1, target_ip: '10.99.0.2', ok: 0 }]
    expect(lastUnresolvedFail(a)?.id).toBe(1)
    expect(lastUnresolvedFail([{ id: 1, target_ip: '10.99.0.1', ok: 1 }])).toBeNull()
  })
})

describe('covRows', () => {
  const segs = [
    { cidr: 'a', total: 10, collected: 0, never: true }, { cidr: 'b', total: 10, collected: 5, never: false },
    { cidr: 'c', total: 10, collected: 10, never: false },
  ]
  it('補 % 並依從未／部分／全收篩', () => {
    expect(covRows(segs, '').map(s => s.pct)).toEqual([0, 50, 100])
    expect(covRows(segs, 'never').map(s => s.cidr)).toEqual(['a'])
    expect(covRows(segs, 'partial').map(s => s.cidr)).toEqual(['b'])
    expect(covRows(segs, 'full').map(s => s.cidr)).toEqual(['c'])
  })
  it('網址亂填就當全部', () => { expect(asCovOnly('x')).toBe(''); expect(asCovOnly('never')).toBe('never') })
})

describe('runSumRows／runSumLine', () => {
  const sm = { total: 2, rows: [{ ip: '1', bucket: 'other' }, { ip: '2', bucket: 'failed' }], buckets: [{ key: 'failed', label: '✕ 失敗' }, { key: 'other', label: '其他' }], counts: { failed: 1, other: 1 } }
  it('點哪一堆列哪一堆', () => {
    expect(runSumRows(sm, '*').length).toBe(2)
    expect(runSumRows(sm, 'other').map(r => r.ip)).toEqual(['1'])
    expect(runSumRows(null, '*')).toEqual([])
    expect(runSumLine(sm)).toBe('完成：2 台 — ✕ 失敗 1・其他 1')
  })
})

describe('dispatchGate（開始收集前，舊頁同一順序）', () => {
  it('空白絕不代表全部', () => {
    expect(dispatchGate({ targets: ' ', segPicked: 0, regOnly: true })).toEqual({ kind: 'warn', text: '請先輸入網段或 IP（空白不代表全部）' })
    expect(dispatchGate({ targets: '', segPicked: 2, regOnly: true }).kind).toBe('warn')
  })
  it('勾了網段沒加入範圍就不送', () => {
    expect(dispatchGate({ targets: '10.99.0.1', segPicked: 1, regOnly: true }).kind).toBe('warn')
  })
  it('超過 256 個位址要確認，句子同舊頁', () => {
    const g = dispatchGate({ targets: '10.99.0.0/23', segPicked: 0, regOnly: false, batchSize: 1024, gapSeconds: 120 })
    expect(g).toEqual({ kind: 'confirm', text: '這次會收 510 個位址，分 1 批，探測約 1 分鐘起跳（連得進去的還要另外收）。確定？' })
    expect(dispatchGate({ targets: '10.99.0.0/24', segPicked: 0, regOnly: true }).kind).toBe('go')
  })
})

describe('納管後收集排隊的勾選', () => {
  const items = [{ asset_serial: 'S1' }, { asset_serial: 'S2' }]
  it('送出去的只有隊伍裡還在而且勾了的', () => {
    expect(pqToSend({ S1: true, S9: true }, items)).toEqual(['S1'])
    expect(pqPrune({ S1: true, S2: false, S9: true }, items)).toEqual({ S1: true })
  })
})
