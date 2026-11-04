<script setup lang="ts">
// 7.2 帳號合規矩陣（V13，2.048.0 V2 原生；取代嵌入的舊 pages/account-matrix.vue）。
// 四個頁籤：儀表板（V15，components/v2/AccountDashboard）／合規表／稽核發現／帳號清單。同一批 API：
//   讀：/api/accounts、/api/accounts/findings、/api/accounts/coverage、/api/accounts/inventoried-hosts、
//       /api/accounts/by-username、/api/accounts/search-explain
//   寫（一般使用者以上；後端 access_policy USER_WRITE 也擋）：/api/accounts/findings/disposition（處置）、
//       /api/accounts/note（手動備註）、/api/accounts/kind（手動改類型）
//   匯出（只能看的帳號沒有；後端也擋）：/api/accounts/export（標準／全匯出）、/api/accounts/matrix/export（自選欄位）、
//       /api/accounts/findings/export、/api/accounts/by-username/export
// 所有條件都在網址上（可分享、儀表板卡片點進來＝整個網址換成那張卡的條件，數字＝筆數）。
// 合規狀態欄判定跟儀表板共用 composables/accountCells（加總＝下鑽）。
// 格子點下去：合規表的狀態格＝再篩這一欄同一個值；帳號名＝這個帳號的明細（全部欄位＋它的稽核發現，可就地處置）。
import { isWin, mDisabled, mEmpty, mKeys, mNeverLogin, mPwExpired, mSudo, mUid0, OS_LABEL } from '~/composables/accountCells'
import type { CellAccount, OsBucket } from '~/composables/accountCells'

interface Finding {
  id: number; rule_id: string; label: string; severity: string; verdict: string
  law: string; username: string; ip: string; hostname: string | null
  asset_serial: string | null; detail: string; kind?: string
  gecos: string | null; note: string | null
  status: string; exempt_until: string | null; decided_by: string | null
  decided_at: string | null; contradiction: boolean; os_bucket?: string | null
}
interface AccountRow extends CellAccount {
  id: number; ip: string; hostname: string | null; asset_serial: string | null
  kind: string; shell: string | null
  department?: string | null; contact?: string | null; custodian?: string | null; last_seen?: string | null
  system_id?: string | null; system?: string | null; owner_department?: string | null
  env_group?: string; os_type?: string; needs_action?: boolean; open_findings?: number
  gid?: number | null; home?: string | null
  kind_override?: string | null; kind_computed?: string
  gecos: string | null; note: string | null
  last_login: string | null; pw_last_change: string | null; pw_max_days: string | null; pw_expires: string | null
  priv_groups: string | null; gone_at: string | null; local_groups?: string | null
}
interface Summary {
  has_data: boolean; failed_count?: number
  run?: { started_at: string; host_count: number; needs_root_count: number; trigger?: string }
}
interface AcctHost {
  ip: string; hostname: string | null; asset_serial: string | null
  environment: string | null; physical_location: string | null
  department: string | null; contact: string | null; custodian: string | null
  username: string; uid: number | null; kind: string | null
  is_sudoer: number; sudo_nopasswd: number; never_logged_in: number
  last_login: string | null; pw_status: string | null; registered: boolean; os_family?: string | null
}

const AM = '/v2/inventory/matrix'
const { apiFetch } = useApi()
const { showToast } = useToast()
const { canWrite, isAdmin } = useAuth()     // 後端匯出（access_policy _ADMIN_EXPORTS）只給管理員
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
const qlink = (patch: Record<string, string | undefined>) => router.resolve({ path: AM, query: { ...route.query, ...patch } }).fullPath

const SEV_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }
const SEV_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 }
const KIND_LABEL: Record<string, string> = {
  human: '真人', service: '服務帳號', default: '系統預設', mgmt: '標準管理帳號', builtin: '內建帳號', domain: '網域帳號',
}
const STATUS_OPTS = [{ v: 'open', t: '待處理' }, { v: 'ack', t: '已確認' }, { v: 'exception', t: '核准例外' }, { v: 'fixed', t: '已修復' }]
const STATUS_LABEL: Record<string, string> = Object.fromEntries(STATUS_OPTS.map(o => [o.v, o.t]))
const ENV_LABEL: Record<string, string> = { prod: '正式', nonprod: '非正式', oa: 'OA', unknown: '未知環境', unset: '未填環境' }
const PW_EXPIRY: Record<string, string> = { never: '永不過期', expired: '已過期', valid: '未過期', na: '—', unknown: '需 root' }

// 合規表狀態欄：一欄一種狀態；k 是篩選與排序共用的穩定 token（判定函式跟儀表板共用）
const MATRIX_COLS = [
  { key: 'pwExpired', label: '密碼過期', fn: mPwExpired, opts: [['expired', '已過期'], ['never', '永不過期'], ['valid', '未過期'], ['needroot', '需 root'], ['na', '—']] },
  { key: 'disabled', label: '帳號停用', fn: mDisabled, opts: [['active', '啟用中'], ['locked', '已停用'], ['needroot', '需 root']] },
  { key: 'sudo', label: 'sudo 權限', fn: mSudo, opts: [['uid0', 'UID 0'], ['nopw', '免密碼'], ['yes', '是'], ['admin', '本機管理員'], ['no', '否']] },
  { key: 'empty', label: '空密碼', fn: mEmpty, opts: [['yes', '是'], ['no', '否'], ['needroot', '需 root'], ['na', '不適用']] },
  { key: 'uid0', label: 'UID 0', fn: mUid0, opts: [['yes', '是'], ['root', 'root'], ['no', '否'], ['na', '不適用']] },
  { key: 'keys', label: '免密碼金鑰', fn: mKeys, opts: [['has', '有'], ['none', '無'], ['needroot', '需 root'], ['na', '不適用']] },
  { key: 'login', label: '曾登入', fn: mNeverLogin, opts: [['never', '從未登入'], ['yes', '有'], ['needroot', '查不到']] },
] as const
type ColKey = typeof MATRIX_COLS[number]['key']
const MATRIX_SLOTS = MATRIX_COLS.map(c => ({ key: c.key, label: c.label, slot: `cell-_${c.key}` }))

// ===== 頁籤（?tab=；沒帶時依條件推：跟舊頁同一套）=====
type Tab = 'dashboard' | 'matrix' | 'findings' | 'accounts'
const TAB_KEYS: Tab[] = ['dashboard', 'matrix', 'findings', 'accounts']
const tab = computed<Tab>({
  get: () => {
    const t = qs('tab') as Tab
    if (TAB_KEYS.includes(t)) return t
    if (qs('col') || MATRIX_COLS.some(c => qs('m_' + c.key))) return 'matrix'
    if (qs('sudoer') === '1' || qs('priv') === '1' || qs('kind') || qs('ip')) return 'accounts'
    if (qs('verdict') || qs('lbl')) return 'findings'
    return 'dashboard'
  },
  set: (v: Tab) => { setQ({ tab: v }) },
})
function pickTab(v: string) { if (TAB_KEYS.includes(v as Tab)) setQ({ tab: v }) }

// ===== 資料 =====
const findings = ref<Finding[]>([])
const summary = ref<Summary | null>(null)
const accounts = ref<AccountRow[]>([])
const hiddenBuiltin = ref(0)
const loading = ref(true)
const err = ref('')
const coverage = ref<{ managed_hosts: number; collected_managed: number; collected_outside: number; pct: number | null } | null>(null)
const covErr = ref('')
const sev = computed(() => (['high', 'medium', 'low'].includes(qs('sev')) ? qs('sev') : ''))
const kind = computed(() => qs('kind'))
const sudoerOnly = computed(() => qs('sudoer') === '1')
const privOnly = computed(() => qs('priv') === '1')
const hideBuiltin = computed(() => qs('hb') === '1')
const beginLoad = useLatest()     // 條件一改就重抓：只採用最新那一次
async function load() {
  const isLatest = beginLoad()
  loading.value = true
  try {
    const [f, a] = await Promise.all([
      apiFetch<{ items: Finding[]; summary: Summary }>('/api/accounts/findings', { query: { severity: sev.value || undefined } }),
      apiFetch<{ items: AccountRow[]; hidden_builtin: number }>('/api/accounts', {
        query: { kind: kind.value || undefined, sudoer_only: sudoerOnly.value || undefined, privileged: privOnly.value || undefined, hide_builtin: hideBuiltin.value || undefined },
      }),
    ])
    if (!isLatest()) return
    findings.value = f.items ?? []
    summary.value = f.summary
    accounts.value = a.items ?? []
    hiddenBuiltin.value = a.hidden_builtin ?? 0
    err.value = ''
  } catch (e: any) {
    if (isLatest()) err.value = `帳號資料載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  } finally {
    if (isLatest()) loading.value = false
  }
}
watch(() => [sev.value, kind.value, sudoerOnly.value, privOnly.value, hideBuiltin.value].join('|'), load)
const invHosts = ref<{ ip: string; hostname: string | null }[]>([])
onMounted(async () => {
  load()
  try { coverage.value = await apiFetch('/api/accounts/coverage') } catch (e: any) { covErr.value = `收集涵蓋率載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
  try { invHosts.value = (await apiFetch<{ items: any[] }>('/api/accounts/inventoried-hosts')).items ?? [] }
  catch { /* 「跳到某一台」的候選清單是輔助；載不到時按 Enter 照樣用打的字去篩 */ }
})

// ===== 範圍條件（網址）：只看這台／這個 OS／這個帳號／判定 =====
const ipF = computed(() => qs('ip'))
const osF = computed<OsBucket | ''>(() => (qs('os') in OS_LABEL && qs('os') !== 'all' ? qs('os') as OsBucket : ''))
const userF = computed(() => qs('user'))
const verdictF = computed(() => (qs('verdict') === 'fail' || qs('verdict') === 'unknown' ? qs('verdict') : ''))
const lblF = computed(() => qs('lbl'))
function scoped<T extends { ip?: string; os_bucket?: string | null; username?: string }>(rows: T[]): T[] {
  return rows.filter(r => (!ipF.value || r.ip === ipF.value)
    && (!osF.value || (r.os_bucket || 'unknown') === osF.value)
    && (!userF.value || r.username === userF.value))
}

// ===== 跳到某一台／查帳號在哪幾台 =====
const hostFindKw = ref('')
const hostFindHits = computed(() => {
  const q = hostFindKw.value.trim().toLowerCase()
  if (!q) return []
  return invHosts.value.filter(h => (h.ip || '').toLowerCase().includes(q) || (h.hostname || '').toLowerCase().includes(q))
})
function goHost(ip?: string) {
  const target = ip || (hostFindHits.value.length === 1 ? hostFindHits.value[0].ip : hostFindKw.value.trim())
  if (!target) return
  router.push({ path: AM, query: { ip: target, tab: 'accounts' } })     // 換成只看這一台（其他條件清掉）
  hostFindKw.value = ''
}
const acctHosts = ref<AcctHost[] | null>(null)
const acctName = ref('')
const acctBusy = ref(false)
const acctErr = ref('')
const beginAcct = useLatest()
async function findAccount(name?: string) {
  const u = (name ?? hostFindKw.value).trim()
  if (!u) return
  const isLatest = beginAcct()
  acctBusy.value = true; acctErr.value = ''; acctName.value = u; acctHosts.value = []
  try {
    const r = await apiFetch<{ items: AcctHost[] }>('/api/accounts/by-username', { query: { username: u } })
    if (isLatest()) acctHosts.value = r.items ?? []
  } catch (e: any) {
    if (isLatest()) acctErr.value = e?.data?.detail ?? e?.message ?? '查詢失敗'
  } finally { if (isLatest()) acctBusy.value = false }
}
// 關閉反查視窗：讓還在路上的那一次作廢，晚回來不會又把視窗打開
function closeAcct() { beginAcct(); acctHosts.value = null; acctBusy.value = false; acctErr.value = '' }
const acctRows = computed(() => (acctHosts.value ?? []).map((h, i) => ({
  ...h, __no: i + 1,
  reg: h.registered ? '' : '未登記',
  priv: h.is_sudoer && isWin(h) ? '本機管理員' : h.sudo_nopasswd ? '免密碼' : h.is_sudoer ? '有' : '',
  login: h.never_logged_in ? '從未登入' : h.last_login,
})))
const ACCT_COLS = [
  { k: 'ip', label: 'IP', mono: true, link: (r: any) => `${AM}?ip=${encodeURIComponent(r.ip)}&tab=accounts` },
  { k: 'reg', label: '資產庫', tone: (r: any) => (r.reg ? 'test' : '') },
  { k: 'hostname', label: '主機名稱', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) },
  { k: 'physical_location', label: '機房' }, { k: 'environment', label: '環境' },
  { k: 'department', label: '使用單位（部門）' }, { k: 'contact', label: '使用者（窗口）' }, { k: 'custodian', label: '保管者' },
  { k: 'uid', label: 'UID', mono: true }, { k: 'priv', label: 'sudo／本機管理員' }, { k: 'login', label: '最後登入', mono: true },
]

// ===== 合規表 =====
// 欄位篩選：網址 m_<欄>=<值>；儀表板中招卡帶來的 col=&val= 也認（同一個條件）
const colVal = (key: string) => qs('m_' + key) || (qs('col') === key ? qs('val') : '')
function setCol(key: ColKey, v: string) {
  const patch: Record<string, string | undefined> = { tab: 'matrix', col: undefined, val: undefined }
  if (qs('col') && qs('col') !== key) patch['m_' + qs('col')] = qs('val') || undefined    // 舊格式轉成 m_ 一起保留
  patch['m_' + key] = v || undefined
  setQ(patch)
}
// 帳號明細裡的狀態格：看「這一欄同一個值」的全部帳號（其他條件清掉，筆數＝這個值的帳號數）
function onlyCell(key: string, v: string) { router.replace({ path: AM, query: { tab: 'matrix', ['m_' + key]: v } }) }
const colFilterActive = computed(() => MATRIX_COLS.some(c => colVal(c.key)))
// 項次：預設排序（主機→帳號）先編好，篩選、重排後同一筆仍是同一號
const matrixAll = computed(() => [...scoped(accounts.value)]
  .sort((a, b) => String(a.hostname || a.ip).localeCompare(String(b.hostname || b.ip), undefined, { numeric: true }) || a.username.localeCompare(b.username))
  .map((a, i) => {
    const row: any = { ...a, __no: i + 1, rk: `${a.ip}|${a.username}` }
    for (const c of MATRIX_COLS) { const cell = c.fn(a); row['_' + c.key] = cell.k; row['_' + c.key + '_t'] = cell.t; row['_' + c.key + '_c'] = cell.c }
    row.sys_t = a.system_id ? `${a.system_id}\n${a.system || '對照表查不到名稱'}` : '未登記系統'
    row.who_t = `${a.department || '部門未填'}\n${a.contact || '窗口未填'}`
    row.uidgid = `${a.uid ?? '—'}／${a.gid ?? '—'}`
    row.homeshell = isWin(a) ? `Windows\n${a.local_groups || a.priv_groups || '—'}` : `${a.home || '—'}\n${a.shell || '—'}`
    row.kind_t = KIND_LABEL[a.kind] || a.kind
    row.cust_t = `${a.owner_department || '—'}\n${a.custodian || '保管者未填'}`
    row.login_t = a.can_login === 1 ? '可登入' : a.can_login === 0 ? '無法登入' : '未採集'
    row.note_t = [a.gecos, a.note ? `手動：${a.note}` : ''].filter(Boolean).join('\n')
    return row
  }))
const matrixFiltered = computed(() => matrixAll.value.filter(r => MATRIX_COLS.every(c => !colVal(c.key) || r['_' + c.key] === colVal(c.key))))
const onlyTodo = computed(() => qs('todo') === '1')
const envSel = computed(() => qs('env'))
const osTypeSel = computed(() => qs('ost'))
const sysSel = computed(() => qs('sys'))
const sysList = (r: AccountRow) => (r.system_id || '').split('、').filter(Boolean)
const optCounts = computed(() => {
  const env = new Map<string, number>(); const os = new Map<string, number>(); const sys = new Map<string, { n: number; name: string }>()
  for (const r of matrixFiltered.value as AccountRow[]) {
    const e = r.env_group || 'unset'; env.set(e, (env.get(e) || 0) + 1)
    const o = r.os_type || '未填'; os.set(o, (os.get(o) || 0) + 1)
    const names = (r.system || '').split('、')
    sysList(r).forEach((id, i) => { const cur = sys.get(id) || { n: 0, name: names[i] || '' }; cur.n++; sys.set(id, cur) })
  }
  return {
    env: [...env.entries()].sort((a, b) => b[1] - a[1]),
    os: [...os.entries()].sort((a, b) => b[1] - a[1]),
    sys: [...sys.entries()].sort((a, b) => a[0].localeCompare(b[0])),
  }
})
// 搜尋：多個詞（空白隔開）都要符合；每筆標出命中哪一欄（「陳大文」可能是窗口、也可能是帳號備註）
const SEARCH_FIELDS: [keyof AccountRow, string][] = [
  ['hostname', '主機'], ['ip', 'IP'], ['username', '帳號'], ['gecos', '備註'], ['note', '手動備註'],
  ['department', '部門'], ['contact', '窗口'], ['custodian', '保管者'], ['system_id', 'AP ID'], ['system', '系統'], ['asset_serial', '序號'],
]
const mkwInput = ref(qs('q'))
let kwTimer: ReturnType<typeof setTimeout> | undefined
watch(mkwInput, v => { clearTimeout(kwTimer); kwTimer = setTimeout(() => { if (v !== qs('q')) setQ({ q: v || undefined }) }, 250) })
watch(() => qs('q'), v => { if (v !== mkwInput.value) mkwInput.value = v })
const mkw = computed(() => qs('q'))
function matchInfo(r: AccountRow, terms: string[]): string[] | null {
  const hit: string[] = []
  for (const t of terms) {
    const fs = SEARCH_FIELDS.filter(([k]) => String(r[k] ?? '').toLowerCase().includes(t)).map(([, l]) => l)
    if (!fs.length) return null
    fs.forEach(l => { if (!hit.includes(l)) hit.push(l) })
  }
  return hit
}
const mRows = computed(() => {
  let rows = matrixFiltered.value
  if (onlyTodo.value) rows = rows.filter(r => r.needs_action)
  if (envSel.value) rows = rows.filter(r => (r.env_group || 'unset') === envSel.value)
  if (osTypeSel.value) rows = rows.filter(r => (r.os_type || '未填') === osTypeSel.value)
  if (sysSel.value) rows = rows.filter(r => sysList(r).includes(sysSel.value))
  const terms = mkw.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return rows
  const out: any[] = []
  for (const r of rows) { const m = matchInfo(r, terms); if (m) out.push({ ...r, hit_t: m.join('、') }) }
  return out
})
const hitSummary = computed(() => {
  const c = new Map<string, number>()
  for (const r of mRows.value) String(r.hit_t || '').split('、').filter(Boolean).forEach(l => c.set(l, (c.get(l) || 0) + 1))
  return [...c.entries()].sort((a, b) => b[1] - a[1])
})
const mFilterActive = computed(() => onlyTodo.value || !!envSel.value || !!osTypeSel.value || !!sysSel.value || !!mkw.value || colFilterActive.value)
function clearMFilters() {
  const patch: Record<string, string | undefined> = { todo: undefined, env: undefined, ost: undefined, sys: undefined, q: undefined, col: undefined, val: undefined }
  for (const c of MATRIX_COLS) patch['m_' + c.key] = undefined
  mkwInput.value = ''
  setQ(patch)
}
// 0 筆時：這個詞在資產庫對得到幾台、幾台還沒收集帳號（0 筆不代表沒有，可能是還沒盤到）
const explain = ref<{ machines: number; collected: number; not_collected: string[]; not_collected_count: number; error?: string } | null>(null)
const beginExplain = useLatest()
watch([mkw, () => mRows.value.length], async ([kw, n]) => {
  const isLatest = beginExplain()
  explain.value = null
  if (!String(kw).trim() || n > 0) return
  try {
    const r = await apiFetch<any>('/api/accounts/search-explain', { query: { q: kw } })
    if (isLatest()) explain.value = r
  } catch (e: any) {
    if (isLatest()) explain.value = { machines: 0, collected: 0, not_collected: [], not_collected_count: 0, error: e?.data?.detail ?? e?.message ?? String(e) }
  }
})
const accLink = (r: any) => qlink({ acc: `${r.ip}|${r.username}` })
const MATRIX_TCOLS = computed(() => [
  { k: 'sys_t', label: '業務系統', min: 110 },
  { k: 'who_t', label: '部門／窗口', min: 100 },
  { k: 'hostname', label: '主機', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null), text: (r: any) => r.hostname || r.ip },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'username', label: '帳號', mono: true, note: '點帳號看這個帳號的全部欄位與稽核發現（可就地處置）' },
  { k: 'uidgid', label: 'UID／GID', mono: true },
  { k: 'note_t', label: '備註', min: 140 },
  { k: 'homeshell', label: 'home／shell' },
  { k: 'kind_t', label: '類型' },
  { k: 'cust_t', label: '保管部門／保管者' },
  { k: 'login_t', label: '可登入' },
  ...MATRIX_COLS.map(c => ({ k: '_' + c.key, label: c.label, text: (r: any) => r['_' + c.key + '_t'] })),
  ...(mkw.value ? [{ k: 'hit_t', label: '命中欄位' }] : []),
])

// ===== 稽核發現 =====
const findingsAll = computed(() => [...scoped(findings.value)]
  .sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9) || a.ip.localeCompare(b.ip, undefined, { numeric: true }) || a.username.localeCompare(b.username))
  .map((f, i) => ({
    ...f, __no: i + 1,
    sev_t: f.verdict === 'unknown' ? '查不到' : (SEV_LABEL[f.severity] ?? f.severity),
    st_t: [STATUS_LABEL[f.status] ?? f.status, f.exempt_until ? `至 ${f.exempt_until}` : '', f.contradiction ? '⚠仍偵測到' : ''].filter(Boolean).join(' '),
    decided: f.decided_by ? `${f.decided_by}${f.decided_at ? ' ' + String(f.decided_at).slice(0, 16) : ''}` : '',
  })))
const fRows = computed(() => findingsAll.value.filter(f => (!verdictF.value || f.verdict === verdictF.value) && (!lblF.value || f.label === lblF.value)))
const grouped = computed(() => qs('grp') === '1')
const groupRows = computed(() => {
  const g = new Map<string, { label: string; sev_t: string; sevk: number; n: number; hosts: Set<string>; open: number }>()
  for (const f of fRows.value) {
    const cur = g.get(f.label) || { label: f.label, sev_t: f.sev_t, sevk: SEV_RANK[f.severity] ?? 9, n: 0, hosts: new Set<string>(), open: 0 }
    cur.n++; cur.hosts.add(f.ip); if (f.status === 'open') cur.open++
    g.set(f.label, cur)
  }
  return [...g.values()].sort((a, b) => a.sevk - b.sevk || b.n - a.n).map((x, i) => ({ __no: i + 1, label: x.label, sev_t: x.sev_t, n: x.n, hosts: x.hosts.size, open: x.open }))
})
const GROUP_COLS = [
  { k: 'sev_t', label: '風險' }, { k: 'label', label: '項目' },
  { k: 'n', label: '條數', mono: true, link: (r: any) => qlink({ lbl: r.label, grp: undefined }) },
  { k: 'hosts', label: '主機數', mono: true }, { k: 'open', label: '待處理', mono: true },
]
const FIND_COLS = [
  { k: 'sev_t', label: '風險' },
  { k: 'ip', label: 'IP', mono: true, link: (r: any) => qlink({ ip: r.ip }) },
  { k: 'hostname', label: '主機', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) },
  { k: 'username', label: '帳號', mono: true },
  { k: 'gecos', label: '備註' },
  { k: 'label', label: '項目', link: (r: any) => qlink({ lbl: r.label }) },
  { k: 'law', label: '依據', brief: (r: any) => (String(r.law || '').length > 14 ? String(r.law).slice(0, 14) + '…' : '') },
  { k: 'detail', label: '判定', mono: true },
  { k: 'st_t', label: '處置' },
  { k: 'decided', label: '處置人／時間', mono: true },
  { k: 'note', label: '說明', min: 140, note: '手動備註，同一個帳號共用一份（合規表的「備註」也是這一份）' },
]

// ===== 帳號清單 =====
const accRows = computed(() => [...scoped(accounts.value)]
  .sort((a, b) => a.ip.localeCompare(b.ip, undefined, { numeric: true }) || a.username.localeCompare(b.username))
  .map((a, i) => ({
    ...a, __no: i + 1, rk: `${a.ip}|${a.username}`, kind_t: KIND_LABEL[a.kind] || a.kind,
    priv_t: [a.uid === 0 ? 'UID 0' : a.is_sudoer && isWin(a) ? '本機管理員' : a.sudo_nopasswd ? 'NOPASSWD' : a.is_sudoer ? 'sudo' : a.kind === 'mgmt' ? '標準管理' : '', a.priv_groups || ''].filter(Boolean).join(' '),
    login_t: a.never_logged_in ? '從未登入' : a.last_login,
    pwc_t: a.pw_last_change || '需 root',
    pwe_t: PW_EXPIRY[a.pw_expiry_status] ?? '—',
    keys_t: a.authorized_keys === null || a.authorized_keys === undefined ? '需 root' : a.authorized_keys,
  })))
const ACC_COLS = [
  { k: 'hostname', label: '主機', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null), text: (r: any) => r.hostname || '（未登記主機名）' },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'username', label: '帳號', mono: true, link: accLink },
  { k: 'gecos', label: '備註' }, { k: 'uid', label: 'UID', mono: true },
  { k: 'kind_t', label: '類型' }, { k: 'priv_t', label: '特權' },
  { k: 'login_t', label: '最後登入', mono: true }, { k: 'pwc_t', label: '上次改密碼', mono: true },
  { k: 'pwe_t', label: '密碼到期' }, { k: 'keys_t', label: '金鑰', mono: true },
]

// ===== 帳號明細（?acc=ip|帳號）：全部欄位＋這個帳號的稽核發現 =====
const accKey = computed(() => qs('acc'))
const detail = ref<AccountRow | null>(null)
const detailFindings = ref<Finding[]>([])
const detailErr = ref('')
const detailBusy = ref(false)
const beginDetail = useLatest()
async function loadDetail() {
  const isLatest = beginDetail()
  detail.value = null; detailFindings.value = []; detailErr.value = ''
  const k = accKey.value
  if (!k) return
  const i = k.indexOf('|')
  const ip = k.slice(0, i), user = k.slice(i + 1)
  detailBusy.value = true
  try {
    const [a, f] = await Promise.all([
      apiFetch<{ items: AccountRow[] }>('/api/accounts', { query: { ip } }),
      apiFetch<{ items: Finding[] }>('/api/accounts/findings'),
    ])
    if (!isLatest()) return
    detail.value = (a.items ?? []).find(x => x.username === user) ?? null
    detailFindings.value = (f.items ?? []).filter(x => x.ip === ip && x.username === user)
    if (!detail.value) detailErr.value = `這一輪盤點裡找不到 ${ip} 上的帳號「${user}」（可能已刪除或這台沒收到）`
  } catch (e: any) {
    if (isLatest()) detailErr.value = `帳號明細載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  } finally { if (isLatest()) detailBusy.value = false }
}
watch(accKey, loadDetail, { immediate: true })
const detailCells = computed(() => (detail.value ? MATRIX_COLS.map(c => ({ key: c.key, label: c.label, ...c.fn(detail.value!) })) : []))
const detailFields = computed(() => {
  const a = detail.value
  if (!a) return []
  return [
    ['IP', a.ip], ['UID／GID', `${a.uid ?? '—'}／${a.gid ?? '—'}`],
    ['業務系統', a.system_id ? `${a.system_id} ${a.system || ''}` : '未登記系統'],
    ['部門／窗口', `${a.department || '部門未填'}／${a.contact || '窗口未填'}`],
    ['保管部門／保管者', `${a.owner_department || '—'}／${a.custodian || '保管者未填'}`],
    [isWin(a) ? '本機群組' : 'home／shell', isWin(a) ? (a.local_groups || a.priv_groups || '—') : `${a.home || '—'}／${a.shell || '—'}`],
    ['特權群組', a.priv_groups || '—'], ['帳號備註（自動）', a.gecos || '—'],
    ['可登入', a.can_login === 1 ? '可登入' : a.can_login === 0 ? '無法登入' : '未採集'],
    ['最後登入', a.never_logged_in ? '從未登入' : (a.last_login || '—')],
    ['上次改密碼', a.pw_last_change || '需 root'], ['密碼最長天數', a.pw_max_days || '—'], ['密碼到期日', a.pw_expires || '—'],
    ['密碼到期', PW_EXPIRY[a.pw_expiry_status] ?? '—'],
    ['免密碼金鑰', a.authorized_keys === null || a.authorized_keys === undefined ? '需 root' : String(a.authorized_keys)],
    ['盤點時間', a.last_seen || '—'], ['已消失', a.gone_at || '—'],
  ] as [string, string][]
})
const detailFindRows = computed(() => detailFindings.value.map((f, i) => ({
  ...f, __no: i + 1,
  sev_t: f.verdict === 'unknown' ? '查不到' : (SEV_LABEL[f.severity] ?? f.severity),
  st_t: [STATUS_LABEL[f.status] ?? f.status, f.exempt_until ? `至 ${f.exempt_until}` : '', f.contradiction ? '⚠仍偵測到' : ''].filter(Boolean).join(' '),
})))
const DETAIL_FIND_COLS = [
  { k: 'sev_t', label: '風險' }, { k: 'label', label: '項目' }, { k: 'law', label: '依據' },
  { k: 'detail', label: '判定', mono: true }, { k: 'st_t', label: '處置' },
]

// ===== 寫入（只給一般使用者以上；後端也擋）=====
const saving = ref('')
async function setDisposition(f: Finding, status: string, exempt_until?: string, note?: string) {
  saving.value = `d:${f.id}`
  try {
    await apiFetch('/api/accounts/findings/disposition', { method: 'PUT', body: { ip: f.ip, username: f.username, rule_id: f.rule_id, status, exempt_until, note } })
    showToast(`已設為「${STATUS_LABEL[status] ?? status}」`, 'success')
    await Promise.all([load(), accKey.value ? loadDetail() : Promise.resolve()])
  } catch (e: any) {
    showToast(`設定失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  } finally { saving.value = '' }
}
async function onStatusChange(f: Finding, ev: Event) {
  const el = ev.target as HTMLSelectElement
  const status = el.value
  if (status === 'exception') {
    const d = window.prompt('核准例外到期日（YYYY-MM-DD，到期自動回待處理）：', '')
    const n = d ? window.prompt('核准例外理由（必填）：', '') : null
    if (!d || !n || !n.trim()) {
      el.value = f.status                         // 沒填完：選單退回原值，不送出
      if (d) showToast('核准例外要寫理由', 'warn')
      return
    }
    await setDisposition(f, 'exception', d, n.trim())
  } else {
    await setDisposition(f, status)
  }
}
const editingNote = ref('')
const noteDraft = ref('')
const nk = (ip: string, u: string) => `${ip}|${u}`
function startNote(ip: string, u: string, cur: string | null) { editingNote.value = nk(ip, u); noteDraft.value = cur || '' }
async function saveNote(ip: string, username: string) {
  saving.value = `n:${nk(ip, username)}`
  try {
    await apiFetch('/api/accounts/note', { method: 'PUT', body: { ip, username, note: noteDraft.value } })
    editingNote.value = ''
    showToast('備註已儲存', 'success')
    await Promise.all([load(), accKey.value ? loadDetail() : Promise.resolve()])
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  } finally { saving.value = '' }
}
async function saveKind(ip: string, username: string, k: string) {
  saving.value = `k:${nk(ip, username)}`
  try {
    await apiFetch('/api/accounts/kind', { method: 'PUT', body: { ip, username, kind: k } })
    showToast(k ? '類型已更新' : '已回到程式判定', 'success')
    await Promise.all([load(), accKey.value ? loadDetail() : Promise.resolve()])      // 改類型會連動規則適用，重載才準
  } catch (e: any) {
    showToast(`更新失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  } finally { saving.value = '' }
}

// ===== 匯出（只能看的帳號沒有；後端也擋）=====
const exporting = ref('')
async function exportFixed(fmt: 'standard' | 'full') {
  if (exporting.value) return
  exporting.value = fmt
  try {
    const res = await fetch(`${apiBase}/api/accounts/export?fmt=${fmt}`, { credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const blob = await res.blob()
    const m = (res.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = m ? decodeURIComponent(m[1]) : `帳號盤點_${fmt}.xlsx`; a.click()
    URL.revokeObjectURL(url)
    // 只給檔案不講「其中 N 筆的類型要人工填」，人會以為它是完整的
    const raw = res.headers.get('X-Export-Summary')
    if (raw && fmt === 'standard') {
      const sum = JSON.parse(decodeURIComponent(raw))
      const bits = [`${sum.rows} 筆`]
      if (sum.unclassified_type) bits.push(`${sum.unclassified_type} 筆的類型要人工填`)
      if (sum.rows_without_system_name) bits.push(`${sum.rows_without_system_name} 筆查不到系統名稱`)
      showToast(bits.join('，'), sum.unclassified_type ? 'warn' : 'success')
    } else showToast('匯出完成', 'success')
  } catch (e: any) {
    showToast(`匯出失敗：${e?.message || e}`, 'error')
  } finally { exporting.value = '' }
}
const EXPORT_COLS = [
  { key: 'hostname', label: '主機' }, { key: 'ip', label: 'IP' },
  { key: 'department', label: '部門（使用單位）' }, { key: 'contact', label: '窗口（使用者）' }, { key: 'custodian', label: '保管者' },
  { key: 'username', label: '帳號' }, { key: 'collected', label: '盤點時間' }, { key: 'note', label: '備註' }, { key: 'kind', label: '類型' },
  { key: 'pwExpired', label: '密碼過期' }, { key: 'disabled', label: '帳號停用' }, { key: 'sudo', label: 'sudo 權限' }, { key: 'empty', label: '空密碼' },
  { key: 'uid0', label: 'UID 0' }, { key: 'keys', label: '免密碼金鑰' }, { key: 'login', label: '曾登入' },
]
const EXPORT_DEFAULT = ['hostname', 'ip', 'department', 'contact', 'username', 'collected', 'note', 'pwExpired', 'disabled', 'sudo']
const showExport = ref(false)
const exportSel = reactive<Record<string, boolean>>(Object.fromEntries(EXPORT_COLS.map(c => [c.key, EXPORT_DEFAULT.includes(c.key)])))
function doExport() {
  const cols = EXPORT_COLS.filter(c => exportSel[c.key]).map(c => c.key)
  if (!cols.length) { showToast('至少要選一個欄位', 'warn'); return }
  const p = new URLSearchParams()
  cols.forEach(c => p.append('cols', c))
  if (kind.value) p.append('kind', kind.value)
  if (hideBuiltin.value) p.append('hide_builtin', 'true')
  window.open(`${apiBase}/api/accounts/matrix/export?${p.toString()}`, '_blank')
  showExport.value = false
}

const TABS = computed(() => [
  { k: 'dashboard', label: '儀表板' },
  // 頁籤數字＝點進去那張表的筆數（同一份 scoped()／篩選結果，OS／主機／帳號條件都算進去）
  { k: 'matrix', label: `合規表 ${mRows.value.length}` },
  { k: 'findings', label: `稽核發現 ${fRows.value.length}` },
  { k: 'accounts', label: `帳號清單 ${accRows.value.length}` },
])
</script>

<template>
  <V2Page width="wide" :back="{ to: '/v2/inventory/account', label: '7.2 帳號盤點' }" :tabs="TABS" :model-value="tab" @update:model-value="pickTab">
    <template #title><h1 class="h1">7.2 帳號合規矩陣</h1></template>
    <template #conclusion>
      <span v-if="summary?.has_data && summary.run" class="muted">最新盤點 <b>{{ summary.run.started_at }}</b>
        （{{ summary.run.trigger === 'schedule' ? '每晚排程' : '手動' }}，{{ summary.run.host_count }} 台<template v-if="summary.failed_count">，{{ summary.failed_count }} 台收集失敗</template>）</span>
      <span v-else-if="summary && !summary.has_data" class="muted">尚未盤點</span>
      <InfoNote>每個帳號對每條規則是否合規；「查不到（權限不足）」不等於合格。逐筆處理在「稽核發現」頁籤；收資料到 7.2 帳號盤點「收集身分」。</InfoNote>
    </template>

    <p v-if="covErr" class="err">{{ covErr }}</p>
    <p v-else-if="coverage" class="cov" :class="{ low: (coverage.pct ?? 0) < 50 }" data-test="coverage">
      帳號收集涵蓋：在管 <b>{{ coverage.managed_hosts.toLocaleString() }}</b> 台中已收 <b>{{ coverage.collected_managed.toLocaleString() }}</b> 台
      <template v-if="coverage.pct !== null">（{{ coverage.pct }}%）</template>
      <span v-if="coverage.collected_outside" class="muted">；另有 {{ coverage.collected_outside }} 台 CIA 未登記</span>
      <NuxtLink v-if="(coverage.pct ?? 0) < 100" to="/v2/onboard?tab=funnel">看還沒收的</NuxtLink>
      <InfoNote>合規表、稽核發現、帳號清單都<b>只包含已收集的主機</b>。涵蓋率低時，「帳號很少」不代表公司帳號少，是還沒收集——還沒收的主機在 6.1 納管漏斗。</InfoNote>
    </p>

    <div class="find">
      <span class="muted">跳到某一台／某個帳號</span>
      <input v-model="hostFindKw" class="in wide" type="search" aria-label="跳到某一台或查帳號"
             placeholder="IP／主機名 → 那一台；帳號名 → 在哪幾台" @keyup.enter="goHost()">
      <InfoNote>這一格不會篩下面的表；要篩表用合規表上方那一格。查不到某台＝這一輪沒收到它的帳號（可能還沒納管、收不到、或被排除），不是「這台沒有帳號」。</InfoNote>
      <button class="btn pri" type="button" :disabled="!hostFindKw.trim()" @click="goHost()">查主機</button>
      <button class="btn" type="button" :disabled="!hostFindKw.trim()" @click="findAccount()">查帳號在哪幾台</button>
      <template v-if="hostFindHits.length > 1">
        <button v-for="h in hostFindHits.slice(0, 6)" :key="h.ip" type="button" class="chip" @click="goHost(h.ip)">{{ h.ip }} <span class="muted">{{ h.hostname || '' }}</span></button>
        <span v-if="hostFindHits.length > 6" class="muted">…共 {{ hostFindHits.length }} 台</span>
      </template>
      <span v-else-if="hostFindKw.trim() && !hostFindHits.length && invHosts.length" class="warn-t">這一輪沒收到這台的帳號</span>
    </div>

    <section v-if="acctHosts !== null" class="box" data-test="acct-hosts">
      <div class="bar">
        <b>帳號 <span class="mono">{{ acctName }}</span></b>
        <span v-if="acctBusy" class="muted">查詢中…</span>
        <span v-else class="muted">在 <b>{{ acctHosts.length }}</b> 台上</span>
        <span class="sp" />
        <a v-if="isAdmin && acctHosts.length" class="btn" :href="`${apiBase}/api/accounts/by-username/export?username=${encodeURIComponent(acctName)}`">匯出 Excel（完整欄位）</a>
        <span v-else-if="acctHosts.length" class="muted" data-test="admin-only">Excel 匯出限管理員</span>
        <button class="btn" type="button" data-test="acct-close" @click="closeAcct">關閉</button>
      </div>
      <p v-if="acctErr" class="err">{{ acctErr }}（這是查詢出錯，不是「沒有這個帳號」）</p>
      <V2Table v-else-if="!acctBusy" :rows="acctRows" :cols="ACCT_COLS" prefix="R" row-key="ip" unit="台" :export-name="`帳號反查_${acctName}`"
               :empty="`這一輪盤點到的主機上沒有叫「${acctName}」的帳號（沒盤點到的主機不在比對範圍內）`" />
    </section>

    <div class="chips">
      <span v-if="ipF" class="fchip">只看這台 · {{ ipF }}（{{ matrixAll.length }} 個帳號、{{ findingsAll.length }} 個發現）<button type="button" @click="setQ({ ip: undefined })">看全部 ✕</button></span>
      <span v-if="osF && tab !== 'dashboard'" class="fchip" data-test="os-chip">只看 {{ OS_LABEL[osF] }} 主機<button type="button" @click="setQ({ os: undefined })">看全部 OS ✕</button></span>
      <span v-if="userF" class="fchip">帳號 · {{ userF }}<button type="button" @click="setQ({ user: undefined })">清除 ✕</button></span>
      <span v-if="privOnly && tab === 'accounts'" class="fchip" data-test="priv-chip">只看特權帳號（sudo／UID 0／標準管理帳號）· {{ accRows.length }} 個<button type="button" @click="setQ({ priv: undefined })">清除 ✕</button></span>
      <span v-if="verdictF && tab === 'findings'" class="fchip" data-test="verdict-chip">只看{{ verdictF === 'unknown' ? '查不到（權限不足）' : '不合規' }}的發現 · {{ fRows.length }} 條<button type="button" @click="setQ({ verdict: undefined })">清除 ✕</button></span>
      <span v-if="lblF && tab === 'findings'" class="fchip">項目 · {{ lblF }}<button type="button" @click="setQ({ lbl: undefined })">清除 ✕</button></span>
    </div>

    <!-- 帳號明細（?acc=）：任一頁籤點帳號名都開這裡 -->
    <section v-if="accKey" class="box detail" data-test="acc-detail">
      <div class="bar">
        <b>帳號明細 <span class="mono">{{ accKey.replace('|', ' · ') }}</span></b>
        <span v-if="detail?.asset_serial"><NuxtLink :to="`/v2/machine/${encodeURIComponent(detail.asset_serial)}`">{{ detail.hostname || detail.ip }} 主機履歷</NuxtLink></span>
        <span class="sp" />
        <button v-if="detail" class="btn" type="button" @click="findAccount(detail.username)">這個帳號在哪幾台</button>
        <NuxtLink v-if="detail" class="btn" :to="`${AM}?ip=${encodeURIComponent(detail.ip)}&tab=accounts`">這台全部帳號</NuxtLink>
        <button class="btn" type="button" @click="setQ({ acc: undefined })">關閉</button>
      </div>
      <p v-if="detailBusy" class="muted">載入中…</p>
      <p v-else-if="detailErr" class="err">{{ detailErr }}</p>
      <template v-else-if="detail">
        <div class="cells">
          <button v-for="c in detailCells" :key="c.key" type="button" class="cellbtn" :class="'c-' + c.c" :title="`看${c.label}＝${c.t}的全部帳號`"
                  @click="onlyCell(c.key, c.k)">
            <span class="cl">{{ c.label }}</span><span>{{ c.t }}</span>
          </button>
        </div>
        <dl class="kv">
          <template v-for="[k, v] in detailFields" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
          <dt>類型</dt>
          <dd>
            <select v-if="canWrite" class="sel" :value="detail.kind_override ? detail.kind : ''" :disabled="!!saving" aria-label="帳號類型"
                    @change="saveKind(detail.ip, detail.username, ($event.target as HTMLSelectElement).value)">
              <option value="">程式判定：{{ KIND_LABEL[detail.kind_computed || detail.kind] || detail.kind_computed || detail.kind }}</option>
              <option v-for="(lbl, k) in KIND_LABEL" :key="k" :value="k">手動：{{ lbl }}</option>
            </select>
            <template v-else>{{ KIND_LABEL[detail.kind] || detail.kind }}<template v-if="detail.kind_override">（手動改過）</template></template>
          </dd>
          <dt>手動備註</dt>
          <dd>
            <template v-if="canWrite && editingNote === nk(detail.ip, detail.username)">
              <input v-model="noteDraft" class="in" aria-label="手動備註" @keyup.enter="saveNote(detail.ip, detail.username)" @keyup.esc="editingNote = ''">
              <button class="btn pri" type="button" :disabled="!!saving" @click="saveNote(detail.ip, detail.username)">存</button>
            </template>
            <template v-else>{{ detail.note || '—' }} <button v-if="canWrite" class="lb" type="button" @click="startNote(detail.ip, detail.username, detail.note)">改</button></template>
          </dd>
        </dl>
        <h3 class="h3">這個帳號的稽核發現 {{ detailFindRows.length }} 條</h3>
        <V2Table :rows="detailFindRows" :cols="DETAIL_FIND_COLS" prefix="D" row-key="id" unit="條" export-name="帳號明細_稽核發現" empty="沒有稽核發現">
          <template #cell-st_t="{ row }">
            <select v-if="canWrite" class="sel" :value="row.status" :disabled="!!saving" aria-label="處置" @change="onStatusChange(row, $event)">
              <option v-for="o in STATUS_OPTS" :key="o.v" :value="o.v">{{ o.t }}</option>
            </select>
            <template v-else>{{ row.st_t }}</template>
            <span v-if="canWrite && (row.exempt_until || row.contradiction)" class="muted"> {{ row.exempt_until ? `至 ${row.exempt_until}` : '' }}{{ row.contradiction ? ' ⚠仍偵測到' : '' }}</span>
          </template>
        </V2Table>
      </template>
    </section>

    <template #tab>
      <V2AccountDashboard v-if="tab === 'dashboard'" />
      <p v-else-if="err" class="err">{{ err }}（這是程式或連線出錯，不是「沒有帳號」）</p>
      <p v-else-if="loading && !accounts.length && !findings.length" class="muted">載入中…</p>

      <!-- 合規表 -->
      <template v-else-if="tab === 'matrix'">
        <p v-if="!accounts.length" class="muted">沒有帳號資料：到 <NuxtLink to="/v2/inventory/account?tab=identity">7.2 帳號盤點「收集身分」</NuxtLink> 收一輪。</p>
        <template v-else>
          <div class="bar">
            <label class="chk"><input type="checkbox" :checked="onlyTodo" @change="setQ({ todo: ($event.target as HTMLInputElement).checked ? '1' : undefined })">只看還需要處理的
              <InfoNote>有失敗的稽核發現、而且處置是「待處理」或「已確認」（已確認＝看過、還沒改好）。核准例外、已修復不算。</InfoNote></label>
            <select class="sel" :class="{ on: envSel }" :value="envSel" aria-label="環境別" @change="setQ({ env: ($event.target as HTMLSelectElement).value || undefined })">
              <option value="">環境別：全部</option>
              <option v-for="[k, n] in optCounts.env" :key="k" :value="k">{{ ENV_LABEL[k] || k }}（{{ n }}）</option>
            </select>
            <select class="sel" :class="{ on: osTypeSel }" :value="osTypeSel" aria-label="OS 類型" @change="setQ({ ost: ($event.target as HTMLSelectElement).value || undefined })">
              <option value="">OS 類型：全部</option>
              <option v-for="[k, n] in optCounts.os" :key="k" :value="k">{{ k }}（{{ n }}）</option>
            </select>
            <select class="sel" :class="{ on: sysSel }" :value="sysSel" aria-label="業務系統" @change="setQ({ sys: ($event.target as HTMLSelectElement).value || undefined })">
              <option value="">業務系統：全部</option>
              <option v-for="[id, v] in optCounts.sys" :key="id" :value="id">{{ id }} {{ v.name }}（{{ v.n }}）</option>
            </select>
            <select class="sel" :class="{ on: kind }" :value="kind" aria-label="類型" @change="setQ({ kind: ($event.target as HTMLSelectElement).value || undefined, tab: 'matrix' })">
              <option value="">全部類型</option>
              <option v-for="(lbl, k) in KIND_LABEL" :key="k" :value="k">{{ lbl }}</option>
            </select>
            <label class="chk"><input type="checkbox" :checked="hideBuiltin" @change="setQ({ hb: ($event.target as HTMLInputElement).checked ? '1' : undefined })">拉掉內建帳號</label>
            <input v-model="mkwInput" type="search" class="in wide" aria-label="篩選合規表" placeholder="篩這張表：人名、部門、主機、IP、帳號、AP ID、系統…">
            <InfoNote>空白隔開的多個詞都要符合；結果多一欄「命中欄位」，看得出是命中窗口還是帳號備註。</InfoNote>
          </div>
          <div class="bar">
            <span class="muted">欄位篩選</span>
            <label v-for="c in MATRIX_COLS" :key="c.key" class="cf">
              {{ c.label }}
              <select class="sel" :class="{ on: colVal(c.key) }" :value="colVal(c.key)" :aria-label="`${c.label}篩選`" @change="setCol(c.key, ($event.target as HTMLSelectElement).value)">
                <option value="">全部</option>
                <option v-for="o in c.opts" :key="o[0]" :value="o[0]">{{ o[1] }}</option>
              </select>
            </label>
            <button v-if="mFilterActive" class="btn" type="button" @click="clearMFilters">清空篩選</button>
          </div>
          <p v-if="mkw.trim() && hitSummary.length" class="muted">「{{ mkw.trim() }}」符合 {{ mRows.length }} 筆，命中：
            <span v-for="[l, n] in hitSummary" :key="l" class="chip">{{ l }} {{ n }}</span></p>
          <p v-if="mkw.trim() && !mRows.length" class="warn-t">
            「{{ mkw.trim() }}」在目前的帳號資料裡 0 筆。
            <template v-if="explain?.error">（查資產庫失敗：{{ explain.error }}）</template>
            <template v-else-if="explain && explain.machines">資產庫有 <b>{{ explain.machines }}</b> 台對得到，其中 <b>{{ explain.not_collected_count }}</b> 台還沒收集帳號<template v-if="explain.not_collected.length">：{{ explain.not_collected.join('、') }}<template v-if="explain.not_collected_count > explain.not_collected.length">…</template></template>——是還沒盤到，不是沒有帳號。</template>
            <template v-else-if="explain">資產庫也找不到對得上的機器（可能打錯，或這個人／部門沒有負責任何機器）。</template>
          </p>
          <div class="bar">
            <span class="muted">顯示 {{ mRows.length }} / {{ matrixAll.length }}</span>
            <InfoNote><b class="c-bad">紅</b>＝問題　<b class="c-warn">黃</b>＝注意　<b class="c-ok">綠</b>＝正常　<b class="c-needroot">藍</b>＝查不到。點狀態格＝再篩這一欄同一個值；點帳號看明細。</InfoNote>
            <span class="sp" />
            <span v-if="!isAdmin" class="muted" data-test="admin-only">標準／全匯出／自選欄位限管理員</span>
            <template v-else>
              <button class="btn" type="button" :disabled="!!exporting" @click="exportFixed('standard')">{{ exporting === 'standard' ? '匯出中…' : '標準帳號盤點' }}</button>
              <button class="btn" type="button" :disabled="!!exporting" @click="exportFixed('full')">{{ exporting === 'full' ? '匯出中…' : '全匯出' }}</button>
              <button class="btn" type="button" @click="showExport = true">自選欄位…</button>
              <InfoNote>標準帳號盤點＝公司現行 18 欄格式，可直接交出去；全匯出＝系統知道的全部欄位；自選欄位＝只勾要給稽核的欄位。下面表格的「匯出」是目前篩選後畫面上的這份。</InfoNote>
            </template>
          </div>
          <V2Table :rows="mRows" :cols="MATRIX_TCOLS" prefix="M" row-key="rk" unit="個帳號" export-name="帳號合規表" empty="沒有符合條件的帳號" data-test="matrix-table">
            <template #cell-username="{ row }">
              <NuxtLink :to="accLink(row)">{{ row.username }}</NuxtLink>
              <div v-if="row.last_seen" class="sub">盤點 {{ String(row.last_seen).slice(0, 10) }}</div>
            </template>
            <template #cell-note_t="{ row }">
              <div v-if="row.gecos" class="sub">{{ row.gecos }}</div>
              <template v-if="canWrite && editingNote === nk(row.ip, row.username)">
                <input v-model="noteDraft" class="in sm" aria-label="手動備註" @keyup.enter="saveNote(row.ip, row.username)" @keyup.esc="editingNote = ''">
                <button class="btn pri" type="button" :disabled="!!saving" @click="saveNote(row.ip, row.username)">存</button>
              </template>
              <button v-else-if="canWrite" type="button" class="lb" @click="startNote(row.ip, row.username, row.note)">{{ row.note ? row.note : '＋備註' }}</button>
              <span v-else>{{ row.note || (row.gecos ? '' : '—') }}</span>
            </template>
            <template #cell-kind_t="{ row }">
              <select v-if="canWrite" class="sel sm" :value="row.kind_override ? row.kind : ''" :disabled="!!saving" aria-label="帳號類型"
                      @change="saveKind(row.ip, row.username, ($event.target as HTMLSelectElement).value)">
                <option value="">{{ KIND_LABEL[row.kind_computed || row.kind] || row.kind_computed || row.kind }}</option>
                <option v-for="(lbl, k) in KIND_LABEL" :key="k" :value="k">✎ {{ lbl }}</option>
              </select>
              <template v-else>{{ row.kind_t }}<span v-if="row.kind_override" class="muted"> ✎</span></template>
            </template>
            <template v-for="c in MATRIX_SLOTS" :key="c.key" #[c.slot]="{ row }">
              <button type="button" class="cellbtn" :class="'c-' + row['_' + c.key + '_c']" :title="`只看${c.label}＝${row['_' + c.key + '_t']}`"
                      @click="setCol(c.key, row['_' + c.key])">{{ row['_' + c.key + '_t'] }}</button>
            </template>
          </V2Table>

          <div v-if="showExport" class="modal-back" @click.self="showExport = false">
            <div class="modal" role="dialog" aria-label="自選欄位匯出">
              <h3 class="h3">匯出 Excel — 選要給稽核的欄位 <InfoNote>只勾必要欄位（資料最小化）。匯出的是目前「類型／拉掉內建」篩選後的帳號；細部篩選可在 Excel 內自己做。</InfoNote></h3>
              <div class="expcols">
                <label v-for="c in EXPORT_COLS" :key="c.key" class="chk"><input v-model="exportSel[c.key]" type="checkbox">{{ c.label }}</label>
              </div>
              <div class="bar"><span class="sp" />
                <button class="btn" type="button" @click="showExport = false">取消</button>
                <button class="btn pri" type="button" @click="doExport">匯出</button>
              </div>
            </div>
          </div>
        </template>
      </template>

      <!-- 稽核發現 -->
      <template v-else-if="tab === 'findings'">
        <div class="bar">
          <select class="sel" :class="{ on: sev }" :value="sev" aria-label="風險等級" @change="setQ({ sev: ($event.target as HTMLSelectElement).value || undefined, tab: 'findings' })">
            <option value="">全部風險等級</option>
            <option value="high">高</option>
            <option value="medium">中</option>
            <option value="low">低（含查不到）</option>
          </select>
          <label class="chk"><input type="checkbox" :checked="grouped" @change="setQ({ grp: ($event.target as HTMLInputElement).checked ? '1' : undefined })">依項目分組</label>
          <span class="sp" />
          <a v-if="isAdmin" class="btn" :href="`${apiBase}/api/accounts/findings/export`" target="_blank" rel="noopener">匯出 Excel（全部發現）</a>
          <span v-else class="muted" data-test="admin-only">Excel 匯出限管理員</span>
        </div>
        <p v-if="!findings.length" class="muted">
          <template v-if="!summary?.has_data">還沒盤點過：到 7.2 帳號盤點「收集身分」對已納管主機收一輪。</template>
          <template v-else>這個條件下沒有稽核發現（或都已核准例外／確認）。</template>
        </p>
        <V2Table v-else-if="grouped" :rows="groupRows" :cols="GROUP_COLS" prefix="G" row-key="label" unit="項" export-name="稽核發現_依項目" empty="沒有符合的發現" />
        <V2Table v-else :rows="fRows" :cols="FIND_COLS" prefix="F" row-key="id" unit="條" export-name="稽核發現" empty="沒有符合的發現" data-test="findings-table">
          <template #cell-username="{ row }">
            <NuxtLink :to="accLink(row)" title="看這個帳號的明細">{{ row.username }}</NuxtLink>
            <NuxtLink class="sub-l" :to="qlink({ user: row.username })" title="只看這個帳號在所有主機上的紀錄">全部主機</NuxtLink>
          </template>
          <template #cell-st_t="{ row }">
            <select v-if="canWrite" class="sel sm" :class="'st-' + row.status" :value="row.status" :disabled="!!saving" aria-label="處置" @change="onStatusChange(row, $event)">
              <option v-for="o in STATUS_OPTS" :key="o.v" :value="o.v">{{ o.t }}</option>
            </select>
            <template v-else>{{ STATUS_LABEL[row.status] ?? row.status }}</template>
            <span v-if="row.contradiction" class="warn-t" title="標為已修復但仍偵測到"> ⚠仍偵測到</span>
            <span v-if="row.exempt_until" class="muted"> 至 {{ row.exempt_until }}</span>
          </template>
          <template #cell-note="{ row }">
            <template v-if="canWrite && editingNote === nk(row.ip, row.username)">
              <input v-model="noteDraft" class="in sm" aria-label="說明" placeholder="輸入說明…" @keyup.enter="saveNote(row.ip, row.username)" @keyup.esc="editingNote = ''">
              <button class="btn pri" type="button" :disabled="!!saving" @click="saveNote(row.ip, row.username)">存</button>
            </template>
            <button v-else-if="canWrite" type="button" class="lb" @click="startNote(row.ip, row.username, row.note)">{{ row.note || '＋加說明' }}</button>
            <template v-else>{{ row.note || '—' }}</template>
          </template>
        </V2Table>
      </template>

      <!-- 帳號清單 -->
      <template v-else-if="tab === 'accounts'">
        <div class="bar">
          <select class="sel" :class="{ on: kind }" :value="kind" aria-label="類型" @change="setQ({ kind: ($event.target as HTMLSelectElement).value || undefined, tab: 'accounts' })">
            <option value="">全部類型</option>
            <option v-for="(lbl, k) in KIND_LABEL" :key="k" :value="k">{{ lbl }}</option>
          </select>
          <label class="chk"><input type="checkbox" :checked="sudoerOnly" @change="setQ({ sudoer: ($event.target as HTMLInputElement).checked ? '1' : undefined, tab: 'accounts' })">只看有 sudo／本機管理員</label>
          <label class="chk"><input type="checkbox" :checked="hideBuiltin" @change="setQ({ hb: ($event.target as HTMLInputElement).checked ? '1' : undefined })">拉掉內建帳號</label>
          <span v-if="hideBuiltin && hiddenBuiltin > 0" class="muted">已隱藏 {{ hiddenBuiltin }} 個乾淨的系統／內建帳號<InfoNote>有稽核發現的內建帳號仍會顯示。</InfoNote></span>
        </div>
        <V2Table :rows="accRows" :cols="ACC_COLS" prefix="A" row-key="rk" unit="個帳號" export-name="帳號清單"
                 :empty="hideBuiltin ? '拉掉內建帳號後沒有其他帳號了' : '沒有帳號資料'" data-test="accounts-table">
          <template #cell-kind_t="{ row }">
            <select v-if="canWrite" class="sel sm" :value="row.kind_override ? row.kind : ''" :disabled="!!saving" aria-label="帳號類型"
                    @change="saveKind(row.ip, row.username, ($event.target as HTMLSelectElement).value)">
              <option value="">{{ KIND_LABEL[row.kind_computed || row.kind] || row.kind_computed || row.kind }}</option>
              <option v-for="(lbl, k) in KIND_LABEL" :key="k" :value="k">✎ {{ lbl }}</option>
            </select>
            <template v-else>{{ row.kind_t }}<span v-if="row.kind_override" class="muted"> ✎</span></template>
          </template>
        </V2Table>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 12px 0 6px; font-size: 14px; font-weight: 600; color: var(--ink-soft); display: flex; gap: 6px; align-items: center; }
.bar, .find, .chips { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 8px; font-size: 13px; }
.find { padding: 8px 12px; border-radius: 10px; background: var(--sub); }
.sp { flex: 1; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
.warn-t { color: var(--warn-text); font-size: 13px; }
.mono { font-family: var(--disp); }
.cov { font-size: 13px; color: var(--ink); margin: 0 0 6px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.cov.low b { color: var(--warn-text); }
.cov a { color: var(--link); }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.in.wide { min-width: 280px; flex: 1; }
.in.sm { padding: 2px 6px; font-size: 12px; width: 140px; }
.sel { font-family: inherit; font-size: 12px; padding: 4px 6px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.sel.on { border-color: var(--brand); background: var(--mint); }
.sel.sm { padding: 1px 4px; }
.chk { display: inline-flex; gap: 4px; align-items: center; font-size: 13px; }
.cf { display: inline-flex; gap: 4px; align-items: center; font-size: 12px; color: var(--ink-soft); }
.btn { font-family: inherit; font-size: 12px; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; text-decoration: none; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--type-on, #fff); }
.btn:disabled { opacity: .5; cursor: default; }
.chip { font-family: inherit; font-size: 12px; padding: 2px 9px; border-radius: 12px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.fchip { display: inline-flex; gap: 8px; align-items: center; padding: 4px 10px; border-radius: 10px; border: 1px solid var(--brand); background: var(--mint); font-weight: 600; font-size: 13px; }
.fchip button { font-family: inherit; border: 1px solid var(--border-strong); background: var(--card); border-radius: 999px; padding: 1px 10px; font-size: 12px; cursor: pointer; color: var(--ink); }
.box { margin: 8px 0 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.detail { border-color: var(--brand); }
.box a, .lb { color: var(--link); }
.lb { background: none; border: 0; cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; text-align: left; }
.kv { display: grid; grid-template-columns: max-content 1fr max-content 1fr; gap: 4px 14px; font-size: 13px; margin: 8px 0; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.cells { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.cellbtn { font-family: inherit; font-size: 12px; padding: 1px 8px; border-radius: 8px; border: 1px solid transparent; background: none; cursor: pointer; color: var(--ink-soft); white-space: nowrap; display: inline-flex; gap: 6px; }
.cellbtn:hover { border-color: var(--border-strong); }
.cellbtn .cl { color: var(--muted); }
.c-bad { color: var(--bad); background: var(--bad-soft); font-weight: 600; }
.c-warn { color: var(--warn-text); background: var(--warn-soft); }
.c-ok { color: var(--good); }
.c-needroot { color: var(--link); background: var(--sub); }
.c-dim { color: var(--muted); }
b.c-bad, b.c-warn, b.c-ok, b.c-needroot { background: none; }
.sub { font-size: 11px; color: var(--muted); }
.sub-l { margin-left: 6px; font-size: 11px; }
.modal-back { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { background: var(--card); border-radius: var(--radius); padding: 16px 18px; width: min(560px, 92vw); box-shadow: var(--shadow); }
.expcols { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; margin: 8px 0 12px; }
@media (max-width: 760px) { .kv { grid-template-columns: max-content 1fr; } }
</style>
