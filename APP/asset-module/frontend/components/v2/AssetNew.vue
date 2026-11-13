<script setup lang="ts">
// 2.1 主機查詢「新增主機」（V21，2.048.25 起 V2 原生；取代嵌入的舊頁 pages/assets/new.vue）。同一批 API，行為不變：
// 單筆表單：GET /api/import/field-mapping（欄位＝Excel 匯入同一份「硬體」對應）、/api/assets/manual/field-options、/api/assets/manual/field-groups、
//   /api/os-catalog、/api/segments/tree、/api/segments/ips?cidr；POST /api/assets/manual（ADMIN_WRITE）＋ POST /api/assets/{serial}/provision-attachment（USER_WRITE，管理員也過）。
// 網段存活掃描：POST /api/assets/scan/discover（只掃 22／445 是否開；ADMIN_WRITE）→ 勾選 → POST /api/assets/scan/import（ADMIN_WRITE）。
// 三個寫入都是 ADMIN_WRITE → 整塊只給管理員（舊頁對所有人顯示、非管理員按了會被擋）。
// 照舊：資產序號必填；依申請單轉錄時單據編號必填；VM 隱藏機櫃編號／硬體編號並清值；附件失敗分開報（資產已建立）；掃描預設勾「未登記」。
type FieldMapping = Record<string, Record<string, string>>
type OsCatalog = Record<string, Record<string, string[]>>
interface FieldGroup { key: string; label: string; hint?: string; fields: string[] }
interface SegOption { cidr: string; label: string; category: string | null; usage: string | null; scan_excluded: boolean; scan_note: string | null }
interface SegNode { location: string; environments: { environment: string; segments: SegOption[] }[] }
interface ScanFoundHost { ip: string; hostname: string | null; open_ports: string[]; already_registered: boolean; existing_asset_serial: string | null }

const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const errMsg = (e: any, fb: string) => { const d = e?.data?.detail; return (typeof d === 'string' ? d : d?.message) ?? fb }

const originalMapping = ref<FieldMapping>({})
const manualFieldOptions = ref<Record<string, string[]>>({})
const osCatalog = ref<OsCatalog>({})
const fieldGroups = ref<FieldGroup[]>([])
const segTree = ref<SegNode[]>([])
const loadErr = ref('')
const helperErr = ref('')
// 欄位清單（field-mapping）跟輔助選單分開載（同舊頁）：輔助的任一支失敗，欄位照樣出來、只是少了選單／分組／網段
async function loadAll() {
  const mappingP = apiFetch<FieldMapping>('/api/import/field-mapping')
  const helpersP = Promise.allSettled([
    apiFetch<Record<string, string[]>>('/api/assets/manual/field-options'), apiFetch<OsCatalog>('/api/os-catalog'),
    apiFetch<{ groups: FieldGroup[] }>('/api/assets/manual/field-groups'), apiFetch<{ tree: SegNode[] }>('/api/segments/tree')])
  try { originalMapping.value = await mappingP; loadErr.value = '' } catch (e: any) { loadErr.value = `讀不到欄位設定：${errMsg(e, '未知錯誤')}` }      // 不靜默：欄位空白會被當成「沒有欄位」
  const [options, catalog, groups, tree] = await helpersP
  if (options.status === 'fulfilled') manualFieldOptions.value = options.value ?? {}
  if (catalog.status === 'fulfilled') osCatalog.value = catalog.value ?? {}
  if (groups.status === 'fulfilled') fieldGroups.value = groups.value?.groups ?? []
  if (tree.status === 'fulfilled') segTree.value = tree.value?.tree ?? []
  const bad = [[options, '欄位選單'], [catalog, '作業系統目錄'], [groups, '欄位分組'], [tree, '網段表']].filter(([r]) => (r as PromiseSettledResult<any>).status === 'rejected').map(([, n]) => n)
  helperErr.value = bad.length ? `部分選單讀不到（${bad.join('、')}），這些欄位改成自行輸入` : ''
}
// 登入身分可能晚一步才確定：變成管理員那一刻才載（只載一次）
let loaded = false
onMounted(() => watch(isAdmin, (a) => { if (a && !loaded) { loaded = true; loadAll() } }, { immediate: true }))

// ===== 手動新增單筆 =====
// 欄位＝field_mapping 的「硬體」（跟 Excel 匯入同一份設定），不另寫死一份
const manualFields = computed(() => Object.entries(originalMapping.value['硬體'] ?? {}).map(([header, field]) => ({ header, field })))
const manualForm = ref<Record<string, string>>({})
const manualSubmitting = ref(false)
const manualError = ref('')
// VM：隱藏實體機才有的欄位並清掉已打的值（隱藏了還送出去等於夾帶看不到的值）
const manualIsVm = ref(false)
const VM_HIDDEN = ['rack_no', 'hardware_no']
watch(manualIsVm, (isVm) => { if (isVm) for (const f of VM_HIDDEN) manualForm.value[f] = '' })
const manualCustomFields = ref<Set<string>>(new Set())
function toggleManualCustom(field: string, useCustom: boolean) {
  const s = new Set(manualCustomFields.value)
  if (useCustom) s.add(field); else s.delete(field)
  manualCustomFields.value = s
  if (!useCustom) manualForm.value[field] = ''
}
// 作業系統：大類→發行版→版本（/api/os-catalog），留自行輸入的退路
const osFamilySel = ref('')
const osDistroSel = ref('')
const osVersionSel = ref('')
const osCustom = ref(false)
const osDistroOptions = computed(() => Object.keys(osCatalog.value[osFamilySel.value] ?? {}))
const osVersionOptions = computed(() => osCatalog.value[osFamilySel.value]?.[osDistroSel.value] ?? [])
watch(osFamilySel, () => { osDistroSel.value = ''; osVersionSel.value = '' })
watch(osDistroSel, () => { osVersionSel.value = '' })
watch(osVersionSel, (v) => { if (!osCustom.value) manualForm.value.os = v })
function toggleOsCustom(useCustom: boolean) {
  osCustom.value = useCustom; osFamilySel.value = ''; osDistroSel.value = ''; osVersionSel.value = ''; manualForm.value.os = ''
}
// 分組（後端 manual_form_groups.json）；設定檔沒列到的欄位落到最後「其他」，不讓欄位消失
const manualVisibleFields = computed(() => manualFields.value.filter(r => !(manualIsVm.value && VM_HIDDEN.includes(r.field))))
const manualGroupedFields = computed(() => {
  const byField = new Map(manualVisibleFields.value.map(r => [r.field, r]))
  const used = new Set<string>()
  const out = fieldGroups.value.map(g => ({ key: g.key, label: g.label, hint: g.hint ?? '',
    rows: g.fields.flatMap(f => { const row = byField.get(f); if (!row) return []; used.add(f); return [row] }) })).filter(g => g.rows.length > 0)
  const rest = manualVisibleFields.value.filter(r => !used.has(r.field))
  if (rest.length) out.push({ key: '_other', label: fieldGroups.value.length ? '其他' : '資產欄位', hint: fieldGroups.value.length ? '尚未分組的欄位' : '', rows: rest })
  return out
})
// IP：機房 → 環境 → 網段；選到網段列出「這段已登記幾個」並帶入建議值（只代表清單裡沒登記）
const segLoc = ref('')
const segEnv = ref('')
const segCidr = ref('')
const segIpInfo = ref<{ used: any[]; suggestion: string | null; capacity: number } | null>(null)
const segIpLoading = ref(false)
const segIpErr = ref('')
const hasSegments = computed(() => segTree.value.length > 0)      // 網段表還沒匯入時 IP 維持自由輸入
const segEnvOptions = computed(() => segTree.value.find(n => n.location === segLoc.value)?.environments.map(e => e.environment) ?? [])
const segCidrOptions = computed(() => (segTree.value.find(n => n.location === segLoc.value)?.environments ?? []).find(e => e.environment === segEnv.value)?.segments ?? [])
const selectedSeg = computed(() => segCidrOptions.value.find(s => s.cidr === segCidr.value) ?? null)
watch(segLoc, () => { segEnv.value = ''; segCidr.value = ''; segIpInfo.value = null })
watch(segEnv, () => { segCidr.value = ''; segIpInfo.value = null })
const beginSeg = useLatest()
watch(segCidr, async (cidr) => {
  const isLatest = beginSeg()
  segIpInfo.value = null; segIpErr.value = ''
  if (!cidr) { segIpLoading.value = false; return }
  segIpLoading.value = true
  try {
    const info = await apiFetch<any>('/api/segments/ips', { query: { cidr } })
    if (!isLatest()) return
    segIpInfo.value = info
    if (info?.suggestion) manualForm.value.ip = info.suggestion      // 多數情況就是要下一個可用的，要改再改
  } catch (e: any) { if (isLatest()) segIpErr.value = `查不到這段已登記的 IP：${errMsg(e, '未知錯誤')}` } finally { if (isLatest()) segIpLoading.value = false }
})

// 來源：直接新增／依申請單轉錄（欄位一樣，只差單據資訊）
const provisionSource = ref<'form' | 'direct'>('direct')
const provisionForm = ref<Record<string, string>>({})
const provisionFields = [
  { key: 'request_no', label: '單據編號', placeholder: '例：ES800011504075', required: true }, { key: 'applicant_unit', label: '申請單位', placeholder: '例：資料工程部' },
  { key: 'applicant', label: '申請人員' }, { key: 'unit_manager', label: '單位主管' }, { key: 'form_date', label: '填表日期', type: 'date' }]
const changeKinds = ['緊急（委外義務時間）', '一般', '標準']
const provisionFile = ref<File | null>(null)        // 資產建立完才有對象可掛，送出成功後才傳
function onProvisionFile(e: Event) { provisionFile.value = (e.target as HTMLInputElement).files?.[0] ?? null }
const createdAsset = ref<{ serial: string; total: number; done: number } | null>(null)

async function submitManualAsset() {
  const asset_serial = (manualForm.value.asset_serial ?? '').trim()
  if (!asset_serial) { manualError.value = '資產序號必填'; return }
  if (provisionSource.value === 'form' && !(provisionForm.value.request_no ?? '').trim()) { manualError.value = '依申請單轉錄時，單據編號必填'; return }
  manualSubmitting.value = true; manualError.value = ''
  try {
    const fields: Record<string, string> = {}
    for (const [k, v] of Object.entries(manualForm.value)) if (v !== '' && v != null) fields[k] = v
    fields.is_vm = manualIsVm.value ? '1' : '0'
    const result = await apiFetch<{ hardware: any; golive: { total: number; done: number } }>('/api/assets/manual', {
      method: 'POST', body: { fields, provision: { source: provisionSource.value, ...provisionForm.value } } })
    const serial = result.hardware.asset_serial
    // 附件失敗不能把「資產已經建好」一起吞掉——分開報
    if (provisionFile.value) {
      try {
        const fd = new FormData(); fd.append('file', provisionFile.value)
        await apiFetch(`/api/assets/${serial}/provision-attachment`, { method: 'POST', body: fd })
      } catch { showToast('資產已建立，但申請單附件上傳失敗，請到資產詳細頁重傳', 'error') }
    }
    createdAsset.value = { serial, total: result.golive?.total ?? 0, done: result.golive?.done ?? 0 }
    showToast(`已新增資產 ${serial}（狀態：待上線）`, 'success')
    manualForm.value = {}; manualCustomFields.value = new Set(); manualIsVm.value = false
    osFamilySel.value = ''; osDistroSel.value = ''; osVersionSel.value = ''; osCustom.value = false
    provisionForm.value = {}; provisionFile.value = null; fileKey.value++
  } catch (e: any) {
    manualError.value = errMsg(e, '新增失敗，請稍後再試')
    showToast(manualError.value, 'error')
  } finally { manualSubmitting.value = false }
}
const fileKey = ref(0)      // 送出成功後清掉選檔框（值已清，畫面也要清）

// ===== 網段存活掃描 =====
const scanCidr = ref('')
const scanning = ref(false)
const scanError = ref('')
const scanFound = ref<ScanFoundHost[] | null>(null)
const scanSelected = ref<Record<string, boolean>>({})
const scanImporting = ref(false)
const pickedCount = computed(() => Object.values(scanSelected.value).filter(Boolean).length)
async function runScan() {
  if (!scanCidr.value.trim()) { scanError.value = '請輸入網段（例：10.99.1.0/24）'; return }
  scanning.value = true; scanError.value = ''; scanFound.value = null; scanSelected.value = {}
  try {
    const r = await apiFetch<{ cidr: string; found: ScanFoundHost[] }>('/api/assets/scan/discover', { method: 'POST', body: { cidr: scanCidr.value.trim() } })
    scanFound.value = r.found
    // 預設勾「還沒登記」的，已登記的不勾——通常是想找漏登記的機器
    scanSelected.value = Object.fromEntries(r.found.filter(h => !h.already_registered).map(h => [h.ip, true]))
    if (r.found.length === 0) showToast('這個網段沒掃到存活主機（開 22 或 445 port 的）', 'info')
  } catch (e: any) {
    scanError.value = errMsg(e, '掃描失敗，請稍後再試')
    showToast(scanError.value, 'error')
  } finally { scanning.value = false }
}
async function importSelectedScan() {
  if (!scanFound.value || pickedCount.value === 0) return
  scanImporting.value = true
  try {
    const hosts = scanFound.value.filter(h => scanSelected.value[h.ip]).map(h => ({ ip: h.ip, hostname: h.hostname }))
    const summary = await apiFetch<{ inserted: number; updated: number; errors: string[] }>('/api/assets/scan/import', { method: 'POST', body: { hosts } })
    showToast(`已納入 ${summary.inserted + summary.updated} 台（新增 ${summary.inserted}／更新 ${summary.updated}）`, 'success')
    scanFound.value = null; scanCidr.value = ''; scanSelected.value = {}
  } catch (e: any) { showToast(errMsg(e, '納入失敗，請稍後再試'), 'error') } finally { scanImporting.value = false }
}
const ipNum = (ip: string) => ip.split('.').reduce((n, p) => n * 256 + Number(p), 0)
const scanRows = computed(() => [...(scanFound.value ?? [])].sort((a, b) => ipNum(a.ip) - ipNum(b.ip)).map((h, i) => ({ ...h, __no: i + 1,
  ports_t: h.open_ports.join('、'), st_t: h.already_registered ? `已登記（${h.existing_asset_serial ?? ''}）` : '未登記' })))
const scanCols = [{ k: 'ip', label: 'IP', mono: true }, { k: 'hostname', label: '主機名（反解）' }, { k: 'ports_t', label: '開放 Port', mono: true },
  { k: 'st_t', label: '狀態', link: (h: any) => (h.existing_asset_serial ? `/v2/machine/${encodeURIComponent(h.existing_asset_serial)}` : null) }]
</script>

<template>
  <div>
    <p v-if="!isAdmin" class="muted">新增主機限管理員<InfoNote>手動新增與網段掃描納入都會寫進資產清冊，只有管理員可以做。</InfoNote></p>
    <template v-else>
      <p v-if="loadErr" class="err">{{ loadErr }}</p>
      <p v-if="helperErr" class="warn">{{ helperErr }}</p>
      <h3 class="h3">手動新增資產<InfoNote>只有一台要單獨補登記時用這裡，不用重跑整份 Excel 匯入。欄位跟 CIA 資產清單一致；<b>資產序號</b>必填且不能跟既有資產重複。送出後資產是<b>「待上線」</b>，要過完上線前檢查表才會變成「使用中」。</InfoNote></h3>
      <div class="chips">
        <label class="chip" :class="{ on: provisionSource === 'direct' }"><input v-model="provisionSource" type="radio" value="direct">直接新增<InfoNote>IT 自己要上機器，沒有申請單。</InfoNote></label>
        <label class="chip" :class="{ on: provisionSource === 'form' }"><input v-model="provisionSource" type="radio" value="form">依申請單轉錄<InfoNote>申請單位填的「主機及網路異動需求單」。</InfoNote></label>
      </div>
      <fieldset v-if="provisionSource === 'form'" class="grp">
        <legend>申請單資訊<InfoNote>這張單的來歷，之後稽核要查「當初是誰申請、誰簽的」。</InfoNote></legend>
        <div class="grid">
          <label v-for="f in provisionFields" :key="f.key" class="fld"><span class="lbl">{{ f.label }}<b v-if="f.required" class="req">*</b></span>
            <input v-model="provisionForm[f.key]" :type="f.type ?? 'text'" :placeholder="f.placeholder ?? ''"></label>
          <div class="fld"><span class="lbl">變更類別</span>
            <SearchableSelect :model-value="provisionForm.change_kind ?? ''" :options="changeKinds" placeholder="請選擇" @update:model-value="(v: string) => (provisionForm.change_kind = v)" /></div>
          <label class="fld wide"><span class="lbl">申請單掃描檔（選填）<InfoNote>存原始檔備查，系統不解析內容。</InfoNote></span>
            <input :key="fileKey" type="file" accept=".doc,.docx,.pdf,.png,.jpg,.jpeg" aria-label="申請單掃描檔" @change="onProvisionFile"></label>
        </div>
      </fieldset>

      <p v-if="createdAsset" class="ok">已建立 <b>{{ createdAsset.serial }}</b>，狀態「待上線」，上線前檢查表 {{ createdAsset.done }}／{{ createdAsset.total }} 項已完成。
        <NuxtLink :to="`/v2/golive/${encodeURIComponent(createdAsset.serial)}`">去填上線檢查表 →</NuxtLink>
        <InfoNote>機器測得到的項目已自動判定。</InfoNote></p>
      <label class="vm"><input v-model="manualIsVm" type="checkbox"> 是否為 VM<InfoNote>勾選後會隱藏機櫃編號／硬體編號——這兩個只有實體機才有意義。</InfoNote></label>

      <fieldset v-for="g in manualGroupedFields" :key="g.key" class="grp">
        <legend>{{ g.label }}<InfoNote v-if="g.hint">{{ g.hint }}</InfoNote></legend>
        <div class="grid">
          <template v-for="row in g.rows" :key="row.field">
            <div v-if="row.field === 'os'" class="fld wide">
              <span class="lbl">{{ row.header }}<button type="button" class="lnk" @click="toggleOsCustom(!osCustom)">{{ osCustom ? '改用選單' : '目錄沒有？自行輸入' }}</button></span>
              <input v-if="osCustom" v-model="manualForm.os" type="text" placeholder="完整 OS 版本字串" aria-label="作業系統">
              <div v-else class="row3">
                <SearchableSelect v-model="osFamilySel" :options="Object.keys(osCatalog)" placeholder="大類" />
                <SearchableSelect v-model="osDistroSel" :options="osDistroOptions" :disabled="!osFamilySel" placeholder="發行版／產品" />
                <SearchableSelect v-model="osVersionSel" :options="osVersionOptions" :disabled="!osDistroSel" placeholder="版本" />
              </div>
            </div>
            <div v-else-if="row.field === 'ip' && hasSegments" class="fld wide">
              <span class="lbl">{{ row.header }}<InfoNote>先選機房與環境，系統只列該段的網段，並帶入一個清單裡還沒登記的位址。</InfoNote></span>
              <div class="row3">
                <SearchableSelect v-model="segLoc" :options="segTree.map((n) => n.location)" placeholder="機房" />
                <SearchableSelect v-model="segEnv" :options="segEnvOptions" :disabled="!segLoc" placeholder="環境" />
                <SearchableSelect v-model="segCidr" :options="segCidrOptions.map((s) => s.cidr)" :disabled="!segEnv" placeholder="網段" />
                <input v-model="manualForm.ip" type="text" placeholder="IP" class="ip" aria-label="IP">
              </div>
              <div v-if="selectedSeg" class="seg">
                <b>{{ selectedSeg.label }}</b><span v-if="selectedSeg.usage">・{{ selectedSeg.category }}／{{ selectedSeg.usage }}</span>
                <span v-if="selectedSeg.scan_excluded" class="warn">・這段被註記「建議排除掃描」</span>
                <div v-if="selectedSeg.scan_note" class="muted">{{ selectedSeg.scan_note }}</div>
                <div v-if="segIpLoading" class="muted">查詢已登記的 IP…</div>
                <div v-else-if="segIpErr" class="err">{{ segIpErr }}</div>
                <div v-else-if="segIpInfo">這段已登記 {{ segIpInfo.used.length }}／{{ segIpInfo.capacity }} 個位址；建議值 <b class="mono">{{ segIpInfo.suggestion ?? '（沒有空位）' }}</b>
                  <InfoNote>這只代表<b>清單裡沒登記</b>，不代表實際上沒人在用，配之前請先 ping 一下。</InfoNote></div>
              </div>
            </div>
            <label v-else-if="row.field === 'asset_serial'" class="fld"><span class="lbl">{{ row.header }}<b class="req">*</b></span>
              <input v-model="manualForm.asset_serial" type="text" placeholder="必填，需唯一"></label>
            <div v-else-if="manualFieldOptions[row.field] && !manualCustomFields.has(row.field)" class="fld">
              <span class="lbl">{{ row.header }}<button type="button" class="lnk" @click="toggleManualCustom(row.field, true)">自行輸入</button></span>
              <SearchableSelect :model-value="manualForm[row.field] ?? ''" :options="manualFieldOptions[row.field]" placeholder="請選擇" @update:model-value="(v: string) => (manualForm[row.field] = v)" />
            </div>
            <label v-else class="fld" :class="{ wide: row.field === 'remark' }">
              <span class="lbl">{{ row.header }}<button v-if="manualFieldOptions[row.field]" type="button" class="lnk" @click="toggleManualCustom(row.field, false)">改用選單</button></span>
              <input v-model="manualForm[row.field]" type="text"></label>
          </template>
        </div>
      </fieldset>
      <p v-if="manualError" class="err">{{ manualError }}</p>
      <div class="bar"><button type="button" class="btn2 pri" :disabled="manualSubmitting" @click="submitManualAsset">{{ manualSubmitting ? '新增中…' : '新增這筆資產' }}</button></div>

      <h3 class="h3">網段存活掃描<InfoNote>輸入網段（例：<code>10.99.1.0/24</code>，只能是內網私有網段），掃這段裡有哪些主機開了 <b>22（SSH）</b>或 <b>445（SMB）</b>——用這兩個 port 判斷是不是「連得到、可管理」的存活主機。掃完只有 IP／主機名，沒有業務欄位，納入後可以到主機履歷補。</InfoNote></h3>
      <div class="bar">
        <input v-model="scanCidr" type="text" placeholder="10.99.1.0/24" class="cidr" aria-label="網段" @keyup.enter="runScan">
        <button type="button" class="btn2" :disabled="scanning" @click="runScan">{{ scanning ? '掃描中…' : '開始掃描' }}</button>
      </div>
      <p v-if="scanError" class="err">{{ scanError }}</p>
      <template v-if="scanFound">
        <V2Table v-model:selected="scanSelected" :rows="scanRows" :cols="scanCols" prefix="S" row-key="ip" unit="台" selectable :max="Math.max(100, scanRows.length)" export-name="網段存活掃描" empty="這個網段沒掃到存活主機" />
        <div v-if="scanFound.length" class="bar">
          <button type="button" class="btn2 pri" :disabled="scanImporting || pickedCount === 0" @click="importSelectedScan">{{ scanImporting ? '納入中…' : `納入勾選的 ${pickedCount} 台` }}</button>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.h3 { margin: 16px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 10px; }
.chip { display: inline-flex; gap: 4px; align-items: center; font-size: 13px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.grp { border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 12px 12px; margin: 0 0 10px; background: var(--card); }
.grp legend { font-size: 13px; font-weight: 600; color: var(--ink); display: inline-flex; gap: 4px; align-items: center; padding: 0 4px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 12px; }
.fld { display: flex; flex-direction: column; gap: 3px; font-size: 13px; }
.fld.wide { grid-column: 1 / -1; }
.lbl { font-size: 12px; color: var(--ink-aux); display: inline-flex; gap: 6px; align-items: center; }
.fld input[type="text"], .fld input[type="date"], .cidr { font-family: inherit; font-size: 13px; padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.row3 { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.ip { max-width: 160px; }
.cidr { min-width: 200px; }
.seg { font-size: 12.5px; margin-top: 4px; padding: 6px 10px; border-radius: 8px; background: var(--sub); color: var(--ink-soft); }
.vm { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; margin: 4px 0 10px; }
.req { color: var(--bad); }
.lnk { background: none; border: none; color: var(--link); text-decoration: underline; cursor: pointer; font-size: 12px; font-family: inherit; padding: 0; }
.btn2 { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2.pri { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.btn2:disabled { opacity: .45; cursor: default; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 13px; display: flex; gap: 4px; align-items: center; }
.warn { color: var(--warn-text); }
.err { color: var(--bad); }
.ok { color: var(--good); font-size: 13px; }
</style>
