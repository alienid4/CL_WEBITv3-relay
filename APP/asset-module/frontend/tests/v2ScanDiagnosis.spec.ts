/**
 * 6.1「掃描診斷」卡（2.069.x）：結論一行、要你處理（N 件）逐項編號＋證據＋處理連結、數字卡點了篩網段、查不到寫「查不到」。
 * Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身；API 全假（例子 IP 一律 10.99.x）。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ScanDiagnosis from '../components/v2/ScanDiagnosis.vue'

const seg = (o: any) => ({ id: 1, name: '網段 A', target: '10.99.1.0/24', enabled: true, enabled_text: '啟用', own_schedule: null, last_time: '2026-11-22 01:00:00', last_age: '9.0 小時前',
  covered_text: '成功涵蓋', last_count: 5, prev_count: 5, delta: 0, zero_streak: 0, status: '正常', ...o })
const BASE = () => ({
  status: '正常', headline: '掃描正常，沒有發現會讓掃不出東西的原因（最近一批 5 台）', now: '2026-11-22 10:00:00', causes: [] as any[],
  schedule: { enabled: true, mode: 'daily', time: '01:00', interval_hours: 6, weekday_label: '週日', last_schedule_run: '2026-11-22 01:00', last_schedule_age: '9.0 小時前', recent_runs: [] },
  quiet_window: { in_window_now: false, pending: null },
  latest_batch: { time: '2026-11-22 01:00:00', age: '9.0 小時前', found: 5, segments_covered: 1, segments_failed: 0 },
  segments: [seg({})], summary: { segments: 1, enabled: 1, disabled: 0, zero_now: 0, failed_now: 0, causes: 0 },
  probe: { ports: [22, 445, 3389, 5985], timeout_seconds: 0.6, icmp_fallback: true, arp_fallback: true },
  thresholds: { fail_ratio: 0.5, zero_streak: 3, stale_days: 2, schedule_grace_min: 30 }, unknowns: ['防火牆是否放行探測埠：資料庫查不到，需從掃描機實測'],
})
let payload: any
let fail = false
beforeEach(() => {
  payload = BASE(); fail = false
  for (const [k, v] of Object.entries({ ref, computed, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string) => (fail ? Promise.reject({ data: { detail: '壞了' } }) : u === '/api/scan/diagnosis' ? Promise.resolve(payload) : Promise.resolve(null)) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'empty'], setup(p) {
  return () => h('div', { class: 'tbl' }, (p.rows ?? []).length ? p.rows.map((r: any) => h('div', { class: 'row' }, `${p.prefix}${r.__no ?? ''} ${r.target ?? r.started_at ?? ''}`)) : p.empty)
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const NuxtLink = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { href: p.to, class: 'lnk' }, slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink } } }
const mountIt = async () => { const w = mount(ScanDiagnosis, opts); await flushPromises(); return w }

describe('掃描診斷卡', () => {
  it('正常：頂端一行結論，要你處理 0 件並寫「沒有要你處理的事」', async () => {
    const w = await mountIt()
    expect(w.get('[data-test=status]').text()).toBe('正常')
    expect(w.get('[data-test=headline]').text()).toContain('掃描正常')
    expect(w.text()).toContain('要你處理（0 件）')
    expect(w.get('[data-test=no-causes]').text()).toBe('沒有要你處理的事')
    expect(w.find('[data-test=causes]').exists()).toBe(false)
  })

  it('有原因：逐項 A1、A2 編號，顯示證據與處理連結', async () => {
    payload.status = '失敗'
    payload.headline = '發現 2 個可能原因'
    payload.causes = [
      { code: 'last_batch_zero', level: '失敗', title: '最近一次整批掃到 0 台', evidence: '2026-11-22 01:00 那一批涵蓋 2 個網段，成功列 0 筆', link: '/v2/onboard?tab=gaps', fix: '從掃描機實測探測埠是否放行' },
      { code: 'segment_zero_streak', level: '只收到一半', title: '網段 10.99.2.0/24 連續 3 次掃到 0 台', evidence: '最近 3 批都成功掃完但 0 台', segment: '10.99.2.0/24' },
    ]
    const w = await mountIt()
    expect(w.text()).toContain('要你處理（2 件）')
    const items = w.findAll('[data-test=causes] li')
    expect(items.length).toBe(2)
    expect(items[0].text()).toContain('A1')
    expect(items[0].text()).toContain('成功列 0 筆')
    expect(items[0].get('a.lnk').attributes('href')).toBe('/v2/onboard?tab=gaps')
    expect(items[1].text()).toContain('A2')
    expect(items[1].text()).toContain('10.99.2.0/24')
    expect(items[1].find('a.lnk').exists()).toBe(false)
  })

  it('數字卡可下鑽：點「最近一次 0 台」只剩那幾個網段，點「網段」回到全部', async () => {
    payload.segments = [seg({ id: 1, target: '10.99.1.0/24' }), seg({ id: 2, target: '10.99.2.0/24', last_count: 0, status: '沒收到' }), seg({ id: 3, target: '10.99.3.0/24', enabled: false, status: '待設定' })]
    payload.summary = { segments: 3, enabled: 2, disabled: 1, zero_now: 1, failed_now: 0, causes: 0 }
    const w = await mountIt()
    expect(w.findAll('.row').length).toBe(3)
    expect(w.get('[data-card="最近一次 0 台"] .n').text()).toBe('1')
    await w.get('[data-card="最近一次 0 台"]').trigger('click')
    expect(w.findAll('.row').map(r => r.text())).toEqual(['S2 10.99.2.0/24'])      // 項次不因篩選而變
    await w.get('[data-card="停用"]').trigger('click')
    expect(w.findAll('.row').map(r => r.text())).toEqual(['S3 10.99.3.0/24'])
    await w.get('[data-card="網段"]').trigger('click')
    expect(w.findAll('.row').length).toBe(3)
  })

  it('查不到就寫查不到：沒有掃描紀錄、沒有網段', async () => {
    payload.latest_batch = { time: null, age: '查不到', found: null, segments_covered: null, segments_failed: null }
    payload.segments = []
    payload.summary = { segments: 0, enabled: 0, disabled: 0, zero_now: 0, failed_now: 0, causes: 0 }
    const w = await mountIt()
    expect(w.get('[data-test=latest]').text()).toContain('查不到')
    expect(w.get('.tbl').text()).toContain('沒有設定任何要掃的網段')
    expect(w.text()).toContain('資料庫查不到')
  })

  it('探測埠與逾時顯示後端回傳的值，不寫死', async () => {
    payload.probe = { ports: [22], timeout_seconds: 1.5, icmp_fallback: false, arp_fallback: true }
    const w = await mountIt()
    expect(w.get('[data-test=ports]').text()).toBe('22')
    expect(w.get('[data-test=timeout]').text()).toContain('1.5 秒')
    expect(w.get('[data-test=timeout]').text()).not.toContain('ICMP')
  })

  it('載入失敗：顯示錯誤與重試，不顯示成「正常」', async () => {
    fail = true
    const w = await mountIt()
    expect(w.text()).toContain('掃描診斷載入失敗：壞了')
    expect(w.find('[data-test=status]').exists()).toBe(false)
  })
})
