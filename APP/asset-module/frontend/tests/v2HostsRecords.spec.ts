// 2.057.x（V61 第 22 批）：舊 /assets 資產查詢搬到 2.1 主機查詢——逐筆登記頁籤、掃到未登記、重複登記「不是重複」。
// 純函式（網址→篩選參數、舊網址轉址）＋實掛（排序、匯出全部、確認）。V2Table／InfoNote 等為簡化替身，全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { recordFilterQuery, toggleMulti } from '../utils/assetRecords'
import { assetFilterTarget, isPorted, v2LegacyTarget } from '../utils/v2LegacyMap'

describe('網址 → /api/assets 篩選參數（跟舊頁 loadAll 同一組）', () => {
  it('只帶有值的、scan_status／virtual 只收固定值、filter_value 跟著 filter_field（空值也算）', () => {
    expect(recordFilterQuery({ scan_status: 'bogus', virtual: 'maybe', os_type: 'Linux', q: '', page: '3' })).toEqual({ os_type: 'Linux' })
    expect(recordFilterQuery({ scan_status: 'ica_only', virtual: 'yes' })).toEqual({ scan_status: 'ica_only', virtual: 'yes' })
    expect(recordFilterQuery({ filter_field: 'device_model', filter_value: '' })).toEqual({ filter_field: 'device_model', filter_value: '' })
    expect(recordFilterQuery({ filter_value: 'x' })).toEqual({})
  })
  it('複選照固定順序、全取消＝undefined', () => {
    expect(toggleMulti('db', 'web', ['web', 'db'])).toBe('web,db')
    expect(toggleMulti('db', 'db', ['db'])).toBeUndefined()
  })
})

describe('舊 /assets 轉址', () => {
  it('帶篩選條件 → 逐筆登記頁籤，條件原樣照帶；只有 q 照舊到主機清單', () => {
    expect(v2LegacyTarget('/assets', { filter_field: 'device_model', filter_value: 'HP DL380' })).toEqual({ path: '/v2/hosts', query: { tab: 'records', filter_field: 'device_model', filter_value: 'HP DL380' } })
    expect(v2LegacyTarget('/assets', { os_type: 'Linux,Windows', role: 'db' })).toEqual({ path: '/v2/hosts', query: { tab: 'records', os_type: 'Linux,Windows', role: 'db' } })
    expect(v2LegacyTarget('/assets', { q: 'web01' })).toEqual({ path: '/v2/hosts', query: { q: 'web01' } })
    expect(assetFilterTarget('custodian', '王小明').query).toMatchObject({ tab: 'records', filter_field: 'custodian', filter_value: '王小明' })
  })
  it('/assets 已移植：legacy=1 也不開舊頁；單台頁 /assets/:serial 不受影響', () => {
    expect(isPorted('/assets')).toBe(true)
    expect(isPorted('/assets/ABC-1')).toBe(false)
  })
})

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let route: any
const replaced: any[] = []
const Table = defineComponent({ props: ['rows', 'cols', 'exportAll'], emits: ['sort'], setup(p, { slots, emit }) {
  return () => h('table', {}, [
    h('tr', {}, (p.cols ?? []).map((c: any) => h('th', { class: `h-${c.k}`, onClick: () => emit('sort', c.k) }, c.label))),
    ...(p.rows ?? []).map((r: any) => h('tr', {}, (p.cols ?? []).map((c: any) => h('td', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))),
  ])
} })
beforeEach(() => {
  calls = []; replaced.length = 0
  route = { query: {} as Record<string, any> }
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(true) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { replaced.push(x) }, push: (x: any) => { replaced.push(x) } }))
  const ROWS = [{ asset_serial: 'S-1', hostname: 'web01', ip: '10.99.1.1', asset_status: '使用中', device_model: 'DL380' }]
  const fetcher = async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/assets/field-groups') return { hardware: { common: ['hostname', 'ip', 'device_model'], advanced: ['owner'] } }
    if (url === '/api/field-meta') return { categories: [{ key: 'use', label: '使用' }], fields: { owner: { label: '擁有者', category: 'use', layer: 'adv' } } }
    if (url === '/api/assets/facets') return { platform: {}, role: { db: 2 }, total: 1, os_type: { Linux: 1 }, os_order: ['Linux'] }
    if (url === '/api/assets') return ROWS
    if (url === '/api/v2/hosts/duplicates') return { groups: [{ no: 1, key: 'k1', hostname: 'web01', ip: '10.99.1.1', kind: 'suspect', kind_label: '疑似真重複', count: 2, extra_rows: 1, serials: 'S-1、S-2', diff_fields: [], systems: '' }], members: [], by_kind: { suspect: 1 }, kind_labels: { suspect: '疑似真重複' }, kind_order: ['suspect'], extra_rows: 1, dismissed_groups: 0 }
    return {}
  }
  vi.stubGlobal('useApi', () => ({ apiFetch: fetcher, apiFetchWithHeaders: async (url: string, opts: any = {}) => ({ data: await fetcher(url, opts), headers: new Headers({ 'X-Total-Count': '1234' }) }) }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, DataCell: { props: ['value'], template: '<span>{{ value }}</span>' },
  RowActions: { template: '<span />' }, OfflineMarkModal: { template: '<div />' } }

describe('逐筆登記頁籤（AssetRecords）', () => {
  it('同一支 /api/assets：網址條件＋分頁送後端；總筆數看 X-Total-Count', async () => {
    route.query = { tab: 'records', os_type: 'Linux', filter_field: 'device_model', filter_value: 'DL380', rpage: '2' }
    const C = (await import('../components/v2/AssetRecords.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    const q = calls.find(c => c.url === '/api/assets')!.opts.query
    expect(q).toEqual({ os_type: 'Linux', filter_field: 'device_model', filter_value: 'DL380', sort_by: 'hostname', order: 'asc', limit: 50, offset: 50 })
    expect(w.text()).toContain('1,234')
    expect(w.text()).toContain('設備機型：DL380')
  })
  it('匯出＝同樣條件、不帶 limit（全部筆數）', async () => {
    route.query = { role: 'db' }
    const C = (await import('../components/v2/AssetRecords.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    const all = await (w.findComponent(Table).props('exportAll') as any)()
    const q = calls.filter(c => c.url === '/api/assets').at(-1)!.opts.query
    expect(q).toEqual({ role: 'db', sort_by: 'hostname', order: 'asc' })
    expect(all[0].__no).toBe(1)
  })
  it('排序：資料表欄位才送；系統推測、操作點了不動（不送會被後端拒絕的 sort_by）', async () => {
    const C = (await import('../components/v2/AssetRecords.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    await w.find('th.h-guess').trigger('click')
    await w.find('th.h-ops').trigger('click')
    expect(replaced.length).toBe(0)
    await w.find('th.h-device_model').trigger('click')
    expect(replaced.at(-1).query).toMatchObject({ rsort: 'device_model' })
  })
  it('進階欄位切一類：表頭換成主機名稱＋資產編號＋那一類', async () => {
    route.query = { rcols: 'use' }
    const C = (await import('../components/v2/AssetRecords.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    expect(w.findAll('th').map(x => x.text())).toEqual(['主機名稱', '資產編號', '擁有者'])
  })
})

describe('重複登記「不是重複」（2.057.0）', () => {
  it('取消確認不送；確定才送同一支 dismiss、body { hostname, ip }', async () => {
    const C = (await import('../components/v2/HostDuplicates.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    vi.stubGlobal('confirm', () => false)
    await w.find('td.c-dismiss button').trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/assets/duplicates/dismiss')).toBe(false)
    vi.stubGlobal('confirm', () => true)
    await w.find('td.c-dismiss button').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/assets/duplicates/dismiss')!.opts).toEqual({ method: 'POST', body: { hostname: 'web01', ip: '10.99.1.1' } })
  })
})
