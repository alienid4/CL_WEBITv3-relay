/**
 * 2.048.23 V30：8.3 EOS（作業系統／硬體／軟體）改 V2 原生，實掛：
 *   - 子分頁、狀態快篩、第二層篩選都在網址（es／est／ef）；項次在篩選前編好不重編
 *   - 移分類照舊先確認，取消不送；匯入對應表照舊先確認；匯入限管理員、寫入限一般帳號以上（只能看的帳號沒有寫入欄）
 *   - 「N 台」下鑽：硬體側 canonical_model、其他 canonical_os；換條件舊回應不覆蓋
 *   - 合併列不能改名／移分類；搜尋跨全部分頁找
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import EosLife from '../components/v2/EosLife.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const route = reactive<{ path: string; query: Record<string, any> }>({ path: '/v2/eos', query: {} })
const replaced: any[] = []
const auth = { isAdmin: false, canWrite: true }
let confirmAns = true
const confirmed: string[] = []
beforeEach(() => {
  pending = []; route.query = { tab: 'os' }; auth.isAdmin = false; auth.canWrite = true; confirmAns = true; replaced.length = 0; confirmed.length = 0
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (to: any) => { replaced.push(to); route.query = to.query } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(auth.canWrite), isViewer: ref(!auth.canWrite) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
  window.confirm = ((m: string) => { confirmed.push(m); return confirmAns }) as any
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { 'data-prefix': p.prefix, 'data-cols': (p.cols ?? []).map((c: any) => c.k).join(',') }, (p.rows ?? []).map((r: any) => h('div', { class: 'row', 'data-no': r.__no },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` },
      slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': JSON.stringify(p.to) }, slots.default?.()) } })
const opts = { props: { view: 'os' }, global: { components: { V2Table, InfoNote: pass, NuxtLink: Link } } }
const it_ = (name: string, status: string, extra: any = {}) => ({ name, status, eos_date: null, source_url: null, confidence: null, note: null, count: 2, ...extra })
const grp = (items: any[], by: Record<string, number> = {}) => ({ by_status: by, items })
const SUMMARY = {
  host_os: grp([it_('RHEL 7', 'expired', { family: 'Linux', linux_distro: 'RHEL' }), it_('Windows 2012', 'expired', { family: 'Windows' }),
    it_('AIX 7.2', 'ok', { family: 'AIX' }), it_('RHEL 8.x', 'ok', { family: 'Linux', merged_versions: ['RHEL 8.1', 'RHEL 8.2'] })], { expired: 5, ok: 3 }),
  firmware: grp([]), software: grp([]), insufficient: grp([]),
  other: grp([it_('15.7(3)M8', 'unknown', { suggested: 'Cisco IOS 15.7' })], { unknown: 1 }),
  hardware: grp([it_('Dell R740', 'upcoming', { family: '主機設備', vendor: 'Dell', kind: 'device_model' })], { upcoming: 4 }),
  os_order: ['Linux', 'Windows', 'AIX'],
}
const reply = (p: any) => (p.url === '/api/eos/summary' ? SUMMARY : p.url === '/api/inventory/eos/hosts' ? { items: [] } : [])
const flush = async (fn: (p: any) => any = reply) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }
const names = (w: any) => w.find('[data-prefix="E"]').findAll('.row').map((r: any) => r.find('.c-name').text().split('（')[0].trim())
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))

describe('EOS 生命週期（V30）', () => {
  it('上方四格是六桶加總；項次照狀態再名稱、篩選後不重編；狀態快篩寫進網址', async () => {
    const w = mount(EosLife, opts)
    await flush()
    expect(w.find('[data-test="eos-st-expired"]').text()).toContain('5')
    expect(w.find('[data-test="eos-st-upcoming"]').text()).toContain('4')         // hardware 桶也算
    expect(names(w)).toEqual(['RHEL 7', 'Windows 2012', 'AIX 7.2', 'RHEL 8.x'])
    await w.find('[data-test="eos-st-ok"]').trigger('click')
    expect(route.query).toMatchObject({ tab: 'os', est: 'ok' })
    await flushPromises()
    const rows = w.find('[data-prefix="E"]').findAll('.row')
    expect(rows.map((r: any) => r.attributes('data-no'))).toEqual(['3', '4'])          // 同一筆同一個號碼
    await w.find('[data-test="eos-tab-other"]').trigger('click')
    expect(route.query).toMatchObject({ tab: 'os', es: 'other', est: 'ok' })
  })
  it('第二層 Linux → 第三層發行版；換子分頁清掉第二／三層', async () => {
    route.query = { tab: 'os', ef: 'Linux', esf: 'RHEL' }
    const w = mount(EosLife, opts)
    await flush()
    expect(names(w)).toEqual(['RHEL 7'])                                               // RHEL 8.x 沒有 linux_distro → 其他
    await w.find('[data-test="eos-tab-vm"]').trigger('click')
    expect(route.query.ef).toBeUndefined(); expect(route.query.esf).toBeUndefined()
  })
  it('移分類先確認，取消不送；確認後 POST 原名與目標分類', async () => {
    const w = mount(EosLife, opts)
    await flush()
    confirmAns = false
    await btn(w.find('[data-prefix="E"] .row'), '→ 韌體').trigger('click')
    expect(confirmed[0]).toBe('把「RHEL 7」歸類改成「韌體」？（技術判斷不變，只是改變由誰維護）')
    await flushPromises()
    expect(pending.some(p => p.url === '/api/eos/category-override')).toBe(false)
    confirmAns = true
    await btn(w.find('[data-prefix="E"] .row'), '→ 韌體').trigger('click')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/eos/category-override')!.opts.body).toEqual({ canonical: 'RHEL 7', category: 'firmware' })
  })
  it('合併列沒有改名框也不能移分類；改名用後端給的 kind，Enter 後失焦不重送', async () => {
    route.query = { tab: 'hw' }
    const w = mount(EosLife, { ...opts, props: { view: 'hw' } })
    await flush()
    const inp = w.find('[data-prefix="E"] .row .c-rename input')
    await inp.setValue('Dell PowerEdge R740')
    await inp.trigger('keyup.enter')
    inp.element.dispatchEvent(new Event('blur'))                                   // 存檔中（輸入框已停用）又失焦：直接派事件，不經 VTU 的停用檢查
    await flushPromises()
    const posts = pending.filter(p => p.url === '/api/normalize/canonical-override')
    expect(posts.length).toBe(1)
    expect(posts[0].opts.body).toEqual({ kind: 'device_model', old_canonical: 'Dell R740', new_canonical: 'Dell PowerEdge R740' })
    w.unmount()
    route.query = { tab: 'os', est: 'ok' }
    const w2 = mount(EosLife, opts)
    await flush()
    const merged = w2.find('[data-prefix="E"]').findAll('.row')[1]
    expect(merged.find('.c-rename input').exists()).toBe(false)
    expect(merged.find('.c-cat button').exists()).toBe(false)
  })
  it('只能看的帳號：沒有改名、分類欄，未分類看不到採用／補對應；匯入只給管理員', async () => {
    auth.canWrite = false
    route.query = { tab: 'os', es: 'other' }
    const w = mount(EosLife, opts)
    await flush()
    const cols = w.find('[data-prefix="E"]').attributes('data-cols')!.split(',')
    expect(cols).not.toContain('rename'); expect(cols).not.toContain('cat'); expect(cols).toContain('alias')
    expect(w.text()).toContain('建議：Cisco IOS 15.7')
    expect(btn(w, '採用')).toBeUndefined(); expect(btn(w, '補對應')).toBeUndefined()
    expect(w.text()).toContain('匯入限管理員')
    expect(w.find('input[type="file"]').exists()).toBe(false)
    expect(w.findAll('a').some((a: any) => (a.attributes('href') ?? '').includes('/api/eos/export'))).toBe(false)
  })
  it('管理員匯入對應表先確認，取消不送', async () => {
    auth.isAdmin = true
    route.query = { tab: 'os', es: 'other' }
    const w = mount(EosLife, opts)
    await flush()
    confirmAns = false
    const input = w.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['x'], 'pending.xlsx')], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(confirmed[0]).toContain('即將匯入「pending.xlsx」')
    expect(pending.some(p => p.url === '/api/normalize/pending/import')).toBe(false)
  })
  it('補對應與採用建議都送 kind=os 的別名', async () => {
    route.query = { tab: 'os', es: 'other' }
    const w = mount(EosLife, opts)
    await flush()
    await btn(w, '採用').trigger('click')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/normalize/alias')!.opts.body).toEqual({ kind: 'os', raw_value: '15.7(3)M8', canonical: 'Cisco IOS 15.7' })
    pending.splice(0)
    await btn(w, '補對應').trigger('click')
    await w.find('input[aria-label="標準名稱"]').setValue('Cisco Network OS 15.7(3)M8')
    await btn(w, '儲存').trigger('click')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/normalize/alias')!.opts.body).toEqual({ kind: 'os', raw_value: '15.7(3)M8', canonical: 'Cisco Network OS 15.7(3)M8' })
  })
  it('「N 台」下鑽：硬體側 canonical_model、其他 canonical_os；換條件舊回應不覆蓋', async () => {
    const w = mount(EosLife, opts)
    await flush()
    const to = JSON.parse(w.find('[data-prefix="E"] .row .c-count a').attributes('data-to')!)
    expect(to.query).toMatchObject({ tab: 'os', edk: 'os', edv: 'RHEL 7' })
    route.query = to.query
    await flushPromises()
    const old = pending.find(p => p.url === '/api/assets')!
    expect(old.opts.query).toEqual({ canonical_os: 'RHEL 7' })
    route.query = { tab: 'hw', edk: 'model', edv: 'Dell R740' }
    await flushPromises()
    const cur = pending.find(p => p.url === '/api/assets' && p !== old)!
    expect(cur.opts.query).toEqual({ canonical_model: 'Dell R740' })
    cur.resolve([{ hostname: 'NEWHOST', ip: '10.99.0.1', asset_serial: 'S1' }])
    old.resolve([{ hostname: 'OLDHOST', ip: '10.99.0.2', asset_serial: 'S2' }])
    await flushPromises()
    expect(w.text()).toContain('NEWHOST'); expect(w.text()).not.toContain('OLDHOST')
  })
  it('搜尋跨全部分頁找，點「分頁」跳過去並清掉搜尋', async () => {
    route.query = { tab: 'os', eq: 'dell' }
    const w = mount(EosLife, opts)
    await flush()
    expect(names(w)).toEqual(['Dell R740'])
    await w.find('[data-prefix="E"] .row .c-_tabLabel button').trigger('click')
    expect(route.query).toEqual({ tab: 'hw', es: 'hardware' })                     // 外層頁籤一起換到「硬體」
  })
  it('<style> 編譯沒有錯', async () => {
    const { parse, compileStyle } = await import('@vue/compiler-sfc')
    const fs = await import('node:fs')
    const d = parse(fs.readFileSync('components/v2/EosLife.vue', 'utf8')).descriptor
    for (const s of d.styles) expect(compileStyle({ source: s.content, filename: 'EosLife', id: 'data-v-x', scoped: true }).errors).toEqual([])
  })
})

describe('第 12 批審查補測', () => {
  const HW = { ...SUMMARY, hardware: grp([...SUMMARY.hardware.items, it_('Dell iDRAC (BMC)', 'ok', { family: '主機設備', vendor: 'Dell', kind: 'os' })], { upcoming: 4 }) }
  const hwReply = (p: any) => (p.url === '/api/eos/summary' ? HW : reply(p))
  it('硬體分頁裡 kind=os 的項目（iDRAC）改名送 kind=os；硬體列下鑽帶 edk=model', async () => {
    route.query = { tab: 'hw' }
    const w = mount(EosLife, { ...opts, props: { view: 'hw' } })
    await flush(hwReply)
    const rows = w.find('[data-prefix="E"]').findAll('.row')
    const idrac = rows.find((r: any) => r.find('.c-name').text().startsWith('Dell iDRAC'))!
    expect(JSON.parse(idrac.find('.c-count a').attributes('data-to')!).query).toMatchObject({ tab: 'hw', edk: 'model', edv: 'Dell iDRAC (BMC)' })
    const inp = idrac.find('.c-rename input')
    await inp.setValue('Dell iDRAC 9')
    await inp.trigger('keyup.enter')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/normalize/canonical-override')!.opts.body).toEqual({ kind: 'os', old_canonical: 'Dell iDRAC (BMC)', new_canonical: 'Dell iDRAC 9' })
  })
  it('搜尋結果也吃狀態快篩；點分頁跳過去狀態快篩留著', async () => {
    route.query = { tab: 'os', eq: 'rhel', est: 'expired' }
    const w = mount(EosLife, opts)
    await flush()
    expect(names(w)).toEqual(['RHEL 7'])
    await w.find('[data-prefix="E"] .row .c-_tabLabel button').trigger('click')
    expect(route.query).toEqual({ tab: 'os', es: 'host_os', est: 'expired' })
  })
  it('移分類成功後重新讀一次 summary', async () => {
    const w = mount(EosLife, opts)
    await flush()
    await btn(w.find('[data-prefix="E"] .row'), '→ 韌體').trigger('click')
    await flushPromises()
    pending.splice(0).forEach(p => p.resolve({ ok: true }))
    await flushPromises()
    expect(pending.some(p => p.url === '/api/eos/summary')).toBe(true)
  })
  it('一般帳號：沒有匯入（只有管理員）', async () => {
    route.query = { tab: 'os', es: 'other' }
    const w = mount(EosLife, opts)
    await flush()
    expect(w.find('input[type="file"]').exists()).toBe(false)
    expect(w.text()).toContain('匯入限管理員')
  })
})
