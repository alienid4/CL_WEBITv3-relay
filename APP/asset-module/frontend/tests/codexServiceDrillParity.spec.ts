import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import Service from '../pages/v2/inventory/service.vue'

const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })

async function fixture(query: Record<string, string> = {}, viewer = false) {
  for (const [k, v] of Object.entries({ computed, ref, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  const route = reactive({ query: { tab: 'list', ...query } })
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ push: vi.fn(async (v: any) => { route.query = v.query }) }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  const write = vi.fn(async () => { throw new Error('No collection authorized in this fixture') })
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: write }))
  const read = vi.fn(async (path: string, opts: any) => {
    if (path !== '/api/services') throw new Error('Unexpected fake endpoint')
    const items = [{ id: 1, ip: '10.99.0.11', hostname: 'demo-service', asset_serial: 'DEMO-1', port: 8080, proto: 'tcp', process: 'demo-http', exposure: 'all' }]
    return { items: opts.query.exposure === 'localhost' ? [] : items,
      summary: { live: 1, current: 1, stale: 0, hosts: 1, exposed: 1, confirmed: 1, gone: 0 } }
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: read }))
  const flow = defineComponent({ setup(_, { slots }) { return () => h('main', slots.extra?.({ tab: 'list' })) } })
  const table = defineComponent({ props: ['rows', 'empty'], setup(p) { return () => h('section', { class: 'fixture-rows' }, p.rows?.length ? p.rows.map((r: any) => h('div', r.host)) : p.empty) } })
  const wrapper = mount(Service, { global: { components: { V2InventoryFlow: flow, V2Table: table }, stubs: { InfoNote: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, read, write }
}

it('點卡片後清除舊關鍵字，顯示該卡片對應資料', async () => {
  const { wrapper, write } = await fixture()
  expect(wrapper.find('.fixture-rows').text()).toContain('demo-service')
  await wrapper.find('input[aria-label="搜尋服務"]').setValue('DEMO-NOT-FOUND')
  expect(wrapper.find('.fixture-rows').text()).toContain('沒有符合的服務')
  await wrapper.findAll('button').find(b => b.text().startsWith('目前在聽的服務'))!.trigger('click')
  await flushPromises()
  expect(write).not.toHaveBeenCalled()
  expect(wrapper.find('input[aria-label="搜尋服務"]').element).toHaveProperty('value', '')
  expect(wrapper.find('.fixture-rows').text()).toContain('demo-service')
})

it('點卡片後清除舊對外篩選，避免卡片1筆但列表空', async () => {
  const { wrapper, read } = await fixture()
  await wrapper.find('select[aria-label="對外"]').setValue('localhost')
  await flushPromises()
  await wrapper.findAll('button').find(b => b.text().startsWith('目前在聽的服務'))!.trigger('click')
  await flushPromises()
  expect(read.mock.calls.at(-1)![1].query.exposure).toBeUndefined()
  expect(wrapper.find('.fixture-rows').text()).toContain('demo-service')
})

it('精確主機下鑽保留IP與資產編號，並含管理服務及已消失資料', async () => {
  const { read } = await fixture({ ip: '10.99.0.11', asset_serial: 'DEMO-1' })
  expect(read.mock.calls.at(-1)![1].query).toMatchObject({ ip: '10.99.0.11', asset_serial: 'DEMO-1', include_infra: true, include_gone: true })
})

it('唯讀角色可看成功來源的服務但沒有收集按鈕，也不執行寫入', async () => {
  const { wrapper, write } = await fixture({}, true)
  expect(wrapper.find('.fixture-rows').text()).toContain('demo-service')
  expect(wrapper.findAll('button').some(b => b.text() === '收集服務')).toBe(false)
  expect(write).not.toHaveBeenCalled()
})
