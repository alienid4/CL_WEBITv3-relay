/**
 * 2.048.18 V27：9.1 資料品質改 V2 原生，實掛：
 *   - 維度「不符合」下鑽 qd=、可信度分數／來源組合下鑽 qt/qv（同一支 /api/trust/hosts）、舊回應不覆蓋
 *   - 人名合併：先預覽 → confirm → 送預覽過的那一組；取消不送；改輸入框清掉預覽；只能看的帳號沒有確認合併
 * V42：7.1 照片／機櫃圖對帳改 V2 原生：匯入與匯出限管理員；確認／判讀錯了一般帳號以上、走 guardedFetch；篩選在網址
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import { upText } from '../utils/upText'
import DataQuality from '../components/v2/DataQuality.vue'
import RackDiagram from '../components/v2/RackDiagram.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void; guarded?: boolean }[] = []
const route = reactive<{ query: Record<string, any> }>({ query: {} })
const auth = { isAdmin: false, canWrite: true }
const navs: any[] = []
const fetcher = (guarded = false) => (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject, guarded }))
beforeEach(() => {
  pending = []; route.query = {}; auth.isAdmin = false; auth.canWrite = true; navs.length = 0
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest, upText })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('navigateTo', (x: any) => { navs.push(x) })
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(auth.canWrite), isViewer: ref(!auth.canWrite) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: fetcher() }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: fetcher(true) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}`, 'data-link': c.link ? JSON.stringify(c.link(r)) ?? '' : '' },
      slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': JSON.stringify(p.to) }, slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: Link, SshProbe: pass } } }
const flush = async (fn: (p: any) => any) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))

const DQ = { asset_total: 10, score: 92, score_sample: 8, score_basis: 'b', coverage_rate: 80, fresh_days: 30, stale_days: 90,
  dimensions: [{ key: 'ip_ok', label: 'IP 正確', kind: 'verifiable', checked: 8, ok: 6, bad: 2, rate: 75, note: '' }] }
const TRUST = { total: 3, machines: 3, rule: 'r', by_score: [{ score: 100, count: 2, reasons: [{ text: 'VM 三來源', count: 2 }] }, { score: 50, count: 1, reasons: [] }],
  by_combo: [{ combo: 'dynassets+cia', count: 3 }] }
const dqReply = (p: any) => (p.url === '/api/data-quality' ? DQ : p.url === '/api/measured-mismatch' ? { corrected: [1], suspect: [], hosts: 1 }
  : p.url === '/api/trust/distribution' ? TRUST : p.url === '/api/data-quality/field-fill' ? { fields: [], total_rows: 0 }
    : p.url === '/api/data-quality/single-person-names' ? { names: ['李太名'] } : { items: [] })

describe('資料品質（V27）', () => {
  it('數字下鑽：不符合 → qd；可信度分數 → qt=s；來源組合 → qt=c；實測不符連 9.1', async () => {
    const w = mount(DataQuality, opts)
    await flush(dqReply)
    expect(JSON.parse(w.find('[data-prefix="Q"] .c-bad').attributes('data-link')!).query).toEqual({ tab: 'quality', qd: 'ip_ok' })
    expect(JSON.parse(w.find('[data-prefix="S"] .c-count').attributes('data-link')!).query).toEqual({ tab: 'quality', qt: 's', qv: '100' })
    expect(JSON.parse(w.find('[data-prefix="C"] .c-count').attributes('data-link')!).query).toEqual({ tab: 'quality', qt: 'c', qv: 'dynassets+cia' })
    expect(w.text()).toContain('dynassets＋CIA')
    expect(w.findAll('a').some(a => a.attributes('data-to')?.includes('"mk":"suspect"'))).toBe(true)
    expect(w.text()).toContain('掃描只涵蓋 80%')
  })
  it('網址 qt=s → /api/trust/hosts?score=；換成 qd 後舊回應不覆蓋', async () => {
    route.query = { tab: 'quality', qt: 's', qv: '100' }
    const w = mount(DataQuality, opts)
    await flushPromises()
    const t = pending.find(p => p.url === '/api/trust/hosts')!
    expect(t.opts.query).toEqual({ score: '100' })
    route.query = { tab: 'quality', qd: 'ip_ok' }
    await flushPromises()
    const d = pending.find(p => p.url === '/api/data-quality/ip_ok')!
    d.resolve({ items: [{ asset_serial: 'A1', hostname: 'h1', reason: 'r' }] })
    await flushPromises()
    t.resolve({ items: [{ asset_serial: 'X1', sources: {}, kind: 'vm' }, { asset_serial: 'X2', sources: {}, kind: 'vm' }] })
    await flush(dqReply)
    expect(w.text()).toContain('1 筆')
    expect(w.text()).toContain('IP 正確：不符合')
  })
  it('人名合併：預覽 → 確認 → 送預覽過的那一組；confirm 取消不送；改輸入清掉預覽', async () => {
    route.query = { tab: 'quality', qn: '1' }
    const w = mount(DataQuality, opts)
    await flush(dqReply)
    await btn(w, '李太名').trigger('click')
    const inputs = w.findAll('input.minp')
    await inputs[1].setValue('李太明')
    await btn(w, '預覽').trigger('click')
    await flushPromises()
    const pv = pending.find(p => p.url === '/api/data-quality/person-merge/preview')!
    expect(pv.opts).toEqual({ method: 'POST', body: { from_name: '李太名', to_name: '李太明' } })
    pending.splice(pending.indexOf(pv), 1); pv.resolve({ from_name: '李太名', to_name: '李太明', total: 2, fields: [{ label: '保管者', count: 2 }], sample: [] })
    await flushPromises()
    const cf = vi.fn(() => false); vi.stubGlobal('confirm', cf); (window as any).confirm = cf
    await btn(w, '確認合併（2 筆）').trigger('click')
    await flushPromises()
    expect(cf).toHaveBeenCalled()
    expect(pending.some(p => p.url === '/api/data-quality/person-merge')).toBe(false)
    ;(window as any).confirm = () => true
    await btn(w, '確認合併').trigger('click')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/data-quality/person-merge')!.opts.body).toEqual({ from_name: '李太名', to_name: '李太明' })
    pending.length = 0
    await inputs[1].setValue('別的名字')
    expect(btn(w, '確認合併')).toBeUndefined()
  })
  it('只能看的帳號：能預覽，沒有確認合併', async () => {
    auth.canWrite = false
    route.query = { tab: 'quality', qn: '1' }
    const w = mount(DataQuality, opts)
    await flush(dqReply)
    const inputs = w.findAll('input.minp')
    await inputs[0].setValue('a'); await inputs[1].setValue('b')
    await btn(w, '預覽').trigger('click')
    await flush(() => ({ from_name: 'a', to_name: 'b', total: 1, fields: [], sample: [] }))
    expect(btn(w, '確認合併')).toBeUndefined()
    expect(w.text()).toContain('合併限一般帳號以上')
  })
  it('載入失敗：主資料寫原因；可信度分布失敗也寫出來', async () => {
    const w = mount(DataQuality, opts)
    await flushPromises()
    for (const p of pending.splice(0)) (p.url === '/api/data-quality' || p.url === '/api/trust/distribution') ? p.reject({ data: { detail: 'boom' } }) : p.resolve(dqReply(p))
    await flushPromises()
    expect(w.text()).toContain('資料品質載入失敗：boom')
  })
})

const IMP = [{ id: 7, site: '內湖', file_name: 'a.xlsx', sha256: 'abcdef0123456789', sheets: 2, rows: 3, imported_by: 'u', imported_at: '2026-11-01 10:00:00' }]
const ROW = (no: number, result: string, extra: any = {}) => ({ row_id: 100 + no, no, sheet: '3F', rack: 'A1', u: '10', u_start: 10, text: `dev${no}`, ip: null, state: null, result,
  match_by: 'IP', source: null, shot_date: null, confidence: null, review: null, sys_serial: `S${no}`, sys_name: `h${no}`, sys_room: '內湖', ...extra })
const RC = { import: IMP[0], rows: [ROW(1, '一致'), ROW(2, '機櫃不同'), ROW(3, '同櫃但U位不同')], counts: { 一致: 1, 機櫃不同: 1, 同櫃但U位不同: 1, 規劃中: 0 },
  groups: { 位置不同: 2, 系統有照片沒有: 1 }, missing_racks: [{ rack: 'B9', room: '內湖', count: 2, names: ['x', 'y'] }],
  system_only: [{ key: 'k', serial: 'S9', name: 'h9', rack: 'A1', u: '', model: 'dell  r740', ip: null, note: '' }], rate: [], rates: null }
const rdReply = (p: any) => (p.url === '/api/rack-diagram/imports' ? IMP : p.url.endsWith('/reconcile') ? RC : p.url.endsWith('/diff') ? { previous: null, added: [], removed: [], moved: [] }
  : p.url === '/api/racks/datacenter' ? { rooms: [{ room: '內湖 3F', racks: [1] }] } : [])

describe('照片／機櫃圖對帳（V42）', () => {
  it('一般帳號：沒有匯入、沒有匯出 CIA 待更正；有確認按鈕；不載入機房清單', async () => {
    route.query = { tab: 'photo', rt: 'import' }
    let w = mount(RackDiagram, opts)
    await flush(rdReply); await flush(rdReply)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    expect(pending.some(p => p.url === '/api/racks/datacenter')).toBe(false)
    w.unmount()
    route.query = { tab: 'photo', rt: 'result' }
    w = mount(RackDiagram, opts)
    await flush(rdReply); await flush(rdReply)
    expect(w.text()).not.toContain('匯出 CIA 待更正')
    expect(w.findAll('button').filter(b => b.text() === '確認（照片對）').length).toBe(2)     // 只有要人確認的兩列
  })
  it('差異群組「位置不同」→ rg，只留兩列；結果 chip → rr', async () => {
    route.query = { tab: 'photo', rt: 'result', rg: '位置不同' }
    const w = mount(RackDiagram, opts)
    await flush(rdReply); await flush(rdReply)
    expect(w.findAll('[data-prefix="R"] .row').length).toBe(2)
    const chip = w.findAll('a').find(a => a.text() === '機櫃不同 1')!
    expect(JSON.parse(chip.attributes('data-to')!).query).toMatchObject({ tab: 'photo', rt: 'result', rr: '機櫃不同' })
    const so = w.findAll('a').find(a => a.text().startsWith('系統有照片沒有 1'))!
    expect(JSON.parse(so.attributes('data-to')!).query).toMatchObject({ rt: 'sysonly' })
  })
  it('確認（照片對）走 guardedFetch、body 照舊；只能看的帳號沒有按鈕', async () => {
    route.query = { tab: 'photo', rt: 'result', rr: '機櫃不同' }
    let w = mount(RackDiagram, opts)
    await flush(rdReply); await flush(rdReply)
    await w.find('input.inp.sm').setValue('看過照片')
    await btn(w, '確認（照片對）').trigger('click')
    await flushPromises()
    const g = pending.find(p => p.guarded)!
    expect(g.url).toBe('/api/rack-diagram/rows/102/review')
    expect(g.opts).toEqual({ method: 'POST', body: { action: 'confirm', note: '看過照片' } })
    g.resolve({ action: 'confirm', action_label: '確認（照片對）', note: '看過照片', reviewed_by: 'u', reviewed_at: '2026-11-05 10:00:00' }); await flushPromises()
    expect(w.text()).toContain('確認（照片對）（u 11-05 10:00）')
    w.unmount()
    auth.canWrite = false
    w = mount(RackDiagram, opts)
    await flush(rdReply); await flush(rdReply)
    expect(btn(w, '確認（照片對）')).toBeUndefined()
  })
  it('管理員：匯入 FormData 照舊、完成後跳到那一份；匯出現場格式列出機房', async () => {
    auth.isAdmin = true
    route.query = { tab: 'photo', rt: 'import' }
    const w = mount(RackDiagram, opts)
    await flush(rdReply); await flush(rdReply)
    const f = w.find('input[type="file"]')
    Object.defineProperty(f.element, 'files', { value: [new File(['x'], 'a.xlsx')] })
    await f.trigger('change')
    await btn(w, '匯入並對帳').trigger('click')
    await flushPromises()
    const post = pending.find(p => p.url === '/api/rack-diagram/import')!
    expect((post.opts.body as FormData).get('site')).toBe('內湖')
    expect((post.opts.body as FormData).get('default_source')).toBeNull()
    post.resolve({ import_id: 8, rows: 3, sheets: 2, skipped: null }); pending.splice(pending.indexOf(post), 1)
    await flush(rdReply)
    expect(navs.at(-1).query).toMatchObject({ rt: 'result', ri: '8' })
    route.query = { tab: 'photo', rt: 'export' }
    await flush(rdReply)
    expect(w.findAll('a').some(a => a.text() === '內湖 3F')).toBe(true)
  })
  it('沒拍到的櫃：點設備數 → rm=機房|櫃，列出設備；同名櫃不同機房不會串', async () => {
    const two = { ...RC, missing_racks: [{ rack: 'B9', room: '內湖 3F', count: 2, names: ['x', 'y'] }, { rack: 'B9', room: '內湖 4F', count: 1, names: ['z'] }] }
    const reply = (p: any) => (p.url.endsWith('/reconcile') ? two : rdReply(p))
    route.query = { tab: 'photo', rt: 'missing' }
    const w = mount(RackDiagram, opts)
    await flush(reply); await flush(reply)
    const links = w.findAll('[data-prefix="X"] .c-count').map(c => JSON.parse(c.attributes('data-link')!).query.rm)
    expect(links).toEqual(['內湖 3F|B9', '內湖 4F|B9'])
    route.query = { tab: 'photo', rt: 'missing', rm: '內湖 4F|B9' }
    await flushPromises()
    expect(w.findAll('[data-prefix="N"] .row').length).toBe(1)
    expect(w.text()).toContain('z')
  })
})

describe('第 10 批審查補測', () => {
  it('沒有任何匯入：管理員仍進得了「匯出現場格式」；確認紀錄頁籤也不被擋', async () => {
    auth.isAdmin = true
    route.query = { tab: 'photo', rt: 'export' }
    const w = mount(RackDiagram, opts)
    const empty = (p: any) => (p.url === '/api/rack-diagram/imports' ? [] : rdReply(p))
    await flush(empty); await flush(empty)
    expect(w.findAll('a').some(a => a.text() === '全部機房')).toBe(true)
    route.query = { tab: 'photo', rt: 'reviews' }
    await flushPromises()
    expect(w.text()).not.toContain('先到「匯入」上傳一份現場圖')
  })
  it('換匯入 id：慢回來的舊對帳不覆蓋新的', async () => {
    const IMP2 = [{ ...IMP[0], id: 8, file_name: 'b.xlsx' }, IMP[0]]
    route.query = { tab: 'photo', rt: 'result', ri: '7' }
    const w = mount(RackDiagram, opts)
    await flushPromises()
    const old = pending.filter(p => p.url.startsWith('/api/rack-diagram/imports/7/'))
    pending.find(p => p.url === '/api/rack-diagram/imports')!.resolve(IMP2)
    route.query = { tab: 'photo', rt: 'result', ri: '8' }
    await flushPromises()
    for (const p of pending.filter(p => p.url.startsWith('/api/rack-diagram/imports/8/'))) p.resolve(p.url.endsWith('/reconcile') ? { ...RC, import: IMP2[0] } : { previous: null, added: [], removed: [], moved: [] })
    await flushPromises()
    for (const p of old) p.resolve(p.url.endsWith('/reconcile') ? RC : { previous: null, added: [], removed: [], moved: [] })
    await flushPromises()
    expect(w.text()).toContain('#8')
    expect(w.text()).not.toContain('#7 ')
  })
  it('人名合併：預覽回來前改了輸入 → 不顯示那份預覽、沒有確認合併', async () => {
    route.query = { tab: 'quality', qn: '1' }
    const w = mount(DataQuality, opts)
    await flush(dqReply)
    const inputs = w.findAll('input.minp')
    await inputs[0].setValue('甲'); await inputs[1].setValue('乙')
    await btn(w, '預覽').trigger('click')
    await flushPromises()
    await inputs[1].setValue('丙')
    pending.find(p => p.url === '/api/data-quality/person-merge/preview')!.resolve({ from_name: '甲', to_name: '乙', total: 3, fields: [], sample: [] })
    await flushPromises()
    expect(btn(w, '確認合併')).toBeUndefined()
  })
})
