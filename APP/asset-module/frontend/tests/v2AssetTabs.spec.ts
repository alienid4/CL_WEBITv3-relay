/**
 * 2.048.27 V20 第 1 批：資產明細的「服務／帳號／弱點」三個分頁搬成 V2 元件，實掛：
 *   - 拿不到不可以顯示成 0 筆：哪一支失敗就講哪一支，其他照常顯示；分頁標題筆數用 state 事件
 *   - 服務：Windows 拆子頁籤、預設藏「微軟內建沒在跑」、搜尋搜全部、埠連到 V2 服務清單；reload 重抓
 *   - 帳號：Windows 用 Windows 欄位（含沒收到帳號但平台是 Windows）、群組下鑽、0 筆講是哪一種
 *   - 弱點：三種狀況分得出來；未結案照修補期限、已結案照結案日期，項次固定
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssetServices from '../components/v2/AssetServices.vue'
import AssetAccounts from '../components/v2/AssetAccounts.vue'
import AssetVulns from '../components/v2/AssetVulns.vue'

let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
beforeEach(() => {
  pending = []
  for (const [k, v] of Object.entries({ ref, computed, watch, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

// V2Table 替身：照 rows 順序畫，有 cell 插槽就用插槽，列上標 __no
const V2Table = defineComponent({ props: ['rows', 'cols', 'empty'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl' }, (p.rows ?? []).length ? (p.rows ?? []).map((r: any) => h('div', { class: 'row', 'data-no': r.__no },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))) : [h('i', { class: 'empty' }, p.empty)])
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { 'data-to': JSON.stringify(p.to) }, slots.default?.()) } })
const opts = (props: any) => ({ props, global: { components: { V2Table, InfoNote: pass, NuxtLink: Link } } })
const answer = async (fn: (p: any) => any) => { await flushPromises(); for (const p of pending.splice(0)) { const v = fn(p); if (v instanceof Error) p.reject({ statusCode: 500, data: { detail: v.message } }); else p.resolve(v) } await flushPromises() }

describe('服務分頁', () => {
  const WIN = [
    { id: 1, name: 'AppSvc', display_name: 'App', state: 'running', start_mode: 'auto', account: 'DOM\\svc', account_kind: 'domain', is_microsoft: false, ports: ['8080/tcp'], gone_at: null },
    { id: 2, name: 'Spooler', display_name: 'Print', state: 'stopped', start_mode: 'manual', account: 'LocalSystem', is_microsoft: true, gone_at: null },
  ]
  it('一支失敗只講那一支，其他照常顯示；state 帶筆數', async () => {
    const w = mount(AssetServices, opts({ serial: 'S1' }))
    await answer(p => (p.url === '/api/services' ? new Error('壞了') : p.url === '/api/services/windows' ? { items: WIN } : { state: 'none', sites: [], pools: [] }))
    expect(w.text()).toContain('服務資料載入失敗——監聽埠：HTTP 500：壞了')
    expect(w.text()).not.toContain('Windows 服務：')
    expect(w.findAll('.row').length).toBe(1)              // Spooler（微軟內建、沒在跑）預設藏起來
    const last = (w.emitted('state') as any[]).at(-1)[0]
    expect(last).toEqual({ loaded: true, err: expect.stringContaining('監聽埠'), n: 2, iisSites: 0 })
  })
  it('勾「顯示全部」才列微軟內建；搜尋時一律搜全部；埠連到 V2 服務清單', async () => {
    const w = mount(AssetServices, opts({ serial: 'S1' }))
    await answer(p => (p.url === '/api/services/windows' ? { items: WIN } : p.url === '/api/services' ? { items: [] } : { state: 'none', sites: [], pools: [] }))
    expect(w.text()).toContain('顯示全部（含 1 個沒在跑的微軟內建）')
    await w.find('input.q').setValue('spool')
    expect(w.findAll('.row').length).toBe(1)
    expect(w.text()).toContain('Spooler')
    await w.find('input.q').setValue('')
    await w.find('label.all input').setValue(true)
    expect(w.findAll('.row').length).toBe(2)
    const a = w.findAll('a').find(x => x.text() === '8080/tcp')!
    expect(JSON.parse(a.attributes('data-to')!)).toEqual({ path: '/v2/inventory/service', query: { tab: 'list', port: '8080' } })
  })
  it('Windows 有監聽埠時拆子頁籤；Linux 只有監聽埠表、項次照 API 順序', async () => {
    const w = mount(AssetServices, opts({ serial: 'S1' }))
    await answer(p => (p.url === '/api/services' ? { items: [{ id: 9, port: 22, proto: 'tcp', guess_source: 'process', exposure: 'all', bind_addr: '0.0.0.0', last_seen: 't', gone_at: null }] }
      : p.url === '/api/services/windows' ? { items: [] } : { state: 'none', sites: [], pools: [] }))
    expect(w.findAll('button.chip').length).toBe(0)
    expect(w.find('.row').attributes('data-no')).toBe('1')
    expect(w.text()).toContain('確定')
    expect(w.text()).toContain('對外')
  })
  it('全部空：講「還沒採集過」並連到 V2 服務盤點；reload 變了就重抓三支', async () => {
    const w = mount(AssetServices, opts({ serial: 'S1', reload: 0 }))
    await answer(p => (p.url === '/api/services/iis' ? { state: 'none', sites: [], pools: [] } : { items: [] }))
    expect(w.text()).toContain('這台還沒採集過服務')
    await w.setProps({ reload: 1 })
    await flushPromises()
    expect(pending.map(p => p.url).sort()).toEqual(['/api/services', '/api/services/iis', '/api/services/windows'])
    expect(pending.find(p => p.url === '/api/services')!.opts.query).toEqual({ asset_serial: 'S1', include_infra: true, sort_by: 'port' })
  })
})

describe('帳號分頁', () => {
  const EMPTY = { title: '尚未收集帳號', detail: '這台還沒成功納管', at: null }
  it('載入失敗講原因，不是 0 個；reload 成功後錯誤清掉', async () => {
    const w = mount(AssetAccounts, opts({ serial: 'S1', empty: EMPTY, reload: 0 }))
    await answer(() => new Error('x'))
    expect(w.text()).toContain('帳號載入失敗：HTTP 500：x')
    expect((w.emitted('state') as any[]).at(-1)[0]).toMatchObject({ loaded: true, n: 0, err: expect.stringContaining('帳號載入失敗') })
    await w.setProps({ reload: 1 })
    await answer(() => ({ items: [{ username: 'root', uid: 0, gid: 0, is_sudoer: 1 }] }))
    expect(w.text()).not.toContain('帳號載入失敗')
    expect(w.text()).toContain('root')
  })
  it('0 筆：顯示資產頁算好的「是哪一種」', async () => {
    const w = mount(AssetAccounts, opts({ serial: 'S1', empty: EMPTY }))
    await answer(() => ({ items: [] }))
    expect(w.text()).toContain('尚未收集帳號')
    expect(w.text()).toContain('這台還沒成功納管')
  })
  it('Windows：Windows 欄位、點群組只看那個群組、再點取消', async () => {
    const w = mount(AssetAccounts, opts({ serial: 'S1', empty: EMPTY }))
    await answer(() => ({ items: [
      { username: 'admin', os_family: 'windows', local_groups: 'Administrators', is_sudoer: 1, can_login: 1, pw_max_days: '99999', sid: 'S-1-5-21-1-2-500' },
      { username: 'bob', os_family: 'windows', local_groups: 'Users', is_sudoer: 0, can_login: 0, pw_max_days: '42' }] }))
    expect(w.find('.c-w_source').exists()).toBe(true)
    expect(w.find('.c-shell').exists()).toBe(false)
    expect(w.text()).toContain('…-500')
    await w.findAll('button.grp').find(b => b.text() === 'Users')!.trigger('click')
    expect(w.findAll('.row').map(r => r.attributes('data-no'))).toEqual(['2'])
    await w.findAll('button.grp').find(b => b.text() === 'Users')!.trigger('click')
    expect(w.findAll('.row').length).toBe(2)
  })
  it('有收到帳號就照帳號本身判（平台寫 Windows 但收到的是 Linux 帳號 → Linux 欄位）', async () => {
    const w = mount(AssetAccounts, opts({ serial: 'S1', empty: EMPTY, platform: 'windows', lastCollected: '2026-11-01 08:00' }))
    await answer(() => ({ items: [{ username: 'u', uid: 1, gid: 1 }] }))
    expect(w.find('input.q').attributes('placeholder')).toContain('shell')
    expect(w.find('.c-uid').text()).toBe('1 / 1')
    expect(w.text()).toContain('最後收集：2026-11-01 08:00')
    expect(pending.length).toBe(0)
  })
  it('帳號照帳號名排（項次靠這個固定）', async () => {
    mount(AssetAccounts, opts({ serial: 'S9', empty: EMPTY }))
    await flushPromises()
    expect(pending[0].opts.query).toEqual({ asset_serial: 'S9', sort_by: 'username' })
  })
})

describe('弱點分頁', () => {
  it('沒報告／不在報告裡／失敗，三種講法不同', async () => {
    let w = mount(AssetVulns, opts({ serial: 'S1' }))
    await answer(() => ({ has_report: false, in_report: false, batch: null, open: [], closed: [] }))
    expect(w.text()).toContain('全站還沒匯入過弱點報告')
    expect(JSON.parse(w.find('a').attributes('data-to')!)).toBe('/v2/patch')
    w = mount(AssetVulns, opts({ serial: 'S1' }))
    await answer(() => ({ has_report: true, in_report: false, batch: { file_name: 'v.xlsx', imported_at: 'd' }, open: [], closed: [] }))
    expect(w.text()).toContain('這一台不在報告裡')
    w = mount(AssetVulns, opts({ serial: 'S1' }))
    await answer(() => new Error('掛了'))
    expect(w.text()).toContain('弱點載入失敗：HTTP 500：掛了')
    expect((w.emitted('state') as any[]).at(-1)[0]).toEqual({ loaded: true, err: expect.any(String), open: 0, closed: 0 })
  })
  it('未結案照修補期限排、項次照這個順序；已結案照結案日期；state 帶兩個數', async () => {
    const w = mount(AssetVulns, opts({ serial: 'S1' }))
    await answer(() => ({ has_report: true, in_report: true, batch: { file_name: 'v.xlsx', imported_at: 'd' },
      open: [{ id: 1, name: 'A', due_date: '2026-12-01', severity: 'High', protocol: 'tcp', port: '443' }, { id: 2, name: 'B', due_date: '2026-01-01', severity: 'Low', overdue: '逾期 3 天' }],
      closed: [{ id: 3, name: 'C', closed_at: '2026-05-01' }] }))
    const tbls = w.findAll('.tbl')
    expect(tbls[0].findAll('.c-name').map(x => x.text())).toEqual(['B', 'A'])
    expect(tbls[0].findAll('.row').map(r => r.attributes('data-no'))).toEqual(['1', '2'])
    expect(tbls[0].text()).toContain('tcp/443')
    expect(tbls[0].find('.c-overdue .bad').exists()).toBe(true)
    expect(tbls[1].findAll('.c-name').map(x => x.text())).toEqual(['C'])
    expect((w.emitted('state') as any[]).at(-1)[0]).toEqual({ loaded: true, err: '', open: 2, closed: 1 })
  })
})

describe('第 14 批審查補測', () => {
  it('Windows 服務＋監聽埠都有：兩顆子頁籤，預設只顯示 Windows 服務表，點監聽埠才換', async () => {
    const w = mount(AssetServices, opts({ serial: 'S1' }))
    await answer(p => (p.url === '/api/services' ? { items: [{ id: 9, port: 22, proto: 'tcp', guess_source: 'port', exposure: 'localhost', gone_at: null }] }
      : p.url === '/api/services/windows' ? { items: [{ id: 1, name: 'AppSvc', state: 'running', is_microsoft: false, gone_at: null }] } : { state: 'none', sites: [], pools: [] }))
    const chips = w.findAll('button.chip')
    expect(chips.map(c => c.text())).toEqual(['Windows 服務 1', '監聽埠 1'])
    expect(w.findAll('.tbl').length).toBe(1)
    expect(w.text()).toContain('AppSvc')
    await chips[1].trigger('click')
    expect(w.findAll('.tbl').length).toBe(1)
    expect(w.text()).not.toContain('AppSvc')
    expect(w.text()).toContain('推測')
  })
  it('IIS 有站台：state 帶站台數；集區身分、層級攤平', async () => {
    const w = mount(AssetServices, opts({ serial: 'S1' }))
    await answer(p => (p.url === '/api/services/iis' ? { state: 'used', sites: [{ id: 1, name: 'Default', state: 'Started', bindings: [], apps: [{ path: '/', pool: 'P1', vdirs: [{ path: '/img' }] }] }],
      pools: [{ name: 'P1', identity_type: 'SpecificUser', user: 'svc_web' }] } : { items: [] }))
    expect((w.emitted('state') as any[]).at(-1)[0]).toMatchObject({ iisSites: 1 })
    expect(w.findAll('.c-path').map(x => x.text())).toEqual(['/', '└ /img'])
    expect(w.findAll('.c-identity').map(x => x.text())).toEqual(['svc_web', 'svc_web'])
  })
  it('舊頁接線：收集全部收完、單一樣收完，都叫服務／帳號分頁重抓', async () => {
    const fs = await import('node:fs')
    const v = fs.readFileSync('pages/assets/[serial].vue', 'utf8')
    expect(v).toMatch(/job === 'service'\) svcReload\.value\+\+/)
    expect(v).toMatch(/job === 'account'\) acctReload\.value\+\+/)
    const poll = v.slice(v.indexOf('async function pollPostCollect'), v.indexOf('let postTries'))
    expect(poll).toContain('svcReload.value++')
    expect(poll).toContain('acctReload.value++')
    expect(v).toContain(':reload="svcReload"')
    expect(v).toContain(':reload="acctReload"')
  })
})

it('三個元件的 <style> 都編得過', async () => {
  const { parse, compileStyle } = await import('@vue/compiler-sfc')
  const fs = await import('node:fs')
  for (const f of ['AssetServices', 'AssetAccounts', 'AssetVulns']) {
    const d = parse(fs.readFileSync(`components/v2/${f}.vue`, 'utf8')).descriptor
    for (const s of d.styles) expect(compileStyle({ source: s.content, filename: f, id: 'data-v-x', scoped: true }).errors, f).toEqual([])
  }
})
