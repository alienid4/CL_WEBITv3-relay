// 硬碟通知（影子模式）設定卡實掛：彙總數字與每人幾顆、空狀態「沒有預計通知」、查不到前次／主機顯示「—」、點數字卡篩選明細、開關與試算呼叫的 API（只記錄、不寄信）、載入失敗訊息。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/DiskNoticeShadowSettings.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
const toasts: string[] = []
let fail = false
let data: any
const ROW = (o: any) => ({ __no: 1, day: '2026-11-21', ip: '10.99.0.1', host: 'app01', disk: 'C:', pct: 91, prev_pct: 85, reason: 'cross90', reason_text: '跨過 90%', immediate: false,
  target: '王大明', target_src: 'CIA負責人', note: null, rule_ver: 'shadow-1', ...o })
const FULL = () => ({ enabled: true, days: 7, rule_ver: 'shadow-1', last_run: { at: '2026-11-21 07:01:00', day: '2026-11-21' },
  summary: { total: 3, owner_disks: 1, no_owner: 1, immediate: 1, per_person: [{ target: '王大明', disks: 1 }] },
  rows: [ROW({}), ROW({ __no: 2, ip: '10.99.0.2', host: null, prev_pct: null, target: 'SP（系統管理者）', target_src: '對不到→SP' }),
    ROW({ __no: 3, ip: '10.99.0.3', pct: 97, reason: 'ge95', reason_text: '≥95%（即時）', immediate: true, target: 'SP（系統管理者）', target_src: '對不到→SP' })] })

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  if (fail) return Promise.reject({ data: { detail: '只有管理員能看硬碟通知（影子模式）' } })
  if (u === '/api/daily-check/disk-notice-shadow/run') return Promise.resolve({ status: 'ok', added: 2 })
  if (u === '/api/daily-check/disk-notice-shadow/settings') { data = { ...data, enabled: o.body.enabled }; return Promise.resolve({ enabled: o.body.enabled }) }
  return Promise.resolve(data)
}
let lastRows: any[] = []
let lastCols: any[] = []
let lastEmpty = ''
const V2Table = defineComponent({
  props: ['rows', 'cols', 'empty'],
  setup(p) { return () => { lastRows = p.rows; lastCols = p.cols; lastEmpty = p.empty; return h('div', { class: 'tbl' }, p.rows.length ? p.rows.map((r: any) => h('div', { class: 'row' }, r.ip)) : p.empty) } },
})
beforeEach(() => {
  calls.length = 0; toasts.length = 0; fail = false; data = FULL()
  for (const [k, v] of Object.entries({ ref, computed, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
})
const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note-i"><slot /></i>' }, V2Table } } })

describe('硬碟通知（影子模式）卡', () => {
  it('顯示彙總數字、每人幾顆、明細項次與「—」', async () => {
    const w = mnt(); await flushPromises()
    expect(w.get('[data-testid=k-all]').text()).toContain('3')
    expect(w.get('[data-testid=k-owner]').text()).toContain('1')
    expect(w.get('[data-testid=k-sp]').text()).toContain('1')
    expect(w.get('[data-testid=k-imm]').text()).toContain('1')
    expect(w.get('[data-testid=per-person]').text()).toContain('王大明 1 顆')
    expect(w.text()).toContain('上次算：2026-11-21 07:01:00')
    expect(lastRows.map(r => r.__no)).toEqual([1, 2, 3])
    const c = (k: string) => lastCols.find(x => x.k === k)
    expect(c('prev_pct').text(lastRows[1])).toBe('—')           // 沒有前次資料
    expect(c('host').text(lastRows[1])).toBe('—')               // 對不到主機名
    expect(c('prev_pct').text(lastRows[0])).toBe('85%')
    expect(lastCols.map(x => x.label)).toContain('規則版本')
  })
  it('空狀態寫「沒有預計通知」、尚未算過寫「還沒算過」', async () => {
    data = { ...FULL(), rows: [], last_run: null, summary: { total: 0, owner_disks: 0, no_owner: 0, immediate: 0, per_person: [] } }
    const w = mnt(); await flushPromises()
    expect(lastEmpty).toBe('沒有預計通知')
    expect(w.text()).toContain('還沒算過')
    expect(w.find('[data-testid=per-person]').exists()).toBe(false)
  })
  it('點數字卡篩選明細', async () => {
    const w = mnt(); await flushPromises()
    await w.get('[data-testid=k-imm]').trigger('click')
    expect(lastRows.map(r => r.ip)).toEqual(['10.99.0.3'])
    await w.get('[data-testid=k-sp]').trigger('click')
    expect(lastRows.map(r => r.ip)).toEqual(['10.99.0.2'])      // 即時類不算在「對不到負責人」
    await w.get('[data-testid=k-owner]').trigger('click')
    expect(lastRows.map(r => r.ip)).toEqual(['10.99.0.1'])
  })
  it('開關只呼叫設定 API；關閉時試算按鈕不能按；試算只打 run', async () => {
    const w = mnt(); await flushPromises()
    await w.get('[data-testid=shadow-switch]').setValue(false); await flushPromises()
    expect(calls.some(c => c.url.endsWith('/settings') && c.opts.method === 'PUT' && c.opts.body.enabled === false)).toBe(true)
    expect((w.get('[data-testid=shadow-run]').element as HTMLButtonElement).disabled).toBe(true)
    await w.get('[data-testid=shadow-switch]').setValue(true); await flushPromises()
    await w.get('[data-testid=shadow-run]').trigger('click'); await flushPromises()
    expect(calls.some(c => c.url.endsWith('/run') && c.opts.method === 'POST')).toBe(true)
    expect(toasts.join()).toContain('不會寄信')
    expect(calls.every(c => c.url.startsWith('/api/daily-check/disk-notice-shadow'))).toBe(true)   // 沒有碰任何 Teams／寄信 API
  })
  it('載入失敗顯示原因、不顯示表', async () => {
    fail = true
    const w = mnt(); await flushPromises()
    expect(w.get('[data-testid=shadow-err]').text()).toContain('只有管理員')
    expect(w.find('.tbl').exists()).toBe(false)
  })
})
