<script setup lang="ts">
// 清空盤點資料：2.051.0 起下架（V51，待決定 1：「不搬，舊頁直接下架」）。
// middleware/v2-legacy.global.ts 會把 /reset 轉到 /v2/settings?st=backup&retired=reset（PORTED，legacy=1 也不開）；
// 這頁只在轉址沒發生時（例如 middleware 被停用）當備援，沒有任何寫入。後端清空 API 缺省關閉（回 410）。
const target = '/v2/settings?st=backup&retired=reset'
onMounted(() => navigateTo(target, { replace: true }))
</script>

<template>
  <div>
    <p>「清空盤點資料」已下架。<NuxtLink :to="target">前往備份與還原</NuxtLink></p>
  </div>
</template>
