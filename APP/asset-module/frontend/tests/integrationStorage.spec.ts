// 儲存設備 API 設定區塊（批 S1）實掛：不收密碼（只挑 storage 類憑證名稱）、新增走 POST、「信任自簽」預設關且勾選有風險提示、
// 連線測試走 POST /test 並顯示結果、檢視者（非 admin）看不到寫入按鈕。Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/IntegrationStorage.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
const toasts: string[] = []
let admin = true
let testReply: any = { ok: true, message: '連線成功：Unity 380 unity-a，帳密已由受保護的 API 驗證' }

const dev = (o: any = {}) => ({
  id: 1, __no: 1, name: 'unity-a', vendor: 'dell_unity', vendor_label: 'Dell Unity', host: '10.99.0.5', port: 443, credential: 'stor-ro',
  ca_bundle: '', trust_self_signed: false, enabled: true, note: '', last_test_at: null, last_test_ok: null, last_test_code: null,
  last_test_msg: null, last_identity: null, last_tls_verified: null, ...o,
})
let devices: any[] = []

function fakeApi(u: string, o: any = {}) {
  calls.push({ url: u, opts: o })
  const m = o.method ?? 'GET'
  if (u === '/api/storage/devices' && m === 'GET') return Promise.resolve({ rows: devices, vendors: [{ key: 'dell_unity', label: 'Dell Unity' }], unsupported_vendors: ['NetApp', 'Pure Storage'] })
  if (u === '/api/credentials') return Promise.resolve([
    { name: 'stor-ro', kind: 'storage', username: 'svc_unity', scope: '10.99.0.' }, { name: 'wug', kind: 'whatsup', username: 'u', scope: null }])
  if (u.endsWith('/test')) return Promise.resolve(testReply)
  return Promise.resolve({ ok: true })
}

const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix'],
  setup(p, { slots }) {
    return () => h('table', {}, p.rows.map((r: any) => h('tr', { class: 'row' }, [
      h('td', { class: 'name' }, r.name), h('td', { class: 'result' }, r.result), h('td', { class: 'tls' }, r.tls), h('td', { class: 'act' }, slots['cell-act']?.({ row: r })),
    ])))
  },
})

beforeEach(() => {
  calls.length = 0; toasts.length = 0; admin = true; devices = [dev()]
  testReply = { ok: true, message: '連線成功：Unity 380 unity-a，帳密已由受保護的 API 驗證' }
  for (const [k, v] of Object.entries({ ref, computed, onMounted, reactive })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => fakeApi(u, o) }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: computed(() => admin) }))
  vi.stubGlobal('confirm', () => true)
  vi.stubGlobal('window', Object.assign(globalThis.window ?? {}, { confirm: () => true }))
})

const mnt = () => mount(Comp, { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, NuxtLink: { template: '<a><slot /></a>' }, V2Table } } })
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text() === t)

describe('儲存設備 API 設定', () => {
  it('列出設備、顯示最近測試結果；憑證下拉只列 storage 類', async () => {
    devices = [dev({ last_test_ok: false, last_test_code: 'unauthorized' })]
    const w = mnt(); await flushPromises()
    expect(w.find('.name').text()).toBe('unity-a')
    expect(w.find('.result').text()).toContain('帳密被拒')
    await btn(w, '新增儲存設備')!.trigger('click')
    const opts = w.findAll('select[aria-label=收集憑證] option').map((o: any) => o.text())
    expect(opts.some((t: string) => t.includes('stor-ro'))).toBe(true)
    expect(opts.some((t: string) => t.includes('wug'))).toBe(false)
  })

  it('表單沒有任何密碼欄；新增送 POST、預設不信任自簽、body 沒有帳密', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '新增儲存設備')!.trigger('click')
    expect(w.find('input[type=password]').exists()).toBe(false)
    expect((w.find('input[type=checkbox]').element as HTMLInputElement).checked).toBe(false)   // 第一個 checkbox＝信任自簽，預設關
    await w.find('input[aria-label=設備名稱]').setValue('unity-b')
    await w.find('input[aria-label="IP 或主機名"]').setValue('10.99.0.6')
    await w.find('select[aria-label=收集憑證]').setValue('stor-ro')
    await btn(w, '儲存')!.trigger('click'); await flushPromises()
    const post = calls.find(c => c.url === '/api/storage/devices' && c.opts?.method === 'POST')!
    expect(post.opts.body).toMatchObject({ name: 'unity-b', host: '10.99.0.6', port: 443, credential: 'stor-ro', trust_self_signed: false, vendor: 'dell_unity' })
    expect(Object.keys(post.opts.body)).not.toContain('password')
    expect(Object.keys(post.opts.body)).not.toContain('username')
  })

  it('勾「信任自簽」會出現風險提示；列表 TLS 欄標「不驗證」', async () => {
    devices = [dev({ trust_self_signed: true })]
    const w = mnt(); await flushPromises()
    expect(w.find('.tls').text()).toContain('不驗證')
    await btn(w, '編輯')!.trigger('click')
    expect(w.text()).toContain('跳過憑證驗證')
  })

  it('連線測試走 POST /test，結果顯示在畫面上；失敗訊息也顯示', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '連線測試')!.trigger('click'); await flushPromises()
    const t = calls.find(c => c.url === '/api/storage/devices/1/test')!
    expect(t.opts.method).toBe('POST')
    expect(w.text()).toContain('連線成功')
    testReply = { ok: false, message: '帳號或密碼被拒絕（HTTP 401）' }
    await btn(w, '連線測試')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('連線失敗')
    expect(w.text()).toContain('HTTP 401')
  })

  it('測試後改設定儲存：舊測試結果清掉，不套到新端點', async () => {
    testReply = { ok: false, message: '帳號或密碼被拒絕（HTTP 401）' }
    const w = mnt(); await flushPromises()
    await btn(w, '連線測試')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('HTTP 401')
    await btn(w, '編輯')!.trigger('click')
    await w.find('input[aria-label="IP 或主機名"]').setValue('10.99.0.77')
    await btn(w, '儲存')!.trigger('click'); await flushPromises()
    expect(w.text()).not.toContain('HTTP 401')
  })

  it('編輯送 PUT 且不改憑證時沿用（送出現值）；刪除走 DELETE', async () => {
    const w = mnt(); await flushPromises()
    await btn(w, '編輯')!.trigger('click')
    await btn(w, '儲存')!.trigger('click'); await flushPromises()
    const put = calls.find(c => c.url === '/api/storage/devices/1' && c.opts?.method === 'PUT')!
    expect(put.opts.body.credential).toBe('stor-ro')
    await btn(w, '刪除')!.trigger('click'); await flushPromises()
    expect(calls.some(c => c.url === '/api/storage/devices/1' && c.opts?.method === 'DELETE')).toBe(true)
  })

  it('非 admin：看得到清單，沒有新增／測試／編輯／刪除', async () => {
    admin = false
    const w = mnt(); await flushPromises()
    expect(w.find('.name').text()).toBe('unity-a')
    expect(btn(w, '新增儲存設備')).toBeUndefined()
    expect(btn(w, '連線測試')).toBeUndefined()
    expect(btn(w, '編輯')).toBeUndefined()
    expect(calls.some(c => c.url === '/api/credentials')).toBe(false)
  })

  it('說明文字收在註裡（InfoNote）：畫面主體沒有長說明句', async () => {
    const w = mnt(); await flushPromises()
    expect(w.find('.note').text()).toContain('只發 GET')
    expect(w.find('.note').text()).toContain('不代表帳密有效')
  })
})
