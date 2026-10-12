<script setup lang="ts">
// 弱點彙總追蹤（2026-09-21 使用者：「CL_Patch 是小工具，我們目前已經在用了，
// 我只是要轉移到系統上，變系統版不是單機版」）。
//
// 那支工具是一整份 vanilla JS（SheetJS + Chart.js，約 5,430 行），分析邏輯已經在用、
// 已經驗過。**這次是搬家不是重做**，所以不改寫成 Vue——改寫是純風險，不會多一個功能。
// 整包放在 public/patch-app/ 原樣跑（不可放 public/patch/，會蓋掉這一頁的路由），這一頁只做三件事：
//   1. 掛進 webit 的選單與登入（不必另外記一個網址）
//   2. 把 API 位址傳進去（前端 :3000、API :8000，工具裡不能寫相對路徑）
//   3. 顯示「這份是誰在什麼時候匯入的」——系統版與單機版的差別就在這裡
const config = useRuntimeConfig()
const { apiFetch } = useApi()

// ⚠️ 靜態包一定要放在**跟這一頁不同的路徑**（public/patch-app/）。
// 2026-09-21 B 在 221 抓到：以前放 public/patch/，build 之後靜態檔優先於 Nuxt 路由，
// /patch 直接回工具的 index.html、這一頁根本不渲染；工具的相對路徑腳本又變成 /js/*.js → 全部 404，
// 整頁沒有任何 JS 執行，?api= 也永遠傳不進去（弱點頁「每次都要自己選檔」的真因）。
const src = computed(() =>
  `/patch-app/index.html?api=${encodeURIComponent(config.public.apiBase)}`)

interface StatusItem {
  key: string; label: string; present: boolean; bytes: number
  file_name: string | null; updated_by: string | null; updated_at: string | null
}
const status = ref<StatusItem[]>([])
const statusErr = ref('')
try {
  const r = await apiFetch<{ items: StatusItem[] }>('/api/patch/status')
  status.value = r.items ?? []
} catch (e: any) {
  // 不靜默吞：共用儲存打不通的話，畫面會退回「每人一份」而使用者不會發現
  statusErr.value = e?.data?.detail || e?.message || '共用儲存狀態讀不到'
}
const workbook = computed(() => status.value.find((s) => s.key === 'workbook'))
</script>

<template>
  <div class="page">
    <div class="bar">
      <h1>弱點彙總追蹤</h1>
      <span v-if="statusErr" class="err">{{ statusErr }}</span>
      <span v-else-if="workbook?.present" class="who">
        目前這份：{{ workbook.file_name || '（未命名）' }}
        <template v-if="workbook.updated_by">· 由 {{ workbook.updated_by }} 匯入</template>
        <template v-if="workbook.updated_at">· {{ workbook.updated_at }}</template>
      </span>
      <span v-else class="who dim">還沒有人匯入過——匯入一次之後，所有人看到的都是同一份</span>
    </div>
    <iframe :src="src" class="frame" title="弱點彙總追蹤" />
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; height: calc(100vh - 56px); }
.bar { display: flex; align-items: baseline; gap: 12px; padding: 8px 16px;
  border-bottom: 1px solid var(--border); flex: 0 0 auto; }
h1 { font-size: 17px; margin: 0; }
.who { font-size: 12.5px; color: var(--ink-soft); }
.dim { color: var(--muted); }
.err { font-size: 12.5px; color: #b91c1c; }
.frame { flex: 1 1 auto; width: 100%; border: 0; }
</style>
