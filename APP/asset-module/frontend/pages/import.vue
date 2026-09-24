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

// 頂端目錄用：捲到對應的匯入卡片。三條匯入路徑在同一頁上下排開,
// 使用者找不到就會以為功能不存在(2026-08-26 找「人員通訊錄」找不到)。
function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })
}

// 共用的「打一支 GET、把回應存成檔案」——匯出資產／兩份空白範本都是同一個動作，
// 只是網址跟預設檔名不同，寫三份遲早某一份漏改 credentials 或漏處理 !res.ok。
// 點「選擇檔案」時先清掉 input 的值：瀏覽器對「再選同一個檔」不會觸發 change，
// 匯入完我們把檔案變數清空了，但框裡還顯示舊檔名——再選同一份 APID.xlsx 就永遠
// 按不下「匯入」（2026-09-14 公司機）。先清值，選哪個檔都一定會觸發 change。
function resetFileInput(e: Event) {
  (e.target as HTMLInputElement).value = ''
}

async function downloadFile(path: string, fallbackName: string) {
  const res = await fetch(`${runtimeConfig.public.apiBase}${path}`, { credentials: 'include' })
  if (!res.ok) throw new Error(await httpReason(res))
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition') || ''
  const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = m ? decodeURIComponent(m[1]) : fallbackName
  a.click()
  URL.revokeObjectURL(url)
}

// ===== 匯出：每一種匯入都配一個匯出（2026-09-09）=====
// 三種格式回答**不同的問題**，不是同一份東西的三種包裝：
//   原始檔＝當初送進來的那個檔／Excel＝現在系統裡是什麼／dump＝整包搬到另一台
// 所以按鈕要分開擺、各自寫清楚，不要做成一個「匯出」下拉選單讓人以為一樣。
interface ExportSource {
  key: string; label: string; row_count: number; origin: string
  has_original: boolean; original_name: string | null; original_size: number | null
}
const exportSources = ref<ExportSource[]>([])
const exportBusy = ref('')

async function loadExportSources() {
  try {
    const r = await apiFetch<{ sources: ExportSource[] }>('/api/import/sources')
    exportSources.value = r.sources ?? []
  } catch { /* 讀不到就不顯示，不擋整頁 */ }
}
onMounted(loadExportSources)

// 匯入表格：四種來源集中在一張表，跟匯出表格對齊，一眼看得到「有哪些、現在幾筆」。
// 下面各自的區塊**保留不動**——RVTools 可多檔、CIA 要預覽與欄位對應，
// 那些是這張表容不下的細節。表格是入口與總覽，不是取代。
function scrollToImport(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function rowCount(key: string): number {
  return exportSources.value.find((s) => s.key === key)?.row_count ?? 0
}
// 讓匯入表格每一排能直接放一顆「匯出」（同欄位、可原封匯回）——不用捲到下面另一張表找。
function exportSrc(key: string): ExportSource | undefined {
  return exportSources.value.find((s) => s.key === key)
}

// ===== 帳號盤點（2026-09-18 使用者：「應該放圖2」——放進匯入／匯出這張表，一列跟其他來源一樣）=====
interface AaStatus { latest: { id: number; row_count: number; host_count: number; imported_at: string; file_name: string | null } | null }
interface AaResult {
  batch_id: number; rows: number; hosts: number; header: string[]; unknown_columns: string[]
  skipped: { row: number; reason: string }[]; skipped_count: number
}
const aaStatus = ref<AaStatus | null>(null)
const aaFile = ref<File | null>(null)
const aaBusy = ref(false)
const aaResult = ref<AaResult | null>(null)
const aaKey = ref(0)          // 匯入後讓下方明細卡片重新載入
const aaMenu = ref(false)     // Excel 按鈕的選單
async function loadAaStatus() {
  try { aaStatus.value = await apiFetch<AaStatus>('/api/account-audit/status') }
  catch (e: any) { showToast(`帳號盤點狀態載入失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error') }
}
onMounted(loadAaStatus)
function aaPick(e: Event) { aaFile.value = (e.target as HTMLInputElement).files?.[0] ?? null; aaResult.value = null }
function aaDownload(path: string) {
  window.open(((useRuntimeConfig().public as any).apiBase || '') + path, '_blank')
}
async function aaImport() {
  if (!aaFile.value) return
  aaBusy.value = true
  try {
    const fd = new FormData()
    fd.append('file', aaFile.value)
    const res = await fetch(`${(useRuntimeConfig().public as any).apiBase || ''}/api/account-audit/import`,
      { method: 'POST', body: fd, credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    aaResult.value = await res.json()
    showToast(`已匯入上次盤點：${aaResult.value!.rows} 筆、${aaResult.value!.hosts} 台`, 'success')
    await loadAaStatus()
    await loadExportSources()   // 原始檔／筆數跟著更新
    aaKey.value++
  } catch (e: any) {
    showToast(`帳號盤點匯入失敗：${e?.message ?? e}`, 'error', 12000)
  } finally {
    aaBusy.value = false
  }
}

async function doExport(src: ExportSource, kind: 'original' | 'excel' | 'dump') {
  exportBusy.value = `${src.key}:${kind}`
  try {
    await downloadFile(`/api/import/${src.key}/export/${kind}`, `${src.key}.${kind}`)
  } catch (e: any) {
    // 「匯出失敗」對使用者沒有用——沒有原始檔跟伺服器壞掉要分開講
    showToast(kind === 'original' && String(e?.message).includes('404')
      ? '這個來源沒有留原始檔（可能是還沒匯入過，或那次匯入早於這個功能上線）'
      : '匯出失敗，請稍後再試', 'error', 8000)
  } finally {
    exportBusy.value = ''
  }
}

// 帳外資產（DYN-/VC-/AUTO-）要不要一起交出去，使用者 2026-08-25：
// 「回交清單也要完全準確」——但兩種意思不同（只交CIA=更新你們的清單／
// 含帳外=這是我實際盤到的，含你們沒登記的 N 台），這是他要拍板的事，
// 不是我幫他決定，所以做成匯出當下的勾選，不是寫死一種行為。
const includeOffBook = ref(false)

async function downloadExport() {
  exporting.value = true
  try {
    const q = includeOffBook.value ? '?include_off_book=true' : ''
    await downloadFile(`/api/export${q}`, 'assets_export.xlsx')
    showToast(
      includeOffBook.value
        ? '已匯出資產資料（含帳外資產，「來源」欄可篩出哪些是你們清單上沒有的）'
        : '已匯出資產資料（僅 CIA 登記，未含帳外資產）',
      'success',
    )
  } catch (err: any) {
    showToast(`匯出失敗：${err?.message ?? '請稍後重試'}`, 'error')
  } finally {
    exporting.value = false
  }
}

// ===== 機房搬遷盤點表（青埔）：匯出/匯入也放這頁，方便找（2026-09-14 使用者）=====
const relLocs = ref<{ loc: string; n: number }[]>([])
const relPicked = ref('')
const relImporting = ref(false)
const relCfg = useRuntimeConfig()
onMounted(async () => {
  try { relLocs.value = (await apiFetch<any>('/api/relocation/locations')).items } catch {}
})
const relExportUrl = computed(() => {
  const b = (relCfg.public as any).apiBase || ''
  const q = relPicked.value ? `?location=${encodeURIComponent(relPicked.value)}` : ''
  return `${b}/api/relocation/export${q}`
})
async function relImport(e: Event) {
  const inp = e.target as HTMLInputElement
  const f = inp.files?.[0]
  if (!f) return
  relImporting.value = true
  try {
    const b = (relCfg.public as any).apiBase || ''
    const fd = new FormData()
    fd.append('file', f)
    const res = await fetch(`${b}/api/relocation/import`, { method: 'POST', body: fd, credentials: 'include' })
    if (!res.ok) {
      let d: any = {}
      try { d = await res.json() } catch {}
      throw new Error(d.detail || ('HTTP ' + res.status))
    }
    const h = res.headers
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = '機房搬遷盤點表_校對版.xlsx'
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
    showToast(`對帳完成：一致 ${h.get('X-Same')}、不一致 ${h.get('X-Diff')}、系統補值 ${h.get('X-Filled')}、查無序號 ${h.get('X-Unknown')}。校對版已下載`, 'success', 15000)
  } catch (err: any) {
    showToast(`匯入失敗：${err?.message ?? err}`, 'error', 15000)
  } finally {
    relImporting.value = false
    inp.value = ''
  }
}

// ===== AD 人員名單（B-23）=====
// 用員編把主機帳號對到人。三張「對不上」清單一定要看得到筆數並點得進去——
// 留空會被當成「沒問題」，那跟「對不上」是兩件事。
const adFile = ref<File | null>(null)
const adUploading = ref(false)
const adErr = ref('')
const adOk = ref('')
const adPanel = ref('')
const adSummary = ref<any>(null)
const adMgr = ref<any[]>([])
const adAcct = ref<any>({ no_empid: [], not_in_ad: [] })
const adOff = ref<any[]>([])

async function loadAd() {
  try {
    adSummary.value = await apiFetch<any>('/api/ad/summary')
  } catch { /* 沒匯過就是沒有，不用吵 */ }
}
loadAd()

function handleAdFile(e: Event) {
  adFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
  adErr.value = ''; adOk.value = ''
}

async function submitAdImport() {
  if (!adFile.value) return
  adUploading.value = true; adErr.value = ''; adOk.value = ''
  try {
    const fd = new FormData()
    fd.append('file', adFile.value)
    const r = await apiFetch<any>('/api/ad/import', { method: 'POST', body: fd })
    adOk.value = `已匯入 ${r.rows} 人（啟用 ${r.enabled_n}／停用 ${r.disabled_n}）`
      + `；主管對上 ${r.mgr_ok}`
      + (r.mgr_fail ? `，對不上 ${r.mgr_fail}` : '')
      + (r.mgr_dupname ? `，同名疑慮 ${r.mgr_dupname}` : '')
      // Excel 會把員編 01003418 吃成 1003418。員編是對應主機帳號的唯一鍵，
      // 掉了前導零會**安靜地全部對不上**。系統替資料做了修正，人要看得到。
      + (r.zero_padded ? `　⚠ 已還原被 Excel 吃掉的前導零：${r.zero_padded} 筆` : '')
    await loadAd()
  } catch (e: any) {
    adErr.value = e?.data?.detail || e?.message || '匯入失敗'
  } finally {
    adUploading.value = false
  }
}

watch(adPanel, async (v) => {
  try {
    if (v === 'mgr') adMgr.value = (await apiFetch<any>('/api/ad/unmatched-managers')).items ?? []
    else if (v === 'acct') adAcct.value = await apiFetch<any>('/api/ad/unmatched-accounts')
    else if (v === 'off') adOff.value = (await apiFetch<any>('/api/ad/disabled-with-account')).items ?? []
  } catch (e: any) {
    adErr.value = e?.data?.detail || e?.message || '讀不到清單'
  }
})

// ===== 網段配置表／存活清單：空白範本下載（2026-08-25 使用者通則：
// 有匯入就要有配對的匯出範本，緊鄰上傳按鈕，不放頁首或另一頁）=====
const segTemplateDownloading = ref(false)
async function downloadSegmentsTemplate() {
  segTemplateDownloading.value = true
  try {
    await downloadFile('/api/segments/export-template', 'segments_template.xlsx')
  } catch (err: any) {
    showToast(`下載失敗：${err?.message ?? '請稍後重試'}`, 'error')
  } finally {
    segTemplateDownloading.value = false
  }
}
const dynTemplateDownloading = ref(false)
async function downloadDynassetsTemplate() {
  dynTemplateDownloading.value = true
  try {
    await downloadFile('/api/import/dynassets/export-template', 'dynassets_template.xlsx')
  } catch (err: any) {
    showToast(`下載失敗：${err?.message ?? '請稍後重試'}`, 'error')
  } finally {
    dynTemplateDownloading.value = false
  }
}

// 其餘四種匯入的範例檔（2026-09-10 使用者：「每一個都要範例檔案，不然 USER 不會知道
// 要匯入什麼資料」）。範例列以「範例」開頭，原封不動匯回來後端會略過。
const tplBusy = ref('')
async function downloadTemplate(source: string) {
  tplBusy.value = source
  try {
    await downloadFile(`/api/import/${source}/export-template`, `${source}_範例.xlsx`)
  } catch (err: any) {
    showToast(`範例檔下載失敗：${err?.message ?? '請稍後重試'}`, 'error')
  } finally {
    tplBusy.value = ''
  }
}

const lastImport = ref<ImportLog | null>(null)
const originalMapping = ref<FieldMapping>({})
const workingMapping = ref<FieldMapping>({})
const selectedFile = ref<File | null>(null)
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
  // 這份匯出是從哪個管理端抓的（RVTools 的 VI SDK Server）。正常只有一個值——
  // 一份匯出＝連一個管理端。出現兩個以上代表這份檔被人合併過。
  vi_sdk_servers?: string[]
  // 換了來源 VC 的 VM。**跨 vCenter 搬遷是正常的**（使用者 2026-08-28），
  // 所以這是紀錄不是警告：中性字色、預設收起。
  vc_moved?: { asset_serial: string; name: string; from: string; to: string }[]
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
const rvUploading = ref(false)
const rvResults = ref<RvFileResult[]>([])
const rvError = ref('')
const rvHistory = ref<ImportLogEntry[]>([])
// 哪幾份檔案的「來源 VC 變動」明細被展開了。預設全部收起——飄移是常態，
// 攤開來會把匯入結果洗掉；要查的時候點開就好。
const movedOpen = ref<Record<number, boolean>>({})
const { sortKey: rvHistSortKey, sortDir: rvHistSortDir, toggle: rvHistToggle, sorted: rvHistSorted } =
  useSort(rvHistory, 'imported_at')

function handleRvFileInput(e: Event) {
  rvFiles.value = Array.from((e.target as HTMLInputElement).files ?? [])
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
  // 第二條規則（2026-08-24）：vCenter 帶 vm_uuid、資產這邊還沒填。舊後端沒有這欄，
  // 設成選用，畫面就只是不顯示這一區。
  uuid_rule?: {
    rule: string; rule_label: string; matchable: number; distinct_assets: number
    skipped: Record<string, number>
    samples: { incoming_hostname: string; incoming_ip: string; target_serial: string
               target_hostname: string; target_ip: string; vm_uuid: string; matched_on: string }[]
  }
}
const reviewInfo = ref<ReviewInfo | null>(null)
const reviewLoading = ref(false)
const reviewMerging = ref(false)
const reviewMsg = ref('')
const reviewOpen = ref(false)
const uuidOpen = ref(false)

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

async function doBatchMerge(which: 'short' | 'uuid' = 'short') {
  const base = reviewInfo.value
  if (!base) return
  // 兩條規則走同一支 API、同一個確認流程，只是帶不同的 rule 名稱。
  const info = which === 'uuid' && base.uuid_rule
    ? { rule: base.uuid_rule.rule, rule_label: base.uuid_rule.rule_label,
        matchable: base.uuid_rule.matchable,
        note: `這 ${base.uuid_rule.matchable.toLocaleString()} 筆其實是 `
            + `${base.uuid_rule.distinct_assets.toLocaleString()} 台機器`
            + `（重複匯入讓同一台被記了好幾筆），而且只寫入 vm_uuid 這一個欄位。` }
    : { rule: base.rule, rule_label: base.rule_label, matchable: base.matchable,
        note: '只會寫入 OS／虛實／VM 識別碼。' }
  if (!info.matchable) return
  const ok = confirm(
    `即將把 ${info.matchable} 筆併進既有資產。\n\n`
    + `配對條件：${info.rule_label}\n`
    + `${info.note}你填的用途、保管者、機房不會被改動。\n\n`
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

// 這批匯入是否「較舊也一律覆蓋」。false=還沒問過；true=使用者已對整批同意過一次，
// 之後遇到較舊的檔就直接覆蓋、不再一一問（2026-09-09 使用者：檔案多時要按很多次）。
// 每次按「匯入」都在 submitRvImport 開頭歸零，同意不會跨批殘留。
const rvForceAll = ref(false)

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
    if (d && typeof d === 'object' && d.code === 'stale_import') {
      // 整批已經同意過一次 → 這份直接覆蓋，不再跳框（省得檔案多要按很多次）。
      if (rvForceAll.value) return submitOneRvFile(file, true)
      const ok = confirm(
        `「${file.name}」的資料時間是 ${d.incoming_data_at}，\n`
        + `比系統現有資料（${d.current_data_at}）還舊。\n\n`
        + `繼續匯入會用舊資料覆蓋較新的 OS／虛實／主機名等欄位，\n`
        + `而且系統不保留欄位變更歷史，蓋掉後只能從每日備份還原。\n\n`
        + `按「確定」＝這批剩下較舊的檔也一律照樣覆蓋，不再一一問。\n`
        + `按「取消」＝略過這一份。`,
      )
      if (ok) {
        rvForceAll.value = true   // 一次同意，整批後面的較舊檔都套用
        return submitOneRvFile(file, true)
      }
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
  rvForceAll.value = false   // 每次匯入重新問一次，整批同意不跨批殘留
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

// ===== 業務系統對照表（api_id → 系統名稱／AP 部門／AP 負責人）=====
// 為什麼要有：資產庫只有 api_id（N-008 這種代碼），沒有中文名稱。在這之前系統
// 名稱是拿同一個 api_id 底下 MIN(asset_name) 湊的——「隨便挑一台機器的名字當
// 系統名」，猜對是運氣。帳號盤點要交出去的 Excel 第一欄就是 system_id + system。
const bsFile = ref<File | null>(null)
const bsUploading = ref(false)
const bsError = ref('')
const bsResult = ref<any>(null)

function handleBsFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) { bsFile.value = f; bsError.value = '' }
}

// ===== 系統類別對照表（第一／二／三類，2026-09-10）=====
// 使用者給的 APID.xlsx（項次／系統類別／APID／系統別）。**只更新類別**——
// 不能走上面的對照表匯入：那支整列覆寫，AP 部門與負責人會被這份沒有那兩欄的檔清空。
const clsFile = ref<File | null>(null)
const clsUploading = ref(false)
const clsResult = ref<any>(null)
function handleClsFile(e: Event) {
  clsFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
}
// 目前筆數：已分級幾個系統（那格原本寫死「—」，匯入成功了卻看不到數字）
const clsCount = ref<{ rated: number; total: number } | null>(null)
async function loadClsCount() {
  try { clsCount.value = await apiFetch('/api/business-systems/class-count') } catch { /* 舊後端沒有這支就顯示 — */ }
}
onMounted(loadClsCount)

async function submitClsImport() {
  if (!clsFile.value) return
  clsUploading.value = true
  clsResult.value = null
  try {
    const fd = new FormData()
    fd.append('file', clsFile.value)
    const r = await apiFetch<any>('/api/business-systems/import-class', { method: 'POST', body: fd })
    clsResult.value = r
    // 對不上的要講出來，不能只說「完成」
    const extra = []
    if (r.not_in_table?.length) extra.push(`${r.not_in_table.length} 個代碼對照表沒有`)
    if (Object.keys(r.conflicts ?? {}).length) extra.push(`${Object.keys(r.conflicts).length} 個同碼多類沒設`)
    if (r.example_rows) extra.push(`${r.example_rows} 列範例已略過`)
    showToast(r.warning
      ? `⚠️ ${r.warning}（已更新 ${r.updated} 個系統）`
      : `系統類別匯入完成：更新 ${r.updated} 個系統${extra.length ? '（' + extra.join('、') + '）' : ''}`,
              r.warning ? 'error' : extra.length ? 'warn' : 'success', 12000)
    clsFile.value = null
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '系統類別匯入失敗', 'error', 10000)
  } finally {
    clsUploading.value = false
    loadClsCount()
  }
}

// ===== 網段配置表（2026-09-11 從 2-4 網段頁搬過來）=====
// 使用者：「網段的匯入匯出應該要到同一個地方，由 B1 去做匯入」。
// 2-4 只留檢視與下鑽；上傳、範本、匯出統一在這裡，才不會有兩個入口各說各話。
// 整批取代：沒出現在這次檔案裡的網段會消失——所以「消失幾段、哪幾段」要在這裡當場講清楚。
const segCount = ref<number | null>(null)
const segFile = ref<File | null>(null)
const segUploading = ref(false)
const segResult = ref<any>(null)
const segExporting = ref(false)
async function loadSegCount() {
  try {
    segCount.value = (await apiFetch<{ segments: unknown[] }>('/api/segments')).segments.length
  } catch { segCount.value = null }   // 只是網段列的數量徽章，載不到就不顯示數字，不擋匯入頁
}
onMounted(loadSegCount)
function handleSegFile(e: Event) {
  segFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
}
async function submitSegImport() {
  if (!segFile.value) return
  segUploading.value = true
  segResult.value = null
  try {
    const fd = new FormData()
    fd.append('file', segFile.value)
    const r = await apiFetch<any>('/api/segments/import', { method: 'POST', body: fd })
    segResult.value = r
    const risky = r.removed_count > 0 || r.warnings?.length
    showToast(`網段匯入完成：${r.imported} 段`
      + (r.removed_count ? `，⚠ 消失 ${r.removed_count} 段` : '')
      + (r.warnings?.length ? `，${r.warnings.length} 筆需要人看` : ''),
    risky ? 'warn' : 'success', 12000)
    segFile.value = null
    await loadSegCount()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '網段匯入失敗', 'error', 10000)
  } finally {
    segUploading.value = false
  }
}
async function exportSegments() {
  segExporting.value = true
  try {
    await downloadFile('/api/segments/export', 'segments.xlsx')
  } catch (err: any) {
    showToast(`網段匯出失敗：${err?.message ?? '請稍後重試'}`, 'error')
  } finally {
    segExporting.value = false
  }
}
async function downloadSegTemplate() {
  tplBusy.value = 'segments'
  try {
    await downloadFile('/api/segments/export-template', 'segments_template.xlsx')
  } catch (err: any) {
    showToast(`範例檔下載失敗：${err?.message ?? '請稍後重試'}`, 'error')
  } finally {
    tplBusy.value = ''
  }
}

async function submitBsImport() {
  if (!bsFile.value) { bsError.value = '請先選擇對照表（.xlsx 或 .csv）'; return }
  bsUploading.value = true
  bsError.value = ''
  bsResult.value = null
  try {
    const formData = new FormData()
    formData.append('file', bsFile.value)
    bsResult.value = await apiFetch<any>('/api/business-systems/import',
                                         { method: 'POST', body: formData })
    showToast(
      `對照表匯入完成：${bsResult.value.imported} 個系統`
      + (bsResult.value.multi_source ? `，其中 ${bsResult.value.multi_source} 個多來源待確認` : '')
      + (bsResult.value.example_rows ? `（${bsResult.value.example_rows} 列範例已略過）` : ''),
      'success', bsResult.value.multi_source ? 8000 : undefined,
    )
    bsFile.value = null
  } catch (err: any) {
    const d = err?.data?.detail
    bsError.value = (typeof d === 'string' ? d : d?.message) ?? '對照表匯入失敗'
    showToast(bsError.value, 'error', 12000)
  } finally {
    bsUploading.value = false
  }
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

// 清空盤點資料已移到獨立頁（5B-4 /reset）——危險操作不再跟匯入混在同一頁。
</script>

<template>
  <div>
    <div class="section-divider">資料匯入</div>
    <div class="breadcrumb-bar">
      <span class="pin">📌</span> <b>資料匯入</b>
      <button class="export-btn" :disabled="exporting" @click="downloadExport">
        {{ exporting ? '匯出中…' : '⬇ 匯出資產（含人員通訊錄）' }}
      </button>
    </div>

    <!-- 這頁有三條獨立的匯入路徑，順序上 CIA 在最後——使用者找「人員通訊錄」時
         捲不到就以為沒有（2026-08-26 實際發生）。頂端直接列出「哪個東西在哪裡」，
         等於一份目錄。 -->
    <p class="whereis">
      <b>要匯什麼，看這裡：</b>
      <a @click="scrollTo('rvtools')">vCenter 虛擬機清單</a>　·
      <a @click="scrollTo('dynassets')">網路存活主機清單</a>　·
      <a @click="scrollTo('bizsys')">業務系統對照表</a>　·
      <a @click="scrollTo('cia')">資產清單／<b>人員通訊錄（分機、代理人）</b>／軟體</a>
    </p>

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

    <!-- 匯入：四種來源集中一張表，跟下面的匯出表格對齊 -->
    <div v-if="exportSources.length" class="card">
      <div class="card-title">
        匯入／匯出
        <InfoNote>每一種來源一列：左邊選檔<b>匯入</b>，右邊直接<b>匯出</b>——同一列看得到「現在幾筆」，不用再捲到別張表。<br><br>RVTools 那列可以<b>一次選多個檔</b>（每台 vCenter 一個），會一個一個匯。按「明細」跳到下面的卡片看逐檔報告、待審核合併、欄位對應。<br><br>匯出三顆鈕回答<b>不同問題</b>：<b>原始檔</b>＝當初送進來那個檔（一位元沒動，對帳用）；<b>Excel</b>＝<b>現在</b>系統裡是什麼（正規化後，給人看／改）；<b>dump</b>＝整包搬到另一台再匯回。<br>⚠️ dump <b>目前是明碼</b>（含 IP／主機名／負責人），別當備份寄出，要寄用「備份與還原」的加密匯出。</InfoNote>
      </div>
      <div class="tbl-wrap">
        <table class="exp-tbl">
          <thead><tr>
            <th>來源</th>
            <th class="num">目前筆數</th>
            <th>檔案格式</th>
            <th>選擇檔案</th>
            <th>動作</th>
          </tr></thead>
          <tbody>
            <tr>
              <td>存活清單（dynassets）</td>
              <td class="num">{{ rowCount('dynassets') }}</td>
              <td class="fmt">.csv／.xlsx</td>
              <td><input type="file" accept=".csv,.xlsx" :disabled="dynUploading" @click="resetFileInput" @change="handleDynFile" /></td>
              <td class="ops">
                <button class="btn small primary" type="button"
                        :disabled="!dynFile || dynUploading" @click="submitDynImport">
                  {{ dynUploading ? '匯入中…' : '匯入' }}
                </button>
                <button class="btn small" type="button" @click="scrollToImport('dynassets')">明細</button>
                <button class="btn small" type="button" :disabled="dynTemplateDownloading"
                        title="下載範例檔：看要填哪些欄位" @click="downloadDynassetsTemplate">範例檔</button>
                <template v-if="exportSrc('dynassets')">
                  <span class="ops-div" aria-hidden="true" />
                  <button class="btn small" type="button"
                          :disabled="!exportSrc('dynassets')!.has_original || !!exportBusy"
                          :title="exportSrc('dynassets')!.has_original ? '原始檔：' + exportSrc('dynassets')!.original_name : '沒有留原始檔（此功能上線前匯入的那批）'"
                          @click="doExport(exportSrc('dynassets')!, 'original')">原始檔</button>
                  <button class="btn small" type="button" :disabled="!rowCount('dynassets') || !!exportBusy"
                          title="匯出目前系統裡的資料（正規化後，可原封匯回）" @click="doExport(exportSrc('dynassets')!, 'excel')">Excel</button>
                  <button class="btn small" type="button" :disabled="!rowCount('dynassets') || !!exportBusy"
                          title="整包 dump，搬到另一台再匯回（明碼，勿當備份寄出）" @click="doExport(exportSrc('dynassets')!, 'dump')">dump</button>
                </template>
              </td>
            </tr>
            <!-- AD 人員名單（B-23）。2026-09-22 使用者請 AD 人員產名單。
                 放這張表而不是另開頁：匯入入口只有一個地方（跟弱點匯入同樣的理由）。
                 ⚠️ 這是全體員工個資——檔案只在記憶體解析，不落磁碟。 -->
            <tr>
              <td>
                AD 人員名單
                <InfoNote>
                  用<b>員編</b>把主機帳號對到人：主機帳號的 gecos 開頭就是員編
                  （<code>01002861-姓名_部門</code>），AD 的 SamAccountName／employeeID 也是。<br>
                  姓名會改、會同名，<b>不能當關聯鍵</b>。<br><br>
                  主管用「<b>姓名＋部門代碼</b>」兩個鍵一起對——AD 同名時 CN 會加後綴
                  （<code>王五2</code>），只比姓名會對到錯的人。<b>對不上就列清單，不硬湊。</b>
                </InfoNote>
              </td>
              <td class="num">{{ adSummary?.total ?? '—' }}</td>
              <td class="fmt">.xlsx／.csv</td>
              <td><input type="file" accept=".xlsx,.csv" :disabled="adUploading"
                         @click="resetFileInput" @change="handleAdFile" /></td>
              <td class="ops">
                <button class="btn small primary" type="button"
                        :disabled="!adFile || adUploading" @click="submitAdImport">
                  {{ adUploading ? '匯入中…' : '匯入' }}
                </button>
                <button class="btn small" type="button" :disabled="!adSummary?.total"
                        @click="adPanel = adPanel === 'mgr' ? '' : 'mgr'">主管對不上</button>
                <button class="btn small" type="button" :disabled="!adSummary?.total"
                        @click="adPanel = adPanel === 'acct' ? '' : 'acct'">帳號對不上</button>
                <button class="btn small" type="button" :disabled="!adSummary?.total"
                        @click="adPanel = adPanel === 'off' ? '' : 'off'">離職仍有帳號</button>
              </td>
            </tr>
            <!-- 這份名單哪來的。人員名單是「這個帳號是誰的」的判斷依據，
                 依據本身要講得出出處；note 用來標「這是測試資料」之類的警示。 -->
            <tr v-if="adSummary?.batch">
              <td colspan="5" class="ad-src">
                <span v-if="adSummary.batch.note" class="ad-note">
                  ⚠ {{ adSummary.batch.note }}
                </span>
                <span class="dim">
                  來源：{{ adSummary.batch.file_name || '—' }}
                  · {{ adSummary.batch.imported_at || '—' }}
                  · 匯入者 {{ adSummary.batch.imported_by || '—' }}
                </span>
              </td>
            </tr>
            <tr v-if="adErr || adOk">
              <td colspan="5">
                <p v-if="adErr" class="err">{{ adErr }}</p>
                <p v-if="adOk" class="ok">{{ adOk }}</p>
              </td>
            </tr>
            <tr v-if="adPanel">
              <td colspan="5">
                <div v-if="adPanel === 'mgr'">
                  <h4>主管對不上／同名疑慮（{{ adMgr.length }}）</h4>
                  <p class="fmt">
                    「沒有主管」不在這裡——那是正常的（例如最上層）。這裡列的是
                    <b>DN 指向的人在名單裡找不到</b>，或<b>同名分不出來</b>。
                  </p>
                  <table class="mini"><thead><tr>
                    <th>員編</th><th>姓名</th><th>部門</th><th>狀態</th><th>主管 DN</th>
                  </tr></thead><tbody>
                    <tr v-for="m in adMgr" :key="m.employee_id">
                      <td class="mono">{{ m.employee_id }}</td><td>{{ m.display_name }}</td>
                      <td>{{ m.dept_name }}（{{ m.dept_code || '無代碼' }}）</td>
                      <td :class="m.manager_state === '同名疑慮' ? 'warn' : 'err'">{{ m.manager_state }}</td>
                      <td class="mono sm">{{ m.manager_dn }}</td>
                    </tr>
                  </tbody></table>
                </div>
                <div v-else-if="adPanel === 'acct'">
                  <h4>主機帳號對不到人</h4>
                  <p class="fmt">
                    兩種原因分開列，<b>處理方式不同</b>：gecos 沒有員編要去主機端補；
                    有員編但名單查無，是名單不全或員編已失效。
                  </p>
                  <h5>gecos 解析不出員編（{{ adAcct.no_empid?.length ?? 0 }}）</h5>
                  <table class="mini"><thead><tr><th>IP</th><th>帳號</th><th>gecos</th></tr></thead>
                    <tbody><tr v-for="(a, i) in adAcct.no_empid" :key="'n' + i">
                      <td class="mono">{{ a.ip }}</td><td>{{ a.username }}</td>
                      <td class="sm">{{ a.gecos || '（空白）' }}</td></tr></tbody></table>
                  <h5>AD 名單查無此員編（{{ adAcct.not_in_ad?.length ?? 0 }}）</h5>
                  <table class="mini"><thead><tr><th>IP</th><th>帳號</th><th>員編</th></tr></thead>
                    <tbody><tr v-for="(a, i) in adAcct.not_in_ad" :key="'a' + i">
                      <td class="mono">{{ a.ip }}</td><td>{{ a.username }}</td>
                      <td class="mono">{{ a.employee_id }}</td></tr></tbody></table>
                </div>
                <div v-else-if="adPanel === 'off'">
                  <h4>已離職（AD 停用）但主機還有帳號（{{ adOff.length }}）</h4>
                  <p class="fmt"><b>這是稽核發現，不是雜訊。</b></p>
                  <table class="mini"><thead><tr>
                    <th>IP</th><th>帳號</th><th>員編</th><th>姓名</th><th>部門</th>
                  </tr></thead><tbody>
                    <tr v-for="(a, i) in adOff" :key="'o' + i">
                      <td class="mono">{{ a.ip }}</td><td>{{ a.username }}</td>
                      <td class="mono">{{ a.employee_id }}</td><td>{{ a.display_name }}</td>
                      <td>{{ a.dept_name }}</td></tr>
                  </tbody></table>
                </div>
              </td>
            </tr>
            <tr>
              <td>RVTools（vCenter）</td>
              <td class="num">{{ rowCount('rvtools') }}</td>
              <td class="fmt">.xlsx（可多檔）</td>
              <td>
                <input type="file" accept=".xlsx" multiple :disabled="rvUploading"
                       @click="resetFileInput" @change="handleRvFileInput" />
                <span v-if="rvFiles.length > 1" class="fmt">已選 {{ rvFiles.length }} 個</span>
              </td>
              <td class="ops">
                <button class="btn small primary" type="button"
                        :disabled="!rvFiles.length || rvUploading" @click="submitRvImport">
                  {{ rvUploading ? '匯入中…' : '匯入' }}
                </button>
                <button class="btn small" type="button" @click="scrollToImport('rvtools')">
                  明細<span v-if="reviewInfo && reviewInfo.total_open" class="rv-pending">待審 {{ reviewInfo.total_open.toLocaleString() }}</span>
                </button>
                <button class="btn small" type="button" :disabled="tplBusy === 'rvtools'"
                        title="下載範例檔：看要填哪些欄位" @click="downloadTemplate('rvtools')">範例檔</button>
                <template v-if="exportSrc('rvtools')">
                  <span class="ops-div" aria-hidden="true" />
                  <button class="btn small" type="button"
                          :disabled="!exportSrc('rvtools')!.has_original || !!exportBusy"
                          :title="exportSrc('rvtools')!.has_original ? '原始檔：' + exportSrc('rvtools')!.original_name : '沒有留原始檔（此功能上線前匯入的那批）'"
                          @click="doExport(exportSrc('rvtools')!, 'original')">原始檔</button>
                  <button class="btn small" type="button" :disabled="!rowCount('rvtools') || !!exportBusy"
                          title="匯出目前系統裡的資料（正規化後，可原封匯回）" @click="doExport(exportSrc('rvtools')!, 'excel')">Excel</button>
                  <button class="btn small" type="button" :disabled="!rowCount('rvtools') || !!exportBusy"
                          title="整包 dump，搬到另一台再匯回（明碼，勿當備份寄出）" @click="doExport(exportSrc('rvtools')!, 'dump')">dump</button>
                </template>
              </td>
            </tr>
            <tr>
              <td>系統類別對照表
                <InfoNote><b>這是「代碼字典」，不是資產清單。</b>它把代碼（例 N-008）對到「第幾類」。<b>匯它不會讓資產被涵蓋</b>——「AP 系統涵蓋幾台」是看 CIA 資產清冊裡每台的 <b>APID 欄</b>，不是看這份。<br><br>第一／二／三類系統的分級表（例：APID.xlsx，欄位「系統類別」＋「APID」）。<b>只會更新系統類別</b>，不動 AP 部門與負責人。<b>整份取代</b>：不在這份清單裡的系統會回到「未分級」。<br><br>同一個代碼在檔案裡有兩種類別時不會猜，那個系統不設類別並列出來；清單有、對照表沒有的代碼也會列出來，不會自己新建。</InfoNote>
              </td>
              <td class="num" :title="clsCount ? `對照表 ${clsCount.total} 個系統，已分級 ${clsCount.rated} 個` : ''">
                {{ clsCount ? clsCount.rated.toLocaleString() : '—' }}
              </td>
              <td class="fmt">.xlsx／.csv</td>
              <td><input type="file" accept=".xlsx,.csv" :disabled="clsUploading" @click="resetFileInput" @change="handleClsFile" /></td>
              <td class="ops">
                <button class="btn small primary" type="button"
                        :disabled="!clsFile || clsUploading" @click="submitClsImport">
                  {{ clsUploading ? '匯入中…' : '匯入' }}
                </button>
                <span class="ops-gap" aria-hidden="true" />
                <button class="btn small" type="button" :disabled="tplBusy === 'sys_class'"
                        title="下載範例檔：看要填哪些欄位" @click="downloadTemplate('sys_class')">範例檔</button>
                <span v-if="clsResult?.not_in_table?.length" class="fmt" style="display:block">對照表沒有：{{ clsResult.not_in_table.join('、') }}</span>
              </td>
            </tr>
            <tr>
              <td>業務系統對照表
                <InfoNote><b>這是「代碼字典」，不是資產清單。</b>把代碼（例 N-008）對到系統中文名／AP 部門／負責人。<b>匯它不會讓資產被涵蓋</b>——涵蓋幾台看 CIA 資產清冊裡每台的 <b>APID 欄</b>。這份只是把代碼翻成看得懂的名字，讓報表不用一堆 N-xxx。</InfoNote>
              </td>
              <td class="num">{{ rowCount('business_system') }}</td>
              <td class="fmt">.xlsx</td>
              <td><input type="file" accept=".csv,.xlsx" :disabled="bsUploading" @click="resetFileInput" @change="handleBsFile" /></td>
              <td class="ops">
                <button class="btn small primary" type="button"
                        :disabled="!bsFile || bsUploading" @click="submitBsImport">
                  {{ bsUploading ? '匯入中…' : '匯入' }}
                </button>
                <button class="btn small" type="button" @click="scrollToImport('bizsys')">明細</button>
                <button class="btn small" type="button" :disabled="tplBusy === 'business_system'"
                        title="下載範例檔：看要填哪些欄位" @click="downloadTemplate('business_system')">範例檔</button>
                <template v-if="exportSrc('business_system')">
                  <span class="ops-div" aria-hidden="true" />
                  <button class="btn small" type="button"
                          :disabled="!exportSrc('business_system')!.has_original || !!exportBusy"
                          :title="exportSrc('business_system')!.has_original ? '原始檔：' + exportSrc('business_system')!.original_name : '沒有留原始檔（此功能上線前匯入的那批）'"
                          @click="doExport(exportSrc('business_system')!, 'original')">原始檔</button>
                  <button class="btn small" type="button" :disabled="!rowCount('business_system') || !!exportBusy"
                          title="匯出目前系統裡的資料（正規化後，可原封匯回）" @click="doExport(exportSrc('business_system')!, 'excel')">Excel</button>
                  <button class="btn small" type="button" :disabled="!rowCount('business_system') || !!exportBusy"
                          title="整包 dump，搬到另一台再匯回（明碼，勿當備份寄出）" @click="doExport(exportSrc('business_system')!, 'dump')">dump</button>
                </template>
              </td>
            </tr>
            <tr>
              <td>CIA 資產清冊
                <InfoNote>三分頁（硬體／人員／軟體）。<b>硬體分頁的「APID」欄＝每台掛哪個系統代碼</b>，這才是讓「AP 系統」涵蓋不為 0 的來源；上面兩份對照表只把代碼翻成名稱／分類。<br><br>表頭認得 <code>APID／AP ID／API ID</code>。重匯會用資產序號對到既有那台、把 APID 補上（不會重複建）。</InfoNote>
              </td>
              <td class="num">{{ rowCount('cia_excel') }}</td>
              <td class="fmt">.xlsx（三分頁）</td>
              <td><input type="file" accept=".xlsx" :disabled="uploading" @click="resetFileInput" @change="handleFileInput" /></td>
              <td class="ops">
                <button class="btn small primary" type="button"
                        :disabled="!selectedFile || uploading" @click="submitImport">
                  {{ uploading ? '匯入中…' : '匯入' }}
                </button>
                <button class="btn small" type="button" @click="scrollToImport('cia')">明細</button>
                <button class="btn small" type="button" :disabled="tplBusy === 'cia_excel'"
                        title="下載範例檔：看要填哪些欄位" @click="downloadTemplate('cia_excel')">範例檔</button>
                <template v-if="exportSrc('cia_excel')">
                  <span class="ops-div" aria-hidden="true" />
                  <button class="btn small" type="button"
                          :disabled="!exportSrc('cia_excel')!.has_original || !!exportBusy"
                          :title="exportSrc('cia_excel')!.has_original ? '原始檔：' + exportSrc('cia_excel')!.original_name : '沒有留原始檔（此功能上線前匯入的那批）'"
                          @click="doExport(exportSrc('cia_excel')!, 'original')">原始檔</button>
                  <button class="btn small" type="button" :disabled="!rowCount('cia_excel') || !!exportBusy"
                          title="匯出目前系統裡的資料（正規化後，可原封匯回）" @click="doExport(exportSrc('cia_excel')!, 'excel')">Excel</button>
                  <button class="btn small" type="button" :disabled="!rowCount('cia_excel') || !!exportBusy"
                          title="整包 dump，搬到另一台再匯回（明碼，勿當備份寄出）" @click="doExport(exportSrc('cia_excel')!, 'dump')">dump</button>
                </template>
              </td>
            </tr>
            <tr>
              <td>網段配置表
                <InfoNote>公司的「總分公司網段配置表」Excel。<b>整批取代</b>：這次檔案裡沒有的網段會從系統消失，所以請上傳<b>完整清單</b>，不要只傳改過的那幾列。<br><br>解析不掉的寫法（一格兩段、IP 範圍）會保留並列出來，不會被靜默丟掉。看網段與下鑽到 IP 在「3-4 網段配置表」。</InfoNote>
              </td>
              <td class="num">{{ segCount ?? '—' }}</td>
              <td class="fmt">.xlsx／.csv／.tsv</td>
              <td><input type="file" accept=".xlsx,.xlsm,.txt,.tsv,.csv" :disabled="segUploading" @click="resetFileInput" @change="handleSegFile" /></td>
              <td class="ops">
                <button class="btn small primary" type="button"
                        :disabled="!segFile || segUploading" @click="submitSegImport">
                  {{ segUploading ? '匯入中…' : '匯入' }}
                </button>
                <NuxtLink class="btn small" to="/segments">明細</NuxtLink>
                <button class="btn small" type="button" :disabled="tplBusy === 'segments'"
                        title="下載範例檔：看要填哪些欄位" @click="downloadSegTemplate">範例檔</button>
                <span class="ops-div" aria-hidden="true" />
                <button class="btn small" type="button" disabled title="網段匯入沒有保留原始檔">原始檔</button>
                <button class="btn small" type="button" :disabled="!segCount || segExporting"
                        title="匯出目前網段（正規化後，可原封匯回）" @click="exportSegments">{{ segExporting ? '準備中…' : 'Excel' }}</button>
                <button class="btn small" type="button" disabled title="網段沒有 dump 格式，搬機用 Excel 即可">dump</button>
              </td>
            </tr>
            <!-- 網段是整批取代，消失的段要當場講清楚，不能只跳一個「完成」 -->
            <tr v-if="segResult">
              <td colspan="5" class="seg-result">
                原檔 <b>{{ segResult.source_rows }}</b> 列 → 進到系統 <b>{{ segResult.imported }}</b> 段
                <template v-if="segResult.was_empty">（第一次匯入，全部是新的）</template>
                <template v-else-if="!segResult.added_count && !segResult.removed_count">・✓ 跟匯入前完全一樣</template>
                <template v-else>・新增 {{ segResult.added_count }}・<b class="seg-bad">消失 {{ segResult.removed_count }}</b>・不變 {{ segResult.unchanged_count }}</template>
                <div v-if="segResult.removed_count" class="seg-bad">
                  ⚠ 消失的：<code v-for="c in segResult.removed" :key="c">{{ c }} </code>
                  <span v-if="segResult.removed_count > segResult.removed.length">…等 {{ segResult.removed_count }} 段</span>
                  ——如果你傳的是部分清單，請改用完整檔重匯。
                </div>
                <div v-for="(w, i) in segResult.warnings" :key="i">
                  需要人看：第 {{ w.row_no }} 列 <code>{{ w.raw_cidr }}</code> {{ w.reason }}
                </div>
              </td>
            </tr>
            <tr>
              <td>帳號盤點
                <InfoNote>把<b>上次的帳號盤點表</b>匯進來當比較基準（格式以你們原本的表為準，A～R）。每次匯入存一批、不覆蓋。<br><br>匯出：<b>盤點報告</b>＝標準 A～R 格式，欄位順序照你最後匯入的檔案，另附「與上次比較」分頁，可直接交出去、也能當下一輪匯回；<b>全部欄位</b>＝再加 sudo、密碼、金鑰、最後登入等內部管理欄。<br><br>「本次未盤點到這台」＝這台這次沒收集到帳號資料，<b>不代表帳號被刪除</b>。</InfoNote>
              </td>
              <td class="num" :title="aaStatus?.latest ? `基準：第 ${aaStatus.latest.id} 批（${aaStatus.latest.imported_at}，${aaStatus.latest.file_name}）` : '還沒匯入過'">
                {{ aaStatus?.latest ? aaStatus.latest.row_count : '—' }}
              </td>
              <td class="fmt">.xlsx／.csv／dump</td>
              <td><input type="file" accept=".xlsx,.xlsm,.csv,.webit3dump" :disabled="aaBusy" @click="resetFileInput" @change="aaPick" /></td>
              <td class="ops">
                <button class="btn small primary" type="button" :disabled="!aaFile || aaBusy" @click="aaImport">
                  {{ aaBusy ? '匯入中…' : '匯入' }}
                </button>
                <button class="btn small" type="button" @click="scrollToImport('account-audit')">明細</button>
                <button class="btn small" type="button" title="下載範例檔：欄位說明與 type 代碼表"
                        @click="aaDownload('/api/account-audit/template')">範例檔</button>
                <span class="ops-div" aria-hidden="true" />
                <button class="btn small" type="button"
                        :disabled="!exportSrc('account_audit')?.has_original || !!exportBusy"
                        :title="exportSrc('account_audit')?.has_original ? `下載當初匯入的原檔：${exportSrc('account_audit')?.original_name}` : '還沒有留原始檔（這個功能上線後的匯入才有）'"
                        @click="doExport(exportSrc('account_audit')!, 'original')">原始檔</button>
                <!-- 使用者 2026-09-18：「寫 EXCEL，點進去後再選要哪一種」 -->
                <span class="aa-menu-wrap">
                  <button class="btn small" type="button" @click="aaMenu = !aaMenu">Excel ▾</button>
                  <span v-if="aaMenu" class="aa-menu" @mouseleave="aaMenu = false">
                    <button type="button" @click="aaMenu = false; aaDownload('/api/account-audit/export?mode=standard')">
                      盤點報告（標準 A～R）<small>照你最後匯入的格式＋「與上次比較」分頁，可直接交出去</small>
                    </button>
                    <button type="button" @click="aaMenu = false; aaDownload('/api/account-audit/export?mode=all')">
                      全部欄位<small>A～R＋sudo、密碼、金鑰、最後登入等內部管理欄＋比較欄</small>
                    </button>
                  </span>
                </span>
                <button class="btn small" type="button" :disabled="!exportSrc('account_audit') || !!exportBusy"
                        title="整包搬到另一台：目前的比較基準（表頭＋每列）。在另一台的帳號盤點列選這個檔匯入即可還原。⚠️ 不加密"
                        @click="doExport(exportSrc('account_audit')!, 'dump')">dump</button>
              </td>
            </tr>
            <tr v-if="aaResult">
              <td colspan="5" class="seg-result">
                帳號盤點：匯入 <b>{{ aaResult.rows }}</b> 筆、<b>{{ aaResult.hosts }}</b> 台（第 {{ aaResult.batch_id }} 批，成為新的比較基準）
                <template v-if="aaResult.unknown_columns.length">・系統不認得、會照樣保留並沿用的欄：<code>{{ aaResult.unknown_columns.join('、') }}</code></template>
                <div v-if="aaResult.skipped_count" class="seg-bad">
                  ⚠ 略過 {{ aaResult.skipped_count }} 列：
                  <span v-for="s in aaResult.skipped.slice(0, 8)" :key="s.row">第 {{ s.row }} 列（{{ s.reason }}）　</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 機房搬遷盤點表（青埔）：跟其它匯入匯出放一起，方便找 -->
    <div class="card">
      <div class="card-title">機房搬遷盤點表（青埔）
        <InfoNote>依「青埔機房搬遷_CIA資產_Master」的 9 分頁格式。<b>匯出</b>＝把現有資料預填成同格式（可選機房）。<b>匯入</b>＝上傳現場填回的表，逐台逐欄跟系統值對帳、回一份校對版（綠＝一致／紅＝不一致／藍＝系統補），差異列進 CIA待異動分頁。<b>只比對、不改資料庫</b>。完整版面在「報告 → 機房搬遷盤點表」。</InfoNote>
      </div>
      <div class="rel-row">
        <label class="rel-lb">匯出機房
          <select v-model="relPicked" class="rel-in">
            <option value="">全部資產（含 VM）</option>
            <option v-for="l in relLocs" :key="l.loc" :value="l.loc">{{ l.loc }}（{{ l.n }}）</option>
          </select>
        </label>
        <a class="rel-btn primary" :href="relExportUrl">⬇ 匯出預填表</a>
        <span class="rel-sep">｜</span>
        <label class="rel-btn">
          <input type="file" accept=".xlsx" hidden :disabled="relImporting" @change="relImport">
          {{ relImporting ? '對帳中…' : '⬆ 匯入對帳（回校對版）' }}
        </label>
        <NuxtLink class="rel-link" to="/relocation">完整頁 →</NuxtLink>
      </div>
    </div>

    <!-- S19 VC 採集器：RVTools（vCenter 盤點）匯入。與 CIA Excel 是兩條獨立路徑 -->
    <div id="rvtools" class="card">
      <div class="card-title">vCenter 盤點（RVTools）明細與待審核 <InfoNote>檔案在上方「匯入」表選 RVTools 那列上傳（可一次多檔，每台 vCenter 一個）。系統會讀 <code>vInfo</code> 分頁把每台 VM 收進資產：對得到的更新機器資料（不動你填的用途／保管者），新的建成 <code>VC-</code> 資產，<b>同 IP 但不同機器</b>這種判不準的會擋下來等你確認，不會亂合併——待確認的就列在這張卡片下面。</InfoNote></div>
      <!-- 待審核批次處理：放在匯入卡片裡，因為它就是匯入的後續步驟。
           判不準的案子留著等人確認是對的（合併錯很難救），但不該讓人逐筆按一千次。 -->
      <div v-if="reviewInfo && reviewInfo.total_open" class="revbox">
        <div class="revhead">
          待人工審核 <b>{{ reviewInfo.total_open.toLocaleString() }}</b> 筆
          <InfoNote>匯入時判不準是否同一台的案子，系統不自動合併。</InfoNote>
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
        <!-- 第二條規則：證據比上面那條更強（vm_uuid 是 VMware 給的唯一識別，換 IP 換名
             都不變）。分開列、分開執行，因為兩者依據不同，人要能各自判斷要不要按。 -->
        <template v-if="reviewInfo.uuid_rule && reviewInfo.uuid_rule.matchable">
          <p class="revline">
            另有 <b class="hit">{{ reviewInfo.uuid_rule.matchable.toLocaleString() }}</b> 筆
            （＝<b>{{ reviewInfo.uuid_rule.distinct_assets.toLocaleString() }}</b> 台機器）是
            「{{ reviewInfo.uuid_rule.rule_label }}」。
            <button class="lnk" type="button" @click="uuidOpen = !uuidOpen">
              {{ uuidOpen ? '收合範例' : '先看幾筆範例' }}
            </button>
          </p>
          <div v-if="uuidOpen" class="tbl-wrap revtbl">
            <table>
              <thead><tr><th>vCenter 給的名稱</th><th>要併進的資產</th><th>資產目前 IP</th><th>靠什麼對上</th></tr></thead>
              <tbody>
                <tr v-for="(s, i) in reviewInfo.uuid_rule.samples" :key="i">
                  <td class="mono">{{ s.incoming_hostname || s.incoming_ip || '—' }}</td>
                  <td>{{ s.target_hostname }} <span class="muted">（{{ s.target_serial }}）</span></td>
                  <td class="mono">{{ s.target_ip }}</td>
                  <td class="muted">{{ s.matched_on }}</td>
                </tr>
              </tbody>
            </table>
            <p class="revnote">
              這條規則<b>只寫入 vm_uuid 一個欄位</b>——合併的用途就是把身分證號補上去，
              補完之後每次 vCenter 同步都能精準對上，不會再一直生新的待審核。
              資產那邊 uuid 已經有值而且跟 vCenter 不同（衝突）的，一律不碰、留給人看。
            </p>
          </div>
          <button class="btn" type="button" :disabled="reviewMerging" @click="doBatchMerge('uuid')">
            {{ reviewMerging ? '合併中…' : `確認補上這 ${reviewInfo.uuid_rule.distinct_assets.toLocaleString()} 台的 VM 識別碼` }}
          </button>
        </template>
        <p v-if="!reviewInfo.matchable && !reviewInfo.uuid_rule?.matchable" class="revline muted">
          目前沒有可安全批次配對的案子，這些需要逐筆判斷。
        </p>
        <p v-if="reviewMsg" class="revok">{{ reviewMsg }}</p>
      </div>
      <p v-if="rvError" class="error-text">{{ rvError }}</p>

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
            <!-- 這份檔案來自哪座 VC。多座 VC 的匯出以前混成一池分不出來，
                 現在每份都講清楚——順便一眼看得出兩份檔是不是同一座。 -->
            <div v-if="r.summary.vi_sdk_servers?.length" class="result-row muted">
              來源管理端：{{ r.summary.vi_sdk_servers.join('、') }}
              <span v-if="r.summary.vi_sdk_servers.length > 1">
                （一份匯出正常只會有一個，出現多個代表這份檔被合併過）
              </span>
            </div>
            <div v-else class="result-row muted">
              來源管理端：未記錄（這份匯出沒有 VI SDK Server 欄，換新版 RVTools 匯出就會有）
            </div>
            <!-- 來源 VC 變動：跨 vCenter 搬遷是正常的，所以是紀錄不是警告。
                 預設收起，要查再點開。 -->
            <div v-if="r.summary.vc_moved?.length" class="result-row muted">
              來源 VC 有變動 {{ r.summary.vc_moved.length }} 台（搬遷是正常的，列出來只是留個紀錄）
              <button type="button" class="link-btn" @click="movedOpen[i] = !movedOpen[i]">
                {{ movedOpen[i] ? '收起' : '看明細' }}
              </button>
              <ul v-if="movedOpen[i]" class="moved-list">
                <li v-for="(m, mi) in r.summary.vc_moved" :key="mi">
                  {{ m.name || m.asset_serial }}：{{ m.from }} → {{ m.to }}
                </li>
              </ul>
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

    <!-- 帳號盤點：匯入上次盤點／範例／匯出本次（2026-09-18） -->
    <AccountAuditImport :key="aaKey" />

    <!-- 業務系統對照表：api_id 是代碼，這張表給它中文名稱與 AP 窗口 -->
    <div id="bizsys" class="card">
      <div class="card-title">業務系統對照表 明細 <InfoNote>檔案在上方「匯入」表選「業務系統對照表」那列上傳（<b>.xlsx 或 .csv</b>，第一列是表頭）。系統會補齊 <code>api_id</code>（<code>N-008</code> 這種代碼）對應的<b>系統名稱／AP 部門／AP 負責人</b>。欄名不寫死，<code>system_id</code>、<code>AP_ID</code>、<code>系統代碼</code>、<code>項目名稱</code> 都認得；以系統代碼為鍵，<b>重匯是更新同一筆，不會長出重複</b>。<br><br>同一個代碼在來源檔對到<b>多組不同名字</b>時，<b>不會偷偷挑一個</b>——全部收進來、標「多來源·待確認」、保留所有候選，你之後再定案。</InfoNote></div>
      <p v-if="bsError" class="error-text">{{ bsError }}</p>
      <div v-if="bsResult" class="card result-card"
           :class="`result-${bsResult.coverage.assets_with_unmapped_api_id > 0 ? 'warn' : 'ok'}`">
        <div class="card-title">匯入結果</div>
        <div class="result-row">
          收進 {{ bsResult.imported }} 個系統
          <span v-if="bsResult.skipped_no_api_id"> · 略過 {{ bsResult.skipped_no_api_id }} 列（沒有系統代碼）</span>
        </div>
        <div v-if="bsResult.multi_source" class="result-row" style="color:var(--warn-text)">
          ⚠ 其中 <b>{{ bsResult.multi_source }}</b> 個代碼有多組不同名字，已標「多來源·待確認」並保留所有候選
          <InfoNote>同一個系統代碼在來源檔對到好幾個不同名字時，系統<b>不會偷偷挑一個</b>（挑錯看不出來）。先取第一個當顯示值、把全部候選存起來、標成待確認；你哪天要定案，在對照表把待確認那幾筆改成正確名字即可。</InfoNote>
        </div>
        <!-- 只講「匯了 N 筆」等於沒回答問題：人要知道的是還差多少台查不到名字，
             以及那是「對照表缺代碼」還是「機器沒填 api_id」——要補的地方不同 -->
        <div class="result-row muted">
          資產庫 {{ bsResult.coverage.assets_total }} 台：
          <b v-if="bsResult.coverage.assets_with_unmapped_api_id">
            {{ bsResult.coverage.assets_with_unmapped_api_id }} 台的代碼不在對照表裡</b>
          <span v-else>代碼都對得上</span>
          · {{ bsResult.coverage.assets_without_api_id }} 台本身沒填代碼（那要補資產資料，不是補對照表）
        </div>
        <div v-if="bsResult.coverage.unmapped_codes.length" class="result-row muted">
          對照表缺這些代碼：<code>{{ bsResult.coverage.unmapped_codes.join('、') }}</code>
        </div>
      </div>
    </div>

    <!-- dynassets 匯入：存活掃描＋CMDB 清單，跟 CIA 登記對帳。第三條獨立匯入路徑 -->
    <div id="dynassets" class="card">
      <div class="card-title">存活清單（dynassets）明細 <InfoNote>檔案在上方「匯入」表選「存活清單（dynassets）」那列上傳（.csv／.xlsx，含 IP／主機名／MAC／OS，是 Satellite＋nmap 存活探測匯出的存活主機清單）。系統會跟 CIA 登記對帳：<b>對得上</b>的更新機器事實（不動你填的用途／保管者），<b>掃到卻沒登記</b>的建成 <code>DYN-</code> 資產（＝漏登記／帳外資產），判不準的擋下等你確認、不亂合併。實體＋虛擬都涵蓋，補上 RVTools 補不到的實體機。<br><br>不確定欄位怎麼填就先下載下面的空白範本。</InfoNote></div>
      <!-- 空白範本留著：這是上傳前會用到的東西，欄位列在範本裡最清楚。 -->
      <div class="export-row">
        <button class="btn ghost" type="button" :disabled="dynTemplateDownloading" @click="downloadDynassetsTemplate">
          {{ dynTemplateDownloading ? '準備中…' : '⬇ 下載空白範本' }}
        </button>
      </div>
      <p v-if="dynError" class="error-text">{{ dynError }}</p>
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

    <div id="cia" class="card">
      <!-- ⚠️ 標題一定要把三個分頁寫出來。2026-08-26 使用者要匯入「人員通訊錄」，
           在這頁上下找不到——因為整頁沒有任何地方出現「人員」兩個字，而人員其實
           就是這份 Excel 的三個分頁之一。跟 8/20「聯絡人明明有卻連講三次沒看到」、
           8/25「CIA 匯出鈕在頁首所以看不到」同一族：**東西在，但使用者用他腦中的
           詞找不到它。** 標題就是搜尋介面，要用使用者會想到的字。 -->
      <div class="card-title">
        CIA 資產清單 明細與匯出
        <span class="sheets">三個分頁：硬體／<b>人員（通訊錄・分機・代理人）</b>／軟體。檔案在上方「匯入」表最後一列上傳</span>
      </div>

      <!-- 匯出目前資產清單：跟上傳按鈕放同一張卡片、緊鄰在一起（2026-08-25 使用者
           通則：有匯入就要有配對的匯出，且不能離上傳區太遠，不然等於沒有）。
           指向既有的 /api/export，不是複製一份新邏輯。 -->
      <div class="export-row">
        <button class="btn ghost" type="button" :disabled="exporting" @click="downloadExport">
          {{ exporting ? '匯出中…' : '⬇ 匯出目前資產清單（含人員分頁，可當範本）' }}
        </button>
        <label class="offbook-check">
          <input v-model="includeOffBook" type="checkbox">
          含帳外資產（DYN-/VC-/AUTO- 開頭，實際存在但沒登記在 CIA 上的）
          <InfoNote>回交給資產清單維護單位時：<b>不勾</b>＝只給你們登記過的那批（更新機器事實）；<b>勾選</b>＝連我們額外盤到、你們清單上沒有的機器也一起給，檔案裡會多一欄「來源」分得出哪些是新發現的。</InfoNote>
        </label>
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
.exp-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.exp-tbl th, .exp-tbl td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }
.exp-tbl th { font-size: 12px; color: var(--ink-soft); font-weight: 600; white-space: nowrap; }
.exp-tbl td.num, .exp-tbl th.num { text-align: right; font-variant-numeric: tabular-nums; }
.exp-tbl td.orig .mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
/* 動作欄要整整齊齊（使用者 2026-09-11）：每顆按鈕同寬、同一種按鈕上下對齊成一欄。
   td 保持 table-cell（改成 flex 會讓那一格的底線斷掉、列高對不齊），
   缺按鈕的列用 .ops-gap 佔位，後面的按鈕才不會往左擠。 */
.exp-tbl td.ops { white-space: nowrap; }
.exp-tbl td.ops > .btn, .exp-tbl td.ops > .ops-gap {
  display: inline-block; box-sizing: border-box; width: 76px; margin-right: 6px;
  text-align: center; vertical-align: middle; position: relative; text-decoration: none; }
/* 匯入群組與匯出群組之間的分隔線（同一欄，匯出接在匯入後面，2026-09-14 使用者） */
.exp-tbl td.ops > .ops-div {
  display: inline-block; width: 1px; height: 20px; margin: 0 12px 0 4px;
  vertical-align: middle; background: var(--border-strong); }
.exp-sz { font-size: 11px; color: var(--ink-soft); margin-left: 6px; }
.exp-tbl td.fmt, .exp-tbl .fmt { font-size: 12px; color: var(--ink-soft); }
/* 待審提示：貼在 RVTools 那列的「明細」按鈕上，讓待人工審核的佇列從上方表就看得到
   （複核佇列藏起來沒人看＝這個專案反覆踩過的錯）。 */
/* 角標浮在按鈕右上角，不撐寬按鈕——撐寬會讓後面的「範例檔」跑出那一欄 */
.rv-pending { position: absolute; top: -9px; right: -10px; padding: 1px 6px; border-radius: 8px;
  background: var(--warn-bg, #5a3d00); color: var(--warn-text, #ffcc66);
  font-size: 11px; font-variant-numeric: tabular-nums; }
.exp-tbl input[type=file] { font-size: 12px; max-width: 260px; }
/* 頂端目錄：三條匯入路徑在同一頁上下排開, CIA(含人員通訊錄)在最後,
   使用者捲不到就以為沒有(2026-08-26 實際發生)。 */
.whereis { margin: 10px 0 0; font-size: 12px; color: var(--ink-soft); }
.whereis a { color: var(--brand-dark); cursor: pointer; text-decoration: none; }
.whereis a:hover { text-decoration: underline; }
.card-title .sheets { display: block; font-weight: 400; font-size: 11.5px;
  color: var(--ink-soft); margin-top: 3px; }
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
  color: var(--warn-text);
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
  background: none; border: none; color: var(--warn-text); text-decoration: underline;
  cursor: pointer; font-family: inherit; padding: 0;
}
.rv-result-item {
  padding: 8px 0; border-bottom: 1px solid var(--border);
}
.rv-result-item:last-child { border-bottom: none; }
.rv-result-file {
  font-size: 12.5px; font-weight: 700; margin-bottom: 3px;
}
/* 跟 adopt.vue／[serial].vue 同一個外觀（各頁 scoped，樣式沒有共用檔） */
.link-btn {
  background: none; border: none; padding: 0 0 0 6px; cursor: pointer;
  color: var(--brand-dark); font-family: inherit; font-size: inherit;
  text-decoration: underline; text-underline-offset: 2px;
}
.link-btn:hover { opacity: .75; }

.moved-list {
  margin: 6px 0 0; padding-left: 18px;
  max-height: 260px; overflow-y: auto;   /* 一批可能幾十台，不要把頁面撐爆 */
}
.moved-list li { line-height: 1.7; }

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
.result-success { border-left-color: #009142; }
.result-warn    { border-left-color: #d99a2b; }
.result-error   { border-left-color: #d9534f; }

.result-verdict {
  font-weight: 700;
  font-size: 15px;
  margin-bottom: 4px;
}
.result-error .result-verdict { color: var(--bad); }
.result-warn .result-verdict  { color: var(--warn-text); }

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
  color: var(--warn-text);
}
.rv-hint {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.7;
  margin: 0 0 12px;
}
.rv-hint code { color: var(--brand-dark); }
.rv-hint b { color: var(--ink-soft); }
.rv-hint.sm { font-size: 11px; margin-top: -4px; }

/* 網段匯入結果：整批取代，消失的段要紅字講清楚 */
.seg-result { font-size: 12.5px; line-height: 1.7; background: var(--bg-soft, #f7f9f8); }
.seg-result code { font-size: 12px; }
.seg-bad { color: var(--bad); }
/* 匯出範本／匯出目前清單：緊鄰上傳按鈕的一列（2026-08-25 通則） */
.export-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }
.offbook-check { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-soft); cursor: pointer; }
.offbook-check input { cursor: pointer; }

/* ===== 待人工審核的批次處理 ===== */
.revbox { margin: 14px 0 4px; padding: 12px 14px; border-radius: 10px;
  border: 1px solid rgba(255,180,84,.32); background: rgba(255,180,84,.05); }
.revhead { font-size: 13px; color: var(--warn-text); display: flex; align-items: baseline;
  gap: 8px; flex-wrap: wrap; }
.revhead b { font-size: 15px; }
.revsub { font-size: 12px; opacity: .72; }
.revline { margin: 8px 0 10px; font-size: 13px; line-height: 1.7; }
.revline .hit { color: var(--brand-dark); font-size: 15px; }
.revline .lnk { background: none; border: none; color: var(--warn-text); text-decoration: underline;
  cursor: pointer; font-size: 12px; font-family: inherit; padding: 0; margin-left: 4px; }
.revtbl { max-height: 260px; overflow-y: auto; margin-bottom: 10px; }
.revtbl .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.revnote { margin: 8px 0 0; font-size: 12px; opacity: .72; line-height: 1.6; }
.revok { margin: 10px 0 0; font-size: 13px; color: var(--brand-dark); }

/* 機房搬遷盤點表卡片 */
.rel-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rel-lb { font-size: 12px; color: var(--muted); }
.rel-in { display: block; margin-top: 3px; padding: 7px 10px; border: 1px solid var(--border-strong);
  border-radius: 8px; background: #fff; color: var(--ink); min-width: 220px; }
.rel-btn { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); cursor: pointer; text-decoration: none; font-size: 13px; }
.rel-btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.rel-sep { color: var(--border-strong); }
.rel-link { color: var(--link); font-size: 13px; margin-left: 4px; }

.aa-menu-wrap { position: relative; display: inline-block; }
.aa-menu { position: absolute; z-index: 20; top: 100%; left: 0; margin-top: 4px; min-width: 300px;
  background: var(--surface, #fff); border: 1px solid var(--border-strong); border-radius: 8px;
  box-shadow: 0 6px 18px rgba(0,0,0,.12); padding: 4px; display: flex; flex-direction: column; }
.aa-menu button { text-align: left; padding: 8px 10px; border: 0; background: transparent; cursor: pointer; border-radius: 6px; font-size: 13px; }
.aa-menu button:hover { background: var(--brand-tint, #eef7f3); }
.aa-menu small { display: block; font-size: 11px; color: var(--ink-soft); margin-top: 2px; }
.mini { border-collapse: collapse; font-size: 12px; margin: 4px 0 10px; }
.mini th, .mini td { border: 1px solid var(--border); padding: 3px 7px; text-align: left; }
.mini td.sm { max-width: 420px; white-space: normal; font-size: 11.5px; }

/* AD 名單來源列。note 是警示（例如「這是測試資料」），不可以跟一般說明同色——
   假資料混在盤點系統裡而沒有標示，本身就是這套系統最不能犯的錯。 */
.ad-src { padding: 4px 8px 8px; font-size: 12px; }
.ad-note { display: inline-block; margin-right: 10px; padding: 2px 8px; border-radius: 3px;
  background: rgba(201, 138, 0, .15); border-left: 3px solid var(--warn, #c98a00);
  font-weight: 600; }
</style>
