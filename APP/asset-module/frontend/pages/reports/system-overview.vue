<script setup lang="ts">
// 部門報告圖表頁 B：主機系統總覽（對應原簡報第2頁：全環境系統架構分布＋
// 運算平台概況＋各機房系統資源分布）。
//
// 2026-08-25 使用者拍板兩件事：
// 1. 頁面拆法要跟原簡報頁數一致（三張圖＝三頁）——業務系統排行／分類對照表
//    維護搬去 5c-3，這頁只留原簡報第2頁的內容。
// 2. 版面要「一模一樣」——圓環圖＋卡片，不是純表格。跟頁A（physical-distribution.vue）
//    同一套 DonutChart 元件、同一套卡片風格，三頁要看起來像同一份報告工具。
definePageMeta({ ssr: false })

interface CategoryRow { name: string; count: number; color: string; pct: number }
interface RoomRow { room: string; core: number; noncore: number; test: number; uncategorized: number; total: number }
interface SystemOverview {
  total: number; core: number; noncore: number; test: number; uncategorized: number
  vm: number; physical: number; virtualization_rate: number
  rooms: RoomRow[]
  core_categories: CategoryRow[]; noncore_categories: CategoryRow[]
  systems_without_category: number; category_note: string; room_note: string
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const data = ref<SystemOverview | null>(null)
const loading = ref(false)
const errorMessage = ref('')

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    data.value = await apiFetch<SystemOverview>('/api/reports/system-overview')
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
await load()

const { sortKey: rKey, sortDir: rDir, toggle: rToggle, sorted: roomsSorted } =
  useSort(computed(() => data.value?.rooms ?? []), '')

// 全環境組成圓環：核心/非核心/測試三色固定，跟頁A的業務分類色（--chart-N 循環）
// 分開一套——這三色是粗分類，語意固定，不該隨類別數量變動而換色。
const GROUP_COLOR: Record<string, string> = {
  核心交易: 'var(--brand-dark)', 非核心: 'var(--chart-2)', 測試: 'var(--warn-text)',
}
const GROUP_BUCKET: Record<string, string> = { 核心交易: 'core', 非核心: 'noncore', 測試: 'test' }
const groupSegments = computed(() => {
  if (!data.value) return []
  return [
    { name: '核心交易', count: data.value.core, color: GROUP_COLOR['核心交易'] },
    { name: '非核心', count: data.value.noncore, color: GROUP_COLOR['非核心'] },
    { name: '測試', count: data.value.test, color: GROUP_COLOR['測試'] },
  ]
})
const groupPct = (n: number) => data.value?.total ? (n / data.value.total * 100).toFixed(1) : '0.0'

const platformSegments = computed(() => {
  if (!data.value) return []
  return [
    { name: 'Virtual Machine', count: data.value.vm, color: 'var(--brand-dark)' },
    { name: 'Physical Server', count: data.value.physical, color: 'var(--chart-gray)' },
  ]
})
const platformPct = (n: number) => data.value?.total ? (n / data.value.total * 100).toFixed(1) : '0.0'

// ===== 頂部圖表／機房分布表下鑽 =====
interface DrillRow {
  asset_serial: string; hostname: string | null; ip: string | null; api_id: string | null
  os_raw: string | null; os_canonical: string | null
  location: string | null; environment: string | null; reason: string
}
const drillOpen = ref(false)
const drillTitle = ref('')
const drillRows = ref<DrillRow[]>([])
const drillLoading = ref(false)
const { sortKey: dKey, sortDir: dDir, toggle: dToggle, sorted: drillSorted } = useSort(drillRows, '')

function onEsc(e: KeyboardEvent) { if (e.key === 'Escape' && drillOpen.value) drillOpen.value = false }
onMounted(() => window.addEventListener('keydown', onEsc))
onUnmounted(() => window.removeEventListener('keydown', onEsc))

async function drill(title: string, query: Record<string, string>) {
  drillOpen.value = true
  drillTitle.value = title
  drillLoading.value = true
  drillRows.value = []
  dKey.value = ''
  try {
    drillRows.value = await apiFetch<DrillRow[]>('/api/reports/system-overview/drill', { params: query })
  } catch (err: any) {
    showToast(`載入失敗：${err?.data?.detail ?? err?.message ?? '請稍後再試'}`, 'error')
    drillOpen.value = false
  } finally {
    drillLoading.value = false
  }
}
function drillGroup(name: string) {
  const bucket = GROUP_BUCKET[name]
  if (bucket) drill(name, { bucket })
}
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>主機系統總覽</h1>
      <button class="btn" @click="load">重新整理</button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="loading" class="dim">載入中…</p>

    <template v-else-if="data">
      <div class="note warn">{{ data.category_note }}</div>

      <div class="top2">
        <!-- 全環境系統架構分布：核心/非核心/測試 -->
        <div class="card chartcard">
          <div class="room-hd">全環境系統架構分布</div>
          <DonutChart
            :segments="groupSegments" :size="196" :stroke-width="22"
            center-label="全環境設備" @segment-click="drillGroup"
          />
          <div class="legend">
            <a v-for="s in groupSegments" :key="s.name" class="lg" @click="drillGroup(s.name)">
              <i class="sw" :style="{ background: s.color }" />{{ s.name }}服務
              <b class="mono">{{ s.count.toLocaleString() }}</b>
              <span class="pctlbl">{{ groupPct(s.count) }}%</span>
            </a>
          </div>
        </div>

        <!-- 運算平台概況：VM vs 實體機＋虛擬化率 -->
        <div class="card chartcard">
          <div class="room-hd">運算平台概況</div>
          <div class="platrow">
            <DonutChart
              :segments="platformSegments" :size="120" :stroke-width="14"
              center-label="總設備數" @segment-click="() => {}"
            />
            <div class="platstats">
              <div class="prow"><i class="sw" :style="{ background: 'var(--brand-dark)' }" />Virtual Machine
                <b class="mono">{{ data.vm.toLocaleString() }}</b>
                <span class="pctlbl">{{ platformPct(data.vm) }}%</span></div>
              <div class="prow"><i class="sw" :style="{ background: 'var(--chart-gray)' }" />Physical Server
                <b class="mono">{{ data.physical.toLocaleString() }}</b>
                <span class="pctlbl">{{ platformPct(data.physical) }}%</span></div>
            </div>
          </div>
          <div class="vrate">
            <span class="vrate-l">系統虛擬化率</span>
            <span class="vrate-n mono">{{ data.virtualization_rate }}%</span>
          </div>
        </div>
      </div>

      <p v-if="data.uncategorized" class="tip">
        其中 <a class="n" @click="drill('尚未分類', { bucket: 'uncategorized' })">{{ data.uncategorized.toLocaleString() }}</a>
        台正式/備援環境的機器還沒有業務分類——到
        <NuxtLink to="/reports/business-systems">業務系統排行</NuxtLink>
        頁補上對照表就會自動歸類。
      </p>

      <!-- 各機房系統資源分布：實體機＋虛擬機合計，跟上面同一份分類邏輯 -->
      <h2>各機房系統資源分布（實體機＋虛擬機）</h2>
      <div class="note warn">{{ data.room_note }}</div>
      <table class="rt">
        <thead><tr>
          <SortTh k="room" :active="rKey" :dir="rDir" @sort="rToggle">機房</SortTh>
          <SortTh k="core" :active="rKey" :dir="rDir" @sort="rToggle" class="num">核心交易</SortTh>
          <SortTh k="noncore" :active="rKey" :dir="rDir" @sort="rToggle" class="num">非核心</SortTh>
          <SortTh k="test" :active="rKey" :dir="rDir" @sort="rToggle" class="num">測試</SortTh>
          <SortTh k="total" :active="rKey" :dir="rDir" @sort="rToggle" class="num">全環境</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="r in roomsSorted" :key="r.room">
            <td>{{ r.room }}</td>
            <td class="num">
              <a v-if="r.core" class="n" @click="drill(`${r.room}・核心交易`, { room: r.room, bucket: 'core' })">{{ r.core.toLocaleString() }}</a>
              <span v-else class="dim">-</span>
            </td>
            <td class="num">
              <a v-if="r.noncore" class="n" @click="drill(`${r.room}・非核心`, { room: r.room, bucket: 'noncore' })">{{ r.noncore.toLocaleString() }}</a>
              <span v-else class="dim">-</span>
            </td>
            <td class="num">
              <a v-if="r.test" class="n" @click="drill(`${r.room}・測試`, { room: r.room, bucket: 'test' })">{{ r.test.toLocaleString() }}</a>
              <span v-else class="dim">-</span>
            </td>
            <td class="num">
              <a v-if="r.total" class="n" @click="drill(`${r.room}・全環境`, { room: r.room })">{{ r.total.toLocaleString() }}</a>
              <span v-else class="dim">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

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
          <thead><tr>
            <SortTh k="hostname" :active="dKey" :dir="dDir" @sort="dToggle">主機名</SortTh>
            <SortTh k="ip" :active="dKey" :dir="dDir" @sort="dToggle">IP</SortTh>
            <SortTh k="api_id" :active="dKey" :dir="dDir" @sort="dToggle">業務系統</SortTh>
            <SortTh k="environment" :active="dKey" :dir="dDir" @sort="dToggle">環境別</SortTh>
            <SortTh k="os_canonical" :active="dKey" :dir="dDir" @sort="dToggle">OS</SortTh>
            <th>判定依據</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in drillSorted" :key="r.asset_serial">
              <td class="rh">{{ r.hostname || r.asset_serial }}</td>
              <td class="mono">{{ r.ip }}</td>
              <td class="mono">{{ r.api_id || '（無）' }}</td>
              <td>{{ r.environment || '（空）' }}</td>
              <td>{{ r.os_canonical || '認不出' }}</td>
              <td class="dim">{{ r.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 版面、表格、下鑽面板風格沿用頁A／系統組月報，三頁要像同一套報告工具。 */
.page { padding: 18px 22px 60px; }
.hd { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
h1 { font-size: 19px; margin: 0; }
h2 { font-size: 15px; margin: 26px 0 8px; }
.btn { padding: 6px 12px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; font-family: inherit; font-size: 12.5px; }
.btn:hover { border-color: var(--brand); }
.dim { color: var(--ink-soft); }
.mono { font-family: ui-monospace, monospace; }

.note { margin: 12px 0 4px; padding: 7px 11px; border-radius: 5px; font-size: 12px; }
.note.warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid rgba(176,106,0,.3); }
.tip { font-size: 12px; color: var(--ink-soft); margin: 8px 0; }
.tip .n { color: var(--brand-dark); cursor: pointer; text-decoration: none; }
.tip .n:hover { text-decoration: underline; }
.tip :deep(a) { color: var(--brand-dark); }

.top2 { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        box-shadow: var(--shadow); padding: 16px; }
.chartcard { flex: 1 1 300px; display: flex; flex-direction: column; align-items: center; }
.room-hd { font-size: 14px; font-weight: 600; color: var(--ink); margin-bottom: 10px; align-self: flex-start; }
.legend { width: 100%; margin-top: 12px; display: flex; flex-direction: column; gap: 5px; }
.lg { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--ink-aux);
      text-decoration: none; cursor: pointer; padding: 3px 4px; border-radius: 4px; }
.lg:hover { background: var(--sub, rgba(0,0,0,.03)); color: var(--ink); }
.lg .sw { width: 9px; height: 9px; border-radius: 3px; flex: none; }
.lg b { margin-left: auto; color: var(--ink); }
.pctlbl { color: var(--ink-soft); font-size: 11px; min-width: 42px; text-align: right; }

.platrow { display: flex; align-items: center; gap: 16px; width: 100%; }
.platstats { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.prow { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--ink-aux); }
.prow .sw { width: 9px; height: 9px; border-radius: 3px; flex: none; }
.prow b { margin-left: auto; color: var(--ink); }
.vrate { width: 100%; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border);
         display: flex; align-items: baseline; justify-content: space-between; }
.vrate-l { font-size: 12.5px; color: var(--ink-soft); }
.vrate-n { font-size: 24px; font-weight: 700; color: var(--brand-dark); letter-spacing: -1px; }

.rt { border-collapse: collapse; font-size: 12.5px; width: 100%; }
.rt th, .rt td { border: 1px solid var(--border); padding: 5px 11px; text-align: left; }
.rt th { background: var(--sub, rgba(15,23,42,.04)); color: var(--ink-soft); font-weight: 600; font-size: 11.5px; }
.rt .num, .rt th.num { text-align: right; }
.rt.small { font-size: 11.5px; }
.n { color: var(--brand-dark); cursor: pointer; text-decoration: none; }
.n:hover { text-decoration: underline; }

/* 下鑽面板：同系統組月報 */
.drillmask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 39; }
.drill { position: fixed; left: 0; right: 0; bottom: 0; max-height: 60vh;
         background: var(--card-solid); border-top: 2px solid var(--brand);
         box-shadow: 0 -6px 22px rgba(0,0,0,.25); display: flex; flex-direction: column; z-index: 40; }
.dhd { padding: 9px 14px; border-bottom: 1px solid var(--border-strong); font-size: 13px;
       display: flex; align-items: center; background: var(--card-solid); position: sticky; top: 0; }
.mini { margin-left: auto; padding: 2px 8px; font-size: 11px; border-radius: 4px;
        border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.dwrap { overflow: auto; padding: 8px 14px 14px; background: var(--card-solid); }
.dwrap .rt { background: var(--card-solid); }
.dwrap .rt thead th { position: sticky; top: 0; background: var(--card-solid); z-index: 1; }
.dwrap .rt td:last-child { white-space: normal; min-width: 260px; max-width: 480px; }

@media (max-width: 700px) { .top2 { flex-direction: column; } }
</style>
