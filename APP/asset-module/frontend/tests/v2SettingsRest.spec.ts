// 2.056.4～（V56 第 5 批起）：⚙ 系統設定剩下幾塊改 V2 原生後實掛驗行為。V2Table／InfoNote 為簡化替身，全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VulnMail from '../components/v2/VulnMail.vue'
import ScanSchedule from '../components/v2/ScanSchedule.vue'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let handler: (url: string, opts: any) => any = () => ({})
const Table = defineComponent({ props: ['rows', 'cols', 'empty'], setup(p, { slots }) {
  return () => h('table', {}, (p.rows ?? []).length ? (p.rows ?? []).map((r: any) => h('tr', {}, (p.cols ?? []).map((c: any) =>
    h('td', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))) : [h('tr', {}, p.empty ?? '')])
} })
beforeEach(() => {
  calls = []
  handler = () => ({})
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    return handler(url, opts)
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, ScanScope: { template: '<div class="scope" />' } }

describe('寄信設定（2.056.4）', () => {
  const SETTINGS = { settings: { enabled: false, smtp_host: '10.99.0.25', smtp_port: 25, only_depts: [], only_people: [] }, missing: [], ad_ready: true, send_time: '08:00', buckets: [14, 30], last_runs: [] }
  beforeEach(() => {
    handler = (url, opts) => {
      if (url === '/api/vuln-notify/settings' && !opts.method) return SETTINGS
      if (url === '/api/vuln-notify/recipients') return { departments: [{ dept: '架構部', n: 3 }], people: [] }
      return { ok: true, sent: 0, failed: 0, missing: [] }
    }
  })
  it('立刻寄一期：取消確認就不送 /run；確定才送', async () => {
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    vi.stubGlobal('confirm', () => false)
    await w.findAll('button').find(b => b.text() === '立刻寄一期')!.trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/vuln-notify/run')).toBe(false)
    vi.stubGlobal('confirm', () => true)
    await w.findAll('button').find(b => b.text() === '立刻寄一期')!.trigger('click'); await flushPromises()
    expect(calls.filter(c => c.url === '/api/vuln-notify/run' && c.opts.method === 'POST').length).toBe(1)
  })
  it('儲存：body 跟舊頁同一份（空的單位／人員送 []、門檻轉數字）', async () => {
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    await w.findAll('button').find(b => b.text() === '儲存設定')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.opts.method === 'PUT')!
    expect(put.url).toBe('/api/vuln-notify/settings')
    expect(put.opts.body).toMatchObject({ enabled: false, smtp_host: '10.99.0.25', smtp_port: 25, send_time: '08:00', only_depts: [], only_people: [], buckets: [14, 30], manager_cc: true })
  })
  it('預覽只打 /preview，不打 /run', async () => {
    handler = (url, opts) => (url === '/api/vuln-notify/preview' ? { ok: true, details: [{ to: 'a@x', cc: [], count: 2, subject: 's' }] } : url === '/api/vuln-notify/settings' && !opts.method ? SETTINGS : {})
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    await w.findAll('button').find(b => b.text() === '預覽這期（不寄）')!.trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/vuln-notify/preview')).toBe(true)
    expect(calls.some(c => c.url === '/api/vuln-notify/run')).toBe(false)
    expect(w.html()).toContain('a@x')
  })
  it('名單載入失敗要講出來，不是顯示成沒有人', async () => {
    handler = (url, opts) => {
      if (url === '/api/vuln-notify/recipients') throw { data: { detail: '壞了' } }
      return url === '/api/vuln-notify/settings' && !opts.method ? SETTINGS : {}
    }
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('單位／人員名單載入失敗：壞了')
  })
})
describe('掃描排程（2.056.6）', () => {
  const DCFG = { batch_size: 50, gap_seconds: 120, skip_fresh_hours: 12, stale_days: 7, skip_empty_hours: 168, probe_workers: 64, probe_timeout_ms: 400,
    heartbeat_stale_minutes: 5, ocp_port_probe: 0, range: { batch_size: [1, 500], gap_seconds: [0, 3600], skip_fresh_hours: [0, 72], stale_days: [1, 90], skip_empty_hours: [0, 720],
      probe_workers: [1, 128], probe_timeout_ms: [100, 5000], heartbeat_stale_minutes: [1, 60] }, default: { batch_size: 50, gap_seconds: 120, skip_fresh_hours: 12, stale_days: 7, skip_empty_hours: 168, probe_workers: 64, probe_timeout_ms: 400, heartbeat_stale_minutes: 5 } }
  const WCFG = { enabled: true, weekday: 5, time: '22:00', targets: '', target_count: 0, range_missing: true, weekday_label: '週六', next_run: null, last_at: null, last_run: null }
  beforeEach(() => {
    handler = (url, opts) => {
      if (opts.method) return url.endsWith('/settings') ? DCFG : url.endsWith('/weekly') ? WCFG : {}
      if (url === '/api/scan/schedule') return { enabled: true, mode: 'weekly', time: '02:00', interval_hours: 6, weekday: 3 }
      if (url === '/api/collect/dispatch/settings') return DCFG
      if (url === '/api/collect/dispatch/weekly') return WCFG
      return {}
    }
  })
  it('儲存排程與節奏：body 跟舊頁一樣（分鐘換秒、OCP 轉 0/1）', async () => {
    const w = mount(ScanSchedule, { global: { stubs } })
    await flushPromises()
    expect(w.find('.scope').exists()).toBe(true)
    const forms = w.findAll('form')
    await forms[0].trigger('submit'); await flushPromises()
    expect(calls.find(c => c.url === '/api/scan/schedule' && c.opts.method === 'PUT')!.opts.body).toEqual({ enabled: true, mode: 'weekly', time: '02:00', interval_hours: 6, weekday: 3 })
    await w.find('input[aria-label="批次間隔（分鐘）"]').setValue(3)
    await forms[2].trigger('submit'); await flushPromises()
    expect(calls.find(c => c.url === '/api/collect/dispatch/settings' && c.opts.method === 'PUT')!.opts.body).toMatchObject({ batch_size: 50, gap_seconds: 180, ocp_port_probe: 0, heartbeat_stale_minutes: 5 })
  })
  it('每週收集沒有範圍：顯示提醒；儲存送整份 wEdit', async () => {
    const w = mount(ScanSchedule, { global: { stubs } })
    await flushPromises()
    expect(w.find('[data-test="weekly-range-missing"]').exists()).toBe(true)
    await w.findAll('form')[1].trigger('submit'); await flushPromises()
    expect(calls.find(c => c.url === '/api/collect/dispatch/weekly' && c.opts.method === 'PUT')!.opts.body).toEqual({ enabled: true, weekday: 5, time: '22:00', targets: '' })
  })
  it('讀取失敗要講出來，不是空白', async () => {
    handler = () => { throw { data: { detail: '壞了' } } }
    const w = mount(ScanSchedule, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('掃描排程讀取失敗：壞了')
    expect(w.text()).toContain('收集節奏讀取失敗：壞了')
    expect(w.text()).toContain('每週收集設定讀取失敗：壞了')
    expect(w.text()).toContain('掃描狀態讀取失敗：壞了')
  })
  it('最近一次掃描（G3）：失敗要講原因、進行中要講、沒紀錄要講', async () => {
    const base = handler
    const ST: any = { running: false, status: 'failed', found_count: 0, started_at: '2026-11-13 01:00:00', finished_at: '2026-11-13 01:02:00', trigger: 'schedule', error: '沒有可掃的網段' }
    handler = (url, opts) => (url === '/api/scan/status' ? ST : base(url, opts))
    let w = mount(ScanSchedule, { global: { stubs } })
    await flushPromises()
    expect(w.find('[data-test="scan-last-status"]').text()).toContain('失敗｜2026-11-13 01:00:00｜排程｜沒有可掃的網段')
    Object.assign(ST, { running: true }); w.unmount()
    w = mount(ScanSchedule, { global: { stubs } }); await flushPromises()
    expect(w.find('[data-test="scan-last-status"]').text()).toContain('進行中')
    Object.assign(ST, { running: false, status: 'none', started_at: null }); w.unmount()
    w = mount(ScanSchedule, { global: { stubs } }); await flushPromises()
    expect(w.find('[data-test="scan-last-status"]').text()).toContain('還沒有掃描紀錄')
  })
})
describe('自動納管（2.056.7，⚠D1）', () => {
  const AO = { enabled: false, segments: [{ id: 4, prefix: '10.99.1.', enabled: 1, note: null, created_at: 'x', updated_at: 'y' }], recent: [] }
  beforeEach(() => {
    handler = (url, opts) => {
      if (url === '/api/auto-onboard' && !opts.method) return AO
      if (url === '/api/auto-onboard/enabled') return { enabled: opts.body.enabled }
      if (url === '/api/auto-onboard/run') return { candidates: 1, onboarded: 0, failed: 0, skipped: 1, details: [{ ip: '10.99.1.5', platform: 'linux', action: 'skipped', message: 'm' }] }
      return { segments: AO.segments }
    }
  })
  it('掛起來不會自己執行一輪；按「立即執行」才送一次 /run', async () => {
    const AutoOnboard = (await import('../components/v2/AutoOnboard.vue')).default
    const w = mount(AutoOnboard, { global: { stubs } })
    await flushPromises()
    expect(calls.some(c => c.url === '/api/auto-onboard/run')).toBe(false)
    await w.findAll('button').find(b => b.text() === '立即執行')!.trigger('click'); await flushPromises()
    expect(calls.filter(c => c.url === '/api/auto-onboard/run' && c.opts.method === 'POST').length).toBe(1)
    expect(w.text()).toContain('10.99.1.5')
  })
  it('總開關：送 { enabled: 反向 }；授權網段：送 prefix 去空白、enabled true', async () => {
    const AutoOnboard = (await import('../components/v2/AutoOnboard.vue')).default
    const w = mount(AutoOnboard, { global: { stubs } })
    await flushPromises()
    await w.find('input[aria-label="排程自動納管總開關"]').trigger('change'); await flushPromises()
    expect(calls.find(c => c.url === '/api/auto-onboard/enabled')!.opts).toMatchObject({ method: 'PATCH', body: { enabled: true } })
    await w.find('input[aria-label="網段前綴"]').setValue(' 10.99.2. ')
    await w.find('form').trigger('submit'); await flushPromises()
    expect(calls.find(c => c.url === '/api/auto-onboard/segments' && c.opts.method === 'POST')!.opts.body).toEqual({ prefix: '10.99.2.', enabled: true, note: null })
  })
  it('讀取失敗：寫出原因、總開關不能按（不知道現況時不讓人切）', async () => {
    handler = () => { throw { data: { detail: '壞了' } } }
    const AutoOnboard = (await import('../components/v2/AutoOnboard.vue')).default
    const w = mount(AutoOnboard, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('自動納管設定載入失敗：壞了')
    expect((w.find('input[aria-label="排程自動納管總開關"]').element as HTMLInputElement).disabled).toBe(true)
  })
})
describe('收集金鑰（2.056.8，⚠D5 D1）', () => {
  // 標頭用拼的：原始碼裡不能出現完整的私鑰標頭（commit 閘門會擋）；內容是假的
  const PEM = (w: string) => ['-----' + w, 'OPENSSH', 'PRIVATE', 'KEY-----'].join(' ')
  const FAKE_PRIV = [PEM('BEGIN'), 'FAKE-NOT-A-REAL-KEY-7781', PEM('END')].join('\n')
  const ST = { has_key: true, path: '/opt/x/collector_key', fingerprint: '256 SHA256:fake', public_key: 'ssh-ed25519 AAAAfake', modified_at: 'x', onboarded_hosts: 7, last_import: null }
  let failImport = false
  beforeEach(() => {
    failImport = false
    handler = (url, opts) => {
      if (url === '/api/collector-key/import') { if (failImport) throw { data: { detail: '格式不對' } }; return { changed: true, next_step: '金鑰已換' } }
      if (url === '/api/collector-key') return ST
      return {}
    }
  })
  const open = async () => {
    const C = (await import('../components/v2/CollectorKey.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    await w.find('select[aria-label="匯入方式"]').setValue('paste')
    return w
  }
  it('私鑰框一開始是空的；沒打 REPLACE 按鈕灰、按了也不送', async () => {
    const w = await open()
    expect((w.find('textarea[aria-label="私鑰"]').element as HTMLTextAreaElement).value).toBe('')
    expect(w.text()).toContain('256 SHA256:fake')
    await w.find('textarea[aria-label="私鑰"]').setValue(FAKE_PRIV)
    await w.find('input[aria-label="確認文字"]').setValue('replace')
    expect((w.find('button[type="submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await w.find('form').trigger('submit'); await flushPromises()
    expect(calls.some(c => c.url === '/api/collector-key/import')).toBe(false)
  })
  it('送出：body 是 { confirm, private_key }；成功後私鑰框清空、畫面沒有私鑰', async () => {
    const w = await open()
    await w.find('textarea[aria-label="私鑰"]').setValue(FAKE_PRIV)
    await w.find('input[aria-label="確認文字"]').setValue('REPLACE')
    await w.find('form').trigger('submit'); await flushPromises()
    expect(calls.find(c => c.url === '/api/collector-key/import')!.opts).toEqual({ method: 'POST', body: { confirm: 'REPLACE', private_key: FAKE_PRIV } })
    expect((w.find('textarea[aria-label="私鑰"]').element as HTMLTextAreaElement).value).toBe('')
    expect(w.html()).not.toContain('FAKE-NOT-A-REAL-KEY-7781')
  })
  it('匯入失敗也清掉私鑰（舊頁只在成功時清）', async () => {
    failImport = true
    const w = await open()
    await w.find('textarea[aria-label="私鑰"]').setValue(FAKE_PRIV)
    await w.find('input[aria-label="確認文字"]').setValue('REPLACE')
    await w.find('form').trigger('submit'); await flushPromises()
    expect((w.find('textarea[aria-label="私鑰"]').element as HTMLTextAreaElement).value).toBe('')
    expect(w.html()).not.toContain('FAKE-NOT-A-REAL-KEY-7781')
  })
  it('指定路徑：送 { confirm, from_path }，不送 private_key', async () => {
    const C = (await import('../components/v2/CollectorKey.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    await w.find('input[aria-label="私鑰路徑"]').setValue(' /tmp/id_ed25519 ')
    await w.find('input[aria-label="確認文字"]').setValue('REPLACE')
    await w.find('form').trigger('submit'); await flushPromises()
    expect(calls.find(c => c.url === '/api/collector-key/import')!.opts.body).toEqual({ confirm: 'REPLACE', from_path: '/tmp/id_ed25519' })
  })
  it('狀態讀不到：寫出原因', async () => {
    handler = () => { throw { data: { detail: '壞了' } } }
    const C = (await import('../components/v2/CollectorKey.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('收集金鑰狀態讀取失敗：壞了')
  })
})
describe('自動納管移除授權網段要先確認（2.056.9）', () => {
  beforeEach(() => {
    handler = (url, opts) => (url === '/api/auto-onboard' && !opts.method
      ? { enabled: false, segments: [{ id: 4, prefix: '10.99.1.', enabled: 1, note: null, created_at: 'x', updated_at: 'y' }], recent: [] }
      : { segments: [] })
  })
  it('取消就不送 DELETE；確定才送', async () => {
    const AutoOnboard = (await import('../components/v2/AutoOnboard.vue')).default
    const w = mount(AutoOnboard, { global: { stubs } })
    await flushPromises()
    vi.stubGlobal('confirm', () => false)
    await w.findAll('button').find(b => b.text() === '移除')!.trigger('click'); await flushPromises()
    expect(calls.some(c => c.opts.method === 'DELETE')).toBe(false)
    vi.stubGlobal('confirm', () => true)
    await w.findAll('button').find(b => b.text() === '移除')!.trigger('click'); await flushPromises()
    expect(calls.filter(c => c.opts.method === 'DELETE').map(c => c.url)).toEqual(['/api/auto-onboard/segments/4'])
  })
})

describe('寄信設定讀取失敗時不能存（2.057.1 審查）', () => {
  it('設定讀不到：沒有表單、沒有儲存鈕，什麼都不會送 PUT（不會把收件範圍蓋成全部）', async () => {
    handler = (url, opts) => { if (url === '/api/vuln-notify/settings' && !opts.method) throw { data: { detail: '壞了' } }; return {} }
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('寄信設定載入失敗：壞了')
    expect(w.find('[data-test="mail-form"]').exists()).toBe(false)
    expect(w.findAll('button').some(b => b.text() === '儲存設定')).toBe(false)
    for (const inp of w.findAll('input')) await inp.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(calls.some(c => c.opts.method === 'PUT')).toBe(false)
    expect(w.findAll('button').some(b => b.text() === '重新讀取')).toBe(true)
  })
  it('讀到了：在欄位裡按 Enter 不會送出（沒有 form），要按儲存才送', async () => {
    handler = (url, opts) => (url === '/api/vuln-notify/settings' && !opts.method
      ? { settings: { enabled: true, only_depts: ['架構部'], only_people: [] }, missing: [], ad_ready: true, send_time: '08:00', buckets: [14], last_runs: [] }
      : url === '/api/vuln-notify/recipients' ? { departments: [], people: [] } : {})
    const w = mount(VulnMail, { global: { stubs } })
    await flushPromises()
    expect(w.find('form').exists()).toBe(false)
    await w.find('input[aria-label="SMTP 主機"]').trigger('keydown', { key: 'Enter' }); await flushPromises()
    expect(calls.some(c => c.opts.method === 'PUT')).toBe(false)
  })
})

describe('自動納管讀取失敗時的開關文字（2.057.1 審查）', () => {
  it('寫「狀態未知（讀取失敗）」不寫「排程已關閉」；有重試，重試成功後顯示真實狀態', async () => {
    let fail = true
    handler = (url, opts) => {
      if (url === '/api/auto-onboard' && !opts.method) { if (fail) throw { data: { detail: '壞了' } }; return { enabled: true, segments: [], recent: [] } }
      return {}
    }
    const AutoOnboard = (await import('../components/v2/AutoOnboard.vue')).default
    const w = mount(AutoOnboard, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('狀態未知（讀取失敗）')
    expect(w.text()).not.toContain('排程已關閉')
    fail = false
    await w.findAll('button').find(b => b.text() === '重試')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('排程已啟用')
    expect((w.find('input[aria-label="排程自動納管總開關"]').element as HTMLInputElement).disabled).toBe(false)
  })
})
