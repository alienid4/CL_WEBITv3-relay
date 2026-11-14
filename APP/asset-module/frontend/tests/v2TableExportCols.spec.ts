// 2.058.11：V2Table 的 exportCols（只在匯出時接在後面的欄）真的進到匯出檔；沒給時匯出跟以前一樣。exportAll 給的列照用。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sortRows, useSort } from '../composables/useSort'
import V2Table from '../components/v2/Table.vue'

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
const rows = [{ key: 'a', name: 'web01' }, { key: 'b', name: 'db02' }]

describe('V2Table exportCols', () => {
  it('畫面欄在前、匯出專用欄接在後面；值從 exportAll 拿到的列取', async () => {
    const w = mount(V2Table, { props: { rows, prefix: 'M', cols: [{ k: 'name', label: '名稱' }], exportCols: [{ k: 'x0', label: '狀態' }],
      exportAll: async () => rows.map(r => ({ ...r, x0: r.key === 'a' ? '已納管' : null })) }, global: { stubs } })
    expect(w.text()).not.toContain('狀態')                        // 畫面上不出現
    await w.find('.xbtn').trigger('click'); await flushPromises()
    expect(out!.h).toEqual(['項次', '名稱', '狀態'])
    expect(out!.b).toEqual([['M1', 'web01', '已納管'], ['M2', 'db02', '']])
  })
  it('沒給 exportCols：跟以前一樣', async () => {
    const w = mount(V2Table, { props: { rows, cols: [{ k: 'name', label: '名稱' }] }, global: { stubs } })
    await w.find('.xbtn').trigger('click'); await flushPromises()
    expect(out!.h).toEqual(['項次', '名稱'])
  })
})
