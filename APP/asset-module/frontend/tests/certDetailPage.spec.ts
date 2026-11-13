// 憑證詳情頁實掛：唯讀帳號固定提示、匯入預排文字的「轉成正式日期／忽略匯入值」、標記已更換／排定更換按鈕、預排更換時間文字唯讀。
// Nuxt 自動匯入以 stubGlobal 提供；V2Page／V2Table 為簡化替身；API 全假。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, Suspense, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as filters from '../composables/certFilters'
import * as fmt from '../composables/certFormat'
import Page from '../pages/v2/certs/[id].vue'
import CertReplaceModal from '../components/CertReplaceModal.vue'

const route = reactive<{ params: Record<string, string>; query: Record<string, string> }>({ params: { id: 'it1' }, query: {} })
const calls: { url: string; opts: any }[] = []
let viewer = false
let detail: any
let guarded: (u: string, o: any) => Promise<any>

const base = () => ({ id: 'it1', name: 's1.example.test', environment: '正式', endpoint: '192.0.2.1', port: 443, sni: '', mount_type: 'F5', host_ips: '', f5_bigip: '', os_type: '', req_unit: '營運部',
  req_contact: '', ap_contact: '', infra_owner: '', ap_tester: '', planned_text: '', planned_date: '', planned_ignored: false, planned_text_date: '', announce_needed: '', replace_method: 'manual',
  replace_status: '', note: '', active: true, contacts_masked: true, manual_fields: [], observations: [], view: { status: 'ok', status_label: '正常', remain_text: '剩 100 天', risk: '' },
  source_label: '手動', created_at: '2026-11-01', key_text: 'k', has_deactivate_note: false })
const OPTS = { mount_types: [{ name: 'F5', active: true }], replace_statuses: ['待排程', '已排程', '已更換', '已驗測'], environments: ['正式', '測試'], deactivate_reasons: [{ code: 'retired', label: '已汰換' }] }

beforeEach(() => {
  route.query = {}
  calls.length = 0
  viewer = false
  detail = base()
  guarded = async () => ({})
  for (const [k, v] of Object.entries({ ...filters, ...fmt, ref, reactive, computed, watch, onMounted, onBeforeUnmount })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (u: string) => (u === '/api/certs/options' ? OPTS : detail) }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: (u: string, o: any) => { calls.push({ url: u, opts: o }); return guarded(u, o) } }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Page = defineComponent({ props: ['modelValue', 'tabs', 'back'], setup(_p, { slots }) { return () => h('div', [slots.title?.(), slots.conclusion?.(), slots.actions?.(), slots.default?.(), slots.tab?.()]) } })
const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
async function mountPage() {
  const Wrap = defineComponent({ render: () => h(Suspense, null, { default: () => h(Page) }) })
  const w = mount(Wrap, { global: { components: { V2Page, V2Table: passthrough, InfoNote: passthrough, NuxtLink: passthrough, CertReplaceModal }, config: { globalProperties: { ...fmt, ...filters } as any } } })
  await flushPromises()
  return w
}
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().includes(t))

describe('詳情頁', () => {
  it('唯讀帳號固定顯示「唯讀帳號」提示，沒有動作按鈕；一般帳號沒有這行', async () => {
    viewer = true
    const w = await mountPage()
    expect(w.text()).toContain('唯讀帳號：只能查看，不能修改')
    expect(btn(w, '標記已更換')).toBeUndefined()
    viewer = false
    expect((await mountPage()).text()).not.toContain('唯讀帳號：只能查看')
  })

  it('有「標記已更換」「排定更換」按鈕，點了開視窗', async () => {
    const w = await mountPage()
    await btn(w, '排定更換')!.trigger('click')
    expect(w.text()).toContain('排定更換：s1.example.test')
  })

  it('預排更換時間是唯讀（匯入原值）；需公告是下拉；舊自由文字值顯示為「匯入原值」選項', async () => {
    detail = { ...base(), planned_text: '12 月初', announce_needed: '視情況' }
    const w = await mountPage()
    const lab = (t: string) => w.findAll('form.frm label').find((l: any) => l.text().startsWith(t))!
    expect(lab('預排更換時間').find('input').attributes('disabled')).toBeDefined()
    const ann = lab('需公告')
    expect(ann.find('select').exists()).toBe(true)
    expect(ann.findAll('option').map((o: any) => o.text())).toEqual(['（未填）', '是', '否', '視情況（匯入原值）'])
  })

  it('匯入預排文字能轉日期：顯示可轉成的日期，「轉成正式日期」「忽略匯入值」各打對的 API', async () => {
    detail = { ...base(), planned_text: '2026-12-10 22:00', planned_text_date: '2026-12-10' }
    vi.stubGlobal('confirm', () => true)
    const w = await mountPage()
    expect(w.text()).toContain('可轉成 2026-12-10')
    await btn(w, '轉成正式日期')!.trigger('click'); await flushPromises()
    expect(calls.at(-1)).toMatchObject({ url: '/api/certs/items/it1/planned-import', opts: { method: 'POST', body: { action: 'convert' } } })
    await btn(w, '忽略匯入值')!.trigger('click'); await flushPromises()
    expect(calls.at(-1)!.opts.body).toEqual({ action: 'ignore' })
  })

  it('匯入文字無法轉日期：只有「忽略匯入值」，沒有「轉成正式日期」', async () => {
    detail = { ...base(), planned_text: '下週末', planned_text_date: '' }
    const w = await mountPage()
    expect(w.text()).toContain('無法轉成日期')
    expect(btn(w, '轉成正式日期')).toBeUndefined()
    expect(btn(w, '忽略匯入值')).toBeTruthy()
  })

  it('已有正式預排日期、或已忽略過：不顯示匯入文字處理列', async () => {
    detail = { ...base(), planned_text: '2026-12-10', planned_text_date: '', planned_date: '2026-12-20' }
    expect(btn(await mountPage(), '忽略匯入值')).toBeUndefined()
    detail = { ...base(), planned_text: '下週末', planned_ignored: true }
    expect(btn(await mountPage(), '忽略匯入值')).toBeUndefined()
  })
})
