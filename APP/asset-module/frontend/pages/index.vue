<script setup lang="ts">
// 儀表板：戰情室 command-center hero（HTML_Mock_戰情室風_v2 為視覺基準）。
// 全部餵真資料——hero 徑向儀表/venn 來自 /api/dashboard/stats，即時清單來自 /api/issues。
// D31精神：不放系統做不到的假資料（歷史趨勢時間序列後端還沒有，故不畫）。
interface DashboardStats {
  environment: string
  ica_count: number
  scanned_count: number
  overlap_count: number
  ica_only_count: number
  scan_only_count: number
  total_ica_count: number
  total_overlap_count: number
  last_scan_time: string | null
  last_scan_ok: boolean
  failed_segments: string[]
  issue_counts: Record<string, number>
}
interface IssueRow {
  id: number
  detected_at: string
  hostname: string | null
  ip: string | null
  issue_type: string
}

const { apiFetch } = useApi()

const ENV_OPTIONS = [
  { value: '正式', label: '正式' },
  { value: '正式+測試', label: '正式＋測試' },
  { value: '全部', label: '全部（含備援）' },
]

const environment = ref('正式')
const stats = ref<DashboardStats | null>(null)
const issues = ref<IssueRow[]>([])
const loading = ref(false)
const errorMessage = ref('')
const { showToast } = useToast()
const scanning = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

async function loadStats() {
  loading.value = true
  errorMessage.value = ''
  try {
    stats.value = await apiFetch<DashboardStats>('/api/dashboard/stats', {
      params: { environment: environment.value },
    })
  } catch {
    errorMessage.value = '儀表板資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
async function loadIssues() {
  try {
    issues.value = await apiFetch<IssueRow[]>('/api/issues', { params: { is_read: false } })
  } catch {
    issues.value = []
  }
}
async function reloadAll() {
  await Promise.all([loadStats(), loadIssues()])
}

await reloadAll()
watch(environment, loadStats)

// 重新掃描：四態（Idle→Pending→Success/Error），背景跑、輪詢狀態、完成刷新儀表板。
function pollScan(startTs: number) {
  const TIMEOUT = 120000
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    if (Date.now() - startTs > TIMEOUT) {
      if (pollTimer) clearInterval(pollTimer)
      scanning.value = false
      showToast('掃描逾時，請稍後再查看結果', 'error')
      return
    }
    let s: any
    try {
      s = await apiFetch<any>('/api/scan/status')
    } catch {
      return
    }
    if (s.running) return
    if (pollTimer) clearInterval(pollTimer)
    scanning.value = false
    if (s.status === 'ok') {
      showToast(`已重新掃描，找到 ${s.found_count} 台活著的主機`, 'success')
      await reloadAll()
    } else if (s.status === 'failed') {
      showToast(`掃描失敗：${s.error ?? '未知原因，請稍後重試'}`, 'error')
    }
  }, 1500)
}

async function triggerRescan() {
  if (scanning.value) return
  scanning.value = true
  try {
    await apiFetch('/api/scan/run', { method: 'POST' })
  } catch (err: any) {
    scanning.value = false
    const detail = err?.data?.detail
    showToast(detail ?? '啟動掃描失敗，請稍後重試', detail ? 'warn' : 'error')
    return
  }
  pollScan(Date.now())
}

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

const _init = await apiFetch<any>('/api/scan/status').catch(() => null)
if (_init?.running) {
  scanning.value = true
  pollScan(Date.now())
}

const totalIssueCount = computed(() =>
  stats.value ? Object.values(stats.value.issue_counts).reduce((s, n) => s + n, 0) : 0,
)
// 一致率＝相符 ÷（登記的 ∪ 網路上掃到的）。徑向儀表 r=86 → 圓周 540。
//
// ⚠️ 分母不能只用「登記數」。舊版是 overlap / ica_count，只回答「我登記的東西還在不在」，
// 完全不問「網路上有多少東西我沒登記」——結果是**登記越少分數越高，一台都不登記就是滿分 100%**。
// 對資產盤點系統語意剛好相反，而且這是頭條數字。實測：網路 6 台、只登記 2 台、4 台沒納管，
// 舊公式顯示 100%。改成聯集後同一份資料是 33%，才對得起「盤點完整度」這個意思。
//
// 用 total_* 是刻意的：那些「掃到卻沒登記」的機器不屬於任何環境，跟環境篩選過的登記數混用
// 會讓分數隨下拉選單跳動，但實際狀況沒變。詳見 api.py dashboard_stats 的註解。
const consistency = computed(() => {
  const s = stats.value
  if (!s) return 0
  const denom = s.total_ica_count + s.scan_only_count
  if (!denom) return 0
  return Math.min(100, (s.total_overlap_count / denom) * 100)
})
const dashOffset = computed(() => 540 * (1 - consistency.value / 100))
// ⚠️ hero 兩框一律用**全站**數字，不吃環境下拉——跟一致率同一個理由：
// 頭條數字被環境偷偷過濾，但標籤沒說，人會直接誤讀。實測踩過：掃描結果頁明明 8 台已登記，
// 儀表板「目前資產」卻寫 2（因為預設只看「正式」，另外 6 台登記在「測試」）。
// 環境篩選仍然作用在下方的明細與下鑽，那裡是分析用途、有標明範圍。
//
// 資產總數＝有效資產（排除停用/報廢/閒置的退役資產）：主盤點該回答「我現在管多少台」，
// 混進退役會製造假重複、也讓平台/OS 統計失真（使用者 2026-08-10 定案）。
// composition 拿得到就用它的 active total；拿不到（舊後端／composition 掛掉）才退回舊欄位。
const totalAssetCount = computed(() => comp.value?.total ?? stats.value?.total_ica_count ?? 0)
const retiredCount = computed(() => comp.value?.retired_count ?? 0)

// 納管四態（互斥且窮盡：每台機器剛好落在一格，加總＝所有知道的機器）。
// 比「資產數／異常數」更有用的原因：每一格都直接對應一個明確動作。
interface MStateItem {
  asset_serial: string | null; hostname: string | null; ip: string | null
  state: string; collect_checked_at: string | null; collect_error: string | null
}
interface ManageState {
  counts: Record<string, number>
  total_known: number
  next_action: Record<string, string>
  scan_time: string | null
  // 後端本來就把逐台明細一起回來了，只是畫面一直沒用——所以「已納管有幾台」
  // 看得到數字，「是哪幾台」卻沒有任何畫面（使用者 2026-08-16 問）。
  items?: MStateItem[]
}
const mstate = ref<ManageState | null>(null)
try {
  mstate.value = await apiFetch<ManageState>('/api/manage-state')
} catch { /* 拿不到就退回不顯示這一區，不擋整頁 */ }

const STATE_ORDER = ['未登記', '未納管', '已納管', '失聯'] as const
const STATE_SUB: Record<string, string> = {
  未登記: '掃到了但沒登記',
  未納管: '登記了但連不進去',
  已納管: '收得到主機名/OS/序號',
  失聯: '登記在案但這次掃不到',
}
const STATE_LINK: Record<string, string> = {
  未登記: '/adopt',
  未納管: '/assets',
  已納管: '/assets',
  失聯: '/issues',
}
function stateCount(k: string) { return mstate.value?.counts?.[k] ?? 0 }

// 點某一態就在原地展開「是哪幾台」。數字點下去要看得到名單，不然那個數字
// 只能拿來焦慮，不能拿來做事（天條二：資料點可追蹤）。
const openState = ref<string | null>(null)
const stateItems = computed<MStateItem[]>(() => {
  if (!openState.value) return []
  return (mstate.value?.items ?? []).filter((i) => i.state === openState.value)
})
function toggleState(k: string) { openState.value = openState.value === k ? null : k }

// 組成統計：儀表板該回答「我的機器長什麼樣子」（幾台 Windows／虛實／環境），
// 那是統計；「兩邊相符／登記卻掃不到」是對帳細節，屬於小功能，不該佔戰情室頭條。
interface Composition {
  total: number
  // 舊後端沒有這兩欄（分開退役資產之前），設成選用，畫面就退回不顯示退役徽章
  total_all?: number
  retired_count?: number
  by_platform: Record<string, number>
  // 平台下鑽：點「Windows」展開看 2019/2022 各幾台。舊後端沒有這欄，選用即可，
  // 沒有就不顯示展開箭頭。
  by_platform_os?: Record<string, Record<string, number>>
  by_environment: Record<string, number>
  // 後端較舊時不會有這個欄位，設成選用，畫面就只是不顯示機房那一區
  by_location?: Record<string, number>
  // 機房 × 環境別交叉（使用者 2026-08-13 要求：「內湖有幾台正式/測試」這種問法）。
  by_location_env?: Record<string, Record<string, number>>
  by_virtualization: Record<string, number>
  by_status: Record<string, number>
  os_from_facts: number
  os_guessed: number
  // 資料治理進度：舊後端沒有這欄，設成選用，畫面就只是不顯示這一區
  data_quality?: DataQuality
}
// 「有幾台 Windows」是機器組成，「還有幾筆沒對帳」是工作進度——兩者都要有。
// 使用者 2026-07-30：合併了 548 筆，儀表板上完全看不出來。
interface DataQuality {
  total: number
  verified_by_vcenter: number
  verified_pct: number
  os_unknown: number
  pending_review: number
  merged_done: number
  duplicate_groups: number
  duplicate_extra_rows: number
}
const comp = ref<Composition | null>(null)
try {
  comp.value = await apiFetch<Composition>('/api/dashboard/composition')
} catch { /* 拿不到就不顯示這一區，不擋整頁 */ }

const PLATFORM_COLOR: Record<string, string> = {
  Windows: '#2563eb', 'AIX/Unix': '#7c3aed', 網路設備: '#d97706',
  // Linux 家族拆開後各自一色，同屬 Linux 系但一眼分得出是哪個發行版大宗
  RHEL: '#dc2626', CentOS: '#7c3aed', Debian: '#db2777', 'Oracle Linux': '#c2410c',
  'Linux(其他)': '#009142',
  // 使用者 2026-08-13 要求新增的平台種類。
  'VMware ESXi': '#0891b2', 'IBM i': '#7c3aed',
  '管理韌體(BMC)': '#64748b', '儲存設備': '#a8730f',
}
// 「未知／未填／N/A」這類「資料缺漏」的分類：一律排到最後、用統一灰色，
// 跟真實分類的彩色區隔開——一眼看出哪些是實際分佈、哪些只是資料還沒補。
const UNKNOWN_KEYS = new Set(['未知', '未填', 'N/A', 'n/a', '(空)', '', '未分類', 'unknown', 'Unknown'])
const UNKNOWN_COLOR = '#54655e'
const isUnknownKey = (k: string) => UNKNOWN_KEYS.has(String(k).trim())

// 平台下鑽：點展開箭頭看這個平台底下的版本細分（Windows → 2019/2022…）。
// 「未知版本／(推測)」這種沒有穩定原始字串可比對的，不給連結（後端 canonical_os
// 篩選只認得正規化過的真 OS，硬點下去會 0 筆，不如老實不當連結）。
function platformOsBreakdown(platform: string) {
  const m = comp.value?.by_platform_os?.[platform] ?? {}
  return Object.entries(m)
    .sort((a, b) => b[1] - a[1])
    .map(([os, n]) => ({ os, n, clickable: !os.endsWith('（推測）') && os !== '未知版本' }))
}

// 原本只留四大類（使用者 2026-08-12 要求）：Windows／Linux／網路設備／其他。
// Linux 各發行版（RHEL/CentOS/Debian/Oracle Linux/其他）原本各自是 by_platform 的獨立鍵，
// 這裡合併成一個「Linux」數字，展開後才看到各發行版細分。
// 使用者 2026-08-13 要求：VMware ESXi／IBM i 拉出來變成獨立卡片（原本併在「其他」
// 裡看不出量），AIX/Unix／管理韌體(BMC)／儲存設備／未知還是併進「其他」。
// 沒列在任何已知分組的鍵（未來新平台種類）自動併入「其他」，不會悄悄漏接畫面。
const PLATFORM_GROUPS: Record<string, string[]> = {
  Windows: ['Windows'],
  Linux: ['RHEL', 'CentOS', 'Debian', 'Oracle Linux', 'Linux(其他)'],
  網路設備: ['網路設備'],
  'VMware ESXi': ['VMware ESXi'],
  'IBM i': ['IBM i'],
  其他: ['AIX/Unix', '管理韌體(BMC)', '儲存設備', '未知'],
}
const GROUP_ORDER = ['Windows', 'Linux', '網路設備', 'VMware ESXi', 'IBM i', '其他']
const GROUP_COLOR: Record<string, string> = {
  Windows: '#2563eb', Linux: '#009142', 網路設備: '#d97706',
  'VMware ESXi': '#0891b2', 'IBM i': '#7c3aed', 其他: UNKNOWN_COLOR,
}
function groupedPlatforms(byPlatform: Record<string, number> | undefined) {
  const m = byPlatform ?? {}
  const known = new Set(Object.values(PLATFORM_GROUPS).flat())
  const leftover = Object.keys(m).filter((k) => !known.has(k))
  const membersOf: Record<string, string[]> = { ...PLATFORM_GROUPS, 其他: [...PLATFORM_GROUPS.其他, ...leftover] }
  return GROUP_ORDER.map((g) => {
    const members = membersOf[g].filter((k) => m[k] !== undefined)
    return { key: g, n: members.reduce((s, k) => s + (m[k] ?? 0), 0), color: GROUP_COLOR[g], members }
  })
}
function groupLink(g: { key: string; members: string[] }) {
  return { path: '/assets', query: { platform: g.members.join(',') } }
}
// 展開狀態分兩層：第一層是哪個大類展開了細分列表，第二層（只有 Linux/其他這種
// 多平台大類才需要）是列表裡哪個平台自己的版本明細又展開了。
const expandedGroup = ref<string | null>(null)
function toggleGroup(k: string) {
  expandedGroup.value = expandedGroup.value === k ? null : k
}
const expandedSubPlatform = ref<string | null>(null)
function toggleSubPlatform(k: string) {
  expandedSubPlatform.value = expandedSubPlatform.value === k ? null : k
}
// 機房 × 環境別交叉表（使用者 2026-08-20 要求，取代原本一排膠囊＋「環境別 ▾」展開）。
//
// 為什麼改成表格：膠囊是一維的，回答不了「板橋的正式機幾台」，要一個一個展開看。
// 更要緊的是膠囊**沒有合計**——看的人無從確認數字加起來等於資產總數，也就無從
// 判斷有沒有東西被漏掉。表格右下角那一格就是在回答這件事。
//
// 欄位不是只有板橋/內湖/敦南：那三個只涵蓋約 3400 台，另外還有分公司、其他據點、
// 以及機房根本沒填的 800 多台。少了那幾欄，畫面上會有一千台憑空消失。
const LOC_ORDER = ['板橋', '內湖', '敦南', '分公司', '未填']
const ENV_ORDER = ['正式', '備援', '測試', '其他', '未填']

// 照固定順序排，但**不在順序表裡的一律接在後面顯示**，不能丟掉：
// 分組規則放在 location_groups.json / environment_groups.json，隨時可能被改，
// 寫死順序又只顯示白名單，改了設定就會有機器從畫面上消失而且沒人發現。
function ordered(present: string[], order: string[]): string[] {
  return [...order.filter(k => present.includes(k)),
          ...present.filter(k => !order.includes(k))]
}

const matrixLocs = computed<string[]>(() =>
  ordered(Object.keys(comp.value?.by_location ?? {}), LOC_ORDER))

const matrixEnvs = computed<string[]>(() => {
  const seen = new Set<string>()
  for (const m of Object.values(comp.value?.by_location_env ?? {})) {
    for (const k of Object.keys(m as Record<string, number>)) seen.add(k)
  }
  return ordered([...seen], ENV_ORDER)
})

function cellCount(loc: string, env: string): number {
  return comp.value?.by_location_env?.[loc]?.[env] ?? 0
}
function rowTotal(env: string): number {
  return matrixLocs.value.reduce((s, l) => s + cellCount(l, env), 0)
}
function colTotal(loc: string): number {
  return matrixEnvs.value.reduce((s, e) => s + cellCount(loc, e), 0)
}
const matrixTotal = computed(() => matrixLocs.value.reduce((s, l) => s + colTotal(l), 0))

// 點格子 → 資產查詢頁。一律走 *_group 參數而不是 filter_field 精確比對，
// 否則「測試」那格（含 UAT/DEV/OA）點進去會少 10 台，數字對不起來（見 api.py 說明）。
function cellLink(loc: string, env: string) {
  return { path: '/assets', query: { location_group: loc, environment_group: env } }
}

// 機房或環境別任一沒填的台數（聯集，不是相加——同一台可能兩欄都沒填，
// 相加會灌水，講出來的數字要禁得起使用者自己去點開核對）。
const cellsUnfilled = computed(() => {
  const locs = matrixLocs.value, envs = matrixEnvs.value
  let n = 0
  for (const l of locs) {
    for (const e of envs) {
      if (l === '未填' || e === '未填') n += cellCount(l, e)
    }
  }
  return n
})

function segments(m: Record<string, number> | undefined, colors?: Record<string, string>) {
  const e = Object.entries(m ?? {}).sort((a, b) => {
    const au = isUnknownKey(a[0]), bu = isUnknownKey(b[0])
    if (au !== bu) return au ? 1 : -1   // 未知/未填一律排最後
    return b[1] - a[1]                  // 其餘按數量大→小
  })
  const total = e.reduce((s, [, n]) => s + n, 0) || 1
  return e.map(([k, n]) => ({
    key: k, n, pct: (n / total) * 100,
    color: isUnknownKey(k) ? UNKNOWN_COLOR
      : (colors?.[k] ?? (k === '虛擬機' || k === '正式' ? '#009142' : '#2563eb')),
  }))
}
// 異常資產＝登記卻掃不到 ＋ 掃到卻沒登記。刻意不含「兩邊相符」——那些是正常的。
// 登記卻掃不到（全站）＝ 全站登記數 − 全站相符數。
const abnormalCount = computed(() => {
  const s = stats.value
  if (!s) return 0
  return (s.total_ica_count - s.total_overlap_count) + s.scan_only_count
})
const ic = (k: string) => stats.value?.issue_counts?.[k] ?? 0

// 每個磚塊/數字都要能點進去看是哪幾台——只看得到數量卻不知道是誰，等於還是要自己去查。
// 環境選擇（正式／正式＋測試／全部）一併帶進下鑽，後端 /api/assets 認得同一組 preset，
// 所以點進去的筆數會跟磚塊上的數字一致（有 test_dashboard_drilldown.py 守著）。
const drill = computed(() => {
  const env = environment.value
  return {
    allAssets: { path: '/assets', query: { environment: env } },
    overlap: { path: '/assets', query: { scan_status: 'overlap', environment: env } },
    icaOnly: { path: '/assets', query: { scan_status: 'ica_only', environment: env } },
    // 「掃到卻沒登記」＝納入管理的候選清單，本來就有專頁
    scanOnly: { path: '/adopt' },
    // 掃描側的事實（這次網路上活著的機器），與 CIA 登記側不同，另有專頁
    scanned: { path: '/scan-results' },
    issue: (t: string) => ({ path: '/issues', query: { type: t } }),
  }
})

function dotColor(t: string): string {
  return t === '異常消失' ? '#ff6b6b' : t === '漏登記' ? '#7fb3ea' : '#ffb867'
}
function typeColor(t: string): string {
  return t === '異常消失' ? '#dc2626' : t === '漏登記' ? '#2563eb' : '#d97706'
}
function fmtTime(s: string): string {
  return s ? s.replace(/^\d{4}-/, '').slice(0, 14) : ''
}
</script>

<template>
  <div class="dash">
    <div class="head">
      <div class="title">
        <div class="ey">ASSET · WAR ROOM</div>
        <h1>資產盤點戰情室</h1>
      </div>
      <div class="right">
        <label class="envwrap">環境
          <select v-model="environment">
            <option v-for="opt in ENV_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <button class="scanbtn" :disabled="scanning" @click="triggerRescan">
          <span v-if="scanning" class="spin" />{{ scanning ? '掃描中…' : '↻ 重新掃描' }}
        </button>
      </div>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="stats">
      <div class="lastscan">
        最後掃描 · {{ stats.last_scan_time ?? '尚未執行過' }}
        <span class="hbadge" :class="{ warn: !stats.last_scan_ok }">
          {{ stats.last_scan_ok ? '所有網段完成' : `${stats.failed_segments.length} 個網段掃描失敗` }}
        </span>
      </div>

      <div class="stage">
        <!-- HERO：招牌是「我管多少台」——公司 3000+ 台，這才是重點 -->
        <div class="glass hero glow-teal">
          <div class="herotext">
            <!-- 總數當招牌 -->
            <div class="headline">
              <NuxtLink to="/assets" class="bignum" title="看全部有效資產（不含退役）">
                <div class="hn">{{ totalAssetCount.toLocaleString() }}</div>
                <div class="hl">資產總數 · 有效在用</div>
              </NuxtLink>
              <NuxtLink v-if="retiredCount > 0"
                        :to="{ path: '/assets', query: { filter_field: 'asset_status', filter_value: '停用,報廢,閒置' } }"
                        class="retired-badge" title="停用/報廢/閒置：資產生命週期的歷史，不算進有效盤點">
                另有 {{ retiredCount.toLocaleString() }} 台退役 →
              </NuxtLink>
              <div class="netside">
                <div class="ns-row">網路上發現 <b>{{ mstate ? mstate.total_known : totalAssetCount }}</b> 台</div>
                <!-- 未登記只講這一次（原本四格＋警示框＋漏登記共講三次，重複）-->
                <NuxtLink v-if="mstate && stateCount('未登記') > 0" to="/adopt" class="ns-alert">
                  ⚠ {{ stateCount('未登記') }} 台在網路上但沒登記 · 納入管理 →
                </NuxtLink>
                <div v-else class="ns-ok">✓ 網路上沒有未登記的主機</div>
              </div>
            </div>

            <!-- 納管進度：四態縮成一條，不再是四個大方塊 -->
            <div v-if="mstate" class="progress">
              <div class="pgk">納管進度</div>
              <div class="pgbar">
                <NuxtLink v-for="k in STATE_ORDER.filter((x) => x !== '未登記')" :key="k"
                          :to="STATE_LINK[k]" class="pgseg" :class="`st-${k}`"
                          :style="{ flexGrow: stateCount(k) || 0.001 }"
                          :title="`${k} ${stateCount(k)} 台：${mstate.next_action?.[k]}`" />
              </div>
              <div class="pgleg">
                <button v-for="k in STATE_ORDER.filter((x) => x !== '未登記')" :key="k"
                        type="button" class="pgi" :class="[`st-${k}`, { on: openState === k }]"
                        :title="mstate.next_action?.[k]" @click="toggleState(k)">
                  {{ k }}<b>{{ stateCount(k) }}</b>
                </button>
              </div>

              <!-- 是哪幾台：數字點下去要列得出名單 -->
              <div v-if="openState" class="stlist">
                <div class="sl-head">
                  <b>{{ openState }}</b> · {{ stateItems.length }} 台
                  <span class="sl-act">{{ mstate.next_action?.[openState] }}</span>
                  <NuxtLink :to="STATE_LINK[openState]" class="sl-more">到清單頁 →</NuxtLink>
                </div>
                <div v-if="!stateItems.length" class="sl-empty">沒有這一態的機器。</div>
                <table v-else class="sltbl">
                  <thead><tr><th>主機名稱</th><th>IP</th><th>上次試連</th><th>連不上的原因</th></tr></thead>
                  <tbody>
                    <tr v-for="i in stateItems.slice(0, 50)" :key="(i.asset_serial || '') + i.ip">
                      <td>
                        <NuxtLink v-if="i.asset_serial" :to="`/assets/${i.asset_serial}`" class="dl">
                          {{ i.hostname || i.asset_serial }}
                        </NuxtLink>
                        <template v-else>{{ i.hostname || '(未登記)' }}</template>
                      </td>
                      <td class="mono">{{ i.ip || '—' }}</td>
                      <td class="small">{{ i.collect_checked_at || '—' }}</td>
                      <td class="small err">{{ i.collect_error || '—' }}</td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="stateItems.length > 50" class="sl-empty">
                  只列前 50 台，其餘請到清單頁。
                </div>
              </div>
            </div>

            <!-- 組成統計＝「素材」：我的機器長什麼樣子（每段可點）-->
            <template v-if="comp">
              <div class="compsec">
                <div class="ck">作業系統平台</div>
                <div class="bar">
                  <span v-for="g in segments(comp.by_platform, PLATFORM_COLOR)" :key="g.key"
                        class="seg" :style="{ width: g.pct + '%', background: g.color }"
                        :title="`${g.key} ${g.n} 台`" />
                </div>
                <div class="ptiles">
                  <div v-for="g in groupedPlatforms(comp.by_platform)" :key="g.key"
                       class="ptile" :style="{ borderColor: g.color + '80' }">
                    <NuxtLink :to="groupLink(g)" class="pt-link" :title="`看 ${g.key} 的資產`">
                      <div class="pt-num mono" :style="{ color: g.color }">{{ g.n }}</div>
                      <div class="pt-lbl"><span class="sw" :style="{ background: g.color }" />{{ g.key }}</div>
                    </NuxtLink>
                    <button v-if="g.members.length" type="button" class="ci-expand"
                            :class="{ open: expandedGroup === g.key }"
                            :title="`展開/收合 ${g.key} 細分`" @click="toggleGroup(g.key)">
                      版本明細 <span class="arw">▸</span>
                    </button>
                    <div v-if="expandedGroup === g.key" class="os-breakdown">
                      <!-- 單一平台大類（Windows／網路設備）：直接展開版本明細 -->
                      <template v-if="g.members.length === 1">
                        <template v-for="o in platformOsBreakdown(g.members[0])" :key="o.os">
                          <NuxtLink v-if="o.clickable"
                                    :to="{ path: '/assets', query: { platform: g.members[0], canonical_os: o.os } }"
                                    class="ci sub" :title="`看 ${o.os} 的資產`">
                            {{ o.os }}<b>{{ o.n }}</b>
                          </NuxtLink>
                          <span v-else class="ci sub dim" :title="'沒有可靠的原始字串可篩選'">
                            {{ o.os }}<b>{{ o.n }}</b>
                          </span>
                        </template>
                      </template>
                      <!-- 多平台大類（Linux／其他）：先列各平台細分，各自再展開版本明細 -->
                      <template v-else>
                        <template v-for="mkey in g.members" :key="mkey">
                          <span class="ci-wrap">
                            <NuxtLink :to="{ path: '/assets', query: { platform: mkey } }"
                                      class="ci sub" :title="`看 ${mkey} 的資產`">
                              {{ mkey }}<b>{{ comp.by_platform?.[mkey] ?? 0 }}</b>
                            </NuxtLink>
                            <button v-if="comp.by_platform_os?.[mkey]" type="button" class="ci-expand sm"
                                    :class="{ open: expandedSubPlatform === mkey }"
                                    :title="`展開/收合 ${mkey} 版本明細`" @click="toggleSubPlatform(mkey)">
                              版本 <span class="arw">▸</span>
                            </button>
                          </span>
                          <div v-if="expandedSubPlatform === mkey" class="os-breakdown sub2">
                            <template v-for="o in platformOsBreakdown(mkey)" :key="o.os">
                              <NuxtLink v-if="o.clickable"
                                        :to="{ path: '/assets', query: { platform: mkey, canonical_os: o.os } }"
                                        class="ci sub" :title="`看 ${o.os} 的資產`">
                                {{ o.os }}<b>{{ o.n }}</b>
                              </NuxtLink>
                              <span v-else class="ci sub dim" :title="'沒有可靠的原始字串可篩選'">
                                {{ o.os }}<b>{{ o.n }}</b>
                              </span>
                            </template>
                          </div>
                        </template>
                      </template>
                    </div>
                  </div>
                </div>
              </div>
              <div class="compsec">
                <div class="ck">虛擬 / 實體</div>
                <div class="cleg">
                  <NuxtLink v-for="g in segments(comp.by_virtualization)" :key="g.key"
                            :to="{ path: '/assets', query: { virtual: g.key === '虛擬機' ? 'yes' : 'no' } }"
                            class="ci" :title="`看${g.key}是哪幾台`">
                    <span class="sw" :style="{ background: g.color }" />{{ g.key }}<b>{{ g.n }}</b>
                  </NuxtLink>
                </div>
              </div>
              <div class="compsec">
                <div class="ck">環境別</div>
                <div class="cleg">
                  <!-- 使用者 2026-08-14 實際發現：「未填」是畫面顯示用的假標籤
                       （後端 bump(by_env, r["environment"] or "未填") 湊出來的），
                       資料庫裡實際存的是 NULL／空字串，不是字面「未填」兩個字——
                       直接把 g.key 送去篩會 0 筆，跟機房分佈那邊踩過同一個坑，
                       這裡也要把「未填」轉成空字串。 -->
                  <NuxtLink v-for="g in segments(comp.by_environment)" :key="g.key"
                            :to="{ path: '/assets', query: { filter_field: 'environment', filter_value: g.key === '未填' ? '' : g.key } }"
                            class="ci">
                    <span class="sw" :style="{ background: g.color }" />{{ g.key }}<b>{{ g.n }}</b>
                  </NuxtLink>
                </div>
              </div>
              <!-- 機房 × 環境別交叉表：一眼回答「板橋的正式機幾台」。
                   每一格都能點進去看是哪幾台；合計列/欄讓人確認總數沒有被漏掉。 -->
              <div v-if="matrixLocs.length && matrixEnvs.length" class="compsec">
                <div class="ck">機房 × 環境別</div>
                <div class="mxwrap">
                  <table class="mx">
                    <thead>
                      <tr>
                        <th class="mxcorner">環境＼機房</th>
                        <th v-for="l in matrixLocs" :key="l">{{ l }}</th>
                        <th class="mxsum">合計</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="e in matrixEnvs" :key="e">
                        <th class="mxrh">{{ e }}</th>
                        <td v-for="l in matrixLocs" :key="l">
                          <!-- 0 顯示為「·」而不是 0：一整片 0 會蓋掉真正有數字的格子。
                               而且 0 不連結——點進去必然空清單，那是白跑一趟。 -->
                          <NuxtLink v-if="cellCount(l, e)" :to="cellLink(l, e)" class="mxn"
                                    :title="`看 ${l}／${e} 的 ${cellCount(l, e)} 台`">
                            {{ cellCount(l, e).toLocaleString() }}
                          </NuxtLink>
                          <span v-else class="mxz">·</span>
                        </td>
                        <td class="mxsum">{{ rowTotal(e).toLocaleString() }}</td>
                      </tr>
                    </tbody>
                    <tfoot>
                      <tr>
                        <th class="mxrh">合計</th>
                        <td v-for="l in matrixLocs" :key="l" class="mxsum">
                          {{ colTotal(l).toLocaleString() }}
                        </td>
                        <td class="mxsum mxgrand">{{ matrixTotal.toLocaleString() }}</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
                <!-- 「未填」不是統計分類，是待辦。講明它會讓總數失真，並給一條路去補。 -->
                <p v-if="cellsUnfilled" class="mxhint">
                  其中 <b>{{ cellsUnfilled.toLocaleString() }}</b> 台的機房或環境別沒填，
                  這部分統計不會準——點「未填」那一列／那一欄可以看到是哪幾台，補齊後數字才對得起來。
                </p>
              </div>
              <div class="osnote">
                OS 來源：{{ comp.os_from_facts }} 台實際收集、{{ comp.os_guessed }} 台由掃描推測
              </div>

              <!-- 資料品質與待辦：上面幾區回答「我有什麼機器」，這一區回答「盤點做到哪了」。
                   每一項都能點進去看是哪幾筆——只知道數字卻不知道是誰，等於還是要自己查。 -->
              <div v-if="comp.data_quality" class="dqsec">
                <div class="ck">資料品質與待辦</div>
                <div class="dqgrid">
                  <NuxtLink class="dq ok" :to="{ path: '/assets', query: { virtual: 'yes' } }">
                    <span class="dqn">{{ comp.data_quality.verified_by_vcenter.toLocaleString() }}</span>
                    <span class="dql">已由 vCenter 校正</span>
                    <span class="dqx">佔 {{ comp.data_quality.verified_pct }}%，拿得到機器自報的事實</span>
                  </NuxtLink>

                  <div v-if="comp.data_quality.merged_done" class="dq done">
                    <span class="dqn">{{ comp.data_quality.merged_done.toLocaleString() }}</span>
                    <span class="dql">已完成合併</span>
                    <span class="dqx">確認過是同一台、已併入既有資產</span>
                  </div>

                  <NuxtLink v-if="comp.data_quality.pending_review" class="dq warn" to="/import">
                    <span class="dqn">{{ comp.data_quality.pending_review.toLocaleString() }}</span>
                    <span class="dql">待人工審核 →</span>
                    <span class="dqx">判不準是否同一台，系統不自動合併</span>
                  </NuxtLink>

                  <NuxtLink v-if="comp.data_quality.duplicate_groups" class="dq warn"
                            :to="{ path: '/assets', query: { show: 'duplicates' } }">
                    <span class="dqn">{{ comp.data_quality.duplicate_groups.toLocaleString() }}</span>
                    <span class="dql">重複登記（組）→</span>
                    <span class="dqx">清掉可少 {{ comp.data_quality.duplicate_extra_rows }} 筆</span>
                  </NuxtLink>

                  <NuxtLink v-if="comp.data_quality.os_unknown" class="dq warn"
                            :to="{ path: '/assets', query: { filter_field: 'os', filter_value: '' } }">
                    <span class="dqn">{{ comp.data_quality.os_unknown.toLocaleString() }}</span>
                    <span class="dql">OS 仍未知 →</span>
                    <span class="dqx">vCenter 涵蓋不到，要靠掃描或 SSH 收集</span>
                  </NuxtLink>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- 右側：異動指標（未登記已在 hero 講過，這裡不重複，只放「跟上次比的變化」）-->
        <div class="stack">
          <div class="mini">
            <NuxtLink :to="drill.issue('異常新增')" class="glass stat"><div class="n amber">{{ ic('異常新增') }}</div><div class="lab">異常新增（這次多出來的）</div></NuxtLink>
            <NuxtLink :to="drill.issue('異常消失')" class="glass stat"><div class="n red">{{ ic('異常消失') }}</div><div class="lab">異常消失（登記卻掃不到）</div></NuxtLink>
            <NuxtLink :to="drill.scanned" class="glass stat"><div class="n teal">{{ stats.scanned_count }}</div><div class="lab">本次掃到存活</div></NuxtLink>
            <NuxtLink to="/assets" class="glass stat"><div class="n blue">{{ comp ? comp.os_from_facts : '—' }}</div><div class="lab">已收到真實資料</div></NuxtLink>
          </div>
        </div>
      </div>

      <!-- 問題即時清單 -->
      <div class="glass feed">
        <h3>
          <span class="beat" />問題即時清單 · {{ totalIssueCount }} 筆待處理
          <NuxtLink to="/issues" class="h3link">全部問題 →</NuxtLink>
        </h3>
        <div v-if="issues.length === 0" class="empty">目前沒有待處理的問題 🎉</div>
        <NuxtLink
          v-for="row in issues.slice(0, 12)"
          :key="row.id"
          :to="drill.issue(row.issue_type)"
          class="frow"
        >
          <span class="dot" :style="{ background: dotColor(row.issue_type) }" />
          <span class="host">{{ row.hostname || '—' }}</span>
          <span class="ip">{{ row.ip || '—' }}</span>
          <span class="type" :style="{ color: typeColor(row.issue_type) }">{{ row.issue_type }}</span>
          <span class="time">{{ fmtTime(row.detected_at) }}</span>
        </NuxtLink>
        <!-- 原本連到 /assets——問題不是資產，連錯地方了；/issues 才是問題清單 -->
        <NuxtLink v-if="issues.length > 12" to="/issues" class="morelink">查看全部 {{ issues.length }} 筆 →</NuxtLink>
      </div>
    </template>
    <p v-else-if="loading" class="loading">載入中…</p>
  </div>
</template>

<style scoped>
.pgi { font-family: inherit; border: none; background: none; cursor: pointer; }
.pgi.on { outline: 1px solid var(--brand); outline-offset: 2px; }
.stlist { margin-top: 10px; border: 1px solid var(--border-strong); background: var(--card); padding: 10px 12px; }
.sl-head { font-size: 12px; display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; margin-bottom: 8px; }
.sl-act { color: var(--muted); font-size: 11px; }
.sl-more { margin-left: auto; font-size: 11px; }
.sl-empty { font-size: 11.5px; color: var(--muted); padding: 6px 0; }
.sltbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.sltbl th, .sltbl td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--border); }
.sltbl th { font-size: 11px; color: var(--muted); font-weight: 700; }
.sltbl .err { color: var(--warn); max-width: 320px; }

.dash { font-family: 'Microsoft JhengHei', sans-serif; }
.head { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 24px; flex-wrap: wrap; gap: 14px; }
.title .ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand); text-transform: uppercase; }
.title h1 { font-family: var(--disp); font-size: 26px; font-weight: 600; margin: 4px 0 0; color: var(--ink); letter-spacing: -.5px; }
.right { display: flex; align-items: center; gap: 14px; }
.envwrap { font-size: 12.5px; color: var(--muted); display: inline-flex; align-items: center; gap: 8px; }
.envwrap select { font-family: inherit; font-size: 12.5px; padding: 7px 10px; }
.scanbtn { font-family: inherit; font-size: 13px; font-weight: 700; padding: 9px 18px; border: none; border-radius: 10px;
  background: linear-gradient(135deg,#009142,#00703a); color: #04120e; cursor: pointer; box-shadow: 0 6px 20px rgba(0,145,66,.3);
  display: inline-flex; align-items: center; gap: 8px; }
.scanbtn:disabled { opacity: .7; cursor: not-allowed; }
.spin { width: 13px; height: 13px; border: 2px solid rgba(4,18,14,.4); border-top-color: #04120e; border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.lastscan { font-family: var(--disp); font-size: 12px; color: var(--muted); margin-bottom: 18px; display: flex; align-items: center; gap: 12px; }
.hbadge { font-size: 11px; padding: 3px 10px; border-radius: 20px; background: rgba(0,145,66,.14); color: var(--brand); font-family: 'Microsoft JhengHei'; }
.hbadge.warn { background: rgba(255,184,103,.16); color: #b45309; }
.error-text { color: var(--bad); font-size: 13px; margin-bottom: 14px; }
.loading { color: var(--muted); }

.glass { background: var(--card); border: 1px solid var(--border); border-radius: 20px; box-shadow: var(--shadow); }
.glow-teal { box-shadow: 0 0 20px rgba(0,145,66,.15); }
.stage { display: grid; grid-template-columns: 1.15fr 1fr; gap: 22px; align-items: stretch; }

.hero { padding: 26px 30px; display: block; }
.herotext { width: 100%; }
/* 招牌：資產總數（公司 3000+ 台，這才是重點）*/
.headline { display: flex; align-items: center; gap: 28px; flex-wrap: wrap;
  padding-bottom: 18px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }
.bignum { text-decoration: none; color: inherit; }
.bignum .hn { font-family: var(--disp); font-size: 64px; font-weight: 700; line-height: 1;
  color: var(--ink); letter-spacing: -3px; }
.bignum:hover .hn { color: var(--brand); }
.bignum .hl { font-size: 12px; color: var(--ink-soft); letter-spacing: 1px; margin-top: 6px; }
.retired-badge { display: inline-block; font-size: 12.5px; font-weight: 700; color: var(--ink-soft);
  background: rgba(100,116,139,.1); border: 1px solid rgba(100,116,139,.3);
  padding: 5px 12px; border-radius: 999px; text-decoration: none; }
.retired-badge:hover { background: rgba(100,116,139,.18); color: var(--ink); }
.netside { display: flex; flex-direction: column; gap: 8px; }
.netside .ns-row { font-size: 13px; color: var(--ink-soft); }
.netside .ns-row b { font-family: var(--disp); font-size: 18px; color: var(--ink); }
.netside .ns-alert { display: inline-block; font-size: 12.5px; font-weight: 700; color: #b45309;
  background: rgba(255,184,103,.12); border: 1px solid rgba(255,184,103,.3);
  padding: 5px 12px; border-radius: 999px; text-decoration: none; }
.netside .ns-alert:hover { background: rgba(255,184,103,.2); }
.netside .ns-ok { font-size: 12.5px; color: var(--brand); }
/* 納管進度：一條，不是四個大方塊 */
.progress { margin-bottom: 16px; }
.progress .pgk { font-size: 10.5px; color: var(--ink-soft); letter-spacing: 1px; margin-bottom: 5px; }
.pgbar { display: flex; height: 10px; border-radius: 999px; overflow: hidden;
  background: var(--border); margin-bottom: 6px; }
.pgseg { display: block; }
.pgseg.st-已納管 { background: #22a85a; }
.pgseg.st-未納管 { background: #7fb3ea; }
.pgseg.st-失聯 { background: #ff8f8f; }
.pgleg { display: flex; flex-wrap: wrap; gap: 4px 14px; }
.pgi { display: flex; align-items: center; gap: 4px; font-size: 12px; text-decoration: none; color: var(--ink-soft); }
.pgi:hover { color: var(--ink); }
.pgi b { font-family: var(--disp); font-size: 14px; margin-left: 2px; }
.pgi.st-已納管 b { color: var(--brand); }
.pgi.st-未納管 b { color: #2563eb; }
.pgi.st-失聯 b { color: #dc2626; }
.hero_legacy { padding: 28px 30px; display: flex; align-items: center; gap: 30px; }
.gauge { position: relative; width: 200px; height: 200px; flex-shrink: 0; }
.gauge .pct { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.gauge .pct b { font-family: var(--disp); font-size: 44px; font-weight: 700; color: var(--ink); letter-spacing: -2px; line-height: 1; }
.gauge .pct span { font-size: 11px; color: var(--ink-soft); letter-spacing: 2px; margin-top: 6px; }
.herotext .k { font-size: 12px; color: var(--ink-soft); letter-spacing: 1px; }
/* 兩個框：目前資產 / 異常資產（取代原本的「N vs M」，那個對比看不出要做什麼） */
.twobox { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
/* 納管四態：互斥且窮盡，每格對應一個動作 */
.fourbox { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 8px; }
.fourbox .box { padding: 10px 12px; }
.fourbox .bn { font-size: 28px; }
.fourbox .st-未登記 .bn { color: #d97706; }
.fourbox .st-未納管 .bn { color: #2563eb; }
.fourbox .st-已納管 .bn { color: var(--brand); }
.fourbox .st-失聯 .bn { color: #dc2626; }
.fourbox .box.zero .bn { color: var(--muted); }
.totals { font-size: 11px; color: var(--muted); margin-bottom: 14px; }
.compsec { margin-bottom: 12px; }
.compsec .ck { font-size: 10.5px; color: var(--ink-soft); letter-spacing: 1px; margin-bottom: 5px; }
.bar { display: flex; height: 8px; border-radius: 999px; overflow: hidden; background: var(--border); margin-bottom: 6px; }
.bar .seg { height: 100%; }
/* 機房 × 環境別交叉表。窄螢幕橫向捲動，不讓表格把整頁撐破。 */
.mxwrap { overflow-x: auto; }
.mx { border-collapse: collapse; font-size: 12px; white-space: nowrap; }
.mx th, .mx td { padding: 4px 10px; text-align: right; border-bottom: 1px solid var(--border); }
.mx thead th { color: var(--ink-soft); font-weight: 600; font-size: 11px; }
.mx .mxcorner, .mx .mxrh { text-align: left; color: var(--ink-soft); font-weight: 600; }
.mx .mxn { color: var(--ink); text-decoration: none; }
.mx .mxn:hover { color: var(--brand); text-decoration: underline; }
.mx .mxz { color: var(--border-strong); }              /* 0 壓暗，讓有數字的格子跳出來 */
.mx .mxsum { color: var(--ink-soft); font-weight: 600; }
.mx tfoot td, .mx tfoot th { border-bottom: none; border-top: 1px solid var(--border-strong); }
.mx .mxgrand { color: var(--ink); }             /* 右下角總數：對得起來與否就看這格 */
.mxhint { margin: 6px 0 0; font-size: 11px; color: #a16207; line-height: 1.6; }

.cleg { display: flex; flex-wrap: wrap; gap: 4px 14px; }
.cleg .ci { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--ink-soft); text-decoration: none; }
.cleg .ci:hover { color: var(--ink); }
.cleg .ci b { color: var(--ink); font-family: var(--disp); font-size: 14px; margin-left: 7px; }
.cleg .sw { width: 8px; height: 8px; border-radius: 2px; }
.osnote { font-size: 10.5px; color: var(--muted); margin-top: 8px; }
/* 平台數字框：跟 EOS 頁的狀態數字框同一套視覺語言（大數字＋標籤＋色框），
   取代原本的小 chip 清單，一眼看出各平台（含 Linux 各發行版）的資產量。 */
.ptiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
.ptile { grid-column: span 1; background: var(--card); border: 1px solid var(--border);
  border-radius: 14px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
.pt-link { text-decoration: none; }
.pt-num { font-size: 24px; font-weight: 700; line-height: 1.1; font-family: var(--disp); }
.pt-lbl { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-soft); margin-top: 2px; }
.pt-link:hover .pt-lbl { color: var(--ink); }
.pt-lbl .sw { width: 8px; height: 8px; border-radius: 2px; flex: none; }
.ci-wrap { display: flex; align-items: center; gap: 6px; }
.ci-expand { display: inline-flex; align-items: center; gap: 4px; cursor: pointer;
  font-size: 11px; font-weight: 600; color: var(--ink-soft); background: rgba(100,116,139,.1);
  border: 1px solid rgba(100,116,139,.3); border-radius: 999px; padding: 3px 10px 3px 9px;
  font-family: inherit; white-space: nowrap; align-self: flex-start; }
.ci-expand:hover { color: var(--brand); border-color: rgba(34,168,90,.5); background: rgba(34,168,90,.1); }
.ci-expand .arw { display: inline-block; font-size: 9px; transition: transform .15s; }
.ci-expand.open { color: var(--brand); border-color: rgba(34,168,90,.5); background: rgba(34,168,90,.12); }
.ci-expand.open .arw { transform: rotate(90deg); }
.os-breakdown { display: flex; flex-wrap: wrap; gap: 4px 12px; width: 100%;
  margin: 0; padding: 6px 0 0; border-top: 1px solid rgba(34,168,90,.2); }
.os-breakdown .ci.sub { font-size: 11px; color: var(--ink-soft); }
.os-breakdown .ci.sub b { font-size: 12.5px; }
.os-breakdown .ci.dim { cursor: default; }
.os-breakdown .ci.dim:hover { color: var(--ink-soft); }
/* Linux／其他這種多平台大類，展開後每個子平台自己還能再展開版本明細——
   縮小字級＋左邊一條細線做視覺縮排，跟第一層的展開區隔開，不會混在一起看不出層次。 */
.ci-expand.sm { font-size: 10px; padding: 2px 8px 2px 7px; }
.os-breakdown.sub2 { margin: 2px 0 2px 4px; padding: 4px 0 4px 10px;
  border-top: none; border-left: 2px solid rgba(100,116,139,.3); }

/* ===== 資料品質與待辦 ===== */
.dqsec { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border); }
.dqgrid { display: grid; gap: 8px; margin-top: 8px;
  grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); }
.dq { display: flex; flex-direction: column; gap: 2px; padding: 9px 11px; border-radius: 9px;
  border: 1px solid var(--border); background: var(--card);
  text-decoration: none; color: inherit; }
/* 可點的才給 hover 反應，純顯示的（已完成合併）不要假裝可點 */
a.dq:hover { border-color: rgba(0,145,66,.45); background: rgba(0,145,66,.06); }
.dq .dqn { font-family: var(--disp); font-size: 21px; line-height: 1.15; color: var(--ink);
  font-variant-numeric: tabular-nums; }
.dq .dql { font-size: 11.5px; color: var(--ink-soft); }
.dq .dqx { font-size: 10px; color: var(--muted); line-height: 1.45; }
.dq.ok .dqn { color: var(--brand); }
.dq.done .dqn { color: #2563eb; }
.dq.warn .dqn { color: #d97706; }
.totals b { color: var(--ink-soft); font-weight: 700; }
.twobox .box {
  display: block; text-decoration: none; padding: 12px 14px; border-radius: 14px;
  background: var(--card); border: 1px solid var(--border);
  transition: border-color .2s, background .2s;
}
.twobox .box:hover { border-color: rgba(0,145,66,.5); background: rgba(0,145,66,.07); }
.twobox .bk { font-size: 11px; color: var(--ink-soft); letter-spacing: 1px; }
.twobox .bn { font-family: var(--disp); font-size: 34px; font-weight: 700; color: var(--ink); line-height: 1.15; letter-spacing: -1px; }
.twobox .bs { font-size: 10.5px; color: var(--muted); }
.twobox .box.bad .bn { color: #dc2626; }
.twobox .box.bad:hover { border-color: rgba(255,107,107,.5); background: rgba(255,107,107,.07); }
.twobox .box.bad.zero .bn { color: var(--brand); }
/* 明細的範圍標示：hero 兩框是全站、下面是環境篩選過的，不標會被當成同一把尺 */
.scope { font-size: 10.5px; color: var(--muted); letter-spacing: .5px; margin-bottom: 5px; }
.twobox .box.bad.zero:hover { border-color: rgba(0,145,66,.5); background: rgba(0,145,66,.07); }
/* ===== 下鑽：每個數字都是連結，但不能長得像一堆藍色底線把戰情室弄髒 =====
   做法是保留原本的排版，只加「可點」的訊號：hover 時提亮 + 左移一點 + 出現箭頭。 */
.legend { display: flex; flex-direction: column; gap: 10px; font-size: 13px; }
.legend .row {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--ink-soft);
  text-decoration: none;
  padding: 3px 6px;
  margin: -3px -6px;
  border-radius: 7px;
  transition: background .16s ease, color .16s ease;
}
.legend .row:hover { background: rgba(15,23,42,.05); color: var(--ink); }
.legend .row::after {
  content: '→';
  opacity: 0;
  margin-left: 4px;
  font-size: 11px;
  transition: opacity .16s ease;
}
.legend .row:hover::after { opacity: .65; }

/* hero 大數字（CIA vs 掃描） */
.numlink {
  color: inherit;
  text-decoration: none;
  border-bottom: 1px dashed transparent;
  transition: color .16s ease, border-color .16s ease;
}
.numlink:hover { color: var(--brand); border-bottom-color: rgba(34,168,90,.5); }

/* 圓環中央的一致率 */
a.pct { text-decoration: none; color: inherit; cursor: pointer; }
a.pct:hover b { color: var(--brand); transition: color .16s ease; }

/* 右側四格指標：整格可點 */
a.stat { display: block; text-decoration: none; color: inherit; cursor: pointer; position: relative; }
a.stat::after {
  content: '→';
  position: absolute;
  top: 14px;
  right: 14px;
  font-size: 12px;
  opacity: 0;
  transition: opacity .16s ease;
}
a.stat:hover::after { opacity: .55; }
a.stat:hover { border-color: rgba(0,145,66,.45); }
.legend .sw { width: 9px; height: 9px; border-radius: 3px; }
.legend b { font-family: var(--disp); color: var(--ink); margin-left: auto; font-variant-numeric: tabular-nums; }

.stack { display: flex; flex-direction: column; gap: 14px; align-self: start; }
.alert { padding: 20px 22px; border-radius: 20px; background: linear-gradient(135deg, rgba(232,124,7,.16), rgba(232,124,7,.05));
  border: 1px solid rgba(232,124,7,.35); display: flex; align-items: center; justify-content: space-between; }
.alert.ok { background: linear-gradient(135deg, rgba(0,145,66,.12), rgba(0,145,66,.03)); border-color: rgba(0,145,66,.3); }
.alert .n { font-family: var(--disp); font-size: 40px; font-weight: 700; color: #d97706; letter-spacing: -1.5px; line-height: 1; }
.alert .lab { font-size: 12px; color: #b45309; margin-top: 4px; }
.alert.ok .lab { color: #00703a; }
.alert .go { color: #b45309; border: 1px solid rgba(255,184,103,.5); border-radius: 9px; padding: 8px 14px; font-size: 12px; font-weight: 700; text-decoration: none; white-space: nowrap; }
.alert .go:hover { background: rgba(255,184,103,.12); }
.mini { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.mini .stat.glass { border-color: #94a3b8; }
.stat { padding: 16px 18px; }
.stat .n { font-family: var(--disp); font-size: 30px; font-weight: 600; letter-spacing: -1px; line-height: 1; }
.stat .lab { font-size: 11.5px; color: var(--muted); margin-top: 6px; }
.n.teal { color: var(--brand); } .n.red { color: #dc2626; } .n.amber { color: #d97706; } .n.blue { color: #2563eb; }

.feed { margin-top: 22px; padding: 22px 26px; }
.feed h3 { font-family: var(--disp); font-size: 13px; letter-spacing: 1px; text-transform: uppercase; color: var(--ink-soft); margin: 0 0 14px; display: flex; align-items: center; gap: 8px; }
.feed .beat { width: 7px; height: 7px; border-radius: 50%; background: #009142; animation: beat 1.8s infinite; }
@keyframes beat { 0%{box-shadow:0 0 0 0 rgba(0,145,66,.55)} 70%{box-shadow:0 0 0 8px rgba(0,145,66,0)} 100%{box-shadow:0 0 0 0 rgba(0,145,66,0)} }
.empty { color: var(--ink-soft); font-size: 13px; padding: 10px 0; }
.frow { display: flex; align-items: center; gap: 14px; padding: 11px 0; border-bottom: 1px solid var(--border); font-size: 13px; text-decoration: none; color: inherit; }
.frow:hover { background: rgba(15,23,42,.03); }
.frow:last-child { border-bottom: none; }
.feed h3 .h3link { margin-left: auto; font-size: 11.5px; letter-spacing: 0; text-transform: none; color: #009142; text-decoration: none; }
.feed h3 .h3link:hover { text-decoration: underline; }
.frow .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.frow .host { font-family: var(--disp); color: var(--ink); width: 150px; }
.frow .ip { font-family: var(--disp); color: var(--muted); width: 140px; font-size: 12px; }
.frow .type { flex: 1; }
.frow .time { margin-left: auto; color: var(--muted); font-size: 12px; font-family: var(--disp); }
.morelink { display: inline-block; margin-top: 14px; font-size: 12.5px; color: var(--brand); text-decoration: none; }
.morelink:hover { text-decoration: underline; }

@media (max-width: 900px) { .stage { grid-template-columns: 1fr; } .hero { flex-direction: column; text-align: center; } }
</style>
