// AD LDAPS 每日同步（ad_ldap）開著時，資料匯入頁手動上傳 AD 名單前要先警告、勾選確認才送；關著不顯示。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
const stubs = { V2Table: true, NuxtLink: { props: ['to'], template: '<a />' }, InfoNote: { template: '<i />' }, V2ImportExports: { template: '<span />' } }

beforeEach(() => {
  calls = []
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: async () => ({}) }))
  vi.stubGlobal('httpReason', async () => 'x')
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/ad/import') return { rows: 3, enabled_n: 3, disabled_n: 0, mgr_ok: 3 }
    if (url === '/api/ad/disabled-with-account') return { items: [] }
    return null
  } }))
})
afterEach(() => vi.unstubAllGlobals())

const WARN = 'AD 每日同步已開啟，手動匯入的名單會在下一次同步被覆蓋；確定要手動匯入嗎？'
async function mountAd(adSummary: any) {
  const C = (await import('../components/v2/ImportOne.vue')).default
  const w = mount(C, { props: { kind: 'ad', src: undefined, apiBase: '', adSummary }, global: { stubs } })
  await flushPromises()
  return w
}
async function pickCsv(w: any) {
  const inp = w.find('input[type=file]')
  Object.defineProperty(inp.element, 'files', { value: [new File(['x'], 'ad.csv')], configurable: true })
  await inp.trigger('change'); await flushPromises()
}
const importCalls = () => calls.filter(c => c.url === '/api/ad/import')

describe('AD 手動匯入：同步開著的警告', () => {
  it('開啟：顯示警告全文與確認勾選；沒勾選時「匯入」鈕不能按、直接觸發也不送', async () => {
    const w = await mountAd({ total: 1, auto_sync_warning: 'AD 自動同步（LDAPS）已開啟' })
    expect(w.find('[data-testid=ad-sync-warning]').text()).toContain(WARN)
    await pickCsv(w)
    const btn = w.find('button.btn')
    expect(btn.attributes('disabled')).toBeDefined()
    await btn.trigger('click'); await flushPromises()
    expect(importCalls()).toHaveLength(0)
  })

  it('勾選確認後才送；送完確認狀態清掉（下一份檔要重新確認）', async () => {
    const w = await mountAd({ total: 1, auto_sync_warning: 'x' })
    await pickCsv(w)
    await w.find('[data-testid=ad-overwrite-confirm]').setValue(true)
    const btn = w.find('button.btn')
    expect(btn.attributes('disabled')).toBeUndefined()
    await btn.trigger('click'); await flushPromises()
    expect(importCalls()).toHaveLength(1)
    expect((w.find('[data-testid=ad-overwrite-confirm]').element as HTMLInputElement).checked).toBe(false)
    await pickCsv(w)
    expect(w.find('button.btn').attributes('disabled')).toBeDefined()
  })

  it('重新選檔會清掉先前的勾選', async () => {
    const w = await mountAd({ total: 1, auto_sync_warning: 'x' })
    await pickCsv(w)
    await w.find('[data-testid=ad-overwrite-confirm]').setValue(true)
    await pickCsv(w)
    expect((w.find('[data-testid=ad-overwrite-confirm]').element as HTMLInputElement).checked).toBe(false)
    expect(w.find('button.btn').attributes('disabled')).toBeDefined()
  })

  it.each([['載入中（adSummary 還是 null）', null, '正在確認'], ['讀取失敗（__error）', { __error: true }, '無法確認 AD 同步狀態']])(
    '%s：不能當成「關閉」——一樣要警告、勾選確認才送', async (_n, summary, text) => {
      const w = await mountAd(summary)
      expect(w.find('[data-testid=ad-sync-warning]').text()).toContain(text)
      await pickCsv(w)
      expect(w.find('button.btn').attributes('disabled')).toBeDefined()
      await w.find('button.btn').trigger('click'); await flushPromises()
      expect(importCalls()).toHaveLength(0)
      await w.find('[data-testid=ad-overwrite-confirm]').setValue(true)
      await w.find('button.btn').trigger('click'); await flushPromises()
      expect(importCalls()).toHaveLength(1)
    })

  it('同步狀態變動（讀到明確開啟）：清掉已選檔案與確認勾選', async () => {
    const w = await mountAd(null)
    await pickCsv(w)
    await w.find('[data-testid=ad-overwrite-confirm]').setValue(true)
    await w.setProps({ adSummary: { total: 1, auto_sync_warning: 'x' } }); await flushPromises()
    expect((w.find('[data-testid=ad-overwrite-confirm]').element as HTMLInputElement).checked).toBe(false)
    expect(w.find('button.btn').attributes('disabled')).toBeDefined()
  })

  it('切換來源（kind）：清掉已選檔案與確認勾選', async () => {
    const w = await mountAd({ total: 1, auto_sync_warning: 'x' })
    await pickCsv(w)
    await w.find('[data-testid=ad-overwrite-confirm]').setValue(true)
    await w.setProps({ kind: 'segments' }); await flushPromises()
    await w.setProps({ kind: 'ad' }); await flushPromises()
    expect((w.find('[data-testid=ad-overwrite-confirm]').element as HTMLInputElement).checked).toBe(false)
    expect(w.find('button.btn').attributes('disabled')).toBeDefined()
  })

  it('關閉（沒有 auto_sync_warning）：不顯示警告與勾選，選檔後直接可以匯入', async () => {
    const w = await mountAd({ total: 1, auto_sync_warning: null })
    expect(w.find('[data-testid=ad-sync-warning]').exists()).toBe(false)
    expect(w.find('[data-testid=ad-overwrite-confirm]').exists()).toBe(false)
    await pickCsv(w)
    const btn = w.find('button.btn')
    expect(btn.attributes('disabled')).toBeUndefined()
    await btn.trigger('click'); await flushPromises()
    expect(importCalls()).toHaveLength(1)
  })
})
