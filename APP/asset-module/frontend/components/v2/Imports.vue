<script setup lang="ts">
// ⚙ 系統設定 → 資料匯入（V36，2.059.1；設計 docs/ui-v2/V36資料匯入_設計.md）：舊 pages/import.vue 整頁改 V2 原生。
// 純搬移（⚠D4）：每種來源用舊頁同一支 API、同樣的確認流程，匯入／寫入邏輯一行不改；CIA 兩段式（2.055.0）原樣。
// 第一眼＝總覽表（一列一個來源：目前筆數、上次匯入、格式）；點來源進那個來源自己的一頁（網址 ?st=import&src=<key>）。
// 單據、合約、值班電話的上傳在各自頁面（要先預覽或是文件），這裡只列筆數與時間並連過去（跟舊頁一樣）。
import { IMPORT_SOURCES, dataDate, lastText, downloadVia } from '~/utils/importV2'
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const rt = useRuntimeConfig()
const apiBase = (rt.public as any).apiBase || ''
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')

// ---- 每種來源的數字（都是舊頁同一支 API；讀不到就顯示「—」，不擋整頁）----
const exportSources = ref<any[]>([])
const lastImport = ref<any>(null)
const certSummary = ref<any>(null)
const acInfo = ref<any>(null)
const adSummary = ref<any>(null)
const aaStatus = ref<any>(null)
const clsCount = ref<any>(null)
const catLast = ref<any>(null)
const segCount = ref<number | null>(null)
const docSum = ref<any>(null)
const ctSum = ref<any>(null)
const relLocs = ref<{ loc: string; n: number }[]>([])
const cfSum = ref<any>(null)
const crSum = ref<any>(null)
const ssSum = ref<any>(null)
const loadErr = ref('')
const soft = async (p: Promise<any>, set: (v: any) => void) => { try { set(await p) } catch { /* 只是這一列的數字，讀不到顯示「—」 */ } }
async function reload() {
  loadErr.value = ''
  await Promise.all([
    apiFetch<any>('/api/import/sources').then(r => { exportSources.value = r.sources ?? [] }).catch((e: any) => { loadErr.value = `來源清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }),
    soft(apiFetch('/api/import/last'), v => { lastImport.value = v }),
    soft(apiFetch('/api/certs/summary'), v => { certSummary.value = v }),
    soft(apiFetch('/api/v2/alert-contacts'), v => { acInfo.value = v }),
    soft(apiFetch('/api/ad/summary'), v => { adSummary.value = v }),
    soft(apiFetch('/api/account-audit/status'), v => { aaStatus.value = v }),
    soft(apiFetch('/api/business-systems/class-count'), v => { clsCount.value = v }),
    soft(apiFetch('/api/reports/system-category/status'), (v: any) => { catLast.value = v?.last_import ?? null }),
    soft(apiFetch('/api/segments'), (v: any) => { segCount.value = v?.segments?.length ?? null }),
    soft(apiFetch('/api/documents/summary'), v => { docSum.value = v }),
    soft(apiFetch('/api/contacts/import-status'), v => { ctSum.value = v }),
    soft(apiFetch('/api/relocation/locations'), (v: any) => { relLocs.value = v?.items ?? [] }),
    soft(apiFetch('/api/cia-fix/summary'), v => { cfSum.value = v }),
    soft(apiFetch('/api/cia-reg/summary'), v => { crSum.value = v }),
    soft(apiFetch('/api/srv-sheet/summary'), v => { ssSum.value = v }),
  ])
}
onMounted(reload)

const src = (key: string) => exportSources.value.find(s => s.key === key)
const lastOf = (key: string) => { const x = src(key); return lastText(x?.last_imported_at, x?.data_date) }
function countOf(key: string): number | null {
  switch (key) {
    case 'certs': return certSummary.value ? certSummary.value.total_active + certSummary.value.inactive : null
    case 'alert_contact': return acInfo.value?.import?.rows ?? null
    case 'ad': return adSummary.value?.total ?? null
    case 'account_audit': return aaStatus.value?.latest?.row_count ?? null
    case 'sys_class': return clsCount.value?.rated ?? null
    case 'syscat': return catLast.value?.accepted ?? null
    case 'segments': return segCount.value
    case 'documents': return docSum.value?.total ?? null
    case 'contracts': return ctSum.value?.contracts?.rows ?? null
    case 'roster': return ctSum.value?.roster?.systems ?? null
    case 'relocation': return null
    case 'cia_fix': return cfSum.value ? (cfSum.value.counts?.total ?? 0) : null
    case 'cia_reg': return crSum.value ? (crSum.value.counts?.total ?? 0) : null
    case 'srv_sheet': return ssSum.value ? (ssSum.value.batch?.rows ?? 0) : null
    default: return src(key) ? src(key).row_count : null
  }
}
function lastFor(key: string): string {
  switch (key) {
    case 'certs': { const i = certSummary.value?.import_info; return i?.last_import_at ? `${i.last_import_local ?? i.last_import_at}${i.last_import_file ? `（${i.last_import_file}）` : ''}` : '—' }
    case 'alert_contact': return acInfo.value?.import ? `${acInfo.value.import.at}（${acInfo.value.import.file}）` : '—'
    case 'ad': return lastText(adSummary.value?.batch?.imported_at, dataDate(adSummary.value?.batch?.file_name))
    case 'account_audit': return lastText(aaStatus.value?.latest?.imported_at, dataDate(aaStatus.value?.latest?.file_name))
    case 'syscat': return catLast.value?.at ?? '—'
    case 'documents': return docSum.value?.last_imported_at ?? '—'
    case 'contracts': return ctSum.value?.contracts?.last_imported_at ?? '—'
    case 'roster': return ctSum.value?.roster?.last_imported_at ?? '—'
    case 'cia_fix': return cfSum.value?.last_reconcile?.at ? `自動核對 ${String(cfSum.value.last_reconcile.at).slice(0, 16)}` : '—'
    case 'cia_reg': return crSum.value?.last_reconcile?.at ? `自動核對 ${String(crSum.value.last_reconcile.at).slice(0, 16)}` : '—'
    case 'srv_sheet': return ssSum.value?.batch ? lastText(ssSum.value.batch.imported_at, ssSum.value.batch.data_date) : '—'
    case 'sys_class': case 'segments': case 'relocation': return '—'
    default: return lastOf(key)
  }
}
const panelUrl = (key: string) => `/v2/settings?st=import&src=${encodeURIComponent(key)}`
const rows = computed(() => IMPORT_SOURCES.map((s, i) => ({
  ...s, __no: i + 1, count: countOf(s.key), last: lastFor(s.key),
  go: s.link ? '到上傳的地方' : '匯入／匯出', to: s.link ?? panelUrl(s.key),
})))
const cols = [
  { k: 'label', label: '來源', link: (r: any) => r.to },
  { k: 'count', label: '目前筆數', mono: true, link: (r: any) => (r.count === null ? null : r.to) },
  { k: 'last', label: '上次匯入（資料日期）', mono: true },
  { k: 'fmt', label: '檔案格式' },
  { k: 'go', label: '動作', link: (r: any) => r.to },
]
const current = computed(() => IMPORT_SOURCES.find(s => s.key === q('src') && !s.link) ?? null)

// ---- 匯出資產（含人員通訊錄）：舊頁頁首那顆，同一支 /api/export ----
const includeOffBook = ref(false)
const exporting = ref(false)
async function downloadExport() {
  exporting.value = true
  try {
    await downloadVia(apiBase, `/api/export${includeOffBook.value ? '?include_off_book=true' : ''}`, 'assets_export.xlsx')
    showToast(includeOffBook.value ? '已匯出資產資料（含 CIA 待補登的機器，「來源」欄可篩出哪些是你們清單上沒有的）' : '已匯出資產資料（僅 CIA 登記，未含 CIA 待補登的機器）', 'success')
  } catch (e: any) { showToast(`匯出失敗：${e?.message ?? '請稍後重試'}`, 'error') } finally { exporting.value = false }
}
</script>

<template>
  <div>
    <!-- 一個來源的頁 -->
    <template v-if="current">
      <div class="bar"><NuxtLink to="/v2/settings?st=import">‹ 資料來源總覽</NuxtLink><b>{{ current.label }}</b>
        <span class="muted">目前 {{ countOf(current.key) ?? '—' }} 筆・上次匯入 {{ lastFor(current.key) }}</span></div>
      <V2ImportCia v-if="current.key === 'cia_excel'" :src="src('cia_excel')" :api-base="apiBase" :last-import="lastImport" @changed="reload" />
      <V2ImportCiaFix v-else-if="current.key === 'cia_fix'" @changed="reload" />
      <V2ImportCiaReg v-else-if="current.key === 'cia_reg'" @changed="reload" />
      <V2ImportSrvSheet v-else-if="current.key === 'srv_sheet'" @changed="reload" />
      <V2ImportRv v-else-if="current.key === 'rvtools'" :src="src('rvtools')" :api-base="apiBase" @changed="reload" />
      <V2ImportOne v-else :kind="current.key" :src="src(current.key)" :api-base="apiBase" :count="countOf(current.key)"
                   :cert-summary="certSummary" :ac-info="acInfo" :ad-summary="adSummary" :rel-locs="relLocs" @changed="reload" />
    </template>

    <!-- 總覽 -->
    <template v-else>
      <p v-if="loadErr" class="err">{{ loadErr }}</p>
      <div class="bar">
        <span v-if="lastImport" class="muted">CIA 上次匯入 {{ lastImport.imported_at }}・共 {{ (lastImport.hardware_count + lastImport.personnel_count + lastImport.software_count).toLocaleString() }} 筆（硬體 {{ lastImport.hardware_count }}／人員 {{ lastImport.personnel_count }}／軟體 {{ lastImport.software_count }}）・匯入人 {{ lastImport.imported_by ?? '—' }}<template v-if="lastImport.error_count > 0">・{{ lastImport.error_count }} 筆略過</template></span>
        <span v-else class="muted">CIA 尚未執行過匯入</span>
        <span class="sp" />
        <label class="chk"><input v-model="includeOffBook" type="checkbox"> 含 CIA 待補登的機器</label>
        <button type="button" class="btn" :disabled="exporting" @click="downloadExport">{{ exporting ? '匯出中…' : '匯出資產（含人員通訊錄）' }}</button>
        <InfoNote>匯出目前資產清單（三分頁：硬體／人員／軟體，可當範本）。回交給資產清單維護單位時：不勾＝只給 CIA 登記過的那批；勾選＝連額外盤到、清單上沒有的機器（DYN-／VC-／AUTO- 開頭）一起給，檔案裡多一欄「來源」分得出哪些是新發現的。</InfoNote>
      </div>
      <V2Table :rows="rows" :cols="cols" prefix="I" unit="種來源" empty="沒有來源" />
      <p class="muted sum">點來源進那個來源的頁：選檔匯入、下載範例檔，以及匯出原始檔／Excel／dump。
        <InfoNote>匯出三種格式回答不同問題：原始檔＝當初送進來那個檔（一位元沒動，對帳用）；Excel＝現在系統裡是什麼（正規化後，可原封匯回）；dump＝整包搬到另一台再匯回。dump 目前是明碼（含 IP／主機名／負責人），別當備份寄出，要寄用「備份與還原」的加密匯出。單據、合約、值班電話要先預覽或是文件，上傳在各自的頁面，這裡只列筆數與時間。</InfoNote></p>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 4px 0 10px; font-size: 13px; }
.sp { flex: 1; }
.muted { color: var(--muted); font-size: 13px; }
.sum { margin-top: 8px; }
.err { color: var(--bad); }
.chk { font-size: 12px; color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; }
.btn { padding: 5px 12px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 13px; }
.btn:hover:not(:disabled) { background: var(--brand-hover); }
.btn:disabled { opacity: .6; cursor: default; }
</style>
