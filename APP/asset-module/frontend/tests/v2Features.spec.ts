/** V2 功能查詢（2.043.10，V73）：目錄＝V2 選單＋舊版功能目錄（轉到 V2 位置），點下去不會掉回舊版。 */
import { describe, expect, it } from 'vitest'
import { featureCatalog } from '../composables/useNav'
import { V2_NAV } from '../composables/useNavV2'
import { searchV2Features, toV2Url, v2FeatureCatalog, whereOf } from '../utils/v2Features'

describe('v2FeatureCatalog', () => {
  const rows = v2FeatureCatalog(featureCatalog())
  it('V2 選單每一項都在、而且每一列都連到 V2', () => {
    const pages = rows.filter(r => r.kind === 'page').map(r => r.to)
    for (const g of V2_NAV) for (const it of g.items) expect(pages).toContain(it.to)
    for (const r of rows) expect(r.to.startsWith('/v2')).toBe(true)
  })
  it('藏在頁面裡的動作也找得到，並標出在 V2 哪一頁', () => {
    const ping = searchV2Features('ping', rows).filter(r => r.kind === 'action')
    expect(ping.length).toBeGreaterThan(0)
    expect(ping.every(r => r.where !== '—')).toBe(true)
  })
  it('打舊頁名找得到它現在在哪', () => {
    const r = rows.find(x => x.kind === 'old' && x.to.startsWith('/v2/onboard?tab=life'))
    expect(r).toBeTruthy()
    expect(r!.where).toBe('6.1 納管作業')
  })
})

describe('toV2Url／whereOf', () => {
  it('舊網址轉成 V2 網址（跟 middleware 同一張表）', () => {
    expect(toV2Url('/lifecycle')).toBe('/v2/onboard?tab=life')
    expect(toV2Url('/blast?q=10.99.1.1')).toBe('/v2/incident?tab=impact&iq=10.99.1.1')
    expect(toV2Url('/login')).toBe('/login')
  })
  it('V2 網址對到選單編號', () => {
    expect(whereOf('/v2/incident?tab=impact')).toBe('4.1 事件應變')
    expect(whereOf('/v2/machine/SN1')).toBe('2.1 主機查詢')
  })
})
