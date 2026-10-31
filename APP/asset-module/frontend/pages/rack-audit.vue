<script setup lang="ts">
// 4 機房與容量 › 機櫃盤點（2026-10-27）：開始盤點（凍結今天的帳）→ 下載離線盤點檔 → 現場只改例外 → 上傳／網頁填
// → 差異清單（每列有下一步）→ 主管頁、匯出報告。邏輯都在後端 rack_inventory.py；這裡不另算數字。
interface Audit { id: number; title: string; status: string; baseline_at: string; created_by: string | null; rack_total: number; book_count: number; rack_done: number; diff_count: number; diff_open: number; rooms: string[] }
interface RackRow { room: string; rack: string; book_count: number; done: boolean; counted: number; result_counts: Record<string, number> | null; extra_count: number; counted_by: string | null; counted_on: string | null; source: string | null; version: number; submitted_at: string | null; diff_count: number; diff_open: number; row_keys: string[] }
interface Detail { id: number; title: string; status: string; baseline_at: string; created_by: string | null; racks: RackRow[]; extra_days: number; can_edit: boolean }
interface Diff { id: number; diff_key: string; room: string; rack: string; kind: string; label: string; next_step: string; action: string | null; book: string; found: string; key: string | null; no: number | null; name: string; asset_serial: string; model: string; u: string; counted_by: string; counted_on: string; note: string; status: string; reason: string | null; status_by: string | null; status_at: string | null; during_change: string[]; due: string | null; aging_days: number | null; overdue: boolean }
interface Summary { rack_total: number; rack_done: number; book_count: number; counted: number; people: string[]; diff_total: number; diff_open: number; diff_overdue: number; by_kind: Record<string, Record<string, number>>; serial_missing: any[]; serial_found: any[]; baseline_at: string }
interface SheetRow { key: string; no: number; u: string; model: string | null; asset_serial: string; hw_serial: string; name: string; name_text: string; result: string; correct_u: string; correct_model: string; correct_serial: string; note: string; state: string }
interface Sheet { room: string; rack: string; rows: SheetRow[]; extras: { model: string; serial: string; u: string; note: string }[]; done: boolean; counted_by: string; counted_on: string; history: any[] }

const { apiFetch } = useApi()
const { showToast } = useToast()
const { guardedFetch } = useGuardedFetch()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

// ===== 盤點清單＋開始盤點 =====
const audits = ref<Audit[]>([])
const canEdit = ref(false)
const listErr = ref('')
async function loadList() {
  try {
    const r = await apiFetch<{ items: Audit[]; can_edit: boolean }>('/api/rack-audits')
    audits.value = r.items
    canEdit.value = r.can_edit
    listErr.value = ''
  } catch (e: any) { listErr.value = `盤點清單載入失敗：${errMsg(e)}` }
}
const listRows = computed(() => audits.value.map((a, i) => ({ ...a, no: audits.value.length - i, prog: a.rack_total ? a.rack_done / a.rack_total : 0, rooms_text: a.rooms.join('、') })))
const L = useSort(listRows, 'no', 'desc')

const rooms = ref<{ room: string; racks: string[] }[]>([])
async function loadRooms() {
  try {
    const dc = await apiFetch<any>('/api/racks/datacenter')
    rooms.value = dc.rooms.filter((r: any) => r.racks.length).map((r: any) => ({ room: r.room, racks: r.racks.map((t: any) => t.rack) }))
  } catch (e: any) { showToast(`機房清單載入失敗：${errMsg(e)}`, 'error') }
}
const startOpen = ref(false)
const startRoom = ref('')
const startPick = ref<Record<string, boolean>>({})
const startBusy = ref(false)
const startRacks = computed(() => rooms.value.find(r => r.room === startRoom.value)?.racks ?? [])
const pickedRacks = computed(() => Object.keys(startPick.value).filter(k => startPick.value[k]))
function openStart(room?: string, rack?: string) {
  startOpen.value = true
  startRoom.value = room || rooms.value[0]?.room || ''
  startPick.value = rack ? { [rack]: true } : {}
}
async function startAudit() {
  const racks = pickedRacks.value
  const scope = racks.length ? `${startRoom.value} ${racks.join('、')}` : `${startRoom.value} 每一櫃（${startRacks.value.length} 櫃）`
  if (!window.confirm(`開始盤點：${scope}？\n會把今天的帳凍結成基準（之後系統裡的異動不影響這次的盤點表）。`)) return
  startBusy.value = true
  try {
    const r = await guardedFetch<{ id: number; racks: number; boxes: number }>('/api/rack-audits', { method: 'POST',
      body: racks.length ? { racks: racks.map(rack => ({ room: startRoom.value, rack })) } : { room: startRoom.value } })
    showToast(`已開始盤點 #${r.id}：${r.racks} 櫃、${r.boxes} 台`, 'success')
    startOpen.value = false
    await loadList()
    openAudit(r.id)
  } catch (e: any) { showToast(`開始盤點失敗：${errMsg(e)}`, 'error') } finally { startBusy.value = false }
}

// ===== 單次盤點 =====
type Tab = 'boss' | 'racks' | 'diffs' | 'fill'
const TABS: { k: Tab; label: string }[] = [{ k: 'boss', label: '總覽' }, { k: 'racks', label: '各櫃進度' }, { k: 'diffs', label: '差異清單' }, { k: 'fill', label: '網頁填寫' }]
const aid = computed(() => Number(route.query.id) || null)
const tab = computed<Tab>({
  get: () => (TABS.some(t => t.k === route.query.tab) ? route.query.tab as Tab : 'boss'),
  set: (v) => router.replace({ query: { ...route.query, tab: v } }),
})
function openAudit(id: number | null) { router.replace({ query: id ? { id: String(id), tab: 'boss' } : {} }) }
const det = ref<Detail | null>(null)
const sum = ref<Summary | null>(null)
const diffList = ref<Diff[]>([])
const detErr = ref('')
async function loadAudit() {
  if (!aid.value) { det.value = null; return }
  try {
    const [d, s, f] = await Promise.all([
      apiFetch<Detail>(`/api/rack-audits/${aid.value}`), apiFetch<Summary>(`/api/rack-audits/${aid.value}/summary`),
      apiFetch<{ items: Diff[] }>(`/api/rack-audits/${aid.value}/diffs`)])
    det.value = d; sum.value = s; diffList.value = f.items; detErr.value = ''
  } catch (e: any) { detErr.value = `盤點載入失敗：${errMsg(e)}` }
}
watch(aid, loadAudit, { immediate: true })
const open = computed(() => det.value?.status === 'open')

// 各櫃進度
const rackFilter = ref<'all' | 'done' | 'todo'>('all')
const rackRows = computed(() => (det.value?.racks ?? []).map((r, i) => ({ ...r, no: i + 1, done_text: r.done ? '已盤' : '未盤',
  ok: r.result_counts?.['在'] ?? 0, gone: r.result_counts?.['不在'] ?? 0, bad: r.result_counts?.['不符'] ?? 0 }))
  .filter(r => rackFilter.value === 'all' || (rackFilter.value === 'done') === r.done))
const R = useSort(rackRows, 'no')

// 上傳
const upBusy = ref(false)
const upRes = ref<{ ok: boolean; errors: string[]; warnings: string[]; saved: any[]; skipped: any[] } | null>(null)
async function upload(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0]
  ;(ev.target as HTMLInputElement).value = ''
  if (!f || !aid.value) return
  upBusy.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    upRes.value = await guardedFetch(`/api/rack-audits/${aid.value}/upload`, { method: 'POST', body: fd })
    if (upRes.value!.ok) showToast(`已收 ${upRes.value!.saved.length} 櫃${upRes.value!.skipped.length ? `，略過 ${upRes.value!.skipped.length} 櫃` : ''}`, 'success')
    else showToast(`檔案有 ${upRes.value!.errors.length} 個錯，整份沒有存`, 'error')
    await loadAudit()
    await loadList()
  } catch (e: any) { showToast(`上傳失敗：${errMsg(e)}`, 'error') } finally { upBusy.value = false }
}

// 差異清單
const diffKind = ref('')
const diffStatus = ref('待處理')
const diffRows = computed(() => diffList.value.filter(d => (!diffKind.value || d.label === diffKind.value) && (!diffStatus.value || d.status === diffStatus.value))
  .map(d => ({ ...d, change_text: d.during_change.join('；'), aging: d.aging_days ?? -1 })))
const D = useSort(diffRows, 'id')
function showDiffs(kind: string, status: string) { diffKind.value = kind; diffStatus.value = status; tab.value = 'diffs' }
async function doAct(d: Diff) {
  const what = d.action === 'mark_removed' ? `把「${d.name}」整箱標記已不在櫃上` : `把「${d.name}」整箱位置更正到 ${d.found}（進 CIA 待更正、可以在機櫃正面復原）`
  if (!window.confirm(`${what}？`)) return
  try {
    const r = await guardedFetch<{ done: string }>(`/api/rack-audits/${aid.value}/diffs/act`, { method: 'POST', body: { diff_key: d.diff_key, action: d.action } })
    showToast(r.done, 'success')
    await loadAudit()
  } catch (e: any) { showToast(`處理失敗：${errMsg(e)}`, 'error') }
}
async function setStatus(d: Diff, status: string) {
  let reason: string | null = null
  if (status === '不處理') { reason = window.prompt('不處理的原因（必填）', ''); if (!reason) return }
  if (status === '已處理') { reason = window.prompt('怎麼處理的（可空）', '') ; if (reason === null) return }
  try {
    await guardedFetch(`/api/rack-audits/${aid.value}/diffs/status`, { method: 'POST', body: { diff_key: d.diff_key, status, reason } })
    await loadAudit()
  } catch (e: any) { showToast(`更新失敗：${errMsg(e)}`, 'error') }
}

// 網頁填寫（同一張表，預設在，只點例外）
const fillRack = ref('')
const sheet = ref<Sheet | null>(null)
const fillBy = ref('')
const fillOn = ref('')
const fillBusy = ref(false)
async function loadSheet() {
  sheet.value = null
  if (!aid.value || !fillRack.value) return
  const [room, rack] = fillRack.value.split('|')
  try {
    sheet.value = await apiFetch<Sheet>(`/api/rack-audits/${aid.value}/rack`, { query: { room, rack } })
    fillBy.value = sheet.value.counted_by || fillBy.value
    fillOn.value = sheet.value.counted_on || new Date().toISOString().slice(0, 10)
    while (sheet.value.extras.length < 5) sheet.value.extras.push({ model: '', serial: '', u: '', note: '' })
  } catch (e: any) { showToast(`這櫃載入失敗：${errMsg(e)}`, 'error') }
}
watch([fillRack, aid], loadSheet)
function fillOpen(r: RackRow) { fillRack.value = `${r.room}|${r.rack}`; tab.value = 'fill' }
const fillRows = computed(() => sheet.value?.rows ?? [])
const F = useSort(fillRows, 'no')
async function saveSheet() {
  const s = sheet.value
  if (!s) return
  if (!fillBy.value.trim()) { showToast('要填盤點人', 'warn'); return }
  fillBusy.value = true
  try {
    const rows = Object.fromEntries(s.rows.map(r => [r.key, { result: r.result, correct_u: r.correct_u, correct_model: r.correct_model, correct_serial: r.correct_serial, note: r.note }]))
    const r = await guardedFetch<{ version: number; replaced: boolean; warnings: string[] }>(`/api/rack-audits/${aid.value}/rack`, { method: 'PUT',
      body: { room: s.room, rack: s.rack, rows, extras: s.extras, counted_by: fillBy.value, counted_on: fillOn.value } })
    showToast(`${s.rack} 已存（第 ${r.version} 版${r.replaced ? '，取代前一版' : ''}）${r.warnings.length ? '；' + r.warnings.join('；') : ''}`, 'success')
    await loadAudit()
    await loadSheet()
  } catch (e: any) { showToast(`存檔失敗：${errMsg(e)}`, 'error') } finally { fillBusy.value = false }
}
async function closeAudit() {
  if (!window.confirm('結案？結案後不能再交結果（差異仍可處理）。')) return
  try { await guardedFetch(`/api/rack-audits/${aid.value}/close`, { method: 'POST' }); await loadAudit(); await loadList() } catch (e: any) { showToast(`結案失敗：${errMsg(e)}`, 'error') }
}
const snRows = computed(() => [...(sum.value?.serial_missing ?? []), ...(sum.value?.serial_found ?? [])].map((x, i) => ({ ...x, no: i + 1 })))
const SN = useSort(snRows, 'no')
const showSn = ref(false)

onMounted(async () => {
  await Promise.all([loadList(), loadRooms()])
  if (route.query.start) openStart(String(route.query.room || ''), String(route.query.rack || ''))
})
</script>

<template>
  <div class="ra">
    <V2Banner to="/v2/inventory/rack" label="7.1 機櫃盤點" />
    <div class="head">
      <h2>機櫃盤點</h2>
      <button v-if="canEdit" class="btn" @click="openStart()">開始盤點</button>
      <InfoNote>流程：開始盤點（把今天的帳凍結成基準）→ 下載盤點檔（一櫃一張表，結果預設「在」）→ 機房裡離線只改例外列 → 回座位上傳，或在「網頁填寫」直接點例外 → 差異清單逐筆處理。結果一律跟基準比，不跟今天的資料比；同一櫃再交＝取代，舊的留歷史。紙本用機櫃正面的「列印盤點表」，列是同一份。</InfoNote>
    </div>

    <div v-if="startOpen" class="card start">
      <b>開始盤點</b>
      <label>機房 <select v-model="startRoom" @change="startPick = {}"><option v-for="r in rooms" :key="r.room" :value="r.room">{{ r.room }}（{{ r.racks.length }} 櫃）</option></select></label>
      <div class="rackpick">
        <label v-for="k in startRacks" :key="k" class="rp" :class="{ on: startPick[k] }"><input v-model="startPick[k]" type="checkbox">{{ k }}</label>
      </div>
      <span class="muted">{{ pickedRacks.length ? `選了 ${pickedRacks.length} 櫃` : '沒勾＝整個機房' }}</span>
      <button class="btn" :disabled="startBusy || !startRoom" @click="startAudit">{{ startBusy ? '凍結中…' : '開始' }}</button>
      <button class="chip" @click="startOpen = false">取消</button>
    </div>

    <p v-if="listErr" class="err">{{ listErr }}</p>
    <div v-if="!aid" class="card">
      <b>盤點紀錄</b> <span class="muted">{{ audits.length }} 次</span>
      <table class="tbl">
        <thead><tr>
          <SortTh k="no" :active="L.sortKey.value" :dir="L.sortDir.value" @sort="L.toggle">項次</SortTh>
          <SortTh k="title" :active="L.sortKey.value" :dir="L.sortDir.value" @sort="L.toggle">盤點</SortTh>
          <SortTh k="baseline_at" :active="L.sortKey.value" :dir="L.sortDir.value" @sort="L.toggle">基準日</SortTh>
          <SortTh k="prog" :active="L.sortKey.value" :dir="L.sortDir.value" @sort="L.toggle">進度</SortTh>
          <SortTh k="book_count" :active="L.sortKey.value" :dir="L.sortDir.value" @sort="L.toggle">帳上台數</SortTh>
          <SortTh k="diff_open" :active="L.sortKey.value" :dir="L.sortDir.value" @sort="L.toggle">差異未處理</SortTh>
          <SortTh k="status" :active="L.sortKey.value" :dir="L.sortDir.value" @sort="L.toggle">狀態</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="a in L.sorted.value" :key="a.id" class="click" @click="openAudit(a.id)">
            <td class="mono">A{{ a.no }}</td><td><span class="lnk">#{{ a.id }} {{ a.title }}</span></td><td class="mono">{{ a.baseline_at.slice(0, 16) }}</td>
            <td class="mono">{{ a.rack_done }}／{{ a.rack_total }} 櫃</td><td class="mono">{{ a.book_count }}</td>
            <td class="mono">{{ a.diff_open }}／{{ a.diff_count }}</td><td>{{ a.status === 'open' ? '進行中' : '已結案' }}</td>
          </tr>
          <tr v-if="!audits.length"><td colspan="7" class="muted">還沒有盤點</td></tr>
        </tbody>
      </table>
    </div>

    <template v-if="aid">
      <div class="crumb"><button class="linkbtn" @click="openAudit(null)">← 盤點紀錄</button>
        <b v-if="det">#{{ det.id }} {{ det.title }}</b><span v-if="det" class="muted">基準 {{ det.baseline_at.slice(0, 16) }}・{{ open ? '進行中' : '已結案' }}</span>
        <a v-if="det" class="btn" :href="`${apiBase}/api/rack-audits/${aid}/xlsx`">下載盤點檔</a>
        <label v-if="det && open && det.can_edit" class="btn up">{{ upBusy ? '上傳中…' : '上傳盤點檔' }}<input type="file" accept=".xlsx" hidden :disabled="upBusy" @change="upload"></label>
        <a v-if="det" class="chip" :href="`${apiBase}/api/rack-audits/${aid}/report.xlsx`">匯出盤點報告</a>
        <button v-if="det && open && det.can_edit" class="chip" @click="closeAudit">結案</button>
      </div>
      <p v-if="detErr" class="err">{{ detErr }}</p>
      <div v-if="upRes" class="card" :class="upRes.ok ? 'okbox' : 'badbox'">
        <b>{{ upRes.ok ? `已收 ${upRes.saved.length} 櫃` : `檔案有 ${upRes.errors.length} 個錯，整份沒有存` }}</b>
        <button class="linkbtn" @click="upRes = null">關閉</button>
        <div v-for="(e, i) in upRes.errors" :key="'e' + i">E{{ i + 1 }}　{{ e }}</div>
        <div v-for="(s, i) in upRes.saved" :key="'s' + i">S{{ i + 1 }}　{{ s.rack }}：第 {{ s.version }} 版{{ s.replaced ? '（取代前一版）' : '' }}，{{ s.counted }} 台、沒登記 {{ s.extras }}</div>
        <div v-for="(s, i) in upRes.skipped" :key="'k' + i" class="muted">K{{ i + 1 }}　{{ s.rack }}：{{ s.why }}</div>
        <div v-for="(w, i) in upRes.warnings" :key="'w' + i" class="muted">W{{ i + 1 }}　{{ w }}</div>
      </div>

      <div class="atabs"><button v-for="t in TABS" :key="t.k" class="atab" :class="{ on: tab === t.k }" type="button" @click="tab = t.k">{{ t.label }}</button></div>

      <!-- 總覽（主管） -->
      <div v-if="tab === 'boss' && sum" class="kpis">
        <button class="kpi" @click="rackFilter = 'all'; tab = 'racks'"><span>進度</span><b>{{ sum.rack_done }}／{{ sum.rack_total }}</b><i>櫃</i></button>
        <button class="kpi" @click="rackFilter = 'done'; tab = 'racks'"><span>已盤／帳上</span><b>{{ sum.counted }}／{{ sum.book_count }}</b><i>台</i></button>
        <button class="kpi" :class="{ warn: sum.diff_open }" @click="showDiffs('', '待處理')"><span>差異未處理</span><b>{{ sum.diff_open }}</b><i>／{{ sum.diff_total }}</i></button>
        <button class="kpi" :class="{ warn: sum.diff_overdue }" @click="showDiffs('櫃上有但沒登記', '待處理')"><span>補登逾期</span><b>{{ sum.diff_overdue }}</b><i>超過 {{ det?.extra_days }} 天</i></button>
        <button class="kpi" @click="showSn = !showSn"><span>序號待補</span><b>{{ sum.serial_missing.length }}</b><i>現場抄到 {{ sum.serial_found.length }}</i></button>
      </div>
      <div v-if="tab === 'boss' && sum" class="card">
        <span class="muted">盤點人：{{ sum.people.join('、') || '—' }}</span>
        <table class="tbl narrow">
          <thead><tr><th>種類</th><th>總數</th><th>待處理</th><th>已處理</th><th>不處理</th></tr></thead>
          <tbody>
            <tr v-for="(v, k) in sum.by_kind" :key="k">
              <td>{{ k }}</td>
              <td><button class="linkbtn" @click="showDiffs(String(k), '')">{{ v.total }}</button></td>
              <td v-for="s in ['待處理', '已處理', '不處理']" :key="s"><button class="linkbtn" @click="showDiffs(String(k), s)">{{ v[s] }}</button></td>
            </tr>
            <tr v-if="!Object.keys(sum.by_kind).length"><td colspan="5" class="muted">沒有差異</td></tr>
          </tbody>
        </table>
        <InfoNote>序號待補＝基準裡沒有原廠序號的箱子；現場抄到的序號會進 CIA 待更正（序號補登）。</InfoNote>
        <table v-if="showSn" class="tbl">
          <thead><tr>
            <SortTh k="no" :active="SN.sortKey.value" :dir="SN.sortDir.value" @sort="SN.toggle">項次</SortTh>
            <SortTh k="rack" :active="SN.sortKey.value" :dir="SN.sortDir.value" @sort="SN.toggle">機櫃</SortTh>
            <SortTh k="name" :active="SN.sortKey.value" :dir="SN.sortDir.value" @sort="SN.toggle">設備</SortTh>
            <SortTh k="model" :active="SN.sortKey.value" :dir="SN.sortDir.value" @sort="SN.toggle">機型</SortTh>
            <SortTh k="u" :active="SN.sortKey.value" :dir="SN.sortDir.value" @sort="SN.toggle">U</SortTh>
            <SortTh k="serial_found" :active="SN.sortKey.value" :dir="SN.sortDir.value" @sort="SN.toggle">現場抄到</SortTh>
          </tr></thead>
          <tbody><tr v-for="x in SN.sorted.value" :key="x.key"><td class="mono">N{{ x.no }}</td><td>{{ x.room }} {{ x.rack }}</td>
            <td><NuxtLink class="lnk" :to="`/assets/${encodeURIComponent(x.asset_serial)}`">{{ x.name }}</NuxtLink></td><td>{{ upText(x.model) }}</td><td class="mono">{{ x.u }}</td><td class="mono">{{ x.serial_found || '—' }}</td></tr></tbody>
        </table>
      </div>

      <!-- 各櫃進度 -->
      <div v-if="tab === 'racks' && det" class="card">
        <div class="bar"><button v-for="f in (['all', 'done', 'todo'] as const)" :key="f" class="chip" :class="{ on: rackFilter === f }" @click="rackFilter = f">{{ { all: '全部', done: '已盤', todo: '未盤' }[f] }}</button></div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">項次</SortTh>
            <SortTh k="rack" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">機櫃</SortTh>
            <SortTh k="book_count" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">帳上</SortTh>
            <SortTh k="done_text" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">狀態</SortTh>
            <SortTh k="ok" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">在</SortTh>
            <SortTh k="gone" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">不在</SortTh>
            <SortTh k="bad" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">不符</SortTh>
            <SortTh k="extra_count" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">沒登記</SortTh>
            <SortTh k="diff_open" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">差異未處理</SortTh>
            <SortTh k="counted_by" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">盤點人</SortTh>
            <SortTh k="counted_on" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">日期</SortTh>
            <SortTh k="version" :active="R.sortKey.value" :dir="R.sortDir.value" @sort="R.toggle">版</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in R.sorted.value" :key="r.room + r.rack">
              <td class="mono">R{{ r.no }}</td>
              <td><button class="linkbtn" @click="fillOpen(r)">{{ r.room }} {{ r.rack }}</button></td>
              <td class="mono"><button class="linkbtn" @click="fillOpen(r)">{{ r.book_count }}</button></td>
              <td>{{ r.done_text }}<span v-if="r.source" class="muted">（{{ r.source === 'xlsx' ? '上傳' : '網頁' }}）</span></td>
              <td class="mono">{{ r.done ? r.ok : '—' }}</td>
              <td class="mono"><button v-if="r.gone" class="linkbtn" @click="showDiffs('不在', '')">{{ r.gone }}</button><span v-else>{{ r.done ? 0 : '—' }}</span></td>
              <td class="mono">{{ r.done ? r.bad : '—' }}</td>
              <td class="mono"><button v-if="r.extra_count" class="linkbtn" @click="showDiffs('櫃上有但沒登記', '')">{{ r.extra_count }}</button><span v-else>{{ r.done ? 0 : '—' }}</span></td>
              <td class="mono"><button v-if="r.diff_open" class="linkbtn" @click="showDiffs('', '待處理')">{{ r.diff_open }}</button><span v-else>0</span></td>
              <td>{{ r.counted_by || '—' }}</td><td class="mono">{{ r.counted_on || '—' }}</td><td class="mono">{{ r.version || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 差異清單 -->
      <div v-if="tab === 'diffs'" class="card">
        <div class="bar">
          <select v-model="diffKind"><option value="">全部種類</option><option v-for="k in Object.keys(sum?.by_kind ?? {})" :key="k" :value="k">{{ k }}</option></select>
          <select v-model="diffStatus"><option value="">全部狀態</option><option v-for="s in ['待處理', '已處理', '不處理']" :key="s" :value="s">{{ s }}</option></select>
          <span class="muted">{{ diffRows.length }} 筆</span>
          <InfoNote>不在 → 確認是否已下架，一鍵「標記已不在櫃上」（整箱）；U 位不符 → 一鍵位置更正（整箱，進 CIA 待更正，可在機櫃正面復原）；型號／序號不符與序號補登、櫃上有但沒登記 → 自動列進 CIA 待更正（xlsx 多了「正確型號」「正確序號」兩欄），補登期限 {{ det?.extra_days }} 天。基準之後系統裡被搬過或改過狀態的，標「盤點期間異動」。</InfoNote>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="id" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">項次</SortTh>
            <SortTh k="rack" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">機櫃</SortTh>
            <SortTh k="label" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">種類</SortTh>
            <SortTh k="name" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">設備</SortTh>
            <SortTh k="book" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">帳上</SortTh>
            <SortTh k="found" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">現場</SortTh>
            <SortTh k="next_step" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">下一步</SortTh>
            <SortTh k="status" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">狀態</SortTh>
            <SortTh k="aging" :active="D.sortKey.value" :dir="D.sortDir.value" @sort="D.toggle">天數</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="d in D.sorted.value" :key="d.diff_key">
              <td class="mono">D{{ d.id }}</td><td>{{ d.room }} {{ d.rack }}<span v-if="d.no" class="muted">・第 {{ d.no }} 項</span></td>
              <td>{{ d.label }}<span v-if="d.during_change.length" class="flag" :title="d.change_text">盤點期間異動</span></td>
              <td><NuxtLink v-if="d.asset_serial" class="lnk" :to="`/assets/${encodeURIComponent(d.asset_serial)}`">{{ d.name }}</NuxtLink><span v-else>{{ d.name }}</span>
                <div v-if="d.note" class="muted small">{{ d.note }}</div></td>
              <td class="mono small">{{ d.book }}</td><td class="mono small">{{ d.found }}</td>
              <td class="small">{{ d.next_step }}
                <template v-if="det?.can_edit && d.status === '待處理'">
                  <button v-if="d.action" class="chip on" @click="doAct(d)">{{ d.action === 'mark_removed' ? '標記已不在櫃上' : '位置更正' }}</button>
                </template>
              </td>
              <td>{{ d.status }}<div v-if="d.status_by" class="muted small">{{ d.status_by }} {{ (d.status_at || '').slice(0, 16) }}{{ d.reason ? '：' + d.reason : '' }}</div>
                <div v-if="det?.can_edit" class="small">
                  <button v-for="s in ['待處理', '已處理', '不處理'].filter(x => x !== d.status)" :key="s" class="linkbtn" @click="setStatus(d, s)">{{ s }}</button>
                </div></td>
              <td class="mono" :class="{ bad: d.overdue }">{{ d.aging_days ?? '' }}<span v-if="d.due" class="muted small">（期限 {{ d.due }}）</span></td>
            </tr>
            <tr v-if="!diffRows.length"><td colspan="9" class="muted">沒有符合的差異</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 網頁填寫 -->
      <div v-if="tab === 'fill' && det" class="card">
        <div class="bar">
          <select v-model="fillRack"><option value="">選一櫃</option><option v-for="r in det.racks" :key="r.room + r.rack" :value="`${r.room}|${r.rack}`">{{ r.room }} {{ r.rack }}（{{ r.book_count }} 台{{ r.done ? '・已盤' : '' }}）</option></select>
          <template v-if="sheet">
            <label>盤點人 <input v-model="fillBy" class="in"></label>
            <label>日期 <input v-model="fillOn" type="date" class="in"></label>
            <button v-if="det.can_edit && open" class="btn" :disabled="fillBusy" @click="saveSheet">{{ fillBusy ? '存檔中…' : sheet.done ? '重交這櫃' : '存這櫃' }}</button>
            <span class="muted">預設全部「在」，只點例外</span>
          </template>
        </div>
        <table v-if="sheet" class="tbl">
          <thead><tr>
            <SortTh k="no" :active="F.sortKey.value" :dir="F.sortDir.value" @sort="F.toggle">項次</SortTh>
            <SortTh k="u" :active="F.sortKey.value" :dir="F.sortDir.value" @sort="F.toggle">U</SortTh>
            <SortTh k="model" :active="F.sortKey.value" :dir="F.sortDir.value" @sort="F.toggle">機型</SortTh>
            <SortTh k="asset_serial" :active="F.sortKey.value" :dir="F.sortDir.value" @sort="F.toggle">資產編號</SortTh>
            <SortTh k="hw_serial" :active="F.sortKey.value" :dir="F.sortDir.value" @sort="F.toggle">原廠序號</SortTh>
            <SortTh k="name_text" :active="F.sortKey.value" :dir="F.sortDir.value" @sort="F.toggle">主機名</SortTh>
            <SortTh k="result" :active="F.sortKey.value" :dir="F.sortDir.value" @sort="F.toggle">結果</SortTh>
            <th>正確 U</th><th>正確型號</th><th>正確序號</th><th>備註</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in F.sorted.value" :key="r.key" :class="{ ex: r.result !== '在' }">
              <td class="mono">{{ r.no }}</td><td class="mono">{{ r.u || '位置不明' }}</td><td>{{ upText(r.model) }}</td><td class="mono small">{{ r.asset_serial }}</td>
              <td class="mono small">{{ upText(r.hw_serial) || '—' }}</td><td class="small">{{ r.name_text }}</td>
              <td><span class="seg"><button v-for="v in ['在', '不在', '不符']" :key="v" :class="{ on: r.result === v }" @click="r.result = v">{{ v }}</button></span></td>
              <td><input v-model="r.correct_u" class="in s" :disabled="r.result !== '不符'"></td>
              <td><input v-model="r.correct_model" class="in" :disabled="r.result !== '不符'"></td>
              <td><input v-model="r.correct_serial" class="in" :disabled="r.result !== '不符'"></td>
              <td><input v-model="r.note" class="in"></td>
            </tr>
            <tr><td colspan="11" class="sub"><b>櫃上有但沒登記</b></td></tr>
            <tr v-for="(e, i) in sheet.extras" :key="'x' + i">
              <td class="mono">新增{{ i + 1 }}</td><td><input v-model="e.u" class="in s" placeholder="U"></td><td><input v-model="e.model" class="in" placeholder="機型"></td><td />
              <td><input v-model="e.serial" class="in" placeholder="序號"></td><td colspan="5" /><td><input v-model="e.note" class="in"></td>
            </tr>
          </tbody>
        </table>
        <div v-if="sheet?.history.length" class="muted small">歷史：
          <span v-for="h in sheet.history" :key="h.id">第 {{ h.version }} 版 {{ h.source === 'xlsx' ? '上傳' : '網頁' }} {{ h.counted_by }} {{ h.submitted_at.slice(0, 16) }}{{ h.superseded ? '（已取代）' : '（現行）' }}；</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ra { font-family: 'Microsoft JhengHei', sans-serif; color: var(--ink); }
.head, .crumb, .bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.head h2 { margin: 0; font-size: 20px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; margin-bottom: 14px; box-shadow: var(--shadow); }
.start { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.rackpick { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; }
.rp { font-size: 12px; padding: 2px 8px; border: 1px solid var(--border-strong); border-radius: 12px; cursor: pointer; display: flex; gap: 4px; align-items: center; }
.rp.on { border-color: var(--brand); background: var(--mint); }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: #fff; cursor: pointer; text-decoration: none; }
.btn:hover { background: var(--brand-hover); }
.btn:disabled { opacity: .6; cursor: default; }
.btn.up { cursor: pointer; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 14px; }
.kpi { text-align: left; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; cursor: pointer; font-family: inherit; display: flex; flex-direction: column; gap: 2px; }
.kpi:hover { border-color: var(--brand); }
.kpi span { font-size: 12px; color: var(--ink-aux); }
.kpi b { font-family: var(--disp); font-size: 24px; color: var(--ink); }
.kpi i { font-style: normal; font-size: 11px; color: var(--muted); }
.kpi.warn b { color: var(--warn-text); }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; margin-top: 8px; }
.tbl.narrow { width: auto; min-width: 420px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 6px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; }
.tbl tr.click { cursor: pointer; }
.tbl tr.click:hover td, .tbl tr.ex td { background: var(--mint); }
.tbl td.sub { background: var(--sub); }
.mono { font-family: var(--disp); }
.small { font-size: 11px; }
.muted { color: var(--muted); font-size: 12px; }
.err, .bad { color: var(--bad); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.linkbtn { background: none; border: 0; padding: 0 6px 0 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; }
.linkbtn:hover { text-decoration: underline; }
.okbox { border-color: var(--good); background: var(--good-soft); font-size: 12.5px; }
.badbox { border-color: var(--bad); background: var(--bad-soft); font-size: 12.5px; }
.flag { display: inline-block; margin-left: 4px; font-size: 10.5px; padding: 0 6px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); }
.in { font-family: inherit; font-size: 12px; padding: 2px 6px; width: 120px; border: 1px solid var(--border-strong); border-radius: 4px; }
.in.s { width: 60px; }
.in:disabled { background: var(--sub); }
.seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: 6px; overflow: hidden; }
.seg button { font-family: inherit; font-size: 12px; padding: 2px 8px; border: 0; background: var(--card); color: var(--ink-aux); cursor: pointer; }
.seg button.on { background: var(--brand); color: #fff; }
select { font-family: inherit; font-size: 12.5px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
</style>
