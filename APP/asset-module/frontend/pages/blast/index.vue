<script setup lang="ts">
// MICS 切片2：/blast 影響範圍查詢。三種問法共用同一顆後端引擎（blast_radius.py），
// 只差 mode 參數與版面重點：
//   probe    陌生 IP 的即時研判——同時看「誰依賴它」與「它依賴誰」（後者可能是肇因）
//   incident 事故當下的爆炸半徑——只看「誰依賴它」
//   planned  計畫性停機事前評估——跟 incident 同一份資料，版面改標題（快照/CSV 是切片3）
//
// 圖形視覺化複製 topology.vue 的 cy/dagre 設定（vendored，公司環境擋 npm）。
//
// 跟 assets/index.vue 同樣的理由關掉 SSR：這頁一進來就 await 抓資料再操作 #cy 的
// DOM（cytoscape），SSR 渲染出的靜態 HTML 跟客戶端 hydrate 時的樹對不起來，
// 而且純內部戰情室工具本來就不需要 SEO／首屏 SSR，關掉最乾脆。
definePageMeta({ ssr: false })
interface ResolveResult {
  status: 'resolved' | 'ambiguous' | 'unregistered'
  node_id?: string; label?: string
  candidates?: { node_id?: string; asset_serial?: string; label: string }[]
  segment?: { location?: string; environment?: string; purpose?: string; category?: string } | null
  confidence?: string
  service?: { known: boolean; service_guess?: string; process?: string; last_seen?: string; note?: string }
}
interface Hit { node_id: string; depth: number; edge_type: string | null; confidence: string; path: any[] }
/** 機房/環境別各桶裡的一筆。node_type 用來區分「是資產但欄位沒填」與
 *  「根本不是資產」（ESXi/cluster/網段這類節點沒有環境別可言）。 */
interface BlastItem {
  node_id: string; node_type: string; label: string
  asset_serial: string | null; hostname: string | null; ip: string | null
}
interface Summary {
  counts: Record<string, number>
  by_biz_system: {
    api_id: string; name: string; availability: number | null; severity: string
    assets: {
      asset_serial: string; label: string; hostname: string | null; ip: string | null
      location: string | null; environment: string | null
      // 這台跟查詢主機「怎麼」有關係——2026-08-20 使用者問「這三台跟這台主機
      // 有關係嗎」才發現漏接：資料 hit 裡本來就有，只是沒串進這個列表。
      depth: number; edge_type: string | null; confidence: string
      owners: {
        name: string; department?: string | null; role?: string | null
        phone?: string | null; phone_note?: string | null
        email?: string | null; email_note?: string | null
        proxy?: string | null; proxy_phone?: string | null
      }[]
    }[]
  }[]
  by_location: { location: string; count: number; items: BlastItem[] }[]
  by_environment: { environment: string; count: number; items: BlastItem[] }[]
  notify: { name: string; phone?: string; proxy?: string; proxy_phone?: string; role?: string }[]
  unknown_owner: { asset_serial: string; label: string }[]
  evidence_breakdown: Record<string, number>
}
interface Coverage {
  dimensions: { name: string; status: 'ok' | 'partial' | 'none' | 'out_of_scope'; detail: string }[]
  complete: number; total: number
}
interface ImpactResult {
  node_id: string; label: string; node_type: string
  dependents: Hit[]; dependencies?: Hit[]; summary: Summary
  coverage?: Coverage
}
interface SnapshotMeta {
  id: number; node_id: string; mode: string; reason: string | null; asked_by: string; asked_at: string
}
interface BizSystemRow {
  node_id: string; api_id: string; name: string
  usage_unit: string | null; custodian: string | null; severity: string; asset_count: number
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()

const MODE_LABEL: Record<string, string> = { probe: '陌生 IP 研判', incident: '事故爆炸半徑', planned: '計畫性停機評估' }
const mode = ref<'probe' | 'incident' | 'planned'>((route.query.mode as any) || 'incident')
const query = ref((route.query.q as string) || '')
const port = ref((route.query.port as string) || '')

const resolving = ref(false)
const resolveResult = ref<ResolveResult | null>(null)
const impactResult = ref<ImpactResult | null>(null)
const loadingImpact = ref(false)

// 圖上節點的 label 對照表（node_id -> label），從 /api/blast/graph 的節點清單順便建
const labelMap = ref<Record<string, string>>({})

let cy: any = null

function loadScript(src: string): Promise<void> {
  return new Promise((res, rej) => {
    if (document.querySelector(`script[src="${src}"]`)) return res()
    const s = document.createElement('script')
    s.src = src
    s.onload = () => res()
    s.onerror = () => rej(new Error(`載入失敗：${src}`))
    document.head.appendChild(s)
  })
}
async function ensureLibs() {
  await loadScript('/vendor/cytoscape.min.js')
  await loadScript('/vendor/dagre.min.js')
  await loadScript('/vendor/cytoscape-dagre.min.js')
}
function dagreOpts() {
  return { name: 'dagre', rankDir: 'LR', nodeSep: 34, rankSep: 90, edgeSep: 12, padding: 26, animate: true, animationDuration: 400 }
}
const CY_STYLE = [
  { selector: 'node', style: {
    label: 'data(label)', 'text-valign': 'center', 'text-halign': 'center',
    'font-family': "'Microsoft JhengHei', sans-serif", 'font-size': '12px', 'font-weight': 700,
    shape: 'round-rectangle', width: 'label', height: 'label', padding: '13px',
    'border-width': 2, color: '#1f2937', 'text-wrap': 'wrap', 'text-max-width': '120px',
  } },
  { selector: 'node[type="host"]', style: { 'background-color': 'rgba(0,145,66,.13)', 'border-color': '#009142' } },
  { selector: 'node[type="esxi"]', style: { 'background-color': 'rgba(127,179,234,.15)', 'border-color': '#2563eb' } },
  { selector: 'node[type="cluster"]', style: { 'background-color': 'rgba(127,179,234,.1)', 'border-color': '#1e40af' } },
  { selector: 'node[type="datastore"]', style: { 'background-color': 'rgba(200,160,255,.13)', 'border-color': '#7c3aed' } },
  { selector: 'node[type="business_service"]', style: { 'background-color': 'rgba(255,184,103,.15)', 'border-color': '#d97706' } },
  { selector: 'node[type="system"]', style: { 'background-color': 'rgba(15,23,42,.06)', 'border-color': '#64748b' } },
  { selector: 'node[type="segment"], node[type="rack"]', style: { 'background-color': 'rgba(15,23,42,.03)', 'border-color': '#576b64', 'border-style': 'dashed' } },
  { selector: 'edge', style: {
    width: 2, 'line-color': '#42615a', 'target-arrow-color': '#42615a',
    'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 1.1,
  } },
  { selector: 'edge[confidence="未驗證"]', style: { 'line-style': 'dashed' } },
  { selector: 'node.center', style: { 'border-color': '#dc2626', 'border-width': 5 } },
  { selector: 'node.search-hit', style: { 'border-color': '#ca8a04', 'border-width': 5 } },
  { selector: '.search-dim', style: { opacity: 0.15 } },
]

async function initCy(elements: any[]) {
  await ensureLibs()
  const cytoscape = (window as any).cytoscape
  if (!cytoscape) { showToast('圖形引擎載入失敗，請重新整理', 'error'); return }
  const container = document.getElementById('cy')
  if (!container) return
  if (cy) { cy.destroy(); cy = null }
  cy = cytoscape({ container, elements, style: CY_STYLE, layout: dagreOpts(), wheelSensitivity: 0.2 })
  cy.on('tap', 'node', (e: any) => {
    const id = e.target.id()
    if (id !== impactResult.value?.node_id) pivotTo(id)
  })
  const center = cy.getElementById(impactResult.value?.node_id)
  if (center && center.length) center.addClass('center')
}
function relayout() { if (cy) cy.layout(dagreOpts()).run() }
function fit() { if (cy) cy.fit(null, 30) }
onUnmounted(() => { if (cy) { cy.destroy(); cy = null } })

// 全螢幕：圖一大就擠在小框裡看不清楚（2026-08-19 使用者反映）。不開新分頁/新路由
// ——同一顆 cy 實例直接把容器變成 fixed 滿版，resize()+fit() 讓它吃到新尺寸，
// 比另建一個頁面複製一份 cy/dagre 設定簡單也不會兩邊維護漂走。
const fullscreen = ref(false)
async function toggleFullscreen() {
  fullscreen.value = !fullscreen.value
  await nextTick()
  if (cy) { cy.resize(); cy.fit(null, 30) }
}
function onEscKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && fullscreen.value) toggleFullscreen()
}
onMounted(() => document.addEventListener('keydown', onEscKey))
onUnmounted(() => document.removeEventListener('keydown', onEscKey))

// 圖上搜尋：同樣是使用者反映「圖大了要能搜」。比對節點 label，命中的亮黃框、
// 其餘壓暗——跟 pivot 選取用的 .center 是不同語意（這是「找」不是「選」），
// 分開一組 class 才不會互相蓋掉彼此的高亮。
const graphSearch = ref('')
function applyGraphSearch() {
  if (!cy) return
  const kw = graphSearch.value.trim().toLowerCase()
  cy.nodes().removeClass('search-hit search-dim')
  if (!kw) return
  const hits = cy.nodes().filter((n: any) => String(n.data('label') || '').toLowerCase().includes(kw))
  if (hits.length === 0) return
  cy.nodes().not(hits).addClass('search-dim')
  hits.addClass('search-hit')
}
function clearGraphSearch() {
  graphSearch.value = ''
  if (cy) cy.nodes().removeClass('search-hit search-dim')
}

async function loadGraph(nodeId: string) {
  graphSearch.value = ''
  try {
    const g = await apiFetch<{ elements: { nodes: any[]; edges: any[] } }>('/api/blast/graph', {
      query: { node_id: nodeId, depth: 3, direction: mode.value === 'probe' ? 'both' : 'dependents' },
    })
    const map: Record<string, string> = {}
    for (const n of g.elements.nodes) map[n.data.id] = n.data.label
    labelMap.value = map
    await nextTick()
    await initCy([...g.elements.nodes, ...g.elements.edges])
  } catch {
    // 圖形化失敗不擋主要查詢結果，表格照樣看得到
  }
}

function labelOf(nodeId: string) { return labelMap.value[nodeId] || nodeId }

async function runImpact(nodeId: string) {
  loadingImpact.value = true
  viewingSnapshot.value = null
  try {
    const r = await apiFetch<ImpactResult>('/api/blast/impact', {
      query: { node_id: nodeId, depth: 6, mode: mode.value === 'planned' ? 'incident' : mode.value },
    })
    impactResult.value = r
    await loadGraph(nodeId)
    if (mode.value === 'planned') await loadSnapshots(nodeId)
  } catch (e: any) {
    showToast(e?.data?.detail ?? '查詢影響範圍失敗', 'error')
    impactResult.value = null
  } finally {
    loadingImpact.value = false
  }
}

// ===== 切片3：計畫性停機評估存證快照 =====
const snapshots = ref<SnapshotMeta[]>([])
const snapshotReason = ref('')
const savingSnapshot = ref(false)
const viewingSnapshot = ref<SnapshotMeta | null>(null)

async function loadSnapshots(nodeId: string) {
  try {
    snapshots.value = await apiFetch<SnapshotMeta[]>('/api/blast/snapshots', { query: { node_id: nodeId } })
  } catch {
    // 快照清單載入失敗不擋主要查詢結果
  }
}

async function saveSnapshot() {
  if (!impactResult.value) return
  savingSnapshot.value = true
  try {
    await apiFetch('/api/blast/snapshot', {
      method: 'POST',
      body: { node_id: impactResult.value.node_id, reason: snapshotReason.value.trim() || null },
    })
    showToast('已存證快照', 'success')
    snapshotReason.value = ''
    await loadSnapshots(impactResult.value.node_id)
  } catch (e: any) {
    showToast(e?.data?.detail ?? '存快照失敗', 'error')
  } finally {
    savingSnapshot.value = false
  }
}

async function viewSnapshotResult(snap: SnapshotMeta) {
  try {
    const full = await apiFetch<SnapshotMeta & { result: ImpactResult }>(`/api/blast/snapshot/${snap.id}`)
    impactResult.value = full.result
    viewingSnapshot.value = snap
  } catch (e: any) {
    showToast(e?.data?.detail ?? '讀取快照失敗', 'error')
  }
}

async function backToLiveQuery() {
  if (!impactResult.value) return
  await runImpact(impactResult.value.node_id)
}

async function backToBrowse() {
  resolveResult.value = null
  impactResult.value = null
  query.value = ''
  viewingSnapshot.value = null
  if (import.meta.client) router.replace({ query: {} })
  if (systemsList.value.length === 0) await loadSystemsList()
}

function snapshotCsvUrl(snapId: number) {
  const base = (useRuntimeConfig().public as any).apiBase || ''
  return `${base}/api/blast/snapshot/${snapId}/csv`
}

// 事故當下要的是「馬上把清單發出去」——不用像計畫性停機那樣先存快照，
// 即時查詢結果直接匯出（2026-08-19 使用者原話：拿到圖第一件事就是匯出聯絡資料）
function downloadImpactCsv() {
  if (!impactResult.value) return
  const base = (useRuntimeConfig().public as any).apiBase || ''
  const url = `${base}/api/blast/impact/csv?` + new URLSearchParams({
    node_id: impactResult.value.node_id, depth: '6',
    mode: mode.value === 'planned' ? 'incident' : mode.value,
  }).toString()
  window.open(url, '_blank')
}

// 檢查清單（2026-08-20 拍板方案A）：事故當下要派工，不是查詢結果——先存一份
// 快照（凍結當下事實，見 save_snapshot 說明），再從快照攤平出檢查清單，給一個
// 連結全隊登入都看同一份，改狀態/寫備註就地存。
const creatingChecklist = ref(false)
async function createChecklistAndGo() {
  if (!impactResult.value) return
  creatingChecklist.value = true
  try {
    const snap = await apiFetch<{ id: number }>('/api/blast/snapshot', {
      method: 'POST',
      body: { node_id: impactResult.value.node_id, mode: mode.value },
    })
    await apiFetch('/api/blast/checklist', { method: 'POST', body: { snapshot_id: snap.id } })
    router.push(`/blast/checklist/${snap.id}`)
  } catch (e: any) {
    showToast(e?.data?.detail ?? '建立檢查清單失敗', 'error')
  } finally {
    creatingChecklist.value = false
  }
}

// 點圖上或表格裡的節點，改以它為中心重新查——不用重打一次搜尋字
async function pivotTo(nodeId: string) {
  resolveResult.value = { status: 'resolved', node_id: nodeId, label: labelOf(nodeId) }
  await runImpact(nodeId)
}

async function doQuery(syncUrl = true) {
  if (!query.value.trim()) { showToast('請輸入 IP／主機名／資產序號／系統名稱', 'warn'); return }
  resolving.value = true
  impactResult.value = null
  resolveResult.value = null
  try {
    const p = port.value.trim() ? Number(port.value.trim()) : undefined
    const r = await apiFetch<ResolveResult>('/api/blast/resolve', { query: { q: query.value.trim(), port: p } })
    resolveResult.value = r
    // 網址同步只在使用者主動查詢時做，初始從網址帶查詢字串進來那次不重複導頁——
    // setup 階段導頁會讓 SSR 渲染跟客戶端 hydrate 的結果對不起來（Vue hydration mismatch）
    if (syncUrl && import.meta.client) {
      router.replace({ query: { ...route.query, mode: mode.value, q: query.value.trim(), port: port.value || undefined } })
    }
    if (r.status === 'resolved' && r.node_id) {
      await runImpact(r.node_id)
    }
  } catch (e: any) {
    showToast(e?.data?.detail ?? '解析失敗', 'error')
  } finally {
    resolving.value = false
  }
}
async function pickCandidate(c: { node_id?: string; asset_serial?: string; label: string }) {
  const nodeId = c.node_id || null
  if (nodeId) {
    resolveResult.value = { status: 'resolved', node_id: nodeId, label: c.label }
    await runImpact(nodeId)
  } else if (c.asset_serial) {
    // 候選只有 asset_serial（IP/hostname 撞多台）：改用序號重查一次，讓後端幫忙轉成 node_id
    query.value = c.asset_serial
    await doQuery()
  }
}

// ===== 瀏覽清單（首頁入口）=====
// 2026-08-19 使用者拍板：全庫5148節點畫成一張「全部關聯圖」會是看不出結構的
// 毛球，選瀏覽清單當「不知道要查什麼名字才能開始」的入口——沒帶查詢字串進來
// 時顯示全部業務系統，點一筆直接以它為中心查影響範圍。
const systemsList = ref<BizSystemRow[]>([])
const systemsListLoading = ref(false)
const systemsFilter = ref('')
async function loadSystemsList() {
  systemsListLoading.value = true
  try {
    systemsList.value = await apiFetch<BizSystemRow[]>('/api/blast/systems')
  } catch {
    systemsList.value = []
  } finally {
    systemsListLoading.value = false
  }
}
const systemsListFiltered = computed(() => {
  const kw = systemsFilter.value.trim().toLowerCase()
  if (!kw) return systemsList.value
  return systemsList.value.filter(r =>
    [r.name, r.api_id, r.usage_unit, r.custodian].some(v => (v || '').toLowerCase().includes(kw)))
})
const { sortKey: sysListSortKey, sortDir: sysListSortDir, toggle: sysListToggle, sorted: sysListSorted } =
  useSort(systemsListFiltered, 'name')
async function pivotToSystem(row: BizSystemRow) {
  resolveResult.value = { status: 'resolved', node_id: row.node_id, label: row.name }
  await runImpact(row.node_id)
}

if (query.value) await doQuery(false)
else await loadSystemsList()

// ===== 表格排序（天條：每張表都能排）=====
const dependentsRows = computed(() =>
  (impactResult.value?.dependents ?? []).map(h => ({ ...h, label: labelOf(h.node_id) })))
const { sortKey: depSortKey, sortDir: depSortDir, toggle: depToggle, sorted: dependentsSorted } =
  useSort(dependentsRows, 'depth')

const dependenciesRows = computed(() =>
  (impactResult.value?.dependencies ?? []).map(h => ({ ...h, label: labelOf(h.node_id) })))
const { sortKey: depcSortKey, sortDir: depcSortDir, toggle: depcToggle, sorted: dependenciesSorted } =
  useSort(dependenciesRows, 'depth')

const bizRows = computed(() => impactResult.value?.summary.by_biz_system ?? [])

// 展開哪個業務系統看它的資產明細。
// 2026-08-20 使用者：「超音樹有三台，但我點進去不知道是哪三台，聯絡人／部門又是誰」。
// 事故當下要的是就地展開——跳去資產查詢頁再篩一次，等於中斷正在做的判讀。
const expandedBiz = ref<string | null>(null)
function toggleBiz(apiId: string) {
  expandedBiz.value = expandedBiz.value === apiId ? null : apiId
}
/** 一個業務系統底下所有機器的負責人，去重後排成一行。
 *
 * 2026-08-20 使用者連講三次「沒看到聯絡人」——資料一直都在，但被我收在「點資產數
 * 才展開」的後面。事故當下要的是**掃一眼就看到要找誰**，不是再按一下。
 * 所以聯絡人直接進表格當一欄；展開保留給「哪一台對應哪個人」這種細節。
 */
function bizOwners(r: { assets: { owners: { name: string; role?: string | null
                                            department?: string | null }[] }[] }) {
  const seen = new Map<string, { name: string; role?: string | null; department?: string | null }>()
  for (const a of r.assets) for (const o of a.owners) if (!seen.has(o.name)) seen.set(o.name, o)
  return [...seen.values()]
}

/** 一台機器的負責人摘要，直接排成「姓名（部門）電話」給人照著打電話。 */
function ownerLine(o: { name: string; department?: string | null; phone?: string | null; role?: string | null }) {
  const bits = [o.name]
  if (o.department) bits.push(`（${o.department}）`)
  if (o.phone) bits.push(o.phone)
  if (o.role) bits.push(`· ${o.role}`)
  return bits.join(' ')
}

// 機房位置／環境別兩張表也要能展開（2026-08-20 使用者：「查到 unknown 又是哪幾台？
// 要能點進去看」）。兩張表各自記住展開的是哪一列，互不影響。
const expandedLoc = ref<string | null>(null)
const expandedEnv = ref<string | null>(null)
function toggleLoc(k: string) { expandedLoc.value = expandedLoc.value === k ? null : k }
function toggleEnv(k: string) { expandedEnv.value = expandedEnv.value === k ? null : k }

/** 一筆命中的顯示名。沒有主機名就退回節點標籤，一定給得出東西讓人辨認。 */
function itemLine(it: BlastItem) {
  const name = it.hostname || it.label || it.asset_serial || it.node_id
  const bits = [name]
  if (it.ip) bits.push(it.ip)
  // 不是資產的節點要標出來：它出現在「未填」桶裡不是資料沒填，是本來就不適用，
  // 不講清楚會有人跑去「補」一個根本不存在的欄位。
  if (!it.asset_serial) bits.push(`· ${it.node_type}（非資產節點）`)
  return bits.join(' ')
}
const { sortKey: bizSortKey, sortDir: bizSortDir, toggle: bizToggle, sorted: bizSorted } = useSort(bizRows, 'severity')

const locRows = computed(() => impactResult.value?.summary.by_location ?? [])
const { sortKey: locSortKey, sortDir: locSortDir, toggle: locToggle, sorted: locSorted } = useSort(locRows, 'count')

const envRows = computed(() => impactResult.value?.summary.by_environment ?? [])
const { sortKey: envSortKey, sortDir: envSortDir, toggle: envToggle, sorted: envSorted } = useSort(envRows, 'count')

const unknownRows = computed(() => impactResult.value?.summary.unknown_owner ?? [])
const { sortKey: unkSortKey, sortDir: unkSortDir, toggle: unkToggle, sorted: unkSorted } = useSort(unknownRows, 'asset_serial')

const countsRows = computed(() =>
  Object.entries(impactResult.value?.summary.counts ?? {}).map(([node_type, count]) => ({ node_type, count })))
const { sortKey: cntSortKey, sortDir: cntSortDir, toggle: cntToggle, sorted: cntSorted } = useSort(countsRows, 'count')

const { sortKey: snapSortKey, sortDir: snapSortDir, toggle: snapToggle, sorted: snapSorted } =
  useSort(snapshots, 'asked_at')
</script>

<template>
  <div class="topo">
    <div class="head">
      <div class="title">
        <div class="ey">ASSET · MICS · 影響範圍查詢</div>
        <h1>影響範圍查詢</h1>
      </div>
    </div>
    <p class="lede">
      輸入 IP／主機名／資產序號／系統名稱，查<b>停機會波及誰</b>（事故／計畫性停機），
      或<b>陌生 IP 打進來是什麼</b>（研判）。查不到登記資料時不回「查無此物」，改給網段推導的線索。
    </p>

    <div class="toolbar">
      <select v-model="mode" class="sel">
        <option v-for="(v, k) in MODE_LABEL" :key="k" :value="k">{{ v }}</option>
      </select>
      <input v-model="query" class="kw" type="text" placeholder="IP／主機名／資產序號／系統名稱" @keyup.enter="() => doQuery()">
      <input v-model="port" class="kw port" type="text" placeholder="port（選填）" @keyup.enter="() => doQuery()">
      <button class="tb go" :disabled="resolving" @click="() => doQuery()">{{ resolving ? '查詢中…' : '🔍 查詢' }}</button>
      <button v-if="resolveResult || impactResult" class="tb" @click="backToBrowse">☰ 瀏覽清單</button>
      <div class="sp" />
      <button v-if="impactResult" class="tb" @click="relayout">⤢ 自動佈局</button>
      <button v-if="impactResult" class="tb" @click="fit">適應畫面</button>
      <button v-if="impactResult" class="tb" @click="downloadImpactCsv">⬇ 匯出CSV（含通知清單）</button>
      <button v-if="impactResult" class="tb go" :disabled="creatingChecklist" @click="createChecklistAndGo">
        {{ creatingChecklist ? '建立中…' : '📋 建立檢查清單' }}
      </button>
    </div>

    <!-- 瀏覽清單：沒有查詢字串、也沒有結果時的首頁入口。全庫5148節點畫成一張
         「全部關聯圖」是看不出結構的毛球，2026-08-19 使用者拍板改用這個。 -->
    <template v-if="!resolveResult && !impactResult">
      <p v-if="systemsListLoading" class="muted">載入業務系統清單中…</p>
      <template v-else-if="systemsList.length">
        <div class="toolbar">
          <input v-model="systemsFilter" class="kw" style="min-width:320px" type="text" placeholder="篩選系統名稱／代碼／部門／保管者…">
          <div class="sp" />
          <span class="dim">共 {{ systemsList.length }} 個業務系統</span>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="name" :active="sysListSortKey" :dir="sysListSortDir" @sort="sysListToggle">系統</SortTh>
            <SortTh k="usage_unit" :active="sysListSortKey" :dir="sysListSortDir" @sort="sysListToggle">使用單位</SortTh>
            <SortTh k="custodian" :active="sysListSortKey" :dir="sysListSortDir" @sort="sysListToggle">保管者</SortTh>
            <SortTh k="severity" :active="sysListSortKey" :dir="sysListSortDir" @sort="sysListToggle">嚴重度</SortTh>
            <SortTh k="asset_count" :active="sysListSortKey" :dir="sysListSortDir" @sort="sysListToggle">主機數</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in sysListSorted" :key="r.api_id">
              <td>
                <button class="dl linkbtn" @click="pivotToSystem(r)">{{ r.name }}</button>
                <span class="dim mono" style="font-size:10px"> {{ r.api_id }}</span>
              </td>
              <td class="dim">{{ r.usage_unit || '—' }}</td>
              <td class="dim">{{ r.custodian || '—' }}</td>
              <td><span class="pill" :class="r.severity === '重大' ? 'gone-pill' : 'open'">{{ r.severity }}</span></td>
              <td class="mono">{{ r.asset_count }}</td>
            </tr>
            <tr v-if="sysListSorted.length === 0"><td colspan="5" class="dim">沒有符合篩選條件的系統</td></tr>
          </tbody>
        </table>
      </template>
      <p v-else class="muted">還沒有業務系統資料——CI 圖譜可能還沒建過，或 hardware 表沒有登記 API ID。</p>
    </template>

    <!-- ambiguous：多筆候選，不自動選 -->
    <div v-if="resolveResult?.status === 'ambiguous'" class="glass panel-msg">
      <div class="pm-title">⚠ 找到多筆符合，請選一個（自動選錯，爆炸半徑就全錯）</div>
      <ul class="candidates">
        <li v-for="(c, i) in resolveResult.candidates" :key="i">
          <button class="cand" @click="pickCandidate(c)">{{ c.label }}</button>
        </li>
      </ul>
    </div>

    <!-- unregistered：退回網段推導，不是 404 -->
    <div v-else-if="resolveResult?.status === 'unregistered'" class="glass panel-msg">
      <div class="pm-title">「{{ query }}」未登記於資產庫（未驗證）</div>
      <template v-if="resolveResult.segment">
        <div class="seg-info">
          推導所屬網段：{{ resolveResult.segment.location || '—' }}
          <span v-if="resolveResult.segment.environment"> · {{ resolveResult.segment.environment }}</span>
          <span v-if="resolveResult.segment.purpose"> · {{ resolveResult.segment.purpose }}</span>
          <span v-if="resolveResult.segment.category"> · {{ resolveResult.segment.category }}</span>
        </div>
      </template>
      <p v-else class="muted">連所屬網段都推不出來——這個 IP 段完全沒有登記資料。</p>
      <div v-if="resolveResult.service" class="svc-info">
        <template v-if="resolveResult.service.known">
          port {{ port }}：{{ resolveResult.service.service_guess || '未知服務' }}
          <span v-if="resolveResult.service.process" class="dim">（{{ resolveResult.service.process }}）</span>
          <span class="dim"> · 最後看到 {{ resolveResult.service.last_seen }}</span>
        </template>
        <template v-else>
          <span class="dim">{{ resolveResult.service.note }}</span>
        </template>
      </div>
    </div>

    <p v-if="loadingImpact" class="muted">計算影響範圍中…</p>

    <template v-if="impactResult">
      <div v-if="viewingSnapshot" class="glass snap-banner">
        正在檢視 <b>{{ viewingSnapshot.asked_at }}</b> 由 <b>{{ viewingSnapshot.asked_by }}</b> 存的快照
        <span v-if="viewingSnapshot.reason">（{{ viewingSnapshot.reason }}）</span>
        ——這是存證當下的資料，跟現在的圖可能不一樣。
        <button class="tb small" @click="backToLiveQuery">↺ 回到即時查詢</button>
      </div>

      <div class="node-head glass">
        <div class="nh-label">{{ impactResult.label }}</div>
        <div class="nh-meta mono">{{ impactResult.node_id }} · {{ impactResult.node_type }}</div>
      </div>

      <div v-if="mode === 'planned' && !viewingSnapshot" class="glass snap-form">
        <div class="psec">存證快照</div>
        <p class="hint">圖會變——三週後要拿得出「當初評估說不影響」是哪次算出來的，存下這次結果。</p>
        <div class="snap-row">
          <input v-model="snapshotReason" class="kw" type="text" placeholder="停機原因／變更單號（選填）">
          <button class="tb go" :disabled="savingSnapshot" @click="saveSnapshot">
            {{ savingSnapshot ? '存證中…' : '📌 存快照' }}
          </button>
        </div>
      </div>

      <template v-if="mode === 'planned' && snapSorted.length > 0">
        <div class="psec">歷次存證快照</div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="asked_at" :active="snapSortKey" :dir="snapSortDir" @sort="snapToggle">時間</SortTh>
            <SortTh k="asked_by" :active="snapSortKey" :dir="snapSortDir" @sort="snapToggle">查詢人</SortTh>
            <SortTh k="reason" :active="snapSortKey" :dir="snapSortDir" @sort="snapToggle">原因</SortTh>
            <th>動作</th>
          </tr></thead>
          <tbody>
            <tr v-for="s in snapSorted" :key="s.id">
              <td class="mono">{{ s.asked_at }}</td>
              <td>{{ s.asked_by }}</td>
              <td class="dim">{{ s.reason || '—' }}</td>
              <td>
                <button class="dl linkbtn" @click="viewSnapshotResult(s)">查看</button>
                ·
                <a class="dl" :href="snapshotCsvUrl(s.id)" target="_blank">下載CSV</a>
              </td>
            </tr>
          </tbody>
        </table>
      </template>

      <div class="tiles">
        <div class="tile">
          <div class="t-num mono">{{ impactResult.dependents.length }}</div>
          <div class="t-lbl">受影響節點數{{ mode === 'probe' ? '（上游）' : '' }}</div>
        </div>
        <div class="tile" :class="{ warn: impactResult.summary.unknown_owner.length > 0 }">
          <div class="t-num mono">{{ impactResult.summary.unknown_owner.length }}</div>
          <div class="t-lbl">查不到負責人</div>
        </div>
        <div class="tile">
          <div class="t-num mono">{{ impactResult.summary.evidence_breakdown['證據'] || 0 }}</div>
          <div class="t-lbl">路徑為「證據」等級</div>
        </div>
        <div class="tile" :class="{ warn: (impactResult.summary.evidence_breakdown['未驗證'] || 0) > 0 }">
          <div class="t-num mono">{{ impactResult.summary.evidence_breakdown['未驗證'] || 0 }}</div>
          <div class="t-lbl">路徑僅「未驗證」等級</div>
        </div>
      </div>

      <div class="layout" :class="{ 'layout-fs': fullscreen }">
        <div class="graphwrap glass" :class="{ fullscreen }">
          <div class="graph-toolbar">
            <input
              v-model="graphSearch" class="kw graph-search" type="text"
              placeholder="在圖上搜尋節點名稱…" @input="applyGraphSearch"
            >
            <button v-if="graphSearch" class="tb small" @click="clearGraphSearch">✕ 清除</button>
            <div class="sp" />
            <button class="tb small" @click="toggleFullscreen">
              {{ fullscreen ? '↙ 離開全螢幕' : '⛶ 全螢幕' }}
            </button>
          </div>
          <div id="cy" />
        </div>

        <div class="panel glass">
          <div class="psec">依類型統計</div>
          <table class="tbl mini">
            <thead><tr>
              <SortTh k="node_type" :active="cntSortKey" :dir="cntSortDir" @sort="cntToggle">類型</SortTh>
              <SortTh k="count" :active="cntSortKey" :dir="cntSortDir" @sort="cntToggle">數量</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="r in cntSorted" :key="r.node_type"><td>{{ r.node_type }}</td><td class="mono">{{ r.count }}</td></tr>
              <tr v-if="cntSorted.length === 0"><td colspan="2" class="dim">無</td></tr>
            </tbody>
          </table>

          <!-- 涵蓋範圍聲明先拿掉（2026-08-20 使用者：「目前做不到，先拿掉，等到確實
               資料能伸進去再評估要不要打開」）。後端 coverage() 跟 Coverage 型別留著
               沒刪，只是這裡不渲染——之後資料補齊要開回來，不用重新設計。 -->

          <div class="psec">依業務系統（嚴重度依可用性欄位）</div>
          <table class="tbl mini biztbl">
            <colgroup><col class="c-name"><col class="c-sev"><col class="c-own"></colgroup>
            <thead><tr>
              <SortTh k="name" :active="bizSortKey" :dir="bizSortDir" @sort="bizToggle">系統</SortTh>
              <!-- 嚴重度／資產數合成一欄、上下疊放：兩個都是短值，各自獨立分欄會把
                   「系統」名稱擠窄（2026-08-20 使用者反映）。兩個排序鍵各自可點，
                   不用犧牲「天條：表格一律可排序」。 -->
              <th class="stackth">
                <span class="sortable" :class="{ on: bizSortKey === 'severity' }" @click="bizToggle('severity')">
                  嚴重度 <i class="arw">{{ bizSortKey === 'severity' ? (bizSortDir === 'asc' ? '▲' : '▼') : '↕' }}</i>
                </span>
                <span class="sortable" :class="{ on: bizSortKey === 'assets' }" @click="bizToggle('assets')">
                  資產數 <i class="arw">{{ bizSortKey === 'assets' ? (bizSortDir === 'asc' ? '▲' : '▼') : '↕' }}</i>
                </span>
              </th>
              <th>負責人</th>
            </tr></thead>
            <tbody>
              <template v-for="r in bizSorted" :key="r.api_id">
                <tr>
                  <td>
                    <NuxtLink class="dl" :to="{ path: '/assets', query: { filter_field: 'api_id', filter_value: r.api_id } }">{{ r.name }}</NuxtLink>
                    <span class="dim mono" style="font-size:10px"> {{ r.api_id }}</span>
                  </td>
                  <td class="stacktd">
                    <span class="pill" :class="r.severity === '重大' ? 'gone-pill' : 'open'">{{ r.severity }}</span>
                    <!-- 數字本身就是展開鈕：事故當下要當場看到是哪幾台、找誰，
                         跳去資產查詢頁再篩一次會中斷正在做的判讀。 -->
                    <button type="button" class="bizx" :class="{ open: expandedBiz === r.api_id }"
                            :title="`展開/收合 ${r.name} 的 ${r.assets.length} 台資產`"
                            @click="toggleBiz(r.api_id)">
                      {{ r.assets.length }} <span class="arw">▸</span>
                    </button>
                  </td>
                  <!-- 聯絡人直接進表格，不必展開就看得到要找誰（2026-08-20 使用者
                       連講三次「沒看到聯絡人」——資料一直都在，只是被我收在點擊後面）。
                       展開留給「哪一台對應哪個人」這種細節。 -->
                  <td class="bizown">
                    <template v-if="bizOwners(r).length">
                      <span v-for="(o, i) in bizOwners(r)" :key="i" class="bizowi">
                        {{ o.name }}<span class="dim">（{{ o.department || '單位未填' }}）</span>
                      </span>
                    </template>
                    <span v-else class="bapend">無登記負責人</span>
                  </td>
                </tr>
                <tr v-if="expandedBiz === r.api_id" class="bizdetail">
                  <td colspan="3">
                    <div v-for="a in r.assets" :key="a.asset_serial" class="bizasset">
                      <div class="bam">
                        <b>{{ a.hostname || a.label || a.asset_serial }}</b>
                        <span v-if="a.ip" class="dim mono"> {{ a.ip }}</span>
                        <span v-if="a.location" class="dim"> · {{ a.location }}</span>
                        <span v-if="a.environment" class="dim"> · {{ a.environment }}</span>
                      </div>
                      <!-- 固定五欄（2026-08-20 使用者要求）：角色／姓名／單位／Email／分機。
                           Email 與分機目前一定是空的，但**不留純空白**——空白會被讀成
                           「這個人沒有分機」，實際是「我們還沒拿到這份資料」。事故當下
                           前者代表不用再找、後者代表要趕快去問。 -->
                      <table v-if="a.owners.length" class="baot">
                        <tr v-for="(o, i) in a.owners" :key="i">
                          <td class="baor">{{ o.role || '負責人' }}</td>
                          <td class="baon">{{ o.name }}</td>
                          <td>{{ o.department || '—' }}</td>
                          <td>
                            <span v-if="o.email">{{ o.email }}</span>
                            <span v-else class="bapend">Email：{{ o.email_note || '待補' }}</span>
                          </td>
                          <td>
                            <span v-if="o.phone" class="mono">分機 {{ o.phone }}</span>
                            <span v-else class="bapend">分機：{{ o.phone_note || '待補' }}</span>
                          </td>
                        </tr>
                      </table>
                      <!-- 「查不到負責人」要講出來，不能留白——留白會被當成還沒載完，
                           而這件事正是事故當下最需要立刻知道的（沒人可通知）。 -->
                      <div v-else class="bao none">⚠ 無登記負責人，需人工確認</div>
                    </div>
                  </td>
                </tr>
              </template>
              <tr v-if="bizSorted.length === 0"><td colspan="3" class="dim">無</td></tr>
            </tbody>
          </table>

          <div class="psec">依機房位置</div>
          <table class="tbl mini">
            <thead><tr>
              <SortTh k="location" :active="locSortKey" :dir="locSortDir" @sort="locToggle">位置</SortTh>
              <SortTh k="count" :active="locSortKey" :dir="locSortDir" @sort="locToggle">數量</SortTh>
            </tr></thead>
            <tbody>
              <template v-for="r in locSorted" :key="r.location">
                <tr>
                  <td>{{ r.location }}</td>
                  <td class="mono">
                    <button type="button" class="bizx" :class="{ open: expandedLoc === r.location }"
                            :title="`展開/收合 ${r.location} 的 ${r.count} 筆`" @click="toggleLoc(r.location)">
                      {{ r.count }} <span class="arw">▸</span>
                    </button>
                  </td>
                </tr>
                <tr v-if="expandedLoc === r.location" class="bizdetail">
                  <td colspan="2">
                    <div v-for="it in r.items" :key="it.node_id" class="bam">{{ itemLine(it) }}</div>
                  </td>
                </tr>
              </template>
              <tr v-if="locSorted.length === 0"><td colspan="2" class="dim">無</td></tr>
            </tbody>
          </table>

          <div class="psec">依環境</div>
          <table class="tbl mini">
            <thead><tr>
              <SortTh k="environment" :active="envSortKey" :dir="envSortDir" @sort="envToggle">環境</SortTh>
              <SortTh k="count" :active="envSortKey" :dir="envSortDir" @sort="envToggle">數量</SortTh>
            </tr></thead>
            <tbody>
              <template v-for="r in envSorted" :key="r.environment">
                <tr>
                  <td>{{ r.environment }}</td>
                  <td class="mono">
                    <button type="button" class="bizx" :class="{ open: expandedEnv === r.environment }"
                            :title="`展開/收合 ${r.environment} 的 ${r.count} 筆`" @click="toggleEnv(r.environment)">
                      {{ r.count }} <span class="arw">▸</span>
                    </button>
                  </td>
                </tr>
                <tr v-if="expandedEnv === r.environment" class="bizdetail">
                  <td colspan="2">
                    <div v-for="it in r.items" :key="it.node_id" class="bam">{{ itemLine(it) }}</div>
                  </td>
                </tr>
              </template>
              <tr v-if="envSorted.length === 0"><td colspan="2" class="dim">無</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 距離/關係(depth/edge_type)不擺出來給人看——2026-08-20 使用者：「距離跟關係
           應該是系統自己算好的，不是人去看的」。這裡只留節點(可點進去換查詢中心)跟
           可信度(證據/推論會影響你該不該信這筆)，depth/edge_type 改成 dependentsSorted
           預設排序依據，不再是可視欄位。 -->
      <div class="section-divider">受影響節點清單{{ mode === 'probe' ? '（誰依賴它——上游）' : '' }}</div>
      <table class="tbl">
        <thead><tr>
          <SortTh k="label" :active="depSortKey" :dir="depSortDir" @sort="depToggle">節點</SortTh>
          <SortTh k="confidence" :active="depSortKey" :dir="depSortDir" @sort="depToggle">可信度</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="h in dependentsSorted" :key="h.node_id">
            <td><button class="dl linkbtn" @click="pivotTo(h.node_id)">{{ h.label }}</button></td>
            <td><span class="pill" :class="h.confidence === '證據' ? 'done' : h.confidence === '推論' ? 'open' : 'gone-pill'">{{ h.confidence }}</span></td>
          </tr>
          <tr v-if="dependentsSorted.length === 0"><td colspan="2" class="dim">末端節點，沒有其他東西依賴它。</td></tr>
        </tbody>
      </table>

      <template v-if="mode === 'probe'">
        <div class="section-divider">它依賴誰（下游——可能是肇因）</div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="label" :active="depcSortKey" :dir="depcSortDir" @sort="depcToggle">節點</SortTh>
            <SortTh k="confidence" :active="depcSortKey" :dir="depcSortDir" @sort="depcToggle">可信度</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="h in dependenciesSorted" :key="h.node_id">
              <td><button class="dl linkbtn" @click="pivotTo(h.node_id)">{{ h.label }}</button></td>
              <td><span class="pill" :class="h.confidence === '證據' ? 'done' : h.confidence === '推論' ? 'open' : 'gone-pill'">{{ h.confidence }}</span></td>
            </tr>
            <tr v-if="dependenciesSorted.length === 0"><td colspan="2" class="dim">它不依賴任何已知節點。</td></tr>
          </tbody>
        </table>
      </template>

      <!-- 「應通知」原本是以人為單位的通訊錄（姓名/角色/電話/代理人），2026-08-20
           使用者：事故當下要回答的是「這台機器多重要、聯絡不到怎麼辦」，不是「這個人
           是誰」——通訊錄視角拿掉，改導去「建立檢查清單」：以機器為單位、依嚴重度
           自動排序、缺聯絡方式會顯眼標警示，才是搶救當下真正要用的清單。 -->
      <div class="section-divider">應通知</div>
      <div class="notifycta">
        <p>依業務系統展開表格已經列出每台機器的負責人；要開一份可勾狀態、寫備註、
          全隊共用的派工清單，按右上角「📋 建立檢查清單」——會依嚴重度自動排序，
          缺聯絡方式的機器會標警示，不用自己在通訊錄裡找。</p>
      </div>

      <template v-if="unkSorted.length > 0">
        <div class="section-divider warn-title">⚠ 查不到負責人（有資產但找不到聯絡窗口）</div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="asset_serial" :active="unkSortKey" :dir="unkSortDir" @sort="unkToggle">資產序號</SortTh>
            <SortTh k="label" :active="unkSortKey" :dir="unkSortDir" @sort="unkToggle">名稱</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in unkSorted" :key="r.asset_serial">
              <td><NuxtLink class="dl mono" :to="`/assets/${r.asset_serial}`">{{ r.asset_serial }}</NuxtLink></td>
              <td>{{ r.label }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </template>
  </div>
</template>

<style scoped>
.topo { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 4px; }
.title .ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand); text-transform: uppercase; }
.title h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); letter-spacing: -.5px; }
.lede { font-size: 13px; color: var(--ink-soft); margin: 8px 0 16px; line-height: 1.7; max-width: 90ch; }
.lede b { color: var(--brand); }

.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }
.toolbar .sp { flex: 1; }
.tb { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 9px;
  border: 1px solid var(--border-strong); background: rgba(15,23,42,.05); color: var(--ink-soft); cursor: pointer; }
.tb:hover:not(:disabled) { border-color: var(--brand); color: var(--brand); }
.tb:disabled { opacity: .55; cursor: progress; }
.tb.go { background: linear-gradient(135deg,#009142,#00703a); color: #04120e; border: none; font-weight: 700; }
.tb.small { padding: 4px 10px; font-size: 11.5px; margin-left: 10px; }
.sel, .kw { font-family: inherit; font-size: 12.5px; padding: 7px 10px; border-radius: 9px;
  border: 1px solid var(--border-strong); background: rgba(15,23,42,.04); color: var(--ink-soft); }
.kw { min-width: 260px; }
.kw.port { min-width: 110px; }

.glass { background: rgba(15,23,42,.035); border: 1px solid rgba(15,23,42,.07); border-radius: 18px; backdrop-filter: blur(10px); }
.panel-msg { padding: 16px 18px; margin-bottom: 16px; }
.pm-title { font-weight: 700; color: var(--ink); margin-bottom: 8px; }
.candidates { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 8px; }
.cand { font-family: inherit; font-size: 12.5px; padding: 7px 12px; border-radius: 9px;
  border: 1px solid rgba(0,145,66,.4); background: rgba(0,145,66,.08); color: var(--brand); cursor: pointer; }
.cand:hover { background: rgba(0,145,66,.16); }
.seg-info { font-size: 13px; color: var(--ink-soft); }
.svc-info { margin-top: 8px; font-size: 13px; }
.muted, .dim { color: var(--ink-soft); opacity: .8; }

.node-head { padding: 14px 18px; margin-bottom: 12px; }
.nh-label { font-size: 18px; font-weight: 700; color: var(--ink); }
.nh-meta { font-size: 11px; color: var(--ink-soft); margin-top: 2px; }

.snap-banner { padding: 10px 16px; margin-bottom: 12px; font-size: 12.5px; color: var(--ink-soft);
  border-color: rgba(255,184,103,.4); }
.snap-banner b { color: #b45309; }
.snap-form { padding: 14px 18px; margin-bottom: 16px; }
.snap-form .hint { font-size: 12px; color: var(--ink-soft); margin: 4px 0 10px; }
.snap-row { display: flex; gap: 10px; align-items: center; }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: rgba(15,23,42,.035); border: 1px solid rgba(15,23,42,.07); border-radius: 14px; padding: 14px 16px; }
.tile.warn { border-color: rgba(230, 170, 60, .45); }
.t-num { font-size: 26px; font-weight: 700; color: var(--brand); line-height: 1.1; }
.t-lbl { font-size: 12px; color: var(--ink-soft); margin-top: 4px; }

.layout { display: grid; grid-template-columns: 1fr 360px; gap: 16px; margin-bottom: 20px; }
.graphwrap { position: relative; min-height: 480px; padding-top: 46px; }
#cy { position: absolute; inset: 46px 0 0 0; }

.graph-toolbar { position: absolute; top: 8px; left: 12px; right: 12px; z-index: 5;
  display: flex; align-items: center; gap: 8px; }
.graph-search { min-width: 0; flex: 1; max-width: 280px; }

.graphwrap.fullscreen { position: fixed; inset: 12px; z-index: 500; min-height: 0;
  box-shadow: 0 30px 80px rgba(0,0,0,.6); }
.layout-fs { position: relative; }
.panel { padding: 14px 16px; overflow: auto; max-height: 700px; }
.psec { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--brand); margin: 14px 0 6px; }
.psec:first-child { margin-top: 0; }
.notifycta { font-size: 12.5px; color: var(--ink-soft); line-height: 1.7; max-width: 70ch;
  padding: 10px 14px; border: 1px solid var(--border-strong); border-radius: 10px; background: rgba(15,23,42,.03); }

.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th, .tbl td { text-align: left; padding: 7px 10px; border-bottom: 1px solid rgba(15,23,42,.06); }
.tbl.mini th, .tbl.mini td { padding: 5px 8px; font-size: 12px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
/* 涵蓋範圍聲明。用邊框與底色跟結果表格區隔，但不做成可收合的元件——
   這塊的價值就在於它一定會被看到。 */
.cov { border: 1px solid rgba(224,176,96,.35); background: rgba(224,176,96,.06);
       border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; }
.covh { font-size: 12px; color: #e0b060; margin-bottom: 6px; }
.covr { display: grid; grid-template-columns: 18px 190px 1fr; gap: 6px;
        font-size: 11px; line-height: 1.6; padding: 1px 0; }
.covr .covn { color: var(--ink); }
.covr .covd { color: var(--ink-soft); }
.covr.ok .covd { color: var(--ink-soft); }
.covr.out_of_scope { opacity: .55; }      /* 明確排除的維度壓暗，但不隱藏 */
@media (max-width: 640px) { .covr { grid-template-columns: 18px 1fr; }
                            .covr .covd { grid-column: 2; } }

/* 業務系統的資產數＝展開鈕，展開後就地列出是哪幾台、找誰。 */
/* 依業務系統表格欄寬（2026-08-20 使用者：系統名很長被擠、資產數個位數卻佔很寬）
   ——嚴重度／資產數合成一欄、上下疊放，把寬度讓給系統名跟負責人。 */
.biztbl { table-layout: fixed; }
.biztbl .c-name { width: 42%; }
.biztbl .c-sev { width: 14%; }
.biztbl .c-own { width: 44%; }
.stackth { display: table-cell; }
.stackth .sortable { display: block; cursor: pointer; user-select: none; white-space: nowrap;
  font-size: 11px; line-height: 1.6; }
.stackth .sortable:hover { color: var(--brand, #009142); }
.stackth .sortable.on { color: var(--brand, #009142); }
.stackth .arw { font-style: normal; font-size: 9px; margin-left: 3px; opacity: .35; }
.stackth .sortable:hover .arw, .stackth .sortable.on .arw { opacity: 1; }
.stacktd { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }

.bizx { background: none; border: none; padding: 0; cursor: pointer; font: inherit;
        color: var(--brand); display: inline-flex; align-items: center; gap: 4px; }
.bizx:hover { text-decoration: underline; }
.bizx .arw { display: inline-block; transition: transform .12s; font-size: 9px; }
.bizx.open .arw { transform: rotate(90deg); }
.bizdetail td { background: rgba(15,23,42,.02); }
.bizasset { padding: 4px 0 6px 10px; border-left: 2px solid var(--border-strong); margin-bottom: 4px; }
.bizasset:last-child { margin-bottom: 0; }
.bam { font-size: 12px; }
.bao { font-size: 11px; color: var(--ink-soft); margin-top: 2px; display: flex; flex-wrap: wrap; gap: 2px 12px; }
.bao.none { color: #e0b060; }      /* 沒有負責人是事故當下必須立刻看到的事 */
.baot { font-size: 11px; color: var(--ink-soft); margin-top: 3px; border-collapse: collapse; }
.baot td { padding: 1px 12px 1px 0; vertical-align: top; }
.baot .baor { color: var(--ink-soft); white-space: nowrap; }
.baot .baon { color: var(--ink); white-space: nowrap; }
/* 負責人直接進表格那一欄 */
.bizown { font-size: 11px; color: var(--ink); }
.bizowi { margin-right: 10px; white-space: nowrap; }
/* 「還沒有這份資料」壓暗但看得見——跟「這個人沒有分機」要分得出來 */
.bapend { color: #6b7f79; font-style: italic; }

.dl { color: var(--brand); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.linkbtn { background: none; border: none; padding: 0; font: inherit; cursor: pointer; }

.pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
.pill.done { background: rgba(0,145,66,.16); color: var(--brand); }
.pill.open { background: rgba(255,184,103,.16); color: #d9a441; }
.pill.gone-pill { background: rgba(255,107,107,.16); color: #dc2626; }

.section-divider { font-size: 13px; font-weight: 700; color: var(--ink); margin: 20px 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid rgba(15,23,42,.08); }
.section-divider.warn-title { color: #b45309; }
</style>
