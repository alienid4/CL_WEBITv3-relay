<script setup lang="ts">
// 4-8 聯絡資訊（v1.499.0）：沒有合約清冊，先手動慢慢補（使用者 2026-10-23）。
// 三層：型號（套到該型號全部機器）／系統（APID）／本機；越具體越優先。合約匯入是次要路徑。
// 待補清單：還沒有廠商／維護電話／合約迄日的機器，依系統、依型號分組，台數多的在前。
const { apiFetch } = useApi()
const { showToast } = useToast()
const { exportTable, busy: exporting } = useTableExport()
const cfg = useRuntimeConfig()
const route = useRoute()
const router = useRouter()

type Tab = 'todo' | 'entries' | 'oncall' | 'import' | 'audit'
const TABS: { k: Tab; label: string }[] = [
  { k: 'todo', label: '待補' }, { k: 'entries', label: '已登錄' }, { k: 'oncall', label: '值班電話' },
  { k: 'import', label: '合約匯入' }, { k: 'audit', label: '異動紀錄' },
]
const tab = computed<Tab>({
  get: () => (TABS.some((t) => t.k === route.query.tab) ? route.query.tab as Tab : 'todo'),
  set: (v) => { router.replace({ query: { tab: v } }) },
})

const todo = ref<any>(null)
const entries = ref<any[]>([])
const oncall = ref<any[]>([])
const contracts = ref<any>(null)
const audit = ref<any[]>([])
const canEdit = ref(false)
const err = ref('')

async function loadAll() {
  err.value = ''
  try {
    const [t, e, o, c, a] = await Promise.all([
      apiFetch<any>('/api/contacts/todo'), apiFetch<any>('/api/contacts/entries'),
      apiFetch<any>('/api/contacts/oncall'), apiFetch<any>('/api/contracts'),
      apiFetch<any>('/api/contacts/audit'),
    ])
    todo.value = t
    entries.value = e.items
    canEdit.value = e.can_edit
    oncall.value = o.items
    contracts.value = c
    audit.value = a.items
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}
await loadAll()

const q = ref(typeof route.query.api === 'string' ? route.query.api : '')
const open = ref<Set<string>>(new Set())
function toggleOpen(k: string) {
  const n = new Set(open.value)
  n.has(k) ? n.delete(k) : n.add(k)
  open.value = n
}
function match(r: any) {
  const s = q.value.trim().toLowerCase()
  return !s || JSON.stringify(r).toLowerCase().includes(s)
}

// ---- 待補
const todoBy = ref<'system' | 'model'>('system')
const todoRows = computed(() => {
  const t = todo.value
  if (!t) return []
  return todoBy.value === 'system'
    ? t.systems.map((r: any, i: number) => ({ ...r, no: `T${i + 1}`, _i: i, target: r.api_id }))
    : t.models.map((r: any, i: number) => ({ ...r, no: `M${i + 1}`, _i: i, target: r.model }))
})
const todoF = computed(() => todoRows.value.filter(match))
const { sortKey: tKey, sortDir: tDir, toggle: tToggle, sorted: todoSorted } = useSort(todoF, '')

// ---- 新增／修改（三層共用一個表單）
const FIELDS = [['vendor', '廠商'], ['phone', '維護電話'], ['contact', '窗口'], ['contract_no', '合約編號'],
  ['contract_end', '合約迄日'], ['note', '備註']] as const
const form = reactive<Record<string, string>>({ level: 'system', key: '' })
const formOpen = ref(false)
const saving = ref(false)
function openForm(level: string, key: string, cur?: any) {
  form.level = level
  form.key = key
  for (const [k] of FIELDS) form[k] = cur?.[k] ?? ''
  formOpen.value = true
}
function fillFromTodo(r: any) {
  const lvl = todoBy.value === 'system' ? 'system' : 'model'
  const existing = entries.value.find((e) => e.level === lvl && e.key === r.target)
  openForm(lvl, r.target, existing)
}
async function saveForm() {
  saving.value = true
  try {
    const body: Record<string, string> = {}
    for (const k of ['level', 'key', ...FIELDS.map((f) => f[0])]) body[k] = form[k] ?? ''
    await apiFetch('/api/contacts/entry', { method: 'PUT', body })
    showToast('已儲存', 'success')
    formOpen.value = false
    await loadAll()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '儲存失敗', 'error')
  } finally {
    saving.value = false
  }
}
async function removeEntry(e: any) {
  if (!confirm(`刪除「${e.level_label}：${e.key}」這筆聯絡資訊？`)) return
  try {
    await apiFetch('/api/contacts/entry', { method: 'PUT', body: { level: e.level, key: e.key } })
    showToast('已刪除', 'success')
    await loadAll()
  } catch (er: any) {
    showToast(er?.data?.detail ?? '刪除失敗', 'error')
  }
}

// ---- 已登錄
const entryRows = computed(() => entries.value.map((r, i) => ({ ...r, no: `E${i + 1}`, _i: i })).filter(match))
const { sortKey: eKey, sortDir: eDir, toggle: eToggle, sorted: entrySorted } = useSort(entryRows, '')

// ---- 值班電話
const ocRows = computed(() => oncall.value.map((r, i) => ({ ...r, no: `O${i + 1}`, _i: i })).filter(match))
const { sortKey: oKey, sortDir: oDir, toggle: oToggle, sorted: ocSorted } = useSort(ocRows, '')
const oc = reactive({ api_id: '', phone: '', note: '' })
async function saveOc() {
  saving.value = true
  try {
    await apiFetch('/api/contacts/oncall', { method: 'PUT', body: { ...oc } })
    showToast('值班電話已儲存', 'success')
    oc.api_id = ''; oc.phone = ''; oc.note = ''
    await loadAll()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '儲存失敗', 'error')
  } finally {
    saving.value = false
  }
}

// ---- 合約匯入
const file = ref<File | null>(null)
const pv = ref<any>(null)
const mapping = reactive<Record<string, string>>({})
const pvFilter = ref<string>('all')
const busy = ref(false)
async function postFile(path: string, withMapping: boolean) {
  const fd = new FormData()
  fd.append('file', file.value!)
  if (withMapping) {
    const m: Record<string, string> = {}
    for (const [k, v] of Object.entries(mapping)) if (v) m[k] = v
    fd.append('mapping', JSON.stringify(m))
  }
  const res = await fetch(`${cfg.public.apiBase}${path}`, { method: 'POST', body: fd, credentials: 'include' })
  const body = await res.json().catch(() => ({})) // 回應不是 JSON（例如閘道錯誤頁）：下一行照狀態碼報錯，不會被當成成功
  if (!res.ok) throw new Error(body?.detail ?? `${res.status}`)
  return body
}
async function onFile(ev: Event) {
  file.value = (ev.target as HTMLInputElement).files?.[0] ?? null
  pv.value = null
  if (file.value) await doPreview(false)
}
async function doPreview(withMapping = true) {
  if (!file.value) return
  busy.value = true
  try {
    pv.value = await postFile('/api/contracts/preview', withMapping)
    for (const k of Object.keys(mapping)) delete mapping[k]
    Object.assign(mapping, pv.value.mapping)
    pvFilter.value = 'all'
  } catch (e: any) {
    showToast(e?.message ?? '預覽失敗', 'error')
  } finally {
    busy.value = false
  }
}
async function doImport() {
  if (!pv.value || !confirm(`存檔會取代目前生效的合約（舊的留在歷史）。共 ${pv.value.row_count} 列，確定？`)) return
  busy.value = true
  try {
    const r = await postFile('/api/contracts/import', true)
    showToast(`已匯入 ${r.row_count} 列`, 'success')
    pv.value = null
    file.value = null
    await loadAll()
  } catch (e: any) {
    showToast(e?.message ?? '匯入失敗', 'error')
  } finally {
    busy.value = false
  }
}
const STATUS_LABEL: Record<string, string> = { matched: '對到', unmatched: '對不上', ambiguous: '多筆（不猜）' }
const BY_LABEL: Record<string, string> = { serial: '序號', hostname: '主機名', model: '型號（全部同型號）' }
function contractRowsOf(rows: any[], prefix: string) {
  return rows.map((r, i) => ({ ...r, no: `${prefix}${i + 1}`, _i: i, status_label: STATUS_LABEL[r.match_status],
    by_label: r.match_by ? BY_LABEL[r.match_by] : '—', machine_count: (r.machines || []).length }))
}
const pvRows = computed(() => (pv.value ? contractRowsOf(pv.value.rows, 'R') : [])
  .filter((r) => pvFilter.value === 'all' || r.match_status === pvFilter.value).filter(match))
const { sortKey: pKey, sortDir: pDir, toggle: pToggle, sorted: pvSorted } = useSort(pvRows, '')
const curFilter = ref('all')
const curRows = computed(() => (contracts.value ? contractRowsOf(contracts.value.rows, 'K') : [])
  .filter((r) => curFilter.value === 'all' || r.match_status === curFilter.value).filter(match))
const { sortKey: kKey, sortDir: kDir, toggle: kToggle, sorted: curSorted } = useSort(curRows, '')
const histRows = computed(() => (contracts.value?.history ?? []).map((r: any, i: number) => ({ ...r, no: `I${i + 1}`, _i: i })))
const { sortKey: hKey, sortDir: hDir, toggle: hToggle, sorted: histSorted } = useSort(histRows, '')
const curCounts = computed(() => {
  const c: Record<string, number> = { matched: 0, unmatched: 0, ambiguous: 0 }
  for (const r of contracts.value?.rows ?? []) c[r.match_status] = (c[r.match_status] || 0) + 1
  return c
})
const C_COLS = [['serial', '序號'], ['asset_code', '資產編號'], ['hostname', '主機名'], ['model', '型號'],
  ['vendor', '廠商'], ['contract_no', '合約編號'], ['end_date', '迄日'], ['phone', '維護電話'], ['contact', '窗口'],
  ['status_label', '比對'], ['by_label', '依據'], ['machine_count', '對到（台）']] as const

// ---- 異動紀錄
const KIND: Record<string, string> = { contact: '聯絡資訊', oncall: '值班電話', contract_import: '合約匯入' }
function brief(j: string | null) {
  if (!j) return '—'
  try {
    const o = JSON.parse(j)
    return ['vendor', 'phone', 'contact', 'contract_no', 'contract_end', 'note', 'filename', 'rows', 'matched', 'unmatched', 'ambiguous']
      .filter((k) => o[k] !== null && o[k] !== undefined && o[k] !== '').map((k) => `${k}=${o[k]}`).join('　') || '—'
  } catch { return j } // 舊格式就原樣顯示，不是錯誤
}
const auditRows = computed(() => audit.value.map((r, i) => ({ ...r, no: `A${i + 1}`, _i: i, kind_label: KIND[r.kind] ?? r.kind,
  before: brief(r.before_json), after: brief(r.after_json) })).filter(match))
const { sortKey: aKey, sortDir: aDir, toggle: aToggle, sorted: auditSorted } = useSort(auditRows, '')

function exp(title: string, cols: [string, string][], rows: any[]) {
  exportTable(title, ['項次', ...cols.map((c) => c[1])], rows.map((r) => [r.no, ...cols.map((c) => r[c[0]] ?? '')]))
}
const MACH = [['hostname', '主機名'], ['ip', 'IP'], ['model', '型號'], ['room', '機房'], ['rack', '機櫃'], ['systems', '系統']] as const
function expTodoMachines(r: any) {
  exportTable(`待補_${r.target}`, ['#', ...MACH.map((c) => c[1])],
    r.machines.map((m: any, i: number) => [i + 1, ...MACH.map((c) => m[c[0]] ?? '')]))
}
useHead({ title: '聯絡資訊' })
</script>

<template>
  <div class="wrap">
    <div class="ey">WAR ROOM · 4-8</div>
    <h1>聯絡資訊
      <InfoNote>
        三層：型號（套到該型號全部機器）／系統（APID）／本機；同一欄越具體的贏，畫面會標來源。
        每台機器取值順序：本機（手動）→ 合約匯入（序號／主機名）→ 系統（手動）→ 型號（手動）→ 合約匯入（型號）。<br>
        待補＝帳內（CIA）現役機器還沒有廠商、維護電話、合約迄日任一項。依系統含 VM，一台掛多個 APID 時各系統都算一次；
        依型號只算實體機，各組加總＝缺資料實體機台數。<br>
        修改限管理員，每次異動記在「異動紀錄」。
      </InfoNote>
    </h1>
    <p v-if="err" class="bad">載入失敗：{{ err }}</p>

    <div v-if="todo" class="cards">
      <button class="card warn" @click="tab = 'todo'"><span class="n">{{ todo.missing_count }}</span><span class="l">待補（台）</span></button>
      <button class="card" @click="tab = 'todo'; todoBy = 'system'"><span class="n">{{ todo.systems.length }}</span><span class="l">待補系統</span></button>
      <button class="card" @click="tab = 'todo'; todoBy = 'model'"><span class="n">{{ todo.models.length }}</span><span class="l">待補型號</span></button>
      <button class="card ok" @click="tab = 'entries'"><span class="n">{{ entries.length }}</span><span class="l">已登錄（筆）</span></button>
      <button class="card ok" @click="tab = 'oncall'"><span class="n">{{ oncall.length }}</span><span class="l">值班電話（系統）</span></button>
    </div>

    <div class="tabs">
      <button v-for="t in TABS" :key="t.k" class="tb" :class="{ on: tab === t.k }" @click="tab = t.k">{{ t.label }}</button>
    </div>
    <div class="bar">
      <input v-model="q" class="kw" placeholder="搜項次、APID、型號、主機名…">
      <button v-if="canEdit && (tab === 'todo' || tab === 'entries')" class="btn pri" @click="openForm('system', '')">＋ 新增</button>
    </div>

    <div v-if="formOpen" class="formbox">
      <label>層級
        <select v-model="form.level"><option value="model">型號</option><option value="system">系統（APID）</option><option value="asset">本機（資產序號）</option></select>
      </label>
      <label>對象<input v-model="form.key" :placeholder="form.level === 'model' ? '例：DELL R750' : form.level === 'system' ? '例：N-001' : '資產序號'"></label>
      <label v-for="[k, lbl] in FIELDS" :key="k">{{ lbl }}<input v-model="form[k]" :placeholder="k === 'contract_end' ? 'YYYY-MM-DD' : ''"></label>
      <div class="acts">
        <button class="btn pri" :disabled="saving" @click="saveForm">{{ saving ? '儲存中…' : '儲存' }}</button>
        <button class="btn" @click="formOpen = false">取消</button>
      </div>
    </div>

    <!-- 待補 -->
    <template v-if="tab === 'todo' && todo">
      <div class="subs">
        <button class="chip" :class="{ on: todoBy === 'system' }" @click="todoBy = 'system'"><b>{{ todo.systems.length }}</b> 依系統</button>
        <button class="chip" :class="{ on: todoBy === 'model' }" @click="todoBy = 'model'"><b>{{ todo.models.length }}</b> 依型號（實體機 {{ todo.physical_missing }} 台）</button>
        <button class="btn" :disabled="exporting" @click="exp(`待補_${todoBy === 'system' ? '依系統' : '依型號'}`, todoBy === 'system' ? [['api_id', 'APID'], ['name', '系統'], ['count', '缺資料（台）']] : [['model', '型號'], ['count', '缺資料（台）']], todoSorted)">匯出 Excel</button>
      </div>
      <div class="tbl-wrap"><table>
        <thead><tr>
          <SortTh k="_i" :active="tKey" :dir="tDir" @sort="tToggle">項次</SortTh>
          <template v-if="todoBy === 'system'">
            <SortTh k="api_id" :active="tKey" :dir="tDir" @sort="tToggle">APID</SortTh>
            <SortTh k="name" :active="tKey" :dir="tDir" @sort="tToggle">系統</SortTh>
          </template>
          <SortTh v-else k="model" :active="tKey" :dir="tDir" @sort="tToggle">型號</SortTh>
          <SortTh k="count" :active="tKey" :dir="tDir" @sort="tToggle">缺資料（台）</SortTh>
          <th v-if="canEdit" />
        </tr></thead>
        <tbody>
          <tr v-if="!todoSorted.length"><td colspan="5" class="muted">沒有待補</td></tr>
          <template v-for="r in todoSorted" :key="r.no">
            <tr class="click" @click="toggleOpen(r.no)">
              <td class="mono">{{ r.no }}</td>
              <template v-if="todoBy === 'system'"><td class="mono">{{ r.api_id }}</td><td>{{ r.name || '—' }}</td></template>
              <td v-else>{{ r.model }}</td>
              <td class="mono num">{{ r.count }}</td>
              <td v-if="canEdit"><button v-if="!r.target.startsWith('（')" class="lnk" @click.stop="fillFromTodo(r)">補資料</button></td>
            </tr>
            <tr v-if="open.has(r.no)" class="det"><td colspan="5">
              <button class="lnk" @click="expTodoMachines(r)">匯出這 {{ r.count }} 台</button>
              <table class="inner">
                <tr><th>#</th><th v-for="c in MACH" :key="c[0]">{{ c[1] }}</th></tr>
                <tr v-for="(m, i) in r.machines" :key="m.key">
                  <td>{{ i + 1 }}</td>
                  <td><NuxtLink :to="`/assets/${encodeURIComponent(m.serial)}`">{{ m.hostname || m.serial }}</NuxtLink></td>
                  <td class="mono">{{ m.ip || '—' }}</td><td>{{ m.model || '—' }}</td><td>{{ m.room }}</td><td>{{ m.rack || '—' }}</td><td>{{ m.systems || '—' }}</td>
                </tr>
              </table>
            </td></tr>
          </template>
        </tbody>
      </table></div>
    </template>

    <!-- 已登錄 -->
    <template v-else-if="tab === 'entries'">
      <div class="subs"><button class="btn" :disabled="exporting" @click="exp('聯絡資訊_已登錄', [['level_label', '層級'], ['key', '對象'], ['target_name', '名稱'], ['vendor', '廠商'], ['phone', '維護電話'], ['contact', '窗口'], ['contract_no', '合約編號'], ['contract_end', '合約迄日'], ['note', '備註'], ['machine_count', '套用（台）'], ['updated_by', '更新者'], ['updated_at', '更新時間']], entrySorted)">匯出 Excel</button></div>
      <div class="tbl-wrap"><table>
        <thead><tr>
          <SortTh k="_i" :active="eKey" :dir="eDir" @sort="eToggle">項次</SortTh>
          <SortTh k="level_label" :active="eKey" :dir="eDir" @sort="eToggle">層級</SortTh>
          <SortTh k="key" :active="eKey" :dir="eDir" @sort="eToggle">對象</SortTh>
          <SortTh k="vendor" :active="eKey" :dir="eDir" @sort="eToggle">廠商</SortTh>
          <SortTh k="phone" :active="eKey" :dir="eDir" @sort="eToggle">維護電話</SortTh>
          <SortTh k="contact" :active="eKey" :dir="eDir" @sort="eToggle">窗口</SortTh>
          <SortTh k="contract_no" :active="eKey" :dir="eDir" @sort="eToggle">合約編號</SortTh>
          <SortTh k="contract_end" :active="eKey" :dir="eDir" @sort="eToggle">合約迄日</SortTh>
          <SortTh k="machine_count" :active="eKey" :dir="eDir" @sort="eToggle">套用（台）</SortTh>
          <SortTh k="updated_at" :active="eKey" :dir="eDir" @sort="eToggle">更新</SortTh>
          <th v-if="canEdit" />
        </tr></thead>
        <tbody>
          <tr v-if="!entrySorted.length"><td colspan="11" class="muted">還沒有登錄任何聯絡資訊</td></tr>
          <template v-for="r in entrySorted" :key="r.no">
            <tr class="click" @click="toggleOpen(r.no)">
              <td class="mono">{{ r.no }}</td><td>{{ r.level_label }}</td>
              <td>{{ r.key }}<span v-if="r.target_name" class="muted"> {{ r.target_name }}</span></td>
              <td>{{ r.vendor || '—' }}</td><td class="mono">{{ r.phone || '—' }}</td><td>{{ r.contact || '—' }}</td>
              <td>{{ r.contract_no || '—' }}</td><td class="mono">{{ r.contract_end || '—' }}</td>
              <td class="mono num">{{ r.machine_count }}</td>
              <td class="small">{{ r.updated_by }}<br><span class="mono">{{ r.updated_at }}</span></td>
              <td v-if="canEdit" class="nowrap">
                <button class="lnk" @click.stop="openForm(r.level, r.key, r)">改</button>
                <button class="lnk bad" @click.stop="removeEntry(r)">刪</button>
              </td>
            </tr>
            <tr v-if="open.has(r.no)" class="det"><td colspan="11">
              <table class="inner">
                <tr><th>#</th><th v-for="c in MACH" :key="c[0]">{{ c[1] }}</th></tr>
                <tr v-for="(m, i) in r.machines" :key="m.key">
                  <td>{{ i + 1 }}</td>
                  <td><NuxtLink :to="`/assets/${encodeURIComponent(m.serial)}`">{{ m.hostname || m.serial }}</NuxtLink></td>
                  <td class="mono">{{ m.ip || '—' }}</td><td>{{ m.model || '—' }}</td><td>{{ m.room }}</td><td>{{ m.rack || '—' }}</td><td>{{ m.systems || '—' }}</td>
                </tr>
                <tr v-if="!r.machines.length"><td colspan="7" class="muted">目前沒有現役機器套到這筆</td></tr>
              </table>
            </td></tr>
          </template>
        </tbody>
      </table></div>
    </template>

    <!-- 值班電話 -->
    <template v-else-if="tab === 'oncall'">
      <div v-if="canEdit" class="formbox">
        <label>APID<input v-model="oc.api_id" placeholder="例：N-001"></label>
        <label>值班電話<input v-model="oc.phone"></label>
        <label>備註<input v-model="oc.note"></label>
        <div class="acts"><button class="btn pri" :disabled="saving || !oc.api_id" @click="saveOc">儲存</button></div>
      </div>
      <div class="subs"><button class="btn" :disabled="exporting" @click="exp('值班電話', [['api_id', 'APID'], ['name', '系統'], ['phone', '值班電話'], ['note', '備註'], ['updated_by', '更新者'], ['updated_at', '更新時間']], ocSorted)">匯出 Excel</button></div>
      <div class="tbl-wrap"><table>
        <thead><tr>
          <SortTh k="_i" :active="oKey" :dir="oDir" @sort="oToggle">項次</SortTh>
          <SortTh k="api_id" :active="oKey" :dir="oDir" @sort="oToggle">APID</SortTh>
          <SortTh k="name" :active="oKey" :dir="oDir" @sort="oToggle">系統</SortTh>
          <SortTh k="phone" :active="oKey" :dir="oDir" @sort="oToggle">值班電話</SortTh>
          <SortTh k="note" :active="oKey" :dir="oDir" @sort="oToggle">備註</SortTh>
          <SortTh k="updated_at" :active="oKey" :dir="oDir" @sort="oToggle">更新</SortTh>
          <th v-if="canEdit" />
        </tr></thead>
        <tbody>
          <tr v-if="!ocSorted.length"><td colspan="7" class="muted">還沒有登錄值班電話</td></tr>
          <tr v-for="r in ocSorted" :key="r.no">
            <td class="mono">{{ r.no }}</td>
            <td class="mono"><NuxtLink :to="`/topology?system=${encodeURIComponent(r.api_id)}`">{{ r.api_id }}</NuxtLink></td>
            <td>{{ r.name || '—' }}</td><td class="mono">{{ r.phone || '—' }}</td><td>{{ r.note || '—' }}</td>
            <td class="small">{{ r.updated_by }}<br><span class="mono">{{ r.updated_at }}</span></td>
            <td v-if="canEdit"><button class="lnk" @click="oc.api_id = r.api_id; oc.phone = r.phone || ''; oc.note = r.note || ''">改</button></td>
          </tr>
        </tbody>
      </table></div>
    </template>

    <!-- 合約匯入 -->
    <template v-else-if="tab === 'import'">
      <div v-if="canEdit" class="formbox col">
        <div>
          <b>上傳合約檔（.xlsx／.csv）</b>
          <InfoNote>先預覽、不會存。比對順序：序號（硬體序號／資產序號）→ 主機名 → 型號；序號或主機名對到不只一台＝多筆，不猜。
            只有型號的列套到該型號全部實體機（型號層級）。存檔取代目前生效的那份，舊的留在歷史；欄位對應會記住，下次同表頭自動帶。</InfoNote>
        </div>
        <input type="file" accept=".xlsx,.xlsm,.csv" @change="onFile">
        <template v-if="pv">
          <div class="mapgrid">
            <label v-for="f in pv.fields" :key="f.key">{{ f.label }}
              <select v-model="mapping[f.key]"><option value="">（不用）</option><option v-for="h in pv.headers" :key="h" :value="h">{{ h }}</option></select>
            </label>
          </div>
          <div class="acts">
            <button class="btn" :disabled="busy" @click="doPreview(true)">{{ busy ? '處理中…' : '套用對應、重新預覽' }}</button>
            <button class="btn pri" :disabled="busy || pv.problems.length > 0" @click="doImport">存檔（取代目前的）</button>
            <span class="muted small">對應來源：{{ pv.mapping_from === 'saved' ? '上次記住的' : pv.mapping_from === 'user' ? '手動' : '自動建議' }}</span>
          </div>
          <p v-for="p in pv.problems" :key="p" class="bad">{{ p }}</p>
          <div class="subs">
            <button class="chip" :class="{ on: pvFilter === 'all' }" @click="pvFilter = 'all'"><b>{{ pv.row_count }}</b> 列</button>
            <button v-for="s in ['matched', 'unmatched', 'ambiguous']" :key="s" class="chip" :class="{ on: pvFilter === s }" @click="pvFilter = s">
              <b>{{ pv.counts[s] }}</b> {{ STATUS_LABEL[s] }}</button>
            <span class="muted small">型號層級 {{ pv.by_model }} 列・迄日讀不懂 {{ pv.bad_end }} 列</span>
          </div>
          <div class="tbl-wrap"><table>
            <thead><tr>
              <SortTh k="_i" :active="pKey" :dir="pDir" @sort="pToggle">項次</SortTh>
              <SortTh v-for="c in C_COLS" :key="c[0]" :k="c[0]" :active="pKey" :dir="pDir" @sort="pToggle">{{ c[1] }}</SortTh>
            </tr></thead>
            <tbody>
              <template v-for="r in pvSorted" :key="r.no">
                <tr class="click" @click="toggleOpen(r.no)">
                  <td class="mono">{{ r.no }}</td>
                  <td v-for="c in C_COLS" :key="c[0]" :class="{ bad: c[0] === 'end_date' && r.bad_end, warnt: c[0] === 'status_label' && r.match_status !== 'matched' }">
                    {{ c[0] === 'end_date' && r.bad_end ? `看不懂：${r.end_date_raw}` : (r[c[0]] ?? '—') }}</td>
                </tr>
                <tr v-if="open.has(r.no)" class="det"><td :colspan="C_COLS.length + 1">
                  <table class="inner">
                    <tr><th>#</th><th v-for="c in MACH" :key="c[0]">{{ c[1] }}</th></tr>
                    <tr v-for="(m, i) in r.machines" :key="m.key"><td>{{ i + 1 }}</td><td>{{ m.hostname || m.serial }}</td><td class="mono">{{ m.ip || '—' }}</td><td>{{ m.model || '—' }}</td><td>{{ m.room }}</td><td>{{ m.rack || '—' }}</td><td>{{ m.systems || '—' }}</td></tr>
                    <tr v-if="!r.machines.length"><td colspan="7" class="muted">對不到任何機器</td></tr>
                  </table>
                </td></tr>
              </template>
            </tbody>
          </table></div>
        </template>
      </div>

      <h3>目前生效
        <span v-if="contracts?.current" class="muted small">{{ contracts.current.filename }}・{{ contracts.current.imported_by }}・{{ contracts.current.imported_at }}</span>
      </h3>
      <p v-if="!contracts?.current" class="muted">還沒有匯入過合約檔（聯絡資訊可以全部手動補）。</p>
      <template v-else>
        <div class="subs">
          <button class="chip" :class="{ on: curFilter === 'all' }" @click="curFilter = 'all'"><b>{{ contracts.current.row_count }}</b> 列</button>
          <button v-for="s in ['matched', 'unmatched', 'ambiguous']" :key="s" class="chip" :class="{ on: curFilter === s }" @click="curFilter = s">
            <b>{{ curCounts[s] }}</b> {{ STATUS_LABEL[s] }}</button>
          <button class="btn" :disabled="exporting" @click="exp('合約匯入_目前生效', C_COLS as any, curSorted)">匯出 Excel</button>
        </div>
        <div class="tbl-wrap"><table>
          <thead><tr>
            <SortTh k="_i" :active="kKey" :dir="kDir" @sort="kToggle">項次</SortTh>
            <SortTh v-for="c in C_COLS" :key="c[0]" :k="c[0]" :active="kKey" :dir="kDir" @sort="kToggle">{{ c[1] }}</SortTh>
          </tr></thead>
          <tbody>
            <template v-for="r in curSorted" :key="r.no">
              <tr class="click" @click="toggleOpen(r.no)">
                <td class="mono">{{ r.no }}</td>
                <td v-for="c in C_COLS" :key="c[0]" :class="{ warnt: c[0] === 'status_label' && r.match_status !== 'matched' }">{{ r[c[0]] ?? '—' }}</td>
              </tr>
              <tr v-if="open.has(r.no)" class="det"><td :colspan="C_COLS.length + 1">
                <table class="inner">
                  <tr><th>#</th><th v-for="c in MACH" :key="c[0]">{{ c[1] }}</th></tr>
                  <tr v-for="(m, i) in r.machines" :key="m.key"><td>{{ i + 1 }}</td><td><NuxtLink :to="`/assets/${encodeURIComponent(m.serial)}`">{{ m.hostname || m.serial }}</NuxtLink></td><td class="mono">{{ m.ip || '—' }}</td><td>{{ m.model || '—' }}</td><td>{{ m.room }}</td><td>{{ m.rack || '—' }}</td><td>{{ m.systems || '—' }}</td></tr>
                  <tr v-if="!r.machines.length"><td colspan="7" class="muted">對不到任何機器</td></tr>
                </table>
              </td></tr>
            </template>
          </tbody>
        </table></div>
        <h3>匯入歷史</h3>
        <div class="tbl-wrap"><table>
          <thead><tr>
            <SortTh k="_i" :active="hKey" :dir="hDir" @sort="hToggle">項次</SortTh>
            <SortTh k="filename" :active="hKey" :dir="hDir" @sort="hToggle">檔名</SortTh>
            <SortTh k="imported_by" :active="hKey" :dir="hDir" @sort="hToggle">匯入者</SortTh>
            <SortTh k="imported_at" :active="hKey" :dir="hDir" @sort="hToggle">時間</SortTh>
            <SortTh k="row_count" :active="hKey" :dir="hDir" @sort="hToggle">列數</SortTh>
            <SortTh k="matched" :active="hKey" :dir="hDir" @sort="hToggle">對到</SortTh>
            <SortTh k="unmatched" :active="hKey" :dir="hDir" @sort="hToggle">對不上</SortTh>
            <SortTh k="ambiguous" :active="hKey" :dir="hDir" @sort="hToggle">多筆</SortTh>
            <SortTh k="is_current" :active="hKey" :dir="hDir" @sort="hToggle">生效</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in histSorted" :key="r.no">
              <td class="mono">{{ r.no }}</td><td>{{ r.filename }}</td><td>{{ r.imported_by }}</td><td class="mono">{{ r.imported_at }}</td>
              <td class="num">{{ r.row_count }}</td><td class="num">{{ r.matched }}</td><td class="num">{{ r.unmatched }}</td><td class="num">{{ r.ambiguous }}</td>
              <td>{{ r.is_current ? '生效中' : '' }}</td>
            </tr>
          </tbody>
        </table></div>
      </template>
    </template>

    <!-- 異動紀錄 -->
    <template v-else-if="tab === 'audit'">
      <div class="subs"><button class="btn" :disabled="exporting" @click="exp('聯絡資訊_異動紀錄', [['changed_at', '時間'], ['changed_by', '誰'], ['kind_label', '類別'], ['level', '層級'], ['key', '對象'], ['before', '改前'], ['after', '改後']], auditSorted)">匯出 Excel</button></div>
      <div class="tbl-wrap"><table>
        <thead><tr>
          <SortTh k="_i" :active="aKey" :dir="aDir" @sort="aToggle">項次</SortTh>
          <SortTh k="changed_at" :active="aKey" :dir="aDir" @sort="aToggle">時間</SortTh>
          <SortTh k="changed_by" :active="aKey" :dir="aDir" @sort="aToggle">誰</SortTh>
          <SortTh k="kind_label" :active="aKey" :dir="aDir" @sort="aToggle">類別</SortTh>
          <SortTh k="level" :active="aKey" :dir="aDir" @sort="aToggle">層級</SortTh>
          <SortTh k="key" :active="aKey" :dir="aDir" @sort="aToggle">對象</SortTh>
          <SortTh k="before" :active="aKey" :dir="aDir" @sort="aToggle">改前</SortTh>
          <SortTh k="after" :active="aKey" :dir="aDir" @sort="aToggle">改後</SortTh>
        </tr></thead>
        <tbody>
          <tr v-if="!auditSorted.length"><td colspan="8" class="muted">沒有異動</td></tr>
          <tr v-for="r in auditSorted" :key="r.no">
            <td class="mono">{{ r.no }}</td><td class="mono">{{ r.changed_at }}</td><td>{{ r.changed_by || '—' }}</td>
            <td>{{ r.kind_label }}</td><td>{{ r.level }}</td><td>{{ r.key }}</td><td class="small">{{ r.before }}</td><td class="small">{{ r.after }}</td>
          </tr>
        </tbody>
      </table></div>
    </template>
  </div>
</template>

<style scoped>
.wrap { padding: 16px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 12px; color: var(--ink); }
h3 { font-size: 15px; margin: 18px 0 8px; color: var(--ink); }
.bad { color: var(--bad); }
.warnt { color: var(--warn-text); }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.card { display: flex; flex-direction: column; align-items: flex-start; padding: 10px 14px; min-width: 130px;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); cursor: pointer; color: inherit; font: inherit; }
.card .n { font-size: 22px; font-weight: 700; font-family: var(--disp); color: var(--ink); }
.card.warn .n { color: var(--warn-text); }
.card.ok .n { color: var(--brand); }
.card .l { font-size: 12px; color: var(--ink-aux); }
.tabs { display: flex; gap: 4px; margin: 10px 0; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tb { font: inherit; padding: 8px 14px; border: 0; background: none; cursor: pointer; color: var(--ink-aux); border-bottom: 2px solid transparent; }
.tb.on { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.subs { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.chip { font: inherit; font-size: 12.5px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; color: var(--ink-aux); }
.chip.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); }
.bar { display: flex; gap: 10px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.kw { min-width: 240px; padding: 6px 8px; border: 1px solid var(--border-strong); border-radius: 8px; font: inherit; }
.btn { font: inherit; font-size: 12.5px; padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: #fff; }
.btn:disabled { opacity: .6; }
.lnk { font: inherit; font-size: 12px; border: 0; background: none; color: var(--link); cursor: pointer; padding: 0 4px; }
.lnk.bad { color: var(--bad); }
.formbox { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; padding: 12px; margin: 8px 0 12px;
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
.formbox.col { flex-direction: column; align-items: stretch; }
.formbox label, .mapgrid label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
.formbox input, .formbox select, .mapgrid select { padding: 4px 6px; border: 1px solid var(--border-strong); border-radius: 6px; font: inherit; color: var(--ink); background: var(--card); }
.mapgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tbl-wrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
td a { color: var(--link); }
tr.click { cursor: pointer; }
tr.click:hover { background: var(--mint); }
tr.det > td { background: var(--sub); }
.inner { background: var(--card); margin-top: 4px; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.num { text-align: right; }
.small { font-size: 11.5px; }
.nowrap { white-space: nowrap; }
.muted { color: var(--muted); }
</style>
