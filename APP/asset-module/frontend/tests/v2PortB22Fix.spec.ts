// 2.058.2（批 22 審查修正，⚠D4）：逐筆登記「標記下線」只標勾選的那幾筆（scope:'single'，同舊 /assets 批次），
// 加回舊頁的確認；重新載入後只留看得到的勾選；以及審查列出的存活突變（各守門都要會紅）。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { pruneSelected, recordFilterQuery } from '../utils/assetRecords'
import { v2LegacyTarget } from '../utils/v2LegacyMap'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let route: any
let viewer = false
let rows: any[] = []
let previewPending = false
let mailFails = false
const Table = defineComponent({ props: ['rows', 'cols', 'selectable', 'selected', 'prefix', 'unit', 'rowKey', 'server', 'exportAll', 'empty', 'max', 'headerClearsAll'], emits: ['update:selected', 'sort', 'page'], setup(p, { slots, emit }) {
  return () => h('div', { class: 'tbl', 'data-selectable': String(!!p.selectable) }, [
    h('button', { class: 'pick', onClick: () => emit('update:selected', { 'S-1': true, 'S-2': true, 'S-9': true }) }, 'pick'),
    ...(p.rows ?? []).map((r: any) => h('div', { class: 'row' }, (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(r[c.k] ?? ''))))),
  ])
} })
const ModalSpy = defineComponent({ props: { serials: Array, scope: String, lockScope: Boolean, confirmFirst: Boolean, who: String }, setup(p) { return () => h('div', { class: 'modal' }, JSON.stringify(p)) } })
const RowActionsStub = defineComponent({ emits: ['changed'], setup(_, { emit }) { return () => h('button', { class: 'ra', onClick: () => emit('changed') }, 'ra') } })
const ScopePreviewStub = defineComponent({ props: ['serials', 'scope'], emits: ['count'], setup(p, { emit }) {
  onMounted(() => { if (!previewPending) emit('count', p.serials.length) })
  return () => h('i')
} })

beforeEach(() => {
  calls = []; viewer = false; previewPending = false; mailFails = false
  rows = [{ asset_serial: 'S-1', hostname: 'web01', ip: '10.99.1.1' }, { asset_serial: 'S-2', hostname: 'web02', ip: '10.99.1.2' }]
  route = { query: {} as Record<string, any> }
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer), isAdmin: ref(!viewer), canWrite: ref(!viewer) }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: () => {}, push: () => {} }))
  const fetcher = async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url === '/api/assets') return rows
    if (url === '/api/assets/field-groups') return { hardware: { common: ['hostname', 'ip'], advanced: [] } }
    if (url === '/api/field-meta') return { categories: [], fields: {} }
    if (url === '/api/assets/facets') return { total: 2, os_type: {}, os_order: [] }
    if (url === '/api/assets/batch-status') return { updated: opts.body.serials, unchanged: [], not_found: [] }
    if (url === '/api/vuln-notify/settings' && !opts.method && mailFails) throw new Error('boom')
    if (url === '/api/v2/hosts/duplicates') return { groups: [{ no: 1, key: 'k1', hostname: 'web01', ip: '10.99.1.1', kind: 'suspect', kind_label: '疑似', count: 2, extra_rows: 1, serials: 'S-1、S-2', diff_fields: [], systems: '' }], members: [], by_kind: { suspect: 1 }, kind_labels: { suspect: '疑似' }, kind_order: ['suspect'], extra_rows: 1, dismissed_groups: 0 }
    return {}
  }
  vi.stubGlobal('useApi', () => ({ apiFetch: fetcher, apiFetchWithHeaders: async (url: string, opts: any = {}) => ({ data: await fetcher(url, opts), headers: new Headers({ 'X-Total-Count': String(rows.length) }) }) }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, DataCell: { props: ['value'], template: '<span>{{ value }}</span>' },
  RowActions: RowActionsStub, OfflineMarkModal: ModalSpy, ScopePreview: ScopePreviewStub }

async function openModalFromRecords() {
  const C = (await import('../components/v2/AssetRecords.vue')).default
  const w = mount(C, { global: { stubs } })
  await flushPromises()
  return w
}

describe('逐筆登記 → 標記下線：只標勾選的那幾筆', () => {
  it('呼叫端給 scope=single、固定範圍、先確認；勾選＝畫面上還看得到的那幾筆', async () => {
    const w = await openModalFromRecords()
    await w.find('button.pick').trigger('click')          // 勾 S-1、S-2，外加一筆看不到的 S-9
    await w.find('.ra').trigger('click'); await flushPromises()   // RowActions 改了某列 → 重新載入
    const btn = w.findAll('button').find(b => b.text() === '標記下線／報廢')!
    await btn.trigger('click'); await flushPromises()
    const p = JSON.parse(w.find('.modal').text())
    expect(p).toMatchObject({ scope: 'single', lockScope: true, confirmFirst: true })
    expect(p.serials).toEqual(['S-1', 'S-2'])               // S-9 已經不在畫面上，不能被送出
  })
  it('pruneSelected：只留這一頁的', () => {
    expect(pruneSelected({ A: true, B: false, C: true }, [{ asset_serial: 'A' }, { asset_serial: 'B' }])).toEqual({ A: true })
  })
  it('只能看的帳號：不能勾、看不到標記下線', async () => {
    viewer = true
    const w = await openModalFromRecords()
    expect(w.find('.tbl').attributes('data-selectable')).toBe('false')
    expect(w.findAll('button').some(b => b.text() === '標記下線／報廢')).toBe(false)
  })
})

describe('OfflineMarkModal', () => {
  const mk = async (props: Record<string, any>) => {
    const C = (await import('../components/OfflineMarkModal.vue')).default
    const w = mount(C, { props: { serials: ['S-1', 'S-2'], ...props }, global: { stubs } })
    await flushPromises()
    return w
  }
  const submit = async (w: any) => { await w.findAll('button').at(-1)!.trigger('click'); await flushPromises() }
  const sent = () => calls.filter(c => c.url === '/api/assets/batch-status')

  it('scope=single：body 只有勾的那幾筆＋scope single；寫「筆」；取消確認不送', async () => {
    const w = await mk({ scope: 'single', lockScope: true, confirmFirst: true })
    expect(w.text()).toContain('2 筆')
    expect(w.findAll('input[type=radio]').length).toBe(0)
    await w.find('input.in.wide').setValue('現場確認已下線')
    vi.stubGlobal('confirm', () => false)
    await submit(w)
    expect(sent().length).toBe(0)
    let asked = ''
    vi.stubGlobal('confirm', (t: string) => { asked = t; return true })
    await submit(w)
    expect(asked).toContain('把勾選的 2 筆標記「待 CIA 異動：停用」？')
    expect(sent()[0].opts).toEqual({ method: 'POST', body: { serials: ['S-1', 'S-2'], status: '停用', reason: '現場確認已下線', scope: 'single' } })
  })
  it('其他呼叫端不變：預設整台、可切換、不多問一次', async () => {
    const w = await mk({})
    expect(w.text()).toContain('2 台')
    expect(w.findAll('input[type=radio]').length).toBe(2)
    await w.find('input.in.wide').setValue('x')
    const c = vi.fn(() => true); vi.stubGlobal('confirm', c)
    await submit(w)
    expect(c).not.toHaveBeenCalled()
    expect(sent()[0].opts.body.scope).toBe('machine')
  })
  it('原因沒填不送', async () => {
    const w = await mk({ scope: 'single', confirmFirst: true })
    vi.stubGlobal('confirm', () => true)
    await submit(w)
    expect(sent().length).toBe(0)
  })
  it('預覽還沒算出筆數不送', async () => {
    previewPending = true
    const w = await mk({ scope: 'single', confirmFirst: true })
    await w.find('input.in.wide').setValue('x')
    vi.stubGlobal('confirm', () => true)
    ;(w.vm as any).submit ? await (w.vm as any).submit() : await submit(w)
    await flushPromises()
    expect(sent().length).toBe(0)
  })
})

describe('機房分組 location_group', () => {
  it('網址 → /api/assets 原樣帶；舊 /assets 網址也帶過來', () => {
    expect(recordFilterQuery({ location_group: '內湖' })).toEqual({ location_group: '內湖' })
    expect(v2LegacyTarget('/assets', { location_group: '內湖' })).toEqual({ path: '/v2/hosts', query: { tab: 'records', location_group: '內湖' } })
  })
  it('送到後端的是 location_group，不是別的鍵', async () => {
    route.query = { tab: 'records', location_group: '內湖' }
    const w = await openModalFromRecords()
    expect(calls.find(c => c.url === '/api/assets')!.opts.query).toMatchObject({ location_group: '內湖' })
    expect(w.text()).toContain('機房分組：內湖')
  })
})

describe('重複登記「不是重複」：只能看的帳號看不到', () => {
  it('viewer 沒有按鈕', async () => {
    viewer = true
    const C = (await import('../components/v2/HostDuplicates.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    expect(w.findAll('button').some(b => b.text() === '不是重複')).toBe(false)
    viewer = false
    const w2 = mount(C, { global: { stubs } }); await flushPromises()
    expect(w2.findAll('button').some(b => b.text() === '不是重複')).toBe(true)
  })
})

describe('寄信設定：讀取失敗就不能存', () => {
  it('載入失敗後直接呼叫 saveMail 也不送 PUT', async () => {
    mailFails = true
    const C = (await import('../components/v2/VulnMail.vue')).default
    const w = mount(C, { global: { stubs } })
    await flushPromises()
    await (w.vm as any).saveMail()
    await flushPromises()
    expect(calls.some(c => c.url === '/api/vuln-notify/settings' && c.opts?.method === 'PUT')).toBe(false)
  })
})
