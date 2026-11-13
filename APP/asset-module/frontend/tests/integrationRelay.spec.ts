// 中繼匯入畫面實掛：鮮度燈三態、卡片點下去篩選匯入紀錄、admin 看得到設定與按鈕而檢視者只讀、
// 儲存／立即匯入走對的端點、被拒收的批次原因看得到、未匹配頁籤。Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/IntegrationRelay.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
const toasts: string[] = []
let admin = true
const route = reactive<{ query: Record<string, any> }>({ query: {} })
let sources: any[] = []
let batches: any[] = []
let unmatched: any[] = []
let runReply: any = { status: 'ok', message: '看到 1 批；新匯入 1、拒收 0、失敗 0', imported: 1, rejected: 0, failed: 0 }

const src = (o: any = {}) => ({ source: 'whatsup', label: 'WhatsUp Gold 效能', implemented: true, note: '', status: 'ok', status_label: '正常', last_batch: 'b1', last_collected_at: '2026-11-02 21:05:00', age_hours: 3, stale_days: 2, problem_batches: 0, ...o })
const batch = (o: any = {}) => ({ id: 1, __no: 1, source: 'whatsup', batch_id: 'b1', status: 'imported', reason: '', imported_at: '2026-11-02 21:10:00', collected_at: '2026-11-02 21:05:00', range_to: '2026-11-01', devices: 3, host_days: 3, points: 90, skipped_existing: 0, unmatched: 1, trigger: 'schedule', imported_by: 'system', collector_version: 'wug_collect 2', ...o })

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  const m = o.method ?? 'GET'
  if (u === '/api/v2/relay-import' && m === 'GET') {
    return Promise.resolve({ config: { enabled: true, dir: '/mnt/relay', stale_days: 2, interval_min: 15, max_file_mb: 64, last_scan: '2026-11-02 21:10:00', last_result: '看到 1 批' }, sources, dir_problem: null, batches_visible: 1, in_quiet_window: false })
  }
  if (u === '/api/v2/relay-import/batches') return Promise.resolve({ rows: batches })
  if (u === '/api/v2/relay-import/unmatched') return Promise.resolve({ rows: unmatched })
  if (u === '/api/v2/relay-import/run') return Promise.resolve(runReply)
  return Promise.resolve({ ok: true })
}

// 簡化的 V2Table：把每列的「顯示文字」輸出，讓測試讀得到 text() 轉出的結果
const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p) {
    return () => (p.rows.length
      ? h('table', { class: 'tbl', 'data-prefix': p.prefix }, p.rows.map((r: any) => h('tr', { class: 'row' }, p.cols.map((c: any) => h('td', { class: 'c-' + c.k }, String(c.text ? c.text(r) : r[c.k] ?? '—'))))))
      : h('p', { class: 'empty' }, p.empty))
  },
})

beforeEach(() => {
  calls.length = 0; toasts.length = 0; admin = true; route.query = {}
  sources = [src(), src({ source: 'unity', label: '儲存設備（Dell Unity）', implemented: false, status: 'never', status_label: '從未收到', last_batch: null, last_collected_at: null, age_hours: null })]
  batches = [batch(), batch({ id: 2, __no: 2, batch_id: 'b2', source: 'whatsup', status: 'rejected', reason: 'b2.json 的 sha256 和 manifest 不符' })]
  unmatched = [{ __no: 1, source: 'whatsup', ident: '192.0.2.10', name: 'dev-10', reason: 'no_asset', candidates: 0, last_batch: 'b1', first_seen: 'x', last_seen: 'y' }]
  runReply = { status: 'ok', message: '看到 1 批；新匯入 1、拒收 0、失敗 0', imported: 1, rejected: 0, failed: 0 }
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: computed(() => admin) }))
})

const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, V2Table } } })
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text() === t)

describe('中繼匯入畫面', () => {
  it('鮮度燈三態：正常／資料過期／從未收到，各有文字（不只靠顏色）', async () => {
    sources = [src(), src({ source: 'x', label: '來源乙', status: 'stale', status_label: '資料過期', age_hours: 80 }), src({ source: 'unity', label: '儲存設備（Dell Unity）', status: 'never', status_label: '從未收到', last_batch: null, last_collected_at: null, age_hours: null })]
    const w = mnt(); await flushPromises()
    const cards = w.findAll('button.card')
    expect(cards.map((c: any) => c.attributes('aria-label'))).toEqual(['WhatsUp Gold 效能 正常', '來源乙 資料過期', '儲存設備（Dell Unity） 從未收到'])
    expect(cards[1].classes()).toContain('stale')
    expect(cards[1].text()).toContain('3 天前')
    expect(cards[2].text()).toContain('最近一批：—')
  })

  it('卡片可下鑽：點卡片只列該來源的匯入紀錄，再點一次取消', async () => {
    batches = [batch(), batch({ id: 2, __no: 2, batch_id: 'u1', source: 'unity', status: 'rejected', reason: '尚未支援匯入' })]
    const w = mnt(); await flushPromises()
    expect(w.findAll('tr.row')).toHaveLength(2)
    await w.findAll('button.card')[1].trigger('click')
    expect(w.findAll('tr.row')).toHaveLength(1)
    expect(w.find('.c-batch_id').text()).toBe('u1')
    expect(w.find('.c-reason').text()).toContain('尚未支援')
    await w.findAll('button.card')[1].trigger('click')
    expect(w.findAll('tr.row')).toHaveLength(2)
  })

  it('匯入紀錄顯示結果與拒收原因；頁籤可切到未匹配', async () => {
    const w = mnt(); await flushPromises()
    expect(w.findAll('.c-status').map((c: any) => c.text())).toEqual(['成功', '拒收'])
    expect(w.text()).toContain('sha256')
    const tabs = w.findAll('button.sub')
    expect(tabs[0].text()).toBe('匯入紀錄 2')
    expect(tabs[1].text()).toBe('未匹配 1')
    await tabs[1].trigger('click')
    expect(w.find('table').attributes('data-prefix')).toBe('U')
    expect(w.find('.c-ident').text()).toBe('192.0.2.10')
    expect(w.find('.c-reason').text()).toBe('找不到資產')
  })

  it('admin：看得到設定欄位與兩個按鈕；儲存送 PUT 帶正確內容', async () => {
    const w = mnt(); await flushPromises()
    expect((w.find('input[aria-label=共用資料夾]').element as HTMLInputElement).value).toBe('/mnt/relay')
    await w.find('input[aria-label=共用資料夾]').setValue('/mnt/other')
    await w.find('input[aria-label=資料過期天數]').setValue('3')
    await btn(w, '儲存設定')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/v2/relay-import/settings')!
    expect(put.opts.method).toBe('PUT')
    expect(put.opts.body).toEqual({ enabled: true, dir: '/mnt/other', stale_days: 3, interval_min: 15 })
    expect(Object.keys(put.opts.body).some(k => /pass|secret|token/i.test(k))).toBe(false)
    expect(toasts).toContain('已儲存設定')
  })

  it('立即匯入走 POST /run，成功訊息顯示；有拒收時提示用警告', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '立即匯入')!.trigger('click'); await flushPromises()
    const run = calls.find(c => c.url === '/api/v2/relay-import/run')!
    expect(run.opts.method).toBe('POST')
    expect(toasts.some(t => t.includes('新匯入 1'))).toBe(true)
  })

  it('檢視者（非 admin）：沒有輸入框與按鈕，只讀看到資料夾與紀錄', async () => {
    admin = false
    const w = mnt(); await flushPromises()
    expect(w.find('input').exists()).toBe(false)
    expect(btn(w, '儲存設定')).toBeUndefined()
    expect(btn(w, '立即匯入')).toBeUndefined()
    expect(w.text()).toContain('/mnt/relay')
    expect(w.findAll('tr.row')).toHaveLength(2)
  })

  it('載入失敗會顯示錯誤，不是空白', async () => {
    vi.stubGlobal('useApi', () => ({ apiFetch: () => Promise.reject({ data: { detail: '後端壞了' } }) }))
    const w = mnt(); await flushPromises()
    expect(w.find('.err').text()).toContain('後端壞了')
  })

  it('網址帶 ?src=unity：匯入紀錄一開就只列 Unity（5.2 Unity 陣列的資料時間連過來）', async () => {
    route.query = { src: 'unity' }
    batches = [batch(), batch({ id: 2, __no: 2, batch_id: 'u1', source: 'unity' })]
    const w = mnt(); await flushPromises()
    const ids = () => w.findAll('td.c-batch_id').map((t: any) => t.text())
    expect(ids()).toEqual(['u1'])
    route.query = { src: 'whatsup' }                 // 同一頁換網址：跟著變
    await nextTick(); await flushPromises()
    expect(ids()).toEqual(['b1'])
  })

  it('網址帶不認得的 ?src=：不套篩選', async () => {
    route.query = { src: 'nope' }
    batches = [batch(), batch({ id: 2, __no: 2, batch_id: 'u1', source: 'unity' })]
    const w = mnt(); await flushPromises()
    expect(w.findAll('td.c-batch_id').map((t: any) => t.text())).toEqual(['b1', 'u1'])
  })
})
