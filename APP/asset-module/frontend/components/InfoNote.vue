<script setup lang="ts">
// 「註」標記：畫面上只留一個小小的「註」，點一下才展開說明。
// 2026-09-07 使用者定案的鐵律：設計理由、注意事項這類長說明**不要攤在畫面上**
// （整頁會像筆記本、不像公開系統），一律收進這個標記後面，要看的人自己點。
// 真正該一眼看到的「這欄是做什麼、填錯會怎樣」那一句，留在畫面上，不要收進來。
const props = withDefaults(defineProps<{ label?: string }>(), { label: '註' })
const open = ref(false)
const root = ref<HTMLElement | null>(null)

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
function onEsc(e: KeyboardEvent) { if (e.key === 'Escape') open.value = false }
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onEsc)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onEsc)
})
</script>

<template>
  <span ref="root" class="note-wrap">
    <button type="button" class="note-mark" :class="{ on: open }"
            :aria-expanded="open" :title="open ? '收合說明' : '展開說明'"
            @click.stop="open = !open">{{ props.label }}</button>
    <span v-if="open" class="note-pop" role="note"><slot /></span>
  </span>
</template>

<style scoped>
.note-wrap { position: relative; display: inline-block; vertical-align: baseline; }
.note-mark { font-size: 10.5px; line-height: 1.4; padding: 0 6px; border-radius: 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--muted);
  cursor: pointer; font-family: inherit; }
.note-mark:hover, .note-mark.on { border-color: var(--brand); color: var(--brand-dark); }
.note-pop { position: absolute; left: 0; top: calc(100% + 5px); z-index: 40;
  width: 340px; max-width: 82vw; text-align: left;
  background: var(--card-solid, #fff); border: 1px solid var(--border-strong);
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.18);
  padding: 10px 13px; font-size: 12px; line-height: 1.75; font-weight: 400;
  color: var(--ink-soft); white-space: normal; }
.note-pop :deep(code) { background: var(--sub, rgba(15,23,42,.07)); padding: 1px 5px;
  border-radius: 4px; font-family: ui-monospace, Consolas, monospace; }
.note-pop :deep(b) { color: var(--ink); }
</style>
