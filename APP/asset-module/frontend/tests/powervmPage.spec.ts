// PowerVM 可觀測架構圖（3.2 架構圖 PowerVM 頁籤）：純函式＋元件實掛。
// 重點：示範橫幅一直在、空狀態文案、未取得≠0、過期標示、四層未取得、viewer 也看得到、示範不能匯出／沒有下鑽、失敗不留舊資料。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身；API 全假、無網路（名稱 DEMO-／TEST-、IP 10.99.x）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as pv from '../composables/powervmView'
import Comp from '../components/v2/PowerVm.vue'

const LAYERS = [
  { key: 'hmc', label: 'HMC', available: true }, { key: 'frame', label: '實體機', available: true },
  { key: 'vios', label: 'VIOS', available: false, reason: '尚未收集（不據此推測）' }, { key: 'lpar', label: 'LPAR', available: true },
  { key: 'network', label: '網路', available: false, reason: '尚未收集（不由名稱或 IP 猜連線）' },
  { key: 'san', label: 'SAN', available: false, reason: '尚未收集（SAN zone 不等於實際存取，不畫成連線）' },
  { key: 'storage', label: '儲存', available: false, reason: '尚未收集' },
]
const lp = (id: string, name: string, o: any = {}) => ({ key: `10.99.0.1|S1|${id}`, lpar_id: id, name, env: 'aixlinux', state: 'Running', os_version: null, proc_units: null, procs: null, mem_mb: null, ...o })
const REAL = (over: any = {}) => ({
  mode: 'real', empty: false, stale_days: 14, layers: LAYERS, next_step: null,
  summary: { consoles: 2, frames: 1, lpars: 3, stale_consoles: 1, error_consoles: 0, unknown_cpu_lpars: 1, unknown_mem_lpars: 1 },
  consoles: [
    { ip: '10.99.0.1', hostname: 'TEST-HMC', hmc_version: 'V10', collected_at: '2026-11-02 08:00:00', source: 'HMC 唯讀指令（lssyscfg／lshwres）', status: { code: 'fresh', label: '有資料' },
      systems: [{ key: '10.99.0.1|S1', frame_key: 'S1', name: 'TEST-FRAME', type_model: '9080-HEX', serial: 'S1', state: 'Operating', firmware: 'FW1', machine_link: '/v2/machine/S1', lpar_count: 3,
        cpu_sum: 2, cpu_unknown: 1, mem_sum_mb: 4096, mem_unknown: 1,
        lpars: [lp('1', 'TEST-A', { proc_units: 2, procs: 4, mem_mb: 4096 }), lp('2', 'TEST-ZERO', { proc_units: 0, procs: 0, mem_mb: 0 }), lp('3', 'TEST-NULL')] }] },
    { ip: '10.99.0.2', hostname: 'TEST-HMC2', hmc_version: 'V9', collected_at: '2026-09-01 08:00:00', source: 'HMC 唯讀指令（lssyscfg／lshwres）', status: { code: 'stale', label: '資料已 62 天未更新' }, systems: [] },
  ], ...over,
})
const EMPTY = () => ({ mode: 'real', empty: true, stale_days: 14, layers: LAYERS, consoles: [], next_step: '到 6.2 設備收集的「HMC」收集一次，這裡才會有資料',
  summary: { consoles: 0, frames: 0, lpars: 0, stale_consoles: 0, error_consoles: 0, unknown_cpu_lpars: 0, unknown_mem_lpars: 0 } })
const DEMO = () => ({ ...REAL(), mode: 'demo', consoles: [{ ...REAL().consoles[0], hostname: 'DEMO-HMC', source: '示範資料（模擬）', systems: [{ ...REAL().consoles[0].systems[0], name: 'DEMO-FRAME', machine_link: null }] }] })

const route = reactive<{ query: Record<string, string> }>({ query: {} })
const calls: string[] = []
let viewer = false
let mode: 'real' | 'empty' | 'fail' = 'real'

beforeEach(() => {
  route.query = {}; calls.length = 0; viewer = false; mode = 'real'
  for (const [k, v] of Object.entries({ ref, computed, watch, onMounted, reactive })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string) => {
    calls.push(u)
    if (mode === 'fail') return Promise.reject(Object.assign(new Error('x'), { data: { detail: '資料庫忙碌' } }))
    if (u.endsWith('/demo')) return Promise.resolve(DEMO())
    return Promise.resolve(mode === 'empty' ? EMPTY() : REAL())
  } }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'empty', 'noExport'], setup(p) {
  return () => h('div', { class: 'tbl', 'data-noexport': String(!!p.noExport) }, p.rows.length
    ? p.rows.map((r: any) => h('div', { class: 'row' }, [`${p.prefix}${r.__no} `, ...p.cols.map((c: any) => `[${c.label}:${c.text ? c.text(r) : r[c.k]}${c.link && c.link(r) ? '→' + c.link(r) : ''}]`)]))
    : [h('p', { class: 'tbl-empty' }, p.empty)])
} })
const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
async function mountIt() {
  const w = mount(Comp, { global: { components: { V2Table, InfoNote: passthrough, NuxtLink: passthrough } } })
  await flushPromises()
  return w
}

describe('powervmView 純函式', () => {
  it('未取得與 0 分開', () => {
    expect(pv.cpuText(null)).toBe('未取得'); expect(pv.cpuText(0)).toBe('0'); expect(pv.cpuText(2.5)).toBe('2.5')
    expect(pv.memText(undefined)).toBe('未取得'); expect(pv.memText(0)).toBe('0 GB'); expect(pv.memText(4096)).toBe('4 GB')
    expect(pv.vcpuText(null)).toBe('未取得'); expect(pv.vcpuText(0)).toBe('0')
  })
  it('篩選：未取得不含 0；回報為 0 只含 0；項次在篩選前編好', () => {
    const rows = pv.lparRows(REAL().consoles)
    expect(rows.map(r => r.__no)).toEqual([1, 2, 3])
    expect(pv.filterLpars(rows, 'cpu_unknown').map(r => r.name)).toEqual(['TEST-NULL'])
    expect(pv.filterLpars(rows, 'zero').map(r => r.name)).toEqual(['TEST-ZERO'])
    expect(pv.filterLpars(rows, 'cpu_unknown')[0].__no).toBe(3)
    expect(pv.filterLpars(rows, 'stale')).toEqual([])
    expect(pv.pvFilter('abc')).toBe('all'); expect(pv.pvFilter('stale')).toBe('stale')
  })
  it('格子配置：HMC 跨它的實體機欄；沒有實體機的 HMC 佔一欄', () => {
    const p = pv.gridPlan(REAL().consoles)
    expect(p.cols).toBe(2)
    expect(p.hmcs.map(h => [h.start, h.span])).toEqual([[1, 1], [2, 1]])
    expect(p.frames[1].s).toBeNull()
  })
})

describe('PowerVM 頁籤', () => {
  it('正式模式：分層軌、四層未取得、未取得≠0、不顯示示範橫幅', async () => {
    const w = await mountIt()
    const t = w.text()
    expect(calls).toEqual(['/api/arch/powervm'])
    expect(w.find('.demo-bar').exists()).toBe(false)
    expect(t).toContain('VIOS：未取得')
    expect(w.find('[data-missing="network"]').text()).toContain('未取得')
    expect(w.find('[data-missing="san"]').text()).toContain('不等於實際存取')
    expect(w.find('[data-missing="storage"]').exists()).toBe(true)
    const lpars = w.findAll('[data-lpar]').map(x => x.text())
    expect(lpars[1]).toContain('CPU 0・vCPU 0・記憶體 0 GB')              // 回報 0
    expect(lpars[2]).toContain('CPU 未取得・vCPU 未取得・記憶體 未取得')   // 沒回報
    expect(t).toContain('另 1 台 LPAR 未取得')                            // 合計不當成完整值
  })
  it('過期 HMC 標「資料已 N 天未更新」；沒有實體機的 HMC 明講沒資料', async () => {
    const t = (await mountIt()).text()
    expect(t).toContain('資料已 62 天未更新')
    expect(t).toContain('這個 HMC 沒有實體機資料')
  })
  it('每個節點顯示來源與收集時間', async () => {
    const w = await mountIt()
    expect(w.find('[data-hmc="10.99.0.1"]').text()).toContain('來源：HMC 唯讀指令')
    expect(w.find('[data-hmc="10.99.0.1"]').text()).toContain('收集時間：2026-11-02 08:00:00')
    expect(w.find('[data-frame="10.99.0.1|S1"]').text()).toContain('來源：')
  })
  it('空狀態：說明下一步、不自動用示範補畫', async () => {
    mode = 'empty'
    const w = await mountIt()
    expect(w.text()).toContain('目前沒有任何已收集的 HMC 資料')
    expect(w.text()).toContain('6.2 設備收集')
    expect(w.find('[data-diagram]').exists()).toBe(false)
    expect(calls).toEqual(['/api/arch/powervm'])          // 只打正式端點
    expect(w.text()).not.toContain('DEMO-')
  })
  it('示範模式：橫幅一直在、DEMO 資料、不能匯出、沒有下鑽連結', async () => {
    route.query = { demo: '1' }
    const w = await mountIt()
    expect(calls).toEqual(['/api/arch/powervm/demo'])
    expect(w.find('.demo-bar').text()).toContain('示範資料')
    expect(w.text()).toContain('DEMO-FRAME')
    expect(w.find('.tbl').attributes('data-noexport')).toBe('true')
    expect(w.text()).not.toContain('→/v2/machine')
  })
  it('正式模式：表格可匯出、實體機名稱有下鑽到主機頁', async () => {
    const w = await mountIt()
    expect(w.find('.tbl').attributes('data-noexport')).toBe('false')
    expect(w.text()).toContain('→/v2/machine/S1')
  })
  it('切示範／切回正式：切回後不殘留示範資料', async () => {
    route.query = { demo: '1' }
    const w = await mountIt()
    expect(w.text()).toContain('DEMO-')
    route.query = {}
    await flushPromises()
    expect(w.find('.demo-bar').exists()).toBe(false)
    expect(w.text()).not.toContain('DEMO-')
    expect(calls).toEqual(['/api/arch/powervm/demo', '/api/arch/powervm'])
  })
  it('卡片下鑽：點「CPU 未取得」只列未取得的那一台（網址帶篩選）', async () => {
    const w = await mountIt()
    expect(w.text()).toContain('L1 ')
    await w.find('[data-card="cpu"]').trigger('click')
    await flushPromises()
    expect(route.query.f).toBe('cpu_unknown')
    const rows = w.findAll('.row').map(r => r.text())
    expect(rows).toHaveLength(1)
    expect(rows[0]).toContain('TEST-NULL'); expect(rows[0]).toContain('L3 ')
  })
  it('viewer 也看得到整張圖（唯讀頁）', async () => {
    viewer = true
    const w = await mountIt()
    expect(w.find('[data-diagram]').exists()).toBe(true)
    expect(w.text()).toContain('TEST-FRAME')
  })
  it('載入失敗：顯示錯誤、不留舊資料', async () => {
    mode = 'fail'
    const w = await mountIt()
    expect(w.text()).toContain('PowerVM 架構載入失敗：資料庫忙碌')
    expect(w.find('[data-diagram]').exists()).toBe(false)
  })
})
