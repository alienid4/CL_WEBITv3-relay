// 容量分析（10.3）：純函式（格子文字／矩陣／設定轉換）＋頁面實掛（範例時段警語、覆蓋不足與未收集不是 0、空白狀態、示範標示、設定只有管理員能改、儲存送出的內容）。
// Nuxt 自動匯入以 stubGlobal 提供；V2Page／V2Table 為簡化替身；API 全假、無網路（主機名 TEST-/DEMO-）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as cv from '../composables/capacityView'
import Page from '../pages/v2/capacity.vue'

const cell = (o: Partial<cv.CapCell>): cv.CapCell => ({ day_type: 'normal', slot: 'early', slot_label: '早盤', metric: 'cpu_busy', samples: 100, expected: 100, days: 5, coverage_pct: 100,
  status: 'ok', avg: 50, p95: 90, max: 95, max_at: '2026-09-08 09:40:00', ...o })

describe('capacityView 純函式', () => {
  it('覆蓋不足／未收集／無資料：寫出來，不是 0 也不是空白', () => {
    expect(cv.capCellText(cell({}), 'p95')).toBe('90%')
    expect(cv.capCellText(cell({ status: 'low_coverage', coverage_pct: 45, avg: null, p95: null, max: 70 }), 'p95')).toBe('覆蓋不足 45%（至少出現 70%）')
    expect(cv.capCellText(cell({ status: 'not_collected', p95: null }), 'p95')).toBe('未收集')
    expect(cv.capCellText(cell({ status: 'no_data', p95: null }), 'max')).toBe('無資料')
    expect(cv.capCellText(cell({ status: 'coarse_interval', p95: null, avg: null, max: null }), 'p95')).toBe('間隔太粗無法評估')
    expect(cv.capCellText(cell({ status: 'few_days', days: 3, p95: null, avg: null, max: 12 }), 'max')).toBe('天數不足（只有 3 天）（至少出現 12%）')
    expect(cv.capCellValue(cell({ status: 'few_days' }), 'max')).toBeNull()
    expect(cv.capCellText(undefined, 'p95')).toBe('無資料')
    expect(cv.capCellText(cell({ p95: null, p95_note: 'x' }), 'p95')).toBe('P95 不可得（只有 avg／max）')
  })
  it('排序用的值：只有正常格有數字；統計可切換', () => {
    expect(cv.capCellValue(cell({}), 'max')).toBe(95)
    expect(cv.capCellValue(cell({ status: 'low_coverage' }), 'p95')).toBeNull()
    expect(cv.capStat('xx')).toBe('max')                     // 預設看尖峰（全期最大），不是平均
    expect(cv.capStat('p95')).toBe('p95')
    expect(cv.capStat('top5')).toBe('top5')
    expect(cv.capStat('avg')).toBe('avg')
  })
  it('矩陣列：項次用後端編號（不隨排序變）；每個時段一欄', () => {
    const host = { host: 'TEST-1', no: 7, evidence: 'limited', evidence_label: '有限制，只能參考', limits: [{ code: 'a', text: 'x' }], days_by_type: { normal: 5 },
      cells: [cell({ slot: 'early' }), cell({ slot: 'mid', p95: 12 })] } as any
    const r = cv.capMatrixRows([host], ['early', 'mid', '_other'], 'normal', 'cpu_busy', 'p95')[0]
    expect(r.__no).toBe(7)
    expect(r.s_early).toBe(90)
    expect(r.s_mid).toBe(12)
    expect(r.s__other).toBeNull()
    expect(r.text._other).toBe('無資料')
    expect(r.limits_n).toBe(1)
  })
  it('限制攤平：項次、範圍（全部共通／這台）', () => {
    const rows = cv.capLimitRows([{ host: 'A', limits: [{ code: 'no_business_latency', text: 't' }, { code: 'span_short', text: 's' }] } as any], [{ code: 'no_business_latency', text: 't' }])
    expect(rows.map((r: any) => [r.__no, r.scope])).toEqual([[1, '全部主機共通'], [2, '這台']])
  })
  it('設定文字 ⇄ 日期清單', () => {
    const l = cv.capTextToDates('2026/09/04 結算日\n\n2026-09-25')
    expect(l).toEqual([{ date: '2026-09-04', note: '結算日' }, { date: '2026-09-25', note: '' }])
    expect(cv.capDatesToText(l)).toBe('2026-09-04 結算日\n2026-09-25')
  })
})

const route = reactive<{ params: Record<string, string>; query: Record<string, string>; path: string }>({ params: {}, query: {}, path: '/v2/capacity' })
const calls: { url: string; opts: any }[] = []
const toasts: { m: string; kind?: string }[] = []
let admin = true
let viewer = false
let example = true
let hostsOverride: any[] | null = null

const mkHost = (no: number, host: string, cells: any[], evidence = 'limited') => ({ host, no, evidence, evidence_label: evidence === 'limited' ? '有限制，只能參考' : '覆蓋不足', data_days: 25, span_days: 35, from: '2026-09-07', to: '2026-10-11',
  interval_s: 60, days_by_type: { normal: 25, off: 0 }, annotated_days: [], peak_cpu: { slot_label: '早盤', value: 90, basis: 'p95' }, source_label: 'NMON', context: { mapped: false }, context: {}, cells,
  limits: [{ code: 'no_business_latency', text: '沒有業務延遲' }, { code: 'span_short', text: '資料只有 35 天' }] })
const OVERVIEW = () => ({
  demo: false, other_key: '_other', other_label: '其他時間', static_limits: [{ code: 'no_business_latency', text: '沒有業務延遲' }],
  metrics: [{ key: 'cpu_busy', label: 'CPU 忙碌', unit: '%' }, { key: 'mem_used', label: '記憶體使用', unit: '%' }],
  day_types: [{ key: 'normal', label: '交易日' }, { key: 'off', label: '非交易日' }],
  config: { slots: [{ key: 'early', label: '盤中', start: '09:30', end: '13:30' }, { key: 'open', label: '開盤時段（重點）', start: '09:00', end: '09:30', priority: true }], slots_are_example: false,
    slots_source: example ? '使用者提供（2026-11-01）' : '管理員設定', unprovided: example ? ['盤後批次的起訖時間（使用者尚未提供）'] : [], marker_types: ['大量交易日', '其他情境'], marker_reasons: ['預期大漲'],
    heavy_days: [], holidays: [], params: { min_coverage_pct: 80, min_days: 20, target_span_days: 90, high_pct: 80 }, warnings: [] },
  sources: [{ key: 'nmon_series', label: 'NMON', available: true, hosts: 1, rows: 25, from: '2026-09-07', to: '2026-10-11', usable_for_slots: true, note: 'ok' }],
  hosts: hostsOverride ?? [mkHost(1, 'TEST-A', [cell({}), cell({ slot: 'open', slot_label: '開盤時段（重點）', priority: true, status: 'low_coverage', coverage_pct: 45, avg: null, p95: null, max: 70 }),
    cell({ slot: 'early', metric: 'mem_used', status: 'not_collected', avg: null, p95: null, max: null })]), mkHost(2, 'TEST-B', [cell({ p95: 12, max: 20 })], 'insufficient')],
})

const MOPT = { types: ['大量交易日', '其他情境'], reasons: ['預期大漲', '預期大跌', '重大事件', '其他'], levels: ['低', '中', '高'], default_ranges: [], kinds: {}, server_now: '2026-09-09T17:00:00', today_kind: 'post_fact' }
let MARKERS: any[] = []
const mk = (id: number, o: any = {}) => ({ id, trade_date: '2026-09-10', ranges: [{ start: '09:00', end: '09:30' }, { start: '09:30', end: '13:30' }], mtype: '大量交易日', reason_cat: '預期大漲', expected_level: '高',
  basis: '預期央行消息', note: '', market_note: '', market_source: '', source_kind: 'pre_announce', origin_source_kind: 'pre_announce', kind_label: '事前預告', origin_kind_label: '事前預告', status: 'active', status_label: '有效', created_by: 'admin', created_at: '2026-09-09T17:00:00',
  supersedes_id: null, locked: false, event_ids: [], voided_at: null, ...o })

beforeEach(() => {
  MARKERS = []
  route.query = {}; calls.length = 0; toasts.length = 0; admin = true; viewer = false; example = true; hostsOverride = null
  for (const [k, v] of Object.entries({ ...cv, ref, reactive, computed, watch, onMounted, onBeforeUnmount })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer), isAdmin: ref(admin) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string, kind?: string) => toasts.push({ m, kind }) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => {
    calls.push({ url: u, opts: o })
    if (u === '/api/v2/capacity/markers') return Promise.resolve({ markers: MARKERS, options: MOPT })
    if (u.endsWith('/audit')) return Promise.resolve({ audit: [{ at: '2026-09-09T17:00:00', actor: 'admin', action: 'create', before: null, after: { basis: 'x' } }] })
    return Promise.resolve(u.endsWith('/demo') ? { ...OVERVIEW(), demo: true } : OVERVIEW())
  } }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: (u: string, o: any) => { calls.push({ url: u, opts: o }); return Promise.resolve({}) } }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Page = defineComponent({ props: ['modelValue', 'tabs'], setup(_p, { slots }) { return () => h('div', [slots.title?.(), slots.conclusion?.(), slots.tab?.()]) } })
const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'empty'], setup(p) {
  return () => h('div', { class: 'tbl' }, p.rows.length
    ? p.rows.map((r: any) => h('div', { class: 'row' }, [`${p.prefix}${r.__no} `, ...p.cols.map((c: any) => `[${c.label}:${c.text ? c.text(r) : r[c.k]}]`)]))
    : [h('p', { class: 'tbl-empty' }, p.empty)])
} })
const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
async function mountPage() {
  const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(Page) }) })
  const w = mount(Wrap, { global: { components: { V2Page, V2Table, InfoNote: passthrough, NuxtLink: passthrough } } })
  await flushPromises()
  return w
}

describe('容量分析頁面', () => {
  it('矩陣：有項次；覆蓋不足、未收集寫出來；沒有「全天平均」欄', async () => {
    const w = await mountPage()
    const t = w.text()
    expect(t).toContain('H1 ')
    expect(t).toContain('[盤中:95%]')                       // 預設統計＝全期最大
    expect(t).toContain('[★ 開盤時段（重點）:覆蓋不足 45%（至少出現 70%）]')
    expect(t).toContain('[其他時間:無資料]')
    expect(t).not.toContain('全天平均:')
    expect(t).toContain('H2 ')
  })
  it('預設時段是使用者提供的：標來源；盤後批次標待使用者提供（不編）；存過後標管理員設定', async () => {
    let w = await mountPage()
    expect(w.find('[data-slots-source]').text()).toContain('使用者提供（2026-11-01）')
    expect(w.find('[data-slots-source]').text()).toContain('待使用者提供')
    expect(w.text()).not.toContain('範例')
    example = false
    w = await mountPage()
    expect(w.find('[data-slots-source]').text()).toContain('管理員設定')
    expect(w.find('[data-slots-source]').text()).not.toContain('待使用者提供')
  })
  it('開盤時段（重點）排在最前面、名稱有★；預設統計是全期最大', async () => {
    const w = await mountPage()
    const t = w.text()
    expect(t.indexOf('[★ 開盤時段（重點）:')).toBeGreaterThan(-1)
    expect(t.indexOf('[★ 開盤時段（重點）:')).toBeLessThan(t.indexOf('[盤中:'))
    expect(w.findAll('button').find((b: any) => b.text() === '全期最大')!.classes()).toContain('on')
    expect(w.findAll('button').some((b: any) => b.text() === '前5尖峰日中位數')).toBe(true)
    expect(w.findAll('button').some((b: any) => b.text() === '大量交易日')).toBe(false)           // 日型只有交易日／非交易日
  })
  it('卡片可下鑽：點「覆蓋不足」只剩那一台', async () => {
    const w = await mountPage()
    await w.findAll('button').find((b: any) => b.text().startsWith('覆蓋不足'))!.trigger('click')
    await flushPromises()
    expect(route.query.ev).toBe('insufficient')
    const w2 = await mountPage()
    expect(w2.text()).toContain('H2 ')
    expect(w2.text()).not.toContain('H1 ')
  })
  it('切換統計與日型寫進網址', async () => {
    const w = await mountPage()
    await w.findAll('button').find((b: any) => b.text() === 'P95')!.trigger('click')
    expect(route.query.stat).toBe('p95')
    await w.findAll('button').find((b: any) => b.text() === '非交易日')!.trigger('click')
    expect(route.query.day).toBe('off')
  })
  it('沒有主機：空白狀態寫下一步與示範入口，不是空白也不是 0', async () => {
    hostsOverride = []
    const w = await mountPage()
    expect(w.text()).toContain('還沒有任何 NMON 歷史資料')
    expect(w.text()).toContain('下一步')
    await w.findAll('button').find((b: any) => b.text() === '看示範資料')!.trigger('click')
    expect(route.query.demo).toBe('1')
  })
  it('示範模式：標示示範、打 demo 端點、不打 overview', async () => {
    route.query = { demo: '1' }
    const w = await mountPage()
    expect(w.text()).toContain('示範資料（模擬，非真實主機）')
    expect(calls.some(c => c.url === '/api/v2/capacity/demo')).toBe(true)
    expect(calls.some(c => c.url === '/api/v2/capacity/overview')).toBe(false)
  })
  it('單機明細：列出該日型所有格、顯示限制數與連結', async () => {
    route.query = { tab: 'detail', host: 'TEST-A' }
    const w = await mountPage()
    expect(w.text()).toContain('有限制，只能參考')
    expect(w.text()).toContain('D1 ')
    expect(w.text()).toContain('這台的 2 項限制')
  })
  it('限制與缺口：攤平、只看某台', async () => {
    route.query = { tab: 'limits', host: 'TEST-A' }
    const w = await mountPage()
    expect(w.text()).toContain('L1 ')
    expect(w.text()).toContain('只看 TEST-A')
    expect(w.text()).not.toContain('TEST-B')
  })
  it('時段設定：管理員可儲存（送出整份設定）；非管理員輸入框停用且沒有儲存鈕', async () => {
    route.query = { tab: 'config' }
    let w = await mountPage()
    await w.find('textarea').setValue('2026-09-04 結算日')
    await w.findAll('button').find((b: any) => b.text() === '儲存設定')!.trigger('click')
    await flushPromises()
    const put = calls.find(c => c.opts?.method === 'PUT')!
    expect(put.url).toBe('/api/v2/capacity/config')
    expect(put.opts.body.heavy_days).toEqual([{ date: '2026-09-04', note: '結算日' }])
    expect(put.opts.body.slots.map((s: any) => s.label)).toEqual(['盤中', '開盤時段（重點）'])
    expect(put.opts.body.marker_types).toEqual(['大量交易日', '其他情境'])
    admin = false
    w = await mountPage()
    expect(w.findAll('button').some((b: any) => b.text() === '儲存設定')).toBe(false)
    expect(w.find('input.inp').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('只有管理員可以修改')
  })
  it('資料來源：列出各來源與是否能做時段分析', async () => {
    route.query = { tab: 'sources' }
    const w = await mountPage()
    expect(w.text()).toContain('S1 ')
    expect(w.text()).toContain('可（逐取樣點）')
  })
})

describe('交易日情境標記頁籤', () => {
  it('admin：有新增表單；來源只是預覽（由伺服器時間判定），表單裡沒有「來源」可選；送出的內容不含 source_kind', async () => {
    route.query = { tab: 'markers' }
    admin = true
    const w = await mountPage()
    expect(w.find('[data-marker-readonly]').exists()).toBe(false)
    expect(w.find('[data-kind-preview]').text()).toContain('不能選')
    expect(w.find('[data-kind-preview]').text()).toContain('事後補標')                    // 伺服器現在 2026-09-09 17:00，交易日期預設今天 → 13:30 後＝事後補標
    expect(w.text()).not.toContain('source_kind')
    const ta = w.findAll('textarea')
    await ta[0].setValue('預期央行消息，法人預估量能放大')
    await w.find('[data-marker-submit]').trigger('click')
    await flushPromises()
    const post = calls.find(c => c.opts?.method === 'POST')!
    expect(post.url).toBe('/api/v2/capacity/markers')
    expect(post.opts.body.basis).toBe('預期央行消息，法人預估量能放大')
    expect('source_kind' in post.opts.body).toBe(false)
  })
  it('預覽規則：未來日期＝事前預告、過去日期＝事後補標、今天依伺服器時間', () => {
    expect(cv.markerKindPreview('2026-09-10', '2026-09-09T17:00:00', 'post_fact')).toBe('pre_announce')
    expect(cv.markerKindPreview('2026-09-08', '2026-09-09T17:00:00', 'post_fact')).toBe('post_fact')
    expect(cv.markerKindPreview('2026-09-09', '2026-09-09T10:00:00', 'intraday')).toBe('intraday')
  })
  it('非 admin：只有管理員群組可以新增或修改的說明，沒有新增表單、沒有修訂／作廢按鈕，但清單與歷史看得到', async () => {
    MARKERS = [mk(1)]
    route.query = { tab: 'markers', mk: '1' }
    admin = false
    const w = await mountPage()
    expect(w.find('[data-marker-readonly]').text()).toContain('只有管理員群組可以新增或修改標記')
    expect(w.find('[data-marker-submit]').exists()).toBe(false)
    expect(w.text()).toContain('M1 ')
    expect(w.find('[data-marker-detail]').exists()).toBe(true)
    expect(w.find('[data-marker-void]').exists()).toBe(false)
    expect(w.findAll('button').some((b: any) => b.text() === '新增修訂版')).toBe(false)
  })
  it('清單與時間軸：項次 M、來源徽章、狀態；作廢鈕只有 admin 且要原因', async () => {
    MARKERS = [mk(2, { trade_date: '2026-09-11', source_kind: 'post_fact', kind_label: '事後補標' }), mk(1)]
    route.query = { tab: 'markers', mk: '2' }
    admin = true
    const w = await mountPage()
    expect(w.text()).toContain('M2 ')
    expect(w.find('[data-timeline]').text()).toContain('事前預告')
    expect(w.find('[data-timeline]').text()).toContain('事後補標')
    await w.find('[data-marker-void]').trigger('click')                                           // 沒寫原因 → 不送出
    await flushPromises()
    expect(calls.some(c => String(c.url).includes('/void'))).toBe(false)
  })
})
