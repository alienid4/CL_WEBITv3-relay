<script setup lang="ts">
// 主機履歷沒有放選單（藍圖）：網址只到 /v2/machine 時，引導到 2.1 主機查詢或上方全站搜尋。
definePageMeta({ layout: 'v2' })
</script>

<template>
  <V2Page>
    <template #title><h1 class="h1">主機履歷</h1></template>
    <template #conclusion>請在上方全站搜尋輸入 IP、主機名稱或資產編號，或從 <NuxtLink to="/v2/hosts">2.1 主機查詢</NuxtLink> 選擇主機。</template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
</style>
