<script setup lang="ts">
// 「待人工審核」處理區（H16：由資料匯入／RVTools 頁搬到 9.1 待補資料；要人處理的事放在做事的地方）。
// 從 ImportRv.vue 原樣搬來（⚠D4 不改寫入邏輯）：GET /api/merge-reviews/auto-matchable；批次合併 POST /api/merge-reviews/batch-merge
// （先 confirm 講清楚筆數與只寫哪幾欄，確認句、body 一字不改）。合併後 emit('changed')，筆數即時更新。
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { canWrite } = useAuth()
const detail = (e: any, d: string) => { const x = e?.data?.detail; return (typeof x === 'string' ? x : x?.message) ?? d }

const review = ref<any>(null)
const loadErr = ref('')
const loaded = ref(false)
const merging = ref(false)
const reviewMsg = ref('')
const show = ref<'' | 'short' | 'uuid'>('')
async function loadReview() {
  try { review.value = await apiFetch('/api/merge-reviews/auto-matchable'); loadErr.value = '' }
  catch (e: any) { review.value = null; loadErr.value = `待人工審核載入失敗：${detail(e, '未知錯誤')}` }
  finally { loaded.value = true }
}
onMounted(loadReview)
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
  <div data-testid="merge-review">
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <p v-else-if="!loaded" class="muted">載入中…</p>
    <template v-else-if="review && review.total_open">
      <h4 class="h4">待人工審核 {{ review.total_open.toLocaleString() }} 筆<InfoNote>匯入時判不準是否同一台的案子，系統不自動合併；下面兩條規則可以安全批次配對，其他要逐筆判斷。</InfoNote></h4>
      <div v-if="review.matchable" class="box">
        <p>其中 <b>{{ review.matchable.toLocaleString() }}</b> 筆可用「{{ review.rule_label }}」安全配對，剩 {{ review.remaining.toLocaleString() }} 筆仍需逐筆判斷。
          <button type="button" class="lnk" @click="show = show === 'short' ? '' : 'short'">{{ show === 'short' ? '收合範例' : '先看幾筆範例' }}</button>
          <InfoNote>只寫入 OS／虛實／VM 識別碼。你填的用途、保管者、機房、盤點單位一律不動，主機名也維持現有寫法（不會被改成 FQDN）。</InfoNote></p>
        <V2Table v-if="show === 'short'" :rows="shortRows" :cols="[{ k: 'incoming_hostname', label: 'vCenter 給的名稱', mono: true }, { k: 'incoming_ip', label: 'IP', mono: true }, { k: 'target', label: '要併進的資產' }]" prefix="A" />
        <button v-if="canWrite" type="button" class="btn" :disabled="merging" @click="batchMerge('short')">{{ merging ? '合併中…' : `確認合併這 ${review.matchable.toLocaleString()} 筆` }}</button>
      </div>
      <div v-if="review.uuid_rule && review.uuid_rule.matchable" class="box">
        <p>另有 <b>{{ review.uuid_rule.matchable.toLocaleString() }}</b> 筆（＝{{ review.uuid_rule.distinct_assets.toLocaleString() }} 台機器）是「{{ review.uuid_rule.rule_label }}」。
          <button type="button" class="lnk" @click="show = show === 'uuid' ? '' : 'uuid'">{{ show === 'uuid' ? '收合範例' : '先看幾筆範例' }}</button>
          <InfoNote>這條規則只寫入 vm_uuid 一個欄位——補完之後每次 vCenter 同步都能精準對上，不會再一直生新的待審核。資產那邊 uuid 已經有值而且跟 vCenter 不同（衝突）的，一律不碰、留給人看。</InfoNote></p>
        <V2Table v-if="show === 'uuid'" :rows="uuidRows" :cols="[{ k: 'incoming', label: 'vCenter 給的名稱', mono: true }, { k: 'target', label: '要併進的資產' }, { k: 'target_ip', label: '資產目前 IP', mono: true }, { k: 'matched_on', label: '靠什麼對上' }]" prefix="U" />
        <button v-if="canWrite" type="button" class="btn" :disabled="merging" @click="batchMerge('uuid')">{{ merging ? '合併中…' : `確認補上這 ${review.uuid_rule.distinct_assets.toLocaleString()} 台的 VM 識別碼` }}</button>
      </div>
      <p v-if="!review.matchable && !review.uuid_rule?.matchable" class="muted">目前沒有可安全批次配對的案子，這些需要逐筆判斷。</p>
      <p v-if="!canWrite" class="muted" data-testid="merge-review-readonly">你的帳號是唯讀，不能批次合併。</p>
      <p v-if="reviewMsg" class="ok">{{ reviewMsg }}</p>
    </template>
    <p v-else class="muted" data-testid="merge-review-none">沒有待人工審核的案子</p>
  </div>
</template>

<style scoped>
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
.ok { color: var(--good); font-size: 13px; }
.h4 { font-size: 14px; margin: 8px 0 6px; display: flex; align-items: center; gap: 6px; }
.box { border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 12px; margin: 6px 0; font-size: 13px; background: var(--sub); }
.btn { padding: 5px 12px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 13px; }
.btn:disabled { opacity: .55; cursor: default; }
.lnk { border: 0; background: none; padding: 0 4px; font: inherit; color: var(--link); cursor: pointer; }
</style>
