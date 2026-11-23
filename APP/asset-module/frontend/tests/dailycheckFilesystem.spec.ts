// 1.5 每日檢查：Unity 檔案系統（收集腳本 v3）的明細列——跟 Pool 同一組趨勢欄位、同一個 V2Table。
// 欄位顯示、查不到＝「—」（不是 0、不是正常）、沒收到那一列是「未收集」、可排序、匯出文字跟畫面一樣。全假資料（10.99.x）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sortRows, useSort } from '../composables/useSort'
import V2Table from '../components/v2/Table.vue'
import SortTh from '../components/SortTh.vue'
import { hasTrend, storageDetailCols } from '../utils/dailycheckTrend'

enableAutoUnmount(afterEach)
let out: { h: string[]; b: any[][] } | null = null
beforeEach(() => {
  out = null
  for (const [k, v] of Object.entries({ ref, computed, watch, useSort, sortRows })) vi.stubGlobal(k, v)
  vi.stubGlobal('useTableExport', () => ({ exportTable: (_n: string, h: string[], b: any[][]) => { out = { h, b } }, busy: ref(false) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useV2ExportName', () => () => 'x')
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false) }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, NuxtLink: { template: '<a><slot /></a>' } }

const none = { pct: null, prev: null, prev_day: null, delta: null, growth: null, samples: 1, days_to_first: null, days_to_full: null, tier: 4, trend_note: '查不到使用率' }
const detail: any[] = [
  { kind: 'Pool', trend: true, name: 'Pool Pool-Gold', state: '正常', pct: 40, prev: null, delta: null, growth: null, samples: 1, days_to_first: null, days_to_full: null, tier: 3, trend_note: '待累積', what: 'p' },
  { kind: '檔案系統', trend: true, name: '檔案系統 share-hot', state: '第一級', pct: 95, prev: 94, delta: 1, growth: 1, samples: 3, days_to_first: 0, days_to_full: 5, tier: 1, trend_note: '已在第一級，約 5 天滿載', what: 'f1' },
  { kind: '檔案系統', trend: true, name: '檔案系統 FS-2', state: '需處理', pct: 80, prev: 79, delta: 1, growth: 1, samples: 3, days_to_first: 10, days_to_full: 20, tier: 2, trend_note: '約 10 天到第一級、20 天滿載', what: 'f2' },
  { kind: '檔案系統', trend: true, name: '檔案系統（沒收到）', state: '未收集', ...none, what: '檔案系統沒收到，不判正常' },
].map((r, i) => ({ ...r, __no: i + 1 }))

const mk = () => mount(V2Table, { props: { rows: detail, cols: storageDetailCols(), prefix: 'S', rowKey: '__no' }, global: { stubs, components: { SortTh } } })
const col = (w: any, label: string) => w.findAll('th').findIndex((t: any) => t.text().includes(label))
const cells = (w: any, label: string) => { const i = col(w, label); return w.findAll('tbody tr').map((r: any) => r.findAll('td')[i].text()) }

describe('Storage 明細：檔案系統列', () => {
  it('有趨勢列（trend＝true）就顯示趨勢欄位；檔案系統列跟 Pool 一樣算有趨勢', () => {
    expect(hasTrend([{ kind: '檔案系統', trend: true }])).toBe(true)
    expect(hasTrend([{ kind: '檔案系統' }])).toBe(false)
  })
  it('容量等級與趨勢欄位；沒收到的那一列全部是「—」且等級是未收集', () => {
    const w = mk()
    expect(cells(w, '容量等級')).toEqual(['正常', '第一級', '需處理', '未收集'])
    expect(cells(w, '使用率')).toEqual(['40%', '95%', '80%', '—'])
    expect(cells(w, '較前次')).toEqual(['—', '+1.0 點', '+1.0 點', '—'])
    expect(cells(w, '到第一級')).toEqual(['—', '已在第一級', '10 天', '—'])
    expect(cells(w, '到滿載')).toEqual(['—', '5 天', '20 天', '—'])
  })
  it('點「使用率」標題排序，再點反向', async () => {
    const w = mk()
    const th = w.findAll('th')[col(w, '使用率')]
    await th.trigger('click')
    expect(cells(w, '名稱')).toEqual(['Pool Pool-Gold', '檔案系統 FS-2', '檔案系統 share-hot', '檔案系統（沒收到）'])      // 查不到使用率的排最後
    await th.trigger('click')
    expect(cells(w, '名稱')[0]).toBe('檔案系統 share-hot')
  })
  it('匯出＝畫面上的文字（含項次）', async () => {
    const w = mk()
    await w.find('.xbtn').trigger('click'); await flushPromises()
    expect(out!.h.slice(0, 4)).toEqual(['項次', '名稱', '容量等級', '使用率'])
    expect(out!.b[1].slice(0, 4)).toEqual(['S2', '檔案系統 share-hot', '第一級', '95%'])
    expect(out!.b[3].slice(0, 4)).toEqual(['S4', '檔案系統（沒收到）', '未收集', '—'])
  })
})
