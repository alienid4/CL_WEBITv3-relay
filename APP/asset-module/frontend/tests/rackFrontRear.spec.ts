// 機櫃「前後各一台」：純函式（utils/rackSide）＋原 RackElevation 實掛（正面／背面切換、「前＋後」標籤）。
// Nuxt 自動匯入以 stubGlobal 提供（寫法同 rackElevationFree.spec.ts），API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSort } from '../composables/useSort'
import RackElevation from '../components/RackElevation.vue'
import { ipSegText, rackCatColor, rackCatOf, rackCats } from '../composables/useRackColors'
import { ghostTitle, hasRear, isGhost, onSide, pairOf, sharesU } from '../utils/rackSide'

describe('rackSide 純函式', () => {
  const front = { key: 'a', side: 'front' as const, fr_with: ['b'] }
  const rear = { key: 'b', side: 'rear' as const, fr_with: ['a'] }
  const solo = { key: 'c' }
  it('沒有任何背面設備：不顯示切換', () => {
    expect(hasRear([front, solo])).toBe(false)
    expect(hasRear([front, rear, solo])).toBe(true)
  })
  it('正面只畫正面（含一般設備），背面只畫背面', () => {
    expect([front, rear, solo].filter(d => onSide(d, 'front')).map(d => d.key)).toEqual(['a', 'c'])
    expect([front, rear, solo].filter(d => onSide(d, 'rear')).map(d => d.key)).toEqual(['b'])
  })
  it('另一面只畫佔位（ghost）：背面視圖是正面設備、正面視圖是背面設備', () => {
    expect([front, rear, solo].filter(d => isGhost(d, 'rear')).map(d => d.key)).toEqual(['a', 'c'])
    expect([front, rear, solo].filter(d => isGhost(d, 'front')).map(d => d.key)).toEqual(['b'])
  })
  it('佔位提示：只有配對的設備才標正面／背面設備，一般設備不特別標', () => {
    expect(ghostTitle({ name: 'x', side: 'rear' }, '10-12U')).toBe('x（10-12U，背面設備）')
    expect(ghostTitle({ name: 'y', side: 'front' }, '10U')).toBe('y（10U，正面設備）')
    expect(ghostTitle({ name: 'z' }, '20U')).toBe('z（20U）')
  })
  it('「前＋後」只有共用 U 的配對才有；點開是正面在前、背面在後的兩台', () => {
    expect(sharesU(front)).toBe(true)
    expect(sharesU(solo)).toBe(false)
    expect(pairOf(rear, [rear, solo, front]).map(d => d.key)).toEqual(['a', 'b'])
  })
})

let detail: any
const calls: string[] = []
const power = (over: Record<string, any> = {}) => ({ w: 3000, volts: 220, kg: 100, if_all_on_w: 0, used_u: 3, free_u: 39, unplaced_count: 0, largest_free_u: 22,
  amps: 13.6, btu_h: 1000, unknown_count: 0, unobserved_count: 0, system_level: [], vendor_move: [], ...over })
const dev = (o: Record<string, any>) => ({ asset_serial: o.key, ip: null, model: null, rack_raw: null, exist_level: null, exist_score: null, exist_label: '', seen: true,
  systems: [], mine: false, state: 'active', state_label: null, asset_status: null, w: null, kg: null, spec_model: null, override: null, unobserved: false,
  overlap_with: [], ...o })
const make = (devices: any[], front_rear: any[]) => ({ room: 'Fixture Room', rack: 'M13', floor: '1F', height_u: 42, power: power(), reservations: [], legacy_reservations: [],
  legacy_audits: [], key_warnings: [], excel: { run: null, cells: [], total: 0 }, devices, systems: [], last_audit: {}, box_count: devices.length, suspect: [],
  orphan_ports: [], excel_only: false, room_racks: ['M13'], can_edit: false, front_rear })

const FRONT = dev({ key: 'k-front', name: 'frontbox01', ip: '192.0.2.11', model: 'Dell R750', u_start: 10, u_end: 11, side: 'front', fr_with: ['k-rear'] })
const REAR = dev({ key: 'k-rear', name: 'rearbox01', ip: '192.0.2.12', model: 'Cisco C9200', u_start: 10, u_end: 11, side: 'rear', fr_with: ['k-front'] })
const SOLO = dev({ key: 'k-solo', name: 'solobox01', ip: '192.0.2.13', model: 'Dell R660', u_start: 20, u_end: 20 })
const FR = [{ decision_id: 1, front: ['k-front'], rear: ['k-rear'], names: { 'k-front': 'frontbox01', 'k-rear': 'rearbox01' } }]

beforeEach(() => {
  calls.length = 0
  for (const [k, v] of Object.entries({ ref, computed, watch, onMounted, onBeforeUnmount, reactive, nextTick, useSort, ipSegText, rackCatColor, rackCatOf, rackCats })) vi.stubGlobal(k, v)
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
const faces = (w: any) => w.findAll('.slots button.face')
const names = (w: any) => faces(w).map((f: any) => f.text())

describe('RackElevation 正面／背面切換（原元件實掛）', () => {
  it('沒有任何背面設備的櫃：不顯示切換鈕，行為跟原本一樣', async () => {
    detail = make([SOLO, { ...FRONT, side: null, fr_with: [] }], [])
    const w = await mountRack()
    expect(w.find('.sidebar').exists()).toBe(false)
    expect(faces(w)).toHaveLength(2)
    expect(w.text()).not.toContain('前＋後')
  })

  it('有背面設備：預設正面——只畫正面設備，背面設備不畫；共用 U 的正面那台標「前＋後」', async () => {
    detail = make([FRONT, REAR, SOLO], FR)
    const w = await mountRack()
    expect(w.find('.sidebar').exists()).toBe(true)
    expect(w.find('[data-side="front"]').classes()).toContain('on')
    const t = names(w)
    expect(faces(w)).toHaveLength(2)
    expect(t.join('|')).toContain('frontbox01')
    expect(t.join('|')).toContain('solobox01')
    expect(t.join('|')).not.toContain('rearbox01')
    expect(w.findAll('.frtag')).toHaveLength(1)
    expect(w.find('.frtag').text()).toBe('前＋後')
    expect(w.findAll('.ghostface')).toHaveLength(1)           // 背面那台在正面視圖畫成佔位，不是空格
    expect(w.find('.ghostface').text()).toContain('背面有設備')
  })

  it('背面設備比正面多佔的 U：正面視圖看得到佔位；雙擊那一格不新增、點佔位看得到是誰', async () => {
    const R3 = dev({ key: 'k-r3', name: 'rear3box', ip: '192.0.2.15', model: 'Cisco C9300', u_start: 10, u_end: 12, side: 'rear', fr_with: ['k-f10', 'k-f11'] })
    const F10 = dev({ key: 'k-f10', name: 'front10', u_start: 10, u_end: 10, side: 'front', fr_with: ['k-r3'] })
    const F11 = dev({ key: 'k-f11', name: 'front11', u_start: 11, u_end: 11, side: 'front', fr_with: ['k-r3'] })
    detail = { ...make([F10, F11, R3], [{ decision_id: 2, front: ['k-f10', 'k-f11'], rear: ['k-r3'], names: {} }]), can_edit: true }
    const w = await mountRack()
    const ghost = w.find('.ghostface')
    expect(ghost.exists()).toBe(true)                          // 10-12U 的背面設備在正面視圖有佔位（12U 正面沒東西，也不是空的）
    expect(ghost.attributes('style')).toContain('height')
    expect(ghost.attributes('title')).toContain('rear3box')
    // 雙擊佔位（12U）：不開新增設備
    const slots = w.find('.slots')
    ;(slots.element as any).getBoundingClientRect = () => ({ top: 0, left: 0, right: 100, bottom: 700, width: 100, height: 700 })
    await slots.trigger('dblclick', { clientY: (42 - 12) * 16 + 4 })
    await flushPromises()
    expect(w.find('.mvmask').exists()).toBe(false)
    // 點佔位：看得到是誰
    await ghost.trigger('click')
    expect(w.find('[data-testid="fr-pair"]').text()).toContain('rear3box')
    // 三台群組：同面的兩台正面設備互不出現在對方的 fr_with（後端另有測試），這裡確認 ghost 之外的面板只有兩台正面
    expect(faces(w)).toHaveLength(2)
  })

  it('空的 U 雙擊仍可新增設備（沒被誤擋）', async () => {
    detail = { ...make([FRONT, REAR, SOLO], FR), can_edit: true }
    const w = await mountRack()
    const slots = w.find('.slots')
    ;(slots.element as any).getBoundingClientRect = () => ({ top: 0, left: 0, right: 100, bottom: 700, width: 100, height: 700 })
    await slots.trigger('dblclick', { clientY: (42 - 30) * 16 + 4 })     // 30U 是空的
    await flushPromises()
    expect(w.find('.mvmask').exists()).toBe(true)
  })

  it('切到背面：只畫背面設備（其他設備只剩淡淡的佔位），背面那台也標「前＋後」', async () => {
    detail = make([FRONT, REAR, SOLO], FR)
    const w = await mountRack()
    await w.find('[data-side="rear"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-side="rear"]').classes()).toContain('on')
    expect(faces(w)).toHaveLength(1)
    expect(faces(w)[0].text()).toContain('rearbox01')
    expect(w.text()).not.toContain('frontbox01  ')            // 面板區沒有正面設備的面板
    expect(w.findAll('.ghostface')).toHaveLength(2)            // 正面的兩台只畫佔位
    expect(w.findAll('.frtag')).toHaveLength(1)
    await w.find('[data-side="front"]').trigger('click')
    await flushPromises()
    expect(faces(w)).toHaveLength(2)
  })

  it('點「前＋後」：展開兩台各是誰（正面在前、背面在後）', async () => {
    detail = make([FRONT, REAR, SOLO], FR)
    const w = await mountRack()
    expect(w.find('[data-testid="fr-pair"]').exists()).toBe(false)
    await w.find('.frtag').trigger('click')
    await flushPromises()
    const box = w.find('[data-testid="fr-pair"]')
    expect(box.exists()).toBe(true)
    const rows = box.findAll('div').map((r: any) => r.text())
    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatch(/^正面\s+frontbox01/)
    expect(rows[1]).toMatch(/^背面\s+rearbox01/)
  })
})
