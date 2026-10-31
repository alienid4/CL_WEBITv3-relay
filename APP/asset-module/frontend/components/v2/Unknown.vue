<script setup lang="ts">
// 2.x「無資料（原因）」（決策 12）：查不到一律這樣寫，不留白。原因裡的全形括號改成冒號／逗號，避免「（…（…））」兩層。
const props = defineProps<{ why: string }>()
const text = computed(() => {
  let t = props.why || '沒有資料'
  for (let i = 0; i < 5 && /（[^（）]*）/.test(t); i++) {
    t = t.replace(/（([^（）]*)）$/, '：$1').replace(/（([^（）]*)）/, '，$1，')
  }
  return t.replace(/，，/g, '，').replace(/，$/, '')
})
</script>

<template>
  <span class="uk">無資料（{{ text }}）</span>
</template>

<style scoped>
.uk { background: var(--st-unknown-bg); color: var(--st-unknown); padding: 1px 6px; border-radius: 6px; font-weight: 400; font-size: .92em; }
</style>
