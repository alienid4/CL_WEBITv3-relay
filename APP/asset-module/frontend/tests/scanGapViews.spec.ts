/** V2 掃描缺口頁籤篩選＝CSV 匯出的 view（2.043.5，GPT 審查 1）；後端同一個判斷在 tests/asset_module/test_scan_gaps.py。 */
import { describe, expect, it } from 'vitest'
import { GAP_OK, gapCsvQuery, gapInView, gapView } from '../utils/scanGapViews'

const rows = [
  { cidr: 'a', kind: GAP_OK, is_gap: false, firewall_first: false, expected_unreachable: false, registered_machines: 3 },
  { cidr: 'b', kind: '從沒掃過', is_gap: true, firewall_first: true, expected_unreachable: false, registered_machines: 2 },
  { cidr: 'c', kind: '從沒掃過', is_gap: true, firewall_first: false, expected_unreachable: false, registered_machines: 0 },
  { cidr: 'd', kind: '從沒掃過', is_gap: false, firewall_first: false, expected_unreachable: true, registered_machines: 1 },
  { cidr: 'e', kind: GAP_OK, is_gap: false, firewall_first: false, expected_unreachable: true, registered_machines: 1 },
]
const pick = (gk: string) => rows.filter(r => gapInView(r, gapView(gk))).map(r => r.cidr)

describe('掃描缺口頁籤', () => {
  it('預設頁不含掃描正常的段、優先申請不含有登記但掃得到的段', () => {
    expect(pick('')).toEqual(['b', 'c'])
    expect(pick('fw')).toEqual(['b'])
  })
  it('預期不通只列掃不到的（跟頁籤數字 expected_unreachable_segments 同口徑）', () => {
    expect(pick('exp')).toEqual(['d'])
    expect(pick('all')).toHaveLength(5)
  })
  it('CSV 帶同一個 view 給後端', () => {
    expect(gapCsvQuery('')).toEqual({ view: 'gap', include_expected: '1' })
    expect(gapCsvQuery('fw')).toEqual({ view: 'fw', include_expected: '1' })
    expect(gapCsvQuery('exp').view).toBe('exp')
    expect(gapCsvQuery('weird').view).toBe('gap')
  })
})
