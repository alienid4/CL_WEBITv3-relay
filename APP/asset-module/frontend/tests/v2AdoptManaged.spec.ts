// 2.058.9／2.058.11（批 23 第 7、8 片＋補齊）：6.1「已納管」收集全部／改分類／篩選（網址）／每列重新納管／匯出欄（實掛）、
// 舊 /adopt 網址（含 #頁簽）轉址、另有納管方式的去處。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { adoptHashTarget, isPorted, v2LegacyTarget } from '../utils/v2LegacyMap'
import { MANAGED_EXPORT_ONLY, collectAllSerials, guideHref, guideText, managedMatch, managedMoveRow, pruneKeys } from '../utils/adoptCollect'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let confirmAns = true
let infoFails = false
let winFails = false
let winGate: Promise<void> | null = null
let admin = true
let write = true
let exportProps: any = null
const route = reactive({ query: {} as Record<string, any> })
let queue: any = { running: false, total: 0, done: 0, ok: 0, failed: 0, deferred: 0, pending: 0, current: null, started_at: null, finished_at: null }
const ROWS = [
  { key: 'k1', name: 'web01', ip: '10.99.0.1', serial: 'S1', type: 'Linux' },
  { key: 'k2', name: 'nohost', ip: '10.99.0.2', serial: null, type: 'Linux' },
  { key: 'k3', name: 'db03', ip: '10.99.0.3', serial: 'S3', type: 'Linux' },
]
const MS = [{ asset_serial: 'S1', ip: '10.99.0.1', hostname: 'web01', os_type: 'Linux', os_override: { by: 'amy', at: '2026-11-01', reason: null } },
  { asset_serial: 'S3', ip: '10.99.0.3', hostname: 'db03', os_type: 'AIX', os_override: null, state: '已納管但失聯', root_key: true }]
let lastRows: any[] = []
const Table = defineComponent({ props: ['rows', 'cols', 'selected', 'selectable', 'prefix', 'rowKey', 'empty', 'max', 'exportCols', 'exportAll'], emits: ['update:selected'], setup(p, { emit, slots }) {
  return () => { exportProps = p; lastRows = p.rows ?? []; return h('div', {}, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [
    h('span', { class: 'ip' }, r.ip), h('span', { class: 'os' }, r.os_text),
    h('button', { class: 'pick', onClick: () => emit('update:selected', { ...(p.selected || {}), [r.key]: true }) }, 'pick'),
    slots['cell-tags_text']?.({ row: r }), h('span', { class: 'pills' }, slots['cell-pills_text']?.({ row: r })), slots['cell-op']?.({ row: r })]))) }
} })
const MoveSpy = defineComponent({ props: ['rows'], setup(p) { return () => h('div', { class: 'move', 'data-rows': JSON.stringify(p.rows) }) } })
beforeEach(() => {
  calls = []; confirmAns = true; infoFails = false; winFails = false; winGate = null; admin = true; write = true; exportProps = null; route.query = { tab: 'onboarded' }
  queue = { running: false, total: 0, done: 0, ok: 0, failed: 0, deferred: 0, pending: 0, current: null, started_at: null, finished_at: null }
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, onBeforeUnmount, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(admin), canWrite: ref(write) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '/b' } }))
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { route.query = Object.fromEntries(Object.entries(x.query || {}).filter(([, v]) => v !== undefined)) } }))
  vi.stubGlobal('confirm', () => confirmAns)
  window.confirm = () => confirmAns
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/manage-state') { if (infoFails) throw Object.assign(new Error('x'), { data: { detail: '逾時' } }); return { items: opts.params.state === '已納管' ? MS : [] } }
    if (url === '/api/onboard/post-collect-queue') return queue
    if (url === '/api/onboard/windows/state') { if (winGate) await winGate; if (winFails) throw Object.assign(new Error('x'), { data: { detail: '500' } }); return { items: { '10.99.0.3': { opted_in: true, is_admin: 0 } } } }
    if (url === '/api/asset-tags/by-serial') return { by_serial: { S1: [{ tag: '機房搬遷' }] } }
    if (url === '/api/onboard/audit/latest-by-ip') return { '10.99.0.3': { fail: { at: '2026-11-08 10:00', short: '登入被拒' } } }
    if (url === '/api/onboard/managed/export-rows') return { items: [{ 資產序號: 'S1', IP: '10.99.0.1', 狀態: '已納管', 機房: '板橋', 納管帳號: 'webit3scan', '硬體規格（最近結果）': '成功' }, { 資產序號: '', IP: '10.99.0.2', 狀態: '已納管但失聯' }] }
    if (url === '/api/onboard/post-collect-batch') return { ...queue, total: opts.body.asset_serials.length, added: opts.body.asset_serials.length, already: 0 }
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const TagSpy = defineComponent({ props: ['rows'], emits: ['done', 'close'], setup(p, { emit }) { return () => h('div', { class: 'tagm', 'data-rows': JSON.stringify(p.rows) }, [h('button', { class: 'tdone', onClick: () => emit('done') }), h('button', { class: 'tclose', onClick: () => emit('close') })]) } })
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, V2AdoptMove: MoveSpy, TagBatchModal: TagSpy }
async function mountIt(rows = ROWS) {
  const C = (await import('../components/v2/AdoptManaged.vue')).default
  const w = mount(C, { props: { rows }, global: { stubs } })
  await flushPromises()
  return w
}
// 讀取用的 POST（批量查標籤、上次納管紀錄；舊頁載入時也這樣查）不算寫入
const READ_POSTS = ['/api/asset-tags/by-serial', '/api/onboard/audit/latest-by-ip']
const writes = () => calls.filter(c => c.opts?.method === 'POST' && !READ_POSTS.includes(c.url))

describe('6.1 已納管：收集全部（實掛）', () => {
  it('載入只讀，不送任何寫入；OS 欄讀 manage-state', async () => {
    const w = await mountIt()
    expect(writes()).toEqual([])
    expect([...new Set(calls.map(c => c.url))].sort()).toEqual(['/api/asset-tags/by-serial', '/api/manage-state', '/api/onboard/audit/latest-by-ip', '/api/onboard/post-collect-queue', '/api/onboard/windows/state'])
    expect(w.findAll('.os').map(x => x.text())).toEqual(['Linux（人工指定）', '—', 'AIX'])
  })
  it('勾三台：只送有序號的兩台；確認句講出另有 1 台不送', async () => {
    let asked = ''
    window.confirm = (m?: string) => { asked = String(m); return true }
    const w = await mountIt()
    for (const b of w.findAll('.pick')) await b.trigger('click')
    expect(w.find('[data-test="collect-all"]').text()).toContain('已勾 2 台')
    await w.find('[data-test="collect-all"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([{ url: '/api/onboard/post-collect-batch', opts: { method: 'POST', body: { asset_serials: ['S1', 'S3'] } } }])
    expect(asked).toContain('對勾選的 2 台各收一輪全部')
    expect(asked).toContain('另有 1 台沒有資產序號')
  })
  it('確認按取消：什麼都不送', async () => {
    confirmAns = false
    const w = await mountIt()
    await w.findAll('.pick')[0].trigger('click')
    await w.find('[data-test="collect-all"]').trigger('click'); await flushPromises()
    expect(writes()).toEqual([])
  })
  it('表格重讀後不在表上的勾選丟掉，不會被送出', async () => {
    const w = await mountIt()
    await w.findAll('.pick')[0].trigger('click')
    await w.setProps({ rows: ROWS.slice(1) }); await flushPromises()
    expect(w.find('[data-test="collect-all"]').attributes('disabled')).toBeDefined()
    await w.findAll('.pick')[1].trigger('click')
    await w.find('[data-test="collect-all"]').trigger('click'); await flushPromises()
    expect(writes()[0].opts.body).toEqual({ asset_serials: ['S3'] })
  })
  it('隊伍在跑：每 3 秒問一次；離開頁面後不再問', async () => {
    vi.useFakeTimers()
    try {
      queue = { ...queue, running: true, total: 2, done: 1, current: 'S1' }
      const w = await mountIt()
      expect(w.find('[data-test="collect-all-progress"]').text()).toContain('web01')
      const n = calls.length
      await vi.advanceTimersByTimeAsync(3100)
      expect(calls.length).toBe(n + 1)
      w.unmount()
      await vi.advanceTimersByTimeAsync(10000)
      expect(calls.length).toBe(n + 1)
    } finally { vi.useRealTimers() }
  })
})

describe('6.1 已納管：改分類（每列一顆）', () => {
  it('用的是那一列；人工指定帶過去（可還原）', async () => {
    const w = await mountIt()
    await w.findAll('[data-test="managed-move"]')[0].trigger('click')
    const rows = JSON.parse(w.find('.move').attributes('data-rows')!)
    expect(rows).toEqual([{ ip: '10.99.0.1', asset_serial: 'S1', hostname: 'web01', message: '已納管主機，目前 OS 類型：Linux', tab: 'ssh',
      os_override: { by: 'amy', at: '2026-11-01', reason: null, os_type: 'Linux', tab: 'ssh' } }])
  })
  it('認不出是哪一台（沒序號、manage-state 也沒有主機名）：寫原因、沒有改分類鍵', async () => {
    const w = await mountIt()
    expect(w.findAll('[data-test="managed-move"]').length).toBe(2)
    expect(w.find('[data-test="managed-no-move"]').exists()).toBe(true)
  })
  it('manage-state 讀不到：寫原因', async () => {
    infoFails = true
    const w = await mountIt()
    expect(w.text()).toContain('讀不到 OS 類型與人工指定')
  })
  it('主機名不拿 V2 的 name（可能是資產名稱）', () => {
    expect(managedMoveRow({ ip: '10.99.0.9', serial: null }, null)).toMatchObject({ hostname: null, asset_serial: null, tab: '' })
  })
})

describe('6.1 已納管：篩選（網址 mos／mq／mtag，舊頁 managedShown 同一套）', () => {
  it('OS 籌碼、關鍵字、標籤都寫進網址；收集全部只送篩選後看得到的', async () => {
    const w = await mountIt()
    await w.find('[data-test="mos-AIX"]').trigger('click'); await flushPromises()
    expect(route.query.mos).toBe('AIX')
    expect(w.findAll('.row .ip').map(x => x.text())).toEqual(['10.99.0.3'])
    await w.find('[data-test="mos-all"]').trigger('click'); await flushPromises()
    await w.find('[data-test="mq"]').setValue('WEB0'); await flushPromises()
    expect(route.query.mq).toBe('WEB0')
    expect(w.findAll('.row .ip').map(x => x.text())).toEqual(['10.99.0.1'])
    await w.find('[data-test="mq"]').setValue(''); await flushPromises()
    await w.find('[data-test="mtag"]').trigger('click'); await flushPromises()
    expect(route.query.mtag).toBe('機房搬遷')
    expect(w.findAll('.row .ip').map(x => x.text())).toEqual(['10.99.0.1'])
  })
  it('網址帶條件打開就照條件篩', async () => {
    route.query = { tab: 'onboarded', mos: 'Linux' }
    const w = await mountIt()
    expect(w.findAll('.row .ip').map(x => x.text())).toEqual(['10.99.0.1'])
  })
  it('勾了之後被篩掉：勾選丟掉，放開篩選也不會回來（修剪）；送出只有看得到的', async () => {
    const w = await mountIt()
    for (const b of w.findAll('.pick')) await b.trigger('click')
    route.query = { ...route.query, mos: 'AIX' }; await flushPromises()
    expect(w.find('[data-test="collect-all"]').text()).toContain('已勾 1 台')
    route.query = { tab: 'onboarded' }; await flushPromises()
    expect(w.find('[data-test="collect-all"]').text()).toContain('已勾 1 台')
    await w.find('[data-test="collect-all"]').trigger('click'); await flushPromises()
    expect(writes()[0].opts.body).toEqual({ asset_serials: ['S3'] })
  })
})

describe('6.1 已納管：每列重新納管（⚠D1）', () => {
  it('Linux 那台開一鍵納管視窗（父頁 OnboardModal）；Windows 納管的開 Windows 視窗；不自己打 API', async () => {
    const w = await mountIt()
    const btns = w.findAll('[data-test="managed-reonboard"]')
    expect(btns.map(b => b.text())).toEqual(['重新納管', '重新納管', '重新納管（Windows）'])
    await btns[0].trigger('click'); await btns[2].trigger('click')
    expect(w.emitted('onboard')).toEqual([['10.99.0.1']])
    expect(w.emitted('win-onboard')).toEqual([[[{ ip: '10.99.0.3', hostname: 'db03', opted_in: true }]]])
    expect(writes()).toEqual([])
  })
  it('一般帳號看不到重新納管', async () => {
    admin = false
    const w = await mountIt()
    expect(w.findAll('[data-test="managed-reonboard"]').length).toBe(0)
  })
  it('讀不到 Windows 納管狀態：重新納管先不給按，原因寫出來', async () => {
    winFails = true
    const w = await mountIt()
    expect(w.findAll('[data-test="managed-reonboard"]').length).toBe(0)
    expect(w.find('[data-test="win-err"]').text()).toContain('讀不到 Windows 納管狀態')
  })
})

describe('6.1 已納管：每列狀態標記、取消納管（⚠D1）、編輯', () => {
  it('狀態標記只顯示：已納管／已納管但失聯、root 金鑰、Windows 本機管理員', async () => {
    const w = await mountIt()
    const pills = w.findAll('.pills').map(x => x.text())
    expect(pills[0]).toBe('✓ 已納管')
    expect(pills[2]).toContain('已納管但失聯')
    expect(pills[2]).toContain('root 金鑰（暫時）')
    expect(pills[2]).toContain('本機管理員：否')
    expect(writes()).toEqual([])
  })
  it('取消納管：Linux 那台交給父頁 RevokeModal、Windows 納管的交給 WinRevokeModal（帶主機名）；不自己打 API', async () => {
    const w = await mountIt()
    const b = w.findAll('[data-test="managed-revoke"]')
    expect(b.length).toBe(3)
    await b[0].trigger('click'); await b[2].trigger('click')
    const ev = w.emitted('revoke')!.map((e: any) => ({ ip: e[0].ip, win: e[0].win, name: e[0].name }))
    expect(ev).toEqual([{ ip: '10.99.0.1', win: false, name: 'web01' }, { ip: '10.99.0.3', win: true, name: 'db03' }])
    expect(writes()).toEqual([])
  })
  it('勾一台按「撤銷納管（單台）」：同一個分法', async () => {
    const w = await mountIt()
    await w.findAll('.pick')[2].trigger('click')
    await w.findAll('button').filter(x => x.text() === '撤銷納管（單台）')[0].trigger('click')
    expect((w.emitted('revoke')![0][0] as any).win).toBe(true)
  })
  it('一般帳號、或 Windows 狀態讀不到：沒有取消納管', async () => {
    admin = false
    let w = await mountIt()
    expect(w.findAll('[data-test="managed-revoke"]').length).toBe(0)
    w.unmount(); admin = true; winFails = true
    w = await mountIt()
    expect(w.findAll('[data-test="managed-revoke"]').length).toBe(0)
  })
  it('✎ 編輯：有序號的連到 V2 編輯表單；沒序號的不給', async () => {
    const w = await mountIt()
    expect(w.findAll('[data-test="managed-edit"]').map(x => x.attributes('href'))).toEqual(['/v2/asset/S1?edit=1', '/v2/asset/S3?edit=1'])
  })
})

describe('6.1 已納管：勾選批次（舊頁同一排）', () => {
  it('勾三台：Linux 那顆只送 Linux 的、Windows 那顆只送 Windows 納管的；被篩掉的不送', async () => {
    const w = await mountIt()
    for (const b of w.findAll('.pick')) await b.trigger('click')
    expect(w.find('[data-test="managed-batch-reonboard"]').text()).toContain('（2）')
    await w.find('[data-test="managed-batch-reonboard"]').trigger('click')
    await w.find('[data-test="managed-win-reonboard"]').trigger('click')
    expect(w.emitted('batch-onboard')).toEqual([[['10.99.0.1', '10.99.0.2']]])
    expect(w.emitted('win-onboard')).toEqual([[[{ ip: '10.99.0.3', hostname: 'db03', opted_in: true }]]])
    route.query = { tab: 'onboarded', mos: 'AIX' }; await flushPromises()
    expect(w.find('[data-test="managed-batch-reonboard"]').attributes('disabled')).toBeDefined()
    expect(writes()).toEqual([])
  })
  it('加標籤：共用視窗拿到看得到而且勾了的；done 只重讀標籤、視窗留著；關掉才清勾選', async () => {
    const w = await mountIt()
    const picks = w.findAll('.pick')
    await picks[0].trigger('click'); await picks[2].trigger('click')
    await w.find('[data-test="managed-tag"]').trigger('click')
    expect(JSON.parse(w.find('.tagm').attributes('data-rows')!)).toEqual([
      { ip: '10.99.0.1', asset_serial: 'S1', hostname: 'web01' }, { ip: '10.99.0.3', asset_serial: 'S3', hostname: 'db03' }])
    const n = calls.filter(c => c.url === '/api/asset-tags/by-serial').length
    await w.find('.tdone').trigger('click'); await flushPromises()
    expect(w.find('.tagm').exists()).toBe(true)
    expect(calls.filter(c => c.url === '/api/asset-tags/by-serial').length).toBe(n + 1)
    await w.find('.tclose').trigger('click'); await flushPromises()
    expect(w.find('.tagm').exists()).toBe(false)
    expect(w.find('[data-test="collect-all"]').text()).toContain('已勾 0 台')
  })
  it('完整 Excel：同一支、帶目前的 OS／關鍵字／標籤（舊頁參數名 os／kw／tag）', async () => {
    route.query = { tab: 'onboarded', mos: 'Linux', mq: ' web ', mtag: '機房搬遷' }
    const w = await mountIt()
    const href = w.find('[data-test="managed-excel"]').attributes('href')!
    expect(href.startsWith('/b/api/onboard/managed/export?')).toBe(true)
    expect(Object.fromEntries(new URL(href, 'http://x').searchParams)).toEqual({ kw: 'web', os: 'Linux', tag: '機房搬遷' })
    route.query = { tab: 'onboarded' }; await flushPromises()
    expect(w.find('[data-test="managed-excel"]').attributes('href')).toBe('/b/api/onboard/managed/export')
  })
  it('一般帳號：沒有批次重新納管、沒有完整 Excel（後端只給管理員）；加標籤照舊頁看得到', async () => {
    admin = false
    const w = await mountIt()
    expect(w.find('[data-test="managed-batch-reonboard"]').exists()).toBe(false)
    expect(w.find('[data-test="managed-excel"]').exists()).toBe(false)
    expect(w.find('[data-test="managed-tag"]').exists()).toBe(true)
  })
})

describe('6.1 已納管：匯出欄（舊 Excel 的欄）', () => {
  it('畫面：標籤、最後確認收得到、上次失敗；管理員匯出另外接上狀態、機房…六樣收集結果', async () => {
    const w = await mountIt()
    const labels = exportProps.cols.map((c: any) => c.label)
    for (const l of ['資產序號', 'OS（系統分類）', '標籤', '最後確認收得到', '上次失敗']) expect(labels).toContain(l)
    expect(exportProps.exportCols.map((c: any) => c.label)).toEqual(MANAGED_EXPORT_ONLY)
    const rows = await exportProps.exportAll()
    expect(rows.map((r: any) => r.ip)).toEqual(['10.99.0.1', '10.99.0.2', '10.99.0.3'])
    expect(rows[0]).toMatchObject({ x0: '已納管', x1: '板橋', x4: 'webit3scan', x5: '成功', last_fail: '', tags_text: '機房搬遷' })
    expect(rows[1]).toMatchObject({ x0: '已納管但失聯' })      // 沒序號的用 IP 對
    expect(rows[2]).toMatchObject({ x0: '', last_fail: '2026-11-08 10:00　登入被拒' })
    expect(rows[2].pills_text).toBe('已納管但失聯、root 金鑰（暫時）、本機管理員：否')     // 匯出的納管狀態欄＝畫面上的標記
    expect(rows[0].pills_text).toBe('✓ 已納管')                                              // 同畫面同一個對照（2.058.13 P2-1）
    expect(w.text()).toBeTruthy()
  })
  it('一般帳號：不接那幾欄、不打那支（同舊 Excel 只給管理員）', async () => {
    admin = false
    await mountIt()
    expect(exportProps.exportCols).toEqual([])
    expect(exportProps.exportAll).toBeNull()
  })
})

describe('6.1 已納管：2.058.13／2.058.13 審查修正', () => {
  it('Windows 狀態讀不到：撤銷納管（單台）不出現（不會把 Windows 納管的開成 Linux 撤銷）', async () => {
    let w = await mountIt()
    expect(w.find('[data-test="managed-revoke-one"]').exists()).toBe(true)
    w.unmount(); winFails = true
    w = await mountIt()
    expect(w.find('[data-test="managed-revoke-one"]').exists()).toBe(false)
    expect(w.findAll('button').filter(x => x.text() === '撤銷納管（單台）').length).toBe(0)
  })
  it('撤銷納管（單台）不會送出被篩掉的那一台', async () => {
    const w = await mountIt()
    await w.findAll('.pick')[0].trigger('click')
    route.query = { tab: 'onboarded', mos: 'AIX' }; await flushPromises()
    expect(w.find('[data-test="managed-revoke-one"]').attributes('disabled')).toBeDefined()
    await w.find('[data-test="managed-revoke-one"]').trigger('click')
    expect(w.emitted('revoke')).toBeUndefined()
    route.query = { tab: 'onboarded' }; await flushPromises()
    const picks = w.findAll('.pick')
    await picks[0].trigger('click'); await picks[2].trigger('click')
    route.query = { tab: 'onboarded', mos: 'AIX' }; await flushPromises()
    await w.find('[data-test="managed-revoke-one"]').trigger('click')
    expect((w.emitted('revoke') as any[]).map(e => e[0].ip)).toEqual(['10.99.0.3'])
  })
  it('項次固定：篩選後同一台還是同一個號碼', async () => {
    const w = await mountIt()
    expect(lastRows.map((r: any) => r.__no)).toEqual([1, 2, 3])
    route.query = { tab: 'onboarded', mos: 'AIX' }; await flushPromises()
    expect(lastRows.map((r: any) => [r.ip, r.__no])).toEqual([['10.99.0.3', 3]])
    expect(w.text()).toBeTruthy()
  })
  it('匯出照目前的篩選（不是全部）', async () => {
    route.query = { tab: 'onboarded', mos: 'AIX' }
    await mountIt()
    const rows = await exportProps.exportAll()
    expect(rows.map((r: any) => r.ip)).toEqual(['10.99.0.3'])
  })
  it('唯讀帳號看不到加標籤（端點要寫入權限）', async () => {
    admin = false; write = false
    const w = await mountIt()
    expect(w.find('[data-test="managed-tag"]').exists()).toBe(false)
  })
  it('✎ 編輯：序號有特殊字元時網址有編碼', async () => {
    const w = await mountIt([{ key: 'kx', name: 'x', ip: '10.99.0.9', serial: 'A/B #1?', type: 'Linux' }])
    expect(w.find('[data-test="managed-edit"]').attributes('href')).toBe(`/v2/asset/${encodeURIComponent('A/B #1?')}?edit=1`)
    expect(w.find('[data-test="managed-edit"]').attributes('href')).toBe('/v2/asset/A%2FB%20%231%3F?edit=1')
  })
})

describe('6.1 已納管：2.058.14 Windows 狀態讀取中', () => {
  it('還沒讀完：重新納管、取消納管、撤銷納管（單台）都不出現；讀完才出現', async () => {
    let release: () => void = () => {}
    winGate = new Promise<void>((r) => { release = r })
    const w = await mountIt()
    expect(w.find('[data-test="managed-revoke-one"]').exists()).toBe(false)
    expect(w.findAll('[data-test="managed-revoke"]').length).toBe(0)
    expect(w.findAll('[data-test="managed-reonboard"]').length).toBe(0)
    expect(w.find('[data-test="managed-batch-reonboard"]').exists()).toBe(false)
    release(); await flushPromises()
    expect(w.find('[data-test="managed-revoke-one"]').exists()).toBe(true)
    expect(w.findAll('[data-test="managed-revoke"]').length).toBe(3)
  })
})

describe('規則', () => {
  it('managedMatch：舊頁同一套（OS 空的算未填、關鍵字不分大小寫、標籤）', () => {
    expect(managedMatch({ ip: '10.99.0.1', os_type: null }, { os: '未填', kw: '', tag: '' })).toBe(true)
    expect(managedMatch({ ip: '10.99.0.1', hostname: 'Web01' }, { os: '', kw: 'WEB', tag: '' })).toBe(true)
    expect(managedMatch({ ip: '10.99.0.1', tags: ['a'] }, { os: '', kw: '', tag: 'b' })).toBe(false)
  })
  it('collectAllSerials／pruneKeys', () => {
    expect(collectAllSerials({ k1: true, k2: true }, ROWS)).toEqual(['S1'])
    expect(pruneKeys({ k1: true, kx: true, k3: false }, ROWS)).toEqual({ k1: true })
  })
  it('另有納管方式：V2 網址照用；舊網址換成 V2；沒有去處寫原因', () => {
    expect(guideHref({ page: '/v2/collect?tab=san' })).toBe('/v2/collect?tab=san')
    expect(guideHref({ page: '/san' })).toBe('/v2/collect?tab=san')
    expect(guideHref({ page: '/hmc' })).toBe('/v2/collect?tab=hmc')
    expect(guideHref({ page: null, note: '目前不收' })).toBeNull()
    expect(guideText({ page: '/v2/collect?tab=hmc', page_label: 'HMC 收集' })).toBe('走 HMC 收集')
    expect(guideText({ page: null, note: '暫時無打算' })).toBe('目前不收：暫時無打算')
    expect(guideText(null)).toBe('')
  })
  it('/adopt 已移植：legacy=1 也轉；#頁簽 對到 V2 同一頁籤', () => {
    expect(isPorted('/adopt')).toBe(true)
    expect(v2LegacyTarget('/adopt')).toEqual({ path: '/v2/onboard', query: {} })
    expect(v2LegacyTarget('/adopt', { legacy: '1' })).toEqual({ path: '/v2/onboard', query: {} })
    expect(v2LegacyTarget('/adopt', {}, '#onboarded')).toEqual({ path: '/v2/onboard', query: { tab: 'onboarded' } })
    expect(v2LegacyTarget('/adopt', {}, '#managed-hosts')).toEqual({ path: '/v2/onboard', query: { tab: 'onboarded' } })
    expect(v2LegacyTarget('/adopt', {}, '#failures')).toEqual({ path: '/v2/onboard', query: { tab: 'failed' } })
    expect(v2LegacyTarget('/adopt', {}, '#collect')).toEqual({ path: '/v2/onboard', query: { tab: 'collect' } })
    expect(v2LegacyTarget('/adopt', {}, '#pending')).toEqual({ path: '/v2/onboard', query: { tab: 'pending' } })
    expect(v2LegacyTarget('/adopt', {}, '#unregistered')).toEqual({ path: '/v2/hosts', query: { tab: 'unreg' } })
    expect(v2LegacyTarget('/adopt', {}, '#nope')).toEqual({ path: '/v2/onboard', query: {} })
    expect(adoptHashTarget('')).toBeNull()
  })
})
