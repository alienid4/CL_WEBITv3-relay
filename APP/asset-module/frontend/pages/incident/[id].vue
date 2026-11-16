<script setup lang="ts">
// 事件應變：一個事件一頁，分頁籤（不做一長頁）。順序＝事故當下的動作順序：
// 先處理這些 → 貼告警找關聯 → 範圍與受影響機器 → 連線測試 → 檢查清單 → 聯絡 → 回報文字 → 時間軸／結案。
// 排序一律：核心系統先、單電源次之、不通再次之。結案後唯讀。
interface IpSeg { ip: string; cidr: string | null; purpose: string | null; vlan: string | null; env: string | null }
interface Mach {
  key: string; asset_serial: string; name: string; ip: string | null; ips: IpSeg[]; model: string | null
  machine_type: string | null; machine_type_label: string | null; room: string | null; floor: string | null
  row: string | null; rack: string | null; u: string | null; esxi: string | null; is_vm: boolean
  api_ids: string[]; api_inferred: string | null; sys_level: string | null; psu: string; psu_label: string
  check: string | null; check_label: string; why?: string[]
  ap: string[]; sp: string[]; people_inferred: string | null; people_garbled: string[]
  host_key: string | null; host_name: string | null; host_serial: string | null; host_u: string | null
}
interface TreeHost { key: string; name: string; serial: string; room: string | null; rack: string | null; row: string | null; count: number; alert_keys: string[]; is_esxi: boolean }
interface TreeGrp { room: string; rack?: string; row?: string; host_count: number; count: number; alert_keys: string[] }
interface Tree { verdicts: { kind: string; text: string; keys: string[] }[]; hosts: TreeHost[]; racks: TreeGrp[]; rows: TreeGrp[]; rooms: TreeGrp[]; unplaced: string[]; alerts: number }
interface Sys { api_id: string; name: string; level: string; level_label: string; machines: string[]; machine_names: string[]; oncall_phone: string | null; contacts: { name: string; role: string; department: string | null; phone: string | null }[] }
interface Task { id: number; level: string; ref: string; name: string; team: string | null; status: string; note: string | null; updated_by: string | null; updated_at: string | null }
interface Run { id: number; started_at: string; total: number; ok: number; down: number; unknown: number; created_by: string | null; items?: { key: string; name: string; ip: string | null; result: string; result_label: string; verdict: string | null; checked_at: string | null }[] }
interface View {
  event: { id: number; type: string; title: string; started_at: string; note: string | null; status: string; summary: string | null; created_by: string | null; closed_at: string | null; next_report_at: string | null; scope: Record<string, any[]> }
  expand: { machines: Mach[]; systems: Sys[]; counts: Record<string, number>; scope_text: string }
  last_run: Run | null; tasks: Task[]; first: Mach[]; teams: string[]; statuses: string[]
  vm_export: { exported_at: string | null; days: number | null; stale: boolean | null } | null
}
interface Common { dim: string; dim_label: string; count: number; total: number; text: string; keys: string[]; scope: Record<string, any[]> }
interface Token { token: string; kind: string; status: string; key?: string; name?: string; candidates?: { key: string; name: string }[] }

const route = useRoute()
const eid = Number(route.params.id)
const { apiFetch } = useApi()
const { showToast } = useToast()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const err = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'

const v = ref<View | null>(null)
const loadErr = ref('')
async function load() {
  try { v.value = await apiFetch<View>(`/api/incidents/${eid}`); loadErr.value = '' } catch (e: any) { loadErr.value = err(e) }
}
await load()
const closed = computed(() => v.value?.event.status !== 'open')

type Tab = 'first' | 'alerts' | 'scope' | 'racks' | 'check' | 'tasks' | 'contacts' | 'report' | 'timeline'
const tab = ref<Tab>(v.value && !Object.values(v.value.event.scope || {}).some((x: any) => x?.length) ? 'alerts' : 'first')

// ---------- 下鑽：任何數字點下去 → 受影響機器頁籤只列那幾台
const mFilter = ref<{ label: string; keys: Set<string> } | null>(null)
function drill(label: string, keys: string[]) { mFilter.value = { label, keys: new Set(keys) }; tab.value = 'scope' }
const M = computed(() => v.value?.expand.machines ?? [])
const keysOf = (pred: (m: Mach) => boolean) => M.value.filter(pred).map(m => m.key)
const tiles = computed(() => {
  const c = v.value?.expand.counts ?? {}
  return [
    { label: '受影響機器', n: c.machines ?? 0, keys: keysOf(() => true) },
    { label: '實體', n: c.physical ?? 0, keys: keysOf(m => !m.is_vm) },
    { label: 'VM', n: c.vms ?? 0, keys: keysOf(m => m.is_vm) },
    { label: '核心系統的機器', n: keysOf(m => m.sys_level === 'core').length, keys: keysOf(m => m.sys_level === 'core'), warn: true },
    { label: '單電源', n: c.single_psu ?? 0, keys: keysOf(m => m.psu === 'single'), warn: true },
    { label: '電源不明', n: c.psu_unknown ?? 0, keys: keysOf(m => m.psu === 'unknown') },
    { label: '不通（最近一輪）', n: keysOf(m => m.check === 'no_response').length, keys: keysOf(m => m.check === 'no_response'), warn: true },
    { label: '未測', n: keysOf(m => !m.check).length, keys: keysOf(m => !m.check) },
  ]
})

// ---------- 受影響機器表
const mRows = computed(() => M.value.filter(m => !mFilter.value || mFilter.value.keys.has(m.key)).map(m => ({
  ...m, no: M.value.indexOf(m) + 1, lv: m.sys_level === 'core' ? 0 : m.sys_level === 'noncore' ? 1 : 2,
  sys_text: m.api_ids.join(', '), loc: [m.room, m.rack, m.u].filter(Boolean).join(' '),
})))
const { sortKey: mk, sortDir: md, toggle: mt, sorted: ms } = useSort(mRows, 'no')

// ---------- 貼告警
const alertText = ref('')
const analyzing = ref(false)
const ar = ref<{ tokens: Token[]; machines: Mach[]; common: Common[]; tree: Tree } | null>(null)
// 推論樹的下鑽：告警對到的機器（可能還不在範圍裡）→ 列在貼告警頁自己的小表
const treePick = ref<{ label: string; keys: Set<string> } | null>(null)
const treeRows = computed(() => (ar.value?.machines ?? []).filter(m => treePick.value?.keys.has(m.key)))
const VERDICT_CLASS: Record<string, string> = { same_rack: 'v-bad', same_row: 'v-bad', one_feed: 'v-bad', check_feed: 'v-warn', single_host: 'v-warn', spread: '', unplaced: 'v-warn' }
async function analyze() {
  if (!alertText.value.trim()) { showToast('先貼告警文字、IP 或主機名', 'warn'); return }
  analyzing.value = true
  try { ar.value = await apiFetch(`/api/incidents/${eid}/alerts`, { method: 'POST', body: { text: alertText.value } }) } catch (e: any) {
    showToast(`分析失敗：${err(e)}`, 'error')
  } finally { analyzing.value = false }
}
const tokRows = computed(() => (ar.value?.tokens ?? []).map((t, i) => ({ ...t, no: i + 1, st: t.status === 'ok' ? '對到' : t.status === 'ambiguous' ? '多台' : '對不到' })))
const { sortKey: tk, sortDir: td, toggle: tt, sorted: ts } = useSort(tokRows, 'no')
async function addScope(scope: Record<string, any[]>, label: string) {
  try {
    await apiFetch(`/api/incidents/${eid}/scope`, { method: 'PUT', body: { scope } })
    showToast(`已加入範圍：${label}`, 'success')
    await load()
    mFilter.value = null
    tab.value = 'first'
  } catch (e: any) { showToast(`加入範圍失敗：${err(e)}`, 'error') }
}
function addAlertMachines() {
  const keys = (ar.value?.machines ?? []).map(m => m.key)
  if (keys.length) addScope({ machines: keys }, `告警對到的 ${keys.length} 台`)
}

// ---------- 手動範圍
const sf = reactive({ room: '', floor: '', row: '', racks: '', esxi: '' })
function addManual() {
  const s: Record<string, any[]> = {}
  const room = sf.room.trim()
  if (sf.racks.trim()) {
    if (!room) { showToast('櫃要配機房（例：板橋機房）', 'warn'); return }
    s.racks = sf.racks.split(/[\s,，、]+/).filter(Boolean).map(r => ({ room, rack: r.toUpperCase() }))
  }
  if (sf.row.trim()) { if (!room) { showToast('排要配機房', 'warn'); return } s.rows = [{ room, row: sf.row.trim().toUpperCase() }] }
  if (sf.floor.trim()) { if (!room) { showToast('樓層要配機房', 'warn'); return } s.floors = [{ room, floor: sf.floor.trim() }] }
  if (sf.esxi.trim()) s.esxi = sf.esxi.split(/[\s,，、]+/).filter(Boolean)
  if (!s.racks && !s.rows && !s.floors && !s.esxi && room) s.rooms = [room]
  if (!Object.keys(s).length) { showToast('至少填一項', 'warn'); return }
  addScope(s, '手動範圍')
}
async function clearScope() {
  if (!window.confirm('清空範圍？（檢查清單已有的列會保留）')) return
  try { await apiFetch(`/api/incidents/${eid}/scope`, { method: 'PUT', body: { scope: {}, replace: true } }); await load() } catch (e: any) {
    showToast(`清空失敗：${err(e)}`, 'error')
  }
}

// ---------- 連線測試
const checking = ref(false)
const runs = ref<Run[]>([])
const shownRun = ref<Run | null>(null)
async function loadRuns() { try { runs.value = await apiFetch<Run[]>(`/api/incidents/${eid}/runs`) } catch { runs.value = [] /* 歷史載不到不擋主畫面，下面表格會顯示「還沒測過」 */ } }
onMounted(loadRuns)
async function runCheck() {
  checking.value = true
  try {
    shownRun.value = await apiFetch<Run>(`/api/incidents/${eid}/check`, { method: 'POST' })
    showToast('這一輪測完了', 'success')
    await load(); await loadRuns()
  } catch (e: any) { showToast(`測試失敗：${err(e)}`, 'error') } finally { checking.value = false }
}
async function showRun(id: number) { try { shownRun.value = await apiFetch<Run>(`/api/incidents/runs/${id}`) } catch (e: any) { showToast(err(e), 'error') } }
const run = computed(() => shownRun.value ?? v.value?.last_run ?? null)
const runFilter = ref<string | null>(null)
const rRows = computed(() => (run.value?.items ?? []).map((x, i) => ({ ...x, no: i + 1 })).filter(x => !runFilter.value || x.result === runFilter.value))
const { sortKey: rk, sortDir: rd, toggle: rt, sorted: rs } = useSort(rRows, 'no')

// ---------- 檢查清單
const taskFilter = ref<{ k: 'status' | 'team'; v: string } | null>(null)
const tRows = computed(() => (v.value?.tasks ?? []).map((t, i) => ({ ...t, no: i + 1, lv: t.level === 'system' ? '系統' : '機器' }))
  .filter(t => !taskFilter.value || (taskFilter.value.k === 'status' ? t.status === taskFilter.value.v : (t.team || '') === taskFilter.value.v)))
const { sortKey: kk, sortDir: kd, toggle: kt, sorted: ks } = useSort(tRows, 'no')
const countBy = (f: (t: Task) => string) => {
  const m: Record<string, number> = {}
  for (const t of v.value?.tasks ?? []) m[f(t)] = (m[f(t)] ?? 0) + 1
  return m
}
async function saveTask(t: Task, patch: Partial<Task>) {
  try {
    const u = await apiFetch<Task>(`/api/incidents/tasks/${t.id}`, { method: 'PUT', body: patch })
    const i = v.value!.tasks.findIndex(x => x.id === t.id)
    if (i >= 0) v.value!.tasks[i] = u
  } catch (e: any) { showToast(`存檔失敗：${err(e)}`, 'error') }
}

// ---------- 聯絡：預設依主機（只列受影響那台自己的 AP／SP），可切依系統
const contactBy = ref<'host' | 'system'>('host')
const garbledOnly = ref(false)
const hRows = computed(() => M.value.map((m, i) => ({ ...m, no: i + 1, sys_text: m.api_ids.join(', '), ap_text: m.ap.join('、'), sp_text: m.sp.join('、') }))
  .filter(m => !garbledOnly.value || m.people_garbled.length))
const { sortKey: hk, sortDir: hd, toggle: ht, sorted: hs } = useSort(hRows, 'no')
const garbledCount = computed(() => M.value.filter(m => m.people_garbled.length).length)
function sysPeople(s: Sys) {
  const mine = M.value.filter(m => s.machines.includes(m.key))
  return { ap: [...new Set(mine.flatMap(m => m.ap))], sp: [...new Set(mine.flatMap(m => m.sp))] }
}
const noPhoneOnly = ref(false)
const sRows = computed(() => (v.value?.expand.systems ?? []).map((s, i) => ({ ...s, no: i + 1, lv: s.level === 'core' ? 0 : s.level === 'noncore' ? 1 : 2, n: s.machines.length, phone: s.oncall_phone || '' }))
  .filter(s => !noPhoneOnly.value || !s.oncall_phone))
const { sortKey: sk, sortDir: sd, toggle: st, sorted: ss } = useSort(sRows, 'no')

// ---------- 機櫃圖：範圍內的設備在機房平面圖上亮（VM 亮它 ESXi 那台的櫃），正面依最近一輪測試上色
interface DcTile { rack: string; prefix: string; device_count: number }
interface DcRoom { room: string; racks: DcTile[] }
const dcRooms = ref<DcRoom[] | null>(null)
const dcErr = ref('')
watch(tab, async (t) => {
  if (t !== 'racks' || dcRooms.value) return
  try { dcRooms.value = (await apiFetch<{ rooms: DcRoom[] }>('/api/racks/datacenter')).rooms } catch (e: any) { dcErr.value = err(e) }
})
const hostItems = computed(() => {
  const by = new Map<string, { key: string; name: string; serial: string | null; room: string | null; rack: string | null; u: string | null; items: Mach[] }>()
  for (const m of M.value) {
    if (!m.host_key) continue
    const h = by.get(m.host_key) ?? { key: m.host_key, name: m.host_name || m.name, serial: m.host_serial, room: m.room, rack: m.rack, u: m.host_u, items: [] }
    h.items.push(m)
    by.set(m.host_key, h)
  }
  return [...by.values()]
})
const unplacedVm = computed(() => M.value.filter(m => !m.host_key))
const statusOf = (hostKey: string) => {
  const self = M.value.find(m => m.key === hostKey)
  const c = self?.check
  return c === 'no_response' ? 'down' : c === 'alive' ? 'up' : 'untested'
}
const statusByKey = computed(() => Object.fromEntries(hostItems.value.map(h => [h.key, statusOf(h.key)])))
const rackRooms = computed(() => [...new Set(hostItems.value.map(h => h.room).filter(Boolean))] as string[])
const rackRoom = ref('')
watch(rackRooms, (r) => { if (!rackRoom.value || !r.includes(rackRoom.value)) rackRoom.value = r[0] ?? '' }, { immediate: true })
const natCmp = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
const litRacks = computed(() => {
  const m = new Map<string, number>()
  for (const h of hostItems.value) if (h.room === rackRoom.value && h.rack) m.set(h.rack, (m.get(h.rack) ?? 0) + h.items.length)
  return m
})
const planRows = computed(() => {
  const room = dcRooms.value?.find(r => r.room === rackRoom.value)
  const g = new Map<string, DcTile[]>()
  for (const t of room?.racks ?? []) { if (!g.has(t.prefix)) g.set(t.prefix, []); g.get(t.prefix)!.push(t) }
  return [...g.entries()].sort((a, b) => natCmp(a[0], b[0])).map(([prefix, tiles]) => ({ prefix, tiles: tiles.sort((a, b) => natCmp(a.rack, b.rack)) }))
})
const fronts = ref<string[]>([])
watch([litRacks, rackRoom], () => { fronts.value = [...litRacks.value.keys()].sort(natCmp).slice(0, 8) }, { immediate: true })
function toggleFront(r: string) { fronts.value = fronts.value.includes(r) ? fronts.value.filter(x => x !== r) : [...fronts.value, r] }
const hiKeysOf = (rack: string) => hostItems.value.filter(h => h.room === rackRoom.value && h.rack === rack).map(h => h.key)
// 現場找機器清單：機房 → 櫃 → U 排好，一台實體機一列（VM 併到它的 ESXi 那列）
const findRows = computed(() => hostItems.value.map(h => ({
  ...h, sys: [...new Set(h.items.flatMap(i => i.api_ids))].join(', '),
  ap: [...new Set(h.items.flatMap(i => i.ap))].join('、'), sp: [...new Set(h.items.flatMap(i => i.sp))].join('、'),
  vms: h.items.filter(i => i.is_vm).length, st: statusOf(h.key),
})).sort((a, b) => natCmp(a.room || '~', b.room || '~') || natCmp(a.rack || '~', b.rack || '~') || (parseInt(b.u || '0') - parseInt(a.u || '0'))))
const ST_LABEL: Record<string, string> = { down: '不通', up: '通', untested: '未測' }
function printFind() {
  const esc = (x: any) => String(x ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]!))
  const rows = findRows.value.map((r, i) => `<tr><td>${i + 1}</td><td>${esc(r.room)}</td><td>${esc(r.rack)}</td><td>${esc(r.u)}</td><td>${esc(r.name)}</td><td>${esc(r.serial)}</td><td>${esc(r.sys)}</td><td>${esc(r.ap)}</td><td>${esc(r.sp)}</td><td>${r.vms || ''}</td><td>${ST_LABEL[r.st]}</td></tr>`).join('')
  const w = window.open('', '_blank')
  if (!w) { showToast('瀏覽器擋了新視窗，請允許彈出視窗', 'warn'); return }
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>現場找機器清單</title><style>body{font-family:'Microsoft JhengHei',sans-serif;font-size:12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;padding:4px 6px;text-align:left}th{background:#eee}</style></head><body><h3>現場找機器清單：${esc(v.value?.event.title)}（${new Date().toLocaleString()}）</h3><table><thead><tr><th>#</th><th>機房</th><th>櫃</th><th>U</th><th>主機</th><th>序號</th><th>系統</th><th>AP</th><th>SP</th><th>VM 數</th><th>最近測試</th></tr></thead><tbody>${rows}</tbody></table></body></html>`)
  w.document.close()
  w.focus()
  w.print()
}

// ---------- 回報
const every = ref(30)
const reportText = ref('')
async function makeReport() {
  try {
    const r = await apiFetch<{ text: string }>(`/api/incidents/${eid}/report`, { method: 'POST', body: { every_min: every.value } })
    reportText.value = r.text
    await load(); await loadTimeline()
  } catch (e: any) { showToast(`產生失敗：${err(e)}`, 'error') }
}
async function copyReport() {
  try { await navigator.clipboard.writeText(reportText.value); showToast('已複製，貼到群組即可', 'success') } catch {
    showToast('瀏覽器不讓複製，請手動選取文字複製', 'warn')
  }
}

// ---------- 時間軸／結案
interface TL { id: number; at: string; kind: string; text: string; created_by: string | null }
const tl = ref<TL[]>([])
async function loadTimeline() { try { tl.value = await apiFetch<TL[]>(`/api/incidents/${eid}/timeline`) } catch (e: any) { showToast(`時間軸載入失敗：${err(e)}`, 'error') } }
onMounted(loadTimeline)
const KIND: Record<string, string> = { open: '開事件', alerts: '貼告警', scope: '範圍', check: '測試', task: '檢查清單', report: '回報', note: '手動', close: '結案' }
const tlRows = computed(() => tl.value.map((t, i) => ({ ...t, no: i + 1, kind_label: KIND[t.kind] ?? t.kind })))
const { sortKey: lk, sortDir: ld, toggle: lt, sorted: ls } = useSort(tlRows, 'no')
const noteText = ref('')
async function addNote() {
  try { await apiFetch(`/api/incidents/${eid}/note`, { method: 'POST', body: { text: noteText.value } }); noteText.value = ''; await loadTimeline() } catch (e: any) {
    showToast(`新增失敗：${err(e)}`, 'error')
  }
}
const summary = ref('')
async function closeEv() {
  if (!summary.value.trim()) { showToast('結案要寫摘要', 'warn'); return }
  if (!window.confirm('結案後這個事件只能看不能改，確定？')) return
  try { await apiFetch(`/api/incidents/${eid}/close`, { method: 'POST', body: { summary: summary.value } }); await load(); await loadTimeline() } catch (e: any) {
    showToast(`結案失敗：${err(e)}`, 'error')
  }
}
const WHY_CLASS: Record<string, string> = { 核心系統: 'core', 不通: 'down', 單電源: 'psu' }
</script>

<template>
  <div class="incpage">
    <V2Banner :to="`/v2/incident/${$route.params.id}`" label="4.1 事件應變（此事件）" />
    <p v-if="loadErr" class="err">事件載入失敗：{{ loadErr }}</p>
    <template v-else-if="v">
      <div class="head">
        <NuxtLink to="/incident" class="lnk small">← 事件清單</NuxtLink>
        <div class="ey">TODAY · 事件應變 · {{ v.event.type }}</div>
        <h1>{{ v.event.title }}</h1>
        <span class="st" :class="{ open: !closed }">{{ closed ? '已結案（唯讀）' : '進行中' }}</span>
        <div class="meta">開始 {{ v.event.started_at.slice(0, 16) }}　開的人 {{ v.event.created_by || '—' }}
          <span v-if="v.event.next_report_at">　下次回報 <b>{{ v.event.next_report_at.slice(11, 16) }}</b></span>
          　範圍：{{ v.expand.scope_text }}</div>
      </div>
      <div v-if="v.vm_export" class="vmfresh" :class="{ stale: v.vm_export.stale !== false }">
        VM 位置資料：<template v-if="v.vm_export.exported_at">vCenter 匯出 <b class="mono">{{ v.vm_export.exported_at }}</b>（{{ v.vm_export.days }} 天前）</template><template v-else>匯出日期不明</template>
        <span v-if="v.vm_export.stale !== false">　VM 可能已被搬到別台 ESXi，「哪些 VM 受影響」可能不準——請重新匯出 RVTools 再匯入。</span>
      </div>

      <div class="tabs" role="tablist">
        <button v-for="t in ([['first', `先處理這些 ${v.first.length}`], ['alerts', '貼告警找關聯'], ['scope', `範圍與機器 ${v.expand.counts.machines}`], ['racks', '機櫃圖'], ['check', '連線測試'], ['tasks', `檢查清單 ${v.tasks.length}`], ['contacts', `聯絡 ${v.expand.counts.systems}`], ['report', '回報文字'], ['timeline', '時間軸／結案']] as [Tab, string][])"
                :key="t[0]" type="button" class="tab" :class="{ active: tab === t[0] }" @click="tab = t[0]">{{ t[1] }}</button>
      </div>

      <!-- 先處理這些 -->
      <div v-if="tab === 'first'" class="panel">
        <div class="tiles">
          <button v-for="t in tiles" :key="t.label" class="tile" :class="{ warn: t.warn && t.n }" @click="drill(t.label, t.keys)">
            <b class="mono">{{ t.n }}</b><span>{{ t.label }}</span>
          </button>
          <button class="tile" :class="{ warn: v.expand.counts.no_phone }" @click="noPhoneOnly = true; tab = 'contacts'"><b class="mono">{{ v.expand.counts.no_phone }}</b><span>系統缺值班電話</span></button>
        </div>
        <p v-if="!v.expand.counts.machines" class="muted">還沒有範圍。到「貼告警找關聯」貼上告警，或到「範圍與機器」手動加。</p>
        <template v-else>
          <div class="sec">先處理這些（核心系統、不通、單電源；核心先）</div>
          <table class="tbl">
            <thead><tr><th>項次</th><th>機器</th><th>為什麼</th><th>位置</th><th>系統</th><th>最近測試</th></tr></thead>
            <tbody>
              <tr v-for="(m, i) in v.first" :key="m.key">
                <td class="mono">F{{ i + 1 }}</td>
                <td><NuxtLink :to="`/assets/${encodeURIComponent(m.asset_serial)}`" class="lnk">{{ m.name }}</NuxtLink><div class="muted small mono">{{ m.ip || '無 IP' }}</div></td>
                <td><span v-for="w in m.why" :key="w" class="why" :class="WHY_CLASS[w]">{{ w }}</span></td>
                <td>{{ [m.room, m.rack, m.u].filter(Boolean).join(' ') || (m.esxi ? `VM（ESXi ${m.esxi}）` : '—') }}</td>
                <td>{{ m.api_ids.join(', ') || '—' }}<div v-if="m.api_inferred" class="muted small">推論：{{ m.api_inferred }}</div></td>
                <td>{{ m.check_label }}</td>
              </tr>
              <tr v-if="!v.first.length"><td colspan="6" class="muted">沒有核心系統、不通或單電源的機器</td></tr>
            </tbody>
          </table>
        </template>
      </div>

      <!-- 貼告警 -->
      <div v-if="tab === 'alerts'" class="panel">
        <p class="muted">把告警簡訊、Signaal 通知、IP 清單、主機名直接貼進來（亂貼沒關係，系統只抓 IPv4 與像主機名的字）。</p>
        <textarea v-model="alertText" class="ta" rows="7" :disabled="closed" placeholder="例：&#10;16:25 DOWN 10.99.1.56&#10;APPSVR12-97 ping fail" />
        <div class="chips"><button class="tb go" :disabled="analyzing || closed" @click="analyze">{{ analyzing ? '分析中…' : '找關聯' }}</button></div>
        <template v-if="ar">
          <div class="sec">推論（由下往上：告警 → 實體機／ESXi → 櫃 → 排 → 機房）</div>
          <div v-for="(x, i) in ar.tree.verdicts" :key="i" class="verdict" :class="VERDICT_CLASS[x.kind]">
            <span class="mono">V{{ i + 1 }}</span> <button class="linkbtn" @click="treePick = { label: x.text, keys: new Set(x.keys) }">{{ x.text }}</button>
          </div>
          <div class="tree">
            <div><b>實體機／ESXi</b>
              <button v-for="h in ar.tree.hosts" :key="h.key" class="chip" @click="treePick = { label: `${h.name} 上的告警`, keys: new Set(h.alert_keys) }">{{ h.name }}{{ h.is_esxi ? '（ESXi）' : '' }} 掉了 {{ h.count }} 台</button>
              <button v-if="ar.tree.unplaced.length" class="chip" @click="treePick = { label: '位置不明的 VM', keys: new Set(ar.tree.unplaced) }">位置不明 {{ ar.tree.unplaced.length }}</button>
            </div>
            <div><b>櫃</b> <button v-for="r in ar.tree.racks" :key="r.room + r.rack" class="chip" @click="treePick = { label: `${r.room} ${r.rack}`, keys: new Set(r.alert_keys) }">{{ r.room }} {{ r.rack }}：{{ r.host_count }} 台實體／{{ r.count }} 筆</button><span v-if="!ar.tree.racks.length" class="muted">—</span></div>
            <div><b>排</b> <button v-for="r in ar.tree.rows" :key="r.room + r.row" class="chip" @click="treePick = { label: `${r.room} ${r.row} 排`, keys: new Set(r.alert_keys) }">{{ r.room }} {{ r.row }} 排：{{ r.host_count }} 台實體／{{ r.count }} 筆</button><span v-if="!ar.tree.rows.length" class="muted">—</span></div>
            <div><b>機房</b> <button v-for="r in ar.tree.rooms" :key="r.room" class="chip" @click="treePick = { label: r.room, keys: new Set(r.alert_keys) }">{{ r.room }}：{{ r.host_count }} 台實體／{{ r.count }} 筆</button></div>
          </div>
          <div v-if="treePick" class="glass pick">
            <b>{{ treePick.label }}（{{ treeRows.length }}）</b> <button class="linkbtn" @click="treePick = null">收起</button>
            <div v-for="(m, i) in treeRows" :key="m.key" class="small">P{{ i + 1 }}　{{ m.name }}　{{ m.ip || '' }}　{{ m.api_ids.join(', ') }}　{{ m.esxi ? `ESXi ${m.esxi}` : (m.rack ? `${m.room} ${m.rack} ${m.u || ''}` : '') }}</div>
          </div>
          <div class="sec">共同點（同一個值涵蓋越多台越前面；一樣多時越具體越前面）</div>
          <div v-for="(c, i) in ar.common" :key="i" class="common">
            <span class="mono">C{{ i + 1 }}</span>
            <button class="linkbtn" @click="drill(c.text, c.keys)">{{ c.text }}</button>
            <button class="tb sm" :disabled="closed" @click="addScope(c.scope, c.text)">設為範圍</button>
          </div>
          <p v-if="!ar.common.length" class="muted">對到的機器沒有共同點（或只對到 0 台）。</p>
          <div class="chips"><button class="tb sm" :disabled="closed || !ar.machines.length" @click="addAlertMachines">只把對到的 {{ ar.machines.length }} 台加入範圍</button></div>
          <div class="sec">逐筆對應（{{ ar.tokens.length }} 個）</div>
          <table class="tbl">
            <thead><tr>
              <SortTh k="no" :active="tk" :dir="td" @sort="tt">項次</SortTh>
              <SortTh k="token" :active="tk" :dir="td" @sort="tt">貼上的</SortTh>
              <SortTh k="st" :active="tk" :dir="td" @sort="tt">結果</SortTh>
              <SortTh k="name" :active="tk" :dir="td" @sort="tt">對到</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="t in ts" :key="t.no" :class="{ bad: t.status !== 'ok' }">
                <td class="mono">A{{ t.no }}</td><td class="mono">{{ t.token }}</td><td>{{ t.st }}</td>
                <td>{{ t.name || (t.candidates ?? []).map(c => c.name).join('、') || '資產庫沒有這個 IP' }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </div>

      <!-- 範圍與機器 -->
      <div v-if="tab === 'scope'" class="panel">
        <div class="glass scopebox">
          <b>手動加範圍</b>
          <input v-model="sf.room" class="kw sm" placeholder="機房，例：板橋機房">
          <input v-model="sf.floor" class="kw xs" placeholder="樓層">
          <input v-model="sf.row" class="kw xs" placeholder="排，例：M">
          <input v-model="sf.racks" class="kw sm" placeholder="櫃，例：M10,M11">
          <input v-model="sf.esxi" class="kw sm" placeholder="ESXi 節點，例：esxi:ESXI12-34">
          <button class="tb go" :disabled="closed" @click="addManual">加入</button>
          <button class="tb" disabled title="需匯入配電表">UPS／PDU（需匯入配電表）</button>
          <button class="tb" :disabled="closed" @click="clearScope">清空範圍</button>
          <InfoNote>只填機房＝整個機房；填了排／樓層／櫃就只加那些（都要配機房）。ESXi 會把它上面的 VM 一起帶進來。實體位置用 CIA 的機房／機櫃欄；VM 位置用它所在 ESXi 的實體機。UPS／PDU 對應沒有資料，要先匯入配電表才能用。</InfoNote>
        </div>
        <div class="chips">
          <span class="muted">受影響機器 {{ M.length }} 台</span>
          <span v-if="mFilter" class="chip on">只看：{{ mFilter.label }}（{{ mFilter.keys.size }}）</span>
          <button v-if="mFilter" class="linkbtn" @click="mFilter = null">看全部</button>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="mk" :dir="md" @sort="mt">項次</SortTh>
            <SortTh k="name" :active="mk" :dir="md" @sort="mt">機器</SortTh>
            <SortTh k="ip" :active="mk" :dir="md" @sort="mt">IP</SortTh>
            <SortTh k="lv" :active="mk" :dir="md" @sort="mt">系統等級</SortTh>
            <SortTh k="sys_text" :active="mk" :dir="md" @sort="mt">系統</SortTh>
            <SortTh k="loc" :active="mk" :dir="md" @sort="mt">位置</SortTh>
            <SortTh k="esxi" :active="mk" :dir="md" @sort="mt">ESXi</SortTh>
            <SortTh k="psu_label" :active="mk" :dir="md" @sort="mt">電源</SortTh>
            <SortTh k="check_label" :active="mk" :dir="md" @sort="mt">最近測試</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="m in ms" :key="m.key">
              <td class="mono">M{{ m.no }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(m.asset_serial)}`" class="lnk">{{ m.name }}</NuxtLink><span v-if="m.is_vm" class="muted small">　VM</span></td>
              <td class="mono small">{{ m.ip || '—' }}</td>
              <td>{{ m.sys_level === 'core' ? '核心' : m.sys_level === 'noncore' ? '非核心' : '未分級' }}</td>
              <td>{{ m.sys_text || '—' }}<div v-if="m.api_inferred" class="muted small">推論：{{ m.api_inferred }}</div></td>
              <td>{{ m.loc || '—' }}</td><td>{{ m.esxi || '—' }}</td>
              <td :class="{ warnc: m.psu === 'single' }">{{ m.psu_label }}</td>
              <td :class="{ warnc: m.check === 'no_response' }">{{ m.check_label }}</td>
            </tr>
            <tr v-if="!ms.length"><td colspan="9" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 機櫃圖 -->
      <div v-if="tab === 'racks'" class="panel">
        <p v-if="dcErr" class="err">機房資料載入失敗：{{ dcErr }}</p>
        <p v-else-if="!M.length" class="muted">還沒有範圍。</p>
        <template v-else>
          <div class="chips">
            <button v-for="r in rackRooms" :key="r" class="chip" :class="{ on: rackRoom === r }" @click="rackRoom = r">{{ r }} {{ hostItems.filter(h => h.room === r).length }} 台實體</button>
            <button v-if="unplacedVm.length" class="chip" @click="drill('位置不明的 VM', unplacedVm.map(m => m.key))">位置不明 {{ unplacedVm.length }}</button>
            <button class="tb sm" :disabled="!findRows.length" @click="printFind">列印現場找機器清單（{{ findRows.length }} 台）</button>
            <InfoNote>亮起的櫃＝範圍內有設備（VM 算它跑的那台 ESXi 的櫃），數字＝受影響台數。下方正面：紅＝最近一輪不通、綠＝通、灰＝還沒測（ESXi 主機本身沒測過就是灰的）。依機櫃編號排列，非實際位置。</InfoNote>
          </div>
          <p v-if="!dcRooms" class="muted">載入機房資料中…</p>
          <div v-for="row in planRows" :key="row.prefix" class="prow">
            <span class="rowname">{{ row.prefix }} 排</span>
            <button v-for="t in row.tiles" :key="t.rack" class="ptile" :class="{ hi: litRacks.has(t.rack), sel: fronts.includes(t.rack) }" @click="toggleFront(t.rack)">
              {{ t.rack }}<span v-if="litRacks.get(t.rack)" class="cnt">{{ litRacks.get(t.rack) }}</span>
            </button>
          </div>
          <div v-if="fronts.length" class="fronts">
            <div v-for="r in fronts" :key="r" class="front1">
              <RackElevation :room="rackRoom" :rack="r" :hi-keys="hiKeysOf(r)" :status-by-key="statusByKey" :u-px="26" compact />
            </div>
          </div>
          <div class="sec">現場找機器清單（機房 → 櫃 → U）</div>
          <table class="tbl">
            <thead><tr><th>項次</th><th>機房</th><th>櫃</th><th>U</th><th>主機</th><th>序號</th><th>系統</th><th>AP</th><th>SP</th><th>VM 數</th><th>最近測試</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in findRows" :key="r.key">
                <td class="mono">K{{ i + 1 }}</td><td>{{ r.room || '—' }}</td><td>{{ r.rack || '—' }}</td><td class="mono">{{ r.u || '—' }}</td>
                <td>{{ r.name }}</td><td class="mono small">{{ r.serial || 'CIA 沒登記' }}</td><td>{{ r.sys || '—' }}</td><td>{{ r.ap || '—' }}</td><td>{{ r.sp || '—' }}</td>
                <td class="mono">{{ r.vms || '' }}</td><td :class="{ warnc: r.st === 'down' }">{{ ST_LABEL[r.st] }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </div>

      <!-- 連線測試 -->
      <div v-if="tab === 'check'" class="panel">
        <div class="chips">
          <button class="tb go" :disabled="checking || closed || !M.length" @click="runCheck">{{ checking ? `測試中（${M.length} 台，最多約 1 分鐘）…` : '測一輪' }}</button>
          <InfoNote>從這台服務機對範圍內每台的 IP 打 ping（3 個封包）＋ TCP 22／445／3389／5985，只讀、不改任何資料。通＝有回應；不通＝都沒回應（關機、不在這個網段、或防火牆全擋，分不出來）；無法測＝沒有 IP 或測試本身沒跑成。從測試機（221）測公司網段一定全部不通，那是正常的，不代表機器掛了。</InfoNote>
        </div>
        <template v-if="run">
          <div class="chips">
            <span class="muted">{{ run.started_at.slice(5, 16) }} 那一輪（{{ run.created_by || '—' }}）</span>
            <button class="chip" :class="{ on: !runFilter }" @click="runFilter = null">全部 {{ run.total }}</button>
            <button class="chip" :class="{ on: runFilter === 'alive' }" @click="runFilter = 'alive'">通 {{ run.ok }}</button>
            <button class="chip" :class="{ on: runFilter === 'no_response' }" @click="runFilter = 'no_response'">不通 {{ run.down }}</button>
            <button class="chip" :class="{ on: runFilter === 'unknown' }" @click="runFilter = 'unknown'">無法測 {{ run.unknown }}</button>
          </div>
          <table class="tbl">
            <thead><tr>
              <SortTh k="no" :active="rk" :dir="rd" @sort="rt">項次</SortTh>
              <SortTh k="name" :active="rk" :dir="rd" @sort="rt">機器</SortTh>
              <SortTh k="ip" :active="rk" :dir="rd" @sort="rt">IP</SortTh>
              <SortTh k="result_label" :active="rk" :dir="rd" @sort="rt">結果</SortTh>
              <SortTh k="verdict" :active="rk" :dir="rd" @sort="rt">說明</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="x in rs" :key="x.no">
                <td class="mono">R{{ x.no }}</td><td>{{ x.name }}</td><td class="mono small">{{ x.ip || '—' }}</td>
                <td :class="{ warnc: x.result === 'no_response' }">{{ x.result_label }}</td><td class="small">{{ x.verdict }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <p v-else class="muted">還沒測過。</p>
        <div class="sec">歷次測試</div>
        <table class="tbl">
          <thead><tr><th>項次</th><th>時間</th><th>通</th><th>不通</th><th>無法測</th><th>誰</th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in runs" :key="r.id">
              <td class="mono">H{{ runs.length - i }}</td>
              <td><button class="linkbtn mono" @click="showRun(r.id)">{{ r.started_at.slice(5, 16) }}</button></td>
              <td class="mono">{{ r.ok }}</td><td class="mono">{{ r.down }}</td><td class="mono">{{ r.unknown }}</td><td>{{ r.created_by || '—' }}</td>
            </tr>
            <tr v-if="!runs.length"><td colspan="6" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 檢查清單 -->
      <div v-if="tab === 'tasks'" class="panel">
        <div class="chips">
          <button class="chip" :class="{ on: !taskFilter }" @click="taskFilter = null">全部 {{ v.tasks.length }}</button>
          <button v-for="s in v.statuses" :key="s" class="chip" :class="{ on: taskFilter?.k === 'status' && taskFilter.v === s }" @click="taskFilter = { k: 'status', v: s }">{{ s }} {{ countBy(t => t.status)[s] ?? 0 }}</button>
          <span class="muted">｜</span>
          <button v-for="g in v.teams" :key="g" class="chip" :class="{ on: taskFilter?.k === 'team' && taskFilter.v === g }" @click="taskFilter = { k: 'team', v: g }">{{ g }} {{ countBy(t => t.team || '')[g] ?? 0 }}</button>
          <InfoNote>每個受影響系統一列（預設 AP 組），沒有系統的機器另外一列（依機器類型給網管／儲存／主機）。所有人改同一份；每次改動都進時間軸（誰、何時、改了什麼）。按 F5 看別人的更新。</InfoNote>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="kk" :dir="kd" @sort="kt">項次</SortTh>
            <SortTh k="lv" :active="kk" :dir="kd" @sort="kt">對象</SortTh>
            <SortTh k="name" :active="kk" :dir="kd" @sort="kt">名稱</SortTh>
            <SortTh k="team" :active="kk" :dir="kd" @sort="kt">組別</SortTh>
            <SortTh k="status" :active="kk" :dir="kd" @sort="kt">狀態</SortTh>
            <SortTh k="note" :active="kk" :dir="kd" @sort="kt">備註</SortTh>
            <SortTh k="updated_at" :active="kk" :dir="kd" @sort="kt">誰／何時</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="t in ks" :key="t.id" :class="'s-' + t.status">
              <td class="mono">T{{ t.no }}</td><td>{{ t.lv }}</td><td>{{ t.name }}</td>
              <td><select :value="t.team ?? ''" :disabled="closed" @change="saveTask(t, { team: ($event.target as HTMLSelectElement).value })"><option v-for="g in v.teams" :key="g">{{ g }}</option></select></td>
              <td><select :value="t.status" :disabled="closed" @change="saveTask(t, { status: ($event.target as HTMLSelectElement).value })"><option v-for="s in v.statuses" :key="s">{{ s }}</option></select></td>
              <td><input :value="t.note ?? ''" class="note" :disabled="closed" @change="saveTask(t, { note: ($event.target as HTMLInputElement).value })"></td>
              <td class="small">{{ t.updated_by || '—' }} {{ (t.updated_at || '').slice(11, 16) }}</td>
            </tr>
            <tr v-if="!ks.length"><td colspan="7" class="muted">沒有（先設範圍）</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 聯絡 -->
      <div v-if="tab === 'contacts'" class="panel">
        <div class="chips">
          <button class="chip" :class="{ on: contactBy === 'host' }" @click="contactBy = 'host'">依主機 {{ M.length }}</button>
          <button class="chip" :class="{ on: contactBy === 'system' }" @click="contactBy = 'system'">依系統 {{ v.expand.systems.length }}</button>
          <span class="muted">｜</span>
          <button class="chip" :class="{ on: garbledOnly, warnchip: garbledCount }" @click="garbledOnly = !garbledOnly; contactBy = 'host'">資料異常，請 CIA 修 {{ garbledCount }}</button>
          <InfoNote>AP＝這台自己登記的使用者（user_name），SP＝這台的保管者（custodian）；只列受影響的這幾台，不是整個系統所有人。VM 自己沒登記人、但用名稱／IP 比對到 CIA 那筆時，沿用那筆並標「推論」。人名欄有冒號、加號、V4 這類符號的是 CIA 匯入錯位，標「資料異常」，不猜。</InfoNote>
        </div>
        <table v-if="contactBy === 'host'" class="tbl">
          <thead><tr>
            <SortTh k="no" :active="hk" :dir="hd" @sort="ht">項次</SortTh>
            <SortTh k="name" :active="hk" :dir="hd" @sort="ht">主機</SortTh>
            <SortTh k="ip" :active="hk" :dir="hd" @sort="ht">IP</SortTh>
            <SortTh k="sys_text" :active="hk" :dir="hd" @sort="ht">系統</SortTh>
            <SortTh k="ap_text" :active="hk" :dir="hd" @sort="ht">AP</SortTh>
            <SortTh k="sp_text" :active="hk" :dir="hd" @sort="ht">SP</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="m in hs" :key="m.key">
              <td class="mono">H{{ m.no }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(m.asset_serial)}`" class="lnk">{{ m.name }}</NuxtLink><span v-if="m.is_vm" class="muted small">　VM</span></td>
              <td class="mono small">{{ m.ip || '—' }}</td>
              <td>{{ m.sys_text || '—' }}</td>
              <td><span v-for="x in m.ap" :key="x" :class="{ warnc: m.people_garbled.includes(x) }">{{ x }}{{ m.people_garbled.includes(x) ? '（資料異常，請 CIA 修）' : '' }} </span><span v-if="!m.ap.length" class="muted">沒有登記</span></td>
              <td><span v-for="x in m.sp" :key="x" :class="{ warnc: m.people_garbled.includes(x) }">{{ x }}{{ m.people_garbled.includes(x) ? '（資料異常，請 CIA 修）' : '' }} </span><span v-if="!m.sp.length" class="muted">沒有登記</span>
                <div v-if="m.people_inferred" class="muted small">推論：{{ m.people_inferred }}</div></td>
            </tr>
            <tr v-if="!hs.length"><td colspan="6" class="muted">沒有</td></tr>
          </tbody>
        </table>
        <template v-else>
        <div class="chips">
          <button class="chip" :class="{ on: !noPhoneOnly }" @click="noPhoneOnly = false">全部系統 {{ v.expand.systems.length }}</button>
          <button class="chip" :class="{ on: noPhoneOnly }" @click="noPhoneOnly = true">缺值班電話 {{ v.expand.counts.no_phone }}</button>
          <NuxtLink to="/contacts" class="lnk small">到 9-10 聯絡資訊補值班電話</NuxtLink>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="sk" :dir="sd" @sort="st">項次</SortTh>
            <SortTh k="lv" :active="sk" :dir="sd" @sort="st">等級</SortTh>
            <SortTh k="api_id" :active="sk" :dir="sd" @sort="st">系統</SortTh>
            <SortTh k="n" :active="sk" :dir="sd" @sort="st">受影響機器</SortTh>
            <SortTh k="phone" :active="sk" :dir="sd" @sort="st">值班電話</SortTh>
            <th>這幾台的 AP／SP</th>
          </tr></thead>
          <tbody>
            <tr v-for="s in ss" :key="s.api_id">
              <td class="mono">C{{ s.no }}</td><td :class="{ warnc: s.level === 'core' }">{{ s.level_label }}</td>
              <td>{{ s.api_id }} {{ s.name }}</td>
              <td><button class="linkbtn mono" @click="drill(`${s.api_id} 的機器`, s.machines)">{{ s.n }}</button></td>
              <td><a v-if="s.oncall_phone" :href="`tel:${s.oncall_phone}`" class="lnk mono">{{ s.oncall_phone }}</a><span v-else class="warnc">沒有值班電話</span></td>
              <td class="small"><div>AP：{{ sysPeople(s).ap.join('、') || '沒有登記' }}</div><div>SP：{{ sysPeople(s).sp.join('、') || '沒有登記' }}</div></td>
            </tr>
            <tr v-if="!ss.length"><td colspan="6" class="muted">沒有</td></tr>
          </tbody>
        </table>
        </template>
      </div>

      <!-- 回報文字 -->
      <div v-if="tab === 'report'" class="panel">
        <div class="chips">
          <label>幾分鐘後再回報 <input v-model.number="every" type="number" min="5" max="240" class="num"></label>
          <button class="tb go" @click="makeReport">產生回報文字</button>
          <button v-if="reportText" class="tb" @click="copyReport">複製</button>
          <InfoNote>數字全部從這一頁算（檢查清單的狀態＋最近一輪連線測試），每產生一次都記進時間軸。短行排版，貼到手機群組不會亂。</InfoNote>
        </div>
        <textarea v-if="reportText" v-model="reportText" class="ta report" rows="18" readonly />
        <p v-else class="muted">按「產生回報文字」。</p>
      </div>

      <!-- 時間軸／結案 -->
      <div v-if="tab === 'timeline'" class="panel">
        <div v-if="!closed" class="chips">
          <input v-model="noteText" class="kw" placeholder="手動記一筆，例：17:25 某台 ESXi 掛維護">
          <button class="tb" @click="addNote">記下</button>
          <a class="lnk small" :href="`${apiBase}/api/incidents/${eid}/export.csv`">匯出（CSV，事後檢討用）</a>
        </div>
        <div v-else class="chips"><span>結案摘要：{{ v.event.summary }}</span><a class="lnk small" :href="`${apiBase}/api/incidents/${eid}/export.csv`">匯出（CSV）</a></div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="lk" :dir="ld" @sort="lt">項次</SortTh>
            <SortTh k="at" :active="lk" :dir="ld" @sort="lt">時間</SortTh>
            <SortTh k="kind_label" :active="lk" :dir="ld" @sort="lt">類型</SortTh>
            <SortTh k="text" :active="lk" :dir="ld" @sort="lt">內容</SortTh>
            <SortTh k="created_by" :active="lk" :dir="ld" @sort="lt">誰</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="t in ls" :key="t.id">
              <td class="mono">L{{ t.no }}</td><td class="mono small">{{ t.at.slice(5, 16) }}</td><td>{{ t.kind_label }}</td>
              <td class="pre">{{ t.text }}</td><td>{{ t.created_by || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!closed" class="glass closebox">
          <b>結案</b>
          <input v-model="summary" class="kw" placeholder="摘要，例：17:50 全部恢復，1 台 ESXi 掛維護待原廠">
          <button class="tb" @click="closeEv">結案（之後唯讀）</button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.incpage { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 10px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; margin-top: 4px; }
h1 { font-family: var(--disp); font-size: 22px; font-weight: 600; margin: 4px 10px 0 0; color: var(--ink); display: inline-block; }
.meta { font-size: 12.5px; color: var(--ink-soft); margin-top: 4px; }
.st { font-size: 12px; padding: 2px 9px; border-radius: 10px; background: var(--sub); vertical-align: middle; }
.st.open { background: var(--bad-soft); color: var(--bad); font-weight: 600; }
.vmfresh { margin: 0 0 10px; padding: 8px 14px; border-radius: 10px; font-size: 13px; background: var(--good-soft); color: var(--ink-soft); border: 1px solid var(--border); }
.vmfresh.stale { background: var(--bad-soft); color: var(--bad); border-color: var(--bad); }
.tabs { display: flex; align-items: center; gap: 4px; margin: 0 0 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab { padding: 8px 14px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; font-family: inherit; }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; overflow-x: auto; }
.tiles { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.tile { display: flex; flex-direction: column; gap: 2px; min-width: 110px; padding: 10px 14px; border-radius: 10px; border: 1px solid var(--border); background: var(--card); cursor: pointer; font-family: inherit; text-align: left; }
.tile b { font-size: 22px; color: var(--ink); }
.tile span { font-size: 12px; color: var(--ink-soft); }
.tile.warn { border-color: var(--bad); background: var(--bad-soft); }
.tile.warn b { color: var(--bad); }
.sec { font-size: 13px; font-weight: 700; color: var(--ink); margin: 14px 0 8px; }
.why { display: inline-block; font-size: 11.5px; padding: 1px 8px; border-radius: 9px; margin-right: 4px; background: var(--sub); }
.why.core { background: var(--bad-soft); color: var(--bad); font-weight: 600; }
.why.down { background: var(--warn-soft); color: var(--warn-text); }
.why.psu { background: var(--warn-soft); color: var(--warn-text); border: 1px dashed var(--warn); }
.ta { width: 100%; box-sizing: border-box; font-family: var(--disp); font-size: 13px; padding: 8px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.ta.report { max-width: 420px; line-height: 1.6; }
.common { display: flex; gap: 10px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--line); font-size: 13.5px; flex-wrap: wrap; }
.scopebox, .closebox { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px 12px; margin-bottom: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--sub); font-size: 13px; }
.closebox { margin-top: 14px; }
.kw { font-family: inherit; font-size: 13px; padding: 7px 10px; min-width: 300px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.kw.sm { min-width: 160px; }
.kw.xs { min-width: 70px; width: 80px; }
.num { width: 64px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.note { font-family: inherit; font-size: 12.5px; padding: 3px 6px; width: 200px; border: 1px solid var(--border-strong); border-radius: 6px; }
.tb { font-family: inherit; font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 9px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); cursor: pointer; }
.tb.sm { font-size: 12px; padding: 4px 10px; }
.tb.go { background: var(--brand); color: var(--card); border-color: var(--brand); }
.tb:disabled { opacity: .55; cursor: not-allowed; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 8px 0 10px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12.5px; padding: 4px 12px; border-radius: 20px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 7px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 7px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; }
.tbl tr.bad td { color: var(--warn-text); }
.tbl tr.s-異常 td { background: var(--bad-soft); }
.tbl tr.s-OK td { background: var(--good-soft); }
.warnc { color: var(--bad); font-weight: 600; }
.pre { white-space: pre-line; }
.mono { font-family: var(--disp); }
.small { font-size: 11.5px; }
.muted { color: var(--muted); }
.err { color: var(--bad); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.linkbtn { background: none; border: 0; padding: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; text-align: left; }
.linkbtn:hover { text-decoration: underline; }
.verdict { padding: 6px 10px; margin-bottom: 6px; border-radius: 8px; background: var(--sub); font-size: 13.5px; }
.verdict.v-bad { background: var(--bad-soft); color: var(--bad); font-weight: 600; }
.verdict.v-warn { background: var(--warn-soft); color: var(--warn-text); }
.tree > div { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 6px 0; font-size: 13px; }
.tree b { width: 90px; }
.pick { padding: 8px 12px; margin: 8px 0; border: 1px solid var(--border); border-radius: 10px; background: var(--sub); }
.chip.warnchip { border-color: var(--bad); color: var(--bad); }
.prow { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.rowname { width: 44px; font-size: 12px; color: var(--ink-aux); }
.ptile { min-width: 66px; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink-soft); font-family: var(--disp); font-size: 12px; cursor: pointer; }
.ptile.hi { background: var(--bad-soft); border-color: var(--bad); color: var(--bad); font-weight: 600; }
.ptile.sel { outline: 3px solid var(--brand-dark); outline-offset: 1px; }
.cnt { display: inline-block; margin-left: 6px; min-width: 18px; padding: 0 5px; border-radius: 9px; background: var(--bad); color: var(--card); font-size: 11px; text-align: center; }
.fronts { display: flex; gap: 14px; overflow-x: auto; align-items: flex-start; margin-top: 10px; padding-bottom: 6px; }
.front1 { flex: none; width: 520px; }
</style>
