<script setup lang="ts">
// 10.3 容量分析 › 封存與保存 › 封存引用（2.042.06）：報告／簽呈引用哪幾筆日摘要。
// 只增不改：更正＝新增下一版（舊版原封不動），作廢＝另記一筆作廢紀錄；每一版（含舊版、作廢的）都會讓被引用的日摘要不被清理。
// 寫入只有管理員（後端另擋，稽核同交易）；大家都看得到全部版本與版本鏈。
import { citeCounts, citeFilter, citeRefsText, parseRefsInput } from '~/composables/capacityCitations'
import type { CiteData, CiteRow } from '~/composables/capacityCitations'

const props = defineProps<{ isAdmin: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const data = ref<CiteData | null>(null)
const err = ref('')
const status = ref<'all' | 'active' | 'superseded' | 'voided'>('all')
const chainOf = ref<number | null>(null)
const busy = ref(false)
const form = reactive({ mode: '' as '' | 'create' | 'revise', of: 0, title: '', purpose: '', refs: '', note: '' })
const voiding = reactive({ id: 0, reason: '' })

async function load() {
  err.value = ''
  try { data.value = await apiFetch<CiteData>('/api/v2/capacity/archive-citations') } catch (e: any) { err.value = `封存引用載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
const counts = computed(() => citeCounts(data.value?.rows ?? []))
const rows = computed(() => citeFilter(data.value?.rows ?? [], status.value).map(r => ({ ...r, __no: r.id, refs_text: citeRefsText(r) })))
const chainRows = computed(() => (chainOf.value === null ? [] : (data.value?.rows ?? []).filter(r => r.root_id === chainOf.value)).map(r => ({ ...r, __no: r.version, refs_text: citeRefsText(r) })))
const cols = [
  { k: 'chain', label: '版本鏈', mono: true, text: (r: CiteRow) => `C${r.root_id}` },
  { k: 'version', label: '版本', mono: true },
  { k: 'title', label: '引用名稱' },
  { k: 'purpose', label: '用途' },
  { k: 'refs_text', label: '引用的日摘要', min: 220, brief: (r: any) => (r.refs_text.length > 60 ? r.refs_text.slice(0, 60) : '') },
  { k: 'ref_rows', label: '涵蓋封存筆數', mono: true },
  { k: 'status_label', label: '狀態' },
  { k: 'created_at', label: '建立時間', mono: true },
  { k: 'created_by', label: '建立者' },
  { k: 'void_reason', label: '作廢原因', text: (r: CiteRow) => (r.voided_at ? `${r.void_reason}（${r.voided_by}，${r.voided_at}）` : '') },
  { k: 'act', label: '操作' },
]
const chainCols = cols.filter(c => c.k !== 'act' && c.k !== 'chain')

function startCreate() { Object.assign(form, { mode: 'create', of: 0, title: '', purpose: '', refs: '', note: '' }) }
function startRevise(r: CiteRow) { Object.assign(form, { mode: 'revise', of: r.id, title: r.title, purpose: r.purpose, refs: r.refs.join('\n'), note: r.note }) }
async function save() {
  const refs = parseRefsInput(form.refs)
  if (!form.title.trim()) { showToast('請填引用名稱（例如報告或簽呈名稱）', 'error'); return }
  if (!refs.length) { showToast('至少要引用一筆日摘要', 'error'); return }
  busy.value = true
  try {
    const body = { title: form.title.trim(), purpose: form.purpose.trim(), note: form.note.trim(), refs }
    const r = form.mode === 'revise'
      ? await guardedFetch<CiteRow>(`/api/v2/capacity/archive-citations/${form.of}/revise`, { method: 'POST', body })
      : await guardedFetch<CiteRow>('/api/v2/capacity/archive-citations', { method: 'POST', body })
    showToast(form.mode === 'revise' ? `已更正，新增第 ${r.version} 版（舊版保留）` : '已新增引用', 'success')
    form.mode = ''
    chainOf.value = r.root_id
    await load()
    emit('changed')
  } catch (e: any) { showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
async function doVoid() {
  if (!voiding.reason.trim()) { showToast('作廢要寫原因', 'error'); return }
  busy.value = true
  try {
    await guardedFetch(`/api/v2/capacity/archive-citations/${voiding.id}/void`, { method: 'POST', body: { reason: voiding.reason.trim() } })
    showToast('已作廢（引用內容保留，被引用的日摘要仍受保護）', 'success')
    voiding.id = 0; voiding.reason = ''
    await load()
    emit('changed')
  } catch (e: any) { showToast(`作廢失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
</script>

<template>
  <section class="cite" data-citations>
    <h3 class="h3">封存引用（{{ data?.chains ?? 0 }} 條版本鏈）<InfoNote>報告、簽呈或會議紀錄引用了哪幾筆日摘要，登記在這裡。引用的寫法：archive:封存編號（指定某一版）、日期|主機（該主機該日的全部版本）、日期（該日全部主機）；指到不存在的日摘要會被拒絕。要改內容按「更正」：會新增下一版，舊版原封不動；不再需要按「作廢」：另記作廢紀錄，原內容保留。每一版（包含被更正取代的舊版與作廢的）都會讓它引用的日摘要不被清理，清理預覽會列出是哪一筆引用保護了它。只有管理員可以新增、更正、作廢，每次都會寫入操作紀錄。</InfoNote></h3>
    <p v-if="err" class="err" role="alert">{{ err }}</p>
    <template v-else-if="data">
      <div class="chips">
        <button type="button" class="chip" :class="{ on: status === 'all' }" @click="status = 'all'">全部 <b>{{ counts.all }}</b></button>
        <button type="button" class="chip" :class="{ on: status === 'active' }" @click="status = 'active'">有效 <b>{{ counts.active }}</b></button>
        <button type="button" class="chip" :class="{ on: status === 'superseded' }" @click="status = 'superseded'">已被更正取代 <b>{{ counts.superseded }}</b></button>
        <button type="button" class="chip" :class="{ on: status === 'voided' }" @click="status = 'voided'">已作廢 <b>{{ counts.voided }}</b></button>
        <button v-if="props.isAdmin" type="button" class="pbtn" :disabled="busy" data-cite-new @click="startCreate">新增引用</button>
      </div>
      <p v-if="!props.isAdmin" class="muted" data-cite-readonly>只有管理員可以新增、更正或作廢引用；你可以查看全部版本。</p>
      <V2Table :rows="rows" :cols="cols" prefix="Y" empty="還沒有任何封存引用" :max="100">
        <template #cell-act="{ row }">
          <button type="button" class="pbtn sm" @click="chainOf = row.root_id">版本鏈</button>
          <template v-if="props.isAdmin && row.status === 'active'">
            <button type="button" class="pbtn sm" :disabled="busy" @click="startRevise(row)">更正</button>
            <button type="button" class="pbtn sm" :disabled="busy" @click="voiding.id = row.id; voiding.reason = ''">作廢</button>
          </template>
        </template>
      </V2Table>
      <div v-if="voiding.id" class="box" data-cite-void>
        <label>作廢引用 #{{ voiding.id }} 的原因 <input v-model="voiding.reason" maxlength="300" class="inp"></label>
        <button type="button" class="pbtn" :disabled="busy" @click="doVoid">確認作廢</button>
        <button type="button" class="pbtn" :disabled="busy" @click="voiding.id = 0">取消</button>
      </div>
      <section v-if="form.mode && props.isAdmin" class="box" data-cite-form>
        <h4>{{ form.mode === 'revise' ? `更正引用 #${form.of}（會新增下一版，舊版保留）` : '新增引用' }}</h4>
        <label>引用名稱 <input v-model="form.title" maxlength="120" class="inp"></label>
        <label>用途 <input v-model="form.purpose" maxlength="200" class="inp"></label>
        <label>引用的日摘要（一行一筆）<textarea v-model="form.refs" rows="4" class="inp" placeholder="archive:123&#10;2026-09-10|HOST-A&#10;2026-09-11" /></label>
        <label>備註 <input v-model="form.note" maxlength="500" class="inp"></label>
        <div class="row">
          <button type="button" class="pbtn on" :disabled="busy" data-cite-save @click="save">{{ busy ? '儲存中…' : '儲存' }}</button>
          <button type="button" class="pbtn" :disabled="busy" @click="form.mode = ''">取消</button>
        </div>
      </section>
      <section v-if="chainOf !== null" class="box" data-cite-chain>
        <h4>版本鏈 C{{ chainOf }}（{{ chainRows.length }} 版，新到舊）<button type="button" class="pbtn sm" @click="chainOf = null">收起</button></h4>
        <V2Table :rows="chainRows" :cols="chainCols" prefix="v" empty="—" :max="50" />
      </section>
    </template>
    <p v-else class="muted">讀取中…</p>
  </section>
</template>

<style scoped>
.cite { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.chip { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.chip.on { border-color: var(--brand); background: var(--mint); }
.box { border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--sub); padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }
.box h4 { margin: 0; font-size: 14px; display: flex; gap: 8px; align-items: center; }
.box label { font-size: 13px; display: flex; flex-direction: column; gap: 2px; }
.row { display: flex; gap: 8px; }
.inp { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; }
.pbtn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.pbtn.on { border-color: var(--brand); background: var(--brand); color: var(--type-on); }
.pbtn.sm { font-size: 12px; padding: 2px 8px; }
.pbtn:disabled { opacity: .45; cursor: default; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; margin: 0; }
</style>
