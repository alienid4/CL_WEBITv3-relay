<script setup lang="ts">
import type { ColorBy, IpSeg } from '~/composables/useRackColors'
// 擬真機櫃正面：深色機櫃外框、每台設備一塊面板（佔它的 U 高）、存在可信度燈號、本系統設備高亮。
// 系統主機部署圖的「機櫃」頁簽與 4-5 機房架構圖共用（資料：/api/racks/detail，rack_layout.rack_detail）。
// 顏色：外框用 main.css 的 --rack-*（實物顏色），其餘用語意變數。
// hiKeys：要亮的設備（4-1 平面圖的搜尋命中）；compact：只畫機櫃正面（平面圖下方並排用，不帶側邊表格）。
// colorBy：面板左邊色條依機器類型／系統等級（4-1 平面圖的「顏色依」）；U 位重疊的設備左右並排、標「重疊」。
const props = defineProps<{ room: string; rack: string; apiId?: string | null; hiKeys?: string[]; compact?: boolean; colorBy?: ColorBy | null; showOverlap?: boolean }>()
const hiSet = computed(() => new Set(props.hiKeys ?? []))

interface Dev {
  key: string; asset_serial: string; name: string; ip: string | null; model: string | null
  u_start: number | null; u_end: number | null; rack_raw: string | null
  exist_level: string | null; exist_score: number | null; exist_label: string; seen: boolean
  systems: { api_id: string; name: string }[]; mine: boolean
  state: 'active' | 'off' | 'pending' | null; state_label: string | null; asset_status: string | null
  w: number | null; kg: number | null; spec_model: string | null; override: string | null; unobserved: boolean
  position_corrected?: boolean; position_note?: string | null; position_by?: string | null; position_at?: string | null
  cia_position?: { room: string; rack: string | null; u_start: number | null; u_end: number | null; raw: string | null } | null
  machine_type?: string; machine_type_label?: string; sys_level?: string; sys_level_label?: string
  ips?: IpSeg[]; envs?: string[]; os?: string | null; overlap_with?: string[]
}
interface Power {
  active_count: number; off_count: number; pending_count: number; w: number; amps: number | null; btu_h: number
  volts: number; kg: number; if_all_on_w: number; used_u: number; free_u: number; largest_free_u: number
  known_count: number; unknown_count: number; unknown_keys: string[]; unobserved_count: number; unobserved_keys: string[]
  system_level: { model: string; records: number; w: number | null; kg: number | null; u: number | null }[]
  vendor_move: string[]
}
interface Detail {
  room: string; rack: string; floor: string | null; height_u: number; devices: Dev[]
  systems: { api_id: string; name: string; device_count: number }[]
  mine_count: number; others_count: number; no_u_count: number; power: Power; can_edit?: boolean
  reservations?: { id: number; label: string; u_start: number; u_end: number; expires_at: string; expired: boolean; created_by: string | null; note: string | null }[]
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

const U_PX = 16
const placed = computed(() => (det.value?.devices ?? []).filter(d => d.u_start))
const unplaced = computed(() => (det.value?.devices ?? []).filter(d => !d.u_start))
const heightU = computed(() => Math.max(det.value?.height_u ?? 42, ...placed.value.map(d => d.u_end ?? 0)))
function rTop(r: { u_end: number }) { return (heightU.value - r.u_end) * U_PX }
function rHgt(r: { u_start: number; u_end: number }) { return (r.u_end - r.u_start + 1) * U_PX - 2 }
function top(d: Dev) { return (heightU.value - (d.u_end ?? 0)) * U_PX }
// U 位重疊：同一群（互相重疊連起來的）左右分欄
const lanes = computed(() => {
  const byKey = new Map(placed.value.map(d => [d.key, d]))
  const out: Record<string, { lane: number; n: number }> = {}
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
    for (const g of grp) out[g.key] = { lane: lane[g.key], n }
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
function faceTitle(d: Dev) {
  const ips = (d.ips ?? []).map(ipSegText).join('\n')
  const ov = (d.overlap_with ?? []).length ? `\n位置重疊：${(d.overlap_with ?? []).map(nameOf).join('、')}` : ''
  return `${d.name}　${d.model || ''}　${d.systems.map(s => s.api_id).join(', ')}\n類型：${d.machine_type_label ?? '不明'}　等級：${d.sys_level_label ?? '未分級'}${ips ? '\n' + ips : ''}${ov}${d.position_corrected ? '\n' + ciaText(d) : ''}`
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
function uText(d: Dev) { return d.u_start ? (d.u_start === d.u_end ? `${d.u_start}U` : `${d.u_start}-${d.u_end}U`) : '' }
function hgt(d: Dev) { return ((d.u_end ?? 0) - (d.u_start ?? 0) + 1) * U_PX - 2 }
const uMarks = computed(() => Array.from({ length: heightU.value }, (_, i) => heightU.value - i))

// 側邊清單（可排序、有項次）；可只看「同櫃其他設備」
const onlyOthers = ref(false)
const listRows = computed(() => (det.value?.devices ?? []).map((d, i) => ({
  ...d, no: i + 1, u_text: d.u_start ? (d.u_start === d.u_end ? `${d.u_start}U` : `${d.u_start}-${d.u_end}U`) : '',
  sys_text: d.systems.map(s => s.api_id).join(', '), exist_sort: d.exist_score ?? -1, mine_text: d.mine ? '本系統' : '',
  w_sort: d.w ?? -1, state_text: d.state_label ?? '', type_text: d.machine_type_label ?? '不明',
  ip_text: (d.ips ?? []).map(x => x.ip).join(', '),
})).filter(d => (!onlyOthers.value || !d.mine) && (!unknownOnly.value || unknownSet.value.has(d.key))
  && (!unobsOnly.value || unobsSet.value.has(d.key))))
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
    await apiFetch('/api/racks/state/bulk', { method: 'PUT', body: { keys: pickedKeys.value, state: kind === 'clear' ? null : kind, note: '現場盤點' } })
    showToast(`已${BULK_LABEL[kind]} ${n} 台`, 'success')
    picked.value = {}
    await load()
  } catch (e: any) {
    showToast(`批次標記失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
async function setState(d: Dev, state: 'active' | 'removed' | null) {
  try {
    await apiFetch('/api/racks/state', { method: 'PUT', body: { key: d.key, state, note: null } })
    showToast(state === 'active' ? `${d.name} 已確認在用` : state === 'removed' ? `${d.name} 已標成不在櫃上` : '已清除人工確認', 'success')
    await load()
  } catch (e: any) {
    showToast(`更新失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
function goAsset(d: Dev) { navigateTo(`/assets/${encodeURIComponent(d.asset_serial)}`) }
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
    const r = await apiFetch<{ results: any[] }>('/api/racks/position/bulk', { method: 'PUT', body: {
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
function nameOf(k: string) { return det.value?.devices.find(d => d.key === k)?.name ?? k }
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
function goSystem(api: string) { navigateTo({ path: '/topology', query: { system: api } }) }
</script>

<template>
  <div class="rackel">
    <p v-if="loading" class="muted">載入中…</p>
    <p v-else-if="err" class="err">機櫃載入失敗：{{ err }}</p>
    <template v-else-if="det">
      <div v-if="compact" class="rhead">
        <b>{{ det.rack }}</b>
        <span class="muted">{{ det.devices.length }} 台</span>
        <span v-if="hiSet.size" class="mine-tag">符合 {{ det.devices.filter(d => hiSet.has(d.key)).length }}</span>
      </div>
      <div v-else class="rhead">
        <b>{{ det.room }}　{{ det.rack }}</b>
        <span v-if="det.floor" class="muted">{{ det.floor }}</span>
        <span class="muted">{{ det.devices.length }} 台</span>
        <span v-if="apiId" class="mine-tag">本系統 {{ det.mine_count }}</span>
        <button v-if="apiId" class="chip" :class="{ on: onlyOthers }" @click="onlyOthers = !onlyOthers">同櫃其他設備 {{ det.others_count }} 台</button>
        <a class="pbtn" :href="`/print/rack-sheet?room=${encodeURIComponent(det.room)}&rack=${encodeURIComponent(det.rack)}${apiId ? `&system=${encodeURIComponent(apiId)}` : ''}`" target="_blank">列印盤點表</a>
        <a class="pbtn" :href="`/print/labels?room=${encodeURIComponent(det.room)}&rack=${encodeURIComponent(det.rack)}`" target="_blank">印標籤</a>
        <button v-if="det.can_edit && !invMode" class="pbtn" @click="startInventory">依盤點結果更正</button>
        <a class="pbtn" :href="`${apiBase}/api/racks/position/pending-cia.xlsx`">CIA 待更正（xlsx）</a>
        <InfoNote>機櫃高度沒有資料，以 {{ det.height_u }}U 畫；只有機櫃代碼、沒寫 U 位的設備列在下方「位置不明」。燈號：綠＝實際看得到（存在可信度達標），灰＝只有登記。</InfoNote>
      </div>
      <div v-if="!compact" class="pline">
        <span><b>{{ (det.power.w / 1000).toFixed(2) }} kW</b>（{{ det.power.amps ?? '—' }} A @{{ det.power.volts }}V、{{ det.power.btu_h.toLocaleString() }} BTU/h）</span>
        <span>{{ det.power.kg }} kg</span>
        <span>已用 {{ det.power.used_u }}U／空 {{ det.power.free_u }}U（最大連續 {{ det.power.largest_free_u }}U）</span>
        <span v-if="det.power.if_all_on_w">若全部開機 +{{ det.power.if_all_on_w }} W</span>
        <button v-if="det.power.unknown_count" class="chip" :class="{ on: unknownOnly }" @click="unknownOnly = !unknownOnly">未知規格 {{ det.power.unknown_count }} 台</button>
        <button v-if="det.power.unobserved_count" class="chip" :class="{ on: unobsOnly }" @click="unobsOnly = !unobsOnly">其中未觀察到 {{ det.power.unobserved_count }} 台</button>
        <span v-for="s in det.power.system_level" :key="s.model" class="sl">工程系統 {{ s.model }}：{{ s.records }} 筆合計一套（{{ s.u ?? '?' }}U、{{ s.kg ?? '?' }} kg、{{ s.w ?? '用電未知' }}{{ s.w ? ' W' : '' }}）</span>
        <span v-if="det.power.vendor_move.length" class="sl">需原廠搬遷：{{ det.power.vendor_move.join('、') }}</span>
        <InfoNote>機櫃口徑看的是「設備實體在不在櫃上」，跟全站台數（存在可信度）是兩回事：使用中＋有機櫃＝在用，算電、算 U、算重量、算台數，系統看不到它（只有登記）就另標「未觀察到」（空心燈）；停用／閒置＋有機櫃＝關機未下架；報廢＋有機櫃＝待確認（可能還在櫃上）。關機未下架與待確認佔 U、算重量、用電算 0，另列「若全部開機」。現場盤點結果可以人工標記（關機未下架／已不在櫃上／清除），每次都留稽核紀錄；「已不在櫃上」的設備從機櫃拿掉。</InfoNote>
        <InfoNote label="電">用電＝每台在用設備「一顆」PSU 的額定瓦數（型號規格表，取 200–240V 可用的最大 PSU），不是加總，也不是實際耗電；電流＝瓦數 ÷ 機房電壓（系統設定 room_volts，預設 220V）；散熱 BTU/h＝瓦數 × 3.412。關機未下架（停用／閒置）與待確認（報廢、或只有登記看不到）的設備佔 U、算重量，用電算 0，另列「若全部開機」。型號對不到規格、或 CIA 只寫籠統描述的，算「未知規格」不算進用電。工程系統（Exadata／ODA）同櫃同型號達 {{ 3 }} 筆以上時，整套數字只算一次。</InfoNote>
      </div>
      <div class="rbody" :class="{ compact }">
        <div>
        <div class="frame" :style="{ height: `${heightU * U_PX + 16}px` }">
          <div class="rails">
            <span v-for="u in uMarks" :key="u" class="umark" :style="{ top: `${(heightU - u) * U_PX + 8}px` }">{{ u % 5 === 0 || u === 1 ? u : '' }}</span>
          </div>
          <div class="slots" :style="{ height: `${heightU * U_PX}px` }">
            <button v-for="d in placed" :key="d.key" class="face" :class="{ mine: d.mine, other: apiId && !d.mine, dormant: d.state !== 'active', hit: hiSet.has(d.key), ovl: !!lanes[d.key] }"
                    :style="faceStyle(d)" :title="faceTitle(d)" @click="goAsset(d)">
              <i class="led" :class="{ on: d.seen, hollow: d.unobserved }" />
              <span class="fn">{{ d.state !== 'active' && d.state_label ? `［${d.state_label}］` : '' }}{{ d.name }}</span>
              <span v-if="d.position_corrected" class="fixmark">現場更正</span>
              <span v-if="showOverlap && lanes[d.key]" class="ovmark">重疊</span>
              <span v-if="hgt(d) > 20" class="fm">{{ d.model || '' }}</span>
            </button>
            <div v-for="r in det.reservations ?? []" :key="'r' + r.id" class="face rsv" :class="{ expired: r.expired }"
                 :style="{ top: `${rTop(r)}px`, height: `${rHgt(r)}px` }"
                 :title="`預留：${r.label}（到 ${r.expires_at}${r.expired ? '，已過期' : ''}）${r.created_by ? '　' + r.created_by : ''}${r.note ? '　' + r.note : ''}`">
              <span class="fn">預留：{{ r.label }}</span>
            </div>
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
          <table class="tbl">
            <thead><tr>
              <th v-if="det.can_edit" class="cbcol">選</th>
              <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
              <SortTh k="u_start" :active="sortKey" :dir="sortDir" @sort="toggle">U</SortTh>
              <SortTh k="name" :active="sortKey" :dir="sortDir" @sort="toggle">設備</SortTh>
              <SortTh k="model" :active="sortKey" :dir="sortDir" @sort="toggle">機型</SortTh>
              <SortTh k="type_text" :active="sortKey" :dir="sortDir" @sort="toggle">類型</SortTh>
              <SortTh k="ip_text" :active="sortKey" :dir="sortDir" @sort="toggle">IP／網段</SortTh>
              <SortTh k="sys_text" :active="sortKey" :dir="sortDir" @sort="toggle">系統</SortTh>
              <SortTh k="exist_sort" :active="sortKey" :dir="sortDir" @sort="toggle">存在</SortTh>
              <SortTh k="state_text" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
              <SortTh k="w_sort" :active="sortKey" :dir="sortDir" @sort="toggle">瓦數</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="d in sorted" :key="d.key" :class="{ mine: d.mine }">
                <td v-if="det.can_edit" class="cbcol"><input v-model="picked[d.key]" type="checkbox"></td>
                <td class="mono">K{{ d.no }}</td>
                <td class="mono" :title="d.position_corrected ? ciaText(d) : ''">{{ d.u_text || '位置不明' }}<span v-if="d.position_corrected" class="fixmark">現場更正</span></td>
                <td><NuxtLink :to="`/assets/${encodeURIComponent(d.asset_serial)}`" class="lnk">{{ d.name }}</NuxtLink></td>
                <td>{{ d.model || '—' }}</td>
                <td>{{ d.type_text }}<span class="muted">・{{ d.sys_level_label ?? '未分級' }}</span><span v-if="showOverlap && (d.overlap_with ?? []).length" class="ovmark">重疊</span></td>
                <td class="small"><div v-for="x in d.ips ?? []" :key="x.ip">{{ ipSegText(x) }}</div><span v-if="!(d.ips ?? []).length" class="muted">—</span></td>
                <td>
                  <button v-for="s in d.systems" :key="s.api_id" class="linkbtn" :title="s.name" @click="goSystem(s.api_id)">{{ s.api_id }}</button>
                  <span v-if="!d.systems.length" class="muted">—</span>
                </td>
                <td><span class="xb" :class="{ off: !d.seen }" :title="d.exist_label">{{ d.exist_level ? (EXIST_SHORT[d.exist_level] ?? d.exist_level) : '不明' }} {{ d.exist_score ?? '' }}</span></td>
                <td>
                  <span :class="{ hatch: d.state !== 'active' }">{{ d.state_label || '—' }}</span>
                  <span v-if="d.unobserved" class="unobs">未觀察到</span>
                  <template v-if="d.state === 'pending'">
                    <button class="linkbtn" @click="setState(d, 'active')">確認在用</button>
                    <button class="linkbtn" @click="setState(d, 'removed')">已不在櫃上</button>
                  </template>
                  <button v-if="d.override" class="linkbtn" @click="setState(d, null)">清除確認</button>
                </td>
                <td class="mono">{{ d.w != null ? `${d.w} W` : (unknownSet.has(d.key) ? '未知規格' : '—') }}
                  <button v-if="det.can_edit" class="linkbtn" @click="editing = d.key">更正位置</button>
                </td>
              </tr>
              <tr v-if="!sorted.length"><td :colspan="det.can_edit ? 11 : 10" class="muted">沒有設備</td></tr>
            </tbody>
          </table>
          <div class="syslist">
            <span class="muted">這櫃的系統：</span>
            <button v-for="s in det.systems" :key="s.api_id" class="chip" :class="{ on: s.api_id === apiId }" @click="goSystem(s.api_id)">{{ s.api_id }} {{ s.name }}（{{ s.device_count }}）</button>
          </div>
          <p v-if="unplaced.length" class="muted">位置不明（只知道在這櫃）：{{ unplaced.length }} 台</p>
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
.rbody { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: start; }
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
.ovmark { display: inline-block; margin-left: 4px; font-size: 9.5px; padding: 0 5px; border-radius: 6px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); white-space: nowrap; }
.face.ovl { border-color: var(--warn); }
.ovlist { margin-top: 8px; }
.led { width: 7px; height: 7px; border-radius: 50%; background: var(--chart-gray); flex: none; }
.led.on { background: var(--good); box-shadow: 0 0 4px var(--good); }
.led.hollow { background: transparent; border: 1.5px solid var(--good); box-shadow: none; }
.face.rsv { background: var(--card); border: 1.5px dashed var(--brand); cursor: default; }
.face.rsv.expired { border-color: var(--chart-gray); opacity: .7; }
.fixmark { display: inline-block; margin-left: 4px; font-size: 9.5px; padding: 0 5px; border-radius: 6px; border: 1px dashed var(--brand); color: var(--brand-dark); background: var(--card); white-space: nowrap; }
.inv { margin-bottom: 8px; }
.note { font-family: inherit; font-size: 12px; padding: 3px 8px; width: 220px; border: 1px solid var(--border-strong); border-radius: 6px; }
.cell { font-family: inherit; font-size: 12px; padding: 2px 5px; width: 70px; border: 1px solid var(--border-strong); border-radius: 4px; }
.cell.n { width: 54px; }
.warnbox { color: var(--warn-text); background: var(--warn-soft); border-radius: 8px; padding: 6px 8px; font-size: 12px; margin-bottom: 8px; }
.small { font-size: 11px; }
.unobs { display: inline-block; margin-left: 4px; font-size: 10.5px; padding: 0 6px; border-radius: 8px; border: 1px solid var(--border-strong); color: var(--ink-aux); }
.bulk { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.cbcol { width: 28px; }
.fn { font-size: 10.5px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fm { font-size: 9.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.side { min-width: 0; }
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
