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

// 審核佇列一定要在 onMounted 裡載，兩個原因：
//  1. SSR 階段拿不到瀏覽器的登入 cookie，那時打 API 會 401（本專案既有問題）。
//  2. 寫在 `await loadAll()` 後面的話，loadAll 在 SSR 拋錯就會讓這行整個不執行——
//     結果是後端日誌上連請求都看不到，畫面只是靜靜地少了那一區，極難查。
//     （2026-07-30 實際踩到：API 直接打是 200，但畫面永遠不顯示。）
onMounted(() => {
  loadReviewInfo()
  loadRvHistory()
})

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
// 跟 CIA Excel 匯入是兩條獨立的匯入路徑：RVTools 是 vCenter 的 VM 清單，走身分解析
// （vm_uuid 強配），對到就更新機器事實、新的建 VC- 資產、判不準的進人工審核不亂合併。
interface RvSummary {
  total_vms: number
  inserted: number
  updated: number
  pending_review: number
  errors: string[]
  extra_sheets?: Record<string, number>
}
interface RvFileResult { file: string; summary?: RvSummary; error?: string }
interface ImportLogEntry {
  id: number; imported_at: string; imported_by: string | null
  hardware_count: number; error_count: number; source: string | null; file_name: string | null
}
// 2026-08-19 使用者拍板「要吃全部」＋「一次匯6個檔案」：檔案選取改多選，
// 逐份依序送出（後端一次只吃一份 xlsx），每份各自的結果留著給使用者對帳，
// 不是只看最後一份、前面的靜靜被蓋掉。
const rvFiles = ref<File[]>([])
const rvDragOver = ref(false)
const rvUploading = ref(false)
const rvResults = ref<RvFileResult[]>([])
const rvError = ref('')
const rvHistory = ref<ImportLogEntry[]>([])
const { sortKey: rvHistSortKey, sortDir: rvHistSortDir, toggle: rvHistToggle, sorted: rvHistSorted } =
  useSort(rvHistory, 'imported_at')

function handleRvFileInput(e: Event) {
  rvFiles.value = Array.from((e.target as HTMLInputElement).files ?? [])
}
function handleRvDrop(e: DragEvent) {
  rvDragOver.value = false
  const dropped = Array.from(e.dataTransfer?.files ?? [])
  if (dropped.length) rvFiles.value = dropped
}
function removeRvFile(i: number) {
  rvFiles.value = rvFiles.value.filter((_, idx) => idx !== i)
}
async function loadRvHistory() {
  try {
    rvHistory.value = await apiFetch<ImportLogEntry[]>(
      '/api/import/log', { query: { source: 'rvtools', limit: 10 } })
  } catch {
    rvHistory.value = []   // 舊版後端沒這支，安靜跳過不擋整頁
  }
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
// ===== 待人工審核：可自動配對的批次處理 =====
// 匯入時判不準的案子會留在審核佇列。1,000 筆逐筆按是做不完的，
// 而其中大半只是「vCenter 給 FQDN、Excel 填短名」這一個原因，可以一次處理完。
interface ReviewInfo {
  total_open: number
  matchable: number
  remaining: number
  rule: string
  rule_label: string
  samples: { incoming_hostname: string; incoming_ip: string
             target_serial: string; target_hostname: string }[]
}
const reviewInfo = ref<ReviewInfo | null>(null)
const reviewLoading = ref(false)
const reviewMerging = ref(false)
const reviewMsg = ref('')
const reviewOpen = ref(false)

async function loadReviewInfo() {
  reviewLoading.value = true
  try {
    reviewInfo.value = await apiFetch<ReviewInfo>('/api/merge-reviews/auto-matchable')
  } catch {
    reviewInfo.value = null   // 舊版後端沒這支，安靜跳過不擋整頁
  } finally {
    reviewLoading.value = false
  }
}

async function doBatchMerge() {
  const info = reviewInfo.value
  if (!info?.matchable) return
  const ok = confirm(
    `即將把 ${info.matchable} 筆併進既有資產。\n\n`
    + `配對條件：${info.rule_label}\n`
    + `只會寫入 OS／虛實／VM 識別碼，你填的用途、保管者、機房不會被改動。\n\n`
    + `確定執行嗎？`,
  )
  if (!ok) return
  reviewMerging.value = true
  reviewMsg.value = ''
  try {
    const r = await apiFetch<{ merged: number; remaining_open: number }>(
      '/api/merge-reviews/batch-merge', { method: 'POST', body: { rule: info.rule } })
    reviewMsg.value = `已合併 ${r.merged} 筆，剩餘待審 ${r.remaining_open} 筆`
    showToast(reviewMsg.value, 'success')
    await loadReviewInfo()
    await loadAll()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '批次合併失敗，請稍後再試', 'error')
  } finally {
    reviewMerging.value = false
  }
}

// 檔案的最後修改時間＝這份匯出檔的「資料時間」。用它擋住「不小心傳了舊檔」：
// 匯入是後蓋前且沒有欄位變更歷史，舊資料蓋掉新的就只能翻每日備份救。
function fileDataAt(f: File): string {
  const d = new Date(f.lastModified)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
    + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function submitOneRvFile(file: File, force = false): Promise<RvFileResult> {
  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('data_at', fileDataAt(file))
    if (force) formData.append('force', 'true')
    const summary = await apiFetch<RvSummary>('/api/import/rvtools', { method: 'POST', body: formData })
    return { file: file.name, summary }
  } catch (err: any) {
    const d = err?.data?.detail
    // 409 stale_import 不是錯誤，是「請確認」：問清楚再決定要不要蓋。
    // 6個檔案一次送時每份各自可能觸發，逐份問不會被前一份的確認框卡住整批。
    if (d && typeof d === 'object' && d.code === 'stale_import') {
      const ok = confirm(
        `「${file.name}」的資料時間是 ${d.incoming_data_at}，\n`
        + `比系統現有資料（${d.current_data_at}）還舊。\n\n`
        + `繼續匯入會用舊資料覆蓋較新的 OS／虛實／主機名等欄位，\n`
        + `而且系統不保留欄位變更歷史，蓋掉後只能從每日備份還原。\n\n`
        + `確定要繼續匯入這份嗎？`,
      )
      if (ok) return submitOneRvFile(file, true)
      return { file: file.name, error: '已取消（資料時間較舊）' }
    }
    return { file: file.name, error: (typeof d === 'string' ? d : d?.message) ?? '匯入失敗，請稍後再試' }
  }
}

async function submitRvImport() {
  if (!rvFiles.value.length) { rvError.value = '請先選擇 RVTools 匯出的 .xlsx（可一次選多個）'; return }
  rvUploading.value = true
  rvError.value = ''
  rvResults.value = []
  const files = [...rvFiles.value]
  for (const f of files) {
    if (files.length > 1) showToast(`匯入中：${f.name}…`, 'info')
    const r = await submitOneRvFile(f)
    rvResults.value = [...rvResults.value, r]
  }
  rvUploading.value = false
  rvFiles.value = []
  const okCount = rvResults.value.filter(r => r.summary).length
  const failCount = rvResults.value.length - okCount
  if (rvResults.value.length === 1 && rvResults.value[0].summary) {
    const v = rvVerdict(rvResults.value[0].summary)
    showToast(v.text, v.tone)
  } else {
    showToast(
      failCount === 0 ? `全部 ${okCount} 份匯入完成` : `完成 ${okCount} 份，${failCount} 份未成功，詳見下方明細`,
      failCount === 0 ? 'success' : 'warn',
    )
  }
  await loadRvHistory()
  await loadAll()
}

// ===== dynassets 匯入（存活掃描＋CMDB，跟 CIA 登記對帳）=====
// 跟 CIA／RVTools 是第三條獨立匯入路徑：dynassets 是「網路上實際存活」的清單，
// 對到 CIA 就更新機器事實、掃到沒登記的建成 DYN- 資產（漏登記）、判不準的進待審。
const dynFile = ref<File | null>(null)
const dynUploading = ref(false)
const dynError = ref('')
const dynResult = ref<any>(null)

function handleDynFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) { dynFile.value = f; dynError.value = '' }
}

async function submitDynImport() {
  if (!dynFile.value) { dynError.value = '請先選擇 dynassets 的 .csv 或 .xlsx'; return }
  dynUploading.value = true
  dynError.value = ''
  dynResult.value = null
  try {
    const formData = new FormData()
    formData.append('file', dynFile.value)
    const summary = await apiFetch<any>('/api/import/dynassets', { method: 'POST', body: formData })
    dynResult.value = summary
    showToast(
      `dynassets 匯入完成：帳實相符 ${summary.updated}、漏登記 ${summary.inserted}、待審 ${summary.pending_review}`,
      'success',
    )
    dynFile.value = null
    await loadAll()
  } catch (err: any) {
    const d = err?.data?.detail
    dynError.value = (typeof d === 'string' ? d : d?.message) ?? 'dynassets 匯入失敗，請稍後再試'
    showToast(dynError.value, 'error')
  } finally {
    dynUploading.value = false
  }
}


// ===== 清空所有盤點資料（危險操作，打 ClearALL 才送出）=====
const resetConfirm = ref('')
const resetting = ref(false)

async function submitReset() {
  if (resetConfirm.value !== 'ClearALL') {
    showToast('請先輸入 ClearALL 才能清空', 'error')
    return
  }
  if (!confirm(
    '確定要清空「所有盤點資料」嗎？\n\n'
    + '資產／人員／軟體／掃描／帳號盤點／待審／業務系統／匯入紀錄會全部刪除，\n'
    + '只保留登入帳號、憑證庫、連線設定、系統設定。\n\n'
    + '⚠️ 此動作不可逆，只能靠備份還原。執行前請先確認已備份。',
  )) return
  resetting.value = true
  try {
    const r = await apiFetch<any>('/api/admin/reset-inventory', {
      method: 'POST', body: { confirm: resetConfirm.value },
    })
    showToast(`已清空 ${r.total_rows} 筆盤點資料（帳號／憑證／設定保留）`, 'success')
    resetConfirm.value = ''
    await loadAll()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '清空失敗，請稍後再試', 'error')
  } finally {
    resetting.value = false
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

    <!-- S19 VC 採集器：RVTools（vCenter 盤點）匯入。與 CIA Excel 是兩條獨立路徑 -->
    <div class="card">
      <div class="card-title">vCenter 盤點匯入（RVTools）— 半自動：人工上傳</div>
      <!-- 待審核批次處理：放在匯入卡片裡，因為它就是匯入的後續步驟。
           判不準的案子留著等人確認是對的（合併錯很難救），但不該讓人逐筆按一千次。 -->
      <div v-if="reviewInfo && reviewInfo.total_open" class="revbox">
        <div class="revhead">
          待人工審核 <b>{{ reviewInfo.total_open.toLocaleString() }}</b> 筆
          <span class="revsub">匯入時判不準是否同一台的案子，系統不自動合併</span>
        </div>
        <template v-if="reviewInfo.matchable">
          <p class="revline">
            其中 <b class="hit">{{ reviewInfo.matchable.toLocaleString() }}</b> 筆可用
            「{{ reviewInfo.rule_label }}」安全配對，
            剩 {{ reviewInfo.remaining.toLocaleString() }} 筆仍需逐筆判斷。
            <button class="lnk" type="button" @click="reviewOpen = !reviewOpen">
              {{ reviewOpen ? '收合範例' : '先看幾筆範例' }}
            </button>
          </p>
          <div v-if="reviewOpen" class="tbl-wrap revtbl">
            <table>
              <thead><tr><th>vCenter 給的名稱</th><th>IP</th><th>要併進的資產</th></tr></thead>
              <tbody>
                <tr v-for="(s, i) in reviewInfo.samples" :key="i">
                  <td class="mono">{{ s.incoming_hostname }}</td>
                  <td class="mono">{{ s.incoming_ip }}</td>
                  <td>{{ s.target_hostname }} <span class="muted">（{{ s.target_serial }}）</span></td>
                </tr>
              </tbody>
            </table>
            <p class="revnote">
              只寫入 OS／虛實／VM 識別碼。你填的用途、保管者、機房、盤點單位一律不動，
              主機名也維持現有寫法（不會被改成 FQDN）。
            </p>
          </div>
          <button class="btn" type="button" :disabled="reviewMerging" @click="doBatchMerge">
            {{ reviewMerging ? '合併中…' : `確認合併這 ${reviewInfo.matchable.toLocaleString()} 筆` }}
          </button>
        </template>
        <p v-else class="revline muted">
          目前沒有可安全批次配對的案子，這些需要逐筆判斷。
        </p>
        <p v-if="reviewMsg" class="revok">{{ reviewMsg }}</p>
      </div>
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
          選擇檔案（可一次選多個）
          <input type="file" accept=".xlsx" multiple class="file-input" @change="handleRvFileInput" />
        </label>
        <div class="dropzone-hint">支援 RVTools 匯出的 .xlsx（需含 vInfo 分頁）；一次選取多份會依序匯入</div>
        <ul v-if="rvFiles.length" class="selected-files">
          <li v-for="(f, i) in rvFiles" :key="i">
            {{ f.name }}
            <button type="button" class="lnk" @click="removeRvFile(i)">移除</button>
          </li>
        </ul>
      </div>
      <p v-if="rvError" class="error-text">{{ rvError }}</p>
      <div class="actions">
        <!-- 一定要寫成 submitRvImport()：不加括號的話 Vue 會把 MouseEvent 當成第一個參數
             傳進去，force 就永遠是 truthy，時序保護等於沒做。 -->
        <button class="btn" type="button" :disabled="rvUploading || !rvFiles.length" @click="submitRvImport()">
          {{ rvUploading ? '匯入中…' : rvFiles.length > 1 ? `匯入 ${rvFiles.length} 份 vCenter 盤點` : '匯入 vCenter 盤點' }}
        </button>
      </div>

      <div v-if="rvResults.length" class="card result-card"
           :class="rvResults.length === 1 && rvResults[0].summary ? `result-${rvVerdict(rvResults[0].summary).tone}` : ''">
        <div class="card-title">匯入結果</div>
        <div v-for="(r, i) in rvResults" :key="i" class="rv-result-item">
          <div class="rv-result-file">{{ r.file }}</div>
          <template v-if="r.summary">
            <div class="result-verdict">{{ rvVerdict(r.summary).text }}</div>
            <div class="result-row">
              讀到 {{ r.summary.total_vms }} 台 · 新增 {{ r.summary.inserted }} · 更新 {{ r.summary.updated }}
              <span v-if="r.summary.pending_review > 0"> · 待人工確認 {{ r.summary.pending_review }}</span>
            </div>
            <div v-if="r.summary.extra_sheets && Object.keys(r.summary.extra_sheets).length" class="result-row muted">
              另外收到：{{ Object.entries(r.summary.extra_sheets).map(([s, n]) => `${s} ${n}筆`).join('、') }}
            </div>
            <div v-if="r.summary.errors.length > 0" class="result-errors">
              <div v-for="(e, ei) in r.summary.errors" :key="ei">{{ e }}</div>
            </div>
          </template>
          <div v-else class="result-errors">{{ r.error }}</div>
        </div>
        <p v-if="rvResults.some(r => r.summary && r.summary.pending_review > 0)" class="result-hint">
          待確認的是「IP 或名稱對得上、但唯一識別碼不同」的機器（可能 IP 被回收或同名）——
          系統不敢自動當同一台，留給你決定，避免把兩台不同機器併成一台。
        </p>
      </div>

      <!-- 匯入紀錄：2026-08-19 使用者原話「要有紀錄表讓我查」——一次要匯好幾份檔案時，
           要看得出哪些已經匯過（檔名+時間），不用憑記憶判斷還剩哪幾份沒匯。 -->
      <div v-if="rvHistory.length" class="rv-history">
        <div class="card-title small">最近匯入紀錄</div>
        <div class="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh k="imported_at" :active="rvHistSortKey" :dir="rvHistSortDir" @sort="rvHistToggle">時間</SortTh>
              <SortTh k="file_name" :active="rvHistSortKey" :dir="rvHistSortDir" @sort="rvHistToggle">檔案</SortTh>
              <SortTh k="hardware_count" :active="rvHistSortKey" :dir="rvHistSortDir" @sort="rvHistToggle">寫入筆數</SortTh>
              <SortTh k="error_count" :active="rvHistSortKey" :dir="rvHistSortDir" @sort="rvHistToggle">錯誤</SortTh>
              <SortTh k="imported_by" :active="rvHistSortKey" :dir="rvHistSortDir" @sort="rvHistToggle">匯入人</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="h in rvHistSorted" :key="h.id">
                <td class="mono">{{ h.imported_at }}</td>
                <td>{{ h.file_name || '—' }}</td>
                <td class="mono">{{ h.hardware_count }}</td>
                <td class="mono" :class="{ 'error-text': h.error_count > 0 }">{{ h.error_count }}</td>
                <td>{{ h.imported_by || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- vCenter/RVTools 的「全自動」模式，緊接在手動上傳後面：
         兩者是同一條管線的兩種觸發方式（誰去拿檔而已），拆兩頁只會讓人找不到。
         2026-08-15 從系統設定搬過來（使用者指出它們其實是同一件事）。 -->
    <VcenterAutoImport @imported="loadAll()" />

    <!-- dynassets 匯入：存活掃描＋CMDB 清單，跟 CIA 登記對帳。第三條獨立匯入路徑 -->
    <div class="card">
      <div class="card-title">存活清單匯入（dynassets）</div>
      <p class="rv-hint">
        上傳另一套系統（Satellite＋nmap 存活探測）匯出的<b>存活主機清單</b>（.csv／.xlsx，含 IP/主機名/MAC/OS）。
        系統會跟 CIA 登記對帳：<b>對得上</b>的更新機器事實（不動你填的用途/保管者），<b>掃到卻沒登記</b>的
        建成 <code>DYN-</code> 資產（＝漏登記／帳外資產），判不準的擋下等你確認、不亂合併。
        實體＋虛擬都涵蓋，補上 RVTools 補不到的實體機。
      </p>
      <div class="dropzone">
        <div class="dropzone-text">選擇 dynassets 存活清單</div>
        <label class="btn file-label">
          選擇檔案
          <input type="file" accept=".csv,.xlsx" class="file-input" @change="handleDynFile" />
        </label>
        <div class="dropzone-hint">支援 .csv／.xlsx（一列一台存活主機，需含 IP 欄）</div>
        <div v-if="dynFile" class="selected-file">已選擇：{{ dynFile.name }}</div>
      </div>
      <p v-if="dynError" class="error-text">{{ dynError }}</p>
      <div class="actions">
        <button class="btn" type="button" :disabled="dynUploading" @click="submitDynImport()">
          {{ dynUploading ? '匯入中…' : '匯入存活清單' }}
        </button>
      </div>
      <div v-if="dynResult" class="card result-card" :class="`result-${dynResult.inserted > 0 ? 'warn' : 'ok'}`">
        <div class="card-title">匯入結果</div>
        <div class="result-row">
          讀到 {{ dynResult.total }} 台 · 帳實相符 {{ dynResult.updated }} ·
          <b>漏登記 {{ dynResult.inserted }}</b>
          <span v-if="dynResult.pending_review > 0"> · 待人工確認 {{ dynResult.pending_review }}</span>
        </div>
        <div v-if="dynResult.inserted > 0" class="result-hint">
          「漏登記」＝掃到存活、但 CIA 沒登記的機器，已建成 <code>DYN-</code> 資產——可能是帳外資產，值得逐台核對。
        </div>
        <div v-if="dynResult.errors && dynResult.errors.length > 0" class="result-errors">
          <div v-for="(e, i) in dynResult.errors" :key="i">{{ e }}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">上傳新檔案（CIA 資產清單 Excel）</div>
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

      <!-- 欄位對應收進預設收合的「進階」摺疊：一般匯入表頭會自動對應，不需要看到這個。
           功能保留（要調的人點開仍可用），只是不再擋在主流程上。 -->
      <details class="mapping-advanced">
        <summary class="mapping-summary">⚙ 進階：欄位對應（一般匯入用不到，表頭會自動對應）</summary>
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
        <div class="actions">
          <button class="btn ghost" type="button" :disabled="savingMapping" @click="saveMapping">
            {{ savingMapping ? '儲存中…' : '儲存欄位對應' }}
          </button>
        </div>
      </details>

      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

      <div class="actions">
        <button class="btn" type="button" :disabled="uploading" @click="submitImport">
          {{ uploading ? '匯入中…' : '預覽並匯入' }}
        </button>
        <button class="btn ghost" type="button" @click="cancelSelection">取消</button>
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

    <!-- 危險區：清空所有盤點資料。像 GitHub 刪 repo，要手動打 ClearALL 才送出。 -->
    <div class="card" style="border:1px solid #c0392b">
      <div class="card-title" style="color:#e74c3c">⚠️ 清空所有盤點資料</div>
      <p class="rv-hint">
        把資產／人員／軟體／掃描／帳號盤點／待審／業務系統／匯入紀錄<b>全部刪除</b>，回到空白供重新匯入。
        <b>會保留</b>登入帳號、憑證庫、連線設定、授權網段、系統設定。
        <b style="color:#e74c3c">此動作不可逆</b>（只能靠備份還原）——執行前請先到管理頁按「立即備份」。
      </p>
      <div class="actions" style="gap:10px;align-items:center">
        <input
          v-model="resetConfirm"
          placeholder="輸入 ClearALL 確認"
          style="padding:8px 12px;border-radius:6px;border:1px solid #555;background:#161616;color: var(--ink);min-width:200px"
        />
        <button
          class="btn"
          type="button"
          :disabled="resetting || resetConfirm !== 'ClearALL'"
          style="background:#c0392b;border-color:#c0392b"
          @click="submitReset"
        >
          {{ resetting ? '清空中…' : '清空所有盤點資料' }}
        </button>
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
  color: var(--ink);
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
.selected-files {
  list-style: none; margin: 10px 0 0; padding: 0;
  font-size: 12.5px; color: var(--brand-dark);
}
.selected-files li {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 4px 0; font-weight: 700;
}
.selected-files .lnk {
  font-weight: 500; font-size: 11.5px;
}
.lnk {
  background: none; border: none; color: #b45309; text-decoration: underline;
  cursor: pointer; font-family: inherit; padding: 0;
}
.rv-result-item {
  padding: 8px 0; border-bottom: 1px solid var(--border);
}
.rv-result-item:last-child { border-bottom: none; }
.rv-result-file {
  font-size: 12.5px; font-weight: 700; margin-bottom: 3px;
}
.rv-history {
  margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border);
}
.card-title.small {
  font-size: 13px; margin-bottom: 8px;
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
  color: var(--ink);
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
.result-success { border-left-color: #009142; }
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

/* ===== 待人工審核的批次處理 ===== */
.revbox { margin: 14px 0 4px; padding: 12px 14px; border-radius: 10px;
  border: 1px solid rgba(255,180,84,.32); background: rgba(255,180,84,.05); }
.revhead { font-size: 13px; color: #b45309; display: flex; align-items: baseline;
  gap: 8px; flex-wrap: wrap; }
.revhead b { font-size: 15px; }
.revsub { font-size: 12px; opacity: .72; }
.revline { margin: 8px 0 10px; font-size: 13px; line-height: 1.7; }
.revline .hit { color: var(--brand, #009142); font-size: 15px; }
.revline .lnk { background: none; border: none; color: #b45309; text-decoration: underline;
  cursor: pointer; font-size: 12px; font-family: inherit; padding: 0; margin-left: 4px; }
.revtbl { max-height: 260px; overflow-y: auto; margin-bottom: 10px; }
.revtbl .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.revnote { margin: 8px 0 0; font-size: 12px; opacity: .72; line-height: 1.6; }
.revok { margin: 10px 0 0; font-size: 13px; color: var(--brand, #009142); }

</style>
