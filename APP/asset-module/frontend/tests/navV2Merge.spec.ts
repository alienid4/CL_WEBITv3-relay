/**
 * 2.x 選單：程式預設（正本）＋管理員覆寫 → 畫面選單（2.042.21，V43 移植）。後端存取規則見 tests/asset_module/test_v2_nav_layout.py。
 */
import { describe, expect, it } from 'vitest'
import { diffNavV2, mergeNavV2, toNavOverride } from '../utils/navV2Merge'

const D = [
  { no: '1', label: '今日', items: [{ no: '1.1', label: 'A', to: '/v2' }, { no: '1.2', label: 'B', to: '/v2/b' }] },
  { no: '2', label: '主機', items: [{ no: '2.1', label: 'C', to: '/v2/c' }] },
  { no: '3', label: '系統', items: [{ no: '3.1', label: 'D', to: '/v2/d' }, { no: '3.2', label: 'E', to: '/v2/e' }] },
]
const shape = (gs: any[]) => gs.map(g => `${g.no}:${g.items.map((i: any) => i.to).join(',')}`)

describe('mergeNavV2', () => {
  it('沒有覆寫＝程式預設（複本，改了不會動到正本）', () => {
    const r = mergeNavV2(D, null)
    expect(shape(r.groups)).toEqual(shape(D))
    r.groups[0].items.pop()
    expect(D[0].items.length).toBe(2)
  })

  it('照覆寫排群組與項目、項目可以搬到別組，名稱編號取程式預設', () => {
    const r = mergeNavV2(D, [{ no: '3', items: ['/v2/e', '/v2/d', '/v2/b'] }, { no: '1', items: ['/v2'] }, { no: '2', items: ['/v2/c'] }])
    expect(shape(r.groups)).toEqual(['3:/v2/e,/v2/d,/v2/b', '1:/v2', '2:/v2/c'])
    expect(r.groups[0].label).toBe('系統')
    expect(r.groups[0].items[2].no).toBe('1.2')          // 搬家不重編號
    expect(r.dropped).toEqual([]); expect(r.added).toEqual([])
  })

  it('程式拿掉的項目／群組丟掉並回報；不會出現在選單', () => {
    const r = mergeNavV2(D, [{ no: '1', items: ['/v2/gone', '/v2', '/v2/b'] }, { no: '9', items: ['/v2/c', '/v2/x'] }, { no: '2', items: [] }, { no: '3', items: ['/v2/d', '/v2/e'] }])
    expect(r.dropped).toEqual(['/v2/gone', '群組 9', '/v2/x'])
    // /v2/c 原本被放進已不存在的群組 9：回到它的預設群組
    expect(shape(r.groups)).toEqual(['1:/v2,/v2/b', '2:/v2/c', '3:/v2/d,/v2/e'])
  })

  it('程式新增的項目自動放回預設群組最後；覆寫沒提到的群組接在最後', () => {
    const r = mergeNavV2(D, [{ no: '3', items: ['/v2/e'] }, { no: '1', items: ['/v2/b', '/v2'] }])
    expect(shape(r.groups)).toEqual(['3:/v2/e,/v2/d', '1:/v2/b,/v2', '2:/v2/c'])
    expect(r.added).toEqual(['/v2/d'])
  })

  it('重複的項目只算第一次；空掉的群組不顯示；壞資料不會讓選單壞掉', () => {
    const r = mergeNavV2(D, [{ no: '2', items: ['/v2/c', '/v2/c', '/v2/d', '/v2/e'] }, { no: '3', items: [] }, null as any, { no: 1 as any, items: [] }])
    expect(shape(r.groups)).toEqual(['2:/v2/c,/v2/d,/v2/e', '1:/v2,/v2/b'])
    expect(mergeNavV2(D, [] as any).groups.length).toBe(3)
    expect(mergeNavV2(D, 'x' as any).groups.length).toBe(3)
  })

  it('每一項都剛好出現一次', () => {
    const r = mergeNavV2(D, [{ no: '1', items: ['/v2/c'] }, { no: '9', items: ['/v2'] }])
    const all = r.groups.flatMap(g => g.items.map(i => i.to)).sort()
    expect(all).toEqual(['/v2', '/v2/b', '/v2/c', '/v2/d', '/v2/e'])
  })

  it('diffNavV2：只列換組與真的換了先後的項目，群組順序有變要講', () => {
    expect(diffNavV2(D, null, null).rows).toEqual([])
    const d = diffNavV2(D, null, [{ no: '3', items: ['/v2/e', '/v2/d', '/v2/b'] }, { no: '1', items: ['/v2'] }, { no: '2', items: ['/v2/c'] }])
    expect(d.rows.map(r => `${r.to}:${r.change}`).sort()).toEqual(['/v2/b:換組', '/v2/d:組內換位置', '/v2/e:組內換位置'])
    expect(d.rows.find(r => r.to === '/v2/b')).toMatchObject({ from: '1 今日', to_group: '3 系統', from_pos: 2, to_pos: 3 })
    expect(d.groupOrderChanged).toBe(true)
    // 只把一項搬走：原組其他項目位置往前，但先後沒變，不列
    const one = diffNavV2(D, null, [{ no: '1', items: ['/v2/b'] }, { no: '2', items: ['/v2/c', '/v2'] }, { no: '3', items: ['/v2/d', '/v2/e'] }])
    expect(one.rows.map(r => r.to)).toEqual(['/v2']); expect(one.groupOrderChanged).toBe(false)
    expect(diffNavV2(D, [{ no: '1', items: ['/v2/gone'] }], [{ no: '1', items: ['/v2/gone'] }]).dropped).toEqual(['/v2/gone'])
  })

  it('toNavOverride 只留群組編號與網址', () => {
    expect(toNavOverride(D)).toEqual([{ no: '1', items: ['/v2', '/v2/b'] }, { no: '2', items: ['/v2/c'] }, { no: '3', items: ['/v2/d', '/v2/e'] }])
  })
})
