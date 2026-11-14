// 容量分析「封存與保存」頁籤：清單有項次 F、階段／被標記引用的卡片可下鑽、點日期看格子明細（缺資料不是 0）、
// 非 admin 只能看（沒有清理鈕）、admin 清理一定先預覽且送出預覽的筆數與截止日、被擋就沒有清理鈕。API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as cv from '../composables/capacityView'
import Page from '../pages/v2/capacity.vue'

const route = reactive<{ params: Record<string, string>; query: Record<string, string>; path: string }>({ params: {}, query: {}, path: '/v2/capacity' })
const calls: { url: string; opts: any }[] = []
const toasts: { m: string; kind?: string }[] = []
let admin = true
let archiveFail = false

const OVERVIEW = () => ({
  demo: false, other_key: '_other', other_label: '其他時間', static_limits: [], metrics: [{ key: 'cpu_busy', label: 'CPU 忙碌', unit: '%' }],
  day_types: [{ key: 'normal', label: '交易日' }, { key: 'off', label: '非交易日' }],
  config: { slots: [{ key: 'open', label: '開盤時段（重點）', start: '09:00', end: '09:30', priority: true }], slots_source: '管理員設定', unprovided: [], marker_types: [], marker_reasons: [],
    heavy_days: [], holidays: [], params: { min_coverage_pct: 80 }, warnings: [] },
  sources: [], hosts: [],
})
const row = (id: number, o: any = {}) => ({ id, host: 'TEST-A', day: '2026-09-10', version: 1, stage: 'final', stage_label: '完成（T+1）', algo_version: 'capacity-day-summary/1', sources: ['nmon'],
  granularity: { interval_s: 60, kinds: ['raw'] }, identity: { mapped: false, asset_serial: null }, content_hash: 'abcdef0123456789', reason: 'T+1 完成', created_at: '2026-09-11T16:00:00', created_by: 'system:capacity_archive',
  protected: false, is_latest: true, cells_n: 6, ok_cells: 1, no_data_cells: 4, low_coverage_cells: 1, evidence: 'limited', ...o })
const SETTINGS = { retention_months: 24, retention_broken: false, default_months: 24, cutoff: '2024-09-14', rows: 3, host_days: 3, oldest: '2024-09-11', newest: '2026-09-10', audit: [{ at: '2026-09-11T16:00:00', actor: 'system:capacity_archive', action: 'auto_run', detail: { written: 3 } }] }
let ROWS: any[] = []
let PREVIEW: any = { cutoff: '2024-09-14', rows: 2, host_days: 2, protected_rows: 1, blocked: null }

beforeEach(() => {
  route.query = { tab: 'archive' }; calls.length = 0; toasts.length = 0; admin = true; archiveFail = false
  ROWS = [row(3, { day: '2026-09-10', stage: 'revision', stage_label: '修訂版', version: 3 }), row(2, { day: '2024-09-12', protected: true, stage: 'final' }), row(1, { day: '2024-09-11', stage: 'provisional', stage_label: '暫定', is_latest: false })]
  for (const [k, v] of Object.entries({ ...cv, ref, reactive, computed, watch, onMounted, onBeforeUnmount })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(!admin), isAdmin: ref(admin) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string, kind?: string) => toasts.push({ m, kind }) }))
  vi.stubGlobal('window', Object.assign(globalThis.window ?? {}, { confirm: () => true }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => {
    calls.push({ url: u, opts: o })
    if (u === '/api/v2/capacity/markers') return Promise.resolve({ markers: [], options: { types: [], reasons: [], levels: [], default_ranges: [], kinds: {}, server_now: '2026-09-12T10:00:00', today_kind: 'post_fact' } })
    if (u === '/api/v2/capacity/archive') return archiveFail ? Promise.reject({ data: { detail: '資料庫忙碌' } }) : Promise.resolve({ settings: SETTINGS, total: ROWS.length, rows: ROWS })
    if (/\/archive\/\d+$/.test(u)) {
      const id = Number(u.split('/').pop())
      return Promise.resolve({ ...ROWS.find(r => r.id === id), protected_reasons: ['標記 #1 的 archive_ref'], summary: { cells: [
        { slot: 'open', slot_label: '開盤時段（重點）', metric: 'cpu_busy', source: 'nmon', samples: 30, expected: 30, coverage_pct: 100, missing_pct: 0, status: 'ok', avg: 50, p95: 50, max: 50, max_at: '2026-09-10 09:10:00', bucket: false },
        { slot: 'mid', slot_label: '盤中', metric: 'cpu_busy', source: 'nmon', samples: 0, expected: 270, coverage_pct: 0, missing_pct: 100, status: 'no_data', avg: null, p95: null, max: null, max_at: null, bucket: false }] } })
    }
    return Promise.resolve(OVERVIEW())
  } }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: (u: string, o: any) => {
    calls.push({ url: u, opts: o })
    if (u.endsWith('/purge/preview')) return Promise.resolve(PREVIEW)
    if (u.endsWith('/purge')) return Promise.resolve({ deleted_rows: 2 })
    if (u.endsWith('/run')) return Promise.resolve({ results: [{ result: 'written' }] })
    return Promise.resolve({})
  } }))
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

describe('封存與保存頁籤', () => {
  it('清單：項次 F、階段、標記引用、保存月數與截止日；說明收在 InfoNote 之外只有一行摘要', async () => {
    const w = await mountPage()
    const t = w.text()
    expect(t).toContain('F1 ')
    expect(t).toContain('F3 ')
    expect(t).toContain('[階段:修訂版]')
    expect(t).toContain('[標記引用:被標記引用，不可清理]')
    expect(w.find('[data-archive-intro]').text()).toContain('保存 24 個月（2024-09-14 以前的日摘要過期）')
    expect(t).toContain('Z1 ')                                                    // 稽核清單也有項次
  })
  it('卡片可下鑽：點「被標記引用」只剩那一筆；點階段只剩該階段；項次不隨篩選改變', async () => {
    let w = await mountPage()
    await w.findAll('button').find((b: any) => b.text().startsWith('被標記引用'))!.trigger('click')
    await flushPromises()
    expect(route.query.apr).toBe('1')
    w = await mountPage()
    expect(w.text()).toContain('F2 ')
    expect(w.text()).not.toContain('F1 ')
    expect(w.text()).not.toContain('F3 ')
    route.query = { tab: 'archive', ast: 'provisional' }
    w = await mountPage()
    expect(w.text()).toContain('F3 ')
    expect(w.text()).not.toContain('F2 ')
    route.query = { tab: 'archive', alt: '1' }
    w = await mountPage()
    expect(w.text()).not.toContain('F3 ')                                         // 只看最新版：第 1 版（is_latest=false）不顯示
  })
  it('明細：缺資料的格子顯示「無資料」與 —，不是 0；顯示標記引用原因', async () => {
    route.query = { tab: 'archive', ar: '2' }
    const w = await mountPage()
    const d = w.find('[data-archive-detail]').text()
    expect(d).toContain('被標記引用，不可清理')
    expect(d).toContain('標記 #1 的 archive_ref')
    expect(d).toContain('[狀態:無資料]')
    expect(d).toContain('[平均:—]')
    expect(d).toContain('[缺值率 %:100]')
    expect(d).not.toContain('[平均:0]')
  })
  it('非 admin：只有說明，沒有補封存／清理按鈕，但看得到清單與稽核', async () => {
    admin = false
    const w = await mountPage()
    expect(w.find('[data-archive-readonly]').text()).toContain('只有管理員群組')
    expect(w.find('[data-archive-preview]').exists()).toBe(false)
    expect(w.find('[data-archive-run]').exists()).toBe(false)
    expect(w.text()).toContain('F1 ')
  })
  it('admin 清理：一定先預覽；送出預覽的筆數與截止日與 confirm；預覽前沒有清理鈕', async () => {
    const w = await mountPage()
    expect(w.find('[data-archive-purge]').exists()).toBe(false)
    await w.find('[data-archive-preview]').trigger('click')
    await flushPromises()
    expect(w.find('[data-archive-pv]').text()).toContain('可清理 2 筆')
    expect(w.find('[data-archive-pv]').text()).toContain('保留 1 筆')
    await w.find('[data-archive-purge]').trigger('click')
    await flushPromises()
    const post = calls.find(c => c.url === '/api/v2/capacity/archive/purge')!
    expect(post.opts.body).toEqual({ expected_rows: 2, cutoff: '2024-09-14', confirm: true })
    expect(toasts.some(t => t.m.includes('已清理 2 筆'))).toBe(true)
  })
  it('預覽被擋（設定損壞或看不懂的引用）：顯示原因、沒有清理鈕', async () => {
    PREVIEW = { cutoff: '2024-09-14', rows: 2, host_days: 2, protected_rows: 0, blocked: '有 1 筆標記的 archive_ref 看不懂' }
    const w = await mountPage()
    await w.find('[data-archive-preview]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('看不懂')
    expect(w.find('[data-archive-purge]').exists()).toBe(false)
    PREVIEW = { cutoff: '2024-09-14', rows: 2, host_days: 2, protected_rows: 1, blocked: null }
  })
  it('載入失敗：顯示錯誤，不是空清單', async () => {
    archiveFail = true
    const w = await mountPage()
    expect(w.text()).toContain('封存資料載入失敗：資料庫忙碌')
    expect(w.text()).not.toContain('還沒有任何日摘要')
  })
  it('儲存保存月數與補封存送出正確內容', async () => {
    const w = await mountPage()
    const inputs = w.findAll('input')
    const months = inputs.find((i: any) => i.attributes('type') === 'number')!
    await months.setValue(36)
    await w.find('[data-archive-save]').trigger('click')
    await flushPromises()
    expect(calls.find(c => c.url === '/api/v2/capacity/archive/settings')!.opts).toMatchObject({ method: 'PUT', body: { retention_months: 36 } })
    const day = inputs.find((i: any) => i.attributes('type') === 'date')!
    await day.setValue('2026-09-10')
    await w.find('[data-archive-run]').trigger('click')
    await flushPromises()
    expect(calls.find(c => c.url === '/api/v2/capacity/archive/run')!.opts.body).toMatchObject({ day: '2026-09-10' })
  })
})
