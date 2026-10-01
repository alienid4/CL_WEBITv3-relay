<script setup lang="ts">
// 各環境實體機分布：內容已抽成 <PhysicalDist /> 元件，跟「分佈統計」第 4 頁籤共用同一份
// （2026-09-12 使用者：「各環境實體機分布可以放進分佈統計當第四頁」）。這頁保留舊路由
// 不壞連結；正式入口在 1-4 分佈統計。
definePageMeta({ ssr: false })
</script>

<template>
  <PhysicalDist />
</template>
