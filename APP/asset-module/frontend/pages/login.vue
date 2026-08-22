<script setup lang="ts">
definePageMeta({ layout: false })

const { login } = useAuth()

const username = ref('')
const password = ref('')
const errorMessage = ref('')
const submitting = ref(false)

// 為什麼要靠網址參數而不是 toast：資料庫還原成功後一定得重新登入，但那時頁面正要
// 跳來這裡，toast 會跟著舊頁面一起消失——使用者落在登入畫面上，完全不知道發生什麼事，
// 只會以為自己莫名其妙被登出、或以為還原失敗了（2026-08-19 實際回報的困惑）。
// 訊息必須留在「使用者最後會看到的那一頁」上。
const route = useRoute()
const notice = computed(() => {
  if (route.query.reason === 'restored') {
    return '✅ 資料庫還原成功。帳號與登入資料存在同一個資料庫裡，'
      + '已被一併換成上傳檔裡的那份，所以你被登出了。'
      + '請改用「上傳的那份備份裡」的帳號密碼登入。'
  }
  return ''
})

async function handleSubmit() {
  if (submitting.value) return
  errorMessage.value = ''
  submitting.value = true
  try {
    await login(username.value, password.value)
    await navigateTo('/')
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '登入失敗，請稍後再試'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="handleSubmit">
      <h1 class="title">資產盤點系統</h1>
      <p class="subtitle">請登入以繼續</p>

      <p v-if="notice" class="notice">{{ notice }}</p>

      <label class="field">
        <span>帳號</span>
        <input v-model="username" type="text" autocomplete="username" required />
      </label>

      <label class="field">
        <span>密碼</span>
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>

      <p v-if="errorMessage" class="error">{{ errorMessage }}</p>

      <button class="submit" type="submit" :disabled="submitting">
        {{ submitting ? '登入中…' : '登入' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--paper);
}
.login-card {
  width: 320px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 32px 28px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}
.title {
  margin: 0 0 4px;
  font-size: 20px;
  color: var(--ink);
}
.subtitle {
  margin: 0 0 24px;
  font-size: 13px;
  color: var(--muted);
}
.field {
  display: block;
  margin-bottom: 16px;
  font-size: 13px;
  color: var(--ink-soft);
}
.field span {
  display: block;
  margin-bottom: 6px;
}
.field input {
  width: 100%;
  padding: 9px 10px;
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  font-size: 14px;
  background: var(--card);
  color: var(--ink);
}
.field input:focus {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}
.error {
  margin: 0 0 16px;
  padding: 8px 10px;
  background: var(--bad-soft);
  color: var(--bad);
  border-radius: 4px;
  font-size: 13px;
}
/* 綠色（good）而不是紅色：這是「成功了，只是需要重新登入」，
   用錯誤色會讓人以為還原失敗——那正是要修掉的誤會。 */
.notice {
  margin: 0 0 16px;
  padding: 10px 12px;
  background: var(--good-soft);
  color: var(--good);
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.6;
}
.submit {
  width: 100%;
  padding: 10px;
  border: none;
  border-radius: 4px;
  background: var(--brand);
  color: var(--ink);
  font-size: 14px;
  cursor: pointer;
}
.submit:hover:not(:disabled) {
  background: var(--brand-dark);
}
.submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
