<script setup lang="ts">
// ⚙ 收集身分 › 收集金鑰與備註 裡的兩張小卡（2.056.3，V56 第 4 批；原舊 /settings credentials 分頁）純搬移：
//   · 收集帳號備註：GET／PUT /api/accounts/collect-config（body { account, account_comment }）。
//   · Windows 正式組態檢核開關：GET／PUT /api/config-audit/windows-settings（body { enabled }）；存失敗把勾選退回去。
// 收集金鑰（Linux）在同一個子頁籤，下一批搬。
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const x = e?.data?.detail; return typeof x === 'string' ? x : x?.message ?? e?.message ?? '未知錯誤' }

const acComment = ref<string | null>(null)
const acAccount = ref('webit3sc')
const acEdit = ref('')
const acBusy = ref(false)
const acErr = ref('')
async function loadAccountComment() {
  try {
    const r = await apiFetch<{ account: string; account_comment: string }>('/api/accounts/collect-config')
    acComment.value = r.account_comment
    acAccount.value = r.account
  } catch (e: any) { acErr.value = `讀取帳號備註失敗：${errMsg(e)}` }
}
async function saveAccountComment() {
  if (acBusy.value) return
  acBusy.value = true
  try {
    const r = await apiFetch<{ account_comment: string }>('/api/accounts/collect-config', { method: 'PUT', body: { account: acAccount.value, account_comment: acEdit.value } })
    acComment.value = r.account_comment
    acEdit.value = ''
    showToast(`帳號備註已設為「${r.account_comment}」`, 'success')
  } catch (e: any) { showToast(`設定失敗：${errMsg(e)}`, 'error') } finally { acBusy.value = false }
}

const winFcb = ref<boolean | null>(null)
const winFcbBusy = ref(false)
const fcbErr = ref('')
async function loadWinFcb() {
  try { winFcb.value = (await apiFetch<{ enabled: boolean }>('/api/config-audit/windows-settings')).enabled } catch (e: any) { fcbErr.value = `Windows 組態檢核設定讀取失敗：${errMsg(e)}` }
}
async function toggleWinFcb(ev: Event) {
  const want = (ev.target as HTMLInputElement).checked
  winFcbBusy.value = true
  try {
    winFcb.value = (await apiFetch<{ enabled: boolean }>('/api/config-audit/windows-settings', { method: 'PUT', body: { enabled: want } })).enabled
    showToast(want ? '已開啟：收集全部會跑 Windows 組態檢核' : '已關閉：Windows 組態檢核顯示「略過」', 'success')
  } catch (e: any) {
    (ev.target as HTMLInputElement).checked = !want
    showToast(`儲存失敗：${errMsg(e)}`, 'error')
  } finally { winFcbBusy.value = false }
}
onMounted(() => { loadAccountComment(); loadWinFcb() })
</script>

<template>
  <div>
    <div class="card">
      <div class="ct">收集帳號備註
        <InfoNote>寫進目標主機 /etc/passwd 的備註欄（GECOS）。每一台被納管主機上的任何本機帳號都看得到，不要放員編、姓名等個資；預設 WEBIT3 collector。存在資料庫、不進程式碼；改了之後已納管的主機重新納管一次就會對齊。只接受中英數、- _ . @ ( ) ／ 與空白，最多 120 字；: 、引號、$、換行一律拒絕。留空儲存＝退回預設。</InfoNote></div>
      <p v-if="acErr" class="err">{{ acErr }}</p>
      <form class="bar" @submit.prevent="saveAccountComment">
        <code>{{ acComment ?? '…' }}</code>
        <input v-model="acEdit" class="in" :placeholder="acComment ?? ''" maxlength="120" :disabled="acBusy" aria-label="帳號備註">
        <button type="submit" class="btn" :disabled="acBusy">{{ acBusy ? '儲存中…' : '儲存' }}</button>
      </form>
    </div>
    <div class="card">
      <div class="ct">Windows 正式組態檢核（會執行 secedit／auditpol）
        <InfoNote>收集全部的「組態檢核」那一步，對 Windows 跑公司基準（組態安全配置檢核表 v4.0，286 條）。經 WinRM 在記憶體執行，會在目標機起 secedit.exe 與 auditpol.exe 兩個唯讀子行程，SOC／EDR 看得到；需本機管理員。預設開啟；SOC 有意見時關掉，Windows 的組態檢核改顯示「略過」。改了下一次收集就生效。</InfoNote></div>
      <p v-if="fcbErr" class="err">{{ fcbErr }}</p>
      <label class="bar"><input type="checkbox" :checked="!!winFcb" :disabled="winFcbBusy || winFcb === null" aria-label="Windows 正式組態檢核" @change="toggleWinFcb">
        {{ winFcb === null ? '讀取中…' : winFcb ? '已開啟' : '已關閉' }}</label>
    </div>
  </div>
</template>

<style scoped>
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; margin-bottom: 12px; }
.ct { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 6px; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 240px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.err { color: var(--bad); font-size: 13px; }
</style>
