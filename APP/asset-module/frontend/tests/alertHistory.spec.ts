// 1.2「告警歷史」頁籤（2026-11-18，三層分離 H13）：磁碟告警歷史表有級別欄與項次、記憶體／SWAP 歷史可切換、讀不到時寫原因不顯示成「沒有告警」。API 全假。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/AlertHistory.vue'

enableAutoUnmount(afterEach)
const HIST = [{ id: 2, __no: 2, at: '2026-11-02 05:00:00', kind: 'alert', tier: 'critical', batch_id: 'b2', threshold: 90, hosts: 2, items: 2, status: 'queued', delivery_key: 'disk:b2:x', delivery_status: 'sent', summary: 'demo-web01｜C:｜95%' },
  { id: 1, __no: 1, at: '2026-11-01 05:00:00', kind: 'sample', tier: null, batch_id: null, threshold: null, hosts: 1, items: 1, status: 'sample', delivery_key: 'live_test:x', delivery_status: 'sent', summary: '10.99.0.10｜C:｜40%' }]
let memFail = false
let lastCols: any[] = []
const V2Table = defineComponent({
  props: ['rows', 'cols', 'empty'],
  setup(p) { return () => { lastCols = p.cols; return h('table', {}, (p.rows as any[]).map((r: any) => h('tr', { class: 'row' }, [h('td', { class: 'no' }, r.__no ?? r.day), h('td', {}, r.summary)]))) } },
})
beforeEach(() => {
  memFail = false
  vi.stubGlobal('ref', ref); vi.stubGlobal('onMounted', onMounted)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string) => {
    if (u === '/api/teams-notify/disk-alert') return Promise.resolve({ history: HIST })
    if (u === '/api/teams-notify/mem-alert') return memFail ? Promise.reject(new Error('boom')) : Promise.resolve({ history: [{ day: '2026-11-01', kind: 'mem', threshold: 90, hosts: 1, status: 'queued', summary: 'demo-db01｜96%' }] })
    return Promise.resolve({})
  } }))
})
const mnt = () => mount(Comp, { global: { stubs: { InfoNote: true, NuxtLink: true, V2Table } } })

describe('1.2 告警歷史', () => {
  it('磁碟歷史表有級別欄（緊急／準備／暴增，範例為 —）與項次', async () => {
    const w = mnt(); await flushPromises()
    const col = lastCols.find(c => c.k === 'tier')!
    expect(col.label).toBe('級別')
    expect(col.text(HIST[0])).toBe('緊急'); expect(col.text(HIST[1])).toBe('—')
    expect(col.text({ tier: 'warn' })).toBe('準備'); expect(col.text({ tier: 'surge' })).toBe('暴增')
    expect(w.findAll('tr.row').map(r => r.find('.no').text())).toEqual(['2', '1'])
  })
  it('切到記憶體／SWAP 看另一份歷史', async () => {
    const w = mnt(); await flushPromises()
    await w.findAll('button.chip')[1].trigger('click')
    expect(w.findAll('tr.row')).toHaveLength(1)
    expect(w.text()).toContain('demo-db01')
  })
  it('記憶體歷史讀不到：寫失敗原因，不顯示成空表', async () => {
    memFail = true
    const w = mnt(); await flushPromises()
    await w.findAll('button.chip')[1].trigger('click')
    expect(w.text()).toContain('記憶體／SWAP 告警歷史載入失敗')
    expect(w.findAll('tr.row')).toHaveLength(0)
  })
})
