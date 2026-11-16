// 2.059.2（V20 收尾）：資產明細與操作改 V2 原生。釘住 ⚠D1／D2 的閘門：
//  VMware（ESXi／vCenter）一律沒有納管與收集按鈕、只能看的帳號沒有寫入按鈕、按鈕打的是舊頁同一支 API。全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { actionFlags, auditPlatformOf, chipMark, isVmwareAsset, revokePlatform } from '../utils/assetOps'

enableAutoUnmount(afterEach)
let calls: { url: string; opts: any }[] = []
let viewer = false
const Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'unit', 'empty'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl' }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' }, (p.cols ?? []).map((c: any) => (slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : h('span', String(r[c.k])))))))
} })
const stubs = { V2Table: Table, NuxtLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, InfoNote: { template: '<i />' }, AliveCheck: { template: '<i class="alive" />' },
  OnboardModal: { props: ['ip'], template: '<div class="onb-modal" />' }, RevokeModal: { props: ['ip', 'platform'], template: '<div class="rev-modal" :data-platform="platform" />' },
  OfflineMarkModal: { template: '<div class="off-modal" />' }, ExemptModal: { template: '<div class="ex-modal" />' } }
const LINUX = { hardware: { asset_serial: 'S1', hostname: 'web01', ip: '10.99.0.1', os: 'Red Hat Enterprise Linux 8', collect_ok: 1, onboard_block: null, onboard_exempt: null }, os_view: { effective: 'RHEL 8', basis: 'measured' }, classify: { os_type: 'Linux', os_type_auto: 'Linux', os_type_override: null } }
const ESXI = { hardware: { asset_serial: 'E1', hostname: 'esx01', ip: '10.99.0.9', os: 'VMware ESXi 7.0', collect_ok: 1, onboard_block: { kind: 'esxi', label: 'VMware', reason: '天條' }, onboard_exempt: null } }
const VCSA_BY_OS_ONLY = { hardware: { asset_serial: 'V1', hostname: 'vc01', ip: '10.99.0.8', os: '', collect_ok: 0, onboard_block: null, onboard_exempt: null }, os_view: { effective: 'VMware vCenter Server Appliance', basis: 'measured' } }

beforeEach(() => {
  calls = []; viewer = false
  for (const [k, v] of Object.entries({ ref, reactive, computed, onMounted, onBeforeUnmount, watch })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useAuth', () => ({ isViewer: ref(viewer), isAdmin: ref(!viewer) }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('confirm', () => true)
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts: any = {}) => {
    calls.push({ url, opts })
    if (url.startsWith('/api/onboard/post-collect/')) return opts.method === 'POST' ? { started: true } : { running: false, results: [] }
    if (url === '/api/adopt/move-choices') return { tabs: [{ tab: 'ssh', label: 'SSH 收集', movable: true, choices: [{ os_type: 'Linux', label: 'Linux' }] }] }
    if (url.endsWith('/tags')) return { tags: [], suggestions: [], known_tags: [] }
    if (url === '/api/config-audit/by-asset') return { state: '沒有檢核結果', run: null }
    if (url.startsWith('/api/host-spec/rebuild?')) return { host_rebuild: null }
    return {}
  } }))
})
afterEach(() => vi.unstubAllGlobals())

describe('判斷（純函式）', () => {
  it('VMware：後端判定或 OS 字樣（登記或實測）任一個說是就是', () => {
    expect(isVmwareAsset(ESXI.hardware)).toBe(true)
    expect(isVmwareAsset(VCSA_BY_OS_ONLY.hardware, VCSA_BY_OS_ONLY.os_view)).toBe(true)
    expect(isVmwareAsset({ os: 'Photon OS' })).toBe(true)
    expect(isVmwareAsset(LINUX.hardware, LINUX.os_view)).toBe(false)
    expect(auditPlatformOf(ESXI.hardware)).toBe('vmware')
    expect(auditPlatformOf({ os: 'Windows' }, { effective: 'Red Hat 9', basis: 'measured' })).toBe('linux')   // 實測優先
    expect(auditPlatformOf({ os: 'AIX 7.2' })).toBe('aix')
  })
  it('按鈕閘門：VMware 沒有納管與收集；唯讀帳號什麼都沒有；還沒納管才有一鍵納管', () => {
    const vm = actionFlags(ESXI.hardware, { vmware: true, viewer: false })
    expect([vm.onboard, vm.reonboard, vm.collect]).toEqual([false, false, false])
    const notYet = actionFlags({ ...LINUX.hardware, collect_ok: 0 }, { vmware: false, viewer: false })
    expect([notYet.onboard, notYet.reonboard, notYet.collect, notYet.revoke]).toEqual([true, false, false, false])
    expect(actionFlags({ ...LINUX.hardware, collect_ok: 0, onboard_block: { kind: 'storage' } }, { vmware: false, viewer: false }).onboard).toBe(false)
    expect(actionFlags({ ...LINUX.hardware, collect_ok: 0, onboard_exempt: { reason: 'x' } }, { vmware: false, viewer: false })).toMatchObject({ onboard: false, unexempt: true, exempt: false })
    expect(Object.values(actionFlags(LINUX.hardware, { vmware: false, viewer: true })).some(Boolean)).toBe(false)
    expect(chipMark({ ok: true, warn: true })).toBe('⚠'); expect(chipMark({ ok: null })).toBe('略過')
    expect(revokePlatform('IBM AIX 7.2')).toBe('aix'); expect(revokePlatform('Windows Server 2019')).toBe('windows'); expect(revokePlatform(null)).toBe('linux')
  })
})

describe('頁首動作', () => {
  async function mk(detail: any) {
    const C = (await import('../components/v2/AssetActions.vue')).default
    const w = mount(C, { props: { detail, serial: detail.hardware.asset_serial }, global: { stubs } }); await flushPromises(); return w
  }
  it('已納管 Linux：重新納管、收集全部、收集規格、取消納管都在；收集全部打同一支 post-collect', async () => {
    const w = await mk(LINUX)
    for (const t of ['act-reonboard', 'act-collect-all', 'act-collect-spec', 'act-revoke']) expect(w.find(`[data-test="${t}"]`).exists()).toBe(true)
    expect(w.find('[data-test="act-onboard"]').exists()).toBe(false)
    await w.find('[data-test="act-collect-all"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.opts?.method === 'POST')!.url).toBe('/api/onboard/post-collect/S1')
    await w.find('[data-test="act-collect-spec"]').trigger('click'); await flushPromises()
    await w.find('[data-test="act-revoke"]').trigger('click'); await flushPromises()
    expect(w.find('.rev-modal').attributes('data-platform')).toBe('linux')
  })
  it('ESXi（已納管過也一樣）：沒有任何納管／收集按鈕，寫明 VMware 不納管不收集', async () => {
    const w = await mk(ESXI)
    for (const t of ['act-onboard', 'act-reonboard', 'act-collect-all', 'act-collect-spec']) expect(w.find(`[data-test="${t}"]`).exists()).toBe(false)
    expect(w.text()).toContain('VMware 設備不納管、不收集')
    expect(calls.filter(c => c.opts?.method === 'POST')).toHaveLength(0)
  })
  it('只靠實測 OS 才認出的 vCenter、還沒納管：也不給一鍵納管', async () => {
    const w = await mk(VCSA_BY_OS_ONLY)
    expect(w.find('[data-test="act-onboard"]').exists()).toBe(false)
  })
  it('唯讀帳號：沒有任何寫入按鈕', async () => {
    viewer = true
    const w = await mk(LINUX)
    expect(w.findAll('button').length).toBe(0)
  })
  it('非納管設備：取消豁免打 /api/onboard-exempt/remove', async () => {
    const w = await mk({ hardware: { ...LINUX.hardware, collect_ok: 0, onboard_exempt: { reason: '廠商維護', created_by: 'a', created_at: 't' } } })
    await w.find('[data-test="act-unexempt"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/onboard-exempt/remove')!.opts.body).toEqual({ serials: ['S1'] })
  })
})

describe('頁籤', () => {
  it('搬移分類：選項跟 6.1 同一份、原因必填、送同一支 PUT', async () => {
    const C = (await import('../components/v2/AssetTagsOs.vue')).default
    const w = mount(C, { props: { detail: LINUX, serial: 'S1' }, global: { stubs } }); await flushPromises()
    await w.find('[data-test="move-open"]').trigger('click'); await flushPromises()
    await w.find('select').setValue('Linux')
    expect(w.find('[data-test="move-submit"]').attributes('disabled')).toBeDefined()
    await w.find('input[aria-label="搬移原因"]').setValue('其實是 Linux')
    await w.find('[data-test="move-submit"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/os-type-overrides')!.opts).toEqual({ method: 'PUT', body: { hostname: 'web01', ip: '10.99.0.1', asset_serial: 'S1', os_type: 'Linux', auto_os_type: 'Linux', reason: '其實是 Linux' } })
  })
  it('收集與檢核：Linux 有兩顆收集鈕，ESXi 沒有', async () => {
    const C = (await import('../components/v2/AssetDocsJobs.vue')).default
    const a = mount(C, { props: { detail: LINUX, serial: 'S1', mode: 'jobs' }, global: { stubs } }); await flushPromises()
    expect(a.find('[data-test="collect-audit"]').exists()).toBe(true)
    await a.find('[data-test="collect-audit"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c.url === '/api/config-audit/collect')!.opts.body).toEqual({ asset_serial: 'S1' })
    const b = mount(C, { props: { detail: ESXI, serial: 'E1', mode: 'jobs' }, global: { stubs } }); await flushPromises()
    expect(b.find('[data-test="collect-audit"]').exists()).toBe(false)
    expect(b.find('[data-test="collect-rebuild"]').exists()).toBe(false)
  })
})

describe('2.059.5 審查：收集鈕跟頁首同一個 VMware 判斷、OS 重建要先納管', () => {
  const VC_CIA = { hardware: { asset_serial: 'V2', hostname: 'vc02', ip: '10.99.0.7', os: '', collect_ok: 1, onboard_block: null, onboard_exempt: null }, os_view: { effective: 'VMware vCenter Server', basis: 'cia' } }
  it('登記 OS 空白、os_view 是 VMware（basis＝cia）：頁首與收集與檢核頁籤都沒有收集鈕', async () => {
    const A = (await import('../components/v2/AssetActions.vue')).default
    const a = mount(A, { props: { detail: VC_CIA, serial: 'V2' }, global: { stubs } }); await flushPromises()
    for (const t of ['act-onboard', 'act-reonboard', 'act-collect-all', 'act-collect-spec']) expect(a.find(`[data-test="${t}"]`).exists()).toBe(false)
    const J = (await import('../components/v2/AssetDocsJobs.vue')).default
    const j = mount(J, { props: { detail: VC_CIA, serial: 'V2', mode: 'jobs' }, global: { stubs } }); await flushPromises()
    expect(j.find('[data-test="collect-audit"]').exists()).toBe(false)
    expect(j.find('[data-test="collect-rebuild"]').exists()).toBe(false)
  })
  it('還沒納管的 Linux：OS 重建收集鈕停用，寫「先納管才能收集」', async () => {
    const J = (await import('../components/v2/AssetDocsJobs.vue')).default
    const j = mount(J, { props: { detail: { ...LINUX, hardware: { ...LINUX.hardware, collect_ok: 0 } }, serial: 'S1', mode: 'jobs' }, global: { stubs } }); await flushPromises()
    expect(j.find('[data-test="collect-rebuild"]').attributes('disabled')).toBeDefined()
    expect(j.find('[data-test="rebuild-need-onboard"]').text()).toBe('先納管才能收集')
    await j.find('[data-test="collect-rebuild"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c.url.startsWith('/api/host-spec/rebuild-collect'))).toBe(false)
  })
})
