<script setup lang="ts">
// 上線前檢查表（單台）：8.2 組態與上線檢核「上線檢查」頁籤、6.1 新設備上線的 ✗ 點進來的畫面（2.010.x）。
// 2.047.0（V32）：改 V2 原生（V2GoliveOne，同一批上線檢查 API：勾選、通過），不再嵌入舊頁。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const serial = computed(() => String(route.params.serial))
</script>

<template>
  <V2Page :back="{ to: '/v2/audit?tab=golive', label: '8.2 組態與上線檢核・上線檢查' }" width="wide">
    <template #title><h1 class="h1">上線前檢查表　<span class="mono">{{ serial }}</span></h1></template>
    <template #conclusion>
      <NuxtLink :to="`/v2/machine/${encodeURIComponent(serial)}`">主機履歷 ›</NuxtLink>
    </template>
    <V2GoliveOne :key="serial" :serial="serial" />
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.mono { font-family: var(--disp); }
</style>
