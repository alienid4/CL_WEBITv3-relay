<script setup lang="ts">
import { candidateRangeText, freeUText, reserveNeedsConfirm, upperBoundText } from '~/utils/rackFree'
// 5.1 機房平面圖（2.x 第二批）：舊 4-1／4-5 機房架構圖搬進 v2 外框。頁籤：平面圖｜機櫃正面｜電力容量｜上架規劃｜位置更正｜Excel 對照。
// 用電與空間只在這頁（10.2 分布統計只放台數並連到這裡）。資料與舊頁同一支 /api/racks/datacenter（rack_layout.py），
// 台數口徑不另算：機房台數＝舊頁同一個 device_count；verify_numbers check_datacenter_v2 釘住它與對帳橋實體設備一致。
// 搜尋、圖例上色（2.007.53）與網段篩選（2.011.x）都在平面圖頁籤。
definePageMeta({ layout: 'v2' })
interface Power { unobserved_keys: string[]; removed_keys: string[]; unknown_keys: string[]; off_keys: string[]; pending_keys: string[]
  w: number; amps: number | null; btu_h: number; volts: number; kg: number; if_all_on_w: number; used_u: number; free_u: number; unplaced_count?: number; known_count?: number; unknown_count?: number; largest_free_u?: number
  unknown_count: number; off_count: number; pending_count: number; unobserved_count: number; removed_count: number }
interface Tile { rack: string; prefix: string; device_count: number; system_count: number; used_u: number; height_u: number; keys: string[]; all_keys: string[]; api_ids: string[]; overlap_count: number; power: Power; excel_only?: boolean; excel_cells?: number }
interface Room { room: string; device_count: number; racked: number; racks: Tile[]; no_rack: any[]; vm_excluded: number; power: Power }
interface DC { can_edit?: boolean; rooms: Room[]; devices: Record<string, any>; total_racked: number; total_devices: number }

const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const natCmp = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }

const dc = ref<DC | null>(null)
const loadErr = ref('')
async function loadDc() { try { dc.value = await apiFetch<DC>('/api/racks/datacenter'); loadErr.value = '' } catch (e: any) { loadErr.value = `機房資料載入失敗：${errMsg(e)}` } }
await loadDc()
const rooms = computed(() => (dc.value?.rooms ?? []).filter(r => r.racks.length > 0))   // 有機櫃的機房（無機櫃資料的機房沒有平面圖可看）
const roomName = computed(() => (rooms.value.some(r => r.room === q('room')) ? q('room') : (rooms.value.find(r => r.racked > 0)?.room ?? rooms.value[0]?.room ?? '')))
const room = computed(() => rooms.value.find(r => r.room === roomName.value) ?? null)

const TABS = computed(() => [{ k: 'plan', label: '平面圖' }, { k: 'front', label: `機櫃正面${q('rack') ? '：' + q('rack') : ''}` }, { k: 'power', label: '電力容量' }, { k: 'place', label: '上架規劃' }, { k: 'fix', label: '位置更正' }, { k: 'excel', label: 'Excel 對照' }])
const tab = computed({ get: () => (TABS.value.some(t => t.k === q('tab')) ? q('tab') : 'plan'), set: (v: string) => go({ tab: v, dl: undefined, dr: undefined }) })
const rackUrl = (rack: string) => `/v2/datacenter?room=${encodeURIComponent(roomName.value)}&tab=front&rack=${encodeURIComponent(rack)}`
const drillUrl = (kind: string, rack = '') => `/v2/datacenter?room=${encodeURIComponent(roomName.value)}&tab=${tab.value}&dl=${kind}${rack ? '&dr=' + encodeURIComponent(rack) : ''}`

// ===== 平面圖 =====
const planRows = computed(() => {
  const m = new Map<string, Tile[]>()
  for (const t of room.value?.racks ?? []) { if (!m.has(t.prefix)) m.set(t.prefix, []); m.get(t.prefix)!.push(t) }
  return [...m.entries()].map(([prefix, tiles]) => ({ prefix, tiles: tiles.sort((a, b) => natCmp(a.rack, b.rack)) })).sort((a, b) => natCmp(a.prefix, b.prefix))
})
// ===== 搜尋與上色（2.007.53，舊版「搜尋與上色」翻新）：多個詞用空白隔開（每個詞都要中，不分大小寫）、顏色依設備類型／系統等級／環境、
// 點圖例只亮那一類；亮起的櫃標出命中台數，其餘變淡，下面列出命中的設備。資料＝同一份 /api/racks/datacenter 的設備欄位。=====
const sq = ref('')
const colorBy = ref<'heat' | 'type' | 'level' | 'env'>('heat')
const legendPick = ref('')
const segPick = ref('')
watch(colorBy, () => { legendPick.value = '' })
const devOf = (k: string) => dc.value?.devices[k]
const catOf = (d: any): string => (colorBy.value === 'type' ? d?.machine_type_label : colorBy.value === 'level' ? d?.sys_level_label : (d?.envs?.[0] || '未填')) || '未知'
const PALETTE = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)']
const roomKeys = computed(() => (room.value?.racks ?? []).flatMap(t => t.keys))
const legend = computed(() => {
  if (colorBy.value === 'heat') return []
  const c = new Map<string, number>()
  for (const k of roomKeys.value) { const n = catOf(devOf(k)); c.set(n, (c.get(n) ?? 0) + 1) }
  return [...c.entries()].sort((a, b) => b[1] - a[1]).map(([name, n], i) => ({ name, n, color: i < PALETTE.length ? PALETTE[i] : 'var(--chart-gray)' }))
})
// 網段篩選（2.011.x，舊版 4-1 的「網段」）：每台的全部 IP（登記＋收集到的每張網卡）對網段配置表，任一個 IP 落在這段就算；一台可在好幾段
const segOptions = computed(() => {
  const m = new Map<string, { cidr: string; purpose: string | null; vlan: string | null; keys: Set<string> }>()
  for (const k of roomKeys.value) {
    for (const x of devOf(k)?.ips ?? []) {
      if (!x.cidr) continue
      if (!m.has(x.cidr)) m.set(x.cidr, { cidr: x.cidr, purpose: x.purpose, vlan: x.vlan, keys: new Set() })
      m.get(x.cidr)!.keys.add(k)
    }
  }
  return [...m.values()].sort((a, b) => natCmp(a.cidr, b.cidr))
})
watch(roomName, () => { if (segPick.value && !segOptions.value.some(o => o.cidr === segPick.value)) segPick.value = '' })
const segKeys = computed<Set<string> | null>(() => (segPick.value ? (segOptions.value.find(o => o.cidr === segPick.value)?.keys ?? new Set()) : null))
const colorOf = (name: string) => legend.value.find(l => l.name === name)?.color ?? 'var(--chart-gray)'
const terms = computed(() => sq.value.trim().toLowerCase().split(/\s+/).filter(Boolean))
const textOf = (d: any) => [d?.name, d?.ip, d?.model, d?.rack_raw, d?.asset_serial, (d?.api_ids ?? []).join(' '), d?.machine_type_label, d?.sys_level_label, (d?.envs ?? []).join(' ')].filter(Boolean).join(' ').toLowerCase()
const litKeys = computed<Set<string> | null>(() => {
  const t = terms.value
  if (!t.length && !legendPick.value && !segKeys.value) return null
  const out = new Set<string>()
  for (const k of roomKeys.value) {
    const d = devOf(k)
    if (segKeys.value && !segKeys.value.has(k)) continue
    if (t.length) { const tx = textOf(d); if (!t.every(w => tx.includes(w))) continue }
    if (legendPick.value && catOf(d) !== legendPick.value) continue
    out.add(k)
  }
  return out
})
const litCount = (t: Tile) => (litKeys.value ? t.keys.filter(k => litKeys.value!.has(k)).length : t.device_count)
function tileStyle(t: Tile) {
  const lit = litKeys.value
  const dim = lit && !t.keys.some(k => lit.has(k)) ? { opacity: '.25' } : {}
  if (colorBy.value === 'heat' || t.excel_only) return { ...heat(t), ...dim }
  const c = new Map<string, number>()
  for (const k of t.keys) { const n = catOf(devOf(k)); c.set(n, (c.get(n) ?? 0) + 1) }
  const top = [...c.entries()].sort((a, b) => b[1] - a[1])[0]
  return { background: top ? `color-mix(in srgb, ${colorOf(top[0])} 70%, var(--card))` : 'var(--card)', color: 'var(--ink)', ...dim }
}
const hitRows = computed(() => {
  if (!litKeys.value) return []
  return [...litKeys.value].map(k => devOf(k)).filter(Boolean).map((d: any) => ({ ...d, rack_text: d.rack ?? '—', u_text: d.u_start ? (d.u_start === d.u_end ? `${d.u_start}U` : `${d.u_start}-${d.u_end}U`) : '', sys: (d.api_ids ?? []).join(', '), cat: catOf(d) }))
    .sort((a: any, b: any) => natCmp(String(a.rack_text), String(b.rack_text)) || (a.u_start ?? 0) - (b.u_start ?? 0))
})
function heat(t: Tile) {
  const pct = Math.round(5 + (t.height_u ? Math.min(1, t.used_u / t.height_u) : 0) * 85)
  return { background: `color-mix(in srgb, var(--brand) ${pct}%, var(--card))`, color: pct > 50 ? 'var(--card)' : 'var(--ink)' }
}
const rackRows = computed(() => (room.value?.racks ?? []).filter(t => !t.excel_only).slice().sort((a, b) => natCmp(a.rack, b.rack)).map(t => ({
  rack: t.rack, prefix: t.prefix, device_count: t.device_count, records: t.all_keys.length, system_count: t.system_count,
  // 有在用設備、已用 U 卻是 0＝U 位沒登錄（找碴 C13），不可以當成使用率 0%
  u_none: t.device_count > 0 && !t.used_u,
  used_u: t.device_count > 0 && !t.used_u ? null : t.used_u, free_u: t.power.free_u,
  // 可用 U 的顯示字：有未定位設備或 U 位未登錄時只能給上限（Codex 29 號：總覽表不可顯示確定的 42）；排序仍用 free_u 數值
  free_text: freeUText(t.power, t.device_count > 0 && !t.used_u),
  ratio: t.device_count > 0 && !t.used_u ? null : (t.height_u ? Math.round(t.used_u * 100 / t.height_u) : 0), overlap: t.overlap_count })))
const uNone = computed(() => rackRows.value.filter(r => r.u_none).length)

// ===== 下鑽：設備清單（同一份 datacenter 資料，數字＝清單筆數）=====
const DRILL: Record<string, string> = { active: '在用設備', all: '全部登記', unknown: '未知規格', off: '關機未下架', pending: '待確認', unobs: '在用但未觀察到', removed: '已不在櫃上', norack: '無機櫃資料' }
function keysOf(kind: string, rack: string): string[] {
  const r = room.value
  if (!r) return []
  if (kind === 'norack') return r.no_rack.map((d: any) => d.key)
  const tiles = rack ? r.racks.filter(t => t.rack === rack) : r.racks
  const p = (t: { power: Power }) => ({ unknown: t.power.unknown_keys, off: t.power.off_keys, pending: t.power.pending_keys, unobs: t.power.unobserved_keys, removed: t.power.removed_keys } as Record<string, string[]>)[kind] ?? []
  if (kind === 'active') return tiles.flatMap(t => t.keys)
  if (kind === 'all') return tiles.flatMap(t => t.all_keys)
  return rack ? tiles.flatMap(p) : p(r)
}
const drill = computed(() => (q('dl') && DRILL[q('dl')] ? { label: `${roomName.value}${q('dr') ? ' ' + q('dr') : ''} ${DRILL[q('dl')]}`, keys: keysOf(q('dl'), q('dr')) } : null))
const drillRows = computed(() => {
  if (!drill.value) return []
  if (q('dl') === 'norack') return (room.value?.no_rack ?? []).map((d: any) => ({ ...d, rack_text: d.rack_raw || '（空白）', u_text: '', state_text: d.no_rack_reason ?? '—', sys: (d.api_ids ?? []).join(', ') }))
  return drill.value.keys.map(k => dc.value?.devices[k] ?? { key: k, name: k }).map((d: any) => ({ ...d, rack_text: d.rack ?? '—',
    u_text: d.u_start ? (d.u_start === d.u_end ? `${d.u_start}U` : `${d.u_start}-${d.u_end}U`) : '', state_text: d.kind === 'removed' ? '已不在櫃上' : (d.state_label || '—'), sys: (d.api_ids ?? []).join(', ') }))
})
const DRILL_COLS = [{ k: 'name', label: '設備', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'ip', label: 'IP', mono: true },
  { k: 'rack_text', label: '機櫃' }, { k: 'u_text', label: 'U', mono: true }, { k: 'model', label: '型號（原值）' }, { k: 'state_text', label: '狀態' }, { k: 'sys', label: '業務系統' }]

// ===== 電力容量 =====
const powerRows = computed(() => (room.value?.racks ?? []).filter(t => !t.excel_only).slice().sort((a, b) => natCmp(a.rack, b.rack)).map(t => ({
  rack: t.rack, devices: t.device_count, w: t.power.w, amps: t.power.amps ?? '—', btu_h: t.power.btu_h, kg: t.power.kg, used_u: t.power.used_u, free_u: t.power.free_u, free_text: t.power.unplaced_count ? `≤${t.power.free_u}（另 ${t.power.unplaced_count} 台未定位）` : t.power.free_u,
  largest_free_u: t.power.largest_free_u ?? 0, largest_text: upperBoundText(t.power.largest_free_u ?? 0, t.power.unplaced_count ?? 0), unknown: t.power.unknown_count, unobs: t.power.unobserved_count, dormant: t.power.off_count + t.power.pending_count, if_on: t.power.if_all_on_w })))

// ===== 上架規劃 =====
const specs = ref<any[]>([])
const pf = reactive({ model: '', u: 1, w: null as number | null, kg: null as number | null, qty: 1, rooms: [] as string[] })
const sugs = ref<any[]>([])
const placing = ref(false)
const rsvs = ref<any[]>([])
async function loadRsv() { try { rsvs.value = await apiFetch<any[]>('/api/racks/reservations') } catch (e: any) { showToast(`預留清單載入失敗：${errMsg(e)}`, 'error') } }
async function openPlace() {
  if (!pf.rooms.length && roomName.value) pf.rooms = [roomName.value]
  if (!specs.value.length) { try { specs.value = (await apiFetch<any>('/api/model-specs')).specs.filter((s: any) => !s.is_generic) } catch (e: any) { showToast(`型號規格載入失敗：${errMsg(e)}`, 'error') } }
  loadRsv()
}
watch(() => pf.model, (m) => { const s = specs.value.find(x => x.model === m); if (s) { pf.u = s.rack_u ?? pf.u; pf.w = s.psu_max_w; pf.kg = s.weight_kg } })
async function findRacks() {
  placing.value = true
  try {
    const r = await apiFetch<any>('/api/racks/placement', { query: { u: pf.u, qty: pf.qty, w: pf.w ?? undefined, kg: pf.kg ?? undefined, rooms: pf.rooms.join(',') || undefined } })
    sugs.value = r.racks
  } catch (e: any) { showToast(`找機櫃失敗：${errMsg(e)}`, 'error') } finally { placing.value = false }
}
const sugRows = computed(() => sugs.value.map(s => ({ ...s, result: (s.fits_state === 'unsure' ? '需確認' : (s.fits ? '放得下' : '放不下')) + (s.legacy_reserved_count ? '（已保守扣除舊鍵預留）' : '') + (s.site_pending ? '（分點不明，待確認）' : ''), free_text: s.uncertain_u ? `≤${s.free_u}（另 ${s.unplaced_count ?? s.no_u_count} 台未定位）` : s.free_u, u_text: candidateRangeText(s.suggest_u_start ? `${s.suggest_u_start}-${s.suggest_u_end}U` : '—', s.unplaced_count ?? s.no_u_count ?? 0), fit_text: upperBoundText(s.fit_count, s.unplaced_count ?? s.no_u_count ?? 0), need_confirm: reserveNeedsConfirm(s),
  w_text: `${s.w_after} W（額定）${s.w_budget ? ` / ${s.w_budget}` : ''}`, kg_text: `${s.kg_after} kg${s.kg_budget ? ` / ${s.kg_budget}` : '（未設定上限）'}`, reason_text: (s.reasons ?? []).join('；') })))
// W221-001：未定位／結果需確認的櫃，預留前須勾選「現場確認空位」並填備註（後端同樣檢核，409）
const rsvConfirm = ref(false)
const rsvNote = ref('')
const rsvBlocked = (s: any) => !!s.need_confirm && !(rsvConfirm.value && rsvNote.value.trim())
async function reserve(s: any) {
  if (rsvBlocked(s)) return
  const label = window.prompt(`預留 ${s.room} ${s.rack} ${s.u_text}：名稱（必填）`, pf.model || '')
  if (!label) return
  const exp = new Date(Date.now() + 30 * 86400e3).toISOString().slice(0, 10)
  const expires = window.prompt('到期日（YYYY-MM-DD）', exp)
  if (!expires) return
  try {
    const r = await guardedFetch<any>('/api/racks/reservations', { method: 'POST', body: { room: s.room, rack: s.rack, u_start: s.suggest_u_start, u_height: pf.u, label, expires_at: expires, model: pf.model || null, w: pf.w, kg: pf.kg, note: rsvNote.value.trim() || null, confirm_verified: s.need_confirm ? true : undefined } })
    showToast(r.collisions?.length ? `已預留，但與 ${r.collisions.length} 項重疊` : '已預留', r.collisions?.length ? 'warn' : 'success')
    await loadRsv(); await findRacks()
  } catch (e: any) { showToast(`預留失敗：${errMsg(e)}`, 'error') }
}
async function cancelRsv(id: number) {
  const r = rsvs.value.find(x => x.id === id)
  if (!r || !window.confirm(`取消預留「${r.label}」（${r.room} ${r.rack} ${r.u_start}U）？`)) return
  try { await guardedFetch(`/api/racks/reservations/${id}/cancel`, { method: 'PUT' }); await loadRsv() } catch (e: any) { showToast(`取消失敗：${errMsg(e)}`, 'error') }
}
const rsvRows = computed(() => rsvs.value.map(r => ({ ...r, u_text: `${r.u_start}${r.u_end !== r.u_start ? `-${r.u_end}` : ''}U`, st: r.expired ? '已過期' : '預留中' })))

// ===== 位置更正 =====
const pending = ref<any[] | null>(null)
const moves = ref<any[]>([])
async function loadFix() {
  try { pending.value = (await apiFetch<any>('/api/racks/position/pending-cia')).rows } catch (e: any) { pending.value = []; showToast(`CIA 待更正載入失敗：${errMsg(e)}`, 'error') }
  try { moves.value = (await apiFetch<any>('/api/racks/position/moves', { query: { room: roomName.value, limit: 50 } })).items } catch (e: any) { showToast(`最近搬移載入失敗：${errMsg(e)}`, 'error') }
}
const pendingRows = computed(() => (pending.value ?? []).filter(r => !roomName.value || r['原機房'] === roomName.value || r['新機房'] === roomName.value)
  .map(r => ({ serial: r['資產序號'], name: r['主機名稱'], kind: r['更正類型'], from: `${r['原Rack'] ?? ''} ${r['原U位'] ?? ''}`.trim(), to: `${r['新Rack'] ?? ''} ${r['新U位'] ?? ''}`.trim(), by: r['盤點人'], on: r['現場盤點日期'] })))
const moveRows = computed(() => moves.value.map(m => ({ ...m, st: m.undone ? `已復原 ${m.undone_at?.slice(0, 16) ?? ''}` : '' })))

watch([tab, roomName], () => { if (tab.value === 'place') openPlace(); else if (tab.value === 'fix') loadFix() }, { immediate: true })
const frontRack = computed(() => (room.value?.racks.some(t => t.rack === q('rack')) ? q('rack') : ''))
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">5.1 機房平面圖</h1></template>
    <template #conclusion>
      <p v-if="loadErr" class="err">{{ loadErr }}</p>
      <div v-else class="bar">
        <button v-for="r in rooms" :key="r.room" type="button" class="chip" :class="{ on: r.room === roomName }" @click="go({ room: r.room, rack: undefined, dl: undefined, dr: undefined })">{{ r.room }} {{ r.racked }} 台</button>
        <InfoNote>機房按鈕上的數字＝在機櫃的實體設備台數（同一台多筆登記算一台，退役不算；VM 不在機櫃裡不列入），等於 2.1 主機查詢「實體設備」頁籤中位於該機房機櫃的台數。「無機櫃資料」是機櫃欄空白或無法判讀的登記，不是實體設備口徑。</InfoNote>
      </div>
      <div v-if="room" class="bar sum">
        <span>機櫃 <b>{{ room.racks.filter(t => !t.excel_only).length }}</b><template v-if="room.racks.some(t => t.excel_only)">　另有 Excel 獨有 <b>{{ room.racks.filter(t => t.excel_only).length }}</b> 櫃（系統沒登記）</template></span>
        <NuxtLink :to="drillUrl('active')">在機櫃實體設備 {{ room.racked }} 台</NuxtLink>
        <NuxtLink :to="drillUrl('norack')">無機櫃資料 {{ room.no_rack.length }} 台</NuxtLink>
        <NuxtLink :to="`/v2/datacenter?room=${encodeURIComponent(roomName)}&tab=power`">額定用電估算 <b>{{ (room.power.w / 1000).toFixed(1) }} kW</b></NuxtLink>
        <NuxtLink :to="`/v2/datacenter?room=${encodeURIComponent(roomName)}&tab=power`">已用 {{ room.power.used_u }}U／可用 {{ room.power.unplaced_count ? `≤${room.power.free_u}U（另 ${room.power.unplaced_count} 台未定位）` : `${room.power.free_u}U` }}</NuxtLink>
      </div>
    </template>
    <template #tab>
      <template v-if="room && drill">
        <div class="bar"><NuxtLink :to="`/v2/datacenter?room=${encodeURIComponent(roomName)}&tab=${tab}`">‹ 返回</NuxtLink><b>{{ drill.label }}</b><span class="muted">{{ drillRows.length }} 台</span></div>
        <V2Table :rows="drillRows" prefix="V" :max="3000" :cols="DRILL_COLS" />
      </template>
      <p v-else-if="!room" class="muted">無機房資料</p>

      <template v-else-if="tab === 'plan'">
        <div class="bar">
          <input v-model="sq" class="in wide" type="search" placeholder="搜尋設備（名稱、IP、型號、系統代號…，多個詞用空白）" aria-label="搜尋設備">
          <select v-model="colorBy" aria-label="顏色依">
            <option value="heat">顏色依：用量</option><option value="type">顏色依：設備類型</option>
            <option value="level">顏色依：系統等級</option><option value="env">顏色依：環境</option>
          </select>
          <select v-model="segPick" aria-label="網段">
            <option value="">網段：全部</option>
            <option v-for="o in segOptions" :key="o.cidr" :value="o.cidr">{{ o.cidr }}{{ o.purpose ? ' ' + o.purpose : '' }}{{ o.vlan ? ' VLAN ' + o.vlan : '' }}（{{ o.keys.size }} 台）</option>
          </select>
          <button v-if="sq || legendPick || segPick" type="button" class="chip" @click="sq = ''; legendPick = ''; segPick = ''">清除</button>
          <span v-if="litKeys" class="muted">命中 {{ litKeys.size }} 台・{{ room.racks.filter(t => t.keys.some(k => litKeys!.has(k))).length }} 櫃</span>
          <InfoNote>網段：每台的全部 IP（登記的 IP＋收集到的每張網卡）對網段配置表，任一個 IP 落在這段就算，一台可以在好幾段。搜尋、網段、圖例可同時用，亮的是三者都符合的設備。多個詞要全部符合才算命中（名稱、IP、型號、機櫃原文、資產編號、系統代號、設備類型、系統等級、環境）。顏色依設備類型／系統等級／環境時，每櫃用台數最多的那一類上色；點下面的圖例只亮那一類。亮起的櫃數字＝命中台數，其餘櫃變淡。</InfoNote>
        </div>
        <div v-if="legend.length" class="bar legend">
          <button v-for="l in legend" :key="l.name" type="button" class="chip lg" :class="{ on: legendPick === l.name }" @click="legendPick = legendPick === l.name ? '' : l.name"><i :style="{ background: l.color }" />{{ l.name }} {{ l.n }}</button>
        </div>
        <div v-for="row in planRows" :key="row.prefix" class="prow">
          <span class="rowname">{{ row.prefix }} 排</span>
          <NuxtLink v-for="t in row.tiles" :key="t.rack" class="tile" :class="{ unone: t.device_count > 0 && !t.used_u, exonly: t.excel_only }" :style="tileStyle(t)" :to="rackUrl(t.rack)" :title="t.excel_only ? `${t.rack}：Excel 盤點有 ${t.excel_cells} 格，系統（CIA）沒有登記這個櫃` : t.device_count > 0 && !t.used_u ? `${t.rack}：${t.device_count} 台、U 位無資料` : `${t.rack}：${t.device_count} 台、已用 ${t.used_u}U／${t.height_u}U`">
            <b>{{ t.excel_only ? 'X' : litCount(t) }}</b><span>{{ t.rack }}</span>
          </NuxtLink>
        </div>
        <p v-if="!planRows.length" class="muted">此機房無可判讀的機櫃資料</p>
        <template v-if="litKeys">
          <h3 class="h3">命中的設備 {{ hitRows.length }} 台</h3>
          <V2Table :rows="hitRows" prefix="H" :max="200" empty="沒有符合的設備"
                   :cols="[{ k: 'name', label: '設備', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'ip', label: 'IP', mono: true }, { k: 'rack_text', label: '機櫃', link: (r: any) => rackUrl(r.rack_text) }, { k: 'u_text', label: 'U', mono: true }, { k: 'model', label: '型號（原值）' }, { k: 'cat', label: '分類' }, { k: 'sys', label: '業務系統' }]" />
        </template>
        <p class="muted small"><span v-if="uNone" class="unone-t">U 位無資料 {{ uNone }} 櫃</span><InfoNote>依機櫃編號排列（非實際樓面位置）；色深＝已用 U 比例；數字＝在用台數。虛線框＝櫃內有在用設備但沒有登錄 U 位（已用 U、使用率顯示無資料，不算 0%）。</InfoNote></p>
        <V2Table :rows="rackRows" prefix="T" :max="1000"
                 :cols="[{ k: 'rack', label: '機櫃', link: (r: any) => rackUrl(r.rack) }, { k: 'prefix', label: '排' }, { k: 'device_count', label: '在用（台）', mono: true, link: (r: any) => drillUrl('active', r.rack) }, { k: 'records', label: '登記（筆）', mono: true, link: (r: any) => drillUrl('all', r.rack) }, { k: 'system_count', label: '業務系統', mono: true }, { k: 'used_u', label: '已用 U', mono: true, text: (r: any) => (r.u_none ? '無資料（U 位未登錄）' : r.used_u) }, { k: 'free_u', label: '可用 U', mono: true, text: (r: any) => r.free_text }, { k: 'ratio', label: '使用率 %', mono: true, text: (r: any) => (r.u_none ? '無資料' : r.ratio) }, { k: 'overlap', label: '位置重疊（台）', mono: true, link: (r: any) => (r.overlap ? `/v2/datacenter?room=${encodeURIComponent(roomName)}&tab=fix` : null) }]" />
      </template>

      <template v-else-if="tab === 'front'">
        <div class="bar">
          <select :value="frontRack" @change="go({ rack: ($event.target as HTMLSelectElement).value || undefined })">
            <option value="">選擇機櫃</option>
            <option v-for="t in [...room.racks].sort((a, b) => natCmp(a.rack, b.rack))" :key="t.rack" :value="t.rack">{{ t.rack }}（{{ t.device_count }} 台）</option>
          </select>
          <NuxtLink v-if="frontRack" :to="drillUrl('active', frontRack)">在用設備清單</NuxtLink>
        </div>
        <p v-if="!frontRack" class="muted">請選擇機櫃，或在平面圖點選機櫃。</p>
        <template v-else>
          <RackElevation :key="roomName + frontRack" :room="roomName" :rack="frontRack" :u-px="40" @changed="loadDc" />
          <OverlapGroups :room="roomName" :rack="frontRack" compact @changed="loadDc" />
        </template>
      </template>

      <template v-else-if="tab === 'power'">
        <dl class="kv">
          <dt>額定用電估算</dt><dd>{{ (room.power.w / 1000).toFixed(1) }} kW（涵蓋 {{ room.power.known_count }}／{{ (room.power.known_count ?? 0) + (room.power.unknown_count ?? 0) }} 台；{{ room.power.amps ?? '—' }} A @{{ room.power.volts }}V、{{ room.power.btu_h.toLocaleString() }} BTU/h）</dd>
          <dt>若全部開機</dt><dd>+{{ (room.power.if_all_on_w / 1000).toFixed(1) }} kW</dd>
          <dt>重量</dt><dd>{{ room.power.kg.toLocaleString() }} kg</dd>
          <dt>空間</dt><dd>已用 {{ room.power.used_u }}U／可用 {{ room.power.unplaced_count ? `≤${room.power.free_u}U（另 ${room.power.unplaced_count} 台未定位）` : `${room.power.free_u}U` }}</dd>
          <dt>未計入用電</dt><dd><NuxtLink :to="drillUrl('unknown')">未知規格 {{ room.power.unknown_count }} 台</NuxtLink>　<NuxtLink :to="drillUrl('off')">關機未下架 {{ room.power.off_count }} 台</NuxtLink>　<NuxtLink :to="drillUrl('pending')">待確認 {{ room.power.pending_count }} 台</NuxtLink></dd>
        </dl>
        <InfoNote label="算法">額定、不是實際耗電（全站沒有實測來源）。用電＝每台在用設備一顆 PSU 的額定瓦數（型號規格表）；對不到規格的列「未知規格」。電流＝瓦數 ÷ 機房電壓；散熱＝瓦數 × 3.412。關機未下架、待確認佔 U 與重量照算、用電算 0，另列「若全部開機」。</InfoNote>
        <V2Table :rows="powerRows" prefix="W" :max="1000"
                 :cols="[{ k: 'rack', label: '機櫃', link: (r: any) => rackUrl(r.rack) }, { k: 'devices', label: '在用（台）', mono: true, link: (r: any) => drillUrl('active', r.rack) }, { k: 'w', label: '額定 W（估算）', mono: true }, { k: 'amps', label: 'A', mono: true }, { k: 'btu_h', label: 'BTU/h', mono: true }, { k: 'kg', label: 'kg', mono: true }, { k: 'used_u', label: '已用 U', mono: true }, { k: 'free_text', label: '可用 U', mono: true }, { k: 'largest_text', label: '最大連續可用 U', mono: true }, { k: 'unknown', label: '未知規格（台）', mono: true, link: (r: any) => (r.unknown ? drillUrl('unknown', r.rack) : null) }, { k: 'unobs', label: '未觀察到（台）', mono: true, link: (r: any) => (r.unobs ? drillUrl('unobs', r.rack) : null) }, { k: 'dormant', label: '關機／待確認（台）', mono: true }, { k: 'if_on', label: '若全開 +W', mono: true }]" />
      </template>

      <template v-else-if="tab === 'place'">
        <div class="bar">
          <label>型號 <input v-model="pf.model" list="v2-spec-models" class="in" placeholder="型號規格表"></label>
          <datalist id="v2-spec-models"><option v-for="s in specs" :key="s.model" :value="s.model" /></datalist>
          <label>U <input v-model.number="pf.u" type="number" min="1" max="20" class="num"></label>
          <label>瓦數 <input v-model.number="pf.w" type="number" min="0" class="num"></label>
          <label>kg <input v-model.number="pf.kg" type="number" min="0" class="num"></label>
          <label>台數 <input v-model.number="pf.qty" type="number" min="1" max="50" class="num"></label>
          <label v-for="r in rooms.filter(x => x.racks.length)" :key="r.room"><input v-model="pf.rooms" type="checkbox" :value="r.room">{{ r.room }}</label>
          <button type="button" class="btn" :disabled="placing" @click="findRacks">{{ placing ? '查詢中…' : '查詢可上架機櫃' }}</button>
          <InfoNote>找有連續可用 U 且放入後用電、重量不超過上限（系統設定 rack_budget）的機櫃；在用、關機未下架、待確認、預留都算佔用。最緊的連續空間排最前面。</InfoNote>
        </div>
        <V2Table v-if="sugRows.length" :rows="sugRows" prefix="P" :max="1000"
                 :cols="[{ k: 'room', label: '機房' }, { k: 'rack', label: '機櫃', link: (r: any) => `/v2/datacenter?room=${encodeURIComponent(r.room)}&tab=front&rack=${encodeURIComponent(r.rack)}` }, { k: 'result', label: '結果' }, { k: 'fit_text', label: '可放（台）', mono: true }, { k: 'u_text', label: '建議 U', mono: true }, { k: 'free_text', label: '可用 U', mono: true }, { k: 'w_text', label: '放入後額定用電', mono: true }, { k: 'kg_text', label: '放入後重量', mono: true }, { k: 'reason_text', label: '原因' }]" />
        <div v-if="dc?.can_edit && sugRows.some(s => s.fits)" class="bar">
          <span class="muted">預留：</span>
          <label v-if="sugRows.some(x => x.fits && x.need_confirm)" class="muted"><input v-model="rsvConfirm" type="checkbox"> 我已現場確認這段 U 位確實是空的</label>
          <input v-if="sugRows.some(x => x.fits && x.need_confirm)" v-model="rsvNote" class="kw" placeholder="備註（確認人／日期，未定位的櫃必填）">
          <button v-for="s in sugRows.filter(x => x.fits).slice(0, 10)" :key="s.room + s.rack" type="button" class="chip" :disabled="rsvBlocked(s)" :title="rsvBlocked(s) ? '空位未核實：請勾選現場確認並填備註' : ''" @click="reserve(s)">{{ s.room }} {{ s.rack }} {{ s.u_text }}</button>
        </div>
        <h3 class="h3">預留中 {{ rsvRows.filter(r => !r.expired).length }}</h3>
        <V2Table :rows="rsvRows" prefix="Y" empty="無預留"
                 :cols="[{ k: 'room', label: '機房' }, { k: 'rack', label: '機櫃', link: (r: any) => `/v2/datacenter?room=${encodeURIComponent(r.room)}&tab=front&rack=${encodeURIComponent(r.rack)}` }, { k: 'u_text', label: 'U', mono: true }, { k: 'label', label: '名稱' }, { k: 'model', label: '型號' }, { k: 'expires_at', label: '到期', mono: true }, { k: 'st', label: '狀態' }, { k: 'created_by', label: '預留人' }]" />
        <div v-if="dc?.can_edit && rsvRows.some(r => !r.expired)" class="bar">
          <span class="muted">取消預留：</span>
          <button v-for="r in rsvRows.filter(x => !x.expired)" :key="r.id" type="button" class="chip" @click="cancelRsv(r.id)">{{ r.label }}（{{ r.rack }} {{ r.u_text }}）</button>
        </div>
      </template>

      <template v-else-if="tab === 'fix'">
        <OverlapGroups :room="roomName" @changed="loadDc" />
        <h3 class="h3">CIA 待更正（{{ roomName }}）{{ pendingRows.length }} 筆</h3>
        <div class="bar"><a class="btn2" :href="`${apiBase}/api/racks/position/pending-cia.xlsx`">匯出 CIA 待更正（全部機房）</a>
          <InfoNote>現場盤點、位置更正、標記已不在櫃上的結果，CIA 尚未同步的清單；匯出後交 CIA 窗口更正。</InfoNote></div>
        <V2Table :rows="pendingRows" prefix="C" empty="無待更正" :max="2000"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => (r.serial ? `/v2/machine/${encodeURIComponent(r.serial)}` : null) }, { k: 'serial', label: '資產編號', mono: true }, { k: 'kind', label: '更正類型' }, { k: 'from', label: '原位置' }, { k: 'to', label: '新位置' }, { k: 'by', label: '盤點人' }, { k: 'on', label: '盤點日期', mono: true }]" />
        <h3 class="h3">最近搬移 {{ moveRows.length }}</h3>
        <V2Table :rows="moveRows" prefix="M" empty="無搬移紀錄"
                 :cols="[{ k: 'at', label: '時間', mono: true }, { k: 'name', label: '設備' }, { k: 'from_text', label: '原位置' }, { k: 'to_text', label: '新位置' }, { k: 'count', label: '筆數', mono: true }, { k: 'by', label: '操作人' }, { k: 'st', label: '狀態' }]" />
        <p class="muted small">復原搬移在機櫃正面頁籤操作。<NuxtLink :to="drillUrl('norack')">無機櫃資料 {{ room.no_rack.length }} 台</NuxtLink></p>
      </template>

      <template v-else-if="tab === 'excel'">
        <V2RackExcelCompare :rooms="(dc?.rooms ?? []).map(r => r.room)" :room="roomName" @changed="loadDc" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sum a { color: var(--link); }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; text-decoration: none; }
.prow { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.rowname { width: 56px; font-size: 12px; color: var(--ink-aux); }
.tile { display: flex; flex-direction: column; align-items: center; justify-content: center; width: 64px; height: 48px; border-radius: 8px; border: 1px solid var(--border); text-decoration: none; font-size: 11px; }
.tile b { font-family: var(--disp); font-size: 15px; }
.kv { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 12px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; } .kv a { color: var(--link); }
.in { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; width: 160px; }
.num { width: 64px; font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.muted { color: var(--muted); font-size: 13px; } .small { font-size: 12px; }
.tile.unone { outline: 2px dashed var(--warn); outline-offset: -2px; }
.tile.exonly { outline: 2px dashed var(--chart-gray); outline-offset: -2px; background: var(--sub); color: var(--ink-soft); }
.in.wide { width: 320px; }
.chip.lg { display: inline-flex; gap: 5px; align-items: center; }
.chip.lg i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.legend { margin-top: -2px; }
.unone-t { color: var(--warn-text); margin-right: 6px; }
.err { color: var(--bad); }
</style>
