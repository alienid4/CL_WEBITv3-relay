/**
 * 2.048.0 V13＋V15 帳號合規矩陣（V2 原生）實掛：
 *   - 寫入（處置下拉）只給一般帳號以上；只能看的帳號看到純文字
 *   - 儀表板中招卡帶來的 col/val：合規表筆數＝卡片數字（同一份 accountCells 判定）
 *   - 條件一改就重抓：先送的晚回來不能蓋掉後送的（useLatest）
 *   - 核准例外沒填完：不送出
 * Nuxt 自動匯入以 stubGlobal 提供；V2Page／V2Table 為簡化替身；API 全假。
 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLatest } from '../composables/useLatest'
import AccountMatrix from '../components/v2/AccountMatrix.vue'

const route = reactive<{ path: string; query: Record<string, string> }>({ path: '/v2/inventory/matrix', query: {} })
let pending: { url: string; opts: any; resolve: (v: any) => void; reject: (e: any) => void }[] = []
let writer = true
let admin = false
const toQuery = (q: Record<string, any>) => Object.fromEntries(Object.entries(q || {}).filter(([, v]) => v !== undefined && v !== '')) as any
beforeEach(() => {
  route.query = {}
  pending = []
  writer = true
  admin = false
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted, onBeforeUnmount, useLatest })) vi.stubGlobal(k, v)
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({
    replace: (o: any) => { route.query = toQuery(o.query) },
    push: (o: any) => { route.query = toQuery(o.query) },
    resolve: (o: any) => ({ fullPath: `${o.path}?${new URLSearchParams(toQuery(o.query)).toString()}` }),
  }))
  vi.stubGlobal('useAuth', () => ({ canWrite: ref(writer), isViewer: ref(!writer), isAdmin: ref(admin) }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('httpReason', async () => '')
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts: any) => new Promise((resolve, reject) => pending.push({ url, opts, resolve, reject })) }))
})
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

// 替身：每列一個 .row，有具名插槽就用插槽，否則印 text(r)／原值
const V2Table = defineComponent({ props: ['rows', 'cols', 'prefix'], setup(p, { slots }) {
  return () => h('div', { 'data-prefix': p.prefix }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))))
} })
const V2Page = defineComponent({ props: ['tabs'], setup(p, { slots }) { return () => h('div', [h('nav', (p.tabs ?? []).map((t: any) => h('span', { class: `tab-${t.k}` }, t.label))), slots.title?.(), slots.conclusion?.(), slots.default?.(), slots.tab?.()]) } })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = { global: { components: { V2Table, V2Page, InfoNote: pass, NuxtLink: pass, V2AccountDashboard: pass } } }

const acc = (over: Record<string, any>) => ({
  id: 1, ip: '10.99.0.1', hostname: 'h1', asset_serial: null, username: 'u1', uid: 1000, kind: 'human', shell: '/bin/bash',
  gecos: null, note: null, last_login: null, never_logged_in: 0, pw_status: 'PS', pw_last_change: null, pw_max_days: null, pw_expires: null,
  pw_expiry_status: 'valid', is_sudoer: 0, sudo_nopasswd: 0, priv_groups: null, authorized_keys: 0, gone_at: null, os_bucket: 'linux', ...over,
})
const ACCS = [
  acc({ id: 1, username: 'a', pw_expiry_status: 'never' }),
  acc({ id: 2, username: 'b', pw_expiry_status: 'never' }),
  acc({ id: 3, username: 'c', pw_expiry_status: 'valid' }),
]
const FIND = { id: 7, rule_id: 'R1', label: '密碼永不過期', severity: 'high', verdict: 'fail', law: '規範', username: 'a', ip: '10.99.0.1', hostname: 'h1',
  asset_serial: null, detail: 'x', gecos: null, note: null, status: 'open', exempt_until: null, decided_by: null, decided_at: null, contradiction: false, os_bucket: 'linux' }
function answer(findings: any[] = [FIND], accounts: any[] = ACCS) {
  for (const p of pending.splice(0)) {
    if (p.url === '/api/accounts/findings') p.resolve({ items: findings, summary: { has_data: true } })
    else if (p.url === '/api/accounts') p.resolve({ items: accounts, hidden_builtin: 0 })
    else if (p.url === '/api/accounts/coverage') p.resolve({ managed_hosts: 3, collected_managed: 1, collected_outside: 0, pct: 33 })
    else p.resolve({ items: [] })
  }
}
async function settle(findings?: any[], accounts?: any[]) {
  for (let i = 0; i < 4; i++) { await flushPromises(); answer(findings, accounts) }
  await flushPromises()
}

describe('帳號合規矩陣', () => {
  it('中招卡帶 col/val：合規表筆數＝符合那個值的帳號數', async () => {
    route.query = { col: 'pwExpired', val: 'never' }
    const w = mount(AccountMatrix, opts)
    await settle()
    expect(w.find('[data-prefix="M"]').findAll('.row').length).toBe(2)
  })

  it('稽核發現：一般帳號有處置下拉，只能看的帳號沒有', async () => {
    route.query = { tab: 'findings' }
    let w = mount(AccountMatrix, opts)
    await settle()
    expect(w.find('select[aria-label="處置"]').exists()).toBe(true)
    w.unmount()
    writer = false
    w = mount(AccountMatrix, opts)
    await settle()
    expect(w.find('select[aria-label="處置"]').exists()).toBe(false)
    expect(w.find('.c-st_t').text()).toContain('待處理')
  })

  it('換風險等級：先送的晚回來不能蓋掉後送的', async () => {
    route.query = { tab: 'findings' }
    const w = mount(AccountMatrix, opts)
    await settle()
    route.query = { tab: 'findings', sev: 'medium' }
    await nextTick()
    const older = pending.splice(0)
    route.query = { tab: 'findings', sev: 'high' }
    await nextTick()
    await settle([FIND, { ...FIND, id: 8, username: 'b' }])
    for (const p of older) p.resolve(p.url === '/api/accounts' ? { items: ACCS } : { items: [], summary: { has_data: true } })
    await flushPromises()
    expect(w.find('[data-prefix="F"]').findAll('.row').length).toBe(2)
  })

  it('核准例外沒填理由：不送出、選單退回原值', async () => {
    route.query = { tab: 'findings' }
    const w = mount(AccountMatrix, opts)
    await settle()
    const prompts = ['2026-12-31', '']
    vi.stubGlobal('prompt', () => prompts.shift())
    window.prompt = (() => prompts.shift()) as any
    const sel = w.find('select[aria-label="處置"]')
    ;(sel.element as HTMLSelectElement).value = 'exception'
    await sel.trigger('change')
    await flushPromises()
    expect(pending.some(p => p.url === '/api/accounts/findings/disposition')).toBe(false)
    expect((sel.element as HTMLSelectElement).value).toBe('open')
  })
})

describe('2.048.2 GPT 審查修正', () => {
  it('頁籤數字＝點進去的筆數（套了 OS 條件）', async () => {
    route.query = { os: 'windows', tab: 'matrix' }
    const accs = [...ACCS, acc({ id: 9, username: 'w', os_bucket: 'windows', os_family: 'windows' })]
    const w = mount(AccountMatrix, opts)
    await settle([FIND, { ...FIND, id: 8, username: 'w', os_bucket: 'windows' }], accs)
    expect(w.find('.tab-matrix').text()).toBe('合規表 1')
    expect(w.find('.tab-findings').text()).toBe('稽核發現 1')
    expect(w.find('.tab-accounts').text()).toBe('帳號清單 1')
    expect(w.find('[data-prefix="M"]').findAll('.row').length).toBe(1)
  })

  it('後端匯出只給管理員：一般帳號看到限管理員、沒有匯出鈕', async () => {
    route.query = { tab: 'findings' }
    let w = mount(AccountMatrix, opts)
    await settle()
    expect(w.find('a[href*="/api/accounts/findings/export"]').exists()).toBe(false)
    expect(w.find('[data-test="admin-only"]').exists()).toBe(true)
    w.unmount()
    admin = true
    w = mount(AccountMatrix, opts)
    await settle()
    expect(w.find('a[href*="/api/accounts/findings/export"]').exists()).toBe(true)
  })

  it('關掉帳號反查後，舊請求晚回來不會又打開', async () => {
    route.query = { tab: 'matrix' }
    const w = mount(AccountMatrix, opts)
    await settle()
    await w.find('input[aria-label="跳到某一台或查帳號"]').setValue('svc')
    await w.findAll('button').find(b => b.text() === '查帳號在哪幾台')!.trigger('click')
    expect(w.find('[data-test="acct-hosts"]').exists()).toBe(true)
    await w.find('[data-test="acct-close"]').trigger('click')
    const p = pending.find(x => x.url === '/api/accounts/by-username')!
    p.resolve({ items: [{ ip: '10.99.0.1', hostname: 'h1', username: 'svc', registered: true }] })
    await flushPromises()
    expect(w.find('[data-test="acct-hosts"]').exists()).toBe(false)
  })
})

describe('帳號儀表板', () => {
  it('中招卡點下去：網址只帶那張卡的條件（數字＝筆數）', async () => {
    const { default: Dash } = await import('../components/v2/AccountDashboard.vue')
    const went: string[] = []
    vi.stubGlobal('navigateTo', (to: string) => { went.push(to) })
    const w = mount(Dash, opts)
    for (let i = 0; i < 4; i++) {
      await flushPromises()
      for (const p of pending.splice(0)) {
        if (p.url === '/api/accounts') p.resolve({ items: ACCS })
        else if (p.url === '/api/accounts/inventoried-hosts') p.resolve({ items: [], os_counts: { linux: 1 } })
        else if (p.url === '/api/accounts/findings') p.resolve({ items: [FIND], summary: { has_data: true, fail_high: 1 } })
        else p.resolve({ items: [] })
      }
    }
    await flushPromises()
    const card = w.find('[data-test="check-pwExpired:never"]')
    expect(card.text()).toContain('2')
    await card.find('button').trigger('click')
    expect(went[0]).toBe('/v2/inventory/matrix?col=pwExpired&val=never&os=linux')
  })
})
