<script setup lang="ts">
// 9.3 主機分類已併入 9.1 待補資料的「主機分類」頁籤（2026-10-28，找碴 C24）。舊網址保留，轉到那個頁籤。
definePageMeta({ layout: 'v2' })
await navigateTo('/v2/datafix?tab=classify', { replace: true, redirectCode: 301 })
</script>

<template>
  <p>已移至 <NuxtLink to="/v2/datafix?tab=classify">9.1 待補資料・主機分類</NuxtLink></p>
</template>
