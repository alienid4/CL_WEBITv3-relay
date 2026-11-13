/**
 * 2.047.2（GPT 複核 1d8f78cd）：快速切條件時，舊請求晚回來不能把新結果蓋掉（畫面條件跟數字會對不起來）。
 * 實掛 ModelSpecs（型號台數下鑽）與 Activity（操作紀錄），API 用手動控制的 Promise：先送 A、再送 B，B 先回、A 後回，
 * 畫面要停在 B。Nuxt 自動匯入以 stubGlobal 提供；V2Table 為簡化替身；無網路。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import ModelSpecs from '../components/v2/ModelSpecs.vue'
import Activity from '../components/v2/Activity.vue'

const route = reactive<{ query: Record<string, string> }>({ query: {} })
let pending: { url: string; opts: any; resolve: (v: any) => void }[] = []

beforeEach(() => {
  route.query = {}
  pending = []
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: any) => { route.query = Object.fromEntries(Object.entries(o.query || {}).filter(([, v]) => v !== undefined && v !== '')) as any } }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(false), isAdmin: ref(true) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useGuardedFetch', () => ({ guardedFetch: () => Promise.resolve({}) }))
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise(resolve => pending.push({ url, opts, resolve })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl', 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, [`${p.prefix}${r.__no} ${r.name ?? r.username ?? ''}`, slots['cell-count']?.({ row: r })])))
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, InfoNote: pass, NuxtLink: pass } } }
const take = (pred: (u: string, o: any) => boolean) => { const i = pending.findIndex(x => pred(x.url, x.opts)); expect(i).toBeGreaterThanOrEqual(0); return pending.splice(i, 1)[0] }

describe('只套用最新那一次請求', () => {
  it('useLatest：新請求開始後，舊的那一次就不算最新', () => {
    const begin = useLatest()
    const a = begin()
    expect(a()).toBe(true)
    const b = begin()
    expect(a()).toBe(false)
    expect(b()).toBe(true)
  })

  it('型號台數下鑽：先點 A 再點 B，A 晚回來不能蓋掉 B', async () => {
    route.query = { ms: 'cov' }
    const w = mount(ModelSpecs, opts)
    await flushPromises()
    take(u => u === '/api/model-specs').resolve({ specs: [], coverage: { rows: [{ device_model: 'A', count: 1, spec: null, keys: ['ka'] }, { device_model: 'B', count: 2, spec: null, keys: ['kb1', 'kb2'] }], matched: 0, total: 3 }, can_edit: false })
    route.query = { ms: 'cov', mm: 'A' }
    await nextTick(); await flushPromises()
    route.query = { ms: 'cov', mm: 'B' }
    await nextTick(); await flushPromises()
    const reqB = take((u, o) => u === '/api/model-specs/coverage-hosts' && o.query.device_model === 'B')
    const reqA = take((u, o) => u === '/api/model-specs/coverage-hosts' && o.query.device_model === 'A')
    reqB.resolve({ items: [{ key: 'kb1', name: 'host-b1' }, { key: 'kb2', name: 'host-b2' }] })
    await flushPromises()
    reqA.resolve({ items: [{ key: 'ka', name: 'host-a' }] })
    await flushPromises()
    const drill = w.find('[data-testid="cov-drill"]').text()
    expect(drill).toContain('型號「B」的機櫃設備 2 台')
    expect(drill).toContain('host-b1')
    expect(drill).not.toContain('host-a')
  })

  it('操作紀錄：先切 7 天再切 30 天，7 天那次晚回來不能蓋掉 30 天', async () => {
    route.query = { ad: '7' }
    const w = mount(Activity, opts)
    await flushPromises()
    route.query = { ad: '30' }
    await nextTick(); await flushPromises()
    const s7 = take(u => u.startsWith('/api/activity/summary?days=7'))
    const l7 = take(u => u.startsWith('/api/activity?') && u.includes('days=7'))
    const s30 = take(u => u.startsWith('/api/activity/summary?days=30'))
    const l30 = take(u => u.startsWith('/api/activity?') && u.includes('days=30'))
    s30.resolve({ days: 30, by_user: [{ username: 'u30', n: 5, last_at: 'x' }], login_failed: 0, total: 5 })
    l30.resolve({ total: 5, limit: 200, offset: 0, rows: [], retain_days: 180 })
    await flushPromises()
    s7.resolve({ days: 7, by_user: [{ username: 'u7', n: 1, last_at: 'x' }], login_failed: 0, total: 1 })
    l7.resolve({ total: 1, limit: 200, offset: 0, rows: [], retain_days: 180 })
    await flushPromises()
    const t = w.text()
    expect(t).toContain('u30')
    expect(t).not.toContain('u7')
    expect(t).toContain('共 5 筆')
  })
})
