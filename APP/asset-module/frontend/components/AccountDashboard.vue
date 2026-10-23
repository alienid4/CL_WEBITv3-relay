<script setup lang="ts">
// 帳號儀表板：只放彙總數字框，每個框都可點下鑽到「帳號合規表」對應的資料。
// 表格獨立在 /account-matrix（使用者 2026-07-23 拍板：儀表板與表格分兩頁）。
// 點框框 → navigateTo('/account-matrix?…') 帶 query，合規表頁自動預選分頁＋套篩選。
//
// 依 OS 分頁（2026-10-21 使用者：「3-7 帳號儀表板 這要分 WINDOWS 跟 LINUX」）：
// 最上面 Linux｜Windows｜（AIX）｜（OS 未知）｜全部，整頁（主機清單、彙總、特權、中招卡）跟著篩。
// 每台歸哪個 OS 由後端 host_os_buckets 決定；中招卡判定跟合規表共用 composables/accountCells，
// 點進去帶 &os=，合規表用同一個分桶篩——卡片數字＝點進去的筆數。
import { dashboardChecks, defaultOsTab, OS_LABEL, osTabs, privBreakdown } from '~/composables/accountCells'
import type { CellAccount, OsBucket, OsTab } from '~/composables/accountCells'

const { apiFetch } = useApi()
const route = useRoute()

type Acc = CellAccount & { kind?: string | null }
interface Summary {
  has_data: boolean
  fail_high?: number; fail_medium?: number; unknown?: number
  accounts?: number; privileged?: number; humans?: number
  run?: { started_at: string; host_count: number }
}

// 盤點到哪幾台（2026-09-16 使用者：「我只知道四台? 不知道哪四台，應該是先列出哪幾台，
// 我再進去點後才是那一台的全部帳號資訊」，並補「每個盤點都是這概念」）。
// 彙總數字對得起來也沒用——人要做的事是「去那台上面改」，所以主機清單要在最前面。
interface HostRow {
  ip: string; asset_serial: string | null; hostname: string | null
  os: string | null; environment: string | null; physical_location: string | null
  department: string | null; contact: string | null; custodian: string | null
  accounts: number; human: number; sudoers: number; never_login: number
  findings: number; findings_high: number; collected_at: string | null; excluded: boolean
  // 已納管＝收集帳號 7 天內連得上（manage_state.summarize() 的 ONBOARDED，EXIST1 起跟 CIA 無關），
  // 後端 account_inventory.inventoried_hosts() 算好直接帶出來，這裡不自己另判。
  onboarded: boolean
  // CIA 有沒有登記（業務資料完整度）——跟已納管是兩件事
  cia_registered?: boolean
  os_bucket?: OsBucket
}
const allHosts = ref<HostRow[]>([])
const osCounts = ref<Partial<Record<OsBucket, number>>>({})

// ===== OS 分頁 =====
const OS_TAB_KEY = 'acctDash.osTab'
const osTab = ref<OsTab>('linux')
// OCP 分頁（2026-10-21）：OCP 節點清單另外抓（跟 OCP 叢集頁同一份來源）；台數＝節點數
interface OcpNode { cluster: string | null; hostname: string | null; ip: string | null; asset_serial: string | null
  role: string | null; os: string | null; environment: string | null; has_accounts: boolean; no?: number }
const ocpNodes = ref<OcpNode[]>([])
const ocpErr = ref('')
const tabs = computed(() => osTabs(osCounts.value, ocpNodes.value.length))
const { sortKey: oKey, sortDir: oDir, toggle: oToggle, sorted: ocpSorted } = useSort(ocpNodes, 'no')
function rememberedTab(): string | null {
  const q = route.query.os
  if (typeof q === 'string' && q in OS_LABEL) return q          // 從合規表帶 ?os= 回來
  try { return localStorage.getItem(OS_TAB_KEY) } catch { return null } // 私密視窗／封鎖儲存：退回預設
}
function pickOs(t: OsTab) {
  if (osTab.value === t) return
  osTab.value = t
  try { localStorage.setItem(OS_TAB_KEY, t) } catch { /* 存不了只是下次不記得，不影響畫面 */ }
  hOnboardFilter.value = 'all'
  loadFindings()
}
const inTab = <T extends { os_bucket?: string | null }>(rows: T[]): T[] =>
  osTab.value === 'all' ? rows : rows.filter((r) => (r.os_bucket || 'unknown') === osTab.value)
const osQ = computed(() => (osTab.value === 'all' ? '' : `&os=${osTab.value}`))
const tabLabel = computed(() => OS_LABEL[osTab.value])

const hosts = computed(() => inTab(allHosts.value))
const hostsError = ref('')
const hostKw = ref('')
const rt = useRuntimeConfig()
// 已納管交叉引用（2026-10-18）：「盤點到的主機」本來就刻意包含 CIA 沒登記的主機
// （帳號稽核要看得到這個缺口，不能為了對齊首頁數字而把它們拿掉），所以用篩選接回去，
// 不是把清單改小。'all' 不篩；點過卡片後只看那一類，再點一次（或點「全部」）取消。
const hOnboardFilter = ref<'all' | 'onboarded' | 'unregistered'>('all')
function pickOnboardFilter(k: 'onboarded' | 'unregistered') {
  hOnboardFilter.value = hOnboardFilter.value === k ? 'all' : k
  hostsOpen.value = true          // 點了篩選就是要看那幾台：自動展開
}
// 主機清單預設收合（2026-10-20 使用者）：展開時把下面的彙總數字擠出畫面。
// 跟服務／軟體／EOS 的 InventoryHosts 同一個行為；台數仍在標題上，點一下展開。
const hostsOpen = ref(false)
const onboardedCount = computed(() => hosts.value.filter((h) => h.onboarded).length)
const notRegisteredCount = computed(() => hosts.value.filter((h) => !h.onboarded).length)
// 項次：以這個分頁的預設排序（高風險條數多→少、同數照 IP）編號，使用者重排後同一台仍是同一號
const numbered = computed(() => {
  const base = [...hosts.value].sort((a, b) => (b.findings_high - a.findings_high)
    || a.ip.localeCompare(b.ip, undefined, { numeric: true }))
  return base.map((h, i) => ({ ...h, seq: i + 1 }))
})
const shownHosts = computed(() => {
  let rows = numbered.value
  if (hOnboardFilter.value === 'onboarded') rows = rows.filter((h) => h.onboarded)
  else if (hOnboardFilter.value === 'unregistered') rows = rows.filter((h) => !h.onboarded)
  const q = hostKw.value.trim().toLowerCase()
  if (!q) return rows
  return rows.filter((h) => [h.ip, h.hostname, h.asset_serial, h.os,
                             h.environment, h.physical_location,
                             h.department, h.contact, h.custodian]
    .some((v) => (v || '').toString().toLowerCase().includes(q)))
})
// 預設照「高風險條數」排——這頁的用途是決定先看哪一台，不是按 IP 唸名冊
const { sortKey: hKey, sortDir: hDir, toggle: hToggle, sorted: hSorted } =
  useSort(shownHosts, 'findings_high', 'desc')

const allAccounts = ref<Acc[]>([])
const accounts = computed(() => inTab(allAccounts.value))
// 稽核發現數與摘要（高／中風險、查不到、特權、真人）由後端依 OS 篩（?os=），換分頁重抓
const findingsCount = ref<number | null>(null)
const summary = ref<Summary | null>(null)
const summaryError = ref('')
const loading = ref(true)

async function loadFindings() {
  findingsCount.value = null
  summary.value = null
  summaryError.value = ''
  try {
    const f = await apiFetch<{ items: any[]; summary: Summary }>('/api/accounts/findings',
      { query: { os: osTab.value === 'all' ? undefined : osTab.value } })
    findingsCount.value = f.items.length
    summary.value = f.summary
  } catch (e: any) {
    // 顯示成「載入失敗」，不是 0（0 會被當成「沒有不合規」）
    summaryError.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}

async function load() {
  loading.value = true
  try {
    const [a, h, o] = await Promise.all([
      apiFetch<{ items: Acc[] }>('/api/accounts'),
      // ⚠️ 不可以 .catch(() => ({items:[]}))：2026-09-17 踩到——SQL 寫錯欄位名讓這支 500，
      // 錯誤被吞掉之後畫面顯示「0 台／這一輪沒有收到任何主機的帳號資料」，
      // 但實際上有 7 台。**程式壞掉被顯示成「沒有資料」是最糟的失敗方式。**
      apiFetch<{ items: HostRow[]; os_counts?: Partial<Record<OsBucket, number>> }>('/api/accounts/inventoried-hosts')
        .catch((e: any) => { hostsError.value = e?.data?.detail ?? e?.message ?? '載入失敗'; return { items: [] as HostRow[], os_counts: undefined } }),
      apiFetch<{ items: OcpNode[] }>('/api/accounts/ocp-nodes')
        .catch((e: any) => { ocpErr.value = `OCP 節點清單載入失敗：${e?.data?.detail ?? e?.message ?? '原因不明'}`; return { items: [] as OcpNode[] } }),
    ])
    ocpNodes.value = (o.items ?? []).map((n, i) => ({ ...n, no: i + 1 }))
    allAccounts.value = a.items
    allHosts.value = h.items ?? []
    osCounts.value = h.os_counts ?? {}
    osTab.value = defaultOsTab(osCounts.value, rememberedTab(), ocpNodes.value.length)
  } catch { /* 拿不到就留空：下面「沒有帳號資料」那行會提示去收一輪 */ } finally {
    loading.value = false
  }
  await loadFindings()
}
onMounted(load)

// 特權帳號趨勢（2026-09-19）：每次採集後存一筆現值，這裡畫成折線。
// 資料是採集後才開始累積的——點數少代表剛開始收，不是沒有特權帳號。
interface PrivSnap {
  taken_at: string; total_accounts: number; privileged: number
  sudoers: number; uid0: number; nopasswd: number; mgmt: number; human: number
}
const trend = ref<PrivSnap[]>([])
onMounted(async () => {
  try { trend.value = (await apiFetch<{ items: PrivSnap[] }>('/api/accounts/privileged-trend')).items }
  catch { /* 輔助小圖，載不到不擋整頁 */ }
})
const latestSnap = computed(() => trend.value[trend.value.length - 1] ?? null)
// 特權組成（現值，依分頁）：每格可點，數字＝點進去的筆數
const privItems = computed(() => privBreakdown(accounts.value, osTab.value))
// SVG 折線：把 privileged 序列映射到 0..100 × 0..30 的座標。只有 1 點畫不出線，改標「剛開始累積」。
const trendPath = computed(() => {
  const xs = trend.value
  if (xs.length < 2) return ''
  const vals = xs.map((s) => s.privileged)
  const max = Math.max(...vals, 1), min = Math.min(...vals, 0)
  const span = max - min || 1
  return xs.map((s, i) => {
    const x = (i / (xs.length - 1)) * 100
    const y = 30 - ((s.privileged - min) / span) * 28 - 1
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})
const trendDelta = computed(() => {
  if (trend.value.length < 2) return null
  return trend.value[trend.value.length - 1].privileged - trend.value[0].privileged
})

// 上排：總量 / 稽核發現 / 風險 / 資料落差 / 帳號屬性。每個框都連到合規表對應視圖。
// 每張卡點進去的清單筆數＝卡片數字（2026-10-21）：高／中風險帶 verdict=fail、查不到帶 verdict=unknown、
// 特權帶 priv=1（後端同一個 PRIV_SQL，含標準管理帳號）。
// n=null＝還在載入或載入失敗（畫面顯示「…」或「—」，不顯示 0）
const kpis = computed(() => {
  const sm = summary.value
  const v = (x: number | undefined) => (sm ? (x ?? 0) : null)
  return [
    { n: accounts.value.length as number | null, label: '受稽核帳號', to: `/account-matrix?tab=matrix${osQ.value}`, tone: '' },
    { n: findingsCount.value, label: '稽核發現', hint: '看明細', to: `/account-matrix?tab=findings${osQ.value}`, tone: '' },
    { n: v(sm?.fail_high), label: '高風險不合規', to: `/account-matrix?tab=findings&sev=high&verdict=fail${osQ.value}`, tone: 'bad' },
    { n: v(sm?.fail_medium), label: '中風險不合規', to: `/account-matrix?tab=findings&sev=medium&verdict=fail${osQ.value}`, tone: 'warn' },
    { n: v(sm?.unknown), label: '查不到（權限不足）', hint: '不等於合格', to: `/account-matrix?tab=findings&verdict=unknown${osQ.value}`, tone: 'warn' },
    { n: v(sm?.privileged), label: osTab.value === 'windows' ? '特權帳號（本機管理員等）' : '特權帳號', hint: `/ ${accounts.value.length} 全部`, to: `/account-matrix?priv=1${osQ.value}`, tone: '' },
    { n: v(sm?.humans), label: '真人帳號', to: `/account-matrix?kind=human${osQ.value}`, tone: '' },
  ]
})

// 下排：各項合規檢查中招數。每個框連到合規表並套上該欄篩選（col+val+os）。
// Windows 分頁不擺 sudo／UID 0／金鑰（不適用），改擺本機管理員／已停用；全部分頁的 OS 專屬卡標範圍。
const checks = computed(() => dashboardChecks(accounts.value, osTab.value))

function go(to: string) { navigateTo(to) }
</script>

<template>
  <div>
    <p class="lead">帳號盤點的彙總數字
      <InfoNote><b>每個框都可以點</b>，點下去會帶你到「帳號合規表」看對應的實際帳號／發現。要收資料到「盤點作業」。</InfoNote></p>

    <p v-if="loading" class="muted">載入中…</p>
    <p v-else-if="allAccounts.length === 0" class="muted">
      沒有帳號資料，到 <NuxtLink class="dl" to="/account-ops">盤點作業</NuxtLink> 收一輪。
    </p>
    <template v-else>
      <!-- OS 分頁：整頁跟著篩。台數＝該 OS 的盤點主機數，點分頁就是下鑽 -->
      <div class="ostabs" role="tablist" data-test="os-tabs">
        <button v-for="t in tabs" :key="t.key" type="button" role="tab" class="ostab"
                :class="{ active: osTab === t.key, unk: t.key === 'unknown' }"
                :aria-selected="osTab === t.key" :data-test="`os-tab-${t.key}`" @click="pickOs(t.key)">
          {{ t.label }} <b>{{ t.n }}</b> 台
        </button>
        <InfoNote>每台照「收集時機器自己回報的作業系統」分；舊資料沒回報的，改看資產庫登記的作業系統。兩邊都判不出來的歸「OS 未知」，照樣計數、可以點進去看是哪幾台。<br><br>Windows 沒有 sudo／UID 0／SSH 金鑰，所以 Windows 分頁不擺這幾張卡，改看「本機管理員」「已停用」。「全部」分頁裡只適用某一種 OS 的卡片會標「不含 Windows」或「僅 Windows」，數字也只算那些主機。</InfoNote>
      </div>

      <p v-if="ocpErr" class="err-line">{{ ocpErr }}</p>
      <!-- OCP 分頁：列出每個節點、標「不盤點」——OCP 另有納管方式、我們沒有帳密，帳號由 OCP 平台管理（刻意，不是漏收） -->
      <section v-if="osTab === 'ocp'" class="hostsec" data-test="ocp-nodes">
        <p class="sechd">OCP 節點 <b>{{ ocpNodes.length }}</b> 台
          <InfoNote>跟「OCP 叢集明細」同一份清單：資產庫登記的作業系統是 OCP／OpenShift／CoreOS／RHCOS，或有人手動指定成 OpenShift 節點。OCP 節點跑的也是 RHEL 系（RHCOS），收集時會回報成 RHEL，所以這裡先認 OCP、不會被算成一般 Linux。登記只寫 RHEL、又沒手動指定的 OCP 節點這裡認不出來。<br><br>OCP 另有納管方式（不走 SSH 建本機帳號）、我們沒有帳密，帳號由 OCP 平台管理，所以標「不盤點」。</InfoNote>
        </p>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <SortTh k="no" :active="oKey" :dir="oDir" @sort="oToggle">#</SortTh>
                <SortTh k="cluster" :active="oKey" :dir="oDir" @sort="oToggle">叢集</SortTh>
                <SortTh k="hostname" :active="oKey" :dir="oDir" @sort="oToggle">主機名</SortTh>
                <SortTh k="ip" :active="oKey" :dir="oDir" @sort="oToggle">IP</SortTh>
                <SortTh k="role" :active="oKey" :dir="oDir" @sort="oToggle">角色</SortTh>
                <SortTh k="environment" :active="oKey" :dir="oDir" @sort="oToggle">環境</SortTh>
                <SortTh k="os" :active="oKey" :dir="oDir" @sort="oToggle">登記 OS</SortTh>
                <SortTh k="has_accounts" :active="oKey" :dir="oDir" @sort="oToggle">帳號盤點</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!ocpSorted.length"><td colspan="8" class="muted">資產庫沒有認定為 OCP 的節點。</td></tr>
              <tr v-for="n in ocpSorted" :key="`${n.asset_serial}-${n.ip}`">
                <td class="mono dim">O{{ n.no }}</td>
                <td><NuxtLink class="dl" to="/ocp">{{ n.cluster || '—' }}</NuxtLink></td>
                <td>
                  <NuxtLink v-if="n.asset_serial" class="dl" :to="`/assets/${n.asset_serial}`">{{ n.hostname || n.asset_serial }}</NuxtLink>
                  <template v-else>{{ n.hostname || '—' }}</template>
                </td>
                <td class="mono">{{ n.ip || '—' }}</td>
                <td>{{ n.role || '—' }}</td>
                <td>{{ n.environment || '—' }}</td>
                <td class="dim">{{ n.os || '—' }}</td>
                <td>
                  <span v-if="n.has_accounts">有帳號資料</span>
                  <span v-else class="dim">不盤點（帳號由 OCP 平台管理）</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <p v-if="accounts.length === 0 && osTab !== 'ocp'" class="muted">{{ tabLabel }} 主機沒有帳號資料。</p>
      <template v-else>
      <!-- 先列出「是哪幾台」（2026-09-16 使用者），點進去才是那一台的帳號明細。
           彙總數字往下移、也縮小——那些是結果，主機清單才是可以動手的東西。 -->
      <section class="hostsec">
        <div class="hhd">
          <button class="tg" type="button" data-test="hosts-toggle" @click="hostsOpen = !hostsOpen">
            {{ hostsOpen ? '▾' : '▸' }} 盤點到的{{ osTab === 'all' ? '' : ' ' + tabLabel + ' ' }}主機 <b>{{ hosts.length }}</b> 台
          </button>
          <!-- 2026-10-18：這裡的「盤點到的主機」刻意包含 CIA 沒登記的主機（帳號稽核的重點
               就是看得到這種缺口），不能為了對齊首頁「已納管」數字而把它們拿掉——所以用
               可點的交叉引用接回去，不是改這份清單的範圍。判斷跟首頁同一把尺
               （manage_state.summarize() 的 ONBOARDED），不是這裡自己另判。 -->
          <span class="onb-x">
            其中
            <a class="dl" href="#" :class="{ on: hOnboardFilter === 'onboarded' }"
               title="收集帳號 7 天內連得上——跟首頁「對帳明細」已納管同一把尺（CIA 有沒有登記都算）"
               @click.prevent="pickOnboardFilter('onboarded')">已納管 {{ onboardedCount }}</a>
            ／
            <a class="dl" href="#" :class="{ on: hOnboardFilter === 'unregistered' }"
               title="帳號資料是以前收的，最近 7 天收集帳號沒連上——不在首頁「已納管」的數字裡"
               @click.prevent="pickOnboardFilter('unregistered')">7 天內沒連上 {{ notRegisteredCount }}</a>
            台
          </span>
          <InfoNote>這一輪實際收到帳號資料的是這幾台。<b>點主機名或 IP</b> 進去看那一台的完整帳號清單。<br><br>沒出現在這裡的機器代表這一輪沒收到（可能還沒納管、或被排除），到「盤點作業」看原因。<br><br>「已納管／7 天內沒連上」跟首頁「已納管」同一把尺（收集帳號 7 天內連得上，CIA 有沒有登記都算），點了只篩選這張表。CIA 沒登記的主機照樣列著，「CIA」欄標「待補登」。</InfoNote>
          <span class="spacer" />
          <template v-if="hostsOpen">
            <input v-model="hostKw" class="hkw" placeholder="搜 IP／主機名／機房／OS／部門／窗口" />
            <a class="btn small" :href="`${rt.public.apiBase}/api/accounts/inventoried-hosts/export`">⬇ 匯出 Excel</a>
          </template>
        </div>
        <template v-if="hostsOpen">
        <div v-if="hOnboardFilter !== 'all'" class="filterbar">
          <span class="fb-l">正在看</span>
          <span class="fb-v">{{ hOnboardFilter === 'onboarded' ? '已納管' : '7 天內沒連上' }}</span>
          <span class="fb-n">{{ shownHosts.length }} 筆</span>
          <button class="fb-x" @click="hOnboardFilter = 'all'">✕ 清除篩選</button>
        </div>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <SortTh k="seq" :active="hKey" :dir="hDir" class="num" @sort="hToggle">項次</SortTh>
                <SortTh k="ip" :active="hKey" :dir="hDir" @sort="hToggle">IP</SortTh>
                <SortTh k="hostname" :active="hKey" :dir="hDir" @sort="hToggle">主機名稱</SortTh>
                <SortTh k="onboarded" :active="hKey" :dir="hDir" @sort="hToggle">已納管</SortTh>
                <SortTh k="physical_location" :active="hKey" :dir="hDir" @sort="hToggle">機房</SortTh>
                <SortTh k="environment" :active="hKey" :dir="hDir" @sort="hToggle">環境</SortTh>
                <SortTh k="os" :active="hKey" :dir="hDir" @sort="hToggle">作業系統</SortTh>
                <!-- 部門與窗口（2026-09-17 使用者：「沒這個以後怎麼找窗口盤點」）——
                     盤點的下一步是去找人，沒有窗口這份清單只能看不能用。 -->
                <SortTh k="department" :active="hKey" :dir="hDir" @sort="hToggle">使用單位（部門）</SortTh>
                <SortTh k="contact" :active="hKey" :dir="hDir" @sort="hToggle">窗口</SortTh>
                <SortTh k="accounts" :active="hKey" :dir="hDir" class="num" @sort="hToggle">帳號數</SortTh>
                <SortTh k="human" :active="hKey" :dir="hDir" class="num" @sort="hToggle">真人</SortTh>
                <SortTh k="sudoers" :active="hKey" :dir="hDir" class="num" @sort="hToggle">{{ osTab === 'windows' ? '本機管理員' : osTab === 'all' ? '有 sudo／本機管理員' : '有 sudo' }}</SortTh>
                <SortTh k="never_login" :active="hKey" :dir="hDir" class="num" @sort="hToggle">從未登入</SortTh>
                <SortTh k="findings" :active="hKey" :dir="hDir" class="num" @sort="hToggle">稽核發現</SortTh>
                <SortTh k="collected_at" :active="hKey" :dir="hDir" @sort="hToggle">盤點時間</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="h in hSorted" :key="h.ip">
                <td class="num muted">{{ h.seq }}</td>
                <td class="mono">
                  <NuxtLink class="dl" :to="`/account-matrix?ip=${encodeURIComponent(h.ip)}`"
                            title="看這台的完整帳號清單">{{ h.ip }}</NuxtLink>
                </td>
                <td>
                  <NuxtLink v-if="h.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(h.asset_serial)}`"
                            title="看這台的資產詳細頁">{{ h.hostname || '—' }}</NuxtLink>
                  <template v-else>{{ h.hostname || '—' }}</template>
                </td>
                <td>
                  <span v-if="h.onboarded" class="pill ok" title="收集帳號 7 天內連得上">✓ 已納管</span>
                  <span v-else class="pill off" title="最近 7 天收集帳號沒連上">7 天內沒連上</span>
                  <span v-if="h.cia_registered === false" class="pill off" title="CIA 沒有登記這台——CIA 待補登">CIA 待補登</span>
                </td>
                <td>{{ h.physical_location || '—' }}</td>
                <td>{{ h.environment || '—' }}</td>
                <td class="ell" :title="h.os || ''">{{ h.os || '—' }}</td>
                <td class="ell" :title="h.department || ''">{{ h.department || '—' }}</td>
                <td class="ell" :title="`使用者：${h.contact || '—'}　保管者：${h.custodian || '—'}`">
                  {{ h.contact || '—' }}
                  <span v-if="h.custodian && h.custodian !== h.contact" class="dim sm">／{{ h.custodian }}</span>
                </td>
                <td class="num">
                  <NuxtLink class="dl" :to="`/account-matrix?ip=${encodeURIComponent(h.ip)}`">{{ h.accounts }}</NuxtLink>
                </td>
                <td class="num">{{ h.human }}</td>
                <td class="num">{{ h.sudoers }}</td>
                <td class="num">{{ h.never_login }}</td>
                <td class="num">
                  <NuxtLink v-if="h.findings" class="dl"
                            :class="{ badn: h.findings_high }"
                            :to="`/account-matrix?tab=findings&ip=${encodeURIComponent(h.ip)}`"
                            :title="h.findings_high ? `其中 ${h.findings_high} 條高風險` : ''">{{ h.findings }}</NuxtLink>
                  <span v-else class="muted">0</span>
                </td>
                <td class="mono muted sm">{{ h.collected_at || '—' }}</td>
              </tr>
              <tr v-if="!hSorted.length">
                <td colspan="15" :class="hostsError ? 'err-cell' : 'muted'">
                  <template v-if="hostsError">
                    ✕ 主機清單載入失敗：{{ hostsError }}
                    <div class="sm">這是<b>程式或連線出錯</b>，不是「沒有資料」——請把這行訊息貼給開發者。</div>
                  </template>
                  <template v-else-if="hostKw">沒有符合「{{ hostKw }}」的主機</template>
                  <template v-else>這一輪沒有收到任何主機的帳號資料</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        </template>
      </section>

      <p class="sechd">彙總數字<span v-if="osTab !== 'all'" class="scope">{{ tabLabel }}</span>
        <InfoNote>這些是上面那批主機加總起來的結果。<b>每個框都可以點</b>，會帶你到「帳號合規表」看對應的實際帳號／發現（只看這個 OS 的主機）。</InfoNote>
      </p>
      <p v-if="summaryError" class="err-line">✕ 稽核摘要載入失敗：{{ summaryError }}（這是程式或連線出錯，不是「沒有不合規」）</p>
      <div class="tiles">
        <div v-for="k in kpis" :key="k.label" class="tile clickable" :class="[k.tone, { hot: k.tone && (k.n ?? 0) > 0 }]"
             @click="go(k.to)">
          <div class="t-num">{{ k.n === null ? (summaryError ? '—' : '…') : k.n }}<small v-if="k.hint && k.hint.startsWith('/')" class="of">{{ k.hint }}</small></div>
          <div class="t-lbl">{{ k.label }}<span v-if="k.hint && !k.hint.startsWith('/')" class="hint">{{ k.hint }}</span></div>
          <span class="drill">看資料 →</span>
        </div>
      </div>

      <!-- 特權帳號趨勢：稽核最看「特權帳號是變多還變少」。每次採集後累積一點。 -->
      <section class="trendsec">
        <p class="sechd">{{ osTab === 'all' ? '特權帳號趨勢' : '特權帳號' }}<span v-if="osTab !== 'all'" class="scope">{{ tabLabel }}</span>
          <InfoNote>特權＝有 sudo／UID 0／標準管理帳號（Windows＝本機管理員／標準管理帳號）。每次採集後存一筆現值。<b>這是採集後才開始累積的</b>——點少代表剛開始收，不是沒有特權帳號。稽核最看的是這條線在變多還變少。<br><br>歷史快照沒有分 OS，所以趨勢線只在「全部」分頁畫；各 OS 分頁看的是目前的組成，每一格都可以點。</InfoNote>
        </p>
        <!-- 各 OS 分頁：目前組成（現值，可下鑽）。歷史快照沒分 OS，不拿全部的線冒充這個 OS 的趨勢 -->
        <div v-if="osTab !== 'all'" class="trend-card" data-test="priv-now">
          <div class="trend-now">
            <a class="tn-num dl" href="#" title="看是哪些帳號" @click.prevent="go(`/account-matrix?priv=1${osQ}`)">{{ summary ? summary.privileged : '…' }}</a>
            <div class="tn-lbl">目前特權帳號</div>
          </div>
          <div class="privs">
            <a v-for="p in privItems" :key="p.label" class="priv dl" href="#" @click.prevent="go(p.to)">
              {{ p.label }} <b>{{ p.n }}</b>
            </a>
          </div>
        </div>
        <div v-else class="trend-card">
          <div v-if="latestSnap" class="trend-now">
            <div class="tn-num">{{ latestSnap.privileged }}</div>
            <div class="tn-lbl">目前特權帳號
              <span v-if="trendDelta !== null" class="tn-delta" :class="trendDelta > 0 ? 'up' : trendDelta < 0 ? 'down' : ''">
                {{ trendDelta > 0 ? '▲ +' + trendDelta : trendDelta < 0 ? '▼ ' + trendDelta : '＝ 持平' }}<span v-if="trendDelta !== 0"> 自首筆</span>
              </span>
            </div>
            <div class="tn-break">sudo {{ latestSnap.sudoers }}・UID0 {{ latestSnap.uid0 }}・免密碼 {{ latestSnap.nopasswd }}・管理帳號 {{ latestSnap.mgmt }}（{{ latestSnap.taken_at }} 快照）</div>
            <div class="privs">
              <a v-for="p in privItems" :key="p.label" class="priv dl" href="#" @click.prevent="go(p.to)">
                {{ p.label }} <b>{{ p.n }}</b>
              </a>
            </div>
          </div>
          <div class="trend-chart">
            <svg v-if="trendPath" viewBox="0 0 100 30" preserveAspectRatio="none" class="spark">
              <path :d="trendPath" fill="none" stroke="var(--brand)" stroke-width="1.2" vector-effect="non-scaling-stroke" />
            </svg>
            <p v-else class="muted sm">
              {{ trend.length === 1 ? '已有第一筆，再採集一輪就畫得出趨勢線。' : '還沒有快照——收一輪帳號後開始累積。' }}
            </p>
          </div>
        </div>
      </section>

      <h3 class="dash-h">各項合規檢查中招數<span v-if="osTab !== 'all'" class="scope">{{ tabLabel }}</span>（點框看是哪些帳號）</h3>
      <div class="tiles checks" data-test="check-cards">
        <div v-for="c in checks" :key="c.key" class="tile check clickable" :data-test="`check-${c.key}`"
             :class="[c.tone, { zero: c.n === 0, hot: (c.n ?? 0) > 0, unk: c.n === null }]"
             @click="c.n === null ? go(c.unknownTo) : (c.n && go(c.to))">
          <div class="t-num">{{ c.n === null ? '查不到' : c.n }}</div>
          <div class="t-lbl">{{ c.label }}<span v-if="c.scope" class="hint">{{ c.scope }}</span></div>
          <a v-if="c.n !== null && c.unknown > 0" class="unk-n" href="#" title="這些帳號這一欄查不到（權限不足／沒收到），不算進上面的數字"
             @click.prevent.stop="go(c.unknownTo)">查不到 {{ c.unknown }}</a>
          <span v-if="(c.n ?? 0) > 0 || c.n === null" class="drill">看帳號 →</span>
        </div>
      </div>

      <p v-if="summary?.run" class="when">
        最後盤點 · {{ summary.run.started_at }}（{{ summary.run.host_count }} 台，全部 OS）
      </p>
      </template>
    </template>
  </div>
</template>

<style scoped>
.err-cell { color: var(--bad); }
.err-cell .sm { font-size: 11.5px; color: var(--ink-soft); margin-top: 3px; }
.hostsec { margin-bottom: 18px; }
.hhd { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 600; margin-bottom: 8px; }
.hhd .spacer { flex: 1; }
.hhd .tg { background: none; border: none; padding: 0; cursor: pointer; font-size: 15px;
  font-weight: 600; color: var(--ink); }
.hkw { padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
       background: var(--card); color: var(--ink); font-size: 13px; min-width: 200px; font-weight: 400; }
.sechd { font-size: 13px; font-weight: 600; color: var(--ink-soft); margin: 14px 0 6px;
         display: flex; align-items: center; gap: 6px; }
.ell { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.badn { color: var(--bad); font-weight: 700; }
.sm { font-size: 11.5px; }
.trendsec { margin: 6px 0 14px; }
.trend-card { display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
  border: 1px solid var(--border); border-radius: var(--radius, 14px); background: var(--card); padding: 14px 18px; }
.trend-now { min-width: 160px; }
.tn-num { font-size: 32px; font-weight: 700; color: var(--brand-dark); line-height: 1; font-family: var(--disp, monospace); }
.tn-lbl { font-size: 12px; color: var(--ink-soft); margin-top: 4px; }
.tn-delta { margin-left: 6px; font-weight: 600; }
.tn-delta.up { color: var(--bad); }
.tn-delta.down { color: var(--good, var(--brand)); }
.tn-break { font-size: 11px; color: var(--muted); margin-top: 3px; }
.trend-chart { flex: 1; min-width: 200px; }
.spark { width: 100%; height: 60px; display: block; }
.mono { font-family: ui-monospace, monospace; }
.lead { color: var(--muted); margin: 0 0 18px; line-height: 1.7; }
.lead b { color: var(--ink, var(--ink)); }
.dl { color: var(--brand-dark); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.dl.on { font-weight: 700; text-decoration: underline; }

/* 已納管交叉引用（2026-10-18）＋篩選中提示條，跟 /adopt 的 filterbar 同一個長相，
   但這裡是獨立元件（scoped style 不會跨元件套用），所以另外定義一份同款式。 */
.onb-x { font-size: 12.5px; color: var(--ink-soft); font-weight: 400; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11.5px;
        font-weight: 600; border: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.pill.ok { color: var(--good, var(--brand-dark)); border-color: var(--good, var(--brand)); background: var(--mint, var(--sub)); }
.pill.off { color: var(--ink-soft); }
.filterbar { display: flex; align-items: center; gap: 8px; font-size: 12.5px; margin-bottom: 8px;
             padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--sub); }
.fb-l { color: var(--muted); }
.fb-v { font-weight: 700; color: var(--ink); }
.fb-n { color: var(--ink-soft); }
.fb-x { margin-left: auto; border: none; background: none; color: var(--brand-dark); cursor: pointer; font-size: 12.5px; }
.fb-x:hover { text-decoration: underline; }

/* 卡片縮小（2026-09-16 使用者：「卡片太大，小一點」）——
   它們是結果、不是主角，主機清單才是可以動手的東西。 */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: 8px; margin-bottom: 8px; }
.tiles.checks { grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); }
.tile { position: relative; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 9px 11px; }
.tile.clickable { cursor: pointer; transition: transform .12s, border-color .12s, box-shadow .12s; }
.tile.clickable:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(0,0,0,.28); border-color: rgba(0,145,66,.4); }
.tile.bad.hot { border-color: rgba(224,108,108,.5); }
.tile.warn.hot { border-color: rgba(230,170,60,.45); }
.t-num { font-size: 20px; font-weight: 700; color: var(--brand-dark); line-height: 1.1; font-variant-numeric: tabular-nums; }
.tile.bad.hot .t-num { color: var(--bad); }
.tile.warn.hot .t-num { color: var(--warn-text); }
.t-num .of { font-size: 13px; color: var(--muted); font-weight: 500; margin-left: 2px; }
.t-lbl { font-size: 11.5px; color: var(--muted); margin-top: 3px; line-height: 1.4; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .7; }
.tile.check.zero { opacity: .5; cursor: default; }
.tile.check.zero:hover { transform: none; box-shadow: none; border-color: var(--border); }
.tile.check.zero .t-num { color: var(--muted); }
.drill { position: absolute; right: 12px; bottom: 10px; font-size: 10px; color: var(--brand-dark);
  opacity: 0; transition: opacity .12s; }
.tile.clickable:hover .drill { opacity: .9; }
.dash-h { font-size: 14px; color: var(--muted); font-weight: 600; margin: 22px 0 10px; }
.when { font-size: 12px; color: var(--muted); margin-top: 16px; }

/* OS 分頁（2026-10-21）：跟合規表頁上方 .tabs/.tab 同一個長相 */
.ostabs { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin: 0 0 14px; }
.ostab { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft);
  padding: 6px 14px; border-radius: 999px; cursor: pointer; font-size: 13px; }
.ostab:hover { border-color: var(--border-strong); }
.ostab.active { border-color: var(--brand); background: var(--mint); color: var(--ink); font-weight: 600; }
.ostab.unk:not(.active) { color: var(--warn-text); }
.ostab b { font-family: var(--disp); }
.scope { margin-left: 6px; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600;
  background: var(--mint); color: var(--brand-dark); }
.err-line { color: var(--bad); font-size: 12.5px; margin: 0 0 6px; }
.privs { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 6px; font-size: 12px; }
.priv b { font-family: var(--disp); }
.tile.check.unk .t-num { color: var(--ink-soft); font-size: 15px; }
.unk-n { display: block; font-size: 10.5px; color: var(--ink-soft); margin-top: 2px; text-decoration: none; }
.unk-n:hover { text-decoration: underline; }
</style>
