/**
 * 2.048.14 V14／V22：7.2 收集身分（盤點作業）、6.1 一鍵批次自動納管改 V2 原生，實掛：
 *   - 收集身分：一般帳號不打 collect-config（後端 ADMIN_READ）、沒有立即盤點／排除；載入失敗要看得到；收集健檢 query 正確
 *   - 管理員：改收集器位址 PUT body 帶目前帳號；排除送 exclude:true
 *   - 批次自動納管：預覽 body；未登記沒勾不送、勾了才進 include_ips；統一密碼沒打 YES 不送；送出後密碼清空；
 *     中斷後「繼續剩下的」只送 remaining、當 include_ips；結果卡片下鑽
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import AccountOps from '../components/v2/AccountOps.vue'
import BatchAuto from '../components/v2/BatchAuto.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const route = reactive<{ query: Record<string, string> }>({ query: {} })
const auth = { isAdmin: false, canWrite: true }
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = false; auth.canWrite = true
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useClipboard', () => ({ copy: async () => true }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(auth.canWrite), isViewer: ref(!auth.canWrite) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'selectable', 'selected', 'rowKey'], emits: ['update:selected'], setup(p, { slots, emit }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [
    p.selectable ? h('input', { type: 'checkbox', class: 'pick', onChange: () => emit('update:selected', { ...p.selected, [r[p.rowKey]]: true }) }) : null,
    ...(p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(r[c.k] ?? '')))])))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: pass } } }
const flush = async (fn: (p: any) => any) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))
const cfg = { account: 'webit3sc', options: [{ value: 'webit3sc', label: '唯讀' }, { value: 'sysinfra', label: '管理' }], pubkey: 'ssh-ed25519 AAA', provision_hint: 'h',
  collector_ip: '10.99.0.5', collector_ip_source: '自動', collector_ip_detected: '10.99.0.5', account_comment: 'c' }
const opsReply = (p: any) => (p.url === '/api/accounts/findings' ? { summary: { has_data: true, failed_count: 1, host_count: 3, hosts_needing_root: 0 }, thresholds: { acct_pw_max_days: 90, acct_idle_days: 60 } }
  : p.url === '/api/accounts/hosts' ? { hosts: [{ asset_serial: 'S1', hostname: 'h1', ip: '10.99.0.1', excluded: false }] } : p.url === '/api/accounts/collect-config' ? cfg : { ok: true })

describe('收集身分（V14）', () => {
  it('一般帳號：不打 collect-config、沒有立即盤點與排除；健檢 query 帶序號', async () => {
    route.query = { tab: 'identity', ax: '1' }
    const w = mount(AccountOps, opts)
    await flushPromises()
    expect(pending.some(p => p.url === '/api/accounts/collect-config')).toBe(false)
    await flush(opsReply)
    expect(btn(w, '立即盤點')).toBeUndefined()
    expect(btn(w, '排除')).toBeUndefined()
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    expect(w.find('[data-test="failed"]').text()).toContain('1/3')
    await w.find('select').setValue('S1')
    await btn(w, '執行健檢').trigger('click')
    await flushPromises()
    const g = pending.splice(0)[0]
    expect(g.url).toBe('/api/accounts/diagnose')
    expect(g.opts.query).toEqual({ asset_serial: 'S1' })
  })
  it('載入失敗看得到（不吞成沒資料）', async () => {
    const w = mount(AccountOps, opts)
    await flushPromises()
    for (const p of pending.splice(0)) p.reject({ data: { detail: 'boom' } })
    await flushPromises()
    expect(w.text()).toContain('盤點摘要載入失敗：boom')
  })
  it('管理員：改收集器位址帶目前帳號；排除送 exclude:true', async () => {
    auth.isAdmin = true
    route.query = { tab: 'identity', ax: '1' }
    const w = mount(AccountOps, opts)
    await flush(opsReply)
    await w.find('input[aria-label="收集器位址"]').setValue('10.99.0.9')
    await btn(w, '儲存').trigger('click')
    await flushPromises()
    const put = pending.splice(0)[0]
    expect(put.opts).toMatchObject({ method: 'PUT', body: { account: 'webit3sc', collector_ip: '10.99.0.9' } })
    put.resolve({ collector_ip: '10.99.0.9' })
    await flush(opsReply)
    await btn(w, '排除').trigger('click')
    await flushPromises()
    expect(pending.splice(0)[0].opts).toEqual({ method: 'PUT', body: { asset_serial: 'S1', exclude: true } })
  })
})

const st = (extra: any = {}) => ({ running: false, run: { id: 1, status: 'done', unify: 0, started_at: 't' }, total: 2, done: 2,
  results: [{ ip: '10.99.1.1', login_ok: 1, password_index: 0, onboarded: 1, password_unified: 0 }, { ip: '10.99.1.2', login_ok: 0, password_index: null, onboarded: 0, password_unified: 0, fail_reason: 'auth' }], ...extra })
async function fill(w: any) {
  await w.find('textarea').setValue('10.99.1.1 10.99.1.9')
  const pw = w.findAll('input[type="password"]')
  await pw[0].setValue('A'); await pw[1].setValue('B')
}
describe('一鍵批次自動納管（V22）', () => {
  it('預覽 body；未登記沒勾不送，勾了才進 include_ips；送出後密碼清空', async () => {
    const w = mount(BatchAuto, opts)
    await flush(() => ({ running: false, run: null, results: [] }))
    await fill(w)
    await btn(w, '先預覽範圍').trigger('click')
    await flushPromises()
    const p = pending.splice(0)[0]
    expect(p.opts.body).toEqual({ ips: ['10.99.1.1', '10.99.1.9'], username: 'root', passwords: ['A'] })
    p.resolve({ run: [{ ip: '10.99.1.1' }], windows: [], other: [], blocked_production: [], unknown: [{ ip: '10.99.1.9', reason: '未登記' }], excluded: [] })
    await flushPromises()
    await w.find('.pick1').setValue(true)
    await btn(w, '開始自動納管').trigger('click')
    await flushPromises()
    const s = pending.splice(0)[0]
    expect(s.url).toBe('/api/onboard/batch-auto')
    expect(s.opts.body).toEqual({ ips: ['10.99.1.1', '10.99.1.9'], username: 'root', passwords: ['A', 'B'], unify_password: false, confirm: '', include_ips: ['10.99.1.9'] })
    s.resolve({})
    await flushPromises()
    for (const x of w.findAll('input[type="password"]')) expect((x.element as HTMLInputElement).value).toBe('')
  })
  it('勾統一密碼沒打 YES：不送', async () => {
    const w = mount(BatchAuto, opts)
    await flush(() => ({ running: false, run: null, results: [] }))
    await fill(w)
    await btn(w, '先預覽範圍').trigger('click')
    await flush(() => ({ run: [{ ip: '10.99.1.1' }], windows: [], other: [], blocked_production: [], unknown: [], excluded: [] }))
    await w.find('.unify input[type="checkbox"]').setValue(true)
    await w.find('input[aria-label="確認 YES"]').setValue('yes')
    await btn(w, '開始自動納管').trigger('click')
    await flushPromises()
    expect(pending.some(p => p.url === '/api/onboard/batch-auto')).toBe(false)
  })
  it('中斷後繼續：只送 remaining 當 include_ips；結果卡片下鑽失敗那幾台', async () => {
    const w = mount(BatchAuto, opts)
    await flush(() => st({ interrupted: true, remaining: [{ ip: '10.99.1.3' }] }))
    expect(w.find('[data-test="interrupted"]').exists()).toBe(true)
    await w.find('[data-res="failed"]').trigger('click')
    expect(w.findAll('[data-prefix="R"] .row').length).toBe(1)
    await w.findAll('input[type="password"]')[0].setValue('A')
    await btn(w, '繼續剩下的').trigger('click')
    await flushPromises()
    const s = pending.find(p => p.url === '/api/onboard/batch-auto')!
    expect(s.opts.body.ips).toEqual(['10.99.1.3'])
    expect(s.opts.body.include_ips).toEqual(['10.99.1.3'])
  })
})

describe('第 8 批審查補測（2.048.15）', () => {
  const pvReply = (unknown: any[]) => ({ run: [{ ip: '10.99.1.1' }], windows: [], other: [], blocked_production: [], unknown, excluded: [] })
  async function previewWith(w: any, unknown: any[]) {
    await btn(w, '先預覽範圍').trigger('click')
    await flushPromises()
    pending.splice(0)[0].resolve(pvReply(unknown))
    await flushPromises()
  }
  it('未登記逐台勾：沒有全選；勾兩台再取消一台，只送剩下那台；重新預覽後勾選清空', async () => {
    const w = mount(BatchAuto, opts)
    await flush(() => ({ running: false, run: null, results: [] }))
    await fill(w)
    await previewWith(w, [{ ip: '10.99.1.8' }, { ip: '10.99.1.9' }])
    expect(w.find('[data-prefix="U"] .pick').exists()).toBe(false)          // 不給表格的勾選（含全選）
    const cb = w.findAll('.pick1')
    await cb[0].setValue(true); await cb[1].setValue(true); await cb[0].setValue(false)
    await btn(w, '開始自動納管').trigger('click')
    await flushPromises()
    const first = pending.splice(0)[0]
    expect(first.opts.body.include_ips).toEqual(['10.99.1.9'])
    first.resolve({}); await flushPromises(); pending.splice(0).forEach(p => p.resolve(st()))
    await flushPromises()
    await fill(w)
    await previewWith(w, [{ ip: '10.99.1.8' }, { ip: '10.99.1.9' }])
    expect(w.findAll('.pick1').every(c => !(c.element as HTMLInputElement).checked)).toBe(true)
    await btn(w, '開始自動納管').trigger('click')
    await flushPromises()
    expect(pending.splice(0)[0].opts.body.include_ips).toEqual([])
  })
  it('沒輸入密碼不送；進度讀取失敗看得到', async () => {
    const w = mount(BatchAuto, opts)
    await flushPromises()
    pending.splice(0)[0].reject({ data: { detail: 'down' } })
    await flushPromises()
    expect(w.text()).toContain('進度讀取失敗')
    await w.find('textarea').setValue('10.99.1.1')
    await btn(w, '先預覽範圍').trigger('click')
    await flush(() => pvReply([]))
    await btn(w, '開始自動納管').trigger('click')
    await flushPromises()
    expect(pending.some(p => p.url === '/api/onboard/batch-auto')).toBe(false)
  })
  it('輪詢：跑完就停、unmount 也停；展開時那批還在跑會接著輪詢', async () => {
    vi.useFakeTimers()
    try {
      const w = mount(BatchAuto, opts)
      await flushPromises()
      pending.splice(0)[0].resolve(st({ running: true }))
      await flushPromises()
      vi.advanceTimersByTime(2000); await flushPromises()
      expect(pending.length).toBe(1)                                          // 還在跑 → 接著輪詢
      pending.splice(0)[0].resolve(st({ running: false }))
      await flushPromises()
      vi.advanceTimersByTime(6000); await flushPromises()
      expect(pending.length).toBe(0)                                          // 跑完就停
      const w2 = mount(BatchAuto, opts)
      await flushPromises()
      pending.splice(0)[0].resolve(st({ running: true }))
      await flushPromises()
      w2.unmount()
      vi.advanceTimersByTime(6000); await flushPromises()
      expect(pending.length).toBe(0)                                          // unmount 後不再打
      w.unmount()
    } finally { vi.useRealTimers() }
  })
  it('收集身分：納回送 exclude:false；只能看的帳號不打摘要與主機清單', async () => {
    auth.isAdmin = true
    route.query = { tab: 'identity', ax: '1' }
    const w = mount(AccountOps, opts)
    await flush((p: any) => (p.url === '/api/accounts/hosts' ? { hosts: [{ asset_serial: 'S2', hostname: 'h2', ip: '10.99.0.2', excluded: true }] } : opsReply(p)))
    await btn(w, '納回').trigger('click')
    await flushPromises()
    expect(pending.splice(0)[0].opts.body).toEqual({ asset_serial: 'S2', exclude: false })
    auth.isAdmin = false; auth.canWrite = false
    const w2 = mount(AccountOps, opts)
    await flushPromises()
    expect(pending.length).toBe(0)
    expect(w2.find('[data-test="viewer"]').exists()).toBe(true)
  })
})
