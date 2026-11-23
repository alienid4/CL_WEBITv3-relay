<script setup lang="ts">
// 2.x 新版頁籤裡嵌入既有畫面（功能、API、權限檢查完全不變）時的外框：
// 把舊頁自己的頁首（大標題、WAR ROOM 小字、分區條、麵包屑、「此功能已有新版」橫幅）收掉，
// 讓新版頁首與頁籤成為唯一的標題，讀起來是同一頁。只動外觀（CSS），不改舊頁程式。
</script>

<template>
  <div class="v2embed">
    <ClientOnly>
      <slot />
      <template #fallback><p class="muted">載入中…</p></template>
    </ClientOnly>
  </div>
</template>

<style scoped>
.v2embed :deep(.v2b),
.v2embed :deep(.section-divider),
.v2embed :deep(.breadcrumb-bar),
.v2embed :deep(.ey),
.v2embed :deep(h1),
.v2embed :deep(.wrap > h2:first-child),
.v2embed :deep(.page > h2:first-child),
.v2embed :deep(.head > h2:first-child),
.v2embed :deep(.head h2) { display: none !important; }
.v2embed :deep(.page), .v2embed :deep(.wrap) { padding: 0; max-width: none; }
.muted { color: var(--muted); font-size: 13px; }
</style>
