<script setup lang="ts">
// 資料匯入 → CIA 補登（負責人交回）（P2 試辦）：兩段式，跟 9.3 修正包交回同一個習慣。
// 預覽（POST /api/cia-reg/upload/preview，不寫）→ 逐列：通過／退回（缺什麼）／說不登記／拒收（為什麼）→ 確認記錄（同一份檔＋雜湊）。
// 同一份檔記過一次，再按確認只回當時結果（冪等）。拒收的列不記；鎖定欄被改、舊版本（已改派）、不是這個人的列都拒收。
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
function pick(e: Event) { file.value = (e.target as HTMLInputElement).files?.[0] ?? null; pv.value = null; res.value = null }
async function doPreview() {
  if (!file.value) { err.value = '請先選擇負責人交回的 .xlsx'; return }
  busy.value = true; err.value = ''; res.value = null
  try { const fd = new FormData(); fd.append('file', file.value); pv.value = await apiFetch<any>('/api/cia-reg/upload/preview', { method: 'POST', body: fd }) } catch (e: any) { err.value = errMsg(e, '預覽失敗'); showToast(err.value, 'error') } finally { busy.value = false }
}
async function doConfirm() {
  if (!file.value || !pv.value) return
  const nn = pv.value.n
  if (!window.confirm(`記錄 ${pv.value.owner_name ?? pv.value.owner} 的交回：通過 ${nn.pass}、退回 ${nn.bounce}、說不登記 ${nn.claim}？${nn.refused ? `另有 ${nn.refused} 列拒收，不記錄。` : ''}`)) return
  busy.value = true; err.value = ''
  try {
    const fd = new FormData(); fd.append('file', file.value); fd.append('file_sha256', pv.value.file_sha256)
    res.value = await guardedFetch<any>('/api/cia-reg/upload/confirm', { method: 'POST', body: fd })
    showToast(res.value.already ? '這份檔之前已記錄過，沒有重複記錄' : `已記錄：通過 ${res.value.n.pass}、退回 ${res.value.n.bounce}`, res.value.n.refused ? 'warn' : 'success')
    pv.value = null; file.value = null; emit('changed')
  } catch (e: any) { err.value = errMsg(e, '記錄失敗'); showToast(err.value, 'error') } finally { busy.value = false }
}
const cur = computed(() => res.value ?? pv.value)
const show = ref('bounce')
const KINDS: Record<string, string> = { pass: '通過', bounce: '退回', claim: '說不登記', refused: '拒收', same: '跟上次一樣' }
const cols = [{ k: 'row', label: 'Excel 列', mono: true }, { k: 'track_code', label: '追蹤碼', mono: true }, { k: 'hostname', label: '主機名稱' }, { k: 'what', label: '說明' }]
const rows = computed(() => (cur.value?.[show.value] ?? []).map((x: any) => ({ ...x, __no: x.row,
  what: Array.isArray(x.why) ? x.why.join('；') : (x.why ?? (x.reason ? `不登記：${x.reason}` : (x.apid ? `APID ${x.apid}` : ''))) })))
</script>

<template>
  <div>
    <div class="bar">
      <input type="file" accept=".xlsx" aria-label="選擇負責人交回的補登檔" @change="pick">
      <button v-if="!isViewer" type="button" class="btn" :disabled="busy || !file" @click="doPreview">{{ busy && !pv ? '讀取中…' : '預覽' }}</button>
      <NuxtLink class="lnk" to="/v2/ciareg">9.4 CIA 補登</NuxtLink>
      <InfoNote>上傳負責人填好的補登檔（9.4 下載的那份）。先預覽：通過、退回（缺哪幾格、選項外的值）、說不登記、拒收（追蹤碼對不到、鎖定欄被改、已改派的舊檔、含巨集或多出工作表）。
        帶出欄的公式不採用，系統依交回的 APID 重新算一次。按「確認記錄」才寫入；同一份檔記過一次不會重複記錄。只記錄補登資料，不改任何資產資料。上傳後請刪除這份檔案（含個人資料）。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="cur">
      <div class="bar"><b>{{ cur.file_name }}</b><span class="muted">負責人 {{ cur.owner_name || cur.owner || '（不明）' }}・共 {{ cur.rows }} 列</span>
        <span v-if="pv?.already" class="warn">這份檔 {{ String(pv.already.at).slice(0, 16) }} 已由 {{ pv.already.by }} 記錄過</span>
        <span class="sp" />
        <button v-if="pv && !isViewer" type="button" class="btn" :disabled="busy" @click="doConfirm">{{ busy ? '記錄中…' : '確認記錄' }}</button></div>
      <p v-if="res" class="ok">已記錄。<NuxtLink class="lnk" :to="`/v2/ciareg?tab=items&who=${encodeURIComponent(res.owner)}`">看 {{ res.owner_name ?? res.owner }} 的機器</NuxtLink></p>
      <div class="bar"><button v-for="(lab, k) in KINDS" :key="k" type="button" class="chip" :class="{ on: show === k }" @click="show = k">{{ lab }} {{ cur.n[k] }}</button></div>
      <V2Table :rows="rows" :cols="cols" prefix="U" row-key="__no" unit="列" empty="沒有" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sp { flex: 1; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.ok { color: var(--good); font-size: 13px; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 8px; padding: 2px 8px; }
.lnk { color: var(--link); font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .6; cursor: default; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; font-family: inherit; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
</style>
