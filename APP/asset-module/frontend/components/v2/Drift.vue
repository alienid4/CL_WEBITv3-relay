<script setup lang="ts">
// 8.2「基線偏移」頁籤（V29，2.048.8 起 V2 原生；取代嵌入的舊頁 pages/drift.vue）。同一批 API，行為不變：
// GET /api/drift?status=open|ack|all、GET /api/drift/summary、POST /api/drift/{id}/disposition（一般帳號以上）、
// POST /api/drift/recheck（管理員；後端 ADMIN_WRITE。回檢只比對已收集到的資料，不連目標機）。
// 分頁在網址：?tab=drift&ds=open|ack|all。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const { canWrite, isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const STATUSES = [{ k: 'open', label: '待處理' }, { k: 'ack', label: '已確認' }, { k: 'all', label: '全部未恢復' }]
const ds = computed(() => (STATUSES.some(s => s.k === route.query.ds) ? String(route.query.ds) : 'open'))
const rows = ref<any[]>([])
const summary = ref<any>(undefined)      // undefined＝還在取；null＝取不到（無法判定）
const loading = ref(true)
const err = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const r = await apiFetch<any>('/api/drift', { query: { status: ds.value } })
    if (!isLatest()) return
    rows.value = (r.drifts ?? []).map((d: any, i: number) => ({ ...d, __no: i + 1, st_t: d.status === 'open' ? '待處理' : '已確認',
      passed_t: d.passed_by ? `${d.passed_by}${d.passed_at ? ` ${d.passed_at}` : ''}` : '—' }))
    err.value = ''
    loading.value = false            // 清單到了就顯示；摘要只影響空狀態的文字
    summary.value = undefined
    // 摘要取不到就不下「無差異」的結論，空狀態只顯示無法判定（W221-013）
    try { const s = await apiFetch<any>('/api/drift/summary'); if (isLatest()) summary.value = s } catch (e) { if (isLatest()) summary.value = null; console.warn('回檢摘要載入失敗（空狀態顯示無法判定）', e) }
  } catch (e: any) {
    if (!isLatest()) return
    rows.value = []; err.value = `基線偏移載入失敗：${errMsg(e)}`
  } finally { if (isLatest()) loading.value = false }
}
onMounted(load)
watch(ds, () => { rows.value = []; load() })

const rechecking = ref(false)
async function recheck() {
  rechecking.value = true
  try {
    const s = await apiFetch<any>('/api/drift/recheck', { method: 'POST' })
    showToast(`已回檢 ${s.checked_assets} 台：新增 ${s.opened} 筆失效、恢復 ${s.recovered} 筆`, s.opened > 0 ? 'warn' : 'success')
    await load()
  } catch (e: any) { showToast(`回檢失敗：${errMsg(e)}`, 'error') } finally { rechecking.value = false }
}
async function decide(d: any, status: 'ack' | 'fixed') {
  try {
    await apiFetch(`/api/drift/${d.id}/disposition`, { method: 'POST', body: { status } })
    showToast(status === 'ack' ? '已標記為已確認' : '已標記為已恢復', 'success')
    await load()
  } catch (e: any) { showToast(`標記失敗：${errMsg(e)}`, 'error') }
}
const emptyText = computed(() => {
  const s = summary.value
  if (s === undefined) return '回檢狀態載入中…'
  if (!s) return '這個分頁沒有項目；回檢狀態暫時取不到，無法判定是否一致。'
  if (s.state === 'no_baseline') return '還沒有已上線的基線——沒有任何主機通過上線前檢查，所以無從比對。'
  if (s.state === 'never_checked') return `尚未回檢——已有 ${s.baseline_count} 筆基線，但還沒跑過回檢，不能說都一致。`
  return `已回檢 ${s.checked_assets} 台，這個分頁無差異（最後回檢 ${s.last_run}；測不到 ${s.unknown_count ?? 0} 項不列入）。`
})
const cols = [
  { k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` },
  { k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true }, { k: 'label', label: '檢查項目' },
  { k: 'baseline_text', label: '上線時（應然）' }, { k: 'current_text', label: '現在（實然）', tone: () => 'test' },
  { k: 'first_detected_at', label: '首次發現', mono: true }, { k: 'last_detected_at', label: '最後發現', mono: true },
  { k: 'passed_t', label: '當初放行' }, { k: 'st_t', label: '狀態', tone: (r: any) => (r.status === 'open' ? 'test' : '') }, { k: 'note', label: '備註' }, { k: 'act', label: '處置' }]
</script>

<template>
  <div>
    <div class="bar">
      <NuxtLink v-for="s in STATUSES" :key="s.k" class="chip" :class="{ on: ds === s.k }" :to="{ query: { tab: 'drift', ds: s.k } }">{{ s.label }}</NuxtLink>
      <InfoNote>上線前檢查通過時，機器可驗的項目會被記成基線——意思是「這台被刻意設定成這樣」。系統每天跟著排程掃描回檢一次，跟基線不一樣就列在這裡。
        收不到資料的主機不會列入（沒去收 ≠ 設定被改）。「已確認」只是記下你看過了，不會改掉基線；機器實際恢復成基線時，下一次回檢會自動把它關掉。</InfoNote>
      <button v-if="isAdmin" type="button" class="btn2" :disabled="rechecking" @click="recheck">{{ rechecking ? '回檢中…' : '立即回檢' }}</button>
      <span v-else class="muted" data-test="admin-only">立即回檢限管理員</span>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <p v-else-if="!rows.length" class="ok" data-testid="drift-empty">{{ emptyText }}</p>
    <V2Table v-else :rows="rows" prefix="B" row-key="id" unit="筆" :cols="cols" export-name="基線偏移">
      <template #cell-act="{ row }">
        <template v-if="canWrite">
          <button v-if="row.status === 'open'" type="button" class="lb" @click="decide(row, 'ack')">已確認</button>
          <button type="button" class="lb" @click="decide(row, 'fixed')">已恢復</button>
        </template>
        <span v-else class="muted">—</span>
      </template>
    </V2Table>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn2:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0 6px 0 0; }
.muted { color: var(--muted); font-size: 12px; }
.ok { color: var(--brand-dark); font-size: 13px; }
.err { color: var(--bad); }
</style>
