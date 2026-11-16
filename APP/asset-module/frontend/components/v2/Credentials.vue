<script setup lang="ts">
// ⚙ 系統設定「收集憑證」頁籤（2.056.0，V56 第 1 批；原舊 /settings 的 credentials 分頁「收集用憑證」那一張卡）。⚠D5 純搬移：
//   · 同一組 API：GET／POST /api/credentials、PUT／DELETE /api/credentials/{名稱}、POST /api/credentials/{名稱}/test。
//   · 後端從不回傳密碼（連密文都不給，只回 has_secret）；畫面上的密碼欄只在送出那一次存在，送完（成功或失敗）立刻清空。
//   · 編輯：名稱與類型不能改；密碼留空＝沿用原本的（送 null）。刪除：仍被引用時後端回 409＋引用清單，照列出來。
//   · 測試：WinRM 要指定一台 IP；其他類型按了直接測（後端限速、失敗 3 次先停）。
// 收集金鑰（Linux）、收集帳號備註、Windows 正式組態檢核開關還在「收集身分 › 收集金鑰與備註」（舊頁，下一批）。
interface CredRow {
  id: number; name: string; kind: string; username: string
  scope: string | null; note: string | null; updated_at: string; has_secret: boolean
  refs?: { type: string; label: string; link: string }[]; ref_count?: number
  last_used_at?: string | null; last_used_ok?: boolean | null
}
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '請稍後重試' }

const creds = ref<CredRow[]>([])
const loadErr = ref('')
const newCred = reactive({ name: '', kind: 'winrm', username: '', password: '', scope: '', note: '' })
const savingCred = ref(false)
const KIND_HINT: Record<string, string> = {
  winrm: 'Windows 排程收集用的服務帳號。適用範圍填網段前綴，同網段自動套用。',
  ssh: 'Linux／AIX 自動納管 bootstrap 用的帳號，納管時從清單挑選。',
  whatsup: 'WhatsUp Gold API 帳號。到「外部整合 → WhatsUp Gold」挑這一筆；只用來讀告警，不改 WhatsUp 設定。',
  vrops: 'Aria Operations（vCenter Operations）API 帳號。到「外部整合 → Aria Operations」挑這一筆；只用來讀取。',
  storage: '儲存設備（SAN Brocade／HMC IBM Power）的唯讀帳號。到 SAN／HMC 收集頁可挑這一筆，也仍可當場輸入、不留存。適用範圍可限定網段。',
}
async function loadCreds() {
  try { creds.value = await apiFetch<CredRow[]>('/api/credentials'); loadErr.value = '' } catch (e: any) { creds.value = []; loadErr.value = `憑證庫載入失敗：${errMsg(e)}` }
}
onMounted(loadCreds)

const editingName = ref<string | null>(null)
function startEditCred(c: CredRow) {
  editingName.value = c.name
  newCred.name = c.name; newCred.kind = c.kind; newCred.username = c.username
  newCred.scope = c.scope || ''; newCred.note = c.note || ''; newCred.password = ''
}
function cancelEditCred() {
  editingName.value = null
  newCred.name = ''; newCred.username = ''; newCred.scope = ''; newCred.note = ''; newCred.password = ''
}
const credTest = reactive({ name: '', kind: 'winrm', ip: '', busy: false, result: null as null | { ok: boolean; message: string; computer?: string; run_as?: string; username?: string; supported?: boolean } })
function openCredTest(c: CredRow) {
  credTest.name = c.name; credTest.kind = c.kind; credTest.result = null
  if (c.kind !== 'winrm') runCredTest()
}
async function runCredTest() {
  if (!credTest.name) return
  if (credTest.kind === 'winrm' && !credTest.ip.trim()) { showToast('要填一台要測試的 IP', 'warn'); return }
  credTest.busy = true; credTest.result = null
  try {
    credTest.result = await apiFetch(`/api/credentials/${encodeURIComponent(credTest.name)}/test`, { method: 'POST', body: credTest.kind === 'winrm' ? { ip: credTest.ip.trim() } : {} })
  } catch (err: any) {
    showToast(`測試沒有執行：${errMsg(err)}`, 'error')
  } finally { credTest.busy = false }
}
async function saveCred() {
  const editing = editingName.value
  if (!newCred.name || !newCred.username || (!editing && !newCred.password)) {
    showToast(editing ? '帳號要填' : '名稱、帳號、密碼都要填', 'warn'); return
  }
  savingCred.value = true
  try {
    if (editing) {
      await apiFetch(`/api/credentials/${encodeURIComponent(editing)}`, { method: 'PUT',
        body: { username: newCred.username, scope: newCred.scope, note: newCred.note, password: newCred.password || null } })
    } else {
      await apiFetch('/api/credentials', { method: 'POST', body: { ...newCred } })
    }
    newCred.password = ''          // 用完立刻從前端清掉
    showToast(`已${editing ? '修改' : '儲存'}憑證「${newCred.name}」`, 'success')
    editingName.value = null
    newCred.name = ''; newCred.username = ''; newCred.scope = ''; newCred.note = ''
    await loadCreds()
  } catch (err: any) {
    showToast(`儲存失敗：${errMsg(err)}`, 'error')
  } finally {
    newCred.password = ''
    savingCred.value = false
  }
}
async function delCred(name: string) {
  // 2.056.1（V56 第 2 批，已定案）：刪除前先確認（只加確認，寫入不變）
  if (!confirm(`確定要刪除收集憑證「${name}」？刪除後無法復原；還被引用的系統會擋下並列出引用處。`)) return
  try {
    await apiFetch(`/api/credentials/${encodeURIComponent(name)}`, { method: 'DELETE' })
    showToast(`已刪除「${name}」`, 'success')
    await loadCreds()
  } catch (err: any) {
    const d = err?.data?.detail
    // 仍被引用：後端回 409 並附引用者清單，直接列出來讓人去改（不是只說「失敗」）
    if (d && typeof d === 'object') {
      showToast(`${d.message}：${(d.refs ?? []).map((r: any) => r.label).join('、')}`, 'warn')
      await loadCreds()
    } else showToast(`刪除失敗：${d ?? err?.message ?? '請稍後重試'}`, 'error')
  }
}
</script>

<template>
  <div>
    <div class="bar">
      <b>收集用憑證</b>
      <InfoNote>這裡統一存放要反覆使用的帳密：Windows WinRM／CIM 收集的服務帳號、Linux 自動納管的 SSH 帳號、WhatsUp Gold／Aria Operations 的 API 帳號、儲存設備（SAN／HMC）的唯讀帳號（Linux 平常收集走 SSH 金鑰，不需要這個）。
        密碼以 Fernet 加密後才存進資料庫，加密金鑰另存於伺服器（0600、不在資料庫裡）。畫面與 API 永遠不會回傳密碼，連密文都不給；每次使用都留稽核（不含密碼）。
        Windows 服務帳號建議：限制來源 IP 只准收集器連 WinRM（5985）、用 JEA 限定端點只跑收集用的唯讀指令、拒絕本機與遠端桌面登入、設帳戶鎖定原則、關閉 Basic 驗證。</InfoNote>
    </div>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <V2CredentialsTable :creds="creds" @test="openCredTest" @edit="startEditCred" @remove="delCred" />

    <div v-if="credTest.name" class="box">
      <b>測試憑證「{{ credTest.name }}」</b>
      <label v-if="credTest.kind === 'winrm'">對這一台登入一次
        <input v-model="credTest.ip" class="in" placeholder="例：10.99.7.20" @keyup.enter="runCredTest">
      </label>
      <button type="button" class="chip" :disabled="credTest.busy" @click="runCredTest">{{ credTest.busy ? '測試中…' : (credTest.kind === 'winrm' ? '開始測試' : '重新測試') }}</button>
      <button type="button" class="chip" :disabled="credTest.busy" @click="credTest.name = ''; credTest.result = null">關閉</button>
      <InfoNote>只登入一次、只問「電腦名稱與目前登入身分」，不收集、不改任何東西。每組憑證 5 秒內只能測一次，30 分鐘內失敗 3 次就先停止——網域帳號打錯太多次會被鎖，所以失敗後請先確認帳號密碼與網域前綴（DOMAIN\帳號）。</InfoNote>
      <div v-if="credTest.result" class="out" :class="credTest.result.ok ? 'ok' : 'bad'">
        <template v-if="credTest.kind !== 'winrm'">{{ credTest.result.ok ? '連線成功：' : (credTest.result.supported === false ? '' : '連線失敗：') }}{{ credTest.result.message }}</template>
        <template v-else-if="credTest.result.ok">登入成功：電腦 {{ credTest.result.computer }}，登入身分 {{ credTest.result.run_as }}</template>
        <template v-else>登入失敗：{{ credTest.result.message }}</template>
      </div>
    </div>

    <h3 class="h3">{{ editingName ? `編輯憑證「${editingName}」` : '新增憑證' }}</h3>
    <form class="form" autocomplete="off" @submit.prevent="saveCred">
      <label>名稱<input v-model="newCred.name" class="in" :disabled="!!editingName" placeholder="例：公司網域收集帳號"></label>
      <label>類型
        <select v-model="newCred.kind" class="in" :disabled="!!editingName">
          <option value="winrm">winrm（Windows 收集）</option>
          <option value="ssh">ssh（Linux 自動納管 bootstrap）</option>
          <option value="whatsup">whatsup（WhatsUp Gold API）</option>
          <option value="vrops">vrops（Aria Operations API）</option>
          <option value="storage">storage（儲存設備 SAN／HMC）</option>
        </select>
        <InfoNote>{{ KIND_HINT[newCred.kind] }}</InfoNote>
      </label>
      <label>帳號<input v-model="newCred.username" class="in" autocomplete="off" placeholder="例：DOMAIN\svc_collect"></label>
      <label>密碼<input v-model="newCred.password" class="in" type="password" autocomplete="new-password" :placeholder="editingName ? '留空＝不改密碼' : ''"></label>
      <label>適用範圍<input v-model="newCred.scope" class="in" placeholder="網段前綴，如 10.99.1.（留空＝通用）"></label>
      <label>備註<input v-model="newCred.note" class="in" placeholder="選填"></label>
      <div class="acts">
        <button type="submit" class="btn" :disabled="savingCred">{{ savingCred ? '儲存中…' : (editingName ? '儲存修改' : '儲存憑證') }}</button>
        <button v-if="editingName" type="button" class="chip" :disabled="savingCred" @click="cancelEditCred">取消編輯</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.box { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 10px 0; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); font-size: 13px; }
.out { width: 100%; font-size: 13px; }
.out.ok { color: var(--good); } .out.bad { color: var(--bad); }
.form { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px 16px; font-size: 13px; }
.form label { display: flex; flex-direction: column; gap: 4px; color: var(--ink-aux); }
.acts { display: flex; gap: 8px; align-items: end; }
.in { font-family: inherit; font-size: 13px; padding: 5px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.err { color: var(--bad); font-size: 13px; }
</style>
