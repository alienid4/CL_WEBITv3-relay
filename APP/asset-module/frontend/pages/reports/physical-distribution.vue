<script setup lang="ts">
// 部門報告圖表頁 A：各環境實體機分布現況。
//
// 2026-08-25 使用者提供現有簡報頁，要求「格式相同、數據是新的即可」——版面照他的
// 簡報，數字改成系統即時算的。口徑已對過帳（見 AI/計畫_部門報告圖表頁…md）：
//   全環境台數 ＝ CIA 正式登記 ＋ 排除退役 ＋ 排除網路/儲存/BMC/未知
// 這頁只算其中的**實體機**，依機房分組；業務用途分色要等對照表，
// 現在每個機房只有一段「未分類」——不是系統壞了，是那張表使用者說「以後再提供」。
definePageMeta({ ssr: false })

interface RoomCategory { name: string; count: number }
interface Room { room: string; total: number; categories: RoomCategory[] }
interface Branch { name: string; count: number }
interface ExcludedModel { device_model: string; count: number }
interface PhysicalDistribution {
  rooms: Room[]
  branches: Branch[]
  total_physical: number
  off_book: Record<string, number>
  category_note: string
  excluded_models: ExcludedModel[]
  excluded_models_total: number
}

const { apiFetch } = useApi()

const data = ref<PhysicalDistribution | null>(null)
const loading = ref(false)
const errorMessage = ref('')

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    data.value = await apiFetch<PhysicalDistribution>('/api/reports/physical-distribution')
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
await load()

// 分類色：業務用途對照表補上之前只有一種色（灰＝未分類）；補上後這裡自動照名稱
// 分色，不用改程式——同一份色階規則跟頁B共用，兩頁看起來才是一套。
// 顏色一律取自 main.css 的 --chart-N 變數，這裡不自己發明 hex（全站視覺規範 §1）。
const PALETTE = ['--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5', '--chart-6']
const catColorCache = new Map<string, string>()
function categoryColor(name: string): string {
  if (name === '未分類') return 'var(--chart-gray)'
  if (!catColorCache.has(name)) {
    catColorCache.set(name, `var(${PALETTE[catColorCache.size % PALETTE.length]})`)
  }
  return catColorCache.get(name)!
}

const OFF_BOOK_LABEL: Record<string, string> = {
  DYN: '存活清單掃到、未登記', VC: 'vCenter 收到、未登記', AUTO: '納管流程建立',
}
const offBookTotal = computed(() =>
  Object.values(data.value?.off_book ?? {}).reduce((s, n) => s + n, 0))

// ===== 下鑽：跟系統組月報同一套模式（mask + 底部滑出面板），
// 加總與清單走同一份後端計算，格子上的數字必然等於點進去的筆數。 =====
interface DrillRow {
  asset_serial: string; hostname: string | null; ip: string | null
  os_raw: string | null; os_canonical: string | null
  location: string | null; environment: string | null
  physical_location_raw: string | null; reason: string
}
const drillOpen = ref(false)
const drillTitle = ref('')
const drillRows = ref<DrillRow[]>([])
const drillLoading = ref(false)
const { sortKey: drillKey, sortDir: drillDir, toggle: drillToggle, sorted: drillSorted } =
  useSort(drillRows, '')

function onEsc(e: KeyboardEvent) { if (e.key === 'Escape' && drillOpen.value) drillOpen.value = false }
onMounted(() => window.addEventListener('keydown', onEsc))
onUnmounted(() => window.removeEventListener('keydown', onEsc))

const { showToast } = useToast()

async function drill(title: string, query: Record<string, string>) {
  drillOpen.value = true
  drillTitle.value = title
  drillLoading.value = true
  drillRows.value = []
  drillKey.value = ''
  try {
    drillRows.value = await apiFetch<DrillRow[]>(
      '/api/reports/physical-distribution/drill', { params: query })
  } catch (err: any) {
    showToast(`載入失敗：${err?.data?.detail ?? err?.message ?? '請稍後再試'}`, 'error')
    drillOpen.value = false
  } finally {
    drillLoading.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>各環境實體機分布現況</h1>
      <button class="btn" @click="load">重新整理</button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="loading" class="dim">載入中…</p>

    <template v-else-if="data">
      <div class="note">
        本頁只算<b>實體機</b>（虛擬機不計入）；口徑＝CIA 正式登記，排除退役、
        排除網路/儲存/BMC/未知（跟系統組月報表1同一套排除規則，兩頁數字對得起來）。
      </div>
      <div class="note warn">{{ data.category_note }}</div>

      <!-- 三個機房各一個圓環圖 -->
      <div class="rooms">
        <div v-for="r in data.rooms" :key="r.room" class="card room">
          <div class="room-hd">{{ r.room }}</div>
          <DonutChart
            :segments="r.categories.map((c) => ({ name: c.name, count: c.count, color: categoryColor(c.name) }))"
            center-label="台"
            @segment-click="drill(`${r.room}／${$event}`, { room: r.room })"
          />
          <div class="legend">
            <a v-for="c in r.categories" :key="c.name" class="lg"
               @click="drill(`${r.room}／${c.name}`, { room: r.room })">
              <i class="sw" :style="{ background: categoryColor(c.name) }" />{{ c.name }}
              <b class="mono">{{ c.count.toLocaleString() }}</b>
            </a>
            <p v-if="!r.categories.length" class="dim sm">這個機房目前沒有實體機</p>
          </div>
          <a class="all" @click="drill(`${r.room}（全部）`, { room: r.room })">
            看全部 {{ r.total.toLocaleString() }} 台 →
          </a>
        </div>
      </div>

      <!-- 分公司逐一列出：使用者的簡報上分公司是一間一間列的，不合併成一個數字 -->
      <h2>分公司</h2>
      <table v-if="data.branches.length" class="rt">
        <thead><tr><th>據點</th><th class="num">台數</th></tr></thead>
        <tbody>
          <tr v-for="b in data.branches" :key="b.name">
            <td>{{ b.name }}</td>
            <td class="num">
              <a class="n" @click="drill(b.name, { branch: b.name })">{{ b.count.toLocaleString() }}</a>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">目前沒有分公司的實體機登記</p>

      <div class="total">
        實體機總計　<b class="mono">{{ data.total_physical.toLocaleString() }}</b> 台
        （三機房 {{ data.rooms.reduce((s, r) => s + r.total, 0).toLocaleString() }}
        ＋分公司 {{ data.branches.reduce((s, b) => s + b.count, 0).toLocaleString() }}）
      </div>

      <!-- 被排除的型號：PC／NB／入侵偵測設備這類「OS 看起來像伺服器、實際不是機房
           伺服器」的機器（2026-08-26 使用者逐一確認）。
           **排除的不是刪掉**——列出來、而且每一項可以點開看是哪幾台，
           否則總數對不起來時沒有人講得出為什麼，使用者也無從拿去跟管理員核對。 -->
      <div v-if="data.excluded_models_total" class="card excl">
        <div class="card-title">
          另排除 {{ data.excluded_models_total.toLocaleString() }} 台「非機房伺服器」（未列入以上台數）
        </div>
        <div class="ob-rows">
          <a v-for="m in data.excluded_models" :key="m.device_model" class="exlink"
             @click="drill(`已排除：${m.device_model}`, { excluded_model: m.device_model })">
            {{ m.device_model }} <b class="mono">{{ m.count }}</b> 台
          </a>
        </div>
        <p class="tip">
          這些機器的 OS 欄位看起來像伺服器（例如 TippingPoint 寫 Linux），但實際是
          PC／筆電／入侵偵測設備，不計入機房實體機。清單由人維護在
          <code>report_groups.json</code>，改設定檔即可調整，不用改程式。
        </p>
        <!-- 2026-08-26 架構面的提醒。排除的是**視角**不是資料：資產庫仍完整保留，
             但「從主機報表排除」若實際效果是「從所有人的視野消失」，那不是分類，
             是把東西掃到地毯下。TippingPoint（入侵偵測防禦）與 Paysecure（金流
             加密機）一樣會 EOS、要打補丁、被稽核問——所以這句話要印在畫面上，
             不能只寫在設定檔註解裡（那是給開發看的，不是給用報表的人看的）。
             金融業的稽核缺失很多是這樣來的：不是沒人管，是每個人都以為別人在管。 -->
        <p class="tip warn">
          ⚠ <b>排除的是視角，不是資料。</b>這些機器仍在資產庫裡、仍然會 EOS、
          仍然要打補丁——特別是入侵偵測與金流加密設備。本表不列入只代表
          「它們不是機房伺服器」，<b>不代表沒人要管</b>；請確認它們在各自的
          權責清單上有歸屬。
        </p>
      </div>

      <!-- 帳外資產：DYN-/VC-/AUTO- 開頭的，實際存在但不在 CIA 清單上。刻意獨立一區、
           不併進上面任何數字——2026-08-25 實測踩過，混算會讓報告數字對不上任何一邊。 -->
      <div v-if="offBookTotal" class="card offbook">
        <div class="card-title">另有 {{ offBookTotal.toLocaleString() }} 台不在 CIA 清單上（未列入以上台數）</div>
        <div class="ob-rows">
          <span v-for="(n, src) in data.off_book" :key="src" v-show="n">
            {{ src }}- <b class="mono">{{ n.toLocaleString() }}</b> 台
            <span class="dim sm">（{{ OFF_BOOK_LABEL[src] }}）</span>
          </span>
        </div>
        <p class="tip">這批是實際存在、但不在資產清單上的機器——正是待複核佇列卡住的來源，值得處理但不能算進報告主數字。</p>
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
            <SortTh k="hostname" :active="drillKey" :dir="drillDir" @sort="drillToggle">主機名</SortTh>
            <SortTh k="ip" :active="drillKey" :dir="drillDir" @sort="drillToggle">IP</SortTh>
            <SortTh k="physical_location_raw" :active="drillKey" :dir="drillDir" @sort="drillToggle">機房原值</SortTh>
            <SortTh k="environment" :active="drillKey" :dir="drillDir" @sort="drillToggle">環境別</SortTh>
            <SortTh k="os_canonical" :active="drillKey" :dir="drillDir" @sort="drillToggle">OS</SortTh>
            <th>判定依據</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in drillSorted" :key="r.asset_serial">
              <td class="rh">{{ r.hostname || r.asset_serial }}</td>
              <td class="mono">{{ r.ip }}</td>
              <td>{{ r.physical_location_raw || '（空）' }}</td>
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
/* 版面、表格風格沿用系統組月報那頁（reports/system-group.vue）的既定樣式，
   兩頁看起來要是同一套報告工具，不要各自發明一套排版。 */
.page { padding: 18px 22px 60px; }
.hd { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
h1 { font-size: 19px; margin: 0; }
h2 { font-size: 15px; margin: 26px 0 8px; }
.btn { padding: 6px 12px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover { border-color: var(--brand); }
.dim { color: var(--ink-soft); }
.dim.sm { font-size: 11px; }
.mono { font-family: ui-monospace, monospace; }

.note { margin: 12px 0 4px; padding: 7px 11px; border-radius: 5px; font-size: 12px;
        background: rgba(0,128,106,.06); color: var(--ink-soft); border: 1px solid rgba(0,128,106,.18); }
.note.warn { background: var(--warn-soft); color: var(--warn-text); border-color: rgba(176,106,0,.3); }

.rooms { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        box-shadow: var(--shadow); padding: 16px; }
.room { flex: 1 1 220px; display: flex; flex-direction: column; align-items: center; text-align: center; }
.room-hd { font-size: 14px; font-weight: 600; color: var(--ink); margin-bottom: 10px; }
.legend { width: 100%; margin-top: 12px; display: flex; flex-direction: column; gap: 5px; }
.lg { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--ink-aux);
      text-decoration: none; cursor: pointer; padding: 3px 4px; border-radius: 4px; }
.lg:hover { background: var(--sub, rgba(0,0,0,.03)); color: var(--ink); }
.lg .sw { width: 9px; height: 9px; border-radius: 3px; flex: none; }
.lg b { margin-left: auto; color: var(--ink); }
.all { display: block; margin-top: 10px; font-size: 12px; color: var(--brand-dark);
       cursor: pointer; text-decoration: none; }
.all:hover { text-decoration: underline; }

.rt { border-collapse: collapse; font-size: 12.5px; width: 100%; max-width: 480px; }
.rt th, .rt td { border: 1px solid var(--border); padding: 5px 11px; text-align: left; }
.rt th { background: var(--sub, rgba(15,23,42,.04)); color: var(--ink-soft); font-weight: 600; font-size: 11.5px; }
.rt .num, .rt th.num { text-align: right; }
.rt.small { font-size: 11.5px; }
.n { color: var(--brand-dark); cursor: pointer; text-decoration: none; }
.n:hover { text-decoration: underline; }

.total { margin-top: 16px; font-size: 13px; color: var(--ink-soft); }
.total b { font-size: 16px; color: var(--ink); }

/* 被排除的型號區：跟帳外資產一樣獨立一區, 不併進主數字 */
.excl { margin-top: 20px; }
.exlink { cursor: pointer; color: var(--brand-dark); text-decoration: none; }
.exlink:hover { text-decoration: underline; }
.offbook { margin-top: 20px; }
.card-title { font-size: 13px; font-weight: 600; color: var(--ink); margin-bottom: 8px; }
.ob-rows { display: flex; gap: 22px; flex-wrap: wrap; font-size: 13px; color: var(--ink-aux); }
.tip { font-size: 11.5px; color: var(--ink-soft); margin: 8px 0 0; }
/* 「排除≠沒人管」那句要看得見, 不能跟一般說明混在一起被略過 */
.tip.warn { color: var(--warn-text, #9a5c00); background: var(--warn-soft);
  border-left: 3px solid var(--warn); padding: 7px 10px; border-radius: 4px;
  line-height: 1.65; }

/* 下鑽面板：同系統組月報，必須不透明底——半透明玻璃卡效果疊在頁面上底下文字會透出來 */
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

@media (max-width: 760px) { .rooms { flex-direction: column; } }
</style>
