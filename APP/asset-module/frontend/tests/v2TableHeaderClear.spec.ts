// 2.055.1（批 19 審查 P1-1，D1 安全）：真的掛 V2Table——header-clears-all 時，表頭取消勾選要清掉「別頁」的勾選。
// 情境：第 1 頁勾 1 台、第 2 頁勾 5 台，在第 1 頁按表頭取消 → 送出 {}（以前只清第 1 頁，第 2 頁 5 台還留著被送進依序納管）。
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { computed, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sortRows, useSort } from '../composables/useSort'
import V2Table from '../components/v2/Table.vue'

enableAutoUnmount(afterEach)
beforeEach(() => {
  for (const [k, v] of Object.entries({ ref, computed, watch, useSort, sortRows })) vi.stubGlobal(k, v)
  vi.stubGlobal('useTableExport', () => ({ exportTable: () => {}, busy: ref(false) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useV2ExportName', () => ref('x'))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false) }))
})
afterEach(() => vi.unstubAllGlobals())

const rows = Array.from({ length: 6 }, (_, i) => ({ key: `k${i}`, name: `n${i}` }))
const sel = Object.fromEntries(rows.map(r => [r.key, true]))       // 第 1 頁（max=1）1 台＋後面 5 台
const stubs = { SortTh: { template: '<th><slot /></th>' }, InfoNote: { template: '<i />' }, NuxtLink: { template: '<a><slot /></a>' } }

function mnt(headerClearsAll: boolean) {
  return mount(V2Table, { props: { rows, cols: [{ k: 'name', label: '名稱' }], selectable: true, max: 1, selected: sel, headerClearsAll }, global: { stubs } })
}

describe('V2Table 表頭取消勾選', () => {
  it('header-clears-all：在第 1 頁取消＝全部清掉（第 2 頁以後的也清）', async () => {
    const w = mnt(true)
    const head = w.find('th.cb input')
    expect((head.element as HTMLInputElement).checked).toBe(true)
    await head.setValue(false)
    expect(w.emitted('update:selected')!.at(-1)![0]).toEqual({})
  })
  it('預設（其他表格）：只清本頁，行為不變', async () => {
    const w = mnt(false)
    await w.find('th.cb input').setValue(false)
    const out = w.emitted('update:selected')!.at(-1)![0] as Record<string, boolean>
    expect(Object.keys(out).filter(k => out[k]).length).toBe(5)
  })
  it('表頭勾選（不是取消）照舊只勾本頁', async () => {
    const w = mount(V2Table, { props: { rows, cols: [{ k: 'name', label: '名稱' }], selectable: true, max: 2, selected: {}, headerClearsAll: true }, global: { stubs } })
    await w.find('th.cb input').setValue(true)
    expect(w.emitted('update:selected')!.at(-1)![0]).toEqual({ k0: true, k1: true })
  })
})
