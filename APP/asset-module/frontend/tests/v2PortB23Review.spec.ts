// 2.058.8（批 23 第 6、7 片獨立審查修正）實掛：
//  P1：2.1 掃到未登記「上次收集」讀不到時，表格照樣在（以前 lastErr 夾在 v-if 鏈中間，整張表不見）。
//  P2：批次試密碼／預檢／取得納管指令只給管理員；上次納管紀錄讀不到時，按鈕旁留著原因、不給勾；
//      6.1 待納管載入後不會自己打開試密碼視窗（M6）；讀不到時不可以當成讀到（M7）。全假資料、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineAsyncComponent, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as ipf from '../utils/ipFilter'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let admin = true
let latestFails = false
let lastFailFails = false
let lastFail: Record<string, any> = {}
const route = reactive({ query: {} as Record<string, any>, hash: '' })
const HOSTS = [{ ip: '10.99.0.1', hostname: 'web01', segment: '10.99.0.0/24', mac: null, is_vm: 0, os_guess: 'Linux' }]
const PENDING = { actionable_tabs: ['ssh', 'winrm'], tab_label: { ssh: 'SSH 收集', winrm: 'WinRM' }, results: [
  { ip: '10.99.0.21', hostname: 'a21', tab: 'ssh', route: 'ssh', status: 'needs_credential' },
  { ip: '10.99.0.22', hostname: 'a22', tab: 'ssh', route: 'ssh', status: 'needs_credential' }] }

const Table = defineComponent({ props: ['rows', 'cols', 'selected', 'selectable', 'prefix', 'rowKey', 'empty', 'max', 'headerClearsAll', 'unit', 'canSelect'], emits: ['update:selected'], setup(p, { emit, slots }) {
  return () => h('div', { class: 'tbl' }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [
    h('span', { class: 'ip' }, r.ip),
    h('button', { class: 'pick', onClick: () => emit('update:selected', { ...(p.selected || {}), [r[p.rowKey || 'key']]: true }) }, 'pick'),
    ...['go', 'op'].map(k => (slots[`cell-${k}`] ? slots[`cell-${k}`]!({ row: r }) : null))])))
} })
const Spy = (cls: string) => defineComponent({ props: ['ips', 'ip'], setup(p) { return () => h('div', { class: cls, 'data-ips': (p.ips ?? []).join(',') }) } })
const Pass = defineComponent({ setup(_, { slots }) { return () => h('div', slots.default?.()) } })
const Page = defineComponent({ props: ['modelValue', 'tabs', 'width'], setup(_, { slots }) { return () => h('div', [slots.title?.(), slots.conclusion?.(), slots.tab?.()]) } })

beforeEach(() => {
  calls = []; admin = true; latestFails = false; lastFailFails = false; lastFail = {}; route.query = {}; route.hash = ''
  for (const [k, v] of Object.entries({ ...ipf, ref, reactive, computed, onMounted, onBeforeUnmount, watch, defineAsyncComponent })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(admin), isViewer: ref(false) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { route.query = x.query ?? route.query } }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: () => {}, busy: ref(false) }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: async (url: string, opts: any) => { calls.push({ url, opts }); return {} } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/scan/unregistered') return HOSTS.map(x => ({ ...x }))
    if (url === '/api/collect/dispatch/latest') { if (latestFails) throw Object.assign(new Error('x'), { data: { detail: '500 錯誤' } }); return { results: [] } }
    if (url === '/api/v2/onboard/pending') return JSON.parse(JSON.stringify(PENDING))
    if (url === '/api/onboard/audit/latest-by-ip') { if (lastFailFails) throw Object.assign(new Error('x'), { data: { detail: '逾時' } }); return lastFail }
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())

const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, ClientOnly: Pass, V2Page: Page,
  BatchProbeModal: Spy('probe-modal'), PrecheckModal: Spy('precheck-modal'), PasteOnboardModal: Spy('paste-modal'), PreinfoPanel: Spy('preinfo'),
  OnboardModal: Spy('om'), BatchOnboardModal: Spy('bom'), WinOnboardModal: Spy('wom'), RevokeModal: Spy('rm'), WinRevokeModal: Spy('wrm'),
  V2AdoptRegister: Spy('reg'), V2AdoptMove: Spy('mv'), V2BatchAuto: Spy('ba') }

describe('2.1 掃到未登記（審查 P1）', () => {
  it('上次收集讀不到：原因寫出來，表格照樣在', async () => {
    latestFails = true
    const C = (await import('../components/v2/HostUnregistered.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    expect(w.find('[data-test="unreg-last-err"]').text()).toContain('500 錯誤')
    expect(w.findAll('.row .ip').map(x => x.text())).toEqual(['10.99.0.1'])
    expect(w.text()).not.toContain('讀取中')
  })
  it('一般帳號看不到「取得納管指令」；管理員看得到', async () => {
    const C = (await import('../components/v2/HostUnregistered.vue')).default
    admin = false
    let w = mount(C, { global: { stubs } }); await flushPromises()
    expect(w.find('[data-test="unreg-paste"]').exists()).toBe(false)
    w.unmount(); admin = true
    w = mount(C, { global: { stubs } }); await flushPromises()
    expect(w.find('[data-test="unreg-paste"]').exists()).toBe(true)
  })
})

async function mountOnboard() {
  route.query = { tab: 'pending' }
  const O = (await import('../pages/v2/onboard.vue')).default
  const w = mount(defineComponent({ setup: () => () => h(Suspense, null, { default: () => h(O) }) }), { global: { stubs } })
  await flushPromises(); await flushPromises()
  return w
}
const probeBtn = (w: any) => w.find('[data-test="pend-probe"]')

describe('6.1 待納管 批次試密碼／預檢（實掛整頁，審查 P2）', () => {
  it('載入後不會自己打開任何試密碼／預檢視窗（M6）', async () => {
    const w = await mountOnboard()
    expect(w.findAll('.row .ip').map((x: any) => x.text())).toEqual(['10.99.0.21', '10.99.0.22'])
    expect(w.find('.probe-modal').exists()).toBe(false)
    expect(w.find('.precheck-modal').exists()).toBe(false)
    expect(calls.some(c => c.url.startsWith('/api/onboard/batch-probe') || c.url.startsWith('/api/onboard/precheck'))).toBe(false)
  })
  it('勾一台按批次試密碼：視窗只拿到那一台；上次被 VMware 擋下的不給', async () => {
    lastFail = { '10.99.0.22': { fail: { short: 'VMware 不可納管' } } }
    const w = await mountOnboard()
    const picks = w.findAll('.row .pick')
    await picks[0].trigger('click'); await picks[1].trigger('click')
    expect(probeBtn(w).text()).toContain('（1）')
    await probeBtn(w).trigger('click')
    expect(w.find('.probe-modal').attributes('data-ips')).toBe('10.99.0.21')
  })
  it('上次納管紀錄讀不到：不給勾、按鈕旁留著原因（M7：不可以當成讀到）', async () => {
    lastFailFails = true
    const w = await mountOnboard()
    await w.findAll('.row .pick')[0].trigger('click')
    expect(probeBtn(w).attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="pend-precheck"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="pend-lastfail-err"]').text()).toContain('逾時')
  })
  it('一般帳號：看不到批次試密碼、預檢', async () => {
    admin = false
    const w = await mountOnboard()
    expect(probeBtn(w).exists()).toBe(false)
    expect(w.find('[data-test="pend-precheck"]').exists()).toBe(false)
  })
})
