import { mount, flushPromises } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import EosFollow from '../components/v2/EosFollow.vue'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('台北凌晨預填重新檢視日須採API的台北日期，不能用UTC昨天', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-01T16:30:00Z'))
  for (const [name, value] of Object.entries({ computed, onMounted, reactive, ref, watch })) vi.stubGlobal(name, value)
  vi.stubGlobal('useRoute', () => ({ query: {} }))
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async () => ({
    today: '2026-11-02', can_write: true, warnings: [], tiles: [],
    settings: { max_horizon_months: 24, review_default_months: 6, reason_classes: [] },
    rows: [{ no: 1, item_key: 'os|demo', owner_ref: 'user-a', todo: true, flags: [],
      hostname: 'demo-a', item: 'demo-os', reply_state: '待回覆', done_state: '未完成' }],
  }) }))
  const table = defineComponent({ props: ['rows'], setup(props, { slots }) {
    return () => h('div', (props.rows || []).map((row: any) => slots['cell-open']?.({ row })))
  } })
  const wrapper = mount(EosFollow, { global: { components: { V2Table: table }, stubs: { InfoNote: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '展開')!.trigger('click')
  await wrapper.find('select').setValue('擬保留')
  // UTC is Nov 1, but the business date returned by the API is Nov 2.
  expect((wrapper.find('input[type=date]').element as HTMLInputElement).value).toBe('2027-05-02')
  wrapper.unmount()
})
