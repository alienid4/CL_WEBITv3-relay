<script setup lang="ts">
// 舊頁上方一行：指向新版位置（2.x 半新半舊期間，例：「此功能已有新版：2.1 主機查詢 ›」）。
defineProps<{ to: string; label: string }>()
</script>

<template>
  <div class="v2b"><span class="star">★</span>此功能已有新版：<NuxtLink :to="to" class="go">{{ label }} ›</NuxtLink></div>
</template>

<style scoped>
.v2b { display: flex; align-items: center; gap: 6px; font-size: 13px; padding: 6px 12px; margin-bottom: 10px; border-radius: 10px;
  background: var(--st-online-bg); color: var(--st-online); border: 1px solid var(--border); }
.star { color: var(--brand); }
.go { color: var(--link); font-weight: 600; text-decoration: none; }
.go:hover { text-decoration: underline; }
</style>
