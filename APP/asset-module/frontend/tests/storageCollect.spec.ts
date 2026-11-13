// 儲存設備資料收集（批 S2）畫面：「收集」只有 admin 看得到、走 POST /collect；列表有「最近一次收集」摘要；
// 「收集明細」唯讀（一般帳號也能開）、不完整／失敗要明講而不是顯示成 0 筆、最近一次失敗時標出目前顯示的是更早的資料。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/IntegrationStorage.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
let admin = true
let collectReply: any = { ok: true, status: 'ok', message: '收集完成：9 筆' }
let detailReply: any

const sections = (o: any = {}) => [
  { key: 'system', label: '設備身份', status: 'ok', rows: 1, code: null, message: '' },
  { key: 'pool', label: 'Storage Pool', status: 'ok', rows: 1, code: null, message: '' },
  { key: 'lun', label: 'LUN', status: 'ok', rows: 2, code: null, message: '' }, ...(o.extra ?? []),
]
const run = (o: any = {}) => ({
  id: 5, device_id: 1, device_name: 'unity-a', endpoint: '10.99.0.5:443', started_at: '2026-11-02 16:00:00', finished_at: '2026-11-02 16:00:09',
  status: 'ok', complete: true, code: 'ok', message: '收集完成', collected_by: 'admin', tls_verified: true, item_count: 4, review_count: 1,
  identity: { name: 'unity-a', model: 'Unity 380', serial: 'FNM1', os_version: '5.3.0.0' }, sections: sections(), ...o,
})
const rowOf = (o: any = {}) => ({ __no: 1, kind: 'lun', kind_label: 'LUN', obj_id: 'res_1', name: 'lun-1', parent: 'pool_1', size_total: 1073741824, size_used: 0,
  size_free: null, review: '', incomplete: false, extra: { wwn: '60:1' }, ...o })
const dev = (o: any = {}) => ({
  id: 1, __no: 1, name: 'unity-a', vendor: 'dell_unity', vendor_label: 'Dell Unity', host: '10.99.0.5', port: 443, credential: 'stor-ro',
  ca_bundle: '', trust_self_signed: false, enabled: true, note: '', last_test_at: null, last_test_ok: null, last_test_code: null,
  last_test_msg: null, last_identity: null, last_tls_verified: null, last_collect: null, ...o,
})
let devices: any[] = []

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  const m = o.method ?? 'GET'
  if (u === '/api/storage/devices' && m === 'GET') return Promise.resolve({ rows: devices, vendors: [{ key: 'dell_unity', label: 'Dell Unity' }], unsupported_vendors: [] })
  if (u === '/api/credentials') return Promise.resolve([])
  if (u.endsWith('/collect')) return Promise.resolve(collectReply)
  if (u.endsWith('/collected')) return Promise.resolve(detailReply)
  return Promise.resolve({ ok: true })
}

const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p, { slots }) {
    return () => p.rows.length === 0 ? h('div', { class: 'empty-' + p.prefix }, p.empty) : h('table', {}, p.rows.map((r: any) => h('tr', { class: 'row-' + p.prefix }, [
      h('td', { class: 'name' }, r.name), h('td', { class: 'collect' }, r.collect), h('td', { class: 'flag' }, r.flag), h('td', { class: 'tg' }, r.total_g),
      h('td', { class: 'act' }, slots['cell-act']?.({ row: r })),
    ])))
  },
})

beforeEach(() => {
  calls.length = 0; admin = true; devices = [dev()]
  collectReply = { ok: true, status: 'ok', message: '收集完成：9 筆' }
  detailReply = { device_id: 1, run: run(), latest: run(), rows: [rowOf(), rowOf({ __no: 2, obj_id: 'res_2', name: 'lun-2', review: '所屬 Pool 在已收 Pool 清單中找不到' })] }
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: computed(() => admin) }))
  vi.stubGlobal('window', Object.assign(globalThis.window ?? {}, { confirm: () => true }))
})

const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, NuxtLink: { template: '<a><slot /></a>' }, V2Table } } })
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text() === t)

describe('儲存設備資料收集（S2）', () => {
  it('admin 按「收集」走 POST /collect，結果顯示在畫面；清單重新載入', async () => {
    const w = mnt(); await flushPromises()
    expect(w.find('.collect').text()).toBe('尚未收集')
    devices = [dev({ last_collect: { id: 5, status: 'ok', complete: true, finished_at: '2026-11-02 16:00:09', item_count: 4, review_count: 1, sections: sections() } })]
    await btn(w, '收集')!.trigger('click'); await flushPromises()
    const c = calls.find(x => x.url === '/api/storage/devices/1/collect')!
    expect(c.opts.method).toBe('POST')
    expect(w.text()).toContain('收集完成：9 筆')
    expect(w.find('.collect').text()).toContain('完整')
    expect(w.find('.collect').text()).toContain('待核 1')
  })

  it('收集不完整：畫面標「收集未完成」與原因，列表摘要寫不完整而非 0 筆', async () => {
    collectReply = { ok: false, status: 'partial', message: '部分資料未收齊，結果不完整：LUN：不完整（帳號或密碼被拒絕（HTTP 401））' }
    devices = [dev({ last_collect: { id: 6, status: 'partial', complete: false, finished_at: '2026-11-02 16:10:00', item_count: 3, review_count: 0, sections: sections() } })]
    const w = mnt(); await flushPromises()
    await btn(w, '收集')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('收集未完成')
    expect(w.text()).toContain('結果不完整')
    expect(w.find('.collect').text()).toContain('不完整')
  })

  it('非 admin：沒有「收集」「連線測試」按鈕，但可以開唯讀明細', async () => {
    admin = false
    const w = mnt(); await flushPromises()
    expect(btn(w, '收集')).toBeUndefined()
    expect(btn(w, '連線測試')).toBeUndefined()
    await btn(w, '收集明細')!.trigger('click'); await flushPromises()
    expect(calls.some(x => x.url === '/api/storage/devices/1/collected')).toBe(true)
    expect(w.findAll('.row-SC').length).toBe(2)
    expect(calls.every(x => (x.opts?.method ?? 'GET') === 'GET')).toBe(true)      // 全程沒有任何寫入
  })

  it('明細：顯示資料時間、各區段狀態、GiB 換算、待核標示；可只看待核', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '收集明細')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('資料時間 2026-11-02 16:00:09')
    expect(w.text()).toContain('LUN：完整，2 筆')
    expect(w.find('.row-SC .tg').text()).toBe('1.00')
    expect(w.text()).toContain('所屬 Pool 在已收 Pool 清單中找不到')
    await btn(w, '待核')!.trigger('click')
    expect(w.findAll('.row-SC').length).toBe(1)
  })

  it('明細：區段失敗或略過要明講，不顯示成 0 筆；最近一次失敗時標出目前顯示的是更早的資料', async () => {
    const bad = run({ id: 9, status: 'failed', complete: false, message: '帳號或密碼被拒絕（HTTP 401）', item_count: 0, finished_at: '2026-11-02 17:00:00',
      sections: [{ key: 'system', label: '設備身份', status: 'failed', rows: 0, code: 'unauthorized', message: '' }] })
    detailReply = { device_id: 1, run: run({ status: 'partial', complete: false, sections: sections({ extra: [
      { key: 'host', label: 'Host', status: 'partial', rows: 1, code: 'timeout', message: '' },
      { key: 'initiator', label: 'Host Initiator', status: 'skipped', rows: 0, code: 'skipped', message: '' }] }) }), latest: bad, rows: [rowOf()] }
    const w = mnt(); await flushPromises()
    await btn(w, '收集明細')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('Host：不完整，1 筆')
    expect(w.text()).toContain('Host Initiator：略過')
    expect(w.text()).not.toContain('Host Initiator：略過，0 筆')
    expect(w.text()).toContain('最近一次收集（2026-11-02 17:00:00）失敗')
    expect(w.text()).toContain('更早')
  })

  it('明細：還沒收過時給提示；過濾後沒資料的空表格說明不等於 0 筆', async () => {
    detailReply = { device_id: 1, run: null, latest: null, rows: [] }
    const w = mnt(); await flushPromises()
    await btn(w, '收集明細')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('還沒有收集紀錄')
  })

  it('說明收在註（InfoNote）：未實機驗證、bytes→GiB、待核不自動合併', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '收集明細')!.trigger('click'); await flushPromises()
    const notes = w.findAll('.note').map((n: any) => n.text()).join('\n')
    expect(notes).toContain('尚未在實機驗證')
    expect(notes).toContain('不會自動新增、合併或刪除任何資產')
  })
})
