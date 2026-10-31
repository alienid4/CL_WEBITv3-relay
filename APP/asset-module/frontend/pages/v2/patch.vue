<script setup lang="ts">
// 8.1 弱點管理（2.x 第四批）：沿用既有 CL_Patch 弱點彙總工具（整包放在 public/patch-app/，以 iframe 嵌入；決策：搬家不重寫）。
// 本頁只提供新版外框與「目前這份資料是誰在何時匯入的」。
definePageMeta({ layout: 'v2' })
const config = useRuntimeConfig()
const { apiFetch } = useApi()
const src = computed(() => `/patch-app/index.html?api=${encodeURIComponent(config.public.apiBase)}`)
const status = ref<any[]>([])
const err = ref('')
try { status.value = (await apiFetch<any>('/api/patch/status')).items ?? [] } catch (e: any) { err.value = `共用資料狀態讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
const wb = computed(() => status.value.find((s: any) => s.key === 'workbook'))
</script>

<template>
  <V2Page width="wide">
    <template #title><h1 class="h1">8.1 弱點管理</h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <span v-else-if="wb?.present">目前資料：{{ wb.file_name || '（未命名）' }}<template v-if="wb.updated_by">・{{ wb.updated_by }} 匯入</template><template v-if="wb.updated_at">・{{ wb.updated_at }}</template></span>
      <span v-else class="muted">尚未匯入弱點資料</span>
      <InfoNote>弱點彙總工具（原 CL_Patch）原樣嵌入：匯入一次後所有人看到同一份。</InfoNote>
    </template>
    <iframe :src="src" class="frame" title="弱點管理" />
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.frame { width: 100%; height: calc(100vh - 170px); min-height: 560px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
