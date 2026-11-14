<script setup lang="ts">
// CIA 資產清冊匯入的預覽（2.055.0，V36，待決定 5：先看影響筆數，確認後才寫入）。只負責顯示＋兩顆按鈕；
// 預覽與確認的 API 在 pages/import.vue（POST /api/import/excel/preview → /api/import/excel/confirm）。
// 數字＝後端在正式庫副本上跑同一支匯入算出來的；每個數字點下去就是下面那張清單（同一份 tables）。
const props = defineProps<{ preview: any; busy?: boolean }>()
const emit = defineEmits<{ (e: 'confirm'): void; (e: 'cancel'): void }>()
const TBL: Record<string, string> = { hardware: '硬體', personnel: '人員', software: '軟體' }
const pick = ref<{ t: string; k: 'new' | 'updated' } | null>(null)
const sumRows = computed(() => Object.entries(props.preview?.tables ?? {}).map(([t, x]: [string, any], i) => ({
  __no: i + 1, t, sheet: TBL[t] ?? x.sheet, new: x.new, updated: x.updated, unchanged: x.unchanged, rejected: x.rejected, removed: x.removed })))
const total = computed(() => sumRows.value.reduce((a, r) => a + r.new + r.updated, 0))
const errors = computed<string[]>(() => props.preview?.summary?.errors ?? [])
const invalid = computed<any[]>(() => (props.preview?.summary?.invalid_status ?? []).map((b: any, i: number) => ({ ...b, __no: i + 1 })))
const LABEL_COLS: Record<string, { k: string; label: string }[]> = {
  hardware: [{ k: 'asset_serial', label: '資產序號' }, { k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP' }],
  personnel: [{ k: 'asset_serial', label: '資產序號' }, { k: 'person_name', label: '人員姓名' }],
  software: [{ k: 'asset_serial', label: '資產序號' }, { k: 'asset_name', label: '資產名稱' }, { k: 'db_software', label: '資料庫／軟體' }],
}
const fmt = (v: any) => (v === null || v === undefined || v === '' ? '（空白）' : String(v))
const detailRows = computed(() => {
  if (!pick.value) return []
  const x = props.preview?.tables?.[pick.value.t]
  const rows = pick.value.k === 'new' ? x?.new_rows ?? [] : x?.updated_rows ?? []
  return rows.map((r: any, i: number) => ({ ...r, __no: i + 1,
    change: r.fields ? Object.entries(r.fields).map(([k, v]: [string, any]) => `${k}：${fmt(v.before)} → ${fmt(v.after)}`).join('；') : '' }))
})
const detailCols = computed(() => [...(LABEL_COLS[pick.value?.t ?? 'hardware'] ?? []), ...(pick.value?.k === 'updated' ? [{ k: 'change', label: '改了什麼（原 → 新）' }] : [])])
const detailTotal = computed(() => (pick.value ? props.preview?.tables?.[pick.value.t]?.[pick.value.k] ?? 0 : 0))
</script>

<template>
  <div class="card pv">
    <div class="bar">
      <b>預覽：{{ preview.filename }}</b>
      <span class="muted">還沒寫入。新增＋更新共 {{ total.toLocaleString() }} 筆</span>
      <InfoNote>系統把正式資料複製一份，在副本上跑同一支匯入，算出會新增、更新、沒變、拒收各幾筆；正式資料一個字都沒動。
        按「確認寫入」才會寫，而且只寫這一份檔案：預覽之後有人又匯入或改過資產、或改了欄位對應，系統會擋下來要你重新預覽（結果會跟預覽不同）。預覽 30 分鐘內有效。</InfoNote>
      <span class="sp" />
      <button type="button" class="btn" :disabled="busy" @click="emit('confirm')">{{ busy ? '寫入中…' : '確認寫入' }}</button>
      <button type="button" class="chip" :disabled="busy" @click="emit('cancel')">取消</button>
    </div>
    <V2Table :rows="sumRows" prefix="P" empty="沒有任何分頁"
             :cols="[{ k: 'sheet', label: '分頁' }, { k: 'new', label: '新增（筆）', mono: true }, { k: 'updated', label: '更新（筆）', mono: true }, { k: 'unchanged', label: '沒變（筆）', mono: true }, { k: 'rejected', label: '拒收（筆）', mono: true }]">
      <template #cell-new="{ row }"><button v-if="row.new" type="button" class="lk" @click="pick = { t: row.t, k: 'new' }">{{ row.new }}</button><span v-else>0</span></template>
      <template #cell-updated="{ row }"><button v-if="row.updated" type="button" class="lk" @click="pick = { t: row.t, k: 'updated' }">{{ row.updated }}</button><span v-else>0</span></template>
    </V2Table>
    <p v-if="sumRows.some(r => r.removed)" class="err">副本裡有資料不見了（匯入不應該刪資料），請不要寫入並回報開發。</p>
    <template v-if="pick">
      <div class="bar"><b>{{ TBL[pick.t] }}・{{ pick.k === 'new' ? '新增' : '更新' }} {{ detailTotal }} 筆</b>
        <span v-if="detailRows.length < detailTotal" class="muted">（列出前 {{ detailRows.length }} 筆）</span>
        <button type="button" class="chip" @click="pick = null">收起</button></div>
      <V2Table :rows="detailRows" :cols="detailCols" prefix="R" :max="500" />
    </template>
    <details v-if="errors.length" class="sub"><summary>拒收與訊息 {{ errors.length }} 則</summary>
      <ol><li v-for="(e, i) in errors" :key="i">{{ e }}</li></ol></details>
    <template v-if="invalid.length">
      <p class="muted">資產狀態不是合法值 {{ invalid.length }} 筆（這幾筆的資產狀態不寫入，保留系統原值）</p>
      <V2Table :rows="invalid" prefix="S" :cols="[{ k: 'asset_serial', label: '資產序號' }, { k: 'value', label: '檔案裡的值' }]" />
    </template>
  </div>
</template>

<style scoped>
.pv { margin: 12px 0; padding: 12px 14px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--card); }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sp { flex: 1; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.lk { border: 0; background: none; padding: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; text-decoration: underline; }
.sub { font-size: 13px; margin: 8px 0; }
</style>
