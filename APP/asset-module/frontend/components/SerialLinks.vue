<script setup lang="ts">
// 資產序號欄：每個序號各自一條連結（缺口清單一台可能有多筆缺口登記）；沒有序號＝這台還沒登記成資產
defineProps<{ serials: string[] }>()
</script>

<template>
  <span v-if="!serials.length" class="dim">未登記</span>
  <template v-else>
    <template v-for="(s, i) in serials" :key="s">
      <span v-if="i">、</span><NuxtLink :to="`/assets/${s}`" class="dl">{{ s }}</NuxtLink>
    </template>
  </template>
</template>
