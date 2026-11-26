// 2.1 主機頁直接納管（2.069.3，含 GPT 審查後第二版）：planHost 純判斷（天條與 fail-closed）＋ HostOnboardAction 實掛＋ 6.1 提示查詢＋ OnboardModal 密碼清除。
// 全假資料、無網路。盤的是「實際頁面組裝會餵進來的欄位組合」，不是只灌 vmware／lpar 布林。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { decidePlatform, latestFailure, lookupPendingMiss, pickRecollectSerial, planHost, preBlock, recollectConfirm, recollectOutcome } from '../utils/hostOnboard'
import type { Load, PlanInput } from '../utils/hostOnboard'
import HostOnboardAction from '../components/v2/HostOnboardAction.vue'
import OnboardModal from '../components/OnboardModal.vue'

enableAutoUnmount(afterEach)
const ok = <T>(data: T): Load<T> => ({ st: 'ok', data })
const IP = '10.99.2.83'
const CHK = { [IP]: { ok: true, why: null, serial: 'S-1' } }
const base = (o: Partial<PlanInput> = {}): PlanInput => ({
  vmware: false, osClass: 'Linux', badgeCode: 'LNX', retired: false, assetStatus: '使用中', lpar: false, onboarded: false, collect: null,
  ips: [IP], serials: ['S-1'], hostname: 'host1', os: 'Red Hat Enterprise Linux 9', measuredOs: null, isAdmin: true,
  attempts: ok({}), win: ok({}), check: ok(CHK), pickedIp: '', ...o,
})

describe('planHost：天條（VMware／退役／LPAR）不靠父層單一布林', () => {
  it('m.vmware 沒傳（undefined）但 os_class＝虛擬化平台 → 不納管，沒有任何動作', () => {
    const p = planHost(base({ vmware: undefined, osClass: '虛擬化平台' }))
    expect(p.phase).toBe('blocked')
    expect([p.canOnboard, p.canRetry, p.canReonboard, p.canRecollect]).toEqual([false, false, false, false])
    expect(p.reason).toContain('VMware')
  })
  it('badge.code 是 ESX、或 vmware 為 true，也都擋（已納管的也不給收集）', () => {
    expect(planHost(base({ badgeCode: 'ESX' })).phase).toBe('blocked')
    const p = planHost(base({ vmware: true, onboarded: true, collect: 'fresh' }))
    expect(p.phase).toBe('blocked'); expect(p.canRecollect).toBe(false)
    expect(preBlock({ vmware: true })).toContain('VMware')
  })
  it('vmware 欄位根本沒有（undefined）、其他訊號也正常 → 查不到（fail-closed），不是未納管', () => {
    const p = planHost(base({ vmware: undefined }))
    expect(p.phase).toBe('unknown'); expect(p.canOnboard).toBe(false); expect(p.canReload).toBe(true)
  })
  it('退役：key 是 ret:（retired）或 asset_status 為停用／報廢 → 不給任何動作，含重新收集（「閒置」不擋）', () => {
    for (const o of [{ retired: true }, { assetStatus: '報廢' }, { assetStatus: '停用' }]) {
      const p = planHost(base({ ...o, onboarded: true, collect: 'fresh' }))
      expect(p.phase, JSON.stringify(o)).toBe('blocked'); expect(p.canRecollect).toBe(false); expect(p.canOnboard).toBe(false)
    }
  })
  it('實體 AIX 可納管、明確標示 LPAR 不可（不是「任何 AIX」）', () => {
    const phys = planHost(base({ os: 'AIX 7.2', badgeCode: 'AIX', osClass: 'AIX', lpar: false }))
    expect(phys.canOnboard).toBe(true); expect(phys.platform).toBe('ssh')
    const lpar = planHost(base({ os: 'AIX 7.2', badgeCode: 'AIX', osClass: 'AIX', lpar: true }))
    expect(lpar.phase).toBe('blocked'); expect(lpar.canOnboard).toBe(false); expect(lpar.reason).toContain('HMC')
    // 已納管的 LPAR 照常顯示已納管
    expect(planHost(base({ lpar: true, onboarded: true, collect: 'fresh' })).phase).toBe('onboarded')
  })
  it('伺服器資格查詢說不行（VMware 含反解／退役）→ 不給，原因照寫', () => {
    const p = planHost(base({ check: ok({ [IP]: { ok: false, why: 'VMware 設備不可納管，沒有登入：x', serial: 'S-1' } }) }))
    expect(p.phase).toBe('blocked'); expect(p.canOnboard).toBe(false); expect(p.reason).toContain('VMware')
  })
})

describe('planHost：已納管過期（stale）與查不到', () => {
  it('onboarded=false 但 collect=stale → 顯示已納管＋過期，主動作是收集全部，不是納管', () => {
    const p = planHost(base({ onboarded: false, collect: 'stale' }))
    expect(p.phase).toBe('stale'); expect(p.mgmt).toBe('已納管'); expect(p.collectText).toContain('過期')
    expect(p.canOnboard).toBe(false); expect(p.canRetry).toBe(false)
    expect(p.canRecollect).toBe(true)
    expect(p.canReonboard).toBe(true)          // 次要連結：三份資料都讀到才給
    expect(planHost(base({ onboarded: false, collect: 'stale', win: { st: 'error', err: 'x' } })).canReonboard).toBe(false)
  })
  it('onboarded 缺欄位／null、collect 缺欄位 → 查不到，動作全停用，可重新讀取', () => {
    for (const o of [{ onboarded: null }, { onboarded: undefined }, { collect: undefined }]) {
      const p = planHost(base(o as any))
      expect(p.phase, JSON.stringify(o)).toBe('unknown'); expect(p.mgmt).toBe('納管狀態查不到')
      expect([p.canOnboard, p.canRetry, p.canRecollect]).toEqual([false, false, false]); expect(p.canReload).toBe(true)
    }
  })
  it('失敗紀錄／Windows 狀態／資格查詢任何一個讀取失敗 → 查不到，不能納管（fail-closed）', () => {
    for (const k of ['attempts', 'win', 'check'] as const) {
      const p = planHost(base({ [k]: { st: 'error', err: '逾時' } }))
      expect(p.phase, k).toBe('unknown'); expect(p.canOnboard).toBe(false); expect(p.canReload).toBe(true); expect(p.reason).toContain('逾時')
    }
    expect(planHost(base({ check: { st: 'loading' } })).phase).toBe('loading')
  })
})

describe('planHost：平台、Windows 狀態、多 IP、權限', () => {
  it('實測 OS 優先於 CIA 登記：CIA 寫 Linux、實測 Windows → Windows；CIA 寫 Windows、實測 Linux → SSH', () => {
    expect(planHost(base({ os: 'Red Hat Enterprise Linux 9', measuredOs: 'Windows Server 2022' })).platform).toBe('windows')
    expect(planHost(base({ os: 'Windows Server 2019', measuredOs: 'Red Hat Enterprise Linux 9' })).platform).toBe('ssh')
    expect(decidePlatform({ ip: IP, winOptedIn: true, measuredOs: 'RHEL' }).platform).toBe('windows')   // Windows 納管狀態最優先
  })
  it('什麼證據都沒有（OS 空白）→ 不猜，不給納管、寫原因', () => {
    const p = planHost(base({ os: '', measuredOs: null }))
    expect(p.canOnboard).toBe(false); expect(p.platform).toBeNull(); expect(p.reason).toContain('6.1')
  })
  it('Windows 視窗的 opted_in：未納管＝false；納管失敗且 Windows 狀態有納管過＝true', () => {
    expect(planHost(base({ measuredOs: 'Windows Server 2022' })).optedIn).toBe(false)
    const f = planHost(base({ measuredOs: 'Windows Server 2022', win: ok({ [IP]: { opted_in: true } }),
      attempts: ok({ [IP]: { ok: 0, platform: 'windows', fail: { at: '2026-11-20 10:00:00', short: 'x' } } }) }))
    expect(f.phase).toBe('failed'); expect(f.optedIn).toBe(true); expect(f.canRetry).toBe(true); expect(f.canOnboard).toBe(false)
  })
  it('重新收集只給管理員；一般 user／viewer 看不到，已納管的納管狀態仍顯示', () => {
    const p = planHost(base({ onboarded: true, collect: 'fresh', isAdmin: false }))
    expect(p.phase).toBe('onboarded'); expect(p.canRecollect).toBe(false); expect(p.reason).toContain('管理員')
    expect(planHost(base({ isAdmin: false })).canOnboard).toBe(false)
  })
  it('多 IP：沒選就不能按（needPick）；只認明確選的 IP；選了不在清單的 IP 也不算', () => {
    const two = { ips: ['10.99.2.83', '10.99.2.84'], check: ok({ '10.99.2.83': { ok: true, serial: 'S-1' }, '10.99.2.84': { ok: true, serial: 'S-1' } }) }
    const p0 = planHost(base({ ...two, pickedIp: '' }))
    expect(p0.needPick).toBe(true); expect(p0.canOnboard).toBe(false); expect(p0.targetIp).toBe('')
    expect(planHost(base({ ...two, pickedIp: '10.99.9.9' })).canOnboard).toBe(false)
    const p1 = planHost(base({ ...two, pickedIp: '10.99.2.84' }))
    expect(p1.canOnboard).toBe(true); expect(p1.targetIp).toBe('10.99.2.84')
  })
  it('失敗紀錄跨 IP 依時間取最新，不是 IP 陣列第一個', () => {
    const at = { 'a': { ok: 0, fail: { at: '2026-11-01 10:00:00', short: '舊' } }, 'b': { ok: 0, fail: { at: '2026-11-19 10:00:00', short: '新' } }, 'c': { ok: 1, fail: null } }
    expect(latestFailure(['a', 'b', 'c'], at as any)?.ip).toBe('b')
    expect(latestFailure(['c'], at as any)).toBeNull()
  })
})

describe('重新收集：序號挑選、確認句、回應判斷', () => {
  it('多登記：第一筆沒 IP、第二筆有 IP → 用第二筆', () => {
    const chk = ok({ [IP]: { ok: true, serial: 'S-2' } })
    expect(pickRecollectSerial(['S-1', 'S-2'], [IP], '', chk)).toEqual({ serial: 'S-2', note: '' })
    const p = planHost(base({ onboarded: true, collect: 'fresh', serials: ['S-1', 'S-2'], check: chk }))
    expect(p.recollectSerial).toBe('S-2')
  })
  it('對不上時退回第一個並註明；單筆登記不註明', () => {
    const r = pickRecollectSerial(['S-1', 'S-2'], [IP], '', ok({}))
    expect(r.serial).toBe('S-1'); expect(r.note).toBe('此台有多筆登記，收集使用序號 S-1')
    expect(pickRecollectSerial(['S-1'], [IP], '', ok({})).note).toBe('')
  })
  it('多筆登記要等 IP→序號對照讀完才給收集（不先用第一筆）', () => {
    expect(planHost(base({ onboarded: true, collect: 'fresh', serials: ['S-1', 'S-2'], check: { st: 'loading' } })).canRecollect).toBe(false)
  })
  it('確認句寫出主機名、IP、序號；回應 missing 或 0 台＝失敗', () => {
    const t = recollectConfirm('host1', IP, 'S-1')
    expect(t).toContain('host1'); expect(t).toContain(IP); expect(t).toContain('S-1')
    expect(recollectOutcome({ added: 1, already: 0, missing: [] }).ok).toBe(true)
    expect(recollectOutcome({ added: 0, already: 1, missing: [] }).ok).toBe(true)
    expect(recollectOutcome({ added: 0, already: 0, missing: ['S-1'] })).toMatchObject({ ok: false })
    expect(recollectOutcome({ added: 0, already: 0 }).ok).toBe(false)
    expect(recollectOutcome({ added: 1, already: 0, missing: ['S-2'] }).ok).toBe(false)   // 有排進去但還有序號查不到，也不算成功
  })
})

// ===== 實掛 =====
let calls: { url: string; opts: any }[] = []
let admin = true
let attemptsOut: any
let winOut: any
let checkOut: any
let collectOut: any
let confirmText = ''
let confirmOk = true
let toasts: { msg: string; kind: string }[] = []
let failOn: string | null = null
const Modal = (name: string) => defineComponent({ name, props: ['ip', 'osGuess', 'rows', 'hostname', 'serial', 'basis'], emits: ['done', 'close'], setup(p) {
  return () => h('div', { class: `stub-${name}`, 'data-ip': p.ip ?? '', 'data-host': p.hostname ?? '', 'data-serial': p.serial ?? '', 'data-basis': p.basis ?? '', 'data-rows': JSON.stringify(p.rows ?? null) })
} })
const stubs = { InfoNote: { template: '<i />' }, OnboardModal: Modal('onboard'), WinOnboardModal: Modal('win') }

beforeEach(() => {
  calls = []; admin = true; attemptsOut = {}; winOut = { items: {} }; checkOut = { items: CHK }; collectOut = { added: 1, already: 0, missing: [] }
  confirmOk = true; confirmText = ''; toasts = []; failOn = null
  for (const [k, v] of Object.entries({ ref, computed, onMounted, watch, reactive, onBeforeUnmount })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: (msg: string, kind: string) => { toasts.push({ msg, kind }) } }))
  vi.stubGlobal('useAuth', () => ({ isAdmin: ref(admin), isViewer: ref(!admin) }))
  vi.stubGlobal('useClipboard', () => ({ copy: async () => true }))
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (failOn && url === failOn) throw { data: { detail: '逾時' } }
    if (url === '/api/onboard/audit/latest-by-ip') return attemptsOut
    if (url === '/api/onboard/windows/state') return winOut
    if (url === '/api/onboard/target-check') return checkOut
    if (url === '/api/onboard/post-collect-batch') return collectOut
    if (url === '/api/credentials') return []
    return {}
  } }))
  vi.stubGlobal('window', Object.assign(window, { confirm: (t: string) => { confirmText = t; return confirmOk } }))
})
afterEach(() => vi.unstubAllGlobals())

async function mk(props: Record<string, any> = {}) {
  const w = mount(HostOnboardAction, { props: { ips: [IP], serials: ['S-1'], hostname: 'host1', os: 'Red Hat Enterprise Linux 9', measuredOs: null, onboarded: false, collect: null, vmware: false, osClass: 'Linux', badgeCode: 'LNX', retired: false, assetStatus: '使用中', lpar: false, ...props }, global: { stubs } })
  await flushPromises()
  return w
}
const FAIL = { [IP]: { ok: 0, platform: 'linux', fail: { at: '2026-11-20 10:00:00', short: '密碼被拒', message: 'Permission denied' } } }

describe('HostOnboardAction 實掛', () => {
  it('未納管：兩行（未納管／最近收集：沒收到）＋「納管」；按下開 6.1 同一個視窗，帶主機名、序號、判定依據', async () => {
    const w = await mk({ measuredOs: 'Red Hat Enterprise Linux 9' })
    expect(w.find('[data-test=hoa-state]').text()).toBe('未納管')
    expect(w.find('[data-test=hoa-collect]').text()).toContain('沒收到')
    await w.find('[data-test=hoa-onboard]').trigger('click')
    const m = w.find('.stub-onboard')
    expect(m.attributes('data-ip')).toBe(IP); expect(m.attributes('data-host')).toBe('host1'); expect(m.attributes('data-serial')).toBe('S-1')
    expect(m.attributes('data-basis')).toContain('實測')
    expect(calls.find(c => c.url === '/api/onboard/audit/latest-by-ip')?.opts).toMatchObject({ method: 'POST', body: { ips: [IP] } })
    expect(calls.find(c => c.url === '/api/onboard/target-check')?.opts).toMatchObject({ query: { ips: IP } })
  })
  it('實測是 Windows（CIA 寫 Linux）→ 開 Windows 視窗，opted_in 是 false（視窗寫「納管」）', async () => {
    const w = await mk({ measuredOs: 'Windows Server 2022' })
    await w.find('[data-test=hoa-onboard]').trigger('click')
    expect(JSON.parse(w.find('.stub-win').attributes('data-rows')!)).toEqual([{ ip: IP, hostname: 'host1', opted_in: false }])
    expect(w.find('.stub-onboard').exists()).toBe(false)
  })
  it('納管失敗：顯示失敗原因＋「重新納管」；Windows 狀態有納管過 → opted_in 帶 true', async () => {
    attemptsOut = { [IP]: { ...FAIL[IP], platform: 'windows' } }; winOut = { items: { [IP]: { opted_in: true } } }
    const w = await mk({ os: 'Windows Server 2019' })
    expect(w.find('[data-test=hoa-state]').text()).toBe('未納管')
    expect(w.find('[data-test=hoa-collect]').text()).toContain('密碼被拒')
    expect(w.find('[data-test=hoa-onboard]').exists()).toBe(false)
    await w.find('[data-test=hoa-retry]').trigger('click')
    expect(JSON.parse(w.find('.stub-win').attributes('data-rows')!)[0]).toMatchObject({ ip: IP, opted_in: true })
  })
  it('失敗紀錄 API 逾時：顯示「納管狀態查不到」，沒有納管按鈕（fail-closed），按「重新讀取」會再查一次', async () => {
    failOn = '/api/onboard/audit/latest-by-ip'
    const w = await mk()
    expect(w.find('[data-test=hoa-state]').text()).toBe('納管狀態查不到')
    expect(w.find('[data-test=hoa-onboard]').exists()).toBe(false); expect(w.find('[data-test=hoa-retry]').exists()).toBe(false)
    expect(w.find('[data-test=hoa-why]').text()).toContain('逾時')
    failOn = null
    await w.find('[data-test=hoa-reload]').trigger('click'); await flushPromises()
    expect(calls.filter(c => c.url === '/api/onboard/audit/latest-by-ip').length).toBe(2)
    expect(w.find('[data-test=hoa-onboard]').exists()).toBe(true)
  })
  it('已納管但過期（onboarded=false、collect=stale）：寫「已納管」＋過期，沒有納管按鈕，主動作是收集全部，次要才有重新納管', async () => {
    const w = await mk({ collect: 'stale' })
    expect(w.find('[data-test=hoa-state]').text()).toBe('已納管')
    expect(w.find('[data-test=hoa-collect]').text()).toContain('過期')
    expect(w.find('[data-test=hoa-onboard]').exists()).toBe(false)
    expect(w.find('[data-test=hoa-recollect]').exists()).toBe(true)
    await w.find('[data-test=hoa-reonboard]').trigger('click')
    expect(w.find('.stub-onboard').attributes('data-ip')).toBe(IP)
  })
  it('已納管：收集全部（確認句有主機名／IP／序號；同一支 API、body 是序號）；排入後按鈕保持停用', async () => {
    vi.useFakeTimers()
    try {
      const w = await mk({ onboarded: true, collect: 'fresh' })
      expect(w.find('[data-test=hoa-onboard]').exists()).toBe(false); expect(w.find('[data-test=hoa-retry]').exists()).toBe(false)
      await w.find('[data-test=hoa-recollect]').trigger('click'); await flushPromises()
      expect(confirmText).toContain('host1'); expect(confirmText).toContain('S-1')
      expect(calls.find(c => c.url === '/api/onboard/post-collect-batch')?.opts).toMatchObject({ method: 'POST', body: { asset_serials: ['S-1'] } })
      expect(toasts.at(-1)).toMatchObject({ kind: 'success' })
      expect((w.find('[data-test=hoa-recollect]').element as HTMLButtonElement).disabled).toBe(true)
      vi.advanceTimersByTime(5100); await flushPromises()
      expect((w.find('[data-test=hoa-recollect]').element as HTMLButtonElement).disabled).toBe(false)
    } finally { vi.useRealTimers() }
  })
  it('重新收集：回應 missing → 顯示失敗、通知上層重讀，不假裝成功；確認按取消就不送', async () => {
    collectOut = { added: 0, already: 0, missing: ['S-1'] }
    const w = await mk({ onboarded: true, collect: 'fresh' })
    await w.find('[data-test=hoa-recollect]').trigger('click'); await flushPromises()
    expect(toasts.at(-1)).toMatchObject({ kind: 'error' }); expect(w.emitted('done')).toBeTruthy()
    confirmOk = false; const n = calls.length
    await w.find('[data-test=hoa-recollect]').trigger('click'); await flushPromises()
    expect(calls.length).toBe(n)
  })
  it('多登記：第一筆沒 IP、第二筆有 IP → 收集用第二筆序號', async () => {
    checkOut = { items: { [IP]: { ok: true, serial: 'S-2' } } }
    const w = await mk({ onboarded: true, collect: 'fresh', serials: ['S-1', 'S-2'] })
    await w.find('[data-test=hoa-recollect]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/onboard/post-collect-batch')?.opts.body).toEqual({ asset_serials: ['S-2'] })
  })
  it('VMware：m.vmware 沒傳但 os_class＝虛擬化平台 → 沒有按鈕、寫原因、一支納管相關 API 都不打', async () => {
    const w = await mk({ vmware: undefined, osClass: '虛擬化平台' })
    expect(w.find('button').exists()).toBe(false); expect(w.find('[data-test=hoa-why]').text()).toContain('VMware'); expect(calls.length).toBe(0)
  })
  it('退役、LPAR：沒有按鈕、寫原因；實體 AIX（lpar=false）有納管按鈕', async () => {
    const w1 = await mk({ retired: true }); expect(w1.find('button').exists()).toBe(false); expect(calls.length).toBe(0)
    const w2 = await mk({ lpar: true }); expect(w2.find('button').exists()).toBe(false); expect(w2.find('[data-test=hoa-why]').text()).toContain('HMC')
    const w3 = await mk({ os: 'AIX 7.2', badgeCode: 'AIX', osClass: 'AIX', lpar: false }); expect(w3.find('[data-test=hoa-onboard]').exists()).toBe(true)
  })
  it('沒有 IP 的未納管主機：寫「這台沒有 IP」、沒有按鈕（不會卡在讀取中）', async () => {
    const w = await mk({ ips: [] })
    expect(w.find('[data-test=hoa-why]').text()).toContain('沒有 IP'); expect(w.find('[data-test=hoa-onboard]').exists()).toBe(false)
  })
  it('一般 user（非管理員）：沒有納管、沒有重新收集', async () => {
    admin = false
    const w = await mk(); expect(w.find('[data-test=hoa-onboard]').exists()).toBe(false); expect(w.find('[data-test=hoa-why]').text()).toContain('管理員')
    const w2 = await mk({ onboarded: true, collect: 'fresh' }); expect(w2.find('[data-test=hoa-recollect]').exists()).toBe(false)
  })
  it('多 IP：有失敗紀錄的預選並顯示在下拉；沒失敗紀錄就要自己選才能按', async () => {
    const two = ['10.99.2.83', '10.99.2.84']
    checkOut = { items: { '10.99.2.83': { ok: true, serial: 'S-1' }, '10.99.2.84': { ok: true, serial: 'S-1' } } }
    attemptsOut = { '10.99.2.84': { ok: 0, platform: 'linux', fail: { at: '2026-11-20 10:00:00', short: '逾時' } } }
    const w = await mk({ ips: two })
    expect((w.find('[data-test=hoa-ip]').element as HTMLSelectElement).value).toBe('10.99.2.84')
    await w.find('[data-test=hoa-retry]').trigger('click')
    expect(w.find('.stub-onboard').attributes('data-ip')).toBe('10.99.2.84')
    attemptsOut = {}
    const w2 = await mk({ ips: two })
    expect(w2.find('[data-test=hoa-onboard]').exists()).toBe(false)
    await w2.find('[data-test=hoa-ip]').setValue('10.99.2.83')
    await w2.find('[data-test=hoa-onboard]').trigger('click')
    expect(w2.find('.stub-onboard').attributes('data-ip')).toBe('10.99.2.83')
  })
})

describe('6.1 待納管篩選為空時的提示（lookupPendingMiss）', () => {
  const mkApi = (machine: any, failures: any, boom: string[] = []) => vi.fn(async (url: string) => {
    if (boom.includes(url)) throw new Error('逾時')
    if (url === '/api/v2/machine') return machine
    if (url === '/api/onboard/failures') return failures
    return {}
  })
  it('其實已納管 → onboarded（帶主機 key）', async () => {
    const api = mkApi({ mode: 'machine', key: 'K1', name: 'h', alive: { onboarded: true } }, { items: [] })
    expect(await lookupPendingMiss(api, IP)).toMatchObject({ state: 'onboarded', key: 'K1' })
    expect(api).toHaveBeenCalledWith('/api/v2/machine', { query: { q: IP } })
  })
  it('已納管但失聯（collect=stale）→ stale，提示去主機頁收集', async () => {
    expect(await lookupPendingMiss(mkApi({ mode: 'machine', key: 'K2', alive: { onboarded: false, collect: { status: 'stale' } } }, { items: [] }), IP)).toMatchObject({ state: 'stale', key: 'K2' })
  })
  it('重複 IP／主機名（candidates）→ candidates 與筆數', async () => {
    const r = await lookupPendingMiss(mkApi({ mode: 'candidates', total: 2, candidates: [{ hostname: 'a' }, { hostname: 'b' }] }, { items: [] }), 'a')
    expect(r).toMatchObject({ state: 'candidates', n: 2, names: ['a', 'b'] })
  })
  it('在納管失敗清單（未解決）→ failed＋原因；已解決的不算', async () => {
    const api = mkApi({ mode: 'none' }, { items: [{ ip: '10.99.2.9', hostname: 'H9', reason: '密碼被拒', resolved: false }] })
    expect(await lookupPendingMiss(api, '10.99.2.9')).toMatchObject({ state: 'failed', reason: '密碼被拒' })
    expect(await lookupPendingMiss(api, 'h9')).toMatchObject({ state: 'failed' })
    expect((await lookupPendingMiss(mkApi({ mode: 'none' }, { items: [{ ip: '10.99.2.9', reason: 'x', resolved: true }] }), '10.99.2.9')).state).toBe('none')
  })
  it('查詢出錯 → error（不是「沒有」）；兩邊都正常查過才是 none', async () => {
    const r = await lookupPendingMiss(mkApi({ mode: 'none' }, { items: [] }, ['/api/onboard/failures']), '10.99.9.9')
    expect(r.state).toBe('error'); expect(r.err).toContain('納管失敗清單')
    expect((await lookupPendingMiss(mkApi({ mode: 'machine', alive: { onboarded: false } }, { items: [] }), '10.99.9.9')).state).toBe('none')
  })
  it('納管失敗清單同一頁只抓一次（快取）', async () => {
    const api = mkApi({ mode: 'none' }, { items: [] }); const cache = {}
    await lookupPendingMiss(api, '10.99.9.1', cache); await lookupPendingMiss(api, '10.99.9.2', cache)
    expect(api.mock.calls.filter(c => c[0] === '/api/onboard/failures').length).toBe(1)
  })
})

describe('OnboardModal：密碼生命週期與頂部目標摘要', () => {
  const mkModal = () => mount(OnboardModal, { props: { ip: IP, hostname: 'host1', serial: 'S-1', basis: '系統實測 OS：RHEL' }, global: { stubs: { InfoNote: { template: '<i />' } } } })
  const pw = (w: any) => (w.find('input[type=password]').element as HTMLInputElement)
  it('頂部摘要寫出主機名、IP、資產序號、判定依據與將建立／不建立什麼', async () => {
    const w = mkModal(); await flushPromises()
    const t = w.find('[data-test=modal-target]').text()
    for (const x of ['host1', IP, 'S-1', '系統實測 OS：RHEL', '將建立', '不建立']) expect(t).toContain(x)
  })
  it('輸入密碼後：切到本機執行／整批、改選已存憑證、按取消，密碼都會被清空（不會留到切回來）', async () => {
    const w = mkModal(); await flushPromises()
    await w.find('select').setValue('linux')
    await w.find('input[type=password]').setValue('secret-1')
    expect(pw(w).value).toBe('secret-1')
    const tabs = w.findAll('.mtab')
    await tabs[1]!.trigger('click'); await tabs[0]!.trigger('click'); await flushPromises()
    expect(pw(w).value).toBe('')
    await w.find('input[type=password]').setValue('secret-2')
    await w.find('.btn.ghost').trigger('click')
    expect(w.emitted('close')).toBeTruthy(); expect(pw(w).value).toBe('')
  })
  it('沒帶主機資訊（6.1 開的）就不顯示摘要區', async () => {
    const w = mount(OnboardModal, { props: { ip: IP }, global: { stubs: { InfoNote: { template: '<i />' } } } }); await flushPromises()
    expect(w.find('[data-test=modal-target]').exists()).toBe(false)
  })
})
