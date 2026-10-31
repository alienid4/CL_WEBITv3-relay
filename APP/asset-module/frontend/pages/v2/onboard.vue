<script setup lang="ts">
// 6.1 納管作業（2.x 第三批）：待納管｜納管失敗｜已納管｜新設備上線｜納管進度｜掃描缺口。
// 只是畫面：納管／收集一律呼叫既有 API 與既有元件（OnboardModal、BatchOnboardModal、WinOnboardModal、RevokeModal、
// WinRevokeModal、一鍵批次自動納管頁），不改任何納管後端邏輯。開始收集（掃網段）、改分類、Push Agent 安裝包、
// 標籤等進階操作仍在舊版「納入管理」頁（連結在頁首）。
// 數字口徑：「已納管」＝2.1 主機查詢同一份（v2_hosts.machines）；新設備上線、納管進度＝/api/v2/onboard/*（v2_onboard.py）。
definePageMeta({ layout: 'v2' })
const BatchAuto = defineAsyncComponent(() => import('~/pages/batch-onboard.vue'))
// 開始收集（2.007.61，舊版 /adopt 的「開始收集」頁）：直接嵌入既有畫面，只顯示收集那一頁；功能、API 不變
const AdoptCollect = defineAsyncComponent(() => import('~/pages/adopt.vue'))
const Funnel = defineAsyncComponent(() => import('~/pages/pipeline.vue'))
const ScanResults = defineAsyncComponent(() => import('~/pages/scan-results.vue'))
// 收集紀錄（2.011.x，舊版散在納入管理／SAN 頁底的 CollectLog）：全部動作的收集／測連線／納管後收集紀錄，點一列看細節或下載給開發者；只讀
const CollectLogPanel = defineAsyncComponent(() => import('~/components/CollectLog.vue'))
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const { exportTable } = useTableExport()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }

// ===== 資料（各頁籤第一次打開才載入；頁籤上的數字在資料載入後顯示）=====
const disp = ref<any>(null); const fails = ref<any>(null); const onb = ref<any>(null); const nd = ref<any>(null); const prog = ref<any>(null); const gaps = ref<any>(null)
const err = reactive<Record<string, string>>({})
async function load(k: string, force = false) {
  try {
    if (k === 'pending' && (force || !disp.value)) disp.value = await apiFetch('/api/v2/onboard/pending')   // 只回畫面用到的欄位（找碴 C8：原本 4.4 MB）
    if (k === 'failed' && (force || !fails.value)) fails.value = await apiFetch('/api/onboard/failures')
    if (k === 'onboarded' && (force || !onb.value)) onb.value = await apiFetch('/api/v2/hosts', { query: { tab: 'onboarded', size: 0 } })   // size=0：整個頁籤（2.1 改伺服器端分頁後要明講）
    if (k === 'newdev' && (force || !nd.value)) nd.value = await apiFetch('/api/v2/onboard/new-devices')
    if (k === 'progress' && (force || !prog.value)) prog.value = await apiFetch('/api/v2/onboard/progress')
    if (k === 'gaps' && (force || !gaps.value)) gaps.value = await apiFetch('/api/reports/scan-gaps')
    err[k] = ''
  } catch (e: any) { err[k] = `載入失敗：${errMsg(e)}` }
}

// ===== 待納管 =====
const NEXT: Record<string, string> = { needs_agent: '安裝 Push Agent', needs_credential: '執行納管', onboard_failed: '納管失敗，檢視原因', failed: '收集失敗，檢視原因',
  needs_winrm_port: '開通 WinRM（5985）', needs_ssh_port: '開通 SSH（22）', win_pending: '勾選後執行 Windows 納管' }
const pendRows = computed(() => {
  const d = disp.value
  if (!d) return []
  const act = new Set<string>(d.actionable_tabs ?? [])
  return d.results.filter((r: any) => act.has(r.tab) && r.status !== 'collected').map((r: any, i: number) => ({
    ...r, __no: i + 1, route_label: d.tab_label?.[r.tab] ?? r.tab, next: (r.status === 'needs_credential' && r.tab === 'winrm') ? '設定 WinRM 憑證' : (NEXT[r.status] ?? r.status),
    name: r.hostname || r.asset_name || '—', at: (r.collected_at || '').slice(0, 16),
    alive_text: aliveText(aliveNow.value[r.ip] ?? r.alive) }))
})
// 說明先顯示冒號前那一句（例：「納管失敗（其他）」），全文點「⋯」展開
const briefMsg = (m: string | null | undefined) => { const t = String(m ?? ''); const i = t.search(/[：:]/); return i > 0 && i < 40 ? t.slice(0, i) : t.length > 40 ? `${t.slice(0, 40)}…` : t }
// 存活（2.007.13）：預設顯示系統已有的最新證據（掃描／收集／偵測存活），「重新偵測」才即時再測勾選的位址
const aliveNow = ref<Record<string, any>>({})
const aliveText = (a: any) => (a ? `${a.state}（${String(a.at || '').slice(5, 16)}・${a.by}）` : '無資料')
const aliveBusy = ref(false)
const { guardedFetch } = useGuardedFetch()
const { isAdmin, isViewer } = useAuth()     // Q91：納管、批次納管、自動納管、Push Agent 安裝包限管理員（後端另外擋）
const { showToast } = useToast()
// Push Agent 安裝包（2.007.61）：沿用既有 /api/collect/agent-package（建立資產與產生腳本的邏輯都在後端）
const pkgOpen = ref(false)
const pkgIp = ref('')
const pkg = ref<any>(null)
const pkgBusy = ref(false)
const pkgCopied = ref(false)
async function getPkg() {
  if (pkgBusy.value) return
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(pkgIp.value.trim())) { showToast('請輸入正確的 IPv4 位址', 'warn'); return }
  pkgBusy.value = true
  try { pkg.value = await guardedFetch<any>('/api/collect/agent-package', { method: 'POST', body: { ip: pkgIp.value.trim() } }) }
  catch (e: any) { showToast(`取得安裝包失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}`, 'error') }
  finally { pkgBusy.value = false }
}
async function copyPkg() {
  try { await navigator.clipboard.writeText(pkg.value?.files?.['bootstrap_watcher.sh'] ?? ''); pkgCopied.value = true; setTimeout(() => { pkgCopied.value = false }, 2000) }
  catch { showToast('複製失敗，請直接選取文字複製', 'warn') }
}
async function recheck() {
  const ips = pickedRows.value.map((r: any) => r.ip)
  if (!ips.length) return
  aliveBusy.value = true
  try {
    const r = await guardedFetch<any>('/api/tools/alive-check/batch', { method: 'POST', body: { ips, scope: '6.1 待納管・重新偵測' } })
    const at = new Date().toLocaleString('sv-SE').slice(0, 16)
    const m = { ...aliveNow.value }
    for (const x of r.items) m[x.ip] = { state: x.result === 'alive' ? '通' : x.result === 'no_response' ? '不通' : '無法判斷', at, by: '偵測存活' }
    aliveNow.value = m
    showToast(`偵測 ${r.summary.total} 台：通 ${r.summary.alive}、不通 ${r.summary.no_response}、無法判斷 ${r.summary.unknown}`, 'success')
  } catch (e: any) { showToast(`偵測失敗：${errMsg(e)}`, 'error') } finally { aliveBusy.value = false }
}
const pendTab = computed(() => q('route'))
// 篩選（2.007.48）：IP／網段（10.99.3、10.99.3.0/24、10.99.3.10-50）＋名稱、開放埠、環境。三個一起生效、都在目前的收集方式頁簽內。
const fQ = ref('')
const fPort = ref('')
const fEnv = ref('')
const fRoom = ref('')
const ipF = computed(() => ipFilter(fQ.value))
const inTab = (r: any) => (!pendTab.value || r.tab === pendTab.value) && (!q('ip') || r.ip === q('ip'))
const portsOf = (r: any): string[] => String(r.open_ports ?? '').split(',').map(x => x.trim()).filter(Boolean)
const pendShown = computed(() => pendRows.value.filter((r: any) => inTab(r)
  && rowMatches(ipF.value, r.ip, `${r.name} ${r.hostname ?? ''} ${r.asset_name ?? ''}`)
  && (!fPort.value || (fPort.value === '—' ? !portsOf(r).length : portsOf(r).includes(fPort.value)))
  && (!fEnv.value || r.environment === fEnv.value)
  && (!fRoom.value || r.room === fRoom.value)))
const pendCount = (t: string) => pendRows.value.filter((r: any) => r.tab === t).length
// 下拉選項只列「目前頁簽」裡真的有的埠／環境，附台數（選了不會是空的）
const portOpts = computed(() => {
  const c = new Map<string, number>()
  for (const r of pendRows.value.filter(inTab)) { const ps = portsOf(r); for (const p of (ps.length ? ps : ['—'])) c.set(p, (c.get(p) ?? 0) + 1) }
  return [...c.entries()].sort((a, b) => (a[0] === '—' ? 1 : b[0] === '—' ? -1 : Number(a[0]) - Number(b[0])))
})
const ENV_ORDER = ['正式', '備援', '測試']
const envOpts = computed(() => {
  const c = new Map<string, number>()
  for (const r of pendRows.value.filter(inTab)) c.set(r.environment || '未分類', (c.get(r.environment || '未分類') ?? 0) + 1)
  const rank = (k: string) => { const i = ENV_ORDER.indexOf(k); return i >= 0 ? i : k === '未分類' ? 99 : 50 }
  return [...c.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
})
const roomOpts = computed(() => {
  const c = new Map<string, number>()
  for (const r of pendRows.value.filter(inTab)) c.set(r.room || '未知', (c.get(r.room || '未知') ?? 0) + 1)
  return [...c.entries()].sort((a, b) => (a[0] === '未知' ? 1 : b[0] === '未知' ? -1 : b[1] - a[1] || a[0].localeCompare(b[0])))
})
const envTone = (r: any) => ({ 正式: 'prod', 備援: 'dr', 測試: 'test' } as Record<string, string>)[r.environment] ?? 'none'
const fActive = computed(() => !!(fQ.value.trim() || fPort.value || fEnv.value || fRoom.value))
function fClear() { fQ.value = ''; fPort.value = ''; fEnv.value = ''; fRoom.value = '' }
const picked = ref<Record<string, boolean>>({})
const pickedRows = computed(() => pendShown.value.filter((r: any) => picked.value[r.ip]))
const sshPicked = computed(() => pickedRows.value.filter((r: any) => r.tab === 'ssh').map((r: any) => r.ip))
const winPicked = computed(() => pickedRows.value.filter((r: any) => r.tab === 'winrm').map((r: any) => ({ ip: r.ip, hostname: r.hostname, opted_in: Boolean(r.win?.opted_in) })))
const canPick = (r: any) => r.tab === 'ssh' || r.tab === 'winrm'
const onboardIp = ref<string | null>(null)
const batchIps = ref<string[] | null>(null)
const winRows = ref<any[] | null>(null)
const showAuto = ref(false)
function after(k: string) { picked.value = {}; load(k, true) }

// ===== 納管失敗 =====
const failRows = computed(() => (fails.value?.items ?? []).filter((r: any) => !r.resolved).map((r: any, i: number) => ({ ...r, __no: i + 1, name: r.hostname || '—', last: (r.last_tried_at || '').slice(0, 16),
  room: r.physical_location || '—', unit: r.department || '—', who: r.contact || r.custodian || '—' })))
const failReason = computed(() => q('reason'))
const failShown = computed(() => failRows.value.filter((r: any) => !failReason.value || r.reason === failReason.value))
const failPicked = ref<Record<string, boolean>>({})
const failIps = computed(() => failShown.value.filter((r: any) => failPicked.value[r.ip]))

// ===== 已納管 =====
const onbRows = computed(() => (onb.value?.rows ?? []).map((r: any) => ({ ...r, win: /windows/i.test(r.type || '') })))
const onbPicked = ref<Record<string, boolean>>({})
const onbOne = computed(() => { const ks = Object.keys(onbPicked.value).filter(k => onbPicked.value[k]); return ks.length === 1 ? onbRows.value.find((r: any) => r.key === ks[0]) : null })
const revoke = ref<any>(null)

// ===== 新設備上線 =====
const ndScope = computed(() => q('scope') || 'new')
const ndStep = computed(() => q('step'))
const ndRows = computed(() => (nd.value?.rows ?? []).filter((r: any) => (ndScope.value === 'all' || r.is_new) && (!ndStep.value || r.next === ndStep.value || (ndStep.value === 'done' && !r.next))))
const MARK = (b: boolean) => (b ? '✓' : '✗')
function stepLink(r: any, k: string): string | null {
  if (r.steps[k]) return null
  return ({ registered: `/v2/datafix?tab=cia`, racked: `/v2/machine/${encodeURIComponent(r.serial)}`, scanned: '/v2/onboard?tab=gaps',
    onboarded: `/v2/onboard?tab=pending${r.ip ? '&ip=' + encodeURIComponent(r.ip) : ''}`, golive: `/v2/golive/${encodeURIComponent(r.serial)}` } as Record<string, string>)[k]
}
const ndTable = computed(() => ndRows.value.map((r: any) => ({ ...r, name: r.hostname || r.serial, s1: MARK(r.steps.registered), s2: MARK(r.steps.racked), s3: MARK(r.steps.scanned), s4: MARK(r.steps.onboarded), s5: MARK(r.steps.golive), first: (r.first_seen || '').slice(0, 10) })))
const ndCount = (k: string) => (nd.value?.rows ?? []).filter((r: any) => (ndScope.value === 'all' || r.is_new) && (k === 'done' ? !r.next : r.next === k)).length

// ===== 納管進度 =====
const progRows = computed(() => (prog.value?.rows ?? []).map((r: any) => ({ family: r.family, scope: r.server ? '納入目標' : '另有收集方式', n_all: r.n_all, n_online: r.n_online,
  n_onboarded: r.n_onboarded, n_online_onboarded: r.n_online_onboarded, gap: r.n_online - r.n_online_onboarded, pct: r.pct })))
const drill = ref<any>(null)
watch(() => [q('fam'), q('kind')], async ([f, k]) => {
  if (!f || !k) { drill.value = null; return }
  try { drill.value = await apiFetch('/api/v2/onboard/progress/drill', { query: { family: f, kind: k } }) } catch (e: any) { err.progress = `清單載入失敗：${errMsg(e)}` }
}, { immediate: true })
const dUrl = (fam: string, kind: string) => `/v2/onboard?tab=progress&fam=${encodeURIComponent(fam)}&kind=${kind}`
const obsRows = computed(() => (prog.value?.observed.by_family ?? []).map((r: any) => ({ ...r, missing_n: r.total - r.observed_count, pct: prog.value?.observed.observed_count ? r.pct : '無資料' })))

// ===== 掃描缺口 =====
const gapRows = computed(() => (gaps.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1 })).filter((r: any) => (q('gk') === 'all' ? true : q('gk') === 'fw' ? r.firewall_first : r.is_gap))
  .map((r: any) => ({ ...r, loc: r.location || '—', env: r.environment || '—', last_ok: (r.last_ok_scan || '').slice(0, 16) || '—' })))
function exportGaps() {
  exportTable('掃描缺口', ['項次', '網段', '機房', '環境', '用途', 'VLAN', '位址數', '狀況', '建議', '登記資產（台）', '最近掃通'],
    gapRows.value.map((r: any) => [`G${r.__no}`, r.cidr, r.loc, r.env, r.purpose ?? '', r.vlan ?? '', r.addresses, r.kind, r.advice, r.registered_machines, r.last_ok]))
}

const TABS = computed(() => [
  { k: 'collect', label: '開始收集' },
  { k: 'pending', label: `待納管${disp.value ? ' ' + pendRows.value.length : ''}` },
  { k: 'failed', label: `納管失敗${fails.value ? ' ' + failRows.value.length : ''}` },
  { k: 'onboarded', label: `已納管${onb.value ? ' ' + onb.value.rows.length : ''}` },
  { k: 'newdev', label: '新設備上線' }, { k: 'progress', label: '納管進度' }, { k: 'collectlog', label: '收集紀錄' },
  { k: 'gaps', label: `掃描缺口${gaps.value ? ' ' + gaps.value.summary.gap_segments : ''}` },
  { k: 'funnel', label: '納管漏斗' }, { k: 'scan', label: '掃描結果' }])
const tab = computed({ get: () => (TABS.value.some(t => t.k === q('tab')) ? q('tab') : 'pending'), set: (v: string) => router.replace({ query: { tab: v } }) })
// 待納管（約 1 萬個位址的收集結果）與新設備上線（約 5 千台）資料量大，只在瀏覽器載入，不塞進伺服器端產生的頁面
const CLIENT_ONLY = ['pending', 'newdev']
if (!CLIENT_ONLY.includes(tab.value)) await load(tab.value)
onMounted(() => { if (CLIENT_ONLY.includes(tab.value)) load(tab.value) })
watch(tab, (t) => load(t))
// ===== 標註網段（2.007.56，舊版「掃描缺口報表 → 人工標註」翻新）：勾選一或多段，選一種標註＋預計台數＋備註，存到既有的 /api/reports/scan-gaps/note =====
const GAP_KINDS = [
  { key: 'unused', label: '目前沒在使用', hint: '網路組先開好、裡面還沒有機器。標了就收起來' },
  { key: 'policy', label: '政策不掃', hint: '例：DMZ，內網不得主動連線。標了就收起來，稽核要問時查得到' },
  { key: 'pending', label: '等開通', hint: '該通、申請中——留在畫面上追蹤' },
  { key: 'watch', label: '盯著', hint: '先不處理但別收起來' },
]
const gapPicked = ref<Record<string, boolean>>({})
const gapPickedRows = computed(() => gapRows.value.filter((r: any) => gapPicked.value[r.cidr]))
const gStatus = ref('')
const gExpect = ref('')
const gNote = ref('')
const gBusy = ref(false)
async function saveGapNote() {
  const rows = gapPickedRows.value
  if (!rows.length) { showToast('請先勾選要標註的網段', 'warn'); return }
  if (!gStatus.value && !window.confirm(`沒有選任何標註種類——會「清除」勾選的 ${rows.length} 段的標註，確定？`)) return
  gBusy.value = true
  let ok = 0
  const bad: string[] = []
  for (const r of rows) {
    try {
      await guardedFetch('/api/reports/scan-gaps/note', { method: 'PUT', body: { cidr: r.cidr, status: gStatus.value, note: gNote.value, expected_hosts: gExpect.value === '' ? null : Number(gExpect.value) } })
      ok++
    } catch (e: any) { bad.push(`${r.cidr}：${errMsg(e)}`) }
  }
  showToast(bad.length ? `完成 ${ok} 段、失敗 ${bad.length} 段（${bad[0]}）` : `已標註 ${ok} 段`, bad.length ? 'warn' : 'success')
  gapPicked.value = {}
  gaps.value = await apiFetch('/api/reports/scan-gaps')
  gBusy.value = false
}
</script>

<template>
<div>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">6.1 納管作業</h1></template>
    <template #conclusion>
      <div class="bar">
        <InfoNote>本頁的納管、批次納管、Windows 納管、撤銷納管沿用既有功能（同樣的檢查、備份、紀錄）。開始收集（掃網段、自動選路）在「開始收集」頁籤。</InfoNote>
      </div>
    </template>
    <template #tab>
      <p v-if="err[tab]" class="err">{{ err[tab] }}</p>

      <!-- 待納管 -->
      <ClientOnly v-if="tab === 'collect'"><V2Embed><AdoptCollect only="collect" /></V2Embed></ClientOnly>
      <ClientOnly v-else-if="tab === 'collectlog'"><V2Embed><CollectLogPanel title="收集失敗與收集紀錄" :start-open="true" /></V2Embed></ClientOnly>

      <template v-else-if="tab === 'pending' && disp">
        <div class="bar">
          <NuxtLink class="chip" :class="{ on: !pendTab }" to="/v2/onboard?tab=pending">全部 {{ pendRows.length }}</NuxtLink>
          <NuxtLink v-for="t in disp.actionable_tabs" :key="t" class="chip" :class="{ on: pendTab === t }" :to="`/v2/onboard?tab=pending&route=${t}`">{{ disp.tab_label[t] }} {{ pendCount(t) }}</NuxtLink>
          <span v-if="q('ip')" class="muted">只看 {{ q('ip') }} <NuxtLink to="/v2/onboard?tab=pending">清除</NuxtLink></span>
          <span class="muted">資料：{{ disp.merged?.run_count ?? 1 }} 次收集合併，最新 {{ (disp.merged?.latest || disp.run?.finished_at || '').slice(0, 16) }}</span>
          <InfoNote>存活＝系統已有的最新證據：網路掃描看到（或該網段之後掃描沒看到＝不通）、收集時有開放埠、或有人按過偵測存活；括號是時間與來源。要即時再測，勾選後按「重新偵測存活」（只測勾選的位址，結果記成偵測報告）。待納管＝最近一次收集（開始收集）結果中，走 SSH、WinRM、Push Agent 且還沒收到資料的位址（一個位址一列）。網路設備、儲存、ESXi 等另有收集方式，在 6.2 設備收集或舊版頁。</InfoNote>
        </div>
        <div class="bar">
          <button v-if="isAdmin" type="button" class="btn" :disabled="sshPicked.length !== 1" @click="onboardIp = sshPicked[0]">納管（單台 SSH）</button>
          <button v-if="isAdmin" type="button" class="btn" :disabled="!sshPicked.length" @click="batchIps = sshPicked">批次納管（SSH {{ sshPicked.length }}）</button>
          <button v-if="isAdmin" type="button" class="btn" :disabled="!winPicked.length" @click="winRows = winPicked">Windows 納管（{{ winPicked.length }}）</button>
          <button v-if="!isViewer" type="button" class="btn2" :disabled="!pickedRows.length || aliveBusy" @click="recheck">{{ aliveBusy ? '偵測中…' : `重新偵測存活（${pickedRows.length}）` }}</button>
          <button v-if="isAdmin" type="button" class="btn2" @click="showAuto = !showAuto">{{ showAuto ? '收起' : '一鍵批次自動納管（測試環境）' }}</button>
          <button v-if="isAdmin" type="button" class="btn2" @click="pkgOpen = true">Push Agent 安裝包</button>
        </div>
        <ClientOnly><div v-if="showAuto" class="embed"><BatchAuto embedded /></div></ClientOnly>
        <div class="bar">
          <b>環境</b>
          <button type="button" class="chip" :class="{ on: !fEnv }" @click="fEnv = ''">全部</button>
          <button v-for="[e, n] in envOpts" :key="e" type="button" class="chip env" :class="['env-' + envTone({ environment: e }), { on: fEnv === e }]" @click="fEnv = fEnv === e ? '' : e">{{ e }} {{ n }}</button>
        </div>
        <div class="bar fbar">
          <input v-model="fQ" class="fin" type="search" placeholder="IP／網段／名稱（例：10.99.3、10.99.3.0/24、10.99.3.10-50）" aria-label="IP／網段／名稱">
          <select v-model="fPort" class="fsel" aria-label="開放埠">
            <option value="">開放埠：全部</option>
            <option v-for="[p, n] in portOpts" :key="p" :value="p">{{ p === '—' ? '沒有開放埠' : `有開 ${p}` }}（{{ n }}）</option>
          </select>
          <select v-model="fRoom" class="fsel" aria-label="機房">
            <option value="">機房：全部</option>
            <option v-for="[m, n] in roomOpts" :key="m" :value="m">{{ m }}（{{ n }}）</option>
          </select>
          <button v-if="fActive" type="button" class="btn2" @click="fClear">清除篩選</button>
          <span class="muted">符合 {{ pendShown.length }} 筆<template v-if="fActive">／此頁簽共 {{ pendRows.filter(inTab).length }} 筆</template></span>
          <InfoNote>IP／網段欄：打 10.99.3 就是 10.99.3.0～255 整段（不會抓到 10.99.30）；也可以打 10.99.3.0/24、10.99.3.10-50、完整 IP，多段用空白或逗號分開。打的不是 IP 就比對主機名稱／資產名稱。環境、機房：已登記的機器用資產登記的，沒登記的看它落在網段配置表哪一段（取最小那一段）；兩邊都沒有環境就是「未分類」、沒有機房就是「未知」。匯出的是篩完的結果。</InfoNote>
        </div>
        <V2Table v-model:selected="picked" :rows="pendShown" prefix="P" row-key="ip" selectable :can-select="canPick" :max="3000" empty="沒有待納管的位址"
                 :cols="[{ k: 'ip', label: 'IP', mono: true, link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'name', label: '主機名稱／資產名稱' }, { k: 'environment', label: '環境', tone: envTone }, { k: 'room', label: '機房', mono: true }, { k: 'route_label', label: '收集方式' }, { k: 'open_ports', label: '開放埠', mono: true }, { k: 'next', label: '下一步' }, { k: 'alive_text', label: '存活' }, { k: 'at', label: '收集時間', mono: true }, { k: 'message', label: '說明', brief: (r: any) => briefMsg(r.message) }]" />
      </template>

      <!-- 納管失敗 -->
      <template v-else-if="tab === 'failed' && fails">
        <div class="bar">
          <NuxtLink class="chip" :class="{ on: !failReason }" to="/v2/onboard?tab=failed">全部 {{ failRows.length }}</NuxtLink>
          <NuxtLink v-for="(n, k) in fails.summary.by_reason" :key="k" class="chip" :class="{ on: failReason === k }" :to="`/v2/onboard?tab=failed&reason=${encodeURIComponent(String(k))}`">{{ k }} {{ failRows.filter((r: any) => r.reason === k).length }}</NuxtLink>
          <InfoNote>最近一次納管嘗試失敗、之後尚未成功的主機（任何入口的納管都算）。重新納管會再走一次相同檢查。</InfoNote>
        </div>
        <div class="bar">
          <button type="button" class="btn" :disabled="!failIps.filter((r: any) => r.platform !== 'windows').length" @click="batchIps = failIps.filter((r: any) => r.platform !== 'windows').map((r: any) => r.ip)">重新納管（{{ failIps.filter((r: any) => r.platform !== 'windows').length }}）</button>
          <button type="button" class="btn" :disabled="!failIps.filter((r: any) => r.platform === 'windows').length" @click="winRows = failIps.filter((r: any) => r.platform === 'windows').map((r: any) => ({ ip: r.ip, hostname: r.hostname, opted_in: true }))">Windows 重新納管（{{ failIps.filter((r: any) => r.platform === 'windows').length }}）</button>
        </div>
        <V2Table v-model:selected="failPicked" :rows="failShown" prefix="F" row-key="ip" selectable :max="3000" empty="沒有納管失敗"
                 :cols="[{ k: 'ip', label: 'IP', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial || r.ip)}` }, { k: 'name', label: '主機名稱' }, { k: 'os_class', label: '作業系統大類' }, { k: 'room', label: '機房', mono: true }, { k: 'unit', label: '使用單位' }, { k: 'who', label: '窗口' }, { k: 'reason', label: '失敗原因' }, { k: 'next_step', label: '下一步' }, { k: 'fail_count', label: '失敗次數', mono: true }, { k: 'last', label: '最近嘗試', mono: true }]" />
      </template>

      <!-- 已納管 -->
      <template v-else-if="tab === 'onboarded' && onb">
        <div class="bar">
          <span class="muted">{{ onbRows.length }} 台</span>
          <button v-if="isAdmin" type="button" class="btn2" :disabled="!onbOne" @click="revoke = onbOne">撤銷納管（單台）</button>
          <InfoNote>已納管＝收集帳號 7 天內連線成功（與 2.1 主機查詢「已納管」頁籤同一份資料、同一個數字）。撤銷納管會移除收集帳號與金鑰，沿用既有撤銷流程。</InfoNote>
        </div>
        <V2Table v-model:selected="onbPicked" :rows="onbRows" prefix="M" selectable :max="3000" empty="沒有已納管的主機"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'type', label: '類型' }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }, { k: 'last_seen', label: '最後偵測', mono: true }]" />
      </template>

      <!-- 新設備上線 -->
      <template v-else-if="tab === 'newdev' && nd">
        <div class="bar">
          <NuxtLink class="chip" :class="{ on: ndScope === 'new' }" :to="`/v2/onboard?tab=newdev&scope=new`">近 {{ nd.new_days }} 天新出現 {{ nd.new_total }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: ndScope === 'all' }" :to="`/v2/onboard?tab=newdev&scope=all`">全部進行中 {{ nd.total }}</NuxtLink>
          <span class="sep" />
          <NuxtLink class="chip" :class="{ on: !ndStep }" :to="`/v2/onboard?tab=newdev&scope=${ndScope}`">全部步驟</NuxtLink>
          <NuxtLink v-for="s in nd.steps" :key="s.key" class="chip" :class="{ on: ndStep === s.key }" :to="`/v2/onboard?tab=newdev&scope=${ndScope}&step=${s.key}`">停在{{ s.label }} {{ ndCount(s.key) }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: ndStep === 'done' }" :to="`/v2/onboard?tab=newdev&scope=${ndScope}&step=done`">五步完成 {{ ndCount('done') }}</NuxtLink>
          <InfoNote>每台五步：CIA 登記 → 上架（有機櫃位置，虛擬機免）→ 網路偵測（掃描或收集帳號連線過）→ 納管（收集帳號 7 天內連線成功）→ 上線檢查（上線前檢查表已通過）。✗ 可點，直接到該步驟的處理頁面。新出現＝登記或第一次被掃到在 {{ nd.new_days }} 天內。退役、報廢、豁免設備不列。</InfoNote>
        </div>
        <V2Table :rows="ndTable" prefix="N" :max="3000" empty="沒有符合的設備"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.serial)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 's1', label: '1 CIA 登記', link: (r: any) => stepLink(r, 'registered') }, { k: 's2', label: '2 上架', link: (r: any) => stepLink(r, 'racked') }, { k: 's3', label: '3 網路偵測', link: (r: any) => stepLink(r, 'scanned') }, { k: 's4', label: '4 納管', link: (r: any) => stepLink(r, 'onboarded') }, { k: 's5', label: '5 上線檢查', link: (r: any) => stepLink(r, 'golive') }, { k: 'next_label', label: '下一步' }, { k: 'first', label: '首次出現', mono: true }, { k: 'stuck_days', label: '停留（天）', mono: true }, { k: 'room', label: '機房', mono: true }, { k: 'unit', label: '使用單位' }]" />
      </template>

      <!-- 納管進度 -->
      <template v-else-if="tab === 'progress' && prog">
        <template v-if="drill">
          <div class="bar"><NuxtLink to="/v2/onboard?tab=progress">‹ 返回</NuxtLink><b>{{ drill.label }}</b><span class="muted">{{ drill.rows.length }} 台</span></div>
          <V2Table :rows="drill.rows" prefix="D" :max="5000" :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'type', label: '類型' }, { k: 'status_label', label: '偵測狀態' }, { k: 'onboarded', label: '已納管' }, { k: 'last_seen', label: '最後偵測', mono: true }, { k: 'system', label: '業務系統' }]" />
        </template>
        <template v-else>
          <dl class="kv">
            <dt>納管覆蓋率</dt><dd><b>{{ prog.server.pct }}%</b>（運作中已納管 {{ prog.server.onboarded }} 台／運作中 {{ prog.server.online }} 台，{{ prog.server_families.join('、') }}；全部已納管 {{ prog.onboarded_total }} 台見「已納管」頁籤）</dd>
            <dt>目標</dt><dd>{{ prog.target_pct }}%　尚差 {{ prog.server.gap_to_target }} 台</dd>
            <dt>連線涵蓋率</dt><dd><template v-if="prog.observed.observed_count">{{ prog.observed.pct }}%（近 {{ prog.observed.days }} 天有連線觀察 {{ prog.observed.observed_count }} 台／現役 {{ prog.observed.total }} 台）</template>
              <template v-else>無資料（尚未有連線觀察資料）<InfoNote>近 {{ prog.observed.days }} 天沒有任何一台回報連線觀察（需已納管並收集服務資料）。這是功能還沒有資料，不是涵蓋率 0%。</InfoNote></template></dd>
          </dl>
          <div class="meter"><i :style="{ width: Math.min(100, prog.server.pct) + '%' }" /><span class="tgt" :style="{ left: prog.target_pct + '%' }" /></div>
          <InfoNote label="算法">納管覆蓋率＝運作中（偵測狀態，存在可信度達門檻）的 {{ prog.server_families.join('、') }} 主機中已納管的比例。網路設備、儲存、虛擬化平台、管理介面另有收集方式，不列入目標。目標百分比為系統設定 onboard_target_pct（目前 {{ prog.target_pct }}%）。連線涵蓋率＝連線推論的收集涵蓋率（現役機器近 N 天有連線觀察）。</InfoNote>
          <h3 class="h3">納管覆蓋率（依作業系統大類）</h3>
          <V2Table :rows="progRows" prefix="R" :cols="[{ k: 'family', label: '作業系統大類' }, { k: 'scope', label: '範圍' }, { k: 'n_all', label: '登記（台）', mono: true, link: (r: any) => dUrl(r.family, 'all') }, { k: 'n_online', label: '運作中（台）', mono: true, link: (r: any) => dUrl(r.family, 'online') }, { k: 'n_online_onboarded', label: '運作中已納管（台）', mono: true }, { k: 'gap', label: '運作中未納管（台）', mono: true, link: (r: any) => dUrl(r.family, 'online_not_onboarded') }, { k: 'n_onboarded', label: '已納管（台）', mono: true, link: (r: any) => dUrl(r.family, 'onboarded') }, { k: 'pct', label: '覆蓋率 %', mono: true }]" />
          <h3 class="h3">連線涵蓋率（依作業系統大類）</h3>
          <V2Table :rows="obsRows" prefix="O" :cols="[{ k: 'family', label: '作業系統大類' }, { k: 'total', label: '現役（台）', mono: true }, { k: 'observed_count', label: '有連線觀察（台）', mono: true }, { k: 'missing_n', label: '無連線觀察（台）', mono: true }, { k: 'pct', label: '涵蓋率 %', mono: true }]" />
          <p class="muted small">連線觀察累積 {{ prog.observed.cumulative_days }} 天{{ prog.observed.month_end_crossed ? '，已跨月底' : '，尚未跨月底（月結批次的連線還看不到）' }}。明細在 <NuxtLink to="/blast">連線推論（舊版）</NuxtLink>。</p>
        </template>
      </template>

      <!-- 掃描缺口 -->
      <!-- 納管漏斗（舊版 /pipeline）與掃描結果（舊版 /scan-results）：直接嵌入既有畫面，寫入動作（就地納管、下線、標非納管）沿用原元件 -->
      <ClientOnly v-else-if="tab === 'funnel'"><V2Embed><Funnel /></V2Embed></ClientOnly>
      <ClientOnly v-else-if="tab === 'scan'"><V2Embed><ScanResults /></V2Embed></ClientOnly>

      <template v-else-if="tab === 'gaps' && gaps">
        <div class="bar">
          <NuxtLink class="chip" :class="{ on: !q('gk') }" to="/v2/onboard?tab=gaps">掃不到的段 {{ gaps.summary.gap_segments }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: q('gk') === 'fw' }" to="/v2/onboard?tab=gaps&gk=fw">有登記資產、優先申請開通 {{ gaps.summary.firewall_first }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: q('gk') === 'all' }" to="/v2/onboard?tab=gaps&gk=all">全部規則內網段 {{ gaps.rows.length }}</NuxtLink>
          <span class="muted">最近掃描 {{ (gaps.latest_scan || '').slice(0, 16) }}・段內登記資產 {{ gaps.summary.gap_registered_machines }} 台</span>
          <button type="button" class="btn2" @click="exportGaps">匯出（防火牆申請附件）</button>
          <InfoNote>規則要掃、但最近掃描沒有結果的網段，依原因分類（從沒掃過／掃描失敗／跑完沒有回應／最近一次沒掃到）。有登記資產卻掃不到的段，是申請防火牆開通的第一順位。已標註不用處理的段預設不列。</InfoNote>
        </div>
        <form v-if="!isViewer" class="gapnote" @submit.prevent="saveGapNote">
          <span>勾選 <b>{{ gapPickedRows.length }}</b> 段，標註為</span>
          <button v-for="k in GAP_KINDS" :key="k.key" type="button" class="chip" :class="{ on: gStatus === k.key }" :title="k.hint" @click="gStatus = gStatus === k.key ? '' : k.key">{{ k.label }}</button>
          <input v-model="gExpect" class="gnum" type="number" min="0" placeholder="預計台數" aria-label="預計台數">
          <input v-model="gNote" class="gtxt" placeholder="備註：為什麼這樣標（誰說的、哪份規範、什麼時候開通…）" aria-label="備註">
          <button type="submit" class="btn" :disabled="gBusy || !gapPickedRows.length">{{ gBusy ? '存檔中…' : '儲存標註' }}</button>
          <InfoNote>「目前沒在使用」「政策不掃」標了就從缺口清單收起來（稽核要問時查得到）；「等開通」「盯著」留在畫面上追蹤。不選任何一種再儲存＝清除標註。預計台數是這段預期會有幾台，拿來判斷掃描結果合不合理。可以一次勾多段、套同一個標註。</InfoNote>
        </form>
        <V2Table v-model:selected="gapPicked" :rows="gapRows" row-key="cidr" selectable prefix="G" :max="2000" empty="沒有掃描缺口"
                 :cols="[{ k: 'cidr', label: '網段', mono: true }, { k: 'note_label', label: '標註' }, { k: 'expected_hosts', label: '預計台數', mono: true }, { k: 'note', label: '備註' }, { k: 'loc', label: '機房' }, { k: 'env', label: '環境' }, { k: 'purpose', label: '用途' }, { k: 'addresses', label: '位址數', mono: true }, { k: 'kind', label: '狀況' }, { k: 'advice', label: '建議' }, { k: 'registered_machines', label: '登記資產（台）', mono: true }, { k: 'last_ok', label: '最近掃通', mono: true }]" />
      </template>
      <p v-else-if="!err[tab]" class="muted">載入中…</p>
    </template>
  </V2Page>

  <div v-if="pkgOpen" class="pkgmask" @click.self="pkgOpen = false">
    <div class="pkgbox">
      <div class="bar"><b>Push Agent 安裝包<template v-if="pkg"> — {{ pkg.ip }}</template></b>
        <InfoNote>系統進不去的主機（22、445 都不通），改由主機自己每天回報。輸入那台的 IP，取得安裝指令與腳本，交給對方用 root 執行一行。只適用 Linux／Unix；這台若還沒登記，會自動建立一筆資產（業務欄位之後補）。</InfoNote></div>
      <form class="bar" @submit.prevent="getPkg">
        <input v-model="pkgIp" class="in" placeholder="主機 IP" aria-label="主機 IP">
        <button type="submit" class="btn" :disabled="pkgBusy || !pkgIp.trim()">{{ pkgBusy ? '取得中…' : '取得安裝包' }}</button>
        <button type="button" class="btn2" @click="pkgOpen = false">關閉</button>
      </form>
      <template v-if="pkg">
        <p v-if="pkg.created_asset" class="muted">已為這台建立資產 {{ pkg.asset_serial }}，業務欄位請稍後補上。</p>
        <ol class="muted">
          <li>請對方把下方腳本存成 /tmp/bootstrap_watcher.sh</li>
          <li>再以 root 執行：<span class="mono">{{ pkg.install_command }}</span></li>
          <li>裝完當天起每天自動回報到 {{ pkg.collector_url }}，之後不需要再用 root 登入這台。</li>
        </ol>
        <textarea class="cmdbox" readonly :value="pkg.files?.['bootstrap_watcher.sh']" rows="10" aria-label="安裝腳本" />
        <div class="bar"><button type="button" class="btn2" @click="copyPkg">{{ pkgCopied ? '已複製' : '複製腳本' }}</button></div>
      </template>
    </div>
  </div>
  <OnboardModal v-if="onboardIp" :ip="onboardIp" @done="after('pending')" @close="onboardIp = null" />
  <BatchOnboardModal v-if="batchIps" :ips="batchIps" @done="after(tab)" @close="batchIps = null" />
  <WinOnboardModal v-if="winRows" :rows="winRows" @done="after(tab)" @close="winRows = null" />
  <RevokeModal v-if="revoke && !revoke.win" :ip="revoke.ip" platform="linux" @done="revoke = null; after('onboarded')" @close="revoke = null" />
  <WinRevokeModal v-if="revoke && revoke.win" :ip="revoke.ip" :hostname="revoke.name" @done="revoke = null; after('onboarded')" @close="revoke = null" />
</div>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sep { width: 12px; }
.fin { font-family: inherit; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); min-width: 340px; flex: 1 1 340px; max-width: 520px; }
.chip.env { font-weight: 700; }
.chip.env-prod { border-color: var(--brand); color: var(--brand-dark); }
.chip.env-test { border-color: var(--warn); color: var(--warn-text); }
.chip.env-prod.on { background: var(--brand); color: var(--type-on); }
.chip.env-test.on { background: var(--warn-soft); color: var(--warn-text); }
.fsel { font-family: inherit; font-size: 13px; padding: 5px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; text-decoration: none; }
.gapnote { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; }
.gnum { width: 100px; font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; }
.gtxt { width: 300px; max-width: 100%; font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .45; cursor: default; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; text-decoration: none; cursor: pointer; }
.embed { border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; margin-bottom: 12px; background: var(--sub); }
.kv { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 8px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.meter { position: relative; height: 10px; border-radius: 6px; background: var(--sub); border: 1px solid var(--border); margin: 4px 0 12px; max-width: 520px; }
.meter i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--brand); border-radius: 6px; }
.meter .tgt { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--ink); }
.muted { color: var(--muted); font-size: 13px; } .small { font-size: 12px; }
.err { color: var(--bad); }
.pkgmask { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 60; }
.pkgbox { background: var(--card); border-radius: 14px; padding: 16px 18px; width: min(760px, 94vw); max-height: 90vh; overflow: auto; }
.cmdbox { width: 100%; font-family: Consolas, monospace; font-size: 12px; border: 1px solid var(--border-strong); border-radius: 8px; padding: 8px; background: var(--sub); color: var(--ink); }
</style>
