// 批 23 第 6、7 片（V17）：6.1 待納管「批次試密碼」「預檢」用的共用視窗——密碼不預填、送出後（成功或失敗）都清空、
// 畫面與 console 不出現密碼；能勾哪幾台＝舊頁 canProbe。全假資料、假密碼。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NO_ONBOARD_TABS, canProbe } from '../utils/adoptCollect'

const FAKE_PW = 'Fake#Leak-Pw-771'
enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let fail = false
const logs: string[] = []
beforeEach(() => {
  calls = []; fail = false; logs.length = 0
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, onBeforeUnmount, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: (m: string) => { logs.push(String(m)) } }))
  vi.stubGlobal('useSort', (rows: any) => ({ sortKey: ref('no'), sortDir: ref('asc'), toggle: () => {}, sorted: rows }))
  for (const k of ['log', 'info', 'warn', 'error', 'debug'] as const) vi.spyOn(console, k).mockImplementation((...a: any[]) => { logs.push(a.map(String).join(' ')) })
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts: JSON.parse(JSON.stringify(opts)) })
    if (fail) throw Object.assign(new Error('boom'), { data: { detail: '登入被拒' } })
    if (url === '/api/onboard/batch-probe') return { total: 1, matched: 1, unmatched: 0, results: [{ ip: '10.99.0.1', matched_password_index: 0, platform: 'linux', uid: 0, has_sudo: true, error: null }] }
    if (url === '/api/onboard/precheck') return { results: [{ no: 1, ip: '10.99.0.1', verdict: '可以納管', reason: '' }], counts: {} }
    return {}
  } }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const stubs = { InfoNote: { template: '<i />' }, SortTh: { template: '<th><slot /></th>' } }

async function mk(name: 'BatchProbeModal' | 'PrecheckModal') {
  const C = (await import(`../components/${name}.vue`)).default
  const w = mount(C, { props: { ips: ['10.99.0.1'] }, global: { stubs } })
  await flushPromises()
  return w
}
const pwInputs = (w: any) => w.findAll('input[type=password]')
const runBtn = (w: any) => w.findAll('button').find((b: any) => /比對|試|預檢|開始/.test(b.text()) && !/取消|關閉/.test(b.text()))!

for (const name of ['BatchProbeModal', 'PrecheckModal'] as const) {
  describe(`${name}：密碼不落地`, () => {
    for (const bad of [false, true]) {
      it(`${bad ? '失敗' : '成功'}後密碼框清空、畫面與 console 沒有密碼；密碼只在那一次請求的 body 裡`, async () => {
        const w = await mk(name)
        expect(pwInputs(w).length).toBeGreaterThan(0)
        for (const i of pwInputs(w)) expect((i.element as HTMLInputElement).value).toBe('')   // 不預填
        await pwInputs(w)[0].setValue(FAKE_PW)
        fail = bad
        await runBtn(w).trigger('click'); await flushPromises()
        expect(calls.length).toBe(1)
        expect(JSON.stringify(calls[0].opts.body)).toContain(FAKE_PW)
        expect(calls[0].url).not.toContain(FAKE_PW)
        for (const i of pwInputs(w)) expect((i.element as HTMLInputElement).value).toBe('')
        expect(w.html()).not.toContain(FAKE_PW)
        expect(logs.join('\n')).not.toContain(FAKE_PW)
      })
    }
  })
}

describe('canProbe（舊頁同一套，fail-closed）', () => {
  const base = { status: 'needs_credential', route: 'ssh', tab: 'ssh', guide: null }
  it('要憑證＋SSH 才可以', () => {
    expect(canProbe(base)).toBe(true)
    expect(canProbe({ ...base, status: 'onboard_failed' })).toBe(true)
    expect(canProbe({ ...base, status: 'collected' })).toBe(false)
    expect(canProbe({ ...base, route: 'winrm' })).toBe(false)
    expect(canProbe({ ...base, guide: { kind: 'x' } })).toBe(false)
  })
  it('VMware／儲存／網路…、Windows 頁簽、AS400、上次被 VMware 擋下的一律不給', () => {
    for (const t of NO_ONBOARD_TABS) expect(canProbe({ ...base, tab: t })).toBe(false)
    for (const t of ['esxi', 'storage', 'network', 'ocp', 'nok_other', 'import', 'agent']) expect(canProbe({ ...base, tab: t })).toBe(false)
    expect(canProbe({ ...base, tab: 'winrm' })).toBe(false)
    expect(canProbe({ ...base, tab: 'mainframe' })).toBe(false)
    expect(canProbe({ ...base, tab: 'mainframe', mainframe_onboardable: true })).toBe(true)
    expect(canProbe({ ...base, last_fail_short: 'VMware 不可納管' })).toBe(false)
  })
})
