// 2.058.10（批 23 審查 P2-5）：舊 /adopt#頁簽 轉址的行為測試（取代原始碼字串比對）：
//  ① 全域 middleware 真的把 to.hash 帶進對照表，轉到 V2 同一頁籤；
//  ② 伺服器端轉址看不到 #、瀏覽器帶著 # 落到 /v2/onboard 時，6.1 頁掛載後自己換成對應頁籤（網址指定 tab 時不動）。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineAsyncComponent, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as ipf from '../utils/ipFilter'

enableAutoUnmount(afterEach)
let navs: any[] = []
const route = reactive({ query: {} as Record<string, any>, hash: '', path: '/v2/onboard' })

beforeEach(() => {
  navs = []; route.query = {}; route.hash = ''
  for (const [k, v] of Object.entries({ ...ipf, ref, reactive, computed, onMounted, onBeforeUnmount, watch, defineAsyncComponent })) vi.stubGlobal(k, v)
  vi.stubGlobal('defineNuxtRouteMiddleware', (fn: any) => fn)
  vi.stubGlobal('navigateTo', (to: any, opts: any) => { navs.push({ to, opts }); return to })
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(true), isViewer: ref(false) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { route.query = x.query ?? route.query } }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: () => {}, busy: ref(false) }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: async () => ({}) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string) => (url === '/api/v2/onboard/pending' ? { actionable_tabs: [], tab_label: {}, results: [] } : {}) }))
})
afterEach(() => vi.unstubAllGlobals())

describe('全域 middleware：舊 /adopt#頁簽', () => {
  const run = async (to: any) => (await import('../middleware/v2-legacy.global')).default(to, null as any)
  it.each([
    ['#onboarded', { path: '/v2/onboard', query: { tab: 'onboarded' } }],
    ['#managed-hosts', { path: '/v2/onboard', query: { tab: 'onboarded' } }],
    ['#failures', { path: '/v2/onboard', query: { tab: 'failed' } }],
    ['#collect', { path: '/v2/onboard', query: { tab: 'collect' } }],
    ['#unregistered', { path: '/v2/hosts', query: { tab: 'unreg' } }],
    ['', { path: '/v2/onboard', query: {} }],
    ['#nope', { path: '/v2/onboard', query: {} }],
  ])('%s', async (hash, want) => {
    await run({ path: '/adopt', query: {}, hash })
    expect(navs).toEqual([{ to: want, opts: { replace: true } }])
  })
  it('legacy=1 也轉（已移植）', async () => {
    await run({ path: '/adopt', query: { legacy: '1' }, hash: '#pending' })
    expect(navs[0].to).toEqual({ path: '/v2/onboard', query: { tab: 'pending' } })
  })
})

const P = defineComponent({ setup(_, { slots }) { return () => h('div', [slots.tab?.()]) } })
async function mountOnboard() {
  const O = (await import('../pages/v2/onboard.vue')).default
  const w = mount(defineComponent({ setup: () => () => h(Suspense, null, { default: () => h(O) }) }),
    { global: { stubs: { V2Page: P, InfoNote: true, V2Table: true, NuxtLink: true, ClientOnly: true, V2AdoptManaged: true } } })
  await flushPromises(); await flushPromises()
  return w
}

describe('6.1 掛載時：帶著舊 # 落到 /v2/onboard', () => {
  it('#unregistered → 2.1 掃到未登記', async () => {
    route.hash = '#unregistered'
    await mountOnboard()
    expect(navs).toEqual([{ to: { path: '/v2/hosts', query: { tab: 'unreg' } }, opts: { replace: true } }])
  })
  it('#managed-hosts → 6.1 已納管', async () => {
    route.hash = '#managed-hosts'
    await mountOnboard()
    expect(navs[0].to).toEqual({ path: '/v2/onboard', query: { tab: 'onboarded' } })
  })
  it('網址已指定 tab：不動', async () => {
    route.hash = '#onboarded'; route.query = { tab: 'pending' }
    await mountOnboard()
    expect(navs).toEqual([])
  })
  it('沒有 #：不動', async () => {
    await mountOnboard()
    expect(navs).toEqual([])
  })
})
