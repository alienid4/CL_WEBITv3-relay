<script setup lang="ts">
// 2.x 查不到的值（決策 12：不留白）。主值只寫「未填」（登記沒填）或「無資料」（其他原因），原因放灰色小字；
// 原因太長就只放滑過提示，不把整句塞進畫面（2026-11-15 畫面重看：括號備註到處重複）。
const props = defineProps<{ why: string }>()
const reason = computed(() => {
  let t = props.why || ''
  for (let i = 0; i < 5 && /（[^（）]*）/.test(t); i++) {
    t = t.replace(/（([^（）]*)）$/, '：$1').replace(/（([^（）]*)）/, '，$1，')
  }
  return t.replace(/，，/g, '，').replace(/，$/, '').trim()
})
const main = computed(() => (/未填|未登錄|未登記/.test(reason.value) ? '未填' : '無資料'))
// 「未填」本身已說明白，原因只放提示；「無資料」的短原因用灰字，長的只放提示
const inline = computed(() => (main.value === '無資料' && reason.value.length > 0 && reason.value.length <= 16 ? reason.value : ''))
</script>

<template>
  <span class="uk" :title="reason || undefined">{{ main }}<small v-if="inline" class="why">{{ inline }}</small></span>
</template>

<style scoped>
.uk { color: var(--st-unknown); font-weight: 400; }
.why { margin-left: 6px; font-size: 12px; color: var(--ink-aux); }
</style>
