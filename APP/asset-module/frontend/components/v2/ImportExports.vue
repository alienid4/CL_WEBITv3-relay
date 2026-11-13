<script setup lang="ts">
// 資料匯入（V36）：一個來源的「原始檔／Excel／dump」三顆匯出鈕＋範例檔。同一支 /api/import/{key}/export/{kind}（舊頁 doExport 原樣）。
import { downloadVia } from '~/utils/importV2'
const props = defineProps<{ src?: any; apiBase: string; template?: string | null; templateName?: string; count?: number | null; noExcel?: boolean }>()
const { showToast } = useToast()
const busy = ref('')
async function doExport(kind: 'original' | 'excel' | 'dump') {
  if (!props.src) return
  busy.value = kind
  try { await downloadVia(props.apiBase, `/api/import/${props.src.key}/export/${kind}`, `${props.src.key}.${kind}`) } catch (e: any) {
    // 「匯出失敗」對使用者沒有用——沒有原始檔跟伺服器壞掉要分開講
    showToast(kind === 'original' && String(e?.message).includes('404') ? '這個來源沒有留原始檔（可能是還沒匯入過，或那次匯入早於這個功能上線）' : '匯出失敗，請稍後再試', 'error', 8000)
  } finally { busy.value = '' }
}
async function doTemplate() {
  if (!props.template) return
  busy.value = 'tpl'
  try { await downloadVia(props.apiBase, props.template, props.templateName || 'template.xlsx') } catch (e: any) { showToast(`範例檔下載失敗：${e?.message ?? '請稍後重試'}`, 'error') } finally { busy.value = '' }
}
const rows = computed(() => props.count ?? props.src?.row_count ?? 0)
</script>

<template>
  <span class="ex">
    <button v-if="template" type="button" class="b" :disabled="busy === 'tpl'" title="下載範例檔：看要填哪些欄位" @click="doTemplate">範例檔</button>
    <template v-if="src">
      <span class="div" aria-hidden="true" />
      <button type="button" class="b" :disabled="!src.has_original || !!busy" :title="src.has_original ? `原始檔：${src.original_name}` : '沒有留原始檔（此功能上線前匯入的那批）'" @click="doExport('original')">原始檔</button>
      <button v-if="!noExcel" type="button" class="b" :disabled="!rows || !!busy" title="匯出目前系統裡的資料（正規化後，可原封匯回）" @click="doExport('excel')">Excel</button>
      <button type="button" class="b" :disabled="(!rows && !noExcel) || !!busy" title="整包 dump，搬到另一台再匯回（明碼，勿當備份寄出）" @click="doExport('dump')">dump</button>
    </template>
  </span>
</template>

<style scoped>
.ex { display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.div { width: 1px; height: 20px; background: var(--border-strong); margin: 0 4px; }
.b { font-family: inherit; font-size: 12px; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.b:disabled { opacity: .45; cursor: default; }
</style>
