<script setup lang="ts">
// 2.x 統一版型（每一頁都長這樣）：① 頁首＝在哪一頁＋一句結論（右邊最多一個主要動作）
// ② 重點＝最多 6 格（V2KeyCell） ③ 明細＝頁簽，點了才看。顏色／圓角一律 main.css 變數。
const props = defineProps<{ back?: { to: string; label: string }; tabs?: { k: string; label: string }[]; modelValue?: string; width?: 'narrow' | 'wide' }>()
const emit = defineEmits<{ 'update:modelValue': [v: string] }>()
// 目前頁籤名稱給頁內表格的匯出檔名用（V2Table 匯出：頁名＋頁籤＋日期；拿掉頁籤上的數字）
const curTab = computed(() => (props.tabs ?? []).find(t => t.k === props.modelValue)?.label.replace(/[\s　]+[\d,，].*$/, '').trim() || '')
provide('v2TabLabel', curTab)
</script>

<template>
  <div class="v2" :class="width || 'wide'">
    <header class="hd">
      <div class="hl">
        <NuxtLink v-if="back" :to="back.to" class="back">‹ {{ back.label }}</NuxtLink>
        <div class="title"><slot name="title" /></div>
        <div v-if="$slots.conclusion" class="concl"><slot name="conclusion" /></div>
      </div>
      <div v-if="$slots.actions" class="acts"><slot name="actions" /></div>
    </header>
    <div v-if="$slots.cells" class="cells"><slot name="cells" /></div>
    <slot />
    <section v-if="tabs?.length" class="tabbox">
      <div class="tabs">
        <button v-for="t in tabs" :key="t.k" type="button" class="tab" :class="{ on: modelValue === t.k }"
                @click="emit('update:modelValue', t.k)">{{ t.label }}</button>
      </div>
      <div class="tabbody"><slot name="tab" /></div>
    </section>
  </div>
</template>

<style scoped>
.v2 { display: flex; flex-direction: column; gap: 16px; color: var(--ink); font-family: 'Noto Sans TC', 'Microsoft JhengHei', system-ui, sans-serif; }
.v2.wide { max-width: none; }   /* 決策 11：內容區全寬 */
.v2.narrow { max-width: none; }
.hd { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
.back { font-size: 13px; color: var(--link); text-decoration: none; }
.back:hover { text-decoration: underline; }
.title { display: flex; align-items: center; gap: 10px; margin-top: 6px; flex-wrap: wrap; }
.concl { font-size: 16px; margin-top: 6px; color: var(--ink); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; }
.cells { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 900px) { .cells { grid-template-columns: 1fr; } }
.tabbox { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { border: 1px solid var(--border); background: var(--card); color: var(--ink); border-radius: 999px; padding: 6px 14px; font-size: 14px; font-family: inherit; cursor: pointer; }
.tab.on { border-color: var(--brand); background: var(--brand); color: var(--type-on); }
.tabbody { min-height: 80px; }
</style>
