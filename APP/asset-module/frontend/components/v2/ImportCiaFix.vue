<script setup lang="ts">
// 資料匯入 → CIA 修正包（管理人交回）（2.060.0）：兩段式，跟 CIA 清冊同一個習慣。
// 預覽（POST /api/cia-fix/upload/preview，不寫）→ 看是哪一批哪一包、會記哪些決定＋差異報告（被改過的列、不合格的列、查無追蹤碼、已結案）
// → 確認記錄（POST /api/cia-fix/upload/confirm，同一份檔＋雜湊）。被改過／不合格的列不記，其餘照記；預覽與結果都明列被擋的列與這幾包剩下的待決定。
// 不是本系統產的包（沒有任何一列追蹤碼對得上）後端整份拒收。
const emit = defineEmits<{ changed: [] }>()
const { guardedFetch } = useGuardedFetch()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const errMsg = (e: any, d: string) => { const x = e?.data?.detail; return typeof x === 'string' ? x : x?.message ?? d }
const file = ref<File | null>(null)
const busy = ref(false)
const err = ref('')
const pv = ref<any>(null)
const res = ref<any>(null)
const show = ref('')       // 「沒填」「跟目前一樣」「剩下待決定」：點數字才展開那張表
function pick(e: Event) { file.value = (e.target as HTMLInputElement).files?.[0] ?? null; pv.value = null; res.value = null }
async function doPreview() {
  if (!file.value) { err.value = '請先選擇管理人交回的 .xlsx'; return }
  busy.value = true; err.value = ''; res.value = null; show.value = ''
  try { const fd = new FormData(); fd.append('file', file.value); pv.value = await apiFetch<any>('/api/cia-fix/upload/preview', { method: 'POST', body: fd }) } catch (e: any) { err.value = errMsg(e, '預覽失敗'); showToast(err.value, 'error') } finally { busy.value = false }
}
async function doConfirm() {
  if (!file.value || !pv.value) return
  const blocked = pv.value.tampered.length + bad(pv.value).length
  if (!window.confirm(`記錄 ${pv.value.record.length} 筆決定？${blocked ? `另有 ${blocked} 列被擋，不會記錄。` : ''}`)) return
  busy.value = true; err.value = ''; show.value = ''
  try {
    const fd = new FormData(); fd.append('file', file.value); fd.append('file_sha256', pv.value.file_sha256)
    res.value = await guardedFetch<any>('/api/cia-fix/upload/confirm', { method: 'POST', body: fd })
    showToast(`已記錄 ${res.value.recorded} 筆決定`, res.value.skipped?.length || res.value.tampered?.length ? 'warn' : 'success')
    pv.value = null; file.value = null; emit('changed')
  } catch (e: any) { err.value = errMsg(e, '記錄失敗'); showToast(err.value, 'error') } finally { busy.value = false }
}
const cur = computed(() => res.value ?? pv.value)
const pkgCols = [{ k: 'name', label: '批次／包' }, { k: 'unit', label: '負責單位' }, { k: 'at', label: '產生時間', mono: true }, { k: 'created_by', label: '產生者' },
  { k: 'cia_data_date', label: 'CIA 匯出日期', mono: true }, { k: 'rows', label: '筆數', mono: true }, { k: 'pending', label: '目前待決定', mono: true }]
const pkgRows = computed(() => (cur.value?.packages ?? []).map((p: any, i: number) => ({ ...p, __no: i + 1, name: `第 ${p.batch_id} 批・第 ${p.pkg_no} 包`, at: (p.created_at || '').slice(0, 16) })))
const recCols = [{ k: 'row', label: 'Excel 列', mono: true }, { k: 'track_code', label: '追蹤碼', mono: true }, { k: 'asset_serial', label: '資產序號', mono: true },
  { k: 'field_label', label: '欄位' }, { k: 'old_value', label: '原值' }, { k: 'newv', label: '新值' }, { k: 'chg', label: '決定' }, { k: 'reject_code', label: '不同意原因' }, { k: 'note', label: '備註' }]
const recRows = computed(() => (pv.value?.record ?? []).map((x: any) => ({ ...x, __no: x.row, newv: x.new_value ?? '（請人填正確值）', chg: `${x.from} → ${x.to_label}` })))
const tamCols = [{ k: 'row', label: 'Excel 列', mono: true }, { k: 'track_code', label: '追蹤碼', mono: true }, { k: 'asset_serial', label: '資產序號', mono: true }, { k: 'what', label: '被改了什麼（檔案 ← 系統）' }]
const tam = (src: any) => (src?.tampered ?? []).map((x: any) => ({ ...x, __no: x.row, what: x.diffs.map((d: any) => `${d.col}：「${d.file}」← 「${d.system}」`).join('\n') }))
const badCols = [{ k: 'row', label: 'Excel 列', mono: true }, { k: 'track_code', label: '追蹤碼', mono: true }, { k: 'asset_serial', label: '資產序號', mono: true }, { k: 'why', label: '為什麼不記錄' }]
const bad = (src: any) => [
  ...(src?.invalid ?? []).map((x: any) => ({ ...x })),
  ...(src?.unknown ?? []).map((x: any) => ({ ...x, why: '查無這個追蹤碼（不是系統產生的列、或被改過）' })),
  ...(src?.closed ?? []).map((x: any) => ({ ...x, why: `已結案（${x.status}），不改決定` })),
  ...(src?.skipped ?? []).map((x: any) => ({ ...x, why: x.why })),
].map((x: any) => ({ ...x, __no: x.row ?? 0 }))
// 「沒填」「跟目前一樣」「剩下待決定」三張表同一種欄（可下鑽、可匯出）
const listCols = [{ k: 'row_t', label: 'Excel 列', mono: true }, { k: 'track_code', label: '追蹤碼', mono: true },
  { k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` },
  { k: 'hostname', label: '主機名稱' }, { k: 'field_label', label: '欄位' }, { k: 'old_value', label: '原值' }, { k: 'newv', label: '新值' }, { k: 'status_label', label: '目前狀態' }, { k: 'extra', label: '說明' }]
const listOf = (k: string) => (cur.value?.[k] ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, row_t: x.row || '—', newv: x.new_value ?? '（請人填正確值）',
  extra: k === 'same' ? `檔案也是「${x.decision}」${x.reject_code ? `（${x.reject_code}）` : ''}` : k === 'still_pending' ? x.pkg : '管理人沒填同意／不同意' }))
const LIST_LABEL: Record<string, string> = { blank: '沒填', same: '跟目前一樣', still_pending: '這幾包剩下待決定' }
const toggle = (k: string) => { show.value = show.value === k ? '' : k }
</script>

<template>
  <div>
    <div class="bar">
      <input type="file" accept=".xlsx" aria-label="選擇管理人交回的修正包" @change="pick">
      <button v-if="!isViewer" type="button" class="btn" :disabled="busy || !file" @click="doPreview">{{ busy && !pv ? '讀取中…' : '預覽' }}</button>
      <NuxtLink class="lnk" to="/v2/ciafix">9.3 CIA 修正包</NuxtLink>
      <InfoNote>上傳 CIA 管理人勾好「同意／不同意」的修正包（系統產生的那份 Excel；不是本系統產的檔會整份拒收）。先預覽：是哪一批哪一包、會記錄哪些決定、哪些列被改過（原值、新值、資產序號、欄位跟系統紀錄不同）、哪些列不合格（不同意沒選原因…）。
        被改過或不合格的列不會記錄，其他正常的列照樣記錄；請把被擋的列退回給管理人修正後再交一次。同一份檔交兩次不會重複記錄。按「確認記錄」才寫入，只記錄決定，不改任何資產資料。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="cur">
      <V2Table :rows="pkgRows" :cols="pkgCols" prefix="K" row-key="name" unit="包" />
      <div v-if="pv" class="bar"><b>{{ pv.file_name }}</b>
        <span class="muted">共 {{ pv.rows }} 列・會記錄 同意 {{ pv.agree }}／不同意 {{ pv.reject }}・被改過 {{ pv.tampered.length }}・不記錄 {{ bad(pv).length }}</span>
        <span class="sp" />
        <button v-if="!isViewer" type="button" class="btn" :disabled="busy || !pv.record.length" @click="doConfirm">{{ busy ? '記錄中…' : `確認記錄 ${pv.record.length} 筆` }}</button></div>
      <p v-else class="ok">已記錄 {{ res.recorded }} 筆決定<template v-if="res.tampered.length || bad(res).length">，{{ res.tampered.length + bad(res).length }} 列被擋沒記（見下表）</template>。
        <NuxtLink class="lnk" :to="`/v2/ciafix?tab=items&upload=${res.file_sha256}`">看這次記錄的 {{ res.recorded }} 筆</NuxtLink></p>
      <div class="bar">
        <button v-for="k in ['blank', 'same', 'still_pending']" :key="k" type="button" class="chip" :class="{ on: show === k }" :disabled="!(cur[k] ?? []).length" @click="toggle(k)">
          {{ LIST_LABEL[k] }} {{ (cur[k] ?? []).length }}</button>
      </div>
      <template v-if="show"><h4 class="h4">{{ LIST_LABEL[show] }}</h4><V2Table :rows="listOf(show)" :cols="listCols" prefix="N" row-key="__no" /></template>
      <template v-if="cur.tampered.length"><h4 class="h4">被改過的列（不記錄）</h4><V2Table :rows="tam(cur)" :cols="tamCols" prefix="T" row-key="row" /></template>
      <template v-if="bad(cur).length"><h4 class="h4">不記錄的列</h4><V2Table :rows="bad(cur)" :cols="badCols" prefix="Q" row-key="row" /></template>
      <template v-if="pv"><h4 class="h4">會記錄的決定</h4>
        <V2Table :rows="recRows" :cols="recCols" prefix="D" row-key="row" empty="沒有新的決定（全部沒填、或跟目前一樣）" /></template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 4px 0 10px; font-size: 13px; }
.sp { flex: 1; }
.h4 { margin: 12px 0 4px; font-size: 14px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.ok { color: var(--good); font-size: 13px; }
.lnk { color: var(--link); }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip:disabled { opacity: .5; cursor: default; }
.btn { padding: 5px 12px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 13px; }
.btn:hover:not(:disabled) { background: var(--brand-hover); }
.btn:disabled { opacity: .6; cursor: default; }
</style>
