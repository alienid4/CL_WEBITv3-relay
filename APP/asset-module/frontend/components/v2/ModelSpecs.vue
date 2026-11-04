<script setup lang="ts">
// ⚙ 型號規格（V38，2.047.0 V2 原生；取代舊 pages/model-specs.vue）。同一支 API：GET／PUT /api/model-specs（model_specs.py）。
// 兩個子分頁：規格（新增型號、逐列編輯、退回種子值、來源連結）｜機櫃型號對應（台數點下去看是哪幾台、未對應的可直接建立規格）。
// 2.047.1（GPT 審查 P1-2）：台數下鑽改走 /api/model-specs/coverage-hosts（跟台數同一份計算），不再丟到 2.1 用關鍵字找。
// 改值限管理員（後端另擋）。網址：?st=specs&ms=cov 切到對應頁。
interface Spec {
  model: string; vendor_model_name: string | null; rack_u: number | null; psu_max_w: number | null; psu_count: number | null
  weight_kg: number | null; plug: string | null; plug_confidence: string | null; depth_mm: number | null
  vendor_move_required: boolean | null; vendor_move_reason: string | null; confidence: string | null; is_generic: boolean
  system_level: Record<string, number | null> | null; note: string | null; aliases: string[]; sources: { field: string; url: string }[]
  manual_fields: string[]; from_seed: boolean; updated_at: string | null; updated_by: string | null
}
interface Cov { device_model: string; count: number; spec: string | null; generic?: boolean; keys: string[] }
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const specs = ref<Spec[]>([])
const cov = ref<{ rows: Cov[]; matched: number; total: number } | null>(null)
const canEdit = ref(false)
const err = ref('')
async function load() {
  try {
    const r = await apiFetch<{ specs: Spec[]; coverage: { rows: Cov[]; matched: number; total: number }; can_edit: boolean }>('/api/model-specs')
    specs.value = r.specs
    cov.value = r.coverage
    canEdit.value = r.can_edit && !isViewer.value
    err.value = ''
  } catch (e: any) { err.value = `型號規格載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
const sub = computed(() => (route.query.ms === 'cov' ? 'cov' : 'specs'))
const setSub = (v: string) => router.replace({ query: { ...route.query, ms: v === 'cov' ? 'cov' : undefined } })
const specRows = computed(() => specs.value.map((s, i) => ({
  ...s, __no: i + 1, alias_text: s.aliases.join('、'), move_text: s.vendor_move_required ? '要' : '', manual_text: s.manual_fields.join(', '),
  plug_text: (s.plug || '') + (s.plug_confidence === 'low' ? '（待確認）' : ''),
  tags: [s.is_generic ? '籠統描述' : '', s.system_level ? `整套 ${s.system_level.rack_u ?? '?'}U／${s.system_level.weight_kg_max ?? '?'}kg／${s.system_level.psu_max_w ?? '?'}W` : ''].filter(Boolean).join('、'),
})))
const covRows = computed(() => (cov.value?.rows ?? []).map((r, i) => ({ ...r, __no: i + 1, status: r.spec ? (r.generic ? '籠統描述' : '已對應') : '未對應' })))
// ---- 編輯（一次一列）
const EDIT_FIELDS: [keyof Spec, string][] = [['rack_u', 'U'], ['psu_max_w', '單顆 PSU W'], ['psu_count', 'PSU 數'], ['weight_kg', '重量 kg'],
  ['depth_mm', '深度 mm'], ['plug', '插頭'], ['confidence', '可信度'], ['note', '備註']]
const editing = ref<Spec | null>(null)
const form = reactive<Record<string, any>>({})
const newModel = ref('')
const busy = ref(false)
function startEdit(s: Spec) {
  editing.value = s
  for (const [k] of EDIT_FIELDS) form[k] = s[k] ?? ''
  form.aliases = s.aliases.join(', ')
  form.vendor_move_required = !!s.vendor_move_required
  form.is_generic = !!s.is_generic
}
async function save(model: string, clear: string[] = []) {
  const values: Record<string, any> = {}
  if (!clear.length) {
    for (const [k] of EDIT_FIELDS) values[k] = form[k] === '' ? null : form[k]
    Object.assign(values, { aliases: form.aliases, vendor_move_required: form.vendor_move_required, is_generic: form.is_generic })
  }
  busy.value = true
  try {
    await guardedFetch('/api/model-specs', { method: 'PUT', body: { model, values, clear } })
    showToast(clear.length ? `「${model}」已退回種子值` : `「${model}」已更新`, 'success')
    editing.value = null
    await load()
  } catch (e: any) { showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
async function addModel() {
  const m = newModel.value.trim()
  if (!m) return
  busy.value = true
  try {
    await guardedFetch('/api/model-specs', { method: 'PUT', body: { model: m, values: { aliases: [m] }, clear: [] } })
    newModel.value = ''
    await load()
    const s = specs.value.find(x => x.model === m)
    if (s) startEdit(s)
  } catch (e: any) { showToast(`新增失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
function useForCoverage(r: Cov) { newModel.value = r.device_model; setSub('specs') }
const drillModel = computed(() => (typeof route.query.mm === 'string' ? route.query.mm : ''))
const drillRows = ref<any[]>([])
const drillErr = ref('')
const beginDrill = useLatest()      // 2.047.2（GPT 複核）：快速切型號時只套用最新那一次，舊請求晚回來丟掉
watch(drillModel, async (m) => {
  const isLatest = beginDrill()
  drillRows.value = []
  if (!m) return
  try {
    const r = await apiFetch<{ items: any[] }>('/api/model-specs/coverage-hosts', { query: { device_model: m } })
    if (!isLatest()) return
    drillRows.value = r.items.map((x, i) => ({ ...x, __no: i + 1, where: [x.room, x.rack, x.u].filter(Boolean).join(' ') }))
    drillErr.value = ''
  } catch (e: any) { if (isLatest()) drillErr.value = `明細載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}, { immediate: true })
const openModel = (m: string | undefined) => router.replace({ query: { ...route.query, ms: 'cov', mm: m } })
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else>
      <div class="bar">
        <span>規格 {{ specs.length }} 筆</span>
        <button v-if="cov" type="button" class="lb" @click="setSub('cov')">機櫃裡對得到規格的設備 {{ cov.matched }}／{{ cov.total }} 台</button>
        <InfoNote>用電＝單顆 PSU 額定瓦數（200–240V AC 可用的最大顆，不是加總）。種子值來自原廠文件（出處在每列的「來源」），人工改的值另存，重新灌種子不會蓋掉；「退回種子值」會清掉人工值。插頭標「待確認」的是原廠沒寫清楚、要現場看的。工程系統（Exadata／ODA）逐筆不填用電／U／重量，同櫃達 3 筆以上時以整套數字算一次。改值限管理員。</InfoNote>
      </div>
      <div class="bar" role="tablist">
        <button type="button" class="chip" :class="{ on: sub === 'specs' }" @click="setSub('specs')">規格 {{ specs.length }}</button>
        <button type="button" class="chip" :class="{ on: sub === 'cov' }" @click="setSub('cov')">機櫃型號對應 {{ cov?.rows.length ?? 0 }}</button>
      </div>
      <template v-if="sub === 'specs'">
        <form v-if="canEdit" class="bar" @submit.prevent="addModel">
          <input v-model="newModel" class="in" type="text" placeholder="新增型號（例：Dell R7625）" aria-label="新增型號">
          <button type="submit" class="btn" :disabled="!newModel.trim() || busy">新增</button>
        </form>
        <V2Table :rows="specRows" prefix="M" row-key="model" export-name="型號規格" empty="沒有型號規格"
                 :cols="[{ k: 'model', label: '型號' }, { k: 'alias_text', label: '別名' }, { k: 'tags', label: '標記' }, { k: 'rack_u', label: 'U', mono: true },
                         { k: 'psu_max_w', label: '單顆 PSU W', mono: true }, { k: 'psu_count', label: 'PSU 數', mono: true }, { k: 'weight_kg', label: 'kg', mono: true },
                         { k: 'depth_mm', label: '深 mm', mono: true }, { k: 'plug_text', label: '插頭' }, { k: 'move_text', label: '原廠搬' },
                         { k: 'confidence', label: '可信度' }, { k: 'manual_text', label: '人工改過' }]">
          <template #cell-model="{ row }">
            <button v-if="canEdit" type="button" class="lb" @click="startEdit(row)">{{ row.model }}</button><span v-else>{{ row.model }}</span>
          </template>
        </V2Table>
        <div v-if="editing" class="box" data-testid="spec-edit">
          <h3 class="h3">編輯：{{ editing.model }}</h3>
          <div class="form">
            <label v-for="[k, lab] in EDIT_FIELDS" :key="k">{{ lab }}<input v-model="form[k]" type="text"></label>
            <label class="wide">別名（逗號分隔）<input v-model="form.aliases" type="text"></label>
            <label class="cb"><input v-model="form.vendor_move_required" type="checkbox">需原廠搬遷</label>
            <label class="cb"><input v-model="form.is_generic" type="checkbox">籠統描述（不算電）</label>
          </div>
          <div class="bar">
            <button type="button" class="btn" :disabled="busy" @click="save(editing.model)">儲存</button>
            <button v-if="editing.manual_fields.length && editing.from_seed" type="button" class="btn2" :disabled="busy" @click="save(editing.model, editing.manual_fields)">退回種子值</button>
            <button type="button" class="btn2" @click="editing = null">取消</button>
            <span class="muted">{{ editing.note }}</span>
            <a v-for="src in editing.sources" :key="src.url" :href="src.url" target="_blank" rel="noopener" class="lnk">來源（{{ src.field }}）</a>
          </div>
        </div>
      </template>
      <V2Table v-else :rows="covRows" prefix="C" row-key="device_model" export-name="機櫃型號對應" empty="機櫃裡沒有設備"
               :cols="[{ k: 'device_model', label: '資產上的型號原值' }, { k: 'count', label: '台數', mono: true }, { k: 'spec', label: '對到的規格' }, { k: 'status', label: '狀態' }]">
        <template #cell-count="{ row }"><button type="button" class="lb" data-testid="cov-count" @click="openModel(drillModel === row.device_model ? undefined : row.device_model)">{{ row.count }}</button></template>
        <template #cell-status="{ row }">{{ row.status }}<button v-if="canEdit && !row.spec" type="button" class="lb" @click="useForCoverage(row)">建立規格</button></template>
      </V2Table>
      <div v-if="sub === 'cov' && drillModel" class="box" data-testid="cov-drill">
        <h3 class="h3">型號「{{ drillModel }}」的機櫃設備 {{ drillRows.length }} 台<button type="button" class="lb" @click="openModel(undefined)">關閉</button></h3>
        <p v-if="drillErr" class="err">{{ drillErr }}</p>
        <V2Table v-else :rows="drillRows" prefix="H" row-key="key" unit="台" :export-name="`型號_${drillModel}`" empty="沒有設備"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'where', label: '機櫃' }]" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; min-width: 260px; border: 1px solid var(--border-strong); border-radius: 8px; }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn:disabled { opacity: .5; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.lb { background: none; border: 0; padding: 0 6px 0 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; text-align: left; }
.box { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--sub); }
.h3 { margin: 0 0 8px; font-size: 15px; }
.form { display: flex; gap: 10px; flex-wrap: wrap; }
.form label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); }
.form label.wide input { width: 320px; }
.form label.cb { flex-direction: row; align-items: center; gap: 5px; font-size: 13px; color: var(--ink-soft); }
.form input[type=text] { font-family: inherit; font-size: 13px; padding: 4px 7px; width: 120px; border: 1px solid var(--border-strong); border-radius: 6px; }
.lnk { color: var(--link); font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
