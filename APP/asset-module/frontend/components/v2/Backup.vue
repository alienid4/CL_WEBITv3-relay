<script setup lang="ts">
// ⚙ 系統設定「備份與還原」頁籤（2.056.2，V56 第 3 批；原舊 /settings 的 backup 分頁）。⚠D4（還原整庫覆蓋）＋D5（匯出金鑰），純搬移：
//   · 同一組 API：GET /api/backup/status、POST /api/backup/run、PUT /api/backup/offsite、GET /api/backup/dump（fetch 下載）、
//     POST /api/backup/restore（fetch 上傳）、GET /api/export-key、POST /api/export-key/generate、PUT /api/export-key/recipient、
//     PUT /api/export-key/encryption。
//   · 還原：照舊只有功能開關 restore 開著才出現；要選 .db 檔＋輸入「覆蓋還原」才按得下去；成功（或 401＝上一次已成功）
//     3 秒後送去重新登入（登入資料存在同一個資料庫，整庫覆蓋會一併換掉）。後端照舊先存證覆蓋前的資料。
//   · 私鑰永遠不經畫面（只顯示公鑰與指紋）；已有私鑰時後端拒絕重新產生（不覆蓋）。
interface BackupHealth {
  status: 'green' | 'yellow' | 'red'; reasons: string[]; checked_at: string
  db: { path: string; exists: boolean; integrity_ok: boolean; integrity_detail: string; journal_mode: string | null; size_bytes: number }
  last_backup: { name: string; size_bytes: number; modified_at: string; age_hours: number | null; integrity_ok: boolean | null; integrity_detail: string } | null
  local: { dir: string; count: number; free_mb: number | null; retention_days: number }
  offsite: { configured: boolean; dir: string | null; count: number }
}
interface ExportKeyStatus {
  is_recipient: boolean; private_key_path: string; my_public_key: string | null; my_fingerprint: string | null
  recipient_fingerprint: string | null; encryption_enabled?: boolean; can_export: boolean
}
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isEnabled, ensureLoaded } = useFeatureFlags()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any, d: string) => { const x = e?.data?.detail; return typeof x === 'string' ? x : x?.message ?? e?.message ?? d }

const backupHealth = ref<BackupHealth | null>(null)
const backupLoading = ref(false)
const backupRunning = ref(false)
const backupError = ref('')
const LAMP_LABEL: Record<string, string> = { green: '正常', yellow: '需要注意', red: '有問題' }
function fmtBytes(n: number | null | undefined) {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
async function loadBackupHealth() {
  backupLoading.value = true
  backupError.value = ''
  try { backupHealth.value = await apiFetch<BackupHealth>('/api/backup/status') } catch (err: any) { backupError.value = errMsg(err, '備份狀態載入失敗，請稍後再試') } finally { backupLoading.value = false }
}
async function runBackupNow() {
  backupRunning.value = true
  backupError.value = ''
  try {
    const r = await apiFetch<any>('/api/backup/run', { method: 'POST' })
    if (r.ok) {
      const extra = r.offsite_path ? '（含異地副本）' : r.offsite_error ? `（異地副本失敗：${r.offsite_error}）` : ''
      showToast(`備份完成 · ${fmtBytes(r.size_bytes)} · 完整性 PASS ${extra}`, r.offsite_error ? 'warn' : 'success')
    } else {
      backupError.value = r.message ?? r.error ?? '備份失敗'          // 後端刻意用 200 回失敗細節，原樣顯示
      showToast(backupError.value, 'error')
    }
    await loadBackupHealth()
  } catch (err: any) {
    backupError.value = errMsg(err, '備份執行失敗，請稍後再試')
    showToast(backupError.value, 'error')
  } finally { backupRunning.value = false }
}
const offsiteDraft = ref('')
const savingOffsite = ref(false)
watch(backupHealth, (h) => { if (h) offsiteDraft.value = h.offsite.dir ?? '' })
async function saveOffsite() {
  savingOffsite.value = true
  try {
    backupHealth.value = await apiFetch<BackupHealth>('/api/backup/offsite', { method: 'PUT', body: { dir: offsiteDraft.value.trim() } })
    showToast(offsiteDraft.value.trim() ? '已設定異地備份路徑' : '已清除異地備份路徑', 'success')
  } catch (err: any) { showToast(`儲存失敗：${errMsg(err, '請稍後重試')}`, 'error') } finally { savingOffsite.value = false }
}

// ===== 匯出加密金鑰（決策 SEC5）=====
const keyStatus = ref<ExportKeyStatus | null>(null)
const keyErr = ref('')
const recipientInput = ref('')
const keyBusy = ref(false)
async function loadKeyStatus() {
  try { keyStatus.value = await apiFetch<ExportKeyStatus>('/api/export-key'); keyErr.value = '' } catch (e: any) { keyErr.value = `匯出金鑰狀態載入失敗：${errMsg(e, '')}` }
}
async function toggleEncryption(on: boolean) {
  keyBusy.value = true
  try {
    await apiFetch('/api/export-key/encryption', { method: 'PUT', body: { enabled: on } })
    showToast(on ? '匯出時加密：已開啟（需設定收件人公鑰）' : '匯出時加密：已關閉', 'success')
    await loadKeyStatus()
  } catch (err: any) { showToast(`切換失敗：${errMsg(err, String(err))}`, 'error') } finally { keyBusy.value = false }
}
async function genKeypair() {
  if (keyBusy.value) return
  keyBusy.value = true
  try {
    const r = await apiFetch<any>('/api/export-key/generate', { method: 'POST' })
    await loadKeyStatus()
    showToast(`金鑰已產生，指紋 ${r.fingerprint}。私鑰留在這台，公鑰複製到來源端。`, 'success', 10000)
  } catch (e: any) { showToast(errMsg(e, '產生失敗'), 'error', 10000) } finally { keyBusy.value = false }   // 已有私鑰時後端拒絕（不覆蓋）
}
async function saveRecipient() {
  if (keyBusy.value) return
  keyBusy.value = true
  try {
    const r = await apiFetch<any>('/api/export-key/recipient', { method: 'PUT', body: { public_key: recipientInput.value } })
    recipientInput.value = ''
    await loadKeyStatus()
    showToast(`收件人公鑰已設定，指紋 ${r.fingerprint}`, 'success')
  } catch (e: any) { showToast(errMsg(e, '設定失敗'), 'error', 10000) } finally { keyBusy.value = false }
}

// ===== 匯出全部資料（下載到自己的電腦）=====
const dumpFormat = ref<'binary' | 'sql' | 'lite'>('binary')
const dumpSplit = ref(false)
const dumpRunning = ref(false)
async function downloadDump() {
  dumpRunning.value = true
  try {
    const splitQ = dumpSplit.value ? '&split_mb=10' : ''
    const res = await fetch(`${apiBase}/api/backup/dump?fmt=${dumpFormat.value}${splitQ}`, { credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const blob = await res.blob()
    const m = (res.headers.get('Content-Disposition') || '').match(/filename="?([^"]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m ? m[1] : (dumpSplit.value ? 'asset_dump_split10MB.zip' : `asset_dump.${dumpFormat.value === 'sql' ? 'sql' : dumpFormat.value === 'lite' ? 'zip' : 'db'}`)
    a.click()
    URL.revokeObjectURL(url)
    showToast(`已匯出並加密（指紋 ${keyStatus.value?.recipient_fingerprint ?? '—'}）` + (dumpSplit.value ? '，已切成 10MB 一片打包成 zip' : ''), 'success', 8000)
  } catch (err: any) { showToast(`匯出失敗：${err?.message ?? '請稍後重試'}`, 'error') } finally { dumpRunning.value = false }
}

// ===== 資料庫還原（整庫覆蓋；功能開關 restore 開著才出現）=====
const restoreFile = ref<File | null>(null)
const restoreConfirmText = ref('')
const restoreRunning = ref(false)
const RESTORE_CONFIRM_WORD = '覆蓋還原'
function onRestoreFileChange(e: Event) { restoreFile.value = (e.target as HTMLInputElement).files?.[0] ?? null }
async function runRestore() {
  if (!restoreFile.value) { showToast('請先選一個 .db 檔案', 'warn'); return }
  if (restoreConfirmText.value !== RESTORE_CONFIRM_WORD) { showToast(`請在確認欄位輸入「${RESTORE_CONFIRM_WORD}」才能執行`, 'warn'); return }
  restoreRunning.value = true
  try {
    const form = new FormData()
    form.append('file', restoreFile.value)
    form.append('confirm', restoreConfirmText.value)   // 2.056.5：伺服器也檢查確認字（不再只靠畫面）
    const res = await fetch(`${apiBase}/api/backup/restore`, { method: 'POST', credentials: 'include', body: form })
    if (!res.ok) {
      // 401 幾乎一定代表「上一次還原已經成功、把登入資料一起換掉了」——不報成還原失敗，直接送去重新登入
      if (res.status === 401) {
        showToast('登入階段已失效。這通常代表上一次還原「已經成功」，而登入資料存在同一個資料庫裡、被一併覆蓋了。請用上傳檔裡的帳號密碼重新登入，再確認資料是否已是你要的那份。', 'warn', 15000)
        setTimeout(() => { navigateTo('/login?reason=restored') }, 3000)
        return
      }
      // 413／502 這類是反向代理回的 HTML：讀 text() 把狀態碼與伺服器講的話帶出來
      const raw = await res.text().catch(() => '')
      let detail = ''
      try { detail = JSON.parse(raw)?.detail ?? '' } catch { detail = raw.slice(0, 300) }
      throw new Error(detail ? `${detail}（HTTP ${res.status}）` : `HTTP ${res.status} ${res.statusText}`)
    }
    const body = await res.json()
    restoreFile.value = null
    restoreConfirmText.value = ''
    showToast(`✅ 還原成功。覆蓋前的舊資料已存證：${body.pre_restore_backup ?? '（無，這是首次建庫）'}。　登入資料被一併覆蓋，3 秒後跳轉登入頁，需要重新登入。`, 'success', 8000)
    setTimeout(() => { navigateTo('/login?reason=restored') }, 3000)
  } catch (err: any) {
    const msg = err?.message || '連線中斷或伺服器沒有回應（請確認後端服務還在，以及檔案是否超過上傳大小限制）'
    showToast(`還原失敗：${msg}`, 'error', 30000)          // 30 秒：不可逆操作出錯時唯一的線索
  } finally { restoreRunning.value = false }
}

onMounted(() => { loadBackupHealth(); loadKeyStatus(); ensureLoaded() })
</script>

<template>
  <div>
    <p v-if="backupError" class="err">{{ backupError }}</p>
    <p v-if="backupLoading && !backupHealth" class="muted">檢查中…</p>
    <p v-if="!backupHealth && !backupLoading && !backupError" class="muted">尚未檢查備份狀態 · <button type="button" class="chip" @click="loadBackupHealth">立即檢查</button></p>

    <template v-if="backupHealth">
      <div class="hero" :class="backupHealth.status">
        <div class="lamp" :class="backupHealth.status" />
        <div class="htext">
          <div class="verdict">備份狀態 · {{ LAMP_LABEL[backupHealth.status] }} <InfoNote>備份用 SQLite 的 VACUUM INTO 產生一致快照（不是直接複製檔案），完成後會實際開檔跑 integrity_check——沒驗過的備份不算備份。</InfoNote></div>
          <div v-if="backupHealth.reasons.length === 0" class="muted">上次備份成功且在時限內、完整性檢查通過、空間充足。</div>
          <ul v-else class="reasons"><li v-for="(r, i) in backupHealth.reasons" :key="i">{{ r }}</li></ul>
        </div>
        <div class="bar">
          <button type="button" class="btn" :disabled="backupRunning" @click="runBackupNow">{{ backupRunning ? '備份中…' : '立即備份' }}</button>
          <button type="button" class="chip" :disabled="backupLoading" @click="loadBackupHealth">{{ backupLoading ? '檢查中…' : '重新檢查' }}</button>
        </div>
      </div>

      <div class="grid">
        <div class="card">
          <div class="ct">上次備份</div>
          <template v-if="backupHealth.last_backup">
            <div class="big">{{ backupHealth.last_backup.modified_at }}</div>
            <div class="line">{{ backupHealth.last_backup.age_hours }} 小時前 · {{ fmtBytes(backupHealth.last_backup.size_bytes) }}</div>
            <div class="line">完整性檢查 <b :class="backupHealth.last_backup.integrity_ok ? 'ok' : 'bad'">{{ backupHealth.last_backup.integrity_ok ? 'PASS' : 'FAIL' }}</b>
              <span v-if="!backupHealth.last_backup.integrity_ok" class="muted">{{ backupHealth.last_backup.integrity_detail }}</span></div>
          </template>
          <div v-else class="line bad">還沒有任何備份</div>
        </div>
        <div class="card">
          <div class="ct">本地備份</div>
          <div class="big">{{ backupHealth.local.count }} 份</div>
          <div class="line">保留 {{ backupHealth.local.retention_days }} 天，超過自動刪除</div>
          <div class="line">磁碟剩餘 {{ backupHealth.local.free_mb === null ? '—' : backupHealth.local.free_mb + ' MB' }}</div>
          <div class="path">{{ backupHealth.local.dir }}</div>
        </div>
        <div class="card">
          <div class="ct">異地備份 <InfoNote>只有本地一份，磁碟壞掉就跟正本一起沒了。設定一個指到另一台機器的路徑（例如掛載另一台的備份資料夾）才算真異地。設好後按「立即備份」測一次；複製成功、有備份份數，燈就會轉綠。</InfoNote></div>
          <div v-if="backupHealth.offsite.configured" class="big">{{ backupHealth.offsite.count }} 份</div>
          <div v-else class="big warn">未設定</div>
          <div v-if="backupHealth.offsite.configured" class="path">{{ backupHealth.offsite.dir }}</div>
          <div class="bar"><input v-model="offsiteDraft" class="in" placeholder="例：/mnt/ai_backup，留空＝清除" aria-label="異地備份路徑">
            <button type="button" class="chip" :disabled="savingOffsite" @click="saveOffsite">{{ savingOffsite ? '儲存中…' : '儲存' }}</button></div>
        </div>
        <div class="card">
          <div class="ct">資料庫本身</div>
          <div class="line">完整性 <b :class="backupHealth.db.integrity_ok ? 'ok' : 'bad'">{{ backupHealth.db.integrity_ok ? 'PASS' : 'FAIL' }}</b></div>
          <div class="line">日誌模式 <b :class="backupHealth.db.journal_mode === 'wal' ? 'ok' : 'warn'">{{ backupHealth.db.journal_mode ?? '—' }}</b></div>
          <div class="line">大小 {{ fmtBytes(backupHealth.db.size_bytes) }}</div>
          <div class="path">{{ backupHealth.db.path }}</div>
        </div>
      </div>
      <p class="muted">檢查時間 {{ backupHealth.checked_at }}</p>
    </template>

    <div class="card wide">
      <div class="ct">匯出加密金鑰 <InfoNote>匯出檔會離開這台機器（下載→email），裡面有全公司主機清單、IP、主機名、帳號名與業務系統歸屬，所以可以加密，只有持私鑰的那台解得開。公鑰不需要保密（只能加密、不能解密）。收件端按「產生金鑰對」，把公鑰複製到來源端貼上就完成；指紋拿來核對兩邊貼對了沒有。私鑰弄丟＝過去所有加密匯出永遠打不開；已有私鑰時會拒絕、不覆蓋。</InfoNote></div>
      <p v-if="keyErr" class="err">{{ keyErr }}</p>
      <label v-if="keyStatus" class="bar"><input type="checkbox" :checked="!!keyStatus.encryption_enabled" :disabled="keyBusy" @change="toggleEncryption(($event.target as HTMLInputElement).checked)">
        匯出時加密：<b>{{ keyStatus.encryption_enabled ? '開' : '關' }}</b></label>
      <div v-if="keyStatus && keyStatus.encryption_enabled" class="bar">
        <template v-if="keyStatus.is_recipient">
          <span class="muted">本機公鑰指紋 <code>{{ keyStatus.my_fingerprint }}</code></span>
          <input class="in mono" :value="keyStatus.my_public_key" readonly aria-label="本機公鑰" @focus="($event.target as HTMLInputElement).select()">
        </template>
        <button v-else type="button" class="btn" :disabled="keyBusy" @click="genKeypair">{{ keyBusy ? '產生中…' : '產生金鑰對' }}</button>
      </div>
      <div v-if="keyStatus && keyStatus.encryption_enabled" class="bar">
        <span class="muted">收件人公鑰</span>
        <input v-model="recipientInput" class="in" :placeholder="keyStatus.recipient_fingerprint ? `已設定（指紋 ${keyStatus.recipient_fingerprint}）` : '貼上收件端的公鑰'" :disabled="keyBusy" aria-label="收件人公鑰" @keyup.enter="saveRecipient">
        <button type="button" class="chip" :disabled="keyBusy || !recipientInput" @click="saveRecipient">儲存</button>
      </div>
    </div>

    <div class="card wide">
      <div class="ct">匯出全部資料（下載到你的電腦） <InfoNote>跟上面的備份不同：這是直接下載一份完整資料的複本到你的電腦，用於搬機／備份轉移。伺服器上的備份不加密；這份會離開機器——「匯出時加密」開著就加密（順便壓縮）；關著就是原始檔，每次匯出記一筆操作紀錄。勾「切成 10MB 一片」會打包成 zip，解壓後逐片寄信（內附併回說明）。</InfoNote></div>
      <div class="bar">
        <select v-model="dumpFormat" class="in" aria-label="匯出格式">
          <option value="binary">二進位資料庫（.db）</option>
          <option value="sql">文字 SQL（.sql）</option>
          <option value="lite">二進位資料庫・壓縮（.zip，約小 8 倍）</option>
        </select>
        <button type="button" class="btn" :disabled="dumpRunning" @click="downloadDump">{{ dumpRunning ? '匯出中…' : '下載' }}</button>
        <label><input v-model="dumpSplit" type="checkbox"> 切成 10MB 一片（給 email）</label>
      </div>
    </div>

    <div v-if="isEnabled('restore')" class="card wide danger" data-test="restore">
      <div class="ct dangerT">資料庫還原（整庫覆蓋）
        <InfoNote>上傳一份先前「匯出全部資料」下載的檔案（加密的 .enc 或伺服器備份的 .db），會整個覆蓋掉現在的正式資料庫。不可逆；覆蓋前系統會自動先把現有資料存證一份到伺服器本機備份目錄，但那是最後防線，不是反悔按鈕。帳號與登入階段也在同一個資料庫，所以還原成功後會被登出，請改用上傳檔中的帳號密碼重新登入。</InfoNote></div>
      <!-- critical 例外：不可逆，看不到會出事，留一句在畫面上 -->
      <p class="warnline">此動作不可逆，會整庫覆蓋並登出。</p>
      <div class="bar">
        <input type="file" accept=".db" aria-label="還原檔" @change="onRestoreFileChange">
        <input v-model="restoreConfirmText" class="in" :placeholder="`輸入「${RESTORE_CONFIRM_WORD}」以啟用按鈕`" aria-label="確認文字">
        <button type="button" class="btn dangerB" :disabled="restoreRunning || !restoreFile || restoreConfirmText !== RESTORE_CONFIRM_WORD" @click="runRestore">{{ restoreRunning ? '還原中…' : '覆蓋還原' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0; font-size: 13px; }
.hero { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; padding: 12px 16px; border: 1px solid var(--border); border-left: 4px solid var(--border-strong); border-radius: var(--radius); background: var(--card); margin-bottom: 12px; }
.hero.green { border-left-color: var(--good); } .hero.yellow { border-left-color: var(--warn); } .hero.red { border-left-color: var(--bad); }
.lamp { width: 14px; height: 14px; border-radius: 50%; background: var(--muted); }
.lamp.green { background: var(--good); } .lamp.yellow { background: var(--warn); } .lamp.red { background: var(--bad); }
.htext { flex: 1; min-width: 240px; }
.verdict { font-size: 15px; font-weight: 700; margin-bottom: 4px; }
.reasons { margin: 4px 0 0; padding-left: 18px; font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; margin-bottom: 8px; }
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; }
.card.wide { margin: 12px 0; }
.card.danger { border-color: var(--bad); }
.ct { font-size: 12.5px; color: var(--ink-aux); margin-bottom: 6px; }
.dangerT { color: var(--bad); font-weight: 700; }
.warnline { color: var(--bad); font-size: 13px; margin: 4px 0; }
.big { font-family: var(--disp); font-size: 18px; font-weight: 700; margin-bottom: 4px; }
.big.warn { color: var(--warn-text); }
.line { font-size: 12.5px; line-height: 1.7; }
.line.bad, .bad { color: var(--bad); } .ok { color: var(--brand-dark); } .warn { color: var(--warn-text); }
.path { font-size: 11.5px; color: var(--muted); word-break: break-all; margin-top: 4px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 220px; }
.in.mono { font-family: var(--disp); font-size: 12px; flex: 1; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.btn.dangerB { border-color: var(--bad); background: var(--bad); }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
</style>
