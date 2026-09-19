<script setup lang="ts">
// OS × 環境 的納管狀態交叉表（2026-09-16 使用者：「OS 為列、環境為欄，格子放納管」）。
//
// 「還有 5244 台要處理」是一個數字，但它不告訴人該從哪裡下手。
// 補佈納管是按 OS 分批做的（Linux 一套腳本、Windows 一套、AIX 又一套），
// 能不能動又要看環境（正式要排維護時間、測試可以隨時來）——
// 所以「Linux × 測試 還有 312 台沒納管」才是可以直接排進行程的單位。
//
// 每一格不是只放一個數字：給「12/45」才知道還有 33 台要做；
// 分母**不含**退役與非納管設備，不然比率永遠到不了 100%，那個數字就沒人看了。
// 每一格都可以點進漏斗頁看是哪幾台（鐵規則：每個數字都要能下鑽）。

interface Cell {
  total: number; onboarded: number; collected: number
  needs_action: number; excluded: number
  coverage: number | null; by_stage: Record<string, number>
}
interface Matrix {
  rows: string[]
  cols: { key: string; label: string }[]
  cells: Record<string, Record<string, Cell>>
  row_totals: Record<string, Cell>
  col_totals: Record<string, Cell>
  grand: Cell
  scan_time: string | null
  duplicates_merged: number
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const rt = useRuntimeConfig()

const m = ref<Matrix | null>(null)
const loading = ref(true)
// 格子裡主要看哪個數字。預設看「還要處理」——這頁的用途是排工作，不是看成績
const metric = ref<'needs_action' | 'onboarded' | 'total'>('needs_action')

const METRIC_LABEL = {
  needs_action: '還要處理',
  onboarded: '已納管',
  total: '總台數',
}

async function load() {
  loading.value = true
  try {
    m.value = await apiFetch<Matrix>('/api/stats/onboard-matrix')
  } catch (e: any) {
    showToast(`交叉表載入失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally {
    loading.value = false
  }
}
onMounted(load)

function cellOf(os: string, env: string): Cell | null {
  return m.value?.cells?.[os]?.[env] ?? null
}
function main(c: Cell | null) {
  if (!c) return 0
  return metric.value === 'needs_action' ? c.needs_action
    : metric.value === 'onboarded' ? c.onboarded + c.collected
      : c.total
}
// 底色照「顧得到的比率」，不是照主要數字——不然切換顯示指標時顏色會跟著亂跳
function tone(c: Cell | null) {
  if (!c || c.coverage === null) return 'none'
  if (c.coverage >= 90) return 'good'
  if (c.coverage >= 50) return 'warn'
  return 'bad'
}
function tip(c: Cell | null) {
  if (!c) return ''
  const parts = [
    `總台數 ${c.total}`,
    `已納管 ${c.onboarded}`,
    c.collected ? `已收集（設備）${c.collected}` : '',
    `還要處理 ${c.needs_action}`,
    c.excluded ? `不需處理（退役／非納管）${c.excluded}` : '',
    c.coverage === null ? '' : `納管率 ${c.coverage}%（分母不含退役與非納管設備）`,
    '',
    ...Object.entries(c.by_stage).map(([s, n]) => `　${s}：${n}`),
  ]
  return parts.filter(Boolean).join('\n')
}
// 點格子 → 漏斗頁帶著同樣的條件（那頁才有逐台清單與可執行的動作）
function drillTo(os: string | null, env: string | null) {
  const q: Record<string, string> = {}
  if (os) q.os_type = os
  if (env) q.env_group = env
  return { path: '/pipeline', query: q }
}
</script>

<template>
  <section class="mx">
    <div class="hd">
      OS × 環境 的納管狀態
      <InfoNote>
        列是 <b>OS 類型</b>、欄是<b>環境</b>，格子裡是該組合的台數。<br><br>
        為什麼這樣切：補佈納管是<b>按 OS 分批</b>做的（Linux／Windows／AIX 腳本不同），
        能不能動又要看<b>環境</b>（正式要排維護時間、測試可以隨時來）。
        所以「Linux × 非正式 還有 N 台沒納管」才是可以直接排進行程的單位。<br><br>
        <b>納管率的分母不含</b>已退役與非納管設備——把它們算進去，比率永遠到不了 100%，
        那個數字就沒人看了。<b>每一格都可以點</b>，會帶著同樣條件跳到納管漏斗看是哪幾台。<br><br>
        台數是<b>照主機算</b>（同主機名＋IP 的重複登記收成一台），跟納管漏斗同一份資料。
      </InfoNote>
    </div>

    <div v-if="loading" class="dim">載入中…</div>
    <template v-else-if="m">
      <div class="bar">
        <span class="lb">格子顯示</span>
        <div class="segs">
          <button v-for="(lb, k) in METRIC_LABEL" :key="k" type="button"
                  :class="{ on: metric === k }" @click="metric = k as any">{{ lb }}</button>
        </div>
        <span class="spacer" />
        <span class="dim sm">
          最近掃描 {{ m.scan_time || '—' }}
          <template v-if="m.duplicates_merged">　·　已收起 {{ m.duplicates_merged }} 筆重複登記</template>
        </span>
        <a class="btn small" :href="`${rt.public.apiBase}/api/stats/onboard-matrix/export`">⬇ 匯出 Excel</a>
      </div>

      <div class="tbl-wrap">
        <table class="grid">
          <thead>
            <tr>
              <th class="corner">OS ＼ 環境</th>
              <th v-for="c in m.cols" :key="c.key">{{ c.label }}</th>
              <th class="tot">合計</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="os in m.rows" :key="os">
              <th class="osname">{{ os }}</th>
              <td v-for="c in m.cols" :key="c.key" class="cell" :class="tone(cellOf(os, c.key))">
                <NuxtLink v-if="cellOf(os, c.key)?.total" :to="drillTo(os, c.key)" :title="tip(cellOf(os, c.key))">
                  <span class="big">{{ main(cellOf(os, c.key)) }}</span>
                  <span class="sub">／{{ cellOf(os, c.key)!.total }}</span>
                  <span v-if="cellOf(os, c.key)!.coverage !== null" class="pct">{{ cellOf(os, c.key)!.coverage }}%</span>
                </NuxtLink>
                <span v-else class="dim">—</span>
              </td>
              <td class="cell tot" :class="tone(m.row_totals[os])">
                <NuxtLink :to="drillTo(os, null)" :title="tip(m.row_totals[os])">
                  <span class="big">{{ main(m.row_totals[os]) }}</span>
                  <span class="sub">／{{ m.row_totals[os].total }}</span>
                </NuxtLink>
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th class="osname">合計</th>
              <td v-for="c in m.cols" :key="c.key" class="cell tot" :class="tone(m.col_totals[c.key])">
                <NuxtLink :to="drillTo(null, c.key)" :title="tip(m.col_totals[c.key])">
                  <span class="big">{{ main(m.col_totals[c.key]) }}</span>
                  <span class="sub">／{{ m.col_totals[c.key].total }}</span>
                </NuxtLink>
              </td>
              <td class="cell tot grand" :title="tip(m.grand)">
                <span class="big">{{ main(m.grand) }}</span>
                <span class="sub">／{{ m.grand.total }}</span>
                <span v-if="m.grand.coverage !== null" class="pct">{{ m.grand.coverage }}%</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <p class="legend">
        大字＝<b>{{ METRIC_LABEL[metric] }}</b>，斜線後是該格總台數，右下角是納管率。
        底色照納管率：<span class="sw good"></span> ≥90%　<span class="sw warn"></span> 50–89%　<span class="sw bad"></span> &lt;50%。
        滑過格子看完整拆解，點格子看是哪幾台。
      </p>
    </template>
  </section>
</template>

<style scoped>
.mx { margin-top: 8px; }
.hd { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.lb { font-size: 13px; color: var(--ink-soft); }
.segs { display: flex; }
.segs button { padding: 4px 11px; border: 1px solid var(--border-strong); background: var(--card);
               color: var(--ink); cursor: pointer; font-size: 12.5px; }
.segs button:first-child { border-radius: 5px 0 0 5px; }
.segs button:last-child { border-radius: 0 5px 5px 0; }
.segs button + button { border-left: none; }
.segs button.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.spacer { flex: 1; }
.sm { font-size: 12px; }

.grid { border-collapse: collapse; width: 100%; }
.grid th, .grid td { border: 1px solid var(--border); padding: 8px 10px; text-align: center; }
.corner { background: var(--sub); font-size: 12.5px; color: var(--ink-soft); text-align: left; }
.osname { background: var(--sub); text-align: left; font-size: 13px; white-space: nowrap; }
.cell a { text-decoration: none; color: inherit; display: block; }
.cell a:hover { text-decoration: underline; }
.big { font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; }
.sub { font-size: 12px; color: var(--muted); }
.pct { display: block; font-size: 10.5px; color: var(--muted); margin-top: 2px; }
.cell.good { background: rgba(0,128,106,.09); }
.cell.warn { background: var(--warn-soft); }
.cell.bad { background: var(--bad-soft, rgba(200,40,40,.1)); }
.cell.none { background: transparent; }
.tot { background: rgba(0,0,0,.03); }
.grand { font-weight: 700; }
.legend { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.8; }
.sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px;
      border: 1px solid var(--border); margin: 0 2px; }
.sw.good { background: rgba(0,128,106,.35); }
.sw.warn { background: var(--warn-soft); }
.sw.bad { background: var(--bad-soft, rgba(200,40,40,.3)); }
.dim { color: var(--muted); }
</style>
