/**
 * 2.047.4（GPT 審查第 2 批）實掛：
 *   - 實體機分布：扇區點下去帶分類（下鑽只列那一類）；連按「重新整理」時舊請求晚回不能蓋掉新的
 *   - 單據檔案室：批次匯入只給管理員（後端 ADMIN_WRITE），一般帳號看到「限管理員」
 * Nuxt 自動匯入以 stubGlobal 提供；V2Table／DonutChart 為簡化替身；API 全假。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import PhysDist from '../components/v2/PhysDist.vue'
import Documents from '../components/v2/Documents.vue'

const route = reactive<{ query: Record<string, string> }>({ query: {} })
let pending: { url: string; opts: any; resolve: (v: any) => void }[] = []
let admin = false
beforeEach(() => {
  route.query = {}
  pending = []
  admin = false
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(admin) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: () => Promise.resolve({}) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise(resolve => pending.push({ url, opts, resolve })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'prefix'], setup(p) { return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, String(r.__no)))) } })
const Donut = defineComponent({ props: ['segments'], emits: ['segment-click'], setup(p, { emit }) {
  return () => h('div', (p.segments ?? []).map((s: any) => h('button', { class: 'seg', onClick: () => emit('segment-click', s.name) }, `${s.name} ${s.count}`)))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, DonutChart: Donut, InfoNote: pass, NuxtLink: pass } } }
const DATA = (total: number) => ({ rooms: [{ room: 'R1', total, categories: [{ name: '甲類', count: 1 }, { name: '乙類', count: total - 1 }] }], branches: [], unclassified: [], unfilled_total: 0,
  total_physical: total, off_book: {}, category_note: '', excluded_models: [], excluded_models_total: 0 })

describe('實體機分布', () => {
  it('扇區點下去：下鑽帶 room 與 category', async () => {
    const w = mount(PhysDist, opts)
    await flushPromises()
    pending.shift()!.resolve(DATA(3))
    await flushPromises()
    await w.find('.seg').trigger('click')
    await nextTick(); await flushPromises()
    const d = pending.find(x => x.url === '/api/reports/physical-distribution/drill')!
    expect(d.opts.params).toEqual({ room: 'R1', category: '甲類' })
  })
  it('連按重新整理：先送的晚回來不能蓋掉後送的', async () => {
    const w = mount(PhysDist, opts)
    await flushPromises()
    pending.shift()!.resolve(DATA(3))
    await flushPromises()
    const btn = w.findAll('button').find(b => b.text() === '重新整理')!
    await btn.trigger('click')
    await btn.trigger('click')
    const [older, newer] = pending.splice(0, 2)
    newer.resolve(DATA(9))
    await flushPromises()
    older.resolve(DATA(5))
    await flushPromises()
    expect(w.text()).toContain('實體機總計 9 台')
  })
})

describe('單據檔案室批次匯入', () => {
  it('一般帳號：沒有上傳按鈕、看到限管理員', async () => {
    const w = mount(Documents, opts)
    await flushPromises()
    expect(w.find('input[type="file"]').exists()).toBe(false)
    expect(w.find('[data-testid="doc-import-admin-only"]').exists()).toBe(true)
  })
  it('管理員：有上傳按鈕', async () => {
    admin = true
    const w = mount(Documents, opts)
    await flushPromises()
    expect(w.find('input[type="file"]').exists()).toBe(true)
    expect(w.find('[data-testid="doc-import-admin-only"]').exists()).toBe(false)
  })
})
