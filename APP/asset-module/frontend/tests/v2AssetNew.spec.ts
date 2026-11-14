/**
 * 2.048.25 V21：2.1「新增主機」改 V2 原生，實掛：
 *   - 只給管理員（三個寫入都是 ADMIN_WRITE）：一般帳號看到「限管理員」、不打任何 API
 *   - 資產序號必填、依申請單轉錄時單據編號必填（都不送出）；送出的 fields 去掉空值、帶 is_vm；VM 清掉機櫃編號／硬體編號
 *   - 附件失敗分開報；網段 IP 帶入建議值、換網段舊回應不覆蓋
 *   - 掃描預設勾「未登記」，納入只送勾選的
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import AssetNew from '../components/v2/AssetNew.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
const auth = { isAdmin: true }
const toasts: string[] = []
beforeEach(() => {
  pending = []; auth.isAdmin = true; toasts.length = 0
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => toasts.push(m) }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(auth.isAdmin), canWrite: ref(true), isViewer: ref(false) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'selected'], emits: ['update:selected'], setup(p, { emit }) {
  return () => h('div', { class: 'tbl' }, (p.rows ?? []).map((r: any) => h('div', { class: 'row', 'data-ip': r.ip },
    [h('input', { type: 'checkbox', class: 'pick', checked: !!p.selected?.[r.ip], onChange: (e: any) => emit('update:selected', { ...p.selected, [r.ip]: e.target.checked }) }),
      ...(p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, String(c.text ? c.text(r) : r[c.k] ?? '')))])))
} })
const Sel = defineComponent({ props: ['modelValue', 'options', 'placeholder'], emits: ['update:modelValue'], setup(p, { emit }) {
  return () => h('select', { 'data-ph': p.placeholder, value: p.modelValue, onChange: (e: any) => emit('update:modelValue', e.target.value) },
    [h('option', { value: '' }, ''), ...(p.options ?? []).map((o: string) => h('option', { value: o }, o))])
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': p.to }, slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: Link, SearchableSelect: Sel, ClientOnly: pass } } }
const MAPPING = { 硬體: { 資產序號: 'asset_serial', 主機名稱: 'hostname', IP: 'ip', 機櫃編號: 'rack_no', 硬體編號: 'hardware_no', 作業系統: 'os' } }
const TREE = { tree: [{ location: '板橋', environments: [{ environment: '正式', segments: [
  { cidr: '10.99.1.0/24', label: 'A段', category: null, usage: null, scan_excluded: false, scan_note: null },
  { cidr: '10.99.2.0/24', label: 'B段', category: null, usage: null, scan_excluded: false, scan_note: null }] }] }] }
const reply = (p: any) => ({ '/api/import/field-mapping': MAPPING, '/api/assets/manual/field-options': {}, '/api/os-catalog': {},
  '/api/assets/manual/field-groups': { groups: [] }, '/api/segments/tree': TREE } as Record<string, any>)[p.url] ?? {}
const flush = async (fn: (p: any) => any = reply) => { await flushPromises(); for (const p of pending.splice(0)) p.resolve(fn(p)); await flushPromises() }
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().startsWith(t))
const field = (w: any, label: string) => w.findAll('label.fld').find((l: any) => l.find('.lbl').text().startsWith(label))!.find('input')

describe('新增主機（V21）', () => {
  it('一般帳號：只看到「限管理員」，不打任何 API', async () => {
    auth.isAdmin = false
    const w = mount(AssetNew, opts)
    await flushPromises()
    expect(w.text()).toContain('新增主機限管理員')
    expect(pending.length).toBe(0)
    expect(btn(w, '新增這筆資產')).toBeUndefined()
  })
  it('序號必填、依申請單時單據編號必填，都不送出；VM 清掉機櫃／硬體編號；送出帶 is_vm、去空值', async () => {
    const w = mount(AssetNew, opts)
    await flush()
    await btn(w, '新增這筆資產').trigger('click')
    expect(w.text()).toContain('資產序號必填')
    await field(w, '資產序號').setValue('S100')
    await field(w, '機櫃編號').setValue('R1')
    await w.find('input[value="form"]').setValue(true)
    await btn(w, '新增這筆資產').trigger('click')
    expect(w.text()).toContain('依申請單轉錄時，單據編號必填')
    expect(pending.some(p => p.url === '/api/assets/manual')).toBe(false)
    await w.find('input[value="direct"]').setValue(true)
    await w.find('.vm input').setValue(true)
    await flushPromises()
    expect(w.findAll('label.fld').some((l: any) => l.text().startsWith('機櫃編號'))).toBe(false)
    await btn(w, '新增這筆資產').trigger('click')
    await flushPromises()
    const post = pending.find(p => p.url === '/api/assets/manual')!
    expect(post.opts.body).toEqual({ fields: { asset_serial: 'S100', is_vm: '1' }, provision: { source: 'direct' } })
  })
  it('附件失敗分開報；成功後連到 V2 上線檢查表', async () => {
    const w = mount(AssetNew, opts)
    await flush()
    await w.find('input[value="form"]').setValue(true)
    await field(w, '資產序號').setValue('S7')
    await field(w, '單據編號').setValue('ES1')
    const f = w.find('input[type="file"]')
    Object.defineProperty(f.element, 'files', { value: [new File(['x'], 'a.pdf')], configurable: true })
    await f.trigger('change')
    await btn(w, '新增這筆資產').trigger('click')
    await flushPromises()
    pending.splice(0).find(p => p.url === '/api/assets/manual')!.resolve({ hardware: { asset_serial: 'S7' }, golive: { total: 5, done: 2 } })
    await flushPromises()
    const att = pending.splice(0).find(p => p.url === '/api/assets/S7/provision-attachment')!
    att.reject({ data: { detail: 'x' } })
    await flushPromises()
    expect(toasts).toContain('資產已建立，但申請單附件上傳失敗，請到資產詳細頁重傳')
    expect(w.find('a[data-to="/v2/golive/S7"]').exists()).toBe(true)
  })
  it('網段：選段帶入建議 IP；換段後舊回應不覆蓋', async () => {
    const w = mount(AssetNew, opts)
    await flush()
    await w.find('select[data-ph="機房"]').setValue('板橋')
    await w.find('select[data-ph="環境"]').setValue('正式')
    await w.find('select[data-ph="網段"]').setValue('10.99.1.0/24')
    await flushPromises()
    const old = pending.find(p => p.url === '/api/segments/ips')!
    expect(old.opts.query).toEqual({ cidr: '10.99.1.0/24' })
    await w.find('select[data-ph="網段"]').setValue('10.99.2.0/24')
    await flushPromises()
    const cur = pending.find(p => p.url === '/api/segments/ips' && p !== old)!
    cur.resolve({ used: [], suggestion: '10.99.2.5', capacity: 254 })
    old.resolve({ used: [], suggestion: '10.99.1.9', capacity: 254 })
    await flushPromises()
    expect((w.find('input[aria-label="IP"]').element as HTMLInputElement).value).toBe('10.99.2.5')
  })
  it('掃描預設勾未登記；納入只送勾選的', async () => {
    const w = mount(AssetNew, opts)
    await flush()
    await w.find('input[aria-label="網段"]').setValue('10.99.3.0/24')
    await btn(w, '開始掃描').trigger('click')
    await flushPromises()
    const d = pending.splice(0).find(p => p.url === '/api/assets/scan/discover')!
    expect(d.opts.body).toEqual({ cidr: '10.99.3.0/24' })
    d.resolve({ cidr: '10.99.3.0/24', found: [
      { ip: '10.99.3.2', hostname: 'a', open_ports: ['22'], already_registered: false, existing_asset_serial: null },
      { ip: '10.99.3.3', hostname: 'b', open_ports: ['445'], already_registered: true, existing_asset_serial: 'S3' },
      { ip: '10.99.3.4', hostname: null, open_ports: ['22'], already_registered: false, existing_asset_serial: null }] })
    await flushPromises()
    expect(btn(w, '納入勾選的').text()).toBe('納入勾選的 2 台')
    await w.find('.row[data-ip="10.99.3.4"] .pick').setValue(false)
    await btn(w, '納入勾選的').trigger('click')
    await flushPromises()
    expect(pending.find(p => p.url === '/api/assets/scan/import')!.opts.body).toEqual({ hosts: [{ ip: '10.99.3.2', hostname: 'a' }] })
  })
  it('<style> 編譯沒有錯', async () => {
    const { parse, compileStyle } = await import('@vue/compiler-sfc')
    const fs = await import('node:fs')
    const d = parse(fs.readFileSync('components/v2/AssetNew.vue', 'utf8')).descriptor
    for (const s of d.styles) expect(compileStyle({ source: s.content, filename: 'AssetNew', id: 'data-v-x', scoped: true }).errors).toEqual([])
  })
})

describe('第 13 批審查補測', () => {
  it('網段表讀不到：欄位照樣出來，另外提示部分選單讀不到', async () => {
    const w = mount(AssetNew, opts)
    await flushPromises()
    for (const p of pending.splice(0)) { if (p.url === '/api/segments/tree') p.reject({ data: { detail: 'x' } }); else p.resolve(reply(p)) }
    await flushPromises()
    expect(w.findAll('label.fld').some((l: any) => l.find('.lbl').text().startsWith('資產序號'))).toBe(true)
    expect(w.text()).toContain('部分選單讀不到（網段表）')
  })
  it('建立成功後清空表單；納入成功後清掉掃描結果與網段框', async () => {
    const w = mount(AssetNew, opts)
    await flush()
    await field(w, '資產序號').setValue('S9')
    await btn(w, '新增這筆資產').trigger('click')
    await flushPromises()
    pending.splice(0).find(p => p.url === '/api/assets/manual')!.resolve({ hardware: { asset_serial: 'S9' }, golive: { total: 1, done: 0 } })
    await flushPromises()
    expect((field(w, '資產序號').element as HTMLInputElement).value).toBe('')
    await w.find('input[aria-label="網段"]').setValue('10.99.3.0/24')
    await btn(w, '開始掃描').trigger('click')
    await flushPromises()
    pending.splice(0).find(p => p.url === '/api/assets/scan/discover')!.resolve({ cidr: '', found: [{ ip: '10.99.3.2', hostname: 'a', open_ports: ['22'], already_registered: false, existing_asset_serial: null }] })
    await flushPromises()
    await btn(w, '納入勾選的').trigger('click')
    await flushPromises()
    pending.splice(0).find(p => p.url === '/api/assets/scan/import')!.resolve({ inserted: 1, updated: 0, errors: [] })
    await flushPromises()
    expect(w.find('.tbl').exists()).toBe(false)
    expect((w.find('input[aria-label="網段"]').element as HTMLInputElement).value).toBe('')
  })
  it('管理員身分晚一步才確定：變成管理員那一刻才載，只載一次', async () => {
    const adm = ref(false)
    vi.stubGlobal('useAuth', () => ({ isAdmin: adm, canWrite: ref(true), isViewer: ref(false) }))
    const w = mount(AssetNew, opts)
    await flushPromises()
    expect(pending.length).toBe(0)
    adm.value = true
    await flushPromises()
    expect(pending.filter(p => p.url === '/api/import/field-mapping').length).toBe(1)
    adm.value = false; await flushPromises(); adm.value = true; await flushPromises()
    expect(pending.filter(p => p.url === '/api/import/field-mapping').length).toBe(1)
    expect(w.text()).not.toContain('新增主機限管理員')
  })
})
