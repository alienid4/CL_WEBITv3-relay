// 設定頁「Aria Operations」內的「每日 VMware 快照」（系統直抓，2.069.0）：開關、立即抓一次、最近一次狀態（含沒收到）、診斷收合。全假資料、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import IntegrationVrops from '../components/v2/IntegrationVrops.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
const toasts: { m: string; t: string }[] = []
let admin = true
let dcStatus: any

const baseDc = (o: any = {}) => ({
  module_enabled: true, enabled: false, configured: true, running: false, window: '06:00～08:30', day: '2026-11-19',
  last: {}, last_ok: null, today: { day: '2026-11-19', complete: false, collected_at: null, source: null }, ...o,
})
function fakeApi(url: string, opts: any = {}) {
  calls.push({ url, opts })
  const m = opts.method ?? 'GET'
  if (url === '/api/integrations/vrops' && m === 'GET') return Promise.resolve({ url: 'https://aria.example.local', auth_source: '', ca_bundle: '', enabled: false, interval_hours: 24, credential: 'aria' })
  if (url === '/api/integrations/vrops/runs') return Promise.resolve({ runs: [] })
  if (url === '/api/integrations/vrops/dailycheck' && m === 'GET') return Promise.resolve(dcStatus)
  if (url === '/api/integrations/vrops/dailycheck' && m === 'PUT') { dcStatus = { ...dcStatus, enabled: opts.body.enabled }; return Promise.resolve(dcStatus) }
  if (url === '/api/integrations/vrops/dailycheck/run') return Promise.resolve({ status: 'started', message: '已開始抓取，幾分鐘後完成；請看最近一次狀態' })
  if (url === '/api/credentials') return Promise.resolve([{ name: 'aria', kind: 'vrops', username: 'svc' }])
  return Promise.resolve({})
}
const V2Table = defineComponent({ props: ['rows'], setup(p) { return () => h('table', String((p.rows ?? []).length)) } })

beforeEach(() => {
  calls.length = 0; toasts.length = 0; admin = true; dcStatus = baseDc()
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string, t: string) => toasts.push({ m, t }) }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(admin), canWrite: ref(admin) }))
})
afterEach(() => vi.unstubAllGlobals())
const mnt = () => mount(IntegrationVrops, { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, V2Table, NuxtLink: { template: '<a><slot /></a>' } } } })

describe('每日 VMware 快照（系統直抓）', () => {
  it('還沒有任何紀錄：寫「沒收到」，不是 0 也不是正常；開關預設關', async () => {
    const w = mnt(); await flushPromises()
    const st = w.get('[data-test="vrops-dc-latest"]')
    expect(st.text()).toContain('沒收到')
    expect(st.text()).toContain('今天：沒收到')
    expect(st.classes()).toContain('bad')
    expect((w.get('input[aria-label="每日 VMware 自動抓取"]').element as HTMLInputElement).checked).toBe(false)
    expect(w.find('[data-test="vrops-dc-adv"]').exists()).toBe(false)
  })

  it('最近一次與今天都有：寫來源與時間；原因與批次在收合的診斷區', async () => {
    dcStatus = baseDc({ enabled: true, last: { at: '2026-11-19 06:03:10', status: 'ok', message: 'VMware 每日檢查：收集器 2', problems: [], calls: 44, elapsed_s: 90 },
      last_ok: { batch_id: 'ariadc_direct_20261119T060000', collected_at: '2026-11-19 06:00:00' }, today: { day: '2026-11-19', complete: true, collected_at: '2026-11-19 06:00:00', source: 'direct' } })
    const w = mnt(); await flushPromises()
    const st = w.get('[data-test="vrops-dc-latest"]')
    expect(st.text()).toContain('成功')
    expect(st.text()).toContain('2026-11-19 06:03:10')
    expect(st.text()).toContain('已收到完整批次（系統直抓）')
    expect(st.classes()).not.toContain('bad')
    const adv = w.get('[data-test="vrops-dc-adv"]')
    expect((adv.element as HTMLDetailsElement).open).toBe(false)
    expect(adv.text()).toContain('ariadc_direct_20261119T060000')
  })

  it('不完整：顯示「不完整」與原因（診斷區），今天仍是「沒收到」', async () => {
    dcStatus = baseDc({ enabled: true, last: { at: '2026-11-19 06:10:00', status: 'incomplete', message: '不完整：ESXi 主機：分頁不完整', problems: ['ESXi 主機：分頁不完整（vROps 說 9 筆，讀到 7 筆）'] } })
    const w = mnt(); await flushPromises()
    expect(w.get('[data-test="vrops-dc-latest"]').text()).toContain('不完整')
    expect(w.get('[data-test="vrops-dc-latest"]').text()).toContain('今天：沒收到')
    expect(w.get('[data-test="vrops-dc-adv"]').text()).toContain('分頁不完整')
  })

  it('admin 打勾送 PUT {enabled:true}；立即抓一次走 POST /run 並顯示開始訊息', async () => {
    dcStatus = baseDc({ enabled: false })
    const w = mnt(); await flushPromises()
    await w.get('input[aria-label="每日 VMware 自動抓取"]').setValue(true)
    await flushPromises()
    const put = calls.find(c => c.url === '/api/integrations/vrops/dailycheck' && c.opts.method === 'PUT')!
    expect(put.opts.body).toEqual({ enabled: true })
    expect(toasts.map(t => t.m)).toContain('已開啟每日自動抓取')
    await w.get('[data-test="vrops-dc-run"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/integrations/vrops/dailycheck/run')!.opts.method).toBe('POST')
    expect(toasts.some(t => t.m.includes('已開始抓取') && t.t === 'success')).toBe(true)
  })

  it('開關關閉時「立即抓一次」按鈕 disabled，點了也不送 POST', async () => {
    const w = mnt(); await flushPromises()
    const b = w.get('[data-test="vrops-dc-run"]')
    expect(b.attributes('disabled')).toBeDefined()
    await b.trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/integrations/vrops/dailycheck/run')).toBe(false)
  })

  it('非管理員：開關鎖住、沒有立即抓一次按鈕，但看得到狀態', async () => {
    admin = false
    const w = mnt(); await flushPromises()
    expect(w.find('[data-test="vrops-dc-run"]').exists()).toBe(false)
    expect(w.get('input[aria-label="每日 VMware 自動抓取"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="vrops-dc-latest"]').text()).toContain('沒收到')
  })

  it('狀態讀取失敗：寫讀取失敗原因，不顯示成正常', async () => {
    const orig = fakeApi
    vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => (u === '/api/integrations/vrops/dailycheck' && !o?.method ? Promise.reject({ data: { detail: '模組未啟用' } }) : orig(u, o)) }))
    const w = mnt(); await flushPromises()
    expect(w.get('[data-test="vrops-dc-latest"]').text()).toContain('狀態讀取失敗（模組未啟用）')
    expect(w.get('[data-test="vrops-dc-latest"]').classes()).toContain('bad')
  })

  it('抓取中輪詢遇到一次暫時錯誤：保留舊狀態、退避後恢復並顯示完成', async () => {
    vi.useFakeTimers()
    try {
      let n = 0
      dcStatus = baseDc({ running: true })
      vi.stubGlobal('useApi', () => ({
        apiFetch: (u: string, o: any) => {
          if (u === '/api/integrations/vrops/dailycheck' && !o?.method) {
            n++
            if (n === 2) return Promise.reject({ data: { detail: '暫時錯誤' } })
            if (n >= 3) return Promise.resolve(baseDc({ running: false, last: { at: '2026-11-19 06:05:00', status: 'ok' }, today: { day: '2026-11-19', complete: true, collected_at: '2026-11-19 06:00:00', source: 'direct' } }))
          }
          return fakeApi(u, o)
        },
      }))
      const w = mnt(); await flushPromises()
      expect(w.get('[data-test="vrops-dc-run"]').text()).toContain('抓取中')
      await vi.advanceTimersByTimeAsync(5000)
      await flushPromises()
      expect(w.get('[data-test="vrops-dc-latest"]').text()).not.toContain('狀態讀取失敗')
      await vi.advanceTimersByTimeAsync(10000)
      await flushPromises()
      expect(w.get('[data-test="vrops-dc-latest"]').text()).toContain('已收到完整批次（系統直抓）')
      expect(w.get('[data-test="vrops-dc-run"]').text()).toContain('立即抓一次')
    } finally { vi.useRealTimers() }
  })

  it('放棄當日與登入被拒次數顯示在狀態列', async () => {
    dcStatus = baseDc({ enabled: true, gave_up_today: { reason: 'login_limit', message: '今天登入被拒已達 3 次' }, auth_rejects_today: 3, auth_limit: 3,
      last: { at: '2026-11-19 07:00:00', status: 'incomplete', error_class: 'auth' } })
    const w = mnt(); await flushPromises()
    const t = w.get('[data-test="vrops-dc-latest"]').text()
    expect(t).toContain('今天不會再自動重試')
    expect(t).toContain('登入被拒 3／3 次')
    expect(w.get('[data-test="vrops-dc-gaveup"]').text()).toContain('已達 3 次')
  })
})
