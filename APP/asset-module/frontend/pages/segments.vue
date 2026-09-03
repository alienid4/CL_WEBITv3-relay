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
  environment_raw: string | null
  vlan: string | null
  remark: string | null
  expanded_from: string | null
  net_start: number | null
  row_no: number | null
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
// 2026-08-26 使用者：「我要怎麼知道無法解析的 9 個是哪 9 個？」
// 原本那 9 筆只在匯入當下的警告區出現，重新整理就消失了——等於「知道有問題，
// 但永遠找不到是哪幾筆」。改成清單上永遠篩得出來。
const rowFilter = ref<'' | 'unparsed' | 'expanded' | 'range'>('')

const filtered = computed(() => {
  const needle = q.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (locFilter.value && r.location !== locFilter.value) return false
    if (scanFilter.value === 'include' && r.scan_excluded) return false
    if (scanFilter.value === 'exclude' && !r.scan_excluded) return false
    if (rowFilter.value === 'unparsed' && r.net_start !== null) return false
    if (rowFilter.value === 'expanded' && !r.expanded_from) return false
    if (rowFilter.value === 'range' && (r.cidr || r.net_start === null)) return false
    if (!needle) return true
    return [r.cidr, r.raw_cidr, r.purpose_desc, r.usage, r.category, r.scan_note,
      r.vlan, r.remark]
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
  // 「無法解析」＝連起訖位址都算不出來（真的看不懂）。
  // 位址範圍寫法有 net_start，查得到「IP 屬於哪段」，不算無法解析——
  // 兩者混在一起會讓人以為問題比實際大。
  unparsed: rows.value.filter((r) => r.net_start === null).length,
  expanded: rows.value.filter((r) => r.expanded_from).length,
  rangeOnly: rows.value.filter((r) => !r.cidr && r.net_start !== null).length,
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

// 空白範本下載（2026-08-25 使用者通則：有匯入就要有配對的匯出範本，
// 緊鄰上傳按鈕，不放頁首或另一頁）。
const runtimeConfig = useRuntimeConfig()
const templateDownloading = ref(false)
const exporting = ref(false)
async function downloadCurrent() {
  exporting.value = true
  try {
    await _download('/api/segments/export', 'segments.xlsx')
  } finally {
    exporting.value = false
  }
}
async function downloadTemplate() {
  templateDownloading.value = true
  try {
    await _download('/api/segments/export-template', 'segments_template.xlsx')
  } finally {
    templateDownloading.value = false
  }
}
async function _download(path: string, fallbackName: string) {
  try {
    const res = await fetch(`${runtimeConfig.public.apiBase}${path}`,
      { credentials: 'include' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m ? decodeURIComponent(m[1]) : fallbackName
    a.click()
    URL.revokeObjectURL(url)
  } catch (err: any) {
    showToast(`下載失敗：${err?.message ?? '請稍後重試'}`, 'error')
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
        <button
          class="btn ghost" type="button" :disabled="exporting || !stats.total"
          title="匯出目前系統裡的網段清單。表頭跟匯入認得的一樣，改完可以直接再匯回來。"
          @click="downloadCurrent"
        >
          {{ exporting ? '準備中…' : '⬆ 匯出目前清單' }}
        </button>
        <button class="btn ghost" type="button" :disabled="templateDownloading" @click="downloadTemplate">
          {{ templateDownloading ? '準備中…' : '⬇ 下載空白範本' }}
        </button>
        <div class="stats">
          <span><b>{{ stats.total }}</b> 段</span>
          <span title="弱掃說明欄裡寫了「建議排除掃描」的網段數。這是資安人員的判斷（員工電腦、UAT、重複 IP 網段），不是系統猜的。">
            建議排除掃描 <b class="warn">{{ stats.excluded }}</b> 段
          </span>
          <span v-if="stats.expanded" title="原檔用「A/24----B/24」這種範圍寫法的格子，系統展開成一段一段。原檔並沒有這麼多列。">
            展開自原檔 <b>{{ stats.expanded }}</b> 段
          </span>
          <span v-if="stats.rangeOnly" title="位址範圍寫法（如 172.16.156.0/24~172.16.157.230）。查得到「IP 屬於哪段」，但不是單一 CIDR，不會出現在新增資產的網段選單。">
            範圍寫法 <b>{{ stats.rangeOnly }}</b> 段
          </span>
          <span v-if="stats.unparsed" title="連起訖位址都算不出來的格子（手寫中文、亂碼）。點下面的「無法解析」頁籤可以列出是哪幾筆。">
            無法解析 <b class="warn">{{ stats.unparsed }}</b> 段
          </span>
          <span>已登記資產 <b>{{ stats.assets }}</b></span>
        </div>
      </div>

      <div v-if="lastImport" class="import-result">
        <div>
          原檔 <b>{{ lastImport.source_rows }}</b> 列 → 進到系統 <b>{{ lastImport.imported }}</b> 段
          <span v-if="lastImport.expanded" class="hint">
            （其中 {{ lastImport.expanded }} 段是從範圍寫法展開的，所以段數比列數多）
          </span>
          ・機房 {{ lastImport.locations }} 個・建議排除掃描 {{ lastImport.scan_excluded }} 段
        </div>
        <!-- 2026-08-26 使用者：「我故意重複匯入同一個檔案，但系統沒有擋掉，
             是覺得無所謂嗎？」——重複匯入確實無所謂（整批取代是冪等的），
             但系統以前沒把這件事講出來，只能用猜的。而真正危險的是「匯入不完整的
             檔案」：沒出現在新檔案裡的網段會直接消失，畫面卻只顯示「匯入 N 段」。 -->
        <div class="diff" :class="{ danger: lastImport.removed_count > 0 }">
          <template v-if="lastImport.was_empty">
            這是第一次匯入，全部 {{ lastImport.imported }} 段都是新的。
          </template>
          <template v-else-if="!lastImport.added_count && !lastImport.removed_count">
            ✓ <b>跟匯入前完全一樣</b>，沒有新增也沒有消失。
            （這張表是<b>整批取代</b>不是累加，所以重複匯入同一個檔案不會變兩份，也不會有影響。）
          </template>
          <template v-else>
            跟匯入前相比：新增 <b class="good">{{ lastImport.added_count }}</b> 段、
            <b class="bad">消失 {{ lastImport.removed_count }}</b> 段、
            不變 {{ lastImport.unchanged_count }} 段。
            <div v-if="lastImport.removed_count" class="removed-note">
              ⚠ 這張表是<b>整批取代</b>：沒有出現在這次檔案裡的網段會從系統消失。
              如果你上傳的是<b>部分清單</b>而不是完整清單，請改用完整檔案重新匯入。
              <div class="removed-list">
                消失的：<code v-for="c in lastImport.removed" :key="c">{{ c }}</code>
                <span v-if="lastImport.removed_count > lastImport.removed.length">
                  …等 {{ lastImport.removed_count }} 段
                </span>
              </div>
            </div>
          </template>
        </div>
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
        <div class="tabs">
          <button :class="{ on: rowFilter === '' }" @click="rowFilter = ''">不限寫法</button>
          <button
            v-if="stats.unparsed" :class="{ on: rowFilter === 'unparsed' }"
            @click="rowFilter = 'unparsed'"
          >無法解析 {{ stats.unparsed }}</button>
          <button
            v-if="stats.rangeOnly" :class="{ on: rowFilter === 'range' }"
            @click="rowFilter = 'range'"
          >範圍寫法 {{ stats.rangeOnly }}</button>
          <button
            v-if="stats.expanded" :class="{ on: rowFilter === 'expanded' }"
            @click="rowFilter = 'expanded'"
          >展開自原檔 {{ stats.expanded }}</button>
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
              <SortTh k="vlan" :active="sortKey" :dir="sortDir" @sort="toggle">VLAN</SortTh>
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
                  <div v-if="r.expanded_from" class="note">
                    展開自原檔第 {{ r.row_no }} 列的 <code>{{ r.expanded_from }}</code>
                  </div>
                  <div v-else-if="!r.cidr && r.net_start !== null" class="note">
                    位址範圍寫法：查得到「IP 屬於哪段」，但不是單一 CIDR，不會出現在網段選單
                  </div>
                  <div v-else-if="!r.cidr" class="note warn">
                    寫法無法解析，不能用於 IP 配置與掃描——請到原檔改成單一 CIDR
                  </div>
                </td>
                <td>{{ r.location ?? '—' }}</td>
                <td>
                  <span
                    v-if="r.environment" class="tag"
                    :class="r.environment === '測試' ? 'test' : 'prod'"
                  >{{ r.environment }}</span>
                  <!-- 認不出來的值不猜成正式（見 segments.derive_environment）。
                       畫面要顯示得出「他填了什麼」，不然人不知道要去改哪一格。 -->
                  <span
                    v-else class="tag unknown"
                    :title="r.environment_raw
                      ? `檔案填的是「${r.environment_raw}」，系統只認得 UAT／PROD`
                      : '這一段沒有環境資訊'"
                  >{{ r.environment_raw ? `？${r.environment_raw}` : '—' }}</span>
                </td>
                <td class="mono">{{ r.vlan ?? '—' }}</td>
                <td>{{ r.category ?? '—' }}</td>
                <td>{{ r.usage ?? '—' }}</td>
                <td>
                  {{ r.purpose_desc ?? '—' }}
                  <div v-if="r.remark" class="note remark">註解：{{ r.remark }}</div>
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
.import-result .diff { margin-top: 6px; padding: 6px 8px; border-radius: 6px;
  background: var(--good-soft); font-size: 12px; line-height: 1.7; }
.import-result .diff.danger { background: var(--bad-soft); }
.import-result .hint { color: var(--ink-aux); }
.import-result .good { color: var(--good); }
.import-result .bad { color: var(--bad); }
.removed-note { margin-top: 4px; }
.removed-list { margin-top: 3px; word-break: break-all; }
.removed-list code { margin-right: 6px; }
.tag.unknown { background: var(--warn-soft); color: var(--warn); }
.note.remark { color: var(--ink-aux); }
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
  background: var(--brand); color: #fff; cursor: pointer; border: none; border-radius: 10px;
  font-family: inherit; }
.btn:hover { background: var(--brand-dark); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
/* 次要按鈕（範本下載這種不是主流程的動作）：跟主按鈕擺一起要看得出主副之分 */
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.ghost:hover { background: var(--card); border-color: var(--brand); color: var(--brand-dark); }
.stats { display: flex; gap: 16px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
.stats b { color: var(--ink); }
.stats b.warn, .warn { color: var(--warn-text); }
.import-result { margin-top: 12px; border: 1px solid var(--border-strong);
  padding: 10px 14px; font-size: 12px; color: var(--ink-soft); line-height: 1.8; }
.warns { margin-top: 8px; }
.warns-hd { color: var(--warn-text); font-weight: 700; margin-bottom: 4px; }
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
.tag.prod { border-color: var(--brand); color: var(--brand-dark); }
.tag.test { color: var(--muted); }
.st { font-size: 11.5px; white-space: nowrap; }
.st.ex { color: var(--warn-text); }
.st.inc { color: var(--brand-dark); }
.caveat { color: var(--muted); font-size: 11px; }
.ip-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ip-chip { display: flex; gap: 6px; align-items: baseline; text-decoration: none;
  border: 1px solid var(--border-strong); padding: 3px 8px; font-size: 11.5px; color: var(--muted); }
.ip-chip b { color: var(--ink); }
.ip-chip:hover { border-color: var(--brand); }
</style>
