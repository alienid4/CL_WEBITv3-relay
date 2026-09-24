<script setup lang="ts">
// 帳號盤點（稽核導向）。
//
// 版面順序刻意是「稽核發現 → 帳號清單」而不是反過來：
// 這個模組的產出是「哪幾條不合規」，清單只是中間產物。
// 先給清單、把發現藏在第二個分頁，等於要稽核員自己判——那就沒解決他的問題。
interface Finding {
  id: number; rule_id: string; label: string; severity: string; verdict: string
  law: string; username: string; ip: string; hostname: string | null
  asset_serial: string | null; detail: string; kind?: string
  gecos: string | null; note: string | null   // gecos=自動帳號備註；note=手動備註
  status: string; exempt_until: string | null; decided_by: string | null
  decided_at: string | null; contradiction: boolean
}
interface AccountRow {
  id: number; ip: string; hostname: string | null; asset_serial: string | null
  username: string; uid: number; kind: string; shell: string | null
  department?: string | null; contact?: string | null; custodian?: string | null   // 部門／窗口／保管者
  last_seen?: string | null                  // 盤點時間（這台最後一次收集到這個帳號）
  // 脈絡（後端 _add_context，跟盤點報告／漏斗同一套）
  system_id?: string | null; system?: string | null; owner_department?: string | null
  env_group?: string; os_type?: string; needs_action?: boolean; open_findings?: number
  gid?: number | null; home?: string | null; can_login?: number | null
  kind_override?: string | null              // 手動改的類型（有值＝已覆寫程式判定）
  kind_computed?: string                     // 程式原判（有覆寫時才帶）
  gecos: string | null                       // 帳號備註（/etc/passwd 第 5 欄，自動）
  note: string | null                        // 手動備註（稽核人員輸入）
  last_login: string | null; never_logged_in: number
  pw_status: string | null; pw_last_change: string | null
  pw_max_days: string | null; pw_expires: string | null
  pw_expiry_status: string                   // never/expired/valid/na/unknown（後端算好）
  is_sudoer: number; sudo_nopasswd: number; priv_groups: string | null
  authorized_keys: number | null; gone_at: string | null
}
interface Summary {
  has_data: boolean
  fail_high?: number; fail_medium?: number; fail_low?: number; unknown?: number
  accounts?: number; privileged?: number; humans?: number
  hosts_needing_root?: number
  failed_count?: number; host_count?: number; run_error?: string | null
  excluded?: string[]
  run?: { started_at: string; host_count: number; needs_root_count: number; trigger?: string }
}

const { apiFetch } = useApi()
const { showToast } = useToast()

// 合規表頁：預設就是整張表。稽核發現/帳號清單是次要分頁。
// 儀表板已獨立成 /accounts；從那邊點框框會帶 query 進來預選分頁＋套篩選。
// 2026-09-12：帳號儀表板併進來當第一個頁籤（摘要→點框下鑽到明細）；預設落在儀表板。
const tab = ref<'dashboard' | 'matrix' | 'findings' | 'accounts'>('dashboard')
const findings = ref<Finding[]>([])
const coverage = ref<{ managed_hosts: number; collected_hosts: number; collected_managed: number;
                       collected_outside: number; pct: number | null } | null>(null)
async function loadCoverage() {
  try { coverage.value = await apiFetch('/api/accounts/coverage') }
  catch (e: any) { console.warn('帳號收集涵蓋率載入失敗', e) }
}
onMounted(loadCoverage)
const accounts = ref<AccountRow[]>([])
const summary = ref<Summary | null>(null)
const thresholds = ref<Record<string, number>>({})
const rules = ref<{ id: string; label: string; law: string }[]>([])
const loading = ref(false)
const errorMessage = ref('')
const sevFilter = ref('')
const kindFilter = ref('')
const sudoerOnly = ref(false)
const hideBuiltin = ref(false)
const hiddenBuiltin = ref(0)
// 天條二：帳號是「身分型」值，點下去要看到這個帳號在所有主機上的紀錄——
// 之前這個連結指去 /accounts（彙總儀表板，完全不吃 user 參數），是個死連結。
// 改成本頁自篩選：三個分頁（矩陣/發現/帳號清單）都吃同一個 usernameFilter。
const usernameFilter = ref('')
// 從儀表板的主機清單點進來（?ip=）：只看那一台（2026-09-16 使用者：
// 「應該是先列出哪幾台，我再進去點後才是那一台的全部帳號資訊」）
const ipFilter = ref('')

function byIp<T extends { ip?: string }>(rows: T[]): T[] {
  return ipFilter.value ? rows.filter((r) => r.ip === ipFilter.value) : rows
}
const fRows = computed(() => byIp(
  usernameFilter.value ? findings.value.filter((f) => f.username === usernameFilter.value) : findings.value))
const aRows = computed(() => byIp(
  usernameFilter.value ? accounts.value.filter((a) => a.username === usernameFilter.value) : accounts.value))
const { sortKey: fKey, sortDir: fDir, toggle: fToggle, sorted: fSorted } = useSort(fRows, 'severity')
const { sortKey: aKey, sortDir: aDir, toggle: aToggle, sorted: aSorted } = useSort(aRows, 'ip')

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [f, a] = await Promise.all([
      apiFetch<{ items: Finding[]; summary: Summary; rules: any[]; thresholds: any }>(
        '/api/accounts/findings', { query: { severity: sevFilter.value || undefined } }),
      apiFetch<{ items: AccountRow[]; hidden_builtin: number }>('/api/accounts', {
        query: {
          kind: kindFilter.value || undefined,
          sudoer_only: sudoerOnly.value,
          hide_builtin: hideBuiltin.value,
        },
      }),
    ])
    findings.value = f.items
    summary.value = f.summary
    thresholds.value = f.thresholds
    rules.value = f.rules
    accounts.value = a.items
    hiddenBuiltin.value = a.hidden_builtin ?? 0
  } catch {
    errorMessage.value = '帳號資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
// 先套「影響載入」的 query（分頁/風險/類型/特權），初次 load 就用對的參數，
// SSR 與 client 首屏才一致（否則 watch 只在 client reload → hydration mismatch）。
const route = useRoute()
const router = useRouter()
function readLoadQuery() {
  const q = route.query
  if (q.tab === 'findings' || q.tab === 'accounts' || q.tab === 'matrix' || q.tab === 'dashboard') tab.value = q.tab as any
  if (typeof q.sev === 'string') sevFilter.value = q.sev
  if (typeof q.kind === 'string') kindFilter.value = q.kind
  if (q.sudoer === '1') { sudoerOnly.value = true; tab.value = 'accounts' }
  usernameFilter.value = typeof q.user === 'string' ? q.user : ''
  ipFilter.value = typeof q.ip === 'string' ? q.ip : ''
  // 指定了主機就直接進「帳號清單」——從主機清單點進來就是要看那台的帳號
  if (ipFilter.value && !q.tab) tab.value = 'accounts'
}

// 查主機：以「這一輪盤點到的主機」為母體比對——打了查不到要講清楚是「沒收到」不是「沒帳號」
const hostFindKw = ref('')
const invHosts = ref<{ ip: string; hostname: string | null }[]>([])
onMounted(async () => {
  try {
    const r = await apiFetch<{ items: any[] }>('/api/accounts/inventoried-hosts')
    invHosts.value = r.items ?? []
  } catch { /* 查主機是輔助功能，載不到就退回用下面的清單找 */ }
})
const hostFindHits = computed(() => {
  const q = hostFindKw.value.trim().toLowerCase()
  if (!q) return []
  return invHosts.value.filter((h) => (h.ip || '').toLowerCase().includes(q)
    || (h.hostname || '').toLowerCase().includes(q))
})
// 反查帳號（2026-09-17 使用者：「帳號 我想要找 例如 webit3 有哪幾台有這帳號」）。
// 這是跟「主機→帳號」相反的問法：停用離職者帳號、確認服務帳號佈到哪些機器都從這裡開始。
interface AcctHost {
  ip: string; hostname: string | null; asset_serial: string | null
  environment: string | null; physical_location: string | null
  department: string | null; contact: string | null; custodian: string | null
  username: string; uid: number | null; kind: string | null
  is_sudoer: number; sudo_nopasswd: number; never_logged_in: number
  last_login: string | null; pw_status: string | null; registered: boolean
}
const acctHosts = ref<AcctHost[] | null>(null)
const acctName = ref('')
const acctBusy = ref(false)
const acctErr = ref('')
const { sortKey: ahKey, sortDir: ahDir, toggle: ahToggle, sorted: ahSorted } =
  useSort(computed(() => acctHosts.value ?? []), 'ip')

async function findAccount(name?: string) {
  const u = (name ?? hostFindKw.value).trim()
  if (!u) return
  acctBusy.value = true
  acctErr.value = ''
  acctName.value = u
  try {
    const r = await apiFetch<{ items: AcctHost[] }>('/api/accounts/by-username', { query: { username: u } })
    acctHosts.value = r.items ?? []
  } catch (e: any) {
    acctHosts.value = []
    acctErr.value = e?.data?.detail ?? e?.message ?? '查詢失敗'
  } finally {
    acctBusy.value = false
  }
}

function goHost(ip?: string) {
  const target = ip || (hostFindHits.value.length === 1 ? hostFindHits.value[0].ip : hostFindKw.value.trim())
  if (!target) return
  router.push({ path: '/account-matrix', query: { ip: target, tab: 'accounts' } })
  hostFindKw.value = ''
}

function clearIpFilter() {
  const query: any = { ...route.query }
  delete query.ip
  router.replace({ path: '/account-matrix', query })
}
function clearUsernameFilter() {
  const query: any = { ...route.query }
  delete query.user
  router.replace({ path: '/account-matrix', query })
}
readLoadQuery()
await load()
watch([sevFilter, kindFilter, sudoerOnly, hideBuiltin], load)

// 發現生命週期：處置狀態
const STATUS_OPTS = [
  { v: 'open', t: '待處理' }, { v: 'ack', t: '已確認' },
  { v: 'exception', t: '核准例外' }, { v: 'fixed', t: '已修復' },
]
const STATUS_LABEL: Record<string, string> = {
  open: '待處理', ack: '已確認', exception: '核准例外', fixed: '已修復',
}
async function setDisposition(f: Finding, status: string, exempt_until?: string) {
  try {
    await apiFetch('/api/accounts/findings/disposition', {
      method: 'PUT',
      body: { ip: f.ip, username: f.username, rule_id: f.rule_id, status, exempt_until },
    })
    await load()
  } catch (e: any) {
    showToast(`設定失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  }
}
async function onStatusChange(f: Finding, status: string) {
  if (status === 'exception') {
    const d = window.prompt('核准例外到期日（YYYY-MM-DD，到期自動回待處理）：', '')
    if (!d) return
    await setDisposition(f, 'exception', d)
  } else {
    await setDisposition(f, status)
  }
}

// 依「項目」分組（同一類問題收成一組 + 計數）vs 平鋪
const groupByLabel = ref(false)
const groupedFindings = computed(() => {
  const g = new Map<string, Finding[]>()
  for (const f of fSorted.value) {
    if (!g.has(f.label)) g.set(f.label, [])
    g.get(f.label)!.push(f)
  }
  return [...g.entries()].map(([label, items]) => ({ label, items }))
})
const openGroups = ref<Set<string>>(new Set())
function toggleGroup(label: string) {
  if (openGroups.value.has(label)) openGroups.value.delete(label)
  else openGroups.value.add(label)
  openGroups.value = new Set(openGroups.value)
}

function exportFindings() {
  const base = (useRuntimeConfig().public as any).apiBase || ''
  window.open(`${base}/api/accounts/findings/export`, '_blank')
}

const SEV_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }
const KIND_LABEL: Record<string, string> = {
  human: '真人', service: '服務帳號', default: '系統預設', mgmt: '標準管理帳號',
  builtin: '內建帳號',
}

// 密碼到期：明講「已過期/未過期/永不過期」，後端算好狀態，不只寫項目名稱
const PW_EXPIRY: Record<string, { text: string; cls: string }> = {
  never: { text: '永不過期', cls: 'danger' },
  expired: { text: '已過期', cls: 'danger' },
  valid: { text: '未過期', cls: 'ok' },
  na: { text: '—', cls: 'dim' },
  unknown: { text: '需 root', cls: 'needroot' },
}
function pwExpiry(a: AccountRow): { text: string; cls: string } {
  return PW_EXPIRY[a.pw_expiry_status] || { text: '—', cls: 'dim' }
}

// ===== 合規矩陣：一欄一種狀態，一眼掃過去 =====
// 每個函式回 { t 顯示字, c 顏色, k 分類鍵 }。k 是漏斗篩選與排序共用的穩定 token。
// c: bad(紅=有問題) / warn(黃=注意) / ok(綠=正常) / dim(灰=無/不適用) / needroot(藍=查不到)
type Cell = { t: string; c: string; k: string }
function mPwExpired(a: AccountRow): Cell {
  const s = a.pw_expiry_status
  if (s === 'expired') return { t: '已過期', c: 'bad', k: 'expired' }
  if (s === 'never') return { t: '永不過期', c: 'bad', k: 'never' }
  if (s === 'valid') return { t: '未過期', c: 'ok', k: 'valid' }
  if (s === 'unknown') return { t: '需 root', c: 'needroot', k: 'needroot' }
  return { t: '—', c: 'dim', k: 'na' }
}
// passwd -S 狀態碼跨發行版有 set/PS/P、locked/LK/L、empty/NP 多種寫法，
// 這裡收斂成 set/locked/empty/null，前端才不會把 LK 當成「沒鎖」。
function pwState(a: AccountRow): 'set' | 'locked' | 'empty' | null {
  const s = (a.pw_status || '').toUpperCase()
  if (s === '') return null
  if (s === 'LOCKED' || s === 'LK' || s === 'L') return 'locked'
  if (s === 'EMPTY' || s === 'NP') return 'empty'
  if (s === 'SET' || s === 'PS' || s === 'P') return 'set'
  return null   // 未知碼＝查不到，寧可標需 root 也不謊報啟用中
}
function mDisabled(a: AccountRow): Cell {
  const s = pwState(a)
  if (s === 'locked') return { t: '已停用', c: 'ok', k: 'locked' }
  if (s === null) return { t: '需 root', c: 'needroot', k: 'needroot' }
  return { t: '啟用中', c: '', k: 'active' }
}
function mSudo(a: AccountRow): Cell {
  if (a.uid === 0) return { t: 'UID 0', c: 'bad', k: 'uid0' }
  if (a.sudo_nopasswd) return { t: '是·免密碼', c: 'bad', k: 'nopw' }
  if (a.is_sudoer) return { t: '是', c: 'warn', k: 'yes' }
  return { t: '否', c: 'dim', k: 'no' }
}
function mEmpty(a: AccountRow): Cell {
  const s = pwState(a)
  if (s === null) return { t: '需 root', c: 'needroot', k: 'needroot' }
  if (s === 'empty') return { t: '是', c: 'bad', k: 'yes' }
  return { t: '否', c: 'dim', k: 'no' }
}
function mUid0(a: AccountRow): Cell {
  if (a.uid === 0 && a.username !== 'root') return { t: '是', c: 'bad', k: 'yes' }
  if (a.uid === 0) return { t: 'root', c: 'dim', k: 'root' }
  return { t: '否', c: 'dim', k: 'no' }
}
function mKeys(a: AccountRow): Cell {
  if (a.authorized_keys === null || a.authorized_keys === undefined) return { t: '需 root', c: 'needroot', k: 'needroot' }
  if (a.authorized_keys > 0) return { t: `${a.authorized_keys} 把`, c: 'warn', k: 'has' }
  return { t: '無', c: 'dim', k: 'none' }
}
function mNeverLogin(a: AccountRow): Cell {
  if (a.never_logged_in) return { t: '從未登入', c: 'warn', k: 'never' }
  return { t: '有', c: 'dim', k: 'yes' }
}

// 漏斗篩選：每個狀態欄的可選值（k → 顯示字）。'' = 全部。
const MATRIX_COLS = [
  { key: 'pwExpired', label: '密碼過期', fn: mPwExpired,
    opts: [['expired', '已過期'], ['never', '永不過期'], ['valid', '未過期'], ['needroot', '需 root'], ['na', '—']] },
  { key: 'disabled', label: '帳號停用', fn: mDisabled,
    opts: [['active', '啟用中'], ['locked', '已停用'], ['needroot', '需 root']] },
  { key: 'sudo', label: 'sudo 權限', fn: mSudo,
    opts: [['uid0', 'UID 0'], ['nopw', '免密碼'], ['yes', '是'], ['no', '否']] },
  { key: 'empty', label: '空密碼', fn: mEmpty,
    opts: [['yes', '是'], ['no', '否'], ['needroot', '需 root']] },
  { key: 'uid0', label: 'UID 0', fn: mUid0,
    opts: [['yes', '是'], ['root', 'root'], ['no', '否']] },
  { key: 'keys', label: '免密碼金鑰', fn: mKeys,
    opts: [['has', '有'], ['none', '無'], ['needroot', '需 root']] },
  { key: 'login', label: '曾登入', fn: mNeverLogin,
    opts: [['never', '從未登入'], ['yes', '有']] },
] as const
const colFilter = reactive<Record<string, string>>({
  pwExpired: '', disabled: '', sudo: '', empty: '', uid0: '', keys: '', login: '',
})
function clearColFilters() {
  for (const c of MATRIX_COLS) colFilter[c.key] = ''
}
const colFilterActive = computed(() => MATRIX_COLS.some(c => colFilter[c.key] !== ''))

// 每列先算好 7 個狀態鍵，篩選＋排序共用；避免模板反覆呼叫函式。
// 用 aRows（已套 usernameFilter）不是原始 accounts，矩陣分頁才會一起吃帳號篩選。
const matrixData = computed(() => aRows.value.map(a => {
  const row: any = { ...a }
  for (const c of MATRIX_COLS) row['_' + c.key] = c.fn(a).k
  return row
}))
const matrixFiltered = computed(() => matrixData.value.filter(r =>
  MATRIX_COLS.every(c => !colFilter[c.key] || r['_' + c.key] === colFilter[c.key])))
// ===== 篩選與搜尋（2026-09-18）=====
// 使用者：「盤點帳號跟機房無關，跟業務系統才有關係，或是要找『李泰益』有哪些帳號」
// 「搜尋你拷問自己，做一個管理者方便查詢的搜尋器」。自我拷問的結論：
//  1. 「李泰益」有兩種意思——他是窗口／保管者的機器上的帳號，或帳號本身是他的（備註有他）
//     → 每筆標出命中哪一欄
//  2. 會打半個名字、IP 前段、系統代碼 → 搜遍主機／IP／帳號／備註／部門／窗口／保管者／AP ID／系統
//     多個詞（空白隔開）要全部符合
//  3. 十幾萬個帳號全畫出來會卡（漏斗的教訓）→ 先畫 300 列、打字停 250ms 才算
//  6. 0 筆要分得出「真的沒有」與「他負責的機器還沒收集帳號」→ 0 筆時去資產庫查
const ENV_LABEL: Record<string, string> = { prod: '正式', nonprod: '非正式', oa: 'OA', unknown: '未知環境', unset: '未填環境' }
const onlyTodo = ref(false)
const envSel = ref('')
const osSel = ref('')
const sysSel = ref('')
function sysList(r: AccountRow): string[] { return (r.system_id || '').split('、').filter(Boolean) }
const optCounts = computed(() => {
  const env = new Map<string, number>(); const os = new Map<string, number>()
  const sys = new Map<string, { n: number; name: string }>()
  const names = (r: AccountRow) => (r.system || '').split('、')
  for (const r of matrixFiltered.value as AccountRow[]) {
    const e = r.env_group || 'unset'; env.set(e, (env.get(e) || 0) + 1)
    const o = r.os_type || '未填'; os.set(o, (os.get(o) || 0) + 1)
    sysList(r).forEach((id, i) => {
      const cur = sys.get(id) || { n: 0, name: names(r)[i] || '' }
      cur.n++; sys.set(id, cur)
    })
  }
  return {
    env: [...env.entries()].sort((a, b) => b[1] - a[1]),
    os: [...os.entries()].sort((a, b) => b[1] - a[1]),
    sys: [...sys.entries()].sort((a, b) => a[0].localeCompare(b[0])),
  }
})
const SEARCH_FIELDS: [keyof AccountRow, string][] = [
  ['hostname', '主機'], ['ip', 'IP'], ['username', '帳號'], ['gecos', '備註'], ['note', '手動備註'],
  ['department', '部門'], ['contact', '窗口'], ['custodian', '保管者'], ['system_id', 'AP ID'],
  ['system', '系統'], ['asset_serial', '序號'],
]
const mkwInput = ref('')
const mkw = ref('')
let kwTimer: ReturnType<typeof setTimeout> | undefined
watch(mkwInput, (v) => { clearTimeout(kwTimer); kwTimer = setTimeout(() => { mkw.value = v }, 250) })
/** 每個詞都要有欄位命中；回命中的欄位名（去重），有詞沒命中回 null */
function matchInfo(r: AccountRow, terms: string[]): string[] | null {
  const hit: string[] = []
  for (const t of terms) {
    const fs = SEARCH_FIELDS.filter(([k]) => String(r[k] ?? '').toLowerCase().includes(t)).map(([, l]) => l)
    if (!fs.length) return null
    fs.forEach((l) => { if (!hit.includes(l)) hit.push(l) })
  }
  return hit
}
const mRows = computed(() => {
  let rows = matrixFiltered.value as (AccountRow & { _hit?: string[] })[]
  if (onlyTodo.value) rows = rows.filter((r) => r.needs_action)
  if (envSel.value) rows = rows.filter((r) => (r.env_group || 'unset') === envSel.value)
  if (osSel.value) rows = rows.filter((r) => (r.os_type || '未填') === osSel.value)
  if (sysSel.value) rows = rows.filter((r) => sysList(r).includes(sysSel.value))
  const terms = mkw.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return rows
  const out: (AccountRow & { _hit?: string[] })[] = []
  for (const r of rows) {
    const m = matchInfo(r, terms)
    if (m) out.push({ ...r, _hit: m })
  }
  return out
})
const hitSummary = computed(() => {
  const c = new Map<string, number>()
  for (const r of mRows.value) (r._hit || []).forEach((l) => c.set(l, (c.get(l) || 0) + 1))
  return [...c.entries()].sort((a, b) => b[1] - a[1])
})
const mFilterActive = computed(() => onlyTodo.value || !!envSel.value || !!osSel.value || !!sysSel.value || !!mkwInput.value)
function clearMFilters() {
  onlyTodo.value = false; envSel.value = ''; osSel.value = ''; sysSel.value = ''
  mkwInput.value = ''; mkw.value = ''; clearColFilters()
}
const { sortKey: mKey, sortDir: mDir, toggle: mToggle, sorted: mSorted } = useSort(mRows, 'hostname')
// 效能：先畫前 300 列（全選／匯出不受影響）
const M_CAP = 300
const showAllM = ref(false)
const mShown = computed(() => (showAllM.value ? mSorted.value : mSorted.value.slice(0, M_CAP)))
watch(mSorted, () => { showAllM.value = false })
// 0 筆時：這個詞在資產庫對得到幾台、幾台還沒收集帳號
const explain = ref<{ machines: number; collected: number; not_collected: string[]; not_collected_count: number; error?: string } | null>(null)
watch([mkw, () => mRows.value.length], async ([kw, n]) => {
  explain.value = null
  if (!String(kw).trim() || n > 0) return
  try {
    explain.value = await apiFetch('/api/accounts/search-explain', { query: { q: kw } })
  } catch (e: any) {
    explain.value = { machines: 0, collected: 0, not_collected: [], not_collected_count: 0,
                      error: e?.data?.detail ?? e?.message ?? String(e) }
  }
})

// 儀表板點框框帶進來的 query，套上欄位漏斗篩選（col+val）；其餘（tab/sev/kind/sudoer）
// 已在 load 前的 readLoadQuery 處理。colFilter 在 setup 同步套，SSR/client 首屏一致。
function applyColQuery() {
  const q = route.query
  if (typeof q.col === 'string' && typeof q.val === 'string' && q.col in colFilter) {
    clearColFilters()
    colFilter[q.col] = q.val
    tab.value = 'matrix'
  }
}
applyColQuery()
// client 端 query 變動（同頁再點不同框）時，兩段都重套。
watch(() => route.query, () => { readLoadQuery(); applyColQuery() })

// ===== 固定格式匯出：公司標準帳號盤點 ／ 全匯出（使用者 2026-08-28 指定）=====
// 跟下面「可選欄位匯出」是兩件事：那個是自己勾要給稽核看什麼；
// 這兩個是固定格式——標準格式直接交出去，全匯出給自己人查。
const runtimeConfig = useRuntimeConfig()
const exporting = ref('')

async function exportFixed(fmt: 'standard' | 'full') {
  if (exporting.value) return
  exporting.value = fmt
  try {
    const res = await fetch(
      `${runtimeConfig.public.apiBase}/api/accounts/export?fmt=${fmt}`,
      { credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m ? decodeURIComponent(m[1]) : `帳號盤點_${fmt}.xlsx`
    a.click()
    URL.revokeObjectURL(url)

    // 只給一個檔案而不講「其中 N 筆的類型要人工填」，人會以為它是完整的。
    const raw = res.headers.get('X-Export-Summary')
    if (raw && fmt === 'standard') {
      const sum = JSON.parse(decodeURIComponent(raw))
      const bits = [`${sum.rows} 筆`]
      if (sum.unclassified_type) bits.push(`${sum.unclassified_type} 筆的類型要人工填`)
      if (sum.rows_without_system_name) bits.push(`${sum.rows_without_system_name} 筆查不到系統名稱`)
      showToast(bits.join('，'), sum.unclassified_type ? 'warn' : 'success')
    } else {
      showToast('匯出完成', 'success')
    }
  } catch (e: any) {
    showToast(`匯出失敗：${e?.message || e}`, 'error')
  } finally {
    exporting.value = ''
  }
}

// ===== 可選欄位匯出：稽核只要看的欄位，資料最小化 =====
const EXPORT_COLS = [
  { key: 'hostname', label: '主機' }, { key: 'ip', label: 'IP' },
  { key: 'department', label: '部門（使用單位）' }, { key: 'contact', label: '窗口（使用者）' }, { key: 'custodian', label: '保管者' },
  { key: 'username', label: '帳號' }, { key: 'collected', label: '盤點時間' },
  { key: 'note', label: '備註' }, { key: 'kind', label: '類型' },
  { key: 'pwExpired', label: '密碼過期' }, { key: 'disabled', label: '帳號停用' },
  { key: 'sudo', label: 'sudo 權限' }, { key: 'empty', label: '空密碼' },
  { key: 'uid0', label: 'UID 0' }, { key: 'keys', label: '免密碼金鑰' }, { key: 'login', label: '曾登入' },
]
// 部門／窗口預設就勾：這張表拿去盤點，第一件事就是知道要找誰確認（使用者 2026-09-18）
const EXPORT_DEFAULT = ['hostname', 'ip', 'department', 'contact', 'username', 'collected', 'note', 'pwExpired', 'disabled', 'sudo']
const showExport = ref(false)
const exportSel = reactive<Record<string, boolean>>(
  Object.fromEntries(EXPORT_COLS.map(c => [c.key, EXPORT_DEFAULT.includes(c.key)])))
function doExport() {
  const cols = EXPORT_COLS.filter(c => exportSel[c.key]).map(c => c.key)
  if (!cols.length) { showToast('至少要選一個欄位', 'warn'); return }
  const base = (useRuntimeConfig().public as any).apiBase || ''
  const qs = new URLSearchParams()
  cols.forEach(c => qs.append('cols', c))
  if (kindFilter.value) qs.append('kind', kindFilter.value)
  if (hideBuiltin.value) qs.append('hide_builtin', 'true')
  window.open(`${base}/api/accounts/matrix/export?${qs.toString()}`, '_blank')
  showExport.value = false
}

// 手動備註編輯（稽核人員輸入；跟 gecos 自動備註不同）
const editingNote = ref('')          // 正在編輯的 key = ip|username
const noteDraft = ref('')
function noteKey(ip: string, username: string) { return `${ip}|${username}` }
function startEditNote(ip: string, username: string, cur: string | null) {
  editingNote.value = noteKey(ip, username)
  noteDraft.value = cur || ''
}
async function saveNote(ip: string, username: string) {
  try {
    await apiFetch('/api/accounts/note', {
      method: 'PUT', body: { ip, username, note: noteDraft.value },
    })
    editingNote.value = ''
    await load()
    showToast('備註已儲存', 'success')
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  }
}

// 手動改類型（程式判錯時；例如 CBoss 被判成內建帳號、實際是服務帳號）。收集不覆寫。
const editingKind = ref('')
function startEditKind(ip: string, username: string) { editingKind.value = noteKey(ip, username) }
async function saveKind(ip: string, username: string, kind: string) {
  try {
    await apiFetch('/api/accounts/kind', { method: 'PUT', body: { ip, username, kind } })
    editingKind.value = ''
    await load()   // 改類型會連動規則適用（例如改成服務帳號後密碼到期變不適用），重載才準
    showToast('類型已更新', 'success')
  } catch (e: any) {
    showToast(`更新失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  }
}
</script>

<template>
  <div>
    <div class="section-divider">帳號合規表</div>

    <p class="lead">一列一個帳號、一欄一種合規狀態
      <InfoNote>要看彙總數字到「帳號儀表板」；要收資料到「盤點作業」。</InfoNote></p>

    <!-- 使用者 2026-09-11：表上看不出上次盤點是什麼時候。資料新鮮度是稽核的前提，放最上面。 -->
    <p v-if="summary?.has_data && summary.run" class="last-run">
      最新盤點：<b>{{ summary.run.started_at }}</b>
      <span class="lr-meta">（{{ summary.run.trigger === 'schedule' ? '每晚排程' : '手動' }}，
        {{ summary.run.host_count }} 台<template v-if="summary.failed_count">，其中 {{ summary.failed_count }} 台收集失敗</template>）</span>
    </p>
    <p v-else-if="summary && !summary.has_data" class="last-run">最新盤點：尚未盤點</p>
    <!-- 收集涵蓋率（2026-09-18 十項盤點第 9 條）：「沒有資料」跟「資料很少」要分得開 -->
    <p v-if="coverage" class="last-run cov" :class="{ low: (coverage.pct ?? 0) < 50 }">
      帳號收集涵蓋：在管 <b>{{ coverage.managed_hosts.toLocaleString() }}</b> 台中已收 <b>{{ coverage.collected_managed.toLocaleString() }}</b> 台
      <template v-if="coverage.pct !== null">（{{ coverage.pct }}%）</template>
      <span v-if="coverage.collected_outside" class="lr-meta">；另有 {{ coverage.collected_outside }} 台收到了但不在在管清單（帳外／未登記）</span>
      <NuxtLink v-if="(coverage.pct ?? 0) < 100" to="/pipeline" class="cov-link">看還沒收的 →</NuxtLink>
      <InfoNote>下面的合規表、稽核發現、帳號盤點都<b>只包含已收集的主機</b>。涵蓋率低時，「帳號很少」不代表公司帳號少，是還沒收集——還沒收的主機在納管漏斗（未納管／還沒盤點帳號那幾關）。</InfoNote>
    </p>

    <div class="tabs">
      <div class="tab" :class="{ active: tab === 'dashboard' }" @click="tab = 'dashboard'">
        儀表板
      </div>
      <div class="tab" :class="{ active: tab === 'matrix' }" @click="tab = 'matrix'">
        合規表 {{ accounts.length }}
      </div>
      <div class="tab" :class="{ active: tab === 'findings' }" @click="tab = 'findings'">
        稽核發現 {{ findings.length }}
      </div>
      <div class="tab" :class="{ active: tab === 'accounts' }" @click="tab = 'accounts'">
        帳號清單 {{ accounts.length }}
      </div>
    </div>

    <!-- 查主機（2026-09-17 使用者：「這個畫面 我還是不能快速找哪一台主機的資訊」「譬如查10.92.198.14」）。
         這頁四個分頁都以「帳號」為單位平鋪，要看某一台得自己用眼睛掃。
         這個框接受 IP 或主機名，按 Enter 直接進那一台的帳號明細。 -->
    <div class="hostfind">
      <!-- [2026-09-20 公司驗收] 這一格跟下面的表格搜尋長得太像：使用者把 svc 打進來，
           表格沒反應（仍 219/219），只跳一句提示。名字與提示字都改得一眼分得出用途。 -->
      <span class="hf-lbl">跳到某一台／某個帳號</span>
      <input v-model="hostFindKw" class="hf-in"
             placeholder="打 IP／主機名跳到那一台；打帳號名查它在哪幾台（不會篩下面的表——要篩表請用下面那格）"
             @keyup.enter="goHost()" />
      <button class="btn small primary" type="button" :disabled="!hostFindKw.trim()" @click="goHost()">查主機</button>
      <button class="btn small" type="button" :disabled="!hostFindKw.trim()" @click="findAccount()">
        查帳號在哪幾台
      </button>
      <span v-if="hostFindHits.length > 1" class="hf-hits">
        <button v-for="h in hostFindHits.slice(0, 6)" :key="h.ip" type="button" class="hf-hit"
                @click="goHost(h.ip)">{{ h.ip }} <span class="dim">{{ h.hostname || '' }}</span></button>
        <span v-if="hostFindHits.length > 6" class="dim sm">…共 {{ hostFindHits.length }} 台</span>
      </span>
      <span v-else-if="hostFindKw && !hostFindHits.length" class="hf-none">
        帳號盤點裡沒有這台——代表<b>這一輪沒收到它的帳號</b>（可能還沒納管、收不到、或被排除），
        不是「這台沒有帳號」。
      </span>
    </div>

    <!-- 帳號反查結果：一列一台，帶部門與窗口（下一步就是去找人） -->
    <section v-if="acctHosts !== null" class="acctres">
      <div class="ar-head">
        帳號 <b class="mono">{{ acctName }}</b>
        <template v-if="acctBusy">　查詢中…</template>
        <template v-else>　在 <b>{{ acctHosts.length }}</b> 台上</template>
        <span class="spacer" />
        <a v-if="acctHosts.length" class="btn small"
           :href="`${runtimeConfig.public.apiBase}/api/accounts/by-username/export?username=${encodeURIComponent(acctName)}`">⬇ 匯出 Excel</a>
        <button class="btn small ghost" type="button" @click="acctHosts = null">關閉</button>
      </div>
      <p v-if="acctErr" class="error-text">✕ {{ acctErr }}（這是查詢出錯，不是「沒有這個帳號」）</p>
      <p v-else-if="!acctHosts.length" class="muted">
        這一輪盤點到的主機上<b>沒有</b>叫「{{ acctName }}」的帳號。
        注意：沒盤點到的主機不在比對範圍內——上面的主機清單才是母體。
      </p>
      <div v-else class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="ip" :active="ahKey" :dir="ahDir" @sort="ahToggle">IP</SortTh>
            <SortTh k="hostname" :active="ahKey" :dir="ahDir" @sort="ahToggle">主機名稱</SortTh>
            <SortTh k="physical_location" :active="ahKey" :dir="ahDir" @sort="ahToggle">機房</SortTh>
            <SortTh k="environment" :active="ahKey" :dir="ahDir" @sort="ahToggle">環境</SortTh>
            <SortTh k="department" :active="ahKey" :dir="ahDir" @sort="ahToggle">使用單位（部門）</SortTh>
            <SortTh k="contact" :active="ahKey" :dir="ahDir" @sort="ahToggle">使用者（窗口）</SortTh>
            <SortTh k="custodian" :active="ahKey" :dir="ahDir" @sort="ahToggle">保管者</SortTh>
            <SortTh k="uid" :active="ahKey" :dir="ahDir" class="num" @sort="ahToggle">UID</SortTh>
            <SortTh k="is_sudoer" :active="ahKey" :dir="ahDir" @sort="ahToggle">sudo</SortTh>
            <SortTh k="last_login" :active="ahKey" :dir="ahDir" @sort="ahToggle">最後登入</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="h in ahSorted" :key="h.ip">
              <td class="mono">
                <NuxtLink class="dl" :to="{ path: '/account-matrix', query: { ip: h.ip, tab: 'accounts' } }">{{ h.ip }}</NuxtLink>
                <span v-if="!h.registered" class="tag bad" title="收到帳號，但資產庫查不到這個 IP">未登記</span>
              </td>
              <td>
                <NuxtLink v-if="h.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(h.asset_serial)}`">{{ h.hostname || '—' }}</NuxtLink>
                <template v-else>{{ h.hostname || '—' }}</template>
              </td>
              <td>{{ h.physical_location || '—' }}</td>
              <td>{{ h.environment || '—' }}</td>
              <td>{{ h.department || '—' }}</td>
              <td>{{ h.contact || '—' }}</td>
              <td>{{ h.custodian || '—' }}</td>
              <td class="num mono">{{ h.uid ?? '—' }}</td>
              <td>
                <span v-if="h.sudo_nopasswd" class="tag bad" title="免密碼 sudo">免密碼</span>
                <span v-else-if="h.is_sudoer" class="tag warn">有</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="sm">{{ h.never_logged_in ? '從未登入' : (h.last_login || '—') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div v-if="ipFilter" class="filter-chip">
      <span class="fc-dot" />
      <span class="fc-text">只看這台 · {{ ipFilter }}（{{ aRows.length }} 個帳號、{{ fRows.length }} 個發現）</span>
      <button class="fc-clear" type="button" @click="clearIpFilter">看全部 ✕</button>
    </div>

    <div v-if="usernameFilter" class="filter-chip">
      <span class="fc-dot" />
      <span class="fc-text">已篩選帳號 · {{ usernameFilter }}</span>
      <button class="fc-clear" type="button" @click="clearUsernameFilter">清除篩選 ✕</button>
    </div>

    <!-- 儀表板：併進來的第一頁籤，自己載自己的資料（不吃合規表的 loading） -->
    <AccountDashboard v-if="tab === 'dashboard'" />

    <p v-if="tab !== 'dashboard' && errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-else-if="tab !== 'dashboard' && loading" class="muted">載入中…</p>

    <!-- 合規表：一列一帳號、一欄一狀態，可排序＋漏斗篩選 -->
    <template v-else-if="tab === 'matrix'">
      <p v-if="accounts.length === 0" class="muted">
        沒有帳號資料，到 <NuxtLink class="dl" to="/account-ops">盤點作業</NuxtLink> 收一輪。
      </p>
      <!-- 篩選列（2026-09-18）：跟業務系統有關、跟機房無關（使用者） -->
      <div v-if="accounts.length" class="bar mfilter">
        <label class="chk"><input v-model="onlyTodo" type="checkbox" />只看還需要處理的
          <InfoNote>有失敗的稽核發現、而且處置是「待處理」或「已確認」（已確認＝看過、還沒改好）。核准例外、已修復不算。</InfoNote></label>
        <select v-model="envSel" class="sel" :class="{ on: envSel }">
          <option value="">環境別：全部</option>
          <option v-for="[k, n] in optCounts.env" :key="k" :value="k">{{ ENV_LABEL[k] || k }}（{{ n }}）</option>
        </select>
        <select v-model="osSel" class="sel" :class="{ on: osSel }">
          <option value="">OS 類型：全部</option>
          <option v-for="[k, n] in optCounts.os" :key="k" :value="k">{{ k }}（{{ n }}）</option>
        </select>
        <select v-model="sysSel" class="sel sys-sel" :class="{ on: sysSel }">
          <option value="">業務系統：全部</option>
          <option v-for="[id, v] in optCounts.sys" :key="id" :value="id">{{ id }} {{ v.name }}（{{ v.n }}）</option>
        </select>
        <input v-model="mkwInput" type="search" class="sel mkw"
               placeholder="篩下面這張表：人名、部門、主機、IP、帳號、AP ID、系統…（空白隔開＝都要符合）" />
        <button v-if="mFilterActive || colFilterActive" class="btn small" type="button" @click="clearMFilters">清空篩選</button>
      </div>
      <p v-if="accounts.length && mkw.trim() && hitSummary.length" class="hit-sum">
        「{{ mkw.trim() }}」符合 {{ mRows.length }} 筆，命中欄位：
        <span v-for="[l, n] in hitSummary" :key="l" class="hit-chip">{{ l }} {{ n }}</span>
      </p>
      <p v-if="accounts.length && mkw.trim() && !mRows.length" class="hit-empty">
        「{{ mkw.trim() }}」在目前的帳號資料裡 <b>0 筆</b>。
        <template v-if="explain?.error">（查資產庫失敗：{{ explain.error }}）</template>
        <template v-else-if="explain && explain.machines">
          但資產庫裡有 <b>{{ explain.machines }}</b> 台機器對得到（主機名／IP／部門／窗口／保管者／系統），
          其中 <b class="c-warn">{{ explain.not_collected_count }}</b> 台<b>還沒收集帳號</b>
          <span v-if="explain.not_collected.length">：{{ explain.not_collected.join('、') }}<span v-if="explain.not_collected_count > explain.not_collected.length">…</span></span>
          ——所以 0 筆不代表沒有帳號，是還沒盤到。
        </template>
        <template v-else-if="explain">資產庫裡也找不到任何對得上的機器——這個詞可能打錯，或這個人／部門沒有負責任何機器。</template>
      </p>
      <div v-if="accounts.length" class="bar">
        <select v-model="kindFilter" class="sel">
          <option value="">全部類型</option>
          <option value="human">真人</option>
          <option value="mgmt">標準管理帳號</option>
          <option value="default">系統預設</option>
          <option value="builtin">內建帳號</option>
          <option value="service">服務帳號</option>
        </select>
        <label class="chk"><input v-model="hideBuiltin" type="checkbox" />拉掉內建帳號</label>
        <button v-if="colFilterActive" class="btn small" type="button" @click="clearColFilters">清除欄位篩選</button>
        <div class="spacer"></div>
        <span class="th-note">顯示 {{ mSorted.length }} / {{ accounts.length }} <InfoNote><b class="c-bad">紅</b>＝問題　<b class="c-warn">黃</b>＝注意　<b class="c-ok">綠</b>＝正常</InfoNote></span>
        <button class="btn" type="button" :disabled="!!exporting"
                title="公司現行的 18 欄格式，可以直接交出去"
                @click="exportFixed('standard')">
          {{ exporting === 'standard' ? '匯出中…' : '標準帳號盤點' }}
        </button>
        <button class="btn" type="button" :disabled="!!exporting"
                title="系統知道的全部欄位，給自己人查用"
                @click="exportFixed('full')">
          {{ exporting === 'full' ? '匯出中…' : '全匯出' }}
        </button>
        <button class="btn primary" type="button" @click="showExport = true">自選欄位…</button>
      </div>
      <!-- 欄位順序照公司帳號盤點表 A～R（使用者 2026-09-18：「順序要跟盤點表一樣，A-R 沒有的往後放」）：
           system_id／system → ap_department／ap_owner → hostname → ip → username → (password 固定 x，不顯示)
           → uid／gid → gecos → home／shell → type → department／owner → login_status；之後才是內部管理欄 -->
      <div v-if="accounts.length" class="tbl-scroll">
        <table class="tbl matrix-tbl">
          <thead>
            <tr>
              <SortTh k="system_id" :active="mKey" :dir="mDir" @sort="mToggle">業務系統</SortTh>
              <SortTh k="department" :active="mKey" :dir="mDir" @sort="mToggle">部門／窗口</SortTh>
              <SortTh k="hostname" :active="mKey" :dir="mDir" @sort="mToggle">主機</SortTh>
              <SortTh k="ip" :active="mKey" :dir="mDir" @sort="mToggle">IP</SortTh>
              <SortTh k="username" :active="mKey" :dir="mDir" @sort="mToggle">帳號</SortTh>
              <SortTh k="uid" :active="mKey" :dir="mDir" class="num" @sort="mToggle">UID／GID</SortTh>
              <SortTh k="gecos" :active="mKey" :dir="mDir" @sort="mToggle">備註</SortTh>
              <SortTh k="shell" :active="mKey" :dir="mDir" @sort="mToggle">home／shell</SortTh>
              <SortTh k="kind" :active="mKey" :dir="mDir" @sort="mToggle">類型</SortTh>
              <SortTh k="custodian" :active="mKey" :dir="mDir" @sort="mToggle">保管部門／保管者</SortTh>
              <SortTh k="can_login" :active="mKey" :dir="mDir" @sort="mToggle">可登入</SortTh>
              <SortTh v-for="c in MATRIX_COLS" :key="c.key" :k="'_' + c.key" :active="mKey" :dir="mDir" @sort="mToggle">{{ c.label }}</SortTh>
            </tr>
            <tr class="filter-row">
              <th colspan="11" class="filter-hint">漏斗篩選 →</th>
              <th v-for="c in MATRIX_COLS" :key="c.key">
                <select v-model="colFilter[c.key]" class="fsel" :class="{ on: colFilter[c.key] }">
                  <option value="">全部</option>
                  <option v-for="o in c.opts" :key="o[0]" :value="o[0]">{{ o[1] }}</option>
                </select>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in mShown" :key="a.id" :class="{ gone: a.gone_at, todo: a.needs_action }">
              <td class="sys-cell" :title="a.system || ''">
                <template v-if="a.system_id">
                  <div class="mono">{{ a.system_id }}</div>
                  <div class="who-sub">{{ a.system || '對照表查不到名稱' }}</div>
                </template>
                <span v-else class="unset">未登記系統</span>
              </td>
              <!-- 部門／窗口：盤點時要找誰確認。跟盤點報告同一套（先查業務系統對照表）；沒填明講「未填」 -->
              <td class="who-cell">
                <div :class="{ unset: !a.department }">{{ a.department || '部門未填' }}</div>
                <div class="who-sub" :class="{ unset: !a.contact }">{{ a.contact || '窗口未填' }}</div>
              </td>
              <td>
                <NuxtLink v-if="a.asset_serial" class="dl" :to="`/assets/${a.asset_serial}`">{{ a.hostname || a.ip }}</NuxtLink>
                <template v-else>{{ a.hostname || '—' }}</template>
              </td>
              <td class="mono dim">{{ a.ip }}</td>
              <td class="mono">{{ a.username }}
                <div v-if="a.last_seen" class="seen-sub" :title="`這台最後一次收集到這個帳號的時間：${a.last_seen}`">盤點 {{ String(a.last_seen).slice(0, 10) }}</div>
                <div v-if="(a as any)._hit" class="hit-sub">命中：{{ (a as any)._hit.join('、') }}</div>
              </td>
              <td class="mono dim num">{{ a.uid ?? '—' }}／{{ a.gid ?? '—' }}</td>
              <td class="note-cell">
                <span class="gecos-auto" v-if="a.gecos">{{ a.gecos }}</span>
                <template v-if="editingNote === noteKey(a.ip, a.username)">
                  <input v-model="noteDraft" class="note-input" autofocus
                         @keyup.enter="saveNote(a.ip, a.username)" @keyup.esc="editingNote = ''" />
                  <button class="note-save" type="button" @click="saveNote(a.ip, a.username)">存</button>
                </template>
                <span v-else class="note-view" @click="startEditNote(a.ip, a.username, a.note)">
                  <template v-if="a.note">📝 {{ a.note }}</template>
                  <span v-else class="note-add">＋備註</span>
                </span>
              </td>
              <td class="mono dim small-cell">
                <div>{{ a.home || '—' }}</div>
                <div class="who-sub">{{ a.shell || '—' }}</div>
              </td>
              <td class="dim kind-cell">
                <select v-if="editingKind === noteKey(a.ip, a.username)" class="kind-input"
                        :value="a.kind" @change="saveKind(a.ip, a.username, ($event.target as HTMLSelectElement).value)">
                  <option v-for="(lbl, k) in KIND_LABEL" :key="k" :value="k">{{ lbl }}</option>
                </select>
                <span v-else class="kind-view" title="點一下手動改類型" @click="startEditKind(a.ip, a.username)">
                  {{ KIND_LABEL[a.kind] || a.kind }}<span v-if="a.kind_override" class="kind-ovr"
                    :title="'手動改過（程式原判：' + (KIND_LABEL[a.kind_computed || ''] || a.kind_computed) + '）'">✎</span>
                </span>
              </td>
              <td class="who-cell">
                <div :class="{ unset: !a.owner_department }">{{ a.owner_department || '—' }}</div>
                <div class="who-sub" :class="{ unset: !a.custodian }">{{ a.custodian || '保管者未填' }}</div>
              </td>
              <td class="dim">{{ a.can_login === 1 ? '可登入' : (a.can_login === 0 ? '無法登入' : '未採集') }}</td>
              <td v-for="c in MATRIX_COLS" :key="c.key"><span class="cell" :class="c.fn(a).c">{{ c.fn(a).t }}</span></td>
            </tr>
          </tbody>
        </table>
        <p v-if="mSorted.length > mShown.length" class="capnote">
          只先畫出前 {{ mShown.length }} 筆（共 {{ mSorted.length }} 筆符合）——全部畫出來篩選會卡。匯出不受影響。
          <button class="btn small" type="button" @click="showAllM = true">顯示全部 {{ mSorted.length }} 筆</button>
        </p>
      </div>

      <!-- 匯出對話框：自己勾要給稽核的欄位，資料最小化 -->
      <div v-if="showExport" class="modal-back" @click.self="showExport = false">
        <div class="modal">
          <h3>匯出 Excel — 選要給稽核的欄位 <InfoNote>只勾必要欄位，不必把所有資料都攤給稽核。匯出的是目前「類型／拉掉內建」篩選後的帳號；細部篩選可在 Excel 內自己做。</InfoNote></h3>
          <div class="exp-cols">
            <label v-for="c in EXPORT_COLS" :key="c.key" class="exp-col">
              <input v-model="exportSel[c.key]" type="checkbox" />{{ c.label }}
            </label>
          </div>
          <div class="modal-btns">
            <button class="btn" type="button" @click="showExport = false">取消</button>
            <button class="btn primary" type="button" @click="doExport">匯出</button>
          </div>
        </div>
      </div>
    </template>

    <!-- 稽核發現 -->
    <template v-else-if="tab === 'findings'">
      <div class="bar">
        <select v-model="sevFilter" class="sel">
          <option value="">全部風險等級</option>
          <option value="high">高</option>
          <option value="medium">中</option>
          <option value="low">低（含查不到）</option>
        </select>
        <label class="chk"><input v-model="groupByLabel" type="checkbox" />依項目分組</label>
        <div class="spacer"></div>
        <button class="btn" type="button" @click="exportFindings">匯出 Excel</button>
      </div>
      <p v-if="findings.length === 0" class="muted">
        <template v-if="!summary?.has_data">還沒盤點過。到「盤點作業」對已納管的 Linux 主機收一輪。</template>
        <template v-else>這個條件下沒有稽核發現（或都已核准例外/確認）。</template>
      </p>
      <table v-else class="tbl findings-tbl">
        <colgroup>
          <col style="width:52px"><col style="width:112px"><col style="width:110px">
          <col style="width:110px"><col style="width:100px"><col style="width:140px">
          <col><col style="width:118px"><col style="width:140px">
        </colgroup>
        <thead>
          <tr>
            <SortTh k="severity" :active="fKey" :dir="fDir" @sort="fToggle">風險</SortTh>
            <SortTh k="ip" :active="fKey" :dir="fDir" @sort="fToggle">IP</SortTh>
            <SortTh k="hostname" :active="fKey" :dir="fDir" @sort="fToggle">主機</SortTh>
            <SortTh k="username" :active="fKey" :dir="fDir" @sort="fToggle">帳號</SortTh>
            <SortTh k="gecos" :active="fKey" :dir="fDir" @sort="fToggle">備註</SortTh>
            <SortTh k="label" :active="fKey" :dir="fDir" @sort="fToggle">項目</SortTh>
            <SortTh k="detail" :active="fKey" :dir="fDir" @sort="fToggle">判定</SortTh>
            <SortTh k="status" :active="fKey" :dir="fDir" @sort="fToggle">處置</SortTh>
            <th>說明 <InfoNote>可手動輸入。</InfoNote></th>
          </tr>
        </thead>
        <!-- 平鋪 -->
        <tbody v-if="!groupByLabel">
          <tr v-for="f in fSorted" :key="f.id" :class="{ 'st-done': f.status === 'ack' || f.status === 'fixed' }">
            <td>
              <span class="sev" :class="f.verdict === 'unknown' ? 'unk' : f.severity"
                    :title="`嚴重程度：${f.verdict === 'unknown' ? '查不到（權限不足，不等於合格）' : SEV_LABEL[f.severity] + '風險'}`">
                {{ f.verdict === 'unknown' ? '查不到' : SEV_LABEL[f.severity] }}
              </span>
            </td>
            <td class="mono dim">{{ f.ip }}</td>
            <td>
              <NuxtLink v-if="f.asset_serial" class="dl" :to="`/assets/${f.asset_serial}`">{{ f.hostname || '—' }}</NuxtLink>
              <template v-else>{{ f.hostname || '—' }}</template>
            </td>
            <td class="mono">
              <NuxtLink class="dl" :to="{ path: '/account-matrix', query: { ...route.query, user: f.username, tab: 'findings' } }"
                        title="看這個帳號在所有主機上的紀錄">{{ f.username }}</NuxtLink>
            </td>
            <td class="dim gecos" :title="f.gecos || ''">{{ f.gecos || '—' }}</td>
            <td>{{ f.label }}<span class="law" :title="`規則 ${f.rule_id}｜依據：${f.law}`">依據</span></td>
            <td class="dim mono">{{ f.detail }}</td>
            <td>
              <select class="st-sel" :class="'st-' + f.status"
                      :value="f.status" @change="onStatusChange(f, ($event.target as HTMLSelectElement).value)">
                <option v-for="o in STATUS_OPTS" :key="o.v" :value="o.v">{{ o.t }}</option>
              </select>
              <span v-if="f.contradiction" class="warn-mini" title="標為已修復但仍偵測到">⚠仍偵測到</span>
              <span v-if="f.exempt_until" class="dim ex-until">至 {{ f.exempt_until }}</span>
            </td>
            <td class="note-cell">
              <template v-if="editingNote === noteKey(f.ip, f.username)">
                <input v-model="noteDraft" class="note-input" placeholder="輸入備註…"
                       @keyup.enter="saveNote(f.ip, f.username)" @keyup.esc="editingNote = ''" />
                <button class="note-save" type="button" @click="saveNote(f.ip, f.username)">存</button>
              </template>
              <span v-else class="note-view" @click="startEditNote(f.ip, f.username, f.note)">
                <template v-if="f.note">{{ f.note }}</template>
                <span v-else class="note-add">＋ 加備註</span>
              </span>
            </td>
          </tr>
        </tbody>
        <!-- 依項目分組：一組一列（項目 ×N），展開看逐台 -->
        <tbody v-else>
          <template v-for="g in groupedFindings" :key="g.label">
            <tr class="grp-row" @click="toggleGroup(g.label)">
              <td>
                <span class="sev" :class="g.items[0].verdict === 'unknown' ? 'unk' : g.items[0].severity">
                  {{ g.items[0].verdict === 'unknown' ? '查不到' : SEV_LABEL[g.items[0].severity] }}
                </span>
              </td>
              <td colspan="8"><b>{{ openGroups.has(g.label) ? '▾' : '▸' }} {{ g.label }}</b>
                <span class="grp-count">×{{ g.items.length }}</span></td>
            </tr>
            <template v-if="openGroups.has(g.label)">
              <tr v-for="f in g.items" :key="f.id" class="grp-item"
                  :class="{ 'st-done': f.status === 'ack' || f.status === 'fixed' }">
                <td></td>
                <td class="mono dim">{{ f.ip }}</td>
                <td>{{ f.hostname || '—' }}</td>
                <td class="mono">
                  <NuxtLink class="dl" :to="{ path: '/account-matrix', query: { ...route.query, user: f.username, tab: 'findings' } }"
                        title="看這個帳號在所有主機上的紀錄">{{ f.username }}</NuxtLink>
                </td>
                <td class="dim gecos" :title="f.gecos || ''">{{ f.gecos || '—' }}</td>
                <td></td>
                <td class="dim mono">{{ f.detail }}</td>
                <td>
                  <select class="st-sel" :class="'st-' + f.status"
                          :value="f.status" @change="onStatusChange(f, ($event.target as HTMLSelectElement).value)">
                    <option v-for="o in STATUS_OPTS" :key="o.v" :value="o.v">{{ o.t }}</option>
                  </select>
                </td>
                <td class="note-cell">
                  <template v-if="editingNote === noteKey(f.ip, f.username)">
                    <input v-model="noteDraft" class="note-input" placeholder="輸入備註…"
                           @keyup.enter="saveNote(f.ip, f.username)" @keyup.esc="editingNote = ''" />
                    <button class="note-save" type="button" @click="saveNote(f.ip, f.username)">存</button>
                  </template>
                  <span v-else class="note-view" @click="startEditNote(f.ip, f.username, f.note)">
                    <template v-if="f.note">{{ f.note }}</template>
                    <span v-else class="note-add">＋</span>
                  </span>
                </td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </template>

    <!-- 帳號清單。
         ⚠️ 這裡以前是裸的 v-else：儀表板分頁不符合上面任何一條就掉進來，
         於是 189 列的帳號明細長在儀表板下面，把主機清單擠到看不見
         （2026-09-17 使用者：「儀表板 不需要這麼多帳號詳細資料」）。分頁要寫明是哪一個。 -->
    <template v-else-if="tab === 'accounts'">
      <div class="bar">
        <select v-model="kindFilter" class="sel">
          <option value="">全部類型</option>
          <option value="human">真人</option>
          <option value="mgmt">標準管理帳號</option>
          <option value="service">服務帳號</option>
          <option value="default">系統預設</option>
        </select>
        <label class="chk"><input v-model="sudoerOnly" type="checkbox" />只看特權帳號</label>
        <label class="chk">
          <input v-model="hideBuiltin" type="checkbox" />拉掉內建帳號
        </label>
        <span v-if="hideBuiltin && hiddenBuiltin > 0" class="hidden-note">
          已隱藏 {{ hiddenBuiltin }} 個乾淨的系統/內建帳號（有稽核發現的仍會顯示）
        </span>
      </div>
      <p v-if="accounts.length === 0" class="muted">
        <template v-if="hideBuiltin">拉掉內建帳號後沒有其他帳號了。</template>
        <template v-else>沒有帳號資料。</template>
      </p>
      <table v-else class="tbl">
        <thead>
          <tr>
            <SortTh k="hostname" :active="aKey" :dir="aDir" @sort="aToggle">主機</SortTh>
            <SortTh k="ip" :active="aKey" :dir="aDir" @sort="aToggle">IP</SortTh>
            <SortTh k="username" :active="aKey" :dir="aDir" @sort="aToggle">帳號</SortTh>
            <SortTh k="gecos" :active="aKey" :dir="aDir" @sort="aToggle">備註</SortTh>
            <SortTh k="uid" :active="aKey" :dir="aDir" @sort="aToggle">UID</SortTh>
            <SortTh k="kind" :active="aKey" :dir="aDir" @sort="aToggle">類型</SortTh>
            <SortTh k="is_sudoer" :active="aKey" :dir="aDir" @sort="aToggle">特權</SortTh>
            <SortTh k="last_login" :active="aKey" :dir="aDir" @sort="aToggle">最後登入</SortTh>
            <SortTh k="pw_last_change" :active="aKey" :dir="aDir" @sort="aToggle">上次改密碼</SortTh>
            <SortTh k="pw_max_days" :active="aKey" :dir="aDir" @sort="aToggle">密碼到期</SortTh>
            <SortTh k="authorized_keys" :active="aKey" :dir="aDir" @sort="aToggle">金鑰</SortTh>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in aSorted" :key="a.id" :class="{ gone: a.gone_at }">
            <td>
              <NuxtLink v-if="a.asset_serial" class="dl" :to="`/assets/${a.asset_serial}`">
                {{ a.hostname || '（未登記主機名）' }}
              </NuxtLink>
              <template v-else>{{ a.hostname || '—' }}</template>
            </td>
            <td class="mono dim">{{ a.ip }}</td>
            <td class="mono">{{ a.username }}</td>
            <td class="dim gecos" :title="a.gecos || ''">{{ a.gecos || '—' }}</td>
            <td class="mono" :class="{ danger: a.uid === 0 && a.username !== 'root' }">{{ a.uid }}</td>
            <td class="kind-cell">
              <select v-if="editingKind === noteKey(a.ip, a.username)" class="kind-input"
                      :value="a.kind" @change="saveKind(a.ip, a.username, ($event.target as HTMLSelectElement).value)">
                <option v-for="(lbl, k) in KIND_LABEL" :key="k" :value="k">{{ lbl }}</option>
              </select>
              <span v-else class="kind-view" title="點一下手動改類型" @click="startEditKind(a.ip, a.username)">
                {{ KIND_LABEL[a.kind] || a.kind }}<span v-if="a.kind_override" class="kind-ovr"
                  :title="'手動改過（程式原判：' + (KIND_LABEL[a.kind_computed || ''] || a.kind_computed) + '）'">✎</span>
              </span>
            </td>
            <td>
              <span v-if="a.uid === 0" class="tag danger">UID 0</span>
              <span v-else-if="a.sudo_nopasswd" class="tag danger">NOPASSWD</span>
              <span v-else-if="a.is_sudoer" class="tag">sudo</span>
              <span v-else-if="a.kind === 'mgmt'" class="tag mgmt" title="機構標準管理帳號，設計上帶 NOPASSWD:ALL（需 root 才看得到明細）">標準管理</span>
              <span v-else class="dim">—</span>
              <span v-if="a.priv_groups" class="grp dim">{{ a.priv_groups }}</span>
            </td>
            <td class="dim">
              <span v-if="a.never_logged_in" class="tag warn">從未登入</span>
              <template v-else>{{ a.last_login || '—' }}</template>
            </td>
            <td class="dim">
              <template v-if="a.pw_last_change">{{ a.pw_last_change }}</template>
              <span v-else class="needroot">需 root</span>
            </td>
            <td class="dim"><span :class="pwExpiry(a).cls">{{ pwExpiry(a).text }}</span></td>
            <td class="mono dim">
              <template v-if="a.authorized_keys !== null">{{ a.authorized_keys }}</template>
              <span v-else class="needroot">需 root</span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </div>
</template>

<style scoped>
.hostfind { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 10px 0;
            padding: 8px 12px; border-radius: 6px; background: rgba(0,0,0,.04); font-size: 13px; }
.hostfind .hf-lbl { color: var(--ink-soft); }
.hf-in { padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
         background: var(--card); color: var(--ink); min-width: 280px; font-size: 13px; }
.hf-hits { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.hf-hit { padding: 2px 9px; border: 1px solid var(--border-strong); border-radius: 10px;
          background: var(--card); color: var(--ink); cursor: pointer; font-size: 12px; }
.hf-none { font-size: 12px; color: var(--warn-text); }
.hostfind .sm { font-size: 11.5px; }
.acctres { margin: 10px 0 14px; padding: 10px 12px; border: 1px solid var(--border);
           border-radius: 8px; }
.ar-head { display: flex; align-items: center; gap: 6px; font-size: 13px; margin-bottom: 8px; }
.ar-head .spacer { flex: 1; }
.acctres .tag { font-size: 11px; padding: 1px 7px; border-radius: 9px; }
.acctres .tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.acctres .tag.bad { background: var(--bad-soft, rgba(200,40,40,.1)); color: var(--bad); }
.acctres .sm { font-size: 11.5px; }
.lead { color: var(--muted); margin: 0 0 16px; line-height: 1.7; }
.last-run { margin: -8px 0 14px; font-size: 13px; color: var(--ink); }
.last-run .lr-meta { color: var(--ink-soft); }
/* 分頁鈕原本沒樣式，三個分頁在畫面上疊成三行純文字（2026-09-11 截圖） */
.tabs { display: flex; gap: 6px; margin: 0 0 14px; flex-wrap: wrap; }
.tab { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft);
  border-radius: 999px; padding: 6px 16px; font-size: 13px; cursor: pointer; }
.tab:hover { border-color: var(--border-strong); }
.tab.active { border-color: var(--brand); background: var(--mint); color: var(--ink); font-weight: 600; }
.filter-chip {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 0 0 14px;
  padding: 10px 14px; border: 1px solid rgba(0,145,66,0.35); border-left: 3px solid #009142;
  border-radius: 10px; background: rgba(0,145,66,0.08); font-size: 13px;
}
.filter-chip .fc-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--brand);
  box-shadow: 0 0 8px rgba(0,145,66,0.9); }
.filter-chip .fc-text { font-weight: 600; }
.filter-chip .fc-clear { margin-left: auto; background: transparent; border: 1px solid rgba(15,23,42,0.22);
  color: inherit; border-radius: 999px; padding: 3px 12px; font-size: 12px; cursor: pointer; }
.filter-chip .fc-clear:hover { border-color: #009142; color: var(--brand-dark); }
/* 卡片縮小（2026-09-16 使用者：「卡片太大，小一點」）——跟帳號儀表板同一組尺寸。
   這些是結果，不是主角；畫面要留給可以動手的清單。 */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: 8px; margin-bottom: 10px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 9px 11px; }
.tile.bad { border-color: rgba(224,108,108,.5); }
.tile.warn { border-color: rgba(230,170,60,.45); }
.t-num { font-size: 20px; font-weight: 700; color: var(--brand-dark); line-height: 1.1;
         font-variant-numeric: tabular-nums; }
.tile.bad .t-num { color: var(--bad); }
.tile.warn .t-num { color: var(--warn-text); }
.t-num .of { font-size: 12px; color: var(--muted); font-weight: 500; }
.t-lbl { font-size: 11.5px; color: var(--muted); margin-top: 3px; line-height: 1.4; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .7; }

.bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.spacer { flex: 1; }
.btn { border-radius: 9px; border: 1px solid var(--border); background: transparent; color: inherit;
  padding: 7px 14px; cursor: pointer; font-size: 13px; font-family: inherit; }
.btn.primary { background: var(--brand); border-color: transparent; color: #fff; font-weight: 600; }
.btn:disabled { opacity: .55; cursor: progress; }
.sel { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 9px; padding: 6px 10px; font-size: 13px; }
.mkw { min-width: 220px; font-family: inherit; }
.chk { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 5px; cursor: pointer; }
.when, .th-note { font-size: 12px; color: var(--muted); }
.hidden-note { font-size: 11px; color: var(--muted); opacity: .8; }
.ip-sub { display: block; font-size: 10px; }
.gecos { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.probe-box { border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; margin-bottom: 16px; }
.probe-toggle { background: none; border: none; color: inherit; cursor: pointer; font-size: 13px; font-family: inherit; padding: 2px 0; }
.probe-lead { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 8px 0; }
.probe-lead b { color: var(--ink-aux); }
.probe-result { margin-top: 10px; }
.pr-head { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; margin-bottom: 8px; align-items: center; }
.pr-head .ok { color: var(--brand-dark); }
.pr-head .bad { color: var(--bad); }
.vd { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.vd.ok { background: rgba(0,145,66,.16); color: var(--brand-dark); }
.vd.bad { background: rgba(224,108,108,.16); color: var(--bad); }
.vd.warn { background: rgba(230,170,60,.16); color: var(--warn-text); }
.vd.dim { color: var(--muted); }
.stderr { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hints { margin: 10px 0 0; padding-left: 18px; font-size: 12px; color: var(--muted); line-height: 1.8; }
.resid { font-size: 11px; margin-top: 8px; }
.acct-sel { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
.acct-sel select { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 8px; padding: 5px 8px; font-size: 12px; font-family: inherit; }
.provision { background: rgba(120,150,220,.08); border: 1px solid rgba(120,150,220,.3);
  border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; font-size: 12px;
  color: var(--muted); line-height: 1.7; }
.provision b { color: var(--ink-aux); }
.pv-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pv-note { margin: 6px 0 0; }
.provision code { background: rgba(15,23,42,.07); padding: 1px 5px; border-radius: 4px; }

.gap { background: rgba(230,170,60,.08); border: 1px solid rgba(230,170,60,.35);
  border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; font-size: 12px;
  color: var(--muted); line-height: 1.75; }
.gap b { color: var(--warn-text); }
.gap.fail { background: rgba(224,108,108,.08); border-color: rgba(224,108,108,.4); }
.gap.fail b { color: var(--bad); }
.fail-err { margin-top: 6px; font-size: 11px; color: var(--muted); }
.excluded-note { font-size: 12px; color: var(--muted); margin-bottom: 14px; padding: 8px 12px; border: 1px dashed var(--border); border-radius: 8px; }
.excluded-note b { color: var(--ink, inherit); }
.exclude-box { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; }
.btn.small { padding: 3px 10px; font-size: 12px; }
.right { text-align: right; }
.gap code { background: rgba(15,23,42,.07); padding: 1px 5px; border-radius: 4px; }
.link-btn { background: none; border: none; color: var(--brand-dark); cursor: pointer;
  font-size: 12px; padding: 0 4px; font-family: inherit; text-decoration: underline; }
.sudo { background: rgba(0,0,0,.35); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; margin: 8px 0 4px; font-size: 11px; overflow-x: auto; white-space: pre; }
.sudo-note { margin: 0; font-size: 11px; line-height: 1.7; }

.sev { font-size: 11px; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.sev.high { background: rgba(224,108,108,.18); color: var(--bad); }
.sev.medium { background: rgba(230,170,60,.18); color: var(--warn-text); }
.sev.low { background: rgba(15,23,42,.08); color: var(--muted); }
.sev.unk { background: rgba(120,150,220,.16); color: var(--ink-aux); }
.law { font-size: 10px; color: var(--muted); border-bottom: 1px dotted var(--border);
  margin-left: 6px; cursor: help; }
.th-hint { font-size: 9px; color: var(--muted); font-weight: 400; margin-left: 3px; }

/* 稽核發現表：固定欄寬、拉滿，不再有的擠有的空 */
.findings-tbl { table-layout: fixed; width: 100%; }
.findings-tbl td { overflow: hidden; text-overflow: ellipsis; }
.findings-tbl td.dim, .findings-tbl td.note-cell { white-space: normal; }
.note-cell { }
.note-view { cursor: pointer; display: inline-block; min-width: 60px; font-size: 12px; }
.note-view:hover { color: var(--brand-dark); }
.note-add { color: var(--muted); opacity: .6; font-size: 11px; }
.note-input { background: var(--card); border: 1px solid var(--brand); color: inherit;
  border-radius: 6px; padding: 3px 6px; font-size: 12px; width: 120px; font-family: inherit; }
.note-save { background: var(--brand); border: none; color: #fff; border-radius: 6px;
  padding: 3px 8px; margin-left: 4px; font-size: 11px; cursor: pointer; font-family: inherit; }
.kind-view { cursor: pointer; border-bottom: 1px dashed var(--border-strong); }
.kind-view:hover { color: var(--brand-dark); border-bottom-color: var(--brand); }
.kind-ovr { color: var(--brand); margin-left: 3px; font-size: 11px; }
.kind-input { background: var(--card); border: 1px solid var(--brand); color: inherit;
  border-radius: 6px; padding: 3px 6px; font-size: 12px; font-family: inherit; }

/* 發現生命週期 */
.st-sel { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 6px; padding: 2px 4px; font-size: 11px; font-family: inherit; }
.st-sel.st-open { border-color: rgba(224,108,108,.5); }
.st-sel.st-ack { color: var(--muted); }
.st-sel.st-exception { border-color: rgba(120,150,220,.5); color: var(--ink-aux); }
.st-sel.st-fixed { color: var(--brand-dark); }
.st-done td { opacity: .55; }
.warn-mini { font-size: 10px; color: var(--bad); margin-left: 4px; }
.ex-until { font-size: 10px; display: block; }
.grp-row { cursor: pointer; }
.grp-row:hover { background: rgba(15,23,42,.04); }
.grp-row b { font-size: 13px; }
.grp-count { color: var(--muted); font-size: 12px; margin-left: 8px; }
.grp-item td:first-child { border-left: 2px solid var(--border); }

.tag { font-size: 10px; padding: 1px 7px; border-radius: 6px; border: 1px solid var(--border); }
.tag.danger { background: rgba(224,108,108,.16); color: var(--bad); border-color: transparent; }
.tag.warn { background: rgba(230,170,60,.16); color: var(--warn-text); border-color: transparent; }
.tag.mgmt { background: rgba(120,150,220,.16); color: var(--ink-aux); border-color: transparent; }
.grp { display: block; font-size: 10px; }
.needroot { font-size: 10px; color: var(--ink-aux); opacity: .85; }
.danger { color: var(--bad); font-weight: 700; }
.dim { color: var(--muted); }
.tbl tr.gone td { opacity: .5; }

/* 合規矩陣：一欄一狀態，紅=有問題 / 黃=注意 / 綠=正常 / 灰=無 / 藍=查不到 */
.tbl-scroll { overflow-x: auto; }
.matrix-tbl { width: 100%; }
.matrix-tbl th { white-space: nowrap; }
.matrix-tbl td { text-align: center; }
.matrix-tbl td:nth-child(-n+4) { text-align: left; }
.cell { display: inline-block; min-width: 62px; padding: 3px 10px; border-radius: 999px;
  font-size: 11.5px; font-weight: 600; background: rgba(15,23,42,.05); color: var(--muted); }
.cell.bad { background: rgba(224,108,108,.18); color: var(--bad); }
.cell.warn { background: rgba(230,170,60,.18); color: var(--warn-text); }
.cell.ok { background: rgba(0,145,66,.16); color: var(--brand-dark); }
.cell.needroot { background: rgba(120,150,220,.14); color: var(--ink-aux); }
.cell.dim { background: transparent; color: var(--muted); opacity: .55; font-weight: 400; }
.dl { color: var(--brand-dark); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.c-bad { color: var(--bad); }
.c-warn { color: var(--warn-text); }
.c-ok { color: var(--brand-dark); }

/* 儀表板檢查項框框 */
.dash-h { font-size: 14px; color: var(--muted); font-weight: 600; margin: 22px 0 10px; }
.tiles.checks { grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); }
.tile.check { cursor: pointer; transition: transform .12s, border-color .12s; }
.tile.check:hover { transform: translateY(-2px); }
.tile.check.bad { border-color: rgba(224,108,108,.5); }
.tile.check.bad .t-num { color: var(--bad); }
.tile.check.warn { border-color: rgba(230,170,60,.45); }
.tile.check.warn .t-num { color: var(--warn-text); }
.tile.check.zero { cursor: default; opacity: .5; }
.tile.check.zero:hover { transform: none; }
.tile.check.zero .t-num { color: var(--muted); }

/* 漏斗篩選列 */
.filter-row th { padding: 4px 6px; background: rgba(0,0,0,.2); }
.filter-hint { text-align: right !important; font-size: 11px; color: var(--muted); font-weight: 400; white-space: nowrap; }
.fsel { background: var(--card); border: 1px solid var(--border); color: inherit; border-radius: 6px;
  padding: 3px 4px; font-size: 11px; font-family: inherit; width: 100%; min-width: 66px; }
.fsel.on { border-color: var(--brand); color: var(--brand-dark); }
.gecos-auto { display: block; font-size: 11px; color: var(--muted); opacity: .8; }

/* 匯出對話框 */
.modal-back { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 900;
  display: flex; align-items: center; justify-content: center; }
.modal { background: var(--card); border: 1px solid var(--border); border-radius: 14px;
  padding: 22px 24px; width: min(460px, 92vw); box-shadow: 0 18px 50px rgba(0,0,0,.5); }
.modal h3 { margin: 0 0 8px; font-size: 16px; }
.modal-note { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 0 0 14px; }
.exp-cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 12px; margin-bottom: 18px; }
.exp-col { font-size: 13px; display: flex; align-items: center; gap: 6px; cursor: pointer; }
.modal-btns { display: flex; justify-content: flex-end; gap: 10px; }
.who-cell { font-size: 13px; line-height: 1.3; min-width: 110px; }
.who-cell .who-sub { font-size: 12px; color: var(--ink-soft); }
.who-cell .unset { color: #b45309; font-style: italic; }
.seen-sub { font-size: 11px; color: var(--ink-soft); font-family: inherit; }
.mfilter { flex-wrap: wrap; gap: 8px; }
.mfilter .sel.on { border-color: var(--brand); background: var(--brand-tint, #eef7f3); }
.mfilter .mkw { flex: 1; min-width: 280px; }
.sys-sel { max-width: 260px; }
.hit-sum { font-size: 12px; color: var(--ink-soft); margin: 2px 0 6px; }
.hit-chip { display: inline-block; margin-right: 6px; padding: 0 8px; border-radius: 10px; background: var(--brand-tint, #eef7f3); color: var(--brand-dark); }
.hit-empty { font-size: 13px; margin: 4px 0 8px; padding: 8px 12px; border-radius: 8px; background: #fff8e6; }
.hit-sub { font-size: 11px; color: var(--brand-dark); }
.sys-cell { min-width: 120px; font-size: 13px; }
.small-cell { font-size: 12px; }
tr.todo td:first-child { box-shadow: inset 3px 0 0 #d97706; }
.capnote { font-size: 12px; color: var(--ink-soft); margin: 8px 4px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.cov.low b { color: #b45309; }
.cov-link { margin-left: 8px; font-size: 12px; }
</style>
