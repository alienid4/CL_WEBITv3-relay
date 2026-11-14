import { describe, expect, it } from 'vitest'
import { moveFavByRoute } from '~/utils/favOrder'

// Codex 1223：保存的清單夾著目錄沒有的路徑時，畫面上把第二個拖到第一個要真的換過來
describe('moveFavByRoute', () => {
  it('隱藏路徑在最前面：/features 拖到 /assets 前面會生效，隱藏的原地保留', () => {
    const saved = ['/DEMO-missing-route', '/assets', '/features']
    expect(moveFavByRoute(saved, '/features', '/assets')).toEqual(['/DEMO-missing-route', '/features', '/assets'])
  })
  it('沒有隱藏路徑時跟以前一樣（往前、往後都對）', () => {
    expect(moveFavByRoute(['/a', '/b', '/c'], '/c', '/a')).toEqual(['/c', '/a', '/b'])
    expect(moveFavByRoute(['/a', '/b', '/c'], '/a', '/c')).toEqual(['/b', '/c', '/a'])
  })
  it('拖到自己、或路徑不在清單：不動、回同一個陣列（不觸發存檔）', () => {
    const l = ['/a', '/b']
    expect(moveFavByRoute(l, '/a', '/a')).toBe(l)
    expect(moveFavByRoute(l, '/x', '/a')).toBe(l)
  })
})
