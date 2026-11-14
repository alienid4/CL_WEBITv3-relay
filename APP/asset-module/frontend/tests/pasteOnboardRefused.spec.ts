// 2.058.18：「取得納管指令」不發的主機（VMware、不是 IPv4…）逐台列原因；全部都不發（400）時列原因，不只一句錯誤。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

enableAutoUnmount(afterEach)
let issueResp: any = null
let issueErr: any = null
let statusCalls: string[] = []

beforeEach(() => {
  issueResp = null; issueErr = null; statusCalls = []
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useClipboard', () => ({ copy: async () => true }))
  vi.stubGlobal('useSort', (rows: any) => ({ sortKey: ref('ip'), sortDir: ref('asc'), toggle: () => {}, sorted: rows }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string) => {
    if (url === '/api/onboard/paste/issue') { if (issueErr) throw issueErr; return issueResp }
    if (url.startsWith('/api/onboard/paste/status')) { statusCalls.push(url); return { rows: [] } }
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())

const stubs = { InfoNote: { template: '<i />' }, SortTh: { template: '<th><slot /></th>' }, NuxtLink: { template: '<a><slot /></a>' } }
async function mountIt(ips: string[]) {
  const C = (await import('../components/PasteOnboardModal.vue')).default
  const w = mount(C, { props: { ips }, global: { stubs } })
  await flushPromises()
  return w
}

describe('取得納管指令：不發的主機', () => {
  it('部分不發：逐台列原因，偵測狀態只列發了的', async () => {
    issueResp = { filename: 'webit3_onboard.sh', sha256: 'abc', version: '1', one_liner: 'curl x', expires_in: 1800,
      issued: ['10.99.0.12'], refused: [{ ip: '10.99.0.11', reason: 'VMware 設備不可納管，沒有登入：ESXi' },
        { ip: '64:ff9b::a63:b', reason: '只接受 IPv4 位址' }] }
    const w = await mountIt(['10.99.0.12', '10.99.0.11', '64:ff9b::a63:b'])
    const rows = w.findAll('[data-test="paste-refused-row"]').map(r => r.text())
    expect(rows).toEqual(['R110.99.0.11VMware 設備不可納管，沒有登入：ESXi', 'R264:ff9b::a63:b只接受 IPv4 位址'])
    expect(w.text()).toContain('偵測狀態')
    expect(statusCalls).toEqual([`/api/onboard/paste/status?ips=${encodeURIComponent('10.99.0.12')}`])
  })
  it('全部都不發（400）：列出每台原因，不是只有一句錯誤；不輪詢', async () => {
    issueErr = Object.assign(new Error('400'), { data: { detail: { message: '這批主機都不能發納管指令：10.99.0.11：VMware 設備不可納管',
      refused: [{ ip: '10.99.0.11', reason: 'VMware 設備不可納管，沒有登入：ESXi' }] } } })
    const w = await mountIt(['10.99.0.11'])
    expect(w.find('[data-test="paste-err"]').text()).toContain('這批主機都不能發納管指令')
    expect(w.findAll('[data-test="paste-refused-row"]').map(r => r.text())).toEqual(['R110.99.0.11VMware 設備不可納管，沒有登入：ESXi'])
    expect(w.text()).not.toContain('[object Object]')
    expect(w.text()).not.toContain('偵測狀態')
    expect(statusCalls).toEqual([])
  })
  it('舊格式的字串錯誤照舊顯示', async () => {
    issueErr = Object.assign(new Error('400'), { data: { detail: '一次最多 500 台' } })
    const w = await mountIt(['10.99.0.12'])
    expect(w.find('[data-test="paste-err"]').text()).toContain('一次最多 500 台')
    expect(w.find('[data-test="paste-refused"]').exists()).toBe(false)
  })
})
