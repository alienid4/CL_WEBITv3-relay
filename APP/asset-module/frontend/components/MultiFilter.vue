<script setup lang="ts">
// 多選篩選（共用元件，2026-09-16 使用者：「我要可以多選擇」「還少 機房選項 譬如 測試 > 內湖 > os」）。
//
// 原本每個篩選都是單選下拉：要看「測試環境的內湖機房的 Linux」得先選環境、再換機房、再換 OS，
// 而且同一欄只能選一個值——想同時看內湖＋板橋就沒辦法。
//
// 這支做三件事：
// 1. **多選**：同一欄可以勾好幾個值（同欄 OR、跨欄 AND，跟原本的篩選語意一致）
// 2. **帶數量**：每個選項後面直接寫這個值有幾筆，不用選了才知道是空的
// 3. **層層收斂**：選項由外層依「已選的上層條件」算出來——選了測試環境，機房就只列
//    測試環境有的機房。空選＝不篩選（不是「都不要」）。

const props = withDefaults(defineProps<{
  label: string
  /** 選項：key 是值、label 是顯示字、n 是筆數 */
  options: { key: string; label: string; n: number }[]
  modelValue: string[]
  /** 選項很多時（例如機房）給搜尋框 */
  searchable?: boolean
}>(), { searchable: false })

const emit = defineEmits<{ (e: 'update:modelValue', v: string[]): void }>()

const open = ref(false)
const kw = ref('')
const root = ref<HTMLElement | null>(null)

const shown = computed(() => {
  const q = kw.value.trim().toLowerCase()
  return q ? props.options.filter((o) => o.label.toLowerCase().includes(q)) : props.options
})

function toggle(k: string) {
  const s = new Set(props.modelValue)
  if (s.has(k)) s.delete(k); else s.add(k)
  emit('update:modelValue', [...s])
}
function clearAll() { emit('update:modelValue', []) }
function selectShown() { emit('update:modelValue', [...new Set([...props.modelValue, ...shown.value.map((o) => o.key)])]) }

// 點外面就收起來：下拉開著擋住表格很煩
function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

const summary = computed(() => {
  if (!props.modelValue.length) return '全部'
  if (props.modelValue.length === 1) {
    return props.options.find((o) => o.key === props.modelValue[0])?.label ?? props.modelValue[0]
  }
  return `已選 ${props.modelValue.length} 項`
})
</script>

<template>
  <div ref="root" class="mf">
    <span class="lb">{{ label }}</span>
    <button type="button" class="tg" :class="{ on: modelValue.length }" @click="open = !open">
      {{ summary }}<span class="cx">▾</span>
    </button>
    <div v-if="open" class="pop">
      <div v-if="searchable" class="srow">
        <input v-model="kw" class="sin" placeholder="搜尋…" />
      </div>
      <div class="arow">
        <button type="button" class="lnk" @click="selectShown">全選{{ kw ? '（符合的）' : '' }}</button>
        <button type="button" class="lnk" @click="clearAll">清除</button>
      </div>
      <ul class="list">
        <li v-for="o in shown" :key="o.key">
          <label>
            <input type="checkbox" :checked="modelValue.includes(o.key)" @change="toggle(o.key)" />
            <span class="t">{{ o.label }}</span>
            <span class="n">{{ o.n }}</span>
          </label>
        </li>
        <li v-if="!shown.length" class="empty">沒有符合的選項</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.mf { position: relative; display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
.lb { color: var(--ink-soft); }
.tg { padding: 4px 10px; border: 1px solid var(--border-strong); border-radius: 5px;
      background: var(--card); color: var(--ink); cursor: pointer; display: inline-flex; gap: 6px; align-items: center; }
.tg.on { border-color: var(--brand); color: var(--brand-dark); font-weight: 600; }
.cx { font-size: 10px; opacity: .6; }
.pop { position: absolute; top: 100%; left: 0; z-index: 30; margin-top: 4px; min-width: 220px;
       max-width: 320px; background: var(--card); border: 1px solid var(--border-strong);
       border-radius: 8px; box-shadow: var(--shadow); padding: 8px; }
.srow { margin-bottom: 6px; }
.sin { width: 100%; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 5px;
       background: var(--card); color: var(--ink); font-size: 12px; }
.arow { display: flex; gap: 10px; margin-bottom: 4px; }
.lnk { background: none; border: none; color: var(--brand-dark); cursor: pointer; font-size: 12px; padding: 0; }
.list { list-style: none; margin: 0; padding: 0; max-height: 280px; overflow: auto; }
.list label { display: flex; align-items: center; gap: 7px; padding: 4px 4px; border-radius: 4px; cursor: pointer; }
.list label:hover { background: var(--sub); }
.t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); font-size: 12px; padding: 6px 4px; }
</style>
