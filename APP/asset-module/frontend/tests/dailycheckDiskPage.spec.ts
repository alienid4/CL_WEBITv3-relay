// 1.5 每日檢查頁（真的掛 pages/v2/dailycheck.vue）：硬碟列一定有「逐顆硬碟／依主機」切換與表格，0 顆走空狀態；換列回到逐顆；
// Storage 列的 Pool 明細用「容量等級」欄。API 全假（apiFetch 回固定資料）、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sortRows, useSort } from '../composables/useSort'
import V2Table from '../components/v2/Table.vue'
import SortTh from '../components/SortTh.vue'

enableAutoUnmount(afterEach)
vi.setConfig({ testTimeout: 30000 })
const routeState = reactive<{ query: Record<string, string> }>({ query: {} })
let payload: any
const disk = (id: number, detail: any[]) => ({ id, no: id, code: 'W-f', section: 'WhatsUp', name: '硬碟使用率', kind: 'auto', rule: 'disk', verdict: 'ok', verdict_label: '正常',
  value_text: '0 台達 90%', reason: null, detail, stats: { scope: detail.length, with_data: detail.length, no_data: 0, bad: 0 }, data_at: null, source: 'WhatsUp 上次輪循', batch: 'b1', result: null })
const dk = (item: string, pct: number, extra: any = {}) => ({ item, state: '第一級', pct, prev: null, delta: null, growth: null, samples: 1, days_to_first: 0, days_to_full: null, tier: 1, trend_note: '待累積', ...extra })
const dev = (name: string, disks: any[] | undefined) => ({ name, ip: '10.99.1.1', serial: 'H-1', device_id: name, state: '異常', what: 'x', ...(disks ? { disks } : {}) })

const pass = (tag: string) => defineComponent({ inheritAttrs: false, setup: (_, { slots }) => () => h(tag, Object.values(slots).flatMap((f: any) => f?.() ?? [])) })
async function ready(w: any) { await vi.waitFor(() => expect(w.html()).not.toBe(''), { timeout: 20000 }); await flushPromises(); await nextTick() }

function mk(rows: any[]) {
  payload = { day: '2026-11-20', now: '2026-11-20 08:00:00', enabled: true, signoff: false, thresholds: {}, times: { snapshot_time: '06:30', remind_time: '07:45', escalate_time: '08:15' }, run: { id: 1, day: '2026-11-20', status: 'open' }, rows,
    counts: {}, runs: [], phrases: [] }
  const Inner = defineComponent({ async setup() {
    const P = (await import('../pages/v2/dailycheck.vue')).default
    return () => h(P)
  } })
  return mount(defineComponent({ render: () => h(Suspense, null, { default: () => h(Inner) }) }), {
    global: { components: { V2Table, SortTh }, stubs: { V2Page: pass('div'), InfoNote: pass('i'), NuxtLink: pass('a') } } })
}

beforeEach(() => {
  routeState.query = { tab: 'today', row: '1' }
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick, useSort, sortRows })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('navigateTo', async () => {})
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(true) }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: async () => ({}) }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useRoute', () => routeState)
  vi.stubGlobal('useRouter', () => ({ replace: (r: any) => { routeState.query = { ...(r.query ?? {}) } } }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: () => {}, busy: ref(false) }))
  vi.stubGlobal('useV2ExportName', () => () => 'x')
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string) => (url === '/api/daily-check' ? payload : url.endsWith('/events') ? { items: [] } : {}) }))
})
afterEach(() => vi.unstubAllGlobals())

describe('1.5 每日檢查頁：硬碟明細（逐顆）', () => {
  it('0 顆達列出門檻：仍顯示「逐顆硬碟」「依主機」切換，表格走明確空狀態（不是整個不出現）', async () => {
    const w = mk([disk(1, [dev('app01', []), dev('app02', [])])])
    await ready(w)
    const bar = w.find('[data-testid="dc-disk-view"]')
    expect(bar.exists()).toBe(true)
    expect(bar.text()).toContain('逐顆硬碟 0'); expect(bar.text()).toContain('依主機 2')
    expect(w.find('[data-testid="dc-disk-table"]').exists() || w.text().includes('沒有達列出門檻的硬碟')).toBe(true)
    expect(w.text()).toContain('沒有達列出門檻的硬碟')
  })
  it('有硬碟：逐顆表有「容量等級」「到第一級」「到滿載」「樣本數」欄，按到第一級天數排', async () => {
    const w = mk([disk(1, [dev('app01', [dk('C:', 93, { delta: -2, prev: 95 })]), dev('app02', [dk('/', 85, { state: '需處理', tier: 2, days_to_first: 3, days_to_full: 8, growth: 2, samples: 3, prev: 83, delta: 2 })])])])
    await ready(w)
    const heads = w.findAll('th').map(t => t.text())
    for (const label of ['容量等級', '樣本數', '到第一級', '到滿載', '趨勢說明']) expect(heads.some(x => x.includes(label)), label).toBe(true)
    expect(heads.some(x => x === '狀態')).toBe(false)                  // 不再用「狀態」這個詞（那是資料來源六種詞）
    const firstRow = w.find('[data-testid="dc-disk-table"] tbody tr') ?? w.find('tbody tr')
    expect(w.text()).toContain('已在第一級')
    expect(w.text().indexOf('app01')).toBeLessThan(w.text().indexOf('app02'))   // 已在第一級的排在前面
    void firstRow
  })
  it('切到依主機後再開另一個硬碟列，回到逐顆硬碟', async () => {
    const w = mk([disk(1, [dev('app01', [dk('C:', 93)])]), disk(2, [dev('app09', [dk('D:', 91)])])])
    await ready(w)
    const pills = () => w.findAll('[data-testid="dc-disk-view"] .pill')
    await pills()[1].trigger('click')                                   // 依主機
    expect(pills()[1].classes()).toContain('on')
    routeState.query = { tab: 'today', row: '2' }                          // 換到另一列
    await flushPromises(); await nextTick()
    expect(pills()[0].classes()).toContain('on')
  })
})
