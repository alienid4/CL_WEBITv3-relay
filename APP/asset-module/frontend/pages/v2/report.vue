<script setup lang="ts">
// 10.1 月報（2.x 第四批）：月報匯出｜系統組報告。沿用既有畫面（同一份程式與 API），收掉舊頁頁首。
// 2.007.19：加「效能圖表」（NMON 逐分鐘、日／小時／原始切換、各指標、可複製連結）。
// 2.007.13：加「主機組月報」（V2HostMonthly，網頁即月報，每頁 16:9 可直接截圖）。
definePageMeta({ layout: 'v2' })
const Monthly = defineAsyncComponent(() => import('~/pages/reports/monthly.vue'))
const Group = defineAsyncComponent(() => import('~/pages/reports/system-group.vue'))
// 主機系統總覽、報表列印（2.008.8，舊版 /reports/system-overview、/reports/host-sources）：直接嵌入既有畫面，只讀
const Overview = defineAsyncComponent(() => import('~/pages/reports/system-overview.vue'))
const HostSources = defineAsyncComponent(() => import('~/pages/reports/host-sources.vue'))
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
      <V2Embed v-else>
        <Monthly v-if="tab === 'monthly'" />
        <Overview v-else-if="tab === 'overview'" />
        <HostSources v-else-if="tab === 'sources'" />
        <Group v-else />
      </V2Embed>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
</style>
