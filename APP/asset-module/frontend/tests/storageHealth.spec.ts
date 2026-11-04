// 儲存設備每日健康／告警檢查（批 S3）畫面：列表有「最近健康檢查」；「健康／告警」唯讀（一般帳號也能開）、「立即檢查」只有 admin；
// 有效／已解除／不確定／已讀 數字可下鑽成清單；失敗或不完整的區段不顯示成 0；最近一次失敗時標出目前顯示的是更早的資料。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/IntegrationStorage.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
let admin = true
let checkReply: any = { ok: true, status: 'ok', message: '檢查完成' }
let detailReply: any
let devices: any[] = []

const verdict = (o: any = {}) => ({ level: 'complete', text: '健康原始值 5；有效告警 1、已解除 1、不確定 1', stale: false, ...o })
const summary = (o: any = {}) => ({
  id: 7, status: 'ok', complete: true, code: 'ok', message: '檢查完成', finished_at: '2026-11-07 07:00:09', trigger: 'auto', attempt_no: 1,
  health_status: 'read', health_value: '5', alert_status: 'ok', n_total: 3, n_effective: 1, n_resolved: 1, n_uncertain: 1, n_acked: 1, n_manual: 0, verdict: verdict(), ...o,
})
const batch = (o: any = {}) => ({
  ...summary(), device_id: 1, device_name: 'unity-a', endpoint: '10.99.0.5:443', day: '2026-11-07', started_at: '2026-11-07 07:00:00', checked_by: null,
  tls_verified: true, os_version: '5.3.0.0', health_at: '2026-11-07 07:00:05', health_code: null, health_msg: null, alert_at: '2026-11-07 07:00:08',
  alert_code: null, alert_msg: null, health_detail: { value: 5, descriptions: ['operating normally'] }, ...o,
})
const alertRow = (o: any = {}) => ({ __no: 1, alert_id: 'a1', state_raw: 'Active_Auto', state_class: 'effective', state_note: '', manual: false, severity_raw: '3',
  alert_time: '2026-11-06T01:00:00Z', acked: true, component: 'sp_a', message: 'm1', incomplete: false, ...o })
const dev = (o: any = {}) => ({
  id: 1, __no: 1, name: 'unity-a', vendor: 'dell_unity', vendor_label: 'Dell Unity', host: '10.99.0.5', port: 443, credential: 'stor-ro',
  ca_bundle: '', trust_self_signed: false, enabled: true, note: '', last_test_at: null, last_test_ok: null, last_test_code: null,
  last_test_msg: null, last_identity: null, last_tls_verified: null, last_collect: null, last_health: null,
  health_schedule: { included: true, reason: '未到每日檢查時間', due: false }, ...o,
})
const std = () => ({
  device_id: 1, batch: batch(), latest: batch(), daily_at: '07:00', alerts_pruned: false, schedule: { included: true, reason: '今天已成功', due: false },
  alerts: [alertRow(), alertRow({ __no: 2, alert_id: 'a2', state_raw: 'Inactive', state_class: 'resolved', acked: false, message: 'm2' }),
    alertRow({ __no: 3, alert_id: 'a3', state_raw: 'Updating', state_class: 'uncertain', acked: null, message: 'm3', state_note: '狀態更新中（Updating），尚未確定' })],
  history: [{ id: 7, day: '2026-11-07', trigger: 'auto', attempt_no: 1, started_at: 's', finished_at: '2026-11-07 07:00:09', status: 'ok', code: 'ok', message: '檢查完成' }],
})

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  const m = o.method ?? 'GET'
  if (u === '/api/storage/devices' && m === 'GET') return Promise.resolve({ rows: devices, vendors: [{ key: 'dell_unity', label: 'Dell Unity' }], unsupported_vendors: [] })
  if (u === '/api/credentials') return Promise.resolve([])
  if (u.endsWith('/health-check')) return Promise.resolve(checkReply)
  if (u.endsWith('/health')) return Promise.resolve(detailReply)
  return Promise.resolve({ ok: true })
}

const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p, { slots }) {
    return () => p.rows.length === 0 ? h('div', { class: 'empty-' + p.prefix }, p.empty) : h('table', {}, p.rows.map((r: any) => h('tr', { class: 'row-' + p.prefix }, [
      h('td', { class: 'name' }, r.name), h('td', { class: 'health' }, r.health), h('td', { class: 'cls' }, r.cls), h('td', { class: 'msg' }, r.message),
      h('td', { class: 'note' }, r.note), h('td', { class: 'act' }, slots['cell-act']?.({ row: r })),
    ])))
  },
})

beforeEach(() => {
  calls.length = 0; admin = true; devices = [dev()]; checkReply = { ok: true, status: 'ok', message: '檢查完成' }; detailReply = std()
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: computed(() => admin) }))
  vi.stubGlobal('window', Object.assign(globalThis.window ?? {}, { confirm: () => true }))
})

const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, NuxtLink: { template: '<a><slot /></a>' }, V2Table } } })
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text() === t)
const chip = (w: any, label: string) => w.findAll('button.mini').find((b: any) => b.text().startsWith(label))

describe('儲存設備每日健康／告警檢查（S3）', () => {
  it('列表有「最近健康檢查」：尚未檢查／未納入原因／最近一批的一句話', async () => {
    devices = [dev({ health_schedule: { included: false, reason: '尚未選收集憑證', due: false } })]
    let w = mnt(); await flushPromises()
    expect(w.find('.health').text()).toContain('未納入每日檢查：尚未選收集憑證')
    devices = [dev()]
    w = mnt(); await flushPromises()
    expect(w.find('.health').text()).toBe('尚未檢查')
    devices = [dev({ last_health: summary() })]
    w = mnt(); await flushPromises()
    expect(w.find('.health').text()).toContain('2026-11-07 07:00:09')
    expect(w.find('.health').text()).toContain('有效告警 1、已解除 1、不確定 1')
  })

  it('admin 按「立即檢查」走 POST /health-check；結果顯示；清單重新載入', async () => {
    const w = mnt(); await flushPromises()
    devices = [dev({ last_health: summary({ trigger: 'manual' }) })]
    await btn(w, '立即檢查')!.trigger('click'); await flushPromises()
    const c = calls.find(x => x.url === '/api/storage/devices/1/health-check')!
    expect(c.opts.method).toBe('POST')
    expect(w.text()).toContain('健康／告警檢查完成')
    expect(w.find('.health').text()).toContain('有效告警 1')
  })

  it('立即檢查不完整：標「未完成」與原因', async () => {
    checkReply = { ok: false, status: 'partial', message: '結果不完整：健康：失敗（Storage 回應錯誤（HTTP 500））' }
    const w = mnt(); await flushPromises()
    await btn(w, '立即檢查')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('健康／告警檢查未完成')
    expect(w.text()).toContain('結果不完整')
  })

  it('非 admin：沒有「立即檢查」，但可以開唯讀的「健康／告警」，全程只有 GET', async () => {
    admin = false
    const w = mnt(); await flushPromises()
    expect(btn(w, '立即檢查')).toBeUndefined()
    await btn(w, '健康／告警')!.trigger('click'); await flushPromises()
    expect(calls.some(x => x.url === '/api/storage/devices/1/health')).toBe(true)
    expect(w.findAll('.row-SA').length).toBe(3)
    expect(calls.every(x => (x.opts?.method ?? 'GET') === 'GET')).toBe(true)
  })

  it('明細：資料時間、健康原始值、有效／已解除／不確定／已讀 各自計數，點了下鑽成清單', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '健康／告警')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('資料時間 2026-11-07 07:00:09')
    expect(w.text()).toContain('設備回傳原始值 5')
    expect(chip(w, '有效 ')!.text()).toBe('有效 1')
    expect(chip(w, '已解除')!.text()).toBe('已解除 1')
    expect(chip(w, '不確定')!.text()).toBe('不確定 1')
    await chip(w, '有效 ')!.trigger('click')
    expect(w.findAll('.row-SA').length).toBe(1)
    expect(w.find('.row-SA .cls').text()).toBe('有效')
    await chip(w, '已解除')!.trigger('click')
    expect(w.find('.row-SA .msg').text()).toBe('m2')
    await chip(w, '不確定')!.trigger('click')
    expect(w.find('.row-SA .note').text()).toContain('Updating')
    await chip(w, '已讀')!.trigger('click')
    expect(w.findAll('.row-SA').length).toBe(1)                                          // 只有 a1 已讀；已讀不等於解除，所以它仍在「有效」
    await chip(w, '全部')!.trigger('click')
    expect(w.findAll('.row-SA').length).toBe(3)
  })

  it('告警失敗：計數顯示「未取得」不是 0，分類按鈕不能點，空狀態寫明不代表沒有告警', async () => {
    detailReply = { ...std(), alerts: [], batch: batch({ status: 'partial', complete: false, alert_status: 'failed', alert_code: 'http_error', alert_msg: 'HTTP 500',
      n_total: null, n_effective: null, n_resolved: null, n_uncertain: null, n_acked: null, n_manual: null,
      verdict: verdict({ level: 'incomplete', text: '健康原始值 5；告警未取得' }) }) }
    const w = mnt(); await flushPromises()
    await btn(w, '健康／告警')!.trigger('click'); await flushPromises()
    expect(chip(w, '有效 ')!.text()).toBe('有效 未取得')
    expect(chip(w, '有效 ')!.attributes('disabled')).toBeDefined()
    expect(w.text()).not.toMatch(/有效 0/)
    expect(w.find('.empty-SA').text()).toContain('不代表沒有告警')
    expect(w.text()).toContain('告警未取得')
  })

  it('告警不完整（分頁中斷）：計數寫「至少 N」；告警空但健康未知時一句話不說正常', async () => {
    detailReply = { ...std(), batch: batch({ status: 'partial', complete: false, alert_status: 'partial', alert_code: 'unauthorized', n_total: 3,
      health_status: 'failed', health_msg: 'HTTP 500', verdict: verdict({ level: 'incomplete', text: '健康未知（讀取失敗）；告警不完整（至少有效 1、不確定 1）' }) }) }
    const w = mnt(); await flushPromises()
    await btn(w, '健康／告警')!.trigger('click'); await flushPromises()
    expect(chip(w, '有效 ')!.text()).toBe('有效 至少 1')
    expect(w.text()).toContain('健康未知')
    expect(w.find('.verdict').text()).not.toContain('正常')
  })

  it('最近一次檢查失敗：標出目前顯示的是更早、最近一次有資料的檢查；過期標示', async () => {
    detailReply = { ...std(), latest: batch({ id: 9, status: 'failed', message: '檢查失敗：連線逾時', finished_at: '2026-11-08 07:00:30' }),
      batch: batch({ verdict: verdict({ stale: true, text: '健康原始值 5；有效告警 1、已解除 1、不確定 1　⚠ 資料已過期' }) }) }
    const w = mnt(); await flushPromises()
    await btn(w, '健康／告警')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('注意：最近一次檢查（2026-11-08 07:00:30）失敗')
    expect(w.text()).toContain('資料已過期')
  })

  it('還沒有紀錄：說明下一步，不顯示 0', async () => {
    detailReply = { ...std(), batch: null, latest: null, alerts: [], history: [] }
    const w = mnt(); await flushPromises()
    await btn(w, '健康／告警')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('還沒有檢查紀錄')
    expect(w.findAll('.row-SA').length).toBe(0)
  })
})
