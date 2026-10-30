<script setup lang="ts">
// 9.2 數量對帳（2.x）：對帳總表｜帳實比對｜計算規則｜偵測證據。設計見 docs/ui-v2/9.2數量對帳設計_2026-10-27.md。
// 合併舊 7-8 對帳橋、7-7 台數口徑、7-2 資料品質的可信度分布、1-5 的帳實比對。
// 數字全由後端算（count_bridge／machine_identity／trust／existence），這裡不另算台數；加總＝下鑽由 verify_numbers 釘住。
// 每個數字寫明單位：筆＝一筆登記；台＝同一台多筆登記只算一台（LPAR 算部、管理口算個）。
// 只讀與匯出；人工指定角色（寫入、限管理員，後端另外擋）2.007.58 起在對帳總表的下鑽明細表：勾選 → 選角色 → 填原因 → 套用（沿用 /api/count-bridge/override，每筆都留稽核紀錄）。
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())

const br = ref<any>(null); const stats = ref<any>(null); const mstate = ref<any>(null)
const mk = ref<any>(null); const trust = ref<any>(null); const comp = ref<any>(null); const exs = ref<any>(null)
const err = reactive<Record<string, string>>({})
async function load(k: string) {
  // 伺服器端產生頁面時，連續多個 await 會讓後面的請求失去 Nuxt 執行環境：同一頁籤要的資料先全部發出、一次 await
  const jobs: Promise<void>[] = []
  if (!br.value) jobs.push(apiFetch<any>('/api/count-bridge').then(v => { br.value = v }))
  if (k === 'recon' && !stats.value) {
    jobs.push(apiFetch<any>('/api/dashboard/stats', { query: { environment: '全部' } }).then(v => { stats.value = v }))
    // 四態抓不到時那幾列顯示「—」（不顯示 0，0 會被當成真的沒有）
    jobs.push(apiFetch<any>('/api/manage-state', { query: { summary: 1 } }).then(v => { mstate.value = v }, () => { mstate.value = null }))
  }
  if (k === 'rule' && !mk.value) jobs.push(apiFetch<any>('/api/machine-key').then(v => { mk.value = v }))
  if (k === 'evidence') {
    if (!trust.value) jobs.push(apiFetch<any>('/api/trust/distribution').then(v => { trust.value = v }))
    if (!comp.value) jobs.push(apiFetch<any>('/api/dashboard/composition').then(v => { comp.value = v }))
    if (!exs.value) jobs.push(apiFetch<any>('/api/existence/summary').then(v => { exs.value = v.summary }))
  }
  try { await Promise.all(jobs); err[k] = '' } catch (e: any) { err[k] = `載入失敗：${errMsg(e)}` }
}

// 數字健檢（2.008.8，舊版 /number-check）：直接嵌入既有畫面，只讀
const NumberCheck = defineAsyncComponent(() => import('~/pages/number-check.vue'))
// 存在可信度（2.010.x，舊版 /existence）與納管狀態明細（舊版 /manage-state-detail）：直接嵌入既有畫面，只讀；
// 裡面的下鑽連結由 middleware/v2-legacy.global.ts 換成本頁對應頁籤，不會跳出新版。
const Existence = defineAsyncComponent(() => import('~/pages/existence.vue'))
const StateDetail = defineAsyncComponent(() => import('~/pages/manage-state-detail.vue'))
const TABS = [{ k: 'bridge', label: '對帳總表' }, { k: 'recon', label: '帳實比對' }, { k: 'rule', label: '計算規則' }, { k: 'evidence', label: '偵測證據' }, { k: 'existence', label: '存在可信度' }, { k: 'state', label: '納管狀態明細' }, { k: 'numcheck', label: '數字健檢' }]
const tab = computed({ get: () => (TABS.some(t => t.k === q('tab')) ? q('tab') : 'bridge'), set: (v: string) => router.replace({ query: { tab: v } }) })
await load(tab.value)
watch(tab, t => load(t))
// 「納管狀態明細」頁籤直接點開時沒帶狀態：預設看「已納管」，不要顯示「沒有指定狀態」
watch(() => [tab.value, q('state')], ([t, st]) => { if (t === 'state' && !st) router.replace({ query: { tab: 'state', state: '已納管' } }) }, { immediate: true })
const u = (t: string, extra: Record<string, string | undefined> = {}) =>
  ({ path: '/v2/reconcile', query: Object.fromEntries(Object.entries({ tab: t, ...extra }).filter(([, v]) => v !== undefined && v !== '')) })

// ===== 對帳總表 =====
const TRIAL_TO: Record<string, string> = { physical: 'physical', hosts: '', mgmt: 'mgmt' }
const roleRows = computed(() => (br.value?.roles ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, units_text: r.unit === '—' ? '—' : `${n(r.units)} ${r.unit}` })))
const roleLabel = (k: string) => br.value?.roles_all?.find((r: any) => r.role === k)?.label ?? k
const sum = computed(() => roleRows.value.reduce((a: any, r: any) => ({ cia: a.cia + r.cia_rows, off: a.off + r.offbook_rows, all: a.all + r.rows }), { cia: 0, off: 0, all: 0 }))
const trial = computed(() => br.value?.trial?.find((t: any) => t.key === q('trial')) ?? null)
const partRows = computed(() => (trial.value?.parts ?? []).map((p: any, i: number) => ({ ...p, __no: i + 1 })))
const MEASURE: Record<string, string> = { rack: '有機櫃', unparsed: '機櫃欄認不出', none: '缺機櫃資料' }
const drillOn = computed(() => !!q('role') || !!q('find'))
const drillTitle = computed(() => {
  if (q('find')) return `查「${q('find')}」`
  const bits = [roleLabel(q('role'))]
  if (q('side')) bits.push(q('side'))
  if (q('measure')) bits.push(MEASURE[q('measure')] ?? q('measure'))
  if (q('counted')) bits.push('存在可信度達門檻')
  return bits.join('・')
})
const items = ref<any[]>([]); const frames = ref(0); const itemsKey = ref(''); const itemsBusy = ref(false)
async function loadItems() {
  const findMode = !q('role')
  const key = findMode ? 'ALL' : JSON.stringify([q('role'), q('side'), q('measure'), q('counted')])
  if (!drillOn.value || key === itemsKey.value) return
  itemsBusy.value = true
  try {
    const query: Record<string, any> = {}
    if (!findMode) {
      query.role = q('role')
      if (q('side')) query.side = q('side')
      if (q('measure')) query.measure = q('measure')
      if (q('counted')) query.counted = true
    }
    const r = await apiFetch<any>('/api/count-bridge/items', { query })
    items.value = r.items; frames.value = r.frames; itemsKey.value = key; err.items = ''
  } catch (e: any) { err.items = `明細載入失敗：${errMsg(e)}` } finally { itemsBusy.value = false }
}
onMounted(loadItems)
watch(() => route.query, loadItems)
const findText = ref(q('find'))
watch(() => route.query.find, v => { findText.value = typeof v === 'string' ? v : '' })
function doFind() { const t = findText.value.trim(); router.replace(u('bridge', t ? { find: t } : {})) }
const itemRows = computed(() => {
  const s = q('role') ? '' : q('find').trim().toLowerCase()
  const list = s ? items.value.filter((x: any) => [x.asset_serial, x.hostname, x.ip, x.model, x.rack_raw].some(v => (v ?? '').toLowerCase().includes(s))) : items.value
  return list.map((x: any) => ({
    ...x, __no: x.no, name: x.hostname || x.asset_serial,
    where: [x.room, x.rack_raw].filter(Boolean).join(' ') + (x.rack_missing ? '（缺機櫃資料）' : x.rack_unparsed ? '（機櫃欄認不出）' : ''),
    why: x.reason + (x.host_serial ? `・本體 ${x.host_serial}` : ''),
    role_text: x.role_label + (x.override ? '（人工指定）' : '') + (x.vios ? '・VIOS' : ''),
  }))
})

// ===== 人工指定角色（2.007.58，原 7-8 對帳橋的「人工指定」）=====
// 會改全站台數的算法（邊角案例用，重匯 CIA 不會蓋掉）。後端：限管理員、登入停用時要管理密碼、每筆寫稽核。
// 一次最多 20 筆：後端每筆都要重算整個機隊的自動判斷，筆數多會等很久。
const PICK_MAX = 20
const picked = ref<Record<string, boolean>>({})
const pickedRows = computed(() => itemRows.value.filter((r: any) => picked.value[r.asset_serial]))
const pickRole = ref('')       // ''＝清除人工指定、回到自動判斷
const pickNote = ref('')
const pickBusy = ref(false)
watch(() => route.query, () => { picked.value = {} })   // 換了下鑽條件，上一份勾選就不算數
async function applyRole() {
  const rows = pickedRows.value
  if (!rows.length) { showToast('請先勾選要指定的登記', 'warn'); return }
  if (rows.length > PICK_MAX) { showToast(`一次最多 ${PICK_MAX} 筆（現在勾了 ${rows.length} 筆），請分批`, 'warn'); return }
  const target = pickRole.value === '' ? null : pickRole.value
  if (target && !pickNote.value.trim()) { showToast('指定角色要填原因（稽核會留）', 'warn'); return }
  const what = target ? `指定為「${roleLabel(target)}」` : '清除人工指定（回到自動判斷）'
  if (!window.confirm(`把勾選的 ${rows.length} 筆${what}？這會改變對帳總表的台數。`)) return
  pickBusy.value = true
  let ok = 0
  const bad: string[] = []
  for (const r of rows) {
    try {
      await guardedFetch('/api/count-bridge/override', { method: 'PUT', body: { asset_serial: r.asset_serial, role: target, note: target ? pickNote.value.trim() : null } })
      ok++
    } catch (e: any) {
      bad.push(`${r.asset_serial}：${errMsg(e)}`)
      // 權限／密碼類錯誤每一筆都會一樣：停下來，不要連問 20 次管理密碼
      if ([401, 403, 428, 429].includes(e?.statusCode ?? e?.response?.status)) break
    }
  }
  showToast(bad.length ? `完成 ${ok} 筆、失敗 ${bad.length} 筆（${bad[0]}）` : `已${what}：${ok} 筆`, bad.length ? 'warn' : 'success')
  picked.value = {}
  pickNote.value = ''
  try {
    br.value = await apiFetch<any>('/api/count-bridge')
    itemsKey.value = ''
    await loadItems()
  } catch (e: any) { err.items = `重新載入失敗：${errMsg(e)}（畫面數字可能是舊的，請重新整理）` }
  pickBusy.value = false
}

// ===== 帳實比對 =====
const reconCards = computed(() => {
  const s = stats.value
  if (!s) return []
  return [
    { key: 'overlap', label: '一致', n: s.covered_overlap_machines ?? s.total_overlap_machines, tone: 'good' },
    { key: 'missing', label: '搜不到（有涵蓋的網段）', n: s.covered_missing_machines, tone: 'bad' },
    { key: 'uncovered', label: '未涵蓋', n: s.uncovered_ica_machines, tone: 'mute' },
  ]
})
const basisText = computed(() => {
  const a = (stats.value?.covered_scan_from ?? '').slice(5, 10); const b = (stats.value?.covered_scan_to ?? '').slice(5, 10)
  if (!a && !b) return '還沒有可用的掃描'
  return a === b || !a || !b ? `依 ${a || b} 掃描` : `依 ${a}～${b} 掃描`
})
const skippedRuns = computed(() => (stats.value?.covered_empty_runs ?? []).filter((x: any) => x.kind !== 'suspect_down'))
const reconItems = ref<any[]>([]); const reconKey = ref('')
async function loadRecon() {
  const b = q('b')
  if (tab.value !== 'recon' || !b || b === reconKey.value) return
  try { reconItems.value = (await apiFetch<any>('/api/count-bridge/recon', { query: { bucket: b } })).items; reconKey.value = b; err.recon2 = '' } catch (e: any) { err.recon2 = `明細載入失敗：${errMsg(e)}` }
}
onMounted(loadRecon)
watch(() => route.query, loadRecon)
const reconRows = computed(() => reconItems.value.map((x: any) => ({ ...x, __no: x.no, serials_text: (x.serials ?? []).join('、') })))
const RETIRED_ALIVE = '退役但仍' + '\u5728\u7dda'
const sc = (k: string) => (mstate.value ? (mstate.value.counts?.[k] ?? 0) : null)
const stateRows = computed(() => [
  { label: '這次掃到、清冊查無', unit: '筆', n: stats.value?.scan_only_count ?? null, to: '/v2/onboard?tab=scan&registered=no', page: '6.1 掃描結果' },
  { label: '已納管', unit: '台', n: sc('已納管'), to: '/v2/reconcile?tab=state&state=已納管', page: '本頁・納管狀態明細' },
  { label: '已納管但失聯', unit: '台', n: sc('已納管但失聯'), to: '/v2/reconcile?tab=state&state=已納管但失聯', page: '本頁・納管狀態明細' },
  { label: '已收集', unit: '台', n: sc('已收集'), to: '/v2/reconcile?tab=state&state=已收集', page: '本頁・納管狀態明細' },
  { label: '登記矛盾', unit: '台', n: sc('登記矛盾'), to: '/v2/onboard?tab=funnel&stage=conflict', page: '6.1 納管漏斗' },
  // 後端四態的鍵名照舊（manage_state），畫面用正式用語
  { label: '退役但仍偵測到', unit: '台', n: sc(RETIRED_ALIVE), to: '/v2/onboard?tab=funnel&stage=retired_alive', page: '6.1 納管漏斗' },
  { label: '沒有 IP', unit: '台', n: sc('沒有 IP'), to: '/v2/onboard?tab=funnel&stage=no_ip', page: '6.1 納管漏斗' },
  { label: '已退役', unit: '台', n: sc('已退役'), to: '/v2/reconcile?tab=state&state=已退役', page: '本頁・納管狀態明細' },
].map((r, i) => ({ ...r, __no: i + 1, n_text: r.n === null ? '—' : `${n(r.n)} ${r.unit}` })))

// ===== 計算規則 =====
const mkKind = computed(() => (['merged', 'pending'].includes(q('mk')) ? q('mk') : 'stops'))
const stopRows = computed(() => Object.entries(mk.value?.stops ?? {}).map(([code, s]: [string, any], i) => ({ ...s, code, __no: i + 1, result: s.hit ? '觸發' : '未觸發' })))
const memText = (ms: any[]) => ms.map((m: any) => [m.hostname || m.asset_serial, m.ip, m.environment].filter(Boolean).join(' ')).join('\n')
const mergedRows = computed(() => (mk.value?.merged ?? []).map((g: any, i: number) => ({ ...g, __no: i + 1, envs_text: (g.envs ?? []).join('、'), n_rows: g.members.length, mem: memText(g.members) })))
const pendingRows = computed(() => (mk.value?.pending ?? []).map((g: any, i: number) => ({ ...g, __no: i + 1, n_rows: g.members.length, mem: memText(g.members) })))
const hits = computed(() => stopRows.value.filter((s: any) => s.hit))

// ===== 偵測證據 =====
const SRC: Record<string, string> = { dynassets: 'dynassets', rvtools: 'RVTools', cia: 'CIA' }
const comboLabel = (c: string) => (c.startsWith('（') ? c : c.split('+').map(s => SRC[s] ?? s).join('＋'))
const scoreRows = computed(() => (trust.value?.by_score ?? []).map((b: any, i: number) => ({ ...b, __no: i + 1, reason_text: b.reasons.map((x: any) => `${x.text} ×${x.count}`).join('\n') })))
const comboRows = computed(() => (trust.value?.by_combo ?? []).map((c: any, i: number) => ({ ...c, __no: i + 1, label: comboLabel(c.combo) })))
// 計入台數的兩種拆法（2.007.14 使用者：「2663 如果是總數，應該等於什麼加什麼」→ 只要這兩條）：
// 依 CIA：有登記＋待補登；依證據：已納管＋在 DY／vCenter 清單＋掃描看得到。數字＝existence.compute 同一份，點了在本頁列清單。
const EQ_LEVELS = [{ k: 'onboarded', label: '已納管' }, { k: 'inventory', label: '在 DY／vCenter 清單' }, { k: 'scan', label: '網段掃描看得到' }]
const eqDrill = computed(() => (route.query.eb !== undefined ? { bucket: q('eb'), level: q('el') } : null))
const eqItems = ref<any[]>([]); const eqKey = ref(''); const eqTitle = ref('')
async function loadEq() {
  const d = eqDrill.value
  if (tab.value !== 'evidence' || !d) return
  const key = JSON.stringify(d)
  if (key === eqKey.value) return
  try {
    const r = await apiFetch<any>('/api/existence/machines', { query: { bucket: d.bucket, ...(d.level ? { level: d.level } : {}) } })
    eqItems.value = r.items.map((x: any) => ({ ...x, name: x.hostname || x.asset_serial || x.ip, seen: (x.last_seen_any || '').slice(0, 16) }))
    eqTitle.value = d.level ? (EQ_LEVELS.find(l => l.k === d.level)?.label ?? d.level) : r.bucket_label
    eqKey.value = key; err.eq = ''
  } catch (e: any) { err.eq = `清單載入失敗：${errMsg(e)}` }
}
onMounted(loadEq)
watch(() => route.query, loadEq)
const osRows = computed(() => {
  const c = comp.value
  if (!c) return []
  const m = (c.by_os_class ?? {}) as Record<string, number>
  const order: string[] = c.os_class_order ?? Object.keys(m)
  const plat = [...order.filter(k => k in m), ...Object.keys(m).filter(k => !order.includes(k))]
  return [
    { label: 'OS 欄有值', n: c.os_from_facts, to: '/v2/hosts?os_source=facts', page: '2.1 主機查詢' },
    { label: 'OS 為掃描推測', n: c.os_guessed, to: '/v2/hosts?os_source=guessed', page: '2.1 主機查詢' },
    { label: 'OS 未知', n: c.data_quality?.os_unknown, to: '/v2/hosts?os_source=unknown', page: '2.1 主機查詢' },
    ...plat.map(k => ({ label: `平台：${k}`, n: m[k], to: `/v2/hosts?os_class=${encodeURIComponent(k)}`, page: '2.1 主機查詢' })),
  ].map((r, i) => ({ ...r, __no: i + 1, n_text: `${n(r.n)} 筆` }))
})
const tq = computed(() => (route.query.sc !== undefined ? { score: q('sc') } : route.query.cb !== undefined ? { combo: q('cb') } : null))
const tHosts = ref<any[]>([]); const tKey = ref('')
const KIND: Record<string, string> = { vm: 'VM', physical: '實體機', esxi: 'ESXi 主機', unknown: '不明（照 VM 算）' }
const yn = (v: boolean | null | undefined) => (v === null ? '不適用' : v ? '有' : '沒有')
async function loadTrust() {
  const key = JSON.stringify(tq.value)
  if (tab.value !== 'evidence' || !tq.value || key === tKey.value) return
  try {
    const r = await apiFetch<any>('/api/trust/hosts', { query: tq.value })
    tHosts.value = r.items.map((h: any) => ({
      ...h, name: h.hostname || h.asset_serial, kind_text: KIND[h.kind] ?? h.kind,
      dyn: yn(h.sources.dynassets), rv: yn(h.sources.rvtools), cia: yn(h.sources.cia),
      evid: h.evidence === 'managed' ? '已納管 +30' : h.evidence === 'collected' ? '已收集（設備）+30'
        : h.evidence === 'alive' ? (h.alive_basis === 'dynassets' ? `網路通 +20（dynassets ${(h.dynassets_imported_at || '').slice(0, 10)}）` : '網路通 +20（掃描）') : '—',
    }))
    tKey.value = key; err.trust = ''
  } catch (e: any) { err.trust = `明細載入失敗：${errMsg(e)}` }
}
onMounted(loadTrust)
watch(() => route.query, loadTrust)
const tTitle = computed(() => (route.query.sc !== undefined ? `${q('sc')} 分` : comboLabel(q('cb'))))
const machine = (s: string) => `/v2/machine/${encodeURIComponent(s)}`
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">9.2 數量對帳</h1></template>
    <template #conclusion>
      <template v-if="br">計入台數 <b class="mono">{{ n(br.totals.counted_machines) }}</b> 台
        <span class="muted">・現役登記 {{ n(br.totals.rows) }} 筆（CIA {{ n(br.totals.cia_rows) }} 筆＋帳外 {{ n(br.totals.offbook_rows) }} 筆）</span>
        <InfoNote>計入台數＝存在可信度 ≥ {{ br.count_threshold }} 的機器（逐台，machine_key），跟首頁、存在可信度同一份。
          筆＝一筆登記；台＝同一台多筆登記只算一台。帳外＝不在 CIA 的登記（掃描、收集、RVTools 建的）。{{ br.as_of }}</InfoNote>
      </template>
    </template>
    <div v-if="br" class="cards">
      <NuxtLink v-for="t in br.trial" :key="t.key" class="kc" :class="{ on: tab === 'bridge' && q('trial') === t.key }" :to="u('bridge', { trial: t.key })">
        <span class="kl">{{ t.label }}</span>
        <b class="kv mono">{{ n(t.value) }}<small> {{ t.unit }}</small></b>
        <span class="ks">其中真的存在 {{ n(t.counted) }}<template v-if="t.key === 'physical'">・有機櫃 {{ n(t.rack) }}・缺機櫃資料 {{ n(t.none) }}</template></span>
      </NuxtLink>
    </div>
    <template #tab>
      <p v-if="err[tab]" class="err">{{ err[tab] }}</p>

      <!-- 對帳總表 -->
      <ClientOnly v-if="tab === 'numcheck'"><V2Embed><NumberCheck /></V2Embed></ClientOnly>
      <ClientOnly v-else-if="tab === 'existence'"><V2Embed><Existence /></V2Embed></ClientOnly>
      <ClientOnly v-else-if="tab === 'state'"><V2Embed><StateDetail /></V2Embed></ClientOnly>

      <template v-else-if="tab === 'bridge' && br">
        <form class="bar" @submit.prevent="doFind">
          <input v-model="findText" class="inp" placeholder="查一台怎麼算：主機名／IP／資產編號／型號／機櫃">
          <button type="submit" class="btn">查</button>
          <InfoNote>搜全部現役登記，列出每一筆被歸到哪個角色、為什麼。判法依序：人工指定 → 重複登記（同一台）→ 有機櫃 U 位的照 5.1 的箱子歸類 →
            沒有 U 位的：管理口 → 帳外低可信度 → ESXi／VM → 機櫃欄認不出（照類型算實體設備）→ VM 訊號（vCenter 清單、VMware MAC、客體 OS 寫法）→
            端點 → 依類型算實體設備（缺機櫃資料）→ 無法判斷。</InfoNote>
        </form>
        <p v-if="br.invalid_overrides?.length" class="warn">失效的人工指定 {{ br.invalid_overrides.length }} 筆（指定的角色已不存在，先照自動判斷算）
          <InfoNote><template v-for="x in br.invalid_overrides" :key="x.asset_serial">{{ x.asset_serial }}：{{ x.role }}（{{ x.updated_by ?? '—' }} {{ x.updated_at }}）<br></template></InfoNote></p>

        <template v-if="drillOn">
          <div class="bar"><NuxtLink :to="u('bridge')">‹ 返回</NuxtLink><b>{{ drillTitle }}</b>
            <span class="muted">{{ n(itemRows.length) }} 筆<template v-if="!q('find') && frames !== items.length">・{{ n(frames) }} 個箱子</template></span></div>
          <p v-if="err.items" class="err">{{ err.items }}</p>
          <p v-else-if="itemsBusy" class="muted">載入中…</p>
          <template v-else>
          <form v-if="br.can_edit" class="rolebar" @submit.prevent="applyRole">
            <span>勾選 <b>{{ pickedRows.length }}</b> 筆，</span>
            <select v-model="pickRole" aria-label="指定角色">
              <option value="">清除人工指定（回到自動判斷）</option>
              <option v-for="o in br.roles_all" :key="o.role" :value="o.role">指定為：{{ o.label }}</option>
            </select>
            <input v-model="pickNote" class="note" placeholder="原因（指定時必填，稽核會留）" aria-label="原因" :disabled="pickRole === ''">
            <button type="submit" class="btn" :disabled="pickBusy || !pickedRows.length">{{ pickBusy ? '套用中…' : '套用' }}</button>
            <InfoNote>邊角案例用：程式判錯的少數幾筆才手動指定，重匯 CIA 不會蓋掉，每次都記稽核。指定會改變對帳總表的台數；一次最多 20 筆。只有管理員能改，其他人看不到這一列。</InfoNote>
          </form>
          <V2Table v-model:selected="picked" :selectable="br.can_edit" :rows="itemRows" prefix="D" :max="200" row-key="asset_serial" :empty="q('find') ? '找不到符合的登記' : '沒有資料'"
                   :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => machine(r.asset_serial) }, { k: 'asset_serial', label: '資產編號', mono: true }, { k: 'side', label: '來源' }, { k: 'ip', label: 'IP', mono: true }, { k: 'os_class', label: 'OS 大類' }, { k: 'model', label: '型號' }, { k: 'where', label: '機房／機櫃' }, { k: 'exist_score', label: '存在可信度', mono: true }, { k: 'role_text', label: '角色' }, { k: 'why', label: '為什麼' }]" />
          </template>
        </template>

        <template v-else-if="trial">
          <div class="bar"><NuxtLink :to="u('bridge')">‹ 返回</NuxtLink><b>{{ trial.label }} {{ n(trial.value) }} {{ trial.unit }} 怎麼組成</b>
            <NuxtLink v-if="TRIAL_TO[trial.key]" class="lnk" :to="`/v2/hosts?tab=${TRIAL_TO[trial.key]}`">清單（2.1 主機查詢）</NuxtLink>
            <InfoNote v-if="trial.key === 'physical'">依類型算：伺服器、儲存、網路、其他設備、ESXi。「有機櫃」跟 5.1 機房平面圖同一份（同一個箱子的 LPAR／元件算一台、管理口併進主機不算）。機櫃欄填了但認不出、或沒填的也照算，分別標示。PC 級機器要在機櫃上才算。</InfoNote>
            <InfoNote v-else-if="trial.key === 'hosts'">OS 實例：實體伺服器＋LPAR＋VM。ESXi 算虛擬化平台、不算；有機櫃的 PC 不算；Exadata／ODA 的 DB 節點算，儲存 cell 不算；VIOS 算在 LPAR（{{ n(br.lpar_vios) }} 筆）。</InfoNote>
            <InfoNote v-else>iLO／ILOM／iDRAC…。掛在本體的不算「台」；找不到本體、有自己 U 位的，也算在實體設備（有機櫃）裡。</InfoNote>
          </div>
          <V2Table :rows="partRows" prefix="P" row-key="label"
                   :cols="[{ k: 'label', label: '組成' }, { k: 'n', label: `${trial.unit}數`, mono: true, link: (r: any) => u('bridge', { role: r.role, measure: ['rack', 'unparsed', 'none'].includes(r.measure) ? r.measure : undefined }) },
                           { k: 'counted', label: '其中真的存在', mono: true, link: (r: any) => u('bridge', { role: r.role, counted: '1' }) },
                           ...(trial.key === 'physical' ? [{ k: 'rack', label: '有機櫃', mono: true, link: (r: any) => (r.rack ? u('bridge', { role: r.role, measure: 'rack' }) : null) }, { k: 'unparsed', label: '機櫃欄認不出', mono: true, link: (r: any) => (r.unparsed ? u('bridge', { role: r.role, measure: 'unparsed' }) : null) }, { k: 'none', label: '缺機櫃資料', mono: true, link: (r: any) => (r.none ? u('bridge', { role: r.role, measure: 'none' }) : null) }] : [])]" />
        </template>

        <template v-else>
          <h3 class="h3">每筆登記歸一種角色
            <InfoNote>每一筆現役登記（不含退役）剛好落在一列。台數：LPAR 以筆算部、同一個箱子的元件以箱子算、重複登記不算。存在＝存在可信度 ≥ {{ br.count_threshold }} 的筆數。點數字看是哪幾筆。</InfoNote></h3>
          <V2Table :rows="roleRows" prefix="R" row-key="role"
                   :cols="[{ k: 'label', label: '角色' },
                           { k: 'cia_rows', label: 'CIA 登記（筆）', mono: true, link: (r: any) => (r.cia_rows ? u('bridge', { role: r.role, side: 'CIA' }) : null) },
                           { k: 'offbook_rows', label: '帳外登記（筆）', mono: true, link: (r: any) => (r.offbook_rows ? u('bridge', { role: r.role, side: '帳外' }) : null) },
                           { k: 'rows', label: '合計（筆）', mono: true, link: (r: any) => (r.rows ? u('bridge', { role: r.role }) : null) },
                           { k: 'units', label: '台數', mono: true, text: (r: any) => r.units_text },
                           { k: 'counted_rows', label: '存在（筆）', mono: true, link: (r: any) => (r.counted_rows ? u('bridge', { role: r.role, counted: '1' }) : null) },
                           { k: 'unparsed_rows', label: '機櫃欄認不出（筆）', mono: true, link: (r: any) => (r.unparsed_rows ? u('bridge', { role: r.role, measure: 'unparsed' }) : null) },
                           { k: 'no_rack_rows', label: '缺機櫃資料（筆）', mono: true, link: (r: any) => (r.no_rack_rows ? u('bridge', { role: r.role, measure: 'none' }) : null) },
                           { k: 'override_rows', label: '人工指定（筆）', mono: true }]" />
          <p class="sum">合計：CIA 登記 <b class="mono">{{ n(sum.cia) }}</b> 筆＋帳外登記 <b class="mono">{{ n(sum.off) }}</b> 筆＝<b class="mono">{{ n(sum.all) }}</b> 筆</p>
        </template>
      </template>

      <!-- 帳實比對 -->
      <template v-else-if="tab === 'recon' && stats">
        <div class="bar">帳實相符 <b class="mono big">{{ stats.match_pct_covered === null || stats.match_pct_covered === undefined ? '—' : `${stats.match_pct_covered}%` }}</b>
          <span class="pill">{{ basisText }}</span>
          <InfoNote>只算這一輪有涵蓋的 {{ n(stats.covered_segments) }} 個網段：每個網段取近 30 天內最近一次有結果的掃描（{{ stats.covered_scan_from ?? '—' }}～{{ stats.covered_scan_to ?? '—' }}），不是今天的即時數字。
            一致 ÷（有涵蓋的登記台數＋這一輪掃到、清冊查無 {{ n(stats.covered_scan_only) }}）。沒掃到的網段不算「對不上」，另列「未涵蓋」。
            一次掃很多段卻全部 0 台的跳過不算；只掃 1～2 段卻 0 台、而前後 24 小時掃描器有掃到東西的，當成那段可能整段斷線、照算。台數都是 CIA 現役登記（排退役、排帳外），逐台。</InfoNote>
        </div>
        <p v-if="stats.covered_suspect_down?.length" class="bad">可能整段斷線：{{ stats.covered_suspect_down.map((x: any) => x.cidr).join('、') }}
          <InfoNote>最近一次補掃一台都沒回，可能整段斷線或防火牆擋了；照算涵蓋，這幾段的機器算在「搜不到」。</InfoNote></p>
        <p v-if="skippedRuns.length" class="warn">{{ skippedRuns.length }} 次掃描回 0 台，沒算進對帳（最近 {{ skippedRuns[0].scan_time }}，{{ skippedRuns[0].segments }} 段）
          <InfoNote>通常是掃描器或網路出問題，不是機器全部不見；對帳先用前一次有結果的掃描。原因到 ⚙ 系統設定的掃描排程查。</InfoNote></p>
        <div class="rc">
          <NuxtLink v-for="c in reconCards" :key="c.key" class="kc" :class="[c.tone, { on: q('b') === c.key }]" :to="u('recon', { b: c.key })">
            <span class="kl">{{ c.label }}</span><b class="kv mono">{{ n(c.n) }}<small> 台</small></b>
          </NuxtLink>
        </div>
        <template v-if="q('b')">
          <div class="bar"><NuxtLink :to="u('recon')">‹ 返回</NuxtLink><b>{{ reconCards.find(c => c.key === q('b'))?.label }}</b><span class="muted">{{ n(reconRows.length) }} 台</span></div>
          <p v-if="err.recon2" class="err">{{ err.recon2 }}</p>
          <V2Table v-else :rows="reconRows" prefix="M" :max="200" unit="台"
                   :cols="[{ k: 'hostname', label: '主機名稱', link: (r: any) => (r.asset_serial ? machine(r.asset_serial) : null) }, { k: 'ip', label: 'IP', mono: true }, { k: 'serials_text', label: '登記序號', mono: true }]" />
        </template>
        <template v-else>
          <h3 class="h3">掃描與納管狀態</h3>
          <V2Table :rows="stateRows" prefix="S" row-key="label"
                   :cols="[{ k: 'label', label: '狀態' }, { k: 'n', label: '數量', mono: true, text: (r: any) => r.n_text, link: (r: any) => r.to }, { k: 'page', label: '清單在' }]" />
          <InfoNote>點數字進明細（數字同一份）。「這次掃到、清冊查無」是掃描紀錄的筆數，其餘是逐台。</InfoNote>
        </template>
      </template>

      <!-- 計算規則 -->
      <template v-else-if="tab === 'rule' && mk">
        <div class="bar">同一台的判法：<b>{{ mk.mode === 'hostname' ? '主機名' : '主機名＋IP（舊規則）' }}</b>
          <span class="muted">舊規則 {{ n(mk.old) }} 台 → 主機名 {{ n(mk.new) }} 台（{{ mk.pct }}%）<template v-if="mk.decision">・判定 {{ mk.decision.decided_at }}</template></span>
          <InfoNote>主機名統一格式（小寫、去網域）後同名＝同一台，多個 IP、業務系統掛在底下。同名但 MAC／硬體序號／環境（正式／測試／備援）不只一種 → 不併、列待確認；
            通用名（localhost、template…）與沒有主機名的 → 照舊規則（主機名＋IP）。退役不計入台數。
            停損（任一成立就留在舊規則）：a 不同環境被併成一台；b 總台數變動超過 10%；c 待確認超過總台數 5%。部署時用正式資料判一次，結果記在這裡。</InfoNote>
        </div>
        <p v-if="hits.length" class="bad">停損：維持舊規則（觸發 {{ hits.map((h: any) => h.code).join('、') }}）</p>
        <div class="bar">
          <NuxtLink class="chip" :class="{ on: mkKind === 'stops' }" :to="u('rule')">停損條件 {{ stopRows.length }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: mkKind === 'merged' }" :to="u('rule', { mk: 'merged' })">合併成一台 {{ n(mergedRows.length) }} 組</NuxtLink>
          <NuxtLink class="chip" :class="{ on: mkKind === 'pending' }" :to="u('rule', { mk: 'pending' })">待確認 {{ n(pendingRows.length) }} 組</NuxtLink>
        </div>
        <V2Table v-if="mkKind === 'stops'" :rows="stopRows" prefix="S" row-key="code" unit="條"
                 :cols="[{ k: 'code', label: '代號' }, { k: 'label', label: '條件' }, { k: 'value', label: '實際值', mono: true }, { k: 'limit', label: '門檻', mono: true }, { k: 'result', label: '結果' }]" />
        <V2Table v-else-if="mkKind === 'merged'" :rows="mergedRows" prefix="G" row-key="name" unit="組" :max="200"
                 :cols="[{ k: 'name', label: '主機名' }, { k: 'n_rows', label: '登記（筆）', mono: true }, { k: 'old_n', label: '舊規則算成（台）', mono: true }, { k: 'envs_text', label: '環境' }, { k: 'mem', label: '成員（主機名 IP 環境）' }]" />
        <V2Table v-else :rows="pendingRows" prefix="P" row-key="name" unit="組" :max="200"
                 :cols="[{ k: 'name', label: '主機名' }, { k: 'n_rows', label: '登記（筆）', mono: true }, { k: 'why', label: '為什麼不併' }, { k: 'mem', label: '成員（主機名 IP 環境）' }]" />
      </template>

      <!-- 偵測證據 -->
      <template v-else-if="tab === 'evidence' && trust && comp">
        <template v-if="eqDrill">
          <div class="bar"><NuxtLink :to="u('evidence')">‹ 返回</NuxtLink><b>{{ eqTitle }}</b><span class="muted">{{ n(eqItems.length) }} 台</span></div>
          <p v-if="err.eq" class="err">{{ err.eq }}</p>
          <V2Table v-else :rows="eqItems" prefix="E" :max="200" row-key="key" unit="台"
                   :cols="[{ k: 'name', label: '主機', link: (r: any) => machine(r.asset_serial || r.key) }, { k: 'ip', label: 'IP', mono: true }, { k: 'os', label: '作業系統' }, { k: 'level_label', label: '證據' }, { k: 'score', label: '存在可信度', mono: true }, { k: 'seen', label: '最後看到', mono: true }]" />
        </template>
        <template v-else-if="tq">
          <div class="bar"><NuxtLink :to="u('evidence')">‹ 返回</NuxtLink><b>可信度：{{ tTitle }}</b><span class="muted">{{ n(tHosts.length) }} 筆</span></div>
          <p v-if="err.trust" class="err">{{ err.trust }}</p>
          <V2Table v-else :rows="tHosts" prefix="T" :max="200" row-key="asset_serial"
                   :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => machine(r.asset_serial) }, { k: 'asset_serial', label: '資產編號', mono: true }, { k: 'ip', label: 'IP', mono: true }, { k: 'score', label: '分數', mono: true }, { k: 'kind_text', label: '型態' }, { k: 'dyn', label: 'dynassets' }, { k: 'rv', label: 'RVTools' }, { k: 'cia', label: 'CIA' }, { k: 'evid', label: '機器證據' }]" />
        </template>
        <template v-else>
          <template v-if="exs">
            <h3 class="h3">計入台數怎麼組成（台）
              <InfoNote>計入台數＝存在可信度 ≥ {{ br?.count_threshold ?? 70 }} 的機器（逐台），跟頁首同一份。兩條是同一批機器的兩種拆法，各自加總都等於計入台數。點數字看清單。</InfoNote></h3>
            <!-- 第 1 行（使用者 2026-10-28：「DY 跟已退役、已納管、已納管但失聯的，放在第 1 行」）：參考數字，不是加總 -->
            <div class="eq ref">
              <NuxtLink :to="u('evidence', { eb: 'pending_confirm' })">只有 DY（待確認） {{ n(exs.pending_confirm) }}</NuxtLink>
              <NuxtLink :to="u('evidence', { eb: 'retired' })">已退役 {{ n(exs.retired) }}</NuxtLink>
              <NuxtLink :to="u('evidence', { eb: 'onboarded' })">已納管 {{ n(exs.onboarded) }}</NuxtLink>
              <NuxtLink :to="u('evidence', { eb: 'onboarded_stale' })">已納管但失聯 {{ n(exs.onboarded_stale) }}</NuxtLink>
            </div>
            <div class="eq"><span class="mono">E1</span>
              <NuxtLink :to="u('evidence', { eb: 'counted' })" class="tot">計入 {{ n(exs.counted) }}</NuxtLink>＝
              <NuxtLink :to="u('evidence', { eb: 'cia_registered' })">CIA 有登記 {{ n(exs.cia_registered) }}</NuxtLink>＋
              <NuxtLink :to="u('evidence', { eb: 'cia_pending' })">CIA 待補登 {{ n(exs.cia_pending) }}</NuxtLink>
              <span v-if="exs.cia_registered + exs.cia_pending !== exs.counted" class="bad">（加總對不上）</span></div>
            <div class="eq"><span class="mono">E2</span>
              <NuxtLink :to="u('evidence', { eb: 'counted' })" class="tot">計入 {{ n(exs.counted) }}</NuxtLink>＝
              <template v-for="(l, i) in EQ_LEVELS" :key="l.k"><template v-if="i">＋</template><NuxtLink :to="u('evidence', { eb: 'counted', el: l.k })">{{ l.label }} {{ n(exs.by_level?.[l.k]) }}</NuxtLink></template>
              <span v-if="EQ_LEVELS.reduce((a, l) => a + (exs.by_level?.[l.k] ?? 0), 0) !== exs.counted" class="bad">（加總對不上）</span></div>
          </template>
          <h3 class="h3">資料可信度分布（{{ n(trust.total) }} 筆登記<template v-if="trust.machines">／{{ n(trust.machines) }} 台</template>）
            <InfoNote>一筆登記一個分數，問的是「這筆登記有幾個來源佐證」，所以以筆計；同一台在 CIA 與帳外各有一筆會各自計分。{{ trust.rule }}。
              實體機不會出現在 RVTools，只算 dynassets＋CIA（換算成 70 分滿分）；型態不明的照 VM 算。</InfoNote></h3>
          <div class="two">
            <V2Table :rows="scoreRows" prefix="C" row-key="score"
                     :cols="[{ k: 'score', label: '分數', mono: true }, { k: 'count', label: '登記（筆）', mono: true, link: (r: any) => u('evidence', { sc: String(r.score) }) }, { k: 'reason_text', label: '分數怎麼來的' }]" />
            <V2Table :rows="comboRows" prefix="K" row-key="combo"
                     :cols="[{ k: 'label', label: '出現在哪幾個來源' }, { k: 'count', label: '登記（筆）', mono: true, link: (r: any) => u('evidence', { cb: r.combo }) }]" />
          </div>
          <h3 class="h3">作業系統怎麼判的（筆）
            <InfoNote>現役登記裡 OS 欄的來源與平台分類。「其他／未知」是資料缺口：OS 欄寫了認不出的字、或沒填。點筆數到 2.1 主機查詢看是哪幾筆登記（「OS 未知」的母體含退役登記）。</InfoNote></h3>
          <V2Table :rows="osRows" prefix="O" row-key="label" :cols="[{ k: 'label', label: '項目' }, { k: 'n', label: '登記（筆）', mono: true, text: (r: any) => r.n_text, link: (r: any) => r.to }, { k: 'page', label: '清單在' }]" />
        </template>
      </template>
      <p v-else-if="!err[tab]" class="muted">載入中…</p>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 16px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.cards, .rc { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.rc { margin: 8px 0 12px; }
@media (max-width: 900px) { .cards, .rc { grid-template-columns: 1fr; } }
.kc { display: flex; flex-direction: column; gap: 4px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; color: var(--ink); text-decoration: none; }
.rc .kc { background: var(--sub); border-radius: 12px; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kl { font-size: 13px; color: var(--ink-soft); }
.kv { font-size: 28px; color: var(--brand-dark); }
.kv small { font-size: 13px; color: var(--muted); }
.ks { font-size: 12px; color: var(--ink-aux); }
.kc.bad .kv { color: var(--bad); } .kc.good .kv { color: var(--good); } .kc.mute .kv { color: var(--ink-aux); }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.big { font-size: 22px; color: var(--brand-dark); }
.pill { font-size: 12px; color: var(--ink-aux); border: 1px solid var(--border-strong); border-radius: 999px; padding: 1px 9px; }
.inp { flex: 1; min-width: 240px; max-width: 460px; padding: 6px 10px; border: 1px solid var(--border-strong); border-radius: 8px; font: inherit; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.lnk { color: var(--link); }
.sum { font-size: 13px; color: var(--ink-soft); margin: 8px 0 0; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.bad { color: var(--bad); background: var(--bad-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.eq { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-size: 15px; margin: 6px 0; }
.eq a { color: var(--link); } .eq .tot { font-weight: 700; }
.eq.ref { gap: 18px; font-size: 14px; }
.two { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; align-items: start; }
@media (max-width: 1000px) { .two { grid-template-columns: 1fr; } }
.rolebar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; }
.rolebar select, .rolebar .note { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.rolebar .note { width: 280px; max-width: 100%; }
</style>
