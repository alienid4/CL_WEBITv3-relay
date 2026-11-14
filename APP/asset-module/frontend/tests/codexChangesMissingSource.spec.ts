import { mount, flushPromises } from '@vue/test-utils'
import { computed, defineAsyncComponent, defineComponent, h, onMounted, ref, Suspense, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import Changes from '../pages/v2/changes.vue'

afterEach(() => vi.unstubAllGlobals())

it('掃描異常來源讀取失敗不可把摘要顯示成未處理0筆', async () => {
  for (const [key, value] of Object.entries({ computed, defineAsyncComponent, onMounted, ref, watch })) vi.stubGlobal(key, value)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(true) }))
  vi.stubGlobal('useRoute', () => ({ query: { tab: 'issues' } }))
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: vi.fn() }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (path: string) => {
    if (path === '/api/issues') throw new Error('DEMO unavailable')
    if (path === '/api/changes') return { dates: ['2026-11-02'], categories: [], date: '2026-11-02', prev_date: '2026-11-01', items: {}, counts: {} }
    throw new Error('Unexpected mock path')
  } }))
  const page = defineComponent({ setup(_, { slots }) {
    return () => h('main', [slots.conclusion?.(), slots.tab?.()])
  } })
  const root = defineComponent({ setup() { return () => h(Suspense, null, { default: () => h(Changes) }) } })
  const wrapper = mount(root, { global: { components: { V2Page: page }, stubs: { V2Table: true, InfoNote: true, NuxtLink: true } } })
  try {
    await flushPromises()
    const text = wrapper.text().replace(/\s+/g, '')
    expect(text).toContain('掃描異常載入失敗')
    expect(text).not.toContain('掃描異常未處理0筆')
  } finally { wrapper.unmount() }
})
