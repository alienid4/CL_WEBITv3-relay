import { describe, expect, it } from 'vitest'
import { layerParts, layerText } from '../utils/eosLayers'

describe('EOS 構成文字（004）', () => {
  it('整機、元件、管理口、未知各自歸類，加總等於原台數', () => {
    const l = { phys_server: 3, appliance: 2, mgmt_attached: 1, unknown: 1, mixed: 1 }
    expect(layerParts(l)).toEqual([{ label: '整機', n: 3 }, { label: '元件', n: 2 }, { label: '管理口', n: 1 }, { label: '未能判斷', n: 2 }])
    expect(layerParts(l).reduce((s, p) => s + p.n, 0)).toBe(8)
    expect(layerText(l)).toBe('整機 3／元件 2／管理口 1／未能判斷 2')
  })
  it('全是整機不加註；沒資料（舊 API）也不顯示', () => {
    expect(layerText({ phys_server: 5, phys_network: 2 })).toBe('')
    expect(layerText(undefined)).toBe('')
    expect(layerText({})).toBe('')
  })
  it('不顯示技術欄名', () => {
    expect(layerText({ appliance: 2, unknown: 1 })).not.toMatch(/appliance|unknown|machine_key|layers/)
  })
})
