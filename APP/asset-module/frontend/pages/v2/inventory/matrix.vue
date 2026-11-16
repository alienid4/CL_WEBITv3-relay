<script setup lang="ts">
// 7.2 帳號合規矩陣（V13＋V15，2.048.0 起 V2 原生，不再嵌入舊頁 /account-matrix）：
// 儀表板／合規表／稽核發現／帳號清單四個頁籤都在 components/v2/AccountMatrix.vue（條件都在網址上，?tab= 選頁籤）。
// 不在選單上；從 7.2 帳號盤點的「合規矩陣」頁籤進來。
definePageMeta({ layout: 'v2' })
</script>

<template>
  <V2AccountMatrix />
</template>
