// 5.2「設備健康」頁籤的狀態用語（2026-11-18，H15）。GPT 審核抓到：verdict.stale＝true 時被顯示成「正常」，
// 把過期的證據說成沒事。固定六種狀態：正常／過期／失敗／只收到一半／沒收到／待設定。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/StorageDeviceHealth.vue'

enableAutoUnmount(afterEach)
const mk = (id: number, name: string, last_health: any, health_schedule: any = { included: true }) => ({ id, name, last_health, health_schedule })
const ROWS = [
  mk(1, 'none', null),
  mk(2, 'excluded', null, { included: false, reason: '沒開排程' }),
  mk(3, 'ok', { finished_at: 't', verdict: { level: 'complete', text: '完整', stale: false } }),
  mk(4, 'stale', { finished_at: 't', verdict: { level: 'complete', text: '完整', stale: true } }),
  mk(5, 'failed', { finished_at: 't', verdict: { level: 'failed', text: '失敗', stale: false } }),
  mk(6, 'partial', { finished_at: 't', verdict: { level: 'incomplete', text: '一半', stale: false } }),
]
let isAdmin = true
const V2Table = defineComponent({ props: ['rows'], setup(p) { return () => h('table', {}, (p.rows as any[]).map((r: any) => h('tr', { class: 'row' }, [h('td', { class: 'n' }, r.name), h('td', { class: 'st' }, r.state)]))) } })
beforeEach(() => {
  isAdmin = true
  vi.stubGlobal('ref', ref); vi.stubGlobal('computed', computed); vi.stubGlobal('onMounted', onMounted)
  vi.stubGlobal('useAuth', () => ({ isAdmin: { value: isAdmin } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: () => Promise.resolve({ rows: ROWS }) }))
})
const mnt = () => mount(Comp, { global: { stubs: { InfoNote: true, NuxtLink: true, StorageHealth: true, IntegrationStorageHealth: true, V2Table } } })

describe('設備健康狀態', () => {
  it('六種狀態各用固定用語；過期不可寫成正常', async () => {
    const w = mnt(); await flushPromises()
    const st = Object.fromEntries(w.findAll('tr.row').map(r => [r.find('.n').text(), r.find('.st').text()]))
    expect(st).toEqual({ none: '沒收到', excluded: '待設定', ok: '正常', stale: '過期', failed: '失敗', partial: '只收到一半' })
  })
})
