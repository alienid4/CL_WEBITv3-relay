<script setup lang="ts">
// ⚙ 聯絡資訊「設備與系統聯絡」（V26，2.047.3 V2 原生；取代嵌入的舊 pages/contacts.vue）。同一批 API：
// /api/contacts/todo、/api/contacts/entries、PUT /api/contacts/entry（存／刪）、/api/contacts/oncall（GET／PUT）、/api/contacts/audit、
// /api/contracts、POST /api/contracts/preview|import（合約檔，先預覽再存）、POST /api/contacts/roster/preview|import（值班名冊，先預覽再寫）、
// /api/contacts/roster/template、/api/night/settings（夜間速查自動檢測開關）。
// 三層：型號（套到該型號全部機器）／系統（APID）／本機；越具體越優先。修改限管理員（後端另擋），每次異動記在「異動紀錄」。
// 網址：?st=contacts&tab=todo|entries|oncall|import|audit&api=關鍵字&by=system|model&cx=展開的那一列（ContactCard 等處的連結沿用 tab／api）。
type Tab = 'todo' | 'entries' | 'oncall' | 'import' | 'audit'
const TABS: { k: Tab; label: string }[] = [{ k: 'todo', label: '待補' }, { k: 'entries', label: '已登錄' }, { k: 'oncall', label: '值班電話' }, { k: 'import', label: '合約匯入' }, { k: 'audit', label: '異動紀錄' }]
const FIELDS = [['vendor', '廠商'], ['phone', '維護電話'], ['contact', '窗口'], ['contract_no', '合約編號'], ['contract_end', '合約迄日'], ['note', '備註']] as const
const STATUS_LABEL: Record<string, string> = { matched: '對到', unmatched: '對不上', ambiguous: '多筆（不猜）' }
const BY_LABEL: Record<string, string> = { serial: '序號', hostname: '主機名', model: '型號（全部同型號）' }
const R_LABEL: Record<string, string> = { new: '新增', update: '覆寫（取代現有值）', unchanged: '不變', conflict: '衝突', error: '格式錯誤' }
const KIND: Record<string, string> = { contact: '聯絡資訊', oncall: '值班電話', contract_import: '合約匯入' }
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const cfg = useRuntimeConfig()
const route = useRoute()
const router = useRouter()
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
const tab = computed<Tab>(() => (TABS.some(t => t.k === qs('tab')) ? qs('tab') as Tab : 'todo'))
const by = computed(() => (qs('by') === 'model' ? 'model' : 'system'))
const cx = computed(() => qs('cx'))
const kw = computed({ get: () => qs('api'), set: (v: string) => setQ({ api: v || undefined }) })
const why = (e: any) => { const d = e?.data?.detail; return (typeof d === 'string' ? d : d?.message) ?? e?.message ?? '未知錯誤' }

const todo = ref<any>(null)
const entries = ref<any[]>([])
const oncall = ref<any[]>([])
const contracts = ref<any>(null)
const audit = ref<any[]>([])
const canEditApi = ref(false)
const canEdit = computed(() => canEditApi.value && !isViewer.value)
const err = ref('')
const loading = ref(true)
const begin = useLatest()
async function loadAll() {
  const isLatest = begin()
  try {
    const [t, e, o, c, a] = await Promise.all([apiFetch<any>('/api/contacts/todo'), apiFetch<any>('/api/contacts/entries'), apiFetch<any>('/api/contacts/oncall'), apiFetch<any>('/api/contracts'), apiFetch<any>('/api/contacts/audit')])
    if (!isLatest()) return
    todo.value = t; entries.value = e.items; canEditApi.value = e.can_edit; oncall.value = o.items; contracts.value = c; audit.value = a.items
    err.value = ''
  } catch (e: any) { if (isLatest()) err.value = `聯絡資訊載入失敗：${why(e)}` } finally { if (isLatest()) loading.value = false }
}
onMounted(loadAll)
const match = (r: any) => { const s = kw.value.trim().toLowerCase(); return !s || JSON.stringify(r).toLowerCase().includes(s) }

// ---- 待補（依系統／依型號）
const todoRows = computed(() => {
  const t = todo.value
  if (!t) return []
  const src = by.value === 'system' ? t.systems.map((r: any) => ({ ...r, target: r.api_id })) : t.models.map((r: any) => ({ ...r, target: r.model, model_text: upText(r.model) }))
  return src.map((r: any, i: number) => ({ ...r, __no: i + 1, rk: `${by.value}:${r.target}` })).filter(match)
})
// ---- 已登錄、值班、合約、異動
const entryRows = computed(() => entries.value.map((r, i) => ({ ...r, __no: i + 1, rk: `e:${r.level}:${r.key}`, target: [r.key, r.target_name].filter(Boolean).join(' ') })).filter(match))
const roleText = (r: any) => ['值班', '備援', '負責人'].filter(x => r.roles?.[x]).map(x => `${x}：${r.roles[x].name || '—'}${r.roles[x].phone ? ' ' + r.roles[x].phone : ''}`).join('；')
const ocRows = computed(() => oncall.value.map((r, i) => ({ ...r, __no: i + 1, roles_text: roleText(r) })).filter(match))
const contractRowsOf = (rows: any[], p: string) => rows.map((r, i) => ({ ...r, __no: i + 1, rk: `${p}:${i}`, status_label: STATUS_LABEL[r.match_status], by_label: r.match_by ? BY_LABEL[r.match_by] : '', machine_count: (r.machines || []).length,
  end_text: r.bad_end ? `看不懂：${r.end_date_raw}` : r.end_date }))
const curFilter = computed(() => (qs('kf') in STATUS_LABEL ? qs('kf') : ''))
const curAll = computed(() => (contracts.value ? contractRowsOf(contracts.value.rows, 'k') : []))
const curRows = computed(() => curAll.value.filter(r => !curFilter.value || r.match_status === curFilter.value).filter(match))
const curCounts = computed(() => Object.fromEntries(Object.keys(STATUS_LABEL).map(k => [k, curAll.value.filter(r => r.match_status === k).length])))
const histRows = computed(() => (contracts.value?.history ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, cur: r.is_current ? '生效中' : '' })))
function brief(j: string | null) {
  if (!j) return ''
  try {
    const o = JSON.parse(j)
    return ['vendor', 'phone', 'contact', 'contract_no', 'contract_end', 'note', 'filename', 'rows', 'matched', 'unmatched', 'ambiguous'].filter(k => o[k] !== null && o[k] !== undefined && o[k] !== '').map(k => `${k}=${o[k]}`).join('　')
  } catch { return j } // 舊格式就原樣顯示，不是錯誤
}
const auditRows = computed(() => audit.value.map((r, i) => ({ ...r, __no: i + 1, kind_label: KIND[r.kind] ?? r.kind, before: brief(r.before_json), after: brief(r.after_json) })).filter(match))
// ---- 展開那一列的機器（?cx=列代號；待補、已登錄、合約都用同一張機器表）
const openMachines = computed(() => {
  const k = cx.value
  if (!k) return null
  const pool: any[] = [...todoRows.value, ...entryRows.value, ...curRows.value, ...pvRows.value]
  const r = pool.find(x => x.rk === k)
  return r ? { title: r.target || r.hostname || r.serial || r.model || '', rows: (r.machines || []).map((m: any, i: number) => ({ ...m, __no: i + 1, model_text: upText(m.model) })) } : null
})
const toggleRow = (rk: string) => setQ({ cx: cx.value === rk ? undefined : rk })
const MACH_COLS = [{ k: 'hostname', label: '主機名', link: (m: any) => (m.serial ? `/v2/machine/${encodeURIComponent(m.serial)}` : null), text: (m: any) => m.hostname || m.serial },
  { k: 'ip', label: 'IP', mono: true }, { k: 'model_text', label: '型號' }, { k: 'room', label: '機房' }, { k: 'rack', label: '機櫃' }, { k: 'systems', label: '系統' }]

// ---- 新增／修改（三層共用一個表單）
const form = reactive<Record<string, string>>({ level: 'system', key: '' })
const formOpen = ref(false)
const saving = ref(false)
function openForm(level: string, key: string, cur?: any) {
  form.level = level; form.key = key
  for (const [k] of FIELDS) form[k] = cur?.[k] ?? ''
  formOpen.value = true
}
function fillFromTodo(r: any) {
  const lvl = by.value === 'system' ? 'system' : 'model'
  openForm(lvl, r.target, entries.value.find(e => e.level === lvl && e.key === r.target))
}
async function saveForm() {
  saving.value = true
  try {
    const body: Record<string, string> = {}
    for (const k of ['level', 'key', ...FIELDS.map(f => f[0])]) body[k] = form[k] ?? ''
    await guardedFetch('/api/contacts/entry', { method: 'PUT', body })
    showToast('已儲存', 'success'); formOpen.value = false; await loadAll()
  } catch (e: any) { showToast(`儲存失敗：${why(e)}`, 'error') } finally { saving.value = false }
}
async function removeEntry(e: any) {
  if (!window.confirm(`刪除「${e.level_label}：${e.key}」這筆聯絡資訊？`)) return
  try { await guardedFetch('/api/contacts/entry', { method: 'PUT', body: { level: e.level, key: e.key } }); showToast('已刪除', 'success'); await loadAll() } catch (er: any) { showToast(`刪除失敗：${why(er)}`, 'error') }
}
// ---- 值班電話
const oc = reactive({ api_id: '', phone: '', note: '' })
async function saveOc() {
  saving.value = true
  try { await guardedFetch('/api/contacts/oncall', { method: 'PUT', body: { ...oc } }); showToast('值班電話已儲存', 'success'); oc.api_id = ''; oc.phone = ''; oc.note = ''; await loadAll() } catch (e: any) { showToast(`儲存失敗：${why(e)}`, 'error') } finally { saving.value = false }
}
const nightAuto = ref(true)
async function loadNight() {
  try { nightAuto.value = (await apiFetch<any>('/api/night/settings')).auto_live !== false } catch { /* 讀不到：維持預設（開），畫面不擋 */ }
}
onMounted(loadNight)
async function setNightAuto(on: boolean) {
  try { nightAuto.value = (await guardedFetch<any>('/api/night/settings', { method: 'PUT', body: { auto_live: on } })).auto_live; showToast(on ? '夜間速查：自動即時檢測已開' : '夜間速查：自動即時檢測已關', 'success') } catch (e: any) { showToast(`儲存失敗：${why(e)}`, 'error'); await loadNight() }
}
// ---- 檔案上傳（名冊、合約）：先預覽不寫庫，確認才寫
async function postFile(path: string, f: File, extra: Record<string, string> = {}) {
  const fd = new FormData()
  fd.append('file', f)
  for (const [k, v] of Object.entries(extra)) fd.append(k, v)
  return await guardedFetch<any>(path, { method: 'POST', body: fd })
}
const rFile = ref<File | null>(null)
const rPv = ref<any>(null)
const rBusy = ref(false)
const rFilter = ref('')
async function onRosterFile(ev: Event) {
  rFile.value = (ev.target as HTMLInputElement).files?.[0] ?? null
  rPv.value = null; rFilter.value = ''
  if (!rFile.value) return
  rBusy.value = true
  try { rPv.value = await postFile('/api/contacts/roster/preview', rFile.value) } catch (e: any) { showToast(`預覽失敗：${why(e)}`, 'error') } finally { rBusy.value = false }
}
async function doRosterImport() {
  const c = rPv.value?.counts
  if (!c || !rFile.value || !window.confirm(`確認匯入：新增 ${c.new}、覆寫現有值 ${c.update} 列寫入；衝突 ${c.conflict}、格式錯誤 ${c.error} 列不會寫。確定？`)) return
  rBusy.value = true
  try {
    const r = await postFile('/api/contacts/roster/import', rFile.value, { file_sha256: rPv.value.file_sha256, plan_digest: rPv.value.plan_digest })
    showToast(`已寫入 ${r.written_rows} 列（${r.systems_written} 個系統），略過 ${r.skipped} 列`, 'success'); rPv.value = null; rFile.value = null; await loadAll()
  } catch (e: any) { showToast(`匯入失敗：${why(e)}`, 'error') } finally { rBusy.value = false }
}
const rRows = computed(() => (rPv.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, status_label: R_LABEL[r.status], change: r.status === 'update' ? `${r.before.phone || '—'} → ${r.after.phone || '—'}` : '' }))
  .filter((r: any) => !rFilter.value || r.status === rFilter.value))
const file = ref<File | null>(null)
const pv = ref<any>(null)
const mapping = reactive<Record<string, string>>({})
const pvFilter = ref('')
const busy = ref(false)
async function onFile(ev: Event) {
  file.value = (ev.target as HTMLInputElement).files?.[0] ?? null
  pv.value = null
  if (file.value) await doPreview(false)
}
const mappingJson = () => JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)))
async function doPreview(withMapping = true) {
  if (!file.value) return
  busy.value = true
  try {
    pv.value = await postFile('/api/contracts/preview', file.value, withMapping ? { mapping: mappingJson() } : {})
    for (const k of Object.keys(mapping)) delete mapping[k]
    Object.assign(mapping, pv.value.mapping)
    pvFilter.value = ''
  } catch (e: any) { showToast(`預覽失敗：${why(e)}`, 'error') } finally { busy.value = false }
}
async function doImport() {
  if (!pv.value || !file.value || !window.confirm(`存檔會取代目前生效的合約（舊的留在歷史）。共 ${pv.value.row_count} 列，確定？`)) return
  busy.value = true
  try { const r = await postFile('/api/contracts/import', file.value, { mapping: mappingJson() }); showToast(`已匯入 ${r.row_count} 列`, 'success'); pv.value = null; file.value = null; await loadAll() } catch (e: any) { showToast(`匯入失敗：${why(e)}`, 'error') } finally { busy.value = false }
}
const pvRows = computed(() => (pv.value ? contractRowsOf(pv.value.rows, 'p') : []).filter(r => !pvFilter.value || r.match_status === pvFilter.value).filter(match))
const C_COLS = [{ k: 'serial', label: '序號', mono: true }, { k: 'asset_code', label: '資產編號' }, { k: 'hostname', label: '主機名' }, { k: 'model', label: '型號' }, { k: 'vendor', label: '廠商' },
  { k: 'contract_no', label: '合約編號' }, { k: 'end_text', label: '迄日', mono: true, tone: (r: any) => (r.bad_end ? 'prod' : '') }, { k: 'phone', label: '維護電話', mono: true }, { k: 'contact', label: '窗口' },
  { k: 'status_label', label: '比對', tone: (r: any) => (r.match_status !== 'matched' ? 'test' : '') }, { k: 'by_label', label: '依據' }, { k: 'machine_count', label: '對到（台）', mono: true }]
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <template v-else>
      <div v-if="todo" class="bar">
        <button type="button" class="card" @click="setQ({ tab: 'todo', cx: undefined, api: undefined })"><b class="warn">{{ todo.missing_count }}</b>待補（台）</button>
        <button type="button" class="card" @click="setQ({ tab: 'todo', by: 'system', cx: undefined, api: undefined })"><b>{{ todo.systems.length }}</b>待補系統</button>
        <button type="button" class="card" @click="setQ({ tab: 'todo', by: 'model', cx: undefined, api: undefined })"><b>{{ todo.models.length }}</b>待補型號</button>
        <button type="button" class="card" @click="setQ({ tab: 'entries', cx: undefined, api: undefined })"><b class="good">{{ entries.length }}</b>已登錄（筆）</button>
        <button type="button" class="card" @click="setQ({ tab: 'oncall', cx: undefined, api: undefined })"><b class="good">{{ oncall.length }}</b>值班電話（系統）</button>
        <InfoNote>三層：型號（套到該型號全部機器）／系統（APID）／本機；同一欄越具體的贏。每台機器取值順序：本機（手動）→ 合約匯入（序號／主機名）→ 系統（手動）→ 型號（手動）→ 合約匯入（型號）。待補＝帳內（CIA）現役機器還沒有廠商、維護電話、合約迄日任一項；依系統含 VM，一台掛多個 APID 時各系統都算一次；依型號只算實體機，各組加總＝缺資料實體機台數。修改限管理員，每次異動記在「異動紀錄」。</InfoNote>
      </div>
      <div class="bar">
        <button v-for="t in TABS" :key="t.k" type="button" class="chip" :class="{ on: tab === t.k }" @click="setQ({ tab: t.k, cx: undefined })">{{ t.label }}</button>
        <input v-model="kw" class="in" type="search" placeholder="搜 APID、型號、主機名…" aria-label="搜尋聯絡資訊">
        <button v-if="canEdit && (tab === 'todo' || tab === 'entries')" type="button" class="btn" @click="openForm('system', '')">＋ 新增</button>
      </div>

      <div v-if="formOpen" class="box form">
        <label>層級<select v-model="form.level"><option value="model">型號</option><option value="system">系統（APID）</option><option value="asset">本機（資產序號）</option></select></label>
        <label>對象<input v-model="form.key" :placeholder="form.level === 'model' ? '例：DELL R750' : form.level === 'system' ? '例：N-001' : '資產序號'"></label>
        <label v-for="[k, lbl] in FIELDS" :key="k">{{ lbl }}<input v-model="form[k]" :placeholder="k === 'contract_end' ? 'YYYY-MM-DD' : ''"></label>
        <button type="button" class="btn" :disabled="saving" @click="saveForm">{{ saving ? '儲存中…' : '儲存' }}</button>
        <button type="button" class="btn2" @click="formOpen = false">取消</button>
      </div>

      <template v-if="tab === 'todo' && todo">
        <div class="bar">
          <button type="button" class="chip" :class="{ on: by === 'system' }" @click="setQ({ by: undefined, cx: undefined })"><b>{{ todo.systems.length }}</b> 依系統</button>
          <button type="button" class="chip" :class="{ on: by === 'model' }" @click="setQ({ by: 'model', cx: undefined })"><b>{{ todo.models.length }}</b> 依型號（實體機 {{ todo.physical_missing }} 台）</button>
        </div>
        <V2Table :rows="todoRows" prefix="T" row-key="rk" :unit="by === 'system' ? '個系統' : '種型號'" :export-name="`待補_${by === 'system' ? '依系統' : '依型號'}`" empty="沒有待補"
                 :cols="by === 'system' ? [{ k: 'api_id', label: 'APID', mono: true, link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'name', label: '系統' }, { k: 'count', label: '缺資料（台）', mono: true }, { k: 'act', label: '', text: () => '' }]
                                        : [{ k: 'model_text', label: '型號' }, { k: 'count', label: '缺資料（台）', mono: true }, { k: 'act', label: '', text: () => '' }]">
          <template #cell-count="{ row }"><button type="button" class="lb" data-testid="contact-todo-n" @click="toggleRow(row.rk)">{{ row.count }}</button></template>
          <template #cell-act="{ row }"><button v-if="canEdit && !String(row.target).startsWith('（')" type="button" class="lb" @click="fillFromTodo(row)">補資料</button></template>
        </V2Table>
      </template>

      <V2Table v-else-if="tab === 'entries'" :rows="entryRows" prefix="E" row-key="rk" unit="筆" export-name="聯絡資訊_已登錄" empty="還沒有登錄任何聯絡資訊"
               :cols="[{ k: 'level_label', label: '層級' }, { k: 'target', label: '對象' }, { k: 'vendor', label: '廠商' }, { k: 'phone', label: '維護電話', mono: true }, { k: 'contact', label: '窗口' },
                       { k: 'contract_no', label: '合約編號' }, { k: 'contract_end', label: '合約迄日', mono: true }, { k: 'note', label: '備註' }, { k: 'machine_count', label: '套用（台）', mono: true },
                       { k: 'updated_by', label: '更新者' }, { k: 'updated_at', label: '更新時間', mono: true }, { k: 'act', label: '', text: () => '' }]">
        <template #cell-machine_count="{ row }"><button type="button" class="lb" @click="toggleRow(row.rk)">{{ row.machine_count }}</button></template>
        <template #cell-act="{ row }"><template v-if="canEdit"><button type="button" class="lb" @click="openForm(row.level, row.key, row)">改</button><button type="button" class="lb bad" @click="removeEntry(row)">刪</button></template></template>
      </V2Table>

      <template v-else-if="tab === 'oncall'">
        <div v-if="canEdit" class="box form">
          <label>APID<input v-model="oc.api_id" placeholder="例：N-001"></label>
          <label>值班電話<input v-model="oc.phone"></label>
          <label>備註<input v-model="oc.note"></label>
          <button type="button" class="btn" :disabled="saving || !oc.api_id" @click="saveOc">儲存</button>
        </div>
        <div v-if="canEdit" class="box">
          <b>夜間速查：自動即時檢測</b><InfoNote>開（預設）：查到主機就自動連線做即時檢測。關：夜間速查只讀資料庫，畫面上有「立即檢測」鈕可手動觸發；VMware 設備不論開關都不檢測。</InfoNote>
          <label class="inl"><input type="checkbox" :checked="nightAuto" @change="setNightAuto(($event.target as HTMLInputElement).checked)"> {{ nightAuto ? '開' : '關' }}</label>
        </div>
        <div v-if="canEdit" class="box">
          <b>匯入值班電話名冊（.xlsx／.csv）</b>
          <InfoNote>欄位：系統代號 api_id、姓名、電話、角色（值班／備援／負責人）、備註。先預覽、不會寫入；確認後才寫。同系統同角色在檔內有多筆不同內容＝衝突，整組不寫。電話只去空白、統一分隔，不改號碼；Excel 請把電話欄設成文字，開頭的 0 才不會消失。找不到的系統代號列為格式錯誤。每次匯入都留異動紀錄（只記檔名與筆數）。</InfoNote>
          <a class="lb" :href="`${cfg.public.apiBase}/api/contacts/roster/template?fmt=csv`">下載範本 CSV</a>
          <a class="lb" :href="`${cfg.public.apiBase}/api/contacts/roster/template?fmt=xlsx`">下載範本 Excel</a>
          <input type="file" accept=".xlsx,.xlsm,.csv" aria-label="值班名冊檔" :disabled="rBusy" @change="onRosterFile">
          <template v-if="rPv">
            <div class="bar">
              <button type="button" class="chip" :class="{ on: !rFilter }" @click="rFilter = ''"><b>{{ rPv.row_count }}</b> 列</button>
              <button v-for="k in ['new', 'update', 'unchanged', 'conflict', 'error']" :key="k" type="button" class="chip" :class="{ on: rFilter === k }" @click="rFilter = k"><b>{{ rPv.counts[k] }}</b> {{ R_LABEL[k] }}</button>
              <button type="button" class="btn" :disabled="rBusy || !(rPv.counts.new + rPv.counts.update)" @click="doRosterImport">確認匯入</button>
            </div>
            <V2Table :rows="rRows" prefix="M" row-key="__no" unit="列" export-name="值班名冊_預覽" empty="沒有符合的列"
                     :cols="[{ k: 'row_no', label: '檔內列', mono: true }, { k: 'status_label', label: '判定', tone: (r: any) => (r.status === 'error' || r.status === 'conflict' ? 'prod' : r.status === 'update' ? 'test' : '') },
                             { k: 'api_id', label: '系統代號', mono: true }, { k: 'role', label: '角色' }, { k: 'name', label: '姓名' }, { k: 'phone', label: '電話', mono: true }, { k: 'note', label: '備註' },
                             { k: 'change', label: '電話改前→改後', mono: true }, { k: 'reason', label: '原因' }]" />
          </template>
        </div>
        <V2Table :rows="ocRows" prefix="O" row-key="api_id" unit="個系統" export-name="值班電話" empty="還沒有登錄值班電話"
                 :cols="[{ k: 'api_id', label: 'APID', mono: true, link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'name', label: '系統' }, { k: 'phone', label: '值班電話', mono: true },
                         { k: 'note', label: '備註' }, { k: 'roles_text', label: '值班姓名／備援／負責人' }, { k: 'updated_by', label: '更新者' }, { k: 'updated_at', label: '更新時間', mono: true }, { k: 'act', label: '', text: () => '' }]">
          <template #cell-act="{ row }"><button v-if="canEdit" type="button" class="lb" @click="oc.api_id = row.api_id; oc.phone = row.phone || ''; oc.note = row.note || ''">改</button></template>
        </V2Table>
      </template>

      <template v-else-if="tab === 'import'">
        <div v-if="canEdit" class="box">
          <b>上傳合約檔（.xlsx／.csv）</b>
          <InfoNote>先預覽、不會存。比對順序：序號（硬體序號／資產序號）→ 主機名 → 型號；序號或主機名對到不只一台＝多筆，不猜。只有型號的列套到該型號全部實體機（型號層級）。存檔取代目前生效的那份，舊的留在歷史；欄位對應會記住，下次同表頭自動帶。</InfoNote>
          <input type="file" accept=".xlsx,.xlsm,.csv" aria-label="合約檔" :disabled="busy" @change="onFile">
          <template v-if="pv">
            <div class="map">
              <label v-for="f in pv.fields" :key="f.key">{{ f.label }}<select v-model="mapping[f.key]"><option value="">（不用）</option><option v-for="h in pv.headers" :key="h" :value="h">{{ h }}</option></select></label>
            </div>
            <div class="bar">
              <button type="button" class="btn2" :disabled="busy" @click="doPreview(true)">{{ busy ? '處理中…' : '套用對應、重新預覽' }}</button>
              <button type="button" class="btn" :disabled="busy || pv.problems.length > 0" @click="doImport">存檔（取代目前的）</button>
              <span class="muted">對應來源：{{ pv.mapping_from === 'saved' ? '上次記住的' : pv.mapping_from === 'user' ? '手動' : '自動建議' }}</span>
            </div>
            <p v-for="p in pv.problems" :key="p" class="err">{{ p }}</p>
            <div class="bar">
              <button type="button" class="chip" :class="{ on: !pvFilter }" @click="pvFilter = ''"><b>{{ pv.row_count }}</b> 列</button>
              <button v-for="s in ['matched', 'unmatched', 'ambiguous']" :key="s" type="button" class="chip" :class="{ on: pvFilter === s }" @click="pvFilter = s"><b>{{ pv.counts[s] }}</b> {{ STATUS_LABEL[s] }}</button>
              <span class="muted">型號層級 {{ pv.by_model }} 列・迄日讀不懂 {{ pv.bad_end }} 列</span>
            </div>
            <V2Table :rows="pvRows" prefix="R" row-key="rk" unit="列" export-name="合約匯入_預覽" empty="沒有符合的列" :cols="C_COLS">
              <template #cell-machine_count="{ row }"><button type="button" class="lb" @click="toggleRow(row.rk)">{{ row.machine_count }}</button></template>
            </V2Table>
          </template>
        </div>
        <h3 class="h3">目前生效<span v-if="contracts?.current" class="muted">{{ contracts.current.filename }}・{{ contracts.current.imported_by }}・{{ contracts.current.imported_at }}</span></h3>
        <p v-if="!contracts?.current" class="muted">還沒有匯入過合約檔（聯絡資訊可以全部手動補）。</p>
        <template v-else>
          <div class="bar">
            <button type="button" class="chip" :class="{ on: !curFilter }" @click="setQ({ kf: undefined, api: undefined })"><b>{{ contracts.current.row_count }}</b> 列</button>
            <button v-for="s in ['matched', 'unmatched', 'ambiguous']" :key="s" type="button" class="chip" :class="{ on: curFilter === s }" @click="setQ({ kf: s, api: undefined })"><b>{{ curCounts[s] }}</b> {{ STATUS_LABEL[s] }}</button>
          </div>
          <V2Table :rows="curRows" prefix="K" row-key="rk" unit="列" export-name="合約匯入_目前生效" empty="沒有符合的列" :cols="C_COLS">
            <template #cell-machine_count="{ row }"><button type="button" class="lb" @click="toggleRow(row.rk)">{{ row.machine_count }}</button></template>
          </V2Table>
          <h3 class="h3">匯入歷史</h3>
          <V2Table :rows="histRows" prefix="I" row-key="__no" unit="次" export-name="合約匯入_歷史" empty="沒有匯入紀錄"
                   :cols="[{ k: 'filename', label: '檔名' }, { k: 'imported_by', label: '匯入者' }, { k: 'imported_at', label: '時間', mono: true }, { k: 'row_count', label: '列數', mono: true },
                           { k: 'matched', label: '對到', mono: true }, { k: 'unmatched', label: '對不上', mono: true }, { k: 'ambiguous', label: '多筆', mono: true }, { k: 'cur', label: '生效' }]" />
        </template>
      </template>

      <V2Table v-else-if="tab === 'audit'" :rows="auditRows" prefix="A" row-key="__no" unit="筆" export-name="聯絡資訊_異動紀錄" empty="沒有異動"
               :cols="[{ k: 'changed_at', label: '時間', mono: true }, { k: 'changed_by', label: '誰' }, { k: 'kind_label', label: '類別' }, { k: 'level', label: '層級' }, { k: 'key', label: '對象' },
                       { k: 'before', label: '改前' }, { k: 'after', label: '改後' }]" />

      <div v-if="openMachines" class="box" data-testid="contact-machines">
        <h3 class="h3">{{ openMachines.title }}：{{ openMachines.rows.length }} 台<button type="button" class="lb" @click="setQ({ cx: undefined })">關閉</button></h3>
        <V2Table :rows="openMachines.rows" prefix="H" row-key="key" unit="台" :export-name="`聯絡資訊_${openMachines.title}`" empty="目前沒有現役機器套到這筆" :cols="MACH_COLS" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; background: var(--card); font-family: inherit; font-size: 12px; cursor: pointer; min-width: 110px; }
.card b { font-family: var(--disp); font-size: 20px; }
.good { color: var(--brand-dark); } .warn { color: var(--warn-text); } .bad { color: var(--bad); }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; min-width: 240px; border: 1px solid var(--border-strong); border-radius: 8px; }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; margin-right: 8px; }
.lb.bad { color: var(--bad); }
.box { margin: 10px 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
.box.form { flex-direction: row; flex-wrap: wrap; align-items: flex-end; }
.box label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--ink-aux); }
.box label.inl { flex-direction: row; align-items: center; gap: 6px; }
.box input:not([type=checkbox]):not([type=file]), .box select { font-family: inherit; font-size: 13px; padding: 4px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.map { display: flex; flex-wrap: wrap; gap: 8px; }
.h3 { margin: 14px 0 6px; font-size: 15px; display: flex; gap: 8px; align-items: baseline; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
