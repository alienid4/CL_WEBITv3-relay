// 2.059.0（V66 收尾）：10.2 分布統計「基礎設施／AP 系統」「OS×環境納管」「入口（VIP）」改 V2 原生。
// 釘住：用舊頁同一支 API、格子連結帶的條件＝點下去那一支 API 的條件（加總＝下鑽）、改分級送同一支 PUT。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let writer = true
const route = reactive({ query: {} as Record<string, any> })
const SYS = {
  envs: ['測試', '正式'], group_order: ['網路設備', '未分群'], class_order: ['第一類', '第二類', '未分級'],
  systems: [
    { api_id: 'I-001', name: '交換器', ap_department: '網路', kind: 'infra', group: '網路設備', sys_class: '第一類', sys_class_core: true, sys_class_override: null, sys_class_computed: '第一類', total: 5, rows: 6, by_env: { 正式: 3, 測試: 2 } },
    { api_id: 'I-002', name: null, ap_department: null, kind: 'infra', group: '未分群', sys_class: '未分級', sys_class_core: false, sys_class_override: '第二類', sys_class_computed: '未分級', total: 1, rows: 1, by_env: { 測試: 1 } },
  ],
  system_count: 2, mapped: 6, mapped_rows: 7, total_assets: 9, unmapped: 3,
  partition: { managed: 7, retired: 1, offbook: 2 }, unmapped_active_cia: 1, mapping: null, kind_overrides: {},
}
const DRILL = { api_id: 'I-001', name: '交換器', total: 5, rows: 6, role_basis: '服務',
  locations: [{ location: '機房甲', total: 4, by_env: { 正式: 3, 測試: 1 } }, { location: '機房乙', total: 1, by_env: { 測試: 1 } }],
  role_matrix: [{ role: '網通', by_location: { 機房甲: 4, 機房乙: 1 }, total: 5 }, { role: 'DB', by_location: {}, total: 0 }] }
const MATRIX = { rows: ['Linux', 'AIX'], cols: [{ key: '正式', label: '正式' }, { key: '測試', label: '測試' }],
  cells: { Linux: { 正式: { total: 10, onboarded: 6, collected: 1, needs_action: 3, excluded: 0, coverage: 70, by_stage: {} } }, AIX: {} },
  row_totals: { Linux: { total: 10, onboarded: 6, collected: 1, needs_action: 3, coverage: 70 }, AIX: { total: 0, onboarded: 0, collected: 0, needs_action: 0, coverage: null } },
  col_totals: { 正式: { total: 10, onboarded: 6, collected: 1, needs_action: 3, coverage: 70 }, 測試: { total: 0, onboarded: 0, collected: 0, needs_action: 0, coverage: null } },
  grand: { total: 10, onboarded: 6, collected: 1, needs_action: 3, coverage: 70 }, scan_time: null, duplicates_merged: 0 }
const VIPS = { entries: [{ vip: '192.0.2.28', kind: 'vip', kind_label: 'VIP', hosts: 3, systems: 2, rows: 4, raw_values: ['192.0.2.28'] },
  { vip: '192.0.2.40', kind: 'cluster', kind_label: '疑似叢集 IP', hosts: 2, systems: 1, rows: 2, raw_values: ['192.0.2.40 cluster'] }],
  others: [{ value: 'N-999', kind: 'apid', kind_label: '像 AP ID', hosts: 1, rows: 1 }], empty_rows: 5, total_rows: 20, basis: '未經 F5 驗證' }

// 表格替身：每列每欄畫出文字，有 link 的畫成 <a href>；有 cell-<k> 插槽就用插槽
const Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'unit', 'max', 'empty'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl', 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, (p.cols ?? []).map((c: any) => {
    if (slots[`cell-${c.k}`]) return slots[`cell-${c.k}`]!({ row: r })
    const t = String(c.text ? c.text(r) : r[c.k])
    const l = c.link ? c.link(r) : null
    return l ? h('a', { href: l, class: `c-${c.k}` }, t) : h('span', { class: `c-${c.k}` }, t)
  }))))
} })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { href: typeof p.to === 'string' ? p.to : `${p.to.path}?${new URLSearchParams(p.to.query)}` }, slots.default?.()) } })
const stubs = { V2Table: Table, NuxtLink: Link, InfoNote: { template: '<i />' } }

beforeEach(() => {
  calls = []; writer = true; route.query = {}
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ canWrite: ref(writer), isAdmin: ref(writer), isViewer: ref(false) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (x: any) => { route.query = x.query } }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/stats/systems') return JSON.parse(JSON.stringify(SYS))
    if (url === '/api/stats/systems/I-001') return DRILL
    if (url === '/api/stats/systems/I-001/assets') return { items: [{ asset_serial: 'S1', hostname: 'sw01', ip: '10.99.0.1', os: null, environment: '正式', location: '機房甲', role: '網通', onboarded: true, purpose: null, device_model: 'X', row_count: 2 }] }
    if (url === '/api/systems/class') return { ok: true }
    if (url === '/api/stats/onboard-matrix') return MATRIX
    if (url === '/api/stats/vip-entries') return VIPS
    if (url === '/api/stats/vip-entries/detail') return { value: opts.query.value, rows: 4, hosts: 1, basis: 'b', systems: [{ api_id: null, name: null, hosts: 1, rows: 1 }], machines: [{ hostname: null, ip: '10.99.0.5', asset_serial: 'S9', serials: ['S9'], row_count: 1, environment: '正式', location: '機房甲', systems_text: '', purposes_text: '' }] }
    throw new Error(`沒預期的呼叫 ${url}`)
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const hrefs = (w: any, cls: string) => w.findAll(`a.${cls}`).map((a: any) => a.attributes('href'))

describe('基礎設施／AP 系統', () => {
  async function mk(kind = 'infra') {
    const C = (await import('../components/v2/DistSystems.vue')).default
    const w = mount(C, { props: { kind }, global: { stubs } }); await flushPromises(); return w
  }
  it('一律全載（limit=0）、分群頁籤只列第一群、格子連到下鑽並帶從哪個環境點進來', async () => {
    const w = await mk()
    expect(calls[0]).toEqual({ url: '/api/stats/systems', opts: { query: { limit: 0, kind: 'infra' } } })
    expect(w.findAll('.gtab').map(x => x.text())).toEqual(['網路設備5', '未分群1'])
    expect(w.findAll('.tbl .row')).toHaveLength(1)
    expect(hrefs(w, 'c-e0')).toEqual(['/v2/dist?tab=infra&g=%E7%B6%B2%E8%B7%AF%E8%A8%AD%E5%82%99&sys=I-001&fe=%E6%AD%A3%E5%BC%8F'])
    expect(hrefs(w, 'c-total')[0]).toContain('sys=I-001')
    expect(hrefs(w, 'c-api_id')).toEqual(['/v2/system/I-001'])
    // 結論列的連結全是 V2 網址（經轉址表），不連舊頁
    for (const a of w.findAll('a')) expect(a.attributes('href')).toMatch(/^\/v2\//)
  })
  it('有寫入權：按「改」→ 選 → 「確認」才送 PUT /api/systems/class 並重載；手動改過的可回到分級表判定（送空）', async () => {
    route.query = { tab: 'infra', g: '未分群' }
    const w = await mk()
    expect(w.find('select.sel').exists()).toBe(false)                     // 還沒按「改」不出現下拉
    await w.find('[data-test="cls-edit"]').trigger('click')
    const sel = w.find('select.sel')
    expect(sel.findAll('option').map(o => o.text())).toContain('回到分級表判定（未分級）')
    await sel.setValue('')
    await flushPromises()
    expect(calls.some(c => c.opts?.method === 'PUT')).toBe(false)          // 只改下拉不送
    await w.find('[data-test="cls-ok"]').trigger('click')
    await flushPromises()
    const put = calls.find(c => c.opts?.method === 'PUT')!
    expect(put).toEqual({ url: '/api/systems/class', opts: { method: 'PUT', body: { api_id: 'I-002', sys_class: '' } } })
    expect(calls.filter(c => c.url === '/api/stats/systems')).toHaveLength(2)
  })
  it('按「改」再「取消」：不送任何東西、下拉收起', async () => {
    const w = await mk()
    await w.find('[data-test="cls-edit"]').trigger('click')
    await w.find('select.sel').setValue('第二類')
    await w.find('[data-test="cls-cancel"]').trigger('click')
    await flushPromises()
    expect(calls.some(c => c.opts?.method === 'PUT')).toBe(false)
    expect(w.find('select.sel').exists()).toBe(false)
  })
  it('不是管理員（2.060.5 改分級限管理員）：看不到下拉，只看到文字', async () => {
    writer = false
    const w = await mk()
    expect(w.find('select').exists()).toBe(false)
    expect(w.find('.c-cls_text').text()).toBe('第一類（核心）')
  })
  it('下鑽：機房×環境格子連到同一支 assets（location＋env），角色列 0 台不畫；點下去的列數＝清單', async () => {
    route.query = { tab: 'infra', sys: 'I-001', fe: '正式', cl: '機房甲', ce: '正式' }
    const w = await mk()
    expect(calls.map(c => c.url)).toContain('/api/stats/systems/I-001')
    const a = calls.find(c => c.url === '/api/stats/systems/I-001/assets')!
    expect(a.opts.query).toEqual({ location: '機房甲', env: '正式' })
    expect(w.text()).toContain('從「正式」點進來')
    const tbls = w.findAll('.tbl')
    expect(tbls.map(t => t.attributes('data-prefix'))).toEqual(['L', 'H', 'R'])
    expect(tbls[2].findAll('.row')).toHaveLength(1)          // DB 0 台不畫
    expect(tbls[1].findAll('.row')).toHaveLength(1)
    expect(hrefs(w, 'c-name')).toEqual(['/v2/machine/S1'])
    // 機房甲×正式格子＝3，連結帶的就是剛剛查的條件
    expect(hrefs(w, 'c-e0')[0]).toContain('cl=%E6%A9%9F%E6%88%BF%E7%94%B2&ce=%E6%AD%A3%E5%BC%8F')
  })
})

describe('OS×環境納管', () => {
  it('格子＝還要處理／總台數・納管率，點下去到 6.1 漏斗同條件；空格是「—」不能點', async () => {
    const C = (await import('../components/v2/DistOnboard.vue')).default
    const w = mount(C, { global: { stubs } }); await flushPromises()
    expect(w.find('a.c-c0').text()).toBe('3／10・70%')
    expect(w.find('a.c-c0').attributes('href')).toBe('/v2/onboard?tab=funnel&os_type=Linux&env_group=%E6%AD%A3%E5%BC%8F')
    expect(w.findAll('a.c-c1')).toHaveLength(0)
    route.query = { tab: 'onboard', om: 'onboarded' }; await flushPromises()
    expect(w.find('a.c-c0').text()).toBe('7／10・70%')          // 已納管＝onboarded＋collected（跟舊頁同口徑）
  })
})

describe('入口（VIP）', () => {
  it('篩判斷、數字點下去看明細（同一支 detail）；沒填 AP ID 的系統不連', async () => {
    route.query = { tab: 'vip', vk: 'cluster' }
    const C = (await import('../components/v2/DistVip.vue')).default
    const w = mount(C, { global: { stubs } }); await flushPromises()
    expect(w.findAll('.tbl[data-prefix="V"] .row')).toHaveLength(1)
    expect(hrefs(w, 'c-hosts')[0]).toBe('/v2/dist?tab=vip&vk=cluster&vip=192.0.2.40')
    route.query = { tab: 'vip', vip: 'N-999' }; await flushPromises()
    expect(calls.find(c => c.url === '/api/stats/vip-entries/detail')!.opts.query).toEqual({ value: 'N-999' })
    expect(w.findAll('a.c-api_text')).toHaveLength(0)
    expect(hrefs(w, 'c-name')).toEqual(['/v2/machine/S9'])
  })
})
