<script setup lang="ts">
// V2 還沒移植的舊版功能入口（2.042.22，使用者：「全部的功能我都要保留」）。
// 舊網址一律轉到 V2（middleware/v2-legacy.global.ts），所以 V2 還缺的功能（docs/V2移植清單.md C、D 類）
// 只能從這裡帶 ?legacy=1 進舊版畫面。移植完一項就把那一條連結拿掉、清單打勾。
// tests/asset_module/test_v2_no_v1_links.py 會檢查清單上每個 C／D 項目在 V2 都至少有一條這種入口。
defineProps<{ links: { label: string; to: string }[] }>()
</script>

<template>
  <div class="legacy" data-testid="v2-legacy-links">
    <span class="tag">舊版畫面（移植中）</span>
    <NuxtLink v-for="l in links" :key="l.to + l.label" :to="l.to" class="lk">{{ l.label }} ↗</NuxtLink>
  </div>
</template>

<style scoped>
.legacy { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin: 0 0 10px; font-size: 12px; }
.tag { color: var(--ink-aux); border: 1px dashed var(--border-strong); border-radius: 6px; padding: 1px 8px; }
.lk { color: var(--brand-dark); text-decoration: none; }
.lk:hover { text-decoration: underline; }
</style>
