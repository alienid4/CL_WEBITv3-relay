/** 目錄清理候選（2.046.0，R18／R19）。 */
import { describe, expect, it } from 'vitest'
import { candRows, nightConfirmText } from '../utils/hkCandidates'

describe('hkCandidates', () => {
  it('每個候選一列；最大一項與原因寫成字；權限／逾時標在每一列', () => {
    const rows = candRows([{ mount: '/data', pct: 91, flags: ['權限不足：略過 3 個沒權限的目錄（數字是下限）'], items: [
      { path: '/data/app/logs', size: '4.8 GB', kb: 5000000, reasons: ['log', '超過 2 年沒動（佔 60%）'], biggest: { kind: 'file', path: '/data/app/logs/a.log', size: '3.0 GB' } },
      { path: '/data/tmp', size: '2.9 GB', kb: 3000000, reasons: ['tmp'], biggest: null }] }])
    expect(rows.map(r => r.__no)).toEqual([1, 2])
    expect(rows[0].biggest).toBe('檔案 /data/app/logs/a.log（3.0 GB）')
    expect(rows[0].reasons).toBe('log、超過 2 年沒動（佔 60%）')
    expect(rows[1].flags).toContain('權限不足')
  })
  it('檔案系統沒有大目錄也列出來，不默默消失', () => {
    const rows = candRows([{ mount: '/u01', pct: 85, flags: [], items: [] }])
    expect(rows).toHaveLength(1)
    expect(rows[0].reasons).toContain('沒有找到')
    expect(candRows(null)).toEqual([])
  })
  it('夜間自動的確認文字講清楚時段與規則', () => {
    const t = nightConfirmText(true)
    for (const s of ['19:00～07:00', '≥80%', '一次一台', '主機忙就跳過', '08:30', '唯讀']) expect(t).toContain(s)
    expect(nightConfirmText(false)).toContain('手動掃描不受影響')
  })
})
