// Copy into the next verified fixed candidate's frontend/tests before running.
// Mount the real component; expected dates are explicit business outcomes.
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import EosFollow from '../components/v2/EosFollow.vue'

const wrappers: VueWrapper[] = []
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function fixture(now: string, today: string | undefined, months = 6, horizon = 24) {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(now))
  for (const [name, value] of Object.entries({ computed, onMounted, reactive, ref, watch })) vi.stubGlobal(name, value)
  vi.stubGlobal('useRoute', () => ({ query: {} }))
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  let businessDay = today
  vi.stubGlobal('useApi', () => ({ apiFetch: async () => ({
    today: businessDay, can_write: true, warnings: [], tiles: [],
    settings: { max_horizon_months: horizon, review_default_months: months, reason_classes: [] },
    rows: [{ no: 1, item_key: 'os|demo', owner_ref: 'user-a', todo: true, flags: [],
      hostname: 'demo-a', item: 'demo-os', reply_state: '待回覆', done_state: '未完成' }],
  }) }))
  const table = defineComponent({ props: ['rows'], setup(props, { slots }) {
    return () => h('div', (props.rows || []).map((row: any) => slots['cell-open']?.({ row })))
  } })
  const wrapper = mount(EosFollow, { global: { components: { V2Table: table }, stubs: { InfoNote: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '展開')!.trigger('click')
  return { wrapper, setToday: (day: string) => { businessDay = day } }
}

it.each([
  ['API業務日期優先於本機日期', '2026-11-01T16:30:00Z', '2026-11-03', 6, '2027-05-03'],
  ['缺API日期時以台北凌晨備援', '2026-11-01T16:30:00Z', undefined, 6, '2027-05-02'],
  ['台北午夜前仍使用當日本地日', '2026-11-01T15:59:59Z', undefined, 6, '2027-05-01'],
  ['API月份設定與月底截齊', '2027-01-31T04:00:00Z', '2027-01-31', 1, '2027-02-28'],
  ['閏年月末保留有效日期', '2024-01-31T04:00:00Z', '2024-01-31', 1, '2024-02-29'],
])('%s', async (_name, now, today, months, expected) => {
  const { wrapper } = await fixture(now as string, today as string | undefined, months as number)
  await wrapper.find('select').setValue('擬保留')
  expect((wrapper.find('input[type=date]').element as HTMLInputElement).value).toBe(expected)
})

it('同一掛載跨日後重新整理取得新業務日期，新的空白建議值採新日期', async () => {
  const { wrapper, setToday } = await fixture('2026-11-01T15:59:59Z', '2026-11-01')
  await wrapper.find('select').setValue('擬保留')
  expect((wrapper.find('input[type=date]').element as HTMLInputElement).value).toBe('2027-05-01')
  await wrapper.find('select').setValue('待回覆')
  setToday('2026-11-02')
  vi.setSystemTime(new Date('2026-11-01T16:01:00Z'))
  await wrapper.findAll('button').find(b => b.text() === '重新整理')!.trigger('click')
  await flushPromises()
  // Reopen the source row: a new form must use the new overview, not setup time.
  await wrapper.findAll('button').find(b => b.text() === '展開')!.trigger('click')
  await wrapper.find('select').setValue('擬保留')
  expect((wrapper.find('input[type=date]').element as HTMLInputElement).value).toBe('2027-05-02')
})

it('最遠期限一個月在月底須與後端截齊二月底一致', async () => {
  const { wrapper } = await fixture('2027-01-31T04:00:00Z', '2027-01-31', 1, 1)
  await wrapper.find('select').setValue('擬保留')
  expect(wrapper.find('input[type=date]').attributes('max')).toBe('2027-02-28')
})
