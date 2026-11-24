// 舊版 V1 → v2 缺口（165 項 5）：部署圖點節點，影響頁籤要以被點的節點為中心，不是丟掉參數。實掛 pages/v2/system/[id].vue。
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, h, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import SystemPage from '../pages/v2/system/[id].vue'

const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })

async function fixture(query: Record<string, string>) {
  for (const [name, value] of Object.entries({ computed, ref, watch })) vi.stubGlobal(name, value)
  vi.stubGlobal('definePageMeta', () => {})
  const route = reactive({ params: { id: 'A-01' }, query })
  const replace = vi.fn(async (to: any) => { route.query = to.query })
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace }))
  vi.stubGlobal('navigateTo', vi.fn())
  const read = vi.fn(async (path: string, opt?: any) => {
    if (path === '/api/blast/system-map') return { name: 'DEMO 系統', summary: { hosts: 0 }, depends_on: [], depended_by: [], hosts: [], rooms: [], outage: [] }
    if (path === '/api/blast/impact') {
      const n = opt?.query?.node_id
      return { label: n === 'esxi:demo-esx' ? 'demo-esx' : 'DEMO 系統', deps_available: true, summary: { by_biz_system: [], by_location: [], notify: [] } }
    }
    throw new Error('Unexpected mock path ' + path)
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: read }))
  const page = defineComponent({ props: ['tabs'], setup(_, { slots }) { return () => h('main', [slots.conclusion?.(), slots.tab?.()]) } })
  const embed = defineComponent({ setup(_, { slots }) { return () => h('div', slots.default?.()) } })
  // V2SystemMap 替身（2.048.1 起 V2 原生，不再 V2Embed 舊 SystemMap）：按鈕代表「在部署圖點了 ESXi 節點」
  const sysMap = defineComponent({ emits: ['impact', 'update:apiId'], setup(_, { emit }) { return () => h('button', { class: 'pick-node', onClick: () => emit('impact', 'esxi:demo-esx') }, '點節點') } })
  const graph = defineComponent({ props: ['nodeId'], setup(p) { return () => h('i', { class: 'graph' }, p.nodeId) } })
  const table = defineComponent({ props: ['rows'], setup() { return () => h('section') } })
  const root = defineComponent({ setup() { return () => h(Suspense, null, { default: () => h(SystemPage) }) } })
  const wrapper = mount(root, { global: {
    components: { V2Page: page, V2Embed: embed, V2SystemMap: sysMap, V2RelationGraph: graph, V2Table: table, ContactCard: true },
    stubs: { InfoNote: true, NuxtLink: true, ClientOnly: { template: '<div><slot /></div>' } } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, read, replace, route }
}
const impactCalls = (read: any) => read.mock.calls.filter((c: any[]) => c[0] === '/api/blast/impact').map((c: any[]) => c[1].query.node_id)

it('部署圖點節點：保留 node 參數切到影響頁籤，並以該節點查影響', async () => {
  const { wrapper, read, replace } = await fixture({ tab: 'deps' })
  await wrapper.find('.pick-node').trigger('click')
  await flushPromises()
  expect(replace).toHaveBeenCalledWith({ query: { tab: 'impact', node: 'esxi:demo-esx' } })
  expect(impactCalls(read)).toEqual(['esxi:demo-esx'])
  expect(wrapper.find('.graph').text()).toBe('esxi:demo-esx')
  expect(wrapper.text()).toContain('以「demo-esx」為中心')
})

it('直接進影響頁籤（沒有 node）：仍以本業務系統為中心', async () => {
  const { wrapper, read } = await fixture({ tab: 'impact' })
  expect(impactCalls(read)).toEqual(['bizsys:A-01'])
  expect(wrapper.find('.graph').text()).toBe('bizsys:A-01')
  expect(wrapper.text()).not.toContain('為中心')
})
