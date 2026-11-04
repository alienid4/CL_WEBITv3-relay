// Run against the next verified fixed candidate, alongside the initial-failure regression.
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineAsyncComponent, defineComponent, h, onMounted, ref, Suspense, watch } from 'vue'
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

async function fixture(empty: boolean, failRefresh = false) {
  for (const [name, value] of Object.entries({ computed, defineAsyncComponent, onMounted, ref, watch })) vi.stubGlobal(name, value)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false) }))
  vi.stubGlobal('useRoute', () => ({ query: { tab: 'issues' } }))
  vi.stubGlobal('useRouter', () => ({ replace: vi.fn() }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  Object.defineProperty(window, 'confirm', { configurable: true, value: vi.fn(() => true) })
  const write = vi.fn(async () => ({ marked: 1 }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: write }))
  let issueReads = 0
  const read = vi.fn(async (path: string) => {
    if (path === '/api/changes') return { dates: ['2026-11-02'], categories: [], date: '2026-11-02', prev_date: '2026-11-01', items: {}, counts: {} }
    if (path === '/api/issues') {
      issueReads++
      if (failRefresh && issueReads > 1) throw new Error('DEMO refresh unavailable')
      return empty ? [] : [{ id: 51, detected_at: '2026-11-02 02:00:00', hostname: 'demo-scan', ip: '10.99.0.51', issue_type: '掃描失敗', is_read: 0, handled_at: null }]
    }
    throw new Error('Unexpected mock path')
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: read }))
  const table = defineComponent({ props: ['rows', 'empty'], emits: ['update:selected'], setup(p, { emit }) {
    return () => h('section', p.rows?.length ? p.rows.map((row: any) => h('button', {
      class: 'fixture-pick', onClick: () => emit('update:selected', { [row.id]: true }),
    }, row.name)) : p.empty)
  } })
  const page = defineComponent({ setup(_, { slots }) { return () => h('main', [slots.conclusion?.(), slots.tab?.()]) } })
  const root = defineComponent({ setup() { return () => h(Suspense, null, { default: () => h(Changes) }) } })
  const wrapper = mount(root, { global: { components: { V2Page: page, V2Table: table }, stubs: { InfoNote: true, NuxtLink: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, write, read }
}

it('來源成功空才可顯示未處理0筆，不能一律以未知取代真正的零', async () => {
  const { wrapper, write } = await fixture(true)
  const text = wrapper.text().replace(/\s+/g, '')
  expect(text).toContain('掃描異常未處理0筆')
  expect(text).not.toContain('掃描異常載入失敗')
  expect(write).not.toHaveBeenCalled()
})

it('成功讀取後操作觸發刷新失敗，舊筆數須明示最後成功資料', async () => {
  const { wrapper, write, read } = await fixture(false, true)
  expect(wrapper.text()).toContain('demo-scan')
  await wrapper.find('.fixture-pick').trigger('click')
  await wrapper.findAll('button').find(b => b.text().startsWith('標記已處理'))!.trigger('click')
  await flushPromises()
  expect(write).toHaveBeenCalledWith('/api/issues/mark-read', { method: 'POST', body: { ids: [51] } })
  expect(read.mock.calls.filter(c => c[0] === '/api/issues')).toHaveLength(2)
  expect(wrapper.text()).toContain('掃描異常載入失敗')
  expect(wrapper.text()).toContain('最後成功資料')
})
