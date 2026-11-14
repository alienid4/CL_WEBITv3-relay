/**
 * 2.048.8 V29／V35：8.2 基線偏移、6.2 HMC 收集改 V2 原生，實掛：
 *   - 基線偏移：分頁 ds 送到後端；換分頁舊請求晚回來不覆蓋；空狀態三態（摘要取不到＝無法判定）；回檢限管理員、處置限一般帳號以上
 *   - HMC：收集送出的 body（當場輸入 vs 選憑證）、收完密碼清空、只能看的帳號沒有收集；明細 hd= 一列一個 LPAR
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import Drift from '../components/v2/Drift.vue'
import Hmc from '../components/v2/Hmc.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const route = reactive<{ query: Record<string, string> }>({ query: {} })
const auth = { isAdmin: false, canWrite: true }
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = false; auth.canWrite = true
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(auth.canWrite), isViewer: ref(!auth.canWrite) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: pass } } }
const dr = (id: number, host: string) => ({ id, asset_serial: `S${id}`, hostname: host, ip: null, item_key: 'k', label: '項目', baseline_text: '關', current_text: '開', status: 'open' })
const flush = async (fn: (p: any) => any) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }

describe('基線偏移（V29）', () => {
  it('換分頁：ds 送到後端，舊請求晚回來不覆蓋', async () => {
    const w = mount(Drift, opts)
    await flushPromises()
    expect(pending[0].opts.query).toEqual({ status: 'open' })
    const older = pending.splice(0)
    route.query = { tab: 'drift', ds: 'ack' }
    await flushPromises()
    expect(pending[0].opts.query).toEqual({ status: 'ack' })
    await flush(p => (p.url === '/api/drift' ? { drifts: [dr(2, 'new')] } : { state: 'checked' }))
    for (const p of older) p.resolve({ drifts: [dr(1, 'old'), dr(3, 'old2')] })
    await flushPromises()
    expect(w.findAll('[data-prefix="B"] .row').length).toBe(1)
    expect(w.text()).toContain('new')
  })
  it('空狀態：摘要取不到＝無法判定，不說「無差異」', async () => {
    const w = mount(Drift, opts)
    await flushPromises()
    pending.splice(0)[0].resolve({ drifts: [] })
    await flushPromises()
    pending.splice(0)[0].reject(new Error('x'))
    await flushPromises()
    expect(w.find('[data-testid="drift-empty"]').text()).toContain('無法判定')
  })
  it('權限：一般帳號有處置、沒有回檢；只能看的帳號兩個都沒有', async () => {
    const w = mount(Drift, opts)
    await flush(p => (p.url === '/api/drift' ? { drifts: [dr(1, 'a')] } : { state: 'checked' }))
    expect(w.text()).toContain('已確認')
    expect(w.findAll('button').some(b => b.text().includes('立即回檢'))).toBe(false)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    auth.canWrite = false
    const w2 = mount(Drift, opts)
    await flush(p => (p.url === '/api/drift' ? { drifts: [dr(1, 'a')] } : { state: 'checked' }))
    expect(w2.findAll('[data-prefix="B"] button').length).toBe(0)
  })
})

describe('HMC 收集（V35）', () => {
  it('只能看的帳號（非管理員）沒有收集按鈕', async () => {
    const w = mount(Hmc, opts)
    await flush(() => ({ items: [{ ip: '10.99.9.1', hostname: 'hmc1', collected: false }], total: 1, collected: 0 }))
    expect(w.findAll('button').filter(b => b.text() === '收集').length).toBe(0)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
  })
  it('管理員：當場輸入帳密收集，送出的 body 正確，收完密碼清空', async () => {
    auth.isAdmin = true
    const w = mount(Hmc, opts)
    await flush(p => (p.url === '/api/credentials' ? [] : { items: [{ ip: '10.99.9.1', hostname: 'hmc1', collected: false }], total: 1, collected: 0 }))
    await w.findAll('button').find(b => b.text() === '收集')!.trigger('click')
    const inputs = w.findAll('.modal input')
    await inputs[0].setValue('ro')
    await inputs[1].setValue('pw')
    await w.findAll('.modal button').find(b => b.text() === '開始收集')!.trigger('click')
    await flushPromises()
    const post = pending.find(p => p.url === '/api/hmc/collect')!
    expect(post.opts.body).toEqual({ ip: '10.99.9.1', username: 'ro', password: 'pw' })
    post.resolve({ system_count: 1, lpar_count: 2 })
    await flush(() => ({ items: [{ ip: '10.99.9.1', hostname: 'hmc1', collected: true }], total: 1, collected: 1 }))
    expect(w.find('.modal').exists()).toBe(false)
    await w.findAll('button').find(b => b.text() === '收集')!.trigger('click')     // 重新開視窗：密碼已清空
    expect((w.findAll('.modal input')[1].element as HTMLInputElement).value).toBe('')
  })
  it('明細 hd=：一列一個 LPAR，帶所屬系統', async () => {
    route.query = { tab: 'hmc', hd: '10.99.9.1' }
    const w = mount(Hmc, opts)
    await flush(p => (p.url.startsWith('/api/hmc/consoles/') ? { data: { hmc_version: 'V10', system_count: 2, lpar_count: 3, systems: [
      { name: 'P1', lpars: [{ name: 'lp1', lpar_id: 1 }, { name: 'lp2', lpar_id: 2 }] }, { name: 'P2', lpars: [{ name: 'lp3', lpar_id: 1 }] }] } } : { items: [], total: 0, collected: 0 }))
    const rows = w.findAll('[data-prefix="L"] .row')
    expect(rows.length).toBe(3)
    expect(rows[2].find('.c-sys').text()).toBe('P2')
    expect(w.findAll('[data-prefix="M"] .row').length).toBe(2)
  })
  it('選已設定的憑證：body 只帶憑證名稱、不帶帳密；取消會清密碼；非管理員不載入憑證清單', async () => {
    const w0 = mount(Hmc, opts)
    await flushPromises()
    expect(pending.some(p => p.url === '/api/credentials')).toBe(false)
    pending.splice(0)
    auth.isAdmin = true
    const w = mount(Hmc, opts)
    await flush(p => (p.url === '/api/credentials' ? [{ name: 'hmc-ro', username: 'ro', kind: 'storage' }] : { items: [{ ip: '10.99.9.1', hostname: 'hmc1', collected: false }], total: 1, collected: 0 }))
    await w.findAll('button').find(b => b.text() === '收集')!.trigger('click')
    await w.findAll('.modal input')[1].setValue('typed')
    await w.findAll('.modal button').find(b => b.text() === '取消')!.trigger('click')
    await w.findAll('button').find(b => b.text() === '收集')!.trigger('click')
    expect((w.findAll('.modal input')[1].element as HTMLInputElement).value).toBe('')
    await w.find('.modal select').setValue('hmc-ro')
    await w.findAll('.modal button').find(b => b.text() === '開始收集')!.trigger('click')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/hmc/collect')!.opts.body).toEqual({ ip: '10.99.9.1', credential: 'hmc-ro' })
    w0.unmount()
  })
})
