<script setup lang="ts">
// 網段配置表：公司「總分公司網段配置表」Excel 的系統版本。
//
// 這頁不只是把 Excel 貼上來——它是三件事的來源：
//   1. 新增資產時「機房 → 環境 → 網段」選 IP（填表的人記不住 10.99.163 是哪裡）
//   2. 掃描範圍：弱掃說明裡註記「建議排除掃描」的段不該被主動掃
//   3. 資料品質涵蓋率的分母：哪些網段該掃卻還沒掃
interface Segment {
  id: number
  cidr: string | null
  raw_cidr: string
  usage_status: string | null
  location: string | null
  purpose_desc: string | null
  category: string | null
  usage: string | null
  environment: string | null
  scan_excluded: number
  scan_note: string | null
  asset_count: number
  capacity: number | null
  imported_at: string | null
}
interface UsedIp {
  asset_serial: string
  hostname: string | null
  ip: string
  asset_status: string | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const rows = ref<Segment[]>([])
const loading = ref(true)
const importing = ref(false)
const lastImport = ref<any>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const q = ref('')
const locFilter = ref('')
const scanFilter = ref<'' | 'include' | 'exclude'>('')

const filtered = computed(() => {
  const needle = q.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (locFilter.value && r.location !== locFilter.value) return false
    if (scanFilter.value === 'include' && r.scan_excluded) return false
    if (scanFilter.value === 'exclude' && !r.scan_excluded) return false
    if (!needle) return true
    return [r.cidr, r.raw_cidr, r.purpose_desc, r.usage, r.category, r.scan_note]
      .some((v) => String(v ?? '').toLowerCase().includes(needle))
  })
})
const { sortKey, sortDir, toggle, sorted } = useSort(filtered, 'cidr')

const locations = computed(() =>
  [...new Set(rows.value.map((r) => r.location).filter(Boolean))].sort() as string[],
)
const stats = computed(() => ({
  total: rows.value.length,
  excluded: rows.value.filter((r) => r.scan_excluded).length,
  unparsed: rows.value.filter((r) => !r.cidr).length,
  assets: rows.value.reduce((n, r) => n + r.asset_count, 0),
}))

async function load() {
  loading.value = true
  try {
    rows.value = (await apiFetch<{ segments: Segment[] }>('/api/segments')).segments
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  importing.value = true
  lastImport.value = null
  try {
    const fd = new FormData()
    fd.append('file', f)
    const r = await apiFetch<any>('/api/segments/import', { method: 'POST', body: fd })
    lastImport.value = r
    showToast(`已匯入 ${r.imported} 段（可解析 ${r.parsed_cidr}）`, r.warnings.length ? 'warn' : 'success')
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '匯入失敗', 'error')
  } finally {
    importing.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

// 天條：數字要能下鑽。點「已登記幾台」就看得到是哪幾台、下一個可用 IP 是什麼
const openCidr = ref('')
const ipDetail = ref<{ used: UsedIp[]; suggestion: string | null; capacity: number } | null>(null)
const ipLoading = ref(false)
async function drill(r: Segment) {
  if (!r.cidr) return
  if (openCidr.value === r.cidr) { openCidr.value = ''; return }
  openCidr.value = r.cidr
  ipLoading.value = true
  ipDetail.value = null
  try {
    ipDetail.value = await apiFetch(`/api/segments/ips`, { query: { cidr: r.cidr } })
  } finally {
    ipLoading.value = false
  }
}
</script>

<template>
  <div>
    <div class="section-divider">資料治理</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>網段配置表</b></div>

    <div class="card">
      <p class="rv-hint">
        來源是公司的「總分公司網段配置表」Excel。<b>匯入是整批取代</b>——Excel 是這份清單的
        唯一真相，段被刪掉就該從系統消失；累加式匯入會讓作廢網段永遠留著，越用越髒。
        解析不掉的寫法（一格兩段、IP 範圍）<b>會保留並列出警告</b>，不會被靜默丟掉。
      </p>
      <div class="bar-row">
        <label class="upload">
          <input
            ref="fileInput" type="file" accept=".xlsx,.xlsm,.txt,.tsv,.csv"
            :disabled="importing" @change="onFile"
          />
          <span class="btn">{{ importing ? '匯入中…' : '匯入網段配置表' }}</span>
        </label>
        <div class="stats">
          <span><b>{{ stats.total }}</b> 段</span>
          <span>建議排除掃描 <b class="warn">{{ stats.excluded }}</b></span>
          <span v-if="stats.unparsed">無法解析 <b class="warn">{{ stats.unparsed }}</b></span>
          <span>已登記資產 <b>{{ stats.assets }}</b></span>
        </div>
      </div>

      <div v-if="lastImport" class="import-result">
        匯入 <b>{{ lastImport.imported }}</b> 段（可解析 CIDR {{ lastImport.parsed_cidr }}、
        機房 {{ lastImport.locations }} 個、建議排除掃描 {{ lastImport.scan_excluded }} 段）
        <div v-if="lastImport.warnings?.length" class="warns">
          <div class="warns-hd">{{ lastImport.warnings.length }} 筆需要人看一下：</div>
          <div v-for="(w, i) in lastImport.warnings" :key="i" class="warn-row">
            第 {{ w.row_no }} 列　<code>{{ w.raw_cidr }}</code>　{{ w.reason }}
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="filters">
        <input v-model="q" class="search" type="search" placeholder="搜尋網段、用途、說明…" />
        <select v-model="locFilter">
          <option value="">全部機房</option>
          <option v-for="l in locations" :key="l" :value="l">{{ l }}</option>
        </select>
        <div class="tabs">
          <button :class="{ on: scanFilter === '' }" @click="scanFilter = ''">全部</button>
          <button :class="{ on: scanFilter === 'include' }" @click="scanFilter = 'include'">可掃描</button>
          <button :class="{ on: scanFilter === 'exclude' }" @click="scanFilter = 'exclude'">建議排除</button>
        </div>
      </div>

      <p v-if="loading" class="muted">載入中…</p>
      <p v-else-if="rows.length === 0" class="muted">
        還沒有網段資料，上面按「匯入網段配置表」上傳 Excel。
      </p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="cidr" :active="sortKey" :dir="sortDir" @sort="toggle">網段</SortTh>
              <SortTh k="location" :active="sortKey" :dir="sortDir" @sort="toggle">機房</SortTh>
              <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="toggle">環境</SortTh>
              <SortTh k="category" :active="sortKey" :dir="sortDir" @sort="toggle">類別</SortTh>
              <SortTh k="usage" :active="sortKey" :dir="sortDir" @sort="toggle">用途</SortTh>
              <SortTh k="purpose_desc" :active="sortKey" :dir="sortDir" @sort="toggle">說明</SortTh>
              <SortTh k="asset_count" :active="sortKey" :dir="sortDir" @sort="toggle">已登記</SortTh>
              <SortTh k="scan_excluded" :active="sortKey" :dir="sortDir" @sort="toggle">掃描</SortTh>
            </tr>
          </thead>
          <tbody>
            <template v-for="r in sorted" :key="r.id">
              <tr :class="{ clickable: !!r.cidr, on: openCidr === r.cidr }" @click="drill(r)">
                <td class="mono">
                  {{ r.cidr ?? r.raw_cidr }}
                  <div v-if="!r.cidr" class="note warn">寫法無法解析，不能用於 IP 配置與掃描</div>
                </td>
                <td>{{ r.location ?? '—' }}</td>
                <td>
                  <span class="tag" :class="r.environment === '測試' ? 'test' : 'prod'">
                    {{ r.environment ?? '—' }}
                  </span>
                </td>
                <td>{{ r.category ?? '—' }}</td>
                <td>{{ r.usage ?? '—' }}</td>
                <td>
                  {{ r.purpose_desc ?? '—' }}
                  <div v-if="r.scan_note" class="note">{{ r.scan_note }}</div>
                </td>
                <td class="mono">
                  {{ r.asset_count }}<span v-if="r.capacity" class="cap">／{{ r.capacity }}</span>
                </td>
                <td>
                  <span v-if="r.scan_excluded" class="st ex">建議排除</span>
                  <span v-else class="st inc">可掃描</span>
                </td>
              </tr>
              <tr v-if="openCidr === r.cidr" class="drill">
                <td colspan="8">
                  <p v-if="ipLoading" class="muted">載入中…</p>
                  <template v-else-if="ipDetail">
                    <p class="rv-hint" style="margin:0 0 8px">
                      這段共 {{ ipDetail.capacity }} 個可用位址，清單裡已登記
                      <b>{{ ipDetail.used.length }}</b> 個。
                      建議下一個：<b class="mono">{{ ipDetail.suggestion ?? '沒有空位' }}</b>
                      <span class="caveat">（只代表這份清單沒登記，不代表實際上沒人在用）</span>
                    </p>
                    <div v-if="ipDetail.used.length" class="ip-chips">
                      <NuxtLink
                        v-for="u in ipDetail.used" :key="u.ip"
                        :to="`/assets/${u.asset_serial}`" class="ip-chip"
                      >
                        <b class="mono">{{ u.ip }}</b>
                        <span>{{ u.hostname ?? u.asset_serial }}</span>
                      </NuxtLink>
                    </div>
                    <p v-else class="muted">這段還沒有任何已登記的資產。</p>
                  </template>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-divider { margin: 0 0 16px; font-size: 11px; color: var(--brand-dark);
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong);
  padding: 8px 14px; font-size: 12.5px; color: var(--ink-soft); display: flex;
  align-items: center; gap: 8px; margin-bottom: 14px; }
.breadcrumb-bar b { color: var(--brand-dark); }
.card { border: 1px solid var(--border); background: var(--card); padding: 16px; margin-bottom: 16px; }
.rv-hint { font-size: 12px; color: var(--muted); line-height: 1.8; margin: 0 0 12px; }
.rv-hint b { color: var(--ink-soft); }
.bar-row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.upload input { display: none; }
.btn { display: inline-block; font-size: 12.5px; font-weight: 700; padding: 8px 18px;
  background: var(--brand); color: var(--ink); cursor: pointer; }
.btn:hover { background: var(--brand-dark); }
.stats { display: flex; gap: 16px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
.stats b { color: var(--ink); }
.stats b.warn, .warn { color: var(--warn, #d8a13a); }
.import-result { margin-top: 12px; border: 1px solid var(--border-strong);
  padding: 10px 14px; font-size: 12px; color: var(--ink-soft); line-height: 1.8; }
.warns { margin-top: 8px; }
.warns-hd { color: var(--warn, #d8a13a); font-weight: 700; margin-bottom: 4px; }
.warn-row { font-size: 11.5px; color: var(--muted); line-height: 1.7; }
.warn-row code { color: var(--ink-soft); }
.filters { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }
.search, .filters select { font-family: inherit; font-size: 12.5px; padding: 6px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.search { min-width: 220px; }
.tabs { display: flex; gap: 6px; }
.tabs button { font-family: inherit; font-size: 12px; padding: 6px 12px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--muted); cursor: pointer; }
.tabs button.on { border-color: var(--brand); color: var(--ink); }
.muted { color: var(--muted); font-size: 12.5px; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 880px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.clickable { cursor: pointer; }
.clickable:hover td { background: rgba(15,23,42,.03); }
.clickable.on td { background: rgba(0,145,66,.06); }
.drill td { background: rgba(0,0,0,.15); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.note { font-size: 10.5px; color: var(--muted); margin-top: 3px; line-height: 1.5; }
.cap { color: var(--muted); }
.tag { font-size: 10.5px; padding: 2px 7px; border: 1px solid var(--border-strong); }
.tag.prod { border-color: var(--brand); color: var(--brand); }
.tag.test { color: var(--muted); }
.st { font-size: 11.5px; white-space: nowrap; }
.st.ex { color: var(--warn, #d8a13a); }
.st.inc { color: var(--brand); }
.caveat { color: var(--muted); font-size: 11px; }
.ip-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ip-chip { display: flex; gap: 6px; align-items: baseline; text-decoration: none;
  border: 1px solid var(--border-strong); padding: 3px 8px; font-size: 11.5px; color: var(--muted); }
.ip-chip b { color: var(--ink); }
.ip-chip:hover { border-color: var(--brand); }
</style>
