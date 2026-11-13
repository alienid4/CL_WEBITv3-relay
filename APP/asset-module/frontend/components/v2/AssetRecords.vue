<script setup lang="ts">
import { NOT_SORTABLE, RECORD_FILTER_KEYS, SCAN_STATUS_LABEL, pruneSelected, recordFilterQuery, toggleMulti } from '~/utils/assetRecords'
// 2.1 主機查詢「逐筆登記」頁籤（2.057.1，V61；原舊 /assets 資產查詢硬體分頁）純搬移：
//   · 同一支 GET /api/assets（limit／offset／sort_by／order＋同一組篩選參數，伺服器端分頁與排序；總筆數看 X-Total-Count），
//     欄位定義 GET /api/assets/field-groups、GET /api/field-meta，篩選按鈕台數 GET /api/assets/facets。
//   · 網址篩選照舊：scan_status、environment、environment_group、location_group、filter_field＋filter_value（任意欄位同值，
//     DataCell 與別頁連過來用）、virtual、os_class、os_source、platform、os_type、os_detail、role、canonical_os、canonical_model、collect、source、q。
//     每個條件一個可移除的標籤；換條件用 router.push（留瀏覽紀錄，按上一頁回得去，同舊頁）。
//   · 欄位：常用欄位＋進階欄位依分類切換（一次一類，同舊頁）。
//   · 勾選後「標記下線／報廢」沿用 OfflineMarkModal（POST /api/assets/batch-status，原因必填、只記 CIA 待異動）；
//     「批次偵測存活」交給外層「偵測存活」頁籤（同一份 V2 偵測流程）。每列動作沿用共用 RowActions（含一鍵納管，判準在後端，⚠D1）。
//   · 匯出＝目前條件、目前排序的全部筆數（同一支 API 不帶 limit）。
//   人員、軟體兩個分頁不搬（待決定 6），改成這裡的「人員／軟體」條件（N20，同一支 /api/assets 的 person／software 參數）。
interface FieldGroups { hardware: { common: string[]; advanced: string[] } }
interface FieldMeta { categories: { key: string; label: string }[]; fields: Record<string, { label: string; category: string; layer: string; help?: string }> }
interface Facets {
  platform: Record<string, number>; role: Record<string, number>; total: number
  os_type?: Record<string, number>; os_order?: string[]; os_detail?: Record<string, number>; os_detail_order?: string[]
  total_machines?: number; total_rows?: number; os_overlap_machines?: number
}
const emit = defineEmits<{ (e: 'alive', serials: string[]): void }>()
const { apiFetch, apiFetchWithHeaders } = useApi()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()

const FIELD_LABELS: Record<string, string> = {
  hostname: '主機名稱', ip: 'IP', device_model: '設備機型', rack_no: '機櫃編號',
  group_name: '群組名稱', api_id: 'API ID', asset_purpose: '資產用途', custodian: '保管者',
  usage_unit: '使用單位', asset_status: '資產狀態', environment: '環境別',
  confidentiality: '機密性', availability: '可用性', integrity: '完整性',
  request_no: '申請單編號', inventory_division: '盤點單位-處別',
  inventory_department: '盤點單位-部門', owner: '擁有者', remark: '附加說明',
  hardware_no: '硬體編號', big_ip_vip: 'BIG IP/VIP', asset_name: '資產名稱',
  infra_type: '整體基礎架構', physical_location: '資產實體位置', quantity: '數量',
  os: '作業系統', user_name: '使用者', owning_company: '所屬公司',
}
const ROLE_LABEL: Record<string, string> = { db: '資料庫', web: 'Web／應用', middleware: '中介服務', infra: '基礎服務', mgmt: '僅管理通道', unknown: '還沒收過服務' }
const ROLE_KEYS = Object.keys(ROLE_LABEL)

const fieldGroups = ref<FieldGroups | null>(null)
const fieldMeta = ref<FieldMeta | null>(null)
const facets = ref<Facets | null>(null)
const rows = ref<Record<string, any>[]>([])
const total = ref(0)
const err = ref('')
const metaErr = ref('')
const loading = ref(false)
const fieldLabel = (k: string) => fieldMeta.value?.fields[k]?.label ?? FIELD_LABELS[k] ?? k
const fieldHelp = (k: string) => fieldMeta.value?.fields[k]?.help ?? ''
const qv = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')

// ===== 篩選（網址為準）=====
const filterQuery = computed(() => recordFilterQuery(route.query))      // 跟舊頁 loadAll 同一組參數、同樣的檢查
async function pushQuery(patch: Record<string, string | undefined>) {
  await router.push({ query: { ...route.query, ...patch, rpage: undefined } })
}
const setOf = (k: string) => new Set(qv(k).split(',').filter(Boolean))
const toggleIn = (k: 'os_type' | 'os_detail' | 'role', v: string, order: readonly string[]) => pushQuery({ [k]: toggleMulti(qv(k), v, order) })
const osChips = computed(() => (facets.value?.os_order ?? []).filter(k => (facets.value?.os_type?.[k] ?? 0) > 0 || setOf('os_type').has(k)))
const detailChips = computed(() => (facets.value?.os_detail_order ?? []).filter(k => (facets.value?.os_detail?.[k] ?? 0) > 0 || setOf('os_detail').has(k)))
const allRolesOn = computed(() => setOf('role').size === ROLE_KEYS.length)
const showMore = ref(false)
const fmt = (n: number | undefined) => (n === undefined ? '' : n.toLocaleString())
const activeChips = computed(() => {
  const out: { text: string; keys: string[] }[] = []
  const f = filterQuery.value
  const add = (text: string, ...keys: string[]) => out.push({ text, keys })
  if (f.scan_status) add(SCAN_STATUS_LABEL[f.scan_status], 'scan_status')
  if (f.q) add(`搜尋：${f.q}`, 'q')
  if (f.environment) add(`環境別：${f.environment}`, 'environment')
  if (f.environment_group) add(`環境分組：${f.environment_group}`, 'environment_group')
  if (f.location_group) add(`機房分組：${f.location_group}`, 'location_group')
  if (f.filter_field) add(`${fieldLabel(f.filter_field)}：${f.filter_value || '（空值）'}`, 'filter_field', 'filter_value')
  if (f.virtual) add(f.virtual === 'yes' ? '虛擬機' : '實體機', 'virtual')
  if (f.os_type) add(`OS 類型：${f.os_type}`, 'os_type')
  if (f.os_detail) add(`Linux 細分：${f.os_detail}`, 'os_detail')
  if (f.os_class) add(`OS 大類：${f.os_class}`, 'os_class')
  if (f.os_source) add(`OS 來源：${f.os_source}`, 'os_source')
  if (f.platform) add(`平台：${f.platform}`, 'platform')
  if (f.canonical_os) add(`版本：${f.canonical_os}`, 'canonical_os')
  if (f.canonical_model) add(`型號：${f.canonical_model}`, 'canonical_model')
  if (f.collect) add(`納管：${f.collect}`, 'collect')
  if (f.source) add(`來源：${f.source}`, 'source')
  if (f.role) add(`在做什麼：${f.role.split(',').map(r => ROLE_LABEL[r] ?? r).join('、')}`, 'role')
  if (f.person) add(`人員：${f.person}`, 'person')
  if (f.software) add(`軟體：${f.software}`, 'software')
  return out
})
const dropKeys = (keys: string[]) => pushQuery(Object.fromEntries(keys.map(k => [k, undefined])))
const clearAll = () => pushQuery(Object.fromEntries(RECORD_FILTER_KEYS.map(k => [k, undefined])))
const searchInput = ref(qv('q'))
watch(() => route.query.q, () => { searchInput.value = qv('q') })
const submitSearch = () => pushQuery({ q: searchInput.value.trim() || undefined })
// N20（舊 /assets 人員、軟體分頁＋任意欄位同值）：條件寫進網址（person／software／filter_field＋filter_value），可分享；
// 任意欄位＝登記的全部欄位（常用＋各類進階），值是完全相同（同舊頁點格子的同值篩選），值留空＝看這欄是空的登記
const personInput = ref(qv('person'))
const softwareInput = ref(qv('software'))
const fieldPick = ref(qv('filter_field'))
const valueInput = ref(qv('filter_value'))
watch(() => [route.query.person, route.query.software, route.query.filter_field, route.query.filter_value], () => {
  personInput.value = qv('person'); softwareInput.value = qv('software'); fieldPick.value = qv('filter_field'); valueInput.value = qv('filter_value')
})
const fieldOptions = computed(() => {
  const keys = ['asset_serial', ...(fieldGroups.value?.hardware.common ?? []), ...(fieldGroups.value?.hardware.advanced ?? [])]
  return [...new Set(keys)].map(k => ({ k, label: k === 'asset_serial' ? '資產編號' : fieldLabel(k) }))
})
const submitConds = () => pushQuery({
  person: personInput.value.trim() || undefined,
  software: softwareInput.value.trim() || undefined,
  filter_field: fieldPick.value || undefined,
  filter_value: fieldPick.value ? valueInput.value.trim() : undefined,
})

// ===== 欄位 =====
const commonColumns = computed(() => fieldGroups.value?.hardware.common.filter(k => k !== 'asset_status') ?? [])
const advancedGroups = computed(() => {
  const fm = fieldMeta.value
  const adv = fieldGroups.value?.hardware.advanced ?? []
  if (!fm) return adv.length ? [{ key: 'other', label: '進階欄位', keys: adv }] : []
  const byCat = new Map<string, string[]>()
  for (const k of adv) { const c = fm.fields[k]?.category ?? 'other'; if (!byCat.has(c)) byCat.set(c, []); byCat.get(c)!.push(k) }
  const gs = fm.categories.map(c => ({ key: c.key, label: c.label, keys: byCat.get(c.key) ?? [] })).filter(g => g.keys.length)
  const known = new Set(gs.flatMap(g => g.keys))
  const left = adv.filter(k => !known.has(k))
  if (left.length) gs.push({ key: 'other', label: '其他', keys: left })
  return gs
})
const colSet = computed(() => qv('rcols') || 'common')         // 網址記住看哪一組欄位（可分享）
const pickCols = (k: string) => router.replace({ query: { ...route.query, rcols: k === 'common' ? undefined : k } })
const advGroup = computed(() => advancedGroups.value.find(g => g.key === colSet.value) ?? null)
const fieldCol = (k: string) => ({ k, label: fieldLabel(k), note: fieldHelp(k) || undefined, mono: k === 'ip' || k === 'asset_serial' })
const cols = computed<any[]>(() => {
  if (advGroup.value) return [fieldCol('hostname'), { k: 'asset_serial', label: '資產編號', mono: true }, ...advGroup.value.keys.filter(k => k !== 'hostname').map(fieldCol)]
  return [
    { k: 'health_rank', label: '體檢', text: (r: any) => r.health_text },
    { k: 'trust_score', label: '可信度', mono: true, text: (r: any) => (r.trust_score ?? '—') },
    { k: 'asset_status', label: '狀態', text: (r: any) => r.status_text },
    ...commonColumns.value.map(fieldCol),
    { k: 'guess', label: '系統推測', text: (r: any) => r.guess_text },
    { k: 'ops', label: '操作', text: () => '' },
  ]
})

// ===== 每列顯示用文字 =====
const isNotCia = (s: any) => /^(DYN-|VC-|AUTO-|ADOPT-)/.test(String(s ?? ''))
function healthText(r: any) {
  if (!r.health_machine) return '—'
  return r.health_headline || (r.health_verified === false ? '未驗證' : '沒問題')
}
function healthTitle(r: any): string {
  const lines = (r.health_issues ?? []).map((i: any) => [`${i.level === 'bad' ? '●' : '○'} ${i.label}：${i.detail}`, `   對照：${i.basis}`, `   下一步：${i.action}`].join('\n'))
  if (r.health_verified === false) lines.push('○ 未經 SSH/WinRM 驗證：以下資料來自登記/掃描，不是機器親口確認的')
  return lines.length ? lines.join('\n\n') : '體檢全部通過'
}
const deco = (r: any, i: number) => ({
  ...r, __no: (page.value - 1) * size.value + i + 1,
  health_text: healthText(r),
  status_text: (r.asset_status ?? (isNotCia(r.asset_serial) ? 'CIA 未登記' : '未知')) + (r.pending_status && r.pending_status !== r.asset_status ? `（待 CIA：${r.pending_status}）` : ''),
  guess_text: r.identity_guess ? `${r.identity_guess_kind === 'os' ? 'OS' : '型號'}：${r.identity_guess}${r.identity_guess_confirmed ? '' : '（猜）'}` : '—',
})

// ===== 分頁、排序（伺服器端；網址 rpage／rsize／rsort／rdesc，跟外層主機清單的分頁參數分開）=====
const page = computed(() => Number(qv('rpage')) || 1)
const size = computed(() => Number(qv('rsize')) || 50)
const sortKey = computed(() => qv('rsort') || 'hostname')
const sortDir = computed<'asc' | 'desc'>(() => (qv('rdesc') === '1' ? 'desc' : 'asc'))
const server = computed(() => ({ total: total.value, page: page.value, size: size.value, sortKey: sortKey.value, sortDir: sortDir.value }))
function onSort(k: string) {
  if (NOT_SORTABLE.has(k)) return
  const desc = k === sortKey.value && sortDir.value === 'asc'
  router.replace({ query: { ...route.query, rsort: k === 'hostname' ? undefined : k, rdesc: desc ? '1' : undefined, rpage: undefined } })
}
function onPage(p: number, s: number) { router.replace({ query: { ...route.query, rpage: p > 1 ? String(p) : undefined, rsize: s !== 50 ? String(s) : undefined } }) }
const assetQuery = (paged = true) => ({ ...filterQuery.value, sort_by: sortKey.value, order: sortDir.value,
  ...(paged ? { limit: size.value, offset: (page.value - 1) * size.value } : {}) })

async function loadMeta() {
  const [fg, fm, fc] = await Promise.allSettled([apiFetch<FieldGroups>('/api/assets/field-groups'), apiFetch<FieldMeta>('/api/field-meta'), apiFetch<Facets>('/api/assets/facets')])
  if (fg.status === 'fulfilled') fieldGroups.value = fg.value
  if (fm.status === 'fulfilled') fieldMeta.value = fm.value
  if (fc.status === 'fulfilled') facets.value = fc.value
  const bad = [fg, fm, fc].map((x, i) => (x.status === 'rejected' ? ['欄位設定', '欄位說明', '篩選台數'][i] : '')).filter(Boolean)
  metaErr.value = bad.length ? `${bad.join('、')}讀取失敗（清單照常可用）` : ''        // 舊頁篩選台數讀不到時靜默；V2 寫出來
}
async function load() {
  loading.value = true
  try {
    const r = await apiFetchWithHeaders<Record<string, any>[]>('/api/assets', { query: assetQuery() })
    rows.value = r.data
    selected.value = pruneSelected(selected.value, r.data)   // 2.058.2：重新載入後（例：RowActions 改了某列）只留還看得到的勾選，離開條件的不能被送出
    total.value = Number(r.headers.get('X-Total-Count') ?? r.data.length)
    err.value = ''
  } catch (e: any) { err.value = `資產資料載入失敗：${e?.data?.detail ?? e?.message ?? '請稍後再試'}` } finally { loading.value = false }
}
const list = computed(() => rows.value.map(deco))
const cellKeys = computed(() => [...new Set([...commonColumns.value, ...(advGroup.value?.keys ?? []), 'hostname'])])
// 匯出：目前條件、目前排序的全部筆數（不帶 limit），項次＝排序後的順序
// N20（全部登記 Excel）：不管畫面看的是哪一組欄位，匯出都附上其餘全部登記欄位（常用＋各類進階），一份檔案就是完整登記
const exportCols = computed<any[]>(() => {
  const shown = new Set(cols.value.map((c: any) => c.k))
  const all = ['asset_serial', 'asset_status', ...commonColumns.value, ...(fieldGroups.value?.hardware.advanced ?? [])]
  return [...new Set(all)].filter(k => !shown.has(k)).map(k => (k === 'asset_serial' ? { k, label: '資產編號' } : fieldCol(k)))
})
const exportAll = async () => (await apiFetch<Record<string, any>[]>('/api/assets', { query: assetQuery(false) })).map((r, i) => ({ ...deco(r, i), __no: i + 1 }))
onMounted(() => { loadMeta(); load() })
watch(() => [JSON.stringify(filterQuery.value), page.value, size.value, sortKey.value, sortDir.value], () => { selected.value = {}; load() })

// ===== 勾選與批次 =====
const selected = ref<Record<string, boolean>>({})
const pickedSerials = computed(() => Object.keys(selected.value).filter(k => selected.value[k]))
const showOffline = ref(false)
</script>

<template>
  <section>
    <div class="bar">
      <input v-model="searchInput" class="in" placeholder="搜尋主機名稱／IP／資產編號…" aria-label="搜尋登記" @keydown.enter="submitSearch">
      <button type="button" class="btn2" @click="submitSearch">搜尋</button>
      <span class="muted">共 {{ total.toLocaleString() }} 筆登記</span>
      <InfoNote>一列是一筆登記（不是一台）；預設不含退役。點任一格的值＝列出同值的所有登記（例如點設備機型，看全部同型號）。數字是伺服器端分頁與排序；匯出（Excel）是目前條件的全部筆數，含全部登記欄位（不只畫面這一組）。</InfoNote>
    </div>
    <p v-if="metaErr" class="warn">{{ metaErr }}</p>

    <form class="bar" aria-label="人員、軟體、欄位條件" @submit.prevent="submitConds">
      <span class="muted flab">條件</span>
      <input v-model="personInput" class="in sm" placeholder="人員" aria-label="人員">
      <input v-model="softwareInput" class="in sm" placeholder="軟體" aria-label="軟體">
      <select v-model="fieldPick" class="in sm" aria-label="欄位">
        <option value="">任意欄位…</option>
        <option v-for="o in fieldOptions" :key="o.k" :value="o.k">{{ o.label }}</option>
      </select>
      <input v-model="valueInput" class="in sm" placeholder="值" aria-label="欄位值" :disabled="!fieldPick">
      <button type="submit" class="btn2">套用</button>
      <InfoNote>人員：比對 CIA 人員表的人員姓名、代理人、隸屬處別／部門（部分相同即可）。軟體：比對 CIA 軟體表的資料庫／軟體、資產名稱（部分相同即可）。任意欄位：該欄的值完全相同；值留空＝這欄是空的登記。條件可以同時用，結果是交集；條件記在網址，複製網址就能分享。</InfoNote>
    </form>

    <div class="bar">
      <span class="muted flab">OS 類型 <InfoNote>跟納管漏斗、OS×環境交叉表同一套分類（含手動改過的）。數字是台數（同一台多筆登記只算一次）、不含退役。</InfoNote></span>
      <button v-for="k in osChips" :key="k" type="button" class="chip" :class="{ on: setOf('os_type').has(k) }" @click="toggleIn('os_type', k, facets?.os_order ?? [])">{{ k }} <i>{{ fmt(facets?.os_type?.[k] ?? 0) }}</i></button>
      <button type="button" class="chip" :aria-expanded="showMore" @click="showMore = !showMore">更多篩選 {{ showMore ? '▲' : '▼' }}</button>
    </div>
    <template v-if="showMore || qv('os_detail') || qv('role')">
      <div v-if="detailChips.length" class="bar">
        <span class="muted flab">Linux 細分 <InfoNote>只有 OS 類型是 Linux 的才有細分（發行版）。給不出發行版的歸「Linux(其他)」。</InfoNote></span>
        <button v-for="k in detailChips" :key="k" type="button" class="chip" :class="{ on: setOf('os_detail').has(k) }" @click="toggleIn('os_detail', k, facets?.os_detail_order ?? [])">{{ k }} <i>{{ fmt(facets?.os_detail?.[k] ?? 0) }}</i></button>
      </div>
      <div class="bar">
        <span class="muted flab">在做什麼 <InfoNote>由服務盤點推導（在聽 3306 就是資料庫），不是人填的欄位。「還沒收過服務」不是沒有服務，是還沒收到它的服務清單。一台可以同時是好幾類，加起來會比總台數多。</InfoNote></span>
        <button v-for="(lb, k) in ROLE_LABEL" :key="k" type="button" class="chip" :class="{ on: setOf('role').has(k) }" @click="toggleIn('role', k, ROLE_KEYS)">{{ lb }} <i>{{ fmt(facets?.role?.[k] ?? 0) }}</i></button>
        <button type="button" class="chip" @click="pushQuery({ role: allRolesOn ? undefined : ROLE_KEYS.join(',') })">{{ allRolesOn ? '清除' : '全選' }}</button>
      </div>
    </template>
    <div v-if="activeChips.length" class="bar">
      <span class="muted flab">已選</span>
      <button v-for="c in activeChips" :key="c.text" type="button" class="chip on" :title="`移除「${c.text}」`" @click="dropKeys(c.keys)">{{ c.text }} ✕</button>
      <button type="button" class="chip" @click="clearAll">全部清空</button>
      <span v-if="facets" class="muted">母體：非退役 {{ fmt(facets.total_machines ?? 0) }} 台（{{ fmt(facets.total_rows ?? 0) }} 筆登記）<template v-if="facets.os_overlap_machines">・{{ facets.os_overlap_machines }} 台的多筆登記 OS 不一致（OS 類型加總會比母體多這幾台）</template></span>
    </div>

    <div class="bar">
      <span class="muted flab">欄位</span>
      <button type="button" class="chip" :class="{ on: !advGroup }" @click="pickCols('common')">常用</button>
      <button v-for="g in advancedGroups" :key="g.key" type="button" class="chip" :class="{ on: advGroup?.key === g.key }" @click="pickCols(g.key)">{{ g.label }}</button>
      <span class="sp" />
      <template v-if="!isViewer">
        <span class="muted">已選 {{ pickedSerials.length }} 筆</span>
        <button type="button" class="btn2" :disabled="!pickedSerials.length" @click="showOffline = true">標記下線／報廢</button>
        <button type="button" class="btn2" :disabled="!pickedSerials.length" @click="emit('alive', pickedSerials)">批次偵測存活</button>
        <InfoNote>勾選只在這一頁內有效（換頁、換條件會清掉）。標記下線／報廢：原因必填，只記「待 CIA 異動」，不改資產狀態也不改台數。批次偵測存活：帶著勾選的登記到「偵測存活」頁籤，只做連線嘗試，VMware 設備排除。</InfoNote>
      </template>
    </div>

    <p v-if="err" class="err" role="alert">{{ err }}</p>
    <p v-else-if="loading && !rows.length" class="muted">載入中…</p>
    <V2Table v-else v-model:selected="selected" :rows="list" :cols="cols" prefix="A" unit="筆" row-key="asset_serial" :selectable="!isViewer"
             :server="server" :export-all="exportAll" :export-cols="exportCols" empty="沒有符合條件的登記" @sort="onSort" @page="onPage">
      <template #cell-health_rank="{ row }"><span :title="healthTitle(row)">{{ row.health_text }}</span></template>
      <template #cell-trust_score="{ row }">
        <NuxtLink v-if="row.trust_score !== null && row.trust_score !== undefined" :to="`/v2/asset/${encodeURIComponent(row.asset_serial)}`">{{ row.trust_score }}</NuxtLink>
        <span v-else class="muted" title="退役資產不評分">—</span>
      </template>
      <template #cell-asset_status="{ row }">
        <NuxtLink :to="{ query: { ...route.query, filter_field: 'asset_status', filter_value: row.asset_status ?? '', rpage: undefined } }">{{ row.status_text }}</NuxtLink>
      </template>
      <template v-for="k in cellKeys" :key="'c' + k" #[`cell-${k}`]="{ row }">
        <DataCell :k="k" :value="row[k]" :serial="row.asset_serial" />
      </template>
      <template #cell-ops="{ row }">
        <span class="ops"><RowActions :row="row" @changed="load()" /><NuxtLink :to="`/v2/asset/${encodeURIComponent(row.asset_serial)}?edit=1`">編輯</NuxtLink></span>
      </template>
    </V2Table>
    <OfflineMarkModal v-if="showOffline" :serials="pickedSerials" scope="single" lock-scope confirm-first :who="pickedSerials.length === 1 ? pickedSerials[0] : ''" @close="showOffline = false" @done="showOffline = false; selected = {}; load()" />
  </section>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.in { font-family: inherit; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; min-width: 300px; background: var(--card); color: var(--ink); }
.in.sm { min-width: 0; width: 150px; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2:disabled { opacity: .5; cursor: default; }
.chip { font-family: inherit; font-size: 12px; padding: 2px 9px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.chip i { font-style: normal; color: var(--ink-aux); margin-left: 2px; }
.flab { min-width: 4.5em; }
.sp { flex: 1; }
.ops { display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.muted { color: var(--muted); }
.err { color: var(--bad); }
.warn { color: var(--warn-text); font-size: 13px; }
</style>
