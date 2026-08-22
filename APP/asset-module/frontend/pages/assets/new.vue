<script setup lang="ts">
// 新增資產：兩種手動入口——單筆表單（CIA 資產清單欄位）／網段存活掃描。
// 原本塞在「資料匯入」頁裡，但這是常用主功能，2026-08-15 使用者要求獨立成頁＋放主選單。
type FieldMapping = Record<string, Record<string, string>>

const { apiFetch } = useApi()
const { showToast } = useToast()

const originalMapping = ref<FieldMapping>({})
async function loadFieldMapping() {
  originalMapping.value = await apiFetch<FieldMapping>('/api/import/field-mapping')
}

// ===== 手動新增單筆資產（CIA 資產清單欄位為主）=====
// 欄位清單直接抓 originalMapping['硬體']（跟 Excel 匯入同一份設定），不另外寫死一份
// 欄位表——field_mapping.json 改了，這裡自動跟著變，符合 D14「欄位對應可調整」精神。
const manualFields = computed(() =>
  Object.entries(originalMapping.value['硬體'] ?? {}).map(([header, field]) => ({ header, field })),
)
const manualForm = ref<Record<string, string>>({})
const manualSubmitting = ref(false)
const manualError = ref('')

// 是否為 VM：勾選後隱藏純實體機才有意義的欄位（機櫃編號/硬體編號），VM 沒有機櫃位置、
// 沒有實體資產編號，硬要填只會逼使用者亂填或留空，不如直接不問。
// 切換到 VM 時同時清掉那兩欄已經打的值——欄位隱藏了但資料還留在表單裡會被悄悄送出，
// 使用者根本看不到卻夾帶了不該有的值。
const manualIsVm = ref(false)
const manualVmHiddenFields = ['rack_no', 'hardware_no']
watch(manualIsVm, (isVm) => {
  if (isVm) for (const f of manualVmHiddenFields) manualForm.value[f] = ''
})

// 選單值：哪些欄位該用選單、選項是什麼由後端依現有資料算（見 manual_field_options）。
// 種類數太多的欄位（型號/用途…）後端根本不會回，畫面上自然還是自由輸入，不用另外判斷。
const manualFieldOptions = ref<Record<string, string[]>>({})
// 使用者選「其他（自行輸入）」的欄位：改顯示文字輸入框，不然選單涵蓋不到全新的值
// （例如全新的部門/全新的人）就永遠填不進去。
const manualCustomFields = ref<Set<string>>(new Set())
function toggleManualCustom(field: string, useCustom: boolean) {
  if (useCustom) manualCustomFields.value.add(field)
  else manualCustomFields.value.delete(field)
  manualCustomFields.value = new Set(manualCustomFields.value)
  if (!useCustom) manualForm.value[field] = ''
}

// 作業系統：分層選單（大類→發行版/產品→版本），來源是 /api/os-catalog，選到的是算好的
// canonical 字串。但目錄不可能涵蓋「公司第一次出現的全新 OS」，留一條自行輸入的退路，
// 跟其他選單欄位邏輯一致，不會把使用者卡死選不出來。
type OsCatalog = Record<string, Record<string, string[]>>
const osCatalog = ref<OsCatalog>({})
const osFamilySel = ref('')
const osDistroSel = ref('')
const osVersionSel = ref('')
const osCustom = ref(false)
const osDistroOptions = computed(() => Object.keys(osCatalog.value[osFamilySel.value] ?? {}))
const osVersionOptions = computed(
  () => osCatalog.value[osFamilySel.value]?.[osDistroSel.value] ?? [],
)
watch(osFamilySel, () => { osDistroSel.value = ''; osVersionSel.value = '' })
watch(osDistroSel, () => { osVersionSel.value = '' })
watch(osVersionSel, (v) => { if (!osCustom.value) manualForm.value.os = v })
function toggleOsCustom(useCustom: boolean) {
  osCustom.value = useCustom
  osFamilySel.value = ''
  osDistroSel.value = ''
  osVersionSel.value = ''
  manualForm.value.os = ''
}

// 欄位分組：29 個欄位攤平成一片格子，填的人根本抓不到重點（2026-08-15 使用者反饋）。
// 分組定義在後端 manual_form_groups.json，不寫死在這裡——同一份分組之後資產詳細頁也要用，
// 寫死前端等於埋第二份會走鐘的真相。（別跟 field_groups.json 搞混，那是查詢頁的常用/進階分層）
interface FieldGroup { key: string; label: string; hint?: string; fields: string[] }
const fieldGroups = ref<FieldGroup[]>([])

// 勾 VM 時實體機專屬欄位整個不出現（不只是隱藏，值也在 watch 裡清掉了）
const manualVisibleFields = computed(() =>
  manualFields.value.filter((r) => !(manualIsVm.value && manualVmHiddenFields.includes(r.field))),
)

// 依分組把欄位排好；設定檔沒列到的欄位落到最後的「其他」組——寧可多一組沒排好的，
// 也不能讓欄位悄悄消失（field_mapping.json 之後加新欄位時就是靠這條接住）。
const manualGroupedFields = computed(() => {
  const byField = new Map(manualVisibleFields.value.map((r) => [r.field, r]))
  const used = new Set<string>()
  const out = fieldGroups.value
    .map((g) => {
      const rows = g.fields.flatMap((f) => {
        const row = byField.get(f)
        if (!row) return []
        used.add(f)
        return [row]
      })
      return { key: g.key, label: g.label, hint: g.hint ?? '', rows }
    })
    .filter((g) => g.rows.length > 0)

  const rest = manualVisibleFields.value.filter((r) => !used.has(r.field))
  if (rest.length) {
    out.push({
      key: '_other',
      // 分組設定檔讀不到時會走這裡，整份欄位還是排得出來，只是沒有分組
      label: fieldGroups.value.length ? '其他' : '資產欄位',
      hint: fieldGroups.value.length ? '尚未分組的欄位' : '',
      rows: rest,
    })
  }
  return out
})

// ===== IP：機房 → 環境 → 網段 三層挑（2026-08-15 使用者指定的順序）=====
// 填表的人記得住「這台在板橋、是正式機」，記不住 10.99.163 是哪一段。
// 選到網段後系統列出「這段已登記哪些 IP」並建議下一個沒被登記的——但只能說
// 「清單裡沒登記」，不能說「沒人在用」（清單本來就不完整，那正是資料品質頁在量的事）。
interface SegNode {
  location: string
  environments: { environment: string; segments: SegOption[] }[]
}
interface SegOption {
  cidr: string
  label: string
  category: string | null
  usage: string | null
  scan_excluded: boolean
  scan_note: string | null
}
const segTree = ref<SegNode[]>([])
const segLoc = ref('')
const segEnv = ref('')
const segCidr = ref('')
const segIpInfo = ref<{ used: any[]; suggestion: string | null; capacity: number } | null>(null)
const segIpLoading = ref(false)
// 網段表還沒匯入時整組不出現，IP 就維持原本的自由輸入——不要為了新功能把舊路擋掉
const hasSegments = computed(() => segTree.value.length > 0)

const segEnvOptions = computed(
  () => segTree.value.find((n) => n.location === segLoc.value)?.environments.map((e) => e.environment) ?? [],
)
const segCidrOptions = computed(() => {
  const envs = segTree.value.find((n) => n.location === segLoc.value)?.environments ?? []
  return envs.find((e) => e.environment === segEnv.value)?.segments ?? []
})
const selectedSeg = computed(() => segCidrOptions.value.find((s) => s.cidr === segCidr.value) ?? null)

watch(segLoc, () => { segEnv.value = ''; segCidr.value = ''; segIpInfo.value = null })
watch(segEnv, () => { segCidr.value = ''; segIpInfo.value = null })
watch(segCidr, async (cidr) => {
  segIpInfo.value = null
  if (!cidr) return
  segIpLoading.value = true
  try {
    segIpInfo.value = await apiFetch(`/api/segments/ips`, { query: { cidr } })
    // 直接把建議 IP 填進去，使用者要改再改——多數情況他就是要下一個可用的
    if (segIpInfo.value?.suggestion) manualForm.value.ip = segIpInfo.value.suggestion
  } finally {
    segIpLoading.value = false
  }
})

async function loadManualHelpers() {
  const [options, catalog, groups, tree] = await Promise.all([
    apiFetch<Record<string, string[]>>('/api/assets/manual/field-options'),
    apiFetch<OsCatalog>('/api/os-catalog'),
    apiFetch<{ groups: FieldGroup[] }>('/api/assets/manual/field-groups'),
    apiFetch<{ tree: SegNode[] }>('/api/segments/tree'),
  ])
  manualFieldOptions.value = options
  osCatalog.value = catalog
  fieldGroups.value = groups.groups
  segTree.value = tree.tree
}

// ===== 來源：依申請單轉錄／直接新增 =====
// 申請單位目前沒有系統帳號，只能填 Word 交給 IT 承辦人轉錄——所以這兩條路的欄位
// 完全一樣，只有單據資訊差別，合併成同一個入口。等哪天開放他們自己上系統填，
// 也只是「同一頁換一個人來填」，不用重做一套。
const provisionSource = ref<'form' | 'direct'>('direct')
const provisionForm = ref<Record<string, string>>({})
const provisionFields = [
  { key: 'request_no', label: '單據編號', placeholder: '例：ES800011504075', required: true },
  { key: 'applicant_unit', label: '申請單位', placeholder: '例：資料工程部' },
  { key: 'applicant', label: '申請人員' },
  { key: 'unit_manager', label: '單位主管' },
  { key: 'form_date', label: '填表日期', type: 'date' },
]
const changeKinds = ['緊急（委外義務時間）', '一般', '標準']
// 上傳要等資產建立完才有對象可以掛，所以先留著檔案、送出成功後才傳
const provisionFile = ref<File | null>(null)
function onProvisionFile(e: Event) {
  provisionFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
}

const createdAsset = ref<{ serial: string; total: number; done: number } | null>(null)

async function submitManualAsset() {
  const asset_serial = (manualForm.value.asset_serial ?? '').trim()
  if (!asset_serial) {
    manualError.value = '資產序號必填'
    return
  }
  if (provisionSource.value === 'form' && !(provisionForm.value.request_no ?? '').trim()) {
    manualError.value = '依申請單轉錄時，單據編號必填'
    return
  }
  manualSubmitting.value = true
  manualError.value = ''
  try {
    const fields: Record<string, string> = {}
    for (const [k, v] of Object.entries(manualForm.value)) {
      if (v !== '' && v != null) fields[k] = v
    }
    fields.is_vm = manualIsVm.value ? '1' : '0'
    const result = await apiFetch<{ hardware: any; golive: { total: number; done: number } }>(
      '/api/assets/manual',
      {
        method: 'POST',
        body: {
          fields,
          provision: { source: provisionSource.value, ...provisionForm.value },
        },
      },
    )
    const serial = result.hardware.asset_serial

    // 附件失敗不能把「資產已經建好」這件事一起吞掉——分開報，不然使用者會以為整筆沒進去
    if (provisionFile.value) {
      try {
        const fd = new FormData()
        fd.append('file', provisionFile.value)
        await apiFetch(`/api/assets/${serial}/provision-attachment`, { method: 'POST', body: fd })
      } catch {
        showToast('資產已建立，但申請單附件上傳失敗，請到資產詳細頁重傳', 'error')
      }
    }

    createdAsset.value = { serial, total: result.golive?.total ?? 0, done: result.golive?.done ?? 0 }
    showToast(`已新增資產 ${serial}（狀態：待上線）`, 'success')
    manualForm.value = {}
    manualCustomFields.value = new Set()
    manualIsVm.value = false
    osFamilySel.value = ''
    osDistroSel.value = ''
    osVersionSel.value = ''
    osCustom.value = false
    provisionForm.value = {}
    provisionFile.value = null
  } catch (err: any) {
    const d = err?.data?.detail
    manualError.value = (typeof d === 'string' ? d : d?.message) ?? '新增失敗，請稍後再試'
    showToast(manualError.value, 'error')
  } finally {
    manualSubmitting.value = false
  }
}

// ===== 網段存活掃描（第二種新增入口：整段掃、勾選要納入哪些）=====
// 只掃機器事實（IP/主機名/開放的 22、445 port），沒有業務欄位——納入後走
// /api/assets/scan/import（借道既有 dynassets 管道），業務欄位事後用資產詳細頁補。
interface ScanFoundHost {
  ip: string
  hostname: string | null
  open_ports: string[]
  already_registered: boolean
  existing_asset_serial: string | null
}
const scanCidr = ref('')
const scanning = ref(false)
const scanError = ref('')
const scanFound = ref<ScanFoundHost[] | null>(null)
const scanSelected = ref<Set<string>>(new Set())
const scanImporting = ref(false)

async function runScan() {
  if (!scanCidr.value.trim()) { scanError.value = '請輸入網段（例：192.168.1.0/24）'; return }
  scanning.value = true
  scanError.value = ''
  scanFound.value = null
  scanSelected.value = new Set()
  try {
    const r = await apiFetch<{ cidr: string; found: ScanFoundHost[] }>('/api/assets/scan/discover', {
      method: 'POST', body: { cidr: scanCidr.value.trim() },
    })
    scanFound.value = r.found
    // 預設勾選「還沒登記」的，已登記的不勾——使用者通常是想找漏登記的機器
    scanSelected.value = new Set(r.found.filter((h) => !h.already_registered).map((h) => h.ip))
    if (r.found.length === 0) showToast('這個網段沒掃到存活主機（開 22 或 445 port 的）', 'info')
  } catch (err: any) {
    const d = err?.data?.detail
    scanError.value = (typeof d === 'string' ? d : d?.message) ?? '掃描失敗，請稍後再試'
    showToast(scanError.value, 'error')
  } finally {
    scanning.value = false
  }
}

function toggleScanHost(ip: string, checked: boolean) {
  if (checked) scanSelected.value.add(ip)
  else scanSelected.value.delete(ip)
  scanSelected.value = new Set(scanSelected.value)
}

async function importSelectedScan() {
  if (!scanFound.value || scanSelected.value.size === 0) return
  scanImporting.value = true
  try {
    const hosts = scanFound.value
      .filter((h) => scanSelected.value.has(h.ip))
      .map((h) => ({ ip: h.ip, hostname: h.hostname }))
    const summary = await apiFetch<{ inserted: number; updated: number; errors: string[] }>(
      '/api/assets/scan/import', { method: 'POST', body: { hosts } },
    )
    showToast(`已納入 ${summary.inserted + summary.updated} 台（新增 ${summary.inserted}／更新 ${summary.updated}）`, 'success')
    scanFound.value = null
    scanCidr.value = ''
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '納入失敗，請稍後再試', 'error')
  } finally {
    scanImporting.value = false
  }
}

// SSR 階段拿不到登入 cookie，打 API 會 401，一律 onMounted 載（既有專案慣例，見 import.vue）。
onMounted(() => {
  loadFieldMapping()
  loadManualHelpers()
})
</script>

<template>
  <div>
    <div class="section-divider">新增資產</div>
    <div class="breadcrumb-bar">
      <span class="pin">📌</span> <b>新增資產</b>
    </div>

    <!-- 手動新增單筆資產：CIA 資產清單欄位為主，欄位清單跟 Excel 匯入共用同一份對應設定 -->
    <div class="card">
      <div class="card-title">手動新增資產</div>
      <p class="rv-hint">
        只有一台要單獨補登記時用這裡，不用重跑整份 Excel 匯入。欄位跟 CIA 資產清單一致；
        <b>資產序號</b>必填且不能跟既有資產重複。送出後資產是<b>「待上線」</b>，
        要過完上線前檢查表才會變成「使用中」。
      </p>

      <!-- 來源切換：兩條路欄位一樣，差別只有單據資訊 -->
      <div class="src-switch">
        <label :class="{ on: provisionSource === 'direct' }">
          <input v-model="provisionSource" type="radio" value="direct" />
          直接新增<span class="sub">IT 自己要上機器，沒有申請單</span>
        </label>
        <label :class="{ on: provisionSource === 'form' }">
          <input v-model="provisionSource" type="radio" value="form" />
          依申請單轉錄<span class="sub">申請單位填的「主機及網路異動需求單」</span>
        </label>
      </div>

      <div v-if="provisionSource === 'form'" class="fgroup prov-block">
        <div class="fgroup-hd">
          <span class="fgroup-label">申請單資訊</span>
          <span class="fgroup-hint">這張單的來歷，之後稽核要查「當初是誰申請、誰簽的」</span>
        </div>
        <div class="manual-grid">
          <label v-for="f in provisionFields" :key="f.key" class="manual-field">
            <span class="manual-label">
              {{ f.label }} <b v-if="f.required" class="req">*</b>
            </span>
            <input
              v-model="provisionForm[f.key]"
              :type="f.type ?? 'text'"
              :placeholder="f.placeholder ?? ''"
            />
          </label>
          <div class="manual-field">
            <span class="manual-label">變更類別</span>
            <SearchableSelect
              :model-value="provisionForm.change_kind ?? ''"
              :options="changeKinds"
              placeholder="請選擇"
              @update:model-value="(v: string) => (provisionForm.change_kind = v)"
            />
          </div>
          <label class="manual-field manual-field-wide">
            <span class="manual-label">
              申請單掃描檔（選填）
              <span class="muted-inline">存原始檔備查，系統不解析內容</span>
            </span>
            <input type="file" accept=".doc,.docx,.pdf,.png,.jpg,.jpeg" @change="onProvisionFile" />
          </label>
        </div>
      </div>

      <!-- 建立成功後把下一步指出來，不然使用者不知道還有上線檢查這關 -->
      <div v-if="createdAsset" class="created-next">
        已建立 <b>{{ createdAsset.serial }}</b>，狀態「待上線」。
        上線前檢查表已開好（{{ createdAsset.done }}／{{ createdAsset.total }} 項已完成，
        機器測得到的已自動判定）。
        <NuxtLink :to="`/golive/${createdAsset.serial}`" class="lnk-strong">去填上線檢查表 →</NuxtLink>
      </div>
      <label class="manual-vm-toggle">
        <input v-model="manualIsVm" type="checkbox" />
        是否為 VM（勾選後會隱藏機櫃編號／硬體編號——這兩個只有實體機才有意義）
      </label>
      <div v-for="g in manualGroupedFields" :key="g.key" class="fgroup">
        <div class="fgroup-hd">
          <span class="fgroup-label">{{ g.label }}</span>
          <span v-if="g.hint" class="fgroup-hint">{{ g.hint }}</span>
        </div>
        <div class="manual-grid">
          <template v-for="row in g.rows" :key="row.field">
            <!-- 作業系統：分層選單（大類→發行版→版本），留自行輸入退路給目錄沒有的全新 OS -->
            <div v-if="row.field === 'os'" class="manual-field manual-field-os">
              <span class="manual-label">
                {{ row.header }}
                <button type="button" class="lnk" @click="toggleOsCustom(!osCustom)">
                  {{ osCustom ? '改用選單' : '目錄沒有？自行輸入' }}
                </button>
              </span>
              <input v-if="osCustom" v-model="manualForm.os" type="text" placeholder="完整 OS 版本字串" />
              <div v-else class="os-cascade">
                <SearchableSelect
                  v-model="osFamilySel"
                  :options="Object.keys(osCatalog)"
                  placeholder="大類"
                />
                <SearchableSelect
                  v-model="osDistroSel"
                  :options="osDistroOptions"
                  :disabled="!osFamilySel"
                  placeholder="發行版／產品"
                />
                <SearchableSelect
                  v-model="osVersionSel"
                  :options="osVersionOptions"
                  :disabled="!osDistroSel"
                  placeholder="版本"
                />
              </div>
            </div>

            <!-- IP：先挑機房→環境→網段，再從那段挑一個沒被登記的位址 -->
            <div v-else-if="row.field === 'ip' && hasSegments" class="manual-field manual-field-wide">
              <span class="manual-label">
                {{ row.header }}
                <span class="muted-inline">先選機房與環境，系統只列該段可用的位址</span>
              </span>
              <div class="ip-cascade">
                <SearchableSelect
                  v-model="segLoc" :options="segTree.map((n) => n.location)" placeholder="機房"
                />
                <SearchableSelect
                  v-model="segEnv" :options="segEnvOptions" :disabled="!segLoc" placeholder="環境"
                />
                <SearchableSelect
                  v-model="segCidr"
                  :options="segCidrOptions.map((s) => s.cidr)"
                  :disabled="!segEnv"
                  placeholder="網段"
                />
                <input v-model="manualForm.ip" type="text" placeholder="IP" class="ip-input" />
              </div>
              <div v-if="selectedSeg" class="seg-info">
                <b>{{ selectedSeg.label }}</b>
                <span v-if="selectedSeg.usage"> · {{ selectedSeg.category }}／{{ selectedSeg.usage }}</span>
                <span v-if="selectedSeg.scan_excluded" class="warn-inline">
                  · 這段被註記「建議排除掃描」
                </span>
                <div v-if="selectedSeg.scan_note" class="note">{{ selectedSeg.scan_note }}</div>
                <div v-if="segIpLoading" class="note">查詢已登記的 IP…</div>
                <div v-else-if="segIpInfo" class="note">
                  這段已登記 {{ segIpInfo.used.length }}／{{ segIpInfo.capacity }} 個位址；
                  已帶入建議值 <b class="mono">{{ segIpInfo.suggestion ?? '（沒有空位）' }}</b>——
                  這只代表<b>清單裡沒登記</b>，不代表實際上沒人在用，配之前請先 ping 一下。
                </div>
              </div>
            </div>

            <!-- 資產序號：必填且唯一，是這張表唯一擋得住重複登記的欄位 -->
            <label v-else-if="row.field === 'asset_serial'" class="manual-field">
              <span class="manual-label">{{ row.header }} <b class="req">*</b></span>
              <input v-model="manualForm.asset_serial" type="text" placeholder="必填，需唯一" />
            </label>

            <!-- 有現成選項的欄位（處別/部門/環境別/保管者…）：可搜尋的選單，避免打錯字。
                 選項多的時候（部門 20+、人名更多）純滾清單找很久，SearchableSelect 打字就到。 -->
            <div
              v-else-if="manualFieldOptions[row.field] && !manualCustomFields.has(row.field)"
              class="manual-field"
            >
              <span class="manual-label">
                {{ row.header }}
                <button type="button" class="lnk" @click="toggleManualCustom(row.field, true)">自行輸入</button>
              </span>
              <SearchableSelect
                :model-value="manualForm[row.field] ?? ''"
                :options="manualFieldOptions[row.field]"
                placeholder="請選擇"
                @update:model-value="(v: string) => (manualForm[row.field] = v)"
              />
            </div>

            <!-- 種類數太多／沒有現成選項的欄位：維持自由輸入。
                 附加說明是整段文字，擠在一格裡看不到自己打了什麼，給它整列寬。 -->
            <label v-else class="manual-field" :class="{ 'manual-field-wide': row.field === 'remark' }">
              <span class="manual-label">
                {{ row.header }}
                <button
                  v-if="manualFieldOptions[row.field]"
                  type="button"
                  class="lnk"
                  @click="toggleManualCustom(row.field, false)"
                >改用選單</button>
              </span>
              <input v-model="manualForm[row.field]" type="text" />
            </label>
          </template>
        </div>
      </div>
      <p v-if="manualError" class="error-text">{{ manualError }}</p>
      <div class="actions">
        <button class="btn" type="button" :disabled="manualSubmitting" @click="submitManualAsset">
          {{ manualSubmitting ? '新增中…' : '新增這筆資產' }}
        </button>
      </div>
    </div>

    <!-- 網段存活掃描：整段掃、勾選要納入哪些。只收機器事實，業務欄位事後補 -->
    <div class="card">
      <div class="card-title">網段存活掃描</div>
      <p class="rv-hint">
        輸入網段（例：<code>192.168.1.0/24</code>，只能是內網私有網段），掃這段裡有哪些主機開了
        <b>22（SSH）</b>或 <b>445（SMB）</b>——用這兩個 port 判斷是不是「連得到、可管理」的存活主機。
        掃完只有 IP／主機名，沒有業務欄位，納入後可以到資產詳細頁補。
      </p>
      <div class="actions">
        <input
          v-model="scanCidr"
          type="text"
          placeholder="192.168.1.0/24"
          class="cidr-input"
          @keyup.enter="runScan"
        />
        <button class="btn" type="button" :disabled="scanning" @click="runScan">
          {{ scanning ? '掃描中…' : '開始掃描' }}
        </button>
      </div>
      <p v-if="scanError" class="error-text">{{ scanError }}</p>

      <div v-if="scanFound" class="tbl-wrap" style="margin-top: 14px">
        <table v-if="scanFound.length > 0">
          <thead>
            <tr>
              <th></th>
              <th>IP</th>
              <th>主機名（反解）</th>
              <th>開放 Port</th>
              <th>狀態</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in scanFound" :key="h.ip">
              <td>
                <input
                  type="checkbox"
                  :checked="scanSelected.has(h.ip)"
                  @change="toggleScanHost(h.ip, ($event.target as HTMLInputElement).checked)"
                />
              </td>
              <td class="mono">{{ h.ip }}</td>
              <td>{{ h.hostname ?? '—' }}</td>
              <td class="mono">{{ h.open_ports.join('、') }}</td>
              <td>
                <span v-if="h.already_registered" class="badge-registered">
                  已登記（{{ h.existing_asset_serial }}）
                </span>
                <span v-else class="badge-new">未登記</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="last-import-info muted" style="padding: 12px">這個網段沒掃到存活主機</p>
      </div>
      <div v-if="scanFound && scanFound.length > 0" class="actions" style="margin-top: 12px">
        <button
          class="btn"
          type="button"
          :disabled="scanImporting || scanSelected.size === 0"
          @click="importSelectedScan"
        >
          {{ scanImporting ? '納入中…' : `納入勾選的 ${scanSelected.size} 台` }}
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
.rv-hint {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.7;
  margin: 0 0 12px;
}
.rv-hint code { color: var(--brand-dark); }
.rv-hint b { color: var(--ink-soft); }
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
.btn:hover:not(:disabled) { background: var(--brand-dark); }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.actions {
  display: flex;
  gap: 10px;
}
.error-text {
  color: var(--bad);
  font-size: 13px;
  margin-bottom: 14px;
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
tr:last-child td { border-bottom: none; }
.last-import-info.muted { color: var(--muted); }

/* ===== 手動新增資產 ===== */
.manual-vm-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--ink-soft);
  margin-bottom: 14px;
  cursor: pointer;
}
.manual-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px 14px;
  margin-bottom: 14px;
}
/* 分組區塊：29 個欄位攤平一片時使用者抓不到重點，用標題把它切成 5 段 */
.fgroup {
  margin-bottom: 18px;
}
.fgroup-hd {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 6px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.fgroup-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--brand-dark);
  letter-spacing: 0.02em;
}
.fgroup-hint {
  font-size: 11px;
  color: var(--muted);
}
.manual-field-wide {
  grid-column: 1 / -1;
}
/* 來源切換：兩個大選項並排，選中的用品牌色框起來 */
.src-switch {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.src-switch label {
  flex: 1;
  min-width: 240px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  font-size: 12.5px;
  color: var(--ink-soft);
  cursor: pointer;
}
.src-switch label.on {
  border-color: var(--brand);
  color: var(--ink);
}
.src-switch .sub {
  font-size: 11px;
  color: var(--muted);
  margin-left: auto;
}
.prov-block {
  border-left: 2px solid var(--brand);
  padding-left: 14px;
}
.muted-inline {
  color: var(--muted);
  font-size: 11px;
  margin-left: 6px;
}
.created-next {
  border: 1px solid var(--brand);
  background: rgba(0,145,66,0.08);
  padding: 10px 14px;
  font-size: 12.5px;
  color: var(--ink-soft);
  margin-bottom: 14px;
  line-height: 1.7;
}
.lnk-strong {
  color: var(--brand);
  font-weight: 700;
  text-decoration: none;
  margin-left: 6px;
}
.lnk-strong:hover { text-decoration: underline; }
/* IP 三層選單：機房→環境→網段→位址，四格並排 */
.ip-cascade {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.ip-cascade > * {
  flex: 1;
  min-width: 130px;
}
.ip-input {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  padding: 6px 10px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
}
.seg-info {
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--muted);
  line-height: 1.7;
}
.seg-info b { color: var(--ink-soft); }
.seg-info .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.warn-inline { color: var(--warn, #d8a13a); }
.manual-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.manual-label {
  font-size: 11.5px;
  color: var(--ink-soft);
}
.manual-label .req {
  color: var(--bad);
}
.manual-field input,
.manual-field select {
  font-family: inherit;
  font-size: 12.5px;
  padding: 6px 10px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
}
.manual-field .lnk {
  background: none;
  border: none;
  color: var(--brand-dark);
  text-decoration: underline;
  cursor: pointer;
  font-size: 11px;
  font-family: inherit;
  padding: 0;
  margin-left: 6px;
}
.manual-field-os {
  grid-column: span 2;
}
.os-cascade {
  display: flex;
  gap: 8px;
}
.os-cascade select {
  flex: 1;
  min-width: 0;
}
.os-cascade select:disabled {
  opacity: 0.5;
}

/* ===== 網段存活掃描 ===== */
.cidr-input {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  padding: 8px 12px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
  min-width: 220px;
}
.badge-registered {
  font-size: 11.5px;
  color: var(--muted);
}
.badge-new {
  font-size: 11.5px;
  font-weight: 700;
  color: var(--brand-dark);
}
</style>
