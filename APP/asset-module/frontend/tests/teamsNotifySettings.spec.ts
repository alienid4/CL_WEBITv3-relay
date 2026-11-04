// Teams 通知設定區塊實掛：網址欄只寫不回顯、設定後清空輸入、預覽走 dry_run 端點且不送、投遞紀錄表有項次與重送、
// 檢視者（不可編輯）看不到寫入按鈕。Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/TeamsNotifySettings.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
let canEdit = true
let authOff = false
let holdText: string | null = null
let liveConfirmed = false
const HOOK = 'https://prod-1.logic.azure.com/workflows/MARKER-xyz'
const toasts: string[] = []

const LOG = [
  { id: 3, __no: 3, kind: 'alert', source_ref: 'wug:2', severity: 'critical', title: '嚴重告警 sw2', status: 'uncertain', status_label: '不確定（可能已送達）', attempts: 1, last_http: null, last_error: 'TimeoutError', created_at: '2026-11-02 09:05:00', sent_at: null, next_try_at: null },
  { id: 4, __no: 4, kind: 'test', source_ref: 'test', severity: 'info', title: '測試', status: 'dead', status_label: '放棄', attempts: 1, last_http: 500, last_error: 'HTTP 500', created_at: '2026-11-02 09:06:00', sent_at: null, next_try_at: null },
  { id: 2, __no: 2, kind: 'alert', source_ref: 'wug:1', severity: 'critical', title: '嚴重告警 sw1', status: 'dead', status_label: '放棄', attempts: 5, last_http: 503, last_error: 'HTTP 503', created_at: '2026-11-02 09:00:00', sent_at: null, next_try_at: null },
  { id: 1, __no: 1, kind: 'summary_daily', source_ref: 'sum', severity: 'info', title: '每日摘要', status: 'sent', status_label: '已送出', attempts: 1, last_http: 202, last_error: null, created_at: '2026-11-02 08:00:00', sent_at: 'x', next_try_at: null },
]
const settings = () => ({ enabled: false, min_severity: 'warning', send_recovery: true, send_incidents: true, summary_daily: false, summary_weekly: false, summary_time: '08:00',
  weekly_day: 0, quiet_start: '', quiet_end: '', max_attempts: 5, base_backoff_sec: 60, live_confirmed: liveConfirmed, dns_guard: true, base_url: '', webhook_set: true })

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  if (u === '/api/teams-notify/settings' && !o.method) return Promise.resolve({ settings: settings(), corrupt: null, missing: [], can_edit: canEdit, auth_disabled: authOff, hold: holdText, queue: { pending: 2, uncertain: 1 }, webhook_host: 'prod-1.example.logic.azure.com' })
  if (u === '/api/teams-notify/log') return Promise.resolve({ items: LOG })
  if (u === '/api/teams-notify/preview') return Promise.resolve({ title: '【示範】嚴重告警', demo: true, card: { attachments: [{ content: { body: [{ type: 'FactSet', facts: [{ title: '設備', value: '示範設備' }] }] } }] } })
  if (u === '/api/teams-notify/test-live') return liveReply()
  return Promise.resolve({ ok: true })
}
let liveReply: () => Promise<any> = () => Promise.resolve({ ok: true, outcome: 'sent', http: 202, reason: '' })

const V2Table = defineComponent({
  props: ['rows', 'cols'],
  setup(p, { slots }) {
    return () => h('table', {}, p.rows.map((r: any) => h('tr', { class: 'row' }, [h('td', { class: 'no' }, r.__no), h('td', {}, r.status_label), h('td', { class: 'act' }, slots['cell-act']?.({ row: r }))])))
  },
})

beforeEach(() => {
  calls.length = 0; toasts.length = 0; canEdit = true; authOff = false; holdText = null; liveConfirmed = false
  for (const [k, v] of Object.entries({ ref, computed, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
  vi.stubGlobal('confirm', () => true)
})

const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, V2Table } } })

describe('Teams 通知設定', () => {
  it('網址欄不回顯：只顯示「已設定」，輸入框是密碼型且空白', async () => {
    const w = mnt(); await flushPromises()
    expect(w.text()).toContain('已設定')
    expect(w.html()).not.toContain('azure')
    const inp = w.find('input[type=password]')
    expect(inp.exists()).toBe(true)
    expect((inp.element as HTMLInputElement).value).toBe('')
  })

  it('設定網址後送出 PUT 並清空輸入', async () => {
    const w = mnt(); await flushPromises()
    await w.find('input[type=password]').setValue(HOOK)
    await w.findAll('button').find(b => b.text() === '設定')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/teams-notify/webhook')!
    expect(put.opts.method).toBe('PUT'); expect(put.opts.body.url).toBe(HOOK)
    expect((w.find('input[type=password]').element as HTMLInputElement).value).toBe('')
    expect(w.html()).not.toContain('MARKER-xyz')
  })

  it('儲存設定不帶 webhook_set 旗標', async () => {
    const w = mnt(); await flushPromises()
    await w.findAll('button').find(b => b.text() === '儲存')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/teams-notify/settings' && c.opts?.method === 'PUT')!
    expect(put.opts.body).not.toHaveProperty('webhook_set')
    expect(put.opts.body.min_severity).toBe('warning')
  })

  it('預覽只打 preview 端點（不送出），並標示示範', async () => {
    const w = mnt(); await flushPromises()
    await w.findAll('button').find(b => b.text().startsWith('預覽'))!.trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/teams-notify/preview')).toBe(true)
    expect(calls.some(c => c.url === '/api/teams-notify/test')).toBe(false)
    expect(w.text()).toContain('示範資料'); expect(w.text()).toContain('示範設備')
  })

  it('投遞紀錄有項次；放棄的列有「重送」、已送出的沒有', async () => {
    const w = mnt(); await flushPromises()
    const rows = w.findAll('tr.row')
    expect(rows.length).toBe(4)
    const byNo = (n: string) => rows.find(r => r.find('.no').text() === n)!
    expect(byNo('2').find('button').exists()).toBe(true)      // 放棄
    expect(byNo('3').find('button').exists()).toBe(true)      // 不確定：管理員可手動重送
    expect(byNo('1').find('button').exists()).toBe(false)     // 已送出
    expect(byNo('4').find('button').exists()).toBe(false)     // 測試列不能重送
    await byNo('3').find('button').trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/teams-notify/log/3/retry' && c.opts.method === 'POST')).toBe(true)
  })

  it('送不出去的原因與不確定的數量清楚顯示', async () => {
    holdText = '尚未確認對外送出（「對外送出已確認」未勾）'
    const w = mnt(); await flushPromises()
    expect(w.text()).toContain('目前不會送出：尚未確認對外送出')
    expect(w.text()).toContain('待送 2 則')
    expect(w.text()).toContain('1 則「不確定（可能已送達）」')
  })

  it('重送前提醒可能造成重複訊息；使用者取消就不送', async () => {
    const asked: string[] = []
    vi.stubGlobal('confirm', (m: string) => { asked.push(m); return false })
    const w = mnt(); await flushPromises()
    const rows = w.findAll('tr.row')
    await rows.find(r => r.find('.no').text() === '3')!.find('button').trigger('click'); await flushPromises()
    expect(asked[0]).toContain('重複訊息')
    expect(calls.some(c => c.url.endsWith('/retry'))).toBe(false)
  })

  it('DNS 內部位址檢查預設勾選，儲存時一併送出', async () => {
    const w = mnt(); await flushPromises()
    await w.findAll('button').find(b => b.text() === '儲存')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/teams-notify/settings' && c.opts?.method === 'PUT')!
    expect(put.opts.body.dns_guard).toBe(true)
  })

  it('送測試訊息：沒勾對外送出已確認就不能按；勾了先二次確認（只顯示網域），確認才送', async () => {
    const w1 = mnt(); await flushPromises()
    const b1 = w1.findAll('button').find(b => b.text().startsWith('送測試訊息到 Teams'))!
    expect(b1.attributes('disabled')).toBeDefined()
    w1.unmount()
    liveConfirmed = true
    const asked: string[] = []
    vi.stubGlobal('confirm', (m: string) => { asked.push(m); return false })
    const w = mnt(); await flushPromises()
    const b = w.findAll('button').find(b => b.text().startsWith('送測試訊息到 Teams'))!
    expect(b.attributes('disabled')).toBeUndefined()
    await b.trigger('click'); await flushPromises()
    expect(asked[0]).toContain('這會真的送一則測試訊息到 Teams 頻道'); expect(asked[0]).toContain('prod-1.example.logic.azure.com')
    expect(asked[0]).not.toContain('https://')
    expect(calls.some(c => c.url === '/api/teams-notify/test-live')).toBe(false)           // 取消＝沒送
    vi.stubGlobal('confirm', () => true)
    await b.trigger('click'); await flushPromises()
    const post = calls.find(c => c.url === '/api/teams-notify/test-live')!
    expect(post.opts.method).toBe('POST'); expect(post.opts.body).toEqual({ confirm: true })
    expect(toasts.some(t => t.includes('HTTP 202'))).toBe(true)
  })

  it('送測試訊息：不確定與 429 都如實顯示', async () => {
    liveConfirmed = true
    liveReply = () => Promise.resolve({ ok: false, outcome: 'uncertain', http: null, reason: 'TimeoutError' })
    let w = mnt(); await flushPromises()
    await w.findAll('button').find(b => b.text().startsWith('送測試訊息到 Teams'))!.trigger('click'); await flushPromises()
    expect(toasts.some(t => t.includes('結果不確定') && t.includes('不會自動重送'))).toBe(true)
    liveReply = () => Promise.reject({ data: { detail: '每 60 秒最多送一次測試訊息，請 30 秒後再試' } })
    await w.findAll('button').find(b => b.text().startsWith('送測試訊息到 Teams'))!.trigger('click'); await flushPromises()
    expect(toasts.some(t => t.includes('沒有送出') && t.includes('30 秒'))).toBe(true)
    liveReply = () => Promise.resolve({ ok: true, outcome: 'sent', http: 202, reason: '' })
  })

  it('登入停用時顯示原因並關閉所有寫入', async () => {
    canEdit = false; authOff = true
    const w = mnt(); await flushPromises()
    expect(w.text()).toContain('登入停用中')
    expect(w.findAll('button').map(b => b.text())).not.toContain('儲存')
    expect(w.find('input[type=password]').exists()).toBe(false)
  })

  it('不可編輯的帳號看不到儲存、設定、測試、重送', async () => {
    canEdit = false
    const w = mnt(); await flushPromises()
    const labels = w.findAll('button').map(b => b.text())
    expect(labels).not.toContain('儲存'); expect(labels).not.toContain('設定'); expect(labels.some(l => l.startsWith('測試送出'))).toBe(false)
    expect(w.find('input[type=password]').exists()).toBe(false)
    expect(w.find('tr.row .act button').exists()).toBe(false)
  })
})
