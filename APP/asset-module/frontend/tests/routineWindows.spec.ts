// 主機頁「例行時段」（2.066.x）：兩個門檻（非開盤／開盤）都顯示、儲存時一起送；只能看的帳號沒有表單。API 全假（ip:192.0.2.x）。
import { flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Comp from '../components/v2/RoutineWindows.vue'

const calls: { url: string; opts: any }[] = []
let canEdit = true
const Table = defineComponent({ props: ['rows', 'cols'], setup: p => () => h('table', (p.rows as any[]).map(r => h('tr', String(r.id ?? r.start_hhmm)))) })
beforeEach(() => {
  calls.length = 0
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o?: any) => {
    calls.push({ url: u, opts: o })
    if (u === '/api/v2/routine') return Promise.resolve({ host_key: 'ip:192.0.2.5', windows: [], log: [], suggestions: [], attention_min: 10, attention_open_min: 3, can_edit: canEdit })
    return Promise.resolve({})
  } }))
})
afterEach(() => { vi.unstubAllGlobals(); canEdit = true })

const mountIt = async () => { const w = mount(Comp, { props: { mkey: 'ip:192.0.2.5' }, global: { stubs: { V2Table: Table, InfoNote: true } } }); await flushPromises(); return w }

it('顯示兩個門檻，儲存一起送', async () => {
  const w = await mountIt()
  expect(w.text()).toContain('非開盤 10 分・開盤 3 分')
  await w.find('[data-testid=rw-open-min]').setValue(0)
  await w.find('[data-testid=rw-long-h]').setValue(36)
  const btns = w.findAll('button').filter(b => b.text() === '儲存')
  await btns[btns.length - 1].trigger('click'); await flushPromises()
  const put = calls.find(c => c.url === '/api/v2/routine-settings')!
  expect(put.opts.body).toEqual({ attention_min: 10, attention_open_min: 0, longterm_hours: 36 })
})

it('只能看的帳號沒有表單也沒有門檻輸入', async () => {
  canEdit = false
  const w = await mountIt()
  expect(w.find('[data-testid=rw-form]').exists()).toBe(false)
  expect(w.find('[data-testid=rw-open-min]').exists()).toBe(false)
})
