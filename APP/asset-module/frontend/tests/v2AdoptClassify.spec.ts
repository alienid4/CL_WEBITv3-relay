// 批 23 第 5 片（V17）：改分類（/api/adopt/move-tab）與加標籤——規則與實掛。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { canMove, failMoveRow, moveBody, moveChoices, type CatTab } from '../utils/adoptCollect'

const TABS: CatTab[] = [
  { tab: 'ssh', label: 'SSH 收集', movable: true, why_not: null, choices: [{ os_type: 'Linux', label: 'Linux', onboardable: true }] },
  { tab: 'mainframe', label: '大型主機', movable: true, why_not: null, choices: [{ os_type: 'AIX', label: 'AIX', onboardable: true }, { os_type: 'IBM i', label: 'AS400', onboardable: false }] },
  { tab: 'esxi', label: 'ESXi', movable: true, why_not: null, choices: [{ os_type: 'VMware', label: 'VMware', onboardable: false }] },
  { tab: 'agent', label: 'Push Agent', movable: false, why_not: '連線狀態', choices: [] },
]

describe('改分類規則（舊頁同一套）', () => {
  it('canMove：要主機名＋IP 或序號', () => {
    expect(canMove({ ip: '10.99.0.1', hostname: 'a' })).toBe(true)
    expect(canMove({ ip: '10.99.0.1', asset_serial: 'S' })).toBe(true)
    expect(canMove({ ip: '10.99.0.1' })).toBe(false)
  })
  it('選項：每個頁簽都列；目前在這不能選；連線狀態不能選；多子類別展開', () => {
    const c = moveChoices([{ ip: '1', tab: 'ssh' }], TABS)
    expect(c.map(x => [x.value, x.disabled])).toEqual([['ssh|Linux', true], ['mainframe|AIX', false], ['mainframe|IBM i', false], ['esxi|VMware', false], ['agent|', true]])
    expect(c.find(x => x.value === 'esxi|VMware')!.note).toBe('（另有納管方式，不試連）')
    expect(c.find(x => x.value === 'mainframe|AIX')!.label).toBe('大型主機 — AIX')
  })
  it('body 同舊頁；還原送 null', () => {
    const rows = [{ ip: '10.99.0.1', hostname: 'a', asset_serial: 'S', tab: 'ssh' }]
    expect(moveBody(rows, 'mainframe|AIX', '  登記錯 ', false)).toEqual({ targets: [{ ip: '10.99.0.1', hostname: 'a', asset_serial: 'S' }], tab: 'mainframe', os_type: 'AIX', reason: '登記錯' })
    expect(moveBody(rows, '', '', true)).toEqual({ targets: [{ ip: '10.99.0.1', hostname: 'a', asset_serial: 'S' }], tab: null, os_type: null, reason: null })
  })
  it('納管失敗列：大類換成頁簽', () => {
    expect(failMoveRow({ ip: '1', hostname: 'h', asset_serial: 'S', os_class: 'Windows' }).tab).toBe('winrm')
    expect(failMoveRow({ ip: '1', os_class: 'X', os_override: { tab: 'esxi' } }).tab).toBe('esxi')
  })
})

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let route: any
const Table = defineComponent({ props: ['rows', 'cols', 'selected', 'selectable', 'prefix', 'rowKey', 'empty', 'max', 'headerClearsAll', 'unit'], emits: ['update:selected'], setup(p, { slots, emit }) {
  return () => h('div', {}, [
    h('button', { class: 'pick', onClick: () => emit('update:selected', { '10.99.0.1': true, '10.99.0.2': true, '10.99.0.3': true }) }, 'pick'),
    ...(p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [r.ip, slots['cell-op'] ? slots['cell-op']!({ row: r }) : null])),
  ])
} })
const TagSpy = defineComponent({ props: ['rows'], setup(p) { return () => h('div', { class: 'tagm' }, JSON.stringify(p.rows)) } })
beforeEach(() => {
  calls = []
  route = reactive({ query: {} as Record<string, any> })
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { route.query = x.query } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/collect/dispatch/latest') return { results: [
      { ip: '10.99.0.1', hostname: 'a', asset_serial: 'S1', tab: 'ssh', status: 'needs_credential', registered: 1 },
      { ip: '10.99.0.2', hostname: null, asset_serial: null, tab: 'ssh', status: 'needs_credential', registered: 0 },
      { ip: '10.99.0.3', hostname: 'esx', asset_serial: 'S3', tab: 'esxi', status: 'not_onboardable', registered: 1 },
      { ip: '10.99.0.9', tab: 'empty', status: 'import_only', registered: 0 },
    ] }
    if (url === '/api/adopt/move-choices') return { tabs: TABS }
    if (url === '/api/asset-tags/by-serial') return { by_serial: { S1: [{ tag: 'root 不可遠端登入', note: null }] } }
    if (url === '/api/adopt/move-tab') return { moved: opts.body.targets.map((t: any) => t.ip), failed: [] }
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, TagBatchModal: TagSpy }
async function mk() {
  const M = (await import('../components/v2/AdoptMove.vue')).default
  const C = (await import('../components/v2/AdoptClassify.vue')).default
  const w = mount(C, { global: { stubs: { ...stubs, V2AdoptMove: M } } })
  await flushPromises()
  return w
}
const writes = () => calls.filter(c => c.opts?.method === 'POST' && c.url !== '/api/asset-tags/by-serial')

describe('改分類與標籤（實掛）', () => {
  it('載入只讀（by-serial 是批量查標籤）；空位址不列', async () => {
    const w = await mk()
    expect(writes()).toEqual([])
    expect(w.findAll('.row').map(r => r.text().slice(0, 9))).toEqual(['10.99.0.1', '10.99.0.2', '10.99.0.3'])
  })
  it('勾三台改分類：認不出的那台不送；選 AIX 才送，body 同舊頁', async () => {
    const w = await mk()
    await w.find('button.pick').trigger('click')
    await w.find('[data-test="classify-move"]').trigger('click'); await flushPromises()
    await w.find('[data-test="move-submit"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([])                                  // 沒選頁簽不送
    await w.find('[data-test="move-dialog"] select').setValue('mainframe|AIX')
    await w.find('[data-test="move-submit"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/adopt/move-tab', opts: { method: 'POST', body: {
      targets: [{ ip: '10.99.0.1', hostname: 'a', asset_serial: 'S1' }, { ip: '10.99.0.3', hostname: 'esx', asset_serial: 'S3' }], tab: 'mainframe', os_type: 'AIX', reason: null } } }])
  })
  it('換分類篩選後，看不到的勾選不會進加標籤', async () => {
    const w = await mk()
    await w.find('button.pick').trigger('click')
    await w.findAll('button').find(b => b.text().startsWith('ESXi'))!.trigger('click'); await flushPromises()
    await w.find('[data-test="classify-tag"]').trigger('click')
    expect(JSON.parse(w.find('.tagm').text()).map((r: any) => r.ip)).toEqual(['10.99.0.3'])
  })
})
