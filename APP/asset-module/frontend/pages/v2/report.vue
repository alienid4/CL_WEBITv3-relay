<script setup lang="ts">
// 10.1 月報（2.x 第四批）：月報匯出｜系統組報告。沿用既有畫面（同一份程式與 API），收掉舊頁頁首。
// 2.007.19：加「效能圖表」（NMON 逐分鐘、日／小時／原始切換、各指標、可複製連結）。
// 2.007.13：加「主機組月報」（V2HostMonthly，網頁即月報，每頁 16:9 可直接截圖）。
definePageMeta({ layout: 'v2' })
// 系統組報告（V49）：2.048.16 起 V2 原生（V2SystemGroup），不再嵌入舊頁
// 主機系統總覽（V50）：2.048.6 起 V2 原生（V2SystemOverview），不再嵌入舊頁
// 2.047.0（V47、V48）：報表列印、月報匯出改 V2 原生（V2HostSources、V2MonthlyExport），不再嵌入舊頁
const route = useRoute()
const router = useRouter()
const TABS = [{ k: 'monthly', label: '月報匯出' }, { k: 'host', label: '主機組月報' }, { k: 'perf', label: '效能圖表' }, { k: 'group', label: '系統組報告' }, { k: 'overview', label: '主機系統總覽' }, { k: 'sources', label: '報表列印' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'monthly'), set: (v: string) => router.replace({ query: { tab: v } }) })
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">10.1 月報</h1></template>
    <template #conclusion><span class="muted">每月匯出的原始資料與系統組報告。台數與分布另見 10.2 分布統計。</span></template>
    <template #tab>
      <ClientOnly v-if="tab === 'host'"><V2HostMonthly /></ClientOnly>
      <ClientOnly v-else-if="tab === 'perf'"><V2NmonExplorer /></ClientOnly>
      <ClientOnly v-else-if="tab === 'monthly'"><V2MonthlyExport /></ClientOnly>
      <ClientOnly v-else-if="tab === 'sources'"><V2HostSources /></ClientOnly>
      <ClientOnly v-else-if="tab === 'overview'"><V2SystemOverview /></ClientOnly>
      <ClientOnly v-else><V2SystemGroup /></ClientOnly>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
</style>
