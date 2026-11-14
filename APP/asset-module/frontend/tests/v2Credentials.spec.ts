// 2.056.0（V56 第 1 批，⚠D5）：⚙ 收集憑證實掛——密碼只在送出那一次存在：送完（成功或失敗）畫面上就沒有；
// 清單只顯示 metadata（後端也不回密碼）；編輯時密碼留空＝送 null（不改）。V2Table／InfoNote 為簡化替身，全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/Credentials.vue'

enableAutoUnmount(afterEach)
const FAKE_PW = 'FAKE-TEST-PW-not-real-4412'
const LIST = [{ id: 1, name: 'wug-a', kind: 'whatsup', username: 'svc_x', scope: null, note: null, updated_at: '2026-11-02 09:00:00', has_secret: true }]
let calls: { url: string; opts: any }[] = []
let failSave = false
const Table = defineComponent({ props: ['creds'], emits: ['edit', 'test', 'remove'], setup(p, { emit }) {
  return () => h('div', { class: 'tbl' }, (p.creds ?? []).map((c: any) => h('button', { class: 'edit', onClick: () => emit('edit', c) }, `${c.name} ${c.username} ${c.has_secret ? '已加密儲存' : ''}`)))
} })
beforeEach(() => {
  calls = []; failSave = false
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if ((opts.method === 'POST' || opts.method === 'PUT') && failSave) throw { data: { detail: '失敗' } }
    return url === '/api/credentials' && !opts.method ? LIST : {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())
const mnt = async () => {
  const w = mount(Comp, { global: { stubs: { InfoNote: { template: '<i />' }, V2CredentialsTable: Table } } })
  await flushPromises()
  return w
}
const pw = (w: any) => w.find('input[type="password"]')

describe('收集憑證', () => {
  it('新增：密碼送出一次，送完畫面上沒有', async () => {
    const w = await mnt()
    const inputs = w.findAll('input')
    await inputs[0].setValue('c1'); await inputs[1].setValue('svc_a'); await pw(w).setValue(FAKE_PW)
    await w.find('form').trigger('submit')
    await flushPromises()
    const post = calls.find(c => c.opts.method === 'POST')!
    expect(post.url).toBe('/api/credentials')
    expect(post.opts.body.password).toBe(FAKE_PW)
    expect((pw(w).element as HTMLInputElement).value).toBe('')
    expect(w.html()).not.toContain(FAKE_PW)
  })
  it('儲存失敗也清掉密碼', async () => {
    failSave = true
    const w = await mnt()
    const inputs = w.findAll('input')
    await inputs[0].setValue('c1'); await inputs[1].setValue('svc_a'); await pw(w).setValue(FAKE_PW)
    await w.find('form').trigger('submit')
    await flushPromises()
    expect((pw(w).element as HTMLInputElement).value).toBe('')
    expect(w.html()).not.toContain(FAKE_PW)
  })
  it('編輯：密碼留空＝送 null（不改），名稱不能改', async () => {
    const w = await mnt()
    await w.find('button.edit').trigger('click')
    expect((w.findAll('input')[0].element as HTMLInputElement).disabled).toBe(true)
    await w.find('form').trigger('submit')
    await flushPromises()
    const put = calls.find(c => c.opts.method === 'PUT')!
    expect(put.url).toBe('/api/credentials/wug-a')
    expect(put.opts.body.password).toBeNull()
  })
})
