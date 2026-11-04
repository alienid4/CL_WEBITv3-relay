/**
 * 2.048.6 V50／V45：10.1 主機系統總覽、3.1 業務系統排行改 V2 原生，實掛：
 *   - 格子／卡片連結帶的條件＝下鑽送到後端的條件（room／bucket、api_id／category；全環境不帶 bucket）
 *   - 下鑽：快速換條件時舊請求晚回來不覆蓋（useLatest）
 *   - 分類對照表：上傳限管理員
 * Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身（會把 link 畫成 data-to）；API 全假。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import SystemOverview from '../components/v2/SystemOverview.vue'
import BizRank from '../components/v2/BizRank.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void }[] = []
const route = reactive<{ query: Record<string, string> }>({ query: {} })
const auth = { isAdmin: false }
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = false
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('navigateTo', () => {})
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(true), isViewer: ref(false) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise(resolve => pending.push({ url, opts, resolve })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => { const to = c.link ? c.link(r) : null; return h('span', { class: `c-${c.k}`, 'data-to': to ? JSON.stringify(to.query ?? to) : undefined }, String(c.text ? c.text(r) : r[c.k] ?? '')) }))))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: pass, DonutChart: pass } } }
const OV = { total: 3, core: 1, noncore: 1, test: 0, uncategorized: 1, vm: 2, physical: 1, virtualization_rate: 66.7,
  rooms: [{ room: '甲', core: 1, noncore: 0, test: 0, uncategorized: 1, total: 2 }], core_categories: [{ name: '下單', count: 1, color: 'chart-1', pct: 33.3 }],
  noncore_categories: [], core_top5: [{ api_id: 'AP1', name: '下單系統', count: 1 }], core_top5_pct: [100], core_other_count: 0,
  all_systems: [{ api_id: 'AP1', name: '下單系統', count: 1, category: '下單' }], systems_without_category: 0, category_note: '', room_note: '' }
const resolveMain = async () => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(OV); await flushPromises() }

describe('主機系統總覽（V50）', () => {
  it('機房格子帶 room＋bucket；全環境格子只帶 room；下鑽送一樣的條件', async () => {
    const w = mount(SystemOverview, opts)
    await resolveMain()
    const row = w.find('[data-prefix="R"] .row')
    expect(JSON.parse(row.find('.c-core').attributes('data-to')!)).toEqual({ tab: 'overview', or: '甲', ob: 'core' })
    expect(JSON.parse(row.find('.c-total').attributes('data-to')!)).toEqual({ tab: 'overview', or: '甲' })
    expect(row.find('.c-noncore').attributes('data-to')).toBeUndefined()        // 0 不給連結
    route.query = { tab: 'overview', or: '甲', ob: 'core' }
    await flushPromises()
    const d = pending.find(p => p.url.endsWith('/drill'))!
    expect(d.opts.query).toEqual({ bucket: 'core', room: '甲' })
  })
  it('下鑽：舊請求晚回來不覆蓋新的', async () => {
    route.query = { tab: 'overview', ob: 'core' }
    const w = mount(SystemOverview, opts)
    await flushPromises()
    const first = pending.splice(0)
    for (const p of first) if (!p.url.endsWith('/drill')) p.resolve(OV)
    const older = first.filter(p => p.url.endsWith('/drill'))
    route.query = { tab: 'overview', ob: 'test' }
    await flushPromises()
    for (const p of pending.splice(0)) p.resolve(p.url.endsWith('/drill') ? [{ asset_serial: 'NEW', hostname: 'new' }] : OV)
    await flushPromises()
    for (const p of older) p.resolve([{ asset_serial: 'OLD', hostname: 'old' }, { asset_serial: 'OLD2', hostname: 'old2' }])
    await flushPromises()
    expect(w.findAll('[data-prefix="D"] .row').length).toBe(1)
    expect(w.text()).toContain('new')
  })
})

describe('業務系統排行（V45）', () => {
  it('Top5 帶 api_id＋core；全環境卡片 bb=all 下鑽不帶 bucket', async () => {
    const w = mount(BizRank, opts)
    await resolveMain()
    expect(JSON.parse(w.find('[data-prefix="T"] .c-count').attributes('data-to')!)).toEqual({ tab: 'rank', ba: 'AP1', bb: 'core' })
    route.query = { tab: 'rank', bb: 'all' }
    await flushPromises()
    expect(pending.find(p => p.url.endsWith('/drill'))!.opts.query).toEqual({})
  })
  it('上傳對照表：非管理員沒有、管理員有', async () => {
    const w = mount(BizRank, opts)
    await resolveMain()
    expect(w.find('input[type="file"]').exists()).toBe(false)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    auth.isAdmin = true
    const w2 = mount(BizRank, opts)
    await resolveMain()
    expect(w2.find('input[type="file"]').exists()).toBe(true)
  })
})

describe('數字與連結對得上（審查 2 P2-6）', () => {
  const Link = defineComponent({ props: ['to'], setup(pp, { slots }) {
    return () => h('a', { class: 'lk', 'data-to': typeof pp.to === 'string' ? pp.to : JSON.stringify(pp.to?.query ?? pp.to) }, slots.default?.())
  } })
  const o2 = { global: { components: { V2Table, InfoNote: pass, NuxtLink: Link, DonutChart: pass } } }
  const OV2 = { ...OV, core: 11, noncore: 12, test: 13, uncategorized: 14, total: 50 }
  it('業務系統排行五格：數字＝後端欄位、連結＝自己的桶', async () => {
    const w = mount(BizRank, o2)
    await flushPromises(); for (const p of pending.splice(0)) p.resolve(OV2); await flushPromises()
    const cards = w.findAll('a.kc')
    const want: [string, number][] = [['core', 11], ['noncore', 12], ['test', 13], ['uncategorized', 14], ['all', 50]]
    expect(cards.length).toBe(5)
    cards.forEach((c, i) => {
      expect(JSON.parse(c.attributes('data-to')!).bb).toBe(want[i][0])
      expect(c.text()).toContain(String(want[i][1]))
    })
  })
  it('主機系統總覽分組表：每一列的連結帶自己的桶，台數＝後端欄位', async () => {
    const w = mount(SystemOverview, o2)
    await flushPromises(); for (const p of pending.splice(0)) p.resolve(OV2); await flushPromises()
    const rows = w.findAll('[data-prefix="G"] .row')
    const want: [string, string, number][] = [['核心交易', 'core', 11], ['非核心', 'noncore', 12], ['測試', 'test', 13], ['未分類', 'uncategorized', 14]]
    expect(rows.length).toBe(4)
    rows.forEach((r, i) => {
      expect(r.find('.c-name').text()).toBe(want[i][0])
      expect(r.find('.c-count').text()).toBe(String(want[i][2]))
      expect(JSON.parse(r.find('.c-count').attributes('data-to')!).ob).toBe(want[i][1])
    })
  })
})
