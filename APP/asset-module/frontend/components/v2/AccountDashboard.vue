<script setup lang="ts">
// 7.2 帳號合規矩陣「儀表板」頁籤（V15，2.048.0 V2 原生；取代嵌入的舊 components/AccountDashboard.vue／pages/accounts.vue）。
// 同一批 API：/api/accounts、/api/accounts/inventoried-hosts（＋/export）、/api/accounts/ocp-nodes、/api/accounts/findings?os=、
// /api/accounts/privileged-trend。中招卡／特權組成的判定跟合規表共用 composables/accountCells（卡片數字＝點進去的筆數）。
// 卡片點下去：整個網址換成那張卡的條件（其他條件一律清掉），所以「數字＝點下去看到的筆數」。
// OS 分頁（Linux／Windows／AIX／OCP／OS 未知／全部）：整頁跟著篩；換分頁重抓稽核摘要，只套最新那一次（useLatest）。
import { dashboardChecks, defaultOsTab, OS_LABEL, osTabs, privBreakdown } from '~/composables/accountCells'
import type { CellAccount, OsBucket, OsTab } from '~/composables/accountCells'

const AM = '/v2/inventory/matrix'
const { apiFetch } = useApi()
const route = useRoute()
const { isAdmin } = useAuth()     // 後端匯出（access_policy _ADMIN_EXPORTS）只給管理員
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string

type Acc = CellAccount & { kind?: string | null }
interface Summary {
  has_data: boolean
  fail_high?: number; fail_medium?: number; unknown?: number
  accounts?: number; privileged?: number; humans?: number
  run?: { started_at: string; host_count: number }
}
interface HostRow {
  ip: string; asset_serial: string | null; hostname: string | null
  os: string | null; environment: string | null; physical_location: string | null
  department: string | null; contact: string | null; custodian: string | null
  accounts: number; human: number; sudoers: number; never_login: number
  findings: number; findings_high: number; collected_at: string | null; excluded: boolean
  onboarded: boolean; cia_registered?: boolean; os_bucket?: OsBucket
}
interface OcpNode { cluster: string | null; hostname: string | null; ip: string | null; asset_serial: string | null
  role: string | null; os: string | null; environment: string | null; has_accounts: boolean }
interface PrivSnap { taken_at: string; total_accounts: number; privileged: number; sudoers: number; uid0: number; nopasswd: number; mgmt: number; human: number }

const allHosts = ref<HostRow[]>([])
const osCounts = ref<Partial<Record<OsBucket, number>>>({})
const ocpNodes = ref<OcpNode[]>([])
const ocpErr = ref('')
const hostsError = ref('')
const allAccounts = ref<Acc[]>([])
const accErr = ref('')
const loading = ref(true)

// ===== OS 分頁 =====
const OS_TAB_KEY = 'acctDash.osTab'
const osTab = ref<OsTab>('linux')
const tabs = computed(() => osTabs(osCounts.value, ocpNodes.value.length))
function rememberedTab(): string | null {
  const q = route.query.os
  if (typeof q === 'string' && q in OS_LABEL) return q
  try { return localStorage.getItem(OS_TAB_KEY) } catch { return null }   // 私密視窗／封鎖儲存：退回預設
}
function pickOs(t: OsTab) {
  if (osTab.value === t) return
  osTab.value = t
  try { localStorage.setItem(OS_TAB_KEY, t) } catch { /* 存不了只是下次不記得，不影響畫面 */ }
  hFilter.value = 'all'
  loadFindings()
}
const inTab = <T extends { os_bucket?: string | null }>(rows: T[]): T[] =>
  osTab.value === 'all' ? rows : rows.filter((r) => (r.os_bucket || 'unknown') === osTab.value)
const osQ = computed(() => (osTab.value === 'all' ? '' : `&os=${osTab.value}`))
const tabLabel = computed(() => OS_LABEL[osTab.value])
const accounts = computed(() => inTab(allAccounts.value))

// ===== 稽核摘要（依 OS，後端算）=====
const findingsCount = ref<number | null>(null)
const summary = ref<Summary | null>(null)
const summaryError = ref('')
const beginF = useLatest()
async function loadFindings() {
  const isLatest = beginF()
  findingsCount.value = null
  summary.value = null
  summaryError.value = ''
  try {
    const f = await apiFetch<{ items: any[]; summary: Summary }>('/api/accounts/findings',
      { query: { os: osTab.value === 'all' ? undefined : osTab.value } })
    if (!isLatest()) return
    findingsCount.value = f.items.length
    summary.value = f.summary
  } catch (e: any) {
    if (isLatest()) summaryError.value = e?.data?.detail ?? e?.message ?? '載入失敗'   // 顯示成「載入失敗」，不是 0
  }
}

async function load() {
  loading.value = true
  const [a, h, o] = await Promise.all([
    apiFetch<{ items: Acc[] }>('/api/accounts')
      .catch((e: any) => { accErr.value = `帳號資料載入失敗：${e?.data?.detail ?? e?.message ?? '原因不明'}`; return { items: [] as Acc[] } }),
    // 不可以吞成「沒有資料」：程式壞掉被顯示成 0 台是最糟的失敗方式（2026-09-17）
    apiFetch<{ items: HostRow[]; os_counts?: Partial<Record<OsBucket, number>> }>('/api/accounts/inventoried-hosts')
      .catch((e: any) => { hostsError.value = e?.data?.detail ?? e?.message ?? '載入失敗'; return { items: [] as HostRow[], os_counts: undefined } }),
    apiFetch<{ items: OcpNode[] }>('/api/accounts/ocp-nodes')
      .catch((e: any) => { ocpErr.value = `OCP 節點清單載入失敗：${e?.data?.detail ?? e?.message ?? '原因不明'}`; return { items: [] as OcpNode[] } }),
  ])
  allAccounts.value = a.items ?? []
  allHosts.value = h.items ?? []
  osCounts.value = h.os_counts ?? {}
  ocpNodes.value = o.items ?? []
  osTab.value = defaultOsTab(osCounts.value, rememberedTab(), ocpNodes.value.length)
  loading.value = false
  await loadFindings()
}
onMounted(load)

// ===== 盤點到的主機（V2Table）=====
// 已納管＝收集帳號 7 天內連得上（manage_state.summarize() 的 ONBOARDED，後端算好帶出來），跟首頁同一把尺。
const hFilter = ref<'all' | 'onboarded' | 'stale'>('all')
const hostKw = ref('')
const hostsOpen = ref(false)     // 預設收合（2026-10-20 使用者：展開時把下面的彙總數字擠出畫面）
const hosts = computed(() => inTab(allHosts.value))
const onboardedCount = computed(() => hosts.value.filter((h) => h.onboarded).length)
const staleCount = computed(() => hosts.value.filter((h) => !h.onboarded).length)
// 只篩這張表，所以點了就先清掉搜尋字（數字＝看到的筆數）
function pickHosts(k: 'onboarded' | 'stale') { hFilter.value = hFilter.value === k ? 'all' : k; hostKw.value = ''; hostsOpen.value = true }
// 項次：預設排序（高風險條數多→少、同數照 IP）先編好，重排後同一台仍是同一號
const numbered = computed(() => [...hosts.value]
  .sort((a, b) => (b.findings_high - a.findings_high) || a.ip.localeCompare(b.ip, undefined, { numeric: true }))
  .map((h, i) => ({
    ...h, __no: i + 1,
    onb: h.onboarded ? '已納管' : '7 天內沒連上',
    cia: h.cia_registered === false ? 'CIA 待補登' : '',
    who: h.custodian && h.custodian !== h.contact ? `${h.contact || '—'}／${h.custodian}` : (h.contact || ''),
  })))
const shownHosts = computed(() => {
  let rows = numbered.value
  if (hFilter.value === 'onboarded') rows = rows.filter((h) => h.onboarded)
  else if (hFilter.value === 'stale') rows = rows.filter((h) => !h.onboarded)
  const q = hostKw.value.trim().toLowerCase()
  if (!q) return rows
  return rows.filter((h) => [h.ip, h.hostname, h.asset_serial, h.os, h.environment, h.physical_location, h.department, h.contact, h.custodian]
    .some((v) => String(v ?? '').toLowerCase().includes(q)))
})
const sudoLabel = computed(() => (osTab.value === 'windows' ? '本機管理員' : osTab.value === 'all' ? '有 sudo／本機管理員' : '有 sudo'))
const HOST_COLS = computed(() => [
  { k: 'ip', label: 'IP', mono: true, link: (r: any) => `${AM}?ip=${encodeURIComponent(r.ip)}` },
  { k: 'hostname', label: '主機名稱', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) },
  { k: 'onb', label: '已納管', tone: (r: any) => (r.onboarded ? 'prod' : 'none') },
  { k: 'cia', label: 'CIA', tone: (r: any) => (r.cia ? 'test' : '') },
  { k: 'physical_location', label: '機房' }, { k: 'environment', label: '環境' }, { k: 'os', label: '作業系統' },
  { k: 'department', label: '使用單位（部門）' }, { k: 'who', label: '窗口／保管者' },
  { k: 'accounts', label: '帳號數', mono: true, link: (r: any) => `${AM}?ip=${encodeURIComponent(r.ip)}` },
  { k: 'human', label: '真人', mono: true }, { k: 'sudoers', label: sudoLabel.value, mono: true },
  { k: 'never_login', label: '從未登入', mono: true },
  { k: 'findings', label: '稽核發現', mono: true, link: (r: any) => (r.findings ? `${AM}?tab=findings&ip=${encodeURIComponent(r.ip)}` : null),
    text: (r: any) => (r.findings_high ? `${r.findings}（高 ${r.findings_high}）` : r.findings) },
  { k: 'collected_at', label: '盤點時間', mono: true },
])
const OCP_COLS = [
  { k: 'cluster', label: '叢集', link: (r: any) => `/v2/arch?tab=ocpdetail${r.cluster ? `&cluster=${encodeURIComponent(r.cluster)}` : ''}` },
  { k: 'hostname', label: '主機名', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) },
  { k: 'ip', label: 'IP', mono: true }, { k: 'role', label: '角色' }, { k: 'environment', label: '環境' }, { k: 'os', label: '登記 OS' },
  { k: 'acct', label: '帳號盤點' },
]
const ocpRows = computed(() => ocpNodes.value.map((n, i) => ({ ...n, __no: i + 1, key: `${n.asset_serial}-${n.ip}`, acct: n.has_accounts ? '有帳號資料' : '不盤點（帳號由 OCP 平台管理）' })))

// ===== 特權帳號趨勢 =====
const trend = ref<PrivSnap[]>([])
onMounted(async () => {
  try { trend.value = (await apiFetch<{ items: PrivSnap[] }>('/api/accounts/privileged-trend')).items }
  catch { /* 輔助小圖，載不到不擋整頁（下面有「還沒有快照」的說明） */ }
})
const latestSnap = computed(() => trend.value[trend.value.length - 1] ?? null)
const privItems = computed(() => privBreakdown(accounts.value, osTab.value))
const trendPath = computed(() => {
  const xs = trend.value
  if (xs.length < 2) return ''
  const vals = xs.map((s) => s.privileged)
  const max = Math.max(...vals, 1), min = Math.min(...vals, 0)
  const span = max - min || 1
  return xs.map((s, i) => `${i === 0 ? 'M' : 'L'}${((i / (xs.length - 1)) * 100).toFixed(1)},${(30 - ((s.privileged - min) / span) * 28 - 1).toFixed(1)}`).join(' ')
})
const trendDelta = computed(() => (trend.value.length < 2 ? null : trend.value[trend.value.length - 1].privileged - trend.value[0].privileged))

// ===== 數字卡：每張的網址只帶自己的條件（其他條件清掉），數字＝點進去的筆數 =====
// 高／中風險帶 verdict=fail、查不到帶 verdict=unknown、特權帶 priv=1（後端同一個 PRIV_SQL，含標準管理帳號）
const kpis = computed(() => {
  const sm = summary.value
  const v = (x: number | undefined) => (sm ? (x ?? 0) : null)
  return [
    { n: accounts.value.length as number | null, label: '受稽核帳號', to: `${AM}?tab=matrix${osQ.value}`, tone: '' },
    { n: findingsCount.value, label: '稽核發現', to: `${AM}?tab=findings${osQ.value}`, tone: '' },
    { n: v(sm?.fail_high), label: '高風險不合規', to: `${AM}?tab=findings&sev=high&verdict=fail${osQ.value}`, tone: 'bad' },
    { n: v(sm?.fail_medium), label: '中風險不合規', to: `${AM}?tab=findings&sev=medium&verdict=fail${osQ.value}`, tone: 'warn' },
    { n: v(sm?.unknown), label: '查不到（權限不足）', to: `${AM}?tab=findings&verdict=unknown${osQ.value}`, tone: 'warn' },
    { n: v(sm?.privileged), label: osTab.value === 'windows' ? '特權帳號（本機管理員等）' : '特權帳號', to: `${AM}?tab=accounts&priv=1${osQ.value}`, tone: '' },
    { n: v(sm?.humans), label: '真人帳號', to: `${AM}?tab=accounts&kind=human${osQ.value}`, tone: '' },
  ]
})
const checks = computed(() => dashboardChecks(accounts.value, osTab.value))
function go(to: string) { navigateTo(to) }
</script>

<template>
  <div>
    <p v-if="loading" class="muted">載入中…</p>
    <p v-else-if="accErr" class="err">{{ accErr }}（這是程式或連線出錯，不是「沒有帳號」）</p>
    <p v-else-if="allAccounts.length === 0" class="muted" data-testid="acct-dash-empty">
      還沒有帳號資料：到 <NuxtLink to="/v2/inventory/account?tab=identity">7.2 帳號盤點「收集身分」</NuxtLink> 收一輪。
    </p>
    <template v-else>
      <div class="bar" role="tablist" data-test="os-tabs">
        <button v-for="t in tabs" :key="t.key" type="button" role="tab" class="chip" :class="{ on: osTab === t.key, unk: t.key === 'unknown' }"
                :aria-selected="osTab === t.key" :data-test="`os-tab-${t.key}`" @click="pickOs(t.key)">
          {{ t.label }} <b>{{ t.n }}</b> 台
        </button>
        <InfoNote>每台照「收集時機器自己回報的作業系統」分；舊資料沒回報的，改看資產庫登記的作業系統。兩邊都判不出來的歸「OS 未知」，照樣計數、可以點進去看是哪幾台。<br><br>Windows 沒有 sudo／UID 0／SSH 金鑰，所以 Windows 分頁不擺這幾張卡，改看「本機管理員」「已停用」。「全部」分頁裡只適用某一種 OS 的卡片會標「不含 Windows」或「僅 Windows」，數字也只算那些主機。</InfoNote>
      </div>
      <p v-if="ocpErr" class="err">{{ ocpErr }}</p>

      <section v-if="osTab === 'ocp'" class="box" data-test="ocp-nodes">
        <h3 class="h3">OCP 節點 <b>{{ ocpNodes.length }}</b> 台
          <InfoNote>跟「OCP 逐台明細」同一份清單：資產庫登記的作業系統是 OCP／OpenShift／CoreOS／RHCOS，或有人手動指定成 OpenShift 節點。OCP 另有納管方式（不走 SSH 建本機帳號）、我們沒有帳密，帳號由 OCP 平台管理，所以標「不盤點」（刻意，不是漏收）。</InfoNote>
        </h3>
        <V2Table :rows="ocpRows" prefix="O" row-key="key" unit="台" :cols="OCP_COLS" export-name="帳號儀表板_OCP節點" empty="資產庫沒有認定為 OCP 的節點" />
      </section>
      <p v-if="accounts.length === 0 && osTab !== 'ocp'" class="muted">{{ tabLabel }} 主機沒有帳號資料。</p>
      <template v-else-if="osTab !== 'ocp' || accounts.length">
        <section class="box">
          <div class="bar">
            <button class="tg" type="button" data-test="hosts-toggle" @click="hostsOpen = !hostsOpen">
              {{ hostsOpen ? '▾' : '▸' }} 盤點到的{{ osTab === 'all' ? '' : ' ' + tabLabel + ' ' }}主機 <b>{{ hosts.length }}</b> 台
            </button>
            <span class="muted">其中</span>
            <button type="button" class="chip" :class="{ on: hFilter === 'onboarded' }" data-test="hosts-onboarded" @click="pickHosts('onboarded')">已納管 <b>{{ onboardedCount }}</b></button>
            <button type="button" class="chip" :class="{ on: hFilter === 'stale' }" data-test="hosts-stale" @click="pickHosts('stale')">7 天內沒連上 <b>{{ staleCount }}</b></button>
            <InfoNote>這一輪實際收到帳號資料的是這幾台；點 IP 或帳號數看那一台的完整帳號清單，點主機名看主機履歷。沒出現在這裡的機器代表這一輪沒收到（可能還沒納管、或被排除）。「已納管／7 天內沒連上」跟首頁「已納管」同一把尺（收集帳號 7 天內連得上，CIA 有沒有登記都算），點了只篩這張表。CIA 未登記的主機照樣列著，「CIA」欄標「待補登」。</InfoNote>
            <span class="sp" />
            <template v-if="hostsOpen">
              <input v-model="hostKw" class="in" type="search" placeholder="搜 IP／主機名／機房／OS／部門／窗口" aria-label="搜尋盤點主機">
              <a v-if="isAdmin" class="xb" :href="`${apiBase}/api/accounts/inventoried-hosts/export`">匯出 Excel（全部 OS）</a>
              <span v-else class="muted" data-test="admin-only">Excel 匯出限管理員</span>
            </template>
          </div>
          <template v-if="hostsOpen">
            <p v-if="hostsError" class="err">主機清單載入失敗：{{ hostsError }}（這是程式或連線出錯，不是「沒有資料」）</p>
            <V2Table v-else :rows="shownHosts" prefix="H" row-key="ip" unit="台" :cols="HOST_COLS" export-name="帳號儀表板_盤點主機"
                     :empty="hostKw ? `沒有符合「${hostKw}」的主機` : '這一輪沒有收到任何主機的帳號資料'" />
          </template>
        </section>

        <h3 class="h3">彙總數字<span v-if="osTab !== 'all'" class="scope">{{ tabLabel }}</span>
          <InfoNote>上面那批主機加總起來的結果。每個框都可以點，會切到「合規表／稽核發現／帳號清單」看對應的帳號或發現（只帶這張卡的條件，數字＝點進去的筆數）。</InfoNote>
        </h3>
        <p v-if="summaryError" class="err">稽核摘要載入失敗：{{ summaryError }}（這是程式或連線出錯，不是「沒有不合規」）</p>
        <div class="tiles" data-test="kpi-cards">
          <button v-for="k in kpis" :key="k.label" type="button" class="tile" :class="[k.tone, { hot: k.tone && (k.n ?? 0) > 0 }]" @click="go(k.to)">
            <span class="tn">{{ k.n === null ? (summaryError ? '—' : '…') : k.n }}</span>
            <span class="tl">{{ k.label }}</span>
          </button>
        </div>

        <h3 class="h3">{{ osTab === 'all' ? '特權帳號趨勢' : '特權帳號' }}<span v-if="osTab !== 'all'" class="scope">{{ tabLabel }}</span>
          <InfoNote>特權＝有 sudo／UID 0／標準管理帳號（Windows＝本機管理員／標準管理帳號）。每次採集後存一筆現值，是採集後才開始累積的——點少代表剛開始收，不是沒有特權帳號。歷史快照沒有分 OS，所以趨勢線只在「全部」分頁畫；各 OS 分頁看目前的組成，每一格都可以點。</InfoNote>
        </h3>
        <div class="box trend" data-test="priv-now">
          <div class="tnow">
            <button v-if="osTab !== 'all'" type="button" class="big lb" @click="go(`${AM}?tab=accounts&priv=1${osQ}`)">{{ summary ? summary.privileged : '…' }}</button>
            <span v-else class="big">{{ latestSnap ? latestSnap.privileged : '—' }}</span>
            <div class="muted">目前特權帳號
              <span v-if="osTab === 'all' && trendDelta !== null" :class="trendDelta > 0 ? 'up' : trendDelta < 0 ? 'down' : ''">
                {{ trendDelta > 0 ? '▲ +' + trendDelta : trendDelta < 0 ? '▼ ' + trendDelta : '＝ 持平' }}<template v-if="trendDelta !== 0"> 自首筆</template>
              </span>
            </div>
            <div v-if="osTab === 'all' && latestSnap" class="muted sm">sudo {{ latestSnap.sudoers }}・UID0 {{ latestSnap.uid0 }}・免密碼 {{ latestSnap.nopasswd }}・管理帳號 {{ latestSnap.mgmt }}（{{ latestSnap.taken_at }} 快照）</div>
            <div class="privs">
              <button v-for="p in privItems" :key="p.label" type="button" class="lb" @click="go(p.to)">{{ p.label }} <b>{{ p.n }}</b></button>
            </div>
          </div>
          <div v-if="osTab === 'all'" class="tchart">
            <svg v-if="trendPath" viewBox="0 0 100 30" preserveAspectRatio="none" class="spark" aria-label="特權帳號趨勢">
              <path :d="trendPath" fill="none" stroke="var(--brand)" stroke-width="1.2" vector-effect="non-scaling-stroke" />
            </svg>
            <p v-else class="muted sm">{{ trend.length === 1 ? '已有第一筆，再採集一輪就畫得出趨勢線。' : '還沒有快照——收一輪帳號後開始累積。' }}</p>
          </div>
        </div>

        <h3 class="h3">各項合規檢查中招數<span v-if="osTab !== 'all'" class="scope">{{ tabLabel }}</span><InfoNote>點框看是哪些帳號（切到合規表、只套那一欄的條件）。「查不到 N」＝這一欄查不到（權限不足／沒收到），不算進上面的數字，點了看是哪些。</InfoNote></h3>
        <div class="tiles" data-test="check-cards">
          <div v-for="c in checks" :key="c.key" class="tile" :data-test="`check-${c.key}`" :class="[c.tone, { zero: c.n === 0, hot: (c.n ?? 0) > 0 }]">
            <button type="button" class="tbtn" :disabled="c.n === 0" @click="c.n === null ? go(c.unknownTo) : go(c.to)">
              <span class="tn">{{ c.n === null ? '查不到' : c.n }}</span>
              <span class="tl">{{ c.label }}<template v-if="c.scope">（{{ c.scope }}）</template></span>
            </button>
            <button v-if="c.n !== null && c.unknown > 0" type="button" class="lb sm" @click="go(c.unknownTo)">查不到 {{ c.unknown }}</button>
          </div>
        </div>
        <p v-if="summary?.run" class="muted sm">最後盤點 · {{ summary.run.started_at }}（{{ summary.run.host_count }} 台，全部 OS）</p>
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 8px; font-size: 13px; }
.sp { flex: 1; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip.unk:not(.on) { color: var(--warn-text); }
.chip b, .tn, .big { font-family: var(--disp); }
.tg { background: none; border: 0; padding: 0; cursor: pointer; font-family: inherit; font-size: 14px; font-weight: 600; color: var(--ink); }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; min-width: 240px; border: 1px solid var(--border-strong); border-radius: 8px; }
.xb { font-size: 12px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); text-decoration: none; }
.box { margin: 8px 0 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.h3 { margin: 14px 0 6px; font-size: 14px; font-weight: 600; color: var(--ink-soft); display: flex; gap: 6px; align-items: center; }
.scope { padding: 1px 8px; border-radius: 999px; font-size: 11px; background: var(--mint); color: var(--brand-dark); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; margin-bottom: 8px; }
.tile { font-family: inherit; text-align: left; display: flex; flex-direction: column; gap: 2px; padding: 9px 11px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); cursor: pointer; }
div.tile { cursor: default; }
.tbtn { font-family: inherit; text-align: left; display: flex; flex-direction: column; gap: 2px; background: none; border: 0; padding: 0; cursor: pointer; }
.tbtn:disabled { cursor: default; }
.tile:hover { border-color: var(--border-strong); }
.tn { font-size: 20px; font-weight: 700; color: var(--brand-dark); }
.tl { font-size: 12px; color: var(--muted); }
.tile.bad.hot { border-color: var(--bad); }
.tile.bad.hot .tn { color: var(--bad); }
.tile.warn.hot { border-color: var(--warn); }
.tile.warn.hot .tn { color: var(--warn-text); }
.tile.zero { opacity: .55; }
.trend { display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }
.tnow { min-width: 180px; }
.big { font-size: 30px; font-weight: 700; color: var(--brand-dark); }
.tchart { flex: 1; min-width: 200px; }
.spark { width: 100%; height: 60px; display: block; }
.privs { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 6px; font-size: 12px; }
.up { color: var(--bad); font-weight: 600; }
.down { color: var(--good); font-weight: 600; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; text-align: left; }
.lb.big { font-size: 30px; }
.muted { color: var(--muted); font-size: 13px; }
.sm { font-size: 12px; }
.err { color: var(--bad); font-size: 13px; }
</style>
