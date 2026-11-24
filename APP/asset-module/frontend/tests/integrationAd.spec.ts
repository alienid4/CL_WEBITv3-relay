// AD 人員名單（LDAPS 每日同步）畫面實掛：連接埠固定 636 不可輸入、儲存送對的內容（不帶密碼）、測試連線／立即同步走對的端點、
// 鮮度燈有文字、紀錄可點開看 DC 錯誤、憑證下拉只列 ldap 類型。Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote／NuxtLink 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/IntegrationAd.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
const toasts: { m: string; t: string }[] = []
let cfg: any
let runs: any[] = []
let testReply: any = { ok: true, message: '連線成功（10.99.0.1），AD 回報 120 個人員帳號，其中啟用 100' }
let runReply: any = { status: 'ok', message: '同步 120 人（原 118 人）' }

const fresh = (o: any = {}) => ({ status: 'ok', label: '正常', last_ok_at: '2026-11-18 07:00:05', age_hours: 5.5, stale_days: 2, last_status: 'ok', last_message: '', blocked: null, ...o })
const baseCfg = () => ({
  hosts: [{ host: '10.99.0.1', cert_name: 'dc01.corp.example.test' }, { host: 'dc02.corp.example.test', cert_name: '' }], port: 636,
  base_dn: 'DC=corp,DC=example,DC=test', ca_bundle: '/etc/pki/ca.pem', enabled: true, run_at: '07:00', credential: 'ad-svc',
  username: 'webit3sc@corp.example.test', has_password: true, stale_days: 2, freshness: fresh(), last_run: null,
  list: { total: 120, batch: { imported_at: '2026-11-18 07:00:05', file_name: 'LDAPS 10.99.0.1', imported_by: 'ad-sync:schedule' } },
})
const run = (o: any = {}) => ({ id: 1, started_at: '2026-11-18 07:00:00', finished_at: '2026-11-18 07:00:05', trigger: 'schedule', started_by: null, status: 'ok', dc: '10.99.0.2',
  entries: 121, rows: 120, enabled_n: 100, disabled_n: 20, mgr_ok: 90, mgr_fail: 3, mgr_dupname: 1, prev_total: 118, message: '同步 120 人', detail: { errors: ['10.99.0.1：連不上或 TLS 驗證失敗：timed out'] }, ...o })

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  const m = o.method ?? 'GET'
  if (u === '/api/integrations/ad' && m === 'GET') return Promise.resolve(cfg)
  if (u === '/api/integrations/ad' && m === 'PUT') return Promise.resolve(cfg)
  if (u === '/api/integrations/ad/runs') return Promise.resolve({ runs })
  if (u === '/api/integrations/ad/test') return Promise.resolve(testReply)
  if (u === '/api/integrations/ad/run') return Promise.resolve(runReply)
  if (u === '/api/credentials') return Promise.resolve([{ name: 'ad-svc', kind: 'ldap', username: 'webit3sc@corp.example.test' }, { name: 'aria', kind: 'vrops', username: 'svc' }])
  return Promise.resolve({ ok: true })
}

const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p) {
    return () => (p.rows.length
      ? h('table', { class: 'tbl', 'data-prefix': p.prefix }, p.rows.map((r: any) => h('tr', { class: 'row' }, p.cols.map((c: any) => h('td', { class: 'c-' + c.k }, String(r[c.k] ?? '—'))))))
      : h('p', { class: 'empty' }, p.empty))
  },
})

beforeEach(() => {
  calls.length = 0; toasts.length = 0
  cfg = baseCfg(); runs = [run()]
  testReply = { ok: true, message: '連線成功（10.99.0.1），AD 回報 120 個人員帳號，其中啟用 100' }
  runReply = { status: 'ok', message: '同步 120 人（原 118 人）' }
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string, t: string) => toasts.push({ m, t }) }))
})

const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, V2Table, NuxtLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } } })
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text() === t)

describe('AD 人員名單同步畫面', () => {
  it('載入設定：DC 一行一台（含憑證主機名）、連接埠固定 636 沒有輸入框', async () => {
    const w = mnt(); await flushPromises()
    expect((w.find('textarea[aria-label=網域控制站]').element as HTMLTextAreaElement).value).toBe('10.99.0.1 dc01.corp.example.test\ndc02.corp.example.test')
    expect(w.find('[data-test=port]').text()).toContain('636')
    expect(w.find('input[aria-label=連接埠]').exists()).toBe(false)
    expect(w.text()).not.toMatch(/略過驗證|skip verify|不驗證/)
  })

  it('憑證下拉只列 ldap 類型', async () => {
    const w = mnt(); await flushPromises()
    const opts = w.find('select[aria-label=收集憑證]').findAll('option').map((o: any) => o.text())
    expect(opts).toEqual(['（請選擇）', 'ad-svc（webit3sc@corp.example.test）'])
    expect(w.find('select[aria-label=收集憑證]').findAll('option')[0].attributes('disabled')).toBeDefined()   // 不能選「自動」（後端不收）
  })

  it('儲存送 PUT，內容沒有密碼欄', async () => {
    const w = mnt(); await flushPromises()
    await w.find('input[aria-label="Base DN"]').setValue('DC=corp2,DC=example,DC=test')
    await btn(w, '儲存設定')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/integrations/ad' && c.opts.method === 'PUT')!
    expect(put.opts.body).toEqual({ hosts: '10.99.0.1 dc01.corp.example.test\ndc02.corp.example.test', base_dn: 'DC=corp2,DC=example,DC=test',
      ca_bundle: '/etc/pki/ca.pem', enabled: true, run_at: '07:00', credential: 'ad-svc' })
    expect(Object.keys(put.opts.body).some(k => /pass|secret|token|port/i.test(k))).toBe(false)
    expect(toasts.map(t => t.m)).toContain('已儲存設定')
  })

  it('測試連線走 POST /test，結果顯示在旁邊；失敗也顯示原因', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '測試連線')!.trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/integrations/ad/test')!.opts.method).toBe('POST')
    expect(w.find('[data-test=test-result]').text()).toContain('連線成功')
    testReply = { ok: false, message: '帳號或密碼被 AD 拒絕（密碼錯誤）' }
    await btn(w, '測試連線')!.trigger('click'); await flushPromises()
    expect(w.find('[data-test=test-result]').text()).toContain('連線失敗：帳號或密碼被 AD 拒絕')
  })

  it('立即同步走 POST /run；被少兩成擋下用警告提示', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '立即同步')!.trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/integrations/ad/run')!.opts.method).toBe('POST')
    expect(toasts.at(-1)).toEqual({ m: '同步 120 人（原 118 人）', t: 'success' })
    runReply = { status: 'refused', message: '新名單 70 人，比現有 100 人少超過兩成，整批不收' }
    await btn(w, '立即同步')!.trigger('click'); await flushPromises()
    expect(toasts.at(-1)!.t).toBe('warn')
  })

  it('鮮度燈有文字（不只靠顏色）：正常／過期／沒收到', async () => {
    let w = mnt(); await flushPromises()
    expect(w.find('[data-test=lamp]').text()).toContain('正常')
    cfg = { ...baseCfg(), freshness: fresh({ status: 'stale', label: '過期', age_hours: 60 }) }
    w = mnt(); await flushPromises()
    expect(w.find('[data-test=lamp]').text()).toContain('過期')
    expect(w.find('[data-test=lamp]').classes()).toContain('stale')
    cfg = { ...baseCfg(), freshness: fresh({ status: 'never', label: '沒收到', last_ok_at: null, age_hours: null }) }
    w = mnt(); await flushPromises()
    expect(w.find('[data-test=lamp]').text()).toContain('沒收到')
    expect(w.text()).toContain('上次成功 —')
  })

  it('目前名單人數可點進資料匯入的 AD 名單', async () => {
    const w = mnt(); await flushPromises()
    const a = w.find('[data-test=list-link]')
    expect(a.text()).toContain('120 人')
    expect(a.attributes('href')).toBe('/v2/settings?st=import&src=ad')
  })

  it('同步紀錄表有數字，點某一次看 DC 錯誤明細，再點收起', async () => {
    const w = mnt(); await flushPromises()
    expect(w.find('.c-rows').text()).toBe('120')
    expect(w.find('.c-mgr').text()).toBe('90／3／1')
    expect(w.find('.c-st').text()).toBe('成功')
    const pick = w.findAll('button.mini')[0]
    await pick.trigger('click')
    expect(w.find('[data-test=run-detail]').text()).toContain('10.99.0.1：連不上')
    await pick.trigger('click')
    expect(w.find('[data-test=run-detail]').exists()).toBe(false)
  })

  it('載入失敗會顯示錯誤，不是空白', async () => {
    vi.stubGlobal('useApi', () => ({ apiFetch: () => Promise.reject({ data: { detail: '只有 admin 帳號能設定或執行AD 人員名單同步' } }) }))
    const w = mnt(); await flushPromises()
    expect(w.find('.err').text()).toContain('只有 admin')
  })

  it('有未儲存的變更時提示：測試用的是已儲存設定；存完提示消失', async () => {
    const w = mnt(); await flushPromises()
    expect(w.find('[data-test=dirty]').exists()).toBe(false)
    await w.find('input[aria-label="Base DN"]').setValue('DC=other,DC=example,DC=test')
    expect(w.find('[data-test=dirty]').text()).toContain('已儲存的設定')
    cfg = { ...baseCfg(), base_dn: 'DC=other,DC=example,DC=test' }
    await btn(w, '儲存設定')!.trigger('click'); await flushPromises()
    expect(w.find('[data-test=dirty]').exists()).toBe(false)
  })

  it('測試連線回 0 筆：顯示警告', async () => {
    testReply = { ok: true, message: '連線成功（10.99.0.1），抽查讀到 0 筆', warning: 'Base DN 底下查不到任何人員帳號' }
    const w = mnt(); await flushPromises()
    await btn(w, '測試連線')!.trigger('click'); await flushPromises()
    expect(w.find('[data-test=test-warning]').text()).toContain('查不到任何人員帳號')
  })

  it('燈號旁顯示生效名單的來源與資料時間；帳號被暫停時顯示原因', async () => {
    let w = mnt(); await flushPromises()
    expect(w.find('[data-test=list-source]').text()).toBe('生效名單來源：LDAPS 10.99.0.1・2026-11-18 07:00:05・ad-sync:schedule')
    expect(w.find('[data-test=blocked]').exists()).toBe(false)
    cfg = { ...baseCfg(), freshness: fresh({ status: 'failed', label: '失敗', blocked: '服務帳號被暫停使用（避免鎖帳）：帳號已鎖定' }) }
    w = mnt(); await flushPromises()
    expect(w.find('[data-test=lamp]').text()).toContain('失敗')
    expect(w.find('[data-test=blocked]').text()).toContain('帳號已鎖定')
  })
})
