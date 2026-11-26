<script setup lang="ts">
// ⚙ 系統設定 → 通知設定 → EOS 追蹤（類別與催辦）。2.070.x 三層分離第 3 批：原本在 8.3 追蹤頁籤的「類別與催辦設定」子頁
// 與「示範資料」按鈕搬到這裡（8.3 操作畫面只留追蹤清單／通知對象／催辦預覽）。同一組 API：/api/eos/follow/settings、/api/eos/follow/demo。
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const st = ref<any>(null)
const err = ref('')
const busy = ref(false)
const demoCount = ref<number | null>(null)
async function loadSettings() {
  err.value = ''
  try { st.value = JSON.parse(JSON.stringify((await apiFetch<any>('/api/eos/follow/settings')).settings)) } catch (e: any) { err.value = `設定讀取失敗：${errMsg(e)}` }
}
async function loadDemoCount() {
  try { demoCount.value = (await apiFetch<any>('/api/eos/follow/overview', { query: { demo: 'false' } })).demo_count ?? 0 } catch { demoCount.value = null }   // 讀不到只是不顯示筆數，不影響設定
}
onMounted(() => { loadSettings(); loadDemoCount() })
async function saveSettings() {
  busy.value = true
  try {
    const x = st.value
    const num = (v: any) => (v === '' || v === null || v === undefined ? null : Number(v))
    const body = { ...x, reminder: { ...x.reminder, unreplied_days: num(x.reminder.unreplied_days), plan_overdue_days: num(x.reminder.plan_overdue_days), hold_expired_days: num(x.reminder.hold_expired_days) } }
    const out = await apiFetch<any>('/api/eos/follow/settings', { method: 'PUT', body })
    showToast(out.unregistered_in_use?.length ? `已存。注意：這些類別仍有紀錄在用但已不在清單：${out.unregistered_in_use.join('、')}` : '設定已存', 'success')
  } catch (e: any) { showToast(`設定存檔失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function demoOp(method: 'POST' | 'DELETE') {
  busy.value = true
  try {
    await apiFetch('/api/eos/follow/demo', { method })
    showToast(method === 'POST' ? '已產生示範資料（預設不顯示，按「看示範資料」）' : '已清除示範資料', 'success')
    await loadDemoCount()
  } catch (e: any) { showToast(`示範資料操作失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div class="efs" data-testid="eos-follow-settings">
    <p v-if="err" class="err">{{ err }} <button type="button" class="mini" @click="loadSettings">重試</button></p>
    <p v-else-if="!st" class="muted">讀取中…</p>
    <template v-else>
      <h4>阻礙來源類別</h4>
      <div v-for="(c, i) in st.reason_classes" :key="i" class="frm">
        <input v-model="c.name" maxlength="40" aria-label="類別名稱"><label><input v-model="c.need_note" type="checkbox"> 需另填說明</label>
        <button type="button" class="mini" :disabled="st.reason_classes.length <= 1" @click="st.reason_classes.splice(i, 1)">移除</button>
      </div>
      <button type="button" class="mini" :disabled="st.reason_classes.length >= 50" @click="st.reason_classes.push({ name: '', need_note: false })">新增類別（最多 50 個）</button>
      <h4>重新檢視與提醒</h4>
      <div class="frm">
        <label>重新檢視預設期間（月；只是表單預填的建議值） <input v-model.number="st.review_default_months" type="number" min="1" max="60"></label>
        <label>「即將到期」提前天數 <input v-model.number="st.soon_days" type="number" min="1" max="365"></label>
        <label>重新檢視日／承諾日最遠可設（今天起幾個月，1～60） <input v-model.number="st.max_horizon_months" type="number" min="1" max="60"></label>
      </div>
      <h4>催辦與升級<InfoNote>預設關閉；啟用後也只預覽，不寄出。預覽看 8.3 追蹤的「催辦預覽」。</InfoNote></h4>
      <div class="frm">
        <label><input v-model="st.reminder.enabled" type="checkbox"> 啟用</label>
        <label>已通知未回覆超過（天） <input v-model="st.reminder.unreplied_days" type="number" min="0" placeholder="不填＝不啟用此規則"></label>
        <label>逾承諾日超過（天） <input v-model="st.reminder.plan_overdue_days" type="number" min="0" placeholder="不填＝不啟用"></label>
        <label>保留已到期超過（天） <input v-model="st.reminder.hold_expired_days" type="number" min="0" placeholder="不填＝不啟用"></label>
        <label>升級對象（文字） <input v-model="st.reminder.escalate_to" maxlength="200"></label>
      </div>
      <button type="button" class="mini pri" :disabled="busy" @click="saveSettings">{{ busy ? '存檔中…' : '儲存' }}</button>
    </template>
    <details class="fold" data-testid="eos-follow-demo"><summary>更多：示範資料（測試用）</summary>
      <div class="frm">
        <NuxtLink class="mini lk" :to="{ path: '/v2/eos', query: { tab: 'follow', efdemo: '1' } }">看示範資料</NuxtLink>
        <button type="button" class="mini" :disabled="busy" @click="demoOp('POST')">產生</button>
        <button v-if="demoCount" type="button" class="mini" :disabled="busy" @click="demoOp('DELETE')">清除 {{ demoCount }} 筆</button>
        <InfoNote>示範資料不會混進真實的 EOS 追蹤項目，也不會寄出任何信件；平常不顯示，只在這裡產生、清除，或從「看示範資料」開啟。</InfoNote>
      </div>
    </details>
  </div>
</template>

<style scoped>
.efs { display: flex; flex-direction: column; gap: 10px; }
.efs h4 { margin: 4px 0 0; font-size: 14px; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .5; cursor: default; }
.mini.pri { background: var(--brand); color: var(--type-on); border-color: var(--brand); align-self: flex-start; }
.mini.lk { text-decoration: none; display: inline-block; }
.err { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; }
.frm { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.frm label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--ink-aux); }
.frm input { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; min-width: 180px; }
.fold { font-size: 13px; color: var(--ink-aux); } .fold summary { cursor: pointer; color: var(--link); }
</style>
