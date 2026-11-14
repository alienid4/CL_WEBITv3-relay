/**
 * 2.047.1（GPT 審查 P1-4、P2-5）：
 *   - 已移植完成的舊網址，legacy=1 也不能再開舊頁（isPorted）
 *   - 匯出全部時照畫面目前的排序（sortRows 與畫面排序同一份）
 */
import { describe, expect, it, vi } from 'vitest'
import { isPorted, v2LegacyTarget } from '../utils/v2LegacyMap'
import { sortRows } from '../composables/useSort'

describe('isPorted', () => {
  it('已移植的頁（含動態路徑）都算', () => {
    for (const p of ['/activity', '/model-specs', '/reports/host-sources', '/reports/monthly', '/scan-results', '/golive', '/golive/S-1', '/blast/checklist/7', '/activity/'])
      expect(isPorted(p), p).toBe(true)
  })
  it('還沒移植的不算（legacy=1 仍可開）', () => {
    for (const p of ['/assets/x/y', '/golivex', '/blast/checklist', '/reports', '/login']) expect(isPorted(p), p).toBe(false)
  })
  it('已移植的頁都有 V2 轉址目標', () => {
    for (const p of ['/activity', '/model-specs', '/reports/host-sources', '/reports/monthly', '/scan-results', '/golive', '/golive/S-1', '/blast/checklist/7'])
      expect(v2LegacyTarget(p, { legacy: '1' }), p).not.toBeNull()
  })
})

describe('v2-legacy 中介層', () => {
  it('已移植的頁帶 legacy=1 照樣轉到 V2；還沒移植的仍放行', async () => {
    vi.stubGlobal('defineNuxtRouteMiddleware', (f: any) => f)
    vi.stubGlobal('navigateTo', (t: any) => ({ redirected: t }))
    const mw = (await import('../middleware/v2-legacy.global')).default as any
    const route = (path: string, query: Record<string, string> = {}) => ({ path, query })
    expect(mw(route('/activity', { legacy: '1' }), route('/v2'))).toMatchObject({ redirected: { path: '/v2/settings' } })
    expect(mw(route('/golive/S-1', { legacy: '1' }), route('/golive/S-1', { legacy: '1' }))).toMatchObject({ redirected: { path: '/v2/golive/S-1' } })
    expect(mw(route('/golivex', { legacy: '1' }), route('/v2'))).toBeUndefined()
    vi.unstubAllGlobals()
  })
})

describe('sortRows（匯出照畫面排序）', () => {
  const rows = [{ n: 2, ip: '10.99.1.110' }, { n: 10, ip: '10.99.1.9' }, { n: null, ip: '' }]
  it('升冪／降冪，空值永遠在後', () => {
    expect(sortRows(rows, 'n', 'asc').map(r => r.n)).toEqual([2, 10, null])
    expect(sortRows(rows, 'n', 'desc').map(r => r.n)).toEqual([10, 2, null])
    expect(sortRows(rows, 'ip', 'asc').map(r => r.ip)).toEqual(['10.99.1.9', '10.99.1.110', ''])
  })
  it('沒有排序欄就照原順序、不改原陣列', () => {
    expect(sortRows(rows, '', 'asc')).toBe(rows)
    sortRows(rows, 'n', 'desc')
    expect(rows[0].n).toBe(2)
  })
})
