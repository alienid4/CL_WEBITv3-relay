// 舊版 V1 → v2 缺口（164 項 1）：掃描異常頁籤補「類型篩選」與「單筆標已處理」。實掛 pages/v2/changes.vue。
// 既有行為（唯讀不顯示寫入、來源失敗顯示未取得）由 codexChangesSourceStates.spec.ts 守，這裡只補新功能與唯讀。
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, reactive, defineAsyncComponent, defineComponent, h, onMounted, ref, Suspense, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import Changes from '../pages/v2/changes.vue'

const wrappers: VueWrapper[] = []
const originalConfirm = Object.getOwnPropertyDescriptor(window, 'confirm')
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  vi.unstubAllGlobals()
  if (originalConfirm) Object.defineProperty(window, 'confirm', originalConfirm)
  else delete (window as any).confirm
})

const ISSUES = [
  { id: 1, detected_at: '2026-11-02 02:00:00', hostname: 'demo-lost', ip: '10.99.0.1', issue_type: '異常消失', is_read: 0, handled_at: null },
  { id: 2, detected_at: '2026-11-02 02:00:00', hostname: 'demo-new', ip: '10.99.0.2', issue_type: '異常新增', is_read: 0, handled_at: null },
  { id: 3, detected_at: '2026-11-01 02:00:00', hostname: 'demo-miss', ip: '10.99.0.3', issue_type: '漏登記', is_read: 0, handled_at: null },
  { id: 4, detected_at: '2026-11-01 02:00:00', hostname: 'demo-done', ip: '10.99.0.4', issue_type: '異常消失', is_read: 1, handled_at: '2026-11-02 08:00:00' },
]

async function fixture(o: { viewer?: boolean; type?: string } = {}) {
  for (const [name, value] of Object.entries({ computed, defineAsyncComponent, onMounted, ref, watch })) vi.stubGlobal(name, value)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(!!o.viewer) }))
  const route = reactive<{ query: Record<string, any> }>({ query: { tab: 'issues', ...(o.type ? { type: o.type } : {}) } })
  const replace = vi.fn((to: any) => { route.query = { ...to.query } })      // 網址跟著 replace 更新（頁面的類型篩選以網址為準）
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  Object.defineProperty(window, 'confirm', { configurable: true, value: vi.fn(() => true) })
  const write = vi.fn(async () => ({ marked: 1 }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: write }))
  const read = vi.fn(async (path: string) => {
    if (path === '/api/changes') return { dates: [], categories: [], date: null, prev_date: null, items: {}, counts: {} }
    if (path === '/api/issues') return ISSUES
    throw new Error('Unexpected mock path')
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: read }))
  // 這個 V2Table 替身會畫出列與具名插槽 cell-act（單筆按鈕）
  const table = defineComponent({ props: ['rows', 'cols', 'empty'], emits: ['update:selected'], setup(p, { slots }) {
    return () => h('section', { class: 'fixture-rows' }, p.rows?.length ? p.rows.map((r: any) => h('div', { class: 'fixture-row' }, [r.name, slots['cell-act']?.({ row: r })])) : p.empty)
  } })
  const page = defineComponent({ setup(_, { slots }) { return () => h('main', [slots.conclusion?.(), slots.tab?.()]) } })
  const root = defineComponent({ setup() { return () => h(Suspense, null, { default: () => h(Changes) }) } })
  const wrapper = mount(root, { global: { components: { V2Page: page, V2Table: table }, stubs: { InfoNote: true, NuxtLink: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, write, read, replace }
}
const rowsText = (w: VueWrapper) => w.findAll('.fixture-row').map(r => r.text())

it('類型篩選：只列所選類型，並同步網址 ?type=；計數隨處理狀態', async () => {
  const { wrapper, replace } = await fixture()
  expect(rowsText(wrapper)).toHaveLength(3)   // 預設只看未處理
  const chip = (t: string) => wrapper.findAll('button.chip').find(b => b.text().startsWith(t))!
  expect(chip('異常消失').text()).toContain('1')       // 未處理的異常消失只有 1（已處理那筆不算）
  await chip('異常消失').trigger('click')
  expect(rowsText(wrapper).join('|')).toContain('demo-lost')
  expect(rowsText(wrapper).join('|')).not.toContain('demo-new')
  expect(replace).toHaveBeenCalledWith({ query: { tab: 'issues', type: '異常消失' } })
  await chip('全部').trigger('click')                   // 「全部」是處理狀態；類型的「全部類型」另一顆
  await wrapper.findAll('button.chip').find(b => b.text().startsWith('全部類型'))!.trigger('click')
  expect(rowsText(wrapper).join('|')).toContain('demo-new')
})

it('深連結 ?type=異常新增 一進來就只列該類型', async () => {
  const { wrapper } = await fixture({ type: '異常新增' })
  expect(rowsText(wrapper)).toHaveLength(1)
  expect(rowsText(wrapper)[0]).toContain('demo-new')
  expect(wrapper.text()).toContain('之前沒看過')   // 類型說明
})

it('單筆標已處理：確認後只送這一筆到 mark-read，並重讀清單；已處理的列沒有按鈕', async () => {
  const { wrapper, write, read } = await fixture()
  const btns = wrapper.findAll('button.lb').filter(b => b.text() === '標已處理')
  expect(btns).toHaveLength(3)
  await btns[1].trigger('click')
  await flushPromises()
  expect(window.confirm).toHaveBeenCalledTimes(1)
  expect(write).toHaveBeenCalledWith('/api/issues/mark-read', { method: 'POST', body: { ids: [2] } })
  expect(read.mock.calls.filter(c => c[0] === '/api/issues')).toHaveLength(2)
  // 切到「全部」：已處理那筆存在但沒有單筆按鈕
  await wrapper.findAll('button.chip').find(b => b.text().startsWith('全部') && !b.text().startsWith('全部類型'))!.trigger('click')
  expect(rowsText(wrapper)).toHaveLength(4)
  expect(wrapper.findAll('.fixture-row').find(r => r.text().includes('demo-done'))!.find('button').exists()).toBe(false)
})

it('唯讀帳號：沒有單筆與批次寫入按鈕，也不呼叫寫入', async () => {
  const { wrapper, write } = await fixture({ viewer: true })
  expect(wrapper.findAll('button').some(b => b.text().includes('標已處理') || b.text().startsWith('標記已處理'))).toBe(false)
  expect(write).not.toHaveBeenCalled()
})

it('網址的類型不存在：明講是篩選造成 0 筆，不當成沒有異常', async () => {
  const { wrapper } = await fixture({ type: '不存在的類型' })
  expect(wrapper.text()).toContain('不在目前資料中')
  expect(wrapper.text()).toContain('不代表沒有異常')
})
