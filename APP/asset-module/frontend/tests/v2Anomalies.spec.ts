/**
 * 1.2「異常報告」（V18，2.047.3 V2 原生）實掛：待處理各組的台數＝點下去列出的筆數；同一台連續兩天消失算「連續消失」一台（不是兩筆）。
 * Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身；API 全假（主機名 TEST-）。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import Anomalies from '../components/v2/Anomalies.vue'

const route = reactive<{ query: Record<string, string> }>({ query: {} })
const ISSUES = [
  { id: 1, detected_at: '2026-11-01 08:00:00', hostname: 'TEST-A', ip: '10.99.0.1', issue_type: '異常消失' },
  { id: 2, detected_at: '2026-11-02 08:00:00', hostname: 'TEST-A', ip: '10.99.0.1', issue_type: '異常消失' },
  { id: 3, detected_at: '2026-11-02 08:00:00', hostname: 'TEST-B', ip: '10.99.0.2', issue_type: '異常消失' },
  { id: 4, detected_at: '2026-11-02 08:00:00', hostname: 'TEST-C', ip: '10.99.0.3', issue_type: '異常新增' },
]
beforeEach(() => {
  route.query = {}
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string) => Promise.resolve(
    u === '/api/issues' ? ISSUES
      : u === '/api/manage-state' ? { scan_time: '2026-11-02 08:00:00', items: [{ hostname: 'TEST-D', ip: '10.99.0.4', state: '未登記' }, { hostname: 'TEST-E', ip: '10.99.0.5', state: '已納管' }] }
        : u === '/api/dashboard/stats' ? { total_ica_count: 10, total_overlap_count: 8, scanned_count: 9, scan_only_count: 1, last_scan_time: null }
          : null) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl', 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row', 'data-key': r.key }, [`${p.prefix}${r.__no} ${r.name ?? r.hostname ?? ''}`, slots['cell-n']?.({ row: r })])))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: pass } } }

describe('異常報告', () => {
  it('各組台數＝點下去的筆數；全部列出＝各組加總', async () => {
    const w = mount(Anomalies, opts)
    await flushPromises()
    const groups = w.findAll('[data-prefix="G"] .row')
    expect(groups.map(g => g.text())).toEqual(['G1 連續消失（2 期以上）1', 'G2 本期首次消失1', 'G3 異常新增1', 'G4 在網路上但未登記1'])
    for (const key of ['repeat', 'first', 'added', 'unreg']) {
      route.query = { ag: key }
      await nextTick()
      expect(w.findAll('[data-prefix="D"] .row')).toHaveLength(1)
    }
    route.query = { ag: 'repeat' }
    await nextTick()
    expect(w.find('[data-prefix="D"]').text()).toContain('TEST-A')
    route.query = { ag: 'all' }
    await nextTick()
    expect(w.findAll('[data-prefix="D"] .row')).toHaveLength(4)
    expect(w.text()).toContain('待處理 4')
  })
})
