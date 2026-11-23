// 1.2 掃描異常「異常消失」掃描品質提示（2.069.2）：讀不到≠完整、null≠完整、門檻文案取自後端、部分網段與整批分開、系統自動關閉≠人工處理。
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, reactive, defineAsyncComponent, defineComponent, h, onMounted, ref, Suspense, watch } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import Changes from '../pages/v2/changes.vue'
import { goneGroup, goneHint, issueState, qualityNotice } from '../composables/scanQuality'

const Q = (o: any = {}) => ({ scan_time: '2026-08-06T01:00', incomplete: false, global_incomplete: false, mode: 'ok', bad_segments: [], reason: '', baseline_events: [], confirm: 5, drop_pct: 20, rule_version: 'v', autoclose_apply: false, invalid_settings: [], ...o })

it('讀不到不等於完整：未載入成功＝unknown，null（沒有掃描紀錄）與完整是不同狀態', () => {
  expect(qualityNotice({ loaded: false, error: 'HTTP 500', data: null }).kind).toBe('unknown')
  expect(qualityNotice({ loaded: false, error: 'HTTP 500', data: null }).text).toContain('不可確認')
  expect(qualityNotice({ loaded: true, error: '', data: null }).kind).toBe('none')
  expect(qualityNotice({ loaded: true, error: '', data: Q() as any }).kind).toBe('ok')
})

it('整批未採用與部分網段未採用文案分開，並給完整網段清單（不截斷）', () => {
  const segs = Array.from({ length: 9 }, (_, i) => `10.99.${i}.0/24`)
  const all = qualityNotice({ loaded: true, error: '', data: Q({ incomplete: true, global_incomplete: true, mode: 'all', bad_segments: segs }) as any })
  expect(all.kind).toBe('all'); expect(all.text).toContain('整批未採用')
  const part = qualityNotice({ loaded: true, error: '', data: Q({ incomplete: true, mode: 'segments', bad_segments: segs }) as any })
  expect(part.kind).toBe('segments'); expect(part.text).toContain('9 個網段未採用，其餘網段照常判定'); expect(part.segments).toHaveLength(9)
})

it('門檻文案取自後端有效設定，不寫死 5', () => {
  expect(goneHint(3)).toContain('連續 3 次'); expect(goneHint(7)).toContain('連續 7 次'); expect(goneHint(7)).not.toContain('連續 5 次')
})

it('處理狀態：系統自動關閉與人工處理分開', () => {
  expect(issueState({ is_read: 0 })).toBe('未處理')
  expect(issueState({ is_read: 1, handled_at: '2026-08-06 08:00:00' })).toContain('已處理')
  const s = issueState({ is_read: 1, auto_closed: 1, close_reason: '已重新掃到' })
  expect(s).toContain('系統自動關閉'); expect(s).toContain('已重新掃到'); expect(s).not.toContain('已處理')
})

const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })

async function page(qualityResponse: 'throw' | any, o: { admin?: boolean; issues?: any[]; preview?: any } = {}) {
  for (const [name, value] of Object.entries({ computed, defineAsyncComponent, onMounted, ref, watch })) vi.stubGlobal(name, value)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(!!o.admin) }))
  const route = reactive<{ query: Record<string, any> }>({ query: { tab: 'issues' } })
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: vi.fn() }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  const write = vi.fn(async () => ({ applied: 1 }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: write }))
  const read = vi.fn(async (path: string) => {
    if (path === '/api/changes') return { dates: [], categories: [], date: null, prev_date: null, items: {}, counts: {} }
    if (path === '/api/issues') return o.issues ?? []
    if (path === '/api/issues/scan-quality') { if (qualityResponse === 'throw') throw new Error('HTTP 500'); return qualityResponse }
    if (path === '/api/issues/auto-close/preview') return o.preview ?? { count: 0, items: [] }
    throw new Error('Unexpected mock path ' + path)
  })
  vi.stubGlobal('useApi', () => ({ apiFetch: read }))
  const table = defineComponent({ props: ['rows', 'cols', 'empty'], emits: ['update:selected'], setup(p, { slots }) {
    return () => h('section', { class: 'fixture-rows' }, p.rows?.length ? p.rows.map((r: any) => h('div', { class: 'fixture-row' }, [r.name, '|', r.state, '|', r.note, slots['cell-act']?.({ row: r })])) : p.empty)
  } })
  const pg = defineComponent({ setup(_, { slots }) { return () => h('main', [slots.conclusion?.(), slots.tab?.()]) } })
  const root = defineComponent({ setup() { return () => h(Suspense, null, { default: () => h(Changes) }) } })
  const w = mount(root, { global: { components: { V2Page: pg, V2Table: table }, stubs: { InfoNote: { template: '<i class="note-i"><slot /></i>' }, NuxtLink: true } } })
  wrappers.push(w)
  await flushPromises()
  return { w, read, write }
}

it('頁面：品質 API 失敗要明講不可確認並可重試；成功後提示消失', async () => {
  const { w, read } = await page('throw')
  expect(w.find('[data-testid="q-unknown"]').text()).toContain('掃描品質讀不到，本次消失數字不可確認')
  expect(w.find('[data-testid="q-unknown"]').text()).toContain('重試')
  expect(read).toHaveBeenCalledWith('/api/issues/scan-quality')
})

it('頁面：回 null（沒有掃描紀錄）不顯示「不可確認」也不顯示不完整', async () => {
  const { w } = await page(null)
  expect(w.find('[data-testid="q-unknown"]').exists()).toBe(false)
  expect(w.find('[data-testid="q-incomplete"]').exists()).toBe(false)
})

it('頁面：部分網段失敗＝顯示 N 個網段未採用並列出全部；採新基準事件也顯示', async () => {
  const { w } = await page(Q({ incomplete: true, mode: 'segments', bad_segments: ['10.99.1.0/24', '10.99.2.0/24'], baseline_events: ['2026-08-06 已採新基準：網段 X 由 10 台降為 5 台（連續 5 批縮減）'] }))
  const t = w.find('[data-testid="q-incomplete"]').text()
  expect(t).toContain('2 個網段未採用，其餘網段照常判定'); expect(t).toContain('10.99.1.0/24'); expect(t).toContain('10.99.2.0/24')
  expect(w.find('[data-testid="q-baseline"]').text()).toContain('已採新基準')
})

it('頁面：「異常消失」說明用後端 confirm（7），不寫死 5；系統自動關閉列顯示原因且有篩選', async () => {
  const issues = [
    { id: 1, detected_at: '2026-08-06 01:00:00', hostname: 'h1', ip: '10.99.0.1', issue_type: '異常消失', is_read: 0, handled_at: null, note: '最近 7 次完整掃描未見（首次未見：2026-07-30）', miss_count: 7 },
    { id: 2, detected_at: '2026-08-06 01:00:00', hostname: 'h2', ip: '10.99.0.2', issue_type: '異常消失', is_read: 1, handled_at: '2026-08-06 02:00:00', auto_closed: 1, close_reason: '已重新掃到' },
  ]
  const { w } = await page(Q({ confirm: 7 }), { issues })
  expect(w.text()).toContain('系統自動關閉 1')
  expect(w.findAll('.note-i').map(n => n.text()).join('|')).not.toContain('連續 5 次完整掃描')
  await w.findAll('button.chip').find(b => b.text().startsWith('系統自動關閉'))!.trigger('click')
  expect(w.text()).toContain('系統自動關閉：已重新掃到')
})

it('頁面：只有管理員看得到預計自動關閉清單與套用按鈕', async () => {
  const preview = { count: 2, items: [{ id: 1, hostname: 'a', ip: '10.99.0.1', reason: 'r1' }, { id: 2, hostname: 'b', ip: '10.99.0.2', reason: 'r2' }] }
  const adm = await page(Q(), { admin: true, preview })
  expect(adm.w.find('[data-testid="preview-box"]').text()).toContain('系統預計自動關閉 2 筆（尚未套用）')
  const usr = await page(Q(), { admin: false, preview })
  expect(usr.w.find('[data-testid="preview-box"]').exists()).toBe(false)
})

it('分組：miss_count 或門檻任一未知（NULL／API 還沒回來）＝歷史次數未知，不當成 0／首次', () => {
  expect(goneGroup(null, 5)).toBe('unknown')
  expect(goneGroup(undefined, 5)).toBe('unknown')
  expect(goneGroup(6, null)).toBe('unknown')                 // API 還沒回來不能先用固定 N 切
  expect(goneGroup(6, 5)).toBe('repeat')
  expect(goneGroup(3, 5)).toBe('below')
  expect(goneGroup(0, 5)).toBe('below')                      // 0 是真的 0，不是未知
})

it('最新一批判定未完成：品質停在舊批時要明講，不可顯示成完整', () => {
  const n = qualityNotice({ loaded: true, error: '', data: Q({ mode: 'pending', judgement_pending: true, latest_scan_time: '2026-08-07T01:00' }) as any })
  expect(n.kind).toBe('pending'); expect(n.text).toContain('最新一批判定未完成'); expect(n.text).toContain('不可確認')
})

it('頁面：品質停在舊批（最新一批判定未完成）顯示紅字；套用時帶回預覽雜湊', async () => {
  const preview = { count: 1, preview_hash: 'abc123', items: [{ id: 1, hostname: 'a', ip: '10.99.0.1', reason: 'r1' }] }
  const { w, write } = await page(Q({ mode: 'pending', judgement_pending: true, latest_scan_time: '2026-08-07T01:00' }), { admin: true, preview })
  expect(w.find('[data-testid="q-pending"]').text()).toContain('最新一批判定未完成')
  Object.defineProperty(window, 'confirm', { configurable: true, value: vi.fn(() => true) })
  await w.findAll('button').find(b => b.text().startsWith('套用'))!.trigger('click')
  await flushPromises()
  expect(write).toHaveBeenCalledWith('/api/issues/auto-close/apply', { method: 'POST', body: { preview_hash: 'abc123' } })
})

it('頁面：異常消失列 miss_count 為 NULL 顯示「歷史次數未知」', async () => {
  const issues = [{ id: 1, detected_at: '2026-08-06 01:00:00', hostname: 'h1', ip: '10.99.0.1', issue_type: '異常消失', is_read: 0, handled_at: null, note: '', miss_count: null }]
  const { w } = await page(Q(), { issues })
  expect(w.text()).toContain('歷史次數未知')
})
