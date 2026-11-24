// 主機履歷「收集摘要」（codex 207）：純函式＋元件實掛（正常／資料已過期／未取得／不適用／讀取失敗、數字磚下鑽、切換主機不被舊回應覆蓋）。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote 為簡化替身；API 全假、無網路（IP 10.99.x）。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as cs from '../composables/collectSummary'
import Comp from '../components/v2/CollectSummary.vue'

enableAutoUnmount(afterEach)

const row = (n: number, kind: string, label: string, state: cs.CollectState, last: string | null, days: number | null): cs.CollectRow =>
  ({ __no: n, kind, label, state, state_label: cs.STATE_LABEL[state], last, days })
const payload = (o: Partial<cs.CollectData> = {}): cs.CollectData => ({
  key: 'k1', stale_days: 8, setting_key: 'collect_stale_days', applicable: true, reason: null,
  rows: [row(1, 'spec', '作業系統與硬體規格', 'never', null, null), row(2, 'service', '服務', 'fresh', '2026-11-09 08:00:00', 1),
    row(3, 'account', '帳號', 'stale', '2026-10-01 08:00:00', 40), row(4, 'software', '軟體', 'stale', '2026-10-02 08:00:00', 39),
    row(5, 'health', '健康檢查', 'fresh', '2026-11-10 01:00:00', 0)], ...o })

describe('collectSummary 純函式', () => {
  it('counts 與篩選同一組欄位：加總＝下鑽', () => {
    const rows = payload().rows
    const c = cs.countsOf(rows)
    expect(c).toEqual({ fresh: 2, stale: 2, never: 1 })
    for (const s of ['fresh', 'stale', 'never'] as const) expect(cs.filterRows(rows, s).length).toBe(c[s])
    expect(cs.filterRows(rows, 'all').length).toBe(5)
  })
  it('距今文字：null 是「—」不是 0 天；0 是今天', () => {
    expect(cs.daysText(null)).toBe('—')
    expect(cs.daysText(0)).toBe('今天')
    expect(cs.daysText(9)).toBe('9 天前')
  })
})

let current: any = payload()
let failWith: any = null
const calls: { url: string; opts: any }[] = []
const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p) {
    return () => h('table', { class: 'tbl-stub' }, [
      ...p.rows.map((r: any) => h('tr', { class: 'r' }, [h('td', `${p.prefix}${r.__no}`), ...p.cols.map((c: any) => h('td', { 'data-k': c.k }, `${c.text ? c.text(r) : r[c.k]}`))])),
      p.rows.length ? null : h('tr', h('td', { class: 'empty' }, p.empty)),
    ])
  },
})
function setup() {
  for (const [k, v] of Object.entries({ ref, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o?: any) => {
    calls.push({ url: u, opts: o })
    return failWith ? Promise.reject(failWith) : Promise.resolve(current)
  } }))
}
const mountIt = async (mkey = 'k1') => {
  const w = mount(Comp, { props: { mkey }, global: { stubs: { V2Table: Table, InfoNote: true } } })
  await flushPromises()
  return w
}
beforeEach(() => { calls.length = 0; current = payload(); failWith = null; setup() })
afterEach(() => { vi.unstubAllGlobals() })

describe('CollectSummary 元件', () => {
  it('五類各一列：最後成功時間、距今、狀態三種不混；從未成功寫「未取得」', async () => {
    const w = await mountIt()
    expect(calls[0].url).toBe('/api/v2/machine/collect-summary')
    expect(calls[0].opts.query).toEqual({ key: 'k1' })
    const rows = w.findAll('tr.r')
    expect(rows.length).toBe(5)
    const t = (i: number) => rows[i].text()
    expect(t(0)).toContain('S1'); expect(t(0)).toContain('未取得'); expect(t(0)).not.toContain('正常')
    expect(t(1)).toContain('2026-11-09 08:00:00'); expect(t(1)).toContain('1 天前'); expect(t(1)).toContain('正常')
    expect(t(2)).toContain('40 天前'); expect(t(2)).toContain('資料已過期')
    expect(t(4)).toContain('今天')
  })
  it('數字磚＝列數；點磚下鑽，再點收起', async () => {
    const w = await mountIt()
    const tile = (s: string) => w.find(`.tile.${s}`)
    expect(tile('fresh').find('b').text()).toBe('2')
    expect(tile('stale').find('b').text()).toBe('2')
    expect(tile('never').find('b').text()).toBe('1')
    await tile('stale').trigger('click')
    expect(w.findAll('tr.r').length).toBe(2)
    expect(w.findAll('tr.r').every(r => r.text().includes('資料已過期'))).toBe(true)
    await tile('stale').trigger('click')
    expect(w.findAll('tr.r').length).toBe(5)
  })
  it('門檻天數顯示在說明（由設定而來，不寫死）', async () => {
    current = payload({ stale_days: 3 })
    const w = await mountIt()
    expect(w.html()).toContain('3')
  })
  it('VMware 設備：不適用，不畫表格也不寫一排「未取得」', async () => {
    current = payload({ applicable: false, reason: 'VMware 設備不由本系統收集，資訊來源是 vCenter／RVTools', rows: [] })
    const w = await mountIt()
    expect(w.text()).toContain('不適用')
    expect(w.find('table').exists()).toBe(false)
    expect(w.text()).not.toContain('未取得')
  })
  it('讀取失敗：寫出原因，不當作沒有資料', async () => {
    failWith = { data: { detail: '查無這台' } }
    const w = await mountIt()
    expect(w.text()).toContain('讀取失敗')
    expect(w.text()).toContain('查無這台')
    expect(w.find('table').exists()).toBe(false)
  })
  it('切換主機重抓；較舊的回應不蓋掉較新的', async () => {
    const resolvers: Record<string, (v: any) => void> = {}
    vi.stubGlobal('useApi', () => ({ apiFetch: (_u: string, o: any) => new Promise((res) => { resolvers[o.query.key] = res }) }))
    const w = mount(Comp, { props: { mkey: 'a' }, global: { stubs: { V2Table: Table, InfoNote: true } } })
    await w.setProps({ mkey: 'b' })
    resolvers.b(payload({ key: 'b', rows: [row(1, 'spec', '作業系統與硬體規格', 'fresh', '2026-11-10 00:00:00', 0)] }))
    await flushPromises()
    resolvers.a(payload({ key: 'a' }))
    await flushPromises()
    expect(w.findAll('tr.r').length).toBe(1)
  })
})

describe('主機履歷頁掛上收集摘要', () => {
  it('概要頁籤裡有 V2CollectSummary，VMware 不顯示', () => {
    const src = readFileSync(resolve(process.cwd(), 'pages/v2/machine/[key].vue'), 'utf-8')
    expect(src).toMatch(/<V2CollectSummary v-if="!m\.vmware" :mkey="m\.key" \/>/)
  })
})
