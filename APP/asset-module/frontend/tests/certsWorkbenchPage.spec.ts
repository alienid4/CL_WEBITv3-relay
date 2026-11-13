// 憑證盤點（7.5）清單頁與批次視窗實掛：條件過濾進網址並重新查詢、快捷區間、標籤與清除、全選目前篩選結果（上限 500）、
// 批次兩段式（預覽→確認、預覽後條件變了要重預覽、409 要重來）、空白頁引導、新增無到期日的提示、檢視者看不到寫入按鈕。
// Nuxt 自動匯入以 stubGlobal 提供；V2Page／V2Table 為簡化替身；API 全假、無網路（資料用 192.0.2.x／example.test）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as filters from '../composables/certFilters'
import * as fmt from '../composables/certFormat'
import Page from '../pages/v2/certs/index.vue'
import CertBatchModal from '../components/CertBatchModal.vue'
import CertReplaceModal from '../components/CertReplaceModal.vue'

const route = reactive<{ params: Record<string, string>; query: Record<string, string> }>({ params: {}, query: {} })
const calls: { url: string; opts: any }[] = []
const exported: { n: string; h: string[]; b: any[][] }[] = []
const toasts: { m: string; kind?: string }[] = []
let viewer = false
let admin = true
let matchedTotal = 3
let summaryOverride: any = null
let guarded: (u: string, o: any) => Promise<any>

const item = (i: number) => ({ id: `id${i}`, seq: i, name: `s${i}.example.test`, environment: '正式', endpoint: `192.0.2.${i}`, port: 443, sni: '', mount_type: 'F5', req_unit: '營運部',
  not_after_local: '2026-12-01 10:00 (UTC+8)', remain_text: '剩 30 天', risk: '中', status: 'd30', status_label: '8–30 天', planned_date: '', announce_needed: '', replace_status: '',
  source_label: '手動', data_updated_local: '2026-11-01 08:00 (UTC+8)', active: true, deactivate_reason_label: '' })
const SUMMARY = () => summaryOverride ?? ({ cards: [{ key: 'none', label: '無到期資料', count: 1 }, { key: 'd30', label: '8–30 天', count: 2 }], total_active: 3, inactive: 0, thresholds: [45, 30, 14],
  now_utc: '', ready: true, can_see_contacts: admin, import_info: { import_items: 2, manual_items: 1, last_import_at: '2026-11-01T00:00:00Z', last_import_local: '2026-11-01 08:00 (UTC+8)', last_import_file: 'survey.xlsx' } })
const OPTS = { mount_types: [{ name: 'F5', active: true, builtin: true, in_use: 3 }, { name: 'Tomcat', active: true, builtin: false, in_use: 0 }], replace_statuses: ['待排程', '已排程', '已更換', '已驗測'],
  environments: ['正式', '測試'], deactivate_reasons: [], units: ['營運部'], batch_max: 500, search_fields: ['憑證名稱', '端點', 'SNI', '申請單位', 'F5 BIG-IP', '主機 IP', 'SAN', '序號'] }

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  if (u === '/api/certs/summary') return Promise.resolve(SUMMARY())
  if (u === '/api/certs/options') return Promise.resolve(OPTS)
  if (u === '/api/certs/batches') return Promise.resolve({ batches: [] })
  if (u === '/api/certs/items') {
    const n = o?.query?.size === 0 ? matchedTotal : Math.min(matchedTotal, 3)
    return Promise.resolve({ items: Array.from({ length: n }, (_, i) => item(i + 1)), matched: matchedTotal, total: 3 })
  }
  return Promise.resolve({})
}

beforeEach(() => {
  route.query = {}
  calls.length = 0
  exported.length = 0
  toasts.length = 0
  viewer = false; admin = true; matchedTotal = 3; summaryOverride = null
  guarded = async () => ({})
  for (const [k, v] of Object.entries({ ...filters, ...fmt, ref, reactive, computed, watch, onMounted, onBeforeUnmount })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useTableExport', () => ({ exportTable: (n: string, h: string[], b: any[][]) => { exported.push({ n, h, b }) }, busy: ref(false) }))
  vi.stubGlobal('useV2ExportName', () => () => '憑證盤點')
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string, kind?: string) => toasts.push({ m, kind }) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: (u: string, o: any) => { calls.push({ url: u, opts: o }); return guarded(u, o) } }))
  vi.stubGlobal('navigateTo', () => {})
  vi.stubGlobal('csrfHeaders', () => ({}))
  vi.stubGlobal('httpReason', async () => '')
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Page = defineComponent({ props: ['modelValue', 'tabs', 'back'], setup(_p, { slots }) { return () => h('div', [slots.title?.(), slots.conclusion?.(), slots.actions?.(), slots.default?.(), slots.tab?.(), ]) } })
const V2Table = defineComponent({ props: ['rows', 'empty', 'cols', 'prefix', 'selected', 'selectable', 'server', 'exportAll', 'rowKey', 'unit'], emits: ['sort', 'page', 'update:selected'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl' }, p.rows.length
    ? p.rows.map((r: any) => h('div', { class: 'row', 'data-id': r.id }, [`${p.prefix}${r.__no} ${r.name}`, slots['cell-replace_status']?.({ row: r }), slots['cell-ops']?.({ row: r })]))
    : [h('p', { class: 'tbl-empty' }, p.empty)])
} })
const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
async function mountPage() {
  const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(Page) }) })
  const w = mount(Wrap, { global: { components: { V2Page, V2Table, InfoNote: passthrough, NuxtLink: passthrough, CertBatchModal, CertReplaceModal },
    config: { globalProperties: { ...fmt, ...filters } as any } } })
  await flushPromises()
  return w
}
const itemCalls = () => calls.filter(c => c.url === '/api/certs/items')
const lastItemQuery = () => itemCalls().at(-1)!.opts.query
const btn = (w: any, text: string) => w.findAll('button').find((b: any) => b.text().includes(text))

describe('條件過濾：進網址、重新查詢、可清除', () => {
  it('載入時帶網址上的條件；顯示「符合 N 筆／共 M 筆」；搜尋框提示列出支援欄位', async () => {
    route.query = { env: '正式', unit: '營運部', sort: 'name', desc: '1' }
    const w = await mountPage()
    expect(lastItemQuery()).toMatchObject({ env: '正式', unit: '營運部', sort: 'name', desc: true, page: 1, size: 100 })
    expect(w.text()).toContain('符合 3 筆／共 3 筆（啟用中）')
    expect(w.find('input.srch').attributes('placeholder')).toBe('搜尋：憑證名稱、端點、SNI、申請單位、F5 BIG-IP、主機 IP、SAN、序號')
    expect(w.text()).toContain('環境：正式')
  })

  it('選下拉＝寫進網址並重新查詢；兩個條件可組合；換條件回到第 1 頁', async () => {
    const w = await mountPage()
    const sels = w.findAll('select.f select, label.f select')
    const byLabel = (t: string) => w.findAll('label.f').find((l: any) => l.text().startsWith(t))!.find('select')
    await byLabel('環境').setValue('正式')
    await flushPromises()
    expect(route.query.env).toBe('正式')
    await byLabel('更換進度').setValue('_none')
    await flushPromises()
    expect(lastItemQuery()).toMatchObject({ env: '正式', rs: '_none' })
    expect(sels.length).toBeGreaterThan(5)
  })

  it('快捷「下個月」＝UTC+8 日曆月第一天到最後一天；再點一次取消', async () => {
    const w = await mountPage()
    await btn(w, '下個月')!.trigger('click')
    await flushPromises()
    const want = filters.certDuePreset('next_month')
    expect(route.query).toMatchObject(want)
    expect(lastItemQuery()).toMatchObject(want)
    expect(btn(w, '下個月')!.classes()).toContain('on')
    await btn(w, '下個月')!.trigger('click')
    await flushPromises()
    expect(route.query.due_from).toBeUndefined()
  })

  it('標籤 ✕ 只移除該條件；「清除篩選」全部清掉；沒條件時沒有清除鈕', async () => {
    route.query = { env: '正式', mount: 'F5', sort: 'name' }
    const w = await mountPage()
    expect(btn(w, '清除篩選（2）')).toBeTruthy()
    await w.findAll('.chip .x')[0].trigger('click')
    await flushPromises()
    expect(route.query.env).toBeUndefined()
    expect(route.query.mount).toBe('F5')
    await btn(w, '清除篩選')!.trigger('click')
    await flushPromises()
    expect(route.query.mount).toBeUndefined()
    expect(route.query.sort).toBe('name')              // 排序不是篩選條件，保留
    expect(btn(w, '清除篩選')).toBeUndefined()
  })

  it('清單連到詳情頁時帶 ret（篩選與排序脈絡）', async () => {
    route.query = { env: '正式', sort: 'name' }
    await mountPage()
    expect(filters.certDetailLink('id1', route.query)).toContain('ret=')
  })
})

describe('批次更新入口', () => {
  it('全選目前篩選結果：向後端要全部、勾選數等於符合筆數；超過 500 不能全選', async () => {
    const w = await mountPage()
    await btn(w, '全選目前篩選結果（3 筆）')!.trigger('click')
    await flushPromises()
    expect(lastItemQuery()).toMatchObject({ size: 0 })
    expect(w.text()).toContain('已選 3 筆')
    matchedTotal = 501
    const w2 = await mountPage()
    await btn(w2, '全選目前篩選結果（501 筆）')!.trigger('click')
    await flushPromises()
    expect(toasts.at(-1)!.m).toContain('超過一次上限 500')
    expect(w2.text()).toContain('已選 0 筆')
  })

  it('沒勾選時批次更新鈕不能按；勾了之後開視窗', async () => {
    const w = await mountPage()
    expect(btn(w, '批次更新（0 筆）')!.attributes('disabled')).toBeDefined()
    await btn(w, '全選目前篩選結果')!.trigger('click')
    await flushPromises()
    await btn(w, '批次更新（3 筆）')!.trigger('click')
    expect(w.text()).toContain('批次更新（3 筆）')
    expect(w.findComponent(CertBatchModal).exists()).toBe(true)
  })

  it('檢視者：沒有新增、批次、選取按鈕；更換進度只讀', async () => {
    viewer = true
    const w = await mountPage()
    expect(btn(w, '新增憑證項目')).toBeUndefined()
    expect(btn(w, '批次更新')).toBeUndefined()
    expect(w.find('select.sel').exists()).toBe(false)
  })

  it('清單上改更換進度＝PUT 只送 replace_status', async () => {
    guarded = async () => ({ changed: ['replace_status'] })
    const w = await mountPage()
    await w.find('select.sel').setValue('已驗測')
    await flushPromises()
    const put = calls.find(c => c.opts?.method === 'PUT')!
    expect(put.url).toBe('/api/certs/items/id1')
    expect(put.opts.body).toEqual({ replace_status: '已驗測' })
  })
})

describe('空白頁與新增', () => {
  it('完全沒資料：講下一步、兩個入口（新增、到資料匯入）', async () => {
    summaryOverride = { ...SUMMARY(), cards: [], total_active: 0, inactive: 0 }
    matchedTotal = 0
    const w = await mountPage()
    expect(w.text()).toContain('還沒有任何憑證項目')
    expect(btn(w, '＋ 新增憑證項目')).toBeTruthy()
    expect(w.html()).toContain('到資料匯入（匯入憑證調查表）')
  })

  it('資料來源列：顯示匯入／手動筆數與最後匯入時間', async () => {
    const w = await mountPage()
    expect(w.text()).toContain('憑證調查表匯入 2 筆')
    expect(w.text()).toContain('2026-11-01 08:00 (UTC+8)')
    expect(w.text()).toContain('survey.xlsx')
  })

  it('新增沒填到期日：明確提示「尚無到期日，不會出現在到期風險卡片」', async () => {
    guarded = async () => ({ id: 'newid' })
    const w = await mountPage()
    await btn(w, '新增憑證項目')!.trigger('click')
    await w.find('form.frm input[required]').setValue('n.example.test')
    await w.find('form.frm').trigger('submit')
    await flushPromises()
    const t = toasts.find(x => x.m.includes('尚無到期日'))!
    expect(t.m).toContain('不會出現在到期風險卡片')
    expect(t.kind).toBe('warn')
    expect(calls.some(c => c.url.endsWith('/observations'))).toBe(false)
  })

  it('新增表單：必填欄位標星號、掛載型態選項來自設定（含自訂的 Tomcat）', async () => {
    const w = await mountPage()
    await btn(w, '新增憑證項目')!.trigger('click')
    expect(w.find('form.frm').text()).toContain('為必填')
    expect(w.find('form.frm .req').exists()).toBe(true)
    const mount = w.findAll('form.frm label').find((l: any) => l.text().startsWith('掛載型態'))!
    expect(mount.findAll('option').map((o: any) => o.text())).toEqual(['（未填）', 'F5', 'Tomcat'])
  })

  it('「無到期資料」卡片做成醒目的待補清單，點了加進篩選', async () => {
    const w = await mountPage()
    const card = w.find('button.card.todo')
    expect(card.text()).toContain('待補登到期日')
    await card.trigger('click')
    await flushPromises()
    expect(route.query.status).toBe('none')
  })
})

describe('批次視窗：兩段式', () => {
  const PV = { selected: 2, will_change: 2, unchanged: 0, inactive_skipped: 0, digest: 'd1', ids: ['a', 'b'], max: 500,
    fields: [{ field: 'replace_status', label: '更換進度', new: '已排程', from: [{ value: '（空）', count: 2 }], will_change: 2 }],
    sample: [{ id: 'a', seq: 1, name: 's1.example.test', environment: '正式', endpoint: '192.0.2.1', changes: [{ label: '更換進度', old: '（空）', new: '已排程' }] }] }
  let apiImpl: (u: string, o: any) => Promise<any>
  beforeEach(() => { vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => { calls.push({ url: u, opts: o }); return apiImpl(u, o) } })) })
  const mountModal = () => mount(CertBatchModal, { props: { ids: ['a', 'b'], isAdmin: true, statuses: OPTS.replace_statuses, max: 500 }, global: { components: { InfoNote: passthrough, V2Table: passthrough } } })

  it('沒勾欄位不能預覽；預覽之前不能確認；預覽只送勾的欄位', async () => {
    apiImpl = async () => PV
    const w = mountModal()
    expect(btn(w, '預覽影響')!.attributes('disabled')).toBeDefined()
    expect(btn(w, '確認套用')!.attributes('disabled')).toBeDefined()
    await w.findAll('input[type=checkbox]')[0].setValue(true)
    await btn(w, '預覽影響')!.trigger('click')
    await flushPromises()
    const pv = calls.find(c => c.url === '/api/certs/batch/preview')!
    expect(pv.opts.body).toEqual({ ids: ['a', 'b'], changes: { replace_status: '待排程' } })
    expect(w.text()).toContain('2 筆會變更')
    expect(btn(w, '確認套用（2 筆）')!.attributes('disabled')).toBeUndefined()
  })

  it('預覽後改了條件，預覽作廢必須重新預覽（避免套用的不是看過的版本）', async () => {
    apiImpl = async () => PV
    const w = mountModal()
    await w.findAll('input[type=checkbox]')[0].setValue(true)
    await btn(w, '預覽影響')!.trigger('click'); await flushPromises()
    await w.find('select').setValue('已更換')
    await flushPromises()
    expect(w.text()).not.toContain('2 筆會變更')
    expect(btn(w, '確認套用')!.attributes('disabled')).toBeDefined()
  })

  it('確認：帶預覽回傳的 ids＋digest＋同樣的 changes；成功後通知上層', async () => {
    apiImpl = async (u) => (u.endsWith('/apply') ? { batch_id: 'abcdef1234', changed: 2 } : PV)
    const w = mountModal()
    await w.findAll('input[type=checkbox]')[0].setValue(true)
    await btn(w, '預覽影響')!.trigger('click'); await flushPromises()
    await btn(w, '確認套用（2 筆）')!.trigger('click'); await flushPromises()
    const ap = calls.find(c => c.url === '/api/certs/batch/apply')!
    expect(ap.opts.body).toEqual({ ids: ['a', 'b'], changes: { replace_status: '待排程' }, digest: 'd1' })
    expect(w.emitted('done')![0]).toEqual(['abcdef1234'])
  })

  it('確認時 409（預覽後資料變了）：顯示原因、預覽作廢、不通知上層', async () => {
    apiImpl = async (u) => { if (u.endsWith('/apply')) throw { statusCode: 409, data: { detail: '預覽之後資料有變動，請重新預覽再確認' } }; return PV }
    const w = mountModal()
    await w.findAll('input[type=checkbox]')[0].setValue(true)
    await btn(w, '預覽影響')!.trigger('click'); await flushPromises()
    await btn(w, '確認套用（2 筆）')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('請重新預覽再確認')
    expect(w.emitted('done')).toBeUndefined()
    expect(btn(w, '確認套用')!.attributes('disabled')).toBeDefined()
  })

  it('非管理員看不到「執行人」欄；預覽失敗顯示後端訊息', async () => {
    apiImpl = async () => { throw { data: { detail: '一次最多批次更新 500 筆' } } }
    const w = mount(CertBatchModal, { props: { ids: ['a'], isAdmin: false, statuses: OPTS.replace_statuses, max: 500 }, global: { components: { InfoNote: passthrough, V2Table: passthrough } } })
    expect(w.findAll('label.row').map((l: any) => l.text())).toHaveLength(3)
    expect(w.findAll('label.row').some((l: any) => l.text().startsWith('執行人'))).toBe(false)
    await w.findAll('input[type=checkbox]')[0].setValue(true)
    await btn(w, '預覽影響')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('一次最多批次更新 500 筆')
  })
})


describe('第三輪（挑剔使用者審查）', () => {
  it('快捷鍵有「7 天內」「本週」；點「7 天內」＝within=7', async () => {
    const w = await mountPage()
    expect(btn(w, '本週')).toBeTruthy()
    await btn(w, '7 天內')!.trigger('click'); await flushPromises()
    expect(lastItemQuery()).toMatchObject({ within: '7' })
  })

  it('非管理員清單頂部有「窗口資料已遮罩」一行；管理員沒有', async () => {
    admin = false
    expect((await mountPage()).text()).toContain('窗口資料已遮罩，請洽管理員')
    admin = true
    expect((await mountPage()).text()).not.toContain('窗口資料已遮罩')
  })

  it('預設（只看啟用中）不顯示「停用」欄；看已停用／全部才出現', async () => {
    const labels = (w: any) => (w.findComponent(V2Table).props('cols') as any[]).map(c => c.label)
    expect(labels(await mountPage())).not.toContain('停用')
    route.query = { active: 'off' }
    expect(labels(await mountPage())).toContain('停用')
  })

  it('篩選後沒有結果：列出套用中的條件、一鍵清除', async () => {
    route.query = { env: '正式', mount: 'F5' }
    matchedTotal = 0
    const w = await mountPage()
    expect(w.text()).toContain('目前條件下沒有符合的憑證')
    expect(w.text()).toContain('環境：正式；掛載型態：F5')
    await btn(w, '一鍵清除篩選')!.trigger('click'); await flushPromises()
    expect(route.query.env).toBeUndefined()
  })

  it('沒有篩選條件時不顯示「沒有符合」那塊', async () => {
    matchedTotal = 0
    expect((await mountPage()).text()).not.toContain('目前條件下沒有符合的憑證')
  })

  it('更換進度欄名帶「僅登記」；新增表單沒有可填的預排更換時間文字欄、需公告是下拉', async () => {
    const w = await mountPage()
    const labels = (w.findComponent(V2Table).props('cols') as any[]).map(c => c.label)
    expect(labels).toContain('更換進度（僅登記）')
    await btn(w, '新增憑證項目')!.trigger('click')
    const f = w.find('form.frm')
    expect(f.text()).not.toContain('預排更換時間')
    const ann = f.findAll('label').find((l: any) => l.text().startsWith('需公告'))!
    expect(ann.find('select').exists()).toBe(true)
    expect(f.text()).toContain('僅登記，不會通知任何人')
  })

  it('復原有衝突：彈窗列出衝突清單；批次紀錄有「影響項次」欄', async () => {
    route.query = { tab: 'batches' }
    const base = fakeApi
    vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => (u === '/api/certs/batches'
      ? Promise.resolve({ batches: [{ id: 'b1', short: 'b1', created_local: 't', item_count: 2, fields: ['更換進度'], summary: '更換進度→已排程', item_seqs: [1, 3], state: 'active', remaining: 2, undone_count: 0 }] })
      : base(u, o)) }))
    guarded = async () => ({ restored: 1, conflicts: [{ id: 'id3', seq: 3, name: 's3.example.test', fields: ['replace_status'] }] })
    vi.stubGlobal('confirm', () => true)
    const w = await mountPage()
    const t = w.findAllComponents(V2Table).find((x: any) => (x.props('cols') as any[]).some(c => c.k === 'seqs_text'))!
    expect(t.props('rows')[0].seqs_text).toBe('C1、C3')
    await btn(w, '整批復原')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('復原結果')
    expect(w.text()).toContain('s3.example.test')
    expect(w.text()).toContain('1 筆在批次之後又被改過')
  })
})


describe('第四輪', () => {
  it('快捷鍵有「下週」；點了＝下個週一到週日', async () => {
    const w = await mountPage()
    await btn(w, '下週')!.trigger('click'); await flushPromises()
    expect(route.query).toMatchObject(filters.certDuePreset('next_week'))
  })

  it('欄名白話：到期分段、風險等級、更換進度；有「動作」欄，唯讀帳號沒有', async () => {
    const w = await mountPage()
    const labels = (w.findComponent(V2Table).props('cols') as any[]).map(c => c.label)
    expect(labels).toEqual(expect.arrayContaining(['到期分段', '風險等級', '更換進度（僅登記）', '動作']))
    expect(labels).not.toContain('狀態')
    expect(labels).not.toContain('風險標示')
    expect(btn(w, '標記已更換')).toBeTruthy()
    expect(btn(w, '排定更換')).toBeTruthy()
    viewer = true
    const v = await mountPage()
    expect((v.findComponent(V2Table).props('cols') as any[]).map(c => c.label)).not.toContain('動作')
  })

  it('標記已更換：沒填新到期日也沒勾稍後補登不能送出；勾稍後補登才送', async () => {
    const w = await mountPage()
    await btn(w, '標記已更換')!.trigger('click')
    expect(w.text()).toContain('標記已更換：s1.example.test')
    await btn(w, '確定')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('請填新的到期時間，或明確勾選「稍後補登」')
    expect(calls.some(c => c.url.endsWith('/mark-replaced'))).toBe(false)
    await w.find('.mask input[type=checkbox]').setValue(true)
    guarded = async () => ({ batch_id: 'b' })
    await btn(w, '確定')!.trigger('click'); await flushPromises()
    const c = calls.find(x => x.url === '/api/certs/items/id1/mark-replaced')!
    expect(c.opts.body).toMatchObject({ later: true, void_old: false })
  })

  it('標記已更換：填新到期日＋作廢舊觀測 → 送出 not_after 與 void_old', async () => {
    guarded = async () => ({ batch_id: 'b' })
    const w = await mountPage()
    await btn(w, '標記已更換')!.trigger('click')
    await w.find('.mask input[type=datetime-local]').setValue('2028-01-15T10:00')
    await w.findAll('.mask input[type=checkbox]')[1].setValue(true)
    await btn(w, '確定')!.trigger('click'); await flushPromises()
    expect(calls.find(x => x.url.endsWith('/mark-replaced'))!.opts.body).toMatchObject({ not_after: '2028-01-15 10:00', later: false, void_old: true })
  })

  it('排定更換：一次送預排日期＋已排程；沒選日期不送', async () => {
    guarded = async () => ({})
    const w = await mountPage()
    await btn(w, '排定更換')!.trigger('click')
    await btn(w, '確定')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('請選預排日期')
    await w.find('.mask input[type=date]').setValue('2026-12-05')
    await btn(w, '確定')!.trigger('click'); await flushPromises()
    const put = calls.find(x => x.opts?.method === 'PUT')!
    expect(put.opts.body).toEqual({ planned_date: '2026-12-05', replace_status: '已排程' })
  })

  it('清單下拉選「已更換」→ 開標記已更換視窗，不直接存', async () => {
    const w = await mountPage()
    await w.find('select.sel').setValue('已更換'); await flushPromises()
    expect(w.text()).toContain('標記已更換：s1.example.test')
    expect(calls.some(c => c.opts?.method === 'PUT')).toBe(false)
  })

  it('匯出所選：只匯出勾選的列', async () => {
    const w = await mountPage()
    expect(btn(w, '匯出所選')).toBeUndefined()
    ;(w.findComponent(V2Table).vm as any).$emit('update:selected', { id2: true })
    await flushPromises()
    await btn(w, '匯出所選（1 筆）')!.trigger('click'); await flushPromises()
    expect(exported).toHaveLength(1)
    expect(exported[0].b).toHaveLength(1)
    expect(exported[0].b[0][1]).toBe('s2.example.test')
    expect(exported[0].h).toContain('更換進度（僅登記）')
    expect(exported[0].h).not.toContain('動作')
  })

  it('含人名匯出：先顯示目前篩選與筆數', async () => {
    route.query = { tab: 'settings', env: '正式' }
    const w = await mountPage()
    expect(w.text()).toContain('共 3 筆')
    expect(w.text()).toContain('環境：正式')
    expect(btn(w, '匯出含人名（3 筆）')).toBeTruthy()
  })
})


describe('第六輪：待補新到期日', () => {
  it('更多條件有「待補新到期日」篩選；風險等級有「待補到期日」選項；選了進網址', async () => {
    const w = await mountPage()
    await btn(w, '更多條件')!.trigger('click')
    const lab = (t: string) => w.findAll('label.f').find((l: any) => l.text().startsWith(t))!
    await lab('待補新到期日').find('select').setValue('1'); await flushPromises()
    expect(route.query.pending).toBe('1')
    expect(lastItemQuery()).toMatchObject({ pending: '1' })
    expect(w.text()).toContain('已更換待補到期日')
    expect(lab('風險等級').findAll('option').map((o: any) => o.text())).toContain('待補到期日')
  })

  it('標記已更換視窗對「稍後補登」講真話：舊到期日不再判斷、暫歸無到期資料', async () => {
    const w = await mountPage()
    await btn(w, '標記已更換')!.trigger('click')
    expect(w.text()).toContain('補登前這筆顯示「待補到期日」')
    expect(w.text()).toContain('舊的到期日不再拿來判斷')
  })
})
