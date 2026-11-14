// Teams 磁碟告警設定區塊實掛：準備／緊急兩級門檻＋暴增條件／啟用儲存（準備須小於緊急）、歷史表有級別、範例按鈕先二次確認（取消不送）、Teams 未啟用提示、歷史表有項次、檢視者看不到寫入按鈕。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/TeamsDiskAlertSettings.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
const toasts: string[] = []
let canEdit = true
let teamsOn = true
let latest: string | null = '2026-11-01'
const HIST = [{ id: 2, __no: 2, at: '2026-11-02 05:00:00', kind: 'alert', tier: 'critical', batch_id: 'b2', threshold: 90, hosts: 2, items: 2, status: 'queued', delivery_key: 'disk:b2:x', delivery_status: 'sent', summary: 'web01（192.0.2.10）｜C:｜95%｜2026-11-01\n192.0.2.11｜D:｜91%｜2026-11-01' },
  { id: 1, __no: 1, at: '2026-11-01 05:00:00', kind: 'sample', tier: null, batch_id: null, threshold: null, hosts: 1, items: 1, status: 'sample', delivery_key: 'live_test:x', delivery_status: 'sent', summary: '192.0.2.10｜C:｜40%｜2026-11-01' }]

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  if (u === '/api/teams-notify/disk-alert') return Promise.resolve({ settings: { enabled: true, warn: 80, critical: 90, surge_hours: 5, surge_rise: 15, surge_floor: 85 }, corrupt: null, teams_enabled: teamsOn, latest_day: latest, history: HIST, can_edit: canEdit })
  if (u === '/api/teams-notify/disk-alert/sample') return Promise.resolve({ ok: true, outcome: 'sent', http: 202, reason: '' })
  return Promise.resolve({ ok: true })
}
let lastCols: any[] = []
const V2Table = defineComponent({
  props: ['rows', 'cols', 'empty'],
  setup(p) { return () => { lastCols = p.cols; return h('table', {}, (p.rows.length ? p.rows : []).map((r: any) => h('tr', { class: 'row' }, [h('td', { class: 'no' }, r.__no), h('td', {}, r.summary)]))) } },
})

beforeEach(() => {
  calls.length = 0; toasts.length = 0; canEdit = true; teamsOn = true; latest = '2026-11-01'
  for (const [k, v] of Object.entries({ ref, computed, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
  vi.stubGlobal('confirm', () => true)
})
const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note-i"><slot /></i>' }, V2Table } } })

describe('Teams 磁碟告警設定', () => {
  it('儲存送出兩級門檻、暴增條件與啟用', async () => {
    const w = mnt(); await flushPromises()
    await w.find('input[aria-label="準備門檻"]').setValue(75)
    await w.find('input[aria-label="緊急門檻"]').setValue(92)
    await w.find('input[aria-label="暴增時間窗"]').setValue(3)
    await w.find('input[aria-label="暴增上升幅度"]').setValue(20)
    await w.find('input[aria-label="暴增下限"]').setValue(88)
    await w.findAll('button').find(b => b.text() === '儲存')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/teams-notify/disk-alert/settings')!
    expect(put.opts.method).toBe('PUT')
    expect(put.opts.body).toEqual({ enabled: true, warn: 75, critical: 92, surge_hours: 3, surge_rise: 20, surge_floor: 88 })
  })

  it('準備門檻不小於緊急門檻時不送出、提示', async () => {
    const w = mnt(); await flushPromises()
    await w.find('input[aria-label="準備門檻"]').setValue(90)
    await w.findAll('button').find(b => b.text() === '儲存')!.trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/teams-notify/disk-alert/settings')).toBe(false)
    expect(toasts.some(t => t.includes('準備門檻要小於緊急門檻'))).toBe(true)
  })

  it('歷史表有級別欄（緊急／準備／暴增，範例為 —）', async () => {
    mnt(); await flushPromises()
    const col = lastCols.find(c => c.k === 'tier')!
    expect(col.label).toBe('級別')
    expect(col.text(HIST[0])).toBe('緊急'); expect(col.text(HIST[1])).toBe('—')
    expect(col.text({ tier: 'warn' })).toBe('準備'); expect(col.text({ tier: 'surge' })).toBe('暴增')
  })

  it('範例按鈕先二次確認，取消不送；確認後送出並回報 HTTP', async () => {
    const asked: string[] = []
    vi.stubGlobal('confirm', (m: string) => { asked.push(m); return false })
    const w = mnt(); await flushPromises()
    const b = w.findAll('button').find(x => x.text() === '用最近一筆資料送範例')!
    await b.trigger('click'); await flushPromises()
    expect(asked[0]).toContain('這會真的送一則【測試】訊息到 Teams 頻道'); expect(asked[0]).toContain('2026-11-01')
    expect(calls.some(c => c.url.endsWith('/sample'))).toBe(false)
    vi.stubGlobal('confirm', () => true)
    await b.trigger('click'); await flushPromises()
    const post = calls.find(c => c.url.endsWith('/sample'))!
    expect(post.opts.body).toEqual({ confirm: true })
    expect(toasts.some(t => t.includes('HTTP 202'))).toBe(true)
  })

  it('沒有資料時範例按鈕不能按；Teams 未啟用時提示只記錄', async () => {
    latest = null; teamsOn = false
    const w = mnt(); await flushPromises()
    expect(w.findAll('button').find(x => x.text() === '用最近一筆資料送範例')!.attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('Teams 通知目前未啟用')
  })

  it('歷史表有項次', async () => {
    const w = mnt(); await flushPromises()
    const rows = w.findAll('tr.row')
    expect(rows.map(r => r.find('.no').text())).toEqual(['2', '1'])
  })

  it('檢視者看不到儲存與範例按鈕', async () => {
    canEdit = false
    const w = mnt(); await flushPromises()
    const labels = w.findAll('button').map(b => b.text())
    expect(labels).not.toContain('儲存'); expect(labels).not.toContain('用最近一筆資料送範例')
  })
})
