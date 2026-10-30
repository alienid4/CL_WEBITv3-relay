<script setup lang="ts">
// ③ 傳送佇列：一次傳送的逐台進度。背景執行，關掉瀏覽器也不會斷；這裡每 2 秒更新一次。
// 「停止」＝未開始的台不再送（已在傳的那一台會傳完）；「重送失敗的」只送失敗或沒開始的台，成功的不再碰。
import { HOST_STATUS, JOB_STATUS, fmtSize } from '~/types/fileTransfer'
const props = defineProps<{ jobId: number | null }>()
const emit = defineEmits<{ open: [id: number]; changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

interface JobSummary { id: number; kind: string; file_name: string; status: string; created_at: string }
const jobs = ref<JobSummary[]>([])
const cur = ref<any>(null)
const sel = ref<number | null>(props.jobId)
const err = ref('')
const busy = ref(false)
const ACTIVE = ['queued', 'running', 'waiting_window']
const active = computed(() => !!cur.value && ACTIVE.includes(cur.value.status))

async function loadList() {
  try {
    const d = await apiFetch<{ jobs: JobSummary[] }>('/api/transfer/jobs')
    jobs.value = d.jobs.slice(0, 30)
    if (sel.value == null && jobs.value.length) sel.value = jobs.value[0].id
    err.value = ''
  } catch (e: any) { err.value = `傳送清單載入失敗：${errMsg(e)}` }
}
async function loadCur() {
  if (sel.value == null) { cur.value = null; return }
  try { cur.value = await apiFetch<any>(`/api/transfer/jobs/${sel.value}`); err.value = '' } catch (e: any) { err.value = `傳送明細載入失敗：${errMsg(e)}` }
}
watch(() => props.jobId, (v) => { if (v != null) sel.value = v })
watch(sel, loadCur)
let timer: ReturnType<typeof setInterval> | null = null
onMounted(async () => {
  await loadList()
  await loadCur()
  timer = setInterval(async () => { if (active.value || !cur.value) { await loadCur(); await loadList() } }, 2000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

async function afterAct(d: any, ok: string, newJob: boolean) {
  showToast(ok, 'success')
  if (newJob) { sel.value = d.id; emit('open', d.id) } else { cur.value = d }
  emit('changed')
  await loadList()
}
async function stop() {
  busy.value = true
  try { afterAct(await apiFetch<any>(`/api/transfer/jobs/${sel.value}/stop`, { method: 'POST' }), '已要求停止', false) } catch (e: any) { showToast(`停止失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function retry() {
  busy.value = true
  try { afterAct(await apiFetch<any>(`/api/transfer/jobs/${sel.value}/retry-failed`, { method: 'POST' }), '已重送失敗的', true) } catch (e: any) { showToast(`重送失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
const rows = computed(() => (cur.value?.hosts ?? []).map((h: any) => ({ ...h, __no: h.seq, status_label: HOST_STATUS[h.status] ?? h.status, pct_label: `${h.pct}%` })))
const cols = [
  { k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true }, { k: 'status_label', label: '狀態', tone: (r: any) => (r.status === 'ok' ? 'prod' : r.status === 'failed' ? 'test' : 'none') },
  { k: 'pct_label', label: '進度', mono: true }, { k: 'message', label: '說明' },
]
const label = (j: JobSummary) => `#${j.id}・${j.kind === 'upload' ? '送出' : '抓回'}・${j.file_name}・${JOB_STATUS[j.status] ?? j.status}`
</script>

<template>
  <div class="card">
    <p v-if="err" class="banner bad">{{ err }}</p>
    <div v-if="!jobs.length && !cur" class="empty">還沒有傳送。到「② 傳檔」把檔案拖到右邊。</div>
    <template v-else>
      <div class="row">
        <select v-model="sel" aria-label="選擇傳送"><option v-for="j in jobs" :key="j.id" :value="j.id">{{ label(j) }}</option></select>
        <template v-if="cur">
          <b>{{ cur.kind === 'upload' ? '傳送' : '抓回' }} {{ cur.file_name }}（{{ fmtSize(cur.size) }}）→ {{ cur.n }} 台</b>
          <span class="tag" :class="cur.status === 'done' ? 'good' : ['failed', 'partial'].includes(cur.status) ? 'bad' : 'run'">{{ JOB_STATUS[cur.status] ?? cur.status }}</span>
          <button v-if="active" type="button" class="btn ghost" :disabled="busy" @click="stop">停止（未開始的台不再送）</button>
          <button v-if="!active && cur.kind === 'upload' && (cur.fail_count || cur.queued_count)" type="button" class="btn ghost" :disabled="busy" @click="retry">重送失敗的</button>
          <span class="muted">成功 {{ cur.ok_count }}・失敗 {{ cur.fail_count }}・傳送中 {{ cur.running_count }}・排隊 {{ cur.queued_count }}</span>
        </template>
      </div>
      <p v-if="cur?.note" class="banner warn">{{ cur.note }}</p>
      <V2Table v-if="cur" :rows="rows" :cols="cols" prefix="R" empty="沒有逐台資料" unit="台" />
    </template>
  </div>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 14px 16px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.muted { color: var(--muted); font-size: 13px; }
.empty { padding: 14px; color: var(--muted); font-size: 13px; }
.banner { border-radius: 10px; padding: 9px 12px; margin: 8px 0; font-size: 13px; }
.banner.bad { background: var(--bad-soft); color: var(--bad); border: 1px solid var(--bad); }
.banner.warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); }
.tag { display: inline-block; border-radius: 999px; padding: 1px 9px; font-size: 12px; border: 1px solid var(--border-strong); color: var(--ink-aux); }
.tag.good { background: var(--mint); color: var(--brand-dark); border-color: var(--brand); }
.tag.bad { background: var(--bad-soft); color: var(--bad); border-color: var(--bad); }
.tag.run { background: var(--st-online-bg); color: var(--st-online); border-color: var(--st-online); }
select { font: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); max-width: 100%; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font: inherit; font-size: 13px; cursor: pointer; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.btn:disabled { opacity: .45; cursor: default; }
</style>
