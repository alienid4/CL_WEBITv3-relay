<script setup lang="ts">
// 1-4 分佈統計：所有「A × B 各幾台」的交叉表集中在這頁，分三個分頁。
//
// 使用者 2026-09-10 定的：
//   分頁＝基礎設施／AP 系統／機房×環境
//   「基礎跟 AP 系統要分開」——依 APID 前綴（I- 基礎、N- 業務），
//     N-207 虛擬化平台由他點名搬到基礎（後端 _KIND_OVERRIDE）
//   「使用者測試(UAT)／開發環境(DEV) 跟測試合併」——所以環境只剩四欄
//   「每個數字要可以深入，深入後以機房角度來看」——點任何一格 → 那格的機房分佈
//
// ⚠️ AP／DB 那塊是**推論**，221 實測只推得出 7%。所以「未分類」跟 AP、DB 並排，
// 不藏起來——藏起來會讓人拿一個 7% 樣本的分類去做決策。

interface Composition {
  by_location?: Record<string, number>
  by_location_env?: Record<string, Record<string, number>>
}
interface SysRow {
  api_id: string; name: string | null; ap_department: string | null
  kind: string; group: string; sys_class: string; sys_class_core: boolean
  sys_class_override?: string | null; sys_class_computed?: string
  total: number; by_env: Record<string, number>
}
interface SysOut {
  systems: SysRow[]; system_count: number; shown: number; envs: string[]
  kind: string | null; group_order: string[]; class_order: string[]
  mapped: number; unmapped: number; total_assets: number
  kind_overrides: Record<string, string>
}
interface CellAsset {
  asset_serial: string; hostname: string | null; ip: string | null
  os: string | null; environment: string; location: string
  role: string; onboarded: boolean; purpose: string | null; device_model: string | null
}
interface Drill {
  api_id: string; name: string | null; ap_department: string | null; ap_owner: string | null
  kind: string; env_filter: string | null; total: number
  locations: { location: string; total: number; by_env: Record<string, number> }[]
  roles: Record<string, number>
  role_matrix: { role: string; by_location: Record<string, number>; total: number }[]
  role_basis: string
}

const { apiFetch } = useApi()
const { showToast } = useToast()

type TabKey = 'infra' | 'ap' | 'matrix' | 'phys'
const TABS: { key: TabKey; label: string }[] = [
  { key: 'infra', label: '基礎設施' },
  { key: 'ap', label: 'AP 系統' },
  { key: 'matrix', label: '機房 × 環境' },
  // 各環境實體機分布（2026-09-12 使用者：從報告頁併進來當第 4 頁）。內容＝共用元件 PhysicalDist。
  { key: 'phys', label: '各環境實體機分布' },
]
const tab = ref<TabKey>('infra')

const comp = ref<Composition | null>(null)
const sys = ref<SysOut | null>(null)
const drill = ref<Drill | null>(null)
const drillBusy = ref('')
const showAll = ref(false)
const loading = ref(true)

const n = (v: number | null | undefined) => (v ?? 0).toLocaleString()

// 照固定順序排，但**不在順序表裡的一律接在後面**，不能丟掉——
// 分組規則隨時可能被改，寫死白名單會讓機器從畫面上默默消失。
const LOC_ORDER = ['板橋', '內湖', '敦南', '分公司', '未填']
const ENV_ORDER = ['正式', '備援', '測試', '未填']
function ordered(present: string[], order: string[]): string[] {
  return [...order.filter(k => present.includes(k)),
          ...present.filter(k => !order.includes(k))]
}
const isGap = (k: string) => k === '未填' || k === '未分類'

// ===== 排序（鐵規則：表格每一欄都要能排）=====
// 交叉表沒辦法直接用 useSort（它吃的是「一列一個物件」，交叉表的欄是動態的），
// 所以這裡自己做一支小的：點欄位就依那一欄的數值排列，再點一次反向。
// **預設不排序**（sortKey 空字串）——固定順序（正式/備援/測試、板橋/內湖/敦南）
// 本身有意義，一進來就被排掉反而看不出那個順序。
function useCellSort(defaultKey = '') {
  const key = ref(defaultKey)
  const dir = ref<'asc' | 'desc'>('desc')
  function toggle(k: string) {
    if (key.value === k) dir.value = dir.value === 'asc' ? 'desc' : 'asc'
    else { key.value = k; dir.value = 'desc' }   // 數字表格第一下給大的先看
  }
  function apply<T>(rows: T[], get: (row: T, k: string) => number | string): T[] {
    if (!key.value) return rows
    const sign = dir.value === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const va = get(a, key.value)
      const vb = get(b, key.value)
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign
      return String(va).localeCompare(String(vb), 'zh-Hant', { numeric: true }) * sign
    })
  }
  return { key, dir, toggle, apply }
}

// ===== 反查：知道機器，想知道它屬於哪個系統 =====
// 使用者 2026-09-10：「SEC01 是哪個 APID 我怎麼找不到」。
// 這頁本來只能由上往下鑽（系統 → 機房 → 哪幾台），沒有反方向的入口。
interface LookupRow {
  asset_serial: string; hostname: string | null; ip: string | null
  api_id: string | null; system_name: string | null; ap_department: string | null
  kind: string | null; group: string | null
  environment: string; location: string; role: string
}
const lookupQ = ref('')
const lookupRows = ref<LookupRow[] | null>(null)
const lookupBusy = ref(false)

async function doLookup() {
  const q = lookupQ.value.trim()
  if (!q) { lookupRows.value = null; return }
  lookupBusy.value = true
  try {
    const r = await apiFetch<{ items: LookupRow[] }>('/api/stats/lookup', { params: { q } })
    lookupRows.value = r.items ?? []
  } catch {
    showToast('查詢失敗', 'error')
  } finally {
    lookupBusy.value = false
  }
}

// 點搜尋結果 → 跳到它所屬的系統並展開。這是「找到之後還要走得過去」，
// 只給答案不給入口，使用者還是得自己回頭捲。
async function gotoSystem(row: LookupRow) {
  if (!row.api_id) {
    showToast('這台沒有填業務系統代碼，沒辦法跳到系統', 'warn', 6000)
    return
  }
  const wantTab: TabKey = row.kind === 'infra' ? 'infra' : 'ap'
  if (tab.value !== wantTab) {
    tab.value = wantTab
    await nextTick()
  }
  if (!showAll.value) await loadSystems(true)   // 可能不在前 10 大裡
  await openDrill(row.api_id, null)
  lookupRows.value = null
}

const mSort = useCellSort()     // 機房 × 環境
const sSort = useCellSort()     // 系統 × 環境
const rSort = useCellSort()     // 角色 × 機房
const cSort = useCellSort()     // 格子明細
const leSort = useCellSort()    // 下鑽：機房 × 環境
const lcSort = useCellSort()    // 下鑽：機房 × 環境某一格的明細

// 下鑽第一張「機房 × 環境」的某一格 → 是哪幾台
const leCell = ref<{ location: string; env: string; items: CellAsset[] } | null>(null)
const leBusy = ref('')
async function openLeCell(location: string, env: string, count: number) {
  if (!drill.value || !count) return
  if (leCell.value && leCell.value.location === location && leCell.value.env === env) {
    leCell.value = null                   // 再點一次收起來
    return
  }
  leBusy.value = location + '|' + env
  try {
    const r = await apiFetch<{ items: CellAsset[] }>(
      '/api/stats/systems/' + encodeURIComponent(drill.value.api_id) + '/assets',
      { params: { location, env } })
    leCell.value = { location, env, items: r.items ?? [] }
  } catch {
    showToast('讀不到這一格的明細', 'error')
  } finally {
    leBusy.value = ''
  }
}
// 換系統時把上一個系統的格子明細收掉，不然會看到別的系統的機器
watch(() => drill.value?.api_id, () => { leCell.value = null })

const sortedDrillLocs = computed(() =>
  leSort.apply(drill.value?.locations ?? [], (l, k) =>
    k === '__loc' ? l.location : k === '__total' ? l.total : (l.by_env[k] ?? 0)))
const sortedLeItems = computed(() =>
  lcSort.apply(leCell.value?.items ?? [], (a, k) => (a as any)[k] ?? ''))
function drillEnvTotal(env: string): number {
  return (drill.value?.locations ?? []).reduce((s, l) => s + (l.by_env[env] ?? 0), 0)
}

// ---- 機房 × 環境 ----
const matrixLocs = computed(() => ordered(Object.keys(comp.value?.by_location ?? {}), LOC_ORDER))
const matrixEnvsRaw = computed(() => {
  const seen = new Set<string>()
  for (const m of Object.values(comp.value?.by_location_env ?? {})) {
    for (const k of Object.keys(m as Record<string, number>)) seen.add(k)
  }
  return ordered([...seen], ENV_ORDER)
})
// 排序後的列。key 是機房名（依那個機房的台數排）或 '__env'（依環境名排）
// 或 '__total'（依整列合計排）。
const matrixEnvs = computed(() => mSort.apply(matrixEnvsRaw.value, (env, k) =>
  k === '__env' ? env : k === '__total' ? rowTotal(env) : cellCount(k, env)))
function cellCount(loc: string, env: string): number {
  return comp.value?.by_location_env?.[loc]?.[env] ?? 0
}
function rowTotal(env: string) { return matrixLocs.value.reduce((s, l) => s + cellCount(l, env), 0) }
function colTotal(loc: string) { return matrixEnvs.value.reduce((s, e) => s + cellCount(loc, e), 0) }
const matrixTotal = computed(() => matrixLocs.value.reduce((s, l) => s + colTotal(l), 0))
// 一律走 *_group 參數而不是精確比對，否則「測試」那格（含 UAT/DEV/OA）
// 點進去會少幾台，數字對不起來。
function cellLink(loc: string, env: string) {
  return { path: '/assets', query: { location_group: loc, environment_group: env } }
}
// 聯集不是相加——同一台可能兩欄都沒填，相加會灌水。
const cellsUnfilled = computed(() => {
  let x = 0
  for (const l of matrixLocs.value) {
    for (const e of matrixEnvs.value) if (l === '未填' || e === '未填') x += cellCount(l, e)
  }
  return x
})

// ---- 系統 × 環境 ----
async function loadSystems(_all = false) {
  if (tab.value === 'matrix' || tab.value === 'phys') return   // 這兩頁不用系統×環境資料
  // ⚠️ 基礎設施與 AP 系統都**一律全載**（2026-09-10 抓到的錯）：
  // 兩頁現在都用「分頁頁籤」（基礎設施分群、AP 分級第一/二/三類），而每個頁籤的
  // 小計是用「目前顯示的列」加出來的——套上「前 10 大」之後，小計會少算沒載進來的
  // 系統（例如網路設備顯示 408、實際 412），但看起來就像完整的總數。這種錯最難發現，
  // 所以分頁頁籤一律全載（天條：加總與下鑽走同一份計算）。AP 147 個系統全載仍很輕。
  sys.value = await apiFetch<SysOut>('/api/stats/systems',
                                     { params: { limit: 0, kind: tab.value } })
  showAll.value = true
}

// 點系統 → 展開這個系統的機房 × 環境。
//
// ⚠️ **一律載入全部環境，點某個環境的數字只是把那一欄標亮**（2026-09-10 改）。
// 原本點「正式 8」會只載正式環境，其他環境的機器整批消失——使用者要找 sec02
// 時看的是切過的一小片，於是以為好麥上面沒有那台。他的原話：「就是因為沒有把
// 全部的環境一直展開，所以我以為好麥上面沒有 sec02」。
// 篩掉而不告知，比不能篩更糟：看的人會把「沒看到」當成「沒有」。
const focusEnv = ref<string | null>(null)

async function openDrill(apiId: string, env: string | null) {
  if (drill.value?.api_id === apiId && focusEnv.value === env) {
    drill.value = null                     // 點同一格再一次＝收起來
    focusEnv.value = null
    return
  }
  focusEnv.value = env
  if (drill.value?.api_id === apiId) return   // 同一個系統，只是換標亮的欄，不用重抓
  drillBusy.value = apiId + (env ?? '')
  try {
    // 不帶 env：永遠拿全部環境
    drill.value = await apiFetch<Drill>('/api/stats/systems/' + encodeURIComponent(apiId))
  } catch {
    showToast('讀不到這個系統的明細', 'error')
  } finally {
    drillBusy.value = ''
  }
}
const drillOpenOn = (apiId: string) => drill.value?.api_id === apiId
const isPending = (role: string) => role === '資料不完整'

// 點了某個系統之後，表格只留那一列（使用者 2026-09-10：「點選該系統 出現該系統
// 就好，跟他無關的都拿掉」）。其他系統還在資料裡，只是不畫——按「看全部」回去。
const visibleSystems = computed(() => {
  const all = sys.value?.systems ?? []
  if (!drill.value) return all
  return all.filter(s => s.api_id === drill.value!.api_id)
})

// 基礎設施再分群（使用者 2026-09-10）：網路設備／Storage／其他大型系統／未分群。
// 只有基礎設施那頁分群；AP 系統照台數排就好。
// 排序套在每一群「之內」——分群本身的順序是使用者定的（1 網路設備…6 其他），
// 那個順序有意義，不該被排序打亂。
function sortSystems(rows: SysRow[]): SysRow[] {
  return sSort.apply(rows, (s, k) =>
    k === '__class' ? (sys.value?.class_order ?? []).indexOf(s.sys_class)
      : k === '__name' ? (s.name || s.api_id)
      : k === '__apid' ? s.api_id
        : k === '__dept' ? (s.ap_department || '')
        : k === '__total' ? s.total
          : (s.by_env[k] ?? 0))
}

const groupTotal = (rows: { total: number }[]) => rows.reduce((a, b) => a + b.total, 0)

// 手動改系統分級（未分級的 547 個可自己指定第一/二/三類；分級表重匯不會沖掉）。
const editingClass = ref('')
async function saveClass(apiId: string, cls: string) {
  try {
    await apiFetch('/api/systems/class', { method: 'PUT', body: { api_id: apiId, sys_class: cls } })
    editingClass.value = ''
    await loadSystems()   // 重載：分級頁籤歸屬、排序、小計都要重算
    showToast('分級已更新', 'success')
  } catch (e: any) {
    showToast(`更新失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  }
}

// 每個分頁頁籤（使用者 2026-09-11：「一個卡片就是一個分頁頁籤」，像 Excel 工作表——
// 點哪個看哪個，不是全部堆一張長表）：
//   基礎設施依「分群」（網路設備／Storage／…），AP 系統依「分級」（第一類／第二類／第三類）。
const subGroups = computed<{ group: string; rows: SysRow[] }[]>(() => {
  if (tab.value === 'infra') {
    const order = sys.value?.group_order ?? []
    return order
      .map(g => ({ group: g, rows: sortSystems(visibleSystems.value.filter(s => s.group === g)) }))
      .filter(x => x.rows.length)
  }
  if (tab.value === 'ap') {
    const order = sys.value?.class_order ?? []
    return order
      .map(c => ({ group: c, rows: sortSystems(visibleSystems.value.filter(s => s.sys_class === c)) }))
      .filter(x => x.rows.length)
  }
  return []
})
const subGroup = ref('')   // 目前選的頁籤（分群名或分級名）
watch(subGroups, (gs) => {
  if (!gs.length) { subGroup.value = ''; return }
  if (!gs.some(g => g.group === subGroup.value)) subGroup.value = gs[0].group
}, { immediate: true })
// 表身要畫哪些列：只畫目前頁籤那一群；沒有分頁（例如資料還沒到）就單表。
const shownGroups = computed(() => {
  if (tab.value === 'matrix') return []
  if (!subGroups.value.length) return [{ group: '', rows: sortSystems(visibleSystems.value) }]
  const g = subGroups.value.find(x => x.group === subGroup.value)
  return g ? [g] : []
})

// 點交叉表某一格 → 列出是哪幾台（使用者：「板橋機房 DB 有 18 台，點進去要知道
// 是哪 18 台」）。不能連到資產查詢頁：**角色不是資料庫欄位**，那頁篩不出同一批。
const cell = ref<{ role: string; location: string; items: CellAsset[] } | null>(null)
const cellBusy = ref('')

async function openCell(role: string, location: string, count: number) {
  if (!drill.value || !count) return
  const key = role + '|' + location
  if (cell.value && cell.value.role === role && cell.value.location === location) {
    cell.value = null                     // 再點一次收起來
    return
  }
  cellBusy.value = key
  try {
    const r = await apiFetch<{ items: CellAsset[] }>(
      '/api/stats/systems/' + encodeURIComponent(drill.value.api_id) + '/assets',
      { params: {
        role, location,
        ...(drill.value.env_filter ? { env: drill.value.env_filter } : {}),
      } })
    cell.value = { role, location, items: r.items ?? [] }
  } catch {
    showToast('讀不到這一格的明細', 'error')
  } finally {
    cellBusy.value = ''
  }
}

const sortedRoleMatrix = computed(() =>
  rSort.apply((drill.value?.role_matrix ?? []).filter(x => x.total), (rm, k) =>
    k === '__role' ? rm.role : k === '__total' ? rm.total : (rm.by_location[k] ?? 0)))

// 格子明細清單也要能排（鐵規則）
const sortedCellItems = computed(() =>
  cSort.apply(cell.value?.items ?? [], (a, k) => (a as any)[k] ?? ''))

const drillEnvs = computed(() => {
  const seen = new Set<string>()
  for (const l of drill.value?.locations ?? []) for (const k of Object.keys(l.by_env)) seen.add(k)
  return ordered([...seen], ENV_ORDER)
})

const overrideNote = computed(() => {
  const o = sys.value?.kind_overrides ?? {}
  return Object.keys(o).length ? Object.keys(o).join('、') : ''
})

watch(tab, async () => {
  drill.value = null
  if (tab.value !== 'matrix') await loadSystems(false)
})

onMounted(async () => {
  try {
    comp.value = await apiFetch<Composition>('/api/dashboard/composition')
    await loadSystems(false)
  } catch {
    showToast('分佈統計載入失敗，請稍後再試', 'error')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <div class="section-divider">分佈統計</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>分佈統計</b></div>

    <!-- 反查：知道機器，想知道它屬於哪個系統（下鑽的反方向） -->
    <div class="lookup">
      <input v-model="lookupQ" class="lk-in" type="search"
             placeholder="輸入主機名／IP／資產編號，查它屬於哪個系統"
             @keyup.enter="doLookup" />
      <button class="btn small primary" type="button" :disabled="lookupBusy" @click="doLookup">
        {{ lookupBusy ? '查詢中…' : '查' }}
      </button>
      <button v-if="lookupRows" class="btn small" type="button"
              @click="lookupRows = null; lookupQ = ''">清除</button>
      <InfoNote>這一頁本來只能由上往下鑽（系統 → 機房 → 哪幾台）。<b>知道機器卻不知道它屬於哪個系統</b>時，從這裡查。<br><br>主機名不分大小寫——資料裡是 <code>sec01</code>，你打 <code>SEC01</code> 也找得到。<br><br>查到之後點那一列，會直接跳到它所屬的系統並展開。</InfoNote>
    </div>

    <div v-if="lookupRows" class="card">
      <div class="ck">查詢結果<span class="gcount">{{ n(lookupRows.length) }} 台</span></div>
      <p v-if="!lookupRows.length" class="note">查不到「{{ lookupQ }}」——確認一下有沒有打錯，或這台可能還沒登記在資產庫。</p>
      <div v-else class="tblwrap">
        <table>
          <thead><tr>
            <th>主機名稱</th><th>IP</th><th>業務系統</th><th class="apid">AP ID</th>
            <th>機房</th><th>環境</th><th>角色</th><th></th>
          </tr></thead>
          <tbody>
            <tr v-for="r in lookupRows" :key="r.asset_serial">
              <th class="rowh">
                <NuxtLink :to="`/assets/${r.asset_serial}`" class="dl">
                  {{ r.hostname || '（無主機名）' }}
                </NuxtLink>
              </th>
              <td class="rowh mono">{{ r.ip || '—' }}</td>
              <td class="rowh">
                {{ r.system_name || (r.api_id ? '（對照表沒有這個代碼）' : '（沒填系統代碼）') }}
              </td>
              <td class="apid">{{ r.api_id || '—' }}</td>
              <td class="rowh">{{ r.location }}</td>
              <td class="rowh">{{ r.environment }}</td>
              <td class="rowh">{{ r.role }}</td>
              <td class="ops">
                <button v-if="r.api_id" class="btn small" type="button" @click="gotoSystem(r)">
                  看這個系統
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="tabs">
      <button v-for="t in TABS" :key="t.key" class="tab" :class="{ on: tab === t.key }"
              type="button" @click="tab = t.key">{{ t.label }}</button>
    </div>

    <p v-if="loading" class="note">載入中…</p>

    <!-- 機房 × 環境 -->
    <section v-if="tab === 'matrix' && matrixLocs.length" class="card">
      <div class="ck">機房 × 環境</div>
      <div class="tblwrap">
        <table>
          <thead><tr>
            <!-- 鐵規則：每一欄都要能排。點欄位＝依那一欄的台數排列這些列 -->
            <th class="srt" :class="{ on: mSort.key.value === '__env' }" @click="mSort.toggle('__env')">
              環境＼機房<i class="arw">{{ mSort.key.value === '__env' ? (mSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <th v-for="loc in matrixLocs" :key="loc" class="srt"
                :class="{ gap: isGap(loc), on: mSort.key.value === loc }" @click="mSort.toggle(loc)">
              {{ loc }}<i class="arw">{{ mSort.key.value === loc ? (mSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <th class="tot srt" :class="{ on: mSort.key.value === '__total' }" @click="mSort.toggle('__total')">
              合計<i class="arw">{{ mSort.key.value === '__total' ? (mSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
          </tr></thead>
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
        <b>{{ n(cellsUnfilled) }}</b> 台機房或環境別沒填
        <InfoNote>底色標注意色的是資料缺口，這些台分不進任何實際位置。（不是相加而是聯集：同一台可能兩欄都沒填，相加會灌水。）</InfoNote>
      </p>
    </section>

    <!-- 基礎設施 ／ AP 系統：同一張表，只差 kind -->
    <!-- 各環境實體機分布：從報告頁併進來的第 4 頁籤，用共用元件（母體＝實體機，與其他頁不同） -->
    <PhysicalDist v-if="tab === 'phys'" />

    <section v-if="tab !== 'matrix' && tab !== 'phys' && sys" class="card">
      <div class="ck">
        {{ tab === 'infra' ? '基礎設施' : 'AP 系統' }} × 環境
        <InfoNote>
          <b>怎麼分基礎與 AP</b>：依業務系統代碼的前綴——<code>I-</code> 是基礎設施（交換器、儲存、工作站…），<code>N-</code> 是業務系統。<b>這是照代碼分的，不是人工判斷</b>；如果有系統歸錯邊，跟我說可以改。<span v-if="overrideNote"><br><br>目前人工指定搬過的：<code>{{ overrideNote }}</code>。</span>
          <br><br><b>環境只有四欄</b>：使用者測試(UAT)、開發環境(DEV) 都<b>併進「測試」</b>。原始資料是分開的，這裡是合併後的數字——要跟來源系統對帳時要記得這件事。
          <br><br>「涵蓋 N 台／對不上 M 台」：這張表只算對得上業務系統的機器，<b>對不上的沒有被丟掉，是另外算</b>——不講的話合計會對不起來。「對不上」用全庫算，切分頁不會跳動。
        </InfoNote>
      </div>
      <p class="note">
        本頁涵蓋 <b>{{ n(sys.mapped) }}</b> 台　／
        <span :class="{ warn: sys.unmapped > 0 }">全庫對不上業務系統 <b>{{ n(sys.unmapped) }}</b> 台</span>　／
        共 {{ n(sys.total_assets) }} 台　·　{{ n(sys.system_count) }} 個系統
      </p>
      <!-- 每個分頁頁籤（像 Excel 工作表）：基礎設施依分群、AP 依分級。點哪個看哪個。 -->
      <div v-if="subGroups.length" class="gtabs">
        <button v-for="g in subGroups" :key="g.group" type="button"
                class="gtab" :class="{ on: subGroup === g.group }" @click="subGroup = g.group">
          {{ g.group }}<span class="gtc">{{ n(groupTotal(g.rows)) }}</span>
        </button>
      </div>
      <p v-if="tab === 'infra' && subGroup === '未分群'" class="ungrp-note">
        這些系統放不進你給的三群（網路設備／Storage／其他大型系統）。
        <InfoNote>沒有硬塞進最像的那一群——塞錯了不會有人發現，數字會一路錯下去。<b>告訴我它們各自該歸哪一群，我加進設定。</b></InfoNote>
      </p>
      <div class="tblwrap" :class="{ tabbed: subGroups.length }">
        <table>
          <thead><tr>
            <!-- 類別（使用者 2026-09-10）：預設順序就是「第一類先、同類裡正式機多的先」，
                 由後端排好（清單只載前 10 大，前端排的話前 10 名本身就挑錯了） -->
            <th class="srt" :class="{ on: sSort.key.value === '__class' }" @click="sSort.toggle('__class')">
              類別<i class="arw">{{ sSort.key.value === '__class' ? (sSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <th class="srt" :class="{ on: sSort.key.value === '__name' }" @click="sSort.toggle('__name')">
              系統<i class="arw">{{ sSort.key.value === '__name' ? (sSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <!-- AP ID 獨立一欄（使用者 2026-09-10）：黏在系統名後面時它看起來像註解，
                 但它是**對照的鍵**——要拿去跟別的系統對帳、要能排序、要能複製 -->
            <th class="apid srt" :class="{ on: sSort.key.value === '__apid' }" @click="sSort.toggle('__apid')">
              AP ID<i class="arw">{{ sSort.key.value === '__apid' ? (sSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <th class="dept srt" :class="{ on: sSort.key.value === '__dept' }" @click="sSort.toggle('__dept')">
              AP 部門<i class="arw">{{ sSort.key.value === '__dept' ? (sSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <th v-for="e in ordered(sys.envs, ENV_ORDER)" :key="e" class="srt"
                :class="{ gap: isGap(e), on: sSort.key.value === e }" @click="sSort.toggle(e)">
              {{ e }}<i class="arw">{{ sSort.key.value === e ? (sSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <th class="tot srt" :class="{ on: sSort.key.value === '__total' }" @click="sSort.toggle('__total')">
              合計<i class="arw">{{ sSort.key.value === '__total' ? (sSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
            </th>
            <th></th>
          </tr></thead>
          <tbody>
            <!-- 基礎設施：分群改成上面的頁籤，表身只畫目前選的那一群（shownGroups）。 -->
            <template v-for="grp in shownGroups" :key="grp.group">
            <template v-for="s in grp.rows" :key="s.api_id">
              <tr :class="{ opened: drillOpenOn(s.api_id) }">
                <td class="cls">
                  <select v-if="editingClass === s.api_id" class="cls-input"
                          :value="s.sys_class" @change="saveClass(s.api_id, ($event.target as HTMLSelectElement).value)">
                    <option v-for="c in sys.class_order" :key="c" :value="c">{{ c }}</option>
                  </select>
                  <template v-else>
                    <span class="clsb" :class="'c' + (sys.class_order.indexOf(s.sys_class) + 1)"
                          style="cursor:pointer" title="點一下手動改分級" @click="editingClass = s.api_id">{{ s.sys_class }}</span>
                    <!-- 分級表原字串有「（核心）」的標出來——不丟原檔的資訊 -->
                    <span v-if="s.sys_class_core" class="core">核心</span>
                    <span v-if="s.sys_class_override" class="cls-ovr"
                          :title="'手動改過（分級表原判：' + (s.sys_class_computed || '未分級') + '）'">✎</span>
                  </template>
                </td>
                <th class="rowh">{{ s.name || '（無名稱）' }}</th>
                <td class="apid mono">{{ s.api_id }}</td>
                <td class="dept">{{ s.ap_department || '—' }}</td>
                <!-- 每一格數字都可以點：點下去看這一格在各機房各幾台 -->
                <td v-for="e in ordered(sys.envs, ENV_ORDER)" :key="e" :class="{ gap: isGap(e) }">
                  <button v-if="s.by_env[e]" type="button" class="cellbtn mono"
                          :class="{ on: drillOpenOn(s.api_id) && focusEnv === e }"
                          :disabled="drillBusy === s.api_id + e"
                          :title="`看這 ${s.by_env[e]} 台在各機房各幾台`"
                          @click="openDrill(s.api_id, e)">{{ n(s.by_env[e]) }}</button>
                  <span v-else class="mono empty">—</span>
                </td>
                <td class="tot">
                  <button type="button" class="cellbtn mono strong"
                          :class="{ on: drillOpenOn(s.api_id) && !focusEnv }"
                          :disabled="drillBusy === s.api_id"
                          title="看整個系統在各機房各幾台"
                          @click="openDrill(s.api_id, null)">{{ n(s.total) }}</button>
                </td>
                <td class="ops">
                  <button class="btn small" type="button" @click="openDrill(s.api_id, null)">看機房</button>
                </td>
              </tr>
              <!-- 下鑽：機房 × 環境 ＋ AP／DB -->
              <tr v-if="drillOpenOn(s.api_id)" class="drill">
                <td :colspan="sys.envs.length + 6">
                  <div class="dwrap">
                    <!-- 第一張：機房 × 環境（使用者 2026-09-10 確認的模擬：「點好麥，點進去，
                         它才是機房 × 環境」）。**永遠顯示全部環境**——點清單上某個環境的
                         數字只會把那一欄標亮，不會把其他環境藏起來。 -->
                    <div class="dblock wide">
                      <div class="dk">
                        機房 × 環境
                        <span class="basis">全部 {{ n(drill?.total) }} 台</span>
                        <span v-if="focusEnv" class="basis">標亮：「{{ focusEnv }}」</span>
                        <InfoNote>點進系統之後<b>永遠顯示全部環境</b>。<br><br>以前點清單上的「正式 8」會只剩正式環境，其他環境的機器整批不見——要找某台機器時看的是切過的一小片，於是以為系統上沒有那台。現在點某個環境只會把那一欄標亮。<br><br>每一格都可以點，看是哪幾台。</InfoNote>
                      </div>
                      <table class="inner">
                        <thead><tr>
                          <th class="srt" :class="{ on: leSort.key.value === '__loc' }"
                              @click="leSort.toggle('__loc')">機房＼環境<i class="arw">{{ leSort.key.value === '__loc' ? (leSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i></th>
                          <th v-for="e in drillEnvs" :key="e" class="srt"
                              :class="{ gap: isGap(e), on: leSort.key.value === e, focus: focusEnv === e }"
                              @click="leSort.toggle(e)">{{ e }}<i class="arw">{{ leSort.key.value === e ? (leSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i></th>
                          <th class="tot srt" :class="{ on: leSort.key.value === '__total' }"
                              @click="leSort.toggle('__total')">合計<i class="arw">{{ leSort.key.value === '__total' ? (leSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i></th>
                        </tr></thead>
                        <tbody>
                          <template v-for="l in sortedDrillLocs" :key="l.location">
                            <tr>
                              <th class="rowh" :class="{ gap: isGap(l.location) }">{{ l.location }}</th>
                              <td v-for="e in drillEnvs" :key="e" :class="{ focus: focusEnv === e }">
                                <button v-if="l.by_env[e]" type="button" class="cellbtn mono"
                                        :class="{ on: leCell?.location === l.location && leCell?.env === e }"
                                        :disabled="leBusy === l.location + '|' + e"
                                        :title="`看是哪 ${l.by_env[e]} 台`"
                                        @click="openLeCell(l.location, e, l.by_env[e])">{{ n(l.by_env[e]) }}</button>
                                <span v-else class="mono empty">—</span>
                              </td>
                              <td class="tot mono">{{ n(l.total) }}</td>
                            </tr>
                            <!-- 這一格是哪幾台 -->
                            <tr v-if="leCell && leCell.location === l.location" class="celllist">
                              <td :colspan="drillEnvs.length + 2">
                                <div class="cl-head">{{ leCell.location }}　·　{{ leCell.env }}　·　{{ n(leCell.items.length) }} 台</div>
                                <table class="inner">
                                  <thead><tr>
                                    <th v-for="c in [
                                          { k: 'hostname', label: '主機名稱' },
                                          { k: 'ip', label: 'IP' },
                                          { k: 'os', label: '作業系統' },
                                          { k: 'role', label: '角色' },
                                          { k: 'purpose', label: '用途說明' }]"
                                        :key="c.k" class="srt" :class="{ on: lcSort.key.value === c.k }"
                                        @click="lcSort.toggle(c.k)">
                                      {{ c.label }}<i class="arw">{{ lcSort.key.value === c.k ? (lcSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
                                    </th>
                                  </tr></thead>
                                  <tbody>
                                    <tr v-for="a in sortedLeItems" :key="a.asset_serial">
                                      <th class="rowh">
                                        <NuxtLink :to="`/assets/${a.asset_serial}`" class="dl">{{ a.hostname || '（無主機名）' }}</NuxtLink>
                                      </th>
                                      <td class="rowh mono">{{ a.ip || '—' }}</td>
                                      <td class="rowh">{{ a.os || '—' }}</td>
                                      <td class="rowh">{{ a.role }}</td>
                                      <td class="rowh basis-cell">{{ a.purpose || '—' }}</td>
                                    </tr>
                                  </tbody>
                                </table>
                              </td>
                            </tr>
                          </template>
                          <tr class="sum">
                            <th class="rowh">合計</th>
                            <td v-for="e in drillEnvs" :key="e" class="mono" :class="{ focus: focusEnv === e }">{{ n(drillEnvTotal(e)) }}</td>
                            <td class="tot mono">{{ n(drill?.total) }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>

                    <!-- 第二張：角色 × 機房（全部環境） -->
                    <div class="dblock wide">
                      <div class="dk">
                        角色 × 機房
                        <span class="basis">全部 {{ n(drill?.total) }} 台</span>
                        <InfoNote>
                          <b>怎麼判的</b>（{{ drill?.role_basis }}）——由強到弱：<br>
                          1. <b>機器上實際跑的服務</b>（tnslsnr／mysqld → DB；nginx／tomcat → AP）。這是<b>證據</b>，機器自己講的。<br>
                          2. 網通／儲存設備、容器平台節點（OpenShift／ESXi）——這些本來就不是 AP 也不是 DB。<br>
                          3. 登記欄位的關鍵字——這是<b>推論</b>。<br><br>
                          <b>「資料不完整」不等於「沒資料」</b>——那些機器的登記資料都在，缺的只是判角色的線索。納管並收一輪服務可以補上大部分。<br><br>
                          每一列的合計加起來會等於這個系統的總台數——對不起來就是有 bug，請告訴我。
                        </InfoNote>
                      </div>
                      <table class="inner">
                        <thead><tr>
                          <th class="srt" :class="{ on: rSort.key.value === '__role' }"
                              @click="rSort.toggle('__role')">角色＼機房<i class="arw">{{ rSort.key.value === '__role' ? (rSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i></th>
                          <th v-for="l in drill?.locations ?? []" :key="l.location" class="srt"
                              :class="{ gap: isGap(l.location), on: rSort.key.value === l.location }"
                              @click="rSort.toggle(l.location)">{{ l.location }}<i class="arw">{{ rSort.key.value === l.location ? (rSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i></th>
                          <th class="tot srt" :class="{ on: rSort.key.value === '__total' }"
                              @click="rSort.toggle('__total')">合計<i class="arw">{{ rSort.key.value === '__total' ? (rSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i></th>
                        </tr></thead>
                        <tbody>
                          <!-- 0 的列不畫，但合計那格會讓人看得出來有沒有漏 -->
                          <template v-for="rm in sortedRoleMatrix" :key="rm.role">
                            <tr>
                              <th class="rowh" :class="{ gap: isPending(rm.role) }">{{ rm.role }}</th>
                              <!-- 每一格可以點：列出是哪幾台 -->
                              <td v-for="l in drill?.locations ?? []" :key="l.location">
                                <button v-if="rm.by_location[l.location]" type="button"
                                        class="cellbtn mono"
                                        :class="{ on: cell?.role === rm.role && cell?.location === l.location }"
                                        :disabled="cellBusy === rm.role + '|' + l.location"
                                        :title="`看是哪 ${rm.by_location[l.location]} 台`"
                                        @click="openCell(rm.role, l.location, rm.by_location[l.location])">
                                  {{ n(rm.by_location[l.location]) }}
                                </button>
                                <span v-else class="mono empty">—</span>
                              </td>
                              <td class="tot mono">{{ n(rm.total) }}</td>
                            </tr>
                            <!-- 這一格是哪幾台 -->
                            <tr v-if="cell && cell.role === rm.role" class="celllist">
                              <td :colspan="(drill?.locations?.length ?? 0) + 2">
                                <div class="cl-head">
                                  {{ cell.location }}　·　{{ cell.role }}　·　{{ n(cell.items.length) }} 台
                                  <InfoNote>這裡列的是<b>這一格實際包含的機器</b>。角色是算出來的（不是資料庫欄位），所以資產查詢頁篩不出同一批——才另外做這個清單。<br><br>「判定依據」那欄是<b>當初判角色時看到的東西</b>，讓你能質疑：如果它被歸錯了，看得出來是哪個字造成的。</InfoNote>
                                </div>
                                <table class="inner">
                                  <thead><tr>
                                    <th v-for="c in [
                                          { k: 'hostname', label: '主機名稱' },
                                          { k: 'ip', label: 'IP' },
                                          { k: 'os', label: '作業系統' },
                                          { k: 'purpose', label: '判定依據' },
                                          { k: 'onboarded', label: '納管' }]"
                                        :key="c.k" class="srt"
                                        :class="{ on: cSort.key.value === c.k }"
                                        @click="cSort.toggle(c.k)">
                                      {{ c.label }}<i class="arw">{{ cSort.key.value === c.k ? (cSort.dir.value === 'asc' ? '▲' : '▼') : '↕' }}</i>
                                    </th>
                                  </tr></thead>
                                  <tbody>
                                    <tr v-for="a in sortedCellItems" :key="a.asset_serial">
                                      <th class="rowh">
                                        <NuxtLink :to="`/assets/${a.asset_serial}`" class="dl">
                                          {{ a.hostname || '（無主機名）' }}
                                        </NuxtLink>
                                      </th>
                                      <td class="rowh mono">{{ a.ip || '—' }}</td>
                                      <td class="rowh">{{ a.os || '—' }}</td>
                                      <td class="rowh basis-cell">{{ a.purpose || a.device_model || '—' }}</td>
                                      <td class="rowh">{{ a.onboarded ? '已納管' : '—' }}</td>
                                    </tr>
                                  </tbody>
                                </table>
                              </td>
                            </tr>
                          </template>
                          <tr class="sum">
                            <th class="rowh">合計</th>
                            <td v-for="l in drill?.locations ?? []" :key="l.location" class="mono">
                              {{ n(l.total) }}
                            </td>
                            <td class="tot mono">{{ n(drill?.total) }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            </template>
          </tbody>
        </table>
      </div>
      <div v-if="drill" class="more">
        <!-- 下鑽只留一列時，給一顆「回到清單」——不然使用者會以為其他系統不見了 -->
        <button class="btn small primary" type="button" @click="drill = null">
          ← 看全部系統
        </button>
      </div>
    </section>

  </div>
</template>

<style scoped>
.lookup { display: flex; gap: 8px; align-items: center; margin: 0 0 14px; flex-wrap: wrap; }
.lk-in { flex: 1; min-width: 280px; max-width: 460px; padding: 7px 12px; font-size: 13px;
  border: 1px solid var(--border-strong); border-radius: 6px;
  background: var(--card); color: var(--ink); }
.tabs { display: flex; gap: 6px; margin: 0 0 14px; flex-wrap: wrap; }
.tab { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft);
  border-radius: 999px; padding: 6px 16px; font-size: 13px; cursor: pointer; }
.tab:hover { border-color: var(--border-strong); }
.tab.on { border-color: var(--brand); background: var(--mint); color: var(--ink); font-weight: 600; }

/* 基礎設施的分群頁籤（Excel 工作表風）：黏在表格上緣 */
.gtabs { display: flex; gap: 4px; flex-wrap: wrap; border-bottom: 2px solid var(--brand); margin: 0; }
.gtab { border: 1px solid var(--border); border-bottom: none; background: var(--sub);
  color: var(--ink-soft); border-radius: 8px 8px 0 0; padding: 8px 14px; font-size: 13px;
  cursor: pointer; margin-bottom: -2px; }
.gtab:hover { color: var(--ink); }
.gtab.on { background: var(--card); color: var(--brand); font-weight: 600;
  border-color: var(--brand); border-bottom: 2px solid var(--card); }
.gtab .gtc { margin-left: 6px; font-size: 11px; color: var(--ink-soft); font-weight: 400; }
.gtab.on .gtc { color: var(--brand); }
.tblwrap.tabbed { border: 1px solid var(--border); border-top: none; border-radius: 0 0 8px 8px; }
.ungrp-note { font-size: 12px; color: var(--ink-soft); margin: 10px 0 4px; }

.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 16px; margin-bottom: 16px; }
.ck { font-size: 13px; font-weight: 600; color: var(--ink); margin-bottom: 10px; }
.tblwrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 7px 10px; border-bottom: 1px solid var(--border); text-align: right; }
thead th { font-size: 12px; color: var(--ink-soft); font-weight: 600; white-space: nowrap; }
th:first-child, .rowh, td.dept, th.dept { text-align: left; }
td.apid, th.apid { font-family: ui-monospace, Consolas, monospace; font-size: 12px;
  color: var(--ink-soft); text-align: left; white-space: nowrap; }
td.dept { font-size: 12px; color: var(--ink-soft); }
.mono { font-family: ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums; }
.tot { font-weight: 600; }
.gap { background: rgba(255, 184, 103, .12); color: var(--warn-text); }
.empty { color: var(--ink-aux); }
.sum th, .sum td { border-top: 2px solid var(--border-strong); font-weight: 600; }
.ops { text-align: right; }
.cellbtn { border: none; background: none; color: var(--brand-dark); cursor: pointer;
  padding: 2px 4px; border-radius: 4px; font-size: 13px; }
.cellbtn:hover { background: var(--sub); text-decoration: underline; }
.cellbtn.strong { font-weight: 600; color: var(--ink); }
.cellbtn.on { background: var(--mint); font-weight: 600; }
.opened { background: var(--sub); }
.drill td { background: var(--sub); }
.dwrap { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; padding: 6px 0; }
.dblock { flex: 1; min-width: 260px; }
.dk { font-size: 12px; font-weight: 600; margin-bottom: 6px; text-align: left; }
.basis { font-weight: 400; font-size: 11px; color: var(--warn-text); margin-left: 6px; }
table.inner th, table.inner td { padding: 5px 8px; font-size: 12px; }
.dblock.wide { flex: 1 1 100%; }
/* 點清單上某個環境的數字 → 下鑽表裡那一欄標亮（不是把其他環境藏起來） */
.inner th.focus, .inner td.focus { background: var(--mint); }
td.cls { text-align: left; white-space: nowrap; }
.clsb { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); }
.clsb.c1 { background: rgba(198, 40, 40, .10); color: var(--bad, #c62828); border-color: currentColor; }
.clsb.c2 { background: rgba(255, 184, 103, .15); color: var(--warn-text); border-color: currentColor; }
.clsb.c3 { color: var(--ink-soft); }
.clsb.c4 { color: var(--ink-aux); border-style: dashed; }
.core { font-size: 10px; margin-left: 4px; padding: 1px 6px; border-radius: 3px;
  background: var(--bad, #c62828); color: #fff; font-weight: 600; }
.cls-ovr { color: var(--brand); margin-left: 3px; font-size: 11px; }
.cls-input { font-family: inherit; font-size: 12px; padding: 2px 4px;
  border: 1px solid var(--brand); border-radius: 6px; background: var(--card); color: var(--ink); }
/* 可排序表頭（鐵規則：表格每一欄都要能排）。未排序時顯示淡色 ↕ 提示可以點。 */
.srt { cursor: pointer; user-select: none; white-space: nowrap; }
.srt:hover { color: var(--ink); }
.srt .arw { font-style: normal; font-size: 10px; margin-left: 4px; opacity: .35; }
.srt.on { color: var(--brand-dark); }
.srt.on .arw { opacity: 1; }
.grouphead th { background: var(--sub); font-size: 12px; font-weight: 600;
  color: var(--ink); padding: 8px 10px; }
.grouphead .gcount { font-weight: 400; color: var(--ink-soft); margin-left: 8px; font-size: 11px; }
.celllist td { background: var(--card); padding: 10px 12px; }
.cl-head { font-size: 12px; font-weight: 600; margin-bottom: 8px; text-align: left; }
.basis-cell { font-size: 12px; color: var(--ink-soft); }
.dl { color: var(--brand-dark); }
.note { font-size: 12px; color: var(--ink-soft); margin: 8px 0 0; }
.warn { color: var(--warn-text); }
.more { margin-top: 10px; display: flex; gap: 8px; }
</style>
