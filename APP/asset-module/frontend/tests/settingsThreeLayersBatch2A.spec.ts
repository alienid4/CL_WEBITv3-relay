// 第 2 批 A 組（三層分離）：寄信設定／每日摘要／EOS 通知／Aria／WhatsUp／儲存設備。
// 驗：預設畫面有「最近一次狀態」（成功／失敗／沒收到不被埋）、診斷內容收在 <details>（預設關）、功能按鈕仍在。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VulnMail from '../components/v2/VulnMail.vue'
import DigestMailSettings from '../components/DigestMailSettings.vue'
import EosNotifySettings from '../components/EosNotifySettings.vue'
import IntegrationVrops from '../components/v2/IntegrationVrops.vue'
import IntegrationWhatsup from '../components/v2/IntegrationWhatsup.vue'

enableAutoUnmount(afterEach)
let handler: (url: string, opts: any) => any = () => ({})
const Table = defineComponent({ props: ['rows', 'cols', 'empty'], setup(p) { return () => h('table', { class: 'tbl' }, String((p.rows ?? []).length)) } })
beforeEach(() => {
  handler = () => ({})
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => handler(url, opts) }))
  vi.stubGlobal('useRoute', () => ({ query: {} }))
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(true), canWrite: ref(true) }))
  vi.stubGlobal('useSort', (rows: any) => ({ sortKey: ref('no'), sortDir: ref('asc'), toggle: () => {}, sorted: rows }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, SortTh: { template: '<th><slot /></th>' } }
const isClosed = (w: any, sel: string) => (w.find(sel).element as HTMLDetailsElement).open === false

describe('寄信設定', () => {
  it('最近一期結果預設可見；預覽／立刻寄／歷史在收合區', async () => {
    handler = (url, opts) => url === '/api/vuln-notify/settings' && !opts.method
      ? { settings: { enabled: true }, missing: [], ad_ready: true, send_time: '08:00', buckets: [14],
          last_runs: [{ run_date: '2026-11-09', schedule: 'weekly', started_at: '2026-11-09 08:00', config_ok: 1, sent: 5, failed: 0, reason: null },
                      { run_date: '2026-11-16', schedule: 'weekly', started_at: '2026-11-16 08:00', config_ok: 1, sent: 3, failed: 2, reason: null }] }
      : url === '/api/vuln-notify/recipients' ? { departments: [], people: [] } : {}
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    const st = w.get('[data-test="mail-last"]')
    expect(st.text()).toContain('2026-11-16')              // 取最新一期，不是清單第一筆
    expect(st.text()).toContain('失敗 2')
    expect(st.classes()).toContain('bad')
    expect(isClosed(w, '[data-test="mail-adv"]')).toBe(true)
    expect(isClosed(w, '[data-test="mail-scope"]')).toBe(true)
    expect(w.get('[data-test="mail-adv"]').text()).toContain('立刻寄一期')
    expect(w.get('[data-test="mail-scope"]').text()).toContain('只通知這些單位')
  })
  it('沒跑過一期：寫「還沒有任何一期跑過」，不顯示成 0', async () => {
    handler = (url, opts) => url === '/api/vuln-notify/settings' && !opts.method ? { settings: { enabled: false }, missing: [], ad_ready: true, buckets: [], last_runs: [] } : { departments: [], people: [] }
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    expect(w.get('[data-test="mail-last"]').text()).toContain('還沒有任何一期跑過')
  })
})

describe('每日異動摘要信', () => {
  const logs = [
    { id: 1, run_date: '2026-11-17', kind: 'schedule', sent_at: '2026-11-17 07:30', recipients: 'a@x', subject: null, status: 'sent', detail: '250 ok', smtp_host: null },
    { id: 2, run_date: '2026-11-18', kind: 'schedule', sent_at: '2026-11-18 07:30', recipients: 'a@x', subject: null, status: 'failed', detail: '550 denied', smtp_host: null },
  ]
  it('預設只顯示最近一次結果；紀錄與 SMTP 回應在收合區', async () => {
    handler = (url) => url === '/api/digest/settings' ? { settings: { mail_enabled: true }, corrupt: null, missing: [], can_edit: true } : url === '/api/digest/mail-log' ? { items: logs } : {}
    const w = mount(DigestMailSettings, { global: { stubs } })
    await flushPromises()
    const st = w.get('[data-test="digest-last"]')
    expect(st.text()).toContain('失敗')
    expect(st.text()).toContain('2026-11-18')
    expect(st.text()).not.toContain('550')                   // 伺服器回應原文不在預設畫面
    expect(isClosed(w, '[data-test="digest-log"]')).toBe(true)
    expect(w.get('[data-test="digest-log"]').text()).toContain('550 denied')
  })
  it('紀錄載入失敗：狀態列講出來，不當成「沒有紀錄」', async () => {
    handler = (url) => { if (url === '/api/digest/mail-log') throw { data: { detail: '壞了' } }; return url === '/api/digest/settings' ? { settings: {}, corrupt: null, missing: [], can_edit: true } : {} }
    const w = mount(DigestMailSettings, { global: { stubs } })
    await flushPromises()
    expect(w.get('[data-test="digest-last"]').text()).toContain('載入失敗')
  })
})

describe('EOS 到期通知（設定頁）', () => {
  it('設定與儲存在外面；試寄、立刻寄、執行清單在收合區；試寄中橫幅仍可見', async () => {
    handler = (url) => url === '/api/eos-notify/settings'
      ? { settings: { enabled: true, trial_to: 't@x' }, corrupt: null, missing: [], ad_ready: true, send_time: '08:00', buckets: [180, 365], can_edit: true,
          last_runs: [{ id: 1, started_at: '2026-11-01 08:00', schedule: 'monthly', sent: 3, failed: 1, reason: null }] }
      : url === '/api/eos-notify/preview' ? { ok: true, buckets: [180, 365], groups: [], orphans: [], stats: {} } : url === '/api/eos-notify/logs' ? { items: [] } : url === '/api/eos-notify/assign' ? { rules: [], devclasses: [] } : {}
    const w = mount(EosNotifySettings, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('試寄中：全部改寄 t@x')
    expect(w.get('[data-test="eos-last"]').text()).toContain('失敗 1')
    expect(isClosed(w, '[data-test="eos-adv"]')).toBe(true)
    const adv = w.get('[data-test="eos-adv"]')
    expect(adv.text()).toContain('立刻寄一期')
    expect(adv.text()).toContain('試寄給')
    expect(adv.text()).toContain('最近執行')
    expect(w.findAll('button').some(b => b.text() === '儲存')).toBe(true)
  })
})

describe('Aria／WhatsUp', () => {
  it('Aria：最新一批狀態可見（含沒收到），紀錄表與錯誤明細在收合區', async () => {
    handler = (url) => url === '/api/integrations/vrops' ? { url: '', auth_source: '', ca_bundle: '', enabled: false, interval_hours: 24, credential: '' }
      : url === '/api/integrations/vrops/runs' ? { runs: [
        { id: 1, started_at: '2026-11-17 03:00:00', trigger: 'schedule', status: 'ok', vms: 10, hosts: 2, error_count: 0 },
        { id: 2, started_at: '2026-11-18 03:00:00', trigger: 'schedule', status: 'partial', vms: 9, hosts: 2, error_count: 2, detail: { errors: ['x'] } }] } : []
    const w = mount(IntegrationVrops, { global: { stubs } })
    await flushPromises()
    expect(w.get('[data-test="vrops-latest"]').text()).toContain('部分成功')
    expect(w.get('[data-test="vrops-latest"]').text()).toContain('2026-11-18')
    expect(isClosed(w, '[data-test="vrops-hist"]')).toBe(true)
    expect(w.get('[data-test="vrops-hist"]').text()).toContain('看某一次的錯誤明細')
    expect(w.findAll('button').some(b => b.text() === '連線測試')).toBe(true)
    handler = (url) => url === '/api/integrations/vrops' ? { url: '', auth_source: '', ca_bundle: '', enabled: false, interval_hours: 24, credential: '' } : url.endsWith('/runs') ? { runs: [] } : []
    const w2 = mount(IntegrationVrops, { global: { stubs } })
    await flushPromises()
    expect(w2.get('[data-test="vrops-latest"]').text()).toContain('沒收到')
  })
  it('WhatsUp：最新一次輪詢可見；紀錄表在收合區', async () => {
    handler = (url) => url === '/api/integrations/whatsup' ? { url: '', ca_bundle: '', enabled: true, interval_min: 5, credential: '' }
      : url.endsWith('/runs') ? { rows: [{ at: '2026-11-18 22:00:00', trigger: 'schedule', status: 'failed', message: 'HTTP 503' }] } : []
    const w = mount(IntegrationWhatsup, { global: { stubs } })
    await flushPromises()
    expect(w.get('[data-test="wug-latest"]').text()).toContain('失敗')
    expect(w.get('[data-test="wug-latest"]').classes()).toContain('bad')
    expect(isClosed(w, '[data-test="wug-hist"]')).toBe(true)
    expect(w.findAll('button').some(b => b.text() === '立即輪詢')).toBe(true)
  })
})
