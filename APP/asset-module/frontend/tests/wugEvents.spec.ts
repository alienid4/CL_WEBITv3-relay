// 4.1 事件應變 →「告警事件」（2.043.0）與夜間速查的「目前告警（WhatsUp）」：元件實掛，API 全假、無網路（IP 192.0.2.x）。
// 釘住：狀態列數字可點（下鑽）、心跳中斷要明講「不代表現在沒事」、每月彙總點次數＝帶同月同台條件進事件清單、設定只有管理員看得到、
// 真假回報打對的 API、夜間速查一台機器顯示目前告警與最近幾次。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/WugEvents.vue'
import Machine from '../components/v2/WugMachine.vue'

enableAutoUnmount(afterEach)

const ev = (id: number, o: Record<string, any> = {}) => ({ __no: id, id, wug_device_id: String(id), monitor: 'Ping', device_name: `dev${id}`, wug_host: '', ip: `192.0.2.${id}`,
  machine_key: `h:dev${id}`, hostname: `dev${id}`, api_id: 'A-001', system_name: '核心交易', owner: '王小明', wug_state: 'Down', status_text: 'Down at least 5 min',
  tier: 'red', tier_label: '🔴 立即', tier_reason: '第一類・正式', started_at: '2026-11-03 10:00:00', ended_at: null, duration_min: 5, market: '開盤', verdict: null,
  open: true, who: `dev${id}`, ...o })
let status: any
let calls: { url: string; opts: any }[] = []
let query: Record<string, string> = {}
const replace = vi.fn((r: any) => { query = { ...r.query } })

const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p, { slots }) {
    return () => h('table', { class: 'tbl', 'data-prefix': p.prefix }, [
      ...p.rows.map((r: any) => h('tr', { class: 'r' }, p.cols.map((c: any) => h('td', { 'data-k': c.k }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : [`${c.text ? c.text(r) : r[c.k]}`])))),
      p.rows.length ? null : h('tr', h('td', p.empty)),
    ])
  },
})

function setup(viewer = false) {
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useRoute', () => ({ get query() { return query } }))
  vi.stubGlobal('useRouter', () => ({ replace, resolve: () => ({ matched: [1] }) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o?: any) => {
    calls.push({ url: u, opts: o })
    if (u === '/api/v2/wug-alerts') return Promise.resolve(status)
    if (u === '/api/v2/wug-alerts/events') return Promise.resolve({ rows: o?.query?.state === 'open' ? [ev(1), ev(2, { tier: 'maint', tier_label: '維護中（不推）' })] : [ev(1), ev(3, { open: false, ended_at: '2026-11-03 10:20:00' })] })
    if (u === '/api/v2/wug-alerts/month') return Promise.resolve({ month: '2026-11', events: 3, minutes: 30, open: 1, unmatched: 1, machines: [
      { __no: 1, key: 'h:dev1', machine_key: 'h:dev1', who: 'dev1', ip: '192.0.2.1', api_id: 'A-001', system_name: '核心交易', events: 2, red: 2, open: 1, minutes: 20, monitors: 'Ping' },
      { __no: 2, key: 'ip:192.0.2.99', machine_key: null, who: 'x', ip: '192.0.2.99', api_id: null, system_name: null, events: 1, red: 1, open: 0, minutes: 10, monitors: 'Ping' },
      { __no: 3, key: 'wug:77', machine_key: null, who: 'noip', ip: null, wug_device_id: '77', api_id: null, system_name: null, events: 1, red: 1, open: 0, minutes: 3, monitors: 'Ping' }] })
    if (u === '/api/v2/wug-alerts/machine') return Promise.resolve({ current: [ev(1)], recent: [ev(4, { open: false, ended_at: '2026-11-02 08:00:00', started_at: '2026-11-02 07:50:00', duration_min: 10 })], total: 2 })
    if (u.includes('/verdict')) return Promise.resolve({ verdict: o.body.verdict })
    return Promise.resolve({ rows: [] })
  } }))
}
const mountIt = async () => { const w = mount(Comp, { global: { stubs: { V2Table: Table, InfoNote: true } } }); await flushPromises(); return w }

beforeEach(() => {
  calls = []; query = {}; replace.mockClear()
  status = { settings: { enabled: true, heartbeat_min: 10, escalate_min: 60, digest_min: 60, unmatched_tier: 'red', retention_days: 730 }, settings_problem: null,
    last_received: '2026-11-03 10:05:00', heartbeat: 'ok', last_scan: { snap_at: '2026-11-03 10:05:00', complete: 1, devices_seen: 2150, problems: 2 },
    open: 1, maintenance: 1, unmatched: 3, market: '開盤', can_edit: false }
  setup()
})
afterEach(() => { vi.unstubAllGlobals() })

describe('WugEvents（告警事件頁籤）', () => {
  it('狀態列：數字可點，未恢復＝帶 state=open 進事件清單；目前告警清單與 count 事件（不含維護）', async () => {
    const w = await mountIt()
    expect(w.text()).toContain('資料 正常')
    expect(w.findAll('.num').map(b => b.text())).toEqual(['未恢復 1', '維護中 1', '對不到資產 3'])
    expect(w.emitted('count')?.[0]).toEqual([1])
    expect(w.findAll('.tbl .r').length).toBe(2)
    await w.findAll('.num')[0].trigger('click')
    await flushPromises()
    expect(query.wsub).toBe('list')
    expect(calls.some(c => c.url === '/api/v2/wug-alerts/events' && c.opts.query.state === 'open')).toBe(true)
  })
  it('心跳中斷：明講最後收到時的狀態不代表現在沒事', async () => {
    status.heartbeat = 'down'
    const w = await mountIt()
    expect(w.text()).toContain('資料 中斷')
    expect(w.find('.warn').text()).toContain('不代表現在沒事')
  })
  it('設定子頁籤只有管理員看得到', async () => {
    let w = await mountIt()
    expect(w.findAll('.sub').map(b => b.text())).not.toContain('設定')
    status.can_edit = true
    w = await mountIt()
    expect(w.findAll('.sub').map(b => b.text())).toContain('設定')
  })
  it('每月彙總：點次數＝同一月同一台進事件清單；對不到資產的用 IP', async () => {
    query = { wsub: 'month', wmonth: '2026-11' }
    const w = await mountIt()
    expect(calls.some(c => c.url === '/api/v2/wug-alerts/month' && c.opts.query.ym === '2026-11')).toBe(true)
    const links = w.findAll('button.link')
    expect(links.map(b => b.text())).toEqual(['2', '1', '1'])
    await links[0].trigger('click'); await flushPromises()
    let last = calls.filter(c => c.url === '/api/v2/wug-alerts/events').pop()!
    expect(last.opts.query).toMatchObject({ month: '2026-11', machine_key: 'h:dev1', ip: '', wug_device_id: '', no_machine: false })
    await links[1].trigger('click'); await flushPromises()
    last = calls.filter(c => c.url === '/api/v2/wug-alerts/events').pop()!
    expect(last.opts.query).toMatchObject({ month: '2026-11', machine_key: '', ip: '192.0.2.99', wug_device_id: '', no_machine: true })
    await links[2].trigger('click'); await flushPromises()                      // 沒有 IP：用 WhatsUp 設備 ID，不會變成整月全部
    last = calls.filter(c => c.url === '/api/v2/wug-alerts/events').pop()!
    expect(last.opts.query).toMatchObject({ month: '2026-11', machine_key: '', ip: '', wug_device_id: '77', no_machine: true })
  })
  it('真假回報打 PUT verdict；只能看的帳號沒有按鈕', async () => {
    const w = await mountIt()
    const btn = w.findAll('button.mini').find(b => b.text() === '假')!
    await btn.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url.includes('/verdict'))!
    expect(put.url).toBe('/api/v2/wug-alerts/events/1/verdict')
    expect(put.opts).toMatchObject({ method: 'PUT', body: { verdict: 'false' } })
    vi.unstubAllGlobals(); setup(true)
    const v = await mountIt()
    expect(v.findAll('button.mini').some(b => b.text() === '真' || b.text() === '假')).toBe(false)
  })
})

describe('WugMachine（夜間速查一台機器）', () => {
  it('顯示目前告警與最近幾次，用 machine key 查', async () => {
    const w = mount(Machine, { props: { mkey: 'h:dev1', ips: ['192.0.2.1'] }, global: { stubs: { NuxtLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    const c = calls.find(x => x.url === '/api/v2/wug-alerts/machine')!
    expect(c.opts.query).toEqual({ key: 'h:dev1', ip: '192.0.2.1' })
    expect(w.find('.wm').classes()).toContain('hot')
    expect(w.text()).toContain('🔴 Ping')
    expect(w.text()).toContain('最近 1 次')
    expect(w.text()).toContain('已恢復')
  })
})
