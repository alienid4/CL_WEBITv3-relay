<script setup lang="ts">
// ⚙ 開發工具 › 功能模組管理（2.056.3，V56 第 4 批；原舊 /settings 的 modules 分頁）。⚠D5（停用登入、寫入密碼保護）純搬移：
//   · 同一組 API：GET／PUT /api/admin/auth-disabled、GET／PUT /api/admin/write-guard、功能開關（useFeatureFlags：GET 清單、setEnabled）。
//   · 停用登入：打開要打字 DISABLE-LOGIN、關掉直接按（安全的方向比較好走）；伺服器環境變數停用時這裡關不掉；改完重新整理。
//   · 寫入密碼保護：關掉（目前開著）或在登入停用中打開，都要先輸入 admin 密碼（window.prompt，送出後不留存）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const { flags, ensureLoaded, setEnabled } = useFeatureFlags()
const errMsg = (e: any, d: string) => { const x = e?.data?.detail; return typeof x === 'string' ? x : x?.message ?? d }

const authOff = ref<{ disabled: boolean; by_env: boolean; env_name: string } | null>(null)
const authOffConfirm = ref('')
const authOffBusy = ref(false)
const wGuard = ref<{ enabled: boolean; auth_disabled: boolean } | null>(null)
const loadErr = ref('')
async function loadAuthOff() {
  try { authOff.value = await apiFetch<any>('/api/admin/auth-disabled') } catch (e: any) { loadErr.value = `停用登入狀態讀取失敗：${errMsg(e, '')}` }
}
async function loadWGuard() {
  try { wGuard.value = await apiFetch<any>('/api/admin/write-guard') } catch (e: any) { loadErr.value = `寫入保護狀態讀取失敗：${errMsg(e, '')}` }
}
async function setAuthOff(enabled: boolean) {
  authOffBusy.value = true
  try {
    await apiFetch<any>('/api/admin/auth-disabled', { method: 'PUT', body: { enabled, confirm: enabled ? authOffConfirm.value : undefined } })
    authOffConfirm.value = ''
    await loadAuthOff()
    showToast(enabled ? '登入已停用——請記得測試完關回來' : '登入已恢復', enabled ? 'warn' : 'success', 10000)
    setTimeout(() => window.location.reload(), 800)            // 重新整理才會套用（守衛與橫幅都在載入時判斷）
  } catch (e: any) { showToast(errMsg(e, '設定失敗'), 'error', 8000) } finally { authOffBusy.value = false }
}
async function setWGuard(on: boolean) {
  let password: string | undefined
  if ((!on && wGuard.value?.enabled) || (on && wGuard.value?.auth_disabled)) {
    const pw = window.prompt(on ? '打開前先輸入一次管理密碼（admin 帳號），確定你記得它：' : '關掉這個保護要輸入管理密碼（admin 帳號）：')
    if (!pw) return
    password = pw
  }
  try {
    await apiFetch('/api/admin/write-guard', { method: 'PUT', body: { enabled: on, password } })
    await loadWGuard()
    showToast(on ? '已打開：登入停用時，寫入動作要輸入管理密碼' : '已關閉', 'success')
  } catch (e: any) { showToast(errMsg(e, '設定失敗'), 'error', 8000) }
}
const togglingKey = ref<string | null>(null)
const flagErr = ref('')
async function toggleModule(moduleKey: string, enabled: boolean) {
  togglingKey.value = moduleKey
  flagErr.value = ''
  try { await setEnabled(moduleKey, enabled) } catch (err: any) { flagErr.value = errMsg(err, '切換失敗') } finally { togglingKey.value = null }
}
const disabledModuleLabel = computed(() => {
  const key = route.query.disabled as string | undefined
  if (!key) return null
  return flags.value.find((f: any) => f.module_key === key)?.label ?? key
})
const rows = computed(() => flags.value.map((f: any, i: number) => ({ ...f, __no: i + 1, state: f.enabled ? '已啟用' : '已停用' })))
onMounted(() => { ensureLoaded(); loadAuthOff(); loadWGuard() })
</script>

<template>
  <div>
    <p v-if="disabledModuleLabel" class="warnline">「{{ disabledModuleLabel }}」目前停用中，要用請在下面啟用。</p>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <div v-if="authOff" class="card">
      <div class="ct">停用登入（測試用）
        <InfoNote>打開之後不需要登入就能使用整套系統——包含所有資產、人員姓名與分機。任何連得到這個網址的人都看得到。打開要打字確認、關掉直接按；只有已經登入的人打得開。開著的時候畫面最上面會有一條關不掉的紅色警告條。</InfoNote></div>
      <p v-if="authOff.by_env" class="muted">目前由伺服器的環境變數 <code>{{ authOff.env_name }}</code> 停用中——這個畫面關不掉它，要到伺服器移除該變數並重啟服務。</p>
      <div v-else class="bar">
        <template v-if="authOff.disabled">
          <span class="bad">登入目前是停用狀態</span>
          <button type="button" class="btn" :disabled="authOffBusy" @click="setAuthOff(false)">恢復登入</button>
        </template>
        <template v-else>
          <span class="muted">登入正常運作</span>
          <input v-model="authOffConfirm" class="in" placeholder="輸入 DISABLE-LOGIN" :disabled="authOffBusy" aria-label="確認文字">
          <button type="button" class="btn" :disabled="authOffBusy || authOffConfirm !== 'DISABLE-LOGIN'" @click="setAuthOff(true)">停用登入</button>
        </template>
      </div>
    </div>
    <div v-if="wGuard" class="card">
      <div class="ct">登入停用時，寫入動作要輸入管理密碼
        <InfoNote>登入停用時，任何人都能在機櫃圖上拖拉搬移、調高度、合併／拆開、標「已不在櫃上」、位置更正、照片對帳確認／判讀錯了。打開之後這些動作都要輸入 admin 帳號的密碼（伺服器用既有的密碼雜湊驗證，不存明文；同一來源 10 分鐘錯 5 次先擋 10 分鐘）。登入正常時這個設定不起作用。不管開不開，每一筆都會記下是誰和來源 IP。</InfoNote></div>
      <div class="bar">
        <span class="muted">目前：<b>{{ wGuard.enabled ? '打開' : '關閉' }}</b>{{ wGuard.auth_disabled ? '（登入停用中）' : '（登入正常，這個設定不起作用）' }}</span>
        <button type="button" class="chip" @click="setWGuard(!wGuard.enabled)">{{ wGuard.enabled ? '關閉' : '打開' }}</button>
      </div>
    </div>
    <p v-if="flagErr" class="err">{{ flagErr }}</p>
    <V2Table :rows="rows" prefix="M" :max="200" empty="沒有功能模組"
             :cols="[{ k: 'label', label: '模組' }, { k: 'state', label: '狀態' }, { k: 'ops', label: '操作', text: () => '' }]">
      <template #cell-ops="{ row }">
        <button type="button" class="chip" :disabled="togglingKey === row.module_key" @click="toggleModule(row.module_key, !row.enabled)">{{ togglingKey === row.module_key ? '處理中…' : row.enabled ? '停用' : '啟用' }}</button>
      </template>
    </V2Table>
  </div>
</template>

<style scoped>
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; margin-bottom: 12px; }
.ct { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 6px; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); max-width: 220px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.muted { color: var(--muted); font-size: 13px; }
.bad, .err, .warnline { color: var(--bad); font-size: 13px; }
</style>
