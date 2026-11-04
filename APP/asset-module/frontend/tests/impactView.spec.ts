/** 4.1 影響範圍查詢的畫面計算（2.043.11，GPT 審查 A2／A3／B1／B2）＋單一事件一列的測試狀態（A4）。 */
import { describe, expect, it } from 'vitest'
import { impactScale, notifySystems, snapshotBelongs, snapshotObsRows } from '../utils/impactView'
import { hostStatus } from '../utils/incidentHost'

const result = {
  dependents: [{}, {}, {}, {}],
  summary: {
    counts: { host: 2, esxi: 1, bizsys: 1 },
    notify: [{ name: '王小明' }, { name: '李小華' }],
    by_biz_system: [
      { api_id: 'N-2', assets: [{ owners: [{ name: '王小明' }] }] },
      { api_id: 'N-1', assets: [{ owners: [{ name: '王小明' }, { name: '李小華' }] }, { owners: [] }] },
    ],
  },
  observed: { auto_admin_ssh: [{ ip: '10.99.1.9', apis: ['N-1'], targets: 12, rows: 40 }], ambiguous: [{ ip: '10.99.1.8', rows: 3, apis: ['N-1', 'N-2'] }], stats: { excluded: 5 } },
}

describe('impactView', () => {
  it('B1：規模一行講清楚單位', () => {
    expect(impactScale(result)).toEqual({ nodes: 4, hosts: 3, systems: 2, notify: 2 })
    expect(impactScale(null)).toEqual({ nodes: 0, hosts: 0, systems: 0, notify: 0 })
  })
  it('B2：通知對象反查影響系統', () => {
    expect(notifySystems(result.summary)).toEqual({ 王小明: ['N-1', 'N-2'], 李小華: ['N-1'] })
  })
  it('A3：快照不屬於目前查詢對象就不用', () => {
    expect(snapshotBelongs({ node_id: 'host:A' }, 'host:A')).toBe(true)
    expect(snapshotBelongs({ node_id: 'host:B' }, 'host:A')).toBe(false)
    expect(snapshotBelongs(null, 'host:A')).toBe(false)
  })
  it('A2：看快照時連線推論下鑽只用快照裡那一份', () => {
    expect(snapshotObsRows(result, 'admin_ssh_auto')).toEqual([{ ip: '10.99.1.9', rows: 40, apis: 'N-1', extra: '連進 12 個系統' }])
    expect(snapshotObsRows(result, 'ambiguous')?.[0].apis).toBe('N-1、N-2')
    expect(snapshotObsRows(result, 'excluded')).toBeNull()        // 快照沒有逐列明細
  })
})

describe('hostStatus（A4）', () => {
  it('ESXi 不在範圍裡：用它上面 VM 的結果彙總', () => {
    const ms = [{ key: 'vm1', host_key: 'esx1', check: 'alive' }, { key: 'vm2', host_key: 'esx1', check: null }]
    expect(hostStatus('esx1', ms)).toBe('up')
    expect(hostStatus('esx1', [...ms, { key: 'vm3', host_key: 'esx1', check: 'no_response' }])).toBe('down')
    expect(hostStatus('esx1', [{ key: 'vm2', host_key: 'esx1', check: null }])).toBe('untested')
  })
  it('自己在範圍裡且測過：用自己的', () => {
    expect(hostStatus('esx1', [{ key: 'esx1', host_key: 'esx1', check: 'alive' }, { key: 'vm1', host_key: 'esx1', check: 'no_response' }])).toBe('up')
    expect(hostStatus('p1', [{ key: 'p1', host_key: 'p1', check: 'no_response' }])).toBe('down')
  })
})
