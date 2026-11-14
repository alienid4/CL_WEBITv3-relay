// 原 v2 事件頁（pages/v2/incident/[id].vue）實掛：時間軸 loading／失敗／成功空／成功有資料／重試／換事件（Codex 78）。
// Nuxt 自動匯入以 stubGlobal 提供；V2Page 輸出 title／conclusion／tab 三個插槽；V2Table 為簡化替身（只驗「空」與「有列」的文字）；
// API 全假、無網路。頁面其他區塊（範圍／影響／通報…）走原模板，API 都回最小假資料。
import { mount, flushPromises } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSort } from '../composables/useSort'
import Page from '../pages/v2/incident/[id].vue'

const route = reactive<{ params: Record<string, string>; query: Record<string, string> }>({ params: { id: '999999' }, query: { tab: 'close' } })
let timelineImpl: (id: string) => Promise<any>
const calls: string[] = []

const ev = (id: string) => ({ id: Number(id), title: `fixture-${id}`, status: 'closed', type: 't', category: 'incident', mode: 'drill', started_at: '2030-01-01 00:00:00',
  closed_at: '2030-01-01 01:00:00', closed_by: 'fixture-closer', summary: 'fixture-summary' })
const view = (id: string) => ({ event: ev(id), expand: { counts: { machines: 0, systems: 0 }, scope_text: '', machines: [], systems: [] }, tasks: [], first: [], teams: [], statuses: [], types: [] })

beforeEach(() => {
  route.params = { id: '999999' }
  route.query = { tab: 'close' }
  calls.length = 0
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('ref', ref)
  vi.stubGlobal('computed', computed)
  vi.stubGlobal('watch', watch)
  vi.stubGlobal('onMounted', onMounted)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useTableExport', () => ({ exportTable: () => {}, busy: ref(false) }))
  vi.stubGlobal('useSort', useSort)
  vi.stubGlobal('useV2ExportName', () => () => 'x')
  vi.stubGlobal('useApi', () => ({
    apiFetch: async (u: string) => {
      calls.push(u)
      const m = /^\/api\/incidents\/(\d+)(\/timeline)?$/.exec(u)
      if (m && m[2]) return timelineImpl(m[1])
      if (m) return view(m[1])
      if (u === '/api/racks/datacenter') return { rooms: [] }
      return {}
    },
  }))
})
afterEach(() => vi.unstubAllGlobals())

const V2Page = defineComponent({ props: ['modelValue', 'tabs', 'back'], setup(_p, { slots }) { return () => h('div', [slots.title?.(), slots.conclusion?.(), slots.default?.(), slots.tab?.()]) } })
const V2Table = defineComponent({ props: ['rows', 'empty', 'cols', 'prefix'], setup(p) {
  return () => (p.rows.length
    ? h('table', p.rows.map((r: any) => h('tr', { class: 'tlrow' }, `${r.at16}|${r.kind}|${r.text}|${r.created_by}`)))
    : h('p', { 'data-testid': 'tl-empty' }, p.empty))
} })
const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('div', slots.default?.()) } })
async function mountPage() {
  const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(Page) }) })
  const w = mount(Wrap, { global: { components: { V2Page, V2Table, V2Unknown: passthrough, InfoNote: passthrough, SortTh: passthrough, NuxtLink: passthrough }, stubs: { NuxtLink: true } } })
  await flushPromises()
  return w
}
const rec = { at: '2030-01-01 00:05:00', kind: 'note', text: 'fixture-evidence', created_by: 'fixture-operator' }

describe('事件頁時間軸：四態', () => {
  it('成功有紀錄：顯示紀錄與人員，沒有失敗／空訊息', async () => {
    timelineImpl = async () => [rec]
    const w = await mountPage()
    expect(w.text()).toContain('fixture-evidence')
    expect(w.text()).toContain('fixture-operator')
    expect(w.find('[data-testid=tl-error]').exists()).toBe(false)
    expect(w.find('[data-testid=tl-empty]').exists()).toBe(false)
  })

  it('成功但空：顯示「尚無紀錄」，不是失敗', async () => {
    timelineImpl = async () => []
    const w = await mountPage()
    expect(w.find('[data-testid=tl-empty]').text()).toBe('尚無紀錄')
    expect(w.find('[data-testid=tl-error]').exists()).toBe(false)
  })

  it('API 失敗（403／500）：明示讀取失敗、處置紀錄尚未確認，且不顯示「尚無紀錄」', async () => {
    for (const detail of ['權限不足', '伺服器錯誤']) {
      timelineImpl = async () => { throw Object.assign(new Error('x'), { data: { detail } }) }
      const w = await mountPage()
      const t = w.find('[data-testid=tl-error]')
      expect(t.exists()).toBe(true)
      expect(t.text()).toContain('時間軸讀取失敗，處置紀錄尚未確認')
      expect(t.text()).toContain(detail)
      expect(w.find('[data-testid=tl-empty]').exists()).toBe(false)
      expect(w.text()).not.toContain('尚無紀錄')
    }
  })

  it('重試：失敗後按重試恢復成功；重試中顯示載入中', async () => {
    let fail = true
    let release: (v: any) => void = () => {}
    timelineImpl = () => (fail ? Promise.reject(new Error('boom')) : new Promise(r => { release = r }))
    const w = await mountPage()
    expect(w.find('[data-testid=tl-error]').exists()).toBe(true)
    fail = false
    await w.find('[data-testid=tl-retry]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid=tl-loading]').exists()).toBe(true)      // 重試中
    release([rec])
    await flushPromises()
    expect(w.find('[data-testid=tl-error]').exists()).toBe(false)
    expect(w.text()).toContain('fixture-evidence')
  })

  it('舊請求晚回來不覆蓋：重試先完成，舊的失敗晚到也不能把畫面改回失敗', async () => {
    let first = true
    let rejectOld: (e: any) => void = () => {}
    timelineImpl = () => {
      if (first) { first = false; return new Promise((_r, rej) => { rejectOld = rej }) }
      return Promise.resolve([rec])
    }
    const w = await mountPage()                                        // 第一次請求還沒回（載入中）
    expect(w.find('[data-testid=tl-loading]').exists()).toBe(true)
    // 第二次（例如 makeReport／addNote 觸發的重載）先完成
    await (w.findComponent(Page).vm as any).loadTimeline?.()
    rejectOld(new Error('late failure'))
    await flushPromises()
    expect(w.find('[data-testid=tl-error]').exists()).toBe(false)
  })
})
