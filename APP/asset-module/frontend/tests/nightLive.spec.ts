/**
 * 夜間速查（/v2/night）自動即時檢測的互動／stub 測試。
 * 以前 tests/asset_module/test_v2_night.py 只讀檔比對字串，證明不了行為；這裡 mock apiFetch 實際跑判斷邏輯。
 * 頁面的 liveCheck 就是：decideLive → (run) → runLiveCheck，所以這裡用同一組函式組出等價流程。
 */
import { describe, expect, it, vi } from 'vitest'
import { decideLive, fetchDownImpact, fetchPhones, resolveAutoLive, runLiveCheck } from '../composables/useNightLive'

type Settings = { auto_live: boolean } | Error

// 模擬一個可變的設定來源＋記錄所有呼叫的 apiFetch
function makeApi(initial: Settings) {
  const state = { settings: initial }
  const calls: { url: string; opts?: any }[] = []
  const apiFetch = vi.fn(async (url: string, opts?: any) => {
    calls.push({ url, opts })
    if (url === '/api/night/settings') {
      if (state.settings instanceof Error) throw state.settings
      return state.settings
    }
    if (url === '/api/health-check/run') return { items: [] }
    if (url === '/api/contacts/card') return { people: {}, maint: {}, oncall: [] }
    if (url === '/api/blast/impact') return { dependencies: [] }
    throw new Error('unexpected ' + url)
  })
  const hits = (u: string) => calls.filter(c => c.url === u).length
  return { state, calls, apiFetch: apiFetch as any, hits }
}

// 頁面 liveCheck 的等價流程：回傳是否真的打出即時檢測
async function liveCheck(api: ReturnType<typeof makeApi>, p: { vmware: boolean; manual: boolean }) {
  const dec = await decideLive(api.apiFetch, { isVmware: p.vmware, manual: p.manual })
  if (dec.action !== 'run') return dec
  await runLiveCheck(api.apiFetch, ['10.99.0.1'], 14000)
  return dec
}

describe('夜間速查：自動即時檢測', () => {
  it('(a) 設定 on：查主機會呼叫 /api/health-check/run（POST、帶逾時）', async () => {
    const api = makeApi({ auto_live: true })
    const dec = await liveCheck(api, { vmware: false, manual: false })
    expect(dec.action).toBe('run')
    expect(api.hits('/api/health-check/run')).toBe(1)
    const call = api.calls.find(c => c.url === '/api/health-check/run')!
    expect(call.opts.method).toBe('POST')
    expect(call.opts.body).toEqual({ ips: ['10.99.0.1'] })
    expect(call.opts.timeout).toBe(14000)
  })

  it('(b) 設定 off：不呼叫即時檢測，狀態是 manual（只查資料庫）', async () => {
    const api = makeApi({ auto_live: false })
    const dec = await liveCheck(api, { vmware: false, manual: false })
    expect(dec.action).toBe('manual')
    expect(dec.msg).toBe('')
    expect(api.hits('/api/health-check/run')).toBe(0)
  })

  it('(c) 設定讀取丟錯：fail-closed，不呼叫且訊息要求按立即檢測；resolveAutoLive 回 failed', async () => {
    const api = makeApi(new Error('boom'))
    const dec = await liveCheck(api, { vmware: false, manual: false })
    expect(dec.action).toBe('manual')
    expect(dec.msg).toBe('無法讀取設定，請按立即檢測')
    expect(api.hits('/api/health-check/run')).toBe(0)
    expect(await resolveAutoLive(async () => { throw new Error('x') })).toEqual({ on: false, failed: true })
    // 設定缺欄位（auto_live 未給）沿用後端預設＝開；明確 false 才關
    expect(await resolveAutoLive(async () => ({}))).toEqual({ on: true, failed: false })
    expect(await resolveAutoLive(async () => ({ auto_live: false }))).toEqual({ on: false, failed: false })
  })

  it('(d) 先 on 後 off（同一頁面第二次查詢）：第二次不呼叫，證明每次都重讀、沒有舊快取', async () => {
    const api = makeApi({ auto_live: true })
    await liveCheck(api, { vmware: false, manual: false })
    expect(api.hits('/api/health-check/run')).toBe(1)
    api.state.settings = { auto_live: false }
    const dec = await liveCheck(api, { vmware: false, manual: false })
    expect(dec.action).toBe('manual')
    expect(api.hits('/api/night/settings')).toBe(2)          // 兩次查詢各讀一次設定
    expect(api.hits('/api/health-check/run')).toBe(1)        // 第二次沒有新增
    // 反向：先 off 後 on 也立刻生效
    api.state.settings = { auto_live: true }
    await liveCheck(api, { vmware: false, manual: false })
    expect(api.hits('/api/health-check/run')).toBe(2)
  })

  it('(e) VMware 設備：不論開關、不論自動或手動，都不呼叫即時檢測，也不必讀設定', async () => {
    for (const s of [{ auto_live: true }, { auto_live: false }, new Error('x')] as Settings[]) {
      for (const manual of [false, true]) {
        const api = makeApi(s)
        const dec = await liveCheck(api, { vmware: true, manual })
        expect(dec.action).toBe('vmware')
        expect(api.hits('/api/health-check/run')).toBe(0)
        expect(api.hits('/api/night/settings')).toBe(0)
      }
    }
  })

  it('(f) 電話／影響範圍／依賴只呼叫只讀端點（GET、無 body、mode=probe），不碰檢測也不碰設定', async () => {
    const api = makeApi({ auto_live: true })
    await fetchPhones(api.apiFetch, { asset_serial: 'S1' })
    await fetchPhones(api.apiFetch, { api_id: 'A-01' })
    await fetchDownImpact(api.apiFetch, 'hw:abc')
    const urls = new Set(api.calls.map(c => c.url))
    expect(urls).toEqual(new Set(['/api/contacts/card', '/api/blast/impact']))
    for (const c of api.calls) {
      expect(c.opts?.method).toBeUndefined()      // 預設 GET
      expect(c.opts?.body).toBeUndefined()
    }
    expect(api.calls.find(c => c.url === '/api/blast/impact')!.opts.query).toEqual({ node_id: 'hw:abc', mode: 'probe', depth: 3 })
    expect(api.hits('/api/health-check/run')).toBe(0)
    expect(api.hits('/api/night/settings')).toBe(0)
  })

  it('(g) 手動「立即檢測」才呼叫：設定關閉或讀不到也照樣執行，且不讀設定', async () => {
    for (const s of [{ auto_live: false }, new Error('x')] as Settings[]) {
      const api = makeApi(s)
      const dec = await liveCheck(api, { vmware: false, manual: true })
      expect(dec.action).toBe('run')
      expect(api.hits('/api/health-check/run')).toBe(1)
      expect(api.hits('/api/night/settings')).toBe(0)
    }
  })
})
