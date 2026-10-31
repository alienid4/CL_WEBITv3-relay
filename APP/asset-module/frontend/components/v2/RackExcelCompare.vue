<script setup lang="ts">
// 5.1 機房平面圖「Excel 對照」頁籤（2.007.20）：上傳機房提供的 Excel 機櫃圖，逐格跟系統比，勾選後把位置寫進系統。
// 使用者 2026-10-28：Excel 為準、目標 9 成相同。流程：選機房＋上傳 → 看每一格的狀態 → 勾選 → 套用（先備份、有稽核、可還原）。
// 預設只勾「系統有這台、但沒填櫃位／登記在別的機房」且沒有任何疑慮的格；兩邊位置不同的、會重疊的、新櫃的，要你自己勾。
// 管理卡、VM、一格對到好幾台、廠牌不同：不能勾（伺服器端也擋）。系統沒有的設備只列出，不在這裡新增。
const props = defineProps<{ rooms: string[]; room: string }>()
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

interface Row { id: number; rack: string; u_start: number; u_end: number; label: string; status: string; sys_name: string | null; sys_ip: string | null
  sys_now: string | null; key: string | null; action: string | null; auto: boolean; blockers: string[]; blocker_text: string }
interface Run { run_id: number; room: string; filename: string; created_by?: string; created_at?: string; summary: any; rows: Row[]; applied: any[]; can_edit?: boolean }

const ST: Record<string, string> = { OK: '一致', POSONLY: '同位置有設備但對不上', NOPOS: '系統有、沒填櫃位', OTHERROOM: '系統有、登記在別間機房', MOVED: '系統有、但位置不同', MISSING: '系統沒有', BLOCKED: '不能自動處理' }
const ORDER = ['NOPOS', 'OTHERROOM', 'MOVED', 'MISSING', 'POSONLY', 'BLOCKED', 'OK']

const target = ref(props.room)
watch(() => props.room, (r) => { if (!run.value) target.value = r })
const run = ref<Run | null>(null)
const runs = ref<any[]>([])
const canEdit = ref(false)
const busy = ref(false)
const filter = ref('')
const picked = ref<Record<string, boolean>>({})
const result = ref<any | null>(null)
const fileEl = ref<HTMLInputElement | null>(null)

async function loadRuns() {
  try { const r = await apiFetch<any>('/api/racks/excel-import/runs'); runs.value = r.items; canEdit.value = !!r.can_edit } catch (e: any) { showToast(`對照紀錄載入失敗：${errMsg(e)}`, 'error') }
}
function setRun(r: Run) {
  run.value = r; target.value = r.room; result.value = null; filter.value = ''
  const done = new Set((r.applied ?? []).map((a: any) => a.id))
  picked.value = Object.fromEntries(r.rows.filter(x => x.auto && !done.has(x.id)).map(x => [String(x.id), true]))
}
async function openRun(id: number) {
  try { setRun(await apiFetch<Run>(`/api/racks/excel-import/${id}`)) } catch (e: any) { showToast(`讀取對照失敗：${errMsg(e)}`, 'error') }
}
async function upload() {
  const f = fileEl.value?.files?.[0]
  if (!f) { showToast('先選一個 Excel 檔（.xlsx）', 'warn'); return }
  busy.value = true
  const fd = new FormData(); fd.append('file', f)
  try {
    const r = await apiFetch<any>('/api/racks/excel-import/preview', { method: 'POST', body: fd, query: { room: target.value } })
    await openRun(r.run_id); await loadRuns()
    showToast(`比對完成：${r.summary.total} 格，嚴格一致 ${r.summary.strict_ok_pct}%`, 'success')
  } catch (e: any) { showToast(`比對失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
const appliedIds = computed(() => new Set((run.value?.applied ?? []).map((a: any) => a.id)))
const rows = computed(() => (run.value?.rows ?? []).map(r => ({ ...r, __no: r.id, st: appliedIds.value.has(r.id) ? '已套用' : ST[r.status] ?? r.status,
  u_text: r.u_start === r.u_end ? `${r.u_start}U` : `${r.u_start}-${r.u_end}U`, sys_text: [r.sys_name, r.sys_ip].filter(Boolean).join(' / '),
  note: appliedIds.value.has(r.id) ? '' : r.blocker_text })))
const shown = computed(() => (filter.value ? rows.value.filter(r => r.status === filter.value) : rows.value))
const counts = computed(() => { const c: Record<string, number> = {}; for (const r of run.value?.rows ?? []) c[r.status] = (c[r.status] ?? 0) + 1; return c })
const pickedIds = computed(() => Object.keys(picked.value).filter(k => picked.value[k]).map(Number))
const canPick = (r: any) => canEdit.value && r.action === 'set' && !appliedIds.value.has(r.id)
async function apply() {
  if (!run.value || !pickedIds.value.length) return
  if (!window.confirm(`把勾選的 ${pickedIds.value.length} 格寫進系統？\n會先備份資料庫，每台都留稽核紀錄，之後可以還原。`)) return
  busy.value = true
  try {
    const r = await guardedFetch<any>('/api/racks/excel-import/apply', { method: 'POST', body: { run_id: run.value.run_id, row_ids: pickedIds.value } })
    result.value = r
    showToast(`已套用 ${r.applied} 格${r.skipped.length ? `，跳過 ${r.skipped.length} 格` : ''}`, r.skipped.length ? 'warn' : 'success')
    await openRun(run.value.run_id); result.value = r; await loadRuns(); emit('changed')
  } catch (e: any) { showToast(`套用失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
const COLS = [
  { k: 'rack', label: '櫃' }, { k: 'u_text', label: 'U', mono: true }, { k: 'label', label: 'Excel 上寫的' }, { k: 'st', label: '比對結果' },
  { k: 'sys_text', label: '系統上的這台' }, { k: 'sys_now', label: '系統現在在哪' }, { k: 'note', label: '要注意' },
]
onMounted(loadRuns)
</script>

<template>
  <div class="rx">
    <div class="bar">
      <label>機房 <select v-model="target" :disabled="!!run"><option v-for="r in rooms" :key="r" :value="r">{{ r }}</option></select></label>
      <input ref="fileEl" type="file" accept=".xlsx" class="file">
      <button type="button" class="btn" :disabled="busy || !canEdit" @click="upload">{{ busy ? '處理中…' : '上傳並比對' }}</button>
      <button v-if="run" type="button" class="btn2" @click="run = null; result = null">換一份</button>
      <InfoNote>上傳機房提供的 Excel 機櫃圖（.xlsx；每個櫃左右各一欄 U 編號、中間放設備）。只讀比對，不改任何設備；要你勾選後按「套用」才會寫。關機版、調整版分頁會自動略過。比對靠 Excel 文字裡的 IP、硬體序號；只寫「Cisco switch」這種沒有識別碼的，只能看同櫃同 U 有沒有設備。</InfoNote>
    </div>
    <div v-if="!canEdit" class="muted">只有管理員可以做機櫃圖對照。</div>

    <template v-if="!run">
      <h3 class="h3">過去的對照 {{ runs.length }}</h3>
      <p v-if="!runs.length" class="muted">還沒有對照過。選機房、選檔案、按「上傳並比對」。</p>
      <ul class="runs"><li v-for="r in runs" :key="r.run_id"><a href="#" @click.prevent="openRun(r.run_id)">#{{ r.run_id }} {{ r.room }}・{{ r.filename }}</a>
        <span class="muted"> {{ r.created_at?.slice(0, 16) }}・嚴格一致 {{ r.summary.strict_ok_pct }}%・已套用 {{ r.applied_count }} 格</span></li></ul>
    </template>

    <template v-else>
      <div class="bar sum">
        <b>{{ run.room }}・{{ run.filename }}</b>
        <span>Excel 共 {{ run.summary.total }} 格</span>
        <span>嚴格一致 <b>{{ Math.round(((counts.OK ?? 0) + appliedIds.size) / Math.max(1, run.summary.total) * 100) }}%</b></span>
        <InfoNote>嚴格一致＝同櫃同 U、而且 IP／序號對得上同一台，加上你已經套用的格。「同位置有設備但對不上」不算一致，因為不確定是不是同一台。略過的分頁：{{ run.summary.skipped_sheets?.join('、') || '無' }}。</InfoNote>
      </div>
      <div class="bar">
        <button type="button" class="chip" :class="{ on: !filter }" @click="filter = ''">全部 {{ run.summary.total }}</button>
        <button v-for="s in ORDER.filter(x => counts[x])" :key="s" type="button" class="chip" :class="{ on: filter === s }" @click="filter = s">{{ ST[s] }} {{ counts[s] }}</button>
      </div>
      <div class="bar">
        <button type="button" class="btn" :disabled="busy || !pickedIds.length || !canEdit" @click="apply">套用勾選的 {{ pickedIds.length }} 格</button>
        <span class="muted">預設已勾「系統有這台、只差櫃位」且沒有疑慮的格。</span>
      </div>
      <div v-if="result" class="res">
        已套用 {{ result.applied }} 格<span v-if="result.backup">，備份在 {{ result.backup }}</span>。
        <ul v-if="result.skipped?.length"><li v-for="s in result.skipped" :key="s.id">第 {{ s.id }} 項：{{ s.why }}</li></ul>
      </div>
      <V2Table v-model:selected="picked" :rows="shown" :cols="COLS" prefix="X" row-key="id" selectable :can-select="canPick" :max="100" empty="沒有符合的格" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.h3 { margin: 12px 0 6px; font-size: 15px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; cursor: default; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; cursor: pointer; font-family: inherit; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.file { font-size: 12px; max-width: 260px; }
.muted { color: var(--muted); font-size: 13px; }
.runs { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.9; }
.runs a { color: var(--link); }
.res { background: var(--mint); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; margin-bottom: 8px; font-size: 13px; }
.res ul { margin: 4px 0 0; padding-left: 18px; }
</style>
