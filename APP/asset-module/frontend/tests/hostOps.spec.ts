// 單機維運記錄本（components/v2/HostOps.vue + composables/hostOps.ts）實掛。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身（保留 cell-id／cell-count 插槽）；API 全假、無網路。
import { mount, flushPromises } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cardValue, demoData, eventRows, filterFor, recoveredText } from '../composables/hostOps'
import HostOps from '../components/v2/HostOps.vue'

const route = reactive<{ query: Record<string, string> }>({ query: {} })
const calls: { url: string; opt: any }[] = []
let viewer = false
let payload: any
let gate: ((url: string, opt: any) => Promise<any>) | null = null

const ev = (id: string, over: any = {}) => ({
  id, __no: 1, source: 'whatsup', source_label: 'WhatsUp Gold 告警', category: 'disk', category_label: '磁碟', partition: 'C:', title: '磁碟 C:', first_at: '2026-10-20 08:00:00',
  last_at: '2026-10-20 09:00:00', recovered_at: '2026-10-20 10:00:00', status: 'recovered', match_label: 'IP＋主機名一致', countable: true, metric_text: '未取得（WhatsUp 只提供告警文字，沒有量測數值）',
  evidence: [{ k: '告警原文', v: 'Disk Free Space - C:' }], handling_text: { action: '未填', root_cause: '未確認', outcome: '未填' }, notes: [], ...over,
})
const base = () => ({
  machine: { key: 'K', name: 'HOST1', ips: [], serials: [] }, window: { period: '30d', label: '最近 30 天', start: '2026-10-02 12:00:00', end: '2026-11-01 12:00:00', tz: 'Asia/Taipei', future: false },
  batch: 'abc123def456', generated_at: '', evidence_any: true, events: [ev('WUG-1-disk:C:'), ev('WUG-2-memory', { category: 'memory', category_label: '記憶體', partition: '', title: '記憶體', status: 'ongoing', recovered_at: null }),
    ev('WUG-3-disk:D:', { partition: 'D:', title: '磁碟 D:' })],
  counts: [{ key: 'disk|C:|whatsup', __no: 1, category: 'disk', category_label: '磁碟', partition: 'C:', source: 'whatsup', source_label: 'WhatsUp Gold 告警', count: 1, ongoing: 0, event_ids: ['WUG-1-disk:C:'] },
    { key: 'disk|D:|whatsup', __no: 2, category: 'disk', category_label: '磁碟', partition: 'D:', source: 'whatsup', source_label: 'WhatsUp Gold 告警', count: 1, ongoing: 0, event_ids: ['WUG-3-disk:D:'] },
    { key: 'memory||whatsup', __no: 3, category: 'memory', category_label: '記憶體', partition: '', source: 'whatsup', source_label: 'WhatsUp Gold 告警', count: 1, ongoing: 1, event_ids: ['WUG-2-memory'] }],
  ongoing_ids: ['WUG-2-memory'], totals: { events: 3, countable: 3, ongoing: 1, disk: 2, memory: 1 },
  coverage: [{ source: 'nmon', label: 'NMON 門檻超標', kind: 'metric_history', state: 'no_data', rows: 0, covers_from: null, covers_to: null, note: '沒有上傳過' }],
  upcoming_coverage: [], upcoming: [], pending: [], orphan_notes: [],
})

beforeEach(() => {
  route.query = {}
  calls.length = 0
  viewer = false
  payload = base()
  gate = null
  vi.stubGlobal('ref', ref)
  vi.stubGlobal('computed', computed)
  vi.stubGlobal('watch', watch)
  vi.stubGlobal('onMounted', onMounted)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opt: any = {}) => { calls.push({ url, opt }); if (gate) return gate(url, opt); return url === '/api/v2/machine/ops' ? payload : {} } }))
})
afterEach(() => vi.unstubAllGlobals())

const V2Table = defineComponent({ props: ['rows', 'cols', 'empty', 'prefix'], setup(p, { slots }) {
  return () => (p.rows.length
    ? h('div', p.rows.map((r: any) => h('div', { class: 'row' }, [slots['cell-id']?.({ row: r }) ?? '', slots['cell-count']?.({ row: r }) ?? '', `${p.prefix}${r.__no}|${r.title ?? ''}|${r.st ?? ''}|${r.rec ?? ''}`])))
    : h('p', { 'data-testid': 'empty' }, p.empty))
} })
const passthrough = defineComponent({ setup(_p, { slots }) { return () => h('div', slots.default?.()) } })
async function mountOps() {
  const w = mount(HostOps, { props: { mkey: 'K' }, global: { components: { V2Table, InfoNote: passthrough } } })
  await flushPromises()
  return w
}

describe('純函式', () => {
  it('篩選：持續中／類別／計次下鑽是同一份 id', () => {
    const d = base()
    expect([...filterFor('ongoing', d as any).ids!]).toEqual(['WUG-2-memory'])
    expect([...filterFor('disk', d as any).ids!].sort()).toEqual(['WUG-1-disk:C:', 'WUG-3-disk:D:'])
    expect(filterFor('all', d as any).ids).toBeNull()
    const f = filterFor('count', d as any, d.counts[0] as any)
    expect(eventRows(d.events as any, f).map(r => r.id)).toEqual(['WUG-1-disk:C:'])
  })
  it('恢復欄：沒恢復的持續中寫「尚未恢復」，沒資料寫「未取得」，不留空也不寫 0', () => {
    expect(recoveredText({ recovered_at: null, status: 'ongoing' })).toBe('尚未恢復')
    expect(recoveredText({ recovered_at: null, status: 'unknown' })).toBe('未取得')
    expect(recoveredText({ recovered_at: '2026-10-20 10:00:00', status: 'recovered', partial_recover: true })).toContain('裝置仍告警')
  })
  it('示範資料標明示範、內部一致（計次 id 都在事件裡）', () => {
    const d = demoData()
    const ids = new Set(d.events.map(e => e.id))
    for (const c of d.counts) { expect(c.count).toBe(c.event_ids.length); c.event_ids.forEach(i => expect(ids.has(i)).toBe(true)) }
    expect(d.demo).toBe(true)
  })
})

describe('事件與維運紀錄 實掛', () => {
  it('載入：事件列、卡片數字、批次、時區；點「磁碟次數」只剩磁碟事件，點清除回全部', async () => {
    const w = await mountOps()
    expect(calls[0].url).toBe('/api/v2/machine/ops')
    expect(calls[0].opt.query).toEqual({ key: 'K', period: '30d' })
    expect(w.text()).toContain('批次 abc123def456')
    expect(w.text()).toContain('Asia/Taipei')
    expect(w.findAll('.row').length).toBe(3)
    const card = w.findAll('button.card').find(b => b.text().includes('磁碟次數'))!
    expect(card.text()).toContain('2')
    await card.trigger('click')
    expect(w.findAll('.row').length).toBe(2)
    expect(w.text()).toContain('目前只看：磁碟')
    await w.findAll('button.lb').find(b => b.text() === '清除')!.trigger('click')
    expect(w.findAll('.row').length).toBe(3)
  })

  it('計次表的次數是下鑽：點了回到事件頁籤且只列那幾筆', async () => {
    const w = await mountOps()
    await w.findAll('button.sub').find(b => b.text().startsWith('計次'))!.trigger('click')
    const links = w.findAll('.row button.lb')
    expect(links.length).toBe(3)
    await links[0].trigger('click')
    expect(w.findAll('.row').length).toBe(1)
    expect(w.text()).toContain('WUG-1-disk:C:')
  })

  it('來源缺口：明講沒資料不代表正常；沒事件時的空白狀態講下一步', async () => {
    payload = { ...base(), events: [], counts: [], ongoing_ids: [], totals: { events: 0, countable: 0, ongoing: 0, disk: 0, memory: 0 } }
    const w = await mountOps()
    expect(w.text()).toContain('NMON 門檻超標')
    expect(w.text()).toContain('不代表正常')
    expect(w.find('[data-testid=empty]').text()).toContain('來源沒資料不等於正常')
  })

  it('選事件可新增處理紀錄：POST 帶 key／event_id／kind／text，成功後重載', async () => {
    const w = await mountOps()
    await w.find('.row button.lb').trigger('click')
    expect(w.find('[data-testid=ops-detail]').text()).toContain('未填')
    await w.find('select').setValue('root_cause')
    await w.find('.add textarea').setValue('備份暫存塞滿')
    await w.findAll('.add button').find(b => b.text() === '新增')!.trigger('click')
    await flushPromises()
    const post = calls.find(c => c.opt.method === 'POST')!
    expect(post.url).toBe('/api/v2/machine/ops/notes')
    expect(post.opt.body).toMatchObject({ key: 'K', event_id: 'WUG-1-disk:C:', kind: 'root_cause', text: '備份暫存塞滿' })
    expect(post.opt.body.client_key).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
    expect(calls.filter(c => c.url === '/api/v2/machine/ops').length).toBe(2)
  })

  it('已更正的舊版標示、作廢要原因才能按；只能看的帳號沒有填寫表單也沒有匯出 MD', async () => {
    payload = base()
    payload.events[0].notes = [{ id: 7, kind: 'action', text: '舊文字', status: 'corrected', created_by: 'a', created_at: 't' },
      { id: 8, kind: 'action', text: '新文字', status: 'active', created_by: 'a', created_at: 't' }]
    const w = await mountOps()
    await w.find('.row button.lb').trigger('click')
    expect(w.find('[data-testid=ops-detail]').text()).toContain('已更正（被新版取代）')
    await w.findAll('.notes button.lb').find(b => b.text() === '作廢')!.trigger('click')
    const ok = w.findAll('.frm button').find(b => b.text() === '確定作廢')!
    expect(ok.attributes('disabled')).toBeDefined()
    expect(w.find('a.lk').exists()).toBe(true)
    viewer = true
    const v = await mountOps()
    await v.find('.row button.lb').trigger('click')
    expect(v.find('.add').exists()).toBe(false)
    expect(v.find('a.lk').exists()).toBe(false)
    expect(v.text()).toContain('只能看的帳號不能填寫')
  })

  it('示範資料：標明示範、不打寫入 API、沒有匯出 MD', async () => {
    const w = await mountOps()
    await w.findAll('button.mini').find(b => b.text() === '看示範資料')!.trigger('click')
    expect(w.text()).toContain('示範資料：以下是範例')
    expect(w.find('a.lk').exists()).toBe(false)
    await w.find('.row button.lb').trigger('click')
    expect(w.find('.add').exists()).toBe(false)
    expect(calls.some(c => c.opt.method === 'POST')).toBe(false)
  })

  it('換期間會重新讀取並帶新期間', async () => {
    const w = await mountOps()
    await w.findAll('button.pill').find(b => b.text() === '未來 30 天')!.trigger('click')
    await flushPromises()
    expect(calls[calls.length - 1].opt.query).toEqual({ key: 'K', period: 'next30' })
  })

  it('快速切期間：舊回應晚到不得蓋掉新畫面（序號防護）', async () => {
    const pending: Record<string, (v: any) => void> = {}
    gate = (_u, opt) => new Promise(res => { pending[opt.query.period] = res })
    const w = mount(HostOps, { props: { mkey: 'K' }, global: { components: { V2Table, InfoNote: passthrough } } })
    await flushPromises()
    await w.findAll('button.pill').find(b => b.text() === '今天')!.trigger('click')
    await flushPromises()
    pending['today']({ ...base(), batch: 'NEW', events: [], counts: [], ongoing_ids: [], totals: { events: 0, countable: 0, ongoing: 0, unknown_end: 0, disk: 0, memory: 0 } })
    await flushPromises()
    pending['30d'](base())                                   // 舊請求最後才回來
    await flushPromises()
    expect(w.text()).toContain('批次 NEW')
    expect(w.text()).not.toContain('abc123def456')
    expect(w.findAll('.row').length).toBe(0)
  })

  it('零事件且所有事件來源對這台都沒資料：卡片寫「未取得」不是 0', async () => {
    payload = { ...base(), events: [], counts: [], ongoing_ids: [], evidence_any: false, totals: { events: 0, countable: 0, ongoing: 0, unknown_end: 0, disk: 0, memory: 0 },
      coverage: [{ source: 'whatsup', label: 'WhatsUp Gold 告警', kind: 'event_history', state: 'no_machine_data', rows: 0, covers_from: null, covers_to: null, note: '' }] }
    const w = await mountOps()
    const cards = w.findAll('button.card').map(b => b.text())
    expect(cards.filter(t => t.includes('未取得')).length).toBe(4)
    expect(w.text()).toContain('不代表正常')
    expect(cardValue(0, { evidence_any: true, totals: { events: 0 } })).toBe(0)
  })

  it('更正者與原作者都顯示；只能看的帳號沒有稽核按鈕；結案時間標明非單機恢復', async () => {
    payload = base()
    payload.events[0].notes = [{ id: 9, kind: 'action', text: '乙改的', status: 'active', created_by: 'bob', original_by: 'alice', created_at: 't' }]
    Object.assign(payload.events[0], { recovered_at: null, status: 'closed', closed_at: '2026-10-21 09:00:00', closed_label: '結案時間，非單機恢復時間' })
    const w = await mountOps()
    expect(w.text()).toContain('結案時間，非單機恢復時間')
    await w.find('.row button.lb').trigger('click')
    expect(w.find('[data-testid=ops-detail]').text()).toContain('原作者 alice、更正者 bob')
    expect(w.findAll('.notes button.lb').some(b => b.text() === '稽核')).toBe(true)
    viewer = true
    const v = await mountOps()
    await v.find('.row button.lb').trigger('click')
    expect(v.findAll('.notes button.lb').some(b => b.text() === '稽核')).toBe(false)
  })
})
