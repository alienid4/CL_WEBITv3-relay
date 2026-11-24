// 2.069.5：1.2 每日異動「比前一個工作日」——畫面要寫出實際比對的兩個日期；沒有可比的前一個工作日要講原因，不顯示 0。
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineAsyncComponent, defineComponent, h, onMounted, ref, Suspense, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import Changes from '../pages/v2/changes.vue'

const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })

async function fixture(changes: any, tab = 'daily', week: any = null) {
  for (const [name, value] of Object.entries({ computed, defineAsyncComponent, onMounted, ref, watch })) vi.stubGlobal(name, value)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(false) }))
  vi.stubGlobal('useRoute', () => ({ query: { tab } }))
  vi.stubGlobal('useRouter', () => ({ replace: vi.fn() }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: vi.fn() }))
  const read = vi.fn(async (path: string) => {
    if (path === '/api/changes') return changes
    if (path === '/api/v2/home/week') return week
    if (path === '/api/issues') return []
    throw new Error('Unexpected mock path ' + path)
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: read }))
  const table = defineComponent({ props: ['rows', 'empty'], setup(p) {
    return () => h('section', (p.rows ?? []).map((r: any) => h('p', { class: 'row' }, [r.date, r.status_text, r.n_text, r.label].filter(Boolean).join(' '))))
  } })
  const page = defineComponent({ setup(_, { slots }) { return () => h('main', [slots.conclusion?.(), slots.tab?.()]) } })
  const root = defineComponent({ setup() { return () => h(Suspense, null, { default: () => h(Changes) }) } })
  const wrapper = mount(root, { global: { components: { V2Page: page, V2Table: table }, stubs: { InfoNote: true, NuxtLink: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

const base = { dates: ['2026-11-23', '2026-11-20'], categories: [{ key: 'added', label: '新增' }], items: {}, counts: { added: 2 }, change_total: 2 }

it('週一的每日異動寫出「與 2026-11-20（週五）比較」', async () => {
  const w = await fixture({ ...base, date: '2026-11-23', prev_date: '2026-11-20', prev_label: '2026-11-20（週五）', reason: null })
  const el = w.find('[data-testid="chg-compare"]')
  expect(el.exists()).toBe(true)
  expect(el.text()).toBe('與 2026-11-20（週五） 比較')
})

it('前一個工作日沒有快照：不寫比較日期，改顯示原因，且異動不顯示成 0', async () => {
  const reason = '沒有可比對的前一個工作日：2026-11-20（週五） 沒有快照'
  const w = await fixture({ ...base, date: '2026-11-23', prev_date: null, prev_label: null, reason, counts: { added: 0 }, change_total: 0 })
  expect(w.find('[data-testid="chg-compare"]').exists()).toBe(false)
  expect(w.find('[data-testid="chg-reason"]').text()).toContain('沒有可比對的前一個工作日')
  expect(w.find('[data-testid="chg-conclusion"]').text()).toContain('異動無資料')
})

it('本週異動：前一個工作日沒有快照的那天，顯示是哪一天沒有，不是「沒有前一天可比」', async () => {
  const week = { rows: [
    { date: '2026-11-23', n: null, status: '無資料（前一個工作日 2026-11-20（週五） 沒有快照）', detail: '', link: null },
    { date: '2026-11-20', n: 3, status: '對比 2026-11-19（週四）', detail: '新增 3', link: null },
  ], freshness: {} }
  const w = await fixture({ ...base, date: '2026-11-23', prev_date: null, prev_label: null, reason: 'x' }, 'week', week)
  const txt = w.text()
  expect(txt).toContain('前一個工作日 2026-11-20（週五） 沒有快照')
  expect(txt).not.toContain('沒有前一天可比')
  expect(txt).toContain('對比 2026-11-19（週四）')
})

it('休市日設定讀不到：比較日期旁要寫出警語（2.070.1，審查 p1#15）', async () => {
  const note = '休市日設定讀不到，只跳過週末（比對日期可能不準）'
  const w = await fixture({ ...base, date: '2026-11-23', prev_date: '2026-11-20', prev_label: '2026-11-20（週五）', reason: null,
    compare: { holiday_note: note } })
  expect(w.find('[data-testid="chg-holiday-note"]').text()).toBe(note)
})

it('休市日設定正常：不顯示警語', async () => {
  const w = await fixture({ ...base, date: '2026-11-23', prev_date: '2026-11-20', prev_label: '2026-11-20（週五）', reason: null,
    compare: { holiday_note: null } })
  expect(w.find('[data-testid="chg-holiday-note"]').exists()).toBe(false)
})

it('1.1 首頁（index.vue）接了 compare.holiday_note（原始碼契約，index 沒有掛載測試）', async () => {
  const { readFileSync } = await import('node:fs')
  const { resolve } = await import('node:path')
  const src = readFileSync(resolve(process.cwd(), 'pages/v2/index.vue'), 'utf-8')
  expect(src).toContain('data.changes.compare?.holiday_note')
  expect(src).toContain('data-testid="chg-holiday-note"')
})
