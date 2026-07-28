<script setup lang="ts">
// S10：系統設定頁-連線設定分頁。D14/D15：帳密與連線目標合併同一表格，行內編輯。
// 密碼write-only：後端從不回傳密碼內容，這裡的密碼輸入框永遠是空的，填了才代表要覆蓋。
// S11：架構圖分頁，資料來源是同一份connections清單（不是另外造假資料），依last_status
// 畫綠/紅/灰邊線，跟連線設定分頁「同一份真相」，改一邊另一邊自動同步。
interface ConnectionRow {
  id: number
  name: string
  connection_type: string | null
  target: string
  port: number | null
  username: string | null
  has_password: boolean
  last_status: string
  last_tested_at: string | null
  enabled?: number
}

const { apiFetch } = useApi()
const route = useRoute()
const { flags, isEnabled, ensureLoaded: ensureFlagsLoaded, setEnabled } = useFeatureFlags()

const { showToast } = useToast()
const initialTab = route.query.disabled ? 'modules' : 'connections'
const activeTab = ref<'connections' | 'diagram' | 'modules' | 'schedule' | 'backup' | 'credentials' | 'autoonboard' | 'vcauto'>(initialTab)
const connections = ref<ConnectionRow[]>([])
// 天條：表格每欄可排。useSort 回傳的是同一批物件的新陣列，
// 所以連線那張表的 v-model 編輯照樣綁得到原物件，排序不會弄丟使用者正在改的內容。
const { sortKey: cnKey, sortDir: cnDir, toggle: cnToggle, sorted: connectionsSorted } =
  useSort(connections, 'name')
const { sortKey: fgKey, sortDir: fgDir, toggle: fgToggle, sorted: flagsSorted } =
  useSort(flags, 'label')
const drafts = reactive<Record<number, { password: string }>>({})

// ===== 收集用憑證（Windows WinRM 服務帳號等）=====
// ⚠️ 密碼只在送出那一次存在於前端，送完立刻清空；後端加密後才進 DB，
// 回應永不含密碼（連密文都不給）。
interface CredRow {
  id: number; name: string; kind: string; username: string
  scope: string | null; note: string | null; updated_at: string; has_secret: boolean
}
const creds = ref<CredRow[]>([])
const newCred = reactive({ name: '', kind: 'winrm', username: '', password: '', scope: '', note: '' })
const savingCred = ref(false)

async function loadCreds() {
  try { creds.value = await apiFetch<CredRow[]>('/api/credentials') } catch { creds.value = [] }
}
async function saveCred() {
  if (!newCred.name || !newCred.username || !newCred.password) {
    showToast('名稱、帳號、密碼都要填', 'warn'); return
  }
  savingCred.value = true
  try {
    await apiFetch('/api/credentials', { method: 'POST', body: { ...newCred } })
    newCred.password = ''          // 用完立刻從前端清掉
    showToast(`已儲存憑證「${newCred.name}」`, 'success')
    newCred.name = ''; newCred.username = ''; newCred.scope = ''; newCred.note = ''
    await loadCreds()
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    newCred.password = ''
    savingCred.value = false
  }
}
async function delCred(name: string) {
  try {
    await apiFetch(`/api/credentials/${encodeURIComponent(name)}`, { method: 'DELETE' })
    showToast(`已刪除「${name}」`, 'success')
    await loadCreds()
  } catch { showToast('刪除失敗', 'error') }
}
watch(activeTab, (t) => { if (t === 'credentials' && !creds.value.length) loadCreds() })

// ===== B：排程自動納管 =====
// 安全閘門是「授權網段」——排程只碰列在這裡且啟用的網段前綴。總開關預設關閉，
// 開了也只碰授權網段內、已登記卻未納管的主機（Linux 用庫裡 ssh 憑證跑 bootstrap，
// Windows 走 WinRM 收集不跑 bootstrap）。
interface SegRow {
  id: number; prefix: string; enabled: number; note: string | null
  created_at: string; updated_at: string
}
interface AutoAuditRow {
  target_ip: string; platform: string | null; login_user: string | null
  ok: number; stage: string | null; message: string | null; created_at: string
}
const aoEnabled = ref(false)
const aoSegments = ref<SegRow[]>([])
const aoRecent = ref<AutoAuditRow[]>([])
const newSeg = reactive({ prefix: '', note: '' })
const aoLoading = ref(false)
const aoSavingSeg = ref(false)
const aoTogglingEnabled = ref(false)
const aoTogglingSeg = ref<number | null>(null)
const aoRunning = ref(false)
const aoRunResult = ref<any>(null)
// 天條：兩張表都要可排
const { sortKey: segKey, sortDir: segDir, toggle: segToggle, sorted: segSorted } =
  useSort(aoSegments, 'prefix')
const { sortKey: aaKey, sortDir: aaDir, toggle: aaToggle, sorted: aoRecentSorted } =
  useSort(aoRecent, 'created_at')

async function loadAutoOnboard() {
  aoLoading.value = true
  try {
    const r = await apiFetch<{ enabled: boolean; segments: SegRow[]; recent: AutoAuditRow[] }>('/api/auto-onboard')
    aoEnabled.value = r.enabled
    aoSegments.value = r.segments
    aoRecent.value = r.recent
  } catch {
    showToast('自動納管設定載入失敗', 'error')
  } finally {
    aoLoading.value = false
  }
}
async function toggleAutoOnboardEnabled() {
  aoTogglingEnabled.value = true
  const next = !aoEnabled.value
  try {
    const r = await apiFetch<{ enabled: boolean }>('/api/auto-onboard/enabled', {
      method: 'PATCH', body: { enabled: next },
    })
    aoEnabled.value = r.enabled
    showToast(next ? '已啟用排程自動納管' : '已關閉排程自動納管', 'success')
  } catch (err: any) {
    showToast(`切換失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoTogglingEnabled.value = false
  }
}
async function saveSeg() {
  if (!newSeg.prefix.trim()) { showToast('網段前綴要填，如 192.168.1.', 'warn'); return }
  aoSavingSeg.value = true
  try {
    const r = await apiFetch<{ segments: SegRow[] }>('/api/auto-onboard/segments', {
      method: 'POST', body: { prefix: newSeg.prefix.trim(), enabled: true, note: newSeg.note || null },
    })
    aoSegments.value = r.segments
    showToast(`已授權網段「${newSeg.prefix.trim()}」`, 'success')
    newSeg.prefix = ''; newSeg.note = ''
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoSavingSeg.value = false
  }
}
async function toggleSeg(row: SegRow) {
  aoTogglingSeg.value = row.id
  const next = row.enabled === 0
  try {
    const r = await apiFetch<{ segments: SegRow[] }>(`/api/auto-onboard/segments/${row.id}/enabled`, {
      method: 'PATCH', body: { enabled: next },
    })
    aoSegments.value = r.segments
  } catch (err: any) {
    showToast(`切換失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoTogglingSeg.value = null
  }
}
async function delSeg(row: SegRow) {
  try {
    const r = await apiFetch<{ segments: SegRow[] }>(`/api/auto-onboard/segments/${row.id}`, { method: 'DELETE' })
    aoSegments.value = r.segments
    showToast(`已移除授權網段「${row.prefix}」`, 'success')
  } catch { showToast('移除失敗', 'error') }
}
async function runAutoOnboardNow() {
  aoRunning.value = true
  aoRunResult.value = null
  try {
    const r = await apiFetch<any>('/api/auto-onboard/run', { method: 'POST' })
    aoRunResult.value = r
    const msg = `候選 ${r.candidates}｜納管 ${r.onboarded}｜失敗 ${r.failed}｜跳過 ${r.skipped}`
    showToast(`執行完成 · ${msg}`, r.failed ? 'warn' : 'success')
    await loadAutoOnboard()   // 稽核與狀態刷新
  } catch (err: any) {
    showToast(`執行失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoRunning.value = false
  }
}
watch(activeTab, (t) => { if (t === 'autoonboard' && !aoSegments.value.length && !aoLoading.value) loadAutoOnboard() })

// ===== VC 自動匯入（方案 B：檔案中繼）=====
// 一台常開的 Windows 排「每晚 RVTools 匯出到共享資料夾」，我們只去抓最新檔。
// 這裡設定監看資料夾＋開關，並顯示鮮度燈（今晚的匯出到底進來了沒）。
interface VcHealth {
  status: 'green' | 'yellow' | 'red' | 'off'
  reason: string
  enabled: boolean
  dir: string
  max_age_hours: number
  last_file?: string
  last_at?: string
  last_result?: string
  newest_file?: string | null
  newest_age_hours?: number | null
}
const vc = reactive({ enabled: false, dir: '', max_age_hours: 36 })
const vcHealth = ref<VcHealth | null>(null)
const vcLoading = ref(false)
const vcSaving = ref(false)
const vcRunning = ref(false)
const VC_LAMP_LABEL: Record<string, string> = {
  green: '正常收得到', yellow: '需要注意', red: '有問題', off: '未啟用',
}
// 要貼到那台 Windows 工作排程器的 RVTools 匯出指令（依使用者設的資料夾即時組出）
const vcExportCmd = computed(() => {
  const d = vc.dir?.trim() || 'D:\\vcenter_export'
  return `"C:\\Program Files (x86)\\Robware\\RVTools\\RVTools.exe" -s <vCenter位址> -u <唯讀帳號> -p <密碼> -c ExportAll2xlsx -d "${d}"`
})

async function loadVcAuto() {
  vcLoading.value = true
  try {
    const h = await apiFetch<VcHealth>('/api/vcenter-autoimport')
    vcHealth.value = h
    vc.enabled = h.enabled; vc.dir = h.dir; vc.max_age_hours = h.max_age_hours
  } catch {
    showToast('vCenter 自動匯入設定載入失敗', 'error')
  } finally {
    vcLoading.value = false
  }
}
async function saveVcAuto() {
  vcSaving.value = true
  try {
    vcHealth.value = await apiFetch<VcHealth>('/api/vcenter-autoimport', {
      method: 'PUT', body: { enabled: vc.enabled, dir: vc.dir.trim(), max_age_hours: Number(vc.max_age_hours) },
    })
    showToast('已儲存 vCenter 自動匯入設定', 'success')
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    vcSaving.value = false
  }
}
async function runVcAutoNow() {
  vcRunning.value = true
  try {
    const r = await apiFetch<any>('/api/vcenter-autoimport/run', { method: 'POST' })
    const MAP: Record<string, string> = {
      imported: r.line ? `已匯入最新檔：${r.line}` : '已匯入最新檔',
      already_current: '最新的那份已經匯過了，沒有新檔',
      no_file: '資料夾裡目前沒有可用的匯出檔',
      no_dir: '資料夾沒設定或不存在',
      error: `匯入失敗：${r.error ?? ''}`,
    }
    const tone = r.status === 'imported' ? 'success' : r.status === 'error' ? 'error' : 'warn'
    showToast(MAP[r.status] ?? r.status, tone as any)
    await loadVcAuto()
  } catch (err: any) {
    showToast(`執行失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    vcRunning.value = false
  }
}
async function copyVcCmd() {
  try { await navigator.clipboard.writeText(vcExportCmd.value); showToast('已複製匯出指令', 'success') }
  catch { showToast('複製失敗，請手動選取', 'warn') }
}
watch(activeTab, (t) => { if (t === 'vcauto' && !vcHealth.value && !vcLoading.value) loadVcAuto() })

const newRow = reactive({ name: '', connection_type: '', target: '', port: '', username: '', password: '' })
const errorMessage = ref('')
const savingId = ref<number | 'new' | null>(null)
const testingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)
const togglingKey = ref<string | null>(null)
const togglingConn = ref<number | null>(null)

/** 來源啟用／停用。停用＝排程掃描跳過，不會被算成「掃描失敗」。
 *  有些來源現階段本來就連不到（CMDB Gateway 在家碰不到公司內網），沒有開關的話
 *  它每次都失敗、每次點亮「掃描不完整」——常態假警報會讓真正的問題沒人看見。 */
async function toggleConnection(row: ConnectionRow) {
  togglingConn.value = row.id
  const next = row.enabled === 0
  try {
    await apiFetch(`/api/connections/${row.id}/enabled`, { method: 'PATCH', body: { enabled: next } })
    row.enabled = next ? 1 : 0
    showToast(`「${row.name}」已${next ? '啟用' : '停用'}`, 'success')
  } catch (err: any) {
    showToast(`切換失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    togglingConn.value = null
  }
}

interface Schedule {
  enabled: boolean
  mode: string
  time: string
  interval_hours: number
}
const schedule = reactive<Schedule>({ enabled: true, mode: 'daily', time: '01:00', interval_hours: 6 })
const savingSchedule = ref(false)

// ===== S14 備份與健康 =====
// 目的：工程師/助理不用進命令列就能看狀態、手動備份。
// 燈號不是「有沒有跑過備份」，後端會實際開檔跑 integrity_check——
// 「檔案在但內容是壞的」才是最危險的情況，只看檔案存在的儀表板會顯示綠燈。
interface BackupHealth {
  status: 'green' | 'yellow' | 'red'
  reasons: string[]
  checked_at: string
  db: {
    path: string; exists: boolean; integrity_ok: boolean
    integrity_detail: string; journal_mode: string | null; size_bytes: number
  }
  last_backup: {
    name: string; size_bytes: number; modified_at: string
    age_hours: number | null; integrity_ok: boolean | null; integrity_detail: string
  } | null
  local: { dir: string; count: number; free_mb: number | null; retention_days: number }
  offsite: { configured: boolean; dir: string | null; count: number }
}

const backupHealth = ref<BackupHealth | null>(null)
const backupLoading = ref(false)
const backupRunning = ref(false)
const backupError = ref('')

const LAMP_LABEL: Record<string, string> = {
  green: '正常', yellow: '需要注意', red: '有問題',
}

function fmtBytes(n: number | null | undefined) {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

async function loadBackupHealth() {
  backupLoading.value = true
  backupError.value = ''
  try {
    backupHealth.value = await apiFetch<BackupHealth>('/api/backup/status')
  } catch (err: any) {
    backupError.value = err?.data?.detail ?? '備份狀態載入失敗，請稍後再試'
  } finally {
    backupLoading.value = false
  }
}

async function runBackupNow() {
  backupRunning.value = true
  backupError.value = ''
  try {
    const r = await apiFetch<any>('/api/backup/run', { method: 'POST' })
    if (r.ok) {
      const extra = r.offsite_path
        ? '（含異地副本）'
        : r.offsite_error ? `（異地副本失敗：${r.offsite_error}）` : ''
      showToast(`備份完成 · ${fmtBytes(r.size_bytes)} · 完整性 PASS ${extra}`,
                r.offsite_error ? 'warn' : 'success')
    } else {
      // 後端刻意用 200 回失敗細節，這裡把原因原樣顯示，不要吞成一句「失敗」
      backupError.value = r.message ?? r.error ?? '備份失敗'
      showToast(backupError.value, 'error')
    }
    await loadBackupHealth()
  } catch (err: any) {
    backupError.value = err?.data?.detail ?? '備份執行失敗，請稍後再試'
    showToast(backupError.value, 'error')
  } finally {
    backupRunning.value = false
  }
}

watch(activeTab, (t) => {
  if (t === 'backup' && !backupHealth.value) loadBackupHealth()
})

// 異地備份路徑：畫面可設（存 app_settings），不用改 systemd 環境變數再重啟。
// 路徑要指到「真正獨立的儲存」才算異地——例如掛載另一台機器（222）的 /ai_backup。
const offsiteDraft = ref('')
const savingOffsite = ref(false)
watch(backupHealth, (h) => { if (h) offsiteDraft.value = h.offsite.dir ?? '' })
async function saveOffsite() {
  savingOffsite.value = true
  try {
    backupHealth.value = await apiFetch<BackupHealth>('/api/backup/offsite', {
      method: 'PUT', body: { dir: offsiteDraft.value.trim() },
    })
    showToast(offsiteDraft.value.trim() ? '已設定異地備份路徑' : '已清除異地備份路徑', 'success')
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    savingOffsite.value = false
  }
}

async function loadSchedule() {
  Object.assign(schedule, await apiFetch<Schedule>('/api/scan/schedule'))
}

async function saveSchedule() {
  savingSchedule.value = true
  errorMessage.value = ''
  try {
    await apiFetch('/api/scan/schedule', {
      method: 'PUT',
      body: {
        enabled: schedule.enabled,
        mode: schedule.mode,
        time: schedule.time,
        interval_hours: Number(schedule.interval_hours),
      },
    })
    showToast('掃描排程已儲存，下次依新設定執行', 'success')
  } catch (err: any) {
    showToast(`排程儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    savingSchedule.value = false
  }
}

async function loadConnections() {
  connections.value = await apiFetch<ConnectionRow[]>('/api/connections')
  for (const c of connections.value) {
    if (!(c.id in drafts)) drafts[c.id] = { password: '' }
  }
}
await Promise.all([loadConnections(), ensureFlagsLoaded(), loadSchedule()])

const disabledModuleLabel = computed(() => {
  const key = route.query.disabled as string | undefined
  if (!key) return null
  return flags.value.find((f) => f.module_key === key)?.label ?? key
})

async function toggleModule(moduleKey: string, enabled: boolean) {
  togglingKey.value = moduleKey
  errorMessage.value = ''
  try {
    await setEnabled(moduleKey, enabled)
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '切換失敗'
  } finally {
    togglingKey.value = null
  }
}

function statusDotClass(status: string) {
  if (status === '綠') return 'green'
  if (status === '紅') return 'red'
  return 'gray'
}
function statusLabel(status: string) {
  if (status === '綠') return '已連線'
  if (status === '紅') return '未連線'
  return '未知'
}

async function saveExisting(row: ConnectionRow) {
  savingId.value = row.id
  errorMessage.value = ''
  try {
    const body: Record<string, any> = {
      name: row.name,
      connection_type: row.connection_type,
      target: row.target,
      port: row.port ? Number(row.port) : null,
      username: row.username,
    }
    const pw = drafts[row.id]?.password
    if (pw) body.password = pw
    await apiFetch(`/api/connections/${row.id}`, { method: 'PUT', body })
    drafts[row.id].password = ''
    await loadConnections()
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '儲存失敗'
  } finally {
    savingId.value = null
  }
}

async function saveNew() {
  if (!newRow.name || !newRow.target) {
    errorMessage.value = '連線名稱與目標位址為必填'
    return
  }
  savingId.value = 'new'
  errorMessage.value = ''
  try {
    await apiFetch('/api/connections', {
      method: 'POST',
      body: {
        name: newRow.name,
        connection_type: newRow.connection_type || null,
        target: newRow.target,
        port: newRow.port ? Number(newRow.port) : null,
        username: newRow.username || null,
        password: newRow.password || null,
      },
    })
    newRow.name = ''
    newRow.connection_type = ''
    newRow.target = ''
    newRow.port = ''
    newRow.username = ''
    newRow.password = ''
    await loadConnections()
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '新增失敗'
  } finally {
    savingId.value = null
  }
}

async function testConnection(row: ConnectionRow) {
  testingId.value = row.id
  errorMessage.value = ''
  try {
    await apiFetch(`/api/connections/${row.id}/test`, { method: 'POST' })
    await loadConnections()
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '測試失敗'
  } finally {
    testingId.value = null
  }
}

async function deleteConnection(row: ConnectionRow) {
  if (!confirm(`確定要刪除連線設定「${row.name}」？此操作無法復原。`)) return
  deletingId.value = row.id
  errorMessage.value = ''
  try {
    await apiFetch(`/api/connections/${row.id}`, { method: 'DELETE' })
    await loadConnections()
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '刪除失敗'
  } finally {
    deletingId.value = null
  }
}

const ROW_HEIGHT = 64
const NODE_W = 150
const NODE_H = 48
const HUB_X = 20
const NODE_X = 420

const diagramHeight = computed(() => Math.max(connections.value.length * ROW_HEIGHT + 20, 120))

const diagramNodes = computed(() =>
  connections.value.map((c, i) => ({
    ...c,
    y: 20 + i * ROW_HEIGHT,
    edgeClass: c.last_status === '綠' ? 'edge-green' : c.last_status === '紅' ? 'edge-red' : 'edge-gray',
    label: c.port ? `${c.name} :${c.port}` : c.name,
  }))
)

const hubY = computed(() => diagramHeight.value / 2)
</script>

<template>
  <div>
    <div class="section-divider">系統設定</div>
    <div class="tabs">
      <div class="tab" :class="{ active: activeTab === 'connections' }" @click="activeTab = 'connections'">
        連線設定
      </div>
      <div class="tab" :class="{ active: activeTab === 'diagram' }" @click="activeTab = 'diagram'">
        架構圖
      </div>
      <div class="tab" :class="{ active: activeTab === 'modules' }" @click="activeTab = 'modules'">
        功能模組管理
      </div>
      <div class="tab" :class="{ active: activeTab === 'credentials' }" @click="activeTab = 'credentials'">
        收集憑證
      </div>
      <div class="tab" :class="{ active: activeTab === 'autoonboard' }" @click="activeTab = 'autoonboard'">
        自動納管
        <span v-if="aoEnabled" class="tab-lamp green" />
      </div>
      <div class="tab" :class="{ active: activeTab === 'vcauto' }" @click="activeTab = 'vcauto'">
        vCenter 匯入
        <span v-if="vcHealth && vcHealth.status !== 'off'" class="tab-lamp"
              :class="vcHealth.status === 'green' ? 'green' : vcHealth.status === 'red' ? 'red' : 'yellow'" />
      </div>
      <div class="tab" :class="{ active: activeTab === 'backup' }" @click="activeTab = 'backup'">
        備份與健康
        <span v-if="backupHealth" class="tab-lamp" :class="backupHealth.status" />
      </div>
      <div class="tab" :class="{ active: activeTab === 'schedule' }" @click="activeTab = 'schedule'">
        掃描排程
      </div>
    </div>

    <div v-if="disabledModuleLabel" class="notice-banner">
      「{{ disabledModuleLabel }}」目前已停用，可在下方「功能模組管理」重新啟用。
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="activeTab === 'connections'">
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="enabled" :active="cnKey" :dir="cnDir" @sort="cnToggle">啟用</SortTh><SortTh k="name" :active="cnKey" :dir="cnDir" @sort="cnToggle">連線</SortTh><SortTh k="target" :active="cnKey" :dir="cnDir" @sort="cnToggle">目標位址／網段</SortTh><SortTh k="port" :active="cnKey" :dir="cnDir" @sort="cnToggle">Port</SortTh><SortTh k="username" :active="cnKey" :dir="cnDir" @sort="cnToggle">帳號</SortTh><th>密碼</th><SortTh k="last_status" :active="cnKey" :dir="cnDir" @sort="cnToggle">狀態</SortTh><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in connectionsSorted" :key="row.id" :class="{ off: row.enabled === 0 }">
              <td>
                <button class="tgl" :class="{ on: row.enabled !== 0 }" type="button"
                        :disabled="togglingConn === row.id"
                        :title="row.enabled === 0 ? '目前停用：排程掃描會跳過，不算掃描失敗' : '目前啟用：會被排程掃描'"
                        @click="toggleConnection(row)">
                  {{ row.enabled === 0 ? '停用' : '啟用' }}
                </button>
              </td>
              <td><input v-model="row.name" type="text" class="cell-input" /></td>
              <td><input v-model="row.target" type="text" class="cell-input" /></td>
              <td><input v-model="row.port" type="text" class="cell-input narrow" /></td>
              <td><input v-model="row.username" type="text" class="cell-input" /></td>
              <td>
                <input
                  v-model="drafts[row.id].password"
                  type="password"
                  class="cell-input"
                  :placeholder="row.has_password ? '留空＝不變更' : '尚未設定'"
                />
              </td>
              <td>
                <span class="status-dot" :class="statusDotClass(row.last_status)">
                  <span class="d"></span>{{ statusLabel(row.last_status) }}
                </span>
              </td>
              <td class="actions-cell">
                <button class="btn small" :disabled="savingId === row.id" @click="saveExisting(row)">
                  {{ savingId === row.id ? '儲存中…' : '儲存' }}
                </button>
                <button class="btn ghost small" :disabled="testingId === row.id" @click="testConnection(row)">
                  {{ testingId === row.id ? '測試中…' : '測試連線' }}
                </button>
                <button class="btn ghost small" :disabled="deletingId === row.id" @click="deleteConnection(row)">
                  刪除
                </button>
              </td>
            </tr>
            <tr>
              <td><input v-model="newRow.name" type="text" class="cell-input" placeholder="連線名稱" /></td>
              <td><input v-model="newRow.target" type="text" class="cell-input" placeholder="目標位址／網段" /></td>
              <td><input v-model="newRow.port" type="text" class="cell-input narrow" placeholder="Port" /></td>
              <td><input v-model="newRow.username" type="text" class="cell-input" placeholder="帳號" /></td>
              <td><input v-model="newRow.password" type="password" class="cell-input" placeholder="密碼" /></td>
              <td class="muted-cell">—</td>
              <td>
                <button class="btn small" :disabled="savingId === 'new'" @click="saveNew">
                  {{ savingId === 'new' ? '新增中…' : '新增連線' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else-if="activeTab === 'diagram'">
      <div v-if="connections.length === 0" class="diagram-placeholder">
        還沒有任何連線設定，請先到「連線設定」分頁新增，架構圖會自動反映同一份資料。
      </div>
      <div v-else class="diagram-wrap">
        <svg :viewBox="`0 0 620 ${diagramHeight}`" width="100%" style="max-width: 620px; display: block; margin: 0 auto">
          <rect :x="HUB_X" :y="hubY - 30" width="130" height="60" rx="3" class="node" />
          <text :x="HUB_X + 65" :y="hubY - 8" text-anchor="middle" class="node-label">資產盤點服務</text>
          <text :x="HUB_X + 65" :y="hubY + 8" text-anchor="middle" class="node-sub">本機</text>

          <template v-for="n in diagramNodes" :key="n.id">
            <line
              :x1="HUB_X + 130"
              :y1="hubY"
              :x2="NODE_X"
              :y2="n.y + NODE_H / 2"
              :class="n.edgeClass"
            />
            <text
              :x="(HUB_X + 130 + NODE_X) / 2"
              :y="(hubY + n.y + NODE_H / 2) / 2 - 6"
              text-anchor="middle"
              class="edge-label"
            >
              {{ n.label }}
            </text>
            <rect :x="NODE_X" :y="n.y" :width="NODE_W" :height="NODE_H" rx="3" class="node" />
            <text :x="NODE_X + NODE_W / 2" :y="n.y + 20" text-anchor="middle" class="node-label">{{ n.name }}</text>
            <text :x="NODE_X + NODE_W / 2" :y="n.y + 36" text-anchor="middle" class="node-sub">
              {{ n.target }}{{ n.port ? ':' + n.port : '' }}
            </text>
          </template>
        </svg>

        <div class="legend-list">
          <div class="row-l"><span class="mk" style="background: var(--good)">綠</span>已連線（最近一次測試成功）</div>
          <div class="row-l"><span class="mk" style="background: var(--bad)">紅</span>未連線（最近一次測試失敗）</div>
          <div class="row-l"><span class="mk" style="background: var(--muted)">灰</span>未知（未測過，或太久沒更新，D15）</div>
        </div>
      </div>
    </template>

    <template v-else-if="activeTab === 'modules'">
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr><SortTh k="label" :active="fgKey" :dir="fgDir" @sort="fgToggle">模組</SortTh><SortTh k="enabled" :active="fgKey" :dir="fgDir" @sort="fgToggle">狀態</SortTh><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in flagsSorted" :key="f.module_key">
              <td>{{ f.label }}</td>
              <td>
                <span class="status-dot" :class="f.enabled ? 'green' : 'gray'">
                  <span class="d"></span>{{ f.enabled ? '已啟用' : '已停用' }}
                </span>
              </td>
              <td>
                <button
                  class="btn small"
                  :class="{ ghost: f.enabled }"
                  :disabled="togglingKey === f.module_key"
                  @click="toggleModule(f.module_key, !f.enabled)"
                >
                  {{ togglingKey === f.module_key ? '處理中…' : f.enabled ? '停用' : '啟用' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="modules-hint">
        「系統設定」本身不列在這裡——它是切換這些開關的畫面，不能被自己鎖死。
      </p>
    </template>

    <!-- 收集用憑證：Windows WinRM 服務帳號等，排程收集反覆使用 -->
    <template v-else-if="activeTab === 'credentials'">
      <div class="card">
        <div class="card-title">收集用憑證</div>
        <p class="credhint">
          Windows 走 <b>WinRM／CIM</b> 收集，需要一組服務帳號（Linux 走 SSH 金鑰，不需要這個）。
          <br>密碼以 <b>Fernet 加密</b>後才存進資料庫，加密金鑰另存於伺服器
          <code>/opt/webit3/.credential_key</code>（0600、不在資料庫裡——資料庫被複製走也解不開）。
          <br>畫面與 API <b>永遠不會回傳密碼</b>，連密文都不給；每次使用都留稽核（不含密碼）。
        </p>

        <div class="tbl-wrap">
          <table>
            <thead><tr>
              <th>名稱</th><th>類型</th><th>帳號</th><th>適用範圍</th><th>密碼</th><th>更新時間</th><th>操作</th>
            </tr></thead>
            <tbody>
              <tr v-for="c in creds" :key="c.id">
                <td>{{ c.name }}</td>
                <td>{{ c.kind }}</td>
                <td class="mono">{{ c.username }}</td>
                <td class="mono">{{ c.scope || '（通用）' }}</td>
                <td><span class="lockchip">🔒 已加密儲存</span></td>
                <td class="mono dim">{{ c.updated_at }}</td>
                <td><button class="btn danger small" @click="delCred(c.name)">刪除</button></td>
              </tr>
              <tr v-if="!creds.length"><td colspan="7" class="dim">尚未設定任何收集憑證</td></tr>
            </tbody>
          </table>
        </div>

        <div class="card-title" style="margin-top:18px">新增／更新憑證</div>
        <div class="credform">
          <label>名稱<input v-model="newCred.name" placeholder="例：公司網域收集帳號" /></label>
          <label>類型
            <select v-model="newCred.kind">
              <option value="winrm">winrm（Windows 收集）</option>
              <option value="ssh">ssh（Linux 自動納管 bootstrap）</option>
            </select>
          </label>
          <label>帳號<input v-model="newCred.username" autocomplete="off" placeholder="例：DOMAIN\svc_collect 或本機管理員" /></label>
          <label>密碼<input v-model="newCred.password" type="password" autocomplete="new-password" /></label>
          <label>適用範圍<input v-model="newCred.scope" placeholder="網段前綴，如 192.168.1.（留空=通用）" /></label>
          <label>備註<input v-model="newCred.note" placeholder="選填" /></label>
        </div>
        <div class="actions">
          <button class="btn" :disabled="savingCred" @click="saveCred">
            {{ savingCred ? '儲存中…' : '儲存憑證' }}
          </button>
        </div>
      </div>
    </template>

    <!-- B：排程自動納管。授權網段是安全閘門，總開關預設關閉。 -->
    <template v-else-if="activeTab === 'autoonboard'">
      <div class="card">
        <div class="ao-head">
          <div>
            <div class="card-title">排程自動納管</div>
            <p class="credhint" style="margin:6px 0 0">
              排程掃描後，自動把<b>授權網段內「已登記卻連不進去」</b>的主機帶到已納管：
              Linux 用憑證庫裡的 <code>ssh</code> 憑證跑 bootstrap，Windows 走 WinRM 收集（不動目標機）。
              <br>只碰下方<b>授權且啟用</b>的網段；未登記的主機不會被自動建成資產（那是人的決定）。
              每次動作都留稽核，<b>永不含密碼</b>。
            </p>
          </div>
          <label class="ao-switch" :class="{ on: aoEnabled }">
            <input type="checkbox" :checked="aoEnabled" :disabled="aoTogglingEnabled"
                   @change="toggleAutoOnboardEnabled" />
            <span class="ao-switch-track"><span class="ao-switch-knob" /></span>
            <span class="ao-switch-label">{{ aoEnabled ? '排程已啟用' : '排程已關閉' }}</span>
          </label>
        </div>

        <div class="card-title" style="margin-top:18px">授權網段</div>
        <p class="credhint" style="margin:0 0 8px">
          只有列在這裡且啟用的網段，排程才會自動納管。前綴用開頭比對，如
          <code>192.168.1.</code> 會涵蓋 <code>192.168.1.*</code>。
        </p>
        <div class="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh k="enabled" :active="segKey" :dir="segDir" @sort="segToggle">啟用</SortTh>
              <SortTh k="prefix" :active="segKey" :dir="segDir" @sort="segToggle">網段前綴</SortTh>
              <SortTh k="note" :active="segKey" :dir="segDir" @sort="segToggle">備註</SortTh>
              <SortTh k="updated_at" :active="segKey" :dir="segDir" @sort="segToggle">更新時間</SortTh>
              <th>操作</th>
            </tr></thead>
            <tbody>
              <tr v-for="s in segSorted" :key="s.id">
                <td>
                  <button class="btn ghost small" :disabled="aoTogglingSeg === s.id"
                          @click="toggleSeg(s)">
                    {{ s.enabled ? '✔ 啟用中' : '停用' }}
                  </button>
                </td>
                <td class="mono">{{ s.prefix }}</td>
                <td>{{ s.note || '—' }}</td>
                <td class="mono dim">{{ s.updated_at }}</td>
                <td><button class="btn danger small" @click="delSeg(s)">移除</button></td>
              </tr>
              <tr v-if="!segSorted.length"><td colspan="5" class="dim">
                還沒有授權任何網段——排程不會自動納管任何主機。
              </td></tr>
            </tbody>
          </table>
        </div>
        <div class="credform" style="margin-top:12px">
          <label>網段前綴<input v-model="newSeg.prefix" placeholder="例：192.168.1." /></label>
          <label>備註<input v-model="newSeg.note" placeholder="選填，如：機房 A 內網" /></label>
        </div>
        <div class="actions">
          <button class="btn" :disabled="aoSavingSeg" @click="saveSeg">
            {{ aoSavingSeg ? '儲存中…' : '授權此網段' }}
          </button>
        </div>

        <div class="ao-run">
          <div>
            <div class="card-title">立即執行一輪</div>
            <p class="credhint" style="margin:4px 0 0">
              手動觸發：試連 → 納管授權網段內未納管的 Linux → 收 facts。操作者在場，不受總開關約束，
              但仍只碰授權網段。會花數秒到數十秒。
            </p>
          </div>
          <button class="btn" :disabled="aoRunning" @click="runAutoOnboardNow">
            {{ aoRunning ? '執行中…' : '立即執行' }}
          </button>
        </div>
        <div v-if="aoRunResult" class="ao-result">
          候選 <b>{{ aoRunResult.candidates }}</b>｜納管成功 <b>{{ aoRunResult.onboarded }}</b>｜
          失敗 <b>{{ aoRunResult.failed }}</b>｜跳過 <b>{{ aoRunResult.skipped }}</b>
          <ul v-if="aoRunResult.details && aoRunResult.details.length" class="ao-detail">
            <li v-for="(d, i) in aoRunResult.details" :key="i">
              <span class="mono">{{ d.ip }}</span>（{{ d.platform }}）·
              <span :class="d.action === 'onboarded' ? 'ok' : d.action === 'failed' ? 'bad' : 'dim'">
                {{ d.action === 'onboarded' ? '已納管' : d.action === 'failed' ? '失敗' : '跳過' }}
              </span> — {{ d.message }}
            </li>
          </ul>
        </div>

        <div class="card-title" style="margin-top:18px">自動納管稽核</div>
        <div class="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh k="created_at" :active="aaKey" :dir="aaDir" @sort="aaToggle">時間</SortTh>
              <SortTh k="target_ip" :active="aaKey" :dir="aaDir" @sort="aaToggle">目標</SortTh>
              <SortTh k="platform" :active="aaKey" :dir="aaDir" @sort="aaToggle">平台</SortTh>
              <SortTh k="login_user" :active="aaKey" :dir="aaDir" @sort="aaToggle">登入帳號</SortTh>
              <SortTh k="ok" :active="aaKey" :dir="aaDir" @sort="aaToggle">結果</SortTh>
              <th>訊息</th>
            </tr></thead>
            <tbody>
              <tr v-for="(a, i) in aoRecentSorted" :key="i">
                <td class="mono dim">{{ a.created_at }}</td>
                <td class="mono">{{ a.target_ip }}</td>
                <td>{{ a.platform || '—' }}</td>
                <td class="mono">{{ a.login_user || '—' }}</td>
                <td><span :class="a.ok ? 'ok' : 'bad'">{{ a.ok ? '成功' : '失敗' }}</span></td>
                <td>{{ a.message || '—' }}</td>
              </tr>
              <tr v-if="!aoRecentSorted.length"><td colspan="6" class="dim">
                還沒有任何自動納管紀錄。
              </td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- VC 自動匯入（方案 B）：Windows 排程匯出 RVTools → 我抓最新檔 -->
    <template v-else-if="activeTab === 'vcauto'">
      <div class="card">
        <div class="ao-head">
          <div>
            <div class="card-title">vCenter 自動匯入</div>
            <p class="credhint" style="margin:6px 0 0">
              一台<b>常開的 Windows</b> 每晚用 <b>RVTools</b> 把 vCenter 盤點匯出到一個資料夾，
              系統就從那個資料夾<b>自動抓最新的檔</b>收進資產——我們不直接連 vCenter、不存它的帳號，
              改版風險交給 RVTools 扛。
            </p>
          </div>
          <label class="ao-switch" :class="{ on: vc.enabled }">
            <input type="checkbox" v-model="vc.enabled" />
            <span class="ao-switch-track"><span class="ao-switch-knob" /></span>
            <span class="ao-switch-label">{{ vc.enabled ? '自動匯入已啟用' : '自動匯入已關閉' }}</span>
          </label>
        </div>

        <div v-if="vcHealth && vcHealth.status !== 'off'" class="bk-hero" :class="vcHealth.status"
             style="margin-top:14px">
          <div class="bk-lamp" :class="vcHealth.status" />
          <div class="bk-hero-text">
            <div class="bk-verdict">匯入鮮度 · {{ VC_LAMP_LABEL[vcHealth.status] }}</div>
            <div class="bk-sub">{{ vcHealth.reason }}</div>
            <div v-if="vcHealth.last_at" class="bk-sub">
              上次匯入：{{ vcHealth.last_at }}（{{ vcHealth.last_result || '—' }}）
            </div>
          </div>
          <div class="bk-actions">
            <button class="btn" type="button" :disabled="vcRunning" @click="runVcAutoNow">
              {{ vcRunning ? '抓取中…' : '立即抓一次' }}
            </button>
          </div>
        </div>

        <div class="credform" style="margin-top:16px">
          <label>監看資料夾（伺服器看得到的路徑或共享）
            <input v-model="vc.dir" placeholder="例：D:\vcenter_export 或 \\NAS\vcenter" />
          </label>
          <label>逾時門檻（小時）
            <input v-model="vc.max_age_hours" type="number" min="1" />
          </label>
        </div>
        <div class="actions">
          <button class="btn" :disabled="vcSaving" @click="saveVcAuto">
            {{ vcSaving ? '儲存中…' : '儲存設定' }}
          </button>
          <button class="btn ghost" :disabled="vcRunning" @click="runVcAutoNow">
            {{ vcRunning ? '抓取中…' : '立即抓一次' }}
          </button>
        </div>

        <div class="card-title" style="margin-top:20px">那台 Windows 要設定的排程匯出</div>
        <p class="credhint" style="margin:0 0 8px">
          在那台常開的 Windows 開「工作排程器」，設一個每晚執行的工作，動作填下面這行
          （把 vCenter 位址、唯讀帳號、密碼換成實際值；資料夾要跟上面設的一致）：
        </p>
        <div class="vc-cmd">
          <code>{{ vcExportCmd }}</code>
          <button class="btn ghost small" @click="copyVcCmd">複製</button>
        </div>
        <p class="credhint" style="margin:8px 0 0">
          需先在那台裝 <b>RVTools</b>（VMware 官方認可的免費工具）。設好後每晚它自己匯出、
          系統自己抓，你完全不用手動——這一頁只是給你看「今晚到底收到了沒」。
        </p>
      </div>
    </template>

    <template v-else-if="activeTab === 'backup'">
      <p v-if="backupError" class="error-text">{{ backupError }}</p>
      <p v-if="backupLoading && !backupHealth" class="muted">檢查中…</p>

      <template v-if="backupHealth">
        <div class="bk-hero" :class="backupHealth.status">
          <div class="bk-lamp" :class="backupHealth.status" />
          <div class="bk-hero-text">
            <div class="bk-verdict">備份狀態 · {{ LAMP_LABEL[backupHealth.status] }}</div>
            <div v-if="backupHealth.reasons.length === 0" class="bk-sub">
              上次備份成功且在時限內、完整性檢查通過、空間充足。
            </div>
            <ul v-else class="bk-reasons">
              <li v-for="(r, i) in backupHealth.reasons" :key="i">{{ r }}</li>
            </ul>
          </div>
          <div class="bk-actions">
            <button class="btn" type="button" :disabled="backupRunning" @click="runBackupNow">
              {{ backupRunning ? '備份中…' : '立即備份' }}
            </button>
            <button class="btn ghost" type="button" :disabled="backupLoading" @click="loadBackupHealth">
              {{ backupLoading ? '檢查中…' : '重新檢查' }}
            </button>
          </div>
        </div>

        <div class="bk-grid">
          <div class="bk-card">
            <div class="bk-card-title">上次備份</div>
            <template v-if="backupHealth.last_backup">
              <div class="bk-big">{{ backupHealth.last_backup.modified_at }}</div>
              <div class="bk-line">
                {{ backupHealth.last_backup.age_hours }} 小時前 ·
                {{ fmtBytes(backupHealth.last_backup.size_bytes) }}
              </div>
              <div class="bk-line">
                完整性檢查
                <span :class="backupHealth.last_backup.integrity_ok ? 'ok' : 'bad'">
                  {{ backupHealth.last_backup.integrity_ok ? 'PASS' : 'FAIL' }}
                </span>
                <span v-if="!backupHealth.last_backup.integrity_ok" class="bk-detail">
                  {{ backupHealth.last_backup.integrity_detail }}
                </span>
              </div>
            </template>
            <div v-else class="bk-line bad">還沒有任何備份</div>
          </div>

          <div class="bk-card">
            <div class="bk-card-title">本地備份</div>
            <div class="bk-big">{{ backupHealth.local.count }} 份</div>
            <div class="bk-line">保留 {{ backupHealth.local.retention_days }} 天，超過自動刪除</div>
            <div class="bk-line">
              磁碟剩餘
              {{ backupHealth.local.free_mb === null ? '—' : backupHealth.local.free_mb + ' MB' }}
            </div>
            <div class="bk-path">{{ backupHealth.local.dir }}</div>
          </div>

          <div class="bk-card">
            <div class="bk-card-title">異地備份</div>
            <template v-if="backupHealth.offsite.configured">
              <div class="bk-big">{{ backupHealth.offsite.count }} 份</div>
              <div class="bk-path">{{ backupHealth.offsite.dir }}</div>
            </template>
            <template v-else>
              <div class="bk-big warn">未設定</div>
              <div class="bk-line">
                只有本地一份，磁碟壞掉就跟正本一起沒了。設定一個指到<b>另一台機器</b>的路徑
                （例如掛載 222 的 <code>/ai_backup</code>）才算真異地。
              </div>
            </template>
            <div class="bk-offsite-edit">
              <input v-model="offsiteDraft" class="bk-offsite-input"
                     placeholder="例：/mnt/ai_backup（掛載 222:/ai_backup），留空=清除" />
              <button class="btn small" :disabled="savingOffsite" @click="saveOffsite">
                {{ savingOffsite ? '儲存中…' : '儲存' }}
              </button>
            </div>
            <div class="bk-line" style="margin-top:6px">
              設好後按上方「立即備份」測一次；複製成功、有備份份數，燈就會轉綠。
              掛載步驟見 <code>AI/SOP_異地備份_掛載222.md</code>。
            </div>
          </div>

          <div class="bk-card">
            <div class="bk-card-title">資料庫本身</div>
            <div class="bk-line">
              完整性
              <span :class="backupHealth.db.integrity_ok ? 'ok' : 'bad'">
                {{ backupHealth.db.integrity_ok ? 'PASS' : 'FAIL' }}
              </span>
            </div>
            <div class="bk-line">
              日誌模式
              <span :class="backupHealth.db.journal_mode === 'wal' ? 'ok' : 'warn'">
                {{ backupHealth.db.journal_mode ?? '—' }}
              </span>
            </div>
            <div class="bk-line">大小 {{ fmtBytes(backupHealth.db.size_bytes) }}</div>
            <div class="bk-path">{{ backupHealth.db.path }}</div>
          </div>
        </div>

        <p class="bk-foot">
          檢查時間 {{ backupHealth.checked_at }}。備份用 SQLite 的 VACUUM INTO 產生一致快照
          （不是直接複製檔案，避免複製到寫入中的頁面），完成後會實際開檔跑
          integrity_check——沒驗過的備份不算備份。
        </p>
      </template>
    </template>

    <template v-else-if="activeTab === 'schedule'">
      <div class="sched-card">
        <div class="sched-row">
          <span class="sched-label">自動掃描</span>
          <label class="switch">
            <input v-model="schedule.enabled" type="checkbox" />
            <span>{{ schedule.enabled ? '啟用' : '已停用（維護時可暫停）' }}</span>
          </label>
        </div>
        <div class="sched-row">
          <span class="sched-label">頻率</span>
          <select v-model="schedule.mode" class="cell-input">
            <option value="daily">每日固定時間</option>
            <option value="interval">每隔 N 小時</option>
          </select>
        </div>
        <div v-if="schedule.mode === 'daily'" class="sched-row">
          <span class="sched-label">執行時間</span>
          <input v-model="schedule.time" type="time" class="cell-input" />
        </div>
        <div v-else class="sched-row">
          <span class="sched-label">間隔</span>
          <input v-model="schedule.interval_hours" type="number" min="1" max="168" class="cell-input narrow" />
          <span class="sched-unit">小時掃一次</span>
        </div>
        <div class="sched-actions">
          <button class="btn small" :disabled="savingSchedule" @click="saveSchedule">
            {{ savingSchedule ? '儲存中…' : '儲存排程' }}
          </button>
        </div>
        <p class="modules-hint">
          改了<b>馬上生效</b>，不用重新部署、也不用進主機改設定檔。維護當天可先「停用」暫停自動掃描，
          事後再啟用；想立刻掃一次，用儀表板右上的「重新掃描」。
        </p>
      </div>
    </template>
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
.tabs {
  display: flex;
  gap: 2px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.tab {
  padding: 8px 16px;
  font-size: 12.5px;
  color: var(--ink-soft);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab.active {
  color: var(--brand-dark);
  font-weight: 700;
  border-bottom-color: var(--brand);
}
.error-text {
  color: var(--bad);
  font-size: 13px;
  margin-bottom: 14px;
}

/* ===== S14 備份與健康 =====
   燈號用顏色 + 文字雙軌（「正常/需要注意/有問題」），不只靠顏色分辨——
   色盲使用者看不出紅綠差別時，文字仍然講得清楚。 */
.tab-lamp {
  display: inline-block;
  width: 7px; height: 7px;
  border-radius: 50%;
  margin-left: 6px;
  vertical-align: middle;
}
.tab-lamp.green { background: #26a889; }
.tab-lamp.yellow { background: #d99a2b; }
.tab-lamp.red { background: #d9534f; }

.bk-hero {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 18px 20px;
  border-radius: 12px;
  border: 1px solid var(--border-strong);
  border-left-width: 4px;
  margin-bottom: 18px;
  flex-wrap: wrap;
}
.bk-hero.green { border-left-color: #26a889; }
.bk-hero.yellow { border-left-color: #d99a2b; }
.bk-hero.red { border-left-color: #d9534f; }
.bk-lamp {
  width: 15px; height: 15px;
  border-radius: 50%;
  margin-top: 4px;
  flex-shrink: 0;
}
.bk-lamp.green { background: #26a889; }
.bk-lamp.yellow { background: #d99a2b; }
.bk-lamp.red { background: #d9534f; }
.bk-hero-text { flex: 1; min-width: 240px; }
.bk-verdict { font-size: 15px; font-weight: 700; margin-bottom: 5px; }
.bk-sub { font-size: 13px; opacity: 0.8; }
.bk-reasons { margin: 4px 0 0; padding-left: 18px; font-size: 13px; line-height: 1.7; }
.bk-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

.bk-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px;
}
.bk-card {
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 14px 16px;
}
.bk-card-title { font-size: 12px; opacity: 0.65; margin-bottom: 8px; }
.bk-big { font-size: 19px; font-weight: 700; margin-bottom: 6px; }
.bk-big.warn { color: #b8791d; }
.bk-line { font-size: 12.5px; line-height: 1.7; }
.bk-line .ok { color: #26a889; font-weight: 700; }
.bk-line .bad { color: #d9534f; font-weight: 700; }
.bk-line .warn { color: #b8791d; font-weight: 700; }
.bk-line.bad { color: #d9534f; }
.bk-detail { display: block; opacity: 0.7; font-size: 11.5px; }
.bk-path {
  margin-top: 8px;
  font-size: 11.5px;
  opacity: 0.55;
  word-break: break-all;
}
.bk-foot { font-size: 12px; opacity: 0.65; line-height: 1.7; margin-top: 16px; }
.bk-foot code { font-size: 11.5px; }
.muted { opacity: 0.7; font-size: 14px; }
.notice-banner {
  background: var(--warn-soft);
  color: var(--warn);
  border: 1px solid var(--border-strong);
  padding: 8px 14px;
  font-size: 12.5px;
  margin-bottom: 14px;
}
.modules-hint {
  font-size: 12px;
  color: var(--muted);
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
  min-width: 720px;
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
.tgl { font-family: inherit; font-size: 11.5px; font-weight: 700; padding: 3px 10px;
  border-radius: 999px; cursor: pointer; border: 1px solid var(--border-strong);
  background: transparent; color: var(--muted); }
.tgl.on { border-color: var(--good); color: var(--good); background: var(--good-soft); }
.tgl:disabled { opacity: .5; cursor: default; }
tr.off { opacity: .55; }
.credhint { font-size: 11.5px; color: var(--muted); line-height: 1.7; margin: 0 0 14px; }
.credhint code { color: var(--brand); }
.credhint b { color: var(--ink-soft); }
.lockchip { font-size: 11px; color: var(--good); background: var(--good-soft); padding: 2px 8px; border-radius: 4px; }
.credform { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px 14px; }
.credform label { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--muted); }
.credform input, .credform select { font-family: inherit; font-size: 13px; padding: 7px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.btn.danger { background: var(--bad); }
.cell-input {
  width: 130px;
  padding: 5px 8px;
  border: 1px solid var(--border-strong);
  font-family: inherit;
  font-size: 12.5px;
  background: var(--card);
  color: var(--ink);
}
.cell-input.narrow {
  width: 55px;
}
.muted-cell {
  color: var(--muted);
}
.actions-cell {
  white-space: nowrap;
  display: flex;
  gap: 6px;
}
.btn {
  font-family: inherit;
  font-weight: 700;
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
.btn.small {
  padding: 5px 10px;
  font-size: 11.5px;
}
.status-dot {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 700;
}
.status-dot .d {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.status-dot.green {
  color: var(--good);
}
.status-dot.green .d {
  background: var(--good);
}
.status-dot.red {
  color: var(--bad);
}
.status-dot.red .d {
  background: var(--bad);
}
.status-dot.gray {
  color: var(--muted);
}
.status-dot.gray .d {
  background: var(--muted);
}
.diagram-placeholder {
  border: 1px solid var(--border);
  background: var(--card);
  padding: 40px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}
.diagram-wrap {
  border: 1px solid var(--border);
  background: var(--card);
  padding: 18px;
}
.node {
  fill: var(--mint);
  stroke: var(--border-strong);
  stroke-width: 1.4;
}
.node-label {
  font-size: 11px;
  fill: var(--ink);
  font-weight: 700;
}
.node-sub {
  font-size: 9px;
  fill: var(--muted);
}
.edge-green {
  stroke: var(--good);
  stroke-width: 2.4;
}
.edge-red {
  stroke: var(--bad);
  stroke-width: 2.4;
  stroke-dasharray: 5 3;
}
.edge-gray {
  stroke: var(--muted);
  stroke-width: 2;
  stroke-dasharray: 2 3;
}
.edge-label {
  font-size: 9.5px;
  fill: var(--ink-soft);
}
.legend-list {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  font-size: 12px;
  color: var(--ink-soft);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.legend-list .mk {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  color: #fff;
  font-size: 9.5px;
  font-weight: 700;
  flex-shrink: 0;
  margin-right: 7px;
}
.legend-list .row-l {
  display: flex;
  align-items: center;
}
.sched-card {
  border: 1px solid var(--border);
  background: var(--card);
  padding: 20px 22px;
  max-width: 460px;
}
.sched-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.sched-label {
  width: 84px;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink-soft);
  flex-shrink: 0;
}
.sched-unit {
  font-size: 12.5px;
  color: var(--ink-soft);
}
.switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--ink);
  cursor: pointer;
}
.sched-actions {
  margin: 4px 0 14px;
}

/* B：排程自動納管 */
.ao-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; flex-wrap: wrap; }
.ao-switch { display: inline-flex; align-items: center; gap: 9px; cursor: pointer; flex-shrink: 0; }
.ao-switch input { position: absolute; opacity: 0; width: 0; height: 0; }
.ao-switch-track {
  width: 44px; height: 24px; border-radius: 999px; background: var(--line, #33414f);
  position: relative; transition: background 0.18s; flex-shrink: 0;
}
.ao-switch-knob {
  position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%;
  background: #fff; transition: transform 0.18s;
}
.ao-switch.on .ao-switch-track { background: #26a889; }
.ao-switch.on .ao-switch-knob { transform: translateX(20px); }
.ao-switch-label { font-size: 12.5px; color: var(--ink-soft, #cbd5e1); font-weight: 600; }
.ao-run {
  display: flex; justify-content: space-between; align-items: center; gap: 20px; flex-wrap: wrap;
  margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--line, #2a3642);
}
.ao-result { margin-top: 12px; font-size: 13px; color: var(--ink-soft, #cbd5e1); }
.ao-result b { color: var(--brand, #26a889); }
.ao-detail { margin: 8px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.75; }
.ao-detail .ok { color: #26a889; font-weight: 700; }
.ao-detail .bad { color: #d9534f; font-weight: 700; }
.ao-detail .dim { color: var(--muted); }
td .ok { color: #26a889; font-weight: 700; }
td .bad { color: #d9534f; font-weight: 700; }
.vc-cmd {
  display: flex; align-items: center; gap: 10px;
  background: var(--code-bg, #0d1b26); border: 1px solid var(--line, #2a3642);
  border-radius: 6px; padding: 10px 12px; overflow-x: auto;
}
.vc-cmd code {
  flex: 1; font-family: 'Space Grotesk', ui-monospace, monospace; font-size: 12px;
  color: var(--brand, #26a889); white-space: nowrap;
}
.vc-cmd .btn { flex-shrink: 0; }
.bk-offsite-edit { display: flex; gap: 8px; margin-top: 10px; }
.bk-offsite-input {
  flex: 1; min-width: 0; font-family: inherit; font-size: 12px; padding: 6px 9px;
  border: 1px solid var(--line, #33414f); border-radius: 5px;
  background: var(--card, #12212c); color: var(--ink, #e6edf3);
}
</style>
