<script setup lang="ts">
// 7.2 帳號合規矩陣（2.011.x）：舊版 /account-matrix 直接嵌入（儀表板／合規表／稽核發現／帳號清單，功能與 API 不變）。
// 不在選單上；從 7.2 帳號盤點的「合規矩陣」頁籤進來。頁內分頁用 ?tab=（舊頁自己讀），所以不再套 V2 頁籤，
// 頁內的連結與篩選都留在本頁（account-matrix.vue／AccountDashboard.vue 嵌在 /v2/ 底下時用目前路徑）。
definePageMeta({ layout: 'v2' })
const AccountMatrix = defineAsyncComponent(() => import('~/pages/account-matrix.vue'))
</script>

<template>
  <V2Page width="wide" :back="{ to: '/v2/inventory/account', label: '7.2 帳號盤點' }">
    <template #title><h1 class="h1">7.2 帳號合規矩陣</h1></template>
    <template #conclusion><InfoNote>每個帳號對每條規則是否合規；「查不到（權限不足）」不等於合格。逐筆處理另見 7.2 帳號盤點「稽核發現」。</InfoNote></template>
    <V2Embed><AccountMatrix /></V2Embed>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
</style>
