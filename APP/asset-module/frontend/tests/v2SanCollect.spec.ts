/**
 * 2.048.12 V52：6.2 SAN 收集改 V2 原生，實掛：
 *   - 權限：一般帳號沒有收集／離線匯入／一鍵修復，有下線；只能看的帳號連下線都沒有；非管理員不載入憑證清單
 *   - 勾選批次收集：同一組帳密依序送、body 正確、收完密碼清空；失敗原因與提示顯示在列上
 *   - 環境篩選 se=、WWPN 明細 sd=；自我檢查本身失敗也顯示黃底
 *   - 離線匯入 409 IP 對不上 → 問人，確定才帶 force 重送
 * V24 收集帳號遷移：卡片下鑽 ms=、切換先確認、取消不送、探測／切換限管理員、沒探測過不顯示 0%
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import San from '../components/v2/San.vue'
import CollectAccount from '../components/v2/CollectAccount.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const route = reactive<{ query: Record<string, string> }>({ query: {} })
const auth = { isAdmin: false, canWrite: true }
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = false; auth.canWrite = true
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(auth.canWrite), isViewer: ref(!auth.canWrite) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'selectable', 'selected', 'rowKey'], emits: ['update:selected'], setup(p, { slots, emit }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [
    p.selectable ? h('input', { type: 'checkbox', class: 'pick', onChange: () => emit('update:selected', { ...p.selected, [r[p.rowKey]]: true }) }) : null,
    ...(p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(r[c.k] ?? '')))])))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const reloads = { n: 0 }
const CollectLog = defineComponent({ setup(_p, { expose }) { expose({ reload: () => { reloads.n++ } }); return () => h('span') } })
const AliveCheck = defineComponent({ setup() { return () => h('span', { class: 'alive' }) } })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': JSON.stringify(p.to) }, slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: Link, CollectLog, OfflineMarkModal: pass, AliveCheck } } }
const sw = (ip: string, extra: any = {}) => ({ ip, hostname: `sw-${ip}`, asset_serial: `S-${ip}`, environment: '測試區', collected: false, wwpn_count: null, ...extra })
const list = (items: any[]) => ({ items, total: items.length, collected: items.filter(i => i.collected).length })
const flush = async (fn: (p: any) => any) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }
const reply = (items: any[]) => (p: any) => (p.url === '/api/san/switches' ? list(items) : p.url === '/api/credentials' ? [] : { ok: true })
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))

describe('SAN 收集（V52）', () => {
  it('一般帳號：沒有收集／離線匯入／勾選，有下線與偵測存活；不載入憑證清單', async () => {
    const w = mount(San, opts)
    await flushPromises()
    expect(pending.some(p => p.url === '/api/credentials')).toBe(false)
    await flush(reply([sw('10.99.1.1')]))
    expect(btn(w, '收集')).toBeUndefined()
    expect(btn(w, '離線匯入')).toBeUndefined()
    expect(w.find('.pick').exists()).toBe(false)
    expect(btn(w, '下線')).toBeDefined()
    expect(w.find('.alive').exists()).toBe(true)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
  })
  it('只能看的帳號：連下線、偵測存活都沒有', async () => {
    auth.canWrite = false
    const w = mount(San, opts)
    await flush(reply([sw('10.99.1.1')]))
    expect(btn(w, '下線')).toBeUndefined()
    expect(w.find('.alive').exists()).toBe(false)
  })
  it('管理員勾兩台批次收集：同一組帳密依序送，失敗原因顯示在列上，收完密碼清空', async () => {
    auth.isAdmin = true
    const w = mount(San, opts)
    await flush(reply([sw('10.99.1.1'), sw('10.99.1.2')]))
    const picks = w.findAll('.pick')
    await picks[0].trigger('change'); await picks[1].trigger('change')
    await btn(w, '收集選取（2）').trigger('click')
    const inputs = w.findAll('.modal input')
    await inputs[0].setValue('ro'); await inputs[1].setValue('pw')
    await w.findAll('.modal button').find(b => b.text() === '開始收集')!.trigger('click')
    await flushPromises()
    let post = pending.find(p => p.url === '/api/san/collect')!
    expect(post.opts.body).toEqual({ ip: '10.99.1.1', username: 'ro', password: 'pw' })
    pending.splice(pending.indexOf(post), 1); post.resolve({ wwpn_count: 7 })
    await flushPromises()
    post = pending.find(p => p.url === '/api/san/collect')!
    expect(post.opts.body).toEqual({ ip: '10.99.1.2', username: 'ro', password: 'pw' })
    pending.splice(pending.indexOf(post), 1); post.reject({ data: { detail: 'connect timed out' } })
    await flushPromises()
    for (const p of pending.splice(0)) p.resolve(list([sw('10.99.1.1', { collected: true, wwpn_count: 7 }), sw('10.99.1.2')]))
    await flushPromises()
    expect(w.text()).toContain('剛收到 7 筆')
    expect(w.text()).toContain('connect timed out')
    expect(w.text()).toContain('防火牆')
    await btn(w, '收集').trigger('click')
    expect((w.findAll('.modal input')[1].element as HTMLInputElement).value).toBe('')
  })
  it('環境篩選 se=prod 只留正式區；明細 sd= 一列一個 WWPN', async () => {
    route.query = { tab: 'san', se: 'prod' }
    const w = mount(San, opts)
    await flush(reply([sw('10.99.1.1', { environment: '正式區' }), sw('10.99.1.2')]))
    expect(w.findAll('[data-prefix="S"] .row').length).toBe(1)
    route.query = { tab: 'san', sd: '10.99.1.1' }
    await flush(p => (p.url.startsWith('/api/san/switches/') ? { data: { switch: 'SW1', zoning_cfg: 'cfgA', rows: [{ wwpn: 'aa', port: 3, switch: 1, zones: ['z1', 'z2'] }, { wwpn: 'bb' }] } } : {}))
    const rows = w.findAll('[data-prefix="W"] .row')
    expect(rows.length).toBe(2)
    expect(rows[0].find('.c-zones_text').text()).toBe('z1、z2')
    expect(rows[0].find('.c-port_text').text()).toBe('1/3')
  })
  it('自我檢查本身失敗：照樣顯示黃底，不是消失', async () => {
    const w = mount(San, opts)
    await flushPromises()
    for (const p of pending.splice(0)) (p.url === '/api/system/collector-selfcheck' ? p.reject(new Error('boom')) : p.resolve(list([])))
    await flushPromises()
    expect(w.find('[data-test="envwarn"]').text()).toContain('自我檢查本身失敗')
    expect(btn(w, '一鍵修復')).toBeUndefined()
  })
  it('離線匯入：409 IP 對不上 → 問人，確定才帶 force 重送；取消不送', async () => {
    auth.isAdmin = true
    const w = mount(San, opts)
    await flush(reply([]))
    await btn(w, '離線匯入').trigger('click')
    await flush(() => ({ commands: ['switchshow'], min_required: ['switchshow'], steps: [] }))
    await w.find('.modal input').setValue('10.99.1.9')
    await w.find('.modal textarea').setValue('switchshow\n...')
    const conf = vi.fn(() => false)
    vi.stubGlobal('confirm', conf)
    await btn(w, '匯入判讀').trigger('click')
    await flushPromises()
    let post = pending.splice(0).find(p => p.url === '/api/san/import')!
    expect(post.opts.body).toEqual({ ip: '10.99.1.9', transcript: 'switchshow\n...', force: false })
    post.reject({ statusCode: 409, data: { detail: 'IP 對不上' } })
    await flushPromises()
    expect(conf).toHaveBeenCalledTimes(1)
    expect(pending.some(p => p.url === '/api/san/import')).toBe(false)
    conf.mockReturnValue(true)
    await btn(w, '匯入判讀').trigger('click')
    await flushPromises()
    post = pending.splice(0).find(p => p.url === '/api/san/import')!
    post.reject({ statusCode: 409, data: { detail: 'IP 對不上' } })
    await flushPromises()
    expect(pending.find(p => p.url === '/api/san/import')!.opts.body.force).toBe(true)
  })
})

const summary = (probed = 3) => ({ probed, target_account: 'webit3sc', legacy_account: 'old', counts: { 可切換: 1, 待納管: 0, 已完成: 2, 兩個都進不去: 0 }, done_pct: 67, denominator: 3, denominator_text: 'x' })
const det = (state: string) => ({ asset_serial: 'S1', hostname: 'h1', ip: '10.99.2.1', platform: 'linux', state, new_ok: state === '已完成' ? 1 : 0, old_ok: 1 })

describe('收集帳號遷移（V24）', () => {
  it('非管理員：沒有探測、沒有切換／還原', async () => {
    route.query = { st: 'identity', sub: 'migration', ms: '可切換' }
    const w = mount(CollectAccount, opts)
    await flush(p => (p.url.includes('/detail') ? { items: [det('可切換')] } : summary()))
    expect(btn(w, '重新探測')).toBeUndefined()
    expect(btn(w, '切換這台')).toBeUndefined()
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    expect(w.findAll('[data-prefix="M"] .row').length).toBe(1)
  })
  it('管理員：ms= 帶到後端；切換先確認，取消不送；確定送對的序號並重抓看板與明細', async () => {
    auth.isAdmin = true
    route.query = { st: 'identity', sub: 'migration', ms: '可切換' }
    const w = mount(CollectAccount, opts)
    await flushPromises()
    expect(pending.some(p => p.url === `/api/collect-account/migration/detail?state=${encodeURIComponent('可切換')}`)).toBe(true)
    await flush(p => (p.url.includes('/detail') ? { items: [det('可切換')] } : summary()))
    const conf = vi.fn(() => false)
    vi.stubGlobal('confirm', conf)
    await btn(w, '切換這台').trigger('click')
    await flushPromises()
    expect(conf).toHaveBeenCalledTimes(1)
    expect(pending.length).toBe(0)
    conf.mockReturnValue(true)
    await btn(w, '切換這台').trigger('click')
    await flushPromises()
    const post = pending.splice(0)[0]
    expect(post.url).toBe('/api/collect-account/migration/switch?asset_serial=S1')
    expect(post.opts.method).toBe('POST')
    post.resolve({ text: '已切換' })
    await flushPromises()
    expect(pending.map(p => p.url)).toContain('/api/collect-account/migration')
    await flush(() => summary())
    expect(pending.some(p => p.url.includes('/detail'))).toBe(true)
    await flush(() => ({ items: [det('已完成')] }))
    expect(w.find('[data-test="msg"]').text()).toBe('已切換')
    expect(btn(w, '還原')).toBeDefined()
  })
  it('沒探測過：講「還沒探測」，不顯示卡片與 0%；看板載不到要顯示錯誤', async () => {
    const w = mount(CollectAccount, opts)
    await flush(() => summary(0))
    expect(w.find('[data-test="never"]').exists()).toBe(true)
    expect(w.find('.card').exists()).toBe(false)
    const w2 = mount(CollectAccount, opts)
    await flushPromises()
    pending.splice(0)[0].reject({ data: { detail: 'db down' } })
    await flushPromises()
    expect(w2.text()).toContain('遷移看板載入失敗：db down')
  })
})

describe('第 7 批審查補測（2.048.13）', () => {
  it('SAN：se= 三種環境各留對的列（含「其他」）', async () => {
    const data = [sw('10.99.1.1', { environment: '正式區' }), sw('10.99.1.2', { environment: '測試區' }), sw('10.99.1.3', { environment: '備援' })]
    for (const [se, ip] of [['prod', '10.99.1.1'], ['test', '10.99.1.2'], ['other', '10.99.1.3']]) {
      route.query = { tab: 'san', se }
      const w = mount(San, opts)
      await flush(reply(data))
      const rows = w.findAll('[data-prefix="S"] .row')
      expect(rows.length).toBe(1)
      expect(rows[0].find('.c-ip').text()).toBe(ip)
      w.unmount()
    }
  })
  it('SAN：明細 sd= 舊請求晚回來不覆蓋新的', async () => {
    route.query = { tab: 'san', sd: '10.99.1.1' }
    const w = mount(San, opts)
    await flushPromises()
    const older = pending.splice(0).filter(p => p.url.startsWith('/api/san/switches/'))
    route.query = { tab: 'san', sd: '10.99.1.2' }
    await flush(p => (p.url.startsWith('/api/san/switches/') ? { data: { switch: 'NEW', rows: [{ wwpn: 'nn' }] } } : {}))
    for (const p of older) p.resolve({ data: { switch: 'OLD', rows: [{ wwpn: 'o1' }, { wwpn: 'o2' }] } })
    await flushPromises()
    expect(w.findAll('[data-prefix="W"] .row').length).toBe(1)
    expect(w.text()).toContain('NEW')
  })
  it('SAN：清單載不到要看得到錯誤；收完重抓失敗時表格與每列失敗原因仍在', async () => {
    const w = mount(San, opts)
    await flushPromises()
    for (const p of pending.splice(0)) (p.url === '/api/san/switches' ? p.reject({ data: { detail: 'db down' } }) : p.resolve({ ok: true }))
    await flushPromises()
    expect(w.find('[data-test="err"]').text()).toContain('SAN 清單載入失敗：db down')
    auth.isAdmin = true
    const w2 = mount(San, opts)
    await flush(reply([sw('10.99.1.1')]))
    await w2.findAll('button').find(b => b.text() === '收集')!.trigger('click')
    const inputs = w2.findAll('.modal input')
    await inputs[0].setValue('ro'); await inputs[1].setValue('pw')
    await w2.findAll('.modal button').find(b => b.text() === '開始收集')!.trigger('click')
    await flushPromises()
    pending.splice(0)[0].reject({ data: { detail: 'auth failed' } })
    await flushPromises()
    pending.splice(0)[0].reject({ data: { detail: 'db down' } })
    await flushPromises()
    expect(w2.find('[data-test="err"]').exists()).toBe(true)
    expect(w2.findAll('[data-prefix="S"] .row').length).toBe(1)
    expect(w2.text()).toContain('auth failed')
  })
  it('SAN：取消登入視窗後密碼清空', async () => {
    auth.isAdmin = true
    const w = mount(San, opts)
    await flush(reply([sw('10.99.1.1')]))
    await w.findAll('button').find(b => b.text() === '收集')!.trigger('click')
    await w.findAll('.modal input')[1].setValue('typed')
    await w.findAll('.modal button').find(b => b.text() === '取消')!.trigger('click')
    await w.findAll('button').find(b => b.text() === '收集')!.trigger('click')
    expect((w.findAll('.modal input')[1].element as HTMLInputElement).value).toBe('')
  })
  it('遷移：卡片與公式數字都連到同一組網址（含 st、sub、ms）', async () => {
    const w = mount(CollectAccount, opts)
    await flush(() => summary())
    const tos = w.findAll('[data-to]').map(a => JSON.parse(a.attributes('data-to')!))
    expect(tos).toContainEqual({ path: '/v2/settings', query: { st: 'identity', sub: 'migration', ms: '可切換' } })
    expect(tos.filter(t => t.query.ms === '已完成').length).toBeGreaterThanOrEqual(3)     // 卡片、百分比、公式裡的數字
  })
  it('遷移：一般帳號看不到「還原」；指令清單用 GET 取文字；動作後重抓不清掉明細', async () => {
    route.query = { st: 'identity', sub: 'migration', ms: '已完成' }
    const w = mount(CollectAccount, opts)
    await flush(p => (p.url.includes('/detail') ? { items: [det('已完成')] } : summary()))
    expect(btn(w, '還原')).toBeUndefined()
    await btn(w, '看有哪些要移除').trigger('click')
    await flush(() => ({ count: 0, items: [], note: '' }))
    await btn(w, '產生指令清單').trigger('click')
    await flushPromises()
    const g = pending.splice(0)[0]
    expect(g.url).toBe('/api/collect-account/migration/removal-script')
    expect(g.opts.method).toBeUndefined()
    expect(g.opts.responseType).toBe('text')
    auth.isAdmin = true
    const w2 = mount(CollectAccount, opts)
    await flush(p => (p.url.includes('/detail') ? { items: [det('已完成')] } : summary()))
    await btn(w2, '還原').trigger('click')
    await flushPromises()
    pending.splice(0)[0].resolve({ text: 'ok' })
    await flushPromises()
    pending.splice(0)[0].resolve(summary())
    await flushPromises()
    expect(w2.findAll('[data-prefix="M"] .row').length).toBe(1)        // 背景重抓中，表格不消失
  })
})
