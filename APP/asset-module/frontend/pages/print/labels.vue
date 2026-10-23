<script setup lang="ts">
// 標籤貼紙：選設備 → 印在 A4 標籤紙上（預設 3×8、70×37mm；別種規格放 app_settings label_presets）。
// 來源跟畫面一樣：?room=&rack=（一櫃）、?room=（一個機房）、?system=（一個系統）、?q=&angle=（搜尋結果）。
// 每張：主機名（大字）、負責人（保管者；沒有就用擁有者）、IP、小字資產序號。
// 字太長會自動縮小到放得下；縮到最小還放不下就換行並在預覽標出來——不會默默被切掉。
definePageMeta({ layout: false })

interface Preset { id: string; name: string; cols: number; rows: number; w_mm: number; h_mm: number; margin_top_mm: number; margin_left_mm: number; gap_x_mm: number; gap_y_mm: number }
interface Item { key: string; name: string; contact: string | null; contact_is_owner: boolean; ip: string | null; asset_serial: string; room: string; rack: string | null; u: string }

const route = useRoute()
const { apiFetch } = useApi()
const qv = (k: string) => (typeof route.query[k] === 'string' && route.query[k]) ? String(route.query[k]) : undefined
const items = ref<Item[]>([])
const presets = ref<Preset[]>([])
const err = ref('')
try {
  const r = await apiFetch<{ presets: Preset[]; items: Item[] }>('/api/racks/labels', {
    query: { room: qv('room'), rack: qv('rack'), api_id: qv('system'), q: qv('q'), angle: qv('angle') },
  })
  items.value = r.items
  presets.value = r.presets
} catch (e: any) {
  err.value = e?.data?.detail ?? e?.message ?? '未知錯誤'
}
const presetId = ref(presets.value[0]?.id ?? '')
const preset = computed(() => presets.value.find(p => p.id === presetId.value) ?? presets.value[0])

// 勾選（預設全選）＋可排序、有項次的清單
const picked = ref<Record<string, boolean>>(Object.fromEntries(items.value.map(i => [i.key, true])))
const rowsNumbered = computed(() => items.value.map((it, i) => ({ ...it, no: i + 1, picked_sort: picked.value[it.key] ? 1 : 0 })))
const { sortKey, sortDir, toggle, sorted } = useSort(rowsNumbered, 'no')
function setAll(v: boolean) { picked.value = Object.fromEntries(items.value.map(i => [i.key, v])) }
const chosen = computed(() => items.value.filter(i => picked.value[i.key]))
const skip = ref(0)   // 從第幾格開始印（用剩的標籤紙）
const pages = computed(() => {
  const p = preset.value
  if (!p) return []
  const per = p.cols * p.rows
  const cells: (Item | null)[] = [...Array(Math.max(0, Math.min(skip.value, per - 1))).fill(null), ...chosen.value]
  const out: (Item | null)[][] = []
  for (let i = 0; i < cells.length; i += per) out.push(cells.slice(i, i + per))
  return out
})

// 縮字：量實際寬度，放不下就一點一點縮；縮到最小還不行就換行並標記
const overflowCount = ref(0)
const sheetsEl = ref<HTMLElement | null>(null)
async function fitAll() {
  await nextTick()
  let over = 0
  for (const el of Array.from(sheetsEl.value?.querySelectorAll<HTMLElement>('.fit') ?? [])) {
    const max = Number(el.dataset.max || 12)
    const min = Number(el.dataset.min || 6)
    el.classList.remove('wrap')
    let size = max
    el.style.fontSize = `${size}pt`
    while (el.scrollWidth > el.clientWidth + 0.5 && size > min) {
      size -= 0.5
      el.style.fontSize = `${size}pt`
    }
    if (el.scrollWidth > el.clientWidth + 0.5) {
      el.classList.add('wrap')
      el.closest('.label')?.classList.add('over')
      over += 1
    }
  }
  overflowCount.value = over
}
onMounted(fitAll)
watch([pages, presetId], () => {
  for (const el of Array.from(sheetsEl.value?.querySelectorAll<HTMLElement>('.label.over') ?? [])) el.classList.remove('over')
  fitAll()
}, { deep: true })
function doPrint() { window.print() }
const mm = (v: number) => `${v}mm`
</script>

<template>
  <div class="printroot">
    <div class="noprint">
      <div class="bar">
        <b>標籤貼紙</b>
        <label>標籤紙
          <select v-model="presetId"><option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option></select>
        </label>
        <label>從第 <input v-model.number="skip" type="number" min="0" :max="(preset?.cols ?? 3) * (preset?.rows ?? 8) - 1" class="num"> 格之後開始</label>
        <span>已選 {{ chosen.length }}／{{ items.length }} 張，{{ pages.length }} 頁</span>
        <span v-if="overflowCount" class="warn">{{ overflowCount }} 處字太長已換行（預覽中框起來的那幾張）</span>
        <button class="btn" :disabled="!chosen.length" @click="doPrint">列印</button>
        <span class="hint">列印設定：縮放 100%（實際大小）、邊界「無」、關掉頁首頁尾。</span>
      </div>
      <p v-if="err" class="err">標籤資料載入失敗：{{ err }}</p>
      <div class="pick">
        <button class="mini" @click="setAll(true)">全選</button>
        <button class="mini" @click="setAll(false)">全不選</button>
        <table class="tbl">
          <thead><tr>
            <SortTh k="picked_sort" :active="sortKey" :dir="sortDir" @sort="toggle">印</SortTh>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
            <SortTh k="name" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
            <SortTh k="contact" :active="sortKey" :dir="sortDir" @sort="toggle">負責人</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
            <SortTh k="rack" :active="sortKey" :dir="sortDir" @sort="toggle">機櫃</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="it in sorted" :key="it.key">
              <td><input v-model="picked[it.key]" type="checkbox"></td>
              <td>L{{ it.no }}</td>
              <td>{{ it.name }}</td>
              <td>{{ it.contact || '（無）' }}<span v-if="it.contact_is_owner" class="hint">（擁有者）</span></td>
              <td>{{ it.ip || '' }}</td>
              <td>{{ it.asset_serial }}</td>
              <td>{{ it.room }} {{ it.rack || '' }} {{ it.u }}</td>
            </tr>
            <tr v-if="!sorted.length"><td colspan="7" class="hint">沒有可印的實體設備</td></tr>
          </tbody>
        </table>
      </div>
      <h3>預覽</h3>
    </div>

    <div ref="sheetsEl" class="sheets">
      <div v-for="(pg, pi) in pages" :key="pi" class="a4"
           :style="{ paddingTop: mm(preset!.margin_top_mm), paddingLeft: mm(preset!.margin_left_mm),
                     gridTemplateColumns: `repeat(${preset!.cols}, ${preset!.w_mm}mm)`,
                     gridAutoRows: mm(preset!.h_mm), columnGap: mm(preset!.gap_x_mm), rowGap: mm(preset!.gap_y_mm) }">
        <div v-for="(it, ci) in pg" :key="ci" class="label">
          <template v-if="it">
            <div class="fit l-name" data-max="15" data-min="7">{{ it.name }}</div>
            <div class="fit l-line" data-max="10" data-min="6">{{ it.contact || '負責人：（無）' }}</div>
            <div class="fit l-line" data-max="10" data-min="6">{{ it.ip || '' }}</div>
            <div class="fit l-small" data-max="7" data-min="5">{{ it.asset_serial }}</div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
@page { size: A4 portrait; margin: 0; }
</style>
<style scoped>
.printroot { font-family: 'Microsoft JhengHei', 'Noto Sans TC', sans-serif; color: var(--ink); background: var(--paper); padding: 16px; min-height: 100vh; }
.bar { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; font-size: 13px; }
.bar select, .num { font-family: inherit; font-size: 13px; padding: 3px 6px; }
.num { width: 56px; }
.btn { font-family: inherit; font-size: 14px; padding: 6px 18px; border-radius: 8px; border: 0; background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled { opacity: .5; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; margin: 0 6px 6px 0; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.hint { color: var(--muted); font-size: 12px; }
.warn { color: var(--warn-text); font-size: 12.5px; font-weight: 600; }
.err { color: var(--bad); }
.pick { max-height: 300px; overflow: auto; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 8px; margin-bottom: 12px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 5px 6px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 4px 6px; border-bottom: 1px solid var(--line); }
.sheets { display: flex; flex-direction: column; align-items: center; gap: 16px; }
.a4 { width: 210mm; height: 297mm; background: var(--card); box-shadow: 0 0 0 1px var(--border-strong); display: grid; align-content: start; overflow: hidden; page-break-after: always; break-after: page; }
.a4:last-child { page-break-after: auto; break-after: auto; }
.label { box-sizing: border-box; padding: 2.5mm 3mm; overflow: hidden; display: flex; flex-direction: column; justify-content: center; gap: .6mm; outline: 1px dashed var(--border); color: var(--ink); }
.label.over { outline: 2px solid var(--warn); }
.fit { white-space: nowrap; overflow: hidden; line-height: 1.15; }
.fit.wrap { white-space: normal; word-break: break-all; overflow: visible; }
.l-name { font-weight: 700; }
.l-small { font-family: Consolas, 'Courier New', monospace; }
@media print {
  .noprint { display: none !important; }
  .printroot { padding: 0; background: none; }
  .sheets { gap: 0; }
  .a4 { box-shadow: none; }
  .label { outline: none; }
  .label.over { outline: none; }
}
</style>
