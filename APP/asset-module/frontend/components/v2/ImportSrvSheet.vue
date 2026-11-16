<script setup lang="ts">
// 資料匯入 → 伺服器資料彙整表（參考來源，2.060.20）：兩段式。預覽（不寫）→ 確認（同一份檔＋sha256）→ 整份取代上一份。
// 只當證據（9.4「彙整表比對」）：不改任何資產資料、不改 CIA。
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
const done = ref<any>(null)
function pick(e: Event) { file.value = (e.target as HTMLInputElement).files?.[0] ?? null; pv.value = null; done.value = null; err.value = '' }
async function doPreview() {
  if (!file.value) { err.value = '請先選擇彙整表 .xlsx'; return }
  busy.value = true; err.value = ''
  try { const fd = new FormData(); fd.append('file', file.value); pv.value = await apiFetch<any>('/api/srv-sheet/preview', { method: 'POST', body: fd }) } catch (e: any) { err.value = errMsg(e, '預覽失敗'); showToast(err.value, 'error') } finally { busy.value = false }
}
async function doConfirm() {
  if (!file.value || !pv.value) return
  const cur = pv.value.current
  if (!window.confirm(`匯入 ${pv.value.rows} 列（已關機 ${pv.value.off_rows} 筆）？${cur ? `\n會取代目前那一份（${String(cur.imported_at).slice(0, 16)}，${cur.rows} 列）。` : ''}\n只當參考，不改任何資產資料。`)) return
  busy.value = true; err.value = ''
  try {
    const fd = new FormData(); fd.append('file', file.value); fd.append('file_sha256', pv.value.sha256)
    done.value = await guardedFetch<any>('/api/srv-sheet/confirm', { method: 'POST', body: fd })
    showToast(`已匯入 ${done.value.batch.rows} 列`, 'success')
    pv.value = null; file.value = null; emit('changed')
  } catch (e: any) { err.value = errMsg(e, '匯入失敗'); showToast(err.value, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div>
    <div class="bar">
      <input type="file" accept=".xlsx" aria-label="選擇伺服器資料彙整表" data-testid="srv-file" @change="pick">
      <button v-if="!isViewer" type="button" class="btn" :disabled="busy || !file" @click="doPreview">{{ busy && !pv ? '讀取中…' : '預覽' }}</button>
      <NuxtLink class="lnk" to="/v2/ciareg?tab=srv">9.4 彙整表比對</NuxtLink>
      <InfoNote>主機組維護的「伺服器資料彙整表」。只讀「伺服器 (主)」（第 2 列中文表頭）與「已關機待處理」兩張分頁，其他分頁不讀。有密碼的檔請先在 Excel 另存一份不加密的再上傳（系統不收、也不存密碼）。
        每次匯入整份取代上一份；只當參考證據（9.4 彙整表比對：表有、CIA 無／欄位不一致／同列矛盾／已關機），不改任何資產資料、不改 CIA。檔案含公司內部伺服器清單，上傳後請刪除本機那份。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <div v-if="pv" class="bar">
      <b>{{ pv.file_name }}</b>
      <span class="muted">「{{ pv.main_sheet }}」{{ pv.rows }} 列（有 IP {{ pv.with_ip }}、IP 空白或格式錯 {{ pv.no_ip }}）・「{{ pv.off_sheet || '沒有已關機分頁' }}」{{ pv.off_rows }} 筆・最新標記時間 {{ pv.data_date || '—' }}</span>
      <span class="sp" />
      <button v-if="!isViewer" type="button" class="btn" :disabled="busy" data-testid="srv-confirm" @click="doConfirm">{{ busy ? '匯入中…' : '確認匯入' }}</button>
    </div>
    <p v-if="done" class="ok">已匯入。<NuxtLink class="lnk" to="/v2/ciareg?tab=srv">到 9.4 看比對結果</NuxtLink></p>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sp { flex: 1; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.ok { color: var(--good); font-size: 13px; }
.lnk { color: var(--link); font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .6; cursor: default; }
</style>
