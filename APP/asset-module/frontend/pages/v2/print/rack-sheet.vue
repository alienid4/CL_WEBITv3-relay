<script setup lang="ts">
// V60（2.047.2）：搬到 /v2/print/rack-sheet（舊 /print/rack-sheet 轉過來，查詢參數照帶）。
// A4 機櫃盤點表（一櫃一頁）：一櫃（?room=&rack=）、整個機房（?room=）或一個系統碰到的櫃（?system=）。
// 用瀏覽器列印（@page A4），不靠任何套件；黑白印得清楚：不靠顏色分辨，序號用最大的等寬字。
// 資料：/api/racks/print-sheet（rack_layout.print_sheets，列數＝那櫃設備數）。
// 2.044.8：拿掉資產序號欄、沒序號留空白、備註只放短標記；一櫃剛好一張 A4（設備多的櫃自動縮字級／行高）。
import { shortMarks } from '~/utils/rackSheetMarks'

definePageMeta({ layout: false })

interface Row { u: string; model: string | null; serial: string | null; asset_serial: string; name: string; api_ids: string[]; marks: string[]; highlight: boolean }
interface Sheet { room: string; rack: string; floor: string | null; device_count: number; record_count?: number; rows: Row[] }

const route = useRoute()
const { apiFetch } = useApi()
const q = (k: string) => (typeof route.query[k] === 'string' && route.query[k]) ? String(route.query[k]) : undefined
const sheets = ref<Sheet[]>([])
const blankRows = ref(5)
const err = ref('')
try {
  const r = await apiFetch<{ sheets: Sheet[]; blank_rows: number }>('/api/racks/print-sheet', {
    query: { room: q('room'), rack: q('rack'), api_id: q('system') },
  })
  sheets.value = r.sheets
  blankRows.value = r.blank_rows
} catch (e: any) {
  err.value = e?.data?.detail ?? e?.message ?? '未知錯誤'
}
const today = new Date().toLocaleDateString('zh-TW')
const title = computed(() => q('system') ? `系統 ${q('system')} 碰到的機櫃` : q('rack') ? `${q('room')} ${q('rack')}` : `${q('room') ?? ''} 全部機櫃`)
function doPrint() { window.print() }

// 一櫃一張 A4：先依列數（設備＋「櫃上有但未登記」標題＋空白列）挑字級，畫出來後再量，超過一頁就再縮一級
const MAX_DENSE = 4
function denseOf(sh: Sheet) {
  const n = sh.rows.length + 1 + blankRows.value
  return n <= 24 ? 0 : n <= 32 ? 1 : n <= 42 ? 2 : n <= 56 ? 3 : 4
}
const dense = ref<Record<string, number>>({})
const keyOf = (sh: Sheet) => sh.room + '|' + sh.rack
const sheetEls = ref<HTMLElement[]>([])
async function fitPages() {
  dense.value = Object.fromEntries(sheets.value.map(sh => [keyOf(sh), denseOf(sh)]))
  await nextTick()
  const probe = document.createElement('div')
  probe.style.cssText = 'position:absolute;visibility:hidden;height:100mm;width:1px'
  document.body.appendChild(probe)
  const mm = probe.getBoundingClientRect().height / 100
  probe.remove()
  if (!mm) return
  const limit = 274 * mm          // A4 297mm − 上下邊界 20mm − 3mm 保險
  for (let guard = 0; guard < MAX_DENSE; guard++) {
    let changed = false
    for (const el of sheetEls.value) {
      const k = el.dataset.key ?? ''
      const inner = el.scrollHeight - 12 * mm      // 畫面上多了 6mm 上下內距，列印時沒有
      if (inner > limit && (dense.value[k] ?? 0) < MAX_DENSE) { dense.value[k] = (dense.value[k] ?? 0) + 1; changed = true }
    }
    if (!changed) break
    await nextTick()
  }
}
onMounted(fitPages)
</script>

<template>
  <div class="printroot">
    <div class="noprint bar">
      <b>機櫃盤點表：{{ title }}</b>
      <span>{{ sheets.length }} 櫃，一櫃一頁</span>
      <button class="btn" :disabled="!sheets.length" @click="doPrint">列印</button>
      <span class="hint">A4 直印；請在列印設定關掉「頁首頁尾」、縮放選「預設／100%」。</span>
    </div>
    <p v-if="err" class="noprint err">盤點表載入失敗：{{ err }}</p>
    <p v-else-if="!sheets.length" class="noprint">沒有可列印的機櫃（這些設備都沒有可判讀的機櫃資料）。</p>

    <section v-for="(sh, i) in sheets" :key="keyOf(sh)" ref="sheetEls" :data-key="keyOf(sh)" class="sheet" :class="'d' + (dense[keyOf(sh)] ?? denseOf(sh))">
      <header class="sh-head">
        <div class="sh-title">機櫃盤點表　{{ sh.room }}　<span class="big">{{ sh.rack }}</span><span v-if="sh.floor">　{{ sh.floor }}</span></div>
        <div class="sh-meta">列印日期 {{ today }}　第 {{ i + 1 }}／{{ sheets.length }} 櫃　{{ sh.device_count }} 台<template v-if="sh.record_count && sh.record_count !== sh.device_count">（{{ sh.record_count }} 筆登記）</template></div>
      </header>
      <table class="sh">
        <colgroup>
          <col class="c-u"><col class="c-model"><col class="c-serial"><col class="c-name"><col class="c-api">
          <col class="c-box"><col class="c-box"><col class="c-box2"><col class="c-note">
        </colgroup>
        <thead><tr>
          <th class="c-u">U</th><th>機型</th><th>序號</th>
          <th>主機名稱</th><th>APID</th>
          <th class="c-box">在</th><th class="c-box">不在</th><th class="c-box">資料<br>不符</th><th>標記／備註</th>
        </tr></thead>
        <tbody>
          <tr v-for="(r, j) in sh.rows" :key="r.asset_serial + j">
            <td class="c-u">{{ r.u || '?' }}</td>
            <td class="t-model">{{ upText(r.model) }}</td>
            <td class="serial">{{ upText(r.serial) }}</td>
            <td class="t-name">{{ r.highlight ? '★ ' : '' }}{{ r.name }}</td>
            <td class="t-api"><template v-for="(a, k) in r.api_ids" :key="a">{{ k ? ' ' : '' }}<span class="nw">{{ a }}</span></template></td>
            <td class="c-box">☐</td><td class="c-box">☐</td><td class="c-box">☐</td>
            <td class="t-note">{{ shortMarks(r.marks) }}</td>
          </tr>
          <tr class="sub"><td colspan="9">櫃上有但未登記</td></tr>
          <tr v-for="n in blankRows" :key="'b' + n" class="blank">
            <td /><td /><td /><td /><td /><td class="c-box">☐</td><td class="c-box">☐</td><td class="c-box">☐</td><td />
          </tr>
        </tbody>
      </table>
      <footer class="sign">
        <span>盤點人：＿＿＿＿＿＿＿＿</span><span>日期：＿＿＿＿＿＿</span><span>複核：＿＿＿＿＿＿＿＿</span>
        <span class="legend">★＝本次查詢的系統；U 為「?」＝登記只寫了機櫃、沒寫 U 位</span>
      </footer>
    </section>
  </div>
</template>

<style>
@page { size: A4 portrait; margin: 10mm; }
</style>
<style scoped>
.printroot { font-family: 'Microsoft JhengHei', 'Noto Sans TC', sans-serif; color: var(--ink); background: var(--card); padding: 16px; min-height: 100vh; }
.bar { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; font-size: 14px; }
.btn { font-family: inherit; font-size: 14px; padding: 6px 18px; border-radius: 8px; border: 0; background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled { opacity: .5; }
.hint { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
/* 畫面上 190mm＋6mm 內距；列印時 190mm 滿版（A4 210mm − 左右 10mm） */
.sheet { --fs: 10pt; --sfs: 12.5pt; --pad: 1.2mm; --blank: 8mm;
  width: 190mm; box-sizing: content-box; margin: 0 auto 24px; padding: 6mm; border: 1px solid var(--border-strong);
  page-break-after: always; break-after: page; page-break-inside: avoid; font-size: var(--fs); }
.sheet:last-child { page-break-after: auto; break-after: auto; }
/* 設備多的櫃：一級一級縮字級／行高，保證一櫃一張 */
.sheet.d1 { --fs: 9pt; --sfs: 11pt; --pad: .8mm; --blank: 6.5mm; }
.sheet.d2 { --fs: 8pt; --sfs: 9.5pt; --pad: .5mm; --blank: 5mm; }
.sheet.d3 { --fs: 7pt; --sfs: 8pt; --pad: .3mm; --blank: 4mm; }
.sheet.d4 { --fs: 6pt; --sfs: 7pt; --pad: .2mm; --blank: 3.2mm; }
.sh-head { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid currentColor; padding-bottom: 2mm; margin-bottom: 2mm; }
.sh-title { font-size: 13pt; font-weight: 700; }
.sh-title .big { font-size: 18pt; }
.sh-meta { font-size: 10pt; }
.sheet.d3 .sh-head, .sheet.d4 .sh-head { padding-bottom: 1mm; margin-bottom: 1mm; }
table.sh { width: 100%; border-collapse: collapse; table-layout: fixed; line-height: 1.2; }
table.sh thead { display: table-header-group; }
table.sh th, table.sh td { border: 1px solid currentColor; padding: var(--pad) 1mm; vertical-align: middle; font-size: var(--fs); word-break: normal; overflow-wrap: break-word; }
table.sh th { font-weight: 700; text-align: left; font-size: min(var(--fs), 9pt); line-height: 1.1; padding: .6mm .8mm; }
table.sh tr { page-break-inside: avoid; break-inside: avoid; }
/* 欄寬（合計 190mm）：U 不換行、APID／主機名不被切、勾選欄窄、備註最寬 */
col.c-u { width: 15mm; }
col.c-model { width: 22mm; }
col.c-serial { width: 30mm; }
col.c-name { width: 30mm; }
col.c-api { width: 15mm; }
col.c-box { width: 9mm; }
col.c-box2 { width: 10mm; }
col.c-note { width: auto; }
.c-u { text-align: center; white-space: nowrap; }
.c-box { text-align: center; padding-left: 0 !important; padding-right: 0 !important; }
td.c-box { font-size: calc(var(--fs) + 2pt) !important; }
th.c-box { text-align: center; }
.t-api .nw { white-space: nowrap; }
.serial { font-family: Consolas, 'Courier New', monospace; font-size: var(--sfs) !important; font-weight: 700; letter-spacing: .3px; word-break: break-all; }
tr.sub td { font-weight: 700; border-top: 2px solid currentColor; }
tr.blank td { height: var(--blank); }
.sign { display: flex; gap: 10mm; flex-wrap: wrap; margin-top: 5mm; font-size: 10.5pt; }
.sign .legend { font-size: 10pt; width: 100%; }
.sheet.d2 .sign, .sheet.d3 .sign, .sheet.d4 .sign { margin-top: 2.5mm; font-size: 9pt; }
.sheet.d2 .sign .legend, .sheet.d3 .sign .legend, .sheet.d4 .sign .legend { font-size: 8pt; }
@media print {
  .noprint { display: none !important; }
  .printroot { padding: 0; background: none; }
  .sheet { border: 0; margin: 0; padding: 0; width: auto; }
}
</style>
