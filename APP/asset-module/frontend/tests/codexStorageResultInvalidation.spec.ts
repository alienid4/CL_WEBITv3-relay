// Prepared for a NEW fixed candidate after 181. Do not rerun the known .66 UI defect.
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import Storage from '../components/v2/IntegrationStorage.vue'

const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })

async function fixture(failSave = false, deferSecondTest = false) {
  for (const [k, v] of Object.entries({ computed, onMounted, reactive, ref })) vi.stubGlobal(k, v)
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(true) }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  let device: any = { id: 1, name: 'DEMO-device', vendor: 'dell_unity', vendor_label: 'Dell Unity', host: '10.99.0.55', port: 443,
    credential: 'stor-a', ca_bundle: '', trust_self_signed: false, enabled: true, note: '',
    last_test_ok: null, last_test_code: null, last_test_msg: null, last_test_at: null, last_identity: null }
  let testCalls = 0
  let finishLate: ((value: any) => void) | undefined
  const apiFetch = vi.fn(async (url: string, opts: any = {}) => {
    if (url === '/api/credentials') return ['stor-a', 'stor-b'].map(name => ({ name, kind: 'storage', username: 'DEMO', scope: '10.99.0.' }))
    if (url === '/api/storage/devices') return { rows: [{ ...device }], vendors: [{ key: 'dell_unity', label: 'Dell Unity' }], unsupported_vendors: [] }
    if (url.endsWith('/test')) {
      testCalls++
      if (deferSecondTest && testCalls === 2) return await new Promise(resolve => { finishLate = resolve })
      device = { ...device, last_test_ok: false, last_test_code: 'unauthorized', last_test_msg: 'DEMO-OLD-TEST', last_test_at: '2026-11-02 07:00:00' }
      return { ok: false, message: 'DEMO-OLD-TEST' }
    }
    if (opts.method === 'PUT') {
      if (failSave) throw new Error('DEMO save rejected')
      device = { ...device, ...opts.body, last_test_ok: null, last_test_code: null, last_test_msg: null, last_test_at: null, last_identity: null }
      return { ...device }
    }
    throw new Error('Unexpected fixture operation')
  })
  vi.stubGlobal('useApi', () => ({ apiFetch }))
  const table = defineComponent({ props: ['rows'], setup(p, { slots }) { return () => h('table', (p.rows ?? []).map((row: any) => h('tr', [
    h('td', `${row.addr} ${row.result}`), h('td', slots['cell-act']?.({ row })),
  ]))) } })
  const wrapper = mount(Storage, { global: { components: { V2Table: table }, stubs: { InfoNote: true, NuxtLink: true } } })
  wrappers.push(wrapper); await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '連線測試')!.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('DEMO-OLD-TEST')
  await wrapper.findAll('button').find(b => b.text() === '編輯')!.trigger('click')
  return { wrapper, apiFetch, finishLate: () => { if (!finishLate) throw new Error('late test not started'); finishLate({ ok: false, message: 'DEMO-LATE-OLD-ENDPOINT' }) } }
}

it.each(['host', 'credential'])('成功更改%s後不得把上一設定的即時測試結果掛到新設定', async field => {
  const { wrapper, apiFetch } = await fixture()
  if (field === 'host') await wrapper.find('input[aria-label="IP 或主機名"]').setValue('10.99.0.56')
  else await wrapper.find('select[aria-label="收集憑證"]').setValue('stor-b')
  await wrapper.findAll('button').find(b => b.text() === '儲存')!.trigger('click'); await flushPromises()
  expect(apiFetch.mock.calls.filter(c => c[1]?.method === 'PUT')).toHaveLength(1)
  expect(wrapper.find('table').text()).toContain('尚未測試')
  // Clearing is expected. An explicitly labelled, endpoint-versioned history design may replace this oracle after review.
  const results = wrapper.find('ul.res')
  expect(results.exists() ? results.text() : '').not.toContain('DEMO-OLD-TEST')
})

it('設定存檔失敗不能冒充已變更或清掉原有效結果', async () => {
  const { wrapper } = await fixture(true)
  await wrapper.find('input[aria-label="IP 或主機名"]').setValue('10.99.0.56')
  await wrapper.findAll('button').find(b => b.text() === '儲存')!.trigger('click'); await flushPromises()
  expect(wrapper.find('table').text()).toContain('10.99.0.55:443')
  expect(wrapper.find('ul.res').text()).toContain('DEMO-OLD-TEST')
  expect(wrapper.find('input[aria-label="IP 或主機名"]').exists()).toBe(true)
})

// Prepared only: review the new candidate's busy/editing contract before execution.
// Explicitly endpoint-labelled history or blocking edits until test completion is also acceptable.
it('舊端點測試延遲回應不得成為新端點的即時結果', async () => {
  const { wrapper, finishLate } = await fixture(false, true)
  await wrapper.findAll('button').find(b => b.text() === '連線測試')!.trigger('click')
  await flushPromises()
  await wrapper.find('input[aria-label="IP 或主機名"]').setValue('10.99.0.56')
  const save = wrapper.findAll('button').find(b => b.text() === '儲存')!
  if (save.attributes('disabled') !== undefined) {
    finishLate(); await flushPromises()
    expect(wrapper.find('table').text()).toContain('10.99.0.55:443')
    return // Product prevented endpoint mutation until the pending operation settled.
  }
  await save.trigger('click'); await flushPromises()
  expect(wrapper.find('table').text()).toContain('10.99.0.56:443')
  finishLate(); await flushPromises()
  const result = wrapper.find('ul.res')
  expect(result.exists() ? result.text() : '').not.toContain('DEMO-LATE-OLD-ENDPOINT')
})
