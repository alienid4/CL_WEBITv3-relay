// 原 RackElevation.vue 實掛（Codex 85／001）：機櫃摘要「最大連續」在有／無未定位設備時的實際 DOM。
// 有未定位：空與最大連續都只給上界（≤…）；無未定位：維持確定值。Nuxt 自動匯入以 stubGlobal 提供，API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSort } from '../composables/useSort'
import RackElevation from '../components/RackElevation.vue'

let detail: any
const calls: string[] = []

const power = (over: Record<string, any> = {}) => ({ w: 3000, volts: 220, kg: 100, if_all_on_w: 0, used_u: 10, free_u: 32, unplaced_count: 0, largest_free_u: 32,
  amps: 13.6, btu_h: 1000, unknown_count: 0, unobserved_count: 0, system_level: [], vendor_move: [], ...over })
const make = (p: any) => ({ room: 'Fixture Room', rack: 'M13', floor: '1F', height_u: 42, power: p, reservations: [], legacy_reservations: [], legacy_audits: [],
  key_warnings: [], excel: { run: null, cells: [], total: 0 }, devices: [], systems: [], last_audit: {}, box_count: 0, suspect: [], orphan_ports: [],
  excel_only: false, room_racks: ['M13'], can_edit: false, ...{} })

beforeEach(() => {
  calls.length = 0
  for (const [k, v] of Object.entries({ ref, computed, watch, onMounted, onBeforeUnmount, reactive, nextTick, useSort })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: async () => ({}) }))
  vi.stubGlobal('useApi', () => ({
    apiFetch: async (u: string) => {
      calls.push(u)
      if (u === '/api/racks/detail') return detail
      if (u === '/api/racks/position/moves') return { items: [] }
      return { systems: [], vendors: [], items: [] }
    },
  }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
async function mountRack() {
  const w = mount(RackElevation, {
    props: { room: 'Fixture Room', rack: 'M13' },
    global: { stubs: { NuxtLink: true, InfoNote: passthrough, SortTh: passthrough, V2Table: true, ClientOnly: passthrough }, components: { InfoNote: passthrough } },
  })
  await flushPromises()
  return w
}
const summary = (w: any) => (w.text().match(/已用[^\n]*?最大連續[^\n]*?U）/) || [''])[0].replace(/\s+/g, '')

describe('機櫃摘要：空／最大連續 U（原 RackElevation 實掛）', () => {
  it('有未定位設備：空 與 最大連續 都只給上界（≤），不顯示確定值', async () => {
    detail = make(power({ unplaced_count: 10, free_u: 42, largest_free_u: 42 }))
    const w = await mountRack()
    const t = summary(w)
    expect(t).toContain('空≤42U')
    expect(t).toContain('另10台未定位')
    expect(t).toContain('最大連續≤42（空位未核實）U')
    expect(t).not.toMatch(/最大連續42U/)
  })

  it('沒有未定位設備：維持確定值，不加 ≤', async () => {
    detail = make(power({ unplaced_count: 0, free_u: 32, largest_free_u: 20 }))
    const w = await mountRack()
    const t = summary(w)
    expect(t).toContain('空32U')
    expect(t).toContain('最大連續20U')
    expect(t).not.toContain('≤')
  })

  it('舊 API 沒有 unplaced_count 欄位：視為 0（不誤加上界）', async () => {
    const p = power({ free_u: 30, largest_free_u: 12 })
    delete (p as any).unplaced_count
    detail = make(p)
    const w = await mountRack()
    expect(summary(w)).toContain('最大連續12U')
  })
})
