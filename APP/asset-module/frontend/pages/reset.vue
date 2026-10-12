<script setup lang="ts">
// 5B-4 清空盤點資料：從「資料匯入」頁抽出來獨立（2026-09-09 使用者：
// 「清空資料也要獨立一個出來，例如 5B-4」）。危險操作單獨一頁，不跟匯入混在一起，
// 少了誤點的機會。像 GitHub 刪 repo，要手動打 ClearALL 才送得出去。
const { apiFetch } = useApi()
const { showToast } = useToast()

const resetConfirm = ref('')
const resetting = ref(false)
const lastResult = ref<string | null>(null)

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
    lastResult.value = `已清空 ${r.total_rows} 筆盤點資料（帳號／憑證／設定保留）`
    showToast(lastResult.value, 'success')
    resetConfirm.value = ''
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
    <div class="section-divider">清空盤點資料</div>

    <div class="card" style="border:1px solid #c0392b">
      <div class="card-title" style="color:var(--bad)">
        ⚠️ 清空所有盤點資料
        <InfoNote>
          把資產／人員／軟體／掃描／帳號盤點／待審／業務系統／匯入紀錄<b>全部刪除</b>，
          回到空白供重新匯入。<b>會保留</b>登入帳號、憑證庫、連線設定、授權網段、系統設定。
          執行前請先到「備份與還原」按「立即備份」。
        </InfoNote>
      </div>
      <!-- 這一句是 critical 例外：不可逆、只能靠備份還原，看不到會出事，故留在畫面上。 -->
      <p class="rv-hint">
        <b style="color:var(--bad)">此動作不可逆</b>，只能靠備份還原。
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
      <p v-if="lastResult" class="rv-hint" style="color:var(--good);margin-top:12px">
        {{ lastResult }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.rv-hint { font-size: 13px; color: var(--ink-soft); margin: 6px 0; }
</style>
