<script setup lang="ts">
// 資料匯入 → RVTools（vCenter）（V36，2.059.1）：舊 pages/import.vue 的 RVTools 區塊原樣搬來（⚠D4 不改寫入邏輯）。
// POST /api/import/rvtools（一次一檔、逐檔送；資料時間＝檔案最後修改時間，比現有舊→後端 409 stale_import→問一次，同意＝這批剩下的較舊檔也照樣覆蓋，每次匯入歸零）。
// 待人工審核：GET /api/merge-reviews/auto-matchable；批次合併 POST /api/merge-reviews/batch-merge（先 confirm 講清楚筆數與只寫哪幾欄）。
// 匯入紀錄：GET /api/import/log?source=rvtools&limit=10。全自動模式：共用元件 VcenterAutoImport（跟舊頁同一個）。
import { rvVerdict, fileDataAt, dataDate, type RvSummary } from '~/utils/importV2'
defineProps<{ src?: any; apiBase: string }>()
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const detail = (e: any, d: string) => { const x = e?.data?.detail; return (typeof x === 'string' ? x : x?.message) ?? d }

const files = ref<File[]>([])
const busy = ref(false)
const err = ref('')
const results = ref<{ file: string; summary?: RvSummary; error?: string }[]>([])
const forceAll = ref(false)
const history = ref<any[]>([])
function pick(e: Event) { files.value = Array.from((e.target as HTMLInputElement).files ?? []) }
function resetInput(e: Event) { (e.target as HTMLInputElement).value = '' }
async function loadHistory() {
  try { history.value = await apiFetch<any[]>('/api/import/log', { query: { source: 'rvtools', limit: 10 } }) } catch { history.value = [] /* 舊版後端沒這支，安靜跳過不擋整頁 */ }
}
async function sendOne(file: File, force = false): Promise<{ file: string; summary?: RvSummary; error?: string }> {
  try {
    const fd = new FormData()
    fd.append('file', file); fd.append('data_at', fileDataAt(file))
    if (force) fd.append('force', 'true')
    return { file: file.name, summary: await apiFetch<RvSummary>('/api/import/rvtools', { method: 'POST', body: fd }) }
  } catch (e: any) {
    const d = e?.data?.detail
    if (d && typeof d === 'object' && d.code === 'stale_import') {       // 不是錯誤，是「請確認」
      if (forceAll.value) return sendOne(file, true)
      const ok = confirm(`「${file.name}」的資料時間是 ${d.incoming_data_at}，\n比系統現有資料（${d.current_data_at}）還舊。\n\n繼續匯入會用舊資料覆蓋較新的 OS／虛實／主機名等欄位，\n而且系統不保留欄位變更歷史，蓋掉後只能從每日備份還原。\n\n按「確定」＝這批剩下較舊的檔也一律照樣覆蓋，不再一一問。\n按「取消」＝略過這一份。`)
      if (ok) { forceAll.value = true; return sendOne(file, true) }
      return { file: file.name, error: '已取消（資料時間較舊）' }
    }
    return { file: file.name, error: detail(e, '匯入失敗，請稍後再試') }
  }
}
async function submit() {
  if (!files.value.length) { err.value = '請先選擇 RVTools 匯出的 .xlsx（可一次選多個）'; return }
  busy.value = true; err.value = ''; results.value = []; forceAll.value = false
  const list = [...files.value]
  for (const f of list) {
    if (list.length > 1) showToast(`匯入中：${f.name}…`, 'info')
    results.value = [...results.value, await sendOne(f)]
  }
  busy.value = false; files.value = []
  const ok = results.value.filter(r => r.summary).length
  const fail = results.value.length - ok
  if (results.value.length === 1 && results.value[0].summary) { const v = rvVerdict(results.value[0].summary); showToast(v.text, v.tone) } else showToast(fail === 0 ? `全部 ${ok} 份匯入完成` : `完成 ${ok} 份，${fail} 份未成功，詳見下方明細`, fail === 0 ? 'success' : 'warn')
  await loadHistory(); await loadReview()
  emit('changed')
}
const resRows = computed(() => results.value.map((r, i) => ({
  __no: i + 1, file: r.file, verdict: r.summary ? rvVerdict(r.summary).text : r.error,
  total: r.summary?.total_vms ?? null, inserted: r.summary?.inserted ?? null, updated: r.summary?.updated ?? null, pending: r.summary?.pending_review ?? null,
  vc: r.summary ? (r.summary.vi_sdk_servers?.length ? r.summary.vi_sdk_servers.join('、') + (r.summary.vi_sdk_servers.length > 1 ? '（多個＝這份檔被合併過）' : '') : '未記錄') : '',
  moved: r.summary?.vc_moved?.length ? r.summary.vc_moved.map(m => `${m.name || m.asset_serial}：${m.from} → ${m.to}`).join('\n') : '',
  extra: r.summary?.extra_sheets ? Object.entries(r.summary.extra_sheets).map(([s, n]) => `${s} ${n}筆`).join('、') : '',
  errors: r.summary?.errors?.join('\n') ?? '',
})))
const resCols = [
  { k: 'file', label: '檔案' }, { k: 'verdict', label: '結果' }, { k: 'total', label: '讀到（台）', mono: true }, { k: 'inserted', label: '新增', mono: true },
  { k: 'updated', label: '更新', mono: true }, { k: 'pending', label: '待人工確認', mono: true }, { k: 'vc', label: '來源管理端' },
  { k: 'moved', label: '來源 VC 變動', brief: (r: any) => (r.moved ? `${r.moved.split('\n').length} 台` : '') }, { k: 'extra', label: '另外收到' }, { k: 'errors', label: '錯誤', brief: (r: any) => (r.errors ? `${r.errors.split('\n').length} 則` : '') },
]
const histRows = computed(() => history.value.map((h: any, i: number) => ({ ...h, __no: i + 1, data_date: dataDate(h.file_name) })))
const histCols = [{ k: 'imported_at', label: '時間', mono: true }, { k: 'file_name', label: '檔案' }, { k: 'data_date', label: '資料日期', mono: true }, { k: 'hardware_count', label: '寫入筆數', mono: true }, { k: 'error_count', label: '錯誤', mono: true }, { k: 'imported_by', label: '匯入人' }]

// ---- 待人工審核：可安全配對的批次合併 ----
const review = ref<any>(null)
const merging = ref(false)
const reviewMsg = ref('')
const show = ref<'' | 'short' | 'uuid'>('')
async function loadReview() { try { review.value = await apiFetch('/api/merge-reviews/auto-matchable') } catch { review.value = null /* 舊版後端沒這支，安靜跳過 */ } }
onMounted(() => { loadHistory(); loadReview() })
async function batchMerge(which: 'short' | 'uuid') {
  const base = review.value
  if (!base) return
  const info = which === 'uuid' && base.uuid_rule
    ? { rule: base.uuid_rule.rule, rule_label: base.uuid_rule.rule_label, matchable: base.uuid_rule.matchable,
        note: `這 ${base.uuid_rule.matchable.toLocaleString()} 筆其實是 ${base.uuid_rule.distinct_assets.toLocaleString()} 台機器（重複匯入讓同一台被記了好幾筆），而且只寫入 vm_uuid 這一個欄位。` }
    : { rule: base.rule, rule_label: base.rule_label, matchable: base.matchable, note: '只會寫入 OS／虛實／VM 識別碼。' }
  if (!info.matchable) return
  if (!confirm(`即將把 ${info.matchable} 筆併進既有資產。\n\n配對條件：${info.rule_label}\n${info.note}你填的用途、保管者、機房不會被改動。\n\n確定執行嗎？`)) return
  merging.value = true; reviewMsg.value = ''
  try {
    const r = await apiFetch<any>('/api/merge-reviews/batch-merge', { method: 'POST', body: { rule: info.rule } })
    reviewMsg.value = `已合併 ${r.merged} 筆，剩餘待審 ${r.remaining_open} 筆`
    showToast(reviewMsg.value, 'success')
    await loadReview(); emit('changed')
  } catch (e: any) { showToast(detail(e, '批次合併失敗，請稍後再試'), 'error') } finally { merging.value = false }
}
const shortRows = computed(() => (review.value?.samples ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1, target: `${s.target_hostname}（${s.target_serial}）` })))
const uuidRows = computed(() => (review.value?.uuid_rule?.samples ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1, incoming: s.incoming_hostname || s.incoming_ip || '', target: `${s.target_hostname}（${s.target_serial}）` })))
</script>

<template>
  <div>
    <div class="bar">
      <input type="file" accept=".xlsx" multiple :disabled="busy" aria-label="選擇 RVTools 檔（可多個）" @click="resetInput" @change="pick" />
      <span v-if="files.length > 1" class="muted">已選 {{ files.length }} 個</span>
      <button type="button" class="btn" :disabled="!files.length || busy" @click="submit">{{ busy ? '匯入中…' : '匯入' }}</button>
      <V2ImportExports :src="src" :api-base="apiBase" template="/api/import/rvtools/export-template" template-name="rvtools_範例.xlsx" />
      <InfoNote>可一次選多個檔（每台 vCenter 一個），會一個一個匯。系統讀 vInfo 分頁把每台 VM 收進資產：對得到的更新機器資料（不動你填的用途／保管者），新的建成 VC- 資產，同 IP 但不同機器這種判不準的擋下來等你確認，不會亂合併。
        檔案的最後修改時間當作資料時間：比系統現有資料舊時會先問，同意一次這批剩下較舊的檔都照樣覆蓋。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <template v-if="results.length">
      <h4 class="h4">匯入結果</h4>
      <V2Table :rows="resRows" :cols="resCols" prefix="R" unit="份" />
      <p v-if="results.some(r => r.summary && r.summary.pending_review > 0)" class="muted">待確認的是「IP 或名稱對得上、但唯一識別碼不同」的機器（可能 IP 被回收或同名）——系統不敢自動當同一台，留給你決定。</p>
    </template>

    <template v-if="review && review.total_open">
      <h4 class="h4">待人工審核 {{ review.total_open.toLocaleString() }} 筆<InfoNote>匯入時判不準是否同一台的案子，系統不自動合併；下面兩條規則可以安全批次配對，其他要逐筆判斷。</InfoNote></h4>
      <div v-if="review.matchable" class="box">
        <p>其中 <b>{{ review.matchable.toLocaleString() }}</b> 筆可用「{{ review.rule_label }}」安全配對，剩 {{ review.remaining.toLocaleString() }} 筆仍需逐筆判斷。
          <button type="button" class="lnk" @click="show = show === 'short' ? '' : 'short'">{{ show === 'short' ? '收合範例' : '先看幾筆範例' }}</button>
          <InfoNote>只寫入 OS／虛實／VM 識別碼。你填的用途、保管者、機房、盤點單位一律不動，主機名也維持現有寫法（不會被改成 FQDN）。</InfoNote></p>
        <V2Table v-if="show === 'short'" :rows="shortRows" :cols="[{ k: 'incoming_hostname', label: 'vCenter 給的名稱', mono: true }, { k: 'incoming_ip', label: 'IP', mono: true }, { k: 'target', label: '要併進的資產' }]" prefix="A" />
        <button type="button" class="btn" :disabled="merging" @click="batchMerge('short')">{{ merging ? '合併中…' : `確認合併這 ${review.matchable.toLocaleString()} 筆` }}</button>
      </div>
      <div v-if="review.uuid_rule && review.uuid_rule.matchable" class="box">
        <p>另有 <b>{{ review.uuid_rule.matchable.toLocaleString() }}</b> 筆（＝{{ review.uuid_rule.distinct_assets.toLocaleString() }} 台機器）是「{{ review.uuid_rule.rule_label }}」。
          <button type="button" class="lnk" @click="show = show === 'uuid' ? '' : 'uuid'">{{ show === 'uuid' ? '收合範例' : '先看幾筆範例' }}</button>
          <InfoNote>這條規則只寫入 vm_uuid 一個欄位——補完之後每次 vCenter 同步都能精準對上，不會再一直生新的待審核。資產那邊 uuid 已經有值而且跟 vCenter 不同（衝突）的，一律不碰、留給人看。</InfoNote></p>
        <V2Table v-if="show === 'uuid'" :rows="uuidRows" :cols="[{ k: 'incoming', label: 'vCenter 給的名稱', mono: true }, { k: 'target', label: '要併進的資產' }, { k: 'target_ip', label: '資產目前 IP', mono: true }, { k: 'matched_on', label: '靠什麼對上' }]" prefix="U" />
        <button type="button" class="btn" :disabled="merging" @click="batchMerge('uuid')">{{ merging ? '合併中…' : `確認補上這 ${review.uuid_rule.distinct_assets.toLocaleString()} 台的 VM 識別碼` }}</button>
      </div>
      <p v-if="!review.matchable && !review.uuid_rule?.matchable" class="muted">目前沒有可安全批次配對的案子，這些需要逐筆判斷。</p>
      <p v-if="reviewMsg" class="ok">{{ reviewMsg }}</p>
    </template>

    <h4 class="h4">最近匯入紀錄</h4>
    <V2Table :rows="histRows" :cols="histCols" prefix="L" unit="次" empty="還沒有匯入紀錄" />

    <VcenterAutoImport @imported="emit('changed')" />
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.ok { color: var(--good); font-size: 13px; }
.h4 { font-size: 14px; margin: 16px 0 6px; display: flex; align-items: center; gap: 6px; }
.box { border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 12px; margin: 6px 0; font-size: 13px; background: var(--sub); }
.btn { padding: 5px 12px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 13px; }
.btn:disabled { opacity: .55; cursor: default; }
.lnk { border: 0; background: none; padding: 0 4px; font: inherit; color: var(--link); cursor: pointer; }
</style>
