/**
 * 2.048.16 V49：10.1 系統組報告改 V2 原生，實掛：
 *   - 摘要卡＝各平台同一欄加總、下鑽帶 registered_only；格子下鑽條件在網址、null 送空字串
 *   - 備註／存定稿只給一般帳號以上；備註 PUT body 照舊；版本明細前 10 名＋其餘一行
 * V46：9.1 主機分類改 V2 原生：篩選 cf、只看某一類 cc、全選符合的（不只這一頁）、批次 body 照舊、帶入限管理員、試算才可確認寫入
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import SystemGroup from '../components/v2/SystemGroup.vue'
import Classify from '../components/v2/Classify.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const route = reactive<{ query: Record<string, any> }>({ query: {} })
const auth = { isAdmin: false, canWrite: true }
const replaced: any[] = []
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = false; auth.canWrite = true; replaced.length = 0
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { replaced.push(x) } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(auth.canWrite), isViewer: ref(!auth.canWrite) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const linkText = (l: any) => (l ? JSON.stringify(l) : '')
const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'selectable', 'selected', 'rowKey'], emits: ['update:selected'], setup(p, { slots, emit }) {
  return () => h('div', { 'data-prefix': p.prefix, 'data-selectable': String(!!p.selectable) }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [
    p.selectable ? h('input', { type: 'checkbox', class: 'pick', onChange: () => emit('update:selected', { ...p.selected, [r[p.rowKey]]: true }) }) : null,
    ...(p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k.replace(/[^a-zA-Z0-9_]/g, '_')}`, 'data-link': c.link ? linkText(c.link(r)) : '' },
      slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? '')))])))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': JSON.stringify(p.to) }, slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: Link } } }
const flush = async (fn: (p: any) => any) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))

const REPORT = {
  meta: { generated_at: '2026-11-05 10:00', period: null, rvtools_exported_at: '2026-11-01', rvtools_note: 'vCenter 匯出於 2026-11-01' },
  platform_lifecycle: { rows: [{ platform: 'Linux', total: 5, 已EOS: 2, 一年內EOS: 1, 尚未公布: 0, 需確認: 1, 支援中: 1 },
                               { platform: 'Windows', total: 3, 已EOS: 1, 一年內EOS: 0, 尚未公布: 0, 需確認: 0, 支援中: 2 }],
                        total: 8, excluded: [{ platform: '網路設備', count: 4 }], excluded_total: 4, retired_excluded: 2, duplicates_merged: 0 },
  os_version_breakdown: Array.from({ length: 12 }, (_, i) => ({ os_canonical: `OS ${i + 1}`, platform: 'Linux', count: 12 - i, eos_status: '已EOS', eos_date: '2024-06-30' })),
  cluster_summary: { locations: [{ location: 'A 機房', total: 3, rows: [{ service: '交易服務', esxi_count: 3 }] }], basis: '依叢集名稱', unmapped_vcenters: [] },
  virtualization_env: { clusters: [{ location: 'A 機房', vcenter: '10.99.0.10', environment: '正式', cluster: null, count: 2, version: null, mixed_version: false, eos_status: null, eos_date: null }],
                        physical_hosts: [] },
  notes: { 'platform:Linux': '換版中' },
}
const rep = (p: any) => (p.url === '/api/reports/system-group' ? REPORT : [])

describe('系統組報告（V49）', () => {
  it('摘要卡＝各平台加總，下鑽帶 registered_only；格子條件在網址', async () => {
    const w = mount(SystemGroup, opts)
    await flush(rep)
    expect(w.text()).toContain('3已 EOS')           // 2 + 1
    expect(w.text()).toContain('4應優先處理')        // 已 3 + 一年內 1
    const card = w.findAll('a').find(a => a.text().includes('已 EOS'))!
    expect(JSON.parse(card.attributes('data-to')!).query).toEqual({ tab: 'group', gk: 'platform', gs: '已EOS', gro: 'true' })
    const cell = w.findAll('.c-_EOS').map(c => c.attributes('data-link')).filter(Boolean)
    expect(JSON.parse(cell[0]!).query).toMatchObject({ gk: 'platform', gp: 'Linux', gs: '已EOS' })
  })
  it('網址帶下鑽條件 → 送 /drill（null 那一列送空字串），標題寫明點的是哪一格', async () => {
    route.query = { tab: 'group', gt: 'ibm', gk: 'cluster', gv: '10.99.0.10', gc: '', gver: '' }
    const w = mount(SystemGroup, opts)
    await flushPromises()
    const d = pending.find(p => p.url === '/api/reports/system-group/drill')!
    expect(d.opts.query).toEqual({ table: 'cluster', vcenter: '10.99.0.10', cluster: '', version: '' })
    pending.splice(pending.indexOf(d), 1); d.resolve([{ host: 'esx1', cluster: null, vcenter: '10.99.0.10', version: null, reason: 'r' }])
    await flush(rep)
    expect(w.text()).toContain('（未歸叢集） 的 ESXi（版本未知）')
    expect(w.text()).toContain('1 筆')
  })
  it('虛擬化環境列的下鑽連結：null 寫成空字串', async () => {
    route.query = { tab: 'group', gt: 'ibm' }
    const w = mount(SystemGroup, opts)
    await flush(rep)
    const l = JSON.parse(w.find('.c-count').attributes('data-link')!)
    expect(l.query).toMatchObject({ gk: 'cluster', gv: '10.99.0.10', gc: '', gver: '' })
  })
  it('一般帳號：存定稿、改備註；PUT body 照舊', async () => {
    const w = mount(SystemGroup, opts)
    await flush(rep)
    await btn(w, '換版中').trigger('click')
    await w.find('.ni').setValue('已排程')
    await w.findAll('button').find(b => b.text() === '存')!.trigger('click')
    await flushPromises()
    const put = pending.find(p => p.url === '/api/reports/system-group/note')!
    expect(put.opts).toEqual({ method: 'PUT', body: { row_key: 'platform:Linux', note: '已排程' } })
    await btn(w, '存為本月定稿').trigger('click')
    // 2.058.23：先查這個月有沒有定稿（GET），沒有才直接存（POST）；有就先問要不要蓋掉
    const get = pending.find(p => p.url === '/api/reports/system-group/snapshots' && !p.opts)!
    get.resolve([])
    await flushPromises()
    expect(pending.find(p => p.url === '/api/reports/system-group/snapshots' && p.opts?.method === 'POST')).toBeTruthy()
  })
  it('只能看的帳號：沒有存定稿、備註不能點', async () => {
    auth.canWrite = false
    const w = mount(SystemGroup, opts)
    await flush(rep)
    expect(btn(w, '存為本月定稿')).toBeUndefined()
    expect(btn(w, '換版中')).toBeUndefined()
    expect(w.text()).toContain('換版中')
  })
  it('版本明細：前 10 名，其餘一行講出來（2 個版本、3 台）', async () => {
    route.query = { tab: 'group', gt: 'os' }
    const w = mount(SystemGroup, opts)
    await flush(rep)
    expect(w.findAll('[data-prefix="V"] .row').length).toBe(10)
    expect(w.text()).toContain('還有 2 個版本未列出，共 3 台')
  })
  it('報告載入失敗：顯示原因', async () => {
    const w = mount(SystemGroup, opts)
    await flushPromises()
    pending.splice(0).forEach(p => p.reject({ data: { detail: 'boom' } }))
    await flushPromises()
    expect(w.text()).toContain('報告載入失敗：boom')
  })
})

const SUM = { total: 3, classified: 1, unclassified: 2, percent: 33.3, by_category: [{ name: '核心交易', count: 1 }], valid_categories: ['核心交易', '測試'] }
const row = (s: string, extra: any = {}) => ({ asset_serial: s, hostname: `h-${s}`, ip: '10.99.1.1', api_id: 'AP1', asset_name: null, environment: null, location: null, platform: 'Linux', category: null, from_asset: false, ...extra })
const cls = (rows: any[]) => (p: any) => (p.url === '/api/classify/summary' ? SUM : { rows })

describe('主機分類（V46）', () => {
  it('預設只看未分類（only=unclassified）；卡片數字可點、網址 cf', async () => {
    const w = mount(Classify, opts)
    await flushPromises()
    expect(pending.map(p => p.url)).toContain('/api/classify?only=unclassified')
    await flush(cls([row('S1'), row('S2')]))
    const card = w.findAll('a').find(a => a.text().startsWith('已分類'))!
    expect(JSON.parse(card.attributes('data-to')!).query).toMatchObject({ tab: 'classify', cf: 'classified' })
  })
  it('分類種類 → 各分類台數；點台數 → cf=all&cc=<分類>，清單只留那一類', async () => {
    route.query = { tab: 'classify', cf: 'cats' }
    const w = mount(Classify, opts)
    await flush(cls([]))
    expect(pending.length).toBe(0)
    const l = JSON.parse(w.find('[data-prefix="K"] .c-count').attributes('data-link')!)
    expect(l.query).toMatchObject({ cf: 'all', cc: '核心交易' })
    route.query = { tab: 'classify', cf: 'all', cc: '核心交易' }
    await flush(cls([row('S1', { category: '核心交易', from_asset: true }), row('S2')]))
    expect(w.findAll('[data-prefix="A"] .row').length).toBe(1)
  })
  it('全選符合的（不只這一頁）→ 套用：PUT body 照舊；清除送 null', async () => {
    const w = mount(Classify, opts)
    await flush(cls([row('S1'), row('S2')]))
    await btn(w, '全選符合的 2 台').trigger('click')
    await w.find('select').setValue('測試')
    await btn(w, '套用到勾選的 2 台').trigger('click')
    await flushPromises()
    const put = pending.find(p => p.url === '/api/classify')!
    expect(put.opts).toEqual({ method: 'PUT', body: { asset_serials: ['S1', 'S2'], category: '測試' } })
    pending.splice(pending.indexOf(put), 1); put.resolve({ updated: 2 })
    await flush(cls([row('S1'), row('S2')]))
    await btn(w, '全選符合的').trigger('click')
    await btn(w, '清除分類').trigger('click')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/classify')!.opts.body.category).toBeNull()
  })
  it('一般帳號沒有帶入；只能看的帳號沒有勾選與批次', async () => {
    let w = mount(Classify, opts)
    await flush(cls([row('S1')]))
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    expect(w.find('input[type="file"]').exists()).toBe(false)
    w.unmount()
    auth.canWrite = false
    w = mount(Classify, opts)
    await flush(cls([row('S1')]))
    expect(w.find('.batch').exists()).toBe(false)
    expect(w.find('[data-prefix="A"]').attributes('data-selectable')).toBe('false')
  })
  it('管理員帶入：沒試算前不能確認寫入；試算送 commit=false', async () => {
    auth.isAdmin = true
    const w = mount(Classify, opts)
    await flush(cls([row('S1')]))
    expect(btn(w, '確認寫入').attributes('disabled')).toBeDefined()
    const f = w.find('input[type="file"]')
    Object.defineProperty(f.element, 'files', { value: [new File(['x'], 'a.xlsx')] })
    await f.trigger('change')
    await btn(w, '試算').trigger('click')
    await flushPromises()
    const post = pending.find(p => p.url === '/api/classify/seed')!
    expect((post.opts.body as FormData).get('commit')).toBe('false')
    post.resolve({ dry_run: true, source_rows: 1, duplicate_source_rows: 0, matched: 1, matched_in_scope: 1, matched_off_scope: 0, multi_match: 0, unmatched: 0, no_hostname: 0,
      invalid_category: 0, unmatched_samples: [], invalid_category_samples: [], valid_categories: [], new_categories: [], renamed_categories: [] })
    await flushPromises()
    expect(btn(w, '確認寫入').attributes('disabled')).toBeUndefined()
    expect(w.text()).toContain('試算，尚未寫入')
  })
  it('載入失敗：顯示原因', async () => {
    const w = mount(Classify, opts)
    await flushPromises()
    pending.splice(0).forEach(p => p.reject({ data: { detail: 'boom' } }))
    await flushPromises()
    expect(w.text()).toContain('資料載入失敗：boom')
  })
})

describe('第 9 批審查補測', () => {
  it('已退役下鑽 gr=true；ESXi 格子帶機房＋分類；列合計、欄合計、表1 合計都能點', async () => {
    route.query = { tab: 'group', gx: '1' }
    let w = mount(SystemGroup, opts)
    await flush(rep)
    const links = w.findAll('[data-prefix="X"] .c-count').map(c => JSON.parse(c.attributes('data-link')!).query)
    expect(links.at(-1)).toMatchObject({ gk: 'platform', gr: 'true' })
    const total = w.findAll('a').find(a => a.text() === '8')!
    expect(JSON.parse(total.attributes('data-to')!).query).toEqual({ tab: 'group', gk: 'platform', gro: 'true' })
    w.unmount()
    route.query = { tab: 'group', gt: 'esxi' }
    w = mount(SystemGroup, opts)
    await flush(rep)
    const cell = JSON.parse(w.find('[data-prefix="E"] .c-L_A___').attributes('data-link')!).query
    expect(cell).toMatchObject({ gk: 'cluster', gl: 'A 機房', gsv: '交易服務', gt: 'esxi' })
    expect(JSON.parse(w.find('[data-prefix="E"] .c-__total').attributes('data-link')!).query).toMatchObject({ gk: 'cluster', gsv: '交易服務' })
    expect(JSON.parse(w.findAll('a').find(a => a.text() === '3')!.attributes('data-to')!).query).toMatchObject({ gk: 'cluster', gl: 'A 機房' })
  })
  it('版本明細下鑽帶 registered_only（跟版本表同一個母體）', async () => {
    route.query = { tab: 'group', gt: 'os' }
    const w = mount(SystemGroup, opts)
    await flush(rep)
    expect(JSON.parse(w.find('[data-prefix="V"] .c-count').attributes('data-link')!).query).toMatchObject({ go: 'OS 1', gro: 'true' })
  })
  it('下鑽換條件：慢的舊回應不覆蓋新的；失敗時筆數顯示 —', async () => {
    route.query = { tab: 'group', gk: 'platform', gp: 'Linux' }
    const w = mount(SystemGroup, opts)
    await flushPromises()
    const first = pending.find(p => p.url === '/api/reports/system-group/drill')!
    route.query = { tab: 'group', gk: 'platform', gp: 'Windows' }
    await flushPromises()
    const second = pending.filter(p => p.url === '/api/reports/system-group/drill').at(-1)!
    expect(second.opts.query.platform).toBe('Windows')
    second.resolve([{ asset_serial: 'W1', hostname: 'w1' }])
    await flushPromises()
    first.resolve([{ asset_serial: 'L1', hostname: 'l1' }, { asset_serial: 'L2', hostname: 'l2' }])
    await flushPromises()
    expect(w.text()).toContain('1 筆')
    route.query = { tab: 'group', gk: 'platform', gp: 'AIX' }
    await flushPromises()
    pending.filter(p => p.url === '/api/reports/system-group/drill').at(-1)!.reject({ data: { detail: 'x' } })
    await flushPromises()
    expect(w.text()).toContain('清單載入失敗：x')
    expect(w.text()).not.toContain('0 筆')
  })
  it('重新整理失敗：上一份報告照樣在，錯誤寫在上方', async () => {
    const w = mount(SystemGroup, opts)
    await flush(rep)
    await btn(w, '重新整理').trigger('click')
    await flushPromises()
    pending.splice(0).forEach(p => p.reject({ data: { detail: 'down' } }))
    await flushPromises()
    expect(w.text()).toContain('報告載入失敗：down')
    expect(w.text()).toContain('應優先處理')
  })
  it('主機分類：卡片連結不帶 cc；全選符合的超過 300 台全送；換 cc、重新載入都清掉勾選', async () => {
    route.query = { tab: 'classify', cf: 'all', cc: '核心交易' }
    const w = mount(Classify, opts)
    const many = Array.from({ length: 350 }, (_, i) => row(`S${i}`, { category: '核心交易' }))
    await flush(cls(many))
    const card = w.findAll('a').find(a => a.text().startsWith('未分類'))!
    expect(JSON.parse(card.attributes('data-to')!).query.cc).toBeUndefined()
    await btn(w, '全選符合的 350 台').trigger('click')
    expect(w.text()).toContain('套用到勾選的 350 台')
    route.query = { tab: 'classify', cf: 'all' }
    await flushPromises()
    expect(w.text()).toContain('已勾選 0 台')
    await btn(w, '全選符合的').trigger('click')
    await w.find('select').setValue('測試')
    await btn(w, '套用到勾選的').trigger('click')
    await flushPromises()
    const put = pending.find(p => p.url === '/api/classify')!
    expect(put.opts.body.asset_serials.length).toBe(350)
    pending.splice(pending.indexOf(put), 1); put.resolve({ updated: 350 })
    await flush(cls(many))
    expect(w.text()).toContain('已勾選 0 台')
  })
  it('帶入：確認寫入之後按鈕又鎖住（要重新試算）', async () => {
    auth.isAdmin = true
    const w = mount(Classify, opts)
    await flush(cls([row('S1')]))
    const f = w.find('input[type="file"]')
    Object.defineProperty(f.element, 'files', { value: [new File(['x'], 'a.xlsx')] })
    await f.trigger('change')
    const res = (dry: boolean) => ({ dry_run: dry, source_rows: 1, duplicate_source_rows: 0, matched: 1, matched_in_scope: 1, matched_off_scope: 0, multi_match: 0, unmatched: 0, no_hostname: 0,
      invalid_category: 0, unmatched_samples: [], invalid_category_samples: [], valid_categories: [], new_categories: [], renamed_categories: [] })
    await btn(w, '試算').trigger('click'); await flushPromises()
    pending.find(p => p.url === '/api/classify/seed')!.resolve(res(true)); pending.length = 0
    await flushPromises()
    await btn(w, '確認寫入').trigger('click'); await flushPromises()
    const post = pending.find(p => p.url === '/api/classify/seed')!
    expect((post.opts.body as FormData).get('commit')).toBe('true')
    pending.splice(pending.indexOf(post), 1); post.resolve(res(false))
    await flush(cls([row('S1')]))
    expect(btn(w, '確認寫入').attributes('disabled')).toBeDefined()
  })
})
