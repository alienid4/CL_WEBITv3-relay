<script setup lang="ts">
// S3 同機多筆：一案的逐欄確認（2.061.1）。每個不同的欄選要取哪一筆的值（預設＝系統建議；平手沒有預設，要人選）＋保留哪一筆。
// 只有「待確認」能確認；已確認要改就先撤回。確認只記錄，不改任何資產資料。
const props = defineProps<{ c: any }>()
const emit = defineEmits<{ done: [] }>()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const d = computed(() => props.c?.detail ?? {})
const serials = computed<string[]>(() => d.value.serials ?? [])
const dec = computed(() => props.c?.decision ?? null)
const keep = ref(''); const pick = ref<Record<string, string>>({}); const note = ref(''); const busy = ref(false)
watch(() => props.c?.id, () => {
  keep.value = dec.value?.keep ?? d.value.keep ?? serials.value[0] ?? ''
  pick.value = Object.fromEntries((d.value.diffs ?? []).map((x: any) => [x.field, dec.value?.fields?.[x.field] ?? x.suggest ?? '']))
}, { immediate: true })
const editable = computed(() => props.c?.status === 'open' && isAdmin.value)     // 確認＝管理員
const rows = computed(() => (d.value.diffs ?? []).map((x: any, i: number) => ({
  __no: i + 1, field: x.field, label: x.label, why: x.why,
  ...Object.fromEntries(serials.value.map(s => [`v_${s}`, x.values?.[s] || '（空白）'])),
})))
const cols = computed(() => [{ k: 'label', label: '欄位' }, ...serials.value.map(s => ({ k: `v_${s}`, label: s })), { k: 'why', label: '建議依據' }, { k: 'choose', label: '取哪一筆' }])
const recRows = computed(() => (d.value.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, filled: d.value.filled?.[r.asset_serial] ?? '—' })))
const recCols = [{ k: 'asset_serial', label: '資產序號', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'hostname', label: '主機名稱' },
  { k: 'ip', label: 'IP' }, { k: 'api_id', label: 'API ID' }, { k: 'asset_name', label: '資產名稱' }, { k: 'filled', label: '有值的欄數' }]
const missing = computed(() => (d.value.diffs ?? []).filter((x: any) => !pick.value[x.field]).length)
async function confirm() {
  if (missing.value) { showToast(`還有 ${missing.value} 個欄位沒選`, 'error'); return }
  if (!window.confirm(`確認：保留 ${keep.value}，其他 ${serials.value.length - 1} 筆請 CIA 停用或併入？（只記錄，交出後才進修正包）`)) return
  busy.value = true
  try {
    const r = await guardedFetch<any>('/api/cia-case/dup/confirm', { method: 'POST', body: { id: props.c.id, keep: keep.value, fields: pick.value, note: note.value || null, seen_last: props.c.last_seen ?? null } })
    showToast(r.updated ? '已確認' : `沒處理：${r.skipped?.[0]?.why ?? ''}`, r.updated ? 'success' : 'warn')
    emit('done')
  } catch (e: any) { showToast(`確認失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div>
    <div class="bar"><b>{{ d.dup_kind_label ?? '—' }}</b><span class="muted">{{ serials.length }} 筆・不同欄位 {{ (d.diffs ?? []).length }} 個</span>
      <InfoNote>配對依據：machine_key {{ c.match_basis?.machine_key }}（口徑 {{ c.match_basis?.mode ?? '—' }}，{{ (c.matched_at || '').slice(0, 16) }}）。
        建議依序：只有一筆有值取它、多數筆的值、欄位最齊那筆；都分不出來＝平手，要人選。保留筆預設欄位最齊，一樣齊取資產序號最早。WEBIT3 不刪任何一筆，交出後由 CIA 管理人改。</InfoNote></div>
    <V2Table :rows="recRows" :cols="recCols" prefix="R" row-key="asset_serial" unit="筆" />
    <V2Table :rows="rows" :cols="cols" prefix="F" row-key="field" unit="個欄位" empty="各筆內容一樣（只差資產序號）">
      <template #cell-choose="{ row }">
        <select v-model="pick[(row as any).field]" :disabled="!editable" :aria-label="`${(row as any).label} 取哪一筆`">
          <option value="">請選</option><option v-for="s in serials" :key="s" :value="s">{{ s }}</option></select>
      </template>
    </V2Table>
    <form v-if="editable" class="actbar" @submit.prevent="confirm">
      <label>保留 <select v-model="keep" aria-label="保留哪一筆"><option v-for="s in serials" :key="s" :value="s">{{ s }}</option></select></label>
      <input v-model="note" class="note" placeholder="備註（選填）" aria-label="備註">
      <button type="submit" class="btn" :disabled="busy || !!missing">{{ busy ? '確認中…' : missing ? `還有 ${missing} 欄沒選` : '確認這一案' }}</button>
    </form>
    <p v-else-if="dec" class="muted">已確認：保留 {{ dec.keep }}</p>
  </div>
</template>

<style scoped>
.muted { color: var(--muted); font-size: 13px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.actbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); font-size: 13px; }
select, .note { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.note { width: 240px; max-width: 100%; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .6; cursor: default; }
</style>
