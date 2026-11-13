// 2.1 主機查詢「偵測存活」頁籤（2.041.97）：實掛 HostAlive.vue。
// Nuxt 自動匯入以 stubGlobal 提供；V2Table／InfoNote／OfflineMarkModal 為簡化替身；API 全假、無網路（IP 10.99.x）。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as ha from '../composables/hostAlive'
import Alive from '../components/v2/HostAlive.vue'

enableAutoUnmount(afterEach)

const row = (sn: string | null, state: ha.AliveRow['state'], ip = '10.99.1.1'): ha.AliveRow => ({
  asset_serial: sn, hostname: sn ? `h-${sn}` : null, ip, state, state_label: ha.ALIVE_LABEL[state], evidence: '推論',
  verdict: '說明', icmp_sent: state === 'excluded' || state === 'skipped' ? null : 3, icmp_recv: 0, avg_ms: null, open_ports: '' })
const report = (rows: ha.AliveRow[], o: Partial<ha.AliveReport> = {}): ha.AliveReport => ({
  run_id: 7, scope: '2.1 主機查詢・批次偵測存活', started_at: '2026-11-02 10:00:00', finished_at: null, created_by: 'tester',
  ports: '22,445,3389,5985', requested: rows.length, deadline_hit: false, counts: ha.aliveCounts(rows), rows, ...o })

const ROWS = [row('A-1', 'alive', '10.99.1.1'), row('A-2', 'no_response', '10.99.1.2'), row('A-3', 'no_response', '10.99.1.3'),
  row('E-1', 'excluded', '10.99.2.1'), row('N-1', 'skipped', '10.99.1.4'), row(null, 'skipped', '10.99.1.5')]

describe('hostAlive 純函式', () => {
  it('計數與下鑽同一組：加總＝下鑽', () => {
    const c = ha.aliveCounts(ROWS)
    expect(c).toEqual({ alive: 1, no_response: 2, unknown: 0, excluded: 1, skipped: 2 })
    for (const k of ha.ALIVE_ORDER) expect(ha.aliveFilter(ROWS, k).length).toBe(c[k])
    expect(Object.values(c).reduce((a, b) => a + b, 0)).toBe(ROWS.length)
  })
  it('標下線只帶連不上且有序號的列', () => {
    expect(ha.offlineSerials([...ROWS, row(null, 'no_response', '10.99.1.9')])).toEqual(['A-2', 'A-3'])
  })
})

let viewer = false
let reports: Record<number, ha.AliveReport> = {}
let postResult: any
let postError: any = null
const calls: { url: string; opts: any }[] = []
const Table = defineComponent({
  props: ['rows', 'cols', 'prefix', 'empty'],
  setup(p, { slots }) {
    return () => h('table', { class: 'tbl-stub' }, [
      ...p.rows.map((r: any) => h('tr', { class: 'r' }, [h('td', `${p.prefix}${r.__no ?? ''}`), ...p.cols.map((c: any) => h('td', { 'data-k': c.k }, slots['cell-' + c.k] ? slots['cell-' + c.k]({ row: r }) : `${c.text ? c.text(r) : r[c.k]}`))])),
    ])
  },
})
const Modal = defineComponent({ props: ['serials', 'defaultReason'], emits: ['close', 'done'], setup(p) { return () => h('div', { class: 'modal-stub', 'data-serials': p.serials.join(','), 'data-reason': p.defaultReason }) } })
const stubs = { V2Table: Table, InfoNote: true, OfflineMarkModal: Modal }
const runItem = (id: number) => ({ run_id: id, started_at: '2026-11-02 10:00:00', created_by: 'tester', scope: 's', total: 3, alive: 1, no_response: 2, unknown: 0, excluded: 1, skipped: 2, from_v2: true })

function setup() {
  for (const [k, v] of Object.entries({ ref, computed, onMounted, watch })) vi.stubGlobal(k, v)
  const api = (u: string, o?: any) => {
    calls.push({ url: u, opts: o })
    if (o?.method === 'POST') return postError ? Promise.reject(postError) : Promise.resolve(postResult)
    if (u.endsWith('/alive-runs')) return Promise.resolve({ items: [runItem(7)] })
    const id = Number(u.split('/').pop())
    return reports[id] ? Promise.resolve(reports[id]) : Promise.reject({ data: { detail: '查無這份偵測報告' } })
  }
  vi.stubGlobal('useApi', () => ({ apiFetch: api }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: api }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer) }))
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
}
beforeEach(() => { calls.length = 0; viewer = false; postError = null; reports = { 7: report(ROWS) }; postResult = report(ROWS); setup() })
afterEach(() => { vi.unstubAllGlobals() })

const mountIt = (serials = ['A-1', 'A-2', 'A-3', 'E-1'], vmware = 1) => mount(Alive, { global: { stubs }, props: { serials, vmware } })
const btn = (w: any, text: string) => w.findAll('button').find((b: any) => b.text().includes(text))

describe('HostAlive 元件', () => {
  it('只送 serials（從不送 ips）；結果磚＝列數、點磚下鑽', async () => {
    const w = mountIt()
    await flushPromises()
    expect(w.text()).toContain('待偵測 4 台')
    expect(w.text()).toContain('VMware 設備 1 台會被排除')
    await btn(w, '開始偵測').trigger('click')
    await flushPromises()
    const post = calls.find(c => c.opts?.method === 'POST')!
    expect(post.url).toBe('/api/v2/hosts/alive-check')
    expect(post.opts.body).toEqual({ serials: ['A-1', 'A-2', 'A-3', 'E-1'] })
    expect(Object.keys(post.opts.body)).toEqual(['serials'])
    expect(w.findAll('.rep tr.r').length).toBe(ROWS.length)
    const tiles = w.findAll('.tile').map(t => t.text())
    expect(tiles).toContain('全部 6')
    expect(tiles).toContain('連不上 2')
    expect(tiles).toContain('已排除（VMware 設備） 1')
    await w.findAll('.tile').find(t => t.text().startsWith('連不上'))!.trigger('click')
    expect(w.findAll('.rep tr.r').length).toBe(2)                         // 磚上的數字＝下鑽出來的列數
  })

  it('viewer 看不到任何寫入按鈕（開始偵測、標下線）', async () => {
    viewer = true
    const w = mountIt()
    await flushPromises()
    await btn(w, '看報告')?.trigger('click')
    expect(btn(w, '開始偵測')).toBeUndefined()
    expect(w.text()).not.toContain('標記下線')
  })

  it('盤中確認按取消（useApi 丟 cancelled）：不顯示失敗、沒有報告', async () => {
    postError = Object.assign(new Error('已取消'), { cancelled: true })
    const w = mountIt()
    await flushPromises()
    await btn(w, '開始偵測').trigger('click')
    await flushPromises()
    expect(w.find('[role=alert]').exists()).toBe(false)
    expect(w.find('.tiles').exists()).toBe(false)
  })

  it('偵測失敗要顯示原因，不吞掉', async () => {
    postError = { data: { detail: '一次最多偵測 200 台' } }
    const w = mountIt()
    await flushPromises()
    await btn(w, '開始偵測').trigger('click')
    await flushPromises()
    expect(w.find('[role=alert]').text()).toContain('一次最多偵測 200 台')
  })

  it('標下線要二次確認，且只帶連不上的序號、原因預填含報告編號', async () => {
    const w = mountIt()
    await flushPromises()
    await btn(w, '開始偵測').trigger('click')
    await flushPromises()
    await btn(w, '標記下線')!.trigger('click')
    expect(w.find('.modal-stub').exists()).toBe(false)               // 第一下只出警語，不直接開
    expect(w.find('.warnbox').text()).toContain('連不上不等於下線')
    expect(w.find('.warnbox').text()).toContain('待 CIA 異動')
    await btn(w, '取消')!.trigger('click')
    expect(w.find('.modal-stub').exists()).toBe(false)
    await btn(w, '標記下線')!.trigger('click')
    await btn(w, '我確認過了')!.trigger('click')
    const m = w.find('.modal-stub')
    expect(m.attributes('data-serials')).toBe('A-2,A-3')
    expect(m.attributes('data-reason')).toContain('報告 #7')
    expect(calls.some(c => String(c.url).includes('batch-status'))).toBe(false)   // 本元件自己不寫待異動，交給既有 Modal
  })

  it('歷史報告：清單可重開，數字與列數一致', async () => {
    const w = mountIt([], 0)
    await flushPromises()
    expect(w.text()).toContain('尚未選主機')
    expect(btn(w, '開始偵測')).toBeUndefined()
    await btn(w, '看報告')!.trigger('click')
    await flushPromises()
    expect(w.findAll('.rep tr.r').length).toBe(ROWS.length)
    expect(w.text()).toContain('報告 #7')
  })
})

describe('hosts.vue 接線', () => {
  const src = readFileSync(resolve(__dirname, '../pages/v2/hosts.vue'), 'utf8')
  it('附加頁籤、按鈕與元件都接上，批次查詢／重複登記沒被動到', () => {
    expect(src).toMatch(/\{ k: 'alive', label: '偵測存活' \}/)
    expect(src).toMatch(/<V2HostAlive v-else-if="extraTab === 'alive'" :serials="aliveSerials" :vmware="aliveVmware" \/>/)
    expect(src).toMatch(/<V2HostBatchLookup v-if="extraTab === 'batch'" \/>/)
    expect(src).toMatch(/<V2HostDuplicates v-else-if="extraTab === 'dups'" \/>/)
    expect(src).toMatch(/v-if="!isViewer" type="button" class="btn2" :disabled="!pickedRows.length" @click="goAlive"/)
  })
  it('前端程式碼從不組 ips 送偵測', () => {
    const comp = readFileSync(resolve(__dirname, '../components/v2/HostAlive.vue'), 'utf8')
    expect(comp).not.toMatch(/\bips\b/)
    expect(comp).not.toContain('/api/tools/alive-check')
  })
})
