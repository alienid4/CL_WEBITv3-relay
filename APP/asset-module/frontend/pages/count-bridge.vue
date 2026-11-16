<script setup lang="ts">
// 7 對帳橋（2026-10-26）：每一筆現役登記歸到剛好一種角色 → 三個候選頭條（試算）。
// 使用者拍板：先做橋、看完分類再決定頭條，所以這頁只「算給人看」，首頁頭條不動。
// 另外兩個分頁收的是從首頁搬過來的「過程數字」（掃描對帳、資料完整度），數字意思不變。
// 所有數字都由後端 count_bridge.py／dashboard 算，這裡不另算（加總＝下鑽由 verify_numbers 釘住）。
interface RoleRow {
  role: string; label: string; unit: string; cia_rows: number; offbook_rows: number; rows: number
  units: number; frames: number; counted_rows: number; counted_units: number; override_rows: number
  vios_rows: number; racked_rows: number; unparsed_rows: number; no_rack_rows: number; no?: string
}
interface Part { role: string; measure: string; label: string; n: number; counted: number; rack: number; unparsed: number; none: number }
interface Trial {
  key: string; label: string; unit: string; value: number; counted: number; rack: number; unparsed: number; none: number; parts: Part[]
}
interface Bridge {
  roles: RoleRow[]; trial: Trial[]; lpar_vios: number; count_threshold: number
  totals: { rows: number; cia_rows: number; offbook_rows: number; override_rows: number; counted_machines: number }
  existence_counted_in_db: number; as_of: string; can_edit: boolean
  roles_all: { role: string; label: string }[]
  invalid_overrides?: { asset_serial: string; role: string; updated_by: string | null; updated_at: string }[]
}
interface Item {
  no: number; asset_serial: string; side: string; hostname: string | null; ip: string | null
  os: string | null; os_class: string; model: string | null; room: string | null; rack: string | null
  u: string | null; rack_raw: string | null; racked: boolean; rack_status: string; rack_unparsed: boolean; rack_missing: boolean; role: string; role_label: string
  auto_role: string | null; override: boolean; reason: string; frame: string; host_serial: string | null
  exist_score: number | null; counted: boolean; vios: boolean
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const { guardedFetch } = useGuardedFetch()   // 寫入關卡：登入停用時要管理密碼（跟 4-5 拖拉搬移同一支）
const route = useRoute()
const router = useRouter()

type Tab = 'bridge' | 'scan' | 'quality'
const TABS: { k: Tab; label: string }[] = [
  { k: 'bridge', label: '對帳橋' }, { k: 'scan', label: '掃描對帳' }, { k: 'quality', label: '資料完整度' },
]
const tab = computed<Tab>({
  get: () => (TABS.some((t) => t.k === route.query.tab) ? route.query.tab as Tab : 'bridge'),
  set: (v) => { router.replace({ query: { tab: v } }) },
})

function n(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : v.toLocaleString()
}

// ===== A 對帳橋 =====
const data = ref<Bridge | null>(null)
const loadErr = ref('')
async function loadBridge() {
  try {
    data.value = await apiFetch<Bridge>('/api/count-bridge')
    loadErr.value = ''
  } catch (e: any) {
    loadErr.value = e?.data?.detail ?? e?.message ?? '對帳橋載入失敗'
  }
}
await loadBridge()

// 項次照後端角色順序固定（R1、R2…），重排後同一列仍是同一個號碼
const roleRows = computed<RoleRow[]>(() => (data.value?.roles ?? []).map((r, i) => ({ ...r, no: `R${i + 1}` })))
const { sortKey, sortDir, toggle, sorted: sortedRoles } = useSort(roleRows)
const sumCia = computed(() => roleRows.value.reduce((s, r) => s + r.cia_rows, 0))
const sumOff = computed(() => roleRows.value.reduce((s, r) => s + r.offbook_rows, 0))
const sumRows = computed(() => roleRows.value.reduce((s, r) => s + r.rows, 0))

// 下鑽：點任何一格 → 列出是哪幾筆（同一份 compute，筆數＝格子上的數字）
interface DrillQ { role?: string; side?: string; measure?: string; counted?: boolean; title: string }
const drillQ = ref<DrillQ | null>(null)
const drillItems = ref<Item[]>([])
const drillFrames = ref(0)
const drillLoading = ref(false)
const q = ref('')
const page = ref(1)
const PAGE = 200
async function openDrill(d: DrillQ) {
  drillQ.value = d
  drillLoading.value = true
  q.value = ''
  page.value = 1
  try {
    const params: Record<string, any> = {}
    if (d.role) params.role = d.role
    if (d.side) params.side = d.side
    if (d.measure && ['rack', 'unparsed', 'none'].includes(d.measure)) params.measure = d.measure
    if (d.counted !== undefined) params.counted = d.counted
    const r = await apiFetch<{ items: Item[]; frames: number }>('/api/count-bridge/items', { params })
    drillItems.value = r.items
    drillFrames.value = r.frames
  } catch (e: any) {
    drillItems.value = []
    showToast(`明細載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally {
    drillLoading.value = false
  }
}
function roleLabel(k?: string) { return data.value?.roles_all.find((r) => r.role === k)?.label ?? k ?? '' }
function cell(r: RoleRow, side?: string) {
  openDrill({ role: r.role, side, title: `${r.no} ${r.label}${side ? `（${side}）` : ''}` })
}
function partDrill(p: Part, counted = false) {
  openDrill({ role: p.role, measure: p.measure, counted: counted || undefined,
              title: `${p.label}${counted ? '（存在可信度達門檻）' : ''}` })
}
// 實體設備的「有機櫃／缺機櫃資料」：各組成各自下鑽（缺機櫃只會出現在實體設備四類與 ESXi）
const RACK_LABEL: Record<string, string> = { rack: '有機櫃', unparsed: '機櫃欄認不出', none: '缺機櫃資料' }
function rackDrill(p: Part, st: 'rack' | 'unparsed' | 'none') {
  openDrill({ role: p.role, measure: st,
              title: `${p.label}（${RACK_LABEL[st]}）` })
}
const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  if (!s) return drillItems.value
  return drillItems.value.filter((x) =>
    [x.asset_serial, x.hostname, x.ip, x.model, x.rack_raw, x.reason, x.os].some((v) => (v ?? '').toLowerCase().includes(s)))
})
const { sortKey: dKey, sortDir: dDir, toggle: dToggle, sorted: dSorted } = useSort(filtered)
const pages = computed(() => Math.max(1, Math.ceil(dSorted.value.length / PAGE)))
const pageRows = computed(() => dSorted.value.slice((page.value - 1) * PAGE, page.value * PAGE))
watch(q, () => { page.value = 1 })

// 人工指定（限管理員；邊角案例用，重匯 CIA 不會蓋掉，每次都留稽核紀錄）
const saving = ref<string | null>(null)
async function setRole(it: Item, role: string) {
  const target = role === '' ? null : role
  if (target === (it.override ? it.role : null)) return
  const note = target ? (window.prompt(`把 ${it.hostname ?? it.asset_serial} 指定成「${roleLabel(target)}」的原因`) ?? null) : null
  if (target && note === null) return
  saving.value = it.asset_serial
  try {
    await guardedFetch('/api/count-bridge/override', { method: 'PUT',
      body: { asset_serial: it.asset_serial, role: target, note } })
    showToast(target ? `已指定：${roleLabel(target)}` : '已清除人工指定，回到自動判斷', 'success')
    await loadBridge()
    if (drillQ.value) await openDrill(drillQ.value)
  } catch (e: any) {
    showToast(`指定失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally {
    saving.value = null
  }
}

// ===== B 掃描對帳／C 資料完整度：從首頁搬過來的過程數字（意思不變，只是不放首頁）=====
interface Stats {
  total_ica_count: number; total_overlap_count: number; scan_only_count: number
  total_ica_machines?: number; total_overlap_machines?: number
  covered_segments?: number; covered_ica_machines?: number; covered_overlap_machines?: number
  covered_missing_machines?: number; uncovered_ica_machines?: number; match_pct_covered?: number | null
  covered_scan_from?: string | null; covered_scan_to?: string | null; covered_scan_only?: number
  covered_empty_runs?: { scan_time: string; segments: number; kind: string }[]
  covered_suspect_down?: { cidr: string; scan_time: string }[]
  last_scan_time: string | null
}
const stats = ref<Stats | null>(null)
const comp = ref<any>(null)
const mstate = ref<any>(null)
const procErr = ref('')
async function loadProcess() {
  if (stats.value && comp.value) return
  try {
    const [s, c] = await Promise.all([
      apiFetch<Stats>('/api/dashboard/stats', { params: { environment: '全部' } }),
      apiFetch<any>('/api/dashboard/composition'),
    ])
    stats.value = s
    comp.value = c
  } catch (e: any) {
    procErr.value = e?.data?.detail ?? e?.message ?? '數字載入失敗'
  }
  try {
    mstate.value = await apiFetch<any>('/api/manage-state', { params: { summary: 1 } })
  } catch (e: any) {
    // 四態抓不到時，下面那幾列顯示「—」（不顯示 0，0 會被當成真的沒有）
    console.warn('四態數字載入失敗', e)
  }
}
watch(tab, (t) => { if (t !== 'bridge') loadProcess() }, { immediate: true })
function stateCount(k: string): number | null { return mstate.value ? (mstate.value.counts?.[k] ?? 0) : null }

// 掃描對帳（只算這次掃描有涵蓋的網段）：下鑽同一份 recon_coverage
const reconBucket = ref<string | null>(null)
const reconItems = ref<any[]>([])
async function openRecon(b: string) {
  reconBucket.value = b
  try {
    reconItems.value = (await apiFetch<any>('/api/count-bridge/recon', { params: { bucket: b } })).items
  } catch (e: any) {
    reconItems.value = []
    showToast(`明細載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
const reconRowsView = computed(() => reconItems.value.map((x) => ({ ...x, serials_text: (x.serials ?? []).join('、') })))
const { sortKey: rKey, sortDir: rDir, toggle: rToggle, sorted: rSorted } = useSort(reconRowsView)
const RECON_LABEL: Record<string, string> = { overlap: '一致', missing: '搜不到（有涵蓋的網段）', uncovered: '未涵蓋' }
const scanCards = computed(() => {
  const s = stats.value
  if (!s) return []
  return [
    { no: 'S1', key: 'overlap', label: '一致', n: s.covered_overlap_machines ?? s.total_overlap_machines, tone: 'good' },
    { no: 'S2', key: 'missing', label: '搜不到（有涵蓋的網段）', n: s.covered_missing_machines, tone: 'bad' },
    { no: 'S3', key: 'uncovered', label: '未涵蓋', n: s.uncovered_ica_machines, tone: 'muted' },
  ]
})
// 依據日期（再審）：讓人一眼看出 73.7% 是哪一天的掃描，不是今天的即時數字
const basisText = computed(() => {
  const a = (stats.value?.covered_scan_from ?? '').slice(5, 10)
  const b = (stats.value?.covered_scan_to ?? '').slice(5, 10)
  if (!a && !b) return '（還沒有可用的掃描）'
  return a === b || !a || !b ? `依 ${a || b} 掃描` : `依 ${a}～${b} 掃描`
})
const skippedRuns = computed(() => (stats.value?.covered_empty_runs ?? []).filter((x) => x.kind !== 'suspect_down'))
const legacyMissing = computed(() => {
  const s = stats.value
  return s ? (s.total_ica_machines ?? s.total_ica_count) - (s.total_overlap_machines ?? s.total_overlap_count) : null
})
// 首頁原本那一串對帳明細（意思不變，從首頁搬過來）
const stateRows = computed(() => [
  { no: 'S4', key: '這次掃到、清冊查無', tone: 'warn', n: stats.value?.scan_only_count ?? null, to: { path: '/adopt' } },
  { no: 'S5', key: '已納管', tone: 'good', n: stateCount('已納管'), to: { path: '/manage-state-detail', query: { state: '已納管' } } },
  { no: 'S6', key: '已納管但失聯', tone: 'bad', n: stateCount('已納管但失聯'), to: { path: '/manage-state-detail', query: { state: '已納管但失聯' } } },
  { no: 'S7', key: '已收集', tone: 'good', n: stateCount('已收集'), to: { path: '/manage-state-detail', query: { state: '已收集' } } },
  { no: 'S8', key: '登記矛盾', tone: 'warn', n: stateCount('登記矛盾'), to: { path: '/pipeline', query: { stage: 'conflict' } } },
  { no: 'S9', key: '退役但仍在線', tone: 'bad', n: stateCount('退役但仍在線'), to: { path: '/pipeline', query: { stage: 'retired_alive' } } },
  { no: 'S10', key: '沒有 IP', tone: 'warn', n: stateCount('沒有 IP'), to: { path: '/pipeline', query: { stage: 'no_ip' } } },
  { no: 'S11', key: '已退役', tone: 'muted', n: stateCount('已退役'), to: { path: '/manage-state-detail', query: { state: '已退役' } } },
])

// 資料完整度：OS 來源、CIA 完整率、平台「其他／未知」
const ex = computed(() => comp.value?.existence ?? null)
const osRows = computed(() => {
  const c = comp.value
  if (!c) return []
  return [
    { no: 'Q1', label: '有填作業系統', unit: '筆', n: c.os_from_facts, to: { path: '/assets', query: { os_source: 'facts' } } },
    { no: 'Q2', label: '掃描推測', unit: '筆', n: c.os_guessed, to: { path: '/assets', query: { os_source: 'guessed' } } },
    { no: 'Q3', label: 'OS 未知', unit: '筆', n: c.data_quality?.os_unknown, to: { path: '/assets', query: { filter_field: 'os', filter_value: '' } } },
  ]
})
const existRows = computed(() => {
  const e = ex.value
  if (!e) return []
  return [
    { no: 'Q4', label: 'CIA 完整率', v: e.cia_complete_pct === null ? '—' : `${e.cia_complete_pct}%`, to: { path: '/existence', query: { bucket: 'cia_registered' } } },
    { no: 'Q5', label: 'CIA 待補登', v: `${n(e.cia_pending)} 台`, to: { path: '/existence', query: { bucket: 'cia_pending' } } },
    { no: 'Q6', label: '疑似下線（不計入）', v: `${n(e.suspect_offline)} 台`, to: { path: '/existence', query: { bucket: 'suspect_offline' } } },
    { no: 'Q7', label: '在管（真的存在）', v: `${n(e.counted)} 台`, to: { path: '/existence', query: { bucket: 'counted' } } },
    { no: 'Q8', label: '已退役', v: `${n(e.retired)} 台`, to: { path: '/existence', query: { bucket: 'retired' } } },
    { no: 'Q9', label: '系統知道的全部機器', v: `${n(e.total_machines)} 台`, to: { path: '/existence', query: { bucket: 'all' } } },
  ]
})
const platformRows = computed(() => {
  const c = comp.value
  if (!c) return []
  const m = (c.by_os_class ?? {}) as Record<string, number>
  const order: string[] = c.os_class_order ?? Object.keys(m)
  const keys = [...order.filter((k) => k in m), ...Object.keys(m).filter((k) => !order.includes(k))]
  return keys.map((k, i) => ({ no: `Q${10 + i}`, key: k, n: m[k], gap: k === '其他' || k === '未知' }))
})
</script>

<template>
  <div class="cb">
    <V2Banner to="/v2/reconcile" label="9.2 數量對帳" />
    <div class="section-divider">對帳橋</div>

    <div class="atabs">
      <button v-for="t in TABS" :key="t.k" class="atab" :class="{ on: tab === t.k }" type="button" @click="tab = t.k">{{ t.label }}</button>
    </div>
    <p v-if="loadErr && tab === 'bridge'" class="error-text">{{ loadErr }}</p>

    <!-- ===== A 對帳橋 ===== -->
    <template v-if="tab === 'bridge' && data">
      <div class="trial">
        <div v-for="t in data.trial" :key="t.key" class="tcard">
          <div class="tk">{{ t.label }} <span class="tag">試算</span>
            <InfoNote>
              <template v-if="t.key === 'physical'">依類型算：伺服器／儲存／網路／其他設備、ESXi。
                「有機櫃」跟 4-5 機房架構圖是同一份（同一個箱子的 LPAR／元件算一台、管理口併進主機不算、
                找不到本體但有自己 U 位的管理口與疑似登記錯誤各算一台），數字＝4-5 機櫃台數。
                機櫃欄填了但認不出的照算、標「機櫃欄認不出」；沒填的也照算、標「缺機櫃資料」（4-5 兩種都列在「無機櫃資料」）。
                PC 級機器要在機櫃上才算，沒有機櫃的桌機筆電歸端點。無法判斷不算。</template>
              <template v-else-if="t.key === 'hosts'">OS 實例：實體伺服器＋LPAR＋VM。ESXi 另列虛擬化平台、不算；
                有機櫃的 PC 不算。一體機元件裡跑 OS 的（Exadata／ODA 的 DB 節點）算，儲存 cell 不算；
                疑似登記錯誤裡是伺服器 OS 的也算（使用者 2026-10-26 決定）。VIOS 算在 LPAR 裡（{{ n(data.lpar_vios) }} 筆）。</template>
              <template v-else>iLO／ILOM／iDRAC…。掛在本體＝4-5 併進主機的那幾個，不算「台」。
                找不到本體、但有自己 U 位的，4-5 照算一台，所以也出現在實體設備（有機櫃）裡。HMC、Serial Console 算實體設備。</template>
              <br>「真的存在」＝存在可信度 ≥ {{ data.count_threshold }}（EXIST1）。首頁頭條還沒換，看完分類再決定。
            </InfoNote>
          </div>
          <div class="tv mono">{{ n(t.value) }}<small> {{ t.unit }}</small></div>
          <div class="tsub">其中真的存在 <b class="mono">{{ n(t.counted) }}</b></div>
          <div v-if="t.key === 'physical'" class="tsub">有機櫃 <b class="mono">{{ n(t.rack) }}</b>
            · 機櫃欄認不出 <b class="mono">{{ n(t.unparsed) }}</b> · 缺機櫃資料 <b class="mono">{{ n(t.none) }}</b></div>
          <ol class="parts">
            <li v-for="p in t.parts" :key="p.role + p.measure">
              <button type="button" class="lnk" @click="partDrill(p)">{{ p.label }}</button>
              <span class="mono">{{ n(p.n) }}</span>
              <button type="button" class="lnk sub" @click="partDrill(p, true)">存在 {{ n(p.counted) }}</button>
              <template v-if="t.key === 'physical'">
                <button type="button" class="lnk sub" @click="rackDrill(p, 'rack')">有櫃 {{ n(p.rack) }}</button>
                <button type="button" class="lnk sub" @click="rackDrill(p, 'unparsed')">認不出 {{ n(p.unparsed) }}</button>
                <button type="button" class="lnk sub" @click="rackDrill(p, 'none')">缺櫃 {{ n(p.none) }}</button>
              </template>
            </li>
          </ol>
        </div>
      </div>

      <p v-if="data.invalid_overrides?.length" class="warnline">
        ⚠ 失效覆寫 {{ data.invalid_overrides.length }} 筆：指定的角色已經沒有了，先照自動判斷算
        <InfoNote>改版拿掉了某些角色，之前人工指定成那些角色的登記不再套用（不會讓整頁壞掉），請重新指定：
          <template v-for="x in data.invalid_overrides" :key="x.asset_serial"><br>{{ x.asset_serial }}：{{ x.role }}（{{ x.updated_by ?? '—' }} {{ x.updated_at }}）</template>
        </InfoNote>
      </p>
      <section class="card">
        <div class="ck">每筆登記歸一種角色
          <InfoNote>每一筆現役登記（不含退役）剛好落在一列；CIA 筆＋帳外筆＝{{ n(data.totals.rows) }} 筆。
            「台／部／個」：LPAR 以筆算部、一體機／同一個箱子的元件以箱子算、重複登記不算。
            「存在」＝存在可信度達門檻的筆數（計入的機器 {{ n(data.totals.counted_machines) }} 台，跟存在可信度頁同一份）。
            判法：先人工指定 → 重複登記（machine_key）→ 有機櫃 U 位的照 4-5 的箱子歸類 → 沒有 U 位的：管理口 →
            帳外低可信度 → ESXi／VM → 機櫃欄認不出（照類型算實體設備）→ VM 訊號（vCenter 清單、VMware MAC、
            vSphere 客體 OS 寫法…）→ 端點 → 依類型算實體設備（缺機櫃資料）→ 無法判斷。
            {{ data.as_of }}</InfoNote>
        </div>
        <div class="tw">
          <table class="tbl">
            <thead><tr>
              <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
              <SortTh k="label" :active="sortKey" :dir="sortDir" @sort="toggle">角色</SortTh>
              <SortTh k="cia_rows" :active="sortKey" :dir="sortDir" @sort="toggle">CIA 筆</SortTh>
              <SortTh k="offbook_rows" :active="sortKey" :dir="sortDir" @sort="toggle">帳外 筆</SortTh>
              <SortTh k="rows" :active="sortKey" :dir="sortDir" @sort="toggle">合計 筆</SortTh>
              <SortTh k="units" :active="sortKey" :dir="sortDir" @sort="toggle">台／部／個</SortTh>
              <SortTh k="counted_rows" :active="sortKey" :dir="sortDir" @sort="toggle">存在 筆</SortTh>
              <SortTh k="unparsed_rows" :active="sortKey" :dir="sortDir" @sort="toggle">機櫃欄認不出</SortTh>
              <SortTh k="no_rack_rows" :active="sortKey" :dir="sortDir" @sort="toggle">缺機櫃資料</SortTh>
              <SortTh k="override_rows" :active="sortKey" :dir="sortDir" @sort="toggle">人工指定</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="r in sortedRoles" :key="r.role">
                <td class="mono">{{ r.no }}</td>
                <td>{{ r.label }}</td>
                <td class="num"><button type="button" class="lnk mono" @click="cell(r, 'CIA')">{{ n(r.cia_rows) }}</button></td>
                <td class="num"><button type="button" class="lnk mono" @click="cell(r, '帳外')">{{ n(r.offbook_rows) }}</button></td>
                <td class="num"><button type="button" class="lnk mono" @click="cell(r)">{{ n(r.rows) }}</button></td>
                <td class="num"><button type="button" class="lnk mono" @click="cell(r)">{{ r.unit === '—' ? '—' : `${n(r.units)} ${r.unit}` }}</button></td>
                <td class="num"><button type="button" class="lnk mono" @click="openDrill({ role: r.role, counted: true, title: `${r.no} ${r.label}（存在）` })">{{ n(r.counted_rows) }}</button></td>
                <td class="num"><button v-if="r.unparsed_rows" type="button" class="lnk mono" @click="openDrill({ role: r.role, measure: 'unparsed', title: `${r.no} ${r.label}（機櫃欄認不出）` })">{{ n(r.unparsed_rows) }}</button><span v-else class="muted">0</span></td>
                <td class="num"><button v-if="r.no_rack_rows" type="button" class="lnk mono" @click="openDrill({ role: r.role, measure: 'none', title: `${r.no} ${r.label}（缺機櫃資料）` })">{{ n(r.no_rack_rows) }}</button><span v-else class="muted">0</span></td>
                <td class="num mono">{{ n(r.override_rows) }}</td>
              </tr>
            </tbody>
            <tfoot><tr>
              <td /><td><b>合計</b></td>
              <td class="num mono"><b>{{ n(sumCia) }}</b></td>
              <td class="num mono"><b>{{ n(sumOff) }}</b></td>
              <td class="num mono"><b>{{ n(sumRows) }}</b></td>
              <td /><td /><td /><td /><td />
            </tr></tfoot>
          </table>
        </div>
      </section>

      <section v-if="drillQ" class="card">
        <div class="dh">
          <div class="ck">{{ drillQ.title }}
            <span class="cnt mono">{{ n(drillItems.length) }} 筆<template v-if="drillFrames !== drillItems.length"> · {{ n(drillFrames) }} 框</template></span>
          </div>
          <input v-model="q" class="q" placeholder="搜尋主機名／IP／序號／型號／機櫃">
          <button type="button" class="lnk" @click="drillQ = null">關閉</button>
        </div>
        <p v-if="drillLoading" class="muted">載入中…</p>
        <div v-else class="tw">
          <table class="tbl">
            <thead><tr>
              <SortTh k="no" :active="dKey" :dir="dDir" @sort="dToggle">項次</SortTh>
              <SortTh k="side" :active="dKey" :dir="dDir" @sort="dToggle">來源</SortTh>
              <SortTh k="asset_serial" :active="dKey" :dir="dDir" @sort="dToggle">序號</SortTh>
              <SortTh k="hostname" :active="dKey" :dir="dDir" @sort="dToggle">主機名</SortTh>
              <SortTh k="ip" :active="dKey" :dir="dDir" @sort="dToggle">IP</SortTh>
              <SortTh k="os_class" :active="dKey" :dir="dDir" @sort="dToggle">OS 大類</SortTh>
              <SortTh k="model" :active="dKey" :dir="dDir" @sort="dToggle">型號</SortTh>
              <SortTh k="rack_raw" :active="dKey" :dir="dDir" @sort="dToggle">機房／機櫃</SortTh>
              <SortTh k="exist_score" :active="dKey" :dir="dDir" @sort="dToggle">存在可信度</SortTh>
              <SortTh k="reason" :active="dKey" :dir="dDir" @sort="dToggle">為什麼</SortTh>
              <SortTh k="role_label" :active="dKey" :dir="dDir" @sort="dToggle">角色</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="x in pageRows" :key="x.asset_serial">
                <td class="mono">{{ x.no }}</td>
                <td>{{ x.side }}</td>
                <td class="mono"><NuxtLink :to="`/assets/${encodeURIComponent(x.asset_serial)}`">{{ x.asset_serial }}</NuxtLink></td>
                <td>{{ x.hostname ?? '—' }}<span v-if="x.vios" class="tag">VIOS</span></td>
                <td class="mono">{{ x.ip ?? '—' }}</td>
                <td>{{ x.os_class }}</td>
                <td>{{ upText(x.model) || '—' }}</td>
                <td>{{ x.room ?? "" }} {{ x.rack_raw ?? "—" }}<span v-if="x.rack_missing" class="tag">缺機櫃資料</span><span v-if="x.rack_unparsed" class="tag">機櫃欄認不出</span></td>
                <td class="num mono" :class="{ muted: !x.counted }">{{ x.exist_score ?? '—' }}</td>
                <td class="why">{{ x.reason }}<template v-if="x.host_serial"> · <NuxtLink :to="`/assets/${encodeURIComponent(x.host_serial)}`">{{ x.host_serial }}</NuxtLink></template></td>
                <td>
                  <select v-if="data.can_edit" class="sel" :disabled="saving === x.asset_serial"
                          :value="x.override ? x.role : ''" @change="setRole(x, ($event.target as HTMLSelectElement).value)">
                    <option value="">自動：{{ x.override ? '（清除指定）' : x.role_label }}</option>
                    <option v-for="o in data.roles_all" :key="o.role" :value="o.role">{{ o.label }}</option>
                  </select>
                  <span v-else>{{ x.role_label }}</span>
                  <span v-if="x.override" class="tag">人工</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="pages > 1" class="pager">
            <button type="button" class="lnk" :disabled="page <= 1" @click="page--">‹ 上一頁</button>
            <span class="mono">{{ page }} / {{ pages }}</span>
            <button type="button" class="lnk" :disabled="page >= pages" @click="page++">下一頁 ›</button>
          </div>
        </div>
      </section>
    </template>

    <!-- ===== B 掃描對帳（從首頁搬來；分母只算這次掃描有涵蓋的網段）===== -->
    <template v-if="tab === 'scan'">
      <p v-if="procErr" class="error-text">{{ procErr }}</p>
      <section v-if="stats" class="card">
        <div class="ck">帳實相符 <b class="mono big">{{ stats.match_pct_covered === null || stats.match_pct_covered === undefined ? '—' : `${stats.match_pct_covered}%` }}</b>
          <span class="basis">{{ basisText }}</span>
          <InfoNote>只算「這一輪」有涵蓋的 {{ n(stats.covered_segments) }} 個網段：每個網段取近 30 天內最近一次**有結果**的掃描
            （{{ stats.covered_scan_from ?? '—' }} ～ {{ stats.covered_scan_to ?? '—' }}），不是最後一次補掃，也不是今天的即時數字。
            一致 ÷（有涵蓋的登記台數 ＋ 這一輪掃到、清冊查無 {{ n(stats.covered_scan_only) }}）。沒掃到的網段不算「對不上」，另列「未涵蓋」。
            一次掃很多段卻全部 0 台（掃描器或網路壞了）跳過不算；只掃 1–2 段卻 0 台的：前後 24 小時內掃描器有掃到東西，才當成那段可能整段斷線、照算（那段的機器算搜不到）；否則是掃描器當時整體沒回應，一樣略過。
            <br><br>舊口徑（首頁以前寫的）：搜不到＝CIA 現役登記 {{ n(stats.total_ica_machines) }} 台 − 一致
            ＝ {{ n(legacyMissing) }} 台，裡面包含這次根本沒掃到的網段；帳實相符＝一致筆 ÷（登記筆 ＋ 掃到沒登記）。
            台數都是 CIA 現役登記（排退役、排帳外），逐台（machine_key）。</InfoNote>
        </div>
        <p v-if="stats.covered_suspect_down?.length" class="badline">
          ⚠ 可能整段斷線：{{ stats.covered_suspect_down.map((x) => x.cidr).join('、') }}
          <InfoNote>這幾段最近一次補掃一台都沒回（只掃了 1–2 段），可能整段斷線或防火牆擋了。照算涵蓋，所以這幾段的機器算在「搜不到」裡。
            <template v-for="x in stats.covered_suspect_down" :key="x.cidr"><br>{{ x.cidr }}（{{ x.scan_time }}）</template>
          </InfoNote>
        </p>
        <p v-if="skippedRuns.length" class="warnline">
          ⚠ {{ skippedRuns.length }} 次掃描回 0 台（最近 {{ skippedRuns[0].scan_time }}，{{ skippedRuns[0].segments }} 段），沒算進對帳<template v-if="skippedRuns.some((x) => x.kind === 'skipped_outage')">；其中 {{ skippedRuns.filter((x) => x.kind === 'skipped_outage').length }} 次補掃：掃描器當時整體沒回應，略過</template>
          <InfoNote>這幾次掃描記下了「掃過哪些網段」，但一台存活都沒有，通常是掃描器或網路出問題，不是機器全部不見。
            對帳先用前一次有結果的掃描；請到 系統設定 → 掃描排程 查原因。
            <template v-for="x in skippedRuns.slice(0, 10)" :key="x.scan_time"><br>{{ x.scan_time }}（{{ x.segments }} 段{{ x.kind === 'skipped_outage' ? '，掃描器當時整體沒回應，略過' : '' }}）</template>
          </InfoNote>
        </p>
        <div class="cards">
          <button v-for="c in scanCards" :key="c.key" type="button" class="scard" :class="[c.tone, { on: reconBucket === c.key }]" @click="openRecon(c.key)">
            <span class="no mono">{{ c.no }}</span>
            <span class="sl">{{ c.label }}</span>
            <b class="mono">{{ n(c.n) }}</b><small>台</small>
          </button>
        </div>
        <div class="rows">
          <NuxtLink v-for="r in stateRows" :key="r.key" :to="r.to" class="rrow">
            <span class="no mono">{{ r.no }}</span><i class="dot" :class="r.tone" />
            <span class="rn">{{ r.key }}</span><b class="mono" :class="r.tone">{{ n(r.n) }}</b><span class="arw">›</span>
          </NuxtLink>
        </div>
      </section>
      <section v-if="reconBucket" class="card">
        <div class="dh">
          <div class="ck">{{ RECON_LABEL[reconBucket] }} <span class="cnt mono">{{ n(reconItems.length) }} 台</span></div>
          <button type="button" class="lnk" @click="reconBucket = null">關閉</button>
        </div>
        <div class="tw">
          <table class="tbl">
            <thead><tr>
              <SortTh k="no" :active="rKey" :dir="rDir" @sort="rToggle">項次</SortTh>
              <SortTh k="hostname" :active="rKey" :dir="rDir" @sort="rToggle">主機名</SortTh>
              <SortTh k="ip" :active="rKey" :dir="rDir" @sort="rToggle">IP</SortTh>
              <SortTh k="serials_text" :active="rKey" :dir="rDir" @sort="rToggle">登記序號</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="x in rSorted.slice(0, 500)" :key="x.key">
                <td class="mono">{{ x.no }}</td>
                <td>{{ x.hostname ?? '—' }}</td>
                <td class="mono">{{ x.ip ?? '—' }}</td>
                <td class="mono"><NuxtLink :to="`/assets/${encodeURIComponent(x.asset_serial)}`">{{ x.serials_text }}</NuxtLink></td>
              </tr>
            </tbody>
          </table>
          <p v-if="rSorted.length > 500" class="muted">只列前 500 台（共 {{ n(rSorted.length) }} 台），用排序找。</p>
        </div>
      </section>
    </template>

    <!-- ===== C 資料完整度（從首頁搬來）===== -->
    <template v-if="tab === 'quality'">
      <p v-if="procErr" class="error-text">{{ procErr }}</p>
      <div v-if="comp" class="qgrid">
        <section class="card">
          <div class="ck">作業系統來源
            <InfoNote>以前首頁寫「N 台實際收集」是錯的：那是現役登記裡 OS 欄有值的**筆數**，不是收集到的台數。
              真的納管收集到的看 2-1／存在可信度的「已納管」。</InfoNote></div>
          <NuxtLink v-for="r in osRows" :key="r.no" :to="r.to" class="rrow">
            <span class="no mono">{{ r.no }}</span><span class="rn">{{ r.label }}</span>
            <b class="mono">{{ n(r.n) }}</b><small>{{ r.unit }}</small><span class="arw">›</span>
          </NuxtLink>
        </section>
        <section v-if="ex" class="card">
          <div class="ck">CIA 完整度
            <InfoNote>CIA 完整率＝在管（存在可信度達門檻）的機器裡有 CIA 登記的比例。
              在管＋疑似下線＋已退役＝系統知道的全部機器（逐台）。</InfoNote></div>
          <NuxtLink v-for="r in existRows" :key="r.no" :to="r.to" class="rrow">
            <span class="no mono">{{ r.no }}</span><span class="rn">{{ r.label }}</span>
            <b class="mono">{{ r.v }}</b><span class="arw">›</span>
          </NuxtLink>
        </section>
        <section class="card">
          <div class="ck">作業系統平台
            <InfoNote>「其他／未知」是資料缺口：OS 欄寫了認不出的字、或根本沒填。點進去看是哪幾筆。</InfoNote></div>
          <NuxtLink v-for="r in platformRows" :key="r.key" :to="{ path: '/assets', query: { os_class: r.key } }" class="rrow" :class="{ gap: r.gap }">
            <span class="no mono">{{ r.no }}</span><span class="rn">{{ r.key }}</span>
            <b class="mono">{{ n(r.n) }}</b><small>筆</small><span class="arw">›</span>
          </NuxtLink>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cb { color: var(--ink); }
.atabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.atab { background: transparent; border: 1px solid var(--border-strong); color: var(--ink-aux);
  border-radius: 999px; padding: 6px 16px; font-size: 13px; cursor: pointer; }
.atab:hover { border-color: var(--brand); }
.atab.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); font-weight: 600; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 18px 20px; margin-bottom: 18px; }
.ck { font-size: 13px; font-weight: 600; color: var(--ink-soft); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.big { font-size: 22px; color: var(--brand-dark); }
.muted { color: var(--muted); }
.error-text { color: var(--bad); font-size: 13px; }
.badline { color: var(--bad); background: var(--bad-soft); border: 1px solid var(--bad); border-radius: 10px; padding: 8px 12px;
  font-size: 12.5px; margin: 0 0 12px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.basis { font-size: 12px; font-weight: 500; color: var(--ink-aux); border: 1px solid var(--border-strong); border-radius: 999px; padding: 1px 9px; }
.warnline { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 8px 12px; font-size: 12.5px;
  margin: 0 0 12px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.tag { font-size: 10.5px; border: 1px solid var(--border-strong); color: var(--ink-aux); border-radius: 999px;
  padding: 1px 7px; margin-left: 6px; font-weight: 500; }
.trial { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-bottom: 18px; }
@media (max-width: 1000px) { .trial { grid-template-columns: 1fr; } }
.tcard { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; }
.tk { font-size: 13px; font-weight: 600; color: var(--ink-soft); display: flex; align-items: center; gap: 4px; }
.tv { font-size: 34px; font-weight: 600; color: var(--brand-dark); margin: 6px 0 2px; }
.tv small { font-size: 14px; color: var(--muted); }
.tsub { font-size: 12.5px; color: var(--ink-aux); margin-bottom: 8px; }
.parts { margin: 0; padding-left: 20px; font-size: 12.5px; }
.parts li { display: flex; gap: 8px; align-items: baseline; padding: 2px 0; }
.parts li .mono { margin-left: auto; }
.lnk { background: none; border: none; padding: 0; color: var(--brand); cursor: pointer; font: inherit; text-align: left; }
.lnk:hover { color: var(--brand-dark); text-decoration: underline; }
.lnk:disabled { color: var(--muted); cursor: not-allowed; text-decoration: none; }
.lnk.sub { color: var(--ink-aux); font-size: 11.5px; }
.tw { overflow-x: auto; margin-top: 10px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th, .tbl td { padding: 7px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.tbl th { color: var(--ink-aux); font-weight: 600; background: var(--sub); }
.tbl tfoot td { border-top: 1px solid var(--border-strong); }
.num { text-align: right; }
.why { color: var(--ink-soft); max-width: 340px; }
.dh { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.cnt { color: var(--muted); font-weight: 500; }
.q { margin-left: auto; min-width: 240px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px; font: inherit; font-size: 12.5px; }
.sel { font: inherit; font-size: 12px; padding: 3px 6px; max-width: 220px; }
.pager { display: flex; gap: 14px; justify-content: center; padding: 10px 0; font-size: 12.5px; }
.cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }
@media (max-width: 800px) { .cards { grid-template-columns: 1fr; } }
.scard { display: flex; align-items: baseline; gap: 8px; background: var(--sub); border: 1px solid var(--border);
  border-radius: 12px; padding: 12px 14px; cursor: pointer; font: inherit; color: var(--ink); text-align: left; }
.scard:hover, .scard.on { border-color: var(--brand); }
.scard b { font-size: 22px; margin-left: auto; }
.scard.bad b { color: var(--bad); }
.scard.good b { color: var(--good); }
.scard.muted b { color: var(--ink-aux); }
.sl { font-size: 12.5px; color: var(--ink-soft); }
.no { font-size: 11px; color: var(--muted); min-width: 28px; }
.rows { margin-top: 6px; }
.rrow { display: flex; align-items: center; gap: 10px; padding: 8px 4px; border-bottom: 1px solid var(--line);
  color: var(--ink); text-decoration: none; font-size: 13px; }
.rrow:hover { background: var(--mint); }
.rrow .rn { flex: 1; }
.rrow b.bad { color: var(--bad); }
.rrow b.good { color: var(--good); }
.rrow b.warn { color: var(--warn-text); }
.rrow.gap .rn { color: var(--warn-text); }
.rrow small { color: var(--muted); }
.arw { color: var(--muted); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--good); flex: none; }
.dot.warn { background: var(--warn); }
.dot.bad { background: var(--bad); }
.dot.muted { background: var(--border-strong); }
.qgrid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; align-items: start; }
@media (max-width: 1100px) { .qgrid { grid-template-columns: 1fr; } }
.qgrid .card { margin-bottom: 0; }
</style>
