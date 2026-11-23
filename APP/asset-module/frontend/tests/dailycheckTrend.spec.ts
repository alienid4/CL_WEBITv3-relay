// 1.5 每日檢查：Pool／逐顆硬碟的趨勢欄位（使用率、前次、較前次、每日成長、樣本數、到第一級、到滿載）。
// 純函式＋V2Table 實掛：欄位顯示、點標題排序、匯出文字跟畫面一樣、查不到顯示「—」（不是 0、不是正常）、容量優先的排序。全假資料（10.99.x）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sortRows, useSort } from '../composables/useSort'
import V2Table from '../components/v2/Table.vue'
import SortTh from '../components/SortTh.vue'
import { diskDetailCols, flattenDisks, fmtDaysFirst, fmtDaysFull, fmtDelta, fmtGrowth, fmtPct, hasTrend, isDiskPanel, storageDetailCols, trendCols } from '../utils/dailycheckTrend'

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

const dk = (item: string, pct: number | null, extra: any = {}) => ({ item, state: '第一級', pct, prev: null, delta: null, growth: null, samples: 1, days_to_first: null, days_to_full: null, tier: 3, trend_note: '待累積', ...extra })
const detail = [
  { name: 'app01', ip: '10.99.1.1', serial: 'H-1', device_id: 'd1', disks: [dk('C:', 99, { prev: 99.5, delta: -0.5, tier: 1, days_to_first: 0, trend_note: '下降中，仍≥90%（7 天最高 99.5%）' }), dk('D:', 80, { state: '需處理' })] },
  { name: 'app02', ip: '10.99.1.2', serial: 'H-2', device_id: 'd2', disks: [dk('/', 80, { state: '需處理', prev: 79, delta: 1, growth: 1, samples: 3, tier: 2, days_to_first: 10, days_to_full: 20, trend_note: '約 10 天到第一級、20 天滿載' })] },
  { name: 'app03', ip: '10.99.1.3', device_id: 'd3', disks: [dk('/', 91, { prev: 90, delta: 1, growth: 1, samples: 3, tier: 1, days_to_first: 0, days_to_full: 9, trend_note: '已在第一級，約 9 天滿載' })] },
  { name: 'app04', ip: '10.99.1.4', device_id: 'd4' },
]

describe('格式：查不到＝—，不是 0', () => {
  it('空值不顯示成 0%／+0.0 點', () => {
    for (const v of [null, undefined, NaN, 'x']) { expect(fmtPct(v)).toBe('—'); expect(fmtDelta(v)).toBe('—'); expect(fmtGrowth(v)).toBe('—'); expect(fmtDaysFirst(v)).toBe('—'); expect(fmtDaysFull(v)).toBe('—') }
    expect(fmtPct(0)).toBe('0%')                                   // 真的是 0 才顯示 0
  })
  it('正負號、單位、0 天的意思', () => {
    expect(fmtDelta(2)).toBe('+2.0 點'); expect(fmtDelta(-2)).toBe('-2.0 點'); expect(fmtDelta(0)).toBe('0.0 點')
    expect(fmtGrowth(0.67)).toBe('+0.67 點／天'); expect(fmtGrowth(-1)).toBe('-1.00 點／天'); expect(fmtPct(93)).toBe('93%')
    expect(fmtDaysFirst(0)).toBe('已在第一級'); expect(fmtDaysFirst(3)).toBe('3 天'); expect(fmtDaysFull(0)).toBe('已滿'); expect(fmtDaysFull(8)).toBe('8 天')
  })
})

describe('flattenDisks', () => {
  it('容量優先：已在第一級的在前（99% 下降中也在 80% 預估 10 天之前），其次到第一級天數，再到滿載天數，最後使用率', () => {
    const rows = flattenDisks(detail)
    expect(rows.map(r => `${r.name} ${r.item}`)).toEqual(['app03 /', 'app01 C:', 'app02 /', 'app01 D:'])      // 91%（9 天滿載）→ 99%（下降中，無天數）→ 80%（10 天到第一級）→ 80%（無估計）
    expect(rows.map(r => r.__no)).toEqual([1, 2, 3, 4])           // 項次固定
    expect(flattenDisks(undefined)).toEqual([]); expect(flattenDisks([{ name: 'x' }])).toEqual([])
  })
  it('帶出主機、IP 讓畫面可下鑽', () => {
    expect(flattenDisks(detail)[0]).toMatchObject({ name: 'app03', ip: '10.99.1.3', item: '/' })
  })
})

describe('hasTrend／isDiskPanel／欄位', () => {
  it('只有 Pool 列（trend=true）才算有趨勢', () => {
    expect(hasTrend([{ kind: 'Pool', trend: true }])).toBe(true)
    expect(hasTrend([{ kind: 'ESXi' }, null as any])).toBe(false); expect(hasTrend(undefined)).toBe(false)
  })
  it('硬碟面板：規則是 disk 且設備帶 disks（可以是空陣列）就顯示；舊規則（沒有 disks 欄）不顯示', () => {
    expect(isDiskPanel('disk', [{ name: 'a', disks: [] }])).toBe(true)
    expect(isDiskPanel('disk', [{ name: 'a' }])).toBe(false); expect(isDiskPanel('perf_cpu', [{ disks: [] }])).toBe(false); expect(isDiskPanel('disk', undefined)).toBe(false)
  })
  it('欄位順序固定，且用「容量等級」不用「狀態」', () => {
    expect(trendCols().map(c => c.label)).toEqual(['使用率', '前次', '較前次', '每日成長', '樣本數', '到第一級', '到滿載', '趨勢說明'])
    expect(storageDetailCols().map(c => c.label)).toEqual(['名稱', '容量等級', '使用率', '前次', '較前次', '每日成長', '樣本數', '到第一級', '到滿載', '趨勢說明', '內容', '採集時間'])
    expect(diskDetailCols().map(c => c.label)).toEqual(['主機', 'IP', '磁碟', '容量等級', '使用率', '前次', '較前次', '每日成長', '樣本數', '到第一級', '到滿載', '趨勢說明'])
  })
  it('容量等級顏色：第一級紅、需處理黃、正常綠、已知例外與未收集灰', () => {
    const t = diskDetailCols().find(c => c.k === 'state')!.tone!
    expect(['第一級', '需處理', '正常', '已知例外', '未收集', '?'].map(x => t({ state: x }))).toEqual(['test', 'dr', 'prod', 'none', 'none', ''])
  })
})

describe('V2Table 實掛：逐顆硬碟表', () => {
  const mk = () => mount(V2Table, { props: { rows: flattenDisks(detail), cols: diskDetailCols(), prefix: 'K', rowKey: '__no' }, global: { stubs, components: { SortTh } } })
  const col = (w: any, label: string) => w.findAll('th').findIndex((t: any) => t.text().includes(label))
  const cells = (w: any, label: string) => { const i = col(w, label); return w.findAll('tbody tr').map((r: any) => r.findAll('td')[i].text()) }

  it('顯示前次、較前次、每日成長、樣本數、兩個天數；查不到的格子是「—」', () => {
    const w = mk()
    expect(cells(w, '使用率')).toEqual(['91%', '99%', '80%', '80%'])
    expect(cells(w, '較前次')).toEqual(['+1.0 點', '-0.5 點', '+1.0 點', '—'])
    expect(cells(w, '每日成長')).toEqual(['+1.00 點／天', '—', '+1.00 點／天', '—'])
    expect(cells(w, '樣本數')).toEqual(['3 天', '1 天', '3 天', '1 天'])
    expect(cells(w, '到第一級')).toEqual(['已在第一級', '已在第一級', '10 天', '—'])
    expect(cells(w, '到滿載')).toEqual(['9 天', '—', '20 天', '—'])
  })
  it('點「使用率」標題排序，再點反向', async () => {
    const w = mk()
    const th = w.findAll('th')[col(w, '使用率')]
    await th.trigger('click')
    expect(cells(w, '使用率')).toEqual(['80%', '80%', '91%', '99%'])
    await th.trigger('click')
    expect(cells(w, '使用率')).toEqual(['99%', '91%', '80%', '80%'])
  })
  it('匯出＝畫面上看到的文字（含項次）', async () => {
    const w = mk()
    await w.find('.xbtn').trigger('click'); await flushPromises()
    expect(out!.h).toEqual(['項次', '主機', 'IP', '磁碟', '容量等級', '使用率', '前次', '較前次', '每日成長', '樣本數', '到第一級', '到滿載', '趨勢說明'])
    expect(out!.b[0]).toEqual(['K1', 'app03', '10.99.1.3', '/', '第一級', '91%', '90%', '+1.0 點', '+1.00 點／天', '3 天', '已在第一級', '9 天', '已在第一級，約 9 天滿載'])
    expect(out!.b[3][7]).toBe('—')
  })
})
