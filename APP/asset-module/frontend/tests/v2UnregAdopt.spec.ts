// 批 23 第 4 片（V17）：2.1「掃到未登記」——手動登記（POST /api/assets/adopt）、一鍵／批次納管只送看得到的勾選。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { adoptPrefill, filterUnreg, pruneIps, visibleIps } from '../utils/adoptCollect'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
const HOSTS = [
  { ip: '10.99.0.1', hostname: 'web01', segment: '10.99.0.0/24', mac: 'aa:bb', is_vm: 1, os_guess: 'Linux' },
  { ip: '10.99.0.2', hostname: null, segment: '10.99.0.0/24', mac: null, is_vm: 0, os_guess: 'Windows' },
]
const Table = defineComponent({ props: ['rows', 'cols', 'selected', 'selectable', 'prefix', 'rowKey', 'empty', 'max', 'headerClearsAll', 'unit'], emits: ['update:selected'], setup(p, { slots, emit }) {
  return () => h('div', {}, [
    h('button', { class: 'pick', onClick: () => emit('update:selected', { '10.99.0.1': true, '10.99.0.2': true }) }, 'pick'),
    ...(p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [r.ip, slots['cell-go'] ? slots['cell-go']!({ row: r }) : null])),
  ])
} })
const Spy = (name: string) => defineComponent({ props: ['ips', 'ip', 'osGuess'], setup(p) { return () => h('div', { class: name }, JSON.stringify(p)) } })
beforeEach(() => {
  calls = []
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(true), isViewer: ref(false) }))   // 2.058.8：取得納管指令只給管理員
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/scan/unregistered') return HOSTS.map(x => ({ ...x }))
    if (url === '/api/collect/dispatch/latest') return { results: [{ ip: '10.99.0.1', status: 'not_onboardable', message: 'VMware ESXi' }] }
    if (url === '/api/field-meta') return { categories: [{ key: 'b', label: '業務' }], fields: { asset_purpose: { label: '資產用途', category: 'b', layer: 'biz', required: true }, environment: { label: '環境別', category: 'b', layer: 'biz', required: true, options: ['測試'] }, ip: { label: 'IP', category: 'b', layer: 'tech' } } }
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, OnboardModal: Spy('om'), BatchOnboardModal: Spy('bom'), V2AdoptRegister: undefined as any }
async function mkUnreg() {
  const R = (await import('../components/v2/AdoptRegister.vue')).default
  const C = (await import('../components/v2/HostUnregistered.vue')).default
  const w = mount(C, { global: { stubs: { ...stubs, V2AdoptRegister: R } } })
  await flushPromises()
  return w
}
const writes = () => calls.filter(c => c.opts?.method)

describe('規則', () => {
  it('預帶同舊頁 openAdopt', () => {
    expect(adoptPrefill(HOSTS[0])).toEqual({ ip: '10.99.0.1', hostname: 'web01', subnet: '10.99.0.0/24', mac: 'aa:bb', is_vm: 'VM' })
    expect(adoptPrefill(HOSTS[1])).toEqual({ ip: '10.99.0.2', hostname: '', subnet: '10.99.0.0/24' })
  })
  it('搜尋每個字都要有；勾選只送看得到的', () => {
    expect(filterUnreg(HOSTS, 'windows 10.99').map(h => h.ip)).toEqual(['10.99.0.2'])
    expect(visibleIps({ '10.99.0.1': true, '10.99.0.2': true }, [HOSTS[1]])).toEqual(['10.99.0.2'])
    expect(pruneIps({ '10.99.0.1': true, x: true }, HOSTS)).toEqual({ '10.99.0.1': true })
  })
})

describe('掃到未登記（實掛）', () => {
  it('載入只讀', async () => {
    await mkUnreg()
    expect(writes()).toEqual([])
  })
  it('搜尋後批次納管只送看得到的那台', async () => {
    const w = await mkUnreg()
    await w.find('button.pick').trigger('click')
    await w.find('input[type=search]').setValue('windows')
    await flushPromises()
    await w.find('[data-test="unreg-batch-onboard"]').trigger('click')
    expect(JSON.parse(w.find('.bom').text()).ips).toEqual(['10.99.0.2'])
  })
  it('一鍵納管＝共用 OnboardModal（ESXi 由後端擋），帶這台的 IP 與研判', async () => {
    const w = await mkUnreg()
    await w.findAll('button').find(b => b.text() === '一鍵納管')!.trigger('click')
    expect(JSON.parse(w.find('.om').text())).toMatchObject({ ip: '10.99.0.1', osGuess: 'Linux' })
    expect(writes()).toEqual([])
  })
  it('手動登記：必填沒填不送；填了送同一支 body，成功後那台從清單拿掉', async () => {
    const w = await mkUnreg()
    await w.findAll('button').find(b => b.text() === '手動登記')!.trigger('click'); await flushPromises()
    await w.find('[data-test="adopt-submit"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([])
    await w.find('input.in:not([placeholder])').setValue('AP 主機')
    await w.find('select.in').setValue('測試')
    await w.find('[data-test="adopt-submit"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/assets/adopt', opts: { method: 'POST', body: { fields: { ip: '10.99.0.1', hostname: 'web01', subnet: '10.99.0.0/24', mac: 'aa:bb', is_vm: 'VM', asset_purpose: 'AP 主機', environment: '測試' } } } }])
    expect(w.findAll('.row').map(r => r.text().slice(0, 9))).toEqual(['10.99.0.2'])
  })
})
