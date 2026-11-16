<script setup lang="ts">
// 2.x 偵測狀態（決策 12）：運作中／逾期未偵測／未偵測到／無資料（原因）。判定在後端 v2_views.status_chip（existence 同一份）。
defineProps<{ status: { key: string; label: string } | null | undefined }>()
</script>

<template>
  <span class="sc" :class="status?.key || 'unknown'">{{ status?.key === 'unknown' || !status ? '' : '● ' }}{{ status?.label || '無資料' }}</span>
</template>

<style scoped>
.sc { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 13px; white-space: nowrap; }
.online { background: var(--st-online-bg); color: var(--st-online); }
.stale { background: var(--st-stale-bg); color: var(--st-stale); }
.missing { background: var(--st-missing-bg); color: var(--st-missing); }
.unknown { background: var(--st-unknown-bg); color: var(--st-unknown); }
</style>
