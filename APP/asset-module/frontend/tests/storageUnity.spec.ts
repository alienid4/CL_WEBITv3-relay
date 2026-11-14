// 5.2「Unity 陣列」頁籤實掛：卡片數字＝下面清單筆數、數字連到對的子檢視＋篩選、篩選後項次不變、LUN 已用沒收寫「未收集」、沒資料有下一步。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote／NuxtLink 為簡化替身；API 全假、無網路。示範資料只用 *.example.test。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { Suspense, computed, defineComponent, h, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/StorageUnity.vue'

enableAutoUnmount(afterEach)
let reply: any
let admin = true
const posts: { u: string; o: any }[] = []
const route = reactive<{ query: Record<string, any> }>({ query: {} })

const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty', 'max'],
  setup(p) {
    return () => (p.rows.length
      ? h('table', { class: 'tbl', 'data-prefix': p.prefix }, p.rows.map((r: any) => h('tr', { class: 'row', 'data-no': r.__no }, p.cols.map((c: any) => {
        const href = c.link ? c.link(r) : null
        const txt = String(c.text ? c.text(r) ?? '—' : r[c.k] ?? '—')
        return h('td', { class: 'c-' + c.k }, href ? h('a', { href }, txt) : txt)
      }))))
      : h('p', { class: 'empty' }, p.empty))
  },
})
const NuxtLink = defineComponent({ props: ['to'], setup(p, { slots }) { return () => h('a', { href: p.to, class: 'nl' }, slots.default?.()) } })

const GB = 1024 ** 3
function data() {
  const arrays = [{ __no: 1, serial: 'SN-A', array: 'UNITY-A', name: 'UNITY-A', model: 'Unity 480', management_host: 'unity-a.example.test', size_total: 150 * GB, size_used: 70 * GB, size_free: 80 * GB, used_pct: 46.7, pool_count: 2, lun_count: 2, host_count: 2, map_count: 2, collected_at: '2026-11-10T21:05:00+08:00', collected_text: '2026-11-10 21:05:00', age_hours: 80, stale: true }]
  const pools = [{ __no: 1, serial: 'SN-A', array: 'UNITY-A', pool_id: 'pool_1', name: 'Pool-Gold', size_total: 100 * GB, size_used: 85 * GB, size_free: 15 * GB, used_pct: 85, hot: true, trend_days: 10, growth_30d: 9 * GB, growth_days: 9, days_to_full: 15, trend_note: '約 15 天' },
    { __no: 2, serial: 'SN-A', array: 'UNITY-A', pool_id: 'pool_2', name: 'Pool-Silver', size_total: 50 * GB, size_used: 10 * GB, size_free: 40 * GB, used_pct: 20, hot: false, trend_days: 3, growth_30d: null, growth_days: null, days_to_full: null, trend_note: '資料不足（3 天）' }]
  const luns = [{ __no: 1, serial: 'SN-A', array: 'UNITY-A', lun_id: 'sv_1', name: 'LUN-DB01', pool: null, size_total: 20 * GB, used: null, free: null, used_pct: null, host_count: 2, hosts: ['db01.example.test', 'ghost01'] },
    { __no: 2, serial: 'SN-A', array: 'UNITY-A', lun_id: 'sv_2', name: 'LUN-SPARE', pool: null, size_total: 5 * GB, used: null, free: null, used_pct: null, host_count: 0, hosts: [] }]
  const hosts = [{ __no: 1, serial: 'SN-A', array: 'UNITY-A', host_id: 'Host_1', name: 'db01.example.test', type_label: '手動建立', match_status: 'matched', match_label: '已對到', asset_serial: 'A001', lun_count: 1, candidates: 1 },
    { __no: 2, serial: 'SN-A', array: 'UNITY-A', host_id: 'Host_3', name: 'ghost01', type_label: '手動建立', match_status: 'no_asset', match_label: '找不到資產', asset_serial: null, lun_count: 1, candidates: 0 }]
  const maps = [{ __no: 1, serial: 'SN-A', array: 'UNITY-A', lun_id: 'sv_1', lun: 'LUN-DB01', host_id: 'Host_1', host: 'db01.example.test', access: '正式', match_status: 'matched', asset_serial: 'A001' },
    { __no: 2, serial: 'SN-A', array: 'UNITY-A', lun_id: 'sv_1', lun: 'LUN-DB01', host_id: 'Host_3', host: 'ghost01', access: '正式', match_status: 'no_asset', asset_serial: null }]
  return { arrays, pools, pools_hot: [pools[0]], luns, hosts, maps, unmatched: [hosts[1]], daily: [{ __no: 1, serial: 'SN-A', array: 'UNITY-A', day: '2026-11-10', pools: 2, size_total: 150 * GB, size_used: 70 * GB, size_free: 80 * GB, used_pct: 46.7 }],
    pool_daily: [{ __no: 1, serial: 'SN-A', array: 'UNITY-A', pool_id: 'pool_1', pool: 'Pool-Gold', day: '2026-11-10', size_total: 100 * GB, size_used: 85 * GB, size_free: 15 * GB, used_pct: 85 },
      { __no: 2, serial: 'SN-A', array: 'UNITY-A', pool_id: 'pool_2', pool: 'Pool-Silver', day: '2026-11-10', size_total: 50 * GB, size_used: 10 * GB, size_free: 40 * GB, used_pct: 20 }],
    settings: { pool_warn_pct: 80, trend_window_days: 30, trend_min_days: 7, stale_days: 3 },
    cards: { arrays: 1, pools_hot: 1, stale_arrays: 1, pools: 2, luns: 2, maps: 2, hosts: 2, matched: 1, unmatched: 1, not_host: 0, size_total: 150 * GB, size_used: 70 * GB, size_free: 80 * GB, used_pct: 46.7 },
    freshness: { status: 'stale', status_label: '資料過期', last_collected_at: '2026-11-07 21:05:00' } }
}

beforeEach(() => {
  reply = data()
  route.query = { tab: 'unity' }
  for (const [k, v] of Object.entries({ ref, computed, reactive })) vi.stubGlobal(k, v)
  vi.stubGlobal('useApi', () => ({ apiFetch: (u: string, o: any) => { posts.push({ u, o }); return Promise.resolve(reply) } }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: computed(() => admin) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => undefined }))
  vi.stubGlobal('confirm', () => true)
  posts.length = 0; admin = true
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: ({ query }: any) => { route.query = query } }))
})

async function mnt() {
  const w = mount(defineComponent({ render: () => h(Suspense, null, { default: () => h(Comp) }) }),
    { global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, V2Table, NuxtLink } } })
  await flushPromises()
  return w
}

describe('5.2 Unity 陣列頁籤', () => {
  it('卡片數字＝清單筆數，點卡片到對應子檢視；頁首有最後收集時間與過期標示', async () => {
    const w = await mnt()
    const chips = w.findAll('a.chip').map(a => [a.text(), a.attributes('href')])
    expect(chips).toContainEqual(['陣列 1', '/v2/storage?tab=unity&uv=arrays'])
    expect(chips).toContainEqual(['對不到 1', '/v2/storage?tab=unity&uv=unmatched'])
    expect(chips).toContainEqual(['主機↔LUN 2', '/v2/storage?tab=unity&uv=map'])
    expect(w.text()).toContain('最後收集 2026-11-07 21:05:00')
    expect(w.find('.stale').text()).toBe('資料過期')
    expect(w.findAll('tr.row')).toHaveLength(1)
  })

  it('陣列列上的每個數字都能下鑽：容量→該陣列 Pool、LUN 數→該陣列 LUN、對應數→該陣列主機↔LUN', async () => {
    const w = await mnt()
    expect(w.find('td.c-size_total a').attributes('href')).toBe('/v2/storage?tab=unity&uv=pools&ua=SN-A')
    expect(w.find('td.c-lun_count a').attributes('href')).toBe('/v2/storage?tab=unity&uv=luns&ua=SN-A')
    expect(w.find('td.c-map_count a').attributes('href')).toBe('/v2/storage?tab=unity&uv=map&ua=SN-A')
    expect(w.find('td.c-size_total').text()).toBe('150.0 GiB')
  })

  it('LUN 的主機數下鑽到主機↔LUN 篩這個 LUN；篩選後項次不變；已用沒收寫「未收集」', async () => {
    route.query = { tab: 'unity', uv: 'luns' }
    let w = await mnt()
    expect(w.find('td.c-used').text()).toBe('未收集')
    expect(w.find('td.c-host_count a').attributes('href')).toBe('/v2/storage?tab=unity&uv=map&ua=SN-A&ul=sv_1')
    w.unmount()
    route.query = { tab: 'unity', uv: 'map', ua: 'SN-A', uh: 'Host_3' }
    w = await mnt()
    const rows = w.findAll('tr.row')
    expect(rows).toHaveLength(1)
    expect(rows[0].attributes('data-no')).toBe('2')
    expect(w.find('td.c-asset_serial').text()).toBe('對不到')
    expect(w.text()).toContain('主機：ghost01 ✕')
  })

  it('對不到清單有下一步：用主機名查連到 2.1 主機查詢', async () => {
    route.query = { tab: 'unity', uv: 'unmatched' }
    const w = await mnt()
    expect(w.find('td.c-name_q a').attributes('href')).toBe('/v2/hosts?q=ghost01')
  })

  it('沒資料：講下一步，不是空白', async () => {
    reply = { ...data(), arrays: [], pools: [], pools_hot: [], pool_daily: [], luns: [], hosts: [], maps: [], unmatched: [], daily: [], cards: { arrays: 0, pools: 0, luns: 0, maps: 0, hosts: 0, unmatched: 0 }, freshness: { status: 'never', status_label: '從未收到', last_collected_at: null } }
    const w = await mnt()
    expect(w.text()).toContain('還沒有 Unity 資料')
    expect(w.find('.note').exists()).toBe(true)
  })

  it('Pool 使用率預警卡＝下鑽清單；Pool 的容量／名稱連到 Pool 層每日容量；不足 7 天寫資料不足', async () => {
    let w = await mnt()
    const hot = w.findAll('a.chip').find(a => a.text().startsWith('Pool 使用率≥80%'))!
    expect(hot.text()).toBe('Pool 使用率≥80% 1')
    expect(hot.attributes('href')).toBe('/v2/storage?tab=unity&uv=hot')
    w.unmount()
    route.query = { tab: 'unity', uv: 'hot' }
    w = await mnt()
    expect(w.findAll('tr.row')).toHaveLength(1)
    expect(w.find('td.c-name a').attributes('href')).toBe('/v2/storage?tab=unity&uv=daily&ua=SN-A&up=pool_1')
    expect(w.find('td.c-days_to_full').text()).toBe('約 15 天')
    w.unmount()
    route.query = { tab: 'unity', uv: 'pools' }
    w = await mnt()
    expect(w.findAll('td.c-days_to_full').map(t => t.text())).toEqual(['約 15 天', '資料不足（3 天）'])
    w.unmount()
    route.query = { tab: 'unity', uv: 'daily', ua: 'SN-A', up: 'pool_1' }
    w = await mnt()
    expect(w.findAll('tr.row')).toHaveLength(1)
    expect(w.find('td.c-pool').text()).toBe('Pool-Gold')
  })

  it('陣列列：自己的資料時間、多久前、過期標示，連到中繼匯入紀錄（篩 Unity）', async () => {
    const w = await mnt()
    expect(w.find('td.c-collected_text a').attributes('href')).toBe('/v2/settings?st=integrations&sub=relay&src=unity')
    expect(w.find('td.c-age_hours').text()).toBe('3 天前')
    expect(w.find('td.c-stale').text()).toBe('資料過期')
    expect(w.text()).toContain('過期陣列 1')
  })

  it('全部 X・篩選後 Y；換檢視保留篩選；清除篩選一鍵清掉', async () => {
    route.query = { tab: 'unity', uv: 'map', ua: 'SN-A', uh: 'Host_3' }
    const w = await mnt()
    expect(w.find('.cnt').text()).toBe('全部 2・篩選後 1')
    expect(w.findAll('a.chip').find(a => a.text().startsWith('LUN'))!.attributes('href')).toBe('/v2/storage?tab=unity&uv=luns&ua=SN-A&uh=Host_3')
    await w.findAll('button').find(b => b.text() === '清除篩選')!.trigger('click')
    expect(route.query.ua).toBeUndefined()
    expect(route.query.uh).toBeUndefined()
    expect(route.query.uv).toBe('map')
  })

  it('成長寫實際天數；Unity 回報 0 個 Pool 有提示，管理員可以接受空快照', async () => {
    route.query = { tab: 'unity', uv: 'pools' }
    reply = { ...data(), empty_alert: { batch_id: '2026-11-11', text: 'Unity 回報 0 個 Pool，已保留舊資料', serial: 'SN-A' } }
    const w = await mnt()
    expect(w.find('td.c-growth_30d').text()).toBe('近 9 天增加 9.0 GiB')
    expect(w.find('.alert').text()).toBe('Unity 回報 0 個 Pool，已保留舊資料')
    await w.findAll('button').find(b => b.text().startsWith('接受空快照'))!.trigger('click')
    await flushPromises()
    const p = posts.find(x => x.u === '/api/v2/relay-import/unity/accept-empty')!
    expect(p.o).toEqual({ method: 'POST', body: { serial: 'SN-A' } })
    w.unmount()
    admin = false
    const v = await mnt()
    expect(v.findAll('button').some(b => b.text().startsWith('接受空快照'))).toBe(false)
  })
})
