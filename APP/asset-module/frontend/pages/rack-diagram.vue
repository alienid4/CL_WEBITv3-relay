<script setup lang="ts">
// 4-5 機櫃圖對帳（2026-10-23）：現場人工維護的 Excel 機櫃圖 ↔ 系統（CIA＋收集）。
// 匯入現場圖（原檔不出公司，在公司端匯入）→ 逐格對帳 → 一致率／CIA 待更正；也能反過來匯出「跟現場同格式」的 Excel。
// v1 只出報告：不寫 CIA、不改位置（要更正走 4-1 機櫃正面的「依盤點結果更正」）。
// 10-24：照片判讀清單也收（來源照片檔名／拍攝日期／把握度）；照片本身是敏感資料，不上傳、只記檔名。
// 每筆差異按「確認（照片對）」或「判讀錯了」（只增不改的紀錄），只有確認過的才進 CIA 待更正。
interface Imp { id: number; site: string; file_name: string; sha256: string; sheets: number; rows: number; imported_by: string | null; imported_at: string }
interface Review { action: string; action_label: string; note: string | null; reviewed_by: string | null; reviewed_at: string }
interface Row { row_id: number; source: string | null; shot_date: string | null; confidence: string | null; review: Review | null; sys_model?: string; no: number; sheet: string; floor: string | null; rack: string; u: string; u_start: number | null; text: string; ip: string | null; state: string | null; result: string; detail?: string; match_by: string | null; sys_name?: string; sys_serial?: string; sys_room?: string; sys_rack?: string; sys_u?: string; sys_state?: string }
interface Rate { sheet: string; rack: string; total: number; comparable: number; ok: number; rate: number | null; u_rate: number | null; counts: Record<string, number> }
interface Rates { placed: number; same_rack: number; u_both: number; u_ok: number; same_rack_rate: number | null; u_rate: number | null }
interface Missing { rack: string; room: string; count: number; names: string[] }
interface SysOnly { key: string; serial: string; name: string; rack: string; u: string; model: string | null; ip: string | null; note: string }
interface Rc { import: Imp; rows: Row[]; counts: Record<string, number>; groups: Record<string, number>; missing_racks: Missing[]; system_only: SysOnly[]; rate: Rate[]; rates: Rates }
interface Diff { previous: { id: number; imported_at: string; file_name: string } | null; added: any[]; removed: any[]; moved: { ip: string; from: string; to: string; text: string }[] }

const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const err = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
const SITES = ['內湖', '敦南', '板橋']

const imports = ref<Imp[]>([])
async function loadImports() { try { imports.value = await apiFetch<Imp[]>('/api/rack-diagram/imports') } catch (e: any) { showToast(`匯入紀錄載入失敗：${err(e)}`, 'error') } }
await loadImports()
const rooms = ref<string[]>([])
try { rooms.value = ((await apiFetch<{ rooms: { room: string; racks: any[] }[] }>('/api/racks/datacenter')).rooms).filter(r => r.racks.length && SITES.some(s => r.room.includes(s))).map(r => r.room) } catch (e: any) {
  showToast(`機房清單載入失敗：${err(e)}`, 'error')
}

type Tab = 'import' | 'result' | 'sysonly' | 'rate' | 'missing' | 'reviews' | 'diff' | 'export'
const tab = ref<Tab>(imports.value.length ? 'result' : 'import')

// ---------- 匯入
const site = ref('內湖')
const file = ref<File | null>(null)
// 檔案沒有來源／拍攝日期／把握度欄時用的預設（例：機櫃圖截圖 2026-10-23、中）
const dft = reactive({ source: '', shot_date: '', confidence: '' })
const uploading = ref(false)
async function upload() {
  if (!file.value) { showToast('先選檔案（.xlsx）', 'warn'); return }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    fd.append('site', site.value)
    if (dft.source) fd.append('default_source', dft.source)
    if (dft.shot_date) fd.append('default_shot_date', dft.shot_date)
    if (dft.confidence) fd.append('default_confidence', dft.confidence)
    const r = await apiFetch<{ import_id: number; rows: number; sheets: number; skipped: string | null }>('/api/rack-diagram/import', { method: 'POST', body: fd })
    showToast(`匯入完成：${r.sheets} 張分頁、${r.rows} 格設備`, 'success')
    if (r.skipped) showToast(r.skipped, 'warn')
    await loadImports()
    await pick(r.import_id)
  } catch (e: any) { showToast(`匯入失敗：${err(e)}`, 'error') } finally { uploading.value = false }
}
const impRows = computed(() => imports.value.map((x, i) => ({ ...x, no: imports.value.length - i })))
const { sortKey: ik, sortDir: id_, toggle: it, sorted: is_ } = useSort(impRows, 'no')

// ---------- 對帳
const cur = ref<number | null>(imports.value[0]?.id ?? null)
const rc = ref<Rc | null>(null)
const diff = ref<Diff | null>(null)
const loading = ref(false)
async function pick(iid: number) {
  cur.value = iid
  loading.value = true
  try {
    ;[rc.value, diff.value] = await Promise.all([apiFetch<Rc>(`/api/rack-diagram/imports/${iid}/reconcile`), apiFetch<Diff>(`/api/rack-diagram/imports/${iid}/diff`)])
    resultPick.value = null
    if (tab.value === 'import') tab.value = 'result'
  } catch (e: any) { showToast(`對帳失敗：${err(e)}`, 'error') } finally { loading.value = false }
}
if (cur.value) await pick(cur.value)
const resultPick = ref<{ label: string; filter: (r: Row) => boolean } | null>(null)
const rRows = computed(() => (rc.value?.rows ?? []).filter(r => !resultPick.value || resultPick.value.filter(r)))
const { sortKey: rk, sortDir: rd, toggle: rt, sorted: rs } = useSort(rRows, 'no')
const RES_CLASS: Record<string, string> = { 一致: 'ok', 規劃中: 'dim', '沒有可比對的編號（只能人工）': 'dim', 未辨識: 'dim', '多筆候選（只能人工）': 'dim' }
// 要人確認的差異（按「確認（照片對）」才進 CIA 待更正）
const REVIEWABLE = ['同櫃但U位不同', '機櫃不同', '機房不同', '機型不同', '系統沒有機櫃資料', '系統沒填機房', '照片有系統沒有']
const POSITION = ['同櫃但U位不同', '機櫃不同', '機房不同']
const notes = reactive<Record<number, string>>({})
async function review(x: Row, action: 'confirm' | 'misread') {
  try {
    const rv = await guardedFetch<Review>(`/api/rack-diagram/rows/${x.row_id}/review`, { method: 'POST', body: { action, note: notes[x.row_id] || null } })
    x.review = rv
    showToast(action === 'confirm' ? '已記下：確認（照片對）' : '已記下：判讀錯了', 'success')
    if (tab.value === 'reviews') await loadReviews()
  } catch (e: any) { showToast(`記錄失敗：${err(e)}`, 'error') }
}
function pickGroup(g: string) {
  if (g === '系統有照片沒有') { tab.value = 'sysonly'; return }
  if (g === '沒拍到的櫃') { tab.value = 'missing'; return }
  const keys = g === '位置不同' ? POSITION : [g]
  resultPick.value = { label: g, filter: r => keys.includes(r.result) }
  tab.value = 'result'
}
const confirmedCount = computed(() => (rc.value?.rows ?? []).filter(r => r.review?.action === 'confirm' && REVIEWABLE.includes(r.result) && r.result !== '照片有系統沒有').length)
const soRows = computed(() => (rc.value?.system_only ?? []).map((x, i) => ({ ...x, no: i + 1 })))
const { sortKey: ok_, sortDir: od, toggle: ot, sorted: os } = useSort(soRows, 'no')
interface RvH { id: number; row_id: number; action_label: string; note: string | null; source: string | null; shot_date: string | null; confidence: string | null; reviewed_by: string | null; reviewed_at: string }
const reviews = ref<RvH[]>([])
async function loadReviews() { if (cur.value) try { reviews.value = await apiFetch<RvH[]>(`/api/rack-diagram/imports/${cur.value}/reviews`) } catch (e: any) { showToast(`確認紀錄載入失敗：${err(e)}`, 'error') } }
watch(tab, t => { if (t === 'reviews') loadReviews() })
const rvRows = computed(() => reviews.value.map((x, i) => ({ ...x, no: reviews.value.length - i })))
const { sortKey: vk, sortDir: vd, toggle: vt, sorted: vs } = useSort(rvRows, 'no')
const okRate = computed(() => rc.value?.rates?.same_rack_rate ?? null)
function drillRack(r: Rate, res?: string) {
  resultPick.value = { label: `${r.sheet} ${r.rack}${res ? ' ' + res : ''}`, filter: x => x.sheet === r.sheet && x.rack === r.rack && (!res || x.result === res) }
  tab.value = 'result'
}
const rateRows = computed(() => (rc.value?.rate ?? []).map((x, i) => ({ ...x, no: i + 1, rate_s: x.rate ?? -1, u_rate_s: x.u_rate ?? -1 })))
const { sortKey: tk, sortDir: td, toggle: tt, sorted: ts } = useSort(rateRows, 'no')
const missRows = computed(() => (rc.value?.missing_racks ?? []).map((x, i) => ({ ...x, no: i + 1 })))
const { sortKey: mk, sortDir: md, toggle: mt, sorted: ms } = useSort(missRows, 'no')
const missOpen = ref<string | null>(null)
const diffKind = ref<'added' | 'removed' | 'moved'>('moved')
const diffRows = computed(() => ((diff.value?.[diffKind.value] ?? []) as any[]).map((x, i) => ({ ...x, no: i + 1, where: x.from ? `${x.from} → ${x.to}` : `${x.rack} ${x.u_start}-${x.u_end}` })))
const { sortKey: dk, sortDir: dd, toggle: dt, sorted: ds } = useSort(diffRows, 'no')
</script>

<template>
  <div class="rdpage">
    <div class="head">
      <div class="ey">DATACENTER · 4-5</div>
      <h1>機櫃圖對帳</h1>
      <InfoNote>現場手畫的 Excel 機櫃圖（內湖 3F／4F、敦南 21F、板橋 5F）跟系統比對。原檔不能出公司，所以在公司端匯入：每張分頁找櫃頭（櫃名下一列兩側有 U 數字）、讀每一格設備（合併格跨幾 U 就是幾 U），抓 IP（第一個對得到系統的那個）、括號裡的序號、已拔電／已關機／閒置等標記；暫定／預留算「規劃中」不比；理線槽、panel、電源排插、限制使用、層板、孔位略過。比對：先用 IP，沒有 IP 用序號；內湖 CIA 的「1-n／2-n／3-n」＝現場圖的 A／B／C n。只出報告，不改 CIA、不改位置。</InfoNote>
    </div>
    <div class="tabs" role="tablist">
      <button v-for="t in ([['import', `匯入 ${imports.length}`], ['result', `對帳結果${rc ? ' ' + rc.rows.length : ''}`], ['sysonly', `系統有照片沒有${rc ? ' ' + rc.system_only.length : ''}`], ['missing', `沒拍到的櫃${rc ? ' ' + rc.missing_racks.length : ''}`], ['reviews', '確認紀錄'], ['rate', '一致率（每櫃）'], ['diff', '跟上次比'], ['export', '匯出現場格式']] as [Tab, string][])"
              :key="t[0]" type="button" class="tab" :class="{ active: tab === t[0] }" @click="tab = t[0]">{{ t[1] }}</button>
    </div>
    <p v-if="rc" class="cur">目前看：#{{ rc.import.id }} {{ rc.import.site }}｜{{ rc.import.file_name }}｜{{ rc.import.imported_at.slice(0, 16) }}（{{ rc.import.imported_by || '—' }}）
      <span v-if="rc.rates">｜兩邊都放得到 {{ rc.rates.placed }} 台：同櫃率 <b>{{ rc.rates.same_rack_rate ?? '—' }}%</b>（{{ rc.rates.same_rack }}）、U 位一致率 <b>{{ rc.rates.u_rate ?? '—' }}%</b>（{{ rc.rates.u_ok }}／{{ rc.rates.u_both }}）</span>
      <InfoNote>一致率只算「兩邊都放得到」的：照片那格配到系統某一台，而且兩邊都有機櫃。同櫃率＝同一櫃的比例；U 位一致率＝同櫃、兩邊都有 U 位的那幾台裡 U 位重疊的比例。沒有可比對編號、規劃中、未辨識、照片有系統沒有、系統沒填機房等都不算進分母。</InfoNote></p>

    <div v-if="tab === 'import'" class="panel">
      <div class="chips">
        <label>機房 <select v-model="site"><option v-for="s in SITES" :key="s">{{ s }}</option></select></label>
        <input type="file" accept=".xlsx,.csv" @change="file = ($event.target as HTMLInputElement).files?.[0] ?? null">
        <input v-model="dft.source" class="note" placeholder="預設來源（可空，例：機櫃圖截圖 2026-10-23）">
        <label>預設拍攝日期 <input v-model="dft.shot_date" type="date"></label>
        <label>預設把握度 <select v-model="dft.confidence"><option value="">（不填）</option><option>高</option><option>中</option><option>低</option></select></label>
        <button class="tb go" :disabled="uploading" @click="upload">{{ uploading ? '匯入中…' : '匯入並對帳' }}</button>
        <InfoNote>兩種檔都收：①現場格式（櫃並排、U 在兩側）；②清單格式（照片判讀結果，一列一台），表頭含「機櫃」「內容」，其他可選：機房、樓層、U起、U迄、IP、序號、來源（照片檔名）、拍攝日期、把握度（高／中／低／未辨識）。看不清楚的寫「未辨識」，系統不會硬配。照片本身不要上傳（敏感資料），只填檔名。配對順序：資產序號 → 主機名稱貼紙 → IP → 機型＋位置（同櫃有兩台同型號就不靠機型配）。</InfoNote>
      </div>
      <table class="tbl">
        <thead><tr>
          <SortTh k="no" :active="ik" :dir="id_" @sort="it">項次</SortTh>
          <SortTh k="site" :active="ik" :dir="id_" @sort="it">機房</SortTh>
          <SortTh k="file_name" :active="ik" :dir="id_" @sort="it">檔名</SortTh>
          <SortTh k="sheets" :active="ik" :dir="id_" @sort="it">分頁</SortTh>
          <SortTh k="rows" :active="ik" :dir="id_" @sort="it">設備格</SortTh>
          <SortTh k="imported_at" :active="ik" :dir="id_" @sort="it">匯入時間</SortTh>
          <SortTh k="imported_by" :active="ik" :dir="id_" @sort="it">誰</SortTh>
          <th>SHA256</th>
        </tr></thead>
        <tbody>
          <tr v-for="x in is_" :key="x.id" :class="{ on: cur === x.id }">
            <td class="mono">I{{ x.no }}</td><td>{{ x.site }}</td>
            <td><button class="linkbtn" @click="pick(x.id)">{{ x.file_name }}</button></td>
            <td class="mono">{{ x.sheets }}</td><td class="mono">{{ x.rows }}</td><td class="mono small">{{ x.imported_at.slice(0, 16) }}</td>
            <td>{{ x.imported_by || '—' }}</td><td class="mono small">{{ x.sha256.slice(0, 12) }}</td>
          </tr>
          <tr v-if="!is_.length"><td colspan="8" class="muted">還沒匯入過</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="tab === 'result'" class="panel">
      <p v-if="!rc" class="muted">先到「匯入」上傳一份現場圖。</p>
      <template v-else>
        <div class="chips groups">
          <b>差異</b>
          <button v-for="(n, g) in rc.groups" :key="g" class="chip" :class="{ warnchip: n, on: resultPick?.label === g }" @click="pickGroup(String(g))">{{ g }} {{ n }}</button>
          <a class="lnk small" :href="`${apiBase}/api/rack-diagram/imports/${rc.import.id}/cia-fix.xlsx`">匯出 CIA 待更正（已確認 {{ confirmedCount }} 筆）</a>
          <NuxtLink class="lnk small" :to="{ path: '/datacenter' }">到 4-1 機櫃正面「依盤點結果更正」</NuxtLink>
        </div>
        <div class="chips">
          <button class="chip" :class="{ on: !resultPick }" @click="resultPick = null">全部 {{ rc.rows.length }}</button>
          <button v-for="(n, k) in rc.counts" :key="k" class="chip" :class="{ on: resultPick?.label === k, warnchip: n && !['一致', '規劃中'].includes(String(k)) }" :disabled="!n"
                  @click="resultPick = { label: String(k), filter: r => r.result === k }">{{ k }} {{ n }}</button>
        </div>
        <p v-if="resultPick" class="muted">只看：{{ resultPick.label }}（{{ rRows.length }}）</p>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="rk" :dir="rd" @sort="rt">項次</SortTh>
            <SortTh k="result" :active="rk" :dir="rd" @sort="rt">結果</SortTh>
            <SortTh k="sheet" :active="rk" :dir="rd" @sort="rt">分頁</SortTh>
            <SortTh k="rack" :active="rk" :dir="rd" @sort="rt">現場櫃</SortTh>
            <SortTh k="u_start" :active="rk" :dir="rd" @sort="rt">現場 U</SortTh>
            <SortTh k="text" :active="rk" :dir="rd" @sort="rt">現場圖文字</SortTh>
            <SortTh k="sys_name" :active="rk" :dir="rd" @sort="rt">系統主機</SortTh>
            <SortTh k="sys_rack" :active="rk" :dir="rd" @sort="rt">系統機房／櫃／U</SortTh>
            <SortTh k="match_by" :active="rk" :dir="rd" @sort="rt">比對方式</SortTh>
            <SortTh k="source" :active="rk" :dir="rd" @sort="rt">來源／日期／把握度</SortTh>
            <th>確認</th>
          </tr></thead>
          <tbody>
            <tr v-for="x in rs" :key="x.no">
              <td class="mono">R{{ x.no }}</td>
              <td :class="RES_CLASS[x.result] ?? 'bad'">{{ x.result }}<div v-if="x.detail" class="muted small">{{ x.detail }}</div></td>
              <td class="small">{{ x.sheet }}</td><td>{{ x.rack }}</td><td class="mono">{{ x.u }}</td>
              <td class="small">{{ x.text }}<span v-if="x.state" class="tag">{{ x.state }}</span></td>
              <td><NuxtLink v-if="x.sys_serial" :to="`/assets/${encodeURIComponent(x.sys_serial)}`" class="lnk">{{ x.sys_name }}</NuxtLink><span v-else class="muted">—</span></td>
              <td class="small">{{ [x.sys_room, x.sys_rack, x.sys_u].filter(Boolean).join(' ') || '—' }}
                <NuxtLink v-if="x.sys_serial && x.result !== '一致'" :to="{ path: '/datacenter', query: { room: x.sys_room } }" class="lnk small">　到 4-1 更正位置</NuxtLink></td>
              <td class="small">{{ x.match_by || '—' }}</td>
              <td class="small">{{ x.source || x.sheet }}<div class="muted">{{ x.shot_date || '日期未填' }}・把握度 {{ x.confidence || '未填' }}</div></td>
              <td class="small rv">
                <template v-if="REVIEWABLE.includes(x.result)">
                  <div v-if="x.review" :class="x.review.action === 'confirm' ? 'ok' : 'dim'">{{ x.review.action_label }}<span class="muted">（{{ x.review.reviewed_by || '—' }} {{ x.review.reviewed_at.slice(5, 16) }}）</span><div v-if="x.review.note" class="muted">{{ x.review.note }}</div></div>
                  <input v-model="notes[x.row_id]" class="note" placeholder="備註（可空）">
                  <button class="tb sm" @click="review(x, 'confirm')">確認（照片對）</button>
                  <button class="tb sm" @click="review(x, 'misread')">判讀錯了</button>
                </template>
                <span v-else class="muted">—</span>
              </td>
            </tr>
            <tr v-if="!rs.length"><td colspan="11" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </template>
    </div>

    <div v-if="tab === 'sysonly'" class="panel">
      <p v-if="!rc" class="muted">先匯入。</p>
      <template v-else>
        <p class="muted">照片／現場圖有拍到這一櫃，但這幾台系統有登記、照片上找不到。<b>不一定是錯</b>：可能裝在背面、被擋住，或已下架——請現場確認。</p>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="ok_" :dir="od" @sort="ot">項次</SortTh>
            <SortTh k="rack" :active="ok_" :dir="od" @sort="ot">系統櫃</SortTh>
            <SortTh k="u" :active="ok_" :dir="od" @sort="ot">系統 U</SortTh>
            <SortTh k="name" :active="ok_" :dir="od" @sort="ot">主機</SortTh>
            <SortTh k="model" :active="ok_" :dir="od" @sort="ot">機型</SortTh>
            <SortTh k="ip" :active="ok_" :dir="od" @sort="ot">IP</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="x in os" :key="x.key">
              <td class="mono">S{{ x.no }}</td><td>{{ x.rack }}</td><td class="mono">{{ x.u || '—' }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(x.serial)}`" class="lnk">{{ x.name }}</NuxtLink></td>
              <td>{{ upText(x.model) || '—' }}</td><td class="mono small">{{ x.ip || '—' }}</td>
            </tr>
            <tr v-if="!os.length"><td colspan="6" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </template>
    </div>

    <div v-if="tab === 'reviews'" class="panel">
      <p class="muted">每一次按「確認（照片對）」／「判讀錯了」都留一筆，不改不刪（稽核用）；同一筆以最新的為準。</p>
      <table class="tbl">
        <thead><tr>
          <SortTh k="no" :active="vk" :dir="vd" @sort="vt">項次</SortTh>
          <SortTh k="reviewed_at" :active="vk" :dir="vd" @sort="vt">時間</SortTh>
          <SortTh k="reviewed_by" :active="vk" :dir="vd" @sort="vt">誰</SortTh>
          <SortTh k="action_label" :active="vk" :dir="vd" @sort="vt">判定</SortTh>
          <SortTh k="row_id" :active="vk" :dir="vd" @sort="vt">哪一列</SortTh>
          <SortTh k="source" :active="vk" :dir="vd" @sort="vt">來源照片</SortTh>
          <SortTh k="note" :active="vk" :dir="vd" @sort="vt">備註</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="x in vs" :key="x.id">
            <td class="mono">C{{ x.no }}</td><td class="mono small">{{ x.reviewed_at.slice(0, 16) }}</td><td>{{ x.reviewed_by || '—' }}</td>
            <td>{{ x.action_label }}</td>
            <td><button class="linkbtn mono" @click="resultPick = { label: `第 ${x.row_id} 列`, filter: r => r.row_id === x.row_id }; tab = 'result'">#{{ x.row_id }}</button></td>
            <td class="small">{{ x.source || '—' }} {{ x.shot_date || '' }}</td><td class="small">{{ x.note || '—' }}</td>
          </tr>
          <tr v-if="!vs.length"><td colspan="7" class="muted">還沒有人確認過</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="tab === 'rate'" class="panel">
      <p v-if="!rc" class="muted">先匯入。</p>
      <table v-else class="tbl">
        <thead><tr>
          <SortTh k="no" :active="tk" :dir="td" @sort="tt">項次</SortTh>
          <SortTh k="sheet" :active="tk" :dir="td" @sort="tt">分頁</SortTh>
          <SortTh k="rack" :active="tk" :dir="td" @sort="tt">櫃</SortTh>
          <SortTh k="total" :active="tk" :dir="td" @sort="tt">設備格</SortTh>
          <SortTh k="comparable" :active="tk" :dir="td" @sort="tt">兩邊都放得到</SortTh>
          <SortTh k="ok" :active="tk" :dir="td" @sort="tt">同櫃</SortTh>
          <SortTh k="rate_s" :active="tk" :dir="td" @sort="tt">同櫃率</SortTh>
          <SortTh k="u_rate_s" :active="tk" :dir="td" @sort="tt">U 位一致率</SortTh>
          <th>不一致的</th>
        </tr></thead>
        <tbody>
          <tr v-for="x in ts" :key="x.sheet + x.rack">
            <td class="mono">G{{ x.no }}</td><td class="small">{{ x.sheet }}</td><td>{{ x.rack }}</td>
            <td><button class="linkbtn mono" @click="drillRack(x)">{{ x.total }}</button></td>
            <td class="mono">{{ x.comparable }}</td>
            <td><button class="linkbtn mono" @click="drillRack(x)">{{ x.ok }}</button></td>
            <td class="mono" :class="{ bad: x.rate !== null && x.rate < 80 }">{{ x.rate === null ? '無法比' : x.rate + '%' }}</td>
            <td class="mono" :class="{ bad: x.u_rate !== null && x.u_rate < 80 }">{{ x.u_rate === null ? '無法比' : x.u_rate + '%' }}</td>
            <td class="small"><template v-for="(n, k) in x.counts" :key="k"><button v-if="k !== '一致'" class="linkbtn" @click="drillRack(x, String(k))">{{ k }} {{ n }}</button>　</template></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="tab === 'missing'" class="panel">
      <p v-if="!rc" class="muted">先匯入。</p>
      <template v-else>
        <p class="muted">系統（CIA）在 {{ rc.import.site }} 有登記、但這份照片／現場圖沒涵蓋到的櫃。<b>沒拍到不等於空櫃</b>：可能只是沒拍，也可能是 CIA 櫃名寫錯。</p>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="mk" :dir="md" @sort="mt">項次</SortTh>
            <SortTh k="room" :active="mk" :dir="md" @sort="mt">機房</SortTh>
            <SortTh k="rack" :active="mk" :dir="md" @sort="mt">系統櫃名</SortTh>
            <SortTh k="count" :active="mk" :dir="md" @sort="mt">設備</SortTh>
          </tr></thead>
          <tbody>
            <template v-for="x in ms" :key="x.rack">
              <tr><td class="mono">X{{ x.no }}</td><td>{{ x.room }}</td><td>{{ x.rack }}</td>
                <td><button class="linkbtn mono" @click="missOpen = missOpen === x.rack ? null : x.rack">{{ x.count }}</button></td></tr>
              <tr v-if="missOpen === x.rack"><td colspan="4" class="small">{{ x.names.join('、') }}</td></tr>
            </template>
            <tr v-if="!ms.length"><td colspan="4" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </template>
    </div>

    <div v-if="tab === 'diff'" class="panel">
      <p v-if="!diff" class="muted">先匯入。</p>
      <p v-else-if="!diff.previous" class="muted">這是這個機房第一次匯入，沒有上一次可以比。</p>
      <template v-else>
        <p class="muted">跟上一次（#{{ diff.previous.id }} {{ diff.previous.file_name }}，{{ diff.previous.imported_at.slice(0, 16) }}）比：</p>
        <div class="chips">
          <button class="chip" :class="{ on: diffKind === 'moved' }" @click="diffKind = 'moved'">搬了 {{ diff.moved.length }}</button>
          <button class="chip" :class="{ on: diffKind === 'added' }" @click="diffKind = 'added'">多了 {{ diff.added.length }}</button>
          <button class="chip" :class="{ on: diffKind === 'removed' }" @click="diffKind = 'removed'">少了 {{ diff.removed.length }}</button>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="dk" :dir="dd" @sort="dt">項次</SortTh>
            <SortTh k="where" :active="dk" :dir="dd" @sort="dt">位置</SortTh>
            <SortTh k="text" :active="dk" :dir="dd" @sort="dt">現場圖文字</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="x in ds" :key="x.no"><td class="mono">D{{ x.no }}</td><td class="mono">{{ x.where }}</td><td class="small">{{ x.text }}</td></tr>
            <tr v-if="!ds.length"><td colspan="3" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </template>
    </div>

    <div v-if="tab === 'export'" class="panel">
      <p class="muted">從系統資料產生「跟現場同格式」的 Excel：一個機房＋排一張分頁、櫃並排、依類別上色（網路綠、虛擬環境橘、DB 藍、儲存淺黃、測試紫、關機未下架／待確認紅）。需要現場比對的格子紅框＋⚠＋註解（依這個機房最後一次匯入的現場圖）；第一張「需比對清單」先看／其次。</p>
      <div class="chips">
        <a class="tb" :href="`${apiBase}/api/rack-diagram/export.xlsx`">全部機房</a>
        <a v-for="r in rooms" :key="r" class="tb" :href="`${apiBase}/api/rack-diagram/export.xlsx?room=${encodeURIComponent(r)}`">{{ r }}</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rdpage { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 10px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 8px 0 0; color: var(--ink); display: inline-block; }
.cur { font-size: 12.5px; color: var(--ink-soft); margin: 0 0 8px; }
.tabs { display: flex; align-items: center; gap: 4px; margin: 0 0 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab { padding: 8px 14px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; font-family: inherit; }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; overflow-x: auto; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 4px 0 10px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12.5px; padding: 4px 12px; border-radius: 20px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip.warnchip { border-color: var(--bad); color: var(--bad); }
.chip:disabled { opacity: .45; cursor: default; }
.tb { font-family: inherit; font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 9px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); cursor: pointer; text-decoration: none; }
.tb.go { background: var(--brand); color: var(--card); border-color: var(--brand); }
.tb:disabled { opacity: .55; cursor: progress; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 7px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 7px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; }
.tbl tr.on td { background: var(--mint); }
.ok { color: var(--good); font-weight: 600; }
.bad { color: var(--bad); font-weight: 600; }
.dim { color: var(--muted); }
.note { font-family: inherit; font-size: 12px; padding: 2px 6px; width: 140px; border: 1px solid var(--border-strong); border-radius: 6px; margin: 2px 4px 2px 0; }
.tb.sm { font-size: 11.5px; padding: 3px 8px; margin: 2px 4px 2px 0; }
.rv { min-width: 230px; }
.groups b { margin-right: 4px; }
.tag { display: inline-block; margin-left: 6px; font-size: 11px; padding: 0 6px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); }
.mono { font-family: var(--disp); }
.small { font-size: 11.5px; }
.muted { color: var(--muted); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.linkbtn { background: none; border: 0; padding: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; text-align: left; }
.linkbtn:hover { text-decoration: underline; }
</style>
