// 2.053.0（V41）：6.1 納管漏斗（components/v2/Funnel.vue）的「下一步」連結直接寫 V2 網址——
// 這裡釘住它們就是轉址表對那幾個舊網址給的同一個目的地（舊頁 ACTION_LINK 的五個＋同台 N 筆的資產清單）。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { isPorted, v2LegacyTarget } from '../utils/v2LegacyMap'

const url = (p: string, q: Record<string, string> = {}) => {
  const t = v2LegacyTarget(p, q)!
  const qs = new URLSearchParams(t.query).toString()
  return t.path + (qs ? `?${qs}` : '')
}
const src = readFileSync(resolve(__dirname, '../components/v2/Funnel.vue'), 'utf8')

describe('納管漏斗連結＝轉址表', () => {
  it('五個下一步', () => {
    for (const [k, old] of [['adopt', '/adopt'], ['collect', '/assets'], ['services', '/services'], ['accounts', '/accounts'], ['check', '/issues']]) {
      expect(src).toContain(`${k}: '${url(old)}'`)
    }
  })
  it('同台 N 筆＝資產清單帶 IP', () => {
    expect(url('/assets', { q: '10.99.1.1' })).toBe('/v2/hosts?q=10.99.1.1')
    expect(src).toContain('`/v2/hosts?q=${encodeURIComponent(row.ip)}`')
  })
  it('舊網址 /pipeline 帶篩選轉到漏斗頁籤，legacy=1 也不開舊頁', () => {
    expect(url('/pipeline', { stage: 'conflict', drill: 'aix' })).toBe('/v2/onboard?tab=funnel&stage=conflict&drill=aix')
    expect(isPorted('/pipeline')).toBe(true)
  })
})
