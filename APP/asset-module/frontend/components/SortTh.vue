<script setup lang="ts">
// 可排序的表頭儲存格（天條：只要是表格，每一欄都要能排）。
// 未排序時顯示淡色 ↕ 提示「這欄可以點」，排序中顯示 ▲/▼。
defineProps<{
  /** 這一欄對應的資料 key */
  k: string
  /** 目前排序中的 key */
  active: string
  /** 目前方向 */
  dir: 'asc' | 'desc'
}>()
const emit = defineEmits<{ (e: 'sort', k: string): void }>()
</script>

<template>
  <th class="sortable" :class="{ on: active === k }" @click="emit('sort', k)">
    <span class="lbl"><slot /></span>
    <i class="arw">{{ active === k ? (dir === 'asc' ? '▲' : '▼') : '↕' }}</i>
  </th>
</template>

<style scoped>
.sortable {
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.sortable:hover { color: var(--brand, #26a889); }
.sortable .arw {
  font-style: normal;
  font-size: 9px;
  margin-left: 5px;
  opacity: 0.35;
  vertical-align: middle;
}
.sortable:hover .arw { opacity: 0.7; }
.sortable.on { color: var(--brand, #26a889); }
.sortable.on .arw { opacity: 1; }
</style>
