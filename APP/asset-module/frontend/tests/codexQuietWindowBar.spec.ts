import { mount, flushPromises } from '@vue/test-utils'
import { computed, onMounted, reactive, ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import QuietWindowBar from '../components/QuietWindowBar.vue'

afterEach(() => vi.unstubAllGlobals())

it.each([false, true])('補跑失敗可見且僅admin有重設入口（admin=%s）', async (admin) => {
  for (const [name, value] of Object.entries({ computed, onMounted, reactive, ref })) vi.stubGlobal(name, value)
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(admin) }))
  const toast = vi.fn()
  vi.stubGlobal('useToast', () => ({ showToast: toast }))
  let reset = false
  const fetch = vi.fn(async (_path: string, opts?: any) => {
    if (opts?.method === 'POST') { reset = true; return {} }
    return { in_quiet_window: true, enabled: true, broken: false, problem: '', start: '08:30', end: '15:00', holidays: [],
      message: '盤中自動排程延後', warning: '自動排程可能因設定／資料庫異常而停擺', recent: [],
      pending: [{ entry: 'vrops_import_tick', state: reset ? 'pending' : 'failed', attempts: reset ? 0 : 3, next_at: '', detail: 'DEMO failure' }] }
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: fetch }))
  const wrapper = mount(QuietWindowBar, { global: { stubs: { InfoNote: true, V2Table: true } } })
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('失敗 3 次')
    expect(wrapper.text()).toContain('自動排程可能因設定／資料庫異常而停擺')
    const button = wrapper.findAll('button').find(b => b.text() === '重設')
    if (!admin) {
      expect(button).toBeUndefined()
      expect(fetch.mock.calls.some(c => c[1]?.method === 'POST')).toBe(false)
    } else {
      expect(button).toBeDefined()
      await button!.trigger('click')
      await flushPromises()
      expect(fetch).toHaveBeenCalledWith('/api/quiet-window/pending/vrops_import_tick/reset', { method: 'POST' })
      expect(wrapper.text()).toContain('等窗外補跑')
      expect(wrapper.text()).not.toContain('失敗 3 次')
      expect(toast).toHaveBeenCalledWith('已重設，下一個窗外週期會補跑', 'success')
    }
  } finally { wrapper.unmount() }
})
