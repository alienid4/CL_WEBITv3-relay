<script setup lang="ts">
// A4 機櫃盤點表（一櫃一頁）：一櫃（?room=&rack=）、整個機房（?room=）或一個系統碰到的櫃（?system=）。
// 用瀏覽器列印（@page A4），不靠任何套件；黑白印得清楚：不靠顏色分辨，序號用最大的等寬字。
// 資料：/api/racks/print-sheet（rack_layout.print_sheets，列數＝那櫃設備數）。
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
</script>

<template>
  <div class="printroot">
    <div class="noprint bar">
      <b>機櫃盤點表：{{ title }}</b>
      <span>{{ sheets.length }} 櫃，一櫃一頁</span>
      <button class="btn" :disabled="!sheets.length" @click="doPrint">列印</button>
      <span class="hint">A4 直印；請在列印設定關掉「頁首頁尾」。</span>
    </div>
    <p v-if="err" class="noprint err">盤點表載入失敗：{{ err }}</p>
    <p v-else-if="!sheets.length" class="noprint">沒有可列印的機櫃（這些設備都沒有可判讀的機櫃資料）。</p>

    <section v-for="(sh, i) in sheets" :key="sh.room + sh.rack" class="sheet">
      <header class="sh-head">
        <div class="sh-title">機櫃盤點表　{{ sh.room }}　<span class="big">{{ sh.rack }}</span><span v-if="sh.floor">　{{ sh.floor }}</span></div>
        <div class="sh-meta">列印日期 {{ today }}　第 {{ i + 1 }}／{{ sheets.length }} 櫃　{{ sh.device_count }} 台<template v-if="sh.record_count && sh.record_count !== sh.device_count">（{{ sh.record_count }} 筆登記）</template></div>
      </header>
      <table class="sh">
        <thead><tr>
          <th class="c-u">U</th><th class="c-model">機型</th><th class="c-serial">序號</th><th class="c-asset">資產序號</th>
          <th class="c-name">主機名稱</th><th class="c-api">APID</th>
          <th class="c-box">在</th><th class="c-box">不在</th><th class="c-box">資料不符</th><th class="c-note">標記／備註</th>
        </tr></thead>
        <tbody>
          <tr v-for="(r, j) in sh.rows" :key="r.asset_serial + j">
            <td class="c-u">{{ r.u || '?' }}</td>
            <td class="c-model">{{ upText(r.model) }}</td>
            <td class="c-serial serial">{{ upText(r.serial) || '（無序號）' }}</td>
            <td class="c-asset mono">{{ r.asset_serial }}</td>
            <td class="c-name">{{ r.highlight ? '★ ' : '' }}{{ r.name }}</td>
            <td class="c-api">{{ r.api_ids.join(' ') }}</td>
            <td class="c-box">☐</td><td class="c-box">☐</td><td class="c-box">☐</td>
            <td class="c-note">{{ r.marks.join('；') }}</td>
          </tr>
          <tr class="sub"><td colspan="10">櫃上有但未登記</td></tr>
          <tr v-for="n in blankRows" :key="'b' + n" class="blank">
            <td /><td /><td /><td /><td /><td /><td class="c-box">☐</td><td class="c-box">☐</td><td class="c-box">☐</td><td />
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
.sheet { width: 190mm; margin: 0 auto 24px; padding: 6mm; border: 1px solid var(--border-strong); page-break-after: always; break-after: page; font-size: 10pt; }
.sheet:last-child { page-break-after: auto; break-after: auto; }
.sh-head { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid currentColor; padding-bottom: 2mm; margin-bottom: 2mm; }
.sh-title { font-size: 13pt; font-weight: 700; }
.sh-title .big { font-size: 18pt; }
.sh-meta { font-size: 10pt; }
table.sh { width: 100%; border-collapse: collapse; table-layout: fixed; }
table.sh thead { display: table-header-group; }
table.sh th, table.sh td { border: 1px solid currentColor; padding: 1.2mm 1mm; vertical-align: middle; font-size: 10pt; word-break: break-all; }
table.sh th { font-weight: 700; text-align: left; }
table.sh tr { page-break-inside: avoid; break-inside: avoid; }
.c-u { width: 11mm; text-align: center; }
.c-model { width: 24mm; }
.c-serial { width: 34mm; }
.c-asset { width: 24mm; }
.c-name { width: 28mm; }
.c-api { width: 14mm; }
.c-box { width: 9mm; text-align: center; font-size: 12pt !important; }
.c-note { width: auto; }
.serial { font-family: Consolas, 'Courier New', monospace; font-size: 12.5pt !important; font-weight: 700; letter-spacing: .3px; }
.mono { font-family: Consolas, 'Courier New', monospace; }
tr.sub td { font-weight: 700; border-top: 2px solid currentColor; }
tr.blank td { height: 8mm; }
.sign { display: flex; gap: 10mm; flex-wrap: wrap; margin-top: 5mm; font-size: 10.5pt; }
.sign .legend { font-size: 10pt; width: 100%; }
@media print {
  .noprint { display: none !important; }
  .printroot { padding: 0; background: none; }
  .sheet { border: 0; margin: 0; padding: 0; width: auto; }
}
</style>
