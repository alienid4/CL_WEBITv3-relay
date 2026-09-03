<script setup lang="ts">
// 首頁（總覽）：依 2026-08-24「示範白綠」全站視覺規範 ＋ 首頁實作規格（方向 1a）重寫。
// 版面順序：頂欄 → 主管結論帶 → Hero 徑向雙環＋對帳明細 → 分佈 → 機房×環境交叉表 → 右欄待辦。
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

function stateCount(k: string) { return mstate.value?.counts?.[k] ?? 0 }

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
// 體檢彙總：首頁只講一句話——幾台完全沒問題、幾台要看。細節在資產查詢頁的體檢欄。
interface HealthSummary {
  total: number; clean: number; needs_review: number
  machine_bad: number; data_bad: number
  by_issue: Record<string, number>
  // 有沒有被 SSH/WinRM 直接驗證過——刻意跟 needs_review 分開講，不是體檢問題，
  // 是誠實揭露。這個數字通常比 needs_review 大很多是正常的：大部分未驗證的機器
  // 其實有 CIA/RVTools/dynassets 資料撐著（2026-08-25 使用者拍板方案A）。
  unverified: number
}
const health = ref<HealthSummary | null>(null)
try {
  health.value = await apiFetch<HealthSummary>('/api/health/summary')
} catch { /* 舊後端沒這支，安靜跳過不擋整頁 */ }
// 前三大卡點：只列前三個，列十個等於沒有重點。
const topIssues = computed(() =>
  Object.entries(health.value?.by_issue ?? {}).slice(0, 3).map(([k, n]) => ({ k, n })),
)

const comp = ref<Composition | null>(null)
try {
  comp.value = await apiFetch<Composition>('/api/dashboard/composition')
} catch { /* 拿不到就不顯示這一區，不擋整頁 */ }

// 「未知／未填／N/A」這類「資料缺漏」的分類：一律排到最後、用統一灰色，
// 跟真實分類的彩色區隔開——一眼看出哪些是實際分佈、哪些只是資料還沒補。
const UNKNOWN_KEYS = new Set(['未知', '未填', 'N/A', 'n/a', '(空)', '', '未分類', 'unknown', 'Unknown'])
const UNKNOWN_COLOR = '#54655e'
const isUnknownKey = (k: string) => UNKNOWN_KEYS.has(String(k).trim())

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

function fmtTime(s: string): string {
  return s ? s.replace(/^\d{4}-/, '').slice(0, 14) : ''
}
// ===== 以下為 2026-08-24「示範白綠」規範＋首頁規格（方向 1a）新增 =====
// 長條圖不換色，只降不透明度分層級（規範 §5）；狀態色只用在真的是狀態的地方。
function greenShade(i: number, total: number): string {
  const a = Math.max(0.18, 0.95 - i * (0.72 / Math.max(1, total - 1)))
  return `rgba(0,128,106,${a.toFixed(2)})`
}
const WARN_FILL = 'rgba(176,106,0,.55)'   // 缺口類（未填/未知）用注意色，跟真實分佈區隔

// 數字一律千分位；沒有值顯示 — 而不是 0（0 是「真的是零」，沒值是「還不知道」）
function n(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : v.toLocaleString()
}

// ===== 主管結論帶 =====
// 全站口徑（不吃環境下拉），理由同 hero：頭條數字被偷偷過濾但標籤沒說，人會誤讀。
const icaOnlyAll = computed(() => {
  const s = stats.value
  return s ? s.total_ica_count - s.total_overlap_count : 0
})
const diffCount = computed(() => icaOnlyAll.value + (stats.value?.scan_only_count ?? 0))
const scannedAlive = computed(() => stats.value?.scanned_count ?? 0)
// 帳實相符率沿用 consistency（分母是聯集，不是只有登記數——理由見上面那段註解）。
const matchPct = computed(() => consistency.value)
// 資料收集率＝OS 是機器自己回報的比例。跟帳實相符是兩件事：一個問「帳對不對」，
// 一個問「我到底摸得到多少台」。
const collectPct = computed(() => {
  const c = comp.value
  if (!c || !c.total) return 0
  return (c.os_from_facts / c.total) * 100
})
// 人力缺口：待審核筆數 ÷ 每週處理量。REVIEW_PER_WEEK 是**估算前提不是事實**，
// 所以畫面上一定要把前提寫出來，否則「約需 11 週」會被當成系統算出來的結論。
const REVIEW_PER_WEEK = 300
const reviewWeeks = computed(() => {
  const pend = comp.value?.data_quality?.pending_review ?? 0
  return Math.ceil(pend / REVIEW_PER_WEEK)
})

// 徑向雙環：外環＝帳實相符、內環＝資料收集率。r=86 → 圓周 540；內環 r=66 → 415。
const dashOuter = computed(() => 540 * (1 - matchPct.value / 100))
const dashInner = computed(() => 415 * (1 - collectPct.value / 100))

// ===== 對帳明細 5 列 =====
const reconRows = computed(() => {
  const s = stats.value
  if (!s) return []
  return [
    { key: '一致', tone: 'good', n: s.total_overlap_count,
      sub: '登記有、網路上也掃得到', to: drill.value.overlap },
    { key: '搜不到', tone: 'bad', n: icaOnlyAll.value,
      sub: '登記在案，這次掃描沒回應', to: drill.value.icaOnly },
    { key: '未登記', tone: 'warn', n: s.scan_only_count,
      sub: '網路上活著，資產庫查無此機', to: drill.value.scanOnly },
    { key: '已納管', tone: 'good', n: stateCount('已納管'),
      sub: '連得進去，收得到主機名／OS／序號', to: { path: '/assets' } },
    { key: '已退役', tone: 'muted', n: retiredCount.value,
      sub: '停用／報廢／閒置，不計入在管台數',
      to: { path: '/assets', query: { filter_field: 'asset_status', filter_value: '停用,報廢,閒置' } } },
  ]
})

// ===== 問題清單：分組摺疊 =====
// 需求重點：失聯機器只會越來越多，清單**不可以隨資料量把首頁撐長**。
// 所以只渲染群組列，展開最多 3 筆，其餘一律導到 /issues。
//
// 「連續消失」怎麼判：comparison_result 沒有「第幾期」這個欄位，但每次比對都會為
// 同一台機器插一筆新的 detected_at。所以同一台（主機名＋IP）出現在 2 個以上不同的
// 掃描日期＝連續多期都沒回來，跟這一期才第一次消失的意義完全不同——前者要查是不是
// 真的退役了，後者可能只是當下網路不通。這是從既有資料推得的，沒有新增欄位。
interface IssueGroup {
  key: string; name: string; sub: string; tone: string
  n: number; oldest: string; items: IssueRow[]; to: any
}
function dayOf(t: string) { return (t || '').slice(0, 10) }
const issueGroups = computed<IssueGroup[]>(() => {
  const lost = issues.value.filter((r) => r.issue_type === '異常消失')
  const days: Record<string, Set<string>> = {}
  for (const r of lost) {
    const k = `${r.hostname ?? ''}|${r.ip ?? ''}`
    ;(days[k] ??= new Set()).add(dayOf(r.detected_at))
  }
  // 同一台只留最新那一筆，否則「9 台」會被同一台的多筆紀錄灌成 9 筆
  const latestOf = (rows: IssueRow[]) => {
    const m: Record<string, IssueRow> = {}
    for (const r of rows) {
      const k = `${r.hostname ?? ''}|${r.ip ?? ''}`
      if (!m[k] || r.detected_at > m[k].detected_at) m[k] = r
    }
    return Object.values(m).sort((a, b) => a.detected_at.localeCompare(b.detected_at))
  }
  const repeat = latestOf(lost.filter((r) => (days[`${r.hostname ?? ''}|${r.ip ?? ''}`]?.size ?? 0) >= 2))
  const first = latestOf(lost.filter((r) => (days[`${r.hostname ?? ''}|${r.ip ?? ''}`]?.size ?? 0) < 2))
  const added = latestOf(issues.value.filter((r) => r.issue_type === '異常新增'))
  // 「在網路上但未登記」不是 comparison_result 的問題型別，是納管四態的一格，
  // 但對窗口來說它跟上面三類是同一種待辦（要去處理的機器），所以放在同一張卡。
  const unregItems: IssueRow[] = (mstate.value?.items ?? [])
    .filter((i) => i.state === '未登記')
    .map((i, idx) => ({ id: -1 - idx, detected_at: mstate.value?.scan_time ?? '', hostname: i.hostname, ip: i.ip, issue_type: '未登記' }))

  const mk = (key: string, name: string, sub: string, tone: string, items: IssueRow[], to: any): IssueGroup => ({
    key, name, sub, tone, n: items.length,
    oldest: items.length ? fmtTime(items[0].detected_at) : '—',
    items, to,
  })
  return [
    mk('repeat', '連續消失（2 期以上）', '兩次以上掃描都沒回應，要查是不是已經退役', 'bad',
       repeat, { path: '/issues', query: { type: '異常消失' } }),
    mk('first', '本期首次消失', '這一期才掃不到，可能只是當下網路不通', 'warn',
       first, { path: '/issues', query: { type: '異常消失' } }),
    mk('added', '異常新增', '這次比對多出來的機器', 'warn',
       added, { path: '/issues', query: { type: '異常新增' } }),
    mk('unreg', '在網路上但未登記', '活著但資產庫查無此機，要納入管理', 'warn',
       unregItems, { path: '/adopt' }),
  ].filter((g) => g.n > 0)
})
const totalPending = computed(() => issueGroups.value.reduce((s, g) => s + g.n, 0))
// 同時只允許一組展開：兩組一起開就會把卡片撐高，違反「資料量不影響高度」。
const openGroup = ref<string | null>(null)
function toggleIssueGroup(k: string) { openGroup.value = openGroup.value === k ? null : k }

// ===== 分佈長條（平台／虛實／環境別）=====
function bars(m: Record<string, number> | undefined, grouped = false) {
  const rows = grouped ? groupedPlatforms(m) : segments(m)
  const total = rows.reduce((s, r) => s + r.n, 0) || 1
  return rows.filter((r) => r.n > 0).map((r, i) => ({
    key: r.key, n: r.n, pct: (r.n / total) * 100,
    fill: isUnknownKey(r.key) || r.key === '其他' ? WARN_FILL : greenShade(i, rows.length),
    members: (r as any).members ?? [],
  }))
}
const platformBars = computed(() => bars(comp.value?.by_platform, true))
const virtBars = computed(() => bars(comp.value?.by_virtualization))
const envBars = computed(() => bars(comp.value?.by_environment))

// OS 來源下鑽：判定規則在後端（/api/assets?os_source=），跟首頁那兩個數字同一條規則。
// ⚠️ 刻意不帶 environment——這兩個數字來自 composition()，那支是全站統計不吃環境篩選
// （跟平台／環境別／機房那幾區的連結一致）。帶了會讓點進去的筆數比畫面數字少。
const osSourceLink = (src: 'facts' | 'guessed') => ({ path: '/assets', query: { os_source: src } })

// 缺口格：機房或環境沒填的那一列/欄要看得出來，不能混在一般格子裡
const isGap = (k: string) => k === '未填' || isUnknownKey(k)

// 匯出目前這批待處理問題。用前端既有資料組 CSV，不另外開後端端點——
// 這裡要的就是「畫面上這幾筆」，跟後端全量匯出是兩回事。
function exportIssuesCsv() {
  const rows = [['群組', '主機名稱', 'IP', '問題類型', '發現時間']]
  for (const g of issueGroups.value) {
    for (const it of g.items) rows.push([g.name, it.hostname ?? '', it.ip ?? '', it.issue_type, it.detected_at])
  }
  // BOM：沒有它 Excel 開中文會變亂碼
  const csv = '﻿' + rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `待處理問題_${(stats.value?.last_scan_time ?? '').slice(0, 10) || 'export'}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
</script>


<template>
  <div class="dash">
    <!-- 1 頂欄：系統名 + 狀態 + 操作 -->
    <header class="top">
      <div class="tleft">
        <h1>資產盤點</h1>
        <span class="live"><i class="dot" :class="{ warn: stats && !stats.last_scan_ok }" />LIVE</span>
        <span v-if="stats" class="stamp mono">
          {{ stats.last_scan_time ?? '尚未執行過' }}
          <em>·</em>
          {{ stats.last_scan_ok ? '全網段完成' : `${stats.failed_segments.length} 個網段失敗` }}
        </span>
      </div>
      <div class="tright">
        <label class="envwrap">環境
          <select v-model="environment">
            <option v-for="opt in ENV_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <button class="btn-primary" :disabled="scanning" @click="triggerRescan">
          <span v-if="scanning" class="spin" />{{ scanning ? '掃描中…' : '重新掃描' }}
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="stats">
      <!-- 2 主管結論帶：三句話講完現況，數字上色 -->
      <section class="verdict">
        <div class="v">
          <div class="vk">RECONCILIATION</div>
          <p class="vt">
            帳實相符 <b class="good">{{ matchPct.toFixed(1) }}%</b>：登記 {{ n(stats.total_ica_count) }} 台、
            網路實掃 {{ n(scannedAlive) }} 台，
            <NuxtLink to="/issues" class="vlink">對不起來 <b class="bad">{{ n(diffCount) }}</b> 台</NuxtLink>。
          </p>
        </div>
        <div class="v">
          <div class="vk">DECISION</div>
          <p class="vt">
            需要決策：<b class="bad">{{ n(icaOnlyAll) }}</b> 台登記在案卻掃不到，
            其中 <b class="bad">{{ n(issueGroups.find((g) => g.key === 'repeat')?.n ?? 0) }}</b> 台
            已連續 2 期以上沒回應，該確認是否退役。
          </p>
        </div>
        <div class="v">
          <div class="vk">CAPACITY</div>
          <p class="vt">
            人力缺口：待人工審核 <b class="warn">{{ n(comp?.data_quality?.pending_review ?? 0) }}</b> 筆，
            約需 <b class="warn">{{ reviewWeeks }}</b> 週消化。
            <span class="vnote">以每週處理 {{ REVIEW_PER_WEEK }} 筆估算</span>
          </p>
        </div>
      </section>

      <!-- 體檢一句話：一眼看出「幾台完全不用管、幾台要看」，點進去就是已經排好序的清單。
           放在結論帶下面、主資料上面，因為它回答的是「我今天要動幾台」。 -->
      <section v-if="health && health.total" class="card hbar">
        <div class="hb-main">
          <NuxtLink :to="{ path: '/assets', query: { sort_by: 'health_rank', order: 'asc' } }"
                    class="hb-num" title="看要處理的機器（已按嚴重度排好）">
            <b class="mono">{{ n(health.needs_review) }}</b> 台要查看
          </NuxtLink>
          <span class="hb-sep">·</span>
          <span class="hb-ok"><b class="mono">{{ n(health.clean) }}</b> 台完全沒問題</span>
          <span class="hb-detail">
            機器有異常 <b class="bad">{{ n(health.machine_bad) }}</b> 台、
            資料有問題 <b class="warn">{{ n(health.data_bad) }}</b> 台
          </span>
        </div>
        <div v-if="topIssues.length" class="hb-issues">
          最常卡在：
          <NuxtLink v-for="t in topIssues" :key="t.k"
                    :to="{ path: '/assets', query: { sort_by: 'health_rank', order: 'asc' } }"
                    class="hb-chip">{{ t.k }} <b class="mono">{{ n(t.n) }}</b></NuxtLink>
        </div>
        <!-- 誠實揭露、不是體檢問題：這個數字通常比上面「要查看」大很多是正常的——
             CIA/RVTools/dynassets 三個被動來源合起來已經有完整盤點，只是沒有
             SSH/WinRM 親自驗證過（2026-08-25 使用者拍板：這兩件事不該混成一個
             黃燈）。 -->
        <p v-if="health.unverified" class="hb-unverified">
          其中 <b class="mono">{{ n(health.unverified) }}</b> 台未經 SSH/WinRM 驗證——
          多數已有登記/掃描資料，只是沒有機器親口確認過
        </p>
      </section>

      <div class="grid">
        <!-- 左欄 -->
        <div class="mainc">
          <!-- 3 Hero：徑向雙環 + 對帳明細，同一張卡 -->
          <section class="card hero">
            <div class="gauge">
              <svg viewBox="0 0 200 200" class="ring">
                <circle cx="100" cy="100" r="86" class="trk" />
                <circle cx="100" cy="100" r="86" class="arc outer" :style="{ strokeDashoffset: dashOuter }" />
                <circle cx="100" cy="100" r="66" class="trk inner" />
                <circle cx="100" cy="100" r="66" class="arc inner" :style="{ strokeDashoffset: dashInner }" />
              </svg>
              <NuxtLink :to="drill.allAssets" class="gc" title="看全部有效資產（不含退役）">
                <div class="gn mono">{{ n(totalAssetCount) }}</div>
                <div class="gl">在管資產</div>
                <div class="gp mono">{{ matchPct.toFixed(1) }}% 帳實相符</div>
              </NuxtLink>
            </div>

            <div class="recon">
              <div class="ck">RECONCILIATION</div>
              <NuxtLink v-for="r in reconRows" :key="r.key" :to="r.to" class="rrow">
                <i class="dot" :class="r.tone" />
                <span class="rn">{{ r.key }}<em>{{ r.sub }}</em></span>
                <b class="mono" :class="r.tone">{{ n(r.n) }}</b>
                <span class="arw">›</span>
              </NuxtLink>
              <p class="rnote">
                外環＝帳實相符率、內環＝資料收集率 {{ collectPct.toFixed(1) }}%（{{ n(comp?.os_from_facts) }} 台的 OS 是機器自己回報的）
              </p>
            </div>
          </section>

          <!-- 4 平台與組成 -->
          <section v-if="comp" class="card dist">
            <div class="dhead">
              <div class="ck">作業系統平台</div>
              <span class="note">
                <NuxtLink :to="osSourceLink('facts')" class="nlink">{{ n(comp.os_from_facts) }} 台實際收集</NuxtLink>、
                <NuxtLink :to="osSourceLink('guessed')" class="nlink">{{ n(comp.os_guessed) }} 台掃描推測</NuxtLink>、
                <NuxtLink :to="{ path: '/assets', query: { filter_field: 'os', filter_value: '' } }" class="nlink">{{ n(comp.data_quality?.os_unknown) }} 台未知</NuxtLink>
              </span>
            </div>
            <div class="bar">
              <span v-for="b in platformBars" :key="b.key" class="seg"
                    :style="{ width: b.pct + '%', background: b.fill }" :title="`${b.key} ${n(b.n)}`" />
            </div>
            <div class="legend">
              <NuxtLink v-for="b in platformBars" :key="b.key"
                        :to="{ path: '/assets', query: { platform: b.members.join(',') } }" class="lg">
                <i class="sw" :style="{ background: b.fill }" />{{ b.key }}<b class="mono">{{ n(b.n) }}</b>
              </NuxtLink>
            </div>

            <div class="two">
              <div>
                <div class="ck">實體 / 虛擬</div>
                <div class="bar sm">
                  <span v-for="b in virtBars" :key="b.key" class="seg"
                        :style="{ width: b.pct + '%', background: b.fill }" :title="`${b.key} ${n(b.n)}`" />
                </div>
                <div class="legend">
                  <NuxtLink v-for="b in virtBars" :key="b.key"
                            :to="{ path: '/assets', query: { virtual: b.key === '虛擬機' ? 'yes' : 'no' } }" class="lg">
                    <i class="sw" :style="{ background: b.fill }" />{{ b.key }}<b class="mono">{{ n(b.n) }}</b>
                  </NuxtLink>
                </div>
              </div>
              <div>
                <div class="ck">環境別</div>
                <div class="bar sm">
                  <span v-for="b in envBars" :key="b.key" class="seg"
                        :style="{ width: b.pct + '%', background: b.fill }" :title="`${b.key} ${n(b.n)}`" />
                </div>
                <div class="legend">
                  <!-- 一律走 environment_group（不是 filter_field 精確比對）：「測試」含 UAT/DEV/OA，
                       精確比對點進去會少算，數字對不起來。理由同下面交叉表的 cellLink。 -->
                  <NuxtLink v-for="b in envBars" :key="b.key"
                            :to="{ path: '/assets', query: { environment_group: b.key } }" class="lg">
                    <i class="sw" :style="{ background: b.fill }" />{{ b.key }}<b class="mono">{{ n(b.n) }}</b>
                  </NuxtLink>
                </div>
              </div>
            </div>
          </section>

          <!-- 5 機房 × 環境交叉表（列＝環境、欄＝機房）。合計那一格是在回答
               「加起來等不等於資產總數」——沒有它，看的人無從判斷有沒有東西被漏掉。 -->
          <section v-if="matrixLocs.length" class="card cross">
            <div class="ck">機房 × 環境</div>
            <div class="tblwrap">
              <table>
                <thead>
                  <tr>
                    <th>環境 \ 機房</th>
                    <th v-for="loc in matrixLocs" :key="loc" :class="{ gap: isGap(loc) }">{{ loc }}</th>
                    <th class="tot">合計</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="env in matrixEnvs" :key="env">
                    <th class="rowh" :class="{ gap: isGap(env) }">{{ env }}</th>
                    <td v-for="loc in matrixLocs" :key="loc" :class="{ gap: isGap(env) || isGap(loc) }">
                      <NuxtLink v-if="cellCount(loc, env)" :to="cellLink(loc, env)" class="cell mono">
                        {{ n(cellCount(loc, env)) }}
                      </NuxtLink>
                      <span v-else class="mono empty">—</span>
                    </td>
                    <td class="tot mono">{{ n(rowTotal(env)) }}</td>
                  </tr>
                  <tr class="sum">
                    <th class="rowh">合計</th>
                    <td v-for="loc in matrixLocs" :key="loc" class="mono">{{ n(colTotal(loc)) }}</td>
                    <td class="tot mono">{{ n(matrixTotal) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="note">
              底色標注意色的是資料缺口——<b>{{ n(cellsUnfilled) }}</b> 台的機房或環境別沒填，
              分不進任何實際位置。（不是相加而是聯集：同一台可能兩欄都沒填，相加會灌水。）
            </p>
          </section>
        </div>

        <!-- 6 右欄（sticky）：待辦與資料乾淨度 -->
        <aside class="side">
          <section class="card issues">
            <div class="ihead">
              <div class="ck">待處理</div>
              <span class="ibadge mono">{{ n(totalPending) }}</span>
            </div>
            <p v-if="!issueGroups.length" class="ok">目前沒有待處理的問題。</p>
            <div v-for="g in issueGroups" :key="g.key" class="ig">
              <button type="button" class="igrow" :class="{ open: openGroup === g.key }" @click="toggleIssueGroup(g.key)">
                <i class="dot" :class="g.tone" />
                <span class="ign">{{ g.name }}<em>{{ g.sub }}</em></span>
                <b class="mono" :class="g.tone">{{ n(g.n) }}</b>
                <span class="ot mono">{{ g.oldest }}</span>
                <span class="arw">{{ openGroup === g.key ? '▾' : '▸' }}</span>
              </button>
              <div v-if="openGroup === g.key" class="igbody">
                <NuxtLink v-for="it in g.items.slice(0, 3)" :key="it.id" :to="g.to" class="iitem">
                  <span class="ih">{{ it.hostname || '（無主機名）' }}</span>
                  <span class="ii mono">{{ it.ip || '—' }}</span>
                  <span class="itm mono">{{ fmtTime(it.detected_at) }}</span>
                </NuxtLink>
                <NuxtLink v-if="g.n > 3" :to="g.to" class="imore">
                  還有 {{ n(g.n - 3) }} 筆，開啟完整清單 ›
                </NuxtLink>
              </div>
            </div>
            <div class="ifoot">
              <NuxtLink to="/issues" class="btn-ghost">開啟完整清單（{{ n(totalPending) }} 筆）›</NuxtLink>
              <button type="button" class="btn-ghost" :disabled="!totalPending" @click="exportIssuesCsv">匯出 CSV</button>
            </div>
          </section>

          <section v-if="comp?.data_quality" class="card clean">
            <div class="ck">資料乾淨度</div>
            <div class="cpct mono">{{ collectPct.toFixed(1) }}<small>%</small></div>
            <p class="csub">{{ n(comp.os_from_facts) }} / {{ n(comp.total) }} 台拿得到機器自報的事實</p>
            <div class="bar sm"><span class="seg" :style="{ width: collectPct + '%', background: 'var(--brand)' }" /></div>
            <NuxtLink v-for="q in [
                        { k: '待人工審核', v: comp.data_quality.pending_review, to: '/import', tone: 'warn', why: '判不準是否同一台，系統不自動合併' },
                        { k: '已由 vCenter 校正', v: comp.data_quality.verified_by_vcenter, to: { path: '/assets', query: { virtual: 'yes' } }, tone: 'good', why: `佔 ${comp.data_quality.verified_pct}%` },
                        { k: '重複登記（組）', v: comp.data_quality.duplicate_groups, to: { path: '/assets', query: { show: 'duplicates' } }, tone: 'warn', why: `清掉可少 ${n(comp.data_quality.duplicate_extra_rows)} 筆` },
                        { k: 'OS 仍未知', v: comp.data_quality.os_unknown, to: { path: '/assets', query: { filter_field: 'os', filter_value: '' } }, tone: 'warn', why: 'vCenter 涵蓋不到，要靠掃描或 SSH' },
                      ]" :key="q.k" :to="q.to" class="qrow">
              <span class="qk">{{ q.k }}<em>{{ q.why }}</em></span>
              <b class="mono" :class="q.tone">{{ n(q.v) }}</b>
              <span class="arw">›</span>
            </NuxtLink>
          </section>
        </aside>
      </div>
    </template>
    <div v-else-if="loading" class="skel">
      <div class="sk h1" /><div class="sk band" /><div class="sk big" />
    </div>
  </div>
</template>

<style scoped>
/* 顏色一律取自 main.css 的變數（全站視覺規範 §1：唯一顏色來源，不新增顏色）。
   數字全部 tabular-nums 對齊；可點的元素一定有 hover 回饋。 */
.dash { color: var(--ink); }

/* ===== 1 頂欄 ===== */
.top { display: flex; align-items: center; justify-content: space-between; gap: 20px;
  flex-wrap: wrap; margin-bottom: 22px; }
.tleft { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.tleft h1 { font-size: 32px; font-weight: 600; letter-spacing: -1px; margin: 0; color: var(--ink); }
.live { display: inline-flex; align-items: center; gap: 7px; font-family: var(--disp);
  font-size: 10.5px; letter-spacing: 2px; color: var(--muted); }
.stamp { font-size: 12.5px; color: var(--muted); }
.stamp em { font-style: normal; margin: 0 6px; color: var(--muted); }
.tright { display: flex; align-items: center; gap: 14px; }
.envwrap { font-size: 12.5px; color: var(--muted); display: inline-flex; align-items: center; gap: 8px; }
.envwrap select { font-family: inherit; font-size: 12.5px; padding: 8px 10px; }

.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--good); flex: none; }
.dot.warn, .dot.bad { background: var(--warn); }
.dot.bad { background: var(--bad); }
.dot.muted { background: var(--border-strong); }
.live .dot { animation: beat 1.8s infinite; }
@keyframes beat { 0%,100% { opacity: 1 } 50% { opacity: .3 } }

.btn-primary { font-family: inherit; font-size: 13px; font-weight: 600; padding: 11px 20px;
  border: none; border-radius: 10px; background: var(--brand); color: #fff; cursor: pointer;
  display: inline-flex; align-items: center; gap: 8px; }
.btn-primary:hover { background: var(--brand-dark); }
.btn-primary:disabled { opacity: .6; cursor: not-allowed; }
.spin { width: 12px; height: 12px; border: 2px solid rgba(255,255,255,.45); border-top-color: #fff;
  border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.error-text { color: var(--bad); font-size: 13px; }

/* ===== 2 主管結論帶 ===== */
.verdict { display: grid; grid-template-columns: repeat(3, 1fr); background: var(--card);
  border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 20px 0; margin-bottom: 22px; }
.verdict .v { padding: 0 26px; border-left: 1px solid var(--line); }
.verdict .v:first-child { border-left: none; }
.vk { font-family: var(--disp); font-size: 10px; letter-spacing: 2px; color: var(--muted); margin-bottom: 8px; }
.vt { font-size: 14px; line-height: 1.65; margin: 0; color: var(--ink-soft); }
.vt b { font-family: var(--disp); font-size: 19px; font-weight: 600; letter-spacing: -.5px;
  font-variant-numeric: tabular-nums; }
.vt b.good { color: var(--brand-dark); } .vt b.warn { color: var(--warn-text); } .vt b.bad { color: var(--bad); }
.vlink { color: inherit; text-decoration: none; border-bottom: 1px solid var(--border-strong); }
.vlink:hover { border-bottom-color: var(--brand); color: var(--brand-dark); }
.vnote { display: block; font-size: 11.5px; color: var(--muted); margin-top: 2px; }

/* ===== 版面：左主右側，右欄跟捲 ===== */
.grid { display: grid; grid-template-columns: minmax(0, 1fr) 372px; gap: 22px; align-items: start; }
.mainc { display: flex; flex-direction: column; gap: 22px; min-width: 0; }
.side { display: flex; flex-direction: column; gap: 22px; position: sticky; top: 86px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 24px 26px; }
.ck { font-family: var(--disp); font-size: 10.5px; letter-spacing: 2px; color: var(--muted);
  margin-bottom: 12px; text-transform: uppercase; }

/* ===== 3 Hero：徑向儀表 + 對帳明細 ===== */
.hero { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 30px; align-items: center; }
.gauge { position: relative; width: 220px; height: 220px; margin: 0 auto; }
.ring { width: 100%; height: 100%; transform: rotate(-90deg); }
.ring .trk { fill: none; stroke: var(--line); stroke-width: 13; }
.ring .trk.inner { stroke-width: 9; }
.ring .arc { fill: none; stroke: var(--brand); stroke-width: 13; stroke-linecap: round;
  stroke-dasharray: 540; transition: stroke-dashoffset .6s ease; }
.ring .arc.inner { stroke-width: 9; stroke-dasharray: 415; stroke: rgba(0,128,106,.45); }
.gc { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; text-decoration: none; color: inherit; }
.gn { font-size: 42px; font-weight: 600; letter-spacing: -3px; line-height: 1; color: var(--ink); }
.gl { font-size: 12.5px; color: var(--muted); margin-top: 6px; }
.gp { font-size: 12px; color: var(--brand-dark); margin-top: 8px; }
.gc:hover .gn { color: var(--brand-dark); }

.recon { min-width: 0; }
.rrow { display: flex; align-items: center; gap: 12px; padding: 11px 10px; margin: 0 -10px;
  border-top: 1px solid var(--line); text-decoration: none; color: inherit; border-radius: 8px; }
.rrow:first-of-type { border-top: none; }
.rrow:hover { background: var(--sub); }
.rn { font-size: 14px; color: var(--ink-soft); flex: 1; min-width: 0; }
.rn em { display: block; font-style: normal; font-size: 11.5px; color: var(--muted); }
.rrow b { font-family: var(--disp); font-size: 20px; font-weight: 600; letter-spacing: -.5px;
  font-variant-numeric: tabular-nums; }
.rrow b.good { color: var(--brand-dark); } .rrow b.warn { color: var(--warn-text); }
.rrow b.bad { color: var(--bad); } .rrow b.muted { color: var(--muted); }
.arw { color: var(--muted); font-size: 15px; }
.rrow:hover .arw { color: var(--brand-dark); }
.rnote { font-size: 11.5px; color: var(--muted); margin: 12px 0 0; line-height: 1.6; }

/* ===== 4 分佈 ===== */
.dhead { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
.note { font-size: 11.5px; color: var(--muted); }
.nlink { color: var(--muted); text-decoration: none; border-bottom: 1px solid var(--border); }
.nlink:hover { color: var(--brand-dark); border-bottom-color: var(--brand); }
.bar { display: flex; height: 10px; border-radius: 999px; overflow: hidden; background: var(--line); margin: 4px 0 12px; }
.bar.sm { height: 6px; }
.bar .seg { height: 100%; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; }
.lg { display: inline-flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--ink-aux);
  text-decoration: none; }
.lg:hover { color: var(--brand-dark); }
.lg .sw { width: 9px; height: 9px; border-radius: 3px; flex: none; }
.lg b { font-family: var(--disp); font-size: 14px; color: var(--ink); font-variant-numeric: tabular-nums; }
.lg:hover b { color: var(--brand-dark); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 26px; margin-top: 22px;
  padding-top: 20px; border-top: 1px solid var(--line); }

/* ===== 5 交叉表 ===== */
.tblwrap { overflow-x: auto; }
.cross table { width: 100%; border-collapse: collapse; font-size: 13px; }
.cross th, .cross td { padding: 9px 12px; border-top: 1px solid var(--line); text-align: right; }
.cross thead th { background: none !important; border-top: none; font-size: 11.5px; color: var(--muted) !important;
  font-weight: 600; }
.cross th:first-child, .cross .rowh { text-align: left; color: var(--ink-soft) !important; background: none !important; font-weight: 600; }
.cross td .cell { color: var(--ink); text-decoration: none; font-variant-numeric: tabular-nums; }
.cross td .cell:hover { color: var(--brand-dark); text-decoration: underline; }
.cross td .empty { color: var(--muted); }
.cross tbody tr:hover td { background: var(--sub) !important; }
.cross .gap { background: var(--warn-soft) !important; }
.cross th.gap, .cross .rowh.gap { color: var(--warn-text) !important; }
.cross td.gap .cell { color: var(--warn-text); }
.cross .sum th, .cross .sum td { border-top: 2px solid #d2dad7; font-weight: 600; }
.cross .tot { color: var(--ink); }

/* ===== 6 右欄：待處理（高度固定，資料再多也不撐長） ===== */
.ihead { display: flex; align-items: center; justify-content: space-between; }
.ihead .ck { margin-bottom: 0; }
.ibadge { font-size: 18px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.ok { font-size: 13px; color: var(--muted); padding: 12px 0 0; }
.ig { border-top: 1px solid var(--line); }
.ig:first-of-type { margin-top: 12px; }
.igrow { display: flex; align-items: center; gap: 10px; width: 100%; padding: 12px 10px; margin: 0 -10px;
  background: none; border: none; cursor: pointer; text-align: left; font-family: inherit; border-radius: 8px; }
.igrow:hover { background: var(--sub); }
.ign { flex: 1; min-width: 0; font-size: 13px; color: var(--ink-soft); }
.ign em { display: block; font-style: normal; font-size: 11.5px; color: var(--muted); line-height: 1.45; }
.igrow b { font-family: var(--disp); font-size: 19px; font-weight: 600; font-variant-numeric: tabular-nums; }
.igrow b.bad { color: var(--bad); } .igrow b.warn { color: var(--warn-text); } .igrow b.good { color: var(--brand-dark); }
.ot { font-size: 11px; color: var(--muted); white-space: nowrap; }
.igbody { background: var(--sub); border-radius: 10px; padding: 6px 10px; margin-bottom: 10px; }
.iitem { display: flex; align-items: baseline; gap: 10px; padding: 7px 0; text-decoration: none;
  color: var(--ink-soft); font-size: 12.5px; border-top: 1px solid var(--line); }
.iitem:first-child { border-top: none; }
.iitem:hover { color: var(--brand-dark); }
.iitem .ih { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.iitem .ii { color: var(--muted); }
.iitem .itm { color: var(--muted); font-size: 11px; }
.imore { display: block; padding: 8px 0 4px; font-size: 12px; color: var(--brand-dark); text-decoration: none; }
.imore:hover { text-decoration: underline; }
.ifoot { display: flex; gap: 8px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }
.btn-ghost { flex: 1; font-family: inherit; font-size: 12px; padding: 9px 12px; border-radius: 10px;
  border: 1px solid var(--border-strong); background: transparent; color: var(--ink-aux);
  text-decoration: none; text-align: center; cursor: pointer; }
.btn-ghost:hover { border-color: var(--brand); color: var(--brand-dark); }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }

/* 資料乾淨度 */
.cpct { font-size: 40px; font-weight: 600; letter-spacing: -2px; line-height: 1; color: var(--brand-dark); }
.cpct small { font-size: 18px; letter-spacing: 0; margin-left: 2px; }
.csub { font-size: 12px; color: var(--muted); margin: 6px 0 10px; }
.qrow { display: flex; align-items: center; gap: 10px; padding: 11px 10px; margin: 0 -10px;
  border-top: 1px solid var(--line); text-decoration: none; color: inherit; border-radius: 8px; }
.qrow:hover { background: var(--sub); }
.qk { flex: 1; min-width: 0; font-size: 13px; color: var(--ink-soft); }
.qk em { display: block; font-style: normal; font-size: 11px; color: var(--muted); }
.qrow b { font-family: var(--disp); font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
.qrow b.good { color: var(--brand-dark); } .qrow b.warn { color: var(--warn-text); }

/* 載入骨架（不用整頁 spinner） */
.skel { display: flex; flex-direction: column; gap: 18px; }
.sk { background: linear-gradient(90deg, var(--line), #f4f6f5, var(--line));
  background-size: 200% 100%; animation: sh 1.2s infinite; border-radius: 12px; }
.sk.h1 { height: 38px; width: 260px; }
.sk.band { height: 96px; }
.sk.big { height: 320px; }
@keyframes sh { to { background-position: -200% 0; } }

@media (max-width: 1280px) {
  .grid { grid-template-columns: 1fr; }
  .side { position: static; }
  .verdict { grid-template-columns: 1fr; gap: 18px; padding: 20px 0; }
  .verdict .v { border-left: none; }
  .hero { grid-template-columns: 1fr; }
}
/* ===== 體檢一句話 ===== */
.hbar { margin-bottom: 22px; padding: 18px 26px; }
.hb-main { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.hb-num { text-decoration: none; color: var(--ink); font-size: 14px; }
.hb-num b { font-family: var(--disp); font-size: 26px; font-weight: 600; letter-spacing: -1px;
  color: var(--warn-text); margin-right: 4px; }
.hb-num:hover b { color: var(--bad); }
.hb-sep { color: var(--border-strong); }
.hb-ok { font-size: 14px; color: var(--ink-aux); }
.hb-ok b { font-family: var(--disp); font-size: 26px; font-weight: 600; letter-spacing: -1px;
  color: var(--brand-dark); margin-right: 4px; }
.hb-unverified { font-size: 11.5px; color: var(--muted); margin: 8px 0 0; }
.hb-detail { font-size: 12.5px; color: var(--muted); margin-left: auto; }
.hb-detail b { font-family: var(--disp); font-size: 15px; }
.hb-detail b.bad { color: var(--bad); } .hb-detail b.warn { color: var(--warn-text); }
.hb-issues { margin-top: 10px; font-size: 12px; color: var(--muted);
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hb-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 11px;
  border-radius: 20px; border: 1px solid var(--border); color: var(--ink-aux);
  text-decoration: none; font-size: 12px; }
.hb-chip:hover { border-color: var(--brand); color: var(--brand-dark); }
</style>