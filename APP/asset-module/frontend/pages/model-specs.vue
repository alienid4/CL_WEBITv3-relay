<script setup lang="ts">
// 型號規格表（2026-10-22）：機櫃用電／重量／U 的依據。種子來自程式附的 model_specs_seed.json（只有型號規格
// 與出處），人工改的值另存、重新灌種子不會蓋掉。資料：/api/model-specs（model_specs.py）。改值限管理員。
interface Spec {
  model: string; vendor_model_name: string | null; rack_u: number | null; psu_max_w: number | null; psu_count: number | null
  weight_kg: number | null; plug: string | null; plug_confidence: string | null; depth_mm: number | null
  vendor_move_required: boolean | null; vendor_move_reason: string | null; confidence: string | null; is_generic: boolean
  system_level: Record<string, number | null> | null; note: string | null; aliases: string[]; sources: { field: string; url: string }[]
  manual_fields: string[]; from_seed: boolean; updated_at: string | null; updated_by: string | null
}
interface Cov { device_model: string; count: number; spec: string | null; generic?: boolean; keys: string[] }

const { apiFetch } = useApi()
const { showToast } = useToast()
const specs = ref<Spec[]>([])
const cov = ref<{ rows: Cov[]; matched: number; total: number } | null>(null)
const canEdit = ref(false)
const err = ref('')
async function load() {
  try {
    const r = await apiFetch<{ specs: Spec[]; coverage: { rows: Cov[]; matched: number; total: number }; can_edit: boolean }>('/api/model-specs')
    specs.value = r.specs
    cov.value = r.coverage
    canEdit.value = r.can_edit
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? '未知錯誤'
  }
}
await load()

type Tab = 'specs' | 'coverage'
const tab = ref<Tab>('specs')
const specRows = computed(() => specs.value.map((s, i) => ({
  ...s, no: i + 1, alias_text: s.aliases.join('、'), move_text: s.vendor_move_required ? '要' : '',
  manual_text: s.manual_fields.join(', '), plug_flag: s.plug_confidence === 'low' ? '待確認' : '',
})))
const { sortKey, sortDir, toggle, sorted } = useSort(specRows, 'no')
const covRows = computed(() => (cov.value?.rows ?? []).map((r, i) => ({ ...r, no: i + 1, status: r.spec ? (r.generic ? '籠統描述' : '已對應') : '未對應' })))
const { sortKey: cKey, sortDir: cDir, toggle: cToggle, sorted: cSorted } = useSort(covRows, 'no')

// 編輯（一次一列）
const EDIT_FIELDS: [keyof Spec, string][] = [['rack_u', 'U'], ['psu_max_w', '單顆 PSU W'], ['psu_count', 'PSU 數'], ['weight_kg', '重量 kg'],
  ['depth_mm', '深度 mm'], ['plug', '插頭'], ['confidence', '可信度'], ['note', '備註']]
const editing = ref<string | null>(null)
const form = reactive<Record<string, any>>({})
const newModel = ref('')
function startEdit(s: Spec) {
  editing.value = s.model
  for (const [k] of EDIT_FIELDS) form[k] = s[k] ?? ''
  form.aliases = s.aliases.join(', ')
  form.vendor_move_required = !!s.vendor_move_required
  form.is_generic = !!s.is_generic
}
async function save(model: string, clear: string[] = []) {
  const values: Record<string, any> = {}
  if (!clear.length) {
    for (const [k] of EDIT_FIELDS) values[k] = form[k] === '' ? null : form[k]
    values.aliases = form.aliases
    values.vendor_move_required = form.vendor_move_required
    values.is_generic = form.is_generic
  }
  try {
    await apiFetch('/api/model-specs', { method: 'PUT', body: { model, values, clear } })
    showToast(clear.length ? `「${model}」已退回種子值` : `「${model}」已更新`, 'success')
    editing.value = null
    await load()
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
async function addModel() {
  const m = newModel.value.trim()
  if (!m) return
  try {
    await apiFetch('/api/model-specs', { method: 'PUT', body: { model: m, values: { aliases: [m] }, clear: [] } })
    newModel.value = ''
    await load()
    const s = specs.value.find(x => x.model === m)
    if (s) startEdit(s)
  } catch (e: any) {
    showToast(`新增失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
function useForCoverage(r: Cov) { newModel.value = r.device_model; tab.value = 'specs' }
</script>

<template>
  <div class="mspage">
    <div class="head">
      <div class="ey">SYSTEM · 型號規格</div>
      <h1>型號規格表</h1>
    </div>
    <p v-if="err" class="err">載入失敗：{{ err }}</p>
    <template v-else>
      <div class="chips">
        <span>規格 {{ specs.length }} 筆</span>
        <span v-if="cov">機櫃裡對得到規格的設備 {{ cov.matched }}／{{ cov.total }} 台</span>
        <InfoNote>用電＝單顆 PSU 額定瓦數（200–240V AC 可用的最大顆，不是加總）。種子值來自原廠文件（出處在每列的「來源」），人工改的值另存，重新灌種子不會蓋掉；「退回種子值」會清掉人工值。插頭標「待確認」的是原廠沒寫清楚、要現場看的。工程系統（Exadata／ODA）逐筆不填用電／U／重量，同櫃達 3 筆以上時以整套數字算一次。改值限管理員。</InfoNote>
      </div>
      <div class="tabs" role="tablist">
        <button class="tab" :class="{ active: tab === 'specs' }" @click="tab = 'specs'">規格</button>
        <button class="tab" :class="{ active: tab === 'coverage' }" @click="tab = 'coverage'">機櫃型號對應 {{ cov?.rows.length ?? 0 }}</button>
      </div>

      <div v-if="tab === 'specs'" class="panel">
        <div v-if="canEdit" class="chips">
          <input v-model="newModel" class="kw" type="text" placeholder="新增型號（例：Dell R7625）">
          <button class="btn" :disabled="!newModel.trim()" @click="addModel">新增</button>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
            <SortTh k="model" :active="sortKey" :dir="sortDir" @sort="toggle">型號</SortTh>
            <SortTh k="rack_u" :active="sortKey" :dir="sortDir" @sort="toggle">U</SortTh>
            <SortTh k="psu_max_w" :active="sortKey" :dir="sortDir" @sort="toggle">單顆 PSU W</SortTh>
            <SortTh k="psu_count" :active="sortKey" :dir="sortDir" @sort="toggle">PSU 數</SortTh>
            <SortTh k="weight_kg" :active="sortKey" :dir="sortDir" @sort="toggle">kg</SortTh>
            <SortTh k="depth_mm" :active="sortKey" :dir="sortDir" @sort="toggle">深 mm</SortTh>
            <SortTh k="plug_flag" :active="sortKey" :dir="sortDir" @sort="toggle">插頭</SortTh>
            <SortTh k="move_text" :active="sortKey" :dir="sortDir" @sort="toggle">原廠搬</SortTh>
            <SortTh k="confidence" :active="sortKey" :dir="sortDir" @sort="toggle">可信度</SortTh>
            <SortTh k="manual_text" :active="sortKey" :dir="sortDir" @sort="toggle">人工改過</SortTh>
          </tr></thead>
          <tbody>
            <template v-for="s in sorted" :key="s.model">
              <tr :class="{ generic: s.is_generic }">
                <td class="mono">M{{ s.no }}</td>
                <td>
                  <button v-if="canEdit" class="linkbtn" @click="startEdit(s)">{{ s.model }}</button><span v-else>{{ s.model }}</span>
                  <span v-if="s.is_generic" class="tag">籠統描述</span>
                  <span v-if="s.system_level" class="tag">整套 {{ s.system_level.rack_u ?? '?' }}U／{{ s.system_level.weight_kg_max ?? '?' }}kg／{{ s.system_level.psu_max_w ?? '?' }}W</span>
                  <div class="muted small">{{ s.alias_text }}</div>
                </td>
                <td class="mono">{{ s.rack_u ?? '—' }}</td><td class="mono">{{ s.psu_max_w ?? '—' }}</td>
                <td class="mono">{{ s.psu_count ?? '—' }}</td><td class="mono">{{ s.weight_kg ?? '—' }}</td>
                <td class="mono">{{ s.depth_mm ?? '—' }}</td>
                <td class="small">{{ s.plug || '—' }}<span v-if="s.plug_flag" class="tag warn">待確認</span></td>
                <td>{{ s.move_text }}</td><td>{{ s.confidence || '—' }}</td><td class="small">{{ s.manual_text }}</td>
              </tr>
              <tr v-if="editing === s.model" class="edit">
                <td colspan="11">
                  <div class="form">
                    <label v-for="[k, lab] in EDIT_FIELDS" :key="k">{{ lab }}<input v-model="form[k]" type="text"></label>
                    <label class="wide">別名（逗號分隔）<input v-model="form.aliases" type="text"></label>
                    <label class="cb"><input v-model="form.vendor_move_required" type="checkbox">需原廠搬遷</label>
                    <label class="cb"><input v-model="form.is_generic" type="checkbox">籠統描述（不算電）</label>
                  </div>
                  <div class="actions">
                    <button class="btn" @click="save(s.model)">儲存</button>
                    <button v-if="s.manual_fields.length && s.from_seed" class="mini" @click="save(s.model, s.manual_fields)">退回種子值</button>
                    <button class="mini" @click="editing = null">取消</button>
                    <span class="muted small">{{ s.note }}</span>
                    <a v-for="src in s.sources" :key="src.url" :href="src.url" target="_blank" rel="noopener" class="lnk small">來源（{{ src.field }}）</a>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'coverage'" class="panel">
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="cKey" :dir="cDir" @sort="cToggle">項次</SortTh>
            <SortTh k="device_model" :active="cKey" :dir="cDir" @sort="cToggle">資產上的型號原值</SortTh>
            <SortTh k="count" :active="cKey" :dir="cDir" @sort="cToggle">台數</SortTh>
            <SortTh k="spec" :active="cKey" :dir="cDir" @sort="cToggle">對到的規格</SortTh>
            <SortTh k="status" :active="cKey" :dir="cDir" @sort="cToggle">狀態</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in cSorted" :key="r.device_model">
              <td class="mono">C{{ r.no }}</td><td>{{ r.device_model }}</td><td class="mono">{{ r.count }}</td>
              <td>{{ r.spec || '—' }}</td>
              <td>{{ r.status }}<button v-if="canEdit && !r.spec" class="linkbtn" @click="useForCoverage(r)">建立規格</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.mspage { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 10px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); }
.chips { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; color: var(--ink-soft); }
.tabs { display: flex; gap: 4px; margin: 0 0 12px; border-bottom: 1px solid var(--border); }
.tab { padding: 8px 16px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; font-family: inherit; }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.kw { font-family: inherit; font-size: 13px; padding: 6px 9px; min-width: 260px; border: 1px solid var(--border-strong); border-radius: 8px; }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled { opacity: .5; }
.mini { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 6px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; }
.tbl tr.generic td { color: var(--muted); }
.tbl tr.edit td { background: var(--sub); }
.tag { display: inline-block; margin-left: 6px; font-size: 10.5px; padding: 0 7px; border-radius: 8px; background: var(--mint); color: var(--brand-dark); }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.form { display: flex; gap: 10px; flex-wrap: wrap; }
.form label { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; color: var(--muted); }
.form label.wide { min-width: 320px; }
.form label.cb { flex-direction: row; align-items: center; gap: 5px; font-size: 12.5px; color: var(--ink-soft); }
.form input[type=text] { font-family: inherit; font-size: 12.5px; padding: 4px 7px; width: 120px; }
.form label.wide input { width: 320px; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.mono { font-family: var(--disp); }
.small { font-size: 11.5px; }
.muted { color: var(--muted); }
.err { color: var(--bad); }
.lnk { color: var(--link); }
.linkbtn { background: none; border: 0; padding: 0 6px 0 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; text-align: left; }
.linkbtn:hover { text-decoration: underline; }
</style>
