// 2.056.1（V56 第 2 批，⚠D5）：⚙ 連線設定實掛——密碼不預填、有輸入才送、送完清空、畫面不出現密碼；刪除要先確認。
// 另驗收集憑證刪除前的確認（取消＝不打 DELETE）。V2Table／InfoNote 為簡化替身，全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Conn from '../components/v2/Connections.vue'
import Cred from '../components/v2/Credentials.vue'

enableAutoUnmount(afterEach)
const FAKE_PW = 'FAKE-TEST-PW-not-real-5521'
const CONNS = [{ id: 3, name: 'scan-a', connection_type: '網路掃描', target: '10.99.9.0/24', port: 22, username: 'svc_x', has_password: true, last_status: '綠', enabled: 1 }]
let calls: { url: string; opts: any }[] = []
const Table = defineComponent({ props: ['rows', 'cols'], setup(p, { slots }) {
  return () => h('table', {}, (p.rows ?? []).map((r: any) => h('tr', {}, (p.cols ?? []).map((c: any) =>
    h('td', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const CredTable = defineComponent({ props: ['creds'], emits: ['remove'], setup(p, { emit }) {
  return () => h('div', {}, (p.creds ?? []).map((c: any) => h('button', { class: 'del', onClick: () => emit('remove', c.name) }, c.name)))
} })
beforeEach(() => {
  calls = []
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (!opts.method && url === '/api/connections') return CONNS.map(c => ({ ...c }))
    if (!opts.method && url === '/api/credentials') return [{ id: 1, name: 'wug-a', kind: 'whatsup', username: 'svc', has_secret: true }]
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, V2CredentialsTable: CredTable }

describe('連線設定', () => {
  it('密碼不預填；沒輸入就不送 password；有輸入才送，送完清空、畫面不出現', async () => {
    const w = mount(Conn, { global: { stubs } })
    await flushPromises()
    const pw = () => w.find('td.c-pw input[type="password"]')
    expect((pw().element as HTMLInputElement).value).toBe('')
    const save = () => w.findAll('td.c-ops button').find(b => b.text() === '儲存')!
    await save().trigger('click'); await flushPromises()
    expect(calls.filter(c => c.opts.method === 'PUT').at(-1)!.opts.body).not.toHaveProperty('password')
    await pw().setValue(FAKE_PW)
    await save().trigger('click'); await flushPromises()
    const put = calls.filter(c => c.opts.method === 'PUT').at(-1)!
    expect(put.url).toBe('/api/connections/3')
    expect(put.opts.body.password).toBe(FAKE_PW)
    expect((pw().element as HTMLInputElement).value).toBe('')
    expect(w.html()).not.toContain(FAKE_PW)
  })
  it('2.056.5：帳號原本沒填（null），儲存時照舊送 null，不是空字串', async () => {
    CONNS[0].username = null as any
    try {
      const w = mount(Conn, { global: { stubs } })
      await flushPromises()
      await w.findAll('td.c-ops button').find(b => b.text() === '儲存')!.trigger('click'); await flushPromises()
      expect(calls.filter(c => c.opts.method === 'PUT').at(-1)!.opts.body.username).toBeNull()
    } finally { CONNS[0].username = 'svc_x' }
  })
  it('刪除：取消確認就不打 DELETE', async () => {
    vi.stubGlobal('confirm', () => false)
    const w = mount(Conn, { global: { stubs } })
    await flushPromises()
    await w.findAll('td.c-ops button').find(b => b.text() === '刪除')!.trigger('click'); await flushPromises()
    expect(calls.some(c => c.opts.method === 'DELETE')).toBe(false)
  })
})

describe('收集憑證刪除確認（2.056.1）', () => {
  it('取消＝不打 DELETE；確定才打', async () => {
    let answer = false
    vi.stubGlobal('confirm', () => answer)
    const w = mount(Cred, { global: { stubs } })
    await flushPromises()
    await w.find('button.del').trigger('click'); await flushPromises()
    expect(calls.some(c => c.opts.method === 'DELETE')).toBe(false)
    answer = true
    await w.find('button.del').trigger('click'); await flushPromises()
    expect(calls.find(c => c.opts.method === 'DELETE')!.url).toBe('/api/credentials/wug-a')
  })
})
