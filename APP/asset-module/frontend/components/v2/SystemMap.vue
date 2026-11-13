<script setup lang="ts">
// 3.1 業務系統頁「上下游」頁籤的系統主機部署圖（V58，2.048.1 V2 原生；取代嵌入的舊 components/SystemMap.vue，
// 舊元件留給舊頁 /topology、/blast 用，舊網址都轉到 V2）。圖照舊；圖下面加一張 V2Table 清單（機房 × 分組，
// 可排序、項次、匯出、台數點下去列出那幾台；列印時不印清單），其他頁籤的表也都換成 V2Table。
// 換系統、主機搜尋、連線證據：條件一改就重抓，只採用最新那一次（useLatest）。
//
// 以下是原元件的說明——系統主機部署圖：選一個業務系統（APID），一眼看到它跑在哪些機房、哪些 Cluster、哪些實體機，
// 機房整間停掉會怎樣，以及上下游系統。4-1 系統主機部署圖、4-2 影響範圍查詢共用這一份
// （不要複製第二份）。資料：/api/blast/system-map（後端 blast_radius.system_map）。
//
// 2026-10-22 改版：原本每台主機一個節點的 cytoscape 圖，178 台排成一欄根本看不了；
// 改成「系統 → 機房框 → Cluster 列／實體列」的巢狀框，主機預設不畫，點列才展開。
// 所有分組、台數、停機判讀都在後端算（rooms／outage），前端只負責畫與下鑽。
// 顏色一律取 main.css 變數。
const props = defineProps<{ apiId?: string | null }>()
const emit = defineEmits<{
  (e: 'update:apiId', v: string | null): void
  /** ESXi／Cluster／Datastore 沒有資產頁可去時，交給外層開影響範圍查詢 */
  (e: 'impact', nodeId: string): void
}>()

interface SysRow {
  node_id: string; api_id: string; name: string; usage_unit?: string | null; custodian?: string | null; severity: string
  asset_count: number; sys_class?: string | null; class_group: string
}
interface HostHit { key: string; hostname: string; asset_serial: string; ips: string[]; systems: { api_id: string; name: string }[] }
interface Host {
  key: string; asset_serial: string; serials: string[]; hostname: string; ips: string[]; ip: string | null
  os: string | null; environment: string | null; location: string | null; room: string; env_group: string
  record_count: number; esxi: string[]; datastores: string[]; on_esxi: boolean
  exist_level: string | null; exist_label: string; exist_score: number | null; seen: boolean
  asset_names: string[]; os_class: string; os_family: string; models: string[]; hw_serials: string[]
}
interface RackRow { key: string; u_start: number | null; u_end: number | null; via_esxi: string | null; rack_raw: string | null }
interface SysRacks {
  rooms: { room: string; host_count: number; racks: { rack: string; floor: string | null; host_count: number; rows: RackRow[]; power: { w: number; kg: number; free_u: number; unplaced_count?: number; largest_free_u: number; unknown_count: number; off_count: number; pending_count: number } }[] }[]
  no_rack: { key: string; raw: string | null; via_esxi: string[] }[]; no_rack_count: number; racked_count: number
}
interface UnregVm {
  key: string; asset_serial: string; hostname: string; ip: string | null; os: string | null; esxi: string[]
  exist_level: string | null; exist_label: string; exist_score: number | null; seen: boolean
}
interface Esxi { node_id: string; label: string; asset_serial: string | null; clusters: string[]; hosts: string[]; host_count: number }
interface Cluster { node_id: string; label: string; esxi: string[]; unregistered_vms: UnregVm[]; unregistered_count: number }
interface Ds { node_id: string; label: string; hosts: string[]; host_count: number }
interface Evidence { count: number; observations: number; first_seen: string | null; last_seen: string | null; hosts: string[]; ports: number[]; protocols: string[] }
interface Dep { api_id: string; name: string; dep_type: string | null; has_hosts: boolean; source?: 'registered' | 'observed'; hub?: boolean; evidence?: Evidence | null }
interface ObservedMeta { hubs: { api_id: string; fan_in: number; fan_out: number }[]; hub_threshold: number; stats: Record<string, number>; settings: Record<string, any> }
interface EdgeRow { id: number; host_ip: string; asset_serial: string | null; direction: string; local_port: number; peer_ip: string; peer_port: number; count: number; first_seen: string | null; last_seen: string | null }
interface RoomGroup { id: string; kind: 'cluster' | 'nocluster' | 'physical'; label: string; hosts: string[]; esxi: string[]; host_count: number }
interface Room {
  room: string; host_count: number; env: Record<string, number>; hosts: string[]; groups: RoomGroup[]
  seen: number; registered_only: number; seen_hosts: string[]; registered_only_hosts: string[]
}
type OutKey = 'here_prod' | 'here_dr' | 'else_prod' | 'else_dr'
interface Outage {
  room: string; keys: Record<OutKey, string[]>; here_prod: number; here_dr: number; else_prod: number; else_dr: number
  verdict_kind: 'low' | 'down' | 'short' | 'ok'; verdict: string
}
interface MapData {
  api_id: string; node_id: string; name: string; usage_unit: string | null; custodian: string | null
  ap_department: string | null; ap_owner: string | null; sys_class: string | null; severity: string
  hosts: Host[]; retired: { asset_serial: string; hostname: string | null; ip: string | null; asset_status: string | null }[]
  esxi: Esxi[]; clusters: Cluster[]; datastores: Ds[]; rooms: Room[]; outage: Outage[]
  depends_on: Dep[]; depended_by: Dep[]; deps_available: boolean; exist_threshold: number; racks: SysRacks
  deps_source?: 'registered' | 'observed' | 'mixed' | null; is_hub?: boolean; observed?: ObservedMeta | null
  summary: Record<string, number>
}

const { apiFetch } = useApi()
const { showToast } = useToast()

// ===== 系統挑選 =====
const systems = ref<SysRow[]>([])
const pickKw = ref('')
const pickOpen = ref(false)
async function loadSystems() {
  try {
    systems.value = await apiFetch<SysRow[]>('/api/blast/systems')
  } catch (e: any) {
    systems.value = []
    showToast(`業務系統清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
const PICK_MAX = 80

// 第一層：系統分級（第一類／第二類…，後端由 sys_class 推出 class_group）。上次選的記在瀏覽器
const CLASS_KEY = 'webit3.systemMap.class'
const ALL = '全部'
function readClass() {
  try { return localStorage.getItem(CLASS_KEY) || ALL } catch { return ALL } // 私密視窗／被擋：退回「全部」
}
const pickClass = ref<string>(ALL)
function setClass(c: string) {
  pickClass.value = c
  try { localStorage.setItem(CLASS_KEY, c) } catch { /* 存不了只是下次不記得，不影響使用 */ }
}
const classGroups = computed(() => {
  const cnt = new Map<string, number>()
  for (const x of systems.value) cnt.set(x.class_group, (cnt.get(x.class_group) ?? 0) + 1)
  return [...cnt.entries()].map(([name, n]) => ({ name, n }))
    .sort((a, b) => (a.name === '未分類' ? 1 : 0) - (b.name === '未分類' ? 1 : 0) || a.name.localeCompare(b.name, 'zh-Hant'))
})
const classOf = computed(() => new Map(systems.value.map(x => [x.api_id, x.class_group])))
function inClass(apiId: string) { return pickClass.value === ALL || classOf.value.get(apiId) === pickClass.value }

const pickMatches = computed(() => {
  const kw = pickKw.value.trim().toLowerCase()
  const src = systems.value.filter(x => inClass(x.api_id))
  if (!kw) return src
  return src.filter(x => [x.api_id, x.name, x.usage_unit, x.custodian].some(v => (v || '').toLowerCase().includes(kw)))
})
const pickFiltered = computed(() => pickMatches.value.slice(0, PICK_MAX))
function pick(x: SysRow) {
  pickOpen.value = false
  pickKw.value = ''
  emit('update:apiId', x.api_id)
}

// 挑選框打的是主機名／IP：後端找出是哪台、屬於哪些系統（只列目前分級裡的系統）
const hostHits = ref<HostHit[]>([])
let hostTimer: ReturnType<typeof setTimeout> | null = null
const beginHost = useLatest()
watch(pickKw, (q) => {
  if (hostTimer) clearTimeout(hostTimer)
  const isLatest = beginHost()
  if (normQ(q).length < 2) { hostHits.value = []; return }
  hostTimer = setTimeout(async () => {
    try {
      const r = await apiFetch<HostHit[]>('/api/blast/host-systems', { query: { q } })
      if (isLatest()) hostHits.value = r
    } catch (e: any) {
      if (!isLatest()) return
      hostHits.value = []
      showToast(`主機查詢失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
    }
  }, 250)
})
const hostHitsShown = computed(() => hostHits.value
  .map(h => ({ ...h, systems: h.systems.filter(x => inClass(x.api_id)) }))
  .filter(h => h.systems.length))
let nextSearch = ''
function pickHost(h: HostHit, apiId: string) {
  pickOpen.value = false
  pickKw.value = ''
  nextSearch = h.hostname
  if (apiId === props.apiId) { mapSearch.value = nextSearch; nextSearch = '' }
  else emit('update:apiId', apiId)
}
function onPickBlur() { setTimeout(() => { pickOpen.value = false }, 150) }
const currentSys = computed(() => systems.value.find(s => s.api_id === props.apiId))

// ===== 地圖資料 =====
const data = ref<MapData | null>(null)
const loading = ref(false)
const loadError = ref('')
const beginMap = useLatest()
async function loadMap(id: string) {
  const isLatest = beginMap()
  // 換系統：上一個系統的連線證據清掉，還在路上的證據請求作廢（晚回來不會蓋到新系統上）
  beginEdge()
  edge.value = null
  edgeLoading.value = false
  loading.value = true
  loadError.value = ''
  try {
    const r = await apiFetch<MapData>('/api/blast/system-map', { query: { api_id: id } })
    if (!isLatest()) return
    data.value = r
    hostFilter.value = { label: '全部', keys: null }
    expanded.value = {}
    mapSearch.value = nextSearch
    nextSearch = ''
    selRack.value = null
    osFilter.value = ALL
  } catch (e: any) {
    if (!isLatest()) return
    data.value = null
    loadError.value = e?.data?.detail ?? e?.message ?? '未知錯誤'
  } finally {
    if (isLatest()) loading.value = false
  }
}
watch(() => props.apiId, (id) => { if (id) loadMap(id); else data.value = null })
onMounted(async () => {
  pickClass.value = readClass()
  await loadSystems()
  if (props.apiId) await loadMap(props.apiId)
})

const hostByKey = computed(() => new Map((data.value?.hosts ?? []).map(h => [h.key, h])))
function esxiLabel(id: string) { return data.value?.esxi.find(e => e.node_id === id)?.label ?? id }
function dsLabel(id: string) { return data.value?.datastores.find(d => d.node_id === id)?.label ?? id }
function clusterLabel(id: string) { return data.value?.clusters.find(c => c.node_id === id)?.label ?? id }
function esxiHosts(id: string) { return data.value?.esxi.find(e => e.node_id === id)?.hosts ?? [] }

// ===== 頁簽 =====
type Tab = 'graph' | 'outage' | 'racks' | 'hosts' | 'infra' | 'deps'
const tab = ref<Tab>('graph')

// ===== 主機清單（所有下鑽都走這裡：後端給的主機鍵清單）=====
type HostFilter = { label: string; keys: string[] | null; retired?: boolean; unreg?: UnregVm[] }
const hostFilter = ref<HostFilter>({ label: '全部', keys: null })
function showKeys(label: string, keys: string[] | null) {
  hostFilter.value = { label, keys }
  tab.value = 'hosts'
}
function showSeen(label: string, seen: boolean) {
  showKeys(label, (data.value?.hosts ?? []).filter(h => h.seen === seen).map(h => h.key))
}
function clusterOf(id: string) { return data.value?.clusters.find(c => c.node_id === id) }
function unregCount(id: string) { return clusterOf(id)?.unregistered_count ?? 0 }
function showUnreg(id: string) {
  const c = clusterOf(id)
  if (!c) return
  hostFilter.value = { label: `同叢集未登記系統的 VM：${c.label}`, keys: null, unreg: c.unregistered_vms }
  tab.value = 'hosts'
}
const unregNumbered = computed(() => (hostFilter.value.unreg ?? []).map((v, i) => ({
  ...v, __no: i + 1, esxi_text: v.esxi.join(', '), exist_sort: v.exist_score ?? -1,
})))
// 存在可信度徽章：畫面放短字＋分數，完整說明放 title
const EXIST_SHORT: Record<string, string> = {
  onboarded: '已納管', inventory: '匯入清單有', collect_stale: '收集較舊', scan: '掃描活著', cia_only: '只有登記', none: '無證據',
}
function existShort(level: string | null) { return level ? (EXIST_SHORT[level] ?? level) : '不明' }
function showPreset(k: 'all' | 'on' | 'off' | 'retired') {
  const hs = data.value?.hosts ?? []
  if (k === 'all') showKeys('全部', null)
  else if (k === 'on') showKeys('掛在 ESXi', hs.filter(h => h.on_esxi).map(h => h.key))
  else if (k === 'off') showKeys('未對應 ESXi', hs.filter(h => !h.on_esxi).map(h => h.key))
  else { hostFilter.value = { label: '退役（備查）', keys: null, retired: true }; tab.value = 'hosts' }
}
const hostsNumbered = computed(() => (data.value?.hosts ?? []).map((h, i) => ({
  ...h, __no: i + 1, exist_t: `${existShort(h.exist_level)} ${h.exist_score ?? ''}`.trim(), exist_sort: h.exist_score ?? -1, os_text: h.os || h.os_class, ip_text: h.ips.join(', '), esxi_text: h.esxi.map(esxiLabel).join(', '),
  ds_text: h.datastores.map(dsLabel).join(', '),
})))
// 地圖內搜主機：主機名／資產名稱／IP，不分大小寫、忽略空白與連字號（跟後端 search_norm 同規則）
const mapSearch = ref('')
// IP 以整段比對（同後端 ip_match）：完全相同，或到某個點為止的前綴；不做子字串
function ipMatch(ip: string, q: string) {
  if (!ip || !q || !/^[\d.]+$/.test(q)) return false
  return ip === q || ip.startsWith(q.endsWith('.') ? q : q + '.')
}
function normQ(v: string | null | undefined) { return (v || '').toLowerCase().replace(/[\s\-_]/g, '') }
// 一個框三個角度：硬體（機型／硬體序號／資產序號）、主機（主機名／資產名稱／IP）、系統（APID／系統名稱）
type Angle = 'hardware' | 'host' | 'system'
const ANGLE_LABEL: Record<Angle, string> = { hardware: '硬體', host: '主機', system: '系統' }
const ANGLES: Angle[] = ['hardware', 'host', 'system']
const searchAngle = ref<Angle | 'all'>('all')
watch(mapSearch, () => { searchAngle.value = 'all' })
const angleKeys = computed<Record<Angle, string[]> | null>(() => {
  const qn = normQ(mapSearch.value)
  const qi = mapSearch.value.trim()
  if (!qn || !data.value) return null
  const hs = data.value.hosts
  const has = (arr: (string | null | undefined)[]) => arr.some(t => !!t && normQ(t).includes(qn))
  const sysHit = normQ(data.value.api_id).includes(qn) || normQ(data.value.name).includes(qn)
  return {
    hardware: hs.filter(h => has([...h.models, ...h.hw_serials, ...h.serials])).map(h => h.key),
    host: hs.filter(h => has([h.hostname, ...h.asset_names]) || h.ips.some(ip => ipMatch(ip, qi))).map(h => h.key),
    system: sysHit ? hs.map(h => h.key) : [],
  }
})
const matchKeys = computed<Set<string> | null>(() => {
  const a = angleKeys.value
  if (!a) return null
  if (searchAngle.value !== 'all') return new Set(a[searchAngle.value])
  return new Set([...a.hardware, ...a.host, ...a.system])
})
const matchHosts = computed(() => matchKeys.value ? roomHosts([...matchKeys.value]) : [])
function roomHit(r: Room) { return !!matchKeys.value && r.hosts.some(k => matchKeys.value!.has(k)) }
function groupHit(g: RoomGroup) { return !!matchKeys.value && g.hosts.some(k => matchKeys.value!.has(k)) }
function isOpen(r: Room, g: RoomGroup) { return !!expanded.value[`${r.room}|${g.id}`] || groupHit(g) }
function groupHosts(g: RoomGroup) {
  const hs = roomHosts(g.hosts)
  return matchKeys.value ? hs.filter(h => matchKeys.value!.has(h.key)) : hs
}

const hostsBeforeOs = computed(() => {
  const keys = hostFilter.value.keys
  let src = hostsNumbered.value
  if (keys) { const set = new Set(keys); src = src.filter(h => set.has(h.key)) }
  if (matchKeys.value) src = src.filter(h => matchKeys.value!.has(h.key))
  return src
})
// OS 大類篩選（後端 os_class 收成 Linux／Windows／AIX／其他；不分頁簽）
const OS_FAMILIES = ['Linux', 'Windows', 'AIX', '其他']
const osFilter = ref<string>(ALL)
const osCounts = computed(() => {
  const c: Record<string, number> = {}
  for (const f of OS_FAMILIES) c[f] = 0
  for (const h of hostsBeforeOs.value) c[h.os_family] = (c[h.os_family] ?? 0) + 1
  return c
})
const hostsFiltered = computed(() => osFilter.value === ALL
  ? hostsBeforeOs.value : hostsBeforeOs.value.filter(h => h.os_family === osFilter.value))
const retiredNumbered = computed(() => (data.value?.retired ?? []).map((r, i) => ({ ...r, __no: i + 1 })))

// ===== 部署圖：機房框 =====
const expanded = ref<Record<string, boolean>>({})
function toggleGroup(id: string) { expanded.value = { ...expanded.value, [id]: !expanded.value[id] } }
const GROUP_KIND: Record<string, string> = { cluster: 'Cluster', nocluster: 'ESXi', physical: '實體' }
function roomHosts(keys: string[]) {
  return keys.map(k => hostByKey.value.get(k)).filter((h): h is Host => !!h)
    .sort((a, b) => a.hostname.localeCompare(b.hostname, 'zh-Hant', { numeric: true }))
}
function isSlim(r: Room) { return r.groups.length === 1 && r.groups[0].kind === 'physical' }

// ===== 機櫃 =====
const selRack = ref<{ room: string; rack: string } | null>(null)
const rackedKeys = computed(() => (data.value?.racks.rooms ?? []).flatMap(r => r.racks.flatMap(k => k.rows.map(x => x.key))))

// ===== 機房停機影響 =====
const outageNumbered = computed(() => (data.value?.outage ?? []).map((o, i) => ({ ...o, __no: i + 1 })))
const OUT_LABEL: Record<OutKey, string> = { here_prod: '這裡的正式', here_dr: '這裡的備援', else_prod: '別處還有正式', else_dr: '別處還有備援' }
const OUT_KEYS: OutKey[] = ['here_prod', 'here_dr', 'else_prod', 'else_dr']
const OUT_SLOTS = OUT_KEYS.map(k => ({ k, slot: `cell-${k}` }))
const OUT_COLS = [{ k: 'room', label: '機房' }, ...OUT_KEYS.map(k => ({ k, label: OUT_LABEL[k], mono: true })), { k: 'verdict', label: '判讀' }]

// ===== ESXi／儲存 =====
const esxiNumbered = computed(() => (data.value?.esxi ?? []).map((e, i) => ({
  ...e, __no: i + 1, cluster_text: e.clusters.map(clusterLabel).join(', '),
})))
const dsNumbered = computed(() => (data.value?.datastores ?? []).map((d, i) => ({ ...d, __no: i + 1 })))

// ===== 上下游 =====
const depsNumbered = computed(() => {
  const d = data.value
  if (!d) return []
  const rows = [
    ...d.depended_by.map(x => ({ ...x, dir: '依賴它' })),
    ...d.depends_on.map(x => ({ ...x, dir: '它依賴' })),
  ]
  return rows.map((r, i) => ({
    ...r, __no: i + 1, key: r.dir + r.api_id, ev_t: r.evidence ? `${r.evidence.observations}（${r.evidence.count} 組連線、埠 ${r.evidence.ports.join(', ')}）` : '', hosts_text: r.has_hosts ? '有' : '無', src_text: srcLabel(r),
    obs: r.evidence?.observations ?? -1, last_seen: r.evidence?.last_seen ?? '',
  }))
})
const DEPS_SOURCE_LABEL: Record<string, string> = { registered: '人工登記', observed: '連線推論', mixed: '人工登記＋連線推論' }
function srcLabel(r: Dep) {
  if (r.source !== 'observed') return '人工登記'
  return (r.evidence?.protocols ?? []).includes('SSH/SFTP') ? '連線推論（SSH/SFTP）' : '連線推論'
}
// 連線推論證據下鑽：caller 依賴 callee
const edge = ref<{ label: string; rows: EdgeRow[]; stale_days: number } | null>(null)
const edgeLoading = ref(false)
const beginEdge = useLatest()
async function openEdge(r: Dep & { dir: string }) {
  if (!data.value) return
  const isLatest = beginEdge()
  const caller = r.dir === '依賴它' ? r.api_id : data.value.api_id
  const callee = r.dir === '依賴它' ? data.value.api_id : r.api_id
  edgeLoading.value = true
  try {
    const res = await apiFetch<{ rows: EdgeRow[]; stale_days: number }>('/api/blast/observed-edge', { query: { caller, callee } })
    if (isLatest()) edge.value = { label: `${caller} → ${callee}`, rows: res.rows, stale_days: res.stale_days }
  } catch (e: any) {
    if (isLatest()) showToast(`證據載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally {
    if (isLatest()) edgeLoading.value = false
  }
}
const edgeRows = computed(() => (edge.value?.rows ?? []).map((r, i) => ({ ...r, __no: i + 1, dir_text: r.direction === 'inbound' ? '進' : '出' })))
// ===== V2Table 欄位 =====
const machineUrl = (serial: string | null | undefined) => (serial ? `/v2/machine/${encodeURIComponent(serial)}` : null)
const UNREG_COLS = [
  { k: 'hostname', label: 'VM', link: (r: any) => machineUrl(r.asset_serial) }, { k: 'ip', label: 'IP', mono: true }, { k: 'os', label: 'OS' },
  { k: 'esxi_text', label: 'ESXi' }, { k: 'exist_sort', label: '存在可信度', text: (r: any) => `${existShort(r.exist_level)} ${r.exist_score ?? ''}`.trim() },
]
const HOST_COLS = [
  { k: 'hostname', label: '主機', link: (r: any) => machineUrl(r.asset_serial) }, { k: 'ip_text', label: 'IP', mono: true },
  { k: 'os_text', label: 'OS' }, { k: 'environment', label: '環境' }, { k: 'room', label: '機房' },
  { k: 'esxi_text', label: 'ESXi' }, { k: 'ds_text', label: 'Datastore' }, { k: 'record_count', label: '登記筆數', mono: true },
  { k: 'exist_sort', label: '存在可信度', text: (r: any) => r.exist_t },
]
const RETIRED_COLS = [
  { k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => machineUrl(r.asset_serial) }, { k: 'hostname', label: '主機' },
  { k: 'ip', label: 'IP', mono: true }, { k: 'asset_status', label: '狀態' },
]
const ESXI_COLS = [
  { k: 'label', label: 'ESXi' }, { k: 'cluster_text', label: 'Cluster' }, { k: 'host_count', label: '本系統台數', mono: true },
  { k: 'asset_serial', label: '資產登記', mono: true, link: (r: any) => machineUrl(r.asset_serial), text: (r: any) => r.asset_serial || '未登記' },
]
const DS_COLS = [{ k: 'label', label: 'Datastore' }, { k: 'host_count', label: '本系統台數', mono: true }]
const DEP_COLS = [
  { k: 'dir', label: '方向' }, { k: 'name', label: '系統' }, { k: 'api_id', label: '代碼', mono: true }, { k: 'dep_type', label: '類型' },
  { k: 'hosts_text', label: '有登記主機' }, { k: 'src_text', label: '來源' }, { k: 'obs', label: '證據（觀察次數）', text: (r: any) => r.ev_t },
  { k: 'last_seen', label: '最後看到', mono: true },
]
const EDGE_COLS = [
  { k: 'host_ip', label: '收集的主機', mono: true, link: (r: any) => machineUrl(r.asset_serial) }, { k: 'dir_text', label: '方向' },
  { k: 'local_port', label: '本機埠', mono: true }, { k: 'peer_ip', label: '對方', mono: true }, { k: 'peer_port', label: '對方埠', mono: true },
  { k: 'count', label: '觀察次數', mono: true }, { k: 'first_seen', label: '第一次看到', mono: true }, { k: 'last_seen', label: '最後看到', mono: true },
]
// 圖下面的清單：一列＝圖上的一個分組（機房 × Cluster／ESXi／實體），「圖上編號」對得到圖上的 M1-2；
// 台數點下去切到主機清單只看那幾台（同一份主機鍵，筆數＝台數）；有搜尋時只列有命中的分組
const graphRows = computed(() => {
  const out: any[] = []
  ;(data.value?.rooms ?? []).forEach((r, i) => r.groups.forEach((g, j) => {
    if (matchKeys.value && !groupHit(g)) return
    out.push({
      __no: out.length + 1, key: `${r.room}|${g.id}`, fig: `M${i + 1}-${j + 1}`, room: r.room, kind_t: GROUP_KIND[g.kind] ?? g.kind,
      label: g.label, esxi_n: g.esxi.length || '', host_count: g.host_count, hosts: g.hosts, gid: g.id,
      unreg: g.kind === 'cluster' ? unregCount(g.id) : '',
    })
  }))
  return out
})
const GRAPH_COLS = [
  { k: 'fig', label: '圖上編號', mono: true }, { k: 'room', label: '機房' }, { k: 'kind_t', label: '類型' }, { k: 'label', label: '名稱' },
  { k: 'esxi_n', label: 'ESXi', mono: true }, { k: 'host_count', label: '本系統台數', mono: true },
  { k: 'unreg', label: '同叢集未登記系統的 VM', mono: true },
]

function recenter(dep: { api_id: string; has_hosts: boolean }) {
  if (!dep.has_hosts) { showToast('這個系統在資產庫沒有登記主機，畫不出地圖', 'warn'); return }
  emit('update:apiId', dep.api_id)
}
</script>

<template>
  <div class="smap">
    <div class="classbar">
      <button class="chip" :class="{ on: pickClass === ALL }" @click="setClass(ALL)">全部 {{ systems.length }}</button>
      <button v-for="c in classGroups" :key="c.name" class="chip" :class="{ on: pickClass === c.name }" @click="setClass(c.name)">{{ c.name }} {{ c.n }}</button>
    </div>
    <div class="picker">
      <div class="pick-box">
        <input v-model="pickKw" class="kw" type="text"
               :placeholder="currentSys ? `${currentSys.api_id}　${currentSys.name}` : '選業務系統：代碼／名稱，或打主機名／IP'"
               @focus="pickOpen = true" @input="pickOpen = true" @blur="onPickBlur">
        <ul v-if="pickOpen" class="pick-list" role="listbox">
          <li v-for="s in pickFiltered" :key="s.api_id" role="option" @mousedown.prevent="pick(s)">
            <span class="mono">{{ s.api_id }}</span>
            <span class="pn">{{ s.name }}</span>
            <span class="pu">{{ s.usage_unit || '' }}</span>
          </li>
          <template v-for="h in hostHitsShown" :key="h.key">
            <li class="hosthit-head muted">主機 <b>{{ h.hostname }}</b> <span class="mono">{{ h.ips.join(', ') }}</span> 屬於：</li>
            <li v-for="x in h.systems" :key="h.key + x.api_id" class="hosthit" role="option" @mousedown.prevent="pickHost(h, x.api_id)">
              <span class="mono">{{ x.api_id }}</span><span class="pn">{{ x.name }}</span>
            </li>
          </template>
          <li v-if="!pickFiltered.length && !hostHitsShown.length" class="muted">沒有符合的系統或主機</li>
          <li v-else-if="pickMatches.length > pickFiltered.length" class="muted">另有 {{ pickMatches.length - pickFiltered.length }} 個，請再打字縮小範圍</li>
        </ul>
      </div>
      <span class="muted">{{ pickClass === ALL ? `共 ${systems.length} 個業務系統` : `${pickClass} ${pickMatches.length} 個` }}</span>
      <InfoNote>選一個業務系統，看它的主機分在哪些機房、哪些 Cluster、哪些是實體機，以及機房整間停機的影響。台數依全站同一把尺（同一台的多筆登記算一台），退役不算。機房依登記的機房欄：去掉編號前綴，「分公司-XX」併成「分公司」。每一格都可以點：機房標題、Cluster 列、實體列都會列出那幾台主機；主機進資產詳細頁。</InfoNote>
    </div>

    <p v-if="!apiId" class="muted empty">先在上面選一個業務系統。</p>
    <p v-else-if="loading" class="muted empty">載入中…</p>
    <p v-else-if="loadError" class="err empty">系統主機部署圖載入失敗：{{ loadError }}</p>

    <div v-else-if="data" class="layout">
      <div class="main">
        <div class="tabs" role="tablist">
          <button type="button" role="tab" class="tab" :class="{ active: tab === 'graph' }" @click="tab = 'graph'">部署圖</button>
          <button type="button" role="tab" class="tab" :class="{ active: tab === 'outage' }" @click="tab = 'outage'">機房停機影響</button>
          <button type="button" role="tab" class="tab" :class="{ active: tab === 'racks' }" @click="tab = 'racks'">機櫃</button>
          <button type="button" role="tab" class="tab" :class="{ active: tab === 'hosts' }" @click="showPreset('all')">主機清單 {{ data.summary.hosts }}</button>
          <button type="button" role="tab" class="tab" :class="{ active: tab === 'infra' }" @click="tab = 'infra'">ESXi／儲存 {{ data.summary.esxi }}</button>
          <button type="button" role="tab" class="tab" :class="{ active: tab === 'deps' }" @click="tab = 'deps'">
            上下游 {{ data.deps_available ? data.summary.depends_on + data.summary.depended_by : '—' }}<span v-if="data.deps_source" class="tab-sub">（{{ DEPS_SOURCE_LABEL[data.deps_source] }}）</span>
          </button>
        </div>

        <!-- 部署圖：系統 → 機房框 → Cluster 列／實體列 -->
        <div v-if="tab === 'graph'">
          <div class="topline">
            <button class="sysbox" @click="showPreset('all')">
              <span class="mono">{{ data.api_id }}</span> · {{ data.name }} · <b>{{ data.summary.hosts }}</b> 台
            </button>
            <div class="seenbox">
              <button class="seen-on" @click="showSeen('實際看得到', true)">實際看得到 <b>{{ data.summary.seen }}</b></button>
              <button class="seen-off" @click="showSeen('只有登記（疑似下線）', false)">只有登記 <b>{{ data.summary.registered_only }}</b></button>
              <InfoNote>「實際看得到」＝存在可信度 ≥ {{ data.exist_threshold }} 分（已納管、在 DY／vCenter 最新匯入、近期收集或掃描到）；「只有登記」＝只在 CIA 有資料、各來源最新快照都沒有它，疑似已下線。分數沿用全站的存在可信度，退役另計。</InfoNote>
            </div>
            <button class="depbox" @click="tab = 'deps'">
              上下游：
              <template v-if="!data.deps_available">尚無資料</template>
              <template v-else>依賴它 {{ data.summary.depended_by }}・它依賴 {{ data.summary.depends_on }}</template>
            </button>
          </div>
          <div class="searchbar">
            <input v-model="mapSearch" class="kw sm" type="text" placeholder="找主機：名稱或 IP">
            <button v-if="mapSearch" class="tb" @click="mapSearch = ''">清除</button>
            <template v-if="matchKeys && angleKeys">
              <span class="found">找到 <b>{{ matchKeys.size }}</b> 台</span>
              <button v-for="a in ANGLES" :key="a" class="chip" :class="{ on: searchAngle === a }" :disabled="!angleKeys[a].length"
                      @click="searchAngle = searchAngle === a ? 'all' : a">{{ ANGLE_LABEL[a] }} {{ angleKeys[a].length }}</button>
              <NuxtLink v-for="h in matchHosts.slice(0, 30)" :key="h.key" :to="`/v2/machine/${encodeURIComponent(h.asset_serial)}`" class="chip lnkchip">{{ h.hostname }}</NuxtLink>
              <span v-if="matchHosts.length > 30" class="muted">…其餘看主機清單</span>
            </template>
          </div>
          <p class="legendline">
            點機房標題或 Cluster 列看主機
            <InfoNote>「同叢集未登記系統的 VM」＝跟本系統主機在同一個 Cluster 的 ESXi 上、但整台沒有任何一筆登記業務系統代碼（APID）的 VM（同一台的多筆登記任一筆有 APID 就不算）。它們不屬於本系統、不計入上面的台數；列出來是因為這個 Cluster 出事時它們也會一起受影響，而且沒有人認領。Cluster 橫跨多個機房時，每個機房框都顯示同一份清單。</InfoNote>
          </p>
          <div class="rooms">
            <div v-for="(r, i) in data.rooms" :key="r.room" class="room" :class="{ slim: isSlim(r), hit: roomHit(r), dim: matchKeys && !roomHit(r) }">
              <button class="room-head" @click="showKeys(`機房：${r.room}`, r.hosts)">
                <span class="no">M{{ i + 1 }}</span>
                <b class="rn">{{ r.room }}</b>
                <span class="rc"><b>{{ r.host_count }}</b> 台</span>
              </button>
              <div class="seenline">
                <button class="seen-on" @click="showKeys(`${r.room}：實際看得到`, r.seen_hosts)">看得到 {{ r.seen }}</button>
                <button class="seen-off" @click="showKeys(`${r.room}：只有登記`, r.registered_only_hosts)">只有登記 {{ r.registered_only }}</button>
              </div>
              <div class="envline">
                <span>正式 {{ r.env['正式'] }}</span><span>備援 {{ r.env['備援'] }}</span><span>測試 {{ r.env['測試'] }}</span>
                <span v-if="r.env['其他']">其他 {{ r.env['其他'] }}</span>
              </div>
              <div v-for="(g, j) in r.groups" :key="g.id" class="grp" :class="[g.kind, { hit: groupHit(g) }]">
                <button class="grp-row" @click="toggleGroup(`${r.room}|${g.id}`)">
                  <span class="no">M{{ i + 1 }}-{{ j + 1 }}</span>
                  <span class="gk">{{ GROUP_KIND[g.kind] }}</span>
                  <span class="gl">{{ g.label }}</span>
                  <span v-if="g.esxi.length" class="ge">ESXi {{ g.esxi.length }}</span>
                  <b class="gc">{{ g.host_count }} 台</b>
                  <span class="caret">{{ isOpen(r, g) ? '▾' : '▸' }}</span>
                </button>
                <button v-if="g.kind === 'cluster' && unregCount(g.id)" class="unreg-row" @click="showUnreg(g.id)">
                  同叢集未登記系統的 VM <b>{{ unregCount(g.id) }}</b> 台
                </button>
                <div v-if="isOpen(r, g)" class="grp-body">
                  <div v-if="g.esxi.length" class="esxi-chips">
                    <button v-for="e in g.esxi" :key="e" class="chip" @click="showKeys(`ESXi：${esxiLabel(e)}`, esxiHosts(e))">{{ esxiLabel(e) }}</button>
                  </div>
                  <ol class="hostlist">
                    <li v-for="h in groupHosts(g)" :key="h.key">
                      <NuxtLink :to="`/v2/machine/${encodeURIComponent(h.asset_serial)}`" class="lnk">{{ h.hostname }}</NuxtLink>
                      <span class="mono muted">{{ h.ip || '' }}</span>
                      <span class="muted">{{ h.environment || '' }}</span>
                      <span class="xb" :class="{ off: !h.seen }" :title="h.exist_label">{{ existShort(h.exist_level) }} {{ h.exist_score ?? '' }}</span>
                    </li>
                  </ol>
                  <button class="linkbtn" @click="showKeys(`${r.room}／${g.label}`, g.hosts)">在主機清單看（可排序）</button>
                </div>
              </div>
            </div>
          </div>
          <section class="glist no-print" data-test="graph-list">
            <h4 class="sub">部署圖清單 {{ graphRows.length }} 組<InfoNote>一列＝圖上一個分組，「圖上編號」對得到圖上的編號。台數點下去切到「主機清單」只看那幾台。有找主機時只列有命中的分組。列印時不印這張清單。</InfoNote></h4>
            <V2Table :rows="graphRows" :cols="GRAPH_COLS" prefix="L" row-key="key" unit="組" :export-name="`系統部署圖_${data.api_id}`" empty="沒有分組">
              <template #cell-host_count="{ row }"><button class="linkbtn mono" @click="showKeys(`${row.room}／${row.label}`, row.hosts)">{{ row.host_count }}</button></template>
              <template #cell-unreg="{ row }"><button v-if="row.unreg" class="linkbtn mono" @click="showUnreg(row.gid)">{{ row.unreg }}</button><template v-else>—</template></template>
            </V2Table>
          </section>
        </div>

        <!-- 機櫃：機房 → 機櫃 → U 位；VM 沿用所在 ESXi 主機的機櫃 -->
        <div v-if="tab === 'racks'">
          <template v-if="selRack">
            <button class="tb" @click="selRack = null">← 回機櫃清單</button>
            <RackElevation :room="selRack.room" :rack="selRack.rack" :api-id="data.api_id" class="mt" />
          </template>
          <template v-else>
            <div class="chips">
              <button class="chip" @click="showKeys('有機櫃資料', rackedKeys)">有機櫃資料 {{ data.racks.racked_count }}</button>
              <button class="chip" @click="showKeys('無機櫃資料', data.racks.no_rack.map(x => x.key))">無機櫃資料 {{ data.racks.no_rack_count }}</button>
              <a v-if="data.racks.rooms.length" class="chip plink" :href="`/v2/print/rack-sheet?system=${encodeURIComponent(data.api_id)}`" target="_blank">列印這些機櫃的盤點表</a>
              <a class="chip plink" :href="`/v2/print/labels?system=${encodeURIComponent(data.api_id)}`" target="_blank">印這個系統的標籤</a>
              <InfoNote>實體機用自己登記的機櫃；VM 沿用它所在 ESXi 主機（ESXi 的資產登記）的機櫃，標「經 ESXi xxx」。機櫃欄寫法認不出來（例如跨兩櫃「M03-M04」、大樓樓層）就不猜，歸「無機櫃資料」，在主機清單看得到原值。樓層資料沒有，系統設定裡設了機櫃代碼對樓層才會顯示。</InfoNote>
            </div>
            <div v-for="(rm, i) in data.racks.rooms" :key="rm.room" class="room rackroom">
              <div class="room-head static"><span class="no">K{{ i + 1 }}</span><b class="rn">{{ rm.room }}</b><span class="rc"><b>{{ rm.host_count }}</b> 台</span></div>
              <div v-for="(rk, j) in rm.racks" :key="rk.rack" class="grp cluster">
                <div class="grp-row static">
                  <span class="no">K{{ i + 1 }}-{{ j + 1 }}</span>
                  <span class="gk">機櫃</span>
                  <span class="gl">{{ rk.rack }}<span v-if="rk.floor" class="muted">　{{ rk.floor }}</span></span>
                  <button class="linkbtn" @click="showKeys(`${rm.room} ${rk.rack}`, rk.rows.map(x => x.key))"><b class="gc">{{ rk.host_count }} 台</b></button>
                  <span class="muted small">整櫃額定用電估算 {{ rk.power.w }} W・{{ rk.power.kg }} kg・空 {{ rk.power.unplaced_count ? `≤${rk.power.free_u}U（另 ${rk.power.unplaced_count} 台未定位）` : `${rk.power.free_u}U` }}<span v-if="rk.power.unknown_count">・未知規格 {{ rk.power.unknown_count }}</span><span v-if="rk.power.off_count + rk.power.pending_count">・關機／待確認 {{ rk.power.off_count + rk.power.pending_count }}</span></span>
                  <button class="tb sm" @click="selRack = { room: rm.room, rack: rk.rack }">看機櫃正面</button>
                </div>
                <ol class="hostlist rackrows">
                  <li v-for="row in rk.rows" :key="row.key">
                    <span class="mono">{{ row.u_start ? (row.u_start === row.u_end ? `${row.u_start}U` : `${row.u_start}-${row.u_end}U`) : '—' }}</span>
                    <NuxtLink v-if="hostByKey.get(row.key)" :to="`/v2/machine/${encodeURIComponent(hostByKey.get(row.key)!.asset_serial)}`" class="lnk">{{ hostByKey.get(row.key)!.hostname }}</NuxtLink>
                    <span v-if="row.via_esxi" class="muted">經 ESXi {{ row.via_esxi }}</span>
                  </li>
                </ol>
              </div>
            </div>
            <p v-if="!data.racks.rooms.length" class="muted">這個系統的主機都沒有可判讀的機櫃資料。</p>
          </template>
        </div>

        <!-- 機房停機影響 -->
        <div v-if="tab === 'outage'">
          <div class="subhead">
            某個機房整間停掉時
            <InfoNote>
              判讀規則：這裡的正式＝0 → 影響小；別處正式＋別處備援＝0 → 整套停擺；別處備援 &lt; 這裡的正式 → 備援不足（別處備援／這裡正式）；其餘 → 備援足夠。
              數字來自資產登記的「環境」欄（測試含 UAT／DEV），無法得知哪台備援對應哪台正式，所以「備援足夠」只代表台數夠，不代表真的切得過去。
            </InfoNote>
          </div>
          <V2Table :rows="outageNumbered" :cols="OUT_COLS" prefix="O" row-key="room" unit="個機房" :export-name="`系統部署圖_停機影響_${data.api_id}`" empty="沒有現役主機">
            <template v-for="o in OUT_SLOTS" :key="o.k" #[o.slot]="{ row }">
              <button class="linkbtn mono" @click="showKeys(`${row.room} 停機：${OUT_LABEL[o.k]}`, row.keys[o.k])">{{ row[o.k] }}</button>
            </template>
            <template #cell-verdict="{ row }"><span class="verdict" :class="row.verdict_kind">{{ row.verdict }}</span></template>
          </V2Table>
        </div>

        <div v-if="tab === 'hosts'">
          <div class="chips">
            <button class="chip" :class="{ on: hostFilter.label === '全部' }" @click="showPreset('all')">全部</button>
            <button class="chip" :class="{ on: hostFilter.label === '掛在 ESXi' }" @click="showPreset('on')">掛在 ESXi</button>
            <button class="chip" :class="{ on: hostFilter.label === '未對應 ESXi' }" @click="showPreset('off')">未對應 ESXi</button>
            <button class="chip" :class="{ on: !!hostFilter.retired }" @click="showPreset('retired')">退役（備查）</button>
            <span v-if="!['全部', '掛在 ESXi', '未對應 ESXi', '退役（備查）'].includes(hostFilter.label)" class="chip on">{{ hostFilter.label }}</span>
            <span class="muted">{{ hostFilter.retired ? retiredNumbered.length : hostFilter.unreg ? unregNumbered.length : hostsFiltered.length }} 筆</span>
          </div>
          <div v-if="!hostFilter.retired && !hostFilter.unreg" class="chips">
            <input v-model="mapSearch" class="kw sm" type="text" placeholder="找主機：名稱或 IP">
            <button class="chip" :class="{ on: osFilter === ALL }" @click="osFilter = ALL">全部 OS {{ hostsBeforeOs.length }}</button>
            <button v-for="f in OS_FAMILIES" :key="f" class="chip" :class="{ on: osFilter === f }" @click="osFilter = f">{{ f }} {{ osCounts[f] }}</button>
          </div>
          <V2Table v-if="hostFilter.unreg" :rows="unregNumbered" :cols="UNREG_COLS" prefix="U" row-key="key" unit="台" export-name="系統部署圖_同叢集未登記VM" empty="沒有">
            <template #cell-exist_sort="{ row }"><span class="xb" :class="{ off: !row.seen }" :title="row.exist_label">{{ existShort(row.exist_level) }} {{ row.exist_score ?? '' }}</span></template>
          </V2Table>
          <V2Table v-else-if="!hostFilter.retired" :rows="hostsFiltered" :cols="HOST_COLS" prefix="H" row-key="key" unit="台" :export-name="`系統部署圖_主機_${hostFilter.label}`" empty="沒有符合的主機">
            <template #cell-esxi_text="{ row }">
              <template v-if="row.esxi.length"><button v-for="e in row.esxi" :key="e" class="linkbtn" @click="showKeys(`ESXi：${esxiLabel(e)}`, esxiHosts(e))">{{ esxiLabel(e) }}</button></template>
              <span v-else class="muted">未對應</span>
            </template>
            <template #cell-ds_text="{ row }">
              <button v-for="x in row.datastores" :key="x" class="linkbtn" @click="showKeys(`Datastore：${dsLabel(x)}`, data.datastores.find(d => d.node_id === x)?.hosts ?? [])">{{ dsLabel(x) }}</button>
              <span v-if="!row.datastores.length" class="muted">—</span>
            </template>
            <template #cell-exist_sort="{ row }"><span class="xb" :class="{ off: !row.seen }" :title="row.exist_label">{{ row.exist_t }}</span></template>
          </V2Table>
          <V2Table v-else :rows="retiredNumbered" :cols="RETIRED_COLS" prefix="R" row-key="asset_serial" unit="筆" export-name="系統部署圖_退役" empty="沒有退役的登記" />
        </div>

        <div v-if="tab === 'infra'">
          <h4 class="sub">ESXi {{ data.esxi.length }}</h4>
          <V2Table :rows="esxiNumbered" :cols="ESXI_COLS" prefix="E" row-key="node_id" unit="台" export-name="系統部署圖_ESXi" empty="這個系統的主機都沒有對應到 ESXi">
            <template #cell-label="{ row }"><button class="linkbtn" @click="emit('impact', row.node_id)">{{ row.label }}</button></template>
            <template #cell-cluster_text="{ row }">
              <button v-for="c in row.clusters" :key="c" class="linkbtn" @click="emit('impact', c)">{{ clusterLabel(c) }}</button>
              <span v-if="!row.clusters.length" class="muted">—</span>
            </template>
            <template #cell-host_count="{ row }"><button class="linkbtn mono" @click="showKeys(`ESXi：${row.label}`, row.hosts)">{{ row.host_count }}</button></template>
          </V2Table>
          <h4 class="sub">Datastore {{ data.datastores.length }}</h4>
          <V2Table :rows="dsNumbered" :cols="DS_COLS" prefix="S" row-key="node_id" unit="個" export-name="系統部署圖_Datastore" empty="沒有 Datastore 資料">
            <template #cell-label="{ row }"><button class="linkbtn" @click="emit('impact', row.node_id)">{{ row.label }}</button></template>
            <template #cell-host_count="{ row }"><button class="linkbtn mono" @click="showKeys(`Datastore：${row.label}`, row.hosts)">{{ row.host_count }}</button></template>
          </V2Table>
        </div>

        <div v-if="tab === 'deps'">
          <p v-if="!data.deps_available" class="muted">
            尚無資料
            <InfoNote>系統之間的上下游（誰呼叫誰、誰傳檔給誰）目前沒有人工登記，也還沒有觀察到的連線（要等服務盤點收集過一次新版的連線資料）。這裡不猜。</InfoNote>
          </p>
          <template v-else>
          <div class="chips">
            <span v-if="data.deps_source">來源：{{ DEPS_SOURCE_LABEL[data.deps_source] }}</span>
            <span v-if="data.is_hub" class="tag hot">樞紐系統</span>
            <InfoNote v-if="data.observed">
              「連線推論」＝從主機上實際觀察到的 TCP 連線推出來的上下游（A 主機連到 B 主機的服務埠＝A 系統依賴 B 系統），
              {{ data.observed.settings.stale_days }} 天內沒再看到的連線不算；SSH/SFTP（22 埠）的系統間傳檔照算、另外標出來，管理埠與跳板連線不算。
              「人工登記」＝系統介面清冊或手動登記。證據數字＝觀察次數，點下去看是哪幾台、哪個埠、什麼時候。
              樞紐系統（跟太多系統都有連線，例如監控、備份；門檻 {{ data.observed.hub_threshold }} 個系統）：影響範圍照樣列它，但不經過它再往外擴散。
              <template v-if="data.observed.hubs.length">目前的樞紐：{{ data.observed.hubs.map(h => h.api_id).join('、') }}。</template>
            </InfoNote>
          </div>
          <V2Table :rows="depsNumbered" :cols="DEP_COLS" prefix="D" row-key="key" unit="個" export-name="系統部署圖_上下游" empty="無上下游">
            <template #cell-name="{ row }"><button class="linkbtn" @click="recenter(row)">{{ row.name }}</button></template>
            <template #cell-src_text="{ row }"><span class="srcb" :class="row.source === 'observed' ? 'obs' : 'reg'">{{ row.src_text }}</span><span v-if="row.hub" class="tag hot">樞紐</span></template>
            <template #cell-obs="{ row }">
              <button v-if="row.evidence" class="linkbtn mono" :disabled="edgeLoading" @click="openEdge(row)">{{ row.evidence.observations }}</button>
              <span v-if="row.evidence" class="muted small">（{{ row.evidence.count }} 組連線、埠 {{ row.evidence.ports.join(', ') }}）</span>
              <span v-else class="muted">—</span>
            </template>
          </V2Table>
          <div v-if="edge" class="edgebox">
            <div class="chips"><b>連線證據 {{ edge.label }}</b><span class="muted">{{ edge.rows.length }} 組（{{ edge.stale_days }} 天內）</span><button class="chip" @click="edge = null">關閉</button></div>
            <V2Table :rows="edgeRows" :cols="EDGE_COLS" prefix="G" row-key="id" unit="組" export-name="系統部署圖_連線證據" empty="沒有（可能已超過天數門檻）" />
          </div>
          </template>
        </div>
      </div>

      <aside class="panel">
        <div class="pname">{{ data.name }}</div>
        <div class="pmeta mono">{{ data.api_id }}</div>
        <div class="tags">
          <span class="tag" :class="{ hot: data.severity === '重大' }">{{ data.severity }}</span>
          <span v-if="data.sys_class" class="tag">{{ data.sys_class }}</span>
          <span v-if="data.is_hub" class="tag hot">樞紐系統</span>
        </div>
        <dl class="kv">
          <dt>使用單位</dt><dd>{{ data.usage_unit || '—' }}</dd>
          <dt>AP 部門</dt><dd>{{ data.ap_department || '—' }}</dd>
          <dt>AP 負責人</dt><dd>{{ data.ap_owner || '—' }}</dd>
          <dt>保管者</dt><dd>{{ data.custodian || '—' }}</dd>
        </dl>
        <div class="kpis">
          <button class="kpi" @click="showPreset('all')"><b>{{ data.summary.hosts }}</b><span>主機（台）</span></button>
          <button class="kpi" @click="showPreset('on')"><b>{{ data.summary.on_esxi }}</b><span>掛在 ESXi</span></button>
          <button class="kpi" @click="showPreset('off')"><b>{{ data.summary.no_esxi }}</b><span>未對應 ESXi</span></button>
          <button class="kpi" @click="tab = 'graph'"><b>{{ data.rooms.length }}</b><span>機房</span></button>
          <button class="kpi" @click="tab = 'infra'"><b>{{ data.summary.esxi }}</b><span>ESXi</span></button>
          <button class="kpi" @click="tab = 'infra'"><b>{{ data.summary.clusters }}</b><span>Cluster</span></button>
          <button class="kpi" @click="tab = 'infra'"><b>{{ data.summary.datastores }}</b><span>Datastore</span></button>
          <button class="kpi" @click="tab = 'deps'"><b>{{ data.deps_available ? data.summary.depended_by + data.summary.depends_on : '—' }}</b><span>上下游系統</span></button>
          <button class="kpi" @click="showPreset('retired')"><b>{{ data.summary.retired_records }}</b><span>退役（筆）</span></button>
        </div>
        <ContactCard class="pcard" :api-id="data.api_id" compact />
      </aside>
    </div>
  </div>
</template>

<style scoped>
@media print { .no-print { display: none !important; } }
.glist { margin-top: 14px; }
.smap { font-family: 'Microsoft JhengHei', sans-serif; }
.picker { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.pick-box { position: relative; }
.classbar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.pick-list li.hosthit-head { cursor: default; font-size: 12px; border-top: 1px dashed var(--line); }
.pick-list li.hosthit { padding-left: 28px; }
.searchbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.kw.sm { min-width: 220px; padding: 6px 9px; font-size: 12.5px; }
.found { font-size: 12.5px; color: var(--ink-aux); }
.found b { font-family: var(--disp); color: var(--brand-dark); }
.lnkchip { text-decoration: none; color: var(--link); }
.room.hit { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand); }
.room.dim { opacity: .4; }
.grp.hit { border-color: var(--brand); background: var(--mint); }
.kw { font-family: inherit; font-size: 13px; padding: 8px 10px; min-width: 360px; border: 1px solid var(--border-strong);
  border-radius: 9px; background: var(--card); color: var(--ink); }
.pick-list { position: absolute; z-index: 30; top: 100%; left: 0; right: 0; min-width: 460px; max-height: 360px; overflow: auto;
  margin: 4px 0 0; padding: 4px 0; list-style: none; background: var(--card); border: 1px solid var(--border-strong);
  border-radius: 10px; box-shadow: var(--shadow); }
.pick-list li { display: flex; gap: 10px; padding: 6px 12px; font-size: 13px; cursor: pointer; align-items: baseline; }
.pick-list li:hover { background: var(--mint); }
.pick-list li.muted { cursor: default; }
.pick-list .pn { color: var(--ink); flex: 1; }
.pick-list .pu { color: var(--muted); font-size: 12px; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); }
.err { color: var(--bad); }
.empty { padding: 30px 4px; }

.layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 16px; align-items: start; }
@media (max-width: 960px) { .layout { grid-template-columns: 1fr; } }
.main { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px 16px; min-width: 0; }
.tabs { display: flex; align-items: center; gap: 4px; margin: 0 0 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab { padding: 8px 16px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; border: 0;
  border-bottom: 2px solid transparent; margin-bottom: -1px; font-family: inherit; }
.tab:hover { color: var(--ink); }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }

/* 部署圖：巢狀框 */
.topline { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.sysbox { font-family: inherit; font-size: 14px; color: var(--ink); background: var(--mint); border: 2px solid var(--brand);
  border-radius: 12px; padding: 10px 16px; cursor: pointer; }
.sysbox b { font-family: var(--disp); font-size: 16px; }
.depbox { font-family: inherit; font-size: 13px; color: var(--ink-aux); background: var(--card); border: 1.5px dashed var(--chart-3);
  border-radius: 12px; padding: 10px 16px; cursor: pointer; }
.rooms { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; align-items: start; }
.room { border: 1px solid var(--border-strong); border-radius: 12px; background: var(--sub); padding: 10px 10px 8px; }
.room.slim { padding-bottom: 6px; }
.room-head { display: flex; align-items: baseline; gap: 8px; width: 100%; background: none; border: 0; padding: 0; cursor: pointer;
  font-family: inherit; text-align: left; color: var(--ink); }
.room-head:hover .rn { color: var(--brand-dark); text-decoration: underline; }
.rn { font-size: 15px; }
.rc { margin-left: auto; font-size: 13px; color: var(--ink-aux); }
.rc b { font-family: var(--disp); font-size: 17px; color: var(--ink); }
.no { font-family: var(--disp); font-size: 11px; color: var(--muted); min-width: 30px; }
.envline { display: flex; gap: 12px; font-size: 12px; color: var(--ink-aux); margin: 4px 0 8px 38px; flex-wrap: wrap; }
.grp { background: var(--card); border: 1px solid var(--border); border-left: 3px solid var(--border-strong); border-radius: 8px; margin-top: 6px; }
.grp.cluster { border-left-color: var(--chart-4); }
.grp.nocluster { border-left-color: var(--chart-2); }
.grp.physical { border-left-color: var(--chart-gray); }
.grp-row { display: flex; align-items: center; gap: 8px; width: 100%; background: none; border: 0; padding: 7px 10px;
  font-family: inherit; font-size: 13px; cursor: pointer; text-align: left; color: var(--ink-soft); }
.grp-row:hover { background: var(--mint); }
.gk { font-size: 10.5px; padding: 1px 7px; border-radius: 10px; background: var(--sub); color: var(--ink-aux); border: 1px solid var(--border); }
.gl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ge { font-size: 11.5px; color: var(--muted); }
.gc { font-family: var(--disp); font-size: 13px; color: var(--ink); white-space: nowrap; }
.caret { color: var(--muted); font-size: 11px; }
.grp-body { padding: 4px 10px 10px 48px; border-top: 1px dashed var(--line); }
.esxi-chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.hostlist { margin: 4px 0 8px; padding-left: 22px; max-height: 260px; overflow: auto; font-size: 12.5px; }
.hostlist li { padding: 2px 0; }
.hostlist li span { margin-left: 8px; font-size: 11.5px; }

.seenbox { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.seenline { display: flex; gap: 6px; margin: 6px 0 0 38px; flex-wrap: wrap; }
.seen-on, .seen-off { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 14px; cursor: pointer; border: 1px solid var(--border-strong); }
.seen-on { background: var(--good-soft); color: var(--brand-dark); border-color: var(--brand); }
.seen-off { background: var(--warn-soft); color: var(--warn-text); border-color: var(--warn); }
.seen-on b, .seen-off b { font-family: var(--disp); }
.unreg-row { display: block; width: 100%; text-align: left; font-family: inherit; font-size: 12px; color: var(--ink-aux);
  background: var(--sub); border: 0; border-top: 1px dashed var(--line); padding: 5px 10px 5px 48px; cursor: pointer; border-radius: 0 0 8px 8px; }
.unreg-row:hover { color: var(--brand-dark); text-decoration: underline; }
.legendline { font-size: 12px; color: var(--muted); margin: 0 0 8px; }
.xb { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 10px; background: var(--good-soft); color: var(--brand-dark); white-space: nowrap; }
.xb.off { background: var(--warn-soft); color: var(--warn-text); }
.plink { text-decoration: none; color: var(--link); }
.small { font-size: 11.5px; }
.static { cursor: default; }
.static:hover { background: none; }
.rackroom { margin-bottom: 10px; }
.rackrows { max-height: 220px; columns: 2; column-gap: 24px; padding-left: 26px; }
.rackrows li span { margin-right: 6px; }
.tb { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 12px; border-radius: 9px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); cursor: pointer; }
.tb:hover { border-color: var(--brand); color: var(--brand-dark); }
.tb.sm { padding: 2px 9px; font-size: 11.5px; }
.mt { margin-top: 10px; }
.tab-sub { font-size: 12px; color: var(--ink-aux); font-weight: 400; }
.srcb { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border-strong); white-space: nowrap; }
.srcb.reg { background: var(--sub); color: var(--ink-aux); }
.srcb.obs { background: var(--mint); color: var(--brand-dark); border-color: var(--brand); }
.edgebox { margin-top: 12px; padding: 10px; border: 1px solid var(--border); border-radius: 10px; background: var(--sub); }
.subhead { font-size: 13px; color: var(--ink-aux); margin: 2px 0 10px; }
.verdict { font-size: 12px; padding: 2px 9px; border-radius: 10px; font-weight: 600; white-space: nowrap; }
.verdict.low { background: var(--sub); color: var(--ink-aux); }
.verdict.ok { background: var(--good-soft); color: var(--brand-dark); }
.verdict.short { background: var(--warn-soft); color: var(--warn-text); }
.verdict.down { background: var(--bad-soft); color: var(--bad); }

.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.chip { font-family: inherit; font-size: 12px; padding: 4px 12px; border-radius: 20px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 14px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 7px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 7px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.linkbtn { background: none; border: 0; padding: 0 8px 0 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; text-align: left; }
.linkbtn:hover { text-decoration: underline; }
.sub { font-size: 13px; color: var(--ink-aux); margin: 6px 0 8px; }

.pcard { margin-top: 12px; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; }
.pname { font-size: 16px; font-weight: 700; color: var(--ink); }
.pmeta { font-size: 12px; color: var(--muted); margin-top: 2px; }
.tags { display: flex; gap: 6px; margin: 8px 0; flex-wrap: wrap; }
.tag { font-size: 11px; padding: 2px 9px; border-radius: 20px; background: var(--mint); color: var(--brand-dark); font-weight: 600; }
.tag.hot { background: var(--bad-soft); color: var(--bad); }
.kv { display: grid; grid-template-columns: 76px 1fr; gap: 4px 8px; font-size: 12.5px; margin: 6px 0 12px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; color: var(--ink-soft); }
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.kpi { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px; border: 1px solid var(--border);
  border-radius: 10px; background: var(--sub); cursor: pointer; font-family: inherit; text-align: left; }
.kpi:hover { border-color: var(--brand); }
.kpi b { font-family: var(--disp); font-size: 18px; color: var(--ink); }
.kpi span { font-size: 11px; color: var(--muted); }
</style>
