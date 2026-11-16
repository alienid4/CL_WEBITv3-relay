// 批 23（V17）：6.1 開始收集 V2 元件實掛——載入只讀、空白不送、排隊取消只送畫面上還在的勾選、按鈕照後端 can_admin。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let pq: any
let latest: any
let dispatchOut: any
const Table = defineComponent({ props: ['rows', 'cols', 'selected', 'selectable', 'prefix', 'rowKey', 'empty', 'max', 'headerClearsAll'], emits: ['update:selected'], setup(p, { emit }) {
  return () => h('div', { class: `tbl-${p.prefix}` }, [h('button', { class: `pick-${p.prefix}`, onClick: () => emit('update:selected', { S1: true, S9: true }) }, 'pick')])
} })
beforeEach(() => {
  calls = []
  latest = { run: null, can_admin: false }
  dispatchOut = { run_id: 7, this_run: { run_id: 7 }, run: { id: 7, status: 'done' } }
  pq = { running: false, items: [{ no: 1, asset_serial: 'S1', ip: '10.99.0.1', state: 'queued' }], lost: [], can_admin: true }
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, onBeforeUnmount, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRoute', () => ({ query: {} }))
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/onboard/post-collect-queue') return pq
    if (url === '/api/collect/dispatch/latest') return latest
    if (url === '/api/collect/dispatch' && opts.method === 'POST') return dispatchOut
    if (url.endsWith('/summary')) return { total: 1, rows: [], buckets: [], counts: {} }
    if (url === '/api/collect/dispatch/progress') return { run: null }
    if (url === '/api/segments/tree') return { tree: [] }
    if (url === '/api/collect/dispatch/coverage') return { segments: [], summary: { segments: 0, never: 0, partial: 0, full: 0 } }
    if (url === '/api/onboard/audit') return []
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, CollectLog: defineComponent({ setup(_, { expose }) { expose({ reload: () => {} }); return () => h('div') } }) }
const writes = () => calls.filter(c => c.opts?.method && c.opts.method !== 'GET')
async function mk() {
  const C = (await import('../components/v2/AdoptCollect.vue')).default
  const w = mount(C, { global: { stubs } })
  await flushPromises()
  return w
}
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))

describe('6.1 開始收集（V2AdoptCollect）', () => {
  it('載入只讀：沒有任何 POST／PUT', async () => {
    await mk()
    expect(calls.length).toBeGreaterThan(5)
    expect(writes()).toEqual([])
  })
  it('範圍空白：開始收集鍵不能按，也不會送', async () => {
    const w = await mk()
    const b = w.find('[data-test="dispatch-start"]')
    expect(b.attributes('disabled')).toBeDefined()
    await b.trigger('click'); await flushPromises()
    expect(writes()).toEqual([])
  })
  it('超過 256 個位址，取消確認就不送', async () => {
    const w = await mk()
    await w.find('textarea').setValue('10.99.0.0/23')
    vi.stubGlobal('confirm', () => false)
    await w.find('[data-test="dispatch-start"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([])
  })
  it('排隊取消：只送畫面上還在隊伍裡的勾選，確認後才送', async () => {
    const w = await mk()
    await btn(w, '看是哪幾台').trigger('click')
    await w.find('button.pick-Q').trigger('click')       // 勾 S1 與不在隊伍裡的 S9
    vi.stubGlobal('confirm', () => false)
    await btn(w, '取消勾選的').trigger('click'); await flushPromises()
    expect(writes()).toEqual([])
    vi.stubGlobal('confirm', () => true)
    await btn(w, '取消勾選的').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/onboard/post-collect-queue/cancel', opts: { method: 'POST', body: { asset_serials: ['S1'] } } }])
  })
  it('後端沒回 can_admin：沒有取消鍵', async () => {
    pq.can_admin = false
    const w = await mk()
    await btn(w, '看是哪幾台').trigger('click')
    expect(btn(w, '取消勾選的')).toBeUndefined()
    expect(btn(w, '全部取消排隊')).toBeUndefined()
  })
})

describe('寫入的 body（實掛，第 2／3 片審查 P2）', () => {
  it('開始收集：body＝{ targets, full, registered_only }；預設只掃已登記', async () => {
    const w = await mk()
    await w.find('textarea').setValue('10.99.0.5')
    await w.find('[data-test="dispatch-start"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/collect/dispatch', opts: { method: 'POST', body: { targets: '10.99.0.5', full: false, registered_only: true } } }])
  })
  it('取消「只掃已登記」、勾全部重收：registered_only=false、full=true', async () => {
    const w = await mk()
    const boxes = w.findAll('input[type=checkbox]')
    await boxes[0].setValue(false)          // 只掃已登記的 IP
    await boxes[1].setValue(true)           // 全部重收
    await w.find('textarea').setValue('10.99.0.0/28')
    await w.find('[data-test="dispatch-start"]').trigger('click'); await flushPromises()
    expect(writes()[0].opts.body).toEqual({ targets: '10.99.0.0/28', full: true, registered_only: false })
  })
  it('停止收集：確認後才送 POST …/{id}/stop、沒有 body', async () => {
    latest = { run: { id: 42, status: 'running', liveness: 'live', target_count: 10 }, can_admin: true }
    const w = await mk()
    vi.stubGlobal('confirm', () => false)
    await w.find('[data-test="dispatch-stop"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([])
    vi.stubGlobal('confirm', () => true)
    await w.find('[data-test="dispatch-stop"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/collect/dispatch/42/stop', opts: { method: 'POST' } }])
  })
  it('後端沒回 can_admin：沒有停止、沒有標記已中斷', async () => {
    latest = { run: { id: 42, status: 'running', liveness: 'stale' }, can_admin: false }
    const w = await mk()
    expect(w.find('[data-test="dispatch-stop"]').exists()).toBe(false)
    expect(w.find('[data-test="dispatch-mark-interrupted"]').exists()).toBe(false)
  })
  it('標記已中斷：確認後才送 POST …/{id}/mark-interrupted', async () => {
    latest = { run: { id: 43, status: 'running', liveness: 'stale', last_progress_at: '2026-11-09 10:00:00', batch_done: 1, batch_total: 3 }, can_admin: true }
    const w = await mk()
    vi.stubGlobal('confirm', () => true)
    await w.find('[data-test="dispatch-mark-interrupted"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/collect/dispatch/43/mark-interrupted', opts: { method: 'POST' } }])
  })
  it('全部取消排隊：body { asset_serials: null }', async () => {
    const w = await mk()
    await btn(w, '看是哪幾台').trigger('click')
    vi.stubGlobal('confirm', () => true)
    await btn(w, '全部取消排隊').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/onboard/post-collect-queue/cancel', opts: { method: 'POST', body: { asset_serials: null } } }])
  })
  it('離開頁面：計時器全部清掉，之後也不會再問進度', async () => {
    vi.useFakeTimers()
    latest = { run: { id: 44, status: 'running', liveness: 'live' }, can_admin: true }
    pq.running = true
    const w = await mk()
    w.unmount()
    const n = calls.length
    await vi.advanceTimersByTimeAsync(20000)
    expect(calls.length).toBe(n)
    vi.useRealTimers()
  })
})
