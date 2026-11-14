// 主機履歷概要補充（2.060.x，舊 1.x 資產頁下架前補齊）：CIA 登記卡、體檢、EOS 徽章、RVTools 證人、待 CIA 異動、分類。
// GPT 挑剔使用者審查後：CIA 登記與體檢預設收合（?pf=）、體檢只講數字且數字可篩（?hf=）、EOS 分兩組、值下鑽「目前／共」＋匯出全部。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote／NuxtLink 為簡化替身；API 全假（IP 10.99.x）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Profile from '../components/v2/HostProfile.vue'

enableAutoUnmount(afterEach)
const calls: { url: string; opts: any }[] = []
let routes: Record<string, any> = {}
const route = reactive({ query: {} as Record<string, any> })
let lastExportAll: any = null
const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'exportAll'],
  setup(p) {
    return () => { lastExportAll = p.exportAll; return h('table', { class: 'tbl' }, p.rows.map((r: any) => h('tr', { class: 'r' }, p.cols.map((c: any) => h('td', { 'data-k': c.k }, `${r[c.k] ?? ''}`))))) }
  },
})
const Link = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { href: typeof p.to === 'string' ? p.to : `${p.to.path}?${new URLSearchParams(p.to.query).toString()}` }, slots.default?.()) } })
const stubs = { V2Table: Table, InfoNote: true, NuxtLink: Link }
beforeEach(() => {
  calls.length = 0; routes = {}; route.query = {}; lastExportAll = null
  for (const [k, v] of Object.entries({ ref, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o?: any) => { calls.push({ url: u, opts: o }); const r = routes[u]; return Promise.resolve(typeof r === 'function' ? r(o) : r) } }))
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: ({ query }: any) => { route.query = query } }))
})

const payload = () => ({
  key: 'h:app01', serial: 'A-1',
  cia: [{ serial: 'A-1', asset_status: '使用中', pending_status: '停用', data_at: '2026-11-01 09:00:00', fields: [
    { k: 'confidentiality', label: '機密性', value: '3', help: '數值 0–5', drill: true },
    { k: 'integrity', label: '完整性', value: '2', help: '數值 0–5', drill: true },
    { k: 'availability', label: '可用性', value: '4', help: '數值 0–5', drill: true },
    { k: 'usage_unit', label: '使用單位', value: '交易部', help: null, drill: true },
    { k: 'remark', label: '附加說明', value: '', help: null, drill: false },
  ] }],
  health: { machine: 'warn', data: 'ok', verified: false, checks_total: 4,
    check_states: { lost: { state: 'passed', reason: '' }, collect: { state: 'found', reason: '' }, eos: { state: 'not_checked', reason: '沒有 OS 資料' }, doc: { state: 'not_applicable', reason: 'VM 沒有單據' } },
    issues: [{ key: 'collect', label: '收集', level: 'warn', light: 'machine', detail: '8 天沒收', basis: '7 天', action: '檢查收集帳號' }] },
  eos: { os: { status: 'expired', eos_date: '2024-06-30', eos_kind: 'EOS' }, os_name: 'RHEL 7', os_link: '/v2/eos?tab=os&edk=os&edv=RHEL+7',
    os_src: { source: 'https://vendor.example.test/lifecycle', entry: 'RHEL 7', verified_on: '2026-10-01', updated_at: null },
    hw: null, hw_link: null, hw_src: null, model_name: null, os_guess: null, model_guess: 'DL380 Gen9', model_guess_confirmed: false,
    guess_src: { source: '由資產用途、資產名稱等欄位推得', updated_at: '2026-11-01 09:00:00' } },
  rvtools: { vm_name: 'app01', guest_os: 'Red Hat Enterprise Linux 7 (64-bit)', ip: '10.99.3.1', imported_at: '2026-11-01 08:00:00', linked: true },
  pending: [{ serial: 'A-1', asset_status: '使用中', pending_status: '停用', note: '已關機', by: 't', at: '2026-11-10' }],
  classify: { os_type: 'Linux', os_detail: 'RHEL', auto: 'Windows', override: { by: 't', at: '2026-11-01', reason: '人工確認' } },
})

async function mk() {
  routes['/api/v2/machine/profile'] = payload()
  const w = mount(Profile, { props: { mkey: 'h:app01' }, global: { stubs } })
  await flushPromises()
  return w
}

describe('HostProfile 概要補充', () => {
  it('預設收合：CIA 與體檢只留一行摘要（CIA 登記 1 筆・C3/I2/A4；體檢數字，不說沒問題）', async () => {
    const w = await mk()
    expect(calls[0]).toEqual({ url: '/api/v2/machine/profile', opts: { query: { key: 'h:app01' } } })
    const cia = w.find('[data-testid="hp-cia"]')
    expect(cia.text()).toContain('CIA 登記 1 筆・C3/I2/A4')
    expect(cia.find('dl').exists()).toBe(false)
    const hl = w.find('[data-testid="hp-health"]')
    expect(hl.find('[data-testid="hp-nums"]').findAll('button').map(b => b.text())).toEqual(['已檢查 2/4', '異常 1', '未驗 1', '不適用 1'])
    expect(hl.text()).not.toMatch(/沒問題/)
    expect(hl.text()).toContain('未經 SSH/WinRM 驗證')
    expect(hl.findAll('table').length).toBe(0)
    expect(hl.find('.n.hot').text()).toContain('未驗')                               // 只有未驗是醒目色
    expect(hl.find('.n.grey').text()).toContain('不適用')
  })
  it('展開記在網址 ?pf=；體檢數字點了篩表（?hf=），再點取消', async () => {
    const w = await mk()
    await w.find('[data-testid="hp-cia"] button.sum').trigger('click')
    expect(route.query.pf).toBe('cia')
    const cia = w.find('[data-testid="hp-cia"]')
    expect(cia.find('a').attributes('href')).toBe('/v2/asset/A-1')                    // 資產編號連到資產明細
    expect(cia.text()).toContain('資產狀態 使用中（待 CIA 異動：停用）'); expect(cia.text()).toContain('資料時間 2026-11-01 09:00')
    expect(cia.text()).toContain('未填')
    const nums = () => w.find('[data-testid="hp-nums"]').findAll('button')
    await nums()[2].trigger('click')                                                   // 未驗
    expect(route.query.hf).toBe('nc'); expect(route.query.pf).toBe('cia,health')
    const t = w.find('[data-testid="hp-health"]').findAll('table')
    expect(t.length).toBe(1); expect(t[0].findAll('tr.r').length).toBe(1); expect(t[0].text()).toContain('沒有 OS 資料')
    await nums()[3].trigger('click')                                                   // 不適用
    expect(w.find('[data-testid="hp-health"]').findAll('table')[0].text()).toContain('VM 沒有單據')
    await nums()[1].trigger('click')                                                   // 異常：狀態表＋問題清單（含機器／資料）
    const t2 = w.find('[data-testid="hp-health"]').findAll('table')
    expect(t2.length).toBe(2); expect(t2[1].find('[data-k="which"]').text()).toBe('機器')
    await nums()[1].trigger('click')
    expect(route.query.hf).toBeUndefined()
  })
  it('EOS 分兩組（EOS／辨識（待確認）），每個徽章標資料來源與日期；大類細分、RVTools 匯入時間可點', async () => {
    const w = await mk()
    const eos = w.find('[data-testid="hp-eos"]')
    expect(eos.findAll('.grp').map(g => g.text())).toEqual(['EOS', '辨識（待確認）'])
    const badges = eos.findAll('a')
    expect(badges[0].attributes('href')).toBe('/v2/eos?tab=os&edk=os&edv=RHEL+7')
    expect(badges[0].attributes('title')).toContain('vendor.example.test'); expect(badges[0].attributes('title')).toContain('查證日：2026-10-01')
    expect(badges[1].text()).toContain('型號猜測：DL380 Gen9'); expect(badges[1].attributes('title')).toContain('資料時間 2026-11-01')
    const flags = w.find('[data-testid="hp-flags"]')
    expect(flags.findAll('a').map(a => a.attributes('href'))).toContain('/v2/eos?tab=os&eq=RHEL')
    const rv = w.find('[data-testid="hp-rv"]')
    expect(rv.findAll('a').map(a => a.attributes('href'))).toContain('/v2/settings?st=import&src=rvtools')
  })
  it('值下鑽：超過 50 筆寫「目前 50／共 N 筆」，有「匯出全部」（向後端拿全部，最多 2000）', async () => {
    routes['/api/v2/machine/same-value'] = (o: any) => (o.query.limit
      ? { items: Array.from({ length: 120 }, (_, i) => ({ serial: `S${i}` })), total: 120 }
      : { field: 'usage_unit', label: '使用單位', value: '交易部', total: 120, shown: 50, truncated: true, max: 2000,
          items: Array.from({ length: 50 }, (_, i) => ({ serial: `S${i}` })) })
    const w = await mk()
    await w.find('[data-testid="hp-cia"] button.sum').trigger('click')
    const btns = w.find('[data-testid="hp-cia"]').findAll('button.lb')
    expect(btns.map(b => b.text())).toEqual(['3', '2', '4', '交易部'])
    await btns[3].trigger('click'); await flushPromises()
    expect(w.find('[data-testid="hp-cia"]').text()).toContain('目前 50／共 120 筆')
    expect(typeof lastExportAll).toBe('function')
    const all = await lastExportAll()
    expect(all.length).toBe(120)
    expect(calls.at(-1)!.opts.query.limit).toBe(2000)
  })
  it('退役（health 為 null）寫不列入體檢；沒有 CIA 登記寫原因', async () => {
    routes['/api/v2/machine/profile'] = { ...payload(), health: null, cia: [], pending: [], classify: null, rvtools: null, eos: { os: null, hw: null } }
    const w = mount(Profile, { props: { mkey: 'x' }, global: { stubs } })
    await flushPromises()
    expect(w.text()).toContain('已退役，不列入體檢')
    expect(w.text()).toContain('CIA 沒有登記這台')
    expect(w.find('[data-testid="hp-eos"]').exists()).toBe(false)
  })
})
