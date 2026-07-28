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
  run?: { started_at: string; host_count: number; needs_root_count: number }
}

const { apiFetch } = useApi()
const { showToast } = useToast()

// 合規表頁：預設就是整張表。稽核發現/帳號清單是次要分頁。
// 儀表板已獨立成 /accounts；從那邊點框框會帶 query 進來預選分頁＋套篩選。
const tab = ref<'matrix' | 'findings' | 'accounts'>('matrix')
const findings = ref<Finding[]>([])
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

const fRows = computed(() => findings.value)
const aRows = computed(() => accounts.value)
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
function readLoadQuery() {
  const q = route.query
  if (q.tab === 'findings' || q.tab === 'accounts' || q.tab === 'matrix') tab.value = q.tab as any
  if (typeof q.sev === 'string') sevFilter.value = q.sev
  if (typeof q.kind === 'string') kindFilter.value = q.kind
  if (q.sudoer === '1') { sudoerOnly.value = true; tab.value = 'accounts' }
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
const matrixData = computed(() => accounts.value.map(a => {
  const row: any = { ...a }
  for (const c of MATRIX_COLS) row['_' + c.key] = c.fn(a).k
  return row
}))
const matrixFiltered = computed(() => matrixData.value.filter(r =>
  MATRIX_COLS.every(c => !colFilter[c.key] || r['_' + c.key] === colFilter[c.key])))
const mRows = computed(() => matrixFiltered.value)
const { sortKey: mKey, sortDir: mDir, toggle: mToggle, sorted: mSorted } = useSort(mRows, 'ip')

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

// ===== 可選欄位匯出：稽核只要看的欄位，資料最小化 =====
const EXPORT_COLS = [
  { key: 'hostname', label: '主機' }, { key: 'ip', label: 'IP' }, { key: 'username', label: '帳號' },
  { key: 'note', label: '備註' }, { key: 'kind', label: '類型' },
  { key: 'pwExpired', label: '密碼過期' }, { key: 'disabled', label: '帳號停用' },
  { key: 'sudo', label: 'sudo 權限' }, { key: 'empty', label: '空密碼' },
  { key: 'uid0', label: 'UID 0' }, { key: 'keys', label: '免密碼金鑰' }, { key: 'login', label: '曾登入' },
]
const EXPORT_DEFAULT = ['hostname', 'ip', 'username', 'note', 'pwExpired', 'disabled', 'sudo']
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
</script>

<template>
  <div>
    <div class="section-divider">帳號合規表</div>

    <p class="lead">
      一列一個帳號、一欄一種合規狀態。要看彙總數字到 <NuxtLink class="dl" to="/accounts">帳號儀表板</NuxtLink>；
      要收資料到 <NuxtLink class="dl" to="/account-ops">盤點作業</NuxtLink>。
    </p>

    <div class="tabs">
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

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>

    <!-- 合規表：一列一帳號、一欄一狀態，可排序＋漏斗篩選 -->
    <template v-else-if="tab === 'matrix'">
      <p v-if="accounts.length === 0" class="muted">
        沒有帳號資料，到 <NuxtLink class="dl" to="/account-ops">盤點作業</NuxtLink> 收一輪。
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
        <span class="th-note">顯示 {{ mSorted.length }} / {{ accounts.length }}　<b class="c-bad">紅</b>=問題 <b class="c-warn">黃</b>=注意 <b class="c-ok">綠</b>=正常</span>
        <button class="btn primary" type="button" @click="showExport = true">匯出 Excel…</button>
      </div>
      <div v-if="accounts.length" class="tbl-scroll">
        <table class="tbl matrix-tbl">
          <thead>
            <tr>
              <SortTh k="hostname" :active="mKey" :dir="mDir" @sort="mToggle">主機</SortTh>
              <SortTh k="ip" :active="mKey" :dir="mDir" @sort="mToggle">IP</SortTh>
              <SortTh k="username" :active="mKey" :dir="mDir" @sort="mToggle">帳號</SortTh>
              <SortTh k="gecos" :active="mKey" :dir="mDir" @sort="mToggle">備註</SortTh>
              <SortTh k="kind" :active="mKey" :dir="mDir" @sort="mToggle">類型</SortTh>
              <SortTh v-for="c in MATRIX_COLS" :key="c.key" :k="'_' + c.key" :active="mKey" :dir="mDir" @sort="mToggle">{{ c.label }}</SortTh>
            </tr>
            <tr class="filter-row">
              <th colspan="5" class="filter-hint">漏斗篩選 →</th>
              <th v-for="c in MATRIX_COLS" :key="c.key">
                <select v-model="colFilter[c.key]" class="fsel" :class="{ on: colFilter[c.key] }">
                  <option value="">全部</option>
                  <option v-for="o in c.opts" :key="o[0]" :value="o[0]">{{ o[1] }}</option>
                </select>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in mSorted" :key="a.id" :class="{ gone: a.gone_at }">
              <td>
                <NuxtLink v-if="a.asset_serial" class="dl" :to="`/assets/${a.asset_serial}`">{{ a.hostname || a.ip }}</NuxtLink>
                <template v-else>{{ a.hostname || '—' }}</template>
              </td>
              <td class="mono dim">{{ a.ip }}</td>
              <td class="mono">{{ a.username }}</td>
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
              <td class="dim">{{ KIND_LABEL[a.kind] || a.kind }}</td>
              <td v-for="c in MATRIX_COLS" :key="c.key"><span class="cell" :class="c.fn(a).c">{{ c.fn(a).t }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 匯出對話框：自己勾要給稽核的欄位，資料最小化 -->
      <div v-if="showExport" class="modal-back" @click.self="showExport = false">
        <div class="modal">
          <h3>匯出 Excel — 選要給稽核的欄位</h3>
          <p class="modal-note">只勾必要欄位，不必把所有資料都攤給稽核。匯出的是目前「類型／拉掉內建」篩選後的帳號；細部篩選可在 Excel 內自己做。</p>
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
            <th>說明<span class="th-hint">（可手動輸入）</span></th>
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
              <NuxtLink class="dl" :to="{ path: '/accounts', query: { user: f.username } }">{{ f.username }}</NuxtLink>
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
                  <NuxtLink class="dl" :to="{ path: '/accounts', query: { user: f.username } }">{{ f.username }}</NuxtLink>
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

    <!-- 帳號清單 -->
    <template v-else>
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
            <td>{{ KIND_LABEL[a.kind] || a.kind }}</td>
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
.lead { color: var(--muted); margin: 0 0 16px; line-height: 1.7; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; }
.tile.bad { border-color: rgba(224,108,108,.5); }
.tile.warn { border-color: rgba(230,170,60,.45); }
.t-num { font-size: 26px; font-weight: 700; color: var(--brand, #26a889); line-height: 1.1; }
.tile.bad .t-num { color: #e06c6c; }
.tile.warn .t-num { color: #d9a441; }
.t-num .of { font-size: 14px; color: var(--muted); font-weight: 500; }
.t-lbl { font-size: 12px; color: var(--muted); margin-top: 4px; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .7; }

.bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.spacer { flex: 1; }
.btn { border-radius: 9px; border: 1px solid var(--border); background: transparent; color: inherit;
  padding: 7px 14px; cursor: pointer; font-size: 13px; font-family: inherit; }
.btn.primary { background: var(--brand, #26a889); border-color: transparent; color: #04120e; font-weight: 600; }
.btn:disabled { opacity: .55; cursor: progress; }
.sel { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 9px; padding: 6px 10px; font-size: 13px; }
.chk { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 5px; cursor: pointer; }
.when, .th-note { font-size: 12px; color: var(--muted); }
.hidden-note { font-size: 11px; color: var(--muted); opacity: .8; }
.ip-sub { display: block; font-size: 10px; }
.gecos { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.probe-box { border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; margin-bottom: 16px; }
.probe-toggle { background: none; border: none; color: inherit; cursor: pointer; font-size: 13px; font-family: inherit; padding: 2px 0; }
.probe-lead { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 8px 0; }
.probe-lead b { color: #8ea6dd; }
.probe-result { margin-top: 10px; }
.pr-head { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; margin-bottom: 8px; align-items: center; }
.pr-head .ok { color: #26a889; }
.pr-head .bad { color: #e06c6c; }
.vd { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.vd.ok { background: rgba(38,168,137,.16); color: #26a889; }
.vd.bad { background: rgba(224,108,108,.16); color: #e06c6c; }
.vd.warn { background: rgba(230,170,60,.16); color: #d9a441; }
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
.provision b { color: #8ea6dd; }
.pv-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pv-note { margin: 6px 0 0; }
.provision code { background: rgba(255,255,255,.07); padding: 1px 5px; border-radius: 4px; }

.gap { background: rgba(230,170,60,.08); border: 1px solid rgba(230,170,60,.35);
  border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; font-size: 12px;
  color: var(--muted); line-height: 1.75; }
.gap b { color: #d9a441; }
.gap.fail { background: rgba(224,108,108,.08); border-color: rgba(224,108,108,.4); }
.gap.fail b { color: #e06c6c; }
.fail-err { margin-top: 6px; font-size: 11px; color: var(--muted); }
.excluded-note { font-size: 12px; color: var(--muted); margin-bottom: 14px; padding: 8px 12px; border: 1px dashed var(--border); border-radius: 8px; }
.excluded-note b { color: var(--ink, inherit); }
.exclude-box { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; }
.btn.small { padding: 3px 10px; font-size: 12px; }
.right { text-align: right; }
.gap code { background: rgba(255,255,255,.07); padding: 1px 5px; border-radius: 4px; }
.link-btn { background: none; border: none; color: var(--brand, #26a889); cursor: pointer;
  font-size: 12px; padding: 0 4px; font-family: inherit; text-decoration: underline; }
.sudo { background: rgba(0,0,0,.35); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; margin: 8px 0 4px; font-size: 11px; overflow-x: auto; white-space: pre; }
.sudo-note { margin: 0; font-size: 11px; line-height: 1.7; }

.sev { font-size: 11px; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.sev.high { background: rgba(224,108,108,.18); color: #e06c6c; }
.sev.medium { background: rgba(230,170,60,.18); color: #d9a441; }
.sev.low { background: rgba(255,255,255,.08); color: var(--muted); }
.sev.unk { background: rgba(120,150,220,.16); color: #8ea6dd; }
.law { font-size: 10px; color: var(--muted); border-bottom: 1px dotted var(--border);
  margin-left: 6px; cursor: help; }
.th-hint { font-size: 9px; color: var(--muted); font-weight: 400; margin-left: 3px; }

/* 稽核發現表：固定欄寬、拉滿，不再有的擠有的空 */
.findings-tbl { table-layout: fixed; width: 100%; }
.findings-tbl td { overflow: hidden; text-overflow: ellipsis; }
.findings-tbl td.dim, .findings-tbl td.note-cell { white-space: normal; }
.note-cell { }
.note-view { cursor: pointer; display: inline-block; min-width: 60px; font-size: 12px; }
.note-view:hover { color: var(--brand, #26a889); }
.note-add { color: var(--muted); opacity: .6; font-size: 11px; }
.note-input { background: var(--card); border: 1px solid var(--brand, #26a889); color: inherit;
  border-radius: 6px; padding: 3px 6px; font-size: 12px; width: 120px; font-family: inherit; }
.note-save { background: var(--brand, #26a889); border: none; color: #04120e; border-radius: 6px;
  padding: 3px 8px; margin-left: 4px; font-size: 11px; cursor: pointer; font-family: inherit; }

/* 發現生命週期 */
.st-sel { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 6px; padding: 2px 4px; font-size: 11px; font-family: inherit; }
.st-sel.st-open { border-color: rgba(224,108,108,.5); }
.st-sel.st-ack { color: var(--muted); }
.st-sel.st-exception { border-color: rgba(120,150,220,.5); color: #8ea6dd; }
.st-sel.st-fixed { color: #26a889; }
.st-done td { opacity: .55; }
.warn-mini { font-size: 10px; color: #e06c6c; margin-left: 4px; }
.ex-until { font-size: 10px; display: block; }
.grp-row { cursor: pointer; }
.grp-row:hover { background: rgba(255,255,255,.04); }
.grp-row b { font-size: 13px; }
.grp-count { color: var(--muted); font-size: 12px; margin-left: 8px; }
.grp-item td:first-child { border-left: 2px solid var(--border); }

.tag { font-size: 10px; padding: 1px 7px; border-radius: 6px; border: 1px solid var(--border); }
.tag.danger { background: rgba(224,108,108,.16); color: #e06c6c; border-color: transparent; }
.tag.warn { background: rgba(230,170,60,.16); color: #d9a441; border-color: transparent; }
.tag.mgmt { background: rgba(120,150,220,.16); color: #8ea6dd; border-color: transparent; }
.grp { display: block; font-size: 10px; }
.needroot { font-size: 10px; color: #8ea6dd; opacity: .85; }
.danger { color: #e06c6c; font-weight: 700; }
.dim { color: var(--muted); }
.tbl tr.gone td { opacity: .5; }

/* 合規矩陣：一欄一狀態，紅=有問題 / 黃=注意 / 綠=正常 / 灰=無 / 藍=查不到 */
.tbl-scroll { overflow-x: auto; }
.matrix-tbl { width: 100%; }
.matrix-tbl th { white-space: nowrap; }
.matrix-tbl td { text-align: center; }
.matrix-tbl td:nth-child(-n+4) { text-align: left; }
.cell { display: inline-block; min-width: 62px; padding: 3px 10px; border-radius: 999px;
  font-size: 11.5px; font-weight: 600; background: rgba(255,255,255,.05); color: var(--muted); }
.cell.bad { background: rgba(224,108,108,.18); color: #e06c6c; }
.cell.warn { background: rgba(230,170,60,.18); color: #d9a441; }
.cell.ok { background: rgba(38,168,137,.16); color: #26a889; }
.cell.needroot { background: rgba(120,150,220,.14); color: #8ea6dd; }
.cell.dim { background: transparent; color: var(--muted); opacity: .55; font-weight: 400; }
.dl { color: var(--brand, #26a889); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.c-bad { color: #e06c6c; }
.c-warn { color: #d9a441; }
.c-ok { color: #26a889; }

/* 儀表板檢查項框框 */
.dash-h { font-size: 14px; color: var(--muted); font-weight: 600; margin: 22px 0 10px; }
.tiles.checks { grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); }
.tile.check { cursor: pointer; transition: transform .12s, border-color .12s; }
.tile.check:hover { transform: translateY(-2px); }
.tile.check.bad { border-color: rgba(224,108,108,.5); }
.tile.check.bad .t-num { color: #e06c6c; }
.tile.check.warn { border-color: rgba(230,170,60,.45); }
.tile.check.warn .t-num { color: #d9a441; }
.tile.check.zero { cursor: default; opacity: .5; }
.tile.check.zero:hover { transform: none; }
.tile.check.zero .t-num { color: var(--muted); }

/* 漏斗篩選列 */
.filter-row th { padding: 4px 6px; background: rgba(0,0,0,.2); }
.filter-hint { text-align: right !important; font-size: 11px; color: var(--muted); font-weight: 400; white-space: nowrap; }
.fsel { background: var(--card); border: 1px solid var(--border); color: inherit; border-radius: 6px;
  padding: 3px 4px; font-size: 11px; font-family: inherit; width: 100%; min-width: 66px; }
.fsel.on { border-color: var(--brand, #26a889); color: var(--brand, #26a889); }
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
</style>
