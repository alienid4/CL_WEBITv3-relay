<script setup lang="ts">
import type { ColorBy, IpSeg } from '~/composables/useRackColors'
// 4-1 機房架構圖（2026-10-22，10-23 立體圖換成平面圖）：每個機房的機櫃——平面圖（一排一列，
// 搜尋命中／選中系統的櫃亮起並標台數，下方並排命中櫃的正面、命中設備在 U 位上亮）、熱度格（色深＝
// 已用 U 比例，附可排序表）、電力、機櫃正面（擬真立面，跟系統主機部署圖「機櫃」頁簽同一個元件）。
// 資料：/api/racks/*（rack_layout.py）。樓面座標沒有資料，平面圖依機櫃編號排列，不畫假位置。
// 平面圖另有：「顏色依」機器類型／系統等級（圖例可點、只亮那一類）、網段篩選（每台全部 IP 對網段）、
// U 位重疊標示、多詞組合搜尋（同欄位 OR、不同欄位 AND，每個詞一個可點的 chip）。
interface Power { unobserved_count: number; unobserved_keys: string[]; removed_count: number; removed_keys: string[]; w: number; amps: number | null; btu_h: number; volts: number; kg: number; if_all_on_w: number; used_u: number; free_u: number; largest_free_u?: number; known_count: number; unknown_count: number; unknown_keys: string[]; off_count: number; pending_count: number; off_keys: string[]; pending_keys: string[]; active_count: number }
interface Tile { overlap_count: number; overlap_keys: string[]; all_keys: string[]; rack: string; prefix: string; floor: string | null; device_count: number; system_count: number; used_u: number; height_u: number; keys: string[]; api_ids: string[]; vm_api_ids: string[]; power: Power }
interface Brief { position_corrected?: boolean; kind?: string; unobserved?: boolean; key: string; asset_serial: string; name: string; ip: string | null; api_ids: string[]; rack_raw: string | null; model: string | null; spec_model: string | null; room: string; rack: string | null; u_start: number | null; u_end: number | null; state: string | null; state_label: string | null; asset_status: string | null; w: number | null
  machine_type?: string; machine_type_label?: string; sys_level?: string; sys_level_label?: string; ips?: IpSeg[]; envs?: string[] }
interface NoRack { key: string; asset_serial: string; name: string; ip: string | null; api_ids: string[]; rack_raw: string | null; model: string | null; exist_level: string | null; exist_score: number | null; exist_label: string; seen: boolean }
interface Room { room: string; device_count: number; racked: number; racks: Tile[]; no_rack: NoRack[]; vm_excluded: number; power: Power }
interface DC { can_edit?: boolean; rooms: Room[]; height_u: number; floor_map_set: boolean; total_racked: number; total_devices: number; data_quality: Record<string, Brief[]>; devices: Record<string, Brief>; system_level_min: number }
type Angle = 'hardware' | 'host' | 'system'
interface Match { key: string; asset_serial: string; name: string; ip: string | null; model: string | null; room: string; kind: string; rack: string | null; u_start: number | null; u_end: number | null; rack_raw: string | null; api_ids: string[] }
interface RackHit { room: string; rack: string; count: number }
interface MultiTerm { term: string; field: string; field_label: string; fields: string[]; total: number; count: number; keys: string[] }
interface SearchRes { mode?: 'multi'; multi?: { terms: MultiTerm[]; unknown: string[]; rule: string }; q: string; counts: Record<Angle, number>; angles: Record<Angle, { label: string; total: number; matches: Match[]; racks: RackHit[]; racks_via_vm?: RackHit[]; systems: { api_id: string; name: string }[] }> }

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()

const dc = ref<DC | null>(null)
const loadErr = ref('')
try {
  dc.value = await apiFetch<DC>('/api/racks/datacenter')
} catch (e: any) {
  loadErr.value = e?.data?.detail ?? e?.message ?? '未知錯誤'
}
const rooms = computed(() => (dc.value?.rooms ?? []).filter(r => r.device_count > 0))
// 這個機房的 datastore 容量（儲存容量頁同一份計算；點了到 4-6 看這個機房）
const storageRooms = ref<{ room: string; capacity_mib: number; in_use_mib: number; free_mib: number; free_pct: number | null; volume_count: number }[]>([])
try {
  storageRooms.value = await apiFetch('/api/storage/rooms')
} catch (e: any) {
  storageRooms.value = []
  showToast(`儲存容量載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
}
function tib(mib: number) { return `${(mib / 1024 / 1024).toFixed(1)} TiB` }
const roomName = ref<string>(typeof route.query.room === 'string' ? route.query.room : '')
if (!roomName.value || !rooms.value.some(r => r.room === roomName.value)) {
  roomName.value = rooms.value.find(r => r.racked > 0)?.room ?? rooms.value[0]?.room ?? ''
}
const room = computed(() => rooms.value.find(r => r.room === roomName.value) ?? null)
const system = ref<string | null>(typeof route.query.system === 'string' ? route.query.system : null)

type View = 'plan' | 'heat' | 'power' | 'front' | 'norack' | 'devlist' | 'dq' | 'search' | 'place' | 'rsv'
const view = ref<View>('plan')
const selRack = ref<string | null>(null)
function pickRoom(r: string) {
  roomName.value = r
  selRack.value = null
  if (view.value === 'front') view.value = 'plan'
  router.replace({ query: { ...route.query, room: r } })
  selectHits()
}
function openRack(rack: string, r?: string) {
  if (r && r !== roomName.value) roomName.value = r
  selRack.value = rack
  view.value = 'front'
}

// ===== 搜尋：一個框三個角度（硬體／主機／系統）=====
const q = ref('')
const res = ref<SearchRes | null>(null)
const angle = ref<Angle>('hardware')
const ANGLES: Angle[] = ['hardware', 'host', 'system']
const ANGLE_LABEL: Record<Angle, string> = { hardware: '硬體', host: '主機', system: '系統' }
let timer: ReturnType<typeof setTimeout> | null = null
watch(q, (v) => {
  if (timer) clearTimeout(timer)
  if (v.replace(/[\s\-_]/g, '').length < 2) { res.value = null; planSel.value = []; return }
  timer = setTimeout(async () => {
    try {
      res.value = await apiFetch<SearchRes>('/api/racks/search', { query: { q: v } })
      termPick.value = null
      angle.value = ANGLES.find(a => res.value!.counts[a] > 0) ?? 'hardware'
      if (ANGLES.some(a => res.value!.counts[a] > 0)) {
        // 只命中一台：直接跳到它的機房；否則留在目前機房（沒命中就跳到命中最多的機房）
        const ms = res.value.angles[angle.value].matches
        const hitRooms = Object.keys(roomHits.value)
        if (ms.length === 1 && rooms.value.some(r => r.room === ms[0].room)) roomName.value = ms[0].room
        else if (hitRooms.length && !roomHits.value[roomName.value]) roomName.value = hitRooms.sort((a, b) => roomHits.value[b] - roomHits.value[a])[0]
        view.value = 'plan'
      }
      selectHits()
    } catch (e: any) {
      res.value = null
      planSel.value = []
      showToast(`搜尋失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
    }
  }, 300)
})
const activeMatches = computed(() => (res.value?.angles[angle.value].matches ?? []).map((m, i) => ({
  ...m, no: i + 1, u_text: m.u_start ? (m.u_start === m.u_end ? `${m.u_start}U` : `${m.u_start}-${m.u_end}U`) : '',
  rack_text: m.rack ?? (m.kind === 'vm' ? '' : (m.rack_raw ? `（${m.rack_raw}）` : '無機櫃資料')), sys_text: m.api_ids.join(', '),
})))
const { sortKey: mKey, sortDir: mDir, toggle: mToggle, sorted: mSorted } = useSort(activeMatches, 'no')

// ===== 平面圖的亮法：搜尋（或某個詞）∩ 網段 ∩ 圖例分類，三者都可選；都沒選就不亮 =====
const colorBy = ref<ColorBy>('type')
// U 位重疊標記：資料裡太多（多半是同一台的分割區分開登記），預設不顯示，要看再打開（2026-10-23 使用者）
const showOverlap = ref(false)
const overlapRacks = computed(() => (room.value?.racks ?? []).filter(t => t.overlap_count).length)
const legendPick = ref<string | null>(null)
const segPick = ref('')
const termPick = ref<string | null>(null)
watch(colorBy, () => { legendPick.value = null })
const roomKeys = computed(() => (room.value?.racks ?? []).flatMap(t => t.all_keys ?? t.keys))
const searchKeys = computed<string[] | null>(() => {
  if (!res.value) return null
  if (termPick.value && res.value.multi) return res.value.multi.terms.find(t => t.term === termPick.value)?.keys ?? []
  return res.value.angles[angle.value].matches.map(x => x.key)
})
const segOptions = computed(() => {
  const m = new Map<string, { cidr: string; purpose: string | null; vlan: string | null; keys: Set<string> }>()
  for (const k of roomKeys.value) {
    for (const x of dc.value?.devices[k]?.ips ?? []) {
      if (!x.cidr) continue
      if (!m.has(x.cidr)) m.set(x.cidr, { cidr: x.cidr, purpose: x.purpose, vlan: x.vlan, keys: new Set() })
      m.get(x.cidr)!.keys.add(k)
    }
  }
  return [...m.values()].sort((a, b) => natCmp(a.cidr, b.cidr))
})
watch(roomName, () => { if (segPick.value && !segOptions.value.some(o => o.cidr === segPick.value)) segPick.value = '' })
const legend = computed(() => rackCats(colorBy.value).map(c => ({
  ...c, keys: roomKeys.value.filter(k => rackCatOf(colorBy.value, dc.value?.devices[k]) === c.k),
})).filter(c => c.keys.length))
const lit = computed<Set<string> | null>(() => {
  const sets: Set<string>[] = []
  if (searchKeys.value) sets.push(new Set(searchKeys.value))
  if (segPick.value) sets.push(segOptions.value.find(o => o.cidr === segPick.value)?.keys ?? new Set())
  if (legendPick.value) sets.push(new Set(legend.value.find(c => c.k === legendPick.value)?.keys ?? []))
  if (!sets.length) return null
  const [first, ...rest] = sets
  return new Set([...first].filter(k => rest.every(r => r.has(k))))
})
const litKeys = computed(() => (lit.value ? [...lit.value] : []))
// 要高亮的櫃：亮起的設備所在的櫃（只有搜尋時含「經 ESXi」的櫃）∪ 選中系統碰到的櫃
const hiRacks = computed(() => {
  const set = new Set<string>()
  if (lit.value) for (const t of room.value?.racks ?? []) if ((t.all_keys ?? t.keys).some(k => lit.value!.has(k))) set.add(t.rack)
  if (res.value && !termPick.value && !segPick.value && !legendPick.value) {
    for (const r of res.value.angles[angle.value].racks_via_vm ?? []) if (r.room === roomName.value) set.add(r.rack)
  }
  if (system.value && room.value) {
    for (const t of room.value.racks) if (t.api_ids.includes(system.value) || t.vm_api_ids.includes(system.value)) set.add(t.rack)
  }
  return set
})
function tileBar(t: Tile) {
  const keys = t.all_keys ?? t.keys
  if (!keys.length) return []
  return rackCats(colorBy.value).map(c => ({ ...c, n: keys.filter(k => rackCatOf(colorBy.value, dc.value?.devices[k]) === c.k).length }))
    .filter(c => c.n).map(c => ({ ...c, pct: (c.n * 100) / keys.length }))
}
function tileTitle(t: Tile) {
  const cats = tileBar(t).map(c => `${c.label} ${c.n}`).join('、')
  return `${t.rack}：${t.device_count} 台、已用 ${t.used_u}U／${t.height_u}U、${t.system_count} 個系統${cats ? '\n' + cats : ''}${t.overlap_count ? `\n位置重疊 ${t.overlap_count} 台` : ''}${t.api_ids.length ? '\n' + t.api_ids.join(', ') : ''}`
}
function setSystem(api: string | null) {
  system.value = api
  router.replace({ query: { ...route.query, system: api || undefined } })
}
function onSysChange(e: Event) { setSystem((e.target as HTMLSelectElement).value || null) }
const roomSystems = computed(() => {
  const s = new Set<string>()
  for (const t of room.value?.racks ?? []) [...t.api_ids, ...t.vm_api_ids].forEach(a => s.add(a))
  return [...s].sort()
})

// ===== 排（機櫃代碼前綴）=====
const rows = computed(() => {
  const m = new Map<string, Tile[]>()
  for (const t of room.value?.racks ?? []) {
    if (!m.has(t.prefix)) m.set(t.prefix, [])
    m.get(t.prefix)!.push(t)
  }
  return [...m.entries()].map(([prefix, tiles]) => ({ prefix, tiles }))
})

// ===== A. 平面圖（依機櫃編號排列，非實際位置）=====
const natCmp = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
const planRows = computed(() => rows.value.map(r => ({ prefix: r.prefix, tiles: [...r.tiles].sort((a, b) => natCmp(a.rack, b.rack)) }))
  .sort((a, b) => natCmp(a.prefix, b.prefix)))
// 每個機房命中幾台（目前搜尋角度；點了某個詞就只算那個詞）
const roomHits = computed(() => {
  const m: Record<string, number> = {}
  const only = termPick.value && searchKeys.value ? new Set(searchKeys.value) : null
  for (const x of res.value?.angles[angle.value].matches ?? []) if (!only || only.has(x.key)) m[x.room] = (m[x.room] ?? 0) + 1
  return m
})
// 目前機房每櫃的數字：搜尋命中台數（含經 ESXi），沒搜尋時＝選中系統在這櫃的台數
const rackCount = computed(() => {
  const m: Record<string, number> = {}
  if (lit.value) {
    for (const t of room.value?.racks ?? []) {
      const n = (t.all_keys ?? t.keys).filter(k => lit.value!.has(k)).length
      if (n) m[t.rack] = n
    }
    if (res.value && !termPick.value && !segPick.value && !legendPick.value) {
      for (const r of res.value.angles[angle.value].racks_via_vm ?? []) if (r.room === roomName.value) m[r.rack] = (m[r.rack] ?? 0) + r.count
    }
  } else if (system.value && room.value) {
    for (const t of room.value.racks) {
      const n = t.keys.filter(k => (dc.value?.devices[k]?.api_ids ?? []).includes(system.value!)).length
      if (n || t.vm_api_ids.includes(system.value)) m[t.rack] = n
    }
  }
  return m
})
// 下方並排的機櫃正面：有亮的就預設全帶（最多 PLAN_MAX 櫃，其餘點平面圖加入）
const PLAN_MAX = 8
const planSel = ref<string[]>([])
const planMore = ref(0)
function selectHits() {
  const order = planRows.value.flatMap(r => r.tiles.map(t => t.rack))
  const all = lit.value || res.value ? order.filter(r => hiRacks.value.has(r)) : []
  planSel.value = all.slice(0, PLAN_MAX)
  planMore.value = Math.max(0, all.length - PLAN_MAX)
}
watch([legendPick, segPick, termPick], () => selectHits())
function pickTerm(t: string) {
  termPick.value = termPick.value === t ? null : t
  if (view.value !== 'plan') view.value = 'plan'
}
function togglePlan(rack: string) {
  const i = planSel.value.indexOf(rack)
  if (i >= 0) planSel.value.splice(i, 1)
  else planSel.value.push(rack)
}
watch(angle, selectHits)

// ===== C. 熱度格 =====
function heatStyle(t: Tile) {
  const ratio = t.height_u ? Math.min(1, t.used_u / t.height_u) : 0
  const pct = Math.round(5 + ratio * 85)
  return { background: `color-mix(in srgb, var(--brand) ${pct}%, var(--card))`, color: pct > 50 ? 'var(--card)' : 'var(--ink)' }
}
const showTable = ref(false)
const tileRows = computed(() => (room.value?.racks ?? []).map((t, i) => ({
  ...t, no: i + 1, ratio: t.height_u ? Math.round(t.used_u * 100 / t.height_u) : 0,
})))
const { sortKey: tKey, sortDir: tDir, toggle: tToggle, sorted: tSorted } = useSort(tileRows, 'no')

// ===== 電力（每櫃）=====
const powerRows = computed(() => (room.value?.racks ?? []).map((t, i) => ({
  rack: t.rack, no: i + 1, devices: t.device_count, w: t.power.w, amps: t.power.amps, btu_h: t.power.btu_h, kg: t.power.kg,
  used_u: t.power.used_u, free_u: t.power.free_u, largest_free_u: t.power.largest_free_u ?? 0,
  unknown: t.power.unknown_count, dormant: t.power.off_count + t.power.pending_count, if_on: t.power.if_all_on_w, power: t.power,
  unobs: t.power.unobserved_count, removed: t.power.removed_count,
})))
const { sortKey: wKey, sortDir: wDir, toggle: wToggle, sorted: wSorted } = useSort(powerRows, 'no')

// ===== 設備清單下鑽（未知規格／關機未下架／待確認）=====
const devList = ref<{ label: string; keys: string[] }>({ label: '', keys: [] })
const editingPos = ref<string | null>(null)
async function reloadDc() { dc.value = await apiFetch<DC>('/api/racks/datacenter') }
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
function showDevs(label: string, keys: string[]) { devList.value = { label, keys }; view.value = 'devlist'; picked.value = {} }
// 批次標記（現場盤點結果，限管理員；每台留稽核紀錄）
const picked = ref<Record<string, boolean>>({})
const pickedKeys = computed(() => Object.keys(picked.value).filter(k => picked.value[k]))
const BULK_LABEL: Record<string, string> = { off: '標記已關機未下架', removed: '標記已不在櫃上', clear: '清除標記' }
async function bulk(kind: 'off' | 'removed' | 'clear') {
  const n = pickedKeys.value.length
  if (!n) return
  if (!window.confirm(`把選取的 ${n} 台「${BULK_LABEL[kind]}」？\n（會留稽核紀錄）`)) return
  try {
    await apiFetch('/api/racks/state/bulk', { method: 'PUT', body: { keys: pickedKeys.value, state: kind === 'clear' ? null : kind, note: '現場盤點' } })
    showToast(`已${BULK_LABEL[kind]} ${n} 台`, 'success')
    picked.value = {}
    dc.value = await apiFetch<DC>('/api/racks/datacenter')
  } catch (e: any) {
    showToast(`批次標記失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
const devRows = computed(() => devList.value.keys.map((k, i) => ({ ...(dc.value?.devices[k] ?? { key: k } as Brief), no: i + 1 }))
  .map(d => ({ ...d, u_text: d.u_start ? (d.u_start === d.u_end ? `${d.u_start}U` : `${d.u_start}-${d.u_end}U`) : '', sys_text: (d.api_ids ?? []).join(', '),
    type_text: d.machine_type_label ?? '不明', ip_text: (d.ips ?? []).map(x => x.ip).join(', ') })))
const { sortKey: lKey, sortDir: lDir, toggle: lToggle, sorted: lSorted } = useSort(devRows, 'no')

// ===== 資料品質 =====
const DQ_LABEL: Record<string, string> = { vm_model: '型號寫 VM 但填了機櫃', garbled: '型號是亂碼', generic: '型號只寫籠統描述', unknown_model: '型號在規格表裡找不到' }
const dqKind = ref('vm_model')
const dqRows = computed(() => (dc.value?.data_quality[dqKind.value] ?? []).map((d, i) => ({ ...d, no: i + 1, sys_text: (d.api_ids ?? []).join(', ') })))
const { sortKey: qKey, sortDir: qDir, toggle: qToggle, sorted: qSorted } = useSort(dqRows, 'no')

// ===== 上架助手 =====
interface Spec { model: string; rack_u: number | null; psu_max_w: number | null; weight_kg: number | null; is_generic: boolean }
interface Sug { room: string; rack: string; fits: boolean; fit_count: number; suggest_u_start: number | null; suggest_u_end: number | null; best_run: number; leftover: number | null; free_u: number; uncertain_u: boolean; no_u_count: number; reserved_u: number; w_now: number; w_after: number; w_budget: number | null; w_ok: boolean | null; amps_after: number | null; kg_now: number; kg_after: number; kg_budget: number | null; kg_ok: boolean | null; unknown_count: number; reasons: string[] }
const specs = ref<Spec[]>([])
const pf = reactive({ model: '', u: 1 as number, w: null as number | null, kg: null as number | null, qty: 1, rooms: [] as string[] })
const sugs = ref<Sug[]>([])
const placing = ref(false)
async function openPlace() {
  view.value = 'place'
  if (!pf.rooms.length && roomName.value) pf.rooms = [roomName.value]
  if (!specs.value.length) {
    try { specs.value = (await apiFetch<{ specs: Spec[] }>('/api/model-specs')).specs.filter(s => !s.is_generic) } catch (e: any) {
      showToast(`型號規格載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
    }
  }
}
watch(() => pf.model, (m) => {
  const s = specs.value.find(x => x.model === m)
  if (s) { pf.u = s.rack_u || pf.u; pf.w = s.psu_max_w; pf.kg = s.weight_kg }
})
async function findRacks() {
  placing.value = true
  try {
    const r = await apiFetch<{ racks: Sug[] }>('/api/racks/placement', { query: { u: pf.u, qty: pf.qty, w: pf.w ?? undefined, kg: pf.kg ?? undefined, rooms: pf.rooms.join(',') || undefined } })
    sugs.value = r.racks
  } catch (e: any) {
    showToast(`查詢失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally {
    placing.value = false
  }
}
const sugRows = computed(() => sugs.value.map((s, i) => ({
  ...s, no: i + 1, result: s.fits ? '可放' : '不行', reason_text: s.reasons.join('；'),
  u_text: s.suggest_u_start ? `${s.suggest_u_start}${s.suggest_u_end !== s.suggest_u_start ? `-${s.suggest_u_end}` : ''}U` : '—',
  w_text: s.w_budget == null ? `${s.w_after} W／未設定上限` : `${s.w_after}／${s.w_budget} W`,
  kg_text: s.kg_budget == null ? `${s.kg_after} kg／未設定上限` : `${s.kg_after}／${s.kg_budget} kg`,
})))
const { sortKey: pKey, sortDir: pDir, toggle: pToggle, sorted: pSorted } = useSort(sugRows, 'no')
// 選定一櫃：登記既有設備的位置，或預留
const chosen = ref<Sug | null>(null)
const pick = reactive({ u_start: 1 as number, q: '', note: '', label: '', expires: '' })
const assets = ref<{ key: string; asset_serial: string; name: string; ip: string | null; model: string | null; room: string; rack: string | null; u_start: number | null; u_end: number | null }[]>([])
function choose(s: Sug) {
  chosen.value = s
  pick.u_start = s.suggest_u_start ?? 1
  const d = new Date(); d.setDate(d.getDate() + 30)
  pick.expires = d.toISOString().slice(0, 10)
  pick.label = pf.model || ''
}
async function searchAssets() {
  try { assets.value = await apiFetch('/api/racks/find-asset', { query: { q: pick.q } }) } catch (e: any) {
    showToast(`找設備失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
async function registerAsset(a: { key: string; name: string }) {
  if (!chosen.value) return
  if (!pick.note.trim()) { showToast('要寫備註（例：10/23 上架）', 'warn'); return }
  try {
    const r = await apiFetch<{ collisions: any[] }>('/api/racks/position', { method: 'PUT', body: {
      key: a.key, room: chosen.value.room, rack: chosen.value.rack, u_start: pick.u_start, u_height: pf.u, note: pick.note } })
    showToast(r.collisions.length ? `已登記，但跟 ${r.collisions.length} 台重疊` : `已登記 ${a.name} 到 ${chosen.value.rack} ${pick.u_start}U`, r.collisions.length ? 'warn' : 'success')
    await reloadDc(); await findRacks()
  } catch (e: any) {
    showToast(`登記失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
async function reserveIt() {
  if (!chosen.value) return
  if (!pick.label.trim()) { showToast('預留要寫名稱', 'warn'); return }
  try {
    const r = await apiFetch<{ collisions: any[] }>('/api/racks/reservations', { method: 'POST', body: {
      room: chosen.value.room, rack: chosen.value.rack, u_start: pick.u_start, u_height: pf.u, label: pick.label,
      expires_at: pick.expires, model: pf.model || null, w: pf.w, kg: pf.kg, note: pick.note || null } })
    showToast(r.collisions.length ? `已預留，但跟 ${r.collisions.length} 項重疊` : '已預留', r.collisions.length ? 'warn' : 'success')
    await loadRsv(); await findRacks()
  } catch (e: any) {
    showToast(`預留失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
// ===== 預留中 =====
interface Rsv { id: number; room: string; rack: string; u_start: number; u_end: number; label: string; model: string | null; expires_at: string; expired: boolean; created_by: string | null; created_at: string; note: string | null }
const rsvs = ref<Rsv[]>([])
async function loadRsv() {
  try { rsvs.value = await apiFetch<Rsv[]>('/api/racks/reservations') } catch (e: any) {
    showToast(`預留清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
onMounted(loadRsv)
const rsvRows = computed(() => rsvs.value.map((r, i) => ({ ...r, no: i + 1, u_text: `${r.u_start}${r.u_end !== r.u_start ? `-${r.u_end}` : ''}U`, st: r.expired ? '已過期' : '預留中' })))
const { sortKey: vKey, sortDir: vDir, toggle: vToggle, sorted: vSorted } = useSort(rsvRows, 'no')
async function cancelRsv(r: Rsv) {
  if (!window.confirm(`取消預留「${r.label}」（${r.room} ${r.rack} ${r.u_start}U）？`)) return
  try { await apiFetch(`/api/racks/reservations/${r.id}/cancel`, { method: 'PUT' }); await loadRsv() } catch (e: any) {
    showToast(`取消失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}

// ===== 無機櫃資料 =====
const noRackRows = computed(() => (room.value?.no_rack ?? []).map((d, i) => ({ ...d, no: i + 1, sys_text: d.api_ids.join(', '), exist_sort: d.exist_score ?? -1 })))
const { sortKey: nKey, sortDir: nDir, toggle: nToggle, sorted: nSorted } = useSort(noRackRows, 'no')
const EXIST_SHORT: Record<string, string> = {
  onboarded: '已納管', inventory: '匯入清單有', collect_stale: '收集較舊', scan: '掃描活著', cia_only: '只有登記', none: '無證據',
}
</script>

<template>
  <div class="dcpage">
    <div class="head">
      <div class="ey">WAR ROOM · 4-5</div>
      <h1>機房架構圖</h1>
    </div>
    <p v-if="loadErr" class="err">機房資料載入失敗：{{ loadErr }}</p>
    <template v-else-if="dc">
      <div class="chips">
        <button v-for="r in rooms" :key="r.room" class="chip" :class="{ on: r.room === roomName }" @click="pickRoom(r.room)">
          {{ r.room }} {{ r.device_count }}<span v-if="roomHits[r.room]" class="cnt">符合 {{ roomHits[r.room] }}</span>
        </button>
        <InfoNote>數字是實體設備台數（同一台多筆登記算一台，退役不算）；VM 不在機櫃裡，不列入（它們在 ESXi 主機上）。機櫃磚只算機櫃欄看得懂的設備，認不出來的（跨兩櫃、大樓樓層、空白）列在「無機櫃資料」。樓面位置沒有資料，不畫假座標；樓層要在系統設定填「機櫃代碼前綴→樓層」才會顯示。</InfoNote>
      </div>
      <div class="toolbar">
        <input v-model="q" class="kw" type="text" placeholder="搜尋：機型／序號、主機名／IP、系統代碼或名稱">
        <template v-if="res && res.multi">
          <span class="muted">組合 {{ res.counts.hardware }} 台（{{ res.multi.rule }}）：</span>
          <button v-for="t in res.multi.terms" :key="t.term" class="chip" :class="{ on: termPick === t.term }"
                  :title="`打中：${t.fields.join('、')}；單獨找 ${t.total} 台`" @click="pickTerm(t.term)">{{ t.term }}（{{ t.field_label }}）{{ t.count }}</button>
          <span v-for="u in res.multi.unknown" :key="'u' + u" class="chip miss">找不到：{{ u }}</span>
        </template>
        <template v-else-if="res">
          <button v-for="a in ANGLES" :key="a" class="chip" :class="{ on: angle === a }" :disabled="!res.counts[a]"
                  @click="angle = a; view = 'search'">{{ ANGLE_LABEL[a] }} {{ res.counts[a] }}</button>
        </template>
        <div class="sp" />
        <label class="sysbox">標示系統
          <select :value="system ?? ''" @change="onSysChange">
            <option value="">（無）</option>
            <option v-for="a in roomSystems" :key="a" :value="a">{{ a }}</option>
          </select>
        </label>
        <NuxtLink v-if="system" :to="{ path: '/topology', query: { system } }" class="lnk">開系統主機部署圖</NuxtLink>
      </div>

      <div v-if="room" class="summary">
        <b>{{ room.room }}</b>
        <span>機櫃 {{ room.racks.length }}</span>
        <span>有機櫃資料 {{ room.racked }} 台</span>
        <button class="linkbtn" @click="view = 'norack'">無機櫃資料 {{ room.no_rack.length }} 台</button>
        <span class="muted">VM 不列 {{ room.vm_excluded }} 台</span>
        <span v-if="hiRacks.size" class="hi-tag">標示中 {{ hiRacks.size }} 櫃</span>
        <NuxtLink v-for="sr in storageRooms.filter(x => x.room === room.room)" :key="sr.room" :to="{ path: '/storage', query: { room: sr.room } }" class="plink">
          儲存 {{ tib(sr.capacity_mib) }}／已用 {{ tib(sr.in_use_mib) }}（{{ sr.volume_count }} 個 datastore）
        </NuxtLink>
      </div>
      <div v-if="room && room.racks.length" class="summary pw">
        <span><b>{{ (room.power.w / 1000).toFixed(1) }} kW</b>（{{ room.power.amps }} A @{{ room.power.volts }}V、{{ room.power.btu_h.toLocaleString() }} BTU/h）</span>
        <span>{{ room.power.kg.toLocaleString() }} kg</span>
        <span>已用 {{ room.power.used_u }}U／空 {{ room.power.free_u }}U</span>
        <button v-if="room.power.unknown_count" class="linkbtn" @click="showDevs(`${room.room} 未知規格`, room.power.unknown_keys)">未知規格 {{ room.power.unknown_count }} 台</button>
        <button v-if="room.power.off_count" class="linkbtn" @click="showDevs(`${room.room} 關機未下架`, room.power.off_keys)">關機未下架 {{ room.power.off_count }}</button>
        <button v-if="room.power.unobserved_count" class="linkbtn" @click="showDevs(`${room.room} 在用但未觀察到`, room.power.unobserved_keys)">其中未觀察到 {{ room.power.unobserved_count }} 台</button>
        <button v-if="room.power.pending_count" class="linkbtn" @click="showDevs(`${room.room} 待確認`, room.power.pending_keys)">待確認 {{ room.power.pending_count }}</button>
        <button v-if="room.power.removed_count" class="linkbtn" @click="showDevs(`${room.room} 已不在櫃上`, room.power.removed_keys)">已不在櫃上 {{ room.power.removed_count }}</button>
        <button class="linkbtn" @click="showDevs(`${room.room} 在用設備`, room.racks.flatMap(t => t.keys))">在用 {{ room.racked }} 台（可批次標記）</button>
        <span v-if="room.power.if_all_on_w">若全部開機 +{{ (room.power.if_all_on_w / 1000).toFixed(1) }} kW</span>
        <InfoNote>機櫃口徑看的是「設備實體在不在櫃上」，跟全站台數（存在可信度）是兩回事：使用中＋有機櫃＝在用，算電、算 U、算重量、算台數，系統看不到它（只有登記）就另標「未觀察到」（空心燈）；停用／閒置＋有機櫃＝關機未下架；報廢＋有機櫃＝待確認（可能還在櫃上）。關機未下架與待確認佔 U、算重量、用電算 0，另列「若全部開機」。現場盤點結果可以人工標記（關機未下架／已不在櫃上／清除），每次都留稽核紀錄；「已不在櫃上」的設備從機櫃拿掉。</InfoNote>
        <a class="plink" :href="`${apiBase}/api/racks/position/pending-cia.xlsx`">CIA 待更正（xlsx）</a>
        <InfoNote label="電">用電＝每台在用設備一顆 PSU 的額定瓦數（型號規格表），只有對得到規格的設備算進來，其他列在「未知規格」；電流＝瓦數 ÷ 機房電壓（系統設定 room_volts，預設 220V）；散熱＝瓦數 × 3.412。關機未下架（停用／閒置還在櫃上）、待確認（報廢還在櫃上、或只有登記看不到）不算台數、用電算 0，佔 U 與重量照算。型號規格在「型號規格表」頁調整。</InfoNote>
        <a v-if="room.racks.length" class="plink" :href="`/print/rack-sheet?room=${encodeURIComponent(room.room)}`" target="_blank">列印整個機房盤點表（一櫃一頁）</a>
        <a class="plink" :href="`/print/labels?room=${encodeURIComponent(room.room)}`" target="_blank">印這個機房的標籤</a>
      </div>

      <div class="tabs" role="tablist">
        <button type="button" class="tab" :class="{ active: view === 'plan' }" @click="view = 'plan'">平面圖</button>
        <button type="button" class="tab" :class="{ active: view === 'heat' }" @click="view = 'heat'">熱度格</button>
        <button type="button" class="tab" :class="{ active: view === 'power' }" @click="view = 'power'">電力</button>
        <button type="button" class="tab" :class="{ active: view === 'front' }" @click="view = 'front'">機櫃正面{{ selRack ? `：${selRack}` : '' }}</button>
        <button type="button" class="tab" :class="{ active: view === 'norack' }" @click="view = 'norack'">無機櫃資料 {{ room?.no_rack.length ?? 0 }}</button>
        <button v-if="res" type="button" class="tab" :class="{ active: view === 'search' }" @click="view = 'search'">搜尋結果 {{ res.counts[angle] }}</button>
        <button type="button" class="tab" :class="{ active: view === 'dq' }" @click="view = 'dq'">資料品質</button>
        <button type="button" class="tab" :class="{ active: view === 'place' }" @click="openPlace">上架助手</button>
        <button type="button" class="tab" :class="{ active: view === 'rsv' }" @click="view = 'rsv'">預留中 {{ rsvs.length }}</button>
        <button v-if="view === 'devlist'" type="button" class="tab active">{{ devList.label }} {{ devList.keys.length }}</button>
      </div>

      <!-- A. 平面圖 -->
      <div v-if="view === 'plan'" class="panel">
        <p v-if="!planRows.length" class="muted">這個機房沒有可判讀的機櫃資料。</p>
        <template v-else>
          <div class="chips">
            <span class="muted">顏色依</span>
            <button class="chip" :class="{ on: colorBy === 'type' }" @click="colorBy = 'type'">機器類型</button>
            <button class="chip" :class="{ on: colorBy === 'level' }" @click="colorBy = 'level'">系統等級</button>
            <button v-if="overlapRacks" class="chip" :class="{ on: showOverlap }" @click="showOverlap = !showOverlap">位置重疊（{{ overlapRacks }} 櫃）</button>
            <span class="sp2" />
            <label class="sysbox">網段
              <select v-model="segPick">
                <option value="">（全部）</option>
                <option v-for="o in segOptions" :key="o.cidr" :value="o.cidr">{{ o.cidr }}{{ o.purpose ? ' ' + o.purpose : '' }}{{ o.vlan ? ' VLAN ' + o.vlan : '' }}（{{ o.keys.size }} 台）</option>
              </select>
            </label>
            <InfoNote>網段：每台的全部 IP（資產登記的 IP＋收集到的每張網卡）對網段配置表，任一個 IP 落在這段就算；一台可以在好幾段。搜尋、網段、圖例三個可以一起用，亮的是三者都符合的設備。</InfoNote>
          </div>
          <div class="chips legendrow">
            <button v-for="c in legend" :key="c.k" class="chip" :class="{ on: legendPick === c.k }" @click="legendPick = legendPick === c.k ? null : c.k">
              <span class="sw" :style="{ background: c.color }" />{{ c.label }} {{ c.keys.length }}
            </button>
            <button v-if="lit" class="linkbtn" @click="showDevs('平面圖亮起的設備', litKeys)">列出亮起的 {{ litKeys.length }} 台</button>
            <button v-if="legendPick || segPick || termPick" class="linkbtn" @click="legendPick = null; segPick = ''; termPick = null">清除篩選</button>
          </div>
          <div v-for="row in planRows" :key="row.prefix" class="heatrow">
            <span class="rowname">{{ row.prefix }} 排</span>
            <button v-for="t in row.tiles" :key="t.rack" class="ptile" :class="{ hi: hiRacks.has(t.rack), sel: planSel.includes(t.rack), dim: !!lit && !hiRacks.has(t.rack) }"
                    :title="tileTitle(t)" @click="togglePlan(t.rack)">
              <span class="pt-top">{{ t.rack }}<span v-if="rackCount[t.rack]" class="cnt">{{ rackCount[t.rack] }}</span></span>
              <span class="pbar"><i v-for="c in tileBar(t)" :key="c.k" :style="{ width: `${c.pct}%`, background: c.color }" /></span>
              <span v-if="showOverlap && t.overlap_count" class="ovb">重疊 {{ t.overlap_count }}</span>
            </button>
          </div>
          <p class="muted legend">依機櫃編號排列，非實際位置。色條＝這櫃各類台數比例；亮框＝含亮起的設備（數字＝台數）。點機櫃加入／移出下方正面。</p>
          <p v-if="!planSel.length" class="muted">點機櫃看正面</p>
          <p v-if="planMore" class="muted">另有 {{ planMore }} 櫃也有亮，點平面圖加入。</p>
          <div v-if="planSel.length" class="fronts">
            <div v-for="r in planSel" :key="r" class="front1">
              <RackElevation :room="roomName" :rack="r" :api-id="system" :hi-keys="litKeys" :color-by="colorBy" :show-overlap="showOverlap" compact />
              <button class="linkbtn" @click="openRack(r)">看完整正面與清單</button>
            </div>
          </div>
        </template>
      </div>

      <!-- C. 熱度格 -->
      <div v-if="view === 'heat'" class="panel">
        <div v-for="row in rows" :key="row.prefix" class="heatrow">
          <span class="rowname">{{ row.prefix }} 排</span>
          <button v-for="t in row.tiles" :key="t.rack" class="heat" :class="{ hi: hiRacks.has(t.rack) }" :style="heatStyle(t)"
                  :title="`${t.rack}：已用 ${t.used_u}U／${t.height_u}U、${t.system_count} 個系統`" @click="openRack(t.rack)">
            <b>{{ t.device_count }}</b><span>{{ t.rack }}</span>
          </button>
        </div>
        <p v-if="!rows.length" class="muted">這個機房沒有可判讀的機櫃資料。</p>
        <button class="tb" @click="showTable = !showTable">{{ showTable ? '收起表格' : '看成表格' }}</button>
        <table v-if="showTable" class="tbl">
          <thead><tr>
            <SortTh k="no" :active="tKey" :dir="tDir" @sort="tToggle">項次</SortTh>
            <SortTh k="rack" :active="tKey" :dir="tDir" @sort="tToggle">機櫃</SortTh>
            <SortTh k="prefix" :active="tKey" :dir="tDir" @sort="tToggle">排</SortTh>
            <SortTh k="device_count" :active="tKey" :dir="tDir" @sort="tToggle">設備</SortTh>
            <SortTh k="system_count" :active="tKey" :dir="tDir" @sort="tToggle">系統</SortTh>
            <SortTh k="used_u" :active="tKey" :dir="tDir" @sort="tToggle">已用 U</SortTh>
            <SortTh k="ratio" :active="tKey" :dir="tDir" @sort="tToggle">使用率</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="t in tSorted" :key="t.rack">
              <td class="mono">T{{ t.no }}</td>
              <td><button class="linkbtn" @click="openRack(t.rack)">{{ t.rack }}</button></td>
              <td>{{ t.prefix }}</td>
              <td class="mono">{{ t.device_count }}</td>
              <td class="mono">{{ t.system_count }}</td>
              <td class="mono">{{ t.used_u }}</td>
              <td class="mono">{{ t.ratio }}%</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 電力：每櫃 -->
      <div v-if="view === 'power'" class="panel">
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="wKey" :dir="wDir" @sort="wToggle">項次</SortTh>
            <SortTh k="rack" :active="wKey" :dir="wDir" @sort="wToggle">機櫃</SortTh>
            <SortTh k="devices" :active="wKey" :dir="wDir" @sort="wToggle">在用</SortTh>
            <SortTh k="w" :active="wKey" :dir="wDir" @sort="wToggle">W</SortTh>
            <SortTh k="amps" :active="wKey" :dir="wDir" @sort="wToggle">A</SortTh>
            <SortTh k="btu_h" :active="wKey" :dir="wDir" @sort="wToggle">BTU/h</SortTh>
            <SortTh k="kg" :active="wKey" :dir="wDir" @sort="wToggle">kg</SortTh>
            <SortTh k="used_u" :active="wKey" :dir="wDir" @sort="wToggle">已用 U</SortTh>
            <SortTh k="free_u" :active="wKey" :dir="wDir" @sort="wToggle">空 U</SortTh>
            <SortTh k="largest_free_u" :active="wKey" :dir="wDir" @sort="wToggle">最大連續空</SortTh>
            <SortTh k="unknown" :active="wKey" :dir="wDir" @sort="wToggle">未知規格</SortTh>
            <SortTh k="unobs" :active="wKey" :dir="wDir" @sort="wToggle">未觀察到</SortTh>
            <SortTh k="dormant" :active="wKey" :dir="wDir" @sort="wToggle">關機／待確認</SortTh>
            <SortTh k="if_on" :active="wKey" :dir="wDir" @sort="wToggle">若全開 +W</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in wSorted" :key="r.rack">
              <td class="mono">W{{ r.no }}</td>
              <td><button class="linkbtn" @click="openRack(r.rack)">{{ r.rack }}</button></td>
              <td class="mono">{{ r.devices }}</td><td class="mono">{{ r.w }}</td><td class="mono">{{ r.amps ?? '—' }}</td>
              <td class="mono">{{ r.btu_h }}</td><td class="mono">{{ r.kg }}</td><td class="mono">{{ r.used_u }}</td>
              <td class="mono">{{ r.free_u }}</td><td class="mono">{{ r.largest_free_u }}</td>
              <td><button v-if="r.unknown" class="linkbtn mono" @click="showDevs(`${r.rack} 未知規格`, r.power.unknown_keys)">{{ r.unknown }}</button><span v-else class="muted">0</span></td>
              <td><button v-if="r.unobs" class="linkbtn mono" @click="showDevs(`${r.rack} 在用但未觀察到`, r.power.unobserved_keys)">{{ r.unobs }}</button><span v-else class="muted">0</span></td>
              <td><button v-if="r.dormant" class="linkbtn mono" @click="showDevs(`${r.rack} 關機未下架／待確認`, [...r.power.off_keys, ...r.power.pending_keys])">{{ r.dormant }}</button><span v-else class="muted">0</span></td>
              <td class="mono">{{ r.if_on || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 設備清單（下鑽）-->
      <div v-if="view === 'devlist'" class="panel">
        <PositionEditor v-if="editingPos && dc.devices[editingPos]" :key="editingPos"
                        :device="{ ...dc.devices[editingPos], rack: dc.devices[editingPos].rack }" @saved="reloadDc" @close="editingPos = null" />
        <div v-if="dc.can_edit" class="chips">
          <span class="muted">已選 {{ pickedKeys.length }} 台</span>
          <button class="chip" :disabled="!pickedKeys.length" @click="bulk('off')">標記已關機未下架</button>
          <button class="chip" :disabled="!pickedKeys.length" @click="bulk('removed')">標記已不在櫃上</button>
          <button class="chip" :disabled="!pickedKeys.length" @click="bulk('clear')">清除標記</button>
        </div>
        <table class="tbl">
          <thead><tr>
            <th v-if="dc.can_edit">選</th>
            <SortTh k="no" :active="lKey" :dir="lDir" @sort="lToggle">項次</SortTh>
            <SortTh k="name" :active="lKey" :dir="lDir" @sort="lToggle">設備</SortTh>
            <SortTh k="rack" :active="lKey" :dir="lDir" @sort="lToggle">機櫃</SortTh>
            <SortTh k="u_text" :active="lKey" :dir="lDir" @sort="lToggle">U</SortTh>
            <SortTh k="model" :active="lKey" :dir="lDir" @sort="lToggle">型號（原值）</SortTh>
            <SortTh k="spec_model" :active="lKey" :dir="lDir" @sort="lToggle">對到的規格</SortTh>
            <SortTh k="type_text" :active="lKey" :dir="lDir" @sort="lToggle">類型</SortTh>
            <SortTh k="ip_text" :active="lKey" :dir="lDir" @sort="lToggle">IP／網段</SortTh>
            <SortTh k="state_label" :active="lKey" :dir="lDir" @sort="lToggle">狀態</SortTh>
            <SortTh k="sys_text" :active="lKey" :dir="lDir" @sort="lToggle">系統</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="d in lSorted" :key="d.key">
              <td v-if="dc.can_edit"><input v-model="picked[d.key]" type="checkbox"></td>
              <td class="mono">V{{ d.no }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(d.asset_serial)}`" class="lnk">{{ d.name }}</NuxtLink></td>
              <td><button v-if="d.rack" class="linkbtn" @click="openRack(d.rack)">{{ d.rack }}</button></td>
              <td class="mono">{{ d.u_text }}<span v-if="d.position_corrected" class="fix">現場更正</span><button v-if="dc.can_edit" class="linkbtn" @click="editingPos = d.key">更正位置</button></td><td>{{ d.model || '（空白）' }}</td><td>{{ d.spec_model || '—' }}</td>
              <td>{{ d.type_text }}<span class="muted">・{{ d.sys_level_label ?? '未分級' }}</span></td>
              <td class="small"><div v-for="x in d.ips ?? []" :key="x.ip">{{ ipSegText(x) }}</div><span v-if="!(d.ips ?? []).length" class="muted">—</span></td>
              <td>{{ d.kind === 'removed' ? '已不在櫃上' : (d.state_label || '—') }}<span v-if="d.unobserved" class="muted">・未觀察到</span><span v-if="d.asset_status" class="muted">（{{ d.asset_status }}）</span></td><td>{{ d.sys_text || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 上架助手 -->
      <div v-if="view === 'place'" class="panel">
        <div class="chips">
          <label>型號 <input v-model="pf.model" list="spec-models" class="kw sm2" placeholder="從規格表選或自己打"></label>
          <datalist id="spec-models"><option v-for="s in specs" :key="s.model" :value="s.model" /></datalist>
          <label>U <input v-model.number="pf.u" type="number" min="1" max="20" class="num"></label>
          <label>瓦數 <input v-model.number="pf.w" type="number" min="0" class="num w"></label>
          <label>kg <input v-model.number="pf.kg" type="number" min="0" class="num w"></label>
          <label>台數 <input v-model.number="pf.qty" type="number" min="1" max="50" class="num"></label>
          <span class="muted">機房：</span>
          <label v-for="r in rooms.filter(x => x.racks.length)" :key="r.room" class="cb"><input v-model="pf.rooms" type="checkbox" :value="r.room">{{ r.room }}</label>
          <button class="tb" :disabled="placing" @click="findRacks">{{ placing ? '找機櫃中…' : '找機櫃' }}</button>
          <InfoNote>找有「連續空 U」放得下的機櫃，用電和重量放了之後不超過上限（系統設定 rack_budget；用電預設每櫃 5000 W，重量沒設就顯示「未設定上限」）。在用（含未觀察到）、關機未下架、待確認、預留都算佔 U。櫃裡有設備沒寫 U 位時，那櫃的空 U 不確定，排在後面並寫原因。最緊的連續空間（放了剩最少）排最前面。</InfoNote>
        </div>
        <table v-if="sugs.length" class="tbl">
          <thead><tr>
            <SortTh k="no" :active="pKey" :dir="pDir" @sort="pToggle">項次</SortTh>
            <SortTh k="room" :active="pKey" :dir="pDir" @sort="pToggle">機房</SortTh>
            <SortTh k="rack" :active="pKey" :dir="pDir" @sort="pToggle">機櫃</SortTh>
            <SortTh k="result" :active="pKey" :dir="pDir" @sort="pToggle">結果</SortTh>
            <SortTh k="fit_count" :active="pKey" :dir="pDir" @sort="pToggle">放得下</SortTh>
            <SortTh k="u_text" :active="pKey" :dir="pDir" @sort="pToggle">建議 U</SortTh>
            <SortTh k="best_run" :active="pKey" :dir="pDir" @sort="pToggle">最緊連續空</SortTh>
            <SortTh k="free_u" :active="pKey" :dir="pDir" @sort="pToggle">空 U</SortTh>
            <SortTh k="w_after" :active="pKey" :dir="pDir" @sort="pToggle">放了之後用電</SortTh>
            <SortTh k="kg_after" :active="pKey" :dir="pDir" @sort="pToggle">放了之後重量</SortTh>
            <SortTh k="reason_text" :active="pKey" :dir="pDir" @sort="pToggle">原因</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="s in pSorted" :key="s.room + s.rack" :class="{ no: !s.fits }">
              <td class="mono">P{{ s.no }}</td><td>{{ s.room }}</td>
              <td><button class="linkbtn" @click="openRack(s.rack, s.room)">{{ s.rack }}</button></td>
              <td>{{ s.result }}</td><td class="mono">{{ s.fit_count }}</td><td class="mono">{{ s.u_text }}</td>
              <td class="mono">{{ s.best_run }}</td><td class="mono">{{ s.free_u }}{{ s.uncertain_u ? '（不確定）' : '' }}</td>
              <td class="mono small">{{ s.w_text }}</td><td class="mono small">{{ s.kg_text }}</td>
              <td class="small">{{ s.reason_text || '—' }}
                <button v-if="dc.can_edit && s.suggest_u_start" class="linkbtn" @click="choose(s)">選這櫃</button></td>
            </tr>
          </tbody>
        </table>
        <div v-if="chosen" class="glass choose">
          <div class="chips"><b>{{ chosen.room }} {{ chosen.rack }}</b>
            <label>起始 U <input v-model.number="pick.u_start" type="number" min="1" max="60" class="num"></label>
            <span class="muted">（{{ pf.u }}U）</span>
            <label>備註 <input v-model="pick.note" class="kw sm2" placeholder="例：10/23 上架"></label>
            <button class="chip" @click="chosen = null">關閉</button>
          </div>
          <div class="chips">
            <b>登記既有設備</b><input v-model="pick.q" class="kw sm2" placeholder="資產序號／主機名" @keyup.enter="searchAssets">
            <button class="chip" @click="searchAssets">找</button>
            <span v-for="a in assets" :key="a.key" class="asset">{{ a.name }}<span class="muted">（{{ a.asset_serial }}，現在 {{ a.room }} {{ a.rack || '無機櫃' }} {{ a.u_start ? `${a.u_start}U` : '' }}）</span>
              <button class="linkbtn" @click="registerAsset(a)">登記到這裡</button></span>
          </div>
          <div class="chips">
            <b>預留（還沒登記的設備）</b>
            <input v-model="pick.label" class="kw sm2" placeholder="名稱，例：新防火牆">
            <label>到期 <input v-model="pick.expires" type="date"></label>
            <button class="chip on" @click="reserveIt">預留</button>
          </div>
        </div>
      </div>

      <!-- 預留中 -->
      <div v-if="view === 'rsv'" class="panel">
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="vKey" :dir="vDir" @sort="vToggle">項次</SortTh>
            <SortTh k="room" :active="vKey" :dir="vDir" @sort="vToggle">機房</SortTh>
            <SortTh k="rack" :active="vKey" :dir="vDir" @sort="vToggle">機櫃</SortTh>
            <SortTh k="u_start" :active="vKey" :dir="vDir" @sort="vToggle">U</SortTh>
            <SortTh k="label" :active="vKey" :dir="vDir" @sort="vToggle">名稱</SortTh>
            <SortTh k="model" :active="vKey" :dir="vDir" @sort="vToggle">型號</SortTh>
            <SortTh k="expires_at" :active="vKey" :dir="vDir" @sort="vToggle">到期</SortTh>
            <SortTh k="st" :active="vKey" :dir="vDir" @sort="vToggle">狀態</SortTh>
            <SortTh k="created_by" :active="vKey" :dir="vDir" @sort="vToggle">預留人</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in vSorted" :key="r.id" :class="{ no: r.expired }">
              <td class="mono">Y{{ r.no }}</td><td>{{ r.room }}</td>
              <td><button class="linkbtn" @click="openRack(r.rack, r.room)">{{ r.rack }}</button></td>
              <td class="mono">{{ r.u_text }}</td><td>{{ r.label }}</td><td>{{ r.model || '—' }}</td>
              <td class="mono">{{ r.expires_at }}</td><td>{{ r.st }}</td>
              <td>{{ r.created_by || '—' }} <button v-if="dc.can_edit" class="linkbtn" @click="cancelRsv(r)">取消</button></td>
            </tr>
            <tr v-if="!vSorted.length"><td colspan="9" class="muted">沒有預留</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 資料品質 -->
      <div v-if="view === 'dq'" class="panel">
        <div class="chips">
          <button v-for="(lab, k) in DQ_LABEL" :key="k" class="chip" :class="{ on: dqKind === k }" @click="dqKind = String(k)">{{ lab }} {{ dc.data_quality[k]?.length ?? 0 }}</button>
          <InfoNote>這些是 CIA 資料要修的地方，這裡只列出來、不改資料。「型號寫 VM 但填了機櫃」不放進機櫃圖；亂碼與籠統描述的型號算「未知規格」不算電；「規格表裡找不到」的型號可以在型號規格表頁補上。</InfoNote>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="qKey" :dir="qDir" @sort="qToggle">項次</SortTh>
            <SortTh k="name" :active="qKey" :dir="qDir" @sort="qToggle">設備</SortTh>
            <SortTh k="room" :active="qKey" :dir="qDir" @sort="qToggle">機房</SortTh>
            <SortTh k="rack_raw" :active="qKey" :dir="qDir" @sort="qToggle">機櫃欄原值</SortTh>
            <SortTh k="model" :active="qKey" :dir="qDir" @sort="qToggle">型號原值</SortTh>
            <SortTh k="sys_text" :active="qKey" :dir="qDir" @sort="qToggle">系統</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="d in qSorted" :key="d.key">
              <td class="mono">Q{{ d.no }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(d.asset_serial)}`" class="lnk">{{ d.name }}</NuxtLink></td>
              <td>{{ d.room }}</td><td>{{ d.rack_raw || '' }}</td><td>{{ d.model || '（空白）' }}</td><td>{{ d.sys_text || '—' }}</td>
            </tr>
            <tr v-if="!qSorted.length"><td colspan="6" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </div>

      <!-- B. 機櫃正面 -->
      <div v-if="view === 'front'" class="panel">
        <p v-if="!selRack" class="muted">在平面圖或熱度格點一個機櫃。</p>
        <RackElevation v-else :room="roomName" :rack="selRack" :api-id="system" :hi-keys="litKeys" :color-by="colorBy" :show-overlap="showOverlap" />
      </div>

      <!-- 無機櫃資料 -->
      <div v-if="view === 'norack'" class="panel">
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="nKey" :dir="nDir" @sort="nToggle">項次</SortTh>
            <SortTh k="name" :active="nKey" :dir="nDir" @sort="nToggle">設備</SortTh>
            <SortTh k="ip" :active="nKey" :dir="nDir" @sort="nToggle">IP</SortTh>
            <SortTh k="model" :active="nKey" :dir="nDir" @sort="nToggle">機型</SortTh>
            <SortTh k="rack_raw" :active="nKey" :dir="nDir" @sort="nToggle">機櫃欄原值</SortTh>
            <SortTh k="sys_text" :active="nKey" :dir="nDir" @sort="nToggle">系統</SortTh>
            <SortTh k="exist_sort" :active="nKey" :dir="nDir" @sort="nToggle">存在</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="d in nSorted" :key="d.key">
              <td class="mono">N{{ d.no }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(d.asset_serial)}`" class="lnk">{{ d.name }}</NuxtLink></td>
              <td class="mono">{{ d.ip || '—' }}</td>
              <td>{{ d.model || '—' }}</td>
              <td>{{ d.rack_raw || '（空白）' }}</td>
              <td>
                <NuxtLink v-for="a in d.api_ids" :key="a" :to="{ path: '/topology', query: { system: a } }" class="lnk sp1">{{ a }}</NuxtLink>
                <span v-if="!d.api_ids.length" class="muted">—</span>
              </td>
              <td><span class="xb" :class="{ off: !d.seen }" :title="d.exist_label">{{ d.exist_level ? (EXIST_SHORT[d.exist_level] ?? d.exist_level) : '不明' }} {{ d.exist_score ?? '' }}</span></td>
            </tr>
            <tr v-if="!nSorted.length"><td colspan="7" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 搜尋結果 -->
      <div v-if="view === 'search' && res" class="panel">
        <div v-if="angle === 'system' && res.angles.system.systems.length" class="chips">
          <span class="muted">符合的系統：</span>
          <button v-for="s in res.angles.system.systems" :key="s.api_id" class="chip" :class="{ on: system === s.api_id }" @click="setSystem(s.api_id)">{{ s.api_id }} {{ s.name }}</button>
        </div>
        <div class="chips">
          <a class="plink" :href="`/print/labels?q=${encodeURIComponent(q)}&angle=${angle}`" target="_blank">把這些設備印成標籤</a>
          <span class="muted">含符合設備的機櫃：</span>
          <button v-for="r in res.angles[angle].racks" :key="r.room + r.rack" class="chip" @click="openRack(r.rack, r.room)">{{ r.room }} {{ r.rack }}（{{ r.count }}）</button>
          <button v-for="r in (res.angles[angle].racks_via_vm ?? [])" :key="'v' + r.room + r.rack" class="chip" title="這個系統的 VM 跑在這櫃的 ESXi 主機上" @click="openRack(r.rack, r.room)">{{ r.room }} {{ r.rack }}（經 ESXi {{ r.count }}）</button>
          <span v-if="!res.angles[angle].racks.length && !(res.angles[angle].racks_via_vm ?? []).length" class="muted">沒有（符合的設備都沒有可判讀的機櫃資料）</span>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="mKey" :dir="mDir" @sort="mToggle">項次</SortTh>
            <SortTh k="room" :active="mKey" :dir="mDir" @sort="mToggle">機房</SortTh>
            <SortTh k="rack_text" :active="mKey" :dir="mDir" @sort="mToggle">機櫃</SortTh>
            <SortTh k="u_start" :active="mKey" :dir="mDir" @sort="mToggle">U</SortTh>
            <SortTh k="name" :active="mKey" :dir="mDir" @sort="mToggle">設備</SortTh>
            <SortTh k="model" :active="mKey" :dir="mDir" @sort="mToggle">機型</SortTh>
            <SortTh k="sys_text" :active="mKey" :dir="mDir" @sort="mToggle">系統</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="m in mSorted" :key="m.key">
              <td class="mono">S{{ m.no }}</td>
              <td>{{ m.room }}</td>
              <td>
                <button v-if="m.rack" class="linkbtn" @click="openRack(m.rack, m.room)">{{ m.rack }}</button>
                <span v-else class="muted">{{ m.rack_text }}</span>
              </td>
              <td class="mono">{{ m.u_text }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(m.asset_serial)}`" class="lnk">{{ m.name }}</NuxtLink></td>
              <td>{{ m.model || '—' }}</td>
              <td>{{ m.sys_text || '—' }}</td>
            </tr>
            <tr v-if="!mSorted.length"><td colspan="7" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dcpage { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 10px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.chip { font-family: inherit; font-size: 12.5px; padding: 4px 12px; border-radius: 20px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip:disabled { opacity: .45; cursor: default; }
.toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.toolbar .sp { flex: 1; }
.kw { font-family: inherit; font-size: 13px; padding: 8px 10px; min-width: 340px; border: 1px solid var(--border-strong); border-radius: 9px; background: var(--card); color: var(--ink); }
.sysbox { font-size: 12.5px; color: var(--ink-aux); display: flex; gap: 6px; align-items: center; }
.sysbox select { font-family: inherit; font-size: 12.5px; padding: 4px 6px; }
.summary { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; font-size: 13px; color: var(--ink-soft); margin-bottom: 8px; }
.plink { font-size: 12.5px; color: var(--link); text-decoration: none; border-bottom: 1px dotted var(--link); }
.summary.pw { font-size: 12.5px; }
.summary.pw b { font-family: var(--disp); color: var(--ink); }
.fix { display: inline-block; margin-left: 4px; font-size: 10px; padding: 0 5px; border-radius: 6px; border: 1px dashed var(--brand); color: var(--brand-dark); }
.kw.sm2 { min-width: 180px; padding: 5px 8px; font-size: 12.5px; }
.num { width: 60px; font-family: inherit; font-size: 12.5px; padding: 4px 6px; }
.num.w { width: 76px; }
.cb { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; }
.tbl tr.no td { color: var(--muted); }
.choose { padding: 10px 12px; margin-top: 10px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--sub); }
.asset { display: inline-flex; gap: 6px; align-items: center; margin-right: 10px; font-size: 12.5px; }
.small { font-size: 11.5px; }
.hi-tag { font-size: 12px; padding: 2px 9px; border-radius: 10px; background: var(--mint-deep); color: var(--brand-dark); }
.tabs { display: flex; align-items: center; gap: 4px; margin: 0 0 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab { padding: 8px 16px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; font-family: inherit; }
.tab:hover { color: var(--ink); }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.ptile { position: relative; display: inline-flex; flex-direction: column; gap: 4px; align-items: stretch; min-width: 76px; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink-soft); font-family: var(--disp); font-size: 12px; cursor: pointer; }
.ptile:hover { border-color: var(--brand-hover); }
.ptile.hi { background: var(--mint-deep); border-color: var(--brand); color: var(--brand-dark); font-weight: 600; }
.ptile.sel { outline: 3px solid var(--brand-dark); outline-offset: 1px; }
.ptile.dim { opacity: .5; }
.pt-top { display: flex; align-items: center; justify-content: center; }
.pbar { display: flex; height: 5px; border-radius: 3px; overflow: hidden; background: var(--line); }
.pbar i { display: block; height: 100%; }
.ovb { font-size: 10px; padding: 0 5px; border-radius: 6px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); font-family: inherit; font-weight: 400; }
.chip.miss { border-style: dashed; color: var(--warn-text); cursor: default; }
.chip .sw { margin: 0 5px 0 0; }
.sp2 { width: 18px; }
.legendrow { margin-bottom: 12px; }
.cnt { display: inline-block; margin-left: 6px; min-width: 18px; padding: 0 5px; border-radius: 9px; background: var(--brand); color: var(--card); font-size: 11px; font-weight: 600; text-align: center; }
.fronts { display: flex; gap: 14px; overflow-x: auto; align-items: flex-start; margin-top: 10px; padding-bottom: 6px; }
.front1 { flex: none; width: 240px; }
.legend { margin-top: 6px; font-size: 12px; }
.sw { display: inline-block; width: 12px; height: 10px; border-radius: 2px; background: var(--chart-gray); vertical-align: middle; margin: 0 4px 0 8px; }
.sw.hi { background: var(--brand); }
.heatrow { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.rowname { width: 44px; font-size: 12px; color: var(--ink-aux); }
.heat { width: 66px; height: 54px; border-radius: 8px; border: 1px solid var(--border); display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: pointer; font-family: inherit; }
.heat b { font-family: var(--disp); font-size: 17px; }
.heat span { font-size: 10.5px; }
.heat.hi { outline: 3px solid var(--brand-dark); outline-offset: 1px; }
.tb { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 12px; border-radius: 9px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); cursor: pointer; margin: 6px 0 10px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 7px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 7px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); }
.err { color: var(--bad); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.sp1 { margin-right: 6px; }
.linkbtn { background: none; border: 0; padding: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; }
.linkbtn:hover { text-decoration: underline; }
.xb { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 10px; background: var(--good-soft); color: var(--brand-dark); white-space: nowrap; }
.xb.off { background: var(--warn-soft); color: var(--warn-text); }
</style>
