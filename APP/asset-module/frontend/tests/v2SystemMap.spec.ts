/**
 * 2.048.1 V58 系統主機部署圖（V2 原生）實掛：
 *   - 圖下面的清單：一列＝圖上一個分組；台數點下去切到主機清單、只列那幾台（筆數＝台數）
 *   - 換系統：先送的晚回來不能蓋掉後送的（useLatest）
 * Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身（有具名插槽就畫插槽）；API 全假。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import SystemMap from '../components/v2/SystemMap.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void }[] = []
beforeEach(() => {
  pending = []
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise(resolve => pending.push({ url, opts, resolve })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = (apiId: string) => ({ props: { apiId }, global: { components: { V2Table, InfoNote: pass, NuxtLink: pass, RackElevation: pass, ContactCard: pass } } })

const host = (key: string, room: string) => ({ key, asset_serial: key, serials: [key], hostname: key, ips: [], ip: null, os: 'RHEL', environment: '正式', location: room, room,
  env_group: '正式', record_count: 1, esxi: [], datastores: [], on_esxi: false, exist_level: 'onboarded', exist_label: '', exist_score: 90, seen: true,
  asset_names: [], os_class: 'Linux', os_family: 'Linux', models: [], hw_serials: [] })
const MAP = (name: string) => ({
  api_id: name, node_id: name, name, usage_unit: null, custodian: null, ap_department: null, ap_owner: null, sys_class: null, severity: '一般',
  hosts: [host('A1', '甲'), host('A2', '甲'), host('B1', '乙')], retired: [], esxi: [], clusters: [], datastores: [],
  rooms: [
    { room: '甲', host_count: 2, env: { 正式: 2, 備援: 0, 測試: 0 }, hosts: ['A1', 'A2'], groups: [{ id: 'p', kind: 'physical', label: '實體', hosts: ['A1', 'A2'], esxi: [], host_count: 2 }], seen: 2, registered_only: 0, seen_hosts: ['A1', 'A2'], registered_only_hosts: [] },
    { room: '乙', host_count: 1, env: { 正式: 1, 備援: 0, 測試: 0 }, hosts: ['B1'], groups: [{ id: 'p', kind: 'physical', label: '實體', hosts: ['B1'], esxi: [], host_count: 1 }], seen: 1, registered_only: 0, seen_hosts: ['B1'], registered_only_hosts: [] },
  ],
  outage: [], depends_on: [], depended_by: [], deps_available: false, exist_threshold: 60,
  racks: { rooms: [], no_rack: [], no_rack_count: 0, racked_count: 0 },
  summary: { hosts: 3, seen: 3, registered_only: 0, esxi: 0, clusters: 0, datastores: 0, on_esxi: 0, no_esxi: 3, depends_on: 0, depended_by: 0, retired_records: 0 },
})
async function settle(map = MAP('S1')) {
  for (let i = 0; i < 3; i++) {
    await flushPromises()
    for (const p of pending.splice(0)) p.resolve(p.url === '/api/blast/system-map' ? map : [])
  }
  await flushPromises()
}

describe('系統主機部署圖', () => {
  it('圖下清單：一列一個分組；台數點下去只列那幾台', async () => {
    const w = mount(SystemMap, opts('S1'))
    await settle()
    const list = w.find('[data-test="graph-list"] [data-prefix="L"]')
    expect(list.findAll('.row').length).toBe(2)
    await list.findAll('.row')[0].find('.c-host_count button').trigger('click')
    await nextTick()
    expect(w.find('[data-prefix="H"]').findAll('.row').length).toBe(2)
  })

  it('換系統：先送的晚回來不能蓋掉後送的', async () => {
    const w = mount(SystemMap, opts('S1'))
    await settle()
    await w.setProps({ apiId: 'S2' })
    const older = pending.splice(0)
    await w.setProps({ apiId: 'S3' })
    await settle(MAP('S3'))
    for (const p of older) p.resolve(MAP('S2'))
    await flushPromises()
    expect(w.text()).toContain('S3')
    expect(w.text()).not.toContain('S2 ·')
  })

  it('換系統：清掉上一個系統的連線證據，舊證據請求晚回來也不顯示（2.048.2）', async () => {
    const withDeps = (name: string) => ({ ...MAP(name), deps_available: true,
      depends_on: [{ api_id: 'X', name: 'X 系統', dep_type: null, has_hosts: true, source: 'observed', evidence: { count: 1, observations: 5, first_seen: null, last_seen: null, hosts: [], ports: [443], protocols: [] } }] })
    const w = mount(SystemMap, opts('S1'))
    await settle(withDeps('S1') as any)
    await w.findAll('button').find(b => b.text().startsWith('上下游'))!.trigger('click')
    await w.find('.c-obs button').trigger('click')
    const edgeReq = pending.find(p => p.url === '/api/blast/observed-edge')!
    pending.splice(pending.indexOf(edgeReq), 1)
    await w.setProps({ apiId: 'S2' })
    await settle(withDeps('S2') as any)
    edgeReq.resolve({ rows: [{ id: 1, host_ip: '10.99.0.9', asset_serial: null, direction: 'outbound', local_port: 0, peer_ip: '10.99.0.8', peer_port: 443, count: 1, first_seen: null, last_seen: null }], stale_days: 30 })
    await flushPromises()
    expect(w.find('.edgebox').exists()).toBe(false)
  })
})
