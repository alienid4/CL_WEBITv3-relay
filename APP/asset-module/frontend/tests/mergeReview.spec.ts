// 9.1「待人工審核」批次合併（2026-11-18，H16：由資料匯入／RVTools 頁搬到 9.1）。
// 搬移後的硬規則：確認句、POST URL 與 body 一字不改；取消不送；成功後 emit('changed')；
// 9.1 所有角色都進得來，唯讀帳號不可看到可按的合併按鈕（後端權限不變，這裡只是外觀）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/MergeReview.vue'

enableAutoUnmount(afterEach)
const REVIEW = {
  total_open: 5, matchable: 3, remaining: 2, rule: 'short_hostname', rule_label: '短主機名相同', samples: [],
  uuid_rule: { matchable: 4, distinct_assets: 2, rule: 'vm_uuid', rule_label: 'vCenter UUID 相符', samples: [] },
}
let calls: { url: string; opts?: any }[] = []
let canWrite = true
let confirmAnswer = true
let confirmText = ''
const V2Table = defineComponent({ props: ['rows', 'cols'], setup() { return () => h('table') } })

beforeEach(() => {
  calls = []; canWrite = true; confirmAnswer = true; confirmText = ''
  vi.stubGlobal('ref', ref); vi.stubGlobal('computed', computed); vi.stubGlobal('onMounted', onMounted)
  vi.stubGlobal('useToast', () => ({ showToast: vi.fn() }))
  vi.stubGlobal('useAuth', () => ({ canWrite: ref(canWrite) }))
  vi.stubGlobal('confirm', (m: string) => { confirmText = m; return confirmAnswer })
  vi.stubGlobal('useApi', () => ({ apiFetch: (url: string, opts?: any) => {
    calls.push({ url, opts })
    if (url === '/api/merge-reviews/auto-matchable') return Promise.resolve(REVIEW)
    if (url === '/api/merge-reviews/batch-merge') return Promise.resolve({ merged: opts.body.rule === 'vm_uuid' ? 4 : 3, remaining_open: 2 })
    return Promise.resolve({})
  } }))
})
const mnt = () => mount(Comp, { global: { stubs: { InfoNote: true, V2Table } } })
const btns = (w: any) => w.findAll('button.btn')

describe('待人工審核批次合併（搬到 9.1 後仍要守住）', () => {
  it('讀同一支 GET，有兩個合併按鈕', async () => {
    const w = mnt(); await flushPromises()
    expect(calls[0].url).toBe('/api/merge-reviews/auto-matchable')
    expect(btns(w)).toHaveLength(2)
    expect(btns(w)[0].text()).toContain('確認合併這 3 筆')
  })
  it('按下先問：確認句講筆數與配對條件；取消就不送', async () => {
    confirmAnswer = false
    const w = mnt(); await flushPromises()
    await btns(w)[0].trigger('click'); await flushPromises()
    expect(confirmText).toContain('即將把 3 筆併進既有資產')
    expect(confirmText).toContain('配對條件：短主機名相同')
    expect(confirmText).toContain('你填的用途、保管者、機房不會被改動')
    expect(calls.some(c => c.url === '/api/merge-reviews/batch-merge')).toBe(false)
  })
  it('確認後 POST 原本的規則 body，成功後通知上層更新筆數', async () => {
    const w = mnt(); await flushPromises()
    await btns(w)[0].trigger('click'); await flushPromises()
    const post = calls.find(c => c.url === '/api/merge-reviews/batch-merge')!
    expect(post.opts.method).toBe('POST'); expect(post.opts.body).toEqual({ rule: 'short_hostname' })
    expect(w.emitted('changed')).toHaveLength(1)
    expect(w.text()).toContain('已合併 3 筆，剩餘待審 2 筆')
  })
  it('UUID 那條規則送自己的 rule', async () => {
    const w = mnt(); await flushPromises()
    await btns(w)[1].trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/merge-reviews/batch-merge')!.opts.body).toEqual({ rule: 'vm_uuid' })
  })
  it('唯讀帳號：沒有合併按鈕，並講明唯讀', async () => {
    canWrite = false
    const w = mnt(); await flushPromises()
    expect(btns(w)).toHaveLength(0)
    expect(w.find('[data-testid=merge-review-readonly]').exists()).toBe(true)
  })
})
