<script setup lang="ts">
// 部門報告圖表頁 C：業務系統排行（對應原簡報第3頁：核心交易服務Top5＋其他重要
// 系統／非核心服務明細）。
//
// 2026-08-25 使用者拍板兩件事：
// 1. 頁面拆法要跟原簡報頁數一致（三張圖＝三頁）——這頁原本併在 system-overview.vue
//    （5c-2）下半部，現在獨立成 5c-3。
// 2. 分類要跟簡報一樣細（核心9項、非核心10項），不維持粗略二分——所以這頁不是
//    單純「依台數排序的系統清單」，是照簡報排版：核心交易服務左欄（Top5個別系統
//    ＋其他重要系統依分類分卡片），非核心服務右欄（依分類分卡片）。
// 資料來源、下鑽、分類對照表機制都跟 5c-2 共用同一支 /api/reports/system-overview*
// （本來就是同一份 report_baseline() 算出來的，拆頁不拆資料，兩邊數字才會永遠對得上）。
definePageMeta({ ssr: false })

interface SystemRow { api_id: string; name: string; count: number; category: string | null }
interface Top5Row { api_id: string; name: string; count: number }
interface CategoryRow { name: string; count: number; color: string; pct: number }
interface SystemOverview {
  total: number; core: number; noncore: number; test: number
  core_categories: CategoryRow[]; noncore_categories: CategoryRow[]
  core_top5: Top5Row[]; core_top5_pct: number[]; core_other_count: number
  top5: SystemRow[]; noncore_systems: SystemRow[]; all_systems: SystemRow[]
  systems_without_category: number; category_note: string
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

const { sortKey, sortDir, toggle, sorted: systemsSorted } =
  useSort(computed(() => data.value?.all_systems ?? []), '')
const showAll = ref(false)
const groupPct = (n: number) => data.value?.total ? (n / data.value.total * 100).toFixed(1) : '0.0'

// ===== 下鑽 =====
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

// ===== 業務系統分類對照表：下載空白範本／上傳填好的檔 =====
const runtimeConfig = useRuntimeConfig()
const templateDownloading = ref(false)
async function downloadTemplate() {
  templateDownloading.value = true
  try {
    const res = await fetch(`${runtimeConfig.public.apiBase}/api/reports/system-category/template`,
      { credentials: 'include' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m ? decodeURIComponent(m[1]) : 'system_category_template.xlsx'
    a.click()
    URL.revokeObjectURL(url)
  } catch (err: any) {
    showToast(`下載失敗：${err?.message ?? '請稍後再試'}`, 'error')
  } finally {
    templateDownloading.value = false
  }
}

const uploading = ref(false)
const uploadMsg = ref('')
async function uploadCategory(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploading.value = true
  uploadMsg.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    const r = await apiFetch<{ accepted: number; rejected: number; total_mapped: number
                               valid_categories: string[] }>(
      '/api/reports/system-category/import', { method: 'POST', body: fd })
    uploadMsg.value = r.rejected
      ? `已套用 ${r.accepted} 筆；${r.rejected} 筆分類名稱看不懂（只認：${r.valid_categories.join('／')}），未套用`
      : `已套用 ${r.accepted} 筆分類`
    showToast(uploadMsg.value, r.rejected ? 'warn' : 'success')
    await load()
  } catch (err: any) {
    const msg = err?.data?.detail ?? err?.message ?? '上傳失敗，請稍後再試'
    uploadMsg.value = msg
    showToast(msg, 'error')
  } finally {
    uploading.value = false
    ;(e.target as HTMLInputElement).value = ''
  }
}
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>業務系統排行</h1>
      <button class="btn" @click="load">重新整理</button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="loading" class="dim">載入中…</p>

    <template v-else-if="data">
      <div class="note warn">{{ data.category_note }}</div>

      <!-- 頂部系統組成列：跟原簡報同一組數字（核心/非核心/測試/全環境），
           跟 5c-2 的圓環圖是同一份資料換個呈現方式——這頁重複顯示一次是因為
           原簡報這頁本來就有自己的頂列，不是漏做。 -->
      <div class="topstrip">
        <div class="tcell label"><i class="ticon gray">▤</i><span>全環境<br>系統組成</span></div>
        <a class="tcell" @click="drill('核心交易', { bucket: 'core' })">
          <i class="ticon core">📈</i>
          <span class="tname">核心交易服務</span>
          <b class="tnum core mono">{{ data.core.toLocaleString() }} 台</b>
          <span class="tpct">({{ groupPct(data.core) }}%)</span>
        </a>
        <a class="tcell" @click="drill('非核心', { bucket: 'noncore' })">
          <i class="ticon noncore">▥</i>
          <span class="tname">非核心服務</span>
          <b class="tnum noncore mono">{{ data.noncore.toLocaleString() }} 台</b>
          <span class="tpct">({{ groupPct(data.noncore) }}%)</span>
        </a>
        <a class="tcell" @click="drill('測試環境', { bucket: 'test' })">
          <i class="ticon test">⚗</i>
          <span class="tname">測試環境</span>
          <b class="tnum test mono">{{ data.test.toLocaleString() }} 台</b>
          <span class="tpct">({{ groupPct(data.test) }}%)</span>
        </a>
        <a class="tcell" @click="drill('全環境', {})">
          <i class="ticon gray">▤</i>
          <span class="tname">全環境設備</span>
          <b class="tnum mono">{{ data.total.toLocaleString() }} 台</b>
          <span class="tpct">(100%)</span>
        </a>
      </div>

      <div class="cols">
        <!-- 核心交易服務：Top5個別系統 + 其他重要系統依分類 -->
        <div class="card col">
          <div class="col-hd core">核心交易服務<span class="dim sm">　共 {{ data.core.toLocaleString() }} 台</span></div>

          <!-- 業務洞察文字：原簡報上的分析描述，固定文字，不是系統算出來的——
               跟人數/交易量沒有連動，改資料不會自動更新這段話。 -->
          <div class="insight">
            <i class="iicon core">📈</i>
            <div>
              <b>與台股交易熱度呈高度正相關成長</b>
              <p class="dim sm">成交量提升、投資人活躍度提高及交易筆數增加時，系統資源需求同步成長。</p>
            </div>
          </div>

          <div class="ranklist">
            <a v-for="(s, i) in data.core_top5" :key="s.api_id" class="rankrow"
               @click="drill(s.name, { api_id: s.api_id, bucket: 'core' })">
              <i class="rank">{{ i + 1 }}</i>
              <span class="rname">{{ s.name }}</span>
              <b class="mono">{{ s.count.toLocaleString() }}</b>
              <span class="pctlbl">{{ (data.core_top5_pct[i] ?? 0).toFixed(1) }}%</span>
            </a>
            <p v-if="!data.core_top5.length" class="dim sm">核心交易服務目前還沒有系統被分類</p>
          </div>
          <p v-if="data.core_other_count" class="subtotal">
            核心交易服務其他系統（小計）
            <a class="n" @click="drill('核心交易服務其他系統', { bucket: 'core' })">{{ data.core_other_count.toLocaleString() }}</a>
          </p>

          <div class="cathd">其他重要系統（核心交易服務）</div>
          <div class="catgrid">
            <a v-for="c in data.core_categories" :key="c.name" class="catchip2"
               :class="{ zero: !c.count }" @click="c.count && drill(c.name, { category: c.name })">
              <i class="sw" :style="{ background: `var(--${c.color})` }" />
              <span class="cname">{{ c.name }}</span>
              <b class="mono">{{ c.count.toLocaleString() }}</b>
              <span class="pctlbl">{{ c.pct.toFixed(1) }}%</span>
            </a>
          </div>
        </div>

        <!-- 非核心服務：依分類 -->
        <div class="card col">
          <div class="col-hd noncore">非核心服務<span class="dim sm">　共 {{ data.noncore.toLocaleString() }} 台</span></div>

          <!-- 業務洞察文字：同左欄，原簡報固定描述，不是即時計算。 -->
          <div class="insight">
            <i class="iicon noncore">👤</i>
            <div>
              <b>與開戶人數呈高度相關成長</b>
              <p class="dim sm">客戶服務／數位通路／語音系統隨開戶人數增加同步成長。</p>
            </div>
          </div>
          <div class="insight">
            <i class="iicon noncore">🏢</i>
            <div>
              <b>與公司規模呈高度相關成長</b>
              <p class="dim sm">監控維運平台、內部應用支援、辦公支援系統隨公司規模擴大同步成長。</p>
            </div>
          </div>

          <div class="catgrid full">
            <a v-for="c in data.noncore_categories" :key="c.name" class="catchip2"
               :class="{ zero: !c.count }" @click="c.count && drill(c.name, { category: c.name })">
              <i class="sw" :style="{ background: `var(--${c.color})` }" />
              <span class="cname">{{ c.name }}</span>
              <b class="mono">{{ c.count.toLocaleString() }}</b>
              <span class="pctlbl">{{ c.pct.toFixed(1) }}%</span>
            </a>
            <p v-if="!data.noncore_categories.length" class="dim sm">非核心服務目前還沒有系統被分類</p>
          </div>
        </div>
      </div>

      <!-- 重點摘要：跟頂列同一組百分比數字（可點下鑽），加三句原簡報的固定業務
           洞察文字（不可點——那是敘述文字，不是查得到清單的數字）。 -->
      <div class="summary">
        <a class="scell" @click="drill('核心交易', { bucket: 'core' })">
          <i class="sicon ok">✓</i>
          <div><b>核心交易服務</b><span class="dim sm">占全環境 {{ groupPct(data.core) }}%（{{ data.core.toLocaleString() }} 台）</span></div>
        </a>
        <a class="scell" @click="drill('非核心', { bucket: 'noncore' })">
          <i class="sicon ok">✓</i>
          <div><b>非核心服務</b><span class="dim sm">占全環境 {{ groupPct(data.noncore) }}%（{{ data.noncore.toLocaleString() }} 台）</span></div>
        </a>
        <a class="scell" @click="drill('測試環境', { bucket: 'test' })">
          <i class="sicon ok">✓</i>
          <div><b>測試環境</b><span class="dim sm">占全環境 {{ groupPct(data.test) }}%（{{ data.test.toLocaleString() }} 台）</span></div>
        </a>
        <div class="scell static">
          <i class="sicon note">📈</i>
          <div><b>核心交易服務</b><span class="dim sm">與台股交易熱度呈高度正相關</span></div>
        </div>
        <div class="scell static">
          <i class="sicon note">👤</i>
          <div><b>客戶服務系統</b><span class="dim sm">與開戶人數呈高度相關</span></div>
        </div>
        <div class="scell static">
          <i class="sicon note">🏢</i>
          <div><b>維運與辦公系統</b><span class="dim sm">與公司規模呈高度相關</span></div>
        </div>
      </div>

      <!-- 完整系統清單：簡報上沒有，但天條二（資料點可追蹤）要求每個數字都能查
           到底——這張表是所有業務系統的原始排行，不受上面分類影響，隨時可查。 -->
      <h2>
        <a class="toggle" @click="showAll = !showAll">{{ showAll ? '收合' : '展開' }}完整系統清單（依台數排序）▾</a>
      </h2>
      <table v-if="showAll" class="rt">
        <thead><tr>
          <th class="num">名次</th>
          <SortTh k="name" :active="sortKey" :dir="sortDir" @sort="toggle">系統名稱</SortTh>
          <SortTh k="api_id" :active="sortKey" :dir="sortDir" @sort="toggle">API ID</SortTh>
          <SortTh k="count" :active="sortKey" :dir="sortDir" @sort="toggle" class="num">台數</SortTh>
          <SortTh k="category" :active="sortKey" :dir="sortDir" @sort="toggle">分類</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="(s, i) in systemsSorted" :key="s.api_id" :class="{ top5: i < 5 && !sortKey }">
            <td class="num dim">{{ i < 5 && !sortKey ? i + 1 : '·' }}</td>
            <td>{{ s.name }}</td>
            <td class="mono">{{ s.api_id }}</td>
            <td class="num">
              <a class="n" @click="drill(s.name, { api_id: s.api_id })">{{ s.count.toLocaleString() }}</a>
            </td>
            <td>
              <span v-if="s.category" class="catchip">{{ s.category }}</span>
              <span v-else class="dim sm">未分類</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 業務系統分類對照表：讀取機制先接好，表一到就生效，不用再改程式 -->
      <h2>業務系統分類對照表</h2>
      <div class="card catcard">
        <p class="tip">
          下載範本填好分類欄再上傳（可用分類見範本第二分頁），
          <NuxtLink to="/reports/system-overview">主機系統總覽</NuxtLink>
          頁與這裡都會立刻反映——
          {{ data.systems_without_category }} / {{ data.all_systems.length }} 個系統尚未分類。
        </p>
        <!-- 2026-08-26：使用者把這張表誤認成主機分類的匯入口。兩個功能長得很像，
             差別要寫在畫面上，不能只寫在程式註解裡。 -->
        <p class="tip warn">
          ⚠ 這張表是<b>按業務系統（APID）</b>分類，<b>一個 APID 一類</b>，
          只當「那台主機還沒逐台分類時的預設值」。
          <b>實測 155 個 APID 裡有 88 個底下的機器橫跨多種分類</b>——同一個業務系統的
          機器不見得同一類，所以真正決定報表數字的是<b>逐台的分類</b>。
          要用管理員那份盤點表一次帶入 2000 多台，請到
          <NuxtLink to="/reports/classify#seed">主機分類作業 → 從外部盤點表帶入分類</NuxtLink>。
        </p>
        <div class="catrow">
          <button class="btn" :disabled="templateDownloading" @click="downloadTemplate">
            {{ templateDownloading ? '準備中…' : '⬇ 下載空白範本（依台數排序）' }}
          </button>
          <label class="btn upload">
            {{ uploading ? '上傳中…' : '⬆ 上傳填好的對照表' }}
            <input type="file" accept=".xlsx" :disabled="uploading" hidden @change="uploadCategory">
          </label>
        </div>
        <p v-if="uploadMsg" class="dim sm">{{ uploadMsg }}</p>
      </div>
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
.tip.warn { color: var(--warn); }
/* 版面、表格、下鑽面板風格沿用頁A／頁B，三頁要像同一套報告工具。 */
.page { padding: 18px 22px 60px; }
.hd { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
h1 { font-size: 19px; margin: 0; }
h2 { font-size: 15px; margin: 26px 0 8px; }
.btn { padding: 6px 12px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; font-family: inherit; font-size: 12.5px; }
.btn:hover { border-color: var(--brand); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn.upload { display: inline-flex; align-items: center; }
.dim { color: var(--ink-soft); }
.dim.sm { font-size: 11px; }
.mono { font-family: ui-monospace, monospace; }

.note { margin: 12px 0 4px; padding: 7px 11px; border-radius: 5px; font-size: 12px; }
.note.warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid rgba(176,106,0,.3); }
.tip { font-size: 12px; color: var(--ink-soft); margin: 8px 0; }
.tip :deep(a) { color: var(--brand-dark); }

.topstrip { display: flex; flex-wrap: wrap; gap: 1px; margin-top: 14px; border-radius: var(--radius);
            overflow: hidden; border: 1px solid var(--border); background: var(--border); }
.tcell { flex: 1 1 160px; display: flex; align-items: center; gap: 8px; padding: 10px 14px;
         background: var(--card); text-decoration: none; cursor: pointer; }
.tcell:hover { background: var(--sub, rgba(0,0,0,.03)); }
.tcell.label { cursor: default; }
.tcell.label:hover { background: var(--card); }
.ticon { font-style: normal; font-size: 16px; width: 30px; height: 30px; border-radius: 6px;
         display: flex; align-items: center; justify-content: center; flex: none; }
.ticon.core { background: var(--good-soft); color: var(--brand-dark); }
.ticon.noncore { background: rgba(37,99,235,.12); color: var(--chart-2); }
.ticon.test { background: var(--warn-soft); color: var(--warn-text); }
.ticon.gray { background: var(--sub, rgba(15,23,42,.06)); color: var(--ink-soft); }
.tname { font-size: 11.5px; color: var(--ink-soft); display: block; }
.tnum { display: block; font-size: 15px; font-weight: 700; color: var(--ink); }
.tnum.core { color: var(--brand-dark); }
.tnum.noncore { color: var(--chart-2); }
.tnum.test { color: var(--warn-text); }
.tpct { font-size: 11px; color: var(--ink-soft); }

.insight { display: flex; gap: 9px; align-items: flex-start; margin-bottom: 10px;
           padding: 8px 9px; border-radius: 6px; background: var(--sub, rgba(0,128,106,.05)); }
.iicon { font-style: normal; font-size: 15px; width: 26px; height: 26px; border-radius: 50%;
         display: flex; align-items: center; justify-content: center; flex: none; }
.iicon.core { background: var(--good-soft); }
.iicon.noncore { background: rgba(37,99,235,.12); }
.insight b { font-size: 12px; color: var(--ink); display: block; }
.insight p { margin: 2px 0 0; }

.summary { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
.scell { flex: 1 1 200px; display: flex; align-items: center; gap: 9px; padding: 10px 12px;
         border-radius: var(--radius); background: var(--card); border: 1px solid var(--border);
         box-shadow: var(--shadow); text-decoration: none; cursor: pointer; }
.scell:hover { border-color: var(--brand); }
.scell.static { cursor: default; }
.scell.static:hover { border-color: var(--border); }
.sicon { font-style: normal; width: 26px; height: 26px; border-radius: 50%; display: flex;
         align-items: center; justify-content: center; flex: none; font-size: 13px; }
.sicon.ok { background: var(--good-soft); color: var(--brand-dark); }
.sicon.note { background: var(--sub, rgba(15,23,42,.06)); color: var(--ink-soft); }
.scell b { font-size: 12.5px; color: var(--ink); display: block; }
.scell span { display: block; }

.cols { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; align-items: flex-start; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        box-shadow: var(--shadow); padding: 16px; }
.col { flex: 1 1 380px; }
.col-hd { font-size: 15px; font-weight: 700; color: var(--ink); margin-bottom: 12px;
          padding-bottom: 8px; border-bottom: 2px solid var(--border); }
.col-hd.core { border-bottom-color: var(--brand-dark); }
.col-hd.noncore { border-bottom-color: var(--chart-2); }

.ranklist { display: flex; flex-direction: column; gap: 3px; }
.rankrow { display: flex; align-items: center; gap: 9px; font-size: 13px; color: var(--ink-aux);
           text-decoration: none; cursor: pointer; padding: 6px 6px; border-radius: 5px; }
.rankrow:hover { background: var(--sub, rgba(0,0,0,.03)); color: var(--ink); }
.rank { width: 20px; height: 20px; border-radius: 50%; background: var(--brand-dark); color: #fff;
        font-style: normal; font-size: 11px; font-weight: 700; display: flex; align-items: center;
        justify-content: center; flex: none; }
.rname { flex: 1; }
.rankrow b { color: var(--ink); }
.pctlbl { color: var(--ink-soft); font-size: 11px; min-width: 42px; text-align: right; }

.subtotal { font-size: 12.5px; color: var(--ink-soft); margin: 8px 2px 4px; }
.subtotal .n { color: var(--brand-dark); cursor: pointer; text-decoration: none; font-weight: 600; }
.subtotal .n:hover { text-decoration: underline; }

.cathd { font-size: 12px; font-weight: 600; color: var(--ink-soft); margin: 14px 0 8px; }
.catgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.catgrid.full { grid-template-columns: 1fr 1fr; margin-top: 4px; }
.catchip2 { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-aux);
            text-decoration: none; cursor: pointer; padding: 6px 8px; border-radius: 5px;
            border: 1px solid var(--border); }
.catchip2:hover { border-color: var(--brand); color: var(--ink); }
.catchip2.zero { opacity: .45; cursor: default; }
.catchip2 .sw { width: 8px; height: 8px; border-radius: 2px; flex: none; }
.catchip2 .cname { flex: 1; }
.catchip2 b { color: var(--ink); }

.toggle { color: var(--brand-dark); cursor: pointer; text-decoration: none; font-size: 14px; }
.toggle:hover { text-decoration: underline; }

.rt { border-collapse: collapse; font-size: 12.5px; width: 100%; }
.rt th, .rt td { border: 1px solid var(--border); padding: 5px 11px; text-align: left; }
.rt th { background: var(--sub, rgba(15,23,42,.04)); color: var(--ink-soft); font-weight: 600; font-size: 11.5px; }
.rt .num, .rt th.num { text-align: right; }
.rt.small { font-size: 11.5px; }
.rt tr.top5 td { background: rgba(0,128,106,.04); }
.n { color: var(--brand-dark); cursor: pointer; text-decoration: none; }
.n:hover { text-decoration: underline; }
.catchip { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px;
           background: var(--good-soft); color: var(--brand-dark); }

.catcard { margin-top: 8px; }
.catrow { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }

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

@media (max-width: 760px) { .cols { flex-direction: column; } .catgrid { grid-template-columns: 1fr; } }
</style>
