import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SerialLinks from '../components/SerialLinks.vue'
import { partialGapNote, partialGapRows, serialsOf } from '../utils/partialGaps'

const pg = (over: any = {}) => ({ n: 2, applies: true, items: [
  { asset_serial: 'S1', hostname: 'h1', ip: '192.0.2.1', machine_state: '失聯', gap_rows: ['S2'], row_states: ['失聯', '未涵蓋'] },
  { asset_serial: 'S3', hostname: 'h2', ip: null, machine_state: '失聯', gap_rows: ['S4', 'S5'], row_states: ['失聯', '未涵蓋'] }], ...over })

describe('次要待辦：部分登記未涵蓋（007）', () => {
  it('整台口徑且有缺口才顯示中文提示', () => {
    expect(partialGapNote(pg())).toContain('另有 2 台')
    expect(partialGapNote(pg())).not.toMatch(/partial|machine_key|gap_rows/)
  })
  it('代表筆口徑（applies=false）或沒有缺口或舊 API：不顯示', () => {
    expect(partialGapNote(pg({ applies: false }))).toBe('')
    expect(partialGapNote(pg({ n: 0, items: [] }))).toBe('')
    expect(partialGapNote(undefined)).toBe('')
  })
  it('清單列帶缺口登記與整台狀態', () => {
    const rows = partialGapRows(pg())
    expect(rows.length).toBe(2)
    expect(rows[1].gap_serials).toEqual(['S4', 'S5'])            // 不再合成「S4、S5」這種不存在的序號
    expect(rows[1].asset_serial).toBe('S4')
    expect(rows[0].state).toContain('失聯')
  })

  // 真元件：每筆缺口各自一條 /assets/<序號> 連結（NuxtLink 以 a 標籤替身，保留 to）
  const stubs = { NuxtLink: { props: ['to'], template: '<a class="dl" :href="to"><slot /></a>' } }
  const links = (serials: string[]) => mount(SerialLinks, { props: { serials }, global: { stubs } })

  it('兩個缺口：兩條獨立連結，沒有合成序號', () => {
    const rows = partialGapRows(pg())
    const w = links(serialsOf(rows[1]))
    expect(w.findAll('a').map(a => a.attributes('href'))).toEqual(['/assets/S4', '/assets/S5'])
    expect(w.text()).toBe('S4、S5')
    expect(w.html()).not.toContain('/assets/S4、S5')
  })
  it('單一缺口：一條連結', () => {
    const w = links(serialsOf(partialGapRows(pg())[0]))
    expect(w.findAll('a').map(a => a.attributes('href'))).toEqual(['/assets/S2'])
  })
  it('一般列與沒有序號：原行為（單一連結／未登記）', () => {
    expect(links(serialsOf({ asset_serial: 'A1' })).findAll('a').map(a => a.attributes('href'))).toEqual(['/assets/A1'])
    expect(links(serialsOf({ asset_serial: null })).text()).toBe('未登記')
    expect(links(serialsOf({ asset_serial: null })).findAll('a').length).toBe(0)
  })
  it('切換 route（換一批資料）：連結跟著換，不殘留舊的', async () => {
    const w = links(['S4', 'S5'])
    await w.setProps({ serials: ['T1'] })
    expect(w.findAll('a').map(a => a.attributes('href'))).toEqual(['/assets/T1'])
  })
  it('applies=false／空清單：不產生任何缺口列', () => {
    expect(partialGapNote(pg({ applies: false }))).toBe('')
    expect(partialGapRows(pg({ items: [], n: 0 })).length).toBe(0)
  })
})
