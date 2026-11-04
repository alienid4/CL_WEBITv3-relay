// 10.3 容量分析「封存引用」（2.042.06）：純函式＋元件實掛。重點：磚＝列數＝下鑽、管理員才有新增／更正／作廢、
// 更正送 revise（不是改舊版）、作廢要原因、版本鏈列出所有版本、非管理員唯讀。API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as cc from '../composables/capacityCitations'
import Cite from '../components/v2/CapacityCitations.vue'
import * as cv from '../composables/capacityView'

enableAutoUnmount(afterEach)

const row = (id: number, root: number, version: number, status: cc.CiteStatus, o: Partial<cc.CiteRow> = {}): cc.CiteRow => ({
  id, root_id: root, chain: `C${root}`, version, supersedes_id: version > 1 ? id - 1 : null, title: `報告${root}`, purpose: '簽呈', note: '',
  refs: [`archive:${id + 10}`], resolved: [{ ref: `archive:${id + 10}`, rows: 1, archive_ids: [id + 10], label: `DEMO-H 2026-09-10 v1` }], ref_rows: 1,
  created_by: 'admin', created_at: '2026-11-03T10:00:00', status, status_label: { active: '有效', superseded: '已被更正取代', voided: '已作廢' }[status],
  is_latest: status !== 'superseded', voided_at: status === 'voided' ? '2026-11-03T11:00:00' : null, voided_by: status === 'voided' ? 'admin' : null,
  void_reason: status === 'voided' ? '撤回' : null, ...o })
const payload = (): cc.CiteData => ({ rows: [row(2, 1, 2, 'active'), row(1, 1, 1, 'superseded'), row(3, 3, 1, 'voided')], counts: { active: 1, superseded: 1, voided: 1 }, chains: 2 })

describe('capacityCitations 純函式', () => {
  it('磚的數字＝同一份清單篩出來的列數（加總＝下鑽）', () => {
    const rs = payload().rows
    const c = cc.citeCounts(rs)
    expect(c).toEqual({ all: 3, active: 1, superseded: 1, voided: 1 })
    for (const s of ['active', 'superseded', 'voided'] as const) expect(cc.citeFilter(rs, s).length).toBe(c[s])
    expect(c.active + c.superseded + c.voided).toBe(c.all)
  })
  it('parseRefsInput：一行一筆、去重、去空白', () => {
    expect(cc.parseRefsInput(' archive:1\n\n2026-09-10|H , archive:1;2026-09-11 ')).toEqual(['archive:1', '2026-09-10|H', '2026-09-11'])
  })
  it('citeRefsText 寫出每個引用對到幾筆', () => {
    expect(cc.citeRefsText(row(1, 1, 1, 'active'))).toBe('archive:11（1 筆：DEMO-H 2026-09-10 v1）')
  })
  it('中位數與超標分鐘：沒有值就顯示原因，不以平均或 P95 代替', () => {
    expect(cv.capMedianText({ median: 42 })).toBe('42')
    expect(cv.capMedianText({ median: null, median_note: '不可得：來源是彙整桶' })).toBe('不可得')
    expect(cv.capMedianText({ median: null, median_note: '不知道：覆蓋不足', avg: 50, p95: 70 })).toBe('不知道')
    expect(cv.capOverText({ over_text: '至少 2 分、至多 4 分（2 分沒有資料）' })).toContain('至少 2 分')
    expect(cv.capOverText({})).toBe('不知道')
  })
})

let current: any
const calls: { url: string; opts: any }[] = []
const writes: { url: string; opts: any }[] = []
const admin = ref(true)
const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p, { slots }) {
    return () => h('table', { class: 'tbl' }, [
      ...p.rows.map((r: any) => h('tr', { class: 'r' }, [h('td', `${p.prefix}${r.__no}`),
        ...p.cols.map((c: any) => h('td', { 'data-k': c.k }, slots['cell-' + c.k] ? slots['cell-' + c.k]!({ row: r }) : `${c.text ? c.text(r) : r[c.k] ?? ''}`))])),
      p.rows.length ? null : h('tr', h('td', { class: 'empty' }, p.empty)),
    ])
  },
})
beforeEach(() => {
  calls.length = 0; writes.length = 0; current = payload()
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => { calls.push({ url: u, opts: o }); return Promise.resolve(current) } }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: (u: string, o: any) => { writes.push({ url: u, opts: o }); return Promise.resolve({ ...row(4, 1, 3, 'active') }) } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
})
afterEach(() => vi.unstubAllGlobals())
const mountIt = (isAdmin = true) => mount(Cite, { props: { isAdmin }, global: { stubs: { V2Table: Table, InfoNote: true } } })

describe('CapacityCitations 元件', () => {
  it('列出全部版本（含舊版與作廢）；點磚下鑽', async () => {
    const w = mountIt()
    await flushPromises()
    expect(calls[0].url).toBe('/api/v2/capacity/archive-citations')
    const tbl = () => w.findAll('table')[0]
    expect(tbl().findAll('tr.r').length).toBe(3)
    await w.findAll('.chip').find(b => b.text().includes('已作廢'))!.trigger('click')
    expect(tbl().findAll('tr.r').length).toBe(1)
    expect(tbl().text()).toContain('撤回（admin，2026-11-03T11:00:00）')
  })
  it('管理員：只有有效版有更正／作廢；更正送 revise、作廢要原因', async () => {
    const w = mountIt()
    await flushPromises()
    const rows = w.findAll('table')[0].findAll('tr.r')
    const btns = (i: number) => rows[i].find('td[data-k="act"]').findAll('button').map(b => b.text())
    expect(btns(0)).toEqual(['版本鏈', '更正', '作廢'])        // 有效
    expect(btns(1)).toEqual(['版本鏈'])                        // 已被更正取代
    expect(btns(2)).toEqual(['版本鏈'])                        // 已作廢
    await rows[0].findAll('button').find(b => b.text() === '更正')!.trigger('click')
    const form = w.find('[data-cite-form]')
    expect(form.text()).toContain('更正引用 #2（會新增下一版，舊版保留）')
    await w.find('[data-cite-save]').trigger('click'); await flushPromises()
    expect(writes[0].url).toBe('/api/v2/capacity/archive-citations/2/revise')
    expect(writes[0].opts.body.refs).toEqual(['archive:12'])
    await w.findAll('table')[0].findAll('tr.r')[0].findAll('button').find(b => b.text() === '作廢')!.trigger('click')
    await w.find('[data-cite-void]').findAll('button').find(b => b.text() === '確認作廢')!.trigger('click')
    expect(writes.length).toBe(1)                                  // 沒寫原因不送
    await w.find('[data-cite-void] input').setValue('簽呈撤回')
    await w.find('[data-cite-void]').findAll('button').find(b => b.text() === '確認作廢')!.trigger('click'); await flushPromises()
    expect(writes[1].url).toBe('/api/v2/capacity/archive-citations/2/void')
    expect(writes[1].opts.body).toEqual({ reason: '簽呈撤回' })
  })
  it('新增：送 create，引用一行一筆', async () => {
    const w = mountIt()
    await flushPromises()
    await w.find('[data-cite-new]').trigger('click')
    const ins = w.find('[data-cite-form]').findAll('input')
    await ins[0].setValue('2026Q4 容量簽呈')
    await w.find('[data-cite-form] textarea').setValue('archive:5\n2026-09-10|DEMO-H')
    await w.find('[data-cite-save]').trigger('click'); await flushPromises()
    expect(writes[0].url).toBe('/api/v2/capacity/archive-citations')
    expect(writes[0].opts.body).toMatchObject({ title: '2026Q4 容量簽呈', refs: ['archive:5', '2026-09-10|DEMO-H'] })
  })
  it('版本鏈：列出同一條鏈的所有版本', async () => {
    const w = mountIt()
    await flushPromises()
    await w.findAll('table')[0].findAll('tr.r')[1].findAll('button').find(b => b.text() === '版本鏈')!.trigger('click')
    const chain = w.find('[data-cite-chain]')
    expect(chain.text()).toContain('版本鏈 C1（2 版，新到舊）')
    expect(chain.findAll('tr.r').length).toBe(2)
  })
  it('非管理員：看得到全部版本，沒有新增／更正／作廢', async () => {
    const w = mountIt(false)
    await flushPromises()
    expect(w.find('[data-cite-new]').exists()).toBe(false)
    expect(w.find('[data-cite-readonly]').exists()).toBe(true)
    expect(w.findAll('button').map(b => b.text())).not.toContain('更正')
    expect(w.findAll('button').map(b => b.text())).not.toContain('作廢')
    expect(w.findAll('table')[0].findAll('tr.r').length).toBe(3)
  })
})
