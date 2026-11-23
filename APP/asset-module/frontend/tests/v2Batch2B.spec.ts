// 第 2 批畫面改版 B 組（三層分離）：頂端狀態、收合區預設狀態、舊網址相容。V2Table／InfoNote 為簡化替身，全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ScanSchedule from '../components/v2/ScanSchedule.vue'
import AutoOnboard from '../components/v2/AutoOnboard.vue'
import Modules from '../components/v2/Modules.vue'
import Contacts from '../components/v2/Contacts.vue'

enableAutoUnmount(afterEach)
let handler: (url: string, opts: any) => any = () => ({})
let query: Record<string, string> = {}
const Table = defineComponent({ props: ['rows', 'cols', 'empty'], setup(p, { slots }) {
  return () => h('table', {}, (p.rows ?? []).map((r: any) => h('tr', {}, (p.cols ?? []).map((c: any) =>
    h('td', {}, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
beforeEach(() => {
  handler = () => ({})
  query = {}
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => handler(url, opts) }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: async (url: string, opts: any = {}) => handler(url, opts) }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(true) }))
  vi.stubGlobal('useLatest', () => () => () => true)
  vi.stubGlobal('useFeatureFlags', () => ({ flags: ref([]), ensureLoaded: () => {}, setEnabled: async () => {}, isEnabled: () => true }))
  vi.stubGlobal('useRoute', () => ({ query }))
  vi.stubGlobal('useRouter', () => ({ replace: (r: any) => { query = { ...(r.query ?? {}) } } }))
})
afterEach(() => vi.unstubAllGlobals())
const stubs = { InfoNote: { template: '<i />' }, V2Table: Table, NuxtLink: { template: '<a><slot /></a>' }, ScanScope: { template: '<div class="scope" />' } }

describe('掃描排程：頂端狀態、設定預設收合', () => {
  const base = (st: any) => (url: string) => {
    if (url === '/api/scan/status') return st
    if (url === '/api/scan/schedule') return { enabled: true, mode: 'daily', time: '01:00', interval_hours: 6, weekday: 6 }
    if (url === '/api/collect/dispatch/settings') {
      const d = { batch_size: 50, gap_seconds: 120, skip_fresh_hours: 12, stale_days: 7, skip_empty_hours: 168, probe_workers: 64, probe_timeout_ms: 400, heartbeat_stale_minutes: 5, ocp_port_probe: 0 }
      return { ...d, range: Object.fromEntries(Object.keys(d).map(k => [k, [0, 9999]])), default: d }
    }
    if (url === '/api/collect/dispatch/weekly') return { enabled: true, weekday: 5, time: '22:00', targets: '', target_count: 0, range_missing: true, weekday_label: '週六', next_run: null, last_at: null, last_run: null }
    return {}
  }
  it('太久沒掃：顯示過期，不是綠色完成；每週整片沒範圍＝待設定', async () => {
    handler = base({ running: false, status: 'ok', found_count: 3, started_at: '2020-01-01 01:00:00', finished_at: '2020-01-01 01:10:00', trigger: 'schedule', error: null })
    const w = mount(ScanSchedule, { global: { stubs } })
    await flushPromises()
    const line = w.find('[data-test="scan-last-status"]')
    expect(line.text()).toContain('過期')
    expect(line.find('.st').classes()).toContain('warn')
    expect(w.text()).toContain('待設定（沒有範圍，到時間不會跑）')
    expect(w.text()).toContain('啟用｜每日 01:00')
  })
  it('四張設定卡都在折疊區裡（預設不展開）', async () => {
    handler = base({ running: false, status: 'none', started_at: null })
    const w = mount(ScanSchedule, { global: { stubs } })
    await flushPromises()
    const folds = w.findAll('details.fold')
    expect(folds.length).toBe(4)
    expect(folds.every(d => d.attributes('open') === undefined)).toBe(true)
    expect(w.findAll('form').every(f => f.element.closest('details'))).toBe(true)
  })
})

describe('自動納管：最近一筆摘要在上，稽核表收合', () => {
  it('摘要取時間最新的一筆；稽核表在 details 內', async () => {
    handler = (url) => (url === '/api/auto-onboard'
      ? { enabled: true, segments: [{ id: 1, prefix: '10.99.1.', enabled: 1, note: null, created_at: 'x', updated_at: 'y' }],
          recent: [{ target_ip: '10.99.1.5', platform: 'linux', login_user: 'u', ok: 1, stage: null, message: null, created_at: '2026-11-18 01:00:00' },
                   { target_ip: '10.99.1.9', platform: 'linux', login_user: 'u', ok: 0, stage: null, message: '連線逾時', created_at: '2026-11-18 09:00:00' }] }
      : {})
    const w = mount(AutoOnboard, { global: { stubs } })
    await flushPromises()
    const last = w.find('[data-test="ao-last"]').text()
    expect(last).toContain('10.99.1.9')
    expect(last).toContain('失敗')
    const fold = w.find('details.fold')
    expect(fold.attributes('open')).toBeUndefined()
    expect(fold.text()).toContain('10.99.1.5')
  })
})

describe('功能模組：停用登入開關收進「測試用」折疊區', () => {
  it('預設收起、有醒目警示；開關仍在 DOM 內', async () => {
    handler = (url) => (url === '/api/admin/auth-disabled' ? { disabled: false, by_env: false, env_name: 'X' } : url === '/api/admin/write-guard' ? { enabled: false, auth_disabled: false } : {})
    const w = mount(Modules, { global: { stubs } })
    await flushPromises()
    const fold = w.find('details.fold')
    expect(fold.attributes('open')).toBeUndefined()
    expect(fold.find('[data-test="test-warn"]').exists()).toBe(true)
    expect(fold.find('input[aria-label="確認文字"]').exists()).toBe(true)
  })
})

describe('聯絡資訊：第一層只有待補／已登錄；舊網址 ?tab= 仍可用', () => {
  const api = (url: string) => {
    if (url === '/api/contacts/todo') return { missing_count: 0, physical_missing: 0, systems: [], models: [] }
    if (url === '/api/contacts/entries') return { can_edit: false, items: [] }
    if (url === '/api/contacts/oncall') return { items: [] }
    if (url === '/api/contracts') return { current: null, rows: [], history: [] }
    if (url === '/api/contacts/audit') return { items: [] }
    return {}
  }
  it('預設：主列只有兩個頁籤，「更多」收起', async () => {
    handler = api
    const w = mount(Contacts, { global: { stubs } })
    await flushPromises()
    const more = w.find('details.more')
    expect(more.attributes('open')).toBeUndefined()
    const mainChips = Array.from(w.find('input[type=search]').element.parentElement!.querySelectorAll('button.chip')).map(b => b.textContent!.trim())
    expect(mainChips).toEqual(['待補', '已登錄'])
    expect(more.text()).toContain('值班電話')
  })
  it('?tab=audit：「更多」自動展開並停在異動紀錄', async () => {
    handler = api
    query = { tab: 'audit' }
    const w = mount(Contacts, { global: { stubs } })
    await flushPromises()
    expect(w.find('details.more').attributes('open')).toBeDefined()
    expect(w.find('details.more summary').text()).toContain('目前：異動紀錄')
  })
})
