// EOS 批三＋批二補強（2.041.80）：覆蓋率下鑽改伺服器端分頁／排序／匯出全部、待核磚、合約比對子頁實掛。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身（把 server／exportAll／事件露出來讓測試驅動）；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CONTRACT_TYPE_LABEL, COV_SORT_KEY, covDrillQuery } from '../utils/eosEvidence'
import * as ev from '../utils/eosEvidence'
import Comp from '../components/v2/EosEvidence.vue'

const route = reactive<{ params: Record<string, string>; query: Record<string, string> }>({ params: {}, query: {} })
const calls: { url: string; query: any }[] = []
let tableProps: any = null
let tableEmit: any = null
const COV = {
  machines: 6, needs_review: 2, snapshot: { id: 'abc', computed_at: '2026-11-02 10:00:00' }, unmatched: [],
  tracks: {
    os: { label: '作業系統', total: 6, confirmed_rate: 40, buckets: { confirmed: 2, presumed: 1, unknown: 1, unmatched: 1, no_value: 1, not_applicable: 0 } },
    hw: { label: '硬體型號', total: 6, confirmed_rate: 100, buckets: { confirmed: 1, presumed: 0, unknown: 0, unmatched: 0, no_value: 5, not_applicable: 0 } },
  },
}
const covRow = (i: number) => ({ no: i, hostname: `h${i}.example.test`, ip: `192.0.2.${i}`, environment: '正式', raw_value: 'Rocky Linux 9', canonical: 'Rocky Linux 9', matched_name: 'Rocky Linux 9', trust: 'confirmed', bucket: 'confirmed', needs_review: i === 1, conflict_values: i === 1 ? 'A；B' : '' })
const CTR = {
  has_import: true, import: { id: 1, filename: 'c.xlsx', imported_at: '2026-11-01' }, contract_rows: 6, compared: 5, effect_note: '影子比對只做比較與記錄：不會改任何 EOS 日期、可信度或風險，也不會寄任何通知。',
  hints: {}, flag: 'all',
  tiles: [{ key: 'all', label: '全部差異', count: 2 }, { key: 'unlinked', label: '合約列未對到資產', count: 1 }, { key: 'model_mismatch', label: '合約型號與資產型號不一致', count: 1 },
    { key: 'contract_after_eos', label: '合約到期日晚於官方 EOS 日期', count: 0 }, { key: 'eos_only_in_contract', label: '合約型號有 EOS 日期、資產型號查不到', count: 0 }],
  rows: [{ no: 1, type: 'unlinked', type_label: '合約列未對到資產', row_no: 5, contract_no: 'K5', vendor: 'V', contract_model: 'X', contract_end: '2027-01-01', detail: '比對狀態：未對到' },
    { no: 2, type: 'model_mismatch', type_label: '合約型號與資產型號不一致', row_no: 4, contract_no: 'K4', vendor: 'V', contract_model: 'X', contract_end: '2026-01-01', hostname: 'a', detail: 'x ≠ y', eos_name: 'N', eos_date: '2027-01-01' }],
}
let ctrOverride: any = null

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, query: o?.query })
  if (u === '/api/eos/coverage') return Promise.resolve(COV)
  if (u === '/api/eos/coverage/rows') {
    const size = Number(o.query.size)
    const all = Array.from({ length: 250 }, (_, i) => covRow(i + 1))
    const rows = size === 0 ? all : all.slice((Number(o.query.page) - 1) * size, Number(o.query.page) * size)
    return Promise.resolve({ rows, total: 250, page: Number(o.query.page), size, snapshot: COV.snapshot })
  }
  if (u === '/api/eos/contract-shadow') return Promise.resolve(ctrOverride ?? CTR)
  return Promise.resolve({ rows: [], tiles: [] })
}

beforeEach(() => {
  route.query = {}
  calls.length = 0
  tableProps = null
  tableEmit = null
  ctrOverride = null
  for (const [k, v] of Object.entries({ ...ev, ref, reactive, computed, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = o.query } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty', 'server', 'exportAll', 'unit'], emits: ['sort', 'page'],
  setup(p, { emit, slots }) {
    return () => {
      if (p.server) { tableProps = p; tableEmit = emit }
      return h('div', { class: p.server ? 'tbl srv' : 'tbl' }, p.rows.length ? p.rows.map((r: any) => h('div', { class: 'row' }, [`${p.prefix}${r.__no}`, slots['cell-open']?.({ row: r })])) : [h('p', { class: 'tbl-empty' }, p.empty)])
    }
  },
})
const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
async function mountIt() {
  const w = mount(Comp, { global: { components: { V2Table, InfoNote: passthrough } } })
  await flushPromises()
  return w
}

describe('覆蓋率下鑽：伺服器端分頁／排序／匯出全部', () => {
  it('covDrillQuery：只送有值的條件；排序鍵走白名單；size=0 全部', () => {
    expect(covDrillQuery({ track: 'os', bucket: 'unmatched' }, 2, 100, '__no', 'asc')).toEqual({ page: '2', size: '100', track: 'os', bucket: 'unmatched' })
    expect(covDrillQuery({ track: 'os', bucket: 'confirmed', item: '' }, 1, 0, 'trust_label', 'desc')).toEqual({ page: '1', size: '0', track: 'os', bucket: 'confirmed', item: '', sort: 'trust', dir: 'desc' })
    expect(covDrillQuery({ track: '', bucket: '', review: true }, 1, 50, 'evil', 'asc')).toEqual({ page: '1', size: '50', review: 'true' })
    expect(COV_SORT_KEY.bucket_label).toBe('bucket')
  })

  it('點格子 → 帶 page／size 取第一頁、server.total 是全部筆數；翻頁、排序都重新向後端取', async () => {
    const w = await mountIt()
    await w.findAll('button.card').find(b => b.text().includes('已確認'))!.trigger('click')
    await flushPromises()
    const first = calls.filter(c => c.url === '/api/eos/coverage/rows').at(-1)!
    expect(first.query).toMatchObject({ track: 'os', bucket: 'confirmed', page: '1', size: '100' })
    expect(tableProps.server).toMatchObject({ total: 250, page: 1, size: 100, sortKey: '__no', sortDir: 'asc' })
    expect(tableProps.rows).toHaveLength(100)
    tableEmit('page', 3, 100)
    await flushPromises()
    expect(tableProps.rows).toHaveLength(50)
    expect(calls.filter(c => c.url === '/api/eos/coverage/rows').at(-1)!.query).toMatchObject({ page: '3', size: '100' })
    tableEmit('sort', 'hostname')
    await flushPromises()
    expect(calls.filter(c => c.url === '/api/eos/coverage/rows').at(-1)!.query).toMatchObject({ sort: 'hostname', dir: 'asc', page: '1' })
    tableEmit('sort', 'hostname')
    await flushPromises()
    expect(calls.filter(c => c.url === '/api/eos/coverage/rows').at(-1)!.query).toMatchObject({ sort: 'hostname', dir: 'desc' })
  })

  it('匯出全部：exportAll 用 size=0 取同條件全部列，項次沿用伺服器編號', async () => {
    const w = await mountIt()
    await w.findAll('button.card').find(b => b.text().includes('已確認'))!.trigger('click')
    await flushPromises()
    tableEmit('sort', 'ip')
    await flushPromises()
    const all = await tableProps.exportAll()
    const last = calls.filter(c => c.url === '/api/eos/coverage/rows').at(-1)!
    expect(last.query).toMatchObject({ size: '0', page: '1', sort: 'ip', dir: 'asc', track: 'os', bucket: 'confirmed' })
    expect(all).toHaveLength(250)
    expect(all[0].__no).toBe(1)
    expect(all[0].bucket_label).toBe('已確認')
  })

  it('待核磚：顯示待核台數，點了以 review 下鑽；明細有「待核」欄', async () => {
    const w = await mountIt()
    const card = w.findAll('button.card').find(b => b.text().includes('待核'))!
    expect(card.text()).toContain('2')
    await card.trigger('click')
    await flushPromises()
    expect(calls.filter(c => c.url === '/api/eos/coverage/rows').at(-1)!.query).toMatchObject({ review: 'true' })
    expect(tableProps.cols.some((c: any) => c.k === 'review_text')).toBe(true)
    expect(tableProps.rows[0].review_text).toBe('待核：A；B')
    expect(tableProps.rows[1].review_text).toBe('')
  })

  it('顯示快照時間', async () => {
    const w = await mountIt()
    expect(w.text()).toContain('2026-11-02 10:00:00')
  })
})

describe('合約比對子頁', () => {
  it('有「合約比對」子頁；點開才讀 API；磚與列同一份；點磚篩選', async () => {
    route.query = { es: 'ctr' }
    const w = await mountIt()
    expect(w.findAll('button.pill').map(b => b.text())).toContain('合約比對')
    expect(calls.some(c => c.url === '/api/eos/contract-shadow')).toBe(true)
    expect(w.text()).toContain('c.xlsx')
    expect(w.text()).toContain('比對 5 組')
    expect(w.findAll('.row')).toHaveLength(2)
    expect(w.text()).toContain('不會改任何 EOS 日期、可信度或風險')
    await w.findAll('button.card').find(b => b.text().includes('合約列未對到資產'))!.trigger('click')
    expect(w.findAll('.row')).toHaveLength(1)
  })

  it('沒有匯入合約：顯示下一步，不報錯', async () => {
    ctrOverride = { has_import: false, rows: [], tiles: [] }
    route.query = { es: 'ctr' }
    const w = await mountIt()
    expect(w.text()).toContain('尚無合約資料')
    expect(w.text()).toContain('資料匯入')
  })

  it('用詞：子頁與類型名稱不出現「核准」「不知道」「搞定」', async () => {
    route.query = { es: 'ctr' }
    const w = await mountIt()
    for (const bad of ['核准', '不知道', '搞定']) {
      expect(w.text()).not.toContain(bad)
      for (const v of Object.values(CONTRACT_TYPE_LABEL)) expect(v).not.toContain(bad)
    }
  })

  it('類型名稱與後端四類一致', () => {
    expect(Object.keys(CONTRACT_TYPE_LABEL)).toEqual(['unlinked', 'model_mismatch', 'contract_after_eos', 'eos_only_in_contract'])
  })
})
