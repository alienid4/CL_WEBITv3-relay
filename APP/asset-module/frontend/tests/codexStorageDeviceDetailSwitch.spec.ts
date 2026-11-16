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
let deferredDetail: Promise<any> | null = null

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
  if (u.endsWith('/collected')) return deferredDetail ?? Promise.resolve(detailReply)
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


it('Codex S2 viewer switches device: previous array data is hidden while new detail is pending', async () => {
  admin = false
  devices = [dev({name:'DEMO-ARRAY-A'}),dev({id:2,name:'DEMO-ARRAY-B',host:'10.99.0.6'})]
  detailReply = {device_id:1,run:run({identity:{model:'DEMO-MODEL-A',serial:'DEMO-SERIAL-A'}}),latest:run(),rows:[rowOf({name:'DEMO-LUN-A'})]}
  const w=mnt(); await flushPromises()
  await w.findAll('button').filter(b=>b.text()==='收集明細')[0].trigger('click');await flushPromises()
  expect(w.find('.det').text()).toContain('DEMO-SERIAL-A')
  let resolveB!: (v:any)=>void
  deferredDetail=new Promise(resolve=>{resolveB=resolve})
  await w.findAll('button').filter(b=>b.text()==='收集明細')[1].trigger('click');await flushPromises()
  const pending=w.find('.det').text()
  resolveB({device_id:2,run:null,latest:null,rows:[]});deferredDetail=null;await flushPromises()
  expect(pending).toContain('DEMO-ARRAY-B')
  expect(pending).not.toContain('DEMO-SERIAL-A')
  expect(pending).not.toContain('DEMO-LUN-A')
})
