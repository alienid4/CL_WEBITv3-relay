/**
 * 2.048.21 V25：8.2 組態檢核改 V2 原生，實掛：
 *   - 卡片／平台表每格下鑽在網址（cd／cp／cr）；cd=untrust 同時列「未查到」條目；條目下鑽舊回應不覆蓋
 *   - 單機明細 ch／cf：認不得的結果值算「未查到」（跟後端 result_bucket 同一條規則）
 *   - 匯入、匯出、腳本下載限管理員（後端 ADMIN_WRITE／_ADMIN_EXPORTS）
 * V34：4.1 批次健檢改 V2 原生：執行與建立基準限管理員；整段帶入／建立基準都先確認，取消不送；輸入剝成純主機；方塊篩選
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import ConfigAudit from '../components/v2/ConfigAudit.vue'
import HealthCheck from '../components/v2/HealthCheck.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const route = reactive<{ query: Record<string, any> }>({ query: {} })
const auth = { isAdmin: false }
let confirmAns = true
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = false; confirmAns = true
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(true), isViewer: ref(false) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
  window.confirm = (() => confirmAns) as any
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}`, 'data-link': c.link ? JSON.stringify(c.link(r)) ?? '' : '' },
      slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': JSON.stringify(p.to) }, slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: Link, V2HealthDetail: pass } } }
const flush = async (fn: (p: any) => any) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))
const link = (w: any, sel: string) => JSON.parse(w.find(sel).attributes('data-link')!).query

const RUNS = [
  { id: 5, hostname: 'bbb', ip: '10.99.0.2', platform: 'AIX', asset_serial: null, trustworthy: false, completeness: '完整', total: 3, compliant: 1, non_compliant: 0, not_applicable: 0, error: 2, coverage: 100 },
  { id: 7, hostname: 'aaa', ip: '10.99.0.1', platform: 'Linux', asset_serial: 'S1', trustworthy: true, completeness: '無完成標記', total: 2, compliant: 1, non_compliant: 1, not_applicable: 0, error: 0, coverage: null },
  { id: 9, hostname: 'ccc', ip: '10.99.0.9', platform: 'AIX', asset_serial: 'S9', trustworthy: false, completeness: '完整', total: 1, compliant: 0, non_compliant: 0, not_applicable: 0, error: 1, coverage: null }]
const SUM = { hosts: 2, untrustworthy: 1, unmatched: 1, no_end_marker: 1,
  by_platform: [{ platform: 'AIX', hosts: 1, Compliant: 1, 'Non-Compliant': 0, 'Not-Applicable': 0, Error: 2, coverage: 100, coverage_formula: '1/1' }] }
const caReply = (p: any) => (p.url === '/api/config-audit' ? { items: RUNS, summary: SUM } : p.url === '/api/scripts' ? { items: [{ name: 'fcb_aix.ksh', platform: 'AIX', size: 2048, script_version: null }] } : { items: [] })

describe('組態檢核（V25）', () => {
  it('卡片與平台表每格下鑽在網址；項次照主機名排、合規率算不出來顯示「未知」', async () => {
    const w = mount(ConfigAudit, opts)
    await flush(caReply)
    expect(JSON.parse(w.find('[data-test="ca-card-untrust"]').attributes('data-to')!).query).toEqual({ tab: 'config', ct: 'overview', cd: 'untrust' })
    expect(link(w, '[data-prefix="P"] .c-c_err')).toEqual({ tab: 'config', ct: 'overview', cd: 'items', cp: 'AIX', cr: 'Error' })
    expect(link(w, '[data-prefix="P"] .c-hosts')).toEqual({ tab: 'config', ct: 'overview', cd: 'platform', cp: 'AIX' })
    route.query = { tab: 'config', ct: 'hosts' }
    await flushPromises()
    const rows = w.findAll('.row')
    expect(rows[0].find('.c-host_t').text()).toBe('aaa')                    // 主機名 A→Z
    expect(rows[0].find('.c-coverage').text()).toBe('未知')
    expect(JSON.parse(rows[1].find('.c-error').attributes('data-link')!).query).toEqual({ tab: 'config', ct: 'hosts', ch: '5', cf: 'Error' })
  })
  it('cd=untrust：主機篩不可信＋撈「未查到」條目（不帶平台）；換成 cd=items 後舊回應不覆蓋', async () => {
    route.query = { tab: 'config', cd: 'untrust' }
    const w = mount(ConfigAudit, opts)
    await flushPromises()
    const old = pending.find(p => p.url === '/api/config-audit/items')!
    expect(old.opts.query).toEqual({ result: 'Error' })
    for (const p of pending.filter(x => x !== old).splice(0)) p.resolve(caReply(p))
    pending = [old]
    await flushPromises()
    expect(w.find('[data-prefix="H"]').findAll('.row').map(r => r.find('.c-host_t').text())).toEqual(['bbb', 'ccc'])   // 只有不可信那兩台
    route.query = { tab: 'config', cd: 'items', cp: 'Linux', cr: 'Non-Compliant' }
    await flushPromises()
    const cur = pending.find(p => p !== old)!
    expect(cur.opts.query).toEqual({ result: 'Non-Compliant', platform: 'Linux' })
    cur.resolve({ items: [{ run_id: 7, seq: 1, hostname: 'aaa', bucket: 'Non-Compliant', item_id: 'X1' }] })
    old.resolve({ items: [{ run_id: 5, seq: 1, hostname: 'bbb', bucket: 'Error', item_id: 'OLD' }] })
    await flushPromises()
    expect(w.text()).toContain('X1')
    expect(w.text()).not.toContain('OLD')
  })
  it('單機明細 cf=Error：認不得的結果值也算「未查到」', async () => {
    route.query = { tab: 'config', ct: 'hosts', ch: '5', cf: 'Error' }
    const w = mount(ConfigAudit, opts)
    await flush(p => (p.url === '/api/config-audit/5'
      ? { hostname: 'bbb', platform: 'AIX', items: [{ seq: 1, item_id: 'A', result: 'Compliant' }, { seq: 2, item_id: 'B', result: 'Error' }, { seq: 3, item_id: 'C', result: '怪值' }] }
      : caReply(p)))
    const ids = w.findAll('.c-item_id').map(x => x.text())
    expect(ids).toEqual(['B', 'C'])
  })
  it('匯入、匯出、腳本下載限管理員', async () => {
    route.query = { tab: 'config', ct: 'import' }
    let w = mount(ConfigAudit, opts)
    await flush(caReply)
    expect(w.find('input[type="file"]').exists()).toBe(false)
    expect(w.text()).toContain('匯入與匯出限管理員')
    expect(btn(w, '下載')).toBeUndefined()
    expect(w.text()).toContain('腳本裡沒寫版本')
    w.unmount()
    auth.isAdmin = true
    w = mount(ConfigAudit, opts)
    await flush(caReply)
    expect(w.find('input[type="file"]').exists()).toBe(true)
    expect(w.findAll('a').some(a => (a.attributes('href') ?? '').endsWith('/api/config-audit/export'))).toBe(true)
    expect(btn(w, '下載')).toBeTruthy()
  })
})

const RES = [
  { ip: '10.99.0.3', overall: 'green', reachable: true, disks: [], failed_units: [], ports: [], notes: [], complete: true },
  { ip: '10.99.0.4', overall: 'red', reachable: false, error: '連不上', disks: [], failed_units: [], ports: [], notes: [] },
  { ip: '10.99.0.5', overall: 'skipped', reachable: false, not_probed: '沒有憑證', disks: [], failed_units: [], ports: [], notes: [] }]

describe('批次健檢（V34）', () => {
  it('只能看／一般帳號沒有執行與建立基準', async () => {
    const w = mount(HealthCheck, opts)
    expect(btn(w, '執行自我檢查')).toBeUndefined()
    expect(btn(w, '建立健康基準')).toBeUndefined()
    expect(w.text()).toContain('限管理員')
  })
  it('管理員執行：送剝乾淨的 IP 與並發；結果異常排前面、點方塊只看那一類、沒去成另標', async () => {
    auth.isAdmin = true
    const w = mount(HealthCheck, opts)
    await w.find('textarea').setValue('http://10.99.0.3/\nroot@10.99.0.4:22, 10.99.0.3')
    await btn(w, '執行自我檢查').trigger('click')
    const run = pending.find(p => p.url === '/api/health-check/run')!
    expect(run.opts).toMatchObject({ method: 'POST', body: { ips: ['10.99.0.3', '10.99.0.4'], concurrency: 8 } })
    run.resolve({ items: RES, summary: { total: 3, red: 1, yellow: 0, green: 1, skipped: 1, unreachable: 1, not_probed: 1 } })
    await flushPromises()
    expect(w.findAll('.c-ip').map(x => x.text())).toEqual(['10.99.0.4', '10.99.0.3', '10.99.0.5'])
    expect(w.findAll('.c-sev')[2].text()).toContain('沒去成')
    await w.findAll('button.scard').find(b => b.text().includes('正常'))!.trigger('click')
    expect(w.findAll('.c-ip').map(x => x.text())).toEqual(['10.99.0.3'])
    await w.findAll('button.scard').find(b => b.text().includes('全部'))!.trigger('click')
    expect(w.findAll('.c-ip').length).toBe(3)
  })
  it('整段帶入先確認，取消不帶入；建立基準先確認，取消不送', async () => {
    auth.isAdmin = true
    const w = mount(HealthCheck, opts)
    await w.find('input[aria-label="網段"]').setValue('10.99.1.0/24')
    confirmAns = false
    await btn(w, '查這段已登記的').trigger('click')
    const t = pending.splice(0)[0]
    expect(t.url).toBe('/api/health-check/targets')
    expect(t.opts.query).toEqual({ cidr: '10.99.1.0/24' })
    t.resolve({ count: 1, items: [{ ip: '10.99.1.9' }] })
    await flushPromises()
    expect((w.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    await btn(w, '建立健康基準').trigger('click')
    expect(pending.length).toBe(0)
    confirmAns = true
    await btn(w, '建立健康基準').trigger('click')
    expect(pending[0]).toMatchObject({ url: '/api/health-check/baseline/run', opts: { method: 'POST' } })
  })
})

describe('第 11 批審查補測', () => {
  it('三個新元件的 <style> 編譯沒有錯（2.048.22：搬 CSS 留下沒有選擇器的碎片會讓 build 失敗）', async () => {
    const { parse, compileStyle } = await import('@vue/compiler-sfc')
    const fs = await import('node:fs')
    for (const f of ['ConfigAudit', 'HealthCheck', 'HealthDetail']) {
      const d = parse(fs.readFileSync(`components/v2/${f}.vue`, 'utf8')).descriptor
      for (const s of d.styles) expect(compileStyle({ source: s.content, filename: f, id: 'data-v-x', scoped: true }).errors, f).toEqual([])
    }
  })
  it('單機明細換 ch 後舊回應不覆蓋', async () => {
    route.query = { tab: 'config', ct: 'hosts', ch: '5' }
    const w = mount(ConfigAudit, opts)
    await flushPromises()
    const old = pending.find(p => p.url === '/api/config-audit/5')!
    route.query = { tab: 'config', ct: 'hosts', ch: '7' }
    await flushPromises()
    const cur = pending.find(p => p.url === '/api/config-audit/7')!
    cur.resolve({ hostname: 'aaa', platform: 'Linux', items: [{ seq: 1, item_id: 'NEW', result: 'Compliant' }] })
    old.resolve({ hostname: 'bbb', platform: 'AIX', items: [{ seq: 1, item_id: 'OLD', result: 'Compliant' }] })
    await flushPromises()
    expect(w.text()).toContain('NEW')
    expect(w.text()).not.toContain('OLD')
  })
  it('「未完整檢查」只算連得到卻沒查完的，不含「沒去成」', async () => {
    auth.isAdmin = true
    const w = mount(HealthCheck, opts)
    await w.find('textarea').setValue('10.99.0.6')
    await btn(w, '執行自我檢查').trigger('click')
    pending.splice(0)[0].resolve({ items: [
      { ip: '10.99.0.6', overall: 'green', reachable: true, complete: false, coverage: { done: 7, total: 9, missing: [] }, disks: [], failed_units: [], ports: [], notes: [] },
      { ip: '10.99.0.7', overall: 'green', reachable: true, complete: false, not_probed: '沒有金鑰', coverage: { done: 0, total: 9, missing: [] }, disks: [], failed_units: [], ports: [], notes: [] }],
    summary: { total: 2, red: 0, yellow: 0, green: 2, skipped: 0, unreachable: 0, incomplete: 1, not_probed: 1 } })
    await flushPromises()
    await w.findAll('button.scard').find(b => b.text().includes('未完整檢查'))!.trigger('click')
    expect(w.findAll('.c-ip').map(x => x.text())).toEqual(['10.99.0.6'])
  })
  it('單台明細實掛：唯讀重掛警示、inode 沒有這回事顯示「—」、磁碟依 level 上色', async () => {
    const HealthDetail = (await import('../components/v2/HealthDetail.vue')).default
    const sel = { ip: '10.99.0.3', platform: 'linux', overall: 'red', reachable: true, complete: true, coverage: { done: 9, total: 9, missing: [] },
      checked_at: '2026-11-05 01:00', took_ms: 1200, diff: null, os: 'RHEL 9', kernel: 'k', uptime: { days: 3 }, cpu: null, load: null, mem: null,
      disks: [{ mount: '/data', use_pct: 97, inode_pct: null, level: 'red', used_kb: 1, size_kb: 2 }], readonly_mounts: [{ mount: '/data', fs: 'xfs', dev: 'sdb' }],
      top_cpu: [], failed_units: [], failed_detail: [], ports: [], process_visible: true, zombies: 0, recent_logins: [], notes: [],
      ssh_fails: null, kernel_errors: null, sudo_log_authorized: null, net: null, tcp: null, io: null, ntp_synced: null, dns_ok: null, logins: null }
    const w = mount(HealthDetail, { props: { sel }, ...opts })
    expect(w.text()).toContain('唯讀重掛')
    expect(w.find('[data-prefix="D"] .c-inode_sort').text()).toBe('—')
    expect(w.find('[data-prefix="D"] .c-use_pct span').classes()).toContain('lv-red')
  })
})
