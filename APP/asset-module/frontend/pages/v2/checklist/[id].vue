<script setup lang="ts">
// MICS 檢查清單（不在選單，從分享連結 /v2/checklist/<快照編號> 進來）。
// 2.047.0（V23）：改 V2 原生（V2Checklist，同一批 /api/blast/checklist* API：全隊登入看同一份、狀態與備註就地存），不再嵌入舊頁。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const id = computed(() => Number(route.params.id))
</script>

<template>
  <V2Page width="wide" :back="{ to: '/v2/incident', label: '4.1 事件應變' }">
    <template #title><h1 class="h1">MICS 檢查清單</h1></template>
    <ClientOnly><V2Checklist :key="id" :id="id" /></ClientOnly>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
</style>
