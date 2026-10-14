<script setup lang="ts">
// 多段圓環圖。純 SVG，不引函式庫——公司環境擋 npm，前端相依只有 nuxt/vue/vue-router
// （計畫檔「技術方案」那節的要求）。做法跟首頁 index.vue 的雙環儀表同一招：
// <circle> 疊 stroke-dasharray／stroke-dashoffset 畫弧，多段就是同一招重複，
// 每段算好起始 offset 疊上去，不必為了圓環圖去改 package.json。
interface Segment { name: string; count: number; color: string }
const props = withDefaults(defineProps<{
  segments: Segment[]
  size?: number
  strokeWidth?: number
  centerLabel?: string
}>(), { size: 168, strokeWidth: 16 })

const emit = defineEmits<{ (e: 'segment-click', name: string): void }>()

const r = computed(() => props.size / 2 - props.strokeWidth / 2 - 2)
const circumference = computed(() => 2 * Math.PI * r.value)
const total = computed(() => props.segments.reduce((s, x) => s + x.count, 0))

// 累計 offset 算每一段的起點；0 台的段直接濾掉，不要在圓環上留一條看不見的縫。
const arcs = computed(() => {
  let acc = 0
  return props.segments
    .filter((s) => s.count > 0)
    .map((s) => {
      const frac = total.value ? s.count / total.value : 0
      const len = frac * circumference.value
      const dashoffset = -acc
      acc += len
      return { ...s, len, dashoffset, pct: frac * 100 }
    })
})
</script>

<template>
  <div class="donut" :style="{ width: size + 'px', height: size + 'px' }">
    <svg :viewBox="`0 0 ${size} ${size}`" class="ring">
      <circle :cx="size / 2" :cy="size / 2" :r="r" class="trk" :stroke-width="strokeWidth" fill="none" />
      <circle
        v-for="a in arcs" :key="a.name"
        :cx="size / 2" :cy="size / 2" :r="r" fill="none"
        :stroke="a.color" :stroke-width="strokeWidth"
        :stroke-dasharray="`${a.len} ${circumference}`"
        :stroke-dashoffset="a.dashoffset"
        class="seg" :class="{ clickable: total > 0 }"
        :title="`${a.name} ${a.count.toLocaleString()} 台（${a.pct.toFixed(1)}%）`"
        @click="emit('segment-click', a.name)"
      />
    </svg>
    <div class="center">
      <div class="n mono">{{ total.toLocaleString() }}</div>
      <div v-if="centerLabel" class="lbl">{{ centerLabel }}</div>
    </div>
  </div>
</template>

<style scoped>
/* 顏色一律由呼叫端傳入（取自 main.css 變數或既定色階），這裡不自己發明色。 */
.donut { position: relative; }
.ring { width: 100%; height: 100%; transform: rotate(-90deg); }
.trk { stroke: var(--line); }
.seg { transition: opacity .12s ease; }
.seg.clickable { cursor: pointer; }
.seg.clickable:hover { opacity: .75; }
.center { position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; pointer-events: none; text-align: center; }
.center .n { font-family: var(--disp); font-size: 26px; font-weight: 600; letter-spacing: -1px;
  color: var(--ink); line-height: 1; }
.center .lbl { font-size: 11px; color: var(--muted); margin-top: 4px; max-width: 80%; }
</style>
