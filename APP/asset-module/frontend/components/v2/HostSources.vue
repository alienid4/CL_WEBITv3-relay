<script setup lang="ts">
// 10.1 月報「報表列印」（V47，2.047.0 V2 原生；取代舊 pages/reports/host-sources.vue）。同一批 API：
// /api/reports/host-sources（一台一列）、/api/reports/host-sources/export（全庫、勾選欄位，給 Excel 樞紐分析）、
// /api/reports/hardware-master/export（資產主檔＋欄位說明）。這頁刻意不做分析，不替使用者過濾任何東西（含退役）。
// 畫面：數字卡（「兩邊都有」點下去只看那批）、搜尋、來源篩選、欄位勾選；表格可排序、項次、匯出目前篩選結果。
interface Col { key: string; label: string }
interface Summary { total_hosts: number; total_rows: number; cia_hosts: number; offbook_only_hosts: number; both_hosts: number; retired_hosts: number; unmatchable_hosts: number; loose_candidate_hosts: number }
interface Resp { items: Record<string, unknown>[]; summary: Summary; columns: Col[]; default_columns: string[] }
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin } = useAuth()     // 兩個匯出都在後端 access_policy _ADMIN_EXPORTS（只給管理員）；Codex 1005：一般使用者也不顯示，免得點了才 403
const route = useRoute()
const router = useRouter()
const data = ref<Resp | null>(null)
const err = ref('')
const chosen = ref<string[]>([])
async function load() {
  try {
    const r = await apiFetch<Resp>('/api/reports/host-sources')
    data.value = r
    chosen.value = [...r.default_columns]
    err.value = ''
  } catch (e: any) { err.value = `報表列印載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }     // 壞掉要講壞掉，不吞成沒資料
}
onMounted(load)
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const kw = computed({ get: () => qs('hq'), set: (v: string) => router.replace({ query: { ...route.query, hq: v || undefined } }) })
const bothOnly = computed(() => qs('both') === '1')
const classSel = computed(() => new Set(qs('src').split(',').filter(Boolean)))
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
function togClass(c: string) {
  const s = new Set(classSel.value)
  if (s.has(c)) s.delete(c); else s.add(c)
  setQ({ src: [...s].join(',') || undefined })
}
const classes = computed(() => [...new Set((data.value?.items ?? []).map(r => String(r.source_class || '')))].sort())
const rows = computed(() => {
  let out = (data.value?.items ?? []).map((r, i) => ({ ...r, __no: i + 1 }))
  if (classSel.value.size) out = out.filter(r => classSel.value.has(String(r.source_class || '')))
  // 「兩邊都有」：CIA 在管、又被帳外來源（DY／vCenter／AUTO）登記的那幾台
  if (bothOnly.value) out = out.filter(r => r.active_cia && (r.in_dy || r.in_vc || r.in_auto))
  const k = kw.value.trim().toLowerCase()
  if (k) out = out.filter(r => ['hostname', 'ip', 'cia_serial', 'offbook_serials', 'systems'].some(f => String(r[f] ?? '').toLowerCase().includes(k)))
  return out
})
function tog(k: string) {
  const i = chosen.value.indexOf(k)
  if (i >= 0) chosen.value.splice(i, 1); else chosen.value.push(k)
}
const cols = computed(() => (data.value?.columns ?? []).filter(c => chosen.value.includes(c.key)).map(c => ({
  k: c.key, label: c.label, mono: true, text: (r: any) => (typeof r[c.key] === 'boolean' ? (r[c.key] ? '是' : '') : r[c.key]),
})))
const apiUrl = (path: string) => ((useRuntimeConfig().public as any).apiBase || '') + path      // 前端跟 API 不同埠，要帶 apiBase
function exportMaster() { window.open(apiUrl('/api/reports/hardware-master/export'), '_blank') }
function exportAllHosts() {
  if (!chosen.value.length) { showToast('至少要勾一個欄位', 'warn'); return }
  window.open(apiUrl('/api/reports/host-sources/export?' + chosen.value.map(c => 'cols=' + encodeURIComponent(c)).join('&')), '_blank')
}
</script>

<template>
  <div>
    <div v-if="isAdmin" class="bar">
      <button type="button" class="btn" @click="exportMaster">匯出資產主檔＋欄位說明（xlsx）</button>
      <InfoNote>hardware 全欄位（一筆一列）＋來源／machine_key／同台筆數；第二分頁是欄位說明（意義、填充率、排除 NA 後的有效率）。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }} <button type="button" class="btn2" @click="load">重試</button></p>
    <p v-else-if="!data" class="muted">載入中…</p>
    <template v-else>
      <div class="bar">
        <span>全庫 <b>{{ data.summary.total_hosts }}</b> 台</span>
        <span>登記 <b>{{ data.summary.total_rows }}</b> 筆</span>
        <span>CIA 登記 <b>{{ data.summary.cia_hosts }}</b> 台</span>
        <span>只有 CIA 待補登紀錄 <b>{{ data.summary.offbook_only_hosts }}</b> 台</span>
        <button type="button" class="chip" :class="{ on: bothOnly }" data-testid="hs-both" @click="setQ({ both: bothOnly ? undefined : '1', hq: undefined, src: undefined })">兩邊都有 <b>{{ data.summary.both_hosts }}</b> 台</button>
        <span>已退役（備查，不計入） <b>{{ data.summary.retired_hosts }}</b> 台</span>
        <span>無法比對 <b>{{ data.summary.unmatchable_hosts }}</b> 台</span>
        <span>其中放寬後有候選 <b>{{ data.summary.loose_candidate_hosts }}</b> 台</span>
        <InfoNote>一台一列（主機名＋IP 去重），標明這台在 CIA 登記過、還是只在 DY／vCenter 掃到。這頁不替你過濾任何東西（含退役）。「比對依據」欄：主機名或 IP 缺一的，一定會被當成獨立一台——那是無法判斷，不是「CIA 真的沒有這台」。放寬後有候選的（見「疑似同一台」欄）系統不會自動合併，要不要認定同一台由你決定。「兩邊都有」＝同一台在 CIA 有登記、又另有 DY／vCenter 自動建的紀錄，兩邊相加會比全庫台數大，差額就是這個數。</InfoNote>
      </div>
      <div class="bar">
        <input v-model="kw" class="in" type="search" placeholder="搜主機名／IP／序號／AP ID…" aria-label="搜尋">
        <span class="lbl">來源</span>
        <button v-for="c in classes" :key="c" type="button" class="chip" :class="{ on: classSel.has(c) }" @click="togClass(c)">{{ c || '（空）' }}</button>
        <button v-if="classSel.size" type="button" class="chip" @click="setQ({ src: undefined })">清除</button>
      </div>
      <div class="bar">
        <span class="lbl">欄位</span>
        <button v-for="c in data.columns" :key="c.key" type="button" class="chip" :class="{ on: chosen.includes(c.key) }" @click="tog(c.key)">{{ c.label }}</button>
      </div>
      <div v-if="isAdmin" class="bar">
        <button type="button" class="btn" @click="exportAllHosts">匯出全庫 Excel（{{ (data.summary.total_hosts + data.summary.retired_hosts).toLocaleString() }} 台 × {{ chosen.length }} 欄）</button>
        <InfoNote>匯出全庫每一台（在用／帳外 {{ data.summary.total_hosts }} 台＋退役備查 {{ data.summary.retired_hosts }} 台），不受搜尋／來源篩選影響，給 Excel 樞紐分析。表格右上角的「匯出」才是目前篩選的結果。</InfoNote>
      </div>
      <p v-else class="muted" data-test="admin-only">資產主檔、全庫 Excel 匯出限管理員</p>
      <V2Table :rows="rows" prefix="H" row-key="__no" unit="台" :cols="cols" export-name="報表列印_來源對照" empty="沒有符合的主機" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.lbl { color: var(--muted); font-size: 12px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; min-width: 260px; border: 1px solid var(--border-strong); border-radius: 8px; }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
