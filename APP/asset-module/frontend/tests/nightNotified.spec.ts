// 夜間速查・今晚通知清單（N3）／緊急檢查（N4）／月報夜間段（N6）：純函式＋元件實掛。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／NuxtLink／InfoNote 為簡化替身；API 全假、無網路（主機名 TEST-、IP 10.99.x）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as nn from '../composables/nightNotified'
import Notified from '../components/v2/NightNotified.vue'
import Check from '../components/v2/NightCheck.vue'

enableAutoUnmount(afterEach)

const NOW = '2026-11-15 02:15:00'
const item = (n: number, o: Partial<nn.NotifiedItem> = {}): nn.NotifiedItem => ({
  id: `wug:${n}`, kind: 'wug', notified_at: `2026-11-15 0${n % 10}:00:00`, title: `事件 ${n}`, detail: null,
  machine_key: `mk${n}`, name: `TEST-${n}`, ip: `10.99.0.${n}`, platform: 'linux', open: true, recovered_at: null, times_tonight: 1,
  system: null, impact: { hosts: 1, systems: 0, big: false, reason: null }, handling: null, ...o,
})
const notified = (items: nn.NotifiedItem[], o: Partial<nn.NotifiedData> = {}): nn.NotifiedData => ({
  now: NOW, hours: 12, data_at: '2026-11-15 02:14:00',
  summary: { total: items.length, open: items.filter(x => x.open).length, tier1: items.filter(x => nn.isTier1(x.system)).length, big_impact: items.filter(x => x.impact?.big).length },
  items, older: { count: 0, link: '/v2/night?view=older' }, ...o,
})
const T1 = { api_id: 'A01', name: '交割系統', tier: '第一類', env: '正式' }

describe('今晚通知清單：純函式', () => {
  it('排序：未恢復在上，再第一類、影響大、通知時間新到舊', () => {
    const rows = nn.sortNotified([
      item(1, { open: false, system: T1 }),
      item(2, { notified_at: '2026-11-15 01:00:00' }),
      item(3, { notified_at: '2026-11-15 03:00:00' }),
      item(4, { notified_at: '2026-11-15 00:30:00', system: T1 }),
      item(5, { notified_at: '2026-11-15 00:10:00', impact: { hosts: 18, systems: 3, big: true, reason: null } }),
    ])
    expect(rows.map(r => r.id)).toEqual(['wug:4', 'wug:5', 'wug:3', 'wug:2', 'wug:1'])
  })
  it('頁首一句：0 件寫「今晚沒有通知你」', () => {
    expect(nn.headline({ total: 0, open: 0, tier1: 0, big_impact: 0 })).toBe('今晚沒有通知你')
    expect(nn.headline(null)).toBe('今晚沒有通知你')
    expect(nn.headline({ total: 3, open: 2, tier1: 1, big_impact: 1 })).toBe('今晚通知你 3 件・交易系統 1 件・影響範圍大 1 件')
  })
  it('時間：同一天只給時分，跨日帶月日；沒有就寫 —', () => {
    expect(nn.hhmm('2026-11-15 01:52:10', NOW)).toBe('01:52')
    expect(nn.hhmm('2026-11-14 23:41:00', NOW)).toBe('11-14 23:41')
    expect(nn.hhmm(null, NOW)).toBe('—')
  })
  it('它現在／影響／處理的文字', () => {
    expect(nn.nowText(item(1), NOW)).toEqual({ tag: '還沒恢復', tone: 'bad', sub: '' })
    expect(nn.nowText(item(1, { open: false, recovered_at: '2026-11-15 00:34:00', times_tonight: 2 }), NOW)).toEqual({ tag: '已恢復', tone: 'good', sub: '00:34 恢復・今晚 2 次' })
    expect(nn.impactText(item(1, { impact: { hosts: 18, systems: 3, big: true, reason: null } }))).toEqual({ main: '18 台・3 個系統', sub: '要通知主管', big: true })
    expect(nn.impactText(item(1, { system: T1 })).main).toBe('交割系統')
    expect(nn.impactText(item(1, { system: T1 })).sub).toBe('第一類・正式')
    expect(nn.impactText(item(1)).main).toBe('沒有登記業務系統')
    expect(nn.handleState(null)).toBe('none')
    expect(nn.handleText({ id: 1, by: '測試員', at: '2026-11-15 02:05:00', result: null, done_at: null }, NOW)).toEqual({ main: '測試員 處理中', sub: '02:05 起' })
    expect(nn.handleText({ id: 1, by: '測試員', at: '2026-11-15 02:05:00', result: '已清 E 碟', done_at: '2026-11-15 02:30:00' }, NOW)).toEqual({ main: '測試員 已處理', sub: '已清 E 碟' })
  })
  it('要你處理＝還沒恢復且沒人接手；這台的通知用 machine_key 或 IP 對', () => {
    const items = [item(1), item(2, { open: false }), item(3, { handling: { id: 1, by: 'x', at: NOW, result: null, done_at: null } })]
    expect(nn.needsYou(items).map(x => x.id)).toEqual(['wug:1'])
    expect(nn.itemsOfHost(items, 'mk2').map(x => x.id)).toEqual(['wug:2'])
    expect(nn.itemsOfHost(items, '10.99.0.3').map(x => x.id)).toEqual(['wug:3'])
    expect(nn.itemsOfHost(items, '')).toEqual([])
  })
  it('VMware 設備判斷（天條：不做即時檢測）', () => {
    for (const p of ['esxi', 'ESXi', 'vcenter', 'VMware vCSA']) expect(nn.isVmwarePlatform(p)).toBe(true)
    for (const p of ['linux', 'windows', 'aix', null]) expect(nn.isVmwarePlatform(p)).toBe(false)
  })
})

describe('緊急檢查：純函式', () => {
  it('結論列四種樣式；後端沒給資料就說不知道', () => {
    const a = (state: nn.AliveState, text: string | null = null) => ({ state, uptime_days: null, booted_at: null, source: '監控', at: '2026-11-15 02:14:00', text })
    expect(nn.verdict(a('alive', '活著・開機 45 天，沒有重開過'))).toMatchObject({ tone: 'ok', icon: '✅', text: '活著・開機 45 天，沒有重開過', src: '02:14・監控' })
    expect(nn.verdict(a('rebooted')).tone).toBe('boot')
    expect(nn.verdict(a('unreachable')).tone).toBe('down')
    expect(nn.verdict(a('unknown')).text).toBe('不知道它現在的狀態')
    expect(nn.verdict(null)).toMatchObject({ tone: 'unknown', icon: '❔' })
  })
  it('跟平常比：基準不足／沒有／失敗寫原因不猜；紅色排前面', () => {
    const b = (o: Partial<nn.Baseline>): nn.Baseline => ({ status: 'ok', days: 7, reason: null, changes: [], same_count: 0, same_brief: null, ...o })
    expect(nn.baselineView(b({ status: 'insufficient', reason: '7 天只有 2 天有資料' }))).toMatchObject({ kind: 'short', msg: '基準不足：7 天只有 2 天有資料' })
    expect(nn.baselineView(b({ status: 'none' })).msg).toBe('基準不足：沒有平常的資料可以比')
    expect(nn.baselineView(b({ status: 'error', reason: '資料庫鎖住' })).msg).toBe('比對失敗：資料庫鎖住')
    expect(nn.baselineView(null).kind).toBe('fail')
    expect(nn.baselineView(b({ same_count: 23 }))).toMatchObject({ kind: 'same', sameCount: 23 })
    const v = nn.baselineView(b({ changes: [{ level: 'amber', what: '新出現連接埠 8080', since: null }, { level: 'red', what: '服務停了', since: null }] }))
    expect(v.kind).toBe('ok')
    expect(v.changes.map(c => c.level)).toEqual(['red', 'amber'])
  })
  it('找誰：查不到／有登記但沒填分開；電話做成可撥', () => {
    expect(nn.contactView({ role: 'AP 負責人', name: null, phone: null, state: 'no_source' }).note).toBe('查不到')
    expect(nn.contactView({ role: 'SP 負責人', name: '測試員', phone: null, state: 'blank' }).note).toBe('有登記但沒填')
    expect(nn.contactView({ role: '主管', name: '測試員', phone: '0900-000-000', state: 'ok' })).toEqual({ name: '測試員', phone: '0900-000-000', tel: 'tel:0900000000', note: '' })
    expect(nn.contactView({ role: '主管', name: '測試員', phone: '', state: 'ok' }).note).toBe('有登記但沒填')
  })
  it('最近 7 天：每天一格、沒給的那天是「沒收到資料」，四種樣式', () => {
    const rows = nn.fillHistory([
      { day: '2026-11-15', state: '異常', items: [{ name: '硬碟使用率', what: 'E: 91%' }] },
      { day: '2026-11-14', state: '檢查失敗', items: [] },
      { day: '2026-11-10', state: '正常', items: [] },
    ], '2026-11-15')
    expect(rows.map(r => r.day)).toEqual(['2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13', '2026-11-14', '2026-11-15'])
    expect(rows.map(r => r.tone)).toEqual(['gray', 'ok', 'gray', 'gray', 'gray', 'warn', 'bad'])
    expect(rows[6].brief).toBe('E: 91%')
    expect(rows[0].state).toBe('沒收到資料')
    expect(nn.badDays(rows)).toBe(1)
    expect(nn.fillHistory(null, '2026-03-01').map(r => r.day)[0]).toBe('2026-02-23')
  })
  it('即時檢測挑連得到的那筆；月報夜間段：讀不到≠0 筆', () => {
    expect(nn.pickCheckItem([{ ip: 'a', reachable: false }, { ip: 'b', reachable: true }]).ip).toBe('b')
    expect(nn.pickCheckItem([])).toBeNull()
    expect(nn.monthNightView(undefined)).toMatchObject({ state: 'missing', msg: '本月夜間通知讀不到' })
    expect(nn.monthNightView({ count: 0, hosts: [], unhandled: 0 })).toMatchObject({ state: 'empty', msg: '本月沒有夜間通知' })
    const v = nn.monthNightView({ count: 3, unhandled: 1, hosts: [{ name: 'TEST-1', ip: '10.99.0.1', times: 2, first_handle_min: 95, result: null }, { name: null, ip: '10.99.0.2', times: 1, first_handle_min: null, result: null }] })
    expect(v.rows.map(r => [r.__no, r.name_t, r.handle_t, r.result_t])).toEqual([[1, 'TEST-1', '1 小時 35 分', '沒寫處理結果'], [2, '10.99.0.2', '沒人處理', '沒人處理']])
    expect(nn.monthNightView({ count: null, reason: '資料庫忙碌', hosts: [], unhandled: 0 })).toMatchObject({ state: 'missing', msg: '本月夜間通知讀不到：資料庫忙碌', rows: [] })
    expect(nn.monthNightView({ count: null, hosts: [], unhandled: 0 } as any)).toMatchObject({ state: 'missing', msg: '本月夜間通知讀不到' })
    const h = nn.monthNightView({ count: 2, unhandled: 1, hosts: [{ name: 'T-A', ip: null, times: 1, first_handle_min: null, handled: true, result: null }, { name: 'T-B', ip: null, times: 1, first_handle_min: null, handled: false, result: null }] })
    expect(h.rows.map(r => [r.handle_t, r.result_t])).toEqual([['有人處理，時間算不出', '沒寫處理結果'], ['沒人處理', '沒人處理']])
    expect(nn.minText(12)).toBe('12 分')
    expect(nn.minText(120)).toBe('2 小時')
  })
})

// ===== 元件實掛 =====
const calls: { url: string; opts: any }[] = []
let routes: Record<string, any> = {}
let level = { admin: true, viewer: false }
const toast = vi.fn()

const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'exportName', 'empty', 'exportCols', 'rowKey', 'unit'],
  setup(p, { slots }) {
    return () => h('div', { class: 'tbl-stub', 'data-export': p.exportName }, [
      ...p.rows.map((r: any) => h('div', { class: 'r', 'data-no': `${p.prefix}${r.__no}` }, p.cols.map((c: any) => {
        const s = slots[`cell-${c.k}`]
        return h('span', { 'data-k': c.k }, s ? s({ row: r }) : [`${c.text ? c.text(r) : r[c.k]}`])
      }))),
      p.rows.length ? null : h('div', { class: 'empty' }, p.empty),
    ])
  },
})
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': JSON.stringify(p.to) }, slots.default?.()) } })

function setup() {
  for (const [k, v] of Object.entries({ ref, computed, onMounted, onBeforeUnmount, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => ({ query: {} }))
  vi.stubGlobal('useToast', () => ({ showToast: toast }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: computed(() => level.admin), isViewer: computed(() => level.viewer) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o?: any) => {
    calls.push({ url: u, opts: o })
    const r = routes[u]
    if (r === undefined) return Promise.reject(new Error('unexpected ' + u))
    return typeof r === 'function' ? r(o) : r instanceof Error ? Promise.reject(r) : Promise.resolve(r)
  } }))
}
const stubs = { V2Table: Table, NuxtLink: Link, InfoNote: true, V2NightAlerts: true }

beforeEach(() => { calls.length = 0; routes = {}; level = { admin: true, viewer: false }; toast.mockReset(); setup() })
afterEach(() => { vi.unstubAllGlobals() })

describe('NightNotified 元件', () => {
  it('0 件：寫「今晚沒有通知你」與「沒有要你處理的事」', async () => {
    routes['/api/v2/night/notified'] = notified([])
    const w = mount(Notified, { global: { stubs } }); await flushPromises()
    expect(w.get('[data-testid="night-concl"]').text()).toBe('今晚沒有通知你')
    expect(w.get('[data-testid="night-todo"]').text()).toContain('沒有要你處理的事')
  })
  it('讀取失敗：不說沒有通知', async () => {
    routes['/api/v2/night/notified'] = Object.assign(new Error('x'), { statusCode: 500 })
    const w = mount(Notified, { global: { stubs } }); await flushPromises()
    expect(w.get('[data-testid="night-concl"]').text()).toBe('讀不到今晚的通知')
    expect(w.text()).not.toContain('今晚沒有通知你')
  })
  it('有資料：項次照預設排序、主機連到緊急檢查、按「我在處理」送出並換成誰處理中', async () => {
    routes['/api/v2/night/notified'] = notified([item(1, { open: false }), item(2, { system: T1 })])
    routes['/api/v2/night/handle'] = { id: 9, by: '測試員', at: '2026-11-15 02:20:00' }
    const w = mount(Notified, { global: { stubs } }); await flushPromises()
    expect(w.get('[data-testid="night-concl"]').text()).toBe('今晚通知你 2 件・交易系統 1 件・影響範圍大 0 件')
    const rows = w.findAll('.r')
    expect(rows.map(r => r.attributes('data-no'))).toEqual(['N1', 'N2'])
    expect(rows[0].text()).toContain('TEST-2')
    expect(JSON.parse(rows[0].get('[data-testid="night-host-link"]').attributes('data-to'))).toEqual({ path: '/v2/night', query: { host: 'mk2' } })
    expect(w.findAll('[data-testid="night-take"]')).toHaveLength(1)    // 已恢復的那列沒有按鈕
    await w.get('[data-testid="night-take"]').trigger('click'); await flushPromises()
    const post = calls.find(c => c.url === '/api/v2/night/handle')
    expect(post?.opts).toMatchObject({ method: 'POST', body: { item_id: 'wug:2', key: 'mk2' } })
    expect(w.findAll('[data-testid="night-take"]')).toHaveLength(0)
    expect(w.text()).toContain('測試員 處理中')
  })
  it('只能看的帳號沒有「我在處理」', async () => {
    level = { admin: false, viewer: true }
    routes['/api/v2/night/notified'] = notified([item(1)])
    const w = mount(Notified, { global: { stubs } }); await flushPromises()
    expect(w.findAll('[data-testid="night-take"]')).toHaveLength(0)
  })
})

const host = (o: Partial<nn.HostCheck> = {}): nn.HostCheck => ({
  key: 'mk1', name: 'TEST-1', ip: '10.99.0.1', platform: 'windows',
  alive: { state: 'alive', uptime_days: 45, booted_at: null, source: '監控', at: '2026-11-15 02:14:00', text: '活著・開機 45 天，沒有重開過' },
  baseline: { status: 'insufficient', days: 7, reason: '7 天只有 2 天有資料', changes: [], same_count: 0, same_brief: null },
  impact: { hosts: 1, systems: [T1], big: false, vms: [], vm_data_at: null, reason: null },
  contacts: [{ role: 'AP 負責人', name: null, phone: null, state: 'no_source' }, { role: 'SP 負責人', name: '測試員', phone: null, state: 'blank' }, { role: '主管', name: '測試主管', phone: '0900-000-000', state: 'ok' }],
  history: [], events_tonight: [], ...o,
})

describe('NightCheck 元件', () => {
  it('一般主機：結論、基準不足寫原因、找誰兩種沒資料分開、7 天 7 格、沒有紅橫幅', async () => {
    routes['/api/v2/night/host'] = host()
    routes['/api/v2/night/notified'] = notified([item(1)])
    const w = mount(Check, { props: { hkey: 'mk1' }, global: { stubs } }); await flushPromises()
    expect(w.get('[data-testid="night-verdict"]').text()).toContain('活著・開機 45 天，沒有重開過')
    expect(w.get('[data-testid="night-baseline-msg"]').text()).toBe('基準不足：7 天只有 2 天有資料')
    expect(w.findAll('[data-testid="night-contact-note"]').map(x => x.text())).toEqual(['查不到', '有登記但沒填'])
    expect(w.html()).toContain('href="tel:0900000000"')
    expect(w.get('[data-testid="night-history"]').findAll('a')).toHaveLength(7)
    expect(w.find('[data-testid="night-banner"]').exists()).toBe(false)
  })
  it('影響超過 3 台：頂端紅橫幅＋主管電話', async () => {
    routes['/api/v2/night/host'] = host({ platform: 'esxi', impact: { hosts: 18, systems: [T1], big: true, vms: [{ name: 'TEST-VM1', api_id: 'A01', power: 'on' }], vm_data_at: '2026-11-15 01:30:00', reason: null } })
    routes['/api/v2/night/notified'] = notified([])
    const w = mount(Check, { props: { hkey: 'mk1' }, global: { stubs } }); await flushPromises()
    const b = w.get('[data-testid="night-banner"]')
    expect(b.text()).toContain('18 台')
    expect(b.text()).toContain('請通知主管')
    expect(b.html()).toContain('tel:0900000000')
    expect(w.text()).toContain('虛擬機清單 2026-11-15 01:30')
  })
  it('ESXi：管理員也沒有「立即檢測」（天條），也不打 health-check', async () => {
    routes['/api/v2/night/host'] = host({ platform: 'esxi' })
    routes['/api/v2/night/notified'] = notified([])
    const w = mount(Check, { props: { hkey: 'mk1' }, global: { stubs } }); await flushPromises()
    expect(w.find('[data-testid="night-check-now"]').exists()).toBe(false)
    expect(calls.some(c => c.url === '/api/health-check/run')).toBe(false)
  })
  it('非管理員沒有「立即檢測」', async () => {
    level = { admin: false, viewer: false }
    routes['/api/v2/night/host'] = host()
    routes['/api/v2/night/notified'] = notified([])
    const w = mount(Check, { props: { hkey: 'mk1' }, global: { stubs } }); await flushPromises()
    expect(w.find('[data-testid="night-check-now"]').exists()).toBe(false)
  })
  it('立即檢測：打 health-check → 存快照 → 用回傳的基準重畫', async () => {
    routes['/api/v2/night/host'] = host()
    routes['/api/v2/night/notified'] = notified([])
    routes['/api/health-check/run'] = { items: [{ ip: '10.99.0.1', reachable: true, checked_at: '2026-11-15 02:16:00' }] }
    routes['/api/v2/night/check-snapshot'] = { id: 3, baseline: { status: 'ok', days: 7, reason: null, changes: [{ level: 'red', what: '服務停了：TEST-SVC', since: null }], same_count: 5, same_brief: 'C: 41%' } }
    const w = mount(Check, { props: { hkey: 'mk1' }, global: { stubs } }); await flushPromises()
    await w.get('[data-testid="night-check-now"]').trigger('click'); await flushPromises()
    const hc = calls.find(c => c.url === '/api/health-check/run')
    expect(hc?.opts).toMatchObject({ method: 'POST', body: { ips: ['10.99.0.1'] } })
    expect(calls.find(c => c.url === '/api/v2/night/check-snapshot')?.opts).toMatchObject({ method: 'POST', body: { key: 'mk1', result: { ip: '10.99.0.1', reachable: true } } })
    expect(w.get('[data-testid="night-changes"]').text()).toContain('服務停了：TEST-SVC')
    expect(w.text()).toContain('其他 5 項跟平常一樣')
  })
  it('盤中確認按取消：不報錯、不存快照', async () => {
    routes['/api/v2/night/host'] = host()
    routes['/api/v2/night/notified'] = notified([])
    routes['/api/health-check/run'] = Object.assign(new Error('cancel'), { cancelled: true })
    const w = mount(Check, { props: { hkey: 'mk1' }, global: { stubs } }); await flushPromises()
    await w.get('[data-testid="night-check-now"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/v2/night/check-snapshot')).toBe(false)
    expect(toast).not.toHaveBeenCalledWith(expect.stringContaining('檢測失敗'), 'error')
  })
  it('我在處理 → 寫處理結果', async () => {
    routes['/api/v2/night/host'] = host()
    routes['/api/v2/night/notified'] = notified([item(1)])
    routes['/api/v2/night/handle'] = { id: 7, by: '測試員', at: '2026-11-15 02:20:00' }
    routes['/api/v2/night/handle/7/result'] = { ok: true }
    const w = mount(Check, { props: { hkey: 'mk1' }, global: { stubs } }); await flushPromises()
    await w.get('[data-testid="night-check-take"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/v2/night/handle')?.opts.body).toEqual({ item_id: 'wug:1', key: 'mk1' })
    await w.get('input[aria-label="處理結果"]').setValue('已清 E 碟備份')
    await w.get('[data-testid="night-check-finish"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/v2/night/handle/7/result')?.opts).toMatchObject({ method: 'POST', body: { result: '已清 E 碟備份' } })
    expect(w.text()).toContain('測試員 已處理：已清 E 碟備份')
  })
})
