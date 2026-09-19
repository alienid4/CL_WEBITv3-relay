<script setup lang="ts">
// 帳號盤點明細（2026-09-18）。匯入與匯出按鈕在上方「匯入／匯出」表的「帳號盤點」那一列
// （使用者：「應該放圖2」）；這張卡片是該列「明細」跳過來的地方：目前基準、匯入歷史、type 代碼表。
// 後端見 account_audit.py。
interface Batch {
  id: number; imported_at: string; file_name: string | null; imported_by: string | null
  row_count: number; skipped_count: number; host_count: number; header?: string[]
}
interface Status {
  latest: Batch | null; history: Batch[]
  type_table: { code: string; info: string }[]; standard_default: string[]
}

const { apiFetch } = useApi()
const status = ref<Status | null>(null)
const statusErr = ref('')

onMounted(async () => {
  try {
    status.value = await apiFetch<Status>('/api/account-audit/status')
  } catch (e: any) {
    statusErr.value = `帳號盤點明細載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  }
})
</script>

<template>
  <div id="account-audit" class="card">
    <div class="card-title">帳號盤點明細
      <InfoNote>匯入／匯出按鈕在上方「匯入／匯出」表的<b>帳號盤點</b>那一列。這裡看目前的比較基準、歷次匯入與 type 代碼表。<br><br>type_：上次盤過的帳號<b>沿用上次人工填的</b>；新帳號依規則推（UID0→1、系統預設→2、DB→4、Anchor/APPM→6、收集帳號→5、真人→7、服務→3），推不出填 8 未知待查。</InfoNote>
    </div>

    <p v-if="statusErr" class="error-text">{{ statusErr }}</p>
    <template v-else-if="status">
      <p v-if="status.latest" class="aa-base">
        目前比較基準：第 <b>{{ status.latest.id }}</b> 批（{{ status.latest.imported_at }}，{{ status.latest.file_name }}，
        {{ status.latest.row_count }} 筆／{{ status.latest.host_count }} 台）
        <span class="muted small">欄位順序：{{ (status.latest.header || []).join('、') }}</span>
      </p>
      <p v-else class="muted">還沒匯入過上次盤點——匯出時會用預設欄位，而且無從比較。</p>

      <table v-if="status.history.length" class="aa-hist">
        <thead><tr><th>批次</th><th>匯入時間</th><th>檔名</th><th class="num">筆數</th><th class="num">台數</th><th class="num">略過</th><th>匯入人</th></tr></thead>
        <tbody>
          <tr v-for="h in status.history" :key="h.id">
            <td>{{ h.id }}</td><td>{{ h.imported_at }}</td><td>{{ h.file_name }}</td>
            <td class="num">{{ h.row_count }}</td><td class="num">{{ h.host_count }}</td>
            <td class="num">{{ h.skipped_count }}</td><td>{{ h.imported_by || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="aa-types">
        <span class="muted small">type 代碼：</span>
        <span v-for="t in status.type_table" :key="t.code" class="chip">{{ t.code }}＝{{ t.info }}</span>
      </div>
    </template>
    <p v-else class="muted">載入中…</p>
  </div>
</template>

<style scoped>
.aa-base { font-size: 13px; margin: 4px 0 8px; }
.aa-base .small { display: block; margin-top: 2px; }
.aa-hist { width: 100%; border-collapse: collapse; font-size: 12px; margin: 6px 0; }
.aa-hist th, .aa-hist td { padding: 3px 8px; border-bottom: 1px solid var(--border); text-align: left; }
.aa-hist .num { text-align: right; }
.aa-types { margin-top: 8px; font-size: 12px; }
.aa-types .chip { display: inline-block; margin: 4px 6px 0 0; padding: 1px 8px; border-radius: 10px; background: var(--brand-tint, #eef7f3); }
.small { font-size: 12px; }
</style>
