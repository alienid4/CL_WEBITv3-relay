// 2.058.7（批 23 第 4、5 片審查修正）：加標籤結果留在畫面上、不能改分類要講原因、上次收集讀不到要講、
// 手動登記用的是那一列（第二列）、資產用途必填、改分類視窗只送認得出的那幾台。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let latestFails = false
const route = reactive({ query: {} as Record<string, any> })
const HOSTS = [
  { ip: '10.99.0.1', hostname: 'web01', segment: '10.99.0.0/24', mac: 'aa:bb', is_vm: 1, os_guess: 'Linux' },
  { ip: '10.99.0.2', hostname: null, segment: '10.99.1.0/24', mac: null, is_vm: 0, os_guess: 'Windows' },
]
const META = { categories: [{ key: 'b', label: '業務' }], fields: {
  asset_purpose: { label: '資產用途', category: 'b', layer: 'biz', required: true },
  environment: { label: '環境別', category: 'b', layer: 'biz', required: true, options: ['測試'] } } }
const TABS = [{ tab: 'ssh', label: 'SSH 收集', movable: true, why_not: null, choices: [{ os_type: 'Linux', label: 'Linux', onboardable: true }] },
  { tab: 'winrm', label: 'WinRM', movable: true, why_not: null, choices: [{ os_type: 'Windows', label: 'Windows', onboardable: true }] }]
const Table = defineComponent({ props: ['rows', 'cols', 'selected', 'selectable', 'prefix', 'rowKey', 'empty', 'max', 'headerClearsAll', 'unit'], emits: ['update:selected'], setup(p, { slots }) {
  return () => h('div', {}, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [r.ip, ...['go', 'op'].map(k => (slots[`cell-${k}`] ? slots[`cell-${k}`]!({ row: r }) : null))])))
} })
const TagSpy = defineComponent({ props: ['rows'], emits: ['done', 'close'], setup(_, { emit }) {
  return () => h('div', { class: 'tagm' }, [h('table', { class: 'results' }, '未登記，無法加標籤'), h('button', { class: 'tdone', onClick: () => emit('done') }, 'd'), h('button', { class: 'tclose', onClick: () => emit('close') }, 'c')])
} })
beforeEach(() => {
  calls = []; latestFails = false; route.query = {}
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(true), isViewer: ref(false) }))   // 2.058.8：取得納管指令只給管理員
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { route.query = x.query } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/scan/unregistered') return HOSTS.map(x => ({ ...x }))
    if (url === '/api/collect/dispatch/latest') {
      if (latestFails) throw Object.assign(new Error('x'), { data: { detail: '500 錯誤' } })
      return { results: [{ ip: '10.99.0.1', hostname: 'a', asset_serial: 'S1', tab: 'ssh', status: 'needs_credential', registered: 1 },
        { ip: '10.99.0.2', hostname: null, asset_serial: null, tab: 'ssh', status: 'needs_credential', registered: 0 }] }
    }
    if (url === '/api/field-meta') return META
    if (url === '/api/adopt/move-choices') return { tabs: TABS }
    if (url === '/api/asset-tags/by-serial') return { by_serial: {} }
    if (url === '/api/adopt/move-tab') return { moved: opts.body.targets.map((t: any) => t.ip), failed: [] }
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const base = { InfoNote: { template: '<i />' }, V2Table: Table, OnboardModal: { template: '<div />' }, BatchOnboardModal: { template: '<div />' }, PasteOnboardModal: { template: '<div />' }, TagBatchModal: TagSpy }
const writes = () => calls.filter(c => c.opts?.method === 'POST' && c.url !== '/api/asset-tags/by-serial')
const byText = (w: any, t: string) => w.findAll('button').filter((b: any) => b.text() === t)

describe('改分類與標籤：加標籤的結果留在畫面上（審查 P1）', () => {
  it('done 只重讀標籤、視窗還在；按關閉才收掉', async () => {
    const M = (await import('../components/v2/AdoptMove.vue')).default
    const C = (await import('../components/v2/AdoptClassify.vue')).default
    const w = mount(C, { global: { stubs: { ...base, V2AdoptMove: M } } })
    await flushPromises()
    ;(w.vm as any).tagRows = [{ ip: '10.99.0.2', asset_serial: null }]
    await flushPromises()
    const before = calls.filter(c => c.url === '/api/asset-tags/by-serial').length
    await w.find('.tdone').trigger('click'); await flushPromises()
    expect(w.find('.tagm .results').exists()).toBe(true)
    expect(calls.filter(c => c.url === '/api/asset-tags/by-serial').length).toBe(before + 1)
    await w.find('.tclose').trigger('click'); await flushPromises()
    expect(w.find('.tagm').exists()).toBe(false)
  })
  it('認不出是哪一台的列：寫出不能改分類，滑過看原因', async () => {
    const C = (await import('../components/v2/AdoptClassify.vue')).default
    const w = mount(C, { global: { stubs: { ...base, V2AdoptMove: { template: '<div />' } } } })
    await flushPromises()
    const s = w.find('[data-test="cannot-move"]')
    expect(s.exists()).toBe(true)
    expect(s.attributes('title')).toContain('認不出是哪一台')
  })
})

describe('改分類視窗：只送認得出的那幾台', () => {
  it('rows 有一台沒主機名也沒序號：不送它，並寫出幾台不搬', async () => {
    const M = (await import('../components/v2/AdoptMove.vue')).default
    const w = mount(M, { props: { rows: [{ ip: '10.99.0.1', hostname: 'a', asset_serial: 'S1', tab: 'ssh' }, { ip: '10.99.0.2', hostname: null, asset_serial: null, tab: 'ssh' }] }, global: { stubs: base } })
    await flushPromises()
    expect(w.text()).toContain('有 1 台認不出是哪一台')
    await w.find('select').setValue('winrm|Windows')
    await w.find('[data-test="move-submit"]').trigger('click'); await flushPromises()
    expect(writes()[0].opts.body.targets).toEqual([{ ip: '10.99.0.1', hostname: 'a', asset_serial: 'S1' }])
  })
})

describe('掃到未登記', () => {
  async function mk() {
    const R = (await import('../components/v2/AdoptRegister.vue')).default
    const C = (await import('../components/v2/HostUnregistered.vue')).default
    const w = mount(C, { global: { stubs: { ...base, V2AdoptRegister: R } } })
    await flushPromises()
    return w
  }
  it('上次收集讀不到：另外寫出錯誤（不是當成沒掃到）', async () => {
    latestFails = true
    const w = await mk()
    expect(w.find('[data-test="unreg-last-err"]').text()).toContain('500 錯誤')
  })
  it('第二列手動登記：預帶的是第二列；只填環境別不送（資產用途必填）', async () => {
    const w = await mk()
    await byText(w, '手動登記')[1].trigger('click'); await flushPromises()
    await w.find('select.in').setValue('測試')
    await w.find('[data-test="adopt-submit"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([])
    await w.find('input.in').setValue('檔案伺服器')
    await w.find('[data-test="adopt-submit"]').trigger('click'); await flushPromises()
    expect(writes()[0].opts.body).toEqual({ fields: { ip: '10.99.0.2', hostname: '', subnet: '10.99.1.0/24', environment: '測試', asset_purpose: '檔案伺服器' } })
  })
})
