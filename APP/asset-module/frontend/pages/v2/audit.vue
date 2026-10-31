<script setup lang="ts">
// 8.2 組態與上線檢核（2.x 第四批）：組態檢核｜上線檢查｜基線偏移。三個頁籤沿用既有畫面（同一份程式與 API：
// /api/config-audit、/api/golive、/api/drift），收掉舊頁的頁首。
definePageMeta({ layout: 'v2' })
const Cfg = defineAsyncComponent(() => import('~/pages/config-audit.vue'))
const Golive = defineAsyncComponent(() => import('~/pages/golive/index.vue'))
const Drift = defineAsyncComponent(() => import('~/pages/drift.vue'))
const route = useRoute()
const router = useRouter()
const TABS = [{ k: 'config', label: '組態檢核' }, { k: 'golive', label: '上線檢查' }, { k: 'drift', label: '基線偏移' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'config'), set: (v: string) => router.replace({ query: { tab: v } }) })
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">8.2 組態與上線檢核</h1></template>
    <template #conclusion>
      <span class="muted">組態檢核結果、上線前檢查表、上線後基線偏移。</span>
      <InfoNote>上線檢查通過的那一刻，自動檢查項目的結果即成為基線；之後每天回檢，與基線不同即列入基線偏移。新設備的上線進度另見 6.1 納管作業「新設備上線」。</InfoNote>
    </template>
    <template #tab>
      <V2Embed>
        <Cfg v-if="tab === 'config'" />
        <Golive v-else-if="tab === 'golive'" />
        <Drift v-else />
      </V2Embed>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
</style>
