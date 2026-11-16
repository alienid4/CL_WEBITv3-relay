/**
 * 2.048.10 V54／V44：9.1 重複登記合併、7.1 搬遷盤點改 V2 原生，實掛：
 *   - 併回：只有管理員能勾；勾了先出確認清單，按「確認併回」才送 apply，body＝勾選的 shell→target；完成後通知頁面重抓
 *   - 還原：confirm 取消就不送
 *   - 搬遷盤點：匯出網址帶目前機房（rl）；非管理員沒有匯出／上傳／收集
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import ScanShells from '../components/v2/ScanShells.vue'
import Relocation from '../components/v2/Relocation.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void }[] = []
const route = reactive<{ query: Record<string, string> }>({ query: {} })
const auth = { isAdmin: true }
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = true
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (r: any) => { route.query = r.query } }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(true), isViewer: ref(false) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise(resolve => pending.push({ url, opts, resolve })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

// V2Table 替身：selectable 時每列一個勾選框（emit update:selected），有具名插槽就畫插槽
const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'selectable', 'selected', 'rowKey'], emits: ['update:selected'], setup(p, { slots, emit }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [
    p.selectable ? h('input', { type: 'checkbox', class: 'pick', onChange: () => emit('update:selected', { ...(p.selected ?? {}), [r[p.rowKey]]: true }) }) : null,
    ...(p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? '')))])))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: pass } } }
const SH = { counts: { mergeable: 2, review: 0, unregistered: 0 }, history: [{ id: 9, merged_at: 't', shell_serial: 'DYN-9', target_serial: 'C-9', restored_at: null }],
  items: [{ shell_serial: 'DYN-1', kind: 'mergeable', target_serial: 'C-1', candidates: [], reason: 'r', hostname: 'h1', ip: '10.99.0.1' },
          { shell_serial: 'DYN-2', kind: 'mergeable', target_serial: 'C-2', candidates: [], reason: 'r', hostname: 'h2', ip: '10.99.0.2' }] }
const flush = async (v: any) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(typeof v === 'function' ? v(p) : v); await flushPromises() }

describe('重複登記合併（V54）', () => {
  it('勾一筆 → 預覽 → 確認併回：只送那一筆，完成後 emit changed', async () => {
    const w = mount(ScanShells, opts)
    await flush(SH)
    await w.findAll('input.pick')[1].trigger('change')
    expect(pending.length).toBe(0)
    await w.findAll('button').find(b => b.text().startsWith('預覽併回'))!.trigger('click')
    expect(w.find('[data-test="confirm"]').text()).toContain('DYN-2')
    expect(pending.length).toBe(0)                                      // 預覽不寫入
    await w.findAll('button').find(b => b.text() === '確認併回')!.trigger('click')
    await flushPromises()
    const ap = pending.find(p => p.url === '/api/scan-shells/apply')!
    expect(ap.opts.body).toEqual({ pairs: [{ shell_serial: 'DYN-2', target_serial: 'C-2' }] })
    ap.resolve({ merged: ['DYN-2'], skipped: [] })
    await flush(SH)
    expect(w.emitted('changed')).toBeTruthy()
  })
  it('非管理員：沒有勾選框也沒有預覽', async () => {
    auth.isAdmin = false
    const w = mount(ScanShells, opts)
    await flush(SH)
    expect(w.findAll('input.pick').length).toBe(0)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
  })
  it('還原：confirm 取消就不送', async () => {
    route.query = { tab: 'shells', sk: 'history' }
    vi.stubGlobal('confirm', () => false)
    const w = mount(ScanShells, opts)
    await flush(SH)
    await w.findAll('button').find(b => b.text() === '還原')!.trigger('click')
    expect(pending.some(p => p.url.startsWith('/api/scan-shells/restore'))).toBe(false)
  })
})

describe('搬遷盤點（V44）', () => {
  const LOCS = (p: any) => (p.url === '/api/relocation/locations' ? { items: [{ loc: '甲機房', n: 3 }, { loc: '乙機房', n: 2 }] } : { collected: 1, last: null })
  it('匯出網址帶目前機房；全部時不帶', async () => {
    route.query = { tab: 'relocate', rl: '甲機房' }
    const w = mount(Relocation, opts)
    await flush(LOCS)
    expect(w.find('a.btn').attributes('href')).toBe('/api/relocation/export?location=%E7%94%B2%E6%A9%9F%E6%88%BF')
    expect(w.text()).toContain('本次匯出 3 台')
    route.query = { tab: 'relocate' }
    await flushPromises()
    expect(w.find('a.btn').attributes('href')).toBe('/api/relocation/export')
  })
  it('非管理員：沒有匯出、上傳、收集', async () => {
    auth.isAdmin = false
    const w = mount(Relocation, opts)
    await flush(LOCS)
    expect(w.find('a.btn').exists()).toBe(false)
    expect(w.find('input[type="file"]').exists()).toBe(false)
    expect(w.findAll('button').some(b => b.text().includes('收集硬體規格'))).toBe(false)
  })
  it('收集硬體規格：確認取消就不送', async () => {
    vi.stubGlobal('confirm', () => false)
    const w = mount(Relocation, opts)
    await flush(LOCS)
    await w.findAll('button').find(b => b.text().includes('收集硬體規格'))!.trigger('click')
    expect(pending.some(p => p.url === '/api/host-spec/collect')).toBe(false)
  })
  it('機房清單載不到：上傳對帳照樣在', async () => {
    const w = mount(Relocation, opts)
    await flushPromises()
    for (const p of pending.splice(0)) p.url === '/api/relocation/locations' ? (p as any).resolve(Promise.reject(new Error('x'))) : p.resolve({ collected: 0, last: null })
    await flushPromises()
    expect(w.text()).toContain('載入失敗')
    expect(w.find('input[type="file"]').exists()).toBe(true)
  })
})

describe('重複登記合併：非管理員的併回紀錄（審查 M4）', () => {
  it('沒有還原按鈕', async () => {
    auth.isAdmin = false
    route.query = { tab: 'shells', sk: 'history' }
    const w = mount(ScanShells, opts)
    await flush(SH)
    expect(w.findAll('button').some(b => b.text() === '還原')).toBe(false)
    expect(w.text()).toContain('未還原')
  })
})
