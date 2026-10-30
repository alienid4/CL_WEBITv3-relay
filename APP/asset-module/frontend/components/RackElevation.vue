<script setup lang="ts">
import type { ColorBy, IpSeg } from '~/composables/useRackColors'
// 擬真機櫃正面：深色機櫃外框、每台設備一塊面板（佔它的 U 高）、存在可信度燈號、本系統設備高亮。
// 系統主機部署圖的「機櫃」頁簽與 4-5 機房架構圖共用（資料：/api/racks/detail，rack_layout.rack_detail）。
// 顏色：外框用 main.css 的 --rack-*（實物顏色），其餘用語意變數。
// hiKeys：要亮的設備（4-1 平面圖的搜尋命中）；compact：只畫機櫃正面（平面圖下方並排用，不帶側邊表格）。
// colorBy：面板左邊色條依機器類型／系統等級（4-1 平面圖的「顏色依」）；U 位重疊的設備左右並排、標「重疊」。
const props = defineProps<{ room: string; rack: string; apiId?: string | null; hiKeys?: string[]; compact?: boolean; colorBy?: ColorBy | null; showOverlap?: boolean; statusByKey?: Record<string, string>; uPx?: number }>()
// uPx：每 U 幾像素（預設 16；事件應變機櫃圖用 26，字才看得清楚）
// statusByKey：事件應變「機櫃圖」用，受影響設備依最近一輪連線測試上色（down 紅／up 綠／untested 灰）
// 機型、序號一律顯示成全大寫、空白換行整理成一個空白（使用者 2026-10-28；既有資料不改，只統一顯示）
const up = (x?: string | null) => (x || '').split(/\s+/).filter(Boolean).join(' ').toUpperCase()
const hiSet = computed(() => new Set(props.hiKeys ?? []))

interface Dev {
  key: string; asset_serial: string; name: string; ip: string | null; model: string | null
  u_start: number | null; u_end: number | null; rack_raw: string | null
  exist_level: string | null; exist_score: number | null; exist_label: string; seen: boolean
  systems: { api_id: string; name: string; guess?: boolean }[]; mine: boolean
  state: 'active' | 'off' | 'pending' | null; state_label: string | null; asset_status: string | null
  w: number | null; kg: number | null; spec_model: string | null; override: string | null; unobserved: boolean
  position_corrected?: boolean; position_note?: string | null; position_by?: string | null; position_at?: string | null
  cia_position?: { room: string; rack: string | null; u_start: number | null; u_end: number | null; raw: string | null } | null
  machine_type?: string; machine_type_label?: string; sys_level?: string; sys_level_label?: string
  ips?: IpSeg[]; envs?: string[]; os?: string | null; overlap_with?: string[]
  // 管理卡（iDRAC／OOB 另外登記的那一筆）：併進主機面板顯示，不畫成第二台（後端 rack_layout.mgmt_pairs）
  mgmt_of?: string | null; mgmt?: { key: string; name: string; ip: string | null; confirmed: boolean; kind?: string; rule?: string }[]
  // 位置重疊處理成「合併為同一台」：併進代表那台的面板（只影響畫面）
  merged_into?: string | null; merged?: { key: string; name: string; ip: string | null; role?: string }[]
  merge_reason?: string; merge_auto?: boolean; hw_serials?: string[]; merge_keys?: string[]; merge_decision_id?: number | null
  // 2026-10-26 一個箱子＝一台：box 本體／member 同箱其他登記／port 併進主機的管理口／orphan 找不到本體／suspect 疑似登記錯誤
  box_role?: 'box' | 'member' | 'port' | 'orphan' | 'suspect'; role_label?: string; orphan_port?: boolean
  mgmt_kind?: string | null; suspect?: boolean; extra_ranges?: [number, number][]
  // 現場更正（面板雙擊改的機型／IP／序號／備註；CIA 不動，進 CIA 待更正）
  remark?: string | null
  field_edits?: Record<string, { value: string; cia: string | null; by: string | null; at: string | null; note: string | null }>
}
// 面板上寫「型號＋IP」（例：DELL R760／IP 10.9x.xx.xx）——同仁找機器、對 Excel 都是用這兩個；
// 沒有 IP（或 0.0.0.0）才退回硬體序號（service tag）→ 資產編號 → 序號不明。主機名、系統放到右邊表格與滑過提示。
function panelLabel(d: Dev) {
  const model = up(d.model) || '機型不明'
  if (d.ip && d.ip !== '0.0.0.0') return { model, tag: `IP ${d.ip}`, kind: 'ip' }
  const sn = (d.hw_serials ?? [])[0]
  if (sn) return { model, tag: sn, kind: '' }
  if (d.asset_serial && !/^(DYN|VC|AUTO)-/i.test(d.asset_serial)) return { model, tag: d.asset_serial, kind: '資產編號' }
  return { model, tag: '序號不明', kind: 'none' }
}
interface Power {
  active_count: number; off_count: number; pending_count: number; w: number; amps: number | null; btu_h: number
  volts: number; kg: number; if_all_on_w: number; used_u: number; free_u: number; unplaced_count?: number; largest_free_u: number
  known_count: number; unknown_count: number; unknown_keys: string[]; unobserved_count: number; unobserved_keys: string[]
  system_level: { model: string; records: number; w: number | null; kg: number | null; u: number | null; inferred?: boolean; rule?: string; sl_min?: number }[]
  vendor_move: string[]
}
interface Detail {
  room: string; rack: string; floor: string | null; height_u: number; devices: Dev[]
  systems: { api_id: string; name: string; device_count: number }[]
  mine_count: number; others_count: number; no_u_count: number; power: Power; can_edit?: boolean
  reservations?: { id: number; label: string; u_start: number; u_end: number; expires_at: string; expired: boolean; created_by: string | null; note: string | null }[]
  last_audit?: Record<string, string>
  legacy_reservations?: { id: number; label: string; u_start: number; u_end: number; expires_at: string; expired: boolean; legacy_note: string }[]
  legacy_audits?: { audit_id: number; book_count: number; legacy_note: string }[]
  key_warnings?: string[]
  excel_only?: boolean
  // Excel 層（人工盤點的機櫃圖）：系統面板還沒畫到的格，畫成虛線框；不算台數、不算用電
  excel?: { run: { run_id: number; filename: string; created_at: string } | null; total: number
            cells: { id: number; key?: string | null; u_start: number; u_end: number; label: string; status: string; sys_name: string | null; sys_ip: string | null; sys_now: string | null; blocker_text: string; suggest: string; sys_serial?: string; sys_asset?: string; sys_systems?: string[] }[]; by_key?: Record<string, string> }
  box_count?: number; suspect?: { keys: string[]; reason: string; names: string[] }[]; orphan_ports?: string[]; room_racks?: string[]
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const det = ref<Detail | null>(null)
const err = ref('')
const loading = ref(false)
async function load() {
  loading.value = true
  err.value = ''
  try {
    det.value = await apiFetch<Detail>('/api/racks/detail', { query: { room: props.room, rack: props.rack, api_id: props.apiId || undefined } })
  } catch (e: any) {
    det.value = null
    err.value = e?.data?.detail ?? e?.message ?? '未知錯誤'
  } finally {
    loading.value = false
  }
}
watch(() => [props.room, props.rack, props.apiId], load, { immediate: true })

const U_PX = props.uPx ?? 16
const big = U_PX >= 22
const placed = computed(() => (det.value?.devices ?? []).filter(d => d.u_start && !d.mgmt_of && !d.merged_into))
const unplaced = computed(() => (det.value?.devices ?? []).filter(d => !d.u_start && !d.mgmt_of && !d.merged_into))
const slotBg = computed(() => ({ height: `${heightU.value * U_PX}px`,
  background: `repeating-linear-gradient(to bottom, var(--rack-slot) 0 ${U_PX - 1}px, var(--rack-frame) ${U_PX - 1}px ${U_PX}px)` }))
const heightU = computed(() => Math.max(det.value?.height_u ?? 42, ...placed.value.map(d => d.u_end ?? 0), ...(det.value?.excel?.cells ?? []).map(c => c.u_end)))
function rTop(r: { u_end: number }) { return (heightU.value - r.u_end) * U_PX }
function rHgt(r: { u_start: number; u_end: number }) { return (r.u_end - r.u_start + 1) * U_PX - 2 }
function top(d: Dev) { return (heightU.value - (d.u_end ?? 0)) * U_PX }
// U 位重疊：同一群（互相重疊連起來的）左右分欄
const lanes = computed(() => {
  const byKey = new Map(placed.value.map(d => [d.key, d]))
  const out: Record<string, { lane: number; n: number; keys: string[] }> = {}
  const done = new Set<string>()
  for (const d of placed.value) {
    if (done.has(d.key) || !(d.overlap_with ?? []).length) continue
    const grp: Dev[] = []
    const stack = [d.key]
    while (stack.length) {
      const k = stack.pop()!
      if (done.has(k) || !byKey.has(k)) continue
      done.add(k)
      grp.push(byKey.get(k)!)
      stack.push(...(byKey.get(k)!.overlap_with ?? []))
    }
    grp.sort((a, b) => (a.u_start ?? 0) - (b.u_start ?? 0) || a.name.localeCompare(b.name))
    const lane: Record<string, number> = {}
    for (const g of grp) {
      const used = new Set((g.overlap_with ?? []).map(k => lane[k]).filter(x => x !== undefined))
      let i = 0
      while (used.has(i)) i++
      lane[g.key] = i
    }
    const n = Math.max(...Object.values(lane)) + 1
    const keys = grp.map(g => g.key)
    for (const g of grp) out[g.key] = { lane: lane[g.key], n, keys }
  }
  return out
})
function faceStyle(d: Dev) {
  const st: Record<string, string> = { top: `${top(d)}px`, height: `${hgt(d)}px` }
  const l = lanes.value[d.key]
  if (l) { st.left = `calc(${(l.lane * 100) / l.n}% + 2px)`; st.width = `calc(${100 / l.n}% - 4px)`; st.right = 'auto' }
  if (props.colorBy) st.borderLeft = `5px solid ${rackCatColor(props.colorBy, rackCatOf(props.colorBy, d))}`
  return st
}
// Excel 一格的文字拆成「備註／機型／IP」：有換行或「 / 」時，前段是備註（例：內網ACI 1-A）、後段是機型＋IP；
// 沒有分隔時整段當機型（例：HPE Server 10.99.164.1）。IP 取第一個 IPv4。
function xlParts(c: { label: string; sys_serial?: string }) {
  const t = oneLine(c.label)
  const ip = (t.match(/\b\d{1,3}(?:\.\d{1,3}){3}\b/) ?? [''])[0]
  const rest = t.replace(ip, '').replace(/\(\s*\)/g, '').trim()
  const parts = rest.split(/\s+\/\s+/).map(x => x.trim()).filter(Boolean)
  const p = { ...(parts.length > 1 ? { note: parts[0], model: parts.slice(1).join(' / '), ip } : { note: '', model: rest, ip }), serial: '' }
  // Excel 常把「機型-序號」寫成同一串，要拆開（序號是機器上的標籤，機型是型號，兩件事）：
  // ① 系統對得到這台、知道它的序號 → 直接把那串從機型拆出去
  const sn = c.sys_serial
  if (sn && p.model.toUpperCase().includes(sn.toUpperCase())) {
    p.model = p.model.replace(new RegExp(sn.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'), '').replace(/[-_／/\s]+$/, '').trim()
    p.serial = sn
    return p
  }
  // ② 系統沒有這台 → 照 IBM 序號的樣子拆：括號裡 6～8 碼英數（有字有數，例：(8445A7W)），
  //   或機型後面接「-」的 7 碼英數（例：IBM_8969_F24-788108A、FS5300-33U-78F21DX）。VIP、SECSVR 這類不是序號。
  const hasLD = (x: string) => /[A-Z]/i.test(x) && /\d/.test(x)
  const paren = p.model.match(/\(([A-Z0-9]{6,8})\)/i)
  if (paren && hasLD(paren[1]) && !/^(VIP|SECSVR)/i.test(paren[1])) {
    p.serial = paren[1].toUpperCase()
    p.model = p.model.replace(paren[0], '').replace(/\s{2,}/g, ' ').trim()
    return p
  }
  const tail = p.model.match(/[-_]([A-Z0-9]{7})(?=$|[\s(（])/i)
  if (tail && hasLD(tail[1]) && /\d/.test(p.model.slice(0, tail.index))) {
    p.serial = tail[1].toUpperCase()
    p.model = (p.model.slice(0, tail.index) + p.model.slice((tail.index ?? 0) + tail[0].length)).replace(/\s{2,}/g, ' ').trim()
  }
  return p
}
// 重要的排前面：系統完全沒有 → 要人看的（待確認／位置不同）→ 只差位置的
const XL_RANK: Record<string, number> = { MISSING: 0, BLOCKED: 1, MOVED: 1, OTHERROOM: 2, NOPOS: 3 }
const xlSorted = computed(() => [...(det.value?.excel?.cells ?? [])].sort((a, b) => (XL_RANK[a.status] ?? 9) - (XL_RANK[b.status] ?? 9) || b.u_end - a.u_end))
const xlCount = computed(() => {
  const n = { MISSING: 0, CHECK: 0, NOPOS: 0 }
  for (const c of det.value?.excel?.cells ?? []) { if (c.status === 'MISSING') n.MISSING++; else if (c.status === 'NOPOS' || c.status === 'OTHERROOM') n.NOPOS++; else n.CHECK++ }
  return n
})
const xlDrawn = computed(() => (det.value?.excel?.cells ?? []).filter(c => !xlBlocker(c)))
// 這格的 U 位已經有系統面板（例如管理卡）：不重疊畫，缺口清單裡照列並註明
function xlBlocker(c: { u_start: number; u_end: number }) {
  return placed.value.find(d => d.u_start != null && d.u_end != null && d.u_start <= c.u_end && d.u_end >= c.u_start) ?? null
}
// 系統面板的「確認」欄：只寫要人注意的事，沒有就留空白
function sysFlag(d: Dev) {
  if (d.orphan_port) return '管理介面・找不到本體'
  if (suspectSet.value.has(d.key)) return '疑似登記錯誤'
  if (props.showOverlap && lanes.value[d.key]) return '重疊'
  if (d.position_corrected) return '現場更正'
  return ''
}
// 序號＝硬體序號（service tag）；資產編號是另一欄，不拿來頂替，否則同一個號碼會出現兩次
function serialOf(d: Dev) { return up((d.hw_serials ?? [])[0]) }
function mgmtTitle(d: Dev) { return (d.mgmt ?? []).map(g => `${g.name}　${mgmtText(g)}`).join(String.fromCharCode(10)) }
// 這台系統設備跟 Excel 對的結果：有 Excel 對照才有；Excel 這個櫃有寫、但沒指到這台 → ABSENT（系統說在這櫃、Excel 沒寫）
function exStatus(d: Dev) {
  const ex = det.value?.excel
  if (!ex || !ex.total) return ''
  return ex.by_key?.[d.key] ?? 'ABSENT'
}
const EX_OK_TEXT: Record<string, string> = { OK: '一致', POSONLY: '名稱不同', ABSENT: 'Excel 沒寫' }
function exText(d: Dev) { const st = exStatus(d); return st ? (EX_OK_TEXT[st] ?? XL_TEXT[st] ?? st) : '' }
const showMore = ref(false)
const moreCols = computed(() => showMore.value || props.compact)
const XL_TEXT: Record<string, string> = { MISSING: 'Excel 有・系統無', NOPOS: '系統未登位置', BLOCKED: '待人工確認', MOVED: '位置不同', OTHERROOM: '系統登在別間' }
const oneLine = (t: string) => (t || '').split('\n').map(x => x.trim()).filter(Boolean).join(' ')
function xlTitle(c: NonNullable<Detail['excel']>['cells'][number]) {
  const L = [`Excel：${oneLine(c.label)}`, XL_TEXT[c.status] ?? c.status]
  if (c.sys_name) L.push(`系統：${c.sys_name}　${c.sys_ip || ''}　${c.sys_now || ''}`)
  if (c.blocker_text) L.push(c.blocker_text)
  L.push(c.suggest)
  return L.join('\n')
}
function faceTitle(d: Dev) {
  const ips = (d.ips ?? []).map(ipSegText).join('\n')
  const ov = (d.overlap_with ?? []).length ? `\n位置重疊：${(d.overlap_with ?? []).map(nameOf).join('、')}` : ''
  const twin = d.merged?.length ? `\n${d.merge_auto ? '系統判斷為同一台' : '人工合併為同一台'}（${d.merge_reason || ''}）：${[d.name, ...d.merged.map(x => x.name)].join('、')}` : ''
  return `${d.name}　${d.ip || ''}　${d.model || ''}　${d.systems.map(s => s.api_id).join(', ')}${twin}\n類型：${d.machine_type_label ?? '不明'}　等級：${d.sys_level_label ?? '未分級'}${ips ? '\n' + ips : ''}${ov}${d.position_corrected ? '\n' + ciaText(d) : ''}`
}
const overlapPairs = computed(() => {
  const seen = new Set<string>()
  const out: { a: Dev; b: Dev }[] = []
  for (const d of placed.value) {
    for (const k of d.overlap_with ?? []) {
      const id = [d.key, k].sort().join('|')
      const o = placed.value.find(x => x.key === k)
      if (seen.has(id) || !o) continue
      seen.add(id)
      out.push({ a: d, b: o })
    }
  }
  return out
})
// 機櫃欄一格寫好幾段 U（「27U、30-31U」）：另一段也畫一塊（同一筆登記、不能拖）
const extraFaces = computed(() => placed.value.flatMap(d => (d.extra_ranges ?? []).map(([a, b], i) => ({ d, a, b, i }))))
function uText(d: Dev) { return d.u_start ? (d.u_start === d.u_end ? `${d.u_start}U` : `${d.u_start}-${d.u_end}U`) : '' }
function hgt(d: Dev) { return ((d.u_end ?? 0) - (d.u_start ?? 0) + 1) * U_PX - 2 }
const uMarks = computed(() => Array.from({ length: heightU.value }, (_, i) => heightU.value - i))

// 側邊清單（可排序、有項次）；可只看「同櫃其他設備」
const onlyOthers = ref(false)
// 管理口（ILOM／iDRAC…）預設不列一列（寫在主機那列「管理 IP」）；打開「顯示管理介面」才列出來
const showMgmt = ref(false)
// 推定同一台：本體那列點開才列出同箱其他登記
const openBox = ref<Set<string>>(new Set())
function toggleBox(k: string) { const s2 = new Set(openBox.value); s2.has(k) ? s2.delete(k) : s2.add(k); openBox.value = s2 }
const suspectSet = computed(() => new Set((det.value?.suspect ?? []).flatMap(g => g.keys)))
// 「N 筆登記」：一次攤開全部（管理口＋同一箱的其他筆），再點收回
function toggleAllRecords() {
  const on = !(showMgmt.value && openBox.value.size)
  showMgmt.value = on
  openBox.value = on ? new Set((det.value?.devices ?? []).filter(x => x.merged?.length).map(x => x.key)) : new Set()
}
function mgmtText(g: { ip: string | null; kind?: string }) { return `管理 IP：${g.ip || '無 IP'}（${g.kind || 'OOB'}）` }
const listRows = computed(() => (det.value?.devices ?? []).map((d, i) => ({
  ...d, no: i + 1, u_text: d.u_start ? (d.u_start === d.u_end ? `${d.u_start}U` : `${d.u_start}-${d.u_end}U`) : '',
  sys_text: d.systems.map(s => s.api_id).join(', '), exist_sort: d.exist_score ?? -1, mine_text: d.mine ? '本系統' : '',
  w_sort: d.w ?? -1, state_text: d.state_label ?? '', type_text: d.machine_type_label ?? '不明',
  ip_text: (d.ips ?? []).map(x => x.ip).join(', '), audit_text: det.value?.last_audit?.[d.key] ?? '',
})).filter(d => (!onlyOthers.value || !d.mine) && (!unknownOnly.value || unknownSet.value.has(d.key))
  && (!unobsOnly.value || unobsSet.value.has(d.key))
  && (showMgmt.value || !d.mgmt_of)
  && (!d.merged_into || openBox.value.has(d.merged_into))))
const { sortKey, sortDir, toggle, sorted } = useSort(listRows, 'no')
const EXIST_SHORT: Record<string, string> = {
  onboarded: '已納管', inventory: '匯入清單有', collect_stale: '收集較舊', scan: '掃描活著', cia_only: '只有登記', none: '無證據',
}
const unknownOnly = ref(false)
const unobsOnly = ref(false)
const unknownSet = computed(() => new Set(det.value?.power.unknown_keys ?? []))
const unobsSet = computed(() => new Set(det.value?.power.unobserved_keys ?? []))
// 批次標記（現場盤點結果，限管理員）
const picked = ref<Record<string, boolean>>({})
const pickedKeys = computed(() => Object.keys(picked.value).filter(k => picked.value[k]))
const BULK_LABEL: Record<string, string> = { off: '標記已關機未下架', removed: '標記已不在櫃上', clear: '清除標記' }
async function bulk(kind: 'off' | 'removed' | 'clear') {
  const n = pickedKeys.value.length
  if (!n) return
  if (!window.confirm(`把選取的 ${n} 台「${BULK_LABEL[kind]}」？\n（會留稽核紀錄）`)) return
  try {
    await guardedFetch('/api/racks/state/bulk', { method: 'PUT', body: { keys: pickedKeys.value, state: kind === 'clear' ? null : kind, note: '現場盤點' } })
    showToast(`已${BULK_LABEL[kind]} ${n} 台`, 'success')
    picked.value = {}
    await load()
  } catch (e: any) {
    showToast(`批次標記失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
async function setState(d: Dev, state: 'active' | 'removed' | null) {
  try {
    await guardedFetch('/api/racks/state', { method: 'PUT', body: { key: d.key, state, note: null } })
    showToast(state === 'active' ? `${d.name} 已確認在用` : state === 'removed' ? `${d.name} 已標成不在櫃上` : '已清除人工確認', 'success')
    await load()
  } catch (e: any) {
    showToast(`更新失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
function goAsset(d: Dev) { navigateTo(`/assets/${encodeURIComponent(d.asset_serial)}`) }
// 面板 ↔ 右邊清單互相指（2026-10-24）：點面板＝選取並捲到清單那一列（不跳頁）；點／滑過清單那一列＝亮面板。
// 要開資產頁：面板上的 ↗、雙擊面板，或清單裡的主機名連結。管理卡那一列指向它的主機面板。
const selKey = ref<string | null>(null)
const hoverKey = ref<string | null>(null)
const panelOf = (d: { key: string; mgmt_of?: string | null; merged_into?: string | null }) => d.mgmt_of || d.merged_into || d.key
function pickPanel(d: Dev, e?: MouseEvent) {
  if (justDragged) { justDragged = false; return }
  if (e && (e.ctrlKey || e.metaKey) && canEdit.value) {        // Ctrl 點選：多選來合併
    const s = new Set(multi.value)
    s.has(d.key) ? s.delete(d.key) : s.add(d.key)
    multi.value = s
    return
  }
  selKey.value = selKey.value === d.key ? null : d.key
  if (!selKey.value || props.compact) return
  nextTick(() => document.getElementById(`rk-${props.rack}-${d.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
}
function pickRow(d: { key: string; mgmt_of?: string | null; merged_into?: string | null }) { selKey.value = panelOf(d) }
const isSel = (d: Dev) => selKey.value === d.key || hoverKey.value === d.key
// ===== 拖拉搬移／調高度／就地合併（2026-10-25；限可編輯）=====
// 防手滑：按住 200ms 以上而且移動超過 6px 才算拖拉；放開一定先跳確認框（要填備註），Esc 取消。
// 拖到別櫃：放在平面圖或「換一櫃」的櫃子按鈕上（data-drop-rack）。寫入走既有的位置更正（會進 CIA 待更正、可還原）。
const { guardedFetch } = useGuardedFetch()
const emit = defineEmits<{ changed: []; dragging: [on: boolean] }>()
const canEdit = computed(() => !!det.value?.can_edit)
const multi = ref<Set<string>>(new Set())
let justDragged = false
interface DragState { d: Dev; mode: 'move' | 'top' | 'bottom' | 'place'; x0: number; y0: number; t0: number; active: boolean; u1: number; u2: number; overRack: string | null }
const drag = ref<DragState | null>(null)
interface Pending { d: Dev; rack: string; u1: number; u2: number; h: number; note: string; busy: boolean; err: string; checking: boolean; ok: boolean }
const pending = ref<Pending | null>(null)
function today() { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}` }
function startDrag(e: PointerEvent, d: Dev, mode: DragState['mode']) {
  if (!canEdit.value || e.button !== 0 || e.ctrlKey || e.metaKey || (mode !== 'place' && (!d.u_start || !d.u_end))) return
  drag.value = { d, mode, x0: e.clientX, y0: e.clientY, t0: Date.now(), active: false, u1: d.u_start ?? 1, u2: d.u_end ?? 1, overRack: null }
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragUp)
  window.addEventListener('keydown', onDragKey)
}
function stopListen() {
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragUp)
  window.removeEventListener('keydown', onDragKey)
}
function onDragMove(e: PointerEvent) {
  const g = drag.value
  if (!g) return
  const dx = e.clientX - g.x0, dy = e.clientY - g.y0
  if (!g.active) {
    if (Math.hypot(dx, dy) < 6 || Date.now() - g.t0 < 200) return
    g.active = true
    if (g.mode === 'move') emit('dragging', true)     // 外層把「換一櫃」的櫃子按鈕打開，才拖得到別櫃
  }
  if (g.mode === 'place') {                         // 位置不明的設備：依指標在櫃子上的高度決定 U（1U；高度在確認框裡可改）
    const box = slotsEl.value?.getBoundingClientRect()
    if (!box || e.clientY < box.top || e.clientY > box.bottom || e.clientX < box.left - 40 || e.clientX > box.right + 40) { g.overRack = null; g.u1 = 0; g.u2 = 0; return }
    const u = Math.min(Math.max(1, heightU.value - Math.floor((e.clientY - box.top) / U_PX)), heightU.value)
    g.u1 = u; g.u2 = u; g.overRack = null
    return
  }
  const du = Math.round(-dy / U_PX)
  const s0 = g.d.u_start!, e0 = g.d.u_end!
  if (g.mode === 'move') { const h = e0 - s0; g.u1 = Math.min(Math.max(1, s0 + du), heightU.value - h); g.u2 = g.u1 + h }
  else if (g.mode === 'top') { g.u2 = Math.min(Math.max(s0, e0 + du), heightU.value); g.u1 = s0 }
  else { g.u1 = Math.max(Math.min(e0, s0 + du), 1); g.u2 = e0 }
  const el = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null
  const r = el?.closest('[data-drop-rack]') as HTMLElement | null
  g.overRack = g.mode === 'move' && r ? r.dataset.dropRack || null : null
  if (g.overRack) { g.u1 = s0; g.u2 = e0 }          // 放到別櫃：先用原本的 U 位，確認框裡可以改
}
function onDragKey(e: KeyboardEvent) { if (e.key === 'Escape') { drag.value = null; stopListen(); emit('dragging', false) } }
function onDragUp() {
  stopListen()
  if (drag.value?.active) emit('dragging', false)
  const g = drag.value
  drag.value = null
  if (!g || !g.active) return
  justDragged = true
  setTimeout(() => { justDragged = false }, 300)
  const rack = g.overRack || props.rack
  if (g.mode === 'place' && g.u1 < 1) return                              // 沒放在櫃子上＝取消
  if (g.mode !== 'place' && rack === props.rack && g.u1 === g.d.u_start && g.u2 === g.d.u_end) return
  pending.value = { d: g.d, rack, u1: g.u1, u2: g.u2, h: g.u2 - g.u1 + 1, note: `拖拉更正 ${today()}`, busy: false,
    err: occupiedBy(g.d, rack, g.u1, g.u2), checking: false, ok: false }
  checkPending()
}
// 確認框可以直接改櫃／起始 U／高度：每改一次就問伺服器（不寫入）撞到誰、超出櫃高、櫃不存在
let checkSeq = 0
let checkTimer: ReturnType<typeof setTimeout> | null = null
function onPendingEdit() {
  const p = pending.value
  if (!p) return
  p.u2 = (Number(p.u1) || 0) + (Number(p.h) || 0) - 1
  p.err = ''
  p.ok = false
  if (checkTimer) clearTimeout(checkTimer)
  checkTimer = setTimeout(checkPending, 250)
}
async function checkPending() {
  const p = pending.value
  if (!p) return
  const seq = ++checkSeq
  if (!String(p.rack || '').trim() || !(p.u1 >= 1) || !(p.h >= 1)) { p.err = '櫃、起始 U、高度都要填'; p.ok = false; return }
  p.checking = true
  try {
    const r = await apiFetch<{ ok: boolean; error: string | null; u_start?: number; u_end?: number }>('/api/racks/position/move-check', {
      query: { key: p.d.key, room: det.value?.room, rack: p.rack, u_start: p.u1, u_height: p.h } })
    if (seq !== checkSeq || pending.value !== p) return
    p.ok = r.ok
    p.err = r.ok ? '' : (r.error || '不能搬到這裡')
  } catch (e: any) {
    if (seq === checkSeq) { p.ok = false; p.err = `檢查失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
  } finally { if (seq === checkSeq) p.checking = false }
}
// 回到上一步：搬完顯示「已搬 X：A→B［復原］」；右邊「最近異動」每筆都能復原
interface MoveRow { id: number; key: string; name: string; from_text: string; to_text: string; count: number; note: string | null; by: string | null; at: string; undone: boolean; undone_by: string | null }
const lastMove = ref<{ id: number; text: string } | null>(null)
const moves = ref<MoveRow[]>([])
async function loadMoves() {
  if (props.compact) return
  try {
    const r = await apiFetch<{ items: MoveRow[] }>('/api/racks/position/moves', { query: { room: props.room, rack: props.rack, limit: 20 } })
    moves.value = r.items
  } catch (e: any) {
    moves.value = []
    showToast(`最近異動載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
watch(() => [props.room, props.rack], loadMoves, { immediate: true })
const moveRows = computed(() => moves.value.map((m, i) => ({ ...m, no: i + 1, state_text: m.undone ? '已復原' : '' })))
const { sortKey: mvKey, sortDir: mvDir, toggle: mvToggle, sorted: mvSorted } = useSort(moveRows, 'no')
async function undoMove(id: number, label: string) {
  if (!window.confirm(`復原「${label}」？\n會回到搬之前的位置（之前有更正就回那筆更正，沒有就回 CIA 位置），留稽核紀錄。`)) return
  const call = (force: boolean) => guardedFetch(`/api/racks/position/moves/${id}/undo`, { method: 'POST', body: { force, note: null } })
  try {
    try {
      await call(false)
    } catch (e: any) {
      const code = e?.statusCode ?? e?.response?.status
      const d = e?.data?.detail
      if (code !== 409 || !d?.changes) throw e
      const lines = d.changes.map((c: any, i: number) => `${i + 1}. ${c.what}`).join('\n')
      if (!window.confirm(`${d.message}：\n${lines}\n\n仍要復原嗎？`)) return
      await call(true)
    }
    showToast(`已復原：${label}`, 'success')
    if (lastMove.value?.id === id) lastMove.value = null
    await load()
    await loadMoves()
    emit('changed')
  } catch (e: any) {
    const d = e?.data?.detail
    showToast(`復原失敗：${typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
// 同櫃先在畫面上檢查有沒有別台（伺服器還會再擋一次）；別櫃交給伺服器講是誰
function occupiedBy(d: Dev, rack: string, u1: number, u2: number): string {
  if (rack !== props.rack) return ''
  const mine = new Set([d.key, ...(d.merged ?? []).map(x => x.key), ...(d.mgmt ?? []).map(x => x.key)])
  const hit = placed.value.filter(x => !mine.has(x.key) && x.u_start && x.u_end && x.u_start <= u2 && u1 <= x.u_end)
  return hit.length ? `目標位置已經有：${hit.map(x => `${panelLabel(x).model} ${panelLabel(x).tag}（${x.name}）`).join('、')}` : ''
}
const slotsEl = ref<HTMLElement | null>(null)
function p_isHeightFix(p: { rack: string; u1: number; u2: number; d: Dev }) { return !!p.d.u_start && p.rack === props.rack && p.u2 - p.u1 !== p.d.u_end! - p.d.u_start }
// Excel 虛線框（系統有對到、單一一台）也能拖：現場實際位置跟 Excel 不同時，拖到實際位置＝現場更正（走同一個確認框、稽核、CIA 待更正）
function startGhostDrag(e: PointerEvent, c: NonNullable<Detail['excel']>['cells'][number]) {
  if (!c.key || !['NOPOS', 'OTHERROOM', 'MOVED'].includes(c.status)) return
  const dv = det.value?.devices.find(x => x.key === c.key)
  if (!dv) return
  startDrag(e, { ...dv, u_start: c.u_start, u_end: c.u_end }, 'move')
}
const uTxt = (a: number, b: number) => a === b ? `${a}U` : `${a}-${b}U`
function pendingText(p: Pending) {
  const lab = `${panelLabel(p.d).model} ${panelLabel(p.d).tag}`
  return `${lab}：${props.rack} ${p.d.u_start ? uTxt(p.d.u_start, p.d.u_end!) : '位置不明'} → ${p.rack} ${p.u1 >= 1 && p.u2 >= p.u1 ? uTxt(p.u1, p.u2) : '?'}`
}
async function confirmMove() {
  const p = pending.value
  if (!p || !p.note.trim() || p.err || !p.ok || p.checking) return
  p.busy = true
  try {
    const r = await guardedFetch<{ move_id: number; name: string; from: { rack: string; u_start: number; u_end: number }; rack: string; u_start: number; u_end: number }>(
      '/api/racks/position/move', { method: 'PUT', body: {
        key: p.d.key, room: det.value?.room, rack: p.rack, u_start: p.u1, u_height: p.h, note: p.note.trim() } })
    const text = `已搬 ${r.name}：${r.from.rack}／${uTxt(r.from.u_start, r.from.u_end)} → ${r.rack}／${uTxt(r.u_start, r.u_end)}`
    lastMove.value = { id: r.move_id, text }
    showToast(text, 'success')
    pending.value = null
    await load()
    await loadMoves()
    emit('changed')
  } catch (e: any) {
    p.err = e?.data?.detail ?? e?.message ?? '更正失敗'
  } finally { if (pending.value) pending.value.busy = false }
}
// 就地合併：重疊並排的那幾台，或 Ctrl 點選的幾台
async function mergeKeys(keys: string[]) {
  if (keys.length < 2) return
  const note = window.prompt(`把這 ${keys.length} 筆合併成同一台？（只影響畫面與用電只算一次；台數、CIA 不動；可以拆開）\n備註（可空）：`, '')
  if (note === null) return
  try {
    await guardedFetch('/api/racks/overlaps/decide', { method: 'POST', body: { room: det.value?.room, rack: det.value?.rack, keys, action: 'merge', note: note || null } })
    showToast('已合併為同一台', 'success')
    multi.value = new Set()
    await load()
    emit('changed')
  } catch (e: any) { showToast(`合併失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
}
async function splitBox(d: Dev) {
  if (!window.confirm(d.merge_auto ? '拆開（其實是不同台）？系統之後不會再自動併這幾筆。' : '復原這個合併？')) return
  try {
    if (d.merge_auto) {
      await guardedFetch('/api/racks/overlaps/decide', { method: 'POST', body: { room: det.value?.room, rack: det.value?.rack, keys: d.merge_keys, action: 'split', note: '在機櫃上拆開' } })
    } else if (d.merge_decision_id) {
      await guardedFetch(`/api/racks/overlaps/${d.merge_decision_id}/undo`, { method: 'POST', body: { note: '在機櫃上拆開' } })
    }
    showToast('已拆開', 'success')
    await load()
    emit('changed')
  } catch (e: any) { showToast(`拆開失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
}

// 位置更正：單台（PositionEditor）與「依盤點結果更正」整櫃一次存
const editing = ref<string | null>(null)
function editTarget(d: Dev) { return { key: d.key, name: d.name, room: det.value?.room ?? '', rack: det.value?.rack ?? null, u_start: d.u_start, u_end: d.u_end, position_corrected: d.position_corrected } }
function ciaText(d: Dev) {
  const c = d.cia_position
  if (!c) return ''
  const u = c.u_start ? (c.u_start === c.u_end ? `${c.u_start}U` : `${c.u_start}-${c.u_end}U`) : ''
  return `CIA：${c.room} ${c.rack ?? (c.raw || '（無機櫃）')} ${u}；更正：${d.position_by ?? ''} ${d.position_at ?? ''} ${d.position_note ?? ''}`
}
const invMode = ref(false)
const invNote = ref('')
const invRows = ref<Record<string, { rack: string; u_start: number | null; u_height: number }>>({})
function startInventory() {
  invRows.value = Object.fromEntries((det.value?.devices ?? []).map(d => [d.key, {
    rack: det.value!.rack, u_start: d.u_start, u_height: d.u_start && d.u_end ? d.u_end - d.u_start + 1 : 1,
  }]))
  invMode.value = true
}
const invChanged = computed(() => (det.value?.devices ?? []).filter((d) => {
  const r = invRows.value[d.key]
  if (!r) return false
  const h = d.u_start && d.u_end ? d.u_end - d.u_start + 1 : 1
  return r.rack !== det.value!.rack || (r.u_start || null) !== (d.u_start || null) || (r.u_height || 1) !== h
}))
const invResult = ref<{ key: string; collisions: { name: string; u_start: number; u_end: number }[] }[]>([])
async function saveInventory() {
  if (!invNote.value.trim()) { showToast('要寫備註（例：10/23 現場盤點）', 'warn'); return }
  if (!invChanged.value.length) { showToast('沒有改到任何一台', 'warn'); return }
  if (!window.confirm(`依盤點結果更正 ${invChanged.value.length} 台的位置？`)) return
  try {
    const r = await guardedFetch<{ results: any[] }>('/api/racks/position/bulk', { method: 'PUT', body: {
      note: invNote.value,
      items: invChanged.value.map(d => ({ key: d.key, room: det.value!.room, ...invRows.value[d.key] })),
    } })
    invResult.value = r.results.filter((x: any) => x.collisions.length)
    showToast(`已更正 ${invChanged.value.length} 台${invResult.value.length ? `，${invResult.value.length} 台 U 位重疊（已存）` : ''}`, invResult.value.length ? 'warn' : 'success')
    invMode.value = false
    await load()
  } catch (e: any) {
    showToast(`更正失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
// ===== 在這個機櫃新增設備（2.007.17 使用者：「機櫃上增加，可手動增加嗎」；限管理員）=====
// 機房、機櫃自動帶入；只填 U 位、資產序號、機型…；新增的資產是「待上線」，只存在本系統（CIA 要另外補）。
interface AddForm { u_start: number | null; u_end: number | null; asset_serial: string; asset_name: string; device_model: string; hw_serial: string; hostname: string; ip: string; api_id: string; mgmt_ip: string; mgmt_kind: string }
const addForm = ref<AddForm | null>(null)
const addBusy = ref(false); const addError = ref(''); const addOverlap = ref<{ name: string; u_start: number; u_end: number }[]>([])
// 雙擊櫃子上的空位 → 開「新增設備」並帶入那一 U（只在空白處；雙擊面板不會觸發）
function onSlotDblClick(e: MouseEvent) {
  if (!canEdit.value || e.target !== e.currentTarget) return
  const box = slotsEl.value?.getBoundingClientRect()
  if (!box) return
  const u = Math.min(Math.max(1, heightU.value - Math.floor((e.clientY - box.top) / U_PX)), heightU.value)
  openAdd()
  if (addForm.value) { addForm.value.u_start = u; addForm.value.u_end = null }
}
const modelOpts = ref<string[]>([])
const vendorRules = ref<{ name: string; aliases: string[]; serial: string }[]>([])
// 序號欄的提示：看機型開頭是哪個廠牌，就寫它的碼數規定（只是提示；真正的檢查在伺服器）
const serialHint = computed(() => {
  const m = (addForm.value?.device_model || '').trim().toLowerCase()
  const v = vendorRules.value.find(x => [x.name, ...x.aliases].some(n => m === n.toLowerCase() || m.startsWith(n.toLowerCase() + ' ')))
  if (!v) return '英數字，例：F39Y1K4'
  if (!v.serial) return `${v.name} 序號：英數字`
  const [lo, hi] = v.serial.split('-')
  return `${v.name} 序號：${hi ? `${lo}–${hi}` : lo} 碼英數`
})
const sysOpts = ref<{ api_id: string; name: string }[]>([])
async function loadModelOpts() {
  if (modelOpts.value.length) return
  try { sysOpts.value = ((await apiFetch<{ systems: { api_id: string; name: string }[] }>('/api/business-systems')).systems ?? []).map(x => ({ api_id: x.api_id, name: x.name })) }
  catch (e: any) { showToast(`系統清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
  try { vendorRules.value = (await apiFetch<{ vendors: { name: string; aliases: string[]; serial: string }[] }>('/api/model-vendors')).vendors }
  catch (e: any) { showToast(`廠牌清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
  try { modelOpts.value = (await apiFetch<{ items: { model: string }[] }>('/api/models/options')).items.map(x => x.model) }
  catch (e: any) { showToast(`機型建議清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
}
function openAdd() {
  loadModelOpts()
  addForm.value = { u_start: null, u_end: null, asset_serial: '', asset_name: '', device_model: '', hw_serial: '', hostname: '', ip: '', api_id: '', mgmt_ip: '', mgmt_kind: 'iDRAC' }; addError.value = ''; addOverlap.value = [] }
async function submitAdd(confirmOverlap = false) {
  const f = addForm.value
  if (!f || !det.value) return
  if (!f.asset_serial.trim() && !(f.hostname || '').trim()) { addError.value = '資產序號留空時，主機名要填'; return }
  if (!f.u_start) { addError.value = '起始 U 要填'; return }
  if (f.mgmt_ip.trim() && !(f.hostname || '').trim()) { addError.value = '填了管理 IP，主機名要填'; return }
  addBusy.value = true; addError.value = ''
  try {
    const res = await guardedFetch<{ mgmt_error?: string }>('/api/racks/add-device', { method: 'POST', body: { room: det.value.room, rack: det.value.rack, ...f, u_end: f.u_end || f.u_start, confirm_overlap: confirmOverlap } })
    if (res?.mgmt_error) showToast(res.mgmt_error, 'warn')
    else showToast(`已新增 ${f.asset_serial || f.hostname}（待上線）`, 'success')
    addForm.value = null
    await load()
    emit('changed')
  } catch (e: any) {
    const d = e?.data?.detail
    if (d && typeof d === 'object' && d.overlap) { addOverlap.value = d.overlap; addError.value = '' } else { addOverlap.value = []; addError.value = `新增失敗：${typeof d === 'string' ? d : (e?.message ?? '未知錯誤')}` }
  } finally { addBusy.value = false }
}
// ===== 面板雙擊改機型／IP／序號／備註（現場更正，2026-10-29；限管理員）=====
// 以現場實際為準，但不直接改 CIA：存自己的表、留稽核（誰、何時、改前改後）、進「CIA 待更正」；還原＝回到 CIA 的值。
const FIELD_LABEL: Record<string, string> = { model: '機型', ip: 'IP', serial: '序號', remark: '備註' }
interface FEdit { d: Dev; field: string; value: string; note: string; busy: boolean; err: string }
const fedit = ref<FEdit | null>(null)
function curVal(d: Dev, f: string) {
  if (f === 'model') return d.model || ''
  if (f === 'ip') return d.ip && d.ip !== '0.0.0.0' ? d.ip : ''
  if (f === 'serial') return (d.hw_serials ?? [])[0] || ''
  return d.remark || ''
}
function openFieldEdit(d: Dev, field: string, e: MouseEvent) {
  if (!canEdit.value || props.compact) return               // 不能改的人：事件照舊往上走（雙擊開資產頁）
  e.stopPropagation()
  loadModelOpts()
  fedit.value = { d, field, value: curVal(d, field), note: '', busy: false, err: '' }
}
function onFieldPick() { if (fedit.value) fedit.value.value = curVal(fedit.value.d, fedit.value.field) }
const feInfo = computed(() => { const f = fedit.value; return f ? f.d.field_edits?.[f.field] ?? null : null })
function feTitle(d: Dev, f: string, base: string) {
  const x = d.field_edits?.[f]
  return x ? `${base}\n現場更正（${x.by ?? '?'} ${x.at ?? ''}）：CIA 原本是「${x.cia ?? ''}」，CIA 待更正` : base
}
async function saveFieldEdit() {
  const f = fedit.value
  if (!f) return
  f.busy = true; f.err = ''
  try {
    await guardedFetch('/api/racks/field-edit', { method: 'PUT', body: { key: f.d.key, field: f.field, value: f.value, note: f.note || null } })
    showToast(`已更正 ${f.d.name} 的${FIELD_LABEL[f.field]}（CIA 待更正）`, 'success')
    fedit.value = null
    await load()
    emit('changed')
  } catch (e: any) {
    const d = e?.data?.detail
    f.err = `更正失敗：${typeof d === 'string' ? d : (e?.message ?? '未知錯誤')}`
  } finally { f.busy = false }
}
async function revertFieldEdit() {
  const f = fedit.value
  if (!f) return
  f.busy = true; f.err = ''
  try {
    await guardedFetch(`/api/racks/field-edit?key=${encodeURIComponent(f.d.key)}&field=${f.field}`, { method: 'DELETE' })
    showToast(`已還原 ${f.d.name} 的${FIELD_LABEL[f.field]}（回到 CIA 的值）`, 'success')
    fedit.value = null
    await load()
    emit('changed')
  } catch (e: any) {
    const d = e?.data?.detail
    f.err = `還原失敗：${typeof d === 'string' ? d : (e?.message ?? '未知錯誤')}`
  } finally { f.busy = false }
}
function nameOf(k: string) { return det.value?.devices.find(d => d.key === k)?.name ?? k }
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
function goSystem(api: string) { navigateTo({ path: '/topology', query: { system: api } }) }
</script>

<template>
  <div class="rackel" :class="{ big }">
    <div v-if="addForm" class="mvmask" @keydown.esc="addForm = null">
      <div class="mvbox">
        <b>在 {{ det?.room }} {{ det?.rack }} 新增設備</b>
        <div class="mvrow">
          <label>起 U <input v-model.number="addForm.u_start" class="cell n" type="number" min="1" max="60"></label>
          <label>迄 U <input v-model.number="addForm.u_end" class="cell n" type="number" min="1" max="60" placeholder="同起"></label>
        </div>
        <div class="mvrow">
          <label>主機名 <input v-model.trim="addForm.hostname" class="cell" style="width:150px"></label>
          <label>機型 <input v-model.trim="addForm.device_model" class="cell" style="width:170px" list="rk-model-opts" placeholder="廠牌 型號，例：IBM S922"></label>
          <datalist id="rk-model-opts"><option v-for="o in modelOpts" :key="o" :value="o" /></datalist>
          <label>IP <input v-model.trim="addForm.ip" class="cell" style="width:120px"></label>
        </div>
        <div class="mvrow">
          <label>序號 <input v-model.trim="addForm.hw_serial" class="cell" style="width:170px" :placeholder="serialHint"></label>
          <label>系統 <input v-model.trim="addForm.api_id" class="cell" style="width:150px" list="rk-sys-opts" placeholder="系統代碼，可留空"></label>
          <datalist id="rk-sys-opts"><option v-for="o in sysOpts" :key="o.api_id" :value="o.api_id">{{ o.name }}</option></datalist>
          <label>管理 IP <input v-model.trim="addForm.mgmt_ip" class="cell" style="width:120px" placeholder="iDRAC／iLO，可留空"></label>
          <label v-if="addForm.mgmt_ip">管理介面
            <select v-model="addForm.mgmt_kind" class="cell"><option v-for="k in ['iDRAC', 'iLO', 'ILOM', 'IMM', 'XCC', 'BMC']" :key="k" :value="k">{{ k }}</option></select>
          </label>
        </div>
        <div class="mvrow">
          <label>備註 <input v-model.trim="addForm.asset_name" class="cell" style="width:150px"></label>
          <label>資產序號 <input v-model.trim="addForm.asset_serial" class="cell" style="width:130px" placeholder="CIA 還沒給就留空"></label>
          <InfoNote>起 U、機型必填；欄位順序跟面板一樣（第一行：主機名、機型、IP；第二行：序號、系統、管理 IP）。機型寫成「廠牌 型號」（例：IBM S922、Dell R760），可從下拉建議選，系統會統一大小寫與空白。系統要選已有的系統代碼（下拉會顯示名稱）。管理 IP 會另建一筆「主機名-iDRAC」這類管理卡，機櫃上併進這台的面板、不算第二台，要先填主機名。資產序號留空時主機名要填：系統先給暫時編號 NEW-IP（沒有 IP 用主機名），CIA 補登後用主機名認成同一台，台數不會多算。新增後狀態是「待上線」，只存在本系統，CIA 要另外補登。</InfoNote>
        </div>
        <p v-if="addOverlap.length" class="err">這段 U 位已有設備：{{ addOverlap.map(o => `${o.name}（${o.u_start}–${o.u_end}U）`).join('、') }}。確定要新增嗎？</p>
        <p v-if="addError" class="err">{{ addError }}</p>
        <div class="mvrow">
          <button v-if="addOverlap.length" class="pbtn" :disabled="addBusy" @click="submitAdd(true)">仍要新增</button>
          <button v-else class="pbtn" :disabled="addBusy" @click="submitAdd(false)">{{ addBusy ? '新增中…' : '新增' }}</button>
          <button class="pbtn" :disabled="addBusy" @click="addForm = null">取消</button>
        </div>
      </div>
    </div>
    <div v-if="fedit" class="mvmask" @keydown.esc="fedit = null">
      <div class="mvbox">
        <b>更正 {{ fedit.d.name }}（{{ det?.rack }} {{ fedit.d.u_start ? uTxt(fedit.d.u_start, fedit.d.u_end ?? fedit.d.u_start) : '位置不明' }}）</b>
        <div class="mvrow">
          <label>欄位
            <select v-model="fedit.field" class="cell" :disabled="fedit.busy" @change="onFieldPick">
              <option v-for="(lb, k) in FIELD_LABEL" :key="k" :value="k">{{ lb }}</option>
            </select>
          </label>
          <label>改成
            <input v-model.trim="fedit.value" class="cell" style="width:230px" :disabled="fedit.busy" list="rk-model-opts"
                   :placeholder="fedit.field === 'model' ? '廠牌 型號，例：IBM S922' : fedit.field === 'serial' ? '英數字，依廠牌檢查碼數' : ''"
                   @keyup.enter="saveFieldEdit">
          </label>
          <datalist v-if="fedit.field === 'model'" id="rk-model-opts"><option v-for="o in modelOpts" :key="o" :value="o" /></datalist>
        </div>
        <div class="mvrow">
          <label>原因（選填） <input v-model.trim="fedit.note" class="cell" style="width:300px" :disabled="fedit.busy" placeholder="例：現場看到標籤是 R760"></label>
          <InfoNote>以現場實際為準，但不會改 CIA：這裡的更正存在本系統，留下是誰、何時改的，並列入「CIA 待更正」給 CIA 負責人照改；CIA 改成一樣後，更正會自動退場。「還原」＝回到 CIA 的值。機型寫成「廠牌 型號」，序號、IP 會檢查格式。</InfoNote>
        </div>
        <p v-if="feInfo" class="muted">目前已有現場更正：{{ feInfo.by ?? '?' }} 於 {{ feInfo.at ?? '' }} 改成「{{ feInfo.value }}」，CIA 原本是「{{ feInfo.cia ?? '' }}」。</p>
        <p v-if="fedit.err" class="err">{{ fedit.err }}</p>
        <div class="mvrow">
          <button class="pbtn" :disabled="fedit.busy" @click="saveFieldEdit">{{ fedit.busy ? '儲存中…' : '儲存更正' }}</button>
          <button v-if="feInfo" class="pbtn" :disabled="fedit.busy" @click="revertFieldEdit">還原成 CIA 的值</button>
          <button class="pbtn" :disabled="fedit.busy" @click="fedit = null">取消</button>
        </div>
      </div>
    </div>
    <div v-if="pending" class="mvmask" @keydown.esc="pending = null">
      <div class="mvbox">
        <b>確認更正位置</b>
        <p class="mvtxt">{{ pendingText(pending) }}</p>
        <p v-if="pending.d.merged?.length || pending.d.mgmt?.length" class="muted">同一台的 {{ (pending.d.merged?.length ?? 0) + (pending.d.mgmt?.length ?? 0) }} 筆登記（含管理卡）會一起搬。</p>
        <div class="mvrow">
          <label>櫃 <input v-model.trim="pending.rack" class="cell" list="rk-racks" @input="onPendingEdit"></label>
          <datalist id="rk-racks"><option v-for="r in det?.room_racks ?? []" :key="r" :value="r" /></datalist>
          <label>起始 U <input v-model.number="pending.u1" type="number" min="1" :max="heightU" class="cell n" @input="onPendingEdit"></label>
          <label>高度 <input v-model.number="pending.h" type="number" min="1" max="20" class="cell n" @input="onPendingEdit"> U</label>
          <span v-if="pending.checking" class="muted">檢查中…</span>
          <span v-else-if="pending.ok" class="okmark">可以放</span>
        </div>
        <label>備註（必填）<input v-model="pending.note" class="note" @keydown.esc="pending = null"></label>
        <p v-if="pending.err" class="warnbox">{{ pending.err }}</p>
        <p class="muted">{{ pending.rack === rack && p_isHeightFix(pending) ? '高度更正' : '位置更正' }}<InfoNote>寫進「位置更正」：留誰／何時，會出現在 CIA 待更正；搬完可以按「復原」回到搬之前，或從右邊「最近異動」復原。</InfoNote></p>
        <div class="bulk">
          <button class="chip on" :disabled="pending.busy || pending.checking || !pending.ok || !pending.note.trim() || !!pending.err" @click="confirmMove">{{ pending.busy ? '更正中…' : '確定' }}</button>
          <button class="chip" @click="pending = null">取消</button>
        </div>
      </div>
    </div>
    <p v-if="loading" class="muted">載入中…</p>
    <p v-else-if="err" class="err">機櫃載入失敗：{{ err }}</p>
    <template v-else-if="det">
      <div v-if="compact" class="rhead">
        <b>{{ det.rack }}</b>
        <span class="muted">{{ det.box_count ?? det.devices.length }} 台</span>
        <span v-if="hiSet.size" class="mine-tag">符合 {{ det.devices.filter(d => hiSet.has(d.key)).length }}</span>
      </div>
      <div v-else class="rhead">
        <b>{{ det.room }}　{{ det.rack }}</b>
        <span v-if="det.floor" class="muted">{{ det.floor }}</span>
        <span class="muted">{{ det.box_count ?? det.devices.length }} 台</span>
        <button v-if="(det.box_count ?? det.devices.length) !== det.devices.length" class="chip" :class="{ on: showMgmt && openBox.size }"
                title="點開：列出全部登記（含管理口、同一箱的其他筆）"
                @click="toggleAllRecords">{{ det.devices.length }} 筆登記</button>
        <button class="chip" :class="{ on: showMgmt }" @click="showMgmt = !showMgmt">顯示管理介面</button>
        <span v-if="apiId" class="mine-tag">本系統 {{ det.mine_count }}</span>
        <button v-if="apiId" class="chip" :class="{ on: onlyOthers }" @click="onlyOthers = !onlyOthers">同櫃其他設備 {{ det.others_count }} 台</button>
        <NuxtLink v-if="det.can_edit" class="pbtn" :to="`/rack-audit?start=1&room=${encodeURIComponent(det.room)}&rack=${encodeURIComponent(det.rack)}`">開始盤點</NuxtLink>
        <button v-if="det.can_edit && !compact" class="pbtn" @click="openAdd">新增設備</button>
        <details class="moremenu">
          <summary class="chip">更多 ▾</summary>
          <div class="moreitems">
              <a class="pbtn" :href="`/print/rack-sheet?room=${encodeURIComponent(det.room)}&rack=${encodeURIComponent(det.rack)}${apiId ? `&system=${encodeURIComponent(apiId)}` : ''}`" target="_blank">列印盤點表</a>
              <a class="pbtn" :href="`/print/labels?room=${encodeURIComponent(det.room)}&rack=${encodeURIComponent(det.rack)}`" target="_blank">印標籤</a>
              <button v-if="det.can_edit && !invMode" class="pbtn" @click="startInventory">依盤點結果更正</button>
              <a class="pbtn" :href="`${apiBase}/api/racks/position/pending-cia.xlsx`">CIA 待更正（xlsx）</a>
              <a class="pbtn" :href="`${apiBase}/api/racks/field-edits.xlsx`">CIA 待更正：機型／IP／序號／備註（xlsx）</a>
          </div>
        </details>
        <InfoNote>機櫃高度沒有資料，以 {{ det.height_u }}U 畫；只有機櫃代碼、沒寫 U 位的設備列在下方「位置不明」。燈號：綠＝實際看得到（存在可信度達標），灰＝只有登記。台數＝實體箱子：管理口（ILOM／iLO／iDRAC／IMM／XCC／BMC，主機名去掉尾巴＝同櫃某台、或同 U 同服務標籤）寫在主機上「管理 IP」，不算台、不佔 U、不算電；找不到本體的管理口照樣列出、照舊算一台（它有自己的 U 位，那裡就有一台實體機），標「管理介面・找不到本體」。推定同一台（主機名＋機型一樣、Power 分割區、同 IP 同機型）只算一台，點「推定同一台」看是哪幾筆。同一段 1–2U 登記超過 4 筆的不合併，列在「疑似登記錯誤」。</InfoNote>
      </div>
      <div v-if="!compact" class="pline">
        <span><b>{{ (det.power.w / 1000).toFixed(2) }} kW</b></span>
        <span>已用 {{ det.power.used_u }}U／空 {{ det.power.unplaced_count ? `≤${det.power.free_u}U（另 ${det.power.unplaced_count} 台未定位）` : `${det.power.free_u}U` }}（最大連續 {{ det.power.largest_free_u }}U）</span>
        <details class="moremenu powermore">
          <summary class="chip">額定用電估算與規格 ▾</summary>
          <div class="moreitems rowitems">
          <span>{{ det.power.amps ?? '—' }} A @{{ det.power.volts }}V、{{ det.power.btu_h.toLocaleString() }} BTU/h、{{ det.power.kg }} kg</span>
          <span v-if="det.power.if_all_on_w">若全部開機 +{{ det.power.if_all_on_w }} W</span>
          <button v-if="det.power.unknown_count" class="chip" :class="{ on: unknownOnly }" @click="unknownOnly = !unknownOnly">未知規格 {{ det.power.unknown_count }} 台</button>
          <button v-if="det.power.unobserved_count" class="chip" :class="{ on: unobsOnly }" @click="unobsOnly = !unobsOnly">其中未觀察到 {{ det.power.unobserved_count }} 台</button>
          <span v-for="s in det.power.system_level" :key="s.model" class="sl">工程系統 {{ s.model }}：<b>推定為同一套</b>（規則：同櫃同型號 ≥{{ s.sl_min ?? 3 }} 筆；共 {{ s.records }} 筆登記）合計（{{ s.u ?? '?' }}U、{{ s.kg ?? '?' }} kg、{{ s.w ?? '用電未知' }}{{ s.w ? ' W' : '' }}）<InfoNote>這是依規則推定，不是現場確認：同一櫃、同型號的登記達門檻就視為同一套工程系統（Exadata／ODA），整套的 U、功率、重量只算一次。整機、元件、管理口不會重複計 U、功率、重量。無法確認的欄位仍標「?」或「用電未知」。門檻在系統設定 system_level_min_records。</InfoNote></span>
          <span v-if="det.power.vendor_move.length" class="sl">需原廠搬遷：{{ det.power.vendor_move.join('、') }}</span>
          </div>
        </details>
        <InfoNote>機櫃口徑看的是「設備實體在不在櫃上」，跟全站台數（存在可信度）是兩回事：使用中＋有機櫃＝在用，算電、算 U、算重量、算台數，系統看不到它（只有登記）就另標「未觀察到」（空心燈）；停用／閒置＋有機櫃＝關機未下架；報廢＋有機櫃＝待確認（可能還在櫃上）。關機未下架與待確認佔 U、算重量、用電算 0，另列「若全部開機」。現場盤點結果可以人工標記（關機未下架／已不在櫃上／清除），每次都留稽核紀錄；「已不在櫃上」的設備從機櫃拿掉。</InfoNote>
        <InfoNote label="電">額定用電估算（不是實際耗電；全站沒有實測來源）＝每台在用設備「一顆」PSU 的額定瓦數（型號規格表，取 200–240V 可用的最大 PSU），不是加總，也不是實際耗電；電流＝瓦數 ÷ 機房電壓（系統設定 room_volts，預設 220V）；散熱 BTU/h＝瓦數 × 3.412。關機未下架（停用／閒置）與待確認（報廢、或只有登記看不到）的設備佔 U、算重量，用電算 0，另列「若全部開機」。型號對不到規格、或 CIA 只寫籠統描述的，算「未知規格」不算進用電。工程系統（Exadata／ODA）同櫃同型號達 {{ 3 }} 筆以上時，整套數字只算一次。</InfoNote>
      </div>
      <div class="rbody" :class="{ compact }">
        <div>
        <div v-if="multi.size" class="multibar">
          已選 {{ multi.size }} 台 <button class="chip" :disabled="multi.size < 2" @click="mergeKeys([...multi])">合併選取的 {{ multi.size }} 台</button>
          <button class="chip" @click="multi = new Set()">取消</button>
        </div>
        <div v-if="drag && drag.active && drag.overRack" class="multibar">放開＝搬到 {{ drag.overRack }}（同樣 U 位，確認框可改）</div>
        <div v-if="det.excel_only" class="multibar">這個櫃只在 Excel 盤點上，系統（CIA）沒有登記任何設備。下面是 Excel 的內容（虛線框），不算台數、不算用電；要納入系統請到 CIA 補登，或雙擊空的 U 位新增設備。</div>
        <div class="frame" :class="{ dragging: drag && drag.active }" :style="{ height: `${heightU * U_PX + 16}px` }">
          <div class="rails">
            <span v-for="u in uMarks" :key="u" class="umark" :style="{ top: `${(heightU - u) * U_PX + 8}px`, lineHeight: `${U_PX}px` }">{{ u }}</span>
          </div>
          <div ref="slotsEl" class="slots" :style="slotBg" :title="canEdit ? '空的 U 位：雙擊可在這裡新增設備' : undefined" @dblclick="onSlotDblClick">
            <button v-for="d in placed" :key="d.key" class="face" :class="{ mine: d.mine, other: apiId && !d.mine, dormant: d.state !== 'active', hit: hiSet.has(d.key), ovl: !!lanes[d.key], sel: isSel(d), ['st-' + (statusByKey?.[d.key] ?? '')]: !!statusByKey?.[d.key] }"
                    :style="faceStyle(d)" :title="faceTitle(d)" @click="pickPanel(d, $event)" @dblclick="goAsset(d)"
                    @pointerdown="startDrag($event, d, 'move')">
              <span v-if="canEdit" class="rh top" title="拖拉調高度（往上）" @pointerdown.stop="startDrag($event, d, 'top')" @click.stop />
              <span v-if="canEdit" class="rh bot" title="拖拉調高度（往下）" @pointerdown.stop="startDrag($event, d, 'bottom')" @click.stop />
              <i v-if="exStatus(d)" class="xdot" :class="'xl-' + exStatus(d)" :title="exText(d)" /><i v-else class="led" :class="{ on: d.seen, hollow: d.unobserved }" /><i v-if="multi.has(d.key)" class="pick">✓</i>
              <span class="xrows">
                <span class="xrow">
                  <span class="xlbox xnote" :class="{ xedited: d.field_edits?.remark }" :title="feTitle(d, 'remark', d.remark ? `${d.name}｜${d.remark}` : d.name)" @dblclick="openFieldEdit(d, 'remark', $event)">{{ d.name }}{{ d.remark ? `｜${d.remark}` : '' }}</span>
                  <span class="xlbox xmodel" :class="{ xedited: d.field_edits?.model }" :title="feTitle(d, 'model', d.model || '')" @dblclick="openFieldEdit(d, 'model', $event)">{{ d.state !== 'active' && d.state_label ? `［${d.state_label}］` : '' }}{{ up(d.model) }}</span>
                  <span class="xlbox xip" :class="{ xedited: d.field_edits?.ip }" :title="feTitle(d, 'ip', d.ip || '')" @dblclick="openFieldEdit(d, 'ip', $event)">{{ d.ip && d.ip !== '0.0.0.0' ? d.ip : '' }}</span>
                  <span class="xlbox xok" :title="sysFlag(d) || exText(d)">{{ sysFlag(d) || exText(d) }}</span>
                </span>
                <span class="xrow">
                  <span class="xlbox xnote" :class="{ xedited: d.field_edits?.serial }" :title="feTitle(d, 'serial', serialOf(d))" @dblclick="openFieldEdit(d, 'serial', $event)">{{ serialOf(d) }}</span>
                  <span class="xlbox xmodel" :title="(d.systems ?? []).map(x => `${x.api_id} ${x.name}`).join('、')">{{ (d.systems ?? []).map(x => x.guess ? x.api_id : x.name).join('、') }}</span>
                  <span class="xlbox xmgmt" :title="mgmtTitle(d)">{{ (d.mgmt ?? []).length ? mgmtText((d.mgmt ?? [])[0]) : '' }}</span>
                </span>
              </span>
              <span class="go" role="link" title="開資產頁" @click.stop="goAsset(d)">↗</span>
              <span v-if="canEdit && lanes[d.key] && lanes[d.key].lane === 0" class="mbtn" title="把並排的這幾筆合併成同一台" @click.stop="mergeKeys(lanes[d.key].keys)" @pointerdown.stop>合併</span>
              <span v-if="canEdit && d.merged?.length" class="mbtn" :title="d.merge_auto ? '拆開（其實是不同台）' : '復原合併'" @click.stop="splitBox(d)" @pointerdown.stop>拆開</span>
              <span v-if="d.merged?.length" class="mgmtc twin" :title="`${d.merge_auto ? '系統判斷為同一台' : '人工合併為同一台'}（${d.merge_reason || ''}）：${[d.name, ...d.merged.map(x => `${x.name}${x.role ? '（' + x.role + '）' : ''}`)].join('、')}`"
                    @click.stop="toggleBox(d.key); pickPanel(d)" @pointerdown.stop>推定同一台 ×{{ d.merged.length + 1 }}</span>
            </button>
            <div v-for="x in extraFaces" :key="`x-${x.d.key}-${x.i}`" class="face extra" :class="{ sel: isSel(x.d) }"
                 :style="{ top: `${(heightU - x.b) * U_PX}px`, height: `${(x.b - x.a + 1) * U_PX - 2}px` }"
                 :title="`${x.d.name}：機櫃欄另一段 ${uTxt(x.a, x.b)}（同一筆登記）`" @click="pickPanel(x.d)">
              <span class="fn">{{ panelLabel(x.d).model }}（另一段 {{ uTxt(x.a, x.b) }}）</span>
            </div>
            <div v-if="drag && drag.active && !drag.overRack && drag.u1 >= 1" class="ghost"
                 :style="{ top: `${(heightU - drag.u2) * U_PX}px`, height: `${(drag.u2 - drag.u1 + 1) * U_PX - 2}px` }">
              {{ uTxt(drag.u1, drag.u2) }}
            </div>
            <div v-for="c in xlDrawn" :key="'x' + c.id" class="face xl" :class="'xl-' + c.status"
                 :style="{ top: `${(heightU - c.u_end) * U_PX}px`, height: `${(c.u_end - c.u_start + 1) * U_PX - 2}px` }"
                 :title="xlTitle(c)" @pointerdown="startGhostDrag($event, c)">
              <i class="xdot" :class="'xl-' + c.status" />
              <span class="xrows">
                <span class="xrow">
                  <span class="xlbox xnote" :title="xlParts(c).note">{{ xlParts(c).note }}</span>
                  <span class="xlbox xmodel" :title="xlParts(c).model">{{ up(xlParts(c).model) }}</span>
                  <span class="xlbox xip">{{ xlParts(c).ip }}</span>
                  <span class="xlbox xok" :class="'xl-' + c.status">{{ XL_TEXT[c.status] ?? c.status }}</span>
                </span>
                <span class="xrow">
                  <span class="xlbox xnote" :title="c.sys_serial || xlParts(c).serial">{{ up(c.sys_serial) || xlParts(c).serial }}</span>
                  <span class="xlbox xmodel" :title="(c.sys_systems ?? []).join('、')">{{ (c.sys_systems ?? []).join('、') }}</span>
                  <span class="xlbox xmgmt" :title="c.sys_name ? `系統登記：${c.sys_name}　${c.sys_now || ''}` : ''"></span>
                </span>
              </span>
            </div>
            <div v-for="r in det.reservations ?? []" :key="'r' + r.id" class="face rsv" :class="{ expired: r.expired }"
                 :style="{ top: `${rTop(r)}px`, height: `${rHgt(r)}px` }"
                 :title="`預留：${r.label}（到 ${r.expires_at}${r.expired ? '，已過期' : ''}）${r.created_by ? '　' + r.created_by : ''}${r.note ? '　' + r.note : ''}`">
              <span class="fn">預留：{{ r.label }}</span>
            </div>
          </div>
        </div>
          <div v-if="!compact && (det.legacy_reservations?.length || det.legacy_audits?.length || det.key_warnings?.length)" class="legacy-key">
            <p class="legacy-key-h">舊鍵資料（分點不明，待確認）<InfoNote>分公司拆成各分點前，預留與盤點記在機房「分公司」底下，無法判斷屬於哪個分點。這裡只列出、不改寫、不歸給任何分點；容量與「可放」判斷已保守把舊鍵預留視為佔用。</InfoNote></p>
            <p v-for="w in det.key_warnings ?? []" :key="w" class="legacy-key-w">{{ w }}</p>
            <p v-for="r in det.legacy_reservations ?? []" :key="'lr' + r.id" class="legacy-key-r">舊鍵預留：{{ r.label }}　{{ r.u_start }}{{ r.u_end !== r.u_start ? `-${r.u_end}` : '' }}U　到 {{ r.expires_at }}{{ r.expired ? '（已過期）' : '' }}　（舊鍵資料，分點不明，待確認）</p>
            <p v-for="a in det.legacy_audits ?? []" :key="'la' + a.audit_id" class="legacy-key-a">舊鍵盤點快照：盤點 #{{ a.audit_id }}　帳上 {{ a.book_count }} 台　（舊鍵資料，分點不明，待確認）</p>
          </div>
          <details v-if="!compact && det.excel?.run" class="xlgap">
            <summary class="xlhead">
              Excel 對照：這櫃 {{ det.excel.total }} 格，{{ xlSorted.length }} 格系統還沒畫到
              <span v-if="xlCount.MISSING" class="xlc xl-MISSING">系統沒有 {{ xlCount.MISSING }}</span>
              <span v-if="xlCount.CHECK" class="xlc">待確認 {{ xlCount.CHECK }}</span>
              <span v-if="xlCount.NOPOS" class="xlc">未登位置 {{ xlCount.NOPOS }}</span>
              <span class="xlhint">（點開看清單）</span>
            </summary>
            <details v-for="c in xlSorted" :key="'g' + c.id" class="xlrow">
              <summary>
                <i class="xdot" :class="'xl-' + c.status" />
                <span class="xlu">{{ uTxt(c.u_start, c.u_end) }}</span>
                <span class="xlname">{{ oneLine(c.label) }}</span>
                <span class="xlst" :class="'xl-' + c.status">{{ XL_TEXT[c.status] ?? c.status }}</span>
              </summary>
              <div class="xlsg">
                <b v-if="xlBlocker(c)">這格 U 位系統已有「{{ xlBlocker(c)?.name }}」，所以圖上沒畫虛線框。</b>
                {{ c.suggest }}{{ c.blocker_text ? '（' + c.blocker_text + '）' : '' }}
              </div>
            </details>
          </details>
          <div v-if="lastMove && !compact" class="undobar">
            <span>{{ lastMove.text }}</span>
            <button class="chip" @click="undoMove(lastMove.id, lastMove.text)">復原</button>
            <button class="linkbtn" @click="lastMove = null">關閉</button>
          </div>
          <div v-if="!compact && det.suspect?.length" class="warnbox ovlist">
            疑似登記錯誤 {{ det.suspect.length }} 處<InfoNote>同一段 1–2U 登記了 5 筆以上，實體放不下；不合併、各算一台，請到現場或 CIA 確認。</InfoNote>
            <div v-for="(g, i) in det.suspect" :key="i">
              S{{ i + 1 }}　{{ g.reason }}：
              <button v-for="k in g.keys" :key="k" class="linkbtn" @click="selKey = k">{{ nameOf(k) }}</button>
            </div>
          </div>
          <div v-if="showOverlap && overlapPairs.length" class="warnbox ovlist">
            位置重疊 {{ overlapPairs.length }} 處：
            <div v-for="(p, i) in overlapPairs" :key="i">O{{ i + 1 }}　{{ p.a.name }}（{{ uText(p.a) }}）↔ {{ p.b.name }}（{{ uText(p.b) }}）</div>
          </div>
        </div>
        <div v-if="!compact" class="side">
          <div v-if="invMode" class="inv">
            <div class="bulk"><b>依盤點結果更正</b><span class="muted">改了 {{ invChanged.length }} 台</span>
              <input v-model="invNote" class="note" type="text" placeholder="備註（必填，例：10/23 現場盤點）">
              <button class="chip on" @click="saveInventory">全部儲存</button><button class="chip" @click="invMode = false">取消</button>
            </div>
            <table class="tbl">
              <thead><tr><th>項次</th><th>設備</th><th>機櫃</th><th>起始 U</th><th>U 數</th><th>原值</th></tr></thead>
              <tbody>
                <tr v-for="(d, i) in det.devices" :key="d.key" :class="{ mine: invChanged.includes(d) }">
                  <td class="mono">I{{ i + 1 }}</td><td>{{ d.name }}</td>
                  <td><input v-model="invRows[d.key].rack" class="cell" type="text"></td>
                  <td><input v-model.number="invRows[d.key].u_start" class="cell n" type="number" min="1" max="60"></td>
                  <td><input v-model.number="invRows[d.key].u_height" class="cell n" type="number" min="1" max="20"></td>
                  <td class="mono small">{{ d.u_start ? `${d.u_start}${d.u_end !== d.u_start ? `-${d.u_end}` : ''}U` : '位置不明' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="invResult.length" class="warnbox">
            這幾台更正後 U 位跟別台重疊（已存，請確認現場）：
            <div v-for="r in invResult" :key="r.key">{{ nameOf(r.key) }} ↔ <span v-for="c in r.collisions" :key="c.name">{{ c.name }}（{{ c.u_start }}U）</span></div>
          </div>
          <PositionEditor v-if="editing && det.devices.find(x => x.key === editing)" :key="editing"
                          :device="editTarget(det.devices.find(x => x.key === editing)!)" @saved="load" @close="editing = null" />
          <div v-if="det.can_edit" class="bulk">
            <span class="muted">已選 {{ pickedKeys.length }} 台</span>
            <button class="chip" :disabled="!pickedKeys.length" @click="bulk('off')">標記已關機未下架</button>
            <button class="chip" :disabled="!pickedKeys.length" @click="bulk('removed')">標記已不在櫃上</button>
            <button class="chip" :disabled="!pickedKeys.length" @click="bulk('clear')">清除標記</button>
          </div>
          <button v-if="!compact" class="chip morebtn" @click="showMore = !showMore">{{ showMore ? "收起次要欄位 ▾" : "顯示更多欄位（類型／系統／存在／瓦數／盤點）▸" }}</button>
          <table class="tbl">
            <thead><tr>
              <th v-if="det.can_edit" class="cbcol">選</th>
              <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
              <SortTh k="u_start" :active="sortKey" :dir="sortDir" @sort="toggle">U</SortTh>
              <SortTh k="name" :active="sortKey" :dir="sortDir" @sort="toggle">設備</SortTh>
              <SortTh k="model" :active="sortKey" :dir="sortDir" @sort="toggle">機型</SortTh>
              <SortTh v-if="moreCols" k="type_text" :active="sortKey" :dir="sortDir" @sort="toggle">類型</SortTh>
              <SortTh k="ip_text" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh v-if="moreCols" k="sys_text" :active="sortKey" :dir="sortDir" @sort="toggle">系統</SortTh>
              <SortTh v-if="moreCols" k="exist_sort" :active="sortKey" :dir="sortDir" @sort="toggle">存在</SortTh>
              <SortTh k="state_text" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
              <SortTh v-if="moreCols" k="w_sort" :active="sortKey" :dir="sortDir" @sort="toggle">瓦數</SortTh>
              <SortTh v-if="moreCols" k="audit_text" :active="sortKey" :dir="sortDir" @sort="toggle">上次盤點</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="d in sorted" :id="`rk-${rack}-${d.key}`" :key="d.key" :class="{ mine: d.mine, selrow: selKey === panelOf(d) }"
                  @click="pickRow(d)" @mouseenter="hoverKey = panelOf(d)" @mouseleave="hoverKey = null">
                <td v-if="det.can_edit" class="cbcol"><input v-model="picked[d.key]" type="checkbox"></td>
                <td class="mono">K{{ d.no }}</td>
                <td class="mono" :title="d.position_corrected ? ciaText(d) : ''">{{ d.u_text || '位置不明' }}<span v-if="d.position_corrected" class="fixmark">現場更正</span></td>
                <td><NuxtLink :to="`/assets/${encodeURIComponent(d.asset_serial)}`" class="lnk">{{ d.name }}</NuxtLink></td>
                <td>{{ up(d.model) || '—' }}</td>
                <td v-if="moreCols">{{ d.type_text }}<span class="muted">・{{ d.sys_level_label ?? '未分級' }}</span><span v-if="d.mgmt_of" class="muted">・{{ nameOf(d.mgmt_of) }} 的管理口（{{ d.mgmt_kind || 'OOB' }}）</span><span v-if="d.merged_into" class="muted">・{{ d.role_label || '同一台' }}（推定跟 {{ nameOf(d.merged_into) }} 同一台）</span>
                  <button v-if="d.merged?.length" class="linkbtn" @click.stop="toggleBox(d.key)">推定同一台・{{ d.merged.length + 1 }} 筆 {{ openBox.has(d.key) ? '▾' : '▸' }}</button>
                  <div v-for="g in d.mgmt ?? []" :key="g.key" class="muted small">{{ mgmtText(g) }}</div>
                  <span v-if="d.orphan_port" class="ovmark">管理介面・找不到本體</span><span v-if="suspectSet.has(d.key)" class="ovmark">疑似登記錯誤</span><span v-if="showOverlap && (d.overlap_with ?? []).length" class="ovmark">重疊</span></td>
                <td class="small"><div v-for="x in d.ips ?? []" :key="x.ip" :title="ipSegText(x)">{{ moreCols ? ipSegText(x) : x.ip }}</div><span v-if="!(d.ips ?? []).length" class="muted">—</span></td>
                <td v-if="moreCols">
                  <button v-for="s in d.systems" :key="s.api_id" class="linkbtn" :title="s.name" @click="goSystem(s.api_id)">{{ s.api_id }}</button>
                  <span v-if="!d.systems.length" class="muted">—</span>
                </td>
                <td v-if="moreCols"><span class="xb" :class="{ off: !d.seen }" :title="d.exist_label">{{ d.exist_level ? (EXIST_SHORT[d.exist_level] ?? d.exist_level) : '不明' }} {{ d.exist_score ?? '' }}</span></td>
                <td>
                  <span :class="{ hatch: d.state !== 'active' }">{{ d.state_label || '—' }}</span>
                  <span v-if="d.unobserved" class="unobs">未觀察到</span>
                  <template v-if="d.state === 'pending'">
                    <button class="linkbtn" @click="setState(d, 'active')">確認在用</button>
                    <button class="linkbtn" @click="setState(d, 'removed')">已不在櫃上</button>
                  </template>
                  <button v-if="d.override" class="linkbtn" @click="setState(d, null)">清除確認</button>
                  <button v-if="det.can_edit" class="linkbtn" @click="editing = d.key">更正位置</button>
                </td>
                <td v-if="moreCols" class="mono">{{ d.w != null ? `${d.w} W` : (unknownSet.has(d.key) ? '未知規格' : '—') }}</td>
                <td v-if="moreCols" class="mono small">{{ d.audit_text || '沒盤過' }}</td>
              </tr>
              <tr v-if="!sorted.length"><td :colspan="12" class="muted">沒有設備</td></tr>
            </tbody>
          </table>
          <div class="syslist">
            <span class="muted">這櫃的系統：</span>
            <button v-for="s in det.systems" :key="s.api_id" class="chip" :class="{ on: s.api_id === apiId }" @click="goSystem(s.api_id)">{{ s.api_id }} {{ s.name }}（{{ s.device_count }}）</button>
          </div>
          <div v-if="unplaced.length" class="unpl">
            <span class="muted">位置不明（只知道在這櫃）{{ unplaced.length }} 台{{ canEdit ? '：按住往左邊的櫃子拖進空的 U 位' : '' }}</span>
            <span v-for="d in unplaced" :key="d.key" class="chip unplchip" :class="{ grab: canEdit }" :title="d.name"
                  @pointerdown="startDrag($event, d, 'place')">{{ d.model || '機型不明' }}　{{ d.ip && d.ip !== '0.0.0.0' ? d.ip : d.name }}</span>
          </div>
          <div v-if="moves.length" class="mvlog">
            <b>最近異動</b> <span class="muted">{{ moves.length }} 筆</span>
            <table class="tbl">
              <thead><tr>
                <SortTh k="no" :active="mvKey" :dir="mvDir" @sort="mvToggle">項次</SortTh>
                <SortTh k="at" :active="mvKey" :dir="mvDir" @sort="mvToggle">時間</SortTh>
                <SortTh k="name" :active="mvKey" :dir="mvDir" @sort="mvToggle">設備</SortTh>
                <SortTh k="from_text" :active="mvKey" :dir="mvDir" @sort="mvToggle">從</SortTh>
                <SortTh k="to_text" :active="mvKey" :dir="mvDir" @sort="mvToggle">到</SortTh>
                <SortTh k="by" :active="mvKey" :dir="mvDir" @sort="mvToggle">誰</SortTh>
                <SortTh k="state_text" :active="mvKey" :dir="mvDir" @sort="mvToggle">復原</SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="m in mvSorted" :key="m.id" @click="selKey = m.key">
                  <td class="mono">M{{ m.no }}</td><td class="mono small">{{ m.at }}</td>
                  <td>{{ m.name }}<span v-if="m.count > 1" class="muted">（{{ m.count }} 筆一起）</span></td>
                  <td class="mono">{{ m.from_text }}</td><td class="mono">{{ m.to_text }}</td><td class="small">{{ m.by || '—' }}</td>
                  <td>
                    <span v-if="m.undone" class="muted">已復原（{{ m.undone_by || '—' }}）</span>
                    <button v-else-if="det.can_edit" class="linkbtn" @click.stop="undoMove(m.id, `${m.name}：${m.from_text} → ${m.to_text}`)">復原</button>
                    <span v-else class="muted">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <OverlapGroups :room="det.room" :rack="det.rack" compact @changed="load" />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.rackel { font-family: 'Microsoft JhengHei', sans-serif; }
.rhead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; color: var(--ink); }
.pbtn { font-size: 12px; padding: 3px 10px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); text-decoration: none; }
.pbtn:hover { border-color: var(--brand); }
.mine-tag { font-size: 12px; padding: 2px 9px; border-radius: 10px; background: var(--mint-deep); color: var(--brand-dark); font-weight: 600; }
.rbody { display: grid; grid-template-columns: 510px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 860px) { .rbody { grid-template-columns: 1fr; } }
.frame { position: relative; background: var(--rack-frame); border: 3px solid var(--rack-frame-edge); border-radius: 6px; padding: 8px 8px 8px 30px; }
.rails { position: absolute; left: 0; top: 0; bottom: 0; width: 28px; }
.umark { position: absolute; left: 4px; font-size: 9px; line-height: 16px; color: var(--rack-label); font-family: var(--disp); }
.slots { position: relative; background: repeating-linear-gradient(to bottom, var(--rack-slot) 0 15px, var(--rack-frame) 15px 16px); border-radius: 2px; }
.face { position: absolute; left: 2px; right: 2px; display: flex; align-items: center; gap: 6px; padding: 0 6px; overflow: hidden;
  background: var(--rack-face); border: 1px solid var(--rack-frame-edge); border-radius: 2px; cursor: pointer; font-family: inherit; text-align: left; }
.face.mine { background: color-mix(in srgb, var(--brand) 22%, var(--rack-face)); border-color: var(--brand); box-shadow: inset 3px 0 0 var(--brand); }
.face.other { opacity: .55; }
.face.dormant { background: repeating-linear-gradient(45deg, var(--rack-face) 0 5px, var(--chart-gray) 5px 7px); }
.hatch { display: inline-block; padding: 0 6px; border-radius: 4px; background: repeating-linear-gradient(45deg, var(--sub) 0 4px, var(--line) 4px 6px); }
.pline { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; font-size: 12.5px; color: var(--ink-soft); margin-bottom: 10px; }
.pline b { font-family: var(--disp); color: var(--ink); }
.sl { font-size: 12px; color: var(--ink-aux); background: var(--sub); border: 1px solid var(--border); border-radius: 8px; padding: 1px 8px; }
.face:hover { outline: 2px solid var(--brand-hover); }
.face.hit { background: color-mix(in srgb, var(--brand) 45%, var(--rack-face)); border-color: var(--brand-dark); box-shadow: 0 0 0 2px var(--brand-dark); opacity: 1; z-index: 1; }
.rbody.compact { grid-template-columns: 1fr; }
.rh { position: absolute; left: 0; right: 0; height: 5px; cursor: ns-resize; z-index: 3; }
.rh.top { top: -2px; }
.rh.bot { bottom: -2px; }
.face:hover .rh { background: color-mix(in srgb, var(--brand) 35%, transparent); }
.mbtn { flex: none; font-size: 10px; padding: 0 6px; border-radius: 6px; border: 1px solid var(--brand); color: var(--brand-dark); background: var(--card); cursor: pointer; }
.pick { flex: none; font-style: normal; font-size: 11px; color: var(--brand-dark); font-weight: 700; }
.ghost { position: absolute; left: 2px; right: 2px; border: 2px dashed var(--brand-dark); background: color-mix(in srgb, var(--brand) 15%, transparent); color: var(--brand-dark); font-size: 11px; z-index: 4; pointer-events: none; display: flex; align-items: center; justify-content: center; }
.frame.dragging .face { opacity: .75; }
.multibar { display: flex; gap: 6px; align-items: center; font-size: 12px; margin-bottom: 6px; }
.mvmask { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); z-index: 1000; display: flex; align-items: center; justify-content: center; }
.mvbox { background: var(--card); border-radius: 12px; padding: 16px 18px; width: min(520px, 92vw); display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
.mvtxt { font-size: 15px; font-weight: 600; color: var(--ink); margin: 0; }
.mvbox .note { width: 100%; }
.face.xl { background: #f7f4ea; border: 1px solid #b9ad82; border-left: 6px solid var(--warn); color: #1f2a26; cursor: default; font-weight: 500; }
.face.xl { gap: 5px; padding: 0 5px; }
.face, .face.xl { padding-right: 22px; }
.face .go { position: absolute; right: 6px; top: 50%; transform: translateY(-50%); }
.xdot { width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--warn); box-shadow: 0 0 0 2px rgba(0,0,0,.12); }
.xdot.xl-MISSING { background: var(--bad); }
.xdot.xl-OK { background: var(--good); }
.xdot.xl-ABSENT { background: var(--chart-gray); }
.xdot.xl-POSONLY { background: #b8860b; }
.xdot.xl-NOPOS, .xdot.xl-OTHERROOM { background: var(--warn); }
.xdot.xl-BLOCKED, .xdot.xl-MOVED { background: #b8860b; }
.xrows { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.xrow { display: flex; gap: 5px; align-items: center; min-width: 0; }
.xlbox.xedited { box-shadow: inset 0 -2px 0 var(--warn); }
.xlbox { display: block; min-height: 18px; border: 1px solid #b9ad82; border-radius: 3px; padding: 0 5px; font-size: 12px; line-height: 16px; background: rgba(255,255,255,.75); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.xnote { flex: 20 1 0; }
.xmodel { flex: 24 1 0; }
.xip { flex: 28 1 0; font-family: var(--disp); text-align: center; }
.xmgmt { flex: 56 1 0; }
.xok { flex: 28 1 0; text-align: center; font-weight: 700; color: #7a4b00; }
.xok.xl-MISSING { color: var(--bad); border-color: var(--bad); }
.face.xl-MISSING { background: #fbeeed; border-color: #d9a6a1; border-left-color: var(--bad); }
.xlgap { margin-top: 10px; font-size: 12px; max-width: 900px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.xlhead { font-weight: 600; color: var(--ink); cursor: pointer; padding: 7px 10px; list-style: none; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.xlhead::-webkit-details-marker, .xlrow > summary::-webkit-details-marker { display: none; }
.xlc { font-weight: 600; font-size: 11.5px; padding: 0 8px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); }
.xlc.xl-MISSING { background: var(--bad-soft); color: var(--bad); border-color: var(--bad); }
.xlhint { font-weight: 400; color: var(--ink-aux); }
.xlrow { border-top: 1px solid var(--line); }
.xlrow > summary { display: grid; grid-template-columns: 14px 64px minmax(0, 1fr) 120px; gap: 8px; padding: 5px 10px; cursor: pointer; align-items: center; list-style: none; }
.xlu { font-family: var(--disp); color: var(--ink-soft); }
.xlname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
.xlst { font-weight: 600; color: var(--warn-text); white-space: nowrap; }
.xlst.xl-MISSING { color: var(--bad); }
.xlsg { color: var(--ink-soft); padding: 2px 10px 8px 96px; }
.face.sel { outline: 3px solid var(--brand-dark); outline-offset: 0; z-index: 2; }
.go { flex: none; margin-left: auto; font-size: 11px; padding: 0 4px; border-radius: 4px; color: var(--link); cursor: pointer; }
.go:hover { background: var(--card); text-decoration: underline; }
.tbl tr.selrow td { background: var(--mint-deep); }
.tbl tbody tr { cursor: pointer; }
.tag { flex: none; font-family: var(--disp); font-size: 10px; color: var(--ink-aux); white-space: nowrap; }
.tag.none { color: var(--muted); font-style: italic; }
.mgmtc.twin { border-color: var(--brand); color: var(--brand-dark); font-weight: 600; }
.rackel.big .tag { font-size: 11.5px; }
.mgmtc { flex: none; font-size: 9.5px; padding: 0 5px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); white-space: nowrap; }
.rackel.big .fn { font-size: 12.5px; font-weight: 600; }
.rackel.big .fm { font-size: 11px; }
.rackel.big .umark { font-size: 10px; }
.rackel.big .mgmtc { font-size: 10.5px; }
.face.st-down { background: color-mix(in srgb, var(--bad) 55%, var(--rack-face)); border-color: var(--bad); box-shadow: 0 0 0 2px var(--bad); opacity: 1; z-index: 1; }
.face.st-up { background: color-mix(in srgb, var(--good) 45%, var(--rack-face)); border-color: var(--good); box-shadow: 0 0 0 2px var(--good); opacity: 1; z-index: 1; }
.face.st-untested { background: color-mix(in srgb, var(--chart-gray) 60%, var(--rack-face)); border-color: var(--chart-gray); box-shadow: 0 0 0 2px var(--chart-gray); opacity: 1; z-index: 1; }
.ovmark { display: inline-block; margin-left: 4px; font-size: 9.5px; padding: 0 5px; border-radius: 6px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); white-space: nowrap; }
.face.ovl { border-color: var(--warn); }
.ovlist { margin-top: 8px; }
.face.extra { background: color-mix(in srgb, var(--rack-face) 70%, var(--card)); border-style: dashed; cursor: pointer; }
.undobar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; padding: 6px 8px; border-radius: 8px; background: var(--mint); border: 1px solid var(--brand); font-size: 12.5px; color: var(--ink); }
.mvrow { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.okmark { font-size: 12px; color: var(--brand-dark); font-weight: 600; }
.mvlog { margin-top: 8px; }
.led { width: 7px; height: 7px; border-radius: 50%; background: var(--chart-gray); flex: none; }
.led.on { background: var(--good); box-shadow: 0 0 4px var(--good); }
.led.hollow { background: transparent; border: 1.5px solid var(--good); box-shadow: none; }
.face.rsv { background: var(--card); border: 1.5px dashed var(--brand); cursor: default; }
.face.rsv.expired { border-color: var(--chart-gray); opacity: .7; }
.fixmark { display: inline-block; margin-left: 4px; font-size: 9.5px; padding: 0 5px; border-radius: 6px; border: 1px dashed var(--brand); color: var(--brand-dark); background: var(--card); white-space: nowrap; }
.inv { margin-bottom: 8px; }
.note { font-family: inherit; font-size: 12px; padding: 3px 8px; width: 220px; border: 1px solid var(--border-strong); border-radius: 6px; }
.mvbox .err { color: var(--bad); margin: 0; }
.cell { font-family: inherit; font-size: 12px; padding: 2px 5px; width: 70px; border: 1px solid var(--border-strong); border-radius: 4px; }
.cell.n { width: 54px; }
.warnbox { color: var(--warn-text); background: var(--warn-soft); border-radius: 8px; padding: 6px 8px; font-size: 12px; margin-bottom: 8px; }
.small { font-size: 11px; }
.unobs { display: inline-block; margin-left: 4px; font-size: 10.5px; padding: 0 6px; border-radius: 8px; border: 1px solid var(--border-strong); color: var(--ink-aux); }
.bulk { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.cbcol { width: 28px; }
.fn { font-size: 10.5px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fm { font-size: 9.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.side { min-width: 0; overflow-x: auto; }
.side .tbl { min-width: 560px; }
.moremenu { display: inline-block; position: relative; }
.moremenu > summary { cursor: pointer; list-style: none; display: inline-block; }
.moremenu > summary::-webkit-details-marker { display: none; }
.moreitems { display: flex; gap: 6px; flex-wrap: wrap; padding: 6px 4px; }
.powermore .moreitems { gap: 10px; align-items: center; font-size: 12px; color: var(--ink-soft); }
.unpl { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 6px 0; }
.unplchip.grab { cursor: grab; touch-action: none; }
.face.xl { touch-action: none; }
.morebtn { margin: 4px 0 6px; }
.unobs { white-space: nowrap; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; margin-bottom: 10px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 6px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); }
.tbl tr.mine td { background: var(--mint); }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.linkbtn { background: none; border: 0; padding: 0 6px 0 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; }
.linkbtn:hover { text-decoration: underline; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.syslist { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.xb { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 10px; background: var(--good-soft); color: var(--brand-dark); white-space: nowrap; }
.xb.off { background: var(--warn-soft); color: var(--warn-text); }
</style>
