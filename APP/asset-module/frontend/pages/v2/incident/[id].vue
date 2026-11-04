<script setup lang="ts">
import { makeLatest } from '~/utils/latestOnly'
import { hostStatus } from '~/utils/incidentHost'
// 4.1 單一事件（2.x 藍圖；不在選單）：步驟頁籤「範圍界定｜影響評估｜連線測試（2.006.7 找碴 C27：與主機履歷的「即時檢測」是不同功能，改名分開）｜通報聯絡｜檢核清單｜回報結案」。
// 全部沿用 incident.py（範圍展開、連線測試、檢核清單、回報文字、時間軸、結案）；通報聯絡的名單與通知信草稿
// 由 v2_incident.py 從同一份 event_view 產生，系統不寄信（使用者自行複製寄出）。結案後唯讀。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const eid = computed(() => String(route.params.id))
const view = ref<any>(null)
const err = ref('')
const viewLatest = makeLatest()
async function load() { const seq = viewLatest.begin(); try { const v = await apiFetch<any>(`/api/incidents/${eid.value}`); if (!viewLatest.isCurrent(seq)) return; view.value = v; err.value = '' } catch (e: any) { if (!viewLatest.isCurrent(seq)) return; err.value = `事件載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } }
await load()
const ev = computed(() => view.value?.event)
const exp = computed(() => view.value?.expand)
const open = computed(() => ev.value?.status === 'open')
const CAT: Record<string, string> = { planned: '計畫停機', incident: '異常事件' }
const TABS = computed(() => [
  { k: 'scope', label: '範圍界定' }, { k: 'impact', label: `影響評估 ${exp.value?.counts?.machines ?? ''}` }, { k: 'check', label: '連線測試' },
  { k: 'notify', label: '通報聯絡' }, { k: 'tasks', label: `檢核清單 ${view.value?.tasks?.length ?? ''}` }, { k: 'close', label: '回報結案' }])
const tab = computed({ get: () => (['scope', 'impact', 'check', 'notify', 'tasks', 'close'].includes(String(route.query.tab)) ? String(route.query.tab) : 'scope'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })
const errMsg = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'

// ===== 範圍界定 =====
const alertText = ref('')
const alertRes = ref<any>(null)
async function analyze() {
  if (!alertText.value.trim()) return
  try { alertRes.value = await apiFetch<any>(`/api/incidents/${eid.value}/alerts`, { method: 'POST', body: { text: alertText.value } }) } catch (e: any) { showToast(`分析失敗：${errMsg(e)}`, 'error') }
}
async function addScope(scope: Record<string, any>, replace = false) {
  try {
    await apiFetch(`/api/incidents/${eid.value}/scope`, { method: 'PUT', body: { scope, replace } })
    showToast(replace ? '已清除範圍' : '已加入範圍', 'success')
    await load()
  } catch (e: any) { showToast(`更新範圍失敗：${errMsg(e)}`, 'error') }
}
const rooms = ref<{ room: string; racks: string[] }[]>([])
onMounted(async () => {
  try { const dc = await apiFetch<any>('/api/racks/datacenter'); rooms.value = dc.rooms.filter((r: any) => r.racks.length).map((r: any) => ({ room: r.room, racks: r.racks.map((t: any) => t.rack) })) } catch { rooms.value = [] /* 機房清單讀不到：仍可貼告警或指定主機 */ }
})
const pickRoom = ref('')
const pickRacks = ref<string[]>([])
const roomRacks = computed(() => rooms.value.find(r => r.room === pickRoom.value)?.racks ?? [])
function addRoomRacks() {
  if (!pickRoom.value) return
  addScope(pickRacks.value.length ? { racks: pickRacks.value.map(rack => ({ room: pickRoom.value, rack })) } : { rooms: [pickRoom.value] })
  pickRacks.value = []
}
const esxiName = ref('')
const segment = ref('')
const hostQ = ref('')
async function addHost() {
  const q = hostQ.value.trim()
  if (!q) return
  try {
    const r = await apiFetch<any>('/api/v2/search', { query: { q } })
    const key = r.top?.kind === 'machine' ? r.top.key : (r.machines?.length === 1 ? r.machines[0].key : null)
    if (!key) { showToast(r.machines?.length ? `「${q}」符合 ${r.total} 台，請輸入更完整的主機名稱或 IP` : `查無「${q}」`, 'warn'); return }
    await addScope({ machines: [key.replace(/^ret:/, '')] })
    hostQ.value = ''
  } catch (e: any) { showToast(`查詢失敗：${errMsg(e)}`, 'error') }
}

// ===== 影響評估 =====
const machineRows = computed(() => (exp.value?.machines ?? []).map((m: any) => ({ ...m, where: m.is_vm ? `ESXi ${m.esxi || '—'}` : [m.room, m.rack, m.u].filter(Boolean).join(' '),
  kind: m.is_vm ? '虛擬機' : '實體機', systems: (m.api_ids || []).join('、'), people: [...(m.ap || []), ...(m.sp || [])].join('、') })))
const sysRows = computed(() => (exp.value?.systems ?? []).map((s: any) => ({ ...s, n: (s.machines || []).length, phone: s.oncall_phone || '' })))
// ===== V67（2.043.10）：舊版 /incident/[id] 剩下的功能 =====
// 影響評估的檢視（?iv=）：依主機｜依業務系統｜現場找機器（可列印）｜機櫃圖；主機篩選（?mf=）；缺值班電話（?np=1）
const iv = computed(() => (['host', 'sys', 'find', 'racks'].includes(String(route.query.iv)) ? String(route.query.iv) : 'host'))
const qTo = (patch: Record<string, string | undefined>) => ({ query: { ...route.query, ...patch } })
const MS = computed(() => (exp.value?.machines ?? []) as any[])
const M_FILTERS: { k: string; label: string; pred: (m: any) => boolean }[] = [
  { k: 'core', label: '核心系統的機器', pred: m => m.sys_level === 'core' },
  { k: 'single', label: '單電源', pred: m => m.psu === 'single' },
  { k: 'psu_unknown', label: '電源不明', pred: m => m.psu === 'unknown' },
  { k: 'vm_unplaced', label: '位置不明的 VM', pred: m => m.is_vm && !m.host_key },
  { k: 'down', label: '不通（最近一輪）', pred: m => m.check === 'no_response' },
  { k: 'untested', label: '未測', pred: m => !m.check },
]
const mf = computed(() => M_FILTERS.find(f => f.k === route.query.mf) ?? null)
const mfCount = (k: string) => MS.value.filter(M_FILTERS.find(f => f.k === k)!.pred).length
const machineShown = computed(() => (mf.value ? machineRows.value.filter((m: any) => mf.value!.pred(m)) : machineRows.value))
const npOnly = computed(() => route.query.np === '1')
const sysShown = computed(() => (npOnly.value ? sysRows.value.filter((s: any) => !s.oncall_phone) : sysRows.value))
// 現場找機器清單：一台實體機一列（VM 併到它的 ESXi 那列），機房 → 櫃 → U 排好
const natCmp = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
const hostItems = computed(() => {
  const by = new Map<string, any>()
  for (const m of MS.value) {
    if (!m.host_key) continue
    const h = by.get(m.host_key) ?? { key: m.host_key, name: m.host_name || m.name, serial: m.host_serial, room: m.room, rack: m.rack, u: m.host_u, items: [] as any[] }
    h.items.push(m)
    by.set(m.host_key, h)
  }
  return [...by.values()]
})
const ST_LABEL: Record<string, string> = { down: '不通', up: '通', untested: '未測' }
// 一列（實體機／ESXi）的最近測試：它自己在範圍裡且測過就用自己的；否則用它上面那些 VM 的結果彙總（A4：事件只含 VM 時，
// ESXi 不在範圍裡，不能一律標「未測」）——任一台不通＝不通，否則有任一台通＝通，都沒測＝未測
const statusOf = (hostKey: string) => hostStatus(hostKey, MS.value)
const findRows = computed(() => hostItems.value.map(h => ({
  ...h, sys: [...new Set(h.items.flatMap((i: any) => i.api_ids || []))].join('、'),
  ap: [...new Set(h.items.flatMap((i: any) => i.ap || []))].join('、'), sp: [...new Set(h.items.flatMap((i: any) => i.sp || []))].join('、'),
  vms: h.items.filter((i: any) => i.is_vm).length, st: ST_LABEL[statusOf(h.key)],
})).sort((a, b) => natCmp(a.room || '~', b.room || '~') || natCmp(a.rack || '~', b.rack || '~') || (parseInt(b.u || '0') - parseInt(a.u || '0'))).map((r, i) => ({ ...r, __no: i + 1 })))
function printFind() {
  const esc = (x: any) => String(x ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]!))
  const rows = findRows.value.map(r => `<tr><td>F${r.__no}</td><td>${esc(r.room)}</td><td>${esc(r.rack)}</td><td>${esc(r.u)}</td><td>${esc(r.name)}</td><td>${esc(r.serial)}</td><td>${esc(r.sys)}</td><td>${esc(r.ap)}</td><td>${esc(r.sp)}</td><td>${r.vms || ''}</td><td>${esc(r.st)}</td></tr>`).join('')
  const w = window.open('', '_blank')
  if (!w) { showToast('瀏覽器擋了新視窗，請允許彈出視窗', 'warn'); return }
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>現場找機器清單</title><style>body{font-family:'Microsoft JhengHei',sans-serif;font-size:12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;padding:4px 6px;text-align:left}th{background:#eee}</style></head><body><h3>現場找機器清單：${esc(ev.value?.title)}（${new Date().toLocaleString()}）</h3><table><thead><tr><th>項次</th><th>機房</th><th>櫃</th><th>U</th><th>主機</th><th>序號</th><th>系統</th><th>AP</th><th>SP</th><th>VM 數</th><th>最近測試</th></tr></thead><tbody>${rows}</tbody></table></body></html>`)
  w.document.close()
  w.focus()
  w.print()
}
// 機櫃圖：範圍內設備所在的櫃，正面依最近一輪測試上色（VM 亮它 ESXi 那台）
const rackRooms = computed(() => [...new Set(hostItems.value.map(h => h.room).filter(Boolean))].sort(natCmp) as string[])
const rackRoom = computed(() => (rackRooms.value.includes(String(route.query.rr)) ? String(route.query.rr) : rackRooms.value[0] ?? ''))
const litRacks = computed(() => {
  const m = new Map<string, number>()
  for (const h of hostItems.value) if (h.room === rackRoom.value && h.rack) m.set(h.rack, (m.get(h.rack) ?? 0) + h.items.length)
  return [...m.entries()].sort((a, b) => natCmp(a[0], b[0]))
})
const shownRacks = computed(() => { const sel = String(route.query.rk || ''); return sel ? sel.split(',').filter(r => litRacks.value.some(([k]) => k === r)) : litRacks.value.slice(0, 6).map(([k]) => k) })
const togRack = (r: string) => { const s = new Set(shownRacks.value); s.has(r) ? s.delete(r) : s.add(r); return qTo({ rk: [...s].join(',') || undefined }) }
const hiKeysOf = (rack: string) => hostItems.value.filter(h => h.room === rackRoom.value && h.rack === rack).map(h => h.key)
const statusByKey = computed(() => Object.fromEntries(hostItems.value.map(h => [h.key, statusOf(h.key)])))
const unplacedVm = computed(() => MS.value.filter(m => m.is_vm && !m.host_key).length)

// ===== 即時檢測 =====
const checking = ref(false)
async function runCheck() {
  checking.value = true
  try { await apiFetch(`/api/incidents/${eid.value}/check`, { method: 'POST' }); showToast('已完成一輪連線測試', 'success'); await load(); loadRuns() } catch (e: any) { showToast(`測試失敗：${errMsg(e)}`, 'error') } finally { checking.value = false }
}
// V67：連線測試每一輪都保留，可以回頭看任一輪（?run=）
const runs = ref<any[]>([])
async function loadRuns() { try { runs.value = await apiFetch<any[]>(`/api/incidents/${eid.value}/runs`) } catch (e: any) { runs.value = []; showToast(`測試歷史載入失敗：${errMsg(e)}`, 'error') } }
watch(tab, (t) => { if (t === 'check') loadRuns() }, { immediate: true })
const pickedRun = ref<any>(null)
watch(() => route.query.run, async (r) => {
  pickedRun.value = null
  if (!r) return
  try { pickedRun.value = await apiFetch<any>(`/api/incidents/runs/${encodeURIComponent(String(r))}`) } catch (e: any) { showToast(`讀取那一輪失敗：${errMsg(e)}`, 'error') }
}, { immediate: true })
const shownRun = computed(() => pickedRun.value ?? view.value?.last_run ?? null)
const runRows = computed(() => (shownRun.value?.items ?? []).map((x: any) => ({ ...x })))
const runHist = computed(() => runs.value.map((r: any, i: number) => ({ ...r, __no: i + 1, at: (r.started_at || '').slice(0, 16), by: r.started_by || r.created_by || '—' })))

// ===== 通報聯絡 =====
const notify = ref<any>(null)
async function loadNotify() { try { notify.value = await apiFetch<any>(`/api/v2/incident/${eid.value}/notify`) } catch (e: any) { showToast(`聯絡名單載入失敗：${errMsg(e)}`, 'error') } }
watch(tab, (t) => { if (t === 'notify' && !notify.value) loadNotify() }, { immediate: true })
async function copy(text: string, what: string) { try { await navigator.clipboard.writeText(text); showToast(`已複製${what}`, 'success') } catch { showToast('瀏覽器不允許複製，請手動選取文字', 'warn') } }

// ===== 檢核清單 =====
const taskRows = computed(() => (view.value?.tasks ?? []).map((t: any, i: number) => ({ ...t, no: i + 1 })))
const { sortKey: tKey, sortDir: tDir, toggle: tToggle, sorted: tSorted } = useSort(taskRows, 'no')
// 可編輯的檢核清單不是共用 V2Table，匯出另做（目前排序後全部列，欄位同畫面）
const { exportTable: _xt, busy: tExporting } = useTableExport()
const _xname = useV2ExportName()
function exportTasks() {
  _xt(_xname(), ['項次', '項目', '組別', '狀態', '備註', '更新人', '更新時間'],
    tSorted.value.map((t: any) => [`K${t.no}`, t.name, t.team || '', t.status || '', t.note || '', t.updated_by || '', (t.updated_at || '').slice(0, 16)]))
}
async function saveTask(t: any, patch: Record<string, any>) {
  try { await apiFetch(`/api/incidents/tasks/${t.id}`, { method: 'PUT', body: patch }); await load() } catch (e: any) { showToast(`更新失敗：${errMsg(e)}`, 'error') }
}

// ===== 回報結案 =====
const report = ref('')
const timeline = ref<any[]>([])
const tlState = ref<'loading' | 'ok' | 'error'>('loading')     // 載入中／成功（可能是空）／失敗——失敗不可冒充「無紀錄」
const tlError = ref('')
const tlLatest = makeLatest()
async function loadTimeline() {
  const seq = tlLatest.begin()
  const id = eid.value
  tlState.value = 'loading'
  tlError.value = ''
  try {
    const r = await apiFetch<any[]>(`/api/incidents/${id}/timeline`)
    if (!tlLatest.isCurrent(seq)) return                           // 舊請求（換事件／重試）晚回來：丟掉
    timeline.value = r
    tlState.value = 'ok'
  } catch (e: any) {
    if (!tlLatest.isCurrent(seq)) return
    timeline.value = []
    tlError.value = errMsg(e)
    tlState.value = 'error'
  }
}
watch(tab, (t) => { if (t === 'close') loadTimeline() }, { immediate: true })
async function makeReport() {
  try { report.value = (await apiFetch<{ text: string }>(`/api/incidents/${eid.value}/report`, { method: 'POST', body: { every_min: 30 } })).text; loadTimeline() } catch (e: any) { showToast(`產生回報失敗：${errMsg(e)}`, 'error') }
}
const noteText = ref('')
async function addNote() {
  if (!noteText.value.trim()) return
  try { await apiFetch(`/api/incidents/${eid.value}/note`, { method: 'POST', body: { text: noteText.value } }); noteText.value = ''; loadTimeline() } catch (e: any) { showToast(`新增紀錄失敗：${errMsg(e)}`, 'error') }
}
const summary = ref('')
async function closeEvent() {
  if (!summary.value.trim()) { showToast('結案需填寫摘要', 'warn'); return }
  if (!window.confirm('確定結案？結案後事件內容只能檢視、不能修改。')) return
  try { await apiFetch(`/api/incidents/${eid.value}/close`, { method: 'POST', body: { summary: summary.value } }); showToast('已結案', 'success'); await load(); loadTimeline() } catch (e: any) { showToast(`結案失敗：${errMsg(e)}`, 'error') }
}
const tlRows = computed(() => timeline.value.map(t => ({ ...t, at16: (t.at || '').slice(0, 16) })))
</script>

<template>
  <V2Page v-model="tab" :tabs="ev ? TABS : undefined" :back="{ to: '/v2/incident', label: '4.1 事件應變' }">
    <template #title>
      <h1 class="h1">{{ ev ? `#${ev.id} ${ev.title}` : '事件' }}</h1>
      <span v-if="ev" class="cat">{{ CAT[ev.category || 'incident'] }}</span>
      <span v-if="ev" class="cat" data-testid="ev-mode">{{ ev.mode === 'formal' ? '正式' : ev.mode === 'drill' ? '演練' : '未分類（歷史）' }}</span>
      <span v-if="ev" class="st" :class="{ closed: !open }">{{ open ? '進行中' : '已結案' }}</span>
    </template>
    <template v-if="ev" #conclusion>{{ ev.type }}・開始 {{ ev.started_at.slice(0, 16) }}・範圍：{{ exp?.scope_text }}・範圍內主機 {{ exp?.counts.machines }} 台、所屬業務系統 {{ exp?.counts.systems }} 套</template>
    <p v-if="err" class="err">{{ err }}</p>

    <template v-if="ev" #tab>
      <!-- 1 範圍界定 -->
      <template v-if="tab === 'scope'">
        <dl class="kv"><dt>目前範圍</dt><dd>{{ exp.scope_text }}<button v-if="open && !isViewer && exp.counts.machines" type="button" class="lb" @click="addScope({}, true)">清除範圍</button></dd>
          <dt>範圍內主機</dt><dd><button type="button" class="lb nm" @click="tab = 'impact'">{{ exp.counts.machines }} 台</button>（實體 {{ exp.counts.physical }}、虛擬 {{ exp.counts.vms }}）</dd></dl>
        <template v-if="open && !isViewer">
          <h3>依機房／機櫃</h3>
          <div class="row"><select v-model="pickRoom" @change="pickRacks = []"><option value="">選擇機房</option><option v-for="r in rooms" :key="r.room" :value="r.room">{{ r.room }}</option></select>
            <select v-model="pickRacks" multiple size="4" :disabled="!pickRoom"><option v-for="k in roomRacks" :key="k" :value="k">{{ k }}</option></select>
            <button type="button" class="btn" :disabled="!pickRoom" @click="addRoomRacks">{{ pickRacks.length ? `加入 ${pickRacks.length} 櫃` : '加入整個機房' }}</button>
            <InfoNote>機櫃可複選（按住 Ctrl）。UPS／PDU 與機櫃的對應尚無資料，停電事件先以機櫃為範圍。</InfoNote></div>
          <h3>指定主機、ESXi 或網段</h3>
          <div class="row"><input v-model="hostQ" class="in" placeholder="主機名稱或 IP" @keydown.enter="addHost"><button type="button" class="btn" @click="addHost">加入主機</button>
            <input v-model="esxiName" class="in" placeholder="ESXi 名稱"><button type="button" class="btn" :disabled="!esxiName.trim()" @click="addScope({ esxi: [esxiName.trim()] }); esxiName = ''">加入 ESXi</button>
            <input v-model="segment" class="in" placeholder="網段，例 10.99.1.0/24"><button type="button" class="btn" :disabled="!segment.trim()" @click="addScope({ segments: [segment.trim()] }); segment = ''">加入網段</button></div>
          <h3>貼上告警找共同點</h3>
          <textarea v-model="alertText" class="ta" rows="4" placeholder="貼上告警文字、IP 或主機名稱（每行一筆或整段）" />
          <div class="row"><button type="button" class="btn" @click="analyze">分析</button></div>
          <template v-if="alertRes">
            <p class="muted">抓到 {{ alertRes.tokens.length }} 個 IP／主機名稱，對應 {{ alertRes.machines.length }} 台。</p>
            <V2Table :rows="alertRes.common.map((c: any) => ({ ...c, act: '' }))" prefix="A" empty="沒有共同點"
                     :cols="[{ k: 'dim_label', label: '共同點' }, { k: 'text', label: '說明' }, { k: 'count', label: '台數', mono: true }]" />
            <div class="row"><span class="muted">設為範圍：</span><button v-for="(c, i) in alertRes.common.slice(0, 6)" :key="i" type="button" class="chip" @click="addScope(c.scope)">{{ c.text }}</button></div>
          </template>
        </template>
      </template>

      <!-- 2 影響評估 -->
      <template v-else-if="tab === 'impact'">
        <dl class="kv"><dt>範圍內主機</dt><dd>{{ exp.counts.machines }} 台（實體 {{ exp.counts.physical }}、虛擬 {{ exp.counts.vms }}）</dd>
          <dt>範圍內主機所屬業務系統</dt><dd>{{ exp.counts.systems }} 套；未登錄值班電話 {{ exp.counts.no_phone }} 套</dd>
          <dt>單電源設備</dt><dd>{{ exp.counts.single_psu }} 台；電源數未知 {{ exp.counts.psu_unknown }} 台</dd></dl>
        <div class="row">
          <NuxtLink class="chip" :class="{ on: iv === 'host' }" :to="qTo({ iv: undefined })">依主機 {{ MS.length }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: iv === 'sys' }" :to="qTo({ iv: 'sys' })">依業務系統 {{ sysRows.length }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: iv === 'find' }" :to="qTo({ iv: 'find' })">現場找機器 {{ findRows.length }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: iv === 'racks' }" :to="qTo({ iv: 'racks' })">機櫃圖</NuxtLink>
        </div>
        <template v-if="iv === 'sys'">
          <div class="row"><NuxtLink class="chip" :class="{ on: !npOnly }" :to="qTo({ np: undefined })">全部 {{ sysRows.length }}</NuxtLink>
            <NuxtLink class="chip" :class="{ on: npOnly }" :to="qTo({ np: '1' })">缺值班電話 {{ exp.counts.no_phone }}</NuxtLink></div>
          <V2Table :rows="sysShown" prefix="S" empty="範圍內沒有對應業務系統的主機" :cols="[{ k: 'api_id', label: '代號', mono: true, link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'name', label: '業務系統' }, { k: 'level_label', label: '等級' }, { k: 'n', label: '主機數', mono: true }, { k: 'phone', label: '值班電話', mono: true, text: (r: any) => r.phone || '沒有值班電話' }]" />
        </template>
        <template v-else-if="iv === 'find'">
          <div class="row"><button type="button" class="btn" :disabled="!findRows.length" @click="printFind">列印現場找機器清單（{{ findRows.length }} 台）</button>
            <span v-if="unplacedVm" class="muted">位置不明的 VM {{ unplacedVm }} 台不在清單</span>
            <InfoNote>一台實體機一列，VM 併到它所在的 ESXi 那一列；依機房 → 機櫃 → U 由上到下排好，帶到機房照著找。「最近測試」是最近一輪連線測試的結果。</InfoNote></div>
          <V2Table :rows="findRows" prefix="F" row-key="key" :max="2000" empty="範圍內沒有已知位置的實體機" export-name="現場找機器清單"
                   :cols="[{ k: 'room', label: '機房' }, { k: 'rack', label: '櫃' }, { k: 'u', label: 'U', mono: true }, { k: 'name', label: '主機', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'serial', label: '序號', mono: true }, { k: 'sys', label: '系統' }, { k: 'ap', label: 'AP' }, { k: 'sp', label: 'SP' }, { k: 'vms', label: 'VM 數', mono: true }, { k: 'st', label: '最近測試' }]" />
        </template>
        <template v-else-if="iv === 'racks'">
          <p v-if="!rackRooms.length" class="muted">範圍內沒有已知機櫃位置的設備</p>
          <template v-else>
            <div class="row"><span class="muted">機房</span>
              <NuxtLink v-for="r in rackRooms" :key="r" class="chip" :class="{ on: r === rackRoom }" :to="qTo({ rr: r, rk: undefined })">{{ r }}</NuxtLink>
              <InfoNote>範圍內的設備在所屬機櫃正面亮起（VM 亮它所在的 ESXi），顏色＝最近一輪連線測試：通／不通／未測。點機櫃切換要看哪幾櫃。</InfoNote></div>
            <div class="row"><span class="muted">機櫃</span>
              <NuxtLink v-for="[k, n] in litRacks" :key="k" class="chip" :class="{ on: shownRacks.includes(k) }" :to="togRack(k)">{{ k }}（{{ n }}）</NuxtLink></div>
            <div class="racks">
              <div v-for="r in shownRacks" :key="r" class="rk"><b>{{ r }}</b>
                <ClientOnly><RackElevation :room="rackRoom" :rack="r" :hi-keys="hiKeysOf(r)" :status-by-key="statusByKey" :u-px="22" compact /></ClientOnly></div>
            </div>
          </template>
        </template>
        <template v-else>
          <div class="row"><NuxtLink class="chip" :class="{ on: !mf }" :to="qTo({ mf: undefined })">全部 {{ MS.length }}</NuxtLink>
            <NuxtLink v-for="f in M_FILTERS" :key="f.k" class="chip" :class="{ on: mf?.k === f.k }" :to="qTo({ mf: f.k })">{{ f.label }} {{ mfCount(f.k) }}</NuxtLink></div>
          <V2Table :rows="machineShown" prefix="M" :max="1000" empty="尚未設定範圍" :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'kind', label: '類型' }, { k: 'where', label: '位置' }, { k: 'systems', label: '業務系統' }, { k: 'psu_label', label: '電源' }, { k: 'check_label', label: '連線測試' }, { k: 'people', label: '負責人' }]" />
        </template>
      </template>

      <!-- 3 即時檢測 -->
      <template v-else-if="tab === 'check'">
        <div class="row"><button v-if="open && !isViewer" type="button" class="btn danger" :disabled="checking || !exp.counts.machines" @click="runCheck">{{ checking ? '測試中…' : '執行一輪連線測試' }}</button>
          <span v-if="view.last_run" class="muted">上一輪 {{ view.last_run.started_at.slice(0, 16) }}：通 {{ view.last_run.ok }}／不通 {{ view.last_run.down }}／無法測 {{ view.last_run.unknown }}</span>
          <InfoNote>唯讀連線測試（ping／連接埠），每一輪都保留。單台詳細檢測請到主機履歷按「即時檢測」。</InfoNote></div>
        <h3>每一輪</h3>
        <V2Table :rows="runHist" prefix="R" empty="還沒測過" export-name="連線測試歷史"
                 :cols="[{ k: 'at', label: '開始時間', mono: true, link: (r: any) => qTo({ run: String(r.id) }) as any }, { k: 'total', label: '台數', mono: true }, { k: 'ok', label: '通', mono: true }, { k: 'down', label: '不通', mono: true }, { k: 'unknown', label: '無法測', mono: true }, { k: 'by', label: '執行人' }]" />
        <h3>{{ pickedRun ? `那一輪：${(pickedRun.started_at || '').slice(0, 16)}` : '最近一輪' }}<NuxtLink v-if="pickedRun" class="lb" :to="qTo({ run: undefined })">回到最近一輪</NuxtLink></h3>
        <V2Table :rows="runRows" prefix="C" :max="1000" empty="尚未測試":cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key || r.ip)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'result_label', label: '結果' }, { k: 'verdict', label: '說明' }, { k: 'checked_at', label: '測試時間', mono: true }]" />
      </template>

      <!-- 4 通報聯絡 -->
      <template v-else-if="tab === 'notify'">
        <p v-if="!notify" class="muted">載入中…</p>
        <template v-else>
          <div class="row"><a class="btn" :href="`${apiBase}/api/v2/incident/${eid}/contacts.csv`">匯出聯絡名單（CSV）</a>
            <span v-if="notify.no_phone.length" class="warn">未登錄值班電話：{{ notify.no_phone.length }} 套</span></div>
          <V2Table :rows="notify.contacts" prefix="N" empty="範圍內沒有需要聯絡的業務系統" :cols="[{ k: 'kind', label: '類別' }, { k: 'ref', label: '代號', mono: true }, { k: 'name', label: '名稱' }, { k: 'level', label: '等級' }, { k: 'machines', label: '主機數', mono: true }, { k: 'oncall_phone', label: '值班電話', mono: true }, { k: 'people', label: '聯絡人' }]" />
          <h3>通知信草稿</h3>
          <dl class="kv"><dt>主旨</dt><dd><span class="mono">{{ notify.mail.subject }}</span><button type="button" class="lb" @click="copy(notify.mail.subject, '主旨')">複製</button></dd></dl>
          <pre class="mail">{{ notify.mail.body }}</pre>
          <div class="row"><button type="button" class="btn" @click="copy(notify.mail.body, '內文')">複製內文</button><span class="muted">系統不會寄信，請確認內容後自行寄出。</span></div>
        </template>
      </template>

      <!-- 5 檢核清單 -->
      <template v-else-if="tab === 'tasks'">
        <p class="muted"><InfoNote>每個業務系統一列，沒有業務系統的主機一列。修改即儲存，所有人看到同一份。</InfoNote>
          <button type="button" class="btn" :disabled="tExporting || !taskRows.length" @click="exportTasks">{{ tExporting ? '匯出中…' : `匯出（${taskRows.length} 筆）` }}</button></p>
        <table class="t">
          <thead><tr>
            <SortTh k="no" :active="tKey" :dir="tDir" @sort="tToggle">項次</SortTh><SortTh k="name" :active="tKey" :dir="tDir" @sort="tToggle">項目</SortTh>
            <SortTh k="team" :active="tKey" :dir="tDir" @sort="tToggle">組別</SortTh><SortTh k="status" :active="tKey" :dir="tDir" @sort="tToggle">狀態</SortTh>
            <SortTh k="note" :active="tKey" :dir="tDir" @sort="tToggle">備註</SortTh><SortTh k="updated_at" :active="tKey" :dir="tDir" @sort="tToggle">更新</SortTh></tr></thead>
          <tbody>
            <tr v-for="t in tSorted" :key="t.id">
              <td class="mono">K{{ t.no }}</td><td>{{ t.name }}</td>
              <td><select :value="t.team" :disabled="!open || isViewer" @change="saveTask(t, { team: ($event.target as HTMLSelectElement).value })"><option v-for="x in view.teams" :key="x" :value="x">{{ x }}</option></select></td>
              <td><select :value="t.status" :disabled="!open || isViewer" @change="saveTask(t, { status: ($event.target as HTMLSelectElement).value })"><option v-for="x in view.statuses" :key="x" :value="x">{{ x }}</option></select></td>
              <td><input :value="t.note || ''" class="in" :disabled="!open || isViewer" @blur="($event.target as HTMLInputElement).value !== (t.note || '') && saveTask(t, { note: ($event.target as HTMLInputElement).value })"></td>
              <td class="mono small">{{ t.updated_by || '' }} {{ (t.updated_at || '').slice(5, 16) }}</td>
            </tr>
            <tr v-if="!taskRows.length"><td colspan="6" class="muted">設定範圍後自動產生</td></tr>
          </tbody>
        </table>
      </template>

      <!-- 6 回報結案 -->
      <template v-else-if="tab === 'close'">
        <div class="row"><button v-if="!isViewer" type="button" class="btn" @click="makeReport">產生回報文字</button><a class="btn" :href="`${apiBase}/api/incidents/${eid}/export.csv`">匯出事件紀錄（CSV）</a></div>
        <template v-if="report"><pre class="mail">{{ report }}</pre><div class="row"><button type="button" class="btn" @click="copy(report, '回報文字')">複製回報文字</button></div></template>
        <template v-if="open">
          <template v-if="!isViewer">
          <h3>新增紀錄</h3>
          <div class="row"><input v-model="noteText" class="in wide" placeholder="例：16:40 UPS 恢復供電" @keydown.enter="addNote"><button type="button" class="btn" @click="addNote">新增</button></div>
          <h3>結案</h3>
          <div class="row"><input v-model="summary" class="in wide" placeholder="結案摘要（必填）"><button type="button" class="btn danger" @click="closeEvent">結案</button></div>
          </template>
        </template>
        <p v-else class="muted">已於 {{ ev.closed_at?.slice(0, 16) }} 結案（{{ ev.closed_by || '—' }}）：{{ ev.summary }}</p>
        <h3>時間軸</h3>
        <p v-if="tlState === 'loading'" class="muted" data-testid="tl-loading">時間軸載入中…</p>
        <p v-else-if="tlState === 'error'" class="err" data-testid="tl-error">時間軸讀取失敗，處置紀錄尚未確認（{{ tlError }}）　<button type="button" class="btn" data-testid="tl-retry" @click="loadTimeline">重試</button></p>
        <V2Table v-else :rows="tlRows" prefix="L" empty="尚無紀錄" :cols="[{ k: 'at16', label: '時間', mono: true }, { k: 'kind', label: '類別' }, { k: 'text', label: '內容' }, { k: 'created_by', label: '人員' }]" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.cat { padding: 3px 10px; border-radius: 999px; background: var(--st-stale-bg); color: var(--st-stale); font-size: 13px; }
.st { padding: 3px 10px; border-radius: 999px; background: var(--st-missing-bg); color: var(--st-missing); font-size: 13px; }
.st.closed { background: var(--st-unknown-bg); color: var(--st-unknown); }
h3 { margin: 16px 0 6px; font-size: 15px; }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 8px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0; font-size: 13px; }
a.chip { text-decoration: none; }
.chip.on { background: var(--mint); }
.racks { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.rk { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; text-decoration: none; cursor: pointer; }
.btn.danger { background: var(--v2-danger); border-color: var(--v2-danger); color: var(--type-on); }
.btn:disabled { opacity: .5; cursor: default; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--brand); background: var(--card); color: var(--brand-dark); cursor: pointer; }
.in { font-family: inherit; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: 200px; }
.in.wide { width: min(560px, 100%); }
.ta { width: 100%; font-family: var(--disp), monospace; font-size: 13px; border: 1px solid var(--border-strong); border-radius: 8px; padding: 8px; }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; margin-left: 10px; }
.lb.nm { margin-left: 0; }
.mail { white-space: pre-wrap; font-size: 13px; background: var(--paper); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; max-height: 420px; overflow: auto; }
.t { width: 100%; border-collapse: collapse; font-size: 13px; }
.t th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.t td { padding: 4px 8px; border-bottom: 1px solid var(--line); }
.mono { font-family: var(--disp); } .small { font-size: 12px; }
.muted { color: var(--muted); font-size: 13px; }
.warn { color: var(--st-stale); }
.err { color: var(--bad); }
</style>
