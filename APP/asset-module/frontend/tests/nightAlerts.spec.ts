// 夜間速查「目前告警中」（AUD-03）：純函式＋元件實掛（未取得／過舊／0 筆有效／有資料、數字磚下鑽、人為更新的盤中二次確認、示範）。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路（裝置名 TEST-、IP 10.99.x）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as na from '../composables/nightAlerts'
import Comp from '../components/v2/NightAlerts.vue'

enableAutoUnmount(afterEach)

const row = (n: number, o: Partial<na.NightAlertRow> = {}): na.NightAlertRow => ({
  __no: n, device_id: String(n), name: `TEST-${n}`, ip: `10.99.0.${n}`, state: '警告', severity: '警告', problem: 'Ping', since: '2026-11-03 01:00:00', duration_min: 60,
  categories: ['其他告警'], partitions: null, truncated: false, match: 'matched', match_label: '已對應', match_note: '', machine_key: `k${n}`, machine_name: `TEST-${n}`, candidates: 0, ...o })
const payload = (o: Partial<na.NightAlertData> = {}): na.NightAlertData => {
  const rows = o.rows ?? []
  return { status: 'ok', status_label: '資料可用', message: '', rows, counts: na.countsOf(rows), data_time: '2026-11-03 01:55:00', age_min: 5, fresh_min: 30, last_failed: false,
    source: { enabled: true, module_enabled: true, polling_enabled: true, interval_min: 5, last_run_at: null, last_run_status: null, last_run_message: null },
    can_refresh: true, can_edit_settings: false, ...o }
}

describe('nightAlerts 純函式', () => {
  it('篩選與 counts 同一組欄位：加總＝下鑽', () => {
    const rows = [row(1, { severity: '嚴重' }), row(2), row(3, { match: 'pending' }), row(4, { match: 'none', severity: '嚴重' })]
    const c = na.countsOf(rows)
    expect(c).toEqual({ total: 4, critical: 2, warning: 2, unmapped: 2 })
    for (const f of ['critical', 'warning', 'unmapped'] as const) expect(na.filterRows(rows, f).length).toBe(c[f])
    expect(na.filterRows(rows, 'all').length).toBe(4)
  })
  it('持續時間文字：null 是未取得，不是 0', () => {
    expect(na.durationText(null)).toBe('未取得')
    expect(na.durationText(0)).toBe('0 分鐘')
    expect(na.durationText(125)).toBe('2 小時 5 分')
    expect(na.durationText(1500)).toBe('1 天 1 小時')
  })
  it('盤中判斷：平日 08:30–15:00（含邊界）', () => {
    expect(na.isTradingWindow(new Date('2026-11-03T08:29:00'))).toBe(false)   // 週二
    expect(na.isTradingWindow(new Date('2026-11-03T08:30:00'))).toBe(true)
    expect(na.isTradingWindow(new Date('2026-11-03T14:59:00'))).toBe(true)
    expect(na.isTradingWindow(new Date('2026-11-03T15:00:00'))).toBe(false)
    expect(na.isTradingWindow(new Date('2026-11-07T10:00:00'))).toBe(false)   // 週六
  })
  it('viewKind：未啟用／從未輪詢＝unavailable，過舊＝stale，零筆有效＝empty', () => {
    expect(na.viewKind(payload({ status: 'not_enabled' }))).toBe('unavailable')
    expect(na.viewKind(payload({ status: 'never' }))).toBe('unavailable')
    expect(na.viewKind(payload({ status: 'stale' }))).toBe('stale')
    expect(na.viewKind(payload({ status: 'unconfirmed' }))).toBe('stale')
    expect(na.viewKind(payload({ status: 'clock' }))).toBe('stale')
    expect(na.viewKind(payload())).toBe('empty')
    expect(na.viewKind(payload({ rows: [row(1)] }))).toBe('rows')
    expect(na.viewKind(null)).toBe('loading')
  })
  it('示範資料：標示示範、counts 與 rows 一致、不用真實網段', () => {
    const d = na.demoData()
    expect(d.demo).toBe(true)
    expect(d.counts).toEqual(na.countsOf(d.rows))
    expect(d.rows.every(r => r.ip.startsWith('192.0.2.') && r.name.startsWith('DEMO-'))).toBe(true)
  })
})

const calls: { url: string; opts: any }[] = []
let current: na.NightAlertData = payload()
let pollResult: any = { status: 'ok', message: '裝置 5 台' }

const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'exportName', 'empty'],
  setup(p) {
    return () => h('table', { class: 'tbl-stub', 'data-export': p.exportName }, [
      ...p.rows.map((r: any) => h('tr', { class: 'r' }, p.cols.map((c: any) => {
        const v = c.text ? c.text(r) : r[c.k]
        const lk = c.link ? c.link(r) : null
        return h('td', { 'data-k': c.k, 'data-link': lk || undefined }, [`${Array.isArray(v) ? v.join('、') : v}`])
      }))),
      p.rows.length ? null : h('tr', h('td', p.empty)),
    ])
  },
})

function setup() {
  for (const [k, v] of Object.entries({ ref, computed, onMounted, onBeforeUnmount, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => ({ query: {} }))
  vi.stubGlobal('useRouter', () => ({ replace: vi.fn() }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o?: any) => {
    calls.push({ url: u, opts: o })
    if (u.startsWith('/api/integrations/whatsup/poll')) return Promise.resolve(pollResult)
    return Promise.resolve(current)
  } }))
}
const mountIt = async () => {
  const w = mount(Comp, { global: { stubs: { V2Table: Table, InfoNote: true } } })
  await flushPromises()
  return w
}

beforeEach(() => { calls.length = 0; current = payload(); pollResult = { status: 'ok', message: '裝置 5 台' }; setup() })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('NightAlerts 元件', () => {
  it('來源未啟用：寫「未取得」，不說沒有告警，沒有數字磚', async () => {
    current = payload({ status: 'not_enabled', status_label: '未取得', message: 'WhatsUp 定期輪詢未啟用，所以本頁沒有告警資料。這不代表目前沒有告警。', data_time: null, age_min: null, can_refresh: false })
    const w = await mountIt()
    expect(w.text()).toContain('未取得')
    expect(w.text()).not.toContain('目前沒有告警　')
    expect(w.find('.okbox').exists()).toBe(false)
    expect(w.find('.tiles').exists()).toBe(false)
    expect(w.text()).not.toContain('更新')                      // can_refresh=false 不顯示按鈕
  })
  it('啟用但從未輪詢：同樣是未取得', async () => {
    current = payload({ status: 'never', status_label: '未取得', message: 'WhatsUp 已啟用，但還沒有成功輪詢過。這不代表目前沒有告警。', data_time: null, age_min: null })
    const w = await mountIt()
    expect(w.text()).toContain('未取得')
    expect(w.find('.okbox').exists()).toBe(false)
  })
  it('資料過舊：標示過舊與資料時間，零筆也不說沒有告警', async () => {
    current = payload({ status: 'stale', status_label: '資料過舊', message: '資料過舊：最後一次成功取得是 2026-11-03 00:10:00（110 分鐘前，門檻 30 分鐘）。', age_min: 110, data_time: '2026-11-03 00:10:00' })
    const w = await mountIt()
    expect(w.text()).toContain('資料過舊')
    expect(w.text()).toContain('2026-11-03 00:10:00')
    expect(w.find('.okbox').exists()).toBe(false)
  })
  it('最近輪詢失敗／資料時間在未來：寫出狀態名稱，零筆也不說沒有告警', async () => {
    current = payload({ status: 'unconfirmed', status_label: '無法確認（最近輪詢失敗）', message: '無法確認：最近一次輪詢失敗', last_failed: true })
    let w = await mountIt()
    expect(w.find('.alert').text()).toContain('無法確認（最近輪詢失敗）')
    expect(w.find('.okbox').exists()).toBe(false)
    current = payload({ status: 'clock', status_label: '時間異常（資料時間在未來）', message: '時間異常', age_min: null })
    w = await mountIt()
    expect(w.find('.alert').text()).toContain('時間異常（資料時間在未來）')
    expect(w.find('.okbox').exists()).toBe(false)
  })
  it('零筆但來源有效：才寫「目前沒有告警」並帶資料時間', async () => {
    const w = await mountIt()
    expect(w.find('.okbox').text()).toContain('目前沒有告警')
    expect(w.find('.okbox').text()).toContain('01:55')
  })
  it('有資料：數字磚＝清單筆數；點磚下鑽；再點收起；主機可連到履歷、待核不連結', async () => {
    current = payload({ rows: [row(1, { severity: '嚴重', state: '當機' }), row(2), row(3, { match: 'pending', machine_key: null, match_label: '待核（不確定是哪一台）' }), row(4, { match: 'none', machine_key: null })] })
    const w = await mountIt()
    const tile = (k: string) => w.find(`.tile.${k}`)
    expect(tile('critical').find('.n').text()).toBe('1')
    expect(tile('warning').find('.n').text()).toBe('3')
    expect(tile('unmapped').find('.n').text()).toBe('2')
    expect(w.findAll('tr.r').length).toBe(4)                     // 預設全部
    await tile('unmapped').trigger('click')
    expect(w.findAll('tr.r').length).toBe(2)
    expect(w.findAll('tr.r').every(r => !r.find('[data-k=name]').attributes('data-link'))).toBe(true)
    await tile('critical').trigger('click')
    expect(w.findAll('tr.r').length).toBe(1)
    expect(w.find('[data-k=name]').attributes('data-link')).toBe('/v2/machine/k1')
    expect(w.find('.tbl-stub').attributes('data-export')).toContain('嚴重')
    await tile('critical').trigger('click')                       // 再點一次收起
    expect(w.find('.tbl-stub').exists()).toBe(false)
  })
  it('分割區抽不到寫未取得、非磁碟類寫—、持續時間未取得', async () => {
    current = payload({ rows: [row(1, { partitions: '未取得', categories: ['磁碟'] }), row(2, { partitions: null, duration_min: null })] })
    const w = await mountIt()
    const cells = (k: string) => w.findAll(`[data-k=${k}]`).map(c => c.text())
    expect(cells('partitions')).toEqual(['未取得', '—'])
    expect(cells('duration_min')).toEqual(['1 小時 0 分', '未取得'])
  })
  it('更新鈕：盤中先確認；取消不送；確認才送且帶 confirm_in_window=true', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-03T10:00:00'))
    const conf = vi.fn(() => false)
    vi.stubGlobal('confirm', conf); (window as any).confirm = conf
    const w = await mountIt()
    const btn = w.find('button.btn')
    expect(btn.text()).toBe('更新')
    await btn.trigger('click'); await flushPromises()
    expect(conf).toHaveBeenCalledWith('現在是盤中，確定要人為執行一次查詢嗎？')
    expect(calls.filter(c => c.url.includes('/poll')).length).toBe(0)
    conf.mockReturnValue(true)
    await btn.trigger('click'); await flushPromises()
    const p = calls.filter(c => c.url.includes('/poll'))
    expect(p.length).toBe(1)
    expect(p[0].url).toBe('/api/integrations/whatsup/poll?confirm_in_window=true')
    expect(p[0].opts.method).toBe('POST')
    expect(calls.filter(c => c.url === '/api/night/alerts').length).toBe(2)   // 更新後重讀
  })
  it('更新鈕：非盤中不問、不帶參數', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-03T20:00:00'))
    const conf = vi.fn(() => true); (window as any).confirm = conf
    const w = await mountIt()
    await w.find('button.btn').trigger('click'); await flushPromises()
    expect(conf).not.toHaveBeenCalled()
    expect(calls.find(c => c.url.includes('/poll'))!.url).toBe('/api/integrations/whatsup/poll')
  })
  it('進頁只讀一次快取，不會自己打輪詢', async () => {
    await mountIt()
    expect(calls.map(c => c.url)).toEqual(['/api/night/alerts'])
  })
  it('示範：標示示範、不呼叫任何寫入、不連到履歷', async () => {
    const w = await mountIt()
    await w.findAll('button.lnk')[0].trigger('click')
    expect(w.text()).toContain('示範資料')
    expect(w.findAll('tr.r').length).toBe(4)
    expect(w.findAll('[data-link]').length).toBe(0)
    expect(w.find('button.btn').exists()).toBe(false)
    expect(calls.map(c => c.url)).toEqual(['/api/night/alerts'])
  })
  it('讀取失敗：顯示失敗與重試，不是空白或沒有告警', async () => {
    vi.stubGlobal('useApi', () => ({ apiFetch: () => Promise.reject({ data: { detail: 'boom' } }) }))
    const w = await mountIt()
    expect(w.find('.alert.bad').text()).toContain('boom')
    expect(w.find('.okbox').exists()).toBe(false)
  })
  it('admin 才有設定：存新鮮度門檻送 PUT', async () => {
    current = payload({ can_edit_settings: true })
    const w = await mountIt()
    await w.find('details.cfg input').setValue(45)
    await w.findAll('details.cfg button')[0].trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/night/alerts/settings')!
    expect(put.opts).toEqual({ method: 'PUT', body: { fresh_min: 45 } })
  })
  it('Q15 中繼來源：跟今日概況同一份，顯示範圍與「看全部」（網址 all=1），沒有更新鈕與門檻', async () => {
    const replace = vi.fn()
    vi.stubGlobal('useRouter', () => ({ replace }))
    current = payload({ rows: [row(1)], can_refresh: false, scope: { mode: 'policy', keywords: ['主機組'], n_all: 3, hidden: 2, label: 'x' },
      source: { kind: 'relay', label: '4.1', enabled: true, module_enabled: true, polling_enabled: false, interval_min: null, last_run_at: null, last_run_status: 'ok', last_run_message: null } })
    const w = await mountIt()
    expect(w.text()).toContain('跟今日概況同一份')
    expect(w.text()).toContain('另有 2 件不在範圍')
    expect(w.text()).not.toContain('門檻：')
    expect(calls[0].opts).toEqual({ query: { scope: 'policy' } })
    await w.find('[data-testid=night-scope-all]').trigger('click')
    expect(replace).toHaveBeenCalledWith({ query: { all: '1' } })
  })
})
