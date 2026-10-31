// 原 manage-state-detail.vue 頁面實掛（Codex 79）：route 切換、次要提示不殘留、失敗不沿用上一頁、舊請求晚到不覆蓋。
// Nuxt 自動匯入以 stubGlobal 提供；NuxtLink 以保留 to 的 a 替身；InfoNote／SortTh 為非目標替身。API 全假、無網路。
import { enableAutoUnmount, mount, flushPromises } from '@vue/test-utils'
import { computed, defineComponent, h, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSort } from '../composables/useSort'
import SerialLinks from '../components/SerialLinks.vue'
import Page from '../pages/manage-state-detail.vue'

const pg = { n: 1, applies: true, items: [{ asset_serial: 'S1', hostname: 'h1', ip: '192.0.2.1', machine_state: '失聯', gap_rows: ['S2'], row_states: ['失聯', '未涵蓋'] }] }
const row = (serial: string, host: string) => ({ asset_serial: serial, hostname: host, ip: '192.0.2.9', state: 'x', collect_checked_at: null, collect_error: null, os_type: null })
const route = reactive<{ query: Record<string, string> }>({ query: { state: '未涵蓋' } })
let api: (url: string, o: any) => Promise<any>
const toasts: string[] = []

beforeEach(() => {
  route.query = { state: '未涵蓋' }
  toasts.length = 0
  vi.stubGlobal('ref', ref)
  vi.stubGlobal('computed', computed)
  vi.stubGlobal('watch', watch)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => api(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
  vi.stubGlobal('useSort', useSort)
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)                 // 每案結束卸載，避免共用 route 的 watcher 殘留

const stubs = {
  NuxtLink: { props: ['to'], template: '<a class="dl" :href="typeof to === \'string\' ? to : JSON.stringify(to)"><slot /></a>' },
  InfoNote: { template: '<i />' },
  SortTh: { template: '<th><slot /></th>' },
}
async function mountPage() {
  const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(Page) }) })
  const w = mount(Wrap, { global: { stubs, components: { SerialLinks } } })
  await flushPromises()
  return w
}
const hrefs = (w: any) => w.findAll('a.dl').map((a: any) => a.attributes('href'))

describe('manage-state-detail 原頁（route 與次要提示）', () => {
  it('未涵蓋（整台口徑有缺口）顯示提示；切到已納管提示消失、標題與一般連結正確', async () => {
    api = async (_u, o) => (o.params.state === 'partial_gaps' ? pg : { state: o.params.state, total: 1, items: [row('R1', 'ok1')] })
    const w = await mountPage()
    expect(w.text()).toContain('另有 1 台')
    route.query = { state: '已納管' }
    await flushPromises()
    expect(w.text()).not.toContain('另有 1 台')
    expect(w.find('h1').text()).toContain('已納管')
    expect(hrefs(w)).toContain('/assets/R1')
  })

  it('applies true→false（代表筆口徑）再載入未涵蓋：提示不顯示', async () => {
    let applies = true
    api = async (_u, o) => (o.params.state === 'partial_gaps' ? { ...pg, applies } : { state: o.params.state, total: 0, items: [] })
    const w = await mountPage()
    expect(w.text()).toContain('另有 1 台')
    applies = false
    route.query = { state: '已納管' }
    await flushPromises()
    route.query = { state: '未涵蓋' }
    await flushPromises()
    expect(w.text()).not.toContain('另有 1 台')
  })

  it('partial_gaps 頁：兩缺口各自連結；再切回未涵蓋，缺口清單不殘留', async () => {
    const two = { n: 1, applies: true, items: [{ asset_serial: 'S3', hostname: 'h2', ip: null, machine_state: '失聯', gap_rows: ['S4', 'S5'], row_states: ['失聯', '未涵蓋'] }] }
    api = async (_u, o) => (o.params.state === 'partial_gaps' ? two : { state: o.params.state, total: 1, items: [row('R2', 'plain')] })
    route.query = { state: 'partial_gaps' }
    const w = await mountPage()
    expect(hrefs(w)).toEqual(['/assets/S4', '/assets/S5'])
    expect(w.find('h1').text()).toContain('部分登記未涵蓋')
    route.query = { state: '未涵蓋' }
    await flushPromises()
    expect(hrefs(w)).toContain('/assets/R2')
    expect(hrefs(w)).not.toContain('/assets/S4')
  })

  it('次要待辦讀取失敗：主清單照常、不顯示提示（不沿用舊提示）', async () => {
    let fail = false
    api = async (_u, o) => {
      if (o.params.state === 'partial_gaps') {
        if (fail) throw new Error('boom')
        return pg
      }
      return { state: o.params.state, total: 1, items: [row('R3', 'main')] }
    }
    const w = await mountPage()
    expect(w.text()).toContain('另有 1 台')
    fail = true
    route.query = { state: '已納管' }
    await flushPromises()
    route.query = { state: '未涵蓋' }
    await flushPromises()
    expect(w.text()).not.toContain('另有 1 台')
    expect(hrefs(w)).toContain('/assets/R3')
  })

  it('主清單讀取失敗：顯示錯誤、不沿用上一頁清單', async () => {
    let fail = false
    api = async (_u, o) => {
      if (fail) throw Object.assign(new Error('x'), { data: { detail: '伺服器錯誤' } })
      return o.params.state === 'partial_gaps' ? pg : { state: o.params.state, total: 1, items: [row('R4', 'before')] }
    }
    const w = await mountPage()
    expect(hrefs(w)).toContain('/assets/R4')
    fail = true
    route.query = { state: '已納管' }
    await flushPromises()
    expect(w.text()).toContain('伺服器錯誤')
    expect(hrefs(w)).not.toContain('/assets/R4')
  })

  it('快速切 route：舊請求晚回來不覆蓋新畫面', async () => {
    let releaseOld: (v: any) => void = () => {}
    api = (_u, o) => {
      if (o.params.state === '失聯') return new Promise(r => { releaseOld = r })
      return Promise.resolve(o.params.state === 'partial_gaps' ? { ...pg, applies: false } : { state: o.params.state, total: 1, items: [row('NEW', 'newer')] })
    }
    route.query = { state: '已納管' }
    const w = await mountPage()
    route.query = { state: '失聯' }
    await flushPromises()
    route.query = { state: '未涵蓋' }
    await flushPromises()
    releaseOld({ state: '失聯', total: 1, items: [row('OLD', 'older')] })
    await flushPromises()
    expect(hrefs(w)).toContain('/assets/NEW')
    expect(hrefs(w)).not.toContain('/assets/OLD')
  })
})
