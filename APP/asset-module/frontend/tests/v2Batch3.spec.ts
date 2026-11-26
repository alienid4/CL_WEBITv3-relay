// 三層分離第 3 批（2.070.x）：1.3 設備風險、8.3 EOS 追蹤（＋⚙ EOS 追蹤設定）、7.1 機櫃盤點「更多」頁籤、搬遷盤點兩階段、
// 2.1 新增主機兩模式、1.2 異常報告收合、10.1 主機組月報分頁、5.1 機房平面圖檢視。
// 改回舊結構（設定／示範資料回到操作畫面、全部攤開）就會紅。Nuxt 自動匯入以 stubGlobal 提供；V2Page／V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import { useSort } from '../composables/useSort'
import { pickRiskTab, riskTabKeys } from '../utils/riskTabs'
import { candRows, nightConfirmText } from '../utils/hkCandidates'
import RiskPage from '../pages/v2/risk.vue'
import EosFollow from '../components/v2/EosFollow.vue'
import EosFollowSettings from '../components/v2/EosFollowSettings.vue'
import InventoryFlow from '../components/v2/InventoryFlow.vue'
import AssetNew from '../components/v2/AssetNew.vue'
import Relocation from '../components/v2/Relocation.vue'
import Anomalies from '../components/v2/Anomalies.vue'
import HostMonthly from '../components/v2/HostMonthly.vue'
import CiaReg from '../pages/v2/ciareg.vue'
import Datacenter from '../pages/v2/datacenter.vue'

enableAutoUnmount(afterEach)
const route = reactive<{ query: Record<string, string>; params: Record<string, string> }>({ query: {}, params: {} })
let handler: (url: string, opts?: any) => any = () => ({})
const calls: { url: string; opts: any }[] = []
let admin = true
const toasts: string[] = []

const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const V2Page = defineComponent({ props: ['modelValue', 'tabs'], setup(p, { slots }) {
  return () => h('main', [h('nav', { class: 'tabs' }, (p.tabs ?? []).map((t: any) => h('b', { class: 'tabk', 'data-k': t.k }, t.label))), slots.title?.(), slots.conclusion?.(), slots.actions?.(), slots.default?.(), slots.tab?.()])
} })
const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'empty'], setup(p) { return () => h('div', { class: 'tbl', 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, String(r.hostname ?? r.name ?? r.title ?? '')))) } })
const stubs = { V2Page, V2Table, V2Coverage: true, InfoNote: pass, NuxtLink: defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { class: 'nl', 'data-to': JSON.stringify(p.to) }, slots.default?.()) } }), ClientOnly: pass }

function stubAll() {
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick, useLatest, useSort, pickRiskTab, riskTabKeys, candRows, nightConfirmText })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
  const api = async (u: string, o: any = {}) => { calls.push({ url: u, opts: o }); return handler(u, o) }
  vi.stubGlobal('useApi', () => ({ apiFetch: api }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: api }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(!admin), isAdmin: ref(admin), canWrite: ref(admin) }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: () => {}, busy: ref(false) }))
  vi.stubGlobal('useV2ExportName', () => (s: string) => s)
  vi.stubGlobal('useFeatureFlags', () => ({ flags: ref([]), ensureLoaded: () => {}, isEnabled: () => true }))
}
beforeEach(() => { route.query = {}; calls.length = 0; toasts.length = 0; admin = true; handler = () => ({}); stubAll() })
afterEach(() => vi.unstubAllGlobals())
const hidden = (w: any) => /display:\s*none/.test(String(w.attributes('style') ?? ''))

// ───────────────────────── 1.3 設備風險
const RISK = () => ({ uptime_enabled: true, settings: { uptime_days: 400 }, data_stale_days: 8,
  uptime: { count: 0, rows: [], collected: 3, with_uptime: 3, unreadable_count: 0, unreadable: [] },
  hardware: { rows: [], coverage: [] }, spof: { count: 0, rows: [], undetermined: [], undetermined_count: 0, decidable_count: 0, systems_with_prod: 0 },
  contract: { has_data: true, rows: [], total: 0, counts: { expired: 0, d90: 0, d180: 0, none: 0, ok: 0 }, ok_rows: [], none_rows: [] } })
const HK = (night: any) => ({ can_edit: true, running: false, alert_pct: 80, old_years: 2, window: { blocked: false, message: '現在可以掃描' }, hosts: [], night })
async function mountRisk() {
  const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(RiskPage) }) })
  const w = mount(Wrap, { global: { stubs: { ...stubs, V2Coverage: true } } })
  await flushPromises()
  return w
}
describe('1.3 設備風險：設定不佔頁首、夜間掃描開關收進設定', () => {
  it('頁首只有結論，沒有「門檻設定」鈕；逾期天數設定在「逾期未重開機」頁籤的折疊裡，欄位對齊目前值', async () => {
    handler = (u) => (u === '/api/risk' ? RISK() : {})
    route.query = { tab: 'uptime' }
    const w = await mountRisk()
    expect(w.text()).not.toContain('門檻設定')
    const fold = w.find('details[data-testid="risk-uptime-set"]')
    expect(fold.exists()).toBe(true)
    expect(fold.attributes('open')).toBeUndefined()
    expect(fold.find('summary').text()).toContain('目前逾期門檻 400 天')
    expect((fold.find('input[type="number"]').element as HTMLInputElement).value).toBe('400')   // 沒展開也不會送出預設的 365
  })
  it('儲存逾期天數：PUT 的內容不變（uptime_days）', async () => {
    handler = (u, o) => (u === '/api/risk' ? RISK() : {})
    route.query = { tab: 'uptime' }
    const w = await mountRisk()
    await w.find('details[data-testid="risk-uptime-set"] input[type="number"]').setValue(500)
    await w.find('details[data-testid="risk-uptime-set"] form').trigger('submit')
    await flushPromises()
    const put = calls.find(c => c.url === '/api/risk/settings')
    expect(put?.opts.method).toBe('PUT')
    expect(put?.opts.body).toEqual({ uptime_days: 500 })
  })
  it('唯讀帳號看不到設定折疊', async () => {
    admin = false; stubAll()
    handler = (u) => (u === '/api/risk' ? RISK() : {})
    route.query = { tab: 'uptime' }
    const w = await mountRisk()
    expect(w.find('details[data-testid="risk-uptime-set"]').exists()).toBe(false)
  })
  it('目錄清理：夜間掃描只顯示開／關狀態，開關在折疊「⚙ 設定」裡（預設收合），按下去仍打同一支 PUT', async () => {
    handler = (u) => (u === '/api/risk' ? RISK() : u === '/api/housekeeping/overview' ? HK({ enabled: false, window: '19:00～07:00', scans: [{ scan_id: 9, hostname: 'TEST-A', status_label: '完成' }], changed_by: 'admin', changed_at: '2026-11-19 10:00' }) : {})
    route.query = { tab: 'hk' }
    window.confirm = () => true
    const w = await mountRisk()
    const st = w.find('[data-testid="hk-night"]')
    expect(st.text()).toContain('夜間自動掃描：關閉')
    expect(st.find('button').exists()).toBe(false)
    const fold = w.find('details[data-testid="hk-night-set"]')
    expect(fold.exists()).toBe(true)
    expect(fold.attributes('open')).toBeUndefined()
    await fold.find('button.setbtn').trigger('click')
    await flushPromises()
    const put = calls.find(c => c.url === '/api/housekeeping/night')
    expect(put?.opts).toMatchObject({ method: 'PUT', body: { enabled: true } })
  })
})

// ───────────────────────── 8.3 EOS 追蹤
const OV = (o: any = {}) => ({ today: '2026-11-20', can_write: true, can_admin: true, tiles: [{ key: 'todo', label: '待辦', count: 0 }, { key: 'all', label: '全部', count: 0 }], rows: [], settings: { reason_classes: [{ name: '廠商未出貨', need_note: false }], review_default_months: 6, max_horizon_months: 24, soon_days: 90, reminder: { enabled: false, unreplied_days: null, plan_overdue_days: null, hold_expired_days: null, escalate_to: '' } }, demo_count: 2, ...o })
describe('8.3 EOS 追蹤：設定與示範資料搬到 ⚙ 系統設定', () => {
  it('追蹤頁沒有「類別與催辦設定」子頁、沒有示範資料鈕，只留 ⚙ 設定連結（管理員）', async () => {
    handler = (u) => (u === '/api/eos/follow/overview' ? OV() : {})
    const w = mount(EosFollow, { global: { stubs: { ...stubs, EosNotifySettings: true } } })
    await flushPromises()
    expect(w.findAll('.bar button.pill').map(b => b.text())).toEqual(['追蹤清單', '通知對象', '催辦預覽'])
    expect(w.text()).not.toContain('看示範資料')
    expect(w.text()).not.toContain('儲存設定')
    expect(w.find('[data-testid="eos-follow-more"]').exists()).toBe(false)
    const gear = w.find('[data-testid="eos-follow-gear"]')
    expect(JSON.parse(gear.attributes('data-to')!)).toEqual({ path: '/v2/settings', query: { st: 'notify', sub: 'eosfollow' } })
  })
  it('非管理員沒有 ⚙ 設定連結', async () => {
    handler = (u) => (u === '/api/eos/follow/overview' ? OV({ can_admin: false }) : {})
    const w = mount(EosFollow, { global: { stubs: { ...stubs, EosNotifySettings: true } } })
    await flushPromises()
    expect(w.find('[data-testid="eos-follow-gear"]').exists()).toBe(false)
  })
  it('網址 efdemo=1 才進示範：打 demo=true、顯示警示橫幅；「回到真實資料」清掉網址參數', async () => {
    route.query = { efdemo: '1' }
    handler = (u) => (u === '/api/eos/follow/overview' ? OV() : {})
    const w = mount(EosFollow, { global: { stubs: { ...stubs, EosNotifySettings: true } } })
    await flushPromises()
    expect(calls.find(c => c.url === '/api/eos/follow/overview')?.opts.query).toEqual({ demo: 'true' })
    expect(w.find('.demo').text()).toContain('示範資料')
    await w.findAll('button.mini').find(b => b.text() === '回到真實資料')!.trigger('click')
    await flushPromises()
    expect(route.query.efdemo).toBeUndefined()
    expect(w.find('.demo').exists()).toBe(false)
  })
  it('設定頁：類別與催辦設定可存（PUT 內容不變）；示範資料產生／清除在折疊裡', async () => {
    handler = (u, o) => (u === '/api/eos/follow/settings' && !o.method ? { settings: OV().settings } : u === '/api/eos/follow/overview' ? OV() : { ok: true })
    const w = mount(EosFollowSettings, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('阻礙來源類別')
    const save = w.findAll('button.pri').find(b => b.text() === '儲存')!
    await save.trigger('click')
    await flushPromises()
    const put = calls.find(c => c.url === '/api/eos/follow/settings' && c.opts.method === 'PUT')
    expect(put?.opts.body.reason_classes).toEqual([{ name: '廠商未出貨', need_note: false }])
    expect(put?.opts.body.reminder).toMatchObject({ enabled: false, unreplied_days: null })
    const demo = w.find('details[data-testid="eos-follow-demo"]')
    expect(demo.attributes('open')).toBeUndefined()
    expect(JSON.parse(demo.find('a.nl').attributes('data-to')!)).toEqual({ path: '/v2/eos', query: { tab: 'follow', efdemo: '1' } })
    await demo.findAll('button').find(b => b.text() === '產生')!.trigger('click')
    await flushPromises()
    expect(calls.some(c => c.url === '/api/eos/follow/demo' && c.opts.method === 'POST')).toBe(true)
    expect(demo.text()).toContain('清除 2 筆')
  })
  it('設定讀取失敗：寫出原因並可重試，不顯示空表單', async () => {
    handler = (u) => { if (u === '/api/eos/follow/settings') throw new Error('boom'); return OV() }
    const w = mount(EosFollowSettings, { global: { stubs } })
    await flushPromises()
    expect(w.find('.err').text()).toContain('設定讀取失敗：boom')
    expect(w.text()).not.toContain('阻礙來源類別')
  })
})

// ───────────────────────── 7.1 機櫃盤點：低頻頁籤收進「更多」
async function mountFlow(props: any, tab?: string) {
  if (tab) route.query = { tab }
  handler = (u) => (u === '/api/v2/inv/rack/tasks' ? { can_edit: true, items: [] } : {})
  const root = defineComponent({ render: () => h(Suspense, null, { default: () => h(InventoryFlow, { kind: 'rack', no: '7.1', title: '機櫃盤點', ...props }) }) })
  const w = mount(root, { global: { stubs: { ...stubs, SortTh: true } } })
  await flushPromises()
  return w
}
describe('7.1 機櫃盤點：搬遷包、搬遷盤點收進「更多」', () => {
  const EXTRA = [{ k: 'racks', label: '各櫃進度' }, { k: 'print', label: '列印' }]
  const MORE = [{ k: 'pkg', label: '搬遷包' }, { k: 'relocate', label: '搬遷盤點' }]
  it('頁籤列沒有搬遷兩籤；「更多」預設收合，裡面有兩顆', async () => {
    const w = await mountFlow({ extraTabs: EXTRA, moreTabs: MORE })
    expect(w.findAll('.tabk').map(t => t.attributes('data-k'))).toEqual(['tasks', 'result', 'diffs', 'report', 'racks', 'print'])
    const more = w.find('details[data-testid="flow-more-tabs"]')
    expect(more.attributes('open')).toBeUndefined()
    expect(more.findAll('button.mt').map(b => b.text())).toEqual(['搬遷包', '搬遷盤點'])
  })
  it('舊網址 ?tab=relocate 照舊能開：更多自動展開、列上暫時多一格', async () => {
    const w = await mountFlow({ extraTabs: EXTRA, moreTabs: MORE }, 'relocate')
    const more = w.find('details[data-testid="flow-more-tabs"]')
    expect(more.attributes('open')).toBeDefined()
    expect(more.find('summary').text()).toContain('搬遷盤點')
    expect(w.findAll('.tabk').map(t => t.attributes('data-k'))).toContain('relocate')
    expect(more.find('button.mt.on').text()).toBe('搬遷盤點')
  })
  it('其他盤點（沒有 moreTabs）不出現「更多」', async () => {
    const w = await mountFlow({ extraTabs: EXTRA })
    expect(w.find('[data-testid="flow-more-tabs"]').exists()).toBe(false)
  })
  it('按「搬遷包」＝切到 ?tab=pkg', async () => {
    const w = await mountFlow({ extraTabs: EXTRA, moreTabs: MORE })
    await w.findAll('button.mt')[0].trigger('click')
    expect(route.query.tab).toBe('pkg')
  })
})

// ───────────────────────── 7.1 搬遷盤點：匯出／匯入對帳兩階段
describe('搬遷盤點：匯出與匯入對帳兩階段', () => {
  it('預設只看「匯出預填盤點表」，對帳區塊在 DOM 但隱藏；切換不丟東西', async () => {
    handler = (u) => (u.includes('/api/relocation') ? { total: 3, locations: [{ loc: '甲機房', n: 3 }], collected: 0, last: null } : {})
    const w = mount(Relocation, { global: { stubs } })
    await flushPromises()
    expect(hidden(w.find('[data-testid="reloc-export"]'))).toBe(false)
    expect(hidden(w.find('[data-testid="reloc-import"]'))).toBe(true)
    const steps = w.findAll('button.step')
    expect(steps.map(b => b.text())).toEqual(['① 匯出預填盤點表', '② 匯入對帳'])
    await steps[1].trigger('click')
    expect(hidden(w.find('[data-testid="reloc-export"]'))).toBe(true)
    expect(hidden(w.find('[data-testid="reloc-import"]'))).toBe(false)
  })
})

// ───────────────────────── 2.1 新增主機
describe('2.1 新增主機：手動新增與網段掃描一次只顯示一件', () => {
  it('預設手動新增；切到網段掃描後掃描區顯示、手動區隱藏（欄位內容保留在 DOM）', async () => {
    handler = () => ({})
    const w = mount(AssetNew, { global: { stubs: { ...stubs, SearchableSelect: true } } })
    await flushPromises()
    expect(hidden(w.find('[data-testid="asset-new-manual"]'))).toBe(false)
    expect(hidden(w.find('[data-testid="asset-new-scan"]'))).toBe(true)
    await w.findAll('[data-testid="asset-new-mode"] button')[1].trigger('click')
    expect(hidden(w.find('[data-testid="asset-new-manual"]'))).toBe(true)
    expect(hidden(w.find('[data-testid="asset-new-scan"]'))).toBe(false)
  })
})

// ───────────────────────── 1.2 異常報告
describe('1.2 異常報告：資料整理與體檢問題收合', () => {
  const api = (u: string) => (u === '/api/issues/scan-quality' ? { confirm: 5 } : u === '/api/issues' ? [] : u === '/api/manage-state' ? { items: [] }
    : u === '/api/dashboard/stats' ? { total_ica_count: 1, total_overlap_count: 1, scanned_count: 1, scan_only_count: 0, last_scan_time: null }
      : u === '/api/dashboard/composition' ? { total: 4, os_from_facts: 3, os_guessed: 1, data_quality: { vcenter_verified: 2 } }
        : u === '/api/health/summary' ? { total: 4, clean: 3, needs_review: 1, machine_bad: 1, data_bad: 0, by_issue: { 缺OS: 1 } } : {})
  it('預設收合；待處理分組在收合區外面，資料整理表與體檢問題表只在收合區裡', async () => {
    handler = api
    const w = mount(Anomalies, { global: { stubs } })
    await flushPromises()
    const q = w.find('details[data-testid="anom-quality"]')
    expect(q.exists()).toBe(true)
    expect(q.attributes('open')).toBeUndefined()
    expect(q.find('summary').text()).toContain('資料要整理')
    expect(w.find('[data-prefix="G"]').exists()).toBe(true)
    expect(q.find('[data-prefix="G"]').exists()).toBe(false)
    expect(q.find('[data-prefix="Q"]').exists()).toBe(true)
    expect(q.find('[data-prefix="P"]').exists()).toBe(true)
    expect(w.findAll('[data-prefix="Q"]').length).toBe(1)
  })
  it('打開下鑽明細（點台數）時自動展開，看得到明細', async () => {
    handler = api
    const w = mount(Anomalies, { global: { stubs } })
    await flushPromises()
    route.query = { ad: 'health' }
    await nextTick(); await flushPromises()
    expect(w.find('details[data-testid="anom-quality"]').attributes('open')).toBeDefined()
  })
})

// ───────────────────────── 10.1 主機組月報
const HM = () => ({ month: '2026-11', months: ['2026-11'], prev: '2026-10', generated_at: '2026-11-20 08:00:00', threshold: 80,
  lifecycle: { rows: [], total: { expired: 0, upcoming: 0 } }, scale: { virt_total: 0, virt_by_room: [] }, nmon: [{ host: 'TEST-A', over: 1, longest_over_min: 10, from: '2026-11-01', to: '2026-11-02', days: [] }],
  incidents: [], nmon_files: [], nmon_perf: [], night: null,
  note: { verdict: '', verdict_note: '', actions: [], attention: [], priorities: [], upcoming: [], value: [], vm_perf: [] } })
describe('10.1 主機組月報：一次看一張投影片', () => {
  it('預設只顯示「總覽」；切到「附錄」才顯示逐台趨勢；截圖模式全部攤開', async () => {
    handler = (u) => (u === '/api/v2/host-monthly' ? HM() : {})
    const w = mount(HostMonthly, { global: { stubs } })
    await flushPromises()
    const slides = w.findAll('section.slide')
    expect(slides.length).toBeGreaterThanOrEqual(8)
    expect(slides.filter(s => !hidden(s)).length).toBe(1)
    expect(w.findAll('button.pg').map(b => b.text())).toEqual(['總覽', '事件', '夜間通知', '效能與容量', '生命週期', '風險追蹤', '下月重點', '附錄（逐台趨勢）'])
    await w.findAll('button.pg')[7].trigger('click')
    const shown = w.findAll('section.slide').filter(s => !hidden(s))
    expect(shown.length).toBe(1)
    expect(shown[0].text()).toContain('Appendix 1')
    await w.findAll('button.btn2').find(b => b.text() === '截圖模式')!.trigger('click')
    expect(w.findAll('section.slide').filter(s => !hidden(s)).length).toBe(slides.length)
    expect(w.find('[data-testid="hm-pager"]').exists()).toBe(false)
  })
})

// ───────────────────────── 9.4 CIA 補登：截止日改在「分派設定」
const CIA_SUM = () => ({ counts: { assigned: 1 }, resend: 0, cia_import: {}, rounds: [{ id: 3, created_at: '2026-11-10 09:00', created_by: 'admin', items: 5, categories: ['DB'], deadline: '2026-12-01', completion: 0.5, cia_data_date: '2026-11-09', snapshot: {} }],
  settings: { assignees: { DB: [] }, pilot: ['DB'], categories: ['DB'] }, categories: ['DB'] })
async function mountCia() {
  const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(CiaReg) }) })
  const w = mount(Wrap, { global: { stubs: { ...stubs, V2Coverage: true } } })
  await flushPromises()
  return w
}
describe('9.4 CIA 補登：截止日不再佔每個頁籤上方', () => {
  beforeEach(() => { handler = (u) => (u === '/api/cia-reg/summary' ? CIA_SUM() : u === '/api/cia-reg/items' ? { items: [] } : {}) })
  it('頁首只顯示「截止 日期」，沒有日期輸入框與存檔鈕；管理員有「⚙ 改截止日」連到分派設定', async () => {
    route.query = { round: '3' }
    const w = await mountCia()
    expect(w.find('[data-testid="ciareg-deadline"]').text()).toBe('截止 2026-12-01')
    expect(w.find('input[type="date"]').exists()).toBe(false)
    expect(w.text()).not.toContain('存截止日')
    expect(w.find('[data-testid="ciareg-deadline-gear"]').attributes('data-to')).toContain('tab=settings')
  })
  it('非管理員沒有改截止日連結', async () => {
    admin = false; stubAll()
    route.query = { round: '3' }
    const w = await mountCia()
    expect(w.find('[data-testid="ciareg-deadline-gear"]').exists()).toBe(false)
  })
  it('分派設定頁籤：改截止日，PUT 內容不變（deadline）', async () => {
    route.query = { round: '3', tab: 'settings' }
    const w = await mountCia()
    const form = w.find('[data-testid="ciareg-deadline-form"]')
    expect(form.exists()).toBe(true)
    await form.find('input[type="date"]').setValue('2026-12-15')
    await form.find('button').trigger('click')
    await flushPromises()
    const put = calls.find(c => c.url === '/api/cia-reg/rounds/3/deadline')
    expect(put?.opts).toMatchObject({ method: 'PUT', body: { deadline: '2026-12-15' } })
  })
  it('沒選輪次：設定頁只提示先選一輪，不顯示輸入框', async () => {
    route.query = { tab: 'settings' }
    const w = await mountCia()
    expect(w.find('[data-testid="ciareg-deadline-form"]').exists()).toBe(false)
    expect(w.text()).toContain('先在頁首選一輪')
  })
})

// ───────────────────────── 5.1 機房平面圖：平面圖／機櫃總表兩個檢視
const PW = () => ({ unobserved_keys: [], removed_keys: [], unknown_keys: [], off_keys: [], pending_keys: [], w: 500, amps: null, btu_h: 0, volts: 220, kg: 0, if_all_on_w: 500, used_u: 2, free_u: 40, unplaced_count: 0, known_count: 1, unknown_count: 0, off_count: 0, pending_count: 0, unobserved_count: 0, removed_count: 0, w_by_model: [], unknown_by_model: [] })
const DCDATA = () => ({ total_racked: 1, total_devices: 1, devices: { k1: { key: 'k1', name: 'TEST-A', rack: 'A01', ip: '10.99.0.1' } },
  rooms: [{ room: '測試機房', device_count: 1, racked: 1, vm_excluded: 0, no_rack: [], audit_stale_keys: [], audit_never_count: 0, no_floor_racks: [], power: PW(),
    racks: [{ rack: 'A01', prefix: 'A', floor: '', device_count: 1, used_u: 2, height_u: 42, all_keys: ['k1'], keys: ['k1'], api_ids: [], system_count: 1, overlap_count: 0, excel_only: false, power: PW() }] }] })
describe('5.1 機房平面圖：平面圖與機櫃總表各自一個檢視', () => {
  it('預設只顯示平面圖；機櫃總表在 DOM 但隱藏，切換後才顯示', async () => {
    handler = (u) => (u === '/api/racks/datacenter' ? DCDATA() : u === '/api/storage/rooms' ? [] : {})
    const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(Datacenter) }) })
    const w = mount(Wrap, { global: { stubs: { ...stubs, RackElevation: true, OverlapGroups: true } } })
    await flushPromises()
    const views = w.find('[data-testid="dc-plan-views"]')
    expect(views.findAll('button').map(b => b.text())).toEqual(['平面圖', '機櫃總表 1'])
    expect(hidden(w.find('[data-testid="dc-plan-map"]'))).toBe(false)
    const tbl = w.find('[data-prefix="T"]')
    expect(tbl.exists()).toBe(true)
    expect(hidden(tbl)).toBe(true)
    await views.findAll('button')[1].trigger('click')
    expect(hidden(w.find('[data-testid="dc-plan-map"]'))).toBe(true)
    expect(hidden(w.find('[data-prefix="T"]'))).toBe(false)
  })
})
