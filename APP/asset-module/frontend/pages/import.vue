<script setup lang="ts">
// S9：資料匯入頁面，對應S2後端（excel_import.py）。欄位對應可調整、不寫死於畫面（D14精神）。
interface ImportLog {
  imported_at: string
  imported_by: string | null
  hardware_count: number
  personnel_count: number
  software_count: number
  error_count: number
}
interface ImportSummary {
  sheets: Record<string, { inserted: number; updated: number; skipped: number }>
  errors: string[]
}
type FieldMapping = Record<string, Record<string, string>>

const NO_IMPORT = '不匯入'
const SHEET_ORDER = ['硬體', '人員', '軟體']

const FIELD_LABELS: Record<string, string> = {
  hostname: '主機名稱', ip: 'IP', device_model: '設備機型', rack_no: '機櫃編號',
  group_name: '群組名稱', api_id: 'API ID', asset_purpose: '資產用途', custodian: '保管者',
  usage_unit: '使用單位', asset_status: '資產狀態', environment: '環境別',
  confidentiality: '機密性', availability: '可用性', integrity: '完整性',
  request_no: '申請單編號', inventory_division: '盤點單位-處別',
  inventory_department: '盤點單位-部門', owner: '擁有者', remark: '附加說明',
  hardware_no: '硬體編號', big_ip_vip: 'BIG IP/VIP', asset_name: '資產名稱',
  infra_type: '整體基礎架構', physical_location: '資產實體位置', quantity: '數量',
  os: '作業系統', user_name: '使用者', owning_company: '所屬公司', asset_serial: '資產序號',
  person_name: '人員姓名', belong_division: '隸屬單位-處別', belong_department: '隸屬單位-部門',
  phone: '聯絡電話', job_desc: '職務概述', proxy1: '代理人1', proxy1_phone: '代理人聯絡電話',
  cloud_service_type: '雲端服務類型', project_zone: '專案/可用區', cloud: 'Cloud',
  db_software: '資料庫/軟體', backup_frequency: '備份頻率', handles_pii: '處理個資',
  outsourced_maintenance: '委外維護',
}
function fieldLabel(key: string) {
  return FIELD_LABELS[key] ?? key
}

/** 把匯入結果判定成「成功／部分成功／完全沒進去」三態。
 *
 * 為什麼需要：後端只要檔案讀得開就回 200，連分頁名稱全錯、一列都沒寫入的情況也是 200
 * ＋ errors 清單。畫面若只是把數字列出來，成功與完全失敗長得一模一樣，使用者會以為
 * 匯好了。這裡明確算出「到底進去幾筆」，讓 UI 有話直說。*/
function summarize(s: ImportSummary) {
  const written = Object.values(s.sheets).reduce((n, x) => n + x.inserted + x.updated, 0)
  const skipped = Object.values(s.sheets).reduce((n, x) => n + x.skipped, 0)
  const errorCount = s.errors.length

  if (written === 0) {
    return {
      tone: 'error' as const,
      verdict: '沒有匯入任何資料',
      hint: errorCount > 0
        ? '請看下方原因；最常見是分頁名稱不是「硬體／人員／軟體」，或表頭對不上欄位對應。'
        : '檔案讀得到，但沒有任何一列符合欄位對應，請確認表頭與分頁名稱。',
      toastMessage: '匯入失敗：沒有任何資料被寫入',
    }
  }
  if (errorCount > 0 || skipped > 0) {
    return {
      tone: 'warn' as const,
      verdict: `部分匯入：成功 ${written} 筆，略過 ${skipped} 筆`,
      hint: '略過的資料請看下方原因，處理後可以再匯一次（重複匯入不會產生重複資料）。',
      toastMessage: `部分匯入：成功 ${written} 筆、略過 ${skipped} 筆`,
    }
  }
  return {
    tone: 'success' as const,
    verdict: `匯入成功：共 ${written} 筆`,
    hint: '',
    toastMessage: `匯入成功，共 ${written} 筆`,
  }
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const runtimeConfig = useRuntimeConfig()
const exporting = ref(false)

async function downloadExport() {
  exporting.value = true
  try {
    const res = await fetch(`${runtimeConfig.public.apiBase}/api/export`, { credentials: 'include' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename="?([^"]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m ? m[1] : 'assets_export.xlsx'
    a.click()
    URL.revokeObjectURL(url)
    showToast('已匯出資產資料（硬體／人員／軟體）', 'success')
  } catch (err: any) {
    showToast(`匯出失敗：${err?.message ?? '請稍後重試'}`, 'error')
  } finally {
    exporting.value = false
  }
}

const lastImport = ref<ImportLog | null>(null)
const originalMapping = ref<FieldMapping>({})
const workingMapping = ref<FieldMapping>({})
const selectedFile = ref<File | null>(null)
const dragOver = ref(false)
const uploading = ref(false)
const savingMapping = ref(false)
const importResult = ref<ImportSummary | null>(null)
const errorMessage = ref('')
const mappingMessage = ref('')

// 欄位對應表原本是「巢狀物件逐層 v-for」，沒辦法排序（天條：表格每欄都要能排）。
// 攤平成一維陣列後就能排，也讓「這個 Excel 欄位對到哪」變成可以照分頁或照欄位名找。
const mappingRows = computed(() =>
  SHEET_ORDER.flatMap((sheet) =>
    Object.keys(originalMapping.value[sheet] ?? {}).map((header) => ({
      sheet,
      header,
      field: fieldLabel(originalMapping.value[sheet][header]),
    })),
  ),
)
const { sortKey: mpKey, sortDir: mpDir, toggle: mpToggle, sorted: mappingSorted } =
  useSort(mappingRows, 'sheet')

async function loadAll() {
  const [last, fm] = await Promise.all([
    apiFetch<ImportLog | null>('/api/import/last'),
    apiFetch<FieldMapping>('/api/import/field-mapping'),
  ])
  lastImport.value = last
  originalMapping.value = fm
  workingMapping.value = JSON.parse(JSON.stringify(fm))
}
await loadAll()

function toggleMapping(sheet: string, header: string, checked: boolean) {
  if (checked) {
    workingMapping.value[sheet][header] = originalMapping.value[sheet][header]
  } else {
    workingMapping.value[sheet][header] = NO_IMPORT
  }
}

async function saveMapping() {
  savingMapping.value = true
  mappingMessage.value = ''
  try {
    const saved = await apiFetch<FieldMapping>('/api/import/field-mapping', {
      method: 'PUT',
      body: { mapping: workingMapping.value },
    })
    originalMapping.value = saved
    mappingMessage.value = '欄位對應已儲存'
  } catch (err: any) {
    mappingMessage.value = err?.data?.detail ?? '欄位對應儲存失敗'
  } finally {
    savingMapping.value = false
  }
}

function handleFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
}
function handleDrop(e: DragEvent) {
  dragOver.value = false
  const dropped = e.dataTransfer?.files?.[0]
  if (dropped) selectedFile.value = dropped
}

async function submitImport() {
  if (!selectedFile.value) {
    errorMessage.value = '請先選擇 .xlsx 檔案'
    return
  }
  uploading.value = true
  errorMessage.value = ''
  importResult.value = null
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    const summary = await apiFetch<ImportSummary>('/api/import/excel', {
      method: 'POST',
      body: formData,
    })
    importResult.value = summary
    // 後端對「檔案讀得開但一列都沒進去」（例如分頁名稱不對）也是回200，
    // 沒有明確判定的話，畫面看起來跟成功一樣，使用者會以為匯好了就走人。
    const v = summarize(summary)
    showToast(v.toastMessage, v.tone)
    selectedFile.value = null
    await loadAll()
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '匯入失敗，請稍後再試'
    showToast(errorMessage.value, 'error')
  } finally {
    uploading.value = false
  }
}

function cancelSelection() {
  selectedFile.value = null
  importResult.value = null
  errorMessage.value = ''
}

// ===== S19 VC 採集器：RVTools（vCenter 盤點）匯入 =====
// 跟 ICA Excel 匯入是兩條獨立的匯入路徑：RVTools 是 vCenter 的 VM 清單，走身分解析
// （vm_uuid 強配），對到就更新機器事實、新的建 VC- 資產、判不準的進人工審核不亂合併。
interface RvSummary {
  total_vms: number
  inserted: number
  updated: number
  pending_review: number
  errors: string[]
}
const rvFile = ref<File | null>(null)
const rvDragOver = ref(false)
const rvUploading = ref(false)
const rvResult = ref<RvSummary | null>(null)
const rvError = ref('')

function handleRvFileInput(e: Event) {
  rvFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
}
function handleRvDrop(e: DragEvent) {
  rvDragOver.value = false
  const dropped = e.dataTransfer?.files?.[0]
  if (dropped) rvFile.value = dropped
}
function rvVerdict(s: RvSummary) {
  const written = s.inserted + s.updated
  if (written === 0 && s.pending_review === 0) {
    return { tone: 'error' as const, text: '沒有讀到任何 VM，請確認是 RVTools 匯出的檔（含 vInfo 分頁）' }
  }
  if (s.pending_review > 0) {
    return {
      tone: 'warn' as const,
      text: `收進 ${written} 台（新增 ${s.inserted}／更新 ${s.updated}），另有 ${s.pending_review} 台判不準、待人工確認`,
    }
  }
  return { tone: 'success' as const, text: `收進 ${written} 台 VM（新增 ${s.inserted}／更新 ${s.updated}）` }
}
async function submitRvImport() {
  if (!rvFile.value) { rvError.value = '請先選擇 RVTools 匯出的 .xlsx'; return }
  rvUploading.value = true
  rvError.value = ''
  rvResult.value = null
  try {
    const formData = new FormData()
    formData.append('file', rvFile.value)
    const summary = await apiFetch<RvSummary>('/api/import/rvtools', { method: 'POST', body: formData })
    rvResult.value = summary
    const v = rvVerdict(summary)
    showToast(v.text, v.tone)
    rvFile.value = null
    await loadAll()
  } catch (err: any) {
    rvError.value = err?.data?.detail ?? 'RVTools 匯入失敗，請稍後再試'
    showToast(rvError.value, 'error')
  } finally {
    rvUploading.value = false
  }
}
</script>

<template>
  <div>
    <div class="section-divider">資料匯入</div>
    <div class="breadcrumb-bar">
      <span class="pin">📌</span> <b>資料匯入</b>
      <button class="export-btn" :disabled="exporting" @click="downloadExport">
        {{ exporting ? '匯出中…' : '⬇ 匯出資產（Excel）' }}
      </button>
    </div>

    <div class="card">
      <div class="card-title">上次匯入</div>
      <div v-if="lastImport" class="last-import-info">
        {{ lastImport.imported_at }}　共
        {{ lastImport.hardware_count + lastImport.personnel_count + lastImport.software_count }} 筆
        （硬體 {{ lastImport.hardware_count }}／人員 {{ lastImport.personnel_count }}／軟體
        {{ lastImport.software_count }}）　匯入人：{{ lastImport.imported_by ?? '—' }}
        <span v-if="lastImport.error_count > 0" class="error-note">
          （{{ lastImport.error_count }} 筆略過，詳見下方匯入結果）
        </span>
      </div>
      <div v-else class="last-import-info muted">尚未執行過匯入</div>
    </div>

    <!-- S19 VC 採集器：RVTools（vCenter 盤點）匯入。與 ICA Excel 是兩條獨立路徑 -->
    <div class="card">
      <div class="card-title">vCenter 盤點匯入（RVTools）</div>
      <p class="rv-hint">
        在 vCenter 用 <b>RVTools</b> 匯出後上傳這份 Excel，系統會讀 <code>vInfo</code> 分頁把每台 VM
        收進資產：對得到的更新機器資料（不動你填的用途/保管者），新的建成 <code>VC-</code> 資產，
        <b>同 IP 但不同機器</b>這種判不準的會擋下來等你確認，不會亂合併。
      </p>
      <div
        class="dropzone"
        :class="{ over: rvDragOver }"
        @dragover.prevent="rvDragOver = true"
        @dragleave.prevent="rvDragOver = false"
        @drop.prevent="handleRvDrop"
      >
        <div class="dropzone-text">拖曳 RVTools 匯出的 Excel 到此處，或</div>
        <label class="btn file-label">
          選擇檔案
          <input type="file" accept=".xlsx" class="file-input" @change="handleRvFileInput" />
        </label>
        <div class="dropzone-hint">支援 RVTools 匯出的 .xlsx（需含 vInfo 分頁）</div>
        <div v-if="rvFile" class="selected-file">已選擇：{{ rvFile.name }}</div>
      </div>
      <p v-if="rvError" class="error-text">{{ rvError }}</p>
      <div class="actions">
        <button class="btn" type="button" :disabled="rvUploading" @click="submitRvImport">
          {{ rvUploading ? '匯入中…' : '匯入 vCenter 盤點' }}
        </button>
      </div>
      <div v-if="rvResult" class="card result-card" :class="`result-${rvVerdict(rvResult).tone}`">
        <div class="card-title">匯入結果</div>
        <div class="result-verdict">{{ rvVerdict(rvResult).text }}</div>
        <div class="result-row">
          讀到 {{ rvResult.total_vms }} 台 · 新增 {{ rvResult.inserted }} · 更新 {{ rvResult.updated }}
          <span v-if="rvResult.pending_review > 0"> · 待人工確認 {{ rvResult.pending_review }}</span>
        </div>
        <div v-if="rvResult.pending_review > 0" class="result-hint">
          待確認的是「IP 或名稱對得上、但唯一識別碼不同」的機器（可能 IP 被回收或同名）——
          系統不敢自動當同一台，留給你決定，避免把兩台不同機器併成一台。
        </div>
        <div v-if="rvResult.errors.length > 0" class="result-errors">
          <div v-for="(e, i) in rvResult.errors" :key="i">{{ e }}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">上傳新檔案（ICA 資產清單 Excel）</div>
      <div
        class="dropzone"
        :class="{ over: dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="handleDrop"
      >
        <div class="dropzone-text">拖曳 Excel 檔案到此處，或</div>
        <label class="btn file-label">
          選擇檔案
          <input type="file" accept=".xlsx" class="file-input" @change="handleFileInput" />
        </label>
        <div class="dropzone-hint">支援 .xlsx，需含硬體／人員／軟體三個分頁</div>
        <div v-if="selectedFile" class="selected-file">已選擇：{{ selectedFile.name }}</div>
      </div>

      <div class="card-title mapping-title">欄位對應（可自行調整，不是固定寫死）</div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="header" :active="mpKey" :dir="mpDir" @sort="mpToggle">Excel 欄位名稱</SortTh>
            <SortTh k="field" :active="mpKey" :dir="mpDir" @sort="mpToggle">對應到系統欄位</SortTh>
            <SortTh k="sheet" :active="mpKey" :dir="mpDir" @sort="mpToggle">分頁</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="row in mappingSorted" :key="`${row.sheet}-${row.header}`">
              <td>{{ row.header }}</td>
              <td>
                <select
                  :value="workingMapping[row.sheet]?.[row.header] !== NO_IMPORT ? 'import' : 'skip'"
                  @change="toggleMapping(row.sheet, row.header, ($event.target as HTMLSelectElement).value === 'import')"
                >
                  <option value="import">{{ row.field }}</option>
                  <option value="skip">不匯入</option>
                </select>
              </td>
              <td>{{ row.sheet }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="mappingMessage" class="mapping-message">{{ mappingMessage }}</p>

      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

      <div class="actions">
        <button class="btn" type="button" :disabled="uploading" @click="submitImport">
          {{ uploading ? '匯入中…' : '預覽並匯入' }}
        </button>
        <button class="btn ghost" type="button" @click="cancelSelection">取消</button>
        <button class="btn ghost" type="button" :disabled="savingMapping" @click="saveMapping">
          {{ savingMapping ? '儲存中…' : '儲存欄位對應' }}
        </button>
      </div>

      <div v-if="importResult" class="card result-card" :class="`result-${summarize(importResult).tone}`">
        <div class="card-title">匯入結果</div>
        <div class="result-verdict">{{ summarize(importResult).verdict }}</div>
        <div v-if="summarize(importResult).hint" class="result-hint">
          {{ summarize(importResult).hint }}
        </div>
        <div v-for="(s, sheetName) in importResult.sheets" :key="sheetName" class="result-row">
          {{ sheetName }}：新增 {{ s.inserted }}／更新 {{ s.updated }}／略過 {{ s.skipped }}
        </div>
        <div v-if="importResult.errors.length > 0" class="result-errors">
          <div v-for="(e, i) in importResult.errors" :key="i">{{ e }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-divider {
  margin: 0 0 16px;
  font-size: 11px;
  color: var(--brand-dark);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.export-btn {
  margin-left: auto;
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 14px;
  border: none;
  background: var(--brand);
  color: #fff;
  cursor: pointer;
}
.export-btn:hover:not(:disabled) { background: var(--brand-dark); }
.export-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.breadcrumb-bar {
  background: var(--mint);
  border: 1px solid var(--border-strong);
  padding: 8px 14px;
  font-size: 12.5px;
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.breadcrumb-bar b {
  color: var(--brand-dark);
}
.card {
  border: 1px solid var(--border);
  background: var(--card);
  padding: 16px;
  margin-bottom: 16px;
}
.card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-soft);
  margin-bottom: 10px;
}
.mapping-title {
  margin-top: 4px;
}
.last-import-info {
  font-size: 12.5px;
  color: var(--ink-soft);
}
.last-import-info.muted {
  color: var(--muted);
}
.error-note {
  color: var(--warn);
}
.dropzone {
  border: 2px dashed var(--border-strong);
  background: var(--mint);
  padding: 28px;
  text-align: center;
  margin-bottom: 14px;
}
.dropzone.over {
  border-color: var(--brand);
  background: var(--mint-deep);
}
.dropzone-text {
  font-size: 13px;
  color: var(--ink-soft);
  margin-bottom: 10px;
}
.dropzone-hint {
  font-size: 11.5px;
  color: var(--muted);
  margin-top: 10px;
}
.selected-file {
  font-size: 12.5px;
  color: var(--brand-dark);
  font-weight: 700;
  margin-top: 10px;
}
.file-label {
  position: relative;
  display: inline-block;
  overflow: hidden;
}
.file-input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.btn {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 700;
  padding: 8px 18px;
  border: none;
  background: var(--brand);
  color: #fff;
  cursor: pointer;
}
.btn:hover:not(:disabled) {
  background: var(--brand-dark);
}
.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.btn.ghost {
  background: var(--card);
  border: 1px solid var(--border-strong);
  color: var(--ink-soft);
}
.tbl-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  margin-bottom: 14px;
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 12.5px;
  min-width: 420px;
}
th, td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}
th {
  color: var(--ink-soft);
  font-weight: 700;
  font-size: 12px;
  background: var(--mint);
}
tr:last-child td {
  border-bottom: none;
}
select {
  font-family: inherit;
  font-size: 12.5px;
  padding: 5px 8px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
}
.mapping-message {
  font-size: 12.5px;
  color: var(--brand-dark);
  margin-bottom: 10px;
}
.error-text {
  color: var(--bad);
  font-size: 13px;
  margin-bottom: 14px;
}
.actions {
  display: flex;
  gap: 10px;
}
.result-card {
  margin-top: 16px;
  margin-bottom: 0;
  background: var(--mint);
  border-left: 4px solid transparent;
}
/* 成敗一眼可辨：只靠數字的話，「全成功」跟「一列都沒進去」長得一模一樣 */
.result-success { border-left-color: #26a889; }
.result-warn    { border-left-color: #d99a2b; }
.result-error   { border-left-color: #d9534f; }

.result-verdict {
  font-weight: 700;
  font-size: 15px;
  margin-bottom: 4px;
}
.result-error .result-verdict { color: #d9534f; }
.result-warn .result-verdict  { color: #b8791d; }

.result-hint {
  font-size: 13px;
  opacity: 0.85;
  margin-bottom: 8px;
  line-height: 1.5;
}
.result-row {
  font-size: 12.5px;
  color: var(--ink-soft);
}
.result-errors {
  margin-top: 8px;
  font-size: 12px;
  color: var(--warn);
}
.rv-hint {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.7;
  margin: 0 0 12px;
}
.rv-hint code { color: var(--brand-dark); }
.rv-hint b { color: var(--ink-soft); }
</style>
