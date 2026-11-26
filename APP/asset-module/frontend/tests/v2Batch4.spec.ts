// 三層分離第 4 批（2.070.x 後續）：4.1 事件應變新增事件改彈窗、7.5 憑證詳情技術欄位收進「更多欄位」、3.2 OCP 逐台明細預設唯讀、
// 2.1 主機查詢批次鈕收成一個選單、9.2 數字健檢口徑版本收進註、5.2 已知例外診斷檢視收進「更多」。
// 改回舊結構（表單展開在清單上方、技術欄位直接攤開、下拉預設就在表格裡、四顆批次鈕並排、版本號貼在頁首、診斷檢視並排）就會紅。
// Nuxt 自動匯入以 stubGlobal 提供；V2Page／V2Table／InfoNote 為簡化替身；API 全假、無網路。
// 2.1 主機查詢與 7.5 憑證詳情兩頁依賴太多 Nuxt 頁面環境，改以「讀原始碼並確認結構」把關（誠實標註：這兩條是原始碼斷言，不是掛載）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import IncidentPage from '../pages/v2/incident/index.vue'
import OcpDetail from '../components/v2/OcpDetail.vue'
import StorageExempt from '../components/v2/StorageExempt.vue'
import NumberCheck from '../components/v2/NumberCheck.vue'
import hostsSrc from '../pages/v2/hosts.vue?raw'
import certIdSrc from '../pages/v2/certs/[id].vue?raw'

enableAutoUnmount(afterEach)
const route = reactive<{ query: Record<string, string>; params: Record<string, string> }>({ query: {}, params: {} })
let handler: (url: string, opts?: any) => any = () => ({})
const calls: { url: string; opts: any }[] = []
let admin = true

const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
// 註：用 <i class="note"> 標出 InfoNote 的內容，才分得出「畫面上」與「點了才展開的註」
const Note = defineComponent({ setup(_p, { slots }) { return () => h('i', { class: 'note' }, slots.default?.()) } })
const V2Page = defineComponent({ props: ['modelValue', 'tabs'], setup(p, { slots }) {
  return () => h('main', [h('nav', { class: 'tabs' }, (p.tabs ?? []).map((t: any) => h('b', { class: 'tabk', 'data-k': t.k }, t.label))), slots.title?.(), slots.conclusion?.(), slots.actions?.(), slots.default?.(), slots.tab?.()])
} })
const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl', 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const stubs = { V2Page, V2Table, InfoNote: Note, NuxtLink: defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { class: 'nl', 'data-to': JSON.stringify(p.to) }, slots.default?.()) } }), ClientOnly: pass }

function stubAll() {
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any },
    push: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  const api = async (u: string, o: any = {}) => { calls.push({ url: u, opts: o }); return handler(u, o) }
  vi.stubGlobal('useApi', () => ({ apiFetch: api }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: api }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(!admin), isAdmin: ref(admin), canWrite: ref(admin) }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: () => {}, busy: ref(false) }))
  vi.stubGlobal('useFeatureFlags', () => ({ flags: ref([]), ensureLoaded: () => {}, isEnabled: () => true }))
  vi.stubGlobal('navigateTo', () => {})
}
beforeEach(() => { route.query = {}; calls.length = 0; admin = true; handler = () => ({}); stubAll() })
afterEach(() => vi.unstubAllGlobals())
const suspended = (comp: any, props: any = {}) => defineComponent({ render: () => h(Suspense, null, { default: () => h(comp, props) }) })

// ───────────────────────── 4.1 事件應變：新增事件改彈窗
describe('4.1 事件應變：新增事件是彈窗，不再展開在清單上方', () => {
  const ev = { id: 7, type: '主機', title: '甲機房 UPS 汰換', started_at: '2026-11-20 09:00:00', status: 'open', created_by: 'a', created_at: '2026-11-20 09:00:00', closed_at: null, category: 'planned', mode: 'formal' }
  async function mountIncident() {
    handler = (u) => (u === '/api/incidents' ? [ev] : {})
    const w = mount(suspended(IncidentPage), { global: { stubs } })
    await flushPromises()
    return w
  }
  it('預設沒有表單；按「新增事件」才出現彈窗，表單在彈窗裡（不在清單之前展開）', async () => {
    const w = await mountIncident()
    expect(w.find('[data-testid="inc-new-dialog"]').exists()).toBe(false)
    expect(w.text()).not.toContain('建立事件')
    await w.findAll('button').find(b => b.text() === '新增事件')!.trigger('click')
    const dlg = w.find('[data-testid="inc-new-dialog"]')
    expect(dlg.exists()).toBe(true)
    expect(dlg.classes()).toContain('dlg-mask')
    expect(dlg.find('section[role="dialog"]').exists()).toBe(true)
    expect(dlg.text()).toContain('事件類別')
    await dlg.find('input[value="planned"]').setValue(true)      // 選了類別才出現事件資料與「建立事件」（原本的流程，沒動）
    expect(w.find('[data-testid="inc-new-dialog"]').text()).toContain('建立事件')
  })
  it('彈窗有「取消」，按了就收起；點遮罩也收起', async () => {
    const w = await mountIncident()
    await w.findAll('button').find(b => b.text() === '新增事件')!.trigger('click')
    await w.find('[data-testid="inc-new-cancel"]').trigger('click')
    expect(w.find('[data-testid="inc-new-dialog"]').exists()).toBe(false)
    await w.findAll('button').find(b => b.text() === '新增事件')!.trigger('click')
    await w.find('[data-testid="inc-new-dialog"]').trigger('click')
    expect(w.find('[data-testid="inc-new-dialog"]').exists()).toBe(false)
  })
  it('從主機履歷帶 ?new=planned 進來照樣直接開彈窗；建立事件仍打同一支 POST（欄位不變）', async () => {
    route.query = { new: 'planned', title: '停機評估' }
    handler = (u, o) => (u === '/api/incidents' && o?.method === 'POST' ? { id: 9 } : u === '/api/incidents' ? [] : {})
    const w = mount(suspended(IncidentPage), { global: { stubs } })
    await flushPromises()
    expect(w.find('[data-testid="inc-new-dialog"]').exists()).toBe(true)
    await w.find('[data-testid="inc-new-dialog"] input[value="formal"]').setValue(true)
    await w.find('[data-testid="inc-new-dialog"] .btn.danger').trigger('click')
    await flushPromises()
    const post = calls.find(c => c.url === '/api/incidents' && c.opts?.method === 'POST')
    expect(post?.opts.body).toMatchObject({ category: 'planned', mode: 'formal', title: '停機評估', type: '主機' })
  })
})

// ───────────────────────── 3.2 OCP 逐台明細
describe('3.2 OCP 逐台明細：預設唯讀，按「編輯」才出現角色／叢集下拉', () => {
  const OCP = () => ({ clusters: [{ cluster: 'ocp-a', basis: '主機名', node_count: 2, by_role: {}, by_stage: {}, versions: { '4.12': 2 }, locations: {}, environments: {}, primary_location: '甲', primary_env: '正式', spans: [], risks: [], vips: [],
    role_tiers: { Master: { count: 1, retired: 0, ready: 1, worst: '', by_stage: {} } },
    nodes: [
      { hostname: 'ocp-m1', ip: '10.99.1.1', asset_serial: 'S1', role: 'Master', os: 'RHCOS', stage: 'onboarded', stage_label: '已納管', tone: '', location: '甲', environment: '正式', api_id: null, purpose: null, dup_count: 1, dup_reason: '', cluster_basis: '主機名', cluster_by: null, cluster_at: null, role_basis: '自動' },
      { hostname: 'ocp-w1', ip: '10.99.1.2', asset_serial: 'S2', role: 'Worker', os: 'RHCOS', stage: 'onboarded', stage_label: '已納管', tone: '', location: '甲', environment: '正式', api_id: null, purpose: null, dup_count: 1, dup_reason: '', cluster_basis: '主機名', cluster_by: null, cluster_at: null, role_basis: '自動' }] }],
    totals: { clusters: 1, nodes: 2, ungrouped: 0 }, cluster_names: ['ocp-a', 'ocp-b'], note: '' })
  async function mountOcp() {
    route.query = { cluster: 'ocp-a' }
    handler = (u) => (u === '/api/arch/ocp' ? OCP() : {})
    const w = mount(OcpDetail, { global: { stubs } })
    await flushPromises()
    return w
  }
  it('一進來表格裡沒有任何下拉；每列有「編輯」', async () => {
    const w = await mountOcp()
    expect(w.findAll('select[aria-label="改角色"]').length).toBe(0)
    expect(w.findAll('select[aria-label="改叢集"]').length).toBe(0)
    expect(w.findAll('[data-testid="ocp-edit"]').length).toBe(2)
  })
  it('按某一列「編輯」只有那一列出現下拉；按「取消」收回', async () => {
    const w = await mountOcp()
    await w.findAll('[data-testid="ocp-edit"]')[0].trigger('click')
    expect(w.findAll('select[aria-label="改角色"]').length).toBe(1)
    expect(w.findAll('select[aria-label="改叢集"]').length).toBe(1)
    await w.find('[data-testid="ocp-edit-cancel"]').trigger('click')
    expect(w.findAll('select[aria-label="改角色"]').length).toBe(0)
  })
  it('改角色仍打同一支 PUT（內容不變），改完自動收回編輯狀態', async () => {
    const w = await mountOcp()
    await w.findAll('[data-testid="ocp-edit"]')[1].trigger('click')
    const sel = w.find('select[aria-label="改角色"]')
    ;(sel.element as HTMLSelectElement).value = 'Infra'
    await sel.trigger('change')
    await flushPromises()
    const put = calls.find(c => c.url === '/api/arch/ocp/role')
    expect(put?.opts.method).toBe('PUT')
    expect(put?.opts.body).toMatchObject({ asset_serial: 'S2', role: 'Infra' })
    expect(w.findAll('select[aria-label="改角色"]').length).toBe(0)
  })
  it('唯讀帳號沒有「編輯」', async () => {
    admin = false; stubAll()
    const w = await mountOcp()
    expect(w.findAll('[data-testid="ocp-edit"]').length).toBe(0)
  })
})

// ───────────────────────── 5.2 已知例外
describe('5.2 已知例外：舊門檻／對不到的磁碟／異動紀錄收進「更多」', () => {
  const EX = () => ({ today: '2026-11-22', rows: [], legacy: [{ type: 'ds', ip: '10.99.2.1', threshold: 90, expires: '2026-12-01', reason: 'x' }], unmapped: [], log: [], candidates: [], presets: [30, 90], default_days: 90 })
  async function mountEx() {
    handler = (u) => (u === '/api/storage/exempt' ? EX() : {})
    const w = mount(suspended(StorageExempt), { global: { stubs } })
    await flushPromises()
    return w
  }
  it('日常只有三顆（生效中、待確認、全部紀錄）；診斷三項在預設收合的「更多」裡', async () => {
    const w = await mountEx()
    const more = w.find('details[data-testid="exempt-more"]')
    expect(more.exists()).toBe(true)
    expect(more.attributes('open')).toBeUndefined()
    const outside = w.findAll('.chip').filter(c => !more.element.contains(c.element)).map(c => c.text())
    expect(outside.some(t => t.startsWith('生效中'))).toBe(true)
    expect(outside.some(t => t.startsWith('全部紀錄'))).toBe(true)
    expect(outside.some(t => t.startsWith('舊門檻') || t.startsWith('對不到的磁碟') || t.startsWith('異動紀錄'))).toBe(false)
    expect(more.text()).toContain('舊門檻 1')
  })
  it('網址指到診斷檢視（?xv=legacy）時「更多」自動展開，看得到那張表', async () => {
    route.query = { xv: 'legacy' }
    const w = await mountEx()
    expect(w.find('details[data-testid="exempt-more"]').attributes('open')).toBeDefined()
    expect(w.find('[data-prefix="L"]').exists()).toBe(true)
  })
})

// ───────────────────────── 9.2 數字健檢
describe('9.2 數字健檢：口徑版本號不貼在頁首，收進註', () => {
  it('頁首只有口徑名稱；版本號在註裡', async () => {
    handler = () => ({ evidence: { scan_time: '2026-11-22 06:00', alive: 3, covered_segments: 1, hardware_rows: 0 }, numbers: [], population: 'x', unit: '台', generated_at: 't', db_fingerprint: null, machine_state_mode: { label: '整台聚合', version: '7.3' } })
    const w = mount(NumberCheck, { global: { stubs } })
    await flushPromises()
    const bar = w.find('.bar > .muted')
    expect(bar.text()).toContain('口徑：整台聚合')
    // 頁首那行（扣掉註）不得出現版本號
    const clone = bar.element.cloneNode(true) as HTMLElement
    clone.querySelectorAll('.note').forEach(n => n.remove())
    expect(clone.textContent).not.toContain('7.3')
    expect(w.find('.bar > .note').text()).toContain('口徑版本 v7.3')
  })
})

// ───────────────────────── 2.1 主機查詢、7.5 憑證詳情（原始碼斷言）
describe('2.1 主機查詢：四顆批次鈕收進「批次操作」選單（原始碼斷言）', () => {
  it('四顆都在同一個 details 裡；details 外不再有並排的批次鈕', () => {
    const m = hostsSrc.match(/<details[^>]*data-testid="hosts-batch-menu"[\s\S]*?<\/details>/)
    expect(m).not.toBeNull()
    const menu = m![0]
    for (const t of ['批次納管', '標記下線／報廢', '加入盤點', '批次偵測存活']) expect(menu).toContain(`>${t}</button>`)
    const rest = hostsSrc.replace(menu, '')
    for (const t of ['批次納管', '標記下線／報廢', '加入盤點', '批次偵測存活']) expect(rest).not.toContain(`>${t}</button>`)
    expect(menu).toContain('showOnboard = true')   // 動作本身沒動
  })
})
describe('7.5 憑證詳情：觀測歷史的技術欄位收進「更多欄位」（原始碼斷言）', () => {
  it('序號、指紋、SAN、簽發者、起始時間都在折疊裡，到期時間與備註留在外面', () => {
    const m = certIdSrc.match(/<details[^>]*data-testid="obs-more-fields"[\s\S]*?<\/details>/)
    expect(m).not.toBeNull()
    const fold = m![0]
    for (const k of ['obs.serial', 'obs.fingerprint_sha256', 'obs.san', 'obs.issuer_dn', 'obs.not_before']) expect(fold).toContain(`v-model="${k}"`)
    const rest = certIdSrc.replace(fold, '')
    expect(rest).toContain('v-model="obs.not_after"')
    expect(rest).toContain('v-model="obs.note"')
    for (const k of ['obs.serial', 'obs.fingerprint_sha256', 'obs.san']) expect(rest).not.toContain(`v-model="${k}"`)
    expect(fold).not.toContain('open')              // 預設收合
  })
})
