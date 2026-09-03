<script setup lang="ts">
// 系統組月報（2026-08-21）。使用者每月要把三張表貼進部門報告，原本是人工統計的。
// 他的原話：「以後我就 COPY 畫面，不用再自己統計」。
//
// 所以這一頁的成敗標準不是「數字算得出來」，是**他敢直接貼進報告**。那要求兩件事：
//   1. 版面要能直接複製（表格乾淨、不要有一堆只在畫面上有意義的裝飾）
//   2. 每個數字都要能追——他的第二個要求：「你每個數字我都要可以追」
//
// 第 2 點是這頁最重要的設計：**每一格數字都是連結**，點下去列出是哪幾台、
// 以及每一台為什麼被分到這一類（reason 欄）。後端加總與下鑽走同一份計算，
// 所以格子上的數字跟清單筆數必然一致——不一致的報表沒有人敢用。
definePageMeta({ ssr: false })

const { apiFetch } = useApi()
const { showToast } = useToast()

interface PlatformRow {
  platform: string; total: number
  已EOS: number; 一年內EOS: number; 尚未公布: number; 需確認: number; 支援中: number
}
interface Report {
  meta: { generated_at: string; period: string | null
          rvtools_exported_at: string | null; rvtools_note: string }
  platform_lifecycle: {
    rows: PlatformRow[]; total: number
    excluded: { platform: string; count: number }[]
    excluded_total: number; retired_excluded: number
  }
  os_version_breakdown: { os_canonical: string; platform: string; count: number
                           eos_status: string | null; eos_date: string | null }[]
  cluster_summary: {
    locations: { location: string; total: number
                 rows: { service: string; esxi_count: number }[] }[]
    basis: string; unmapped_vcenters: string[]
  }
  virtualization_env: {
    clusters: { location: string; vcenter: string; environment: string; cluster: string
                count: number; version: string | null; mixed_version: boolean
                eos_status: string | null; eos_date: string | null }[]
    physical_hosts: Record<string, any>[]
  }
  notes: Record<string, string>
}

const report = ref<Report | null>(null)
const loading = ref(true)
const STATUS_COLS = ['已EOS', '一年內EOS', '尚未公布', '需確認', '支援中'] as const

async function load() {
  loading.value = true
  try {
    report.value = await apiFetch<Report>('/api/reports/system-group')
  } catch (err: any) {
    showToast(`報告載入失敗：${err?.data?.detail ?? err?.message ?? '請稍後再試'}`, 'error', 15000)
  } finally {
    loading.value = false
  }
}
onMounted(load)

// ---- 排序（天條：表格一律可排序）----
// 表1／表3的兩張都是單一陣列，直接用全站共用的 useSort。表2跟下鑽面板的表格
// 是動態生成、每組資料各自一張小表——useSort 內部用 ref()，在 v-for/computed
// 裡動態呼叫的話每次重繪都會重建一份新的 ref，排序狀態記不住，所以這兩處改用
// 底下這組共用的手動排序狀態＋sortRows()，一個控制同時套用到所有小表。
const platformRows = computed(() => report.value?.platform_lifecycle.rows ?? [])
const { sortKey: platformSortKey, sortDir: platformSortDir, toggle: platformToggle, sorted: platformSorted } =
  useSort(platformRows, '')

// 版本明細預設不排序：維持後端算好的優先序（依台數大到小），
// 使用者點表頭才改用該欄位排序。
const versionRows = computed(() => report.value?.os_version_breakdown ?? [])
const { sortKey: versionSortKey, sortDir: versionSortDir, toggle: versionToggle, sorted: versionSorted } =
  useSort(versionRows, '')

// 2026-08-21 使用者：「有什麼設計可以在10行左右呈現」——預設只顯示前10名，
// 其餘摺總成一行講出來（不能悄悄砍掉不講），可以展開看全部。
const showAllVersions = ref(false)
const versionDisplayed = computed(() =>
  showAllVersions.value ? versionSorted.value : versionSorted.value.slice(0, 10))
const versionHiddenCount = computed(() =>
  showAllVersions.value ? 0 : Math.max(0, versionSorted.value.length - 10))
const versionHiddenTotal = computed(() =>
  versionSorted.value.slice(10).reduce((sum, v) => sum + v.count, 0))

const clusterRows = computed(() => report.value?.virtualization_env.clusters ?? [])
const { sortKey: clusterSortKey, sortDir: clusterSortDir, toggle: clusterToggle, sorted: clusterSorted } =
  useSort(clusterRows, '')

const hostRows = computed(() => report.value?.virtualization_env.physical_hosts ?? [])
const { sortKey: hostSortKey, sortDir: hostSortDir, toggle: hostToggle, sorted: hostSorted } =
  useSort(hostRows, '')

function sortRows<T extends Record<string, any>>(rows: T[], key: string, dir: 'asc' | 'desc'): T[] {
  if (!key) return rows
  return [...rows].sort((a, b) => {
    const av = a[key]; const bv = b[key]
    const ea = av === null || av === undefined || av === ''
    const eb = bv === null || bv === undefined || bv === ''
    if (ea && eb) return 0
    if (ea) return 1
    if (eb) return -1
    const mul = dir === 'asc' ? 1 : -1
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * mul
    return String(av).localeCompare(String(bv), 'zh-Hant', { numeric: true }) * mul
  })
}

const loc2Sort = ref<{ key: string; dir: 'asc' | 'desc' }>({ key: '', dir: 'asc' })
function loc2Toggle(key: string) {
  loc2Sort.value = loc2Sort.value.key === key
    ? { key, dir: loc2Sort.value.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: 'asc' }
}

const drillSort = ref<{ key: string; dir: 'asc' | 'desc' }>({ key: '', dir: 'asc' })
function drillToggle(key: string) {
  drillSort.value = drillSort.value.key === key
    ? { key, dir: drillSort.value.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: 'asc' }
}
const drillSorted = computed(() => sortRows(drillRows.value, drillSort.value.key, drillSort.value.dir))

// ---- 下鑽 ----
// 點任何一格數字都開這個面板。標題要講清楚「你點的是哪一格」，
// 否則列了 200 台出來，人會忘記自己在看什麼。
const drillOpen = ref(false)
const drillTitle = ref('')
const drillRows = ref<any[]>([])
const drillLoading = ref(false)
const drillKind = ref<'platform' | 'cluster'>('platform')

// Esc 關閉。點了十幾格之後，手不用離開鍵盤就能關掉。
function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape' && drillOpen.value) drillOpen.value = false
}
onMounted(() => document.addEventListener('keydown', onEsc))
onUnmounted(() => document.removeEventListener('keydown', onEsc))

// R2：版本欄要標EOS，跟表1同一套配色語彙，看的人不用再學一套新顏色。
// 2026-08-21 使用者反問「這些資訊不是都查過了嗎」——「尚未公布」（查到這個
// 產品，官方就是沒公布日期）跟「需確認」（EOS表根本沒這個產品）分開上色，
// 不要混成同一種灰色，不然還是看不出差在哪。
function eosClass(status: string) {
  if (status === '已EOS') return 'eos-expired'
  if (status === '一年內EOS') return 'eos-upcoming'
  if (status === '支援中') return 'eos-ok'
  if (status === '尚未公布') return 'eos-nodate'
  return 'eos-unknown'
}

async function drill(kind: 'platform' | 'cluster', title: string, query: Record<string, any>) {
  drillOpen.value = true
  drillKind.value = kind
  drillTitle.value = title
  drillLoading.value = true
  drillRows.value = []
  drillSort.value = { key: '', dir: 'asc' }  // 每次開新的下鑽面板都重設排序，
  // 不然上一次(platform)排的欄位名帶進這一次(cluster)可能根本不存在
  try {
    drillRows.value = await apiFetch<any[]>('/api/reports/system-group/drill', {
      query: { table: kind, ...query },
    })
  } catch (err: any) {
    showToast(`清單載入失敗：${err?.data?.detail ?? err?.message}`, 'error', 15000)
    drillOpen.value = false
  } finally {
    drillLoading.value = false
  }
}

// ---- 備註（可編輯，跨月保留）----
const editingKey = ref<string | null>(null)
const editingText = ref('')
function startEdit(key: string) {
  editingKey.value = key
  editingText.value = report.value?.notes?.[key] ?? ''
}
async function saveNote() {
  if (!editingKey.value) return
  const key = editingKey.value
  try {
    await apiFetch('/api/reports/system-group/note', {
      method: 'PUT', body: { row_key: key, note: editingText.value },
    })
    if (report.value) report.value.notes = { ...report.value.notes, [key]: editingText.value }
    editingKey.value = null
    showToast('備註已存，下個月開這頁還會在', 'success')
  } catch (err: any) {
    showToast(`備註存檔失敗：${err?.data?.detail ?? err?.message}`, 'error', 15000)
  }
}

// ---- 月份定稿 ----
const period = ref(new Date().toISOString().slice(0, 7))
const saving = ref(false)
async function saveSnapshot() {
  saving.value = true
  try {
    await apiFetch('/api/reports/system-group/snapshots', {
      method: 'POST', body: { period: period.value },
    })
    showToast(`${period.value} 已存檔。之後數字再變，這份不會跟著變`, 'success', 8000)
  } catch (err: any) {
    showToast(`存檔失敗：${err?.data?.detail ?? err?.message}`, 'error', 15000)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>系統組報告</h1>
      <div class="hdact">
        <input v-model="period" type="month" class="mon">
        <button class="btn" :disabled="saving" @click="saveSnapshot">
          {{ saving ? '存檔中…' : '存為本月定稿' }}
        </button>
        <button class="btn" @click="load">重新整理</button>
      </div>
    </div>

    <p v-if="loading" class="dim">載入中…</p>

    <template v-else-if="report">
      <!-- 資料新鮮度固定顯示、不給關。這一頁最大的風險不是算錯，是算得很精確
           但底層是三週前的快照，而看報告的人不知道。 -->
      <div class="fresh" :class="{ warn: !report.meta.rvtools_exported_at }">
        {{ report.meta.rvtools_note }}　·　產生於 {{ report.meta.generated_at }}
      </div>

      <!-- ===== 表1 ===== -->
      <h2>平台數量與生命週期</h2>
      <table class="rt">
        <thead><tr>
          <SortTh k="platform" :active="platformSortKey" :dir="platformSortDir" @sort="platformToggle">平台類別</SortTh>
          <SortTh k="total" :active="platformSortKey" :dir="platformSortDir" @sort="platformToggle" class="num">總量</SortTh>
          <SortTh v-for="s in STATUS_COLS" :key="s" :k="s" :active="platformSortKey" :dir="platformSortDir"
                  @sort="platformToggle" class="num">{{ s }}</SortTh>
          <th class="wide">備註說明</th>
        </tr></thead>
        <tbody>
          <tr v-for="r in platformSorted" :key="r.platform">
            <td class="rh">{{ r.platform }}</td>
            <td class="num">
              <a v-if="r.total" class="n" @click="drill('platform', `${r.platform}（全部）`, { platform: r.platform })">{{ r.total }}</a>
              <span v-else class="z">·</span>
            </td>
            <td v-for="s in STATUS_COLS" :key="s" class="num">
              <a v-if="r[s]" class="n" @click="drill('platform', `${r.platform} ／ ${s}`, { platform: r.platform, status: s })">{{ r[s] }}</a>
              <span v-else class="z">·</span>
            </td>
            <td class="wide">
              <template v-if="editingKey === `platform:${r.platform}`">
                <input v-model="editingText" class="ni" @keyup.enter="saveNote">
                <button class="mini" @click="saveNote">存</button>
                <button class="mini" @click="editingKey = null">取消</button>
              </template>
              <span v-else class="note" @click="startEdit(`platform:${r.platform}`)">
                {{ report.notes[`platform:${r.platform}`] || '＋ 加備註' }}
              </span>
            </td>
          </tr>
          <tr class="sum">
            <td class="rh">合計</td>
            <td class="num">{{ report.platform_lifecycle.total }}</td>
            <td :colspan="STATUS_COLS.length + 1"></td>
          </tr>
        </tbody>
      </table>

      <table class="rt small">
        <thead><tr><th>不列入本報告</th><th class="num">臺數</th></tr></thead>
        <tbody>
          <tr v-for="e in report.platform_lifecycle.excluded" :key="e.platform">
            <td class="rh">{{ e.platform }}</td>
            <td class="num">
              <a class="n" @click="drill('platform', `不列入本報告：${e.platform}`, { bucket: e.platform })">{{ e.count }}</a>
            </td>
          </tr>
          <tr>
            <td class="rh">已退役</td>
            <td class="num">
              <a class="n" @click="drill('platform', '已退役（不列入）', { retired: true })">{{ report.platform_lifecycle.retired_excluded }}</a>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 2026-08-21 使用者四輪拍板：
           第一輪「只寫這樣子主管不會知道詳細資訊，譬如RHEL 7.9、26台」——表1
           只到平台大類，這裡攤到版本這一層。
           第二輪看了實際畫面「真的太多了」，追加：只列急迫的（其他狀態不用
           列，這是行動清單不是全量統計）；小版本併進大版本（7.9併進7）。
           第三輪反問「但有EOS的嗎」——已EOS也要列，改成已EOS＋一年內EOS都列。
           第四輪「有什麼設計可以在10行左右呈現」：拿掉平台類別欄（版本名稱
           就看得出是什麼，不用另外一欄）跟EOS狀態欄（這張表本來就只有已EOS/
           一年內EOS兩種，欄位是廢話）；預設只顯示前10名，其餘摺總一行講出來
           （不能因為砍長尾就悄悄消失），可展開看全部；排序改依台數(見後端
           os_version_breakdown()的排序規則調整)，避免衝擊最大的項目被埋掉。 -->
      <h3>作業系統版本明細（已EOS／一年內到期）</h3>
      <table class="rt">
        <thead><tr>
          <SortTh k="os_canonical" :active="versionSortKey" :dir="versionSortDir" @sort="versionToggle">版本</SortTh>
          <SortTh k="count" :active="versionSortKey" :dir="versionSortDir" @sort="versionToggle" class="num">臺數</SortTh>
          <SortTh k="eos_date" :active="versionSortKey" :dir="versionSortDir" @sort="versionToggle">EOS日期</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="v in versionDisplayed" :key="v.os_canonical">
            <td class="rh mono">{{ v.os_canonical }}</td>
            <td class="num">
              <a class="n" @click="drill('platform', `${v.os_canonical}`, { os_canonical: v.os_canonical })">{{ v.count }}</a>
            </td>
            <td class="mono">
              <span class="eosb" :class="eosClass(v.eos_status || '')">{{ v.eos_date || v.eos_status }}</span>
            </td>
          </tr>
          <tr v-if="!report.os_version_breakdown.length">
            <td colspan="3" class="dim">無</td>
          </tr>
        </tbody>
      </table>
      <p v-if="versionHiddenCount > 0" class="tip">
        還有 {{ versionHiddenCount }} 個版本未列出，共 {{ versionHiddenTotal }} 台——
        <a class="n" @click="showAllVersions = true">顯示全部</a>
      </p>
      <p v-else-if="showAllVersions && versionSorted.length > 10" class="tip">
        <a class="n" @click="showAllVersions = false">收合回前10名</a>
      </p>

      <!-- ===== 表2 ===== -->
      <h2>叢集分類與 ESXi 數量</h2>
      <p v-if="report.cluster_summary.unmapped_vcenters.length" class="warnline">
        ⚠ 有 vCenter 對不到機房：{{ report.cluster_summary.unmapped_vcenters.join('、') }}
        —— 請在 report_groups.json 補上，否則這批 ESXi 會歸在「未對應」
      </p>
      <div class="locwrap">
        <div v-for="loc in report.cluster_summary.locations" :key="loc.location" class="locbox">
          <div class="locname">{{ loc.location }}</div>
          <table class="rt small">
            <thead><tr>
              <SortTh k="service" :active="loc2Sort.key" :dir="loc2Sort.dir" @sort="loc2Toggle">叢集分類</SortTh>
              <SortTh k="esxi_count" :active="loc2Sort.key" :dir="loc2Sort.dir" @sort="loc2Toggle" class="num">ESXi 數量</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="r in sortRows(loc.rows, loc2Sort.key, loc2Sort.dir)" :key="r.service">
                <td class="rh">{{ r.service }}</td>
                <td class="num">
                  <a class="n" @click="drill('cluster', `${loc.location} ／ ${r.service}`, { location: loc.location, service: r.service })">{{ r.esxi_count }}</a>
                </td>
              </tr>
              <tr class="sum"><td class="rh">合計</td><td class="num">{{ loc.total }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ===== 表3 ===== -->
      <h2>虛擬化與 AIX、IBM i 環境資訊</h2>
      <table class="rt">
        <thead><tr>
          <SortTh k="location" :active="clusterSortKey" :dir="clusterSortDir" @sort="clusterToggle">Location</SortTh>
          <SortTh k="vcenter" :active="clusterSortKey" :dir="clusterSortDir" @sort="clusterToggle">vCenter IP</SortTh>
          <SortTh k="environment" :active="clusterSortKey" :dir="clusterSortDir" @sort="clusterToggle">環境</SortTh>
          <SortTh k="cluster" :active="clusterSortKey" :dir="clusterSortDir" @sort="clusterToggle">Cluster</SortTh>
          <SortTh k="count" :active="clusterSortKey" :dir="clusterSortDir" @sort="clusterToggle" class="num">臺數</SortTh>
          <SortTh k="version" :active="clusterSortKey" :dir="clusterSortDir" @sort="clusterToggle">版本</SortTh>
          <SortTh k="eos_status" :active="clusterSortKey" :dir="clusterSortDir" @sort="clusterToggle" class="ctr">EOS</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="c in clusterSorted" :key="c.vcenter + c.cluster + c.version">
            <td class="rh">{{ c.location }}</td>
            <td class="mono">{{ c.vcenter }}</td>
            <td>{{ c.environment }}</td>
            <td>{{ c.cluster || '（未歸叢集）' }}</td>
            <td class="num">
              <!-- R2：混版拆成一叢集多列，每列各自算自己的臺數，下鑽要帶 version
                   才會對到剛好這幾台（2026-08-21 backlog，跟表1「可追」同一原則）。 -->
              <a class="n" @click="drill('cluster', `${c.cluster} 的 ESXi（${c.version || '版本未知'}）`, { vcenter: c.vcenter, cluster: c.cluster, version: c.version })">{{ c.count }}</a>
            </td>
            <td class="mono" :class="{ mixed: c.mixed_version }">
              {{ c.version || '—' }}<span v-if="c.mixed_version" class="mixtag">混版</span>
            </td>
            <td class="ctr">
              <span v-if="c.eos_status" class="eosb" :class="eosClass(c.eos_status)">
                {{ c.eos_status }}<span v-if="c.eos_date"> {{ c.eos_date }}</span>
              </span>
              <span v-else class="dim">—</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 2026-08-21 使用者拍板：改成固定名單（見後端 physical_hosts_report()），
           不是規則自動偵測出來的10台。IP在hardware表對不到資產時仍要出現、標
           「查無登記」——不能因為對不到就悄悄從報告消失。 -->
      <h3>實體主機（AIX／IBM i）</h3>
      <table class="rt">
        <thead><tr>
          <SortTh k="location" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">Location</SortTh>
          <SortTh k="ip" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">IP</SortTh>
          <SortTh k="environment" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">環境</SortTh>
          <SortTh k="service" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">服務</SortTh>
          <SortTh k="hostname" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">主機名</SortTh>
          <SortTh k="product" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">作業系統</SortTh>
          <SortTh k="os_canonical" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">版本</SortTh>
          <SortTh k="eos_status" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle" class="ctr">EOS狀態</SortTh>
          <SortTh k="eos_date" :active="hostSortKey" :dir="hostSortDir" @sort="hostToggle">EOS日期</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="h in hostSorted" :key="h.ip"
              :class="{ notfound: !h.found }">
            <td class="rh">{{ h.location || '未填' }}</td>
            <td class="mono">{{ h.ip }}</td>
            <td>{{ h.environment || '未填' }}</td>
            <td>{{ h.service }}</td>
            <td>
              <span v-if="h.found">{{ h.hostname || '（無主機名）' }}</span>
              <span v-else class="bapend">⚠ 查無登記</span>
            </td>
            <td>{{ h.product || '—' }}</td>
            <td class="mono">{{ h.os_canonical || h.os_raw || '—' }}</td>
            <td class="ctr">
              <span v-if="h.eos_status" class="eosb" :class="eosClass(h.eos_status)">{{ h.eos_status }}</span>
              <span v-else class="dim">—</span>
            </td>
            <td class="mono">{{ h.eos_date || '—' }}</td>
          </tr>
          <tr v-if="!report.virtualization_env.physical_hosts.length">
            <td colspan="9" class="dim">無</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- ===== 下鑽面板 ===== -->
    <!-- 遮罩：讓面板真的浮在最上層，也讓人點旁邊就能關掉 -->
    <div v-if="drillOpen" class="drillmask" @click="drillOpen = false" />
    <div v-if="drillOpen" class="drill">
      <div class="dhd">
        <b>{{ drillTitle }}</b>
        <span class="dim">　{{ drillRows.length }} 筆</span>
        <button class="mini" @click="drillOpen = false">關閉</button>
      </div>
      <p v-if="drillLoading" class="dim">載入中…</p>
      <div v-else class="dwrap">
        <table class="rt small">
          <thead v-if="drillKind === 'platform'"><tr>
            <SortTh k="hostname" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">主機名</SortTh>
            <SortTh k="ip" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">IP</SortTh>
            <SortTh k="os_raw" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">OS 原始值</SortTh>
            <SortTh k="os_canonical" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">正規化</SortTh>
            <SortTh k="eos_status" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle" class="ctr">EOS</SortTh>
            <th>判定依據</th>
          </tr></thead>
          <thead v-else><tr>
            <SortTh k="host" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">ESXi</SortTh>
            <SortTh k="cluster" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">Cluster</SortTh>
            <SortTh k="vcenter" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">vCenter</SortTh>
            <SortTh k="version" :active="drillSort.key" :dir="drillSort.dir" @sort="drillToggle">版本</SortTh>
            <th>判定依據</th>
          </tr></thead>
          <tbody v-if="drillKind === 'platform'">
            <tr v-for="r in drillSorted" :key="r.asset_serial">
              <td class="rh">{{ r.hostname || r.asset_serial }}</td>
              <td class="mono">{{ r.ip }}</td>
              <td>{{ r.os_raw || '（空）' }}</td>
              <td>{{ r.os_canonical || '認不出' }}</td>
              <td class="ctr">{{ r.eos_date || r.eos_status }}</td>
              <td class="dim">{{ r.reason }}</td>
            </tr>
          </tbody>
          <tbody v-else>
            <tr v-for="r in drillSorted" :key="r.host + r.cluster">
              <td class="rh mono">{{ r.host }}</td>
              <td>{{ r.cluster }}</td>
              <td class="mono">{{ r.vcenter }}</td>
              <td>{{ r.version }}</td>
              <td class="dim">{{ r.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 18px 22px 60px; }
.hd { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
h1 { font-size: 19px; margin: 0; }
h2 { font-size: 15px; margin: 26px 0 8px; }
h3 { font-size: 13px; margin: 18px 0 6px; color: var(--ink-soft); }
.hdact { display: flex; gap: 8px; align-items: center; }
.mon { padding: 6px 10px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); }
.btn { padding: 6px 12px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover { border-color: var(--brand); }

/* 資料新鮮度：固定顯示、不可關 */
.fresh { margin: 12px 0 4px; padding: 7px 11px; border-radius: 5px; font-size: 12px;
         background: rgba(0,145,66,.08); color: var(--ink-soft);
         border: 1px solid rgba(0,145,66,.2); }
.fresh.warn { background: rgba(224,176,96,.1); color: var(--warn-text);
              border-color: rgba(224,176,96,.35); }
.tip { font-size: 11px; color: var(--ink-soft); margin: 4px 0 0; }
.warnline { font-size: 12px; color: var(--warn-text); margin: 0 0 8px; }

/* 表格刻意樸素：這些是要被整塊複製貼進 Word/PPT 的，
   花俏的底色與陰影貼過去只會變成一堆雜訊。
   對齊改成按欄位性質給：文字/代號類左靠（預設）、真正的數字右靠（.num）、
   徽章類置中（.ctr）——2026-08-21 使用者：「該往前對齊的要往前對齊，該置中
   的要置中」，原本整張表除了第一欄全部靠右，連IP/版本這種文字也被擠右邊。 */
.rt { border-collapse: collapse; font-size: 12.5px; }
.rt th, .rt td { border: 1px solid var(--border); padding: 5px 11px; text-align: left; }
.rt th { background: rgba(15,23,42,.04); color: var(--ink-soft); font-weight: 600; font-size: 11.5px; }
.rt .num, .rt th.num { text-align: right; }
.rt .ctr, .rt th.ctr { text-align: center; }
.rt .wide { min-width: 240px; }
.rt.small { font-size: 11.5px; }
.rt .sum td { background: rgba(15,23,42,.03); font-weight: 600; }
.mono { font-family: ui-monospace, monospace; }
.z { color: var(--border-strong); }
.dim { color: var(--ink-soft); }

/* 每個數字都是連結——這是「可追」的入口 */
.n { color: var(--brand-dark); cursor: pointer; text-decoration: none; }
.n:hover { text-decoration: underline; }

.note { cursor: pointer; color: var(--ink-soft); font-size: 11.5px; }
.note:hover { color: var(--ink); }
.ni { padding: 3px 7px; border-radius: 4px; border: 1px solid var(--border-strong);
      background: var(--card); color: var(--ink); min-width: 200px; font-size: 11.5px; }
.mini { margin-left: 5px; padding: 2px 8px; font-size: 11px; border-radius: 4px;
        border: 1px solid var(--border-strong); background: var(--card);
        color: var(--ink); cursor: pointer; }

.locwrap { display: flex; gap: 18px; flex-wrap: wrap; }
.locname { font-size: 12px; color: var(--ink-soft); margin-bottom: 4px; }
.mixed { color: var(--warn-text); }
.mixtag { font-size: 10px; margin-left: 5px; padding: 1px 5px; border-radius: 3px;
          background: rgba(224,176,96,.16); }

.eosb { display: inline-block; font-size: 10px; margin-left: 6px; padding: 1px 6px;
        border-radius: 3px; white-space: nowrap; }
.eos-expired { color: var(--bad); background: rgba(255,107,107,.14); }
.eos-upcoming { color: var(--warn-text); background: rgba(224,176,96,.16); }
.eos-ok { color: var(--brand-dark); background: rgba(0,145,66,.14); }
.eos-unknown { color: var(--ink-soft); background: rgba(15,23,42,.06); }
.eos-nodate { color: var(--ink-aux); background: rgba(127,180,217,.14); }

.bapend { color: var(--bad); font-size: 11.5px; font-weight: 600; }
.notfound { opacity: .75; }

/* 下鑽面板：固定在底部浮在最上層，一定要用不透明底色（--card-solid／--card
   現在都是實心白 #ffffff，兩者皆可），不可以用半透明玻璃卡效果——拿來當
   浮動面板疊在頁面上，底下整頁的文字會透出來疊在一起完全看不懂
   （2026-08-21 使用者截圖回報「點選 AIX/IBM i 畫面很亂」，就是這個）。 */
.drillmask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 39; }
.drill { position: fixed; left: 0; right: 0; bottom: 0; max-height: 60vh;
         background: var(--card-solid); border-top: 2px solid var(--brand);
         box-shadow: 0 -6px 22px rgba(0,0,0,.6); display: flex; flex-direction: column; z-index: 40; }
.dhd { padding: 9px 14px; border-bottom: 1px solid var(--border-strong); font-size: 13px;
       display: flex; align-items: center; background: var(--card-solid);
       position: sticky; top: 0; }
.dhd .mini { margin-left: auto; }
.dwrap { overflow: auto; padding: 8px 14px 14px; background: var(--card-solid); }
/* 清單也用實心底，並讓表頭在捲動時黏住——200 台捲到一半就忘記哪欄是哪欄 */
.dwrap .rt { background: var(--card-solid); width: 100%; }
.dwrap .rt thead th { position: sticky; top: 0; background: var(--card-solid); z-index: 1; }
/* 判定依據那欄字多，給它換行不要撐爆表格 */
.dwrap .rt td:last-child { white-space: normal; min-width: 260px; max-width: 480px; }
</style>
