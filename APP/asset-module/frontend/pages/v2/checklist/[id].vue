<script setup lang="ts">
// MICS 檢查清單（2.011.x；不在選單，從分享連結 /v2/checklist/<快照編號> 進來）：舊版 /blast/checklist/<id> 直接嵌入，
// 功能與 API（/api/blast/checklist*）不變——全隊登入看同一份、狀態與備註就地存。
// 產生清單的入口仍在舊版 /blast「產生檢查清單」；V2 事件應變有自己的「檢核清單」（4.1 單一事件）。
definePageMeta({ layout: 'v2' })
const Checklist = defineAsyncComponent(() => import('~/pages/blast/checklist/[id].vue'))
</script>

<template>
  <V2Page width="wide" :back="{ to: '/v2/incident', label: '4.1 事件應變' }">
    <template #title><h1 class="h1">MICS 檢查清單</h1></template>
    <V2Embed><Checklist /></V2Embed>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
</style>
