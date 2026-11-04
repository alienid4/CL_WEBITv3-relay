// 2.1 主機查詢「批次查詢」「重複登記」（2.041.84）：純函式＋兩個元件實掛。
// 重點：部分命中明顯標示且不算「找到」、查無一定有列且進同一份表（匯出走同一份 rows）、磚＝列數＝下鑽、重複登記只讀（沒有刪除／釋放按鈕）。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路（IP 10.99.x）。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as hl from '../composables/hostLookup'
import Batch from '../components/v2/HostBatchLookup.vue'
import Dups from '../components/v2/HostDuplicates.vue'

enableAutoUnmount(afterEach)

const lrow = (no: number, term: string, status: hl.LookupStatus, o: Partial<hl.LookupRow> = {}): hl.LookupRow => ({
  no, term, input_count: 1, status, status_label: hl.LOOKUP_LABEL[status], matched_by: status === 'none' ? null : '主機名', partial: status === 'partial',
  count: status === 'none' ? 0 : 1, hint: null, key: status === 'one' ? `k${no}` : null, name: status === 'one' ? term : null, ip: status === 'one' ? '10.99.0.' + no : null,
  type: null, system: null, where: null, detect: null, records: null, retired: null, candidates: [], candidates_text: '', ...o })
const lpayload = (rows: hl.LookupRow[], mode: 'exact' | 'partial' = 'exact'): hl.LookupData => ({
  mode, mode_label: mode === 'exact' ? '精確比對' : '含部分比對', rows,
  summary: { raw_lines: rows.length + 1, blank: 1, unique: rows.length, duplicates_merged: 0, ...hl.lookupCounts(rows) },
  limits: { max_raw_lines: 2000, max_term_len: 128, max_total_chars: 60000, max_unique: 500 }, notes: [] })

describe('hostLookup 純函式', () => {
  it('parseTerms：換行／逗號／空白分隔，空白項略過', () => {
    expect(hl.parseTerms('a\r\nb, c;d\t e\n\n')).toEqual(['a', 'b', 'c', 'd', 'e'])
    expect(hl.parseTerms('   ')).toEqual([])
  })
  it('lookupCounts 與 lookupFilter 同一組：加總＝下鑽', () => {
    const rows = [lrow(1, 'a', 'one'), lrow(2, 'b', 'none'), lrow(3, 'c', 'partial'), lrow(4, 'd', 'many'), lrow(5, 'e', 'none')]
    const c = hl.lookupCounts(rows)
    expect(c).toEqual({ one: 1, many: 1, partial: 1, none: 2 })
    for (const s of ['one', 'many', 'partial', 'none'] as const) expect(hl.lookupFilter(rows, s).length).toBe(c[s])
    expect(hl.lookupFilter(rows, 'all').length).toBe(5)
  })
})

let current: any
let failWith: any = null
const calls: { url: string; opts: any }[] = []
const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p) {
    return () => h('table', { class: 'tbl-stub' }, [
      ...p.rows.map((r: any) => h('tr', { class: 'r' }, [h('td', `${p.prefix}${r.__no}`), ...p.cols.map((c: any) => h('td', { 'data-k': c.k }, `${c.text ? c.text(r) : r[c.k]}`))])),
      p.rows.length ? null : h('tr', h('td', { class: 'empty' }, p.empty)),
    ])
  },
})
let routes: Record<string, any> = {}            // 依網址回不同內容（釋放流程會同時打好幾支）
let routeFail: Record<string, any> = {}
const adminOn = ref(false)
const toasts: string[] = []
function setup() {
  for (const [k, v] of Object.entries({ ref, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o?: any) => {
    calls.push({ url: u, opts: o })
    if (routeFail[u]) return Promise.reject(routeFail[u])
    if (u in routes) return Promise.resolve(typeof routes[u] === 'function' ? routes[u](o) : routes[u])
    return failWith ? Promise.reject(failWith) : Promise.resolve(current)
  } }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: computed(() => adminOn.value) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => { toasts.push(m) } }))
}
const stubs = { V2Table: Table, InfoNote: true }
beforeEach(() => { calls.length = 0; failWith = null; routes = {}; routeFail = {}; adminOn.value = false; toasts.length = 0; setup() })
afterEach(() => { vi.unstubAllGlobals() })

describe('HostBatchLookup 元件', () => {
  const run = async (w: any, text: string) => {
    await w.find('textarea').setValue(text)
    await w.find('button.pri').trigger('click')
    await flushPromises()
  }
  it('送出清單與精確模式；查無的每一筆都有列，磚＝列數，點磚下鑽', async () => {
    current = lpayload([lrow(1, 'web01', 'one'), lrow(2, 'nope', 'none'), lrow(3, 'AP-2', 'many', { matched_by: 'APID', count: 2, candidates_text: 'web01（10.99.2.1）、web02（10.99.2.2）' }), lrow(4, 'gone', 'none')])
    const w = mount(Batch, { global: { stubs } })
    expect(w.find('button.pri').attributes('disabled')).toBeDefined()           // 沒輸入不能查
    await run(w, 'web01\nnope\nAP-2\ngone\n')
    expect(calls[0].url).toBe('/api/v2/hosts/batch-lookup')
    expect(calls[0].opts.method).toBe('POST')
    expect(calls[0].opts.body).toEqual({ terms: ['web01', 'nope', 'AP-2', 'gone'], partial: false })
    expect(w.findAll('tr.r').length).toBe(4)
    const none = w.findAll('tr.r').filter(r => r.text().includes('查無'))
    expect(none.length).toBe(2)
    expect(w.find('.tile.none b').text()).toBe('2')
    expect(w.find('.tile.one b').text()).toBe('1')
    expect(w.find('.tile.many b').text()).toBe('1')
    expect(w.find('.tile.partial').exists()).toBe(false)                         // 精確模式沒有部分命中磚
    await w.find('.tile.none').trigger('click')
    expect(w.findAll('tr.r').length).toBe(2)
    expect(w.findAll('tr.r').every(r => r.text().includes('查無'))).toBe(true)
    await w.find('.tile.none').trigger('click')
    expect(w.findAll('tr.r').length).toBe(4)
  })
  it('含部分比對：送 partial=true，部分命中標「部分命中」且獨立一顆磚，不混進找到', async () => {
    current = lpayload([lrow(1, 'demo-uni', 'partial', { matched_by: '部分比對', key: null, count: 1, candidates_text: 'demo-uni01（10.99.1.1）' }), lrow(2, 'web01', 'one')], 'partial')
    const w = mount(Batch, { global: { stubs } })
    await w.find('input[type=checkbox]').setValue(true)
    await run(w, 'demo-uni\nweb01')
    expect(calls[0].opts.body.partial).toBe(true)
    expect(w.find('.tile.partial b').text()).toBe('1')
    expect(w.find('.tile.one b').text()).toBe('1')
    const r0 = w.findAll('tr.r')[0].text()
    expect(r0).toContain('部分命中'); expect(r0).toContain('部分比對'); expect(r0).not.toContain('找到')
  })
  it('後端拒絕（超量）：顯示原因，不留舊結果', async () => {
    failWith = { data: { detail: '一次最多 2000 行（含空白與重複行），目前 2001 行' } }
    const w = mount(Batch, { global: { stubs } })
    await run(w, 'x')
    expect(w.text()).toContain('批次查詢失敗')
    expect(w.text()).toContain('2000')
    expect(w.find('table').exists()).toBe(false)
  })
  it('查詢中按鈕停用，不會連送兩次', async () => {
    vi.stubGlobal('useApi', () => ({ apiFetch: () => new Promise(() => {}) }))
    const w = mount(Batch, { global: { stubs } })
    await w.find('textarea').setValue('a')
    await w.find('button.pri').trigger('click')
    expect(w.find('button.pri').attributes('disabled')).toBeDefined()
    expect(w.find('button.pri').text()).toContain('查詢中')
  })
})

const dup = (no: number, kind: string, serials: string[]): hl.DupGroup => ({ no, key: `host${no}`, hostname: `host${no}`, ip: '10.99.5.' + no, count: serials.length, extra_rows: kind === 'suspect' ? serials.length - 1 : 0,
  kind, kind_label: ({ suspect: '疑似真重複', multi: '一機多系統', split: 'VIP／用途分列' } as any)[kind], identical: false, diff_fields: kind === 'multi' ? ['api_id'] : [], serials, systems: [], origin_text: `匯入或手動新增 ${serials.length} 筆` })
const dmem = (g: hl.DupGroup): hl.DupMember[] => g.serials.map(s => ({ group_no: g.no, group_key: g.key, kind: g.kind, kind_label: g.kind_label, serial: s, hostname: g.hostname, ip: g.ip,
  api_id: null, how: '匯入或手動新增', created_at: null, updated_at: null, from_import: '未取得', strong_ids: '無', diff: g.diff_fields.length ? { api_id: s } : {} }))
const dpayload = (): hl.DupData => {
  const gs = [dup(1, 'suspect', ['A1', 'A2']), dup(2, 'multi', ['B1', 'B2', 'B3']), dup(3, 'split', ['C1', 'C2'])]
  return { groups: gs, members: gs.flatMap(dmem), by_kind: { suspect: 1, multi: 1, split: 1 }, kind_labels: { suspect: '疑似真重複', multi: '一機多系統', split: 'VIP／用途分列' },
    kind_order: ['suspect', 'multi', 'split'], extra_rows: 1, dismissed_groups: 2 }
}

describe('HostDuplicates 元件', () => {
  it('每組一列＋每筆登記一列；來源路徑、不同欄位都在；磚＝組數；非管理員只讀（沒有刪除／釋放按鈕，也不打釋放紀錄）', async () => {
    current = dpayload()
    const w = mount(Dups, { global: { stubs } })
    await flushPromises()
    expect(calls[0].url).toBe('/api/v2/hosts/duplicates')
    const tables = w.findAll('table')
    expect(tables.length).toBe(2)
    expect(tables[0].findAll('tr.r').length).toBe(3)
    expect(tables[1].findAll('tr.r').length).toBe(7)                              // 2+3+2 筆；加總＝組內筆數
    expect(tables[0].text()).toContain('R1'); expect(tables[0].text()).toContain('匯入或手動新增 2 筆')
    expect(tables[0].text()).toContain('業務系統')                                 // diff_fields 的中文欄位名
    expect(w.text()).toContain('已標記不是重複 2 組')
    expect(w.findAll('button').map(b => b.text()).join('|')).not.toMatch(/釋放|刪除|合併|還原/)
    expect(w.text()).not.toContain('釋放紀錄')
    expect(calls.map(c => c.url)).toEqual(['/api/v2/hosts/duplicates'])         // 非管理員不打管理員專用的釋放紀錄端點
  })
  it('點類型磚：組與明細同步下鑽，再點收起', async () => {
    current = dpayload()
    const w = mount(Dups, { global: { stubs } })
    await flushPromises()
    const tiles = w.findAll('.tile')
    await tiles.find(t => t.text().includes('一機多系統'))!.trigger('click')
    const tables = w.findAll('table')
    expect(tables[0].findAll('tr.r').length).toBe(1)
    expect(tables[1].findAll('tr.r').length).toBe(3)
    await tiles.find(t => t.text().includes('一機多系統'))!.trigger('click')
    expect(w.findAll('table')[1].findAll('tr.r').length).toBe(7)
  })
  it('沒有重複：寫出空白狀態；讀取失敗：寫出原因', async () => {
    current = { ...dpayload(), groups: [], members: [], by_kind: { suspect: 0, multi: 0, split: 0 }, extra_rows: 0 }
    let w = mount(Dups, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('目前沒有重複登記的組')
    failWith = { data: { detail: 'boom' } }
    w = mount(Dups, { global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('重複登記讀取失敗')
    expect(w.text()).toContain('boom')
  })
})

// ===== 釋放序號流程（管理員；2.041.97）=====
// 這個替身表格支援勾選（selectable／rowKey／v-model:selected）與 cell-<欄> 具名插槽，其餘同上。
const SelTable = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty', 'selectable', 'rowKey', 'selected'],
  emits: ['update:selected'],
  setup(p, { slots, emit }) {
    return () => h('table', { class: 'tbl-stub' }, [
      ...p.rows.map((r0: any, i: number) => ({ ...r0, __no: typeof r0.__no === 'number' ? r0.__no : i + 1 })).map((r: any) => h('tr', { class: 'r' }, [
        p.selectable ? h('td', h('input', { type: 'checkbox', class: 'pick', 'data-k': r[p.rowKey],
          checked: !!p.selected?.[r[p.rowKey]], onChange: (e: any) => emit('update:selected', { ...(p.selected ?? {}), [r[p.rowKey]]: e.target.checked }) })) : null,
        h('td', `${p.prefix}${r.__no}`),
        ...p.cols.map((c: any) => h('td', { 'data-k': c.k }, slots['cell-' + c.k] ? slots['cell-' + c.k]!({ row: r }) : `${c.text ? c.text(r) : r[c.k]}`)),
      ])),
      p.rows.length ? null : h('tr', h('td', { class: 'empty' }, p.empty)),
    ])
  },
})
const rstubs = { V2Table: SelTable, InfoNote: true }
const pvPayload = (o: Partial<hl.ReleasePreview> = {}): hl.ReleasePreview => ({
  key: 'host1', hostname: 'host1', ip: '10.99.5.1', kind: 'suspect', kind_label: '疑似真重複', current: ['A1', 'A2'], keep: ['A1'],
  release: [{ serial: 'A2', hostname: 'host1', ip: '10.99.5.1', api_id: 'AP-1', asset_status: '使用中', from_cia: true, counts: { hardware: 1, software: 2, personnel: 1, host_package: 0 },
    total_rows: 4, kept_refs: { lifecycle_migration_log: 3 } }],
  totals: { serials: 1, rows: 4, by_table: { hardware: 1, software: 2, personnel: 1 } }, cia_serials: ['A2'],
  warnings: ['以下序號來自 CIA 清冊，釋放後下次匯入 CIA 會再出現，請同步修改 CIA 清冊：A2'], blockers: [], blocked: false, count_before: 2, count_after: 1,
  confirm_word: '釋放', fingerprint: 'fp123', ...o })
const rel = (o: Partial<hl.ReleaseRecord> = {}): hl.ReleaseRecord => ({ id: 7, merged_at: '2026-11-02 10:00:00', actor: 'boss', hostname: 'host1', ip: '10.99.5.1', serial: 'A2', kept_serials: 'A1',
  batch_id: 'b', from_cia: '是', state: '可還原', reason: null, restorable: true, restored_at: null, restored_by: null, rows: 4, counts: { hardware: 1, software: 2 }, ...o })

describe('HostDuplicates 釋放序號（管理員）', () => {
  const boot = async (list: hl.ReleaseRecord[] = []) => {
    adminOn.value = true
    routes['/api/v2/hosts/duplicates'] = dpayload()
    routes['/api/v2/hosts/duplicates/releases'] = { items: list, restorable: list.filter(r => r.restorable).length }
    const w = mount(Dups, { global: { stubs: rstubs } })
    await flushPromises()
    return w
  }
  const pickSerial = async (w: any, serial: string) => { const el = w.find(`input.pick[data-k="${serial}"]`); await el.setValue(true) }
  const previewBtn = (w: any) => w.findAll('button').find((b: any) => b.text().startsWith('預覽釋放'))

  it('管理員才看得到勾選、預覽按鈕與釋放紀錄；沒勾選時預覽按鈕停用', async () => {
    const w = await boot([rel()])
    expect(w.findAll('input.pick').length).toBe(7)
    expect(previewBtn(w).attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('釋放紀錄／還原')
    expect(calls.map(c => c.url)).toContain('/api/v2/hosts/duplicates/releases')
    // 非管理員：同一份資料不出現這些
    adminOn.value = false
    const w2 = mount(Dups, { global: { stubs: rstubs } })
    await flushPromises()
    expect(w2.findAll('input.pick').length).toBe(0)
    expect(previewBtn(w2)).toBeUndefined()
    expect(w2.text()).not.toContain('釋放紀錄／還原')
  })
  it('跨組勾選：預覽鈕停用並說明「一次只能釋放同一組」，不送請求', async () => {
    const w = await boot()
    await pickSerial(w, 'A2'); await pickSerial(w, 'B2')
    expect(previewBtn(w).attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('一次只能釋放同一組')
    expect(calls.filter(c => c.url.endsWith('release-preview')).length).toBe(0)
  })
  it('預覽 → 輸入確認文字才能執行 → 送出指紋與確認文字 → 重新載入並顯示結果', async () => {
    const w = await boot()
    routes['/api/v2/hosts/duplicates/release-preview'] = pvPayload()
    routes['/api/v2/hosts/duplicates/release'] = { ok: true, released: ['A2'], rows: 4 }
    await pickSerial(w, 'A2')
    await previewBtn(w).trigger('click'); await flushPromises()
    const pc = calls.find(c => c.url.endsWith('release-preview'))!
    expect(pc.opts.method).toBe('POST'); expect(pc.opts.body).toEqual({ key: 'host1', release_serials: ['A2'] })
    const t = w.text()
    expect(t).toContain('釋放後剩 1 筆'); expect(t).toContain('共刪除 4 筆'); expect(t).toContain('來自 CIA'); expect(t).toContain('下次匯入 CIA 會再出現')
    expect(t).toContain('軟體登記 2'); expect(t).toContain('人員 1'); expect(t).not.toContain('host_package')        // 0 筆不列；用正式名稱不用表名
    expect(t).toContain('保留不動的歷史紀錄')
    const run = w.findAll('button').find((b: any) => b.text() === '執行釋放')!
    expect(run.attributes('disabled')).toBeDefined()
    await w.find('input[aria-label="確認文字"]').setValue('不要')
    expect(w.findAll('button').find((b: any) => b.text() === '執行釋放')!.attributes('disabled')).toBeDefined()
    await w.find('input[aria-label="確認文字"]').setValue('釋放')
    expect(w.findAll('button').find((b: any) => b.text() === '執行釋放')!.attributes('disabled')).toBeUndefined()
    const n0 = calls.length
    await w.findAll('button').find((b: any) => b.text() === '執行釋放')!.trigger('click'); await flushPromises()
    const rc = calls.find(c => c.url === '/api/v2/hosts/duplicates/release')!
    expect(rc.opts.body).toEqual({ key: 'host1', release_serials: ['A2'], confirm: '釋放', fingerprint: 'fp123' })
    expect(calls.slice(n0).map(c => c.url)).toEqual(expect.arrayContaining(['/api/v2/hosts/duplicates', '/api/v2/hosts/duplicates/releases']))
    expect(w.text()).toContain('已釋放 A2'); expect(w.text()).toContain('可在下方「釋放紀錄」還原')
    expect(w.find('input[aria-label="確認文字"]').exists()).toBe(false)
  })
  it('預覽列出「目前不能釋放」的原因：即使輸入確認文字，執行鈕也停用', async () => {
    const w = await boot()
    routes['/api/v2/hosts/duplicates/release-preview'] = pvPayload({ blocked: true, blockers: ['序號 A2 在 host_api_key 有 1 筆密碼／金鑰類資料，必須先處理'] })
    await pickSerial(w, 'A2')
    await previewBtn(w).trigger('click'); await flushPromises()
    expect(w.text()).toContain('目前不能釋放'); expect(w.text()).toContain('host_api_key')
    await w.find('input[aria-label="確認文字"]').setValue('釋放')
    expect(w.findAll('button').find((b: any) => b.text() === '執行釋放')!.attributes('disabled')).toBeDefined()
  })
  it('輸入要釋放的序號也算確認', async () => {
    const w = await boot()
    routes['/api/v2/hosts/duplicates/release-preview'] = pvPayload()
    await pickSerial(w, 'A2')
    await previewBtn(w).trigger('click'); await flushPromises()
    await w.find('input[aria-label="確認文字"]').setValue('A2')
    expect(w.findAll('button').find((b: any) => b.text() === '執行釋放')!.attributes('disabled')).toBeUndefined()
  })
  it('預覽被後端拒絕（至少保留一筆）：顯示原因，不出現執行鈕', async () => {
    const w = await boot()
    routeFail['/api/v2/hosts/duplicates/release-preview'] = { data: { detail: '釋放後這組至少要保留一筆，不能全部釋放' } }
    await pickSerial(w, 'A1'); await pickSerial(w, 'A2')
    await previewBtn(w).trigger('click'); await flushPromises()
    expect(w.text()).toContain('預覽失敗'); expect(w.text()).toContain('至少要保留一筆')
    expect(w.findAll('button').some((b: any) => b.text() === '執行釋放')).toBe(false)
  })
  it('釋放失敗（預覽後資料變動）：講明資料沒有變動，不重新載入', async () => {
    const w = await boot()
    routes['/api/v2/hosts/duplicates/release-preview'] = pvPayload()
    routeFail['/api/v2/hosts/duplicates/release'] = { data: { detail: '預覽之後資料已有變動，請重新預覽再執行' } }
    await pickSerial(w, 'A2')
    await previewBtn(w).trigger('click'); await flushPromises()
    await w.find('input[aria-label="確認文字"]').setValue('釋放')
    const n0 = calls.length
    await w.findAll('button').find((b: any) => b.text() === '執行釋放')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('釋放失敗，資料沒有變動'); expect(w.text()).toContain('預覽之後資料已有變動')
    expect(calls.length - n0).toBe(1)
  })
  it('釋放紀錄：可還原列有還原鈕（二段確認）；不可還原／已還原的列沒有，並寫出原因', async () => {
    const w = await boot([rel(), rel({ id: 8, serial: 'Z9', state: '序號已占用', reason: '這個序號目前已被其他資產使用，需先處理才能還原', restorable: false }),
      rel({ id: 9, serial: 'Y8', state: '已還原', restorable: false, restored_at: '2026-11-02 11:00:00', restored_by: 'boss' })])
    const rows = w.findAll('tr.r').filter((r: any) => r.find('td[data-k="act"]').exists())
    expect(rows.length).toBe(3)
    expect(rows[0].text()).toContain('X1'); expect(rows[0].text()).toContain('資產主檔 1、軟體登記 2')
    expect(rows[1].text()).toContain('序號已占用：這個序號目前已被其他資產使用'); expect(rows[1].find('button').exists()).toBe(false)
    expect(rows[2].text()).toContain('已還原（boss，2026-11-02 11:00:00）'); expect(rows[2].find('button').exists()).toBe(false)
    routes['/api/v2/hosts/duplicates/restore'] = { ok: true, serial: 'A2', restored_rows: 4 }
    await rows[0].find('button').trigger('click')
    expect(calls.some(c => c.url.endsWith('/restore'))).toBe(false)                // 第一下只是要求確認
    const ok = w.findAll('button').find((b: any) => b.text().startsWith('確定還原'))!
    await ok.trigger('click'); await flushPromises()
    expect(calls.find(c => c.url.endsWith('/restore'))!.opts.body).toEqual({ audit_id: 7 })
    expect(w.text()).toContain('已還原 A2（4 筆）')
  })
  it('還原被拒絕：toast 講明資料沒有變動', async () => {
    const w = await boot([rel()])
    routeFail['/api/v2/hosts/duplicates/restore'] = { data: { detail: '序號 A2 已被現有資產使用，無法還原' } }
    await w.findAll('tr.r').find((r: any) => r.find('td[data-k="act"]').exists())!.find('button').trigger('click')
    await w.findAll('button').find((b: any) => b.text().startsWith('確定還原'))!.trigger('click'); await flushPromises()
    expect(toasts.some(t => t.includes('還原失敗，資料沒有變動') && t.includes('已被現有資產使用'))).toBe(true)
  })
})

describe('hostLookup 釋放用純函式', () => {
  it('selectedRelease：只回勾選的序號與其所屬組；跨組會有多個 key', () => {
    const ms = [...dmem(dup(1, 'suspect', ['A1', 'A2'])), ...dmem(dup(2, 'multi', ['B1', 'B2']))]
    expect(hl.selectedRelease(ms, { A2: true })).toEqual({ keys: ['host1'], serials: ['A2'] })
    expect(hl.selectedRelease(ms, { A2: true, B1: true, A1: false }).keys).toEqual(['host1', 'host2'])
    expect(hl.selectedRelease(ms, {})).toEqual({ keys: [], serials: [] })
  })
  it('confirmOk：只認「釋放」或要釋放的序號（去空白）', () => {
    expect(hl.confirmOk('釋放', '釋放', ['A2'])).toBe(true)
    expect(hl.confirmOk(' A2 ', '釋放', ['A2'])).toBe(true)
    expect(hl.confirmOk('A1', '釋放', ['A2'])).toBe(false)
    expect(hl.confirmOk('', '釋放', ['A2'])).toBe(false)
  })
  it('countsText：0 筆不列、表名換成正式名稱', () => {
    expect(hl.countsText({ hardware: 1, software: 2, host_package: 0, 'source_record.resolved_hardware_id（接回用）': 3 })).toBe('資產主檔 1、軟體登記 2、來源紀錄（接回用） 3')
  })
})

describe('hosts.vue 掛上兩個附加頁籤', () => {
  const src = readFileSync(resolve(process.cwd(), 'pages/v2/hosts.vue'), 'utf-8')
  it('頁籤有批次查詢與重複登記，且附加頁籤不送給 /api/v2/hosts（一律用 all）', () => {
    expect(src).toMatch(/\{ k: 'batch', label: '批次查詢' \}/)
    expect(src).toMatch(/\{ k: 'dups', label: '重複登記' \}/)
    expect(src).toMatch(/tab: extraTab\.value \? 'all' : tab\.value/)
    expect(src).toMatch(/<V2HostBatchLookup v-if="extraTab === 'batch'" \/>/)
    expect(src).toMatch(/<V2HostDuplicates v-else-if="extraTab === 'dups'" \/>/)
  })
})
