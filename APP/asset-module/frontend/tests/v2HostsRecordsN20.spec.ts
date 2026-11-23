// N20（V61）：逐筆登記頁籤補舊 /assets 的人員、軟體、任意欄位條件——寫進網址（可分享）、送同一支 /api/assets、匯出同條件全部。
// V2Table／InfoNote 為簡化替身，全假資料（10.99.x）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { recordFilterQuery } from '../utils/assetRecords'
import { v2LegacyTarget } from '../utils/v2LegacyMap'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let route: any
const pushed: any[] = []
const Table = defineComponent({ props: ['rows', 'cols', 'exportAll', 'exportCols'], setup(p) { return () => h('table', {}, (p.rows ?? []).map((r: any) => h('tr', {}, r.asset_serial))) } })
beforeEach(() => {
  calls = []; pushed.length = 0
  route = { query: {} as Record<string, any> }
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(true) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { pushed.push(x) }, push: (x: any) => { pushed.push(x) } }))
  const fetcher = async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/assets/field-groups') return { hardware: { common: ['hostname', 'ip', 'device_model'], advanced: ['custodian'] } }
    if (url === '/api/field-meta') return { categories: [], fields: { custodian: { label: '保管者', category: 'use', layer: 'adv' } } }
    if (url === '/api/assets/facets') return { platform: {}, role: {}, total: 1 }
    if (url === '/api/assets') return [{ asset_serial: 'S-1', hostname: 'web01', ip: '10.99.1.1' }]
    return {}
  }
  vi.stubGlobal('useApi', () => ({ apiFetch: fetcher, apiFetchWithHeaders: async (url: string, opts: any = {}) => ({ data: await fetcher(url, opts), headers: new Headers({ 'X-Total-Count': '1' }) }) }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, DataCell: { template: '<span />' },
  RowActions: { template: '<span />' }, OfflineMarkModal: { template: '<div />' } }

describe('網址參數', () => {
  it('person／software 照帶給 /api/assets；舊網址帶這兩個也轉逐筆登記', () => {
    expect(recordFilterQuery({ person: '王小明', software: 'Oracle', page: '2' })).toEqual({ person: '王小明', software: 'Oracle' })
    expect(v2LegacyTarget('/assets', { person: '王小明' })).toEqual({ path: '/v2/hosts', query: { tab: 'records', person: '王小明' } })
  })
})

describe('條件列', () => {
  it('網址條件送後端、匯出同條件不帶 limit、已選標籤看得到', async () => {
    route.query = { tab: 'records', person: '王', software: 'Oracle', filter_field: 'custodian', filter_value: '系統部' }
    const C = (await import('../components/v2/AssetRecords.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    const q = calls.find(c => c.url === '/api/assets')!.opts.query
    expect(q).toMatchObject({ person: '王', software: 'Oracle', filter_field: 'custodian', filter_value: '系統部', limit: 50 })
    expect(w.text()).toContain('人員：王')
    expect(w.text()).toContain('軟體：Oracle')
    expect(w.text()).toContain('保管者：系統部')
    await (w.findComponent(Table).props('exportAll') as any)()
    const xq = calls.filter(c => c.url === '/api/assets').at(-1)!.opts.query
    expect(xq).toEqual({ person: '王', software: 'Oracle', filter_field: 'custodian', filter_value: '系統部', sort_by: 'hostname', order: 'asc' })
  })
  it('套用：人員、軟體、任意欄位（含全部登記欄位可選）寫進網址；值留空＝看空值', async () => {
    route.query = { tab: 'records' }
    const C = (await import('../components/v2/AssetRecords.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    const opts = w.findAll('select[aria-label="欄位"] option').map(o => o.attributes('value'))
    expect(opts).toEqual(['', 'asset_serial', 'hostname', 'ip', 'device_model', 'custodian'])
    await w.find('input[aria-label="人員"]').setValue(' 王小明 ')
    await w.find('input[aria-label="軟體"]').setValue('Oracle')
    await w.find('select[aria-label="欄位"]').setValue('custodian')
    await w.find('form').trigger('submit')
    expect(pushed.at(-1).query).toMatchObject({ tab: 'records', person: '王小明', software: 'Oracle', filter_field: 'custodian', filter_value: '' })
    await w.find('select[aria-label="欄位"]').setValue('')
    await w.find('input[aria-label="人員"]').setValue('')
    await w.find('form').trigger('submit')
    const last = pushed.at(-1).query
    expect(last.person).toBeUndefined()
    expect(last.filter_field).toBeUndefined()
    expect(last.filter_value).toBeUndefined()
  })
})
