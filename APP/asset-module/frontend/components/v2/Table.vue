<script setup lang="ts">
// 2.x 表格（全站同一個）：點標題排序、再點反向；每列有項次；空格一律寫「—」不留白。link(row) 有回值的欄位可以點；
// text(row) 可以讓畫面文字跟排序值分開（例：數量 null 顯示「無資料」、照數字排）。
// selectable＝每列前面一個勾選框（批次動作用），勾選結果用 v-model:selected（{ rowKey 值: true }）；canSelect(row) 可以讓某些列不能勾。
//
// 項次（找碴 C19，2.006.3）：列上有 __no 就用它（頁面在篩選前先編好、或伺服器給的固定編號），沒有才用目前清單的順序。
// 所以篩選、換排序、換頁之後，同一筆還是同一個號碼；頁面匯出也用同一個 __no。
// 分頁（找碴 C7）：超過一頁（max，預設 100）就出現分頁列「第 x／y 頁」，不再「先列 N 筆、點標題排序看其他」。
// server＝伺服器端分頁／排序（2.1 主機查詢）：表格不自己排、不自己切頁，只送出 sort／page 事件。
// cell-<欄代號> 具名插槽（Q91）：欄位要放下拉選單／按鈕時用（例：帳號權限表的「等級」下拉）；匯出仍用 text(r)／欄位原值。
// 匯出（2.007.7，使用者：「我要說一次你才幫我加一次」→ 全站預設）：每張表都有「匯出」，匯出目前這份清單的全部列
// （篩選、排序後、不只這一頁；伺服器端分頁的表由 exportAll 向同一支 API 拿全部），項次、欄名、顯示文字跟畫面一樣。
// 檔名＝頁名＋頁籤＋日期（useV2ExportName）。
// mono 欄（IP、數字、時間）不換行（2.007.13 使用者：「IP 的欄位太窄」）。
// tone(row)＝把這格畫成有顏色的標籤（回 'prod'｜'dr'｜'test'｜'none'，沒回值就是一般文字；例：環境「正式／測試」要一眼看得出來）；排序與匯出仍是原文字。
// brief(row)＝長說明先顯示一句短的＋「⋯」，點了才展開全文（使用者：「說明不用一次全部顯示…太佔版面」）；匯出一律是全文。
interface Col { k: string; label: string; mono?: boolean; link?: (r: any) => string | null; text?: (r: any) => any; brief?: (r: any) => string; tone?: (r: any) => string }
interface Server { total: number; page: number; size: number; sortKey: string; sortDir: 'asc' | 'desc' }
const props = withDefaults(defineProps<{
  rows: Record<string, any>[]; cols: Col[]
  prefix?: string; empty?: string; max?: number
  selectable?: boolean; rowKey?: string; selected?: Record<string, boolean>; canSelect?: (r: any) => boolean
  server?: Server | null; unit?: string
  exportName?: string; exportAll?: (() => Promise<Record<string, any>[]>) | null
}>(), { prefix: '', empty: '無資料', max: 100, selectable: false, rowKey: 'key', selected: () => ({}), server: null, unit: '筆', exportAll: null })
const emit = defineEmits<{ 'update:selected': [v: Record<string, boolean>]; sort: [k: string]; page: [p: number, size: number] }>()
const numbered = computed(() => props.rows.map((r, i) => ({ ...r, __no: typeof r.__no === 'number' ? r.__no : i + 1 })))
const { sortKey, sortDir, toggle, sorted } = useSort(numbered, '__no')
const size = ref(props.max)
const page = ref(1)
watch(() => props.rows, () => { if (!props.server) page.value = 1 })
watch([sortKey, sortDir], () => { page.value = 1 })
const total = computed(() => (props.server ? props.server.total : props.rows.length))
const curSize = computed(() => (props.server ? props.server.size : size.value))
const curPage = computed(() => (props.server ? props.server.page : page.value))
const pages = computed(() => Math.max(1, Math.ceil(total.value / Math.max(1, curSize.value))))
const shown = computed(() => (props.server ? numbered.value : sorted.value.slice((page.value - 1) * size.value, page.value * size.value)))
const activeKey = computed(() => (props.server ? props.server.sortKey : sortKey.value))
const activeDir = computed(() => (props.server ? props.server.sortDir : sortDir.value))
function onSort(k: string) { if (props.server) emit('sort', k); else toggle(k) }
function go(p: number, s = curSize.value) {
  const n = Math.min(Math.max(1, p), Math.max(1, Math.ceil(total.value / Math.max(1, s))))
  if (props.server) emit('page', n, s)
  else { size.value = s; page.value = n }
}
const SIZES = computed(() => [...new Set([props.max, 100, 500].filter(n => n > 0))].sort((a, b) => a - b))
const val = (v: any) => (v === null || v === undefined || v === '' ? '—' : Array.isArray(v) ? v.join('、') || '—' : v)
const cell = (c: Col, r: any) => val(c.text ? c.text(r) : r[c.k])
const { exportTable, busy: exporting } = useTableExport()
const { showToast } = useToast()
const exportName = useV2ExportName()
const { isViewer } = useAuth()     // Q91：只能看的帳號不能匯出（後端另外擋；這裡不放按鈕）
async function doExport() {
  try {
    let list: any[]
    if (props.exportAll) {
      list = (await props.exportAll()).map((r, i) => ({ ...r, __no: typeof r.__no === 'number' ? r.__no : i + 1 }))
    } else {
      list = sorted.value
    }
    const head = ['項次', ...props.cols.map(c => c.label)]
    const body = list.map(r => [`${props.prefix}${r.__no}`, ...props.cols.map(c => { const v = c.text ? c.text(r) : r[c.k]; return v === null || v === undefined ? '' : Array.isArray(v) ? v.join('、') : v })])
    await exportTable(props.exportName || exportName(), head, body)
  } catch (e: any) { showToast(`匯出失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
}
const opened = ref<Record<string, boolean>>({})
const longText = (c: Col, r: any) => { const full = String(cell(c, r)); const b = c.brief ? c.brief(r) : ''; return b && b !== full ? b : '' }
const ok = (r: any) => (props.canSelect ? props.canSelect(r) : true)
function setOne(r: any, v: boolean) { emit('update:selected', { ...props.selected, [String(r[props.rowKey])]: v }) }
const allOn = computed(() => shown.value.length > 0 && shown.value.filter(ok).every(r => props.selected[String(r[props.rowKey])]))
function setAll(v: boolean) {
  const n = { ...props.selected }
  for (const r of shown.value) if (ok(r)) n[String(r[props.rowKey])] = v
  emit('update:selected', n)
}
</script>

<template>
  <div class="tw">
    <div v-if="!isViewer && (rows.length || (server && server.total))" class="xbar"><button type="button" class="xbtn" :disabled="exporting" @click="doExport">{{ exporting ? '匯出中…' : `匯出（${total.toLocaleString()} ${unit}）` }}</button></div>
    <table class="t">
      <thead><tr>
        <th v-if="selectable" class="cb"><input type="checkbox" :checked="allOn" title="全選本頁" @change="setAll(($event.target as HTMLInputElement).checked)"></th>
        <SortTh k="__no" :active="activeKey" :dir="activeDir" @sort="onSort">項次</SortTh>
        <SortTh v-for="c in cols" :key="c.k" :k="c.k" :active="activeKey" :dir="activeDir" @sort="onSort">{{ c.label }}</SortTh>
      </tr></thead>
      <tbody>
        <tr v-for="r in shown" :key="r.__no" :class="{ sel: selectable && selected[String(r[rowKey])] }">
          <td v-if="selectable" class="cb"><input type="checkbox" :disabled="!ok(r)" :checked="!!selected[String(r[rowKey])]" @change="setOne(r, ($event.target as HTMLInputElement).checked)"></td>
          <td class="mono">{{ prefix }}{{ r.__no }}</td>
          <td v-for="c in cols" :key="c.k" :class="{ mono: c.mono, brief: !!c.brief }">
            <slot v-if="$slots['cell-' + c.k]" :name="'cell-' + c.k" :row="r" />
            <NuxtLink v-else-if="c.link && c.link(r)" :to="c.link(r)!">{{ cell(c, r) }}</NuxtLink>
            <template v-else-if="longText(c, r) && !opened[`${r.__no}:${c.k}`]">{{ longText(c, r) }} <button type="button" class="more" title="看詳細" @click="opened = { ...opened, [`${r.__no}:${c.k}`]: true }">⋯</button></template>
            <template v-else-if="longText(c, r)">{{ cell(c, r) }} <button type="button" class="more" title="收起" @click="opened = { ...opened, [`${r.__no}:${c.k}`]: false }">收起</button></template>
            <span v-else-if="c.tone && c.tone(r)" class="tag" :class="'tag-' + c.tone(r)">{{ cell(c, r) }}</span>
            <template v-else>{{ cell(c, r) }}</template>
          </td>
        </tr>
        <tr v-if="!rows.length"><td :colspan="cols.length + (selectable ? 2 : 1)" class="muted">{{ empty }}</td></tr>
      </tbody>
    </table>
    <div v-if="total > curSize" class="pager">
      <button type="button" :disabled="curPage <= 1" @click="go(1)">«</button>
      <button type="button" :disabled="curPage <= 1" @click="go(curPage - 1)">‹ 上一頁</button>
      <span>第 {{ curPage }}／{{ pages }} 頁・共 {{ total.toLocaleString() }} {{ unit }}</span>
      <button type="button" :disabled="curPage >= pages" @click="go(curPage + 1)">下一頁 ›</button>
      <button type="button" :disabled="curPage >= pages" @click="go(pages)">»</button>
      <label>每頁 <select :value="curSize" @change="go(1, Number(($event.target as HTMLSelectElement).value))"><option v-for="n in SIZES" :key="n" :value="n">{{ n }}</option></select> 筆</label>
    </div>
  </div>
</template>

<style scoped>
.tw { overflow-x: auto; }
.t { width: 100%; border-collapse: collapse; font-size: 13px; }
.t :deep(th) { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.t td { padding: 6px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; white-space: pre-line; overflow-wrap: anywhere; }
.t tr.sel td { background: var(--mint); }
.cb { width: 28px; }
.t a { color: var(--link); }
.mono { font-family: var(--disp); }
.t td.mono { white-space: nowrap; }
.t td.brief { min-width: 200px; }
.more { font-family: inherit; font-size: 12px; padding: 0 6px; margin-left: 2px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.muted { color: var(--muted); font-size: 13px; }
.tag { display: inline-block; font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; white-space: nowrap; }
.tag-prod { background: var(--brand); color: var(--type-on); }
.tag-dr { background: var(--mint-deep); color: var(--brand-dark); box-shadow: inset 0 0 0 1px var(--brand); }
.tag-test { background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.tag-none { background: var(--sub); color: var(--muted); font-weight: 400; }
.xbar { display: flex; justify-content: flex-end; margin-bottom: 4px; }
.xbtn { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.xbtn:disabled { opacity: .5; cursor: default; }
.pager { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; font-size: 13px; color: var(--ink-aux); }
.pager button { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.pager button:disabled { opacity: .4; cursor: default; }
.pager select { font-family: inherit; font-size: 12px; padding: 2px 4px; border: 1px solid var(--border-strong); border-radius: 6px; }
</style>
