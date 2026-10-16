<script setup lang="ts">
// 報表列印：來源對照表（2026-09-18 使用者：「給我一張 IP/hostname × DY/CIA 表，
// 欄位我可以自己選，我要拿去 Excel 跑樞紐分析」）。
// 這頁刻意**不做分析**——只把一台一列的原始資料攤平，分析交給 Excel。
interface Col { key: string; label: string }
interface Summary {
  total_hosts: number; total_rows: number; cia_hosts: number
  offbook_only_hosts: number; both_hosts: number
  retired_hosts: number; unmatchable_hosts: number; loose_candidate_hosts: number
}
interface Resp {
  items: Record<string, unknown>[]; summary: Summary
  columns: Col[]; default_columns: string[]
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const data = ref<Resp | null>(null)
const loading = ref(true)
const loadError = ref('')
const chosen = ref<string[]>([])
const q = ref('')
const classSel = ref<Set<string>>(new Set())

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const r = await apiFetch<Resp>('/api/reports/host-sources')
    data.value = r
    chosen.value = [...r.default_columns]
  } catch (e: unknown) {
    // 不可以吞成「沒有資料」——壞掉要講壞掉（2026-09-17 的教訓）
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

const classes = computed(() => {
  const s = new Set<string>()
  for (const r of data.value?.items || []) s.add(String(r.source_class || ''))
  return [...s].sort()
})

const bothOnly = ref(false)
const rows = computed(() => {
  let out = data.value?.items || []
  if (classSel.value.size) out = out.filter(r => classSel.value.has(String(r.source_class || '')))
  // 只列「兩邊都有（重疊）」：CIA 在管且同時被帳外來源（DY/vCenter/AUTO）登記——
  // 同一台被兩個頭條各算一次的那些，公司對帳最要看的就是這批（221=0、公司=2,250）。
  if (bothOnly.value) out = out.filter(r => r.active_cia && (r.in_dy || r.in_vc || r.in_auto))
  const kw = q.value.trim().toLowerCase()
  if (kw) {
    out = out.filter(r => ['hostname', 'ip', 'cia_serial', 'offbook_serials', 'systems']
      .some(k => String(r[k] ?? '').toLowerCase().includes(kw)))
  }
  return out
})

function tog(k: string) {
  const i = chosen.value.indexOf(k)
  if (i >= 0) chosen.value.splice(i, 1)
  else chosen.value.push(k)
}
function togClass(c: string) {
  const s = new Set(classSel.value)
  s.has(c) ? s.delete(c) : s.add(c)
  classSel.value = s
}
function cell(r: Record<string, unknown>, k: string) {
  const v = r[k]
  if (typeof v === 'boolean') return v ? '是' : ''
  return v === null || v === undefined || v === '' ? '—' : String(v)
}
// ⚠️ 一定要帶 apiBase：前端（3000）跟 API 不同埠，少了前綴會打到 Nuxt 自己 → 404
//（2026-09-18 v1.216～v1.219 就是這樣在公司機匯出失敗）
function apiUrl(path: string) {
  return ((useRuntimeConfig().public as any).apiBase || '') + path
}
function exportMaster() {
  window.open(apiUrl('/api/reports/hardware-master/export'), '_blank')
}
function exportXlsx() {
  if (!chosen.value.length) { showToast('至少要勾一個欄位', 'warn'); return }
  const qs = chosen.value.map(c => 'cols=' + encodeURIComponent(c)).join('&')
  window.open(apiUrl('/api/reports/host-sources/export?' + qs), '_blank')
}
</script>

<template>
  <div>
    <div class="section-divider">報告</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>報表列印</b>
      <InfoNote>一台一列（主機名＋IP 去重），標明這台在 <b>CIA</b> 登記過、還是只在 <b>DY／vCenter</b> 掃到。欄位自己勾，匯出成 Excel 後自行跑樞紐分析。<b>這頁不替你過濾任何東西</b>（含退役），因為先過濾就等於替你決定要看什麼。注意「比對依據」欄：主機名或 IP 缺一的，一定會被當成獨立一台、永遠配不到別的來源——那是<b>無法判斷</b>，不是「CIA 真的沒有這台」。</InfoNote>
    </div>

    <div class="card">
      <div class="qrow">
        <b>資產主檔</b>
        <button class="chip go" @click="exportMaster">匯出資產主檔＋欄位說明（xlsx）</button>
        <span class="muted">hardware 全欄位（一筆一列）＋來源／machine_key／同台筆數；第二分頁是欄位說明（意義、填充率、排除 NA 後的有效率）。</span>
      </div>
    </div>

    <div class="card" v-if="loadError">
      <p class="err">載入失敗：{{ loadError }}　<button class="chip" @click="load">重試</button></p>
    </div>

    <div class="card" v-else-if="loading"><p class="muted">載入中…</p></div>

    <template v-else-if="data">
      <div class="card">
        <div class="stats">
          <span>全庫 <b>{{ data.summary.total_hosts }}</b> 台</span>
          <span>登記 <b>{{ data.summary.total_rows }}</b> 筆</span>
          <span>CIA 在管 <b>{{ data.summary.cia_hosts }}</b> 台</span>
          <span>只在帳外 <b>{{ data.summary.offbook_only_hosts }}</b> 台</span>
          <span>兩邊都有 <b class="hi">{{ data.summary.both_hosts }}</b> 台</span>
          <span>已退役 <b>{{ data.summary.retired_hosts }}</b> 台</span>
          <span>無法比對 <b>{{ data.summary.unmatchable_hosts }}</b> 台</span>
          <span>其中放寬後有候選 <b class="hi">{{ data.summary.loose_candidate_hosts }}</b> 台</span>
        </div>
        <p class="note" v-if="data.summary.loose_candidate_hosts > 0">
          有 <b>{{ data.summary.loose_candidate_hosts }}</b> 台缺主機名或 IP，但<b>只憑其中一個欄位</b>找得到可能是同一台的機器
          （見「疑似同一台」欄）。<b>系統不會自動合併</b>——IP 會被回收、主機名會重複，併錯比漏抓更糟，要不要認定同一台由你決定。
        </p>
        <p class="note" v-if="data.summary.both_hosts > 0">
          「兩邊都有」<b>{{ data.summary.both_hosts }}</b> 台代表同一台機器在 CIA 和帳外各登記一次，
          因此首頁「在管」與「另有未登記」兩個頭條會各算它一次——兩者相加會比全庫台數大，差額就是這個數。
        </p>
      </div>

      <div class="card">
        <div class="filters">
          <input v-model="q" class="search" type="search" placeholder="搜主機名／IP／序號／AP ID…" />
          <label class="ck" :title="`同一台在 CIA 和帳外各登記一次的 ${data?.summary.both_hosts ?? 0} 台`">
            <input v-model="bothOnly" type="checkbox" /> 只列兩邊都有（重疊 {{ data?.summary.both_hosts ?? 0 }}）
          </label>
          <div class="qrow" v-if="classes.length">
            <span class="qlbl">來源</span>
            <button v-for="c in classes" :key="c" class="chip" :class="{ on: classSel.has(c) }" @click="togClass(c)">{{ c }}</button>
            <button v-if="classSel.size" class="chip clr" @click="classSel = new Set()">清除</button>
          </div>
          <div class="qrow">
            <span class="qlbl">欄位</span>
            <button v-for="c in data.columns" :key="c.key" class="chip" :class="{ on: chosen.includes(c.key) }" @click="tog(c.key)">{{ c.label }}</button>
          </div>
          <div class="qrow">
            <button class="chip go" @click="exportXlsx">匯出 Excel（{{ rows.length }} 台 × {{ chosen.length }} 欄）</button>
            <span class="muted">匯出的是<b>全部 {{ data.summary.total_hosts }} 台</b>，不受上面的搜尋／來源篩選影響——篩選留給 Excel 做。</span>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="tbl-wrap">
          <table class="tbl">
            <thead><tr><th v-for="c in data.columns.filter(x => chosen.includes(x.key))" :key="c.key">{{ c.label }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in rows.slice(0, 300)" :key="i">
                <td v-for="c in data.columns.filter(x => chosen.includes(x.key))" :key="c.key" class="mono">{{ cell(r, c.key) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted" v-if="rows.length > 300">畫面只預覽前 300 台（共 {{ rows.length }} 台符合篩選）。完整資料請用上面的匯出。</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ck { font-size: 12.5px; display: inline-flex; align-items: center; gap: 5px; color: var(--ink-soft); }
.stats { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 8px; }
.stats b { font-size: 1.15em; }
.hi { color: #c25e00; }
.note { font-size: .88em; color: #555; margin: 4px 0 0; }
.err { color: #b00; }
.filters { display: flex; flex-direction: column; gap: 8px; }
.qrow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.qlbl { font-size: .85em; color: #666; min-width: 3em; }
.search { padding: 6px 10px; min-width: 260px; }
.chip { border: 1px solid #ccc; border-radius: 12px; padding: 2px 10px; background: #fff; cursor: pointer; font-size: .85em; }
.chip.on { background: #0b7; color: #fff; border-color: #0b7; }
.chip.go { background: #06c; color: #fff; border-color: #06c; }
.chip.clr { color: #888; }
.tbl-wrap { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: .85em; }
.tbl th, .tbl td { border-bottom: 1px solid #eee; padding: 4px 8px; text-align: left; white-space: nowrap; }
.mono { font-family: ui-monospace, monospace; }
.muted { color: #777; font-size: .85em; }
</style>
