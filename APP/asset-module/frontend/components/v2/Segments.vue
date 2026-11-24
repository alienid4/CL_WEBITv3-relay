<script setup lang="ts">
// ⚙ 網段配置（V55，2.047.2 V2 原生；取代舊 pages/segments.vue）。同一批 API：GET /api/segments、GET /api/segments/ips?cidr=。
// 公司「總分公司網段配置表」Excel 的系統版本；匯入／匯出在 ⚙ 資料匯入（這頁只檢視、篩選、下鑽）。
// 篩選：搜尋、機房／環境／用途複選（同維度 OR、跨維度 AND）、掃描（可掃描／建議排除）、寫法（無法解析／範圍寫法／展開自原檔）——都帶在網址上。
// 已登記台數點下去：這段已登記哪些 IP（點進主機履歷）、建議下一個可用 IP。
interface Segment { id: number; cidr: string | null; raw_cidr: string; usage_status: string | null; location: string | null; purpose_desc: string | null; category: string | null; usage: string | null; environment: string | null; environment_raw: string | null; vlan: string | null; remark: string | null; expanded_from: string | null; net_start: number | null; row_no: number | null; scan_excluded: number; scan_note: string | null; asset_count: number; capacity: number | null; imported_at: string | null }
interface UsedIp { asset_serial: string; hostname: string | null; ip: string; asset_status: string | null }
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
// 數字卡（全部段數裡的 N 段）點下去：先清掉其他篩選，數字＝點下去的筆數
const only = (patch: Record<string, string>) => setQ({ sq: undefined, sr: undefined, se: undefined, su: undefined, ss: undefined, sw: undefined, cidr: undefined, ...patch })
const rows = ref<Segment[]>([])
const loading = ref(true)
const err = ref('')
async function load() {
  loading.value = true
  try { rows.value = (await apiFetch<{ segments: Segment[] }>('/api/segments')).segments; err.value = '' } catch (e: any) { err.value = `網段載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { loading.value = false }
}
onMounted(load)
const kw = computed({ get: () => qs('sq'), set: (v: string) => setQ({ sq: v || undefined }) })
const sel = (k: string) => new Set(qs(k).split('|').filter(Boolean))
const roomSel = computed(() => sel('sr'))
const envSel = computed(() => sel('se'))
const useSel = computed(() => sel('su'))
function togSel(k: string, v: string) {
  const s = sel(k)
  if (s.has(v)) s.delete(v); else s.add(v)
  setQ({ [k]: [...s].join('|') || undefined })
}
const scan = computed(() => (['include', 'exclude'].includes(qs('ss')) ? qs('ss') : ''))
const shape = computed(() => (['unparsed', 'expanded', 'range'].includes(qs('sw')) ? qs('sw') : ''))
const uniq = (f: (r: Segment) => string | null) => [...new Set(rows.value.map(f).filter(Boolean))].sort() as string[]
const locations = computed(() => uniq(r => r.location))
const envs = computed(() => uniq(r => r.environment))
const usages = computed(() => uniq(r => r.usage))
const stats = computed(() => ({
  total: rows.value.length, excluded: rows.value.filter(r => r.scan_excluded).length,
  unparsed: rows.value.filter(r => r.net_start === null).length,            // 連起訖都算不出來（真的看不懂）
  expanded: rows.value.filter(r => r.expanded_from).length,
  range: rows.value.filter(r => !r.cidr && r.net_start !== null).length,    // 位址範圍寫法：查得到 IP 屬於哪段，不是單一 CIDR
  assets: rows.value.reduce((n, r) => n + r.asset_count, 0),
}))
const filtered = computed(() => {
  const n = kw.value.trim().toLowerCase()
  return rows.value.filter(r => {
    if (roomSel.value.size && !roomSel.value.has(r.location ?? '')) return false
    if (envSel.value.size && !envSel.value.has(r.environment ?? '')) return false
    if (useSel.value.size && !useSel.value.has(r.usage ?? '')) return false
    if (scan.value === 'include' && r.scan_excluded) return false
    if (scan.value === 'exclude' && !r.scan_excluded) return false
    if (shape.value === 'unparsed' && r.net_start !== null) return false
    if (shape.value === 'expanded' && !r.expanded_from) return false
    if (shape.value === 'range' && (r.cidr || r.net_start === null)) return false
    return !n || [r.cidr, r.raw_cidr, r.purpose_desc, r.usage, r.category, r.scan_note, r.vlan, r.remark].some(v => String(v ?? '').toLowerCase().includes(n))
  }).map((r, i) => ({
    ...r, __no: i + 1, seg: r.cidr ?? r.raw_cidr,
    seg_note: r.expanded_from ? `展開自原檔第 ${r.row_no} 列的 ${r.expanded_from}` : (!r.cidr && r.net_start !== null) ? '位址範圍寫法（不會出現在網段選單）' : !r.cidr ? '寫法無法解析——請到原檔改成單一 CIDR' : '',
    env: r.environment ?? (r.environment_raw ? `？${r.environment_raw}` : ''),
    desc: [r.purpose_desc, r.remark ? `註解：${r.remark}` : '', r.scan_note].filter(Boolean).join('；'),
    used: r.capacity ? `${r.asset_count}／${r.capacity}（${Math.round(r.asset_count / r.capacity * 100)}%）` : String(r.asset_count),
    scan_text: r.scan_excluded ? '建議排除' : '可掃描',
  }))
})
// ---- 下鑽：這段已登記哪些 IP、建議下一個
const openCidr = computed(() => qs('cidr'))
const ipDetail = ref<{ used: UsedIp[]; suggestion: string | null; capacity: number } | null>(null)
const ipErr = ref('')
const beginIps = useLatest()      // 快速換網段時只套用最新那一次
watch(openCidr, async (c) => {
  const isLatest = beginIps()
  ipDetail.value = null
  if (!c) return
  try {
    const r = await apiFetch<{ used: UsedIp[]; suggestion: string | null; capacity: number }>('/api/segments/ips', { query: { cidr: c } })
    if (!isLatest()) return
    ipDetail.value = r
    ipErr.value = ''
  } catch (e: any) { if (isLatest()) ipErr.value = `IP 明細載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}, { immediate: true })
const ipRows = computed(() => (ipDetail.value?.used ?? []).map((u, i) => ({ ...u, __no: i + 1, name: u.hostname ?? u.asset_serial })))
const COLS = [
  { k: 'seg', label: '網段', mono: true }, { k: 'seg_note', label: '寫法' }, { k: 'location', label: '機房' },
  { k: 'env', label: '環境', tone: (r: any) => (r.environment === '測試' ? 'test' : r.environment ? 'prod' : 'none') },
  { k: 'vlan', label: 'VLAN', mono: true }, { k: 'category', label: '類別' }, { k: 'usage', label: '用途' }, { k: 'desc', label: '說明' },
  { k: 'asset_count', label: '已登記', mono: true, text: (r: any) => r.used }, { k: 'scan_text', label: '掃描', tone: (r: any) => (r.scan_excluded ? 'test' : '') },
]
</script>

<template>
  <div>
    <div class="bar">
      <span><b>{{ stats.total }}</b> 段</span>
      <button type="button" class="lb" @click="only({ ss: 'exclude' })">建議排除掃描 <b>{{ stats.excluded }}</b> 段</button>
      <button v-if="stats.expanded" type="button" class="lb" @click="only({ sw: 'expanded' })">展開自原檔 <b>{{ stats.expanded }}</b> 段</button>
      <button v-if="stats.range" type="button" class="lb" @click="only({ sw: 'range' })">範圍寫法 <b>{{ stats.range }}</b> 段</button>
      <button v-if="stats.unparsed" type="button" class="lb" @click="only({ sw: 'unparsed' })">無法解析 <b>{{ stats.unparsed }}</b> 段</button>
      <span>已登記資產 <b>{{ stats.assets }}</b></span>
      <InfoNote>來源是公司的「總分公司網段配置表」Excel，匯入在 ⚙ 資料匯入、整批取代（段被刪掉就從系統消失）。建議排除掃描＝弱掃說明欄寫了「建議排除掃描」（資安人員的判斷）。展開自原檔＝原檔用「A/24----B/24」範圍寫法，系統展開成一段一段。範圍寫法查得到「IP 屬於哪段」但不是單一 CIDR，不會出現在新增資產的網段選單。環境認不出來的不猜成正式，顯示「？原值」。已登記台數點下去看是哪幾個 IP、建議下一個可用 IP（只代表這份清單沒登記，不代表實際上沒人在用）。</InfoNote>
    </div>
    <div class="bar">
      <input v-model="kw" class="in" type="search" placeholder="搜尋網段、用途、說明…" aria-label="搜尋網段">
      <button type="button" class="chip" :class="{ on: !scan }" @click="setQ({ ss: undefined })">掃描不限</button>
      <button type="button" class="chip" :class="{ on: scan === 'include' }" @click="setQ({ ss: 'include' })">可掃描</button>
      <button type="button" class="chip" :class="{ on: scan === 'exclude' }" @click="setQ({ ss: 'exclude' })">建議排除</button>
      <button type="button" class="chip" :class="{ on: !shape }" @click="setQ({ sw: undefined })">不限寫法</button>
    </div>
    <div v-for="d in [{ k: 'sr', label: '機房', opts: locations, set: roomSel }, { k: 'se', label: '環境', opts: envs, set: envSel }, { k: 'su', label: '用途', opts: usages, set: useSel }]" :key="d.k" class="bar">
      <template v-if="d.opts.length">
        <span class="lbl">{{ d.label }}</span>
        <button v-for="o in d.opts" :key="o" type="button" class="chip" :class="{ on: d.set.has(o) }" @click="togSel(d.k, o)">{{ o }}</button>
        <button v-if="d.set.size" type="button" class="chip" @click="setQ({ [d.k]: undefined })">清除</button>
      </template>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <V2Table v-else :rows="filtered" prefix="N" row-key="id" unit="段" :cols="COLS" export-name="網段配置"
             :empty="loading ? '載入中…' : (rows.length ? '這個條件沒有網段' : '還沒有網段資料：到 ⚙ 資料匯入的「網段配置表」上傳 Excel')">
      <template #cell-asset_count="{ row }">
        <button v-if="row.cidr" type="button" class="lb" @click="setQ({ cidr: openCidr === row.cidr ? undefined : row.cidr })">{{ row.used }}</button>
        <span v-else>{{ row.used }}</span>
      </template>
    </V2Table>
    <div v-if="openCidr" class="box" data-testid="seg-ips">
      <h3 class="h3">{{ openCidr }}<button type="button" class="lb" @click="setQ({ cidr: undefined })">關閉</button></h3>
      <p v-if="ipErr" class="err">{{ ipErr }}</p>
      <template v-else-if="ipDetail">
        <p class="note">這段共 {{ ipDetail.capacity }} 個可用位址，清單裡已登記 <b>{{ ipDetail.used.length }}</b> 個。建議下一個：<b class="mono">{{ ipDetail.suggestion ?? '沒有空位' }}</b>
          <InfoNote>只代表這份清單沒登記，不代表實際上沒人在用。</InfoNote></p>
        <V2Table :rows="ipRows" prefix="I" row-key="ip" :export-name="`網段已登記_${openCidr}`" empty="這段還沒有任何已登記的資產"
                 :cols="[{ k: 'ip', label: 'IP', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'name', label: '主機名稱' }, { k: 'asset_status', label: '資產狀態' }]" />
      </template>
      <p v-else class="muted">載入中…</p>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 8px; font-size: 13px; }
.lbl { color: var(--muted); font-size: 12px; min-width: 2.5em; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; min-width: 260px; border: 1px solid var(--border-strong); border-radius: 8px; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; }
.box { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.h3 { margin: 0 0 8px; font-size: 15px; display: flex; gap: 8px; align-items: center; }
.note { font-size: 13px; margin: 0 0 8px; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
