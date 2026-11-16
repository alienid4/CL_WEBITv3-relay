// 舊版 V1 → v2 缺口（164／165 項）：機櫃盤點「結案」＋差異表補登逾期／天數／期限。實掛 components/v2/InventoryFlow.vue。
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, h, ref, Suspense, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import InventoryFlow from '../components/v2/InventoryFlow.vue'
import { useSort } from '../composables/useSort'

const wrappers: VueWrapper[] = []
const originalConfirm = Object.getOwnPropertyDescriptor(window, 'confirm')
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  vi.unstubAllGlobals()
  if (originalConfirm) Object.defineProperty(window, 'confirm', originalConfirm)
  else delete (window as any).confirm
})

interface Opt { status?: string; viewer?: boolean; canEdit?: boolean; tab?: string; confirmOk?: boolean }
async function fixture(o: Opt = {}) {
  for (const [name, value] of Object.entries({ computed, ref, watch, useSort })) vi.stubGlobal(name, value)
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(!!o.viewer) }))
  vi.stubGlobal('useRoute', () => ({ query: { tab: o.tab || 'tasks', task: '7' } }))
  vi.stubGlobal('useRouter', () => ({ replace: vi.fn() }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: vi.fn(), busy: ref(false) }))
  vi.stubGlobal('useV2ExportName', () => (s: string) => s)
  Object.defineProperty(window, 'confirm', { configurable: true, value: vi.fn(() => o.confirmOk !== false) })
  const write = vi.fn(async () => ({ id: 7, status: 'closed' }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: write }))
  const read = vi.fn(async (path: string) => {
    if (path === '/api/v2/inv/rack/tasks') {
      return { can_edit: o.canEdit !== false, items: [{ id: 7, title: 'DEMO 機櫃盤點', created_at: '2026-11-01 09:00', created_by: 'demo', status: o.status || '進行中', scope: '1／2 櫃已盤', size: 10, diff_count: 1, diff_open: 1 }] }
    }
    if (path === '/api/v2/inv/rack/tasks/7/diffs') {
      return { prev: null, rows: [{ diff_key: 'k1', host: 'DEMO-R1 A01', name: 'demo-box', kind: '櫃上有但沒登記', before: '', after: 'x', next_step: '補登', action: null, status: '待處理',
        reason: null, status_by: null, status_at: null, key: null, overdue: true, aging_days: 12, due: '2026-10-15' }] }
    }
    throw new Error('Unexpected mock path ' + path)
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: read }))
  const page = defineComponent({ props: ['tabs'], setup(_, { slots }) { return () => h('main', [slots.conclusion?.(), slots.tab?.()]) } })
  const table = defineComponent({ props: ['rows', 'empty'], setup(p) { return () => h('section', p.rows?.map((r: any) => h('div', r.title))) } })
  const sortTh = defineComponent({ props: ['k'], setup(_, { slots }) { return () => h('th', slots.default?.()) } })
  const root = defineComponent({ setup() { return () => h(Suspense, null, { default: () => h(InventoryFlow, { kind: 'rack', no: '7.1', title: '機櫃盤點' }) }) } })
  const wrapper = mount(root, { global: { components: { V2Page: page, V2Table: table, SortTh: sortTh }, stubs: { InfoNote: true, NuxtLink: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, write, read }
}

it('進行中的機櫃盤點：有權限者看到「結案」，二次確認後呼叫 close 並重讀任務', async () => {
  const { wrapper, write, read } = await fixture()
  const btn = wrapper.findAll('button').find(b => b.text() === '結案')!
  expect(btn).toBeTruthy()
  await btn.trigger('click')
  await flushPromises()
  expect(window.confirm).toHaveBeenCalledTimes(1)
  expect(write).toHaveBeenCalledWith('/api/rack-audits/7/close', { method: 'POST' })
  expect(read.mock.calls.filter(c => c[0] === '/api/v2/inv/rack/tasks')).toHaveLength(2)
})

it('二次確認按取消：不呼叫 close', async () => {
  const { wrapper, write } = await fixture({ confirmOk: false })
  await wrapper.findAll('button').find(b => b.text() === '結案')!.trigger('click')
  await flushPromises()
  expect(write).not.toHaveBeenCalled()
})

it('唯讀帳號、無編輯權限者看不到結案鈕', async () => {
  for (const o of [{ viewer: true }, { canEdit: false }]) {
    const { wrapper, write } = await fixture(o)
    expect(wrapper.findAll('button').some(b => b.text() === '結案')).toBe(false)
    expect(write).not.toHaveBeenCalled()
    wrapper.unmount()
  }
})

it('已結案：沒有結案鈕，標示不能再交盤點結果（唯讀）', async () => {
  const { wrapper } = await fixture({ status: '已結案' })
  expect(wrapper.findAll('button').some(b => b.text() === '結案')).toBe(false)
  expect(wrapper.text()).toContain('已結案')
})

it('差異處理表帶出補登逾期：天數、期限、逾期標示', async () => {
  const { wrapper } = await fixture({ tab: 'diffs' })
  const text = wrapper.text()
  expect(text).toContain('天數／期限')
  expect(text).toContain('12')
  expect(text).toContain('期限 2026-10-15')
  expect(text).toContain('逾期')
  expect(wrapper.find('td.bad').exists()).toBe(true)
})
