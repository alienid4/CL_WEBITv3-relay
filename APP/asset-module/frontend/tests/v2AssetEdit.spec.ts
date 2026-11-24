// 2.055.1（批 19 審查 P2-3）：真的掛資產編輯表單（components/v2/AssetEdit.vue），驗行為不驗字串：
//   · 只能看的帳號看不到表單（M6）
//   · 主鍵、建立／更新時間不在表單、也不會送出去（M1c）
//   · 按「取消」只送 cancel，不會打 PUT（M9）；按「儲存」才打一次 PUT，body 規則跟舊頁一樣
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, reactive, ref, Suspense } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssetEdit from '../components/v2/AssetEdit.vue'

enableAutoUnmount(afterEach)
const HW = { id: 7, asset_serial: 'HW-T-1', hostname: 'fake01', ip: '10.99.1.1', asset_status: '使用中', pending_status: null, remark: 'old', created_at: '2026-01-01', updated_at: '2026-01-02' }
const FG = { hardware: { common: ['asset_serial', 'hostname', 'ip', 'asset_status', 'created_at', 'updated_at', 'id'], advanced: ['remark'] } }
let calls: { url: string; opts: any }[] = []
let viewer = false
beforeEach(() => {
  calls = []; viewer = false
  for (const [k, v] of Object.entries({ ref, reactive, computed })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/assets/field-groups') return FG
    if (url === '/api/field-meta') return { fields: {} }
    if (opts.method === 'PUT') return { hardware: { ...HW, ...opts.body.fields } }
    return { hardware: HW }
  } }))
})
afterEach(() => vi.unstubAllGlobals())

async function mnt() {
  const Host = defineComponent({ setup: (_, { emit }) => () => h(Suspense, null, { default: () => h(AssetEdit, { serial: 'HW-T-1', onSaved: () => emit('saved'), onCancel: () => emit('cancel') }) }) })
  const w = mount(Host, { global: { stubs: { InfoNote: { template: '<i />' }, SearchableSelect: { template: '<i />' } } } })
  await flushPromises()
  return w
}
const puts = () => calls.filter(c => c.opts?.method === 'PUT')

describe('資產編輯表單', () => {
  it('只能看的帳號：沒有表單、沒有儲存鈕', async () => {
    viewer = true
    const w = await mnt()
    expect(w.find('form').exists()).toBe(false)
    expect(w.text()).toContain('編輯限一般帳號以上')
  })
  it('主鍵與建立／更新時間不在表單裡', async () => {
    const w = await mnt()
    expect(w.find('form').exists()).toBe(true)
    for (const k of ['asset_serial', 'id', 'created_at', 'updated_at']) expect(w.find(`#ae-${k}`).exists()).toBe(false)
    expect(w.find('#ae-hostname').exists()).toBe(true)
  })
  it('取消：不打 PUT', async () => {
    const w = await mnt()
    const cancel = w.findAll('button').find(b => b.text() === '取消')!
    await cancel.trigger('click')
    await flushPromises()
    expect(puts()).toHaveLength(0)
    expect(w.emitted('cancel')).toBeTruthy()
  })
  it('儲存：打一次 PUT；不送主鍵、建立／更新時間；資產狀態沒改不送', async () => {
    const w = await mnt()
    await w.find('#ae-remark').setValue('new')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(puts()).toHaveLength(1)
    const f = puts()[0].opts.body.fields
    expect(puts()[0].url).toBe('/api/assets/HW-T-1')
    expect(f.remark).toBe('new')
    for (const k of ['asset_serial', 'id', 'created_at', 'updated_at', 'asset_status']) expect(f).not.toHaveProperty(k)
    expect(w.emitted('saved')).toBeTruthy()
  })
})
