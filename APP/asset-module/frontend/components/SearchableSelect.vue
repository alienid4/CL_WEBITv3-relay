<script setup lang="ts">
// 可搜尋的下拉選單（combobox）——取代原生 <select>。
//
// 為什麼要做：手動新增資產的「部門」有 20+ 個選項、OS 版本更多，原生 select 只能一路滾，
// 使用者已經知道自己要選「數據分析部」卻還得在清單裡用眼睛找。打三個字就到，差很多。
// （2026-08-15 使用者以部門欄位為例提的需求。）
//
// 刻意的行為：
//  - 選項少（< searchThreshold）就不顯示搜尋框：五個選項還要先點搜尋框反而礙事
//  - 只能選清單裡的值：這是防呆選單，自由輸入的退路由外層的「自行輸入」切換負責
//  - 鍵盤可用：↑↓ 選、Enter 確定、Esc 收；開啟時自動聚焦搜尋框，可以直接打字
//  - 比對忽略大小寫與前後空白，中文則是單純的子字串比對（部門/OS 名稱不需要拼音）
interface Props {
  modelValue: string
  options: string[]
  placeholder?: string
  disabled?: boolean
  /** 選項達這個數量才顯示搜尋框 */
  searchThreshold?: number
}
const props = withDefaults(defineProps<Props>(), {
  placeholder: '請選擇',
  disabled: false,
  searchThreshold: 8,
})
const emit = defineEmits<{ 'update:modelValue': [string] }>()

const open = ref(false)
const q = ref('')
const cursor = ref(-1)
const boxRef = ref<HTMLElement | null>(null)
const searchRef = ref<HTMLInputElement | null>(null)
const listRef = ref<HTMLElement | null>(null)

const showSearch = computed(() => props.options.length >= props.searchThreshold)
const filtered = computed(() => {
  const needle = q.value.trim().toLowerCase()
  if (!needle) return props.options
  return props.options.filter((o) => o.toLowerCase().includes(needle))
})

function toggle() {
  if (props.disabled) return
  open.value ? close() : openPanel()
}
function openPanel() {
  open.value = true
  q.value = ''
  // 游標預設停在目前選中的那一項，開啟後直接按 Enter 不會誤改成別的值
  cursor.value = props.options.indexOf(props.modelValue)
  nextTick(() => {
    searchRef.value?.focus()
    scrollToCursor()
  })
}
function close() {
  open.value = false
  cursor.value = -1
}
function pick(v: string) {
  emit('update:modelValue', v)
  close()
}

// 清單捲動時把游標那一項帶進可視範圍，不然鍵盤選到第 20 項畫面還停在最上面
function scrollToCursor() {
  const el = listRef.value?.querySelector<HTMLElement>('.opt.on')
  el?.scrollIntoView({ block: 'nearest' })
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.preventDefault(); close(); return }
  if (!open.value) {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPanel() }
    return
  }
  const n = filtered.value.length
  if (e.key === 'ArrowDown' && n) {
    e.preventDefault()
    cursor.value = (indexInFiltered() + 1) % n
    syncCursorToOption()
  } else if (e.key === 'ArrowUp' && n) {
    e.preventDefault()
    cursor.value = (indexInFiltered() - 1 + n) % n
    syncCursorToOption()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    const v = filtered.value[indexInFiltered()] ?? filtered.value[0]
    // 只剩一個候選時直接選它——打完字按 Enter 就走，不用再多按一次方向鍵
    if (v !== undefined) pick(v)
  }
}
// cursor 存的是「filtered 裡的位置」；篩選字串一變、位置就得重算，不然會指到別項
function indexInFiltered() {
  return cursor.value < 0 || cursor.value >= filtered.value.length ? 0 : cursor.value
}
function syncCursorToOption() {
  nextTick(scrollToCursor)
}
watch(q, () => { cursor.value = 0 })

function onDocClick(e: MouseEvent) {
  if (boxRef.value && !boxRef.value.contains(e.target as Node)) close()
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div ref="boxRef" class="ss" :class="{ disabled }">
    <button
      type="button"
      class="trigger"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
      @keydown="onKey"
    >
      <span :class="['val', { ph: !modelValue }]">{{ modelValue || placeholder }}</span>
      <span class="caret" aria-hidden="true">▾</span>
    </button>

    <div v-if="open" class="panel">
      <input
        v-if="showSearch"
        ref="searchRef"
        v-model="q"
        class="search"
        type="text"
        placeholder="輸入關鍵字篩選…"
        @keydown="onKey"
        @click.stop
      />
      <div ref="listRef" class="list" role="listbox">
        <button
          v-if="modelValue"
          type="button"
          class="opt clear"
          @click="pick('')"
        >{{ placeholder }}（清除）</button>
        <button
          v-for="(opt, i) in filtered"
          :key="opt"
          type="button"
          class="opt"
          :class="{ on: i === indexInFiltered(), sel: opt === modelValue }"
          role="option"
          :aria-selected="opt === modelValue"
          @click="pick(opt)"
          @mouseenter="cursor = i"
        >{{ opt }}</button>
        <p v-if="filtered.length === 0" class="empty">找不到符合「{{ q }}」的選項</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ss { position: relative; }
.trigger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-family: inherit;
  font-size: 12.5px;
  padding: 6px 10px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
  cursor: pointer;
  text-align: left;
}
.trigger:disabled { opacity: 0.5; cursor: not-allowed; }
.ss .val { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ss .val.ph { color: var(--muted); }
.caret { font-size: 10px; opacity: 0.6; flex: none; }

/* 背景用 --card-solid 而不是 --card：--card 是半透明玻璃卡，浮層用它會讓底下的
   表單欄位整片透上來，看起來像文字疊在一起（GlobalSearch.vue 踩過同一個坑）。 */
.panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 60;
  background: var(--card-solid, #101c19);
  border: 1px solid var(--border-strong);
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.5);
  padding: 6px;
  min-width: 200px;
}
.search {
  width: 100%;
  font-family: inherit;
  font-size: 12.5px;
  padding: 6px 8px;
  margin-bottom: 6px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
  outline: none;
}
.search:focus { border-color: var(--brand, #009142); }
.list { max-height: 240px; overflow-y: auto; }
.opt {
  display: block;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  color: inherit;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.5;
  padding: 6px 8px;
  cursor: pointer;
}
.opt.on { background: rgba(0,145,66,0.12); }
.opt.sel { color: var(--brand, #009142); font-weight: 700; }
.opt.clear { color: var(--muted); }
.empty { margin: 0; padding: 10px 8px; font-size: 11.5px; color: var(--muted); }
</style>
