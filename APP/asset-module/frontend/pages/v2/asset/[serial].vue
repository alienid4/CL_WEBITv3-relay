<script setup lang="ts">
// 資產明細與編輯（2.010.x）：主機履歷「資產明細與編輯」連過來的畫面。
// 整頁嵌入既有資產明細（舊版 /assets/<資產編號>，同一份程式與 API：資產 API；編輯、標籤、納管後收集都在原畫面，寫入邏輯不動）。
// 看資料請用主機履歷；要改欄位（?edit=1）才來這裡。
definePageMeta({ layout: 'v2' })
const AssetOne = defineAsyncComponent(() => import('~/pages/assets/[serial].vue'))
const route = useRoute()
const serial = computed(() => String(route.params.serial))
</script>

<template>
  <V2Page :back="{ to: `/v2/machine/${encodeURIComponent(serial)}`, label: '主機履歷' }" width="wide">
    <template #title><h1 class="h1">資產明細與編輯　<span class="mono">{{ serial }}</span></h1></template>
    <V2Embed><AssetOne :key="serial" /></V2Embed>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.mono { font-family: var(--disp); }
</style>
