/**
 * 2.048.4 V31／V37／V39：9.2 存在可信度、納管狀態明細、數字健檢改 V2 原生，實掛：
 *   - 存在可信度：清單筆數＝後端回的那一桶；換桶時舊請求晚回來不能蓋掉新的（useLatest）
 *   - 納管狀態明細：已退役組成卡片 ?rp= 篩表、項次不重編；換狀態舊請求晚到不覆蓋；失敗不沿用上一份
 *   - 數字健檢：?nk= 列那個數字的原因
 * Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身；API 全假。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import Existence from '../components/v2/Existence.vue'
import StateDetail from '../components/v2/StateDetail.vue'
import NumberCheck from '../components/v2/NumberCheck.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const route = reactive<{ query: Record<string, string> }>({ query: {} })
const auth = { isAdmin: false, canWrite: true }
beforeEach(() => {
  pending = []
  route.query = {}
  auth.isAdmin = false; auth.canWrite = true
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(auth.canWrite), isViewer: ref(!auth.canWrite) }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: async () => {}, busy: ref(false) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row', 'data-no': r.__no },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: pass, SerialLinks: pass } } }
const qOf = (p: any) => p.opts?.query ?? {}

const SUM = { summary: { counted: 2, cia_registered: 1, cia_pending: 1, pending_confirm: 0, suspect_offline: 0, retired: 0, onboarded: 1, onboarded_stale: 0,
  by_level: {}, level_score: {}, level_label: {}, count_threshold: 70, cia_complete_pct: 50 }, as_of: 'x' }
const mach = (key: string) => ({ key, asset_serial: key, hostname: key, ip: null, os: null, has_cia: true, level: 'onboarded', level_label: '', score: 100, evidence: ['onboarded'], last_seen_any: null, pending_confirm: false })

describe('存在可信度（V31）', () => {
  it('清單＝後端那一桶；換桶時舊請求晚回來不覆蓋', async () => {
    const w = mount(Existence, opts)
    await flushPromises()
    for (const p of pending.splice(0)) p.resolve(p.url.endsWith('/summary') ? SUM : { bucket: 'counted', bucket_label: '計入台數', level: null, total: 2, items: [mach('A'), mach('B')] })
    await flushPromises()
    expect(w.find('[data-prefix="X"]').findAll('.row').length).toBe(2)
    route.query = { tab: 'existence', bucket: 'cia_pending' }
    await flushPromises()
    const older = pending.splice(0)
    route.query = { tab: 'existence', bucket: 'onboarded' }
    await flushPromises()
    for (const p of pending.splice(0)) p.resolve({ bucket: 'onboarded', bucket_label: '已納管', level: null, total: 1, items: [mach('N')] })
    await flushPromises()
    for (const p of older) p.resolve({ bucket: 'cia_pending', bucket_label: '待補登', level: null, total: 1, items: [mach('OLD')] })
    await flushPromises()
    expect(w.text()).toContain('已納管')
    expect(w.text()).not.toContain('OLD')
  })
})

const RET = { state: '已退役', total: 3, rows: 4, retired_values: ['報廢'], also_active: 1, multi_row: 1, retired_alive: 0, merged_shells: 0, pending_cia: 0,
  matrix: [{ status: '報廢', source: 'CIA', machines: 2, rows: 3 }, { status: '下線', source: 'CIA', machines: 1, rows: 1 }],
  items: [
    { asset_serial: 'S3', hostname: 'c', ip: null, state: '已退役', statuses: '下線', source: 'CIA', row_count: 1 },
    { asset_serial: 'S1', hostname: 'a', ip: null, state: '已退役', statuses: '報廢', source: 'CIA', row_count: 2, also_active: true },
    { asset_serial: 'S2', hostname: 'b', ip: null, state: '已退役', statuses: '報廢', source: 'CIA', row_count: 1 },
    { asset_serial: 'S4', hostname: 'd', ip: null, state: '已退役', statuses: '報廢', source: '帳外', row_count: 1 },
  ] }

describe('納管狀態明細（V37）', () => {
  it('已退役組成：卡片台數＝篩出的列數，項次照主機名、篩選後不重編', async () => {
    route.query = { tab: 'state', state: '已退役' }
    const w = mount(StateDetail, opts)
    await flushPromises()
    expect(qOf(pending[0]).state).toBe('已退役:breakdown')
    pending.splice(0)[0].resolve(RET)
    await flushPromises()
    expect(w.findAll('[data-prefix="S"] .row').map(r => r.attributes('data-no'))).toEqual(['1', '2', '3', '4'])
    route.query = { tab: 'state', state: '已退役', rp: '報廢|CIA' }
    await flushPromises()
    const rows = w.findAll('[data-prefix="S"] .row')
    expect(rows.length).toBe(RET.matrix[0].machines)
    expect(rows.map(r => r.attributes('data-no'))).toEqual(['1', '2'])
    route.query = { tab: 'state', state: '已退役', rp: 'also' }
    await flushPromises()
    expect(w.findAll('[data-prefix="S"] .row').length).toBe(RET.also_active)
  })

  it('換狀態：舊請求晚到不覆蓋；失敗不沿用上一份清單', async () => {
    route.query = { tab: 'state', state: '已納管' }
    const w = mount(StateDetail, opts)
    await flushPromises()
    const older = pending.splice(0)
    route.query = { tab: 'state', state: '已收集' }
    await flushPromises()
    pending.splice(0)[0].resolve({ state: '已收集', total: 1, items: [{ asset_serial: 'NEW', hostname: 'new', ip: null, state: '已收集' }] })
    await flushPromises()
    older[0].resolve({ state: '已納管', total: 1, items: [{ asset_serial: 'OLD', hostname: 'old', ip: null, state: '已納管' }] })
    await flushPromises()
    expect(w.text()).toContain('new')
    expect(w.text()).not.toContain('old')
    route.query = { tab: 'state', state: '未涵蓋' }
    await flushPromises()
    pending.splice(0)[0].reject(new Error('boom'))
    await flushPromises()
    expect(w.text()).toContain('載入失敗')
    expect(w.find('[data-prefix="S"]').exists()).toBe(false)
  })
})

describe('數字健檢（V39）', () => {
  it('?nk= 列那個數字的每一類原因', async () => {
    route.query = { tab: 'numcheck', nk: 'managed' }
    const w = mount(NumberCheck, opts)
    await flushPromises()
    pending.splice(0)[0].resolve({ evidence: {}, numbers: [
      { key: 'managed', label: '已納管', how: '', source: '', limits: '', screen: 5, recalc: 4, diff: 1, mismatch: 1, unexplained: 1,
        explanations: [{ why: '畫面多一台', n: 1, unexplained: true, sample: [{ hostname: 'h1', ip: '10.99.0.1' }] }], zero: { checked: false, why: '', text: null } },
      { key: 'collected', label: '已收集', how: '', source: '', limits: '', screen: 3, recalc: 3, diff: 0, mismatch: 0, unexplained: 0, explanations: [], zero: { checked: true, why: '', text: null } },
    ] })
    await flushPromises()
    expect(w.findAll('[data-prefix="W"] .row').length).toBe(1)
    expect(w.text()).toContain('h1 10.99.0.1')
    route.query = { tab: 'numcheck' }
    await flushPromises()
    expect(w.findAll('[data-prefix="N"] .row').length).toBe(2)
  })
})

describe('權限與參數（審查 P2-6）', () => {
  const sumAndList = async () => {
    await flushPromises()
    for (const p of pending.splice(0)) p.resolve(p.url.endsWith('/summary') ? SUM : { bucket: 'all', bucket_label: '全部', level: 'scan', total: 0, items: [] })
    await flushPromises()
  }
  it('CIA 補登清單：非管理員看不到下載、看到限管理員；管理員看得到', async () => {
    const w = mount(Existence, opts)
    await sumAndList()
    expect(w.find('a[href$="cia-pending/export"]').exists()).toBe(false)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    auth.isAdmin = true
    const w2 = mount(Existence, opts)
    await sumAndList()
    expect(w2.find('a[href$="cia-pending/export"]').exists()).toBe(true)
  })
  it('依證據等級：level 有送到後端', async () => {
    route.query = { tab: 'existence', bucket: 'all', level: 'scan' }
    mount(Existence, opts)
    await flushPromises()
    const lst = pending.find(p => p.url === '/api/existence/machines')!
    expect(qOf(lst)).toEqual({ bucket: 'all', level: 'scan' })
  })
  it('完整差異清單：只能看的帳號沒有按鈕', async () => {
    auth.canWrite = false
    const w = mount(NumberCheck, opts)
    await flushPromises()
    pending.splice(0)[0].resolve({ evidence: {}, numbers: [] })
    await flushPromises()
    expect(w.text()).not.toContain('匯出完整差異清單')
    expect(w.find('[data-test="user-only"]').exists()).toBe(true)
  })
})
