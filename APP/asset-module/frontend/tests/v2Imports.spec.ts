// 2.059.1（V36）：⚙ 資料匯入整頁改 V2 原生。釘住：每種來源打的是舊頁同一支 API、同樣的確認流程（D4 純搬移）。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ciaVerdict, dataDate, fileDataAt, IMPORT_SOURCES, lastText, rvVerdict } from '../utils/importV2'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let guarded: { url: string; opts: any }[] = []
let stale = 0
let fmReply: any = null
const route = reactive({ query: {} as Record<string, any> })
const Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'unit', 'max', 'empty', 'noExport'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl', 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, (p.cols ?? []).map((c: any) => {
    if (slots[`cell-${c.k}`]) return slots[`cell-${c.k}`]!({ row: r })
    const t = String(c.text ? c.text(r) : r[c.k]); const l = c.link ? c.link(r) : null
    return l ? h('a', { href: l, class: `c-${c.k}` }, t) : h('span', { class: `c-${c.k}` }, t)
  }))))
} })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { href: p.to }, slots.default?.()) } })
const stubs = { V2Table: Table, NuxtLink: Link, InfoNote: { template: '<i />' }, V2CiaPreview: { props: ['preview'], emits: ['confirm', 'cancel'], template: '<button class="cfm" @click="$emit(\'confirm\')">確認寫入</button>' },
  VcenterAutoImport: { template: '<div />' }, AccountAuditImport: { template: '<div />' }, CertImportPanel: { props: ['file', 'isAdmin'], template: '<div class="cip" />' } }

beforeEach(() => {
  calls = []; guarded = []; stale = 0; fmReply = null; route.query = {}
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('confirm', () => true)
  vi.stubGlobal('httpReason', async () => 'x')
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: async (url: string, opts: any) => { guarded.push({ url, opts }); return { total: 2, new: 1, changed: 1, removed: 0 } } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/import/sources') return { sources: [{ key: 'rvtools', row_count: 12, has_original: true, original_name: 'a.xlsx', last_imported_at: '2026-11-01 10:00:00', data_date: '2026-10-31' }, { key: 'cia_excel', row_count: 30, has_original: false }] }
    if (url === '/api/segments') return { segments: [1, 2, 3] }
    if (url === '/api/import/rvtools') {
      if (stale > 0 && !opts.body.get('force')) { stale--; throw Object.assign(new Error('409'), { data: { detail: { code: 'stale_import', incoming_data_at: 'a', current_data_at: 'b' } } }) }
      return { total_vms: 5, inserted: 1, updated: 4, pending_review: 0, errors: [] }
    }
    if (url === '/api/import/excel/preview') return { token: 'T', file_sha256: 'H', tables: {} }
    if (url === '/api/import/excel/confirm') return { sheets: { 硬體: { inserted: 1, updated: 0, skipped: 0 } }, errors: [] }
    if (url === '/api/import/field-mapping') return opts.method === 'PUT' ? { 硬體: opts.body.mapping.硬體 } : (fmReply ?? { 硬體: { 主機名稱: 'hostname' } })
    if (url === '/api/segments/import') return { source_rows: 3, imported: 2, added_count: 0, removed_count: 1, unchanged_count: 2, removed: ['10.99.9.0/24'], warnings: [] }
    if (url === '/api/v2/alert-contacts/import') return { total: 2, new: 1, changed: 1, unchanged: 0, removed: 0, error_count: 0, errors: [], missing_columns: [], blockers: [], can_confirm: true, plan_digest: 'D1' }
    if (url === '/api/import/log' || url === '/api/merge-reviews/auto-matchable') return url === '/api/import/log' ? [] : null
    return null
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const file = (name: string) => new File(['x'], name, { lastModified: Date.UTC(2026, 10, 1) })
async function pickFile(w: any, f: File | File[], sel = 'input[type=file]') {
  const inp = w.find(sel)
  Object.defineProperty(inp.element, 'files', { value: Array.isArray(f) ? f : [f], configurable: true })
  await inp.trigger('change'); await flushPromises()
}

describe('純函式（舊頁原樣抽出）', () => {
  it('資料日期、上次匯入文字、CIA／RVTools 判定', () => {
    expect(dataDate('X-RVTools_export_all_2026-08-28_13.49.57.xlsx')).toBe('2026-08-28')
    expect(lastText('2026-11-01 10:00:59', '2026-10-31')).toBe('2026-11-01 10:00（資料 2026-10-31）')
    expect(lastText(null, null)).toBe('—')
    expect(ciaVerdict({ sheets: { 硬體: { inserted: 0, updated: 0, skipped: 3 } }, errors: ['分頁名稱不對'] }).tone).toBe('error')
    expect(ciaVerdict({ sheets: { 硬體: { inserted: 2, updated: 0, skipped: 1 } }, errors: [] }).verdict).toBe('部分匯入：成功 2 筆，略過 1 筆')
    expect(rvVerdict({ total_vms: 0, inserted: 0, updated: 0, pending_review: 0, errors: [] }).tone).toBe('error')
    expect(fileDataAt({ lastModified: new Date(2026, 0, 2, 3, 4, 5).getTime() })).toBe('2026-01-02 03:04:05')
    expect(IMPORT_SOURCES.map(s => s.key)).toContain('cia_excel')
  })
})

describe('總覽', () => {
  it('一列一個來源，項次固定；筆數與上次匯入用舊頁同一支 API；點來源進 ?src=；單據等連到各自上傳的地方', async () => {
    const C = (await import('../components/v2/Imports.vue')).default
    const w = mount(C, { global: { stubs: { ...stubs, V2ImportCia: true, V2ImportRv: true, V2ImportOne: true } } }); await flushPromises()
    const rows = w.findAll('.tbl .row')
    expect(rows).toHaveLength(IMPORT_SOURCES.length)
    const rv = rows[1]
    expect(rv.find('.c-count').text()).toBe('12')
    expect(rv.find('.c-last').text()).toBe('2026-11-01 10:00（資料 2026-10-31）')
    expect(rv.find('a.c-label').attributes('href')).toBe('/v2/settings?st=import&src=rvtools')
    expect(rows[6].find('.c-count').text()).toBe('3')                                          // 網段＝/api/segments 筆數
    expect(rows[12].find('a.c-go').attributes('href')).toBe('/v2/settings?st=documents')
    for (const a of w.findAll('a')) expect(a.attributes('href')).toMatch(/^\/v2\//)
  })
})

describe('RVTools', () => {
  it('逐檔送、資料時間＝檔案修改時間；較舊的問一次，同意後這批剩下的也帶 force', async () => {
    stale = 2
    const C = (await import('../components/v2/ImportRv.vue')).default
    const w = mount(C, { props: { apiBase: '' }, global: { stubs: { ...stubs, V2ImportExports: true } } }); await flushPromises()
    const confirmSpy = vi.fn(() => true); vi.stubGlobal('confirm', confirmSpy)
    await pickFile(w, [file('a.xlsx'), file('b.xlsx')])
    await w.find('button.btn').trigger('click'); await flushPromises()
    const sent = calls.filter(c => c.url === '/api/import/rvtools')
    expect(sent.map(c => [c.opts.body.get('file').name, c.opts.body.get('force')])).toEqual([['a.xlsx', null], ['a.xlsx', 'true'], ['b.xlsx', null], ['b.xlsx', 'true']])
    expect(sent[0].opts.body.get('data_at')).toMatch(/^2026-11-0[12] /)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
  })
})

describe('CIA 資產清冊', () => {
  it('先預覽（不寫）→ 確認只送預覽那一份的 token＋檔案雜湊', async () => {
    const C = (await import('../components/v2/ImportCia.vue')).default
    const w = mount(C, { props: { apiBase: '' }, global: { stubs: { ...stubs, V2ImportExports: true } } }); await flushPromises()
    await pickFile(w, file('cia.xlsx'))
    await w.find('button.btn').trigger('click'); await flushPromises()
    expect(calls.map(c => c.url)).toContain('/api/import/excel/preview')
    expect(calls.map(c => c.url)).not.toContain('/api/import/excel/confirm')
    await w.find('.cfm').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/import/excel/confirm')!.opts.body).toEqual({ token: 'T', file_sha256: 'H' })
    expect(calls.map(c => c.url)).not.toContain('/api/import/excel')
    expect(w.text()).toContain('匯入成功：共 1 筆')
  })
  it('欄位對應：停用中的列（_disabled）照樣列出、可改回「匯入」並送回原本的欄位（Codex 1193）', async () => {
    fmReply = { 硬體: { 主機名稱: 'hostname' }, _disabled: { 硬體: { IP: 'ip' } } }
    const C = (await import('../components/v2/ImportCia.vue')).default
    const w = mount(C, { props: { apiBase: '' }, global: { stubs: { ...stubs, V2ImportExports: true } } }); await flushPromises()
    await w.find('button.lnk').trigger('click'); await flushPromises()
    const sel = w.find('select[aria-label="IP 是否匯入"]')
    expect(sel.exists()).toBe(true)
    expect((sel.element as HTMLSelectElement).value).toBe('不匯入')
    await sel.setValue('匯入'); await flushPromises()
    await w.findAll('button.btn.ghost').find(b => b.text().includes('儲存欄位對應'))!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/import/field-mapping' && c.opts.method === 'PUT')!
    expect(put.opts.body.mapping.硬體).toEqual({ 主機名稱: 'hostname', IP: 'ip' })
    expect(put.opts.body.mapping._disabled).toBeUndefined()
  })
})

describe('其他來源', () => {
  it('網段：整批取代，消失的段當場列出來', async () => {
    const C = (await import('../components/v2/ImportOne.vue')).default
    const w = mount(C, { props: { kind: 'segments', apiBase: '', count: 3 }, global: { stubs: { ...stubs, V2ImportExports: true } } })
    await pickFile(w, file('seg.xlsx'))
    await w.find('button.btn').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/segments/import')).toBeTruthy()
    expect(w.text()).toContain('消失 1')
    expect(w.text()).toContain('10.99.9.0/24')
  })
  it('告警聯絡：選檔＝預覽（confirm=false），確認走 guardedFetch 並帶 plan_digest', async () => {
    const C = (await import('../components/v2/ImportOne.vue')).default
    const w = mount(C, { props: { kind: 'alert_contact', apiBase: '', acInfo: { can_edit: true } }, global: { stubs: { ...stubs, V2ImportExports: true } } })
    await pickFile(w, file('ac.xlsx'))
    const pv = calls.find(c => c.url === '/api/v2/alert-contacts/import')!
    expect(pv.opts.body.get('confirm')).toBe('false')
    expect(guarded).toHaveLength(0)
    await w.find('button.btn').trigger('click'); await flushPromises()
    expect(guarded[0].opts.body.get('confirm')).toBe('true')
    expect(guarded[0].opts.body.get('plan_digest')).toBe('D1')
  })
  it('憑證：非管理員看不到選檔', async () => {
    const C = (await import('../components/v2/ImportOne.vue')).default
    const w = mount(C, { props: { kind: 'certs', apiBase: '', certSummary: { can_see_contacts: false } }, global: { stubs: { ...stubs, V2ImportExports: true } } })
    expect(w.find('input[type=file]').exists()).toBe(false)
    expect(w.text()).toContain('僅管理員可匯入')
  })
})
