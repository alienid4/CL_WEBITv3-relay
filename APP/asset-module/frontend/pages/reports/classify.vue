<script setup lang="ts">
// 主機分類作業頁（5c-4）。
//
// 為什麼要有這頁：報表頁B／頁C 的三桶（核心交易／非核心／測試）與分色全部靠
// hardware.system_category。2026-08-26 拿管理員那份《系統盤點(全環境)Data.xlsx》
// 對過報表範圍 2740 台之後：**452 台那份 Excel 根本沒涵蓋**
//（Windows Server 186／Linux 136／ESXi 71／IBM i 43／AIX 8／Windows Client 8）。
// 這 452 台只能人工判斷，所以批次修改不是加分項，是必要功能——一台一台點
// 452 次沒有人會做完，做不完報表就永遠有一塊是灰的。
//
// ⚠️ **這套分類跟 CIA 資訊資產清冊無關。** 使用者 2026-08-26 原話：「這個分類是
// 為了要算出這三張 PPT 的類別所產生的一個獨特的分類。」——所以畫面上的「環境別」
// 只是**給人判斷時的旁證**，不是判定依據，兩者不一致也不是錯。
//（本頁原本有一個「環境別與分類不一致」的旗標，就是把兩件事當成同一件事，已移除。）
//
// 設計上的兩個堅持：
// 1. **未分類排最前面**。這頁的用途是把它們清掉，不是瀏覽全部。
// 2. **匯入預設乾跑**。先看「對上幾筆、對不上哪些」，確認過才寫入。
definePageMeta({ ssr: false })

interface Row {
  asset_serial: string
  hostname: string | null
  ip: string | null
  api_id: string | null
  asset_name: string | null
  environment: string | null
  location: string | null
  platform: string | null
  category: string | null
  from_asset: boolean
}
interface Summary {
  total: number
  classified: number
  unclassified: number
  percent: number
  by_category: { name: string; count: number }[]
  valid_categories: string[]
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const summary = ref<Summary | null>(null)
const rows = ref<Row[]>([])
const loading = ref(false)
const errorMessage = ref('')
const filter = ref<'unclassified' | 'classified' | ''>('unclassified')
const keyword = ref('')
const selected = ref<Set<string>>(new Set())

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const q = filter.value ? `?only=${filter.value}` : ''
    const [s, r] = await Promise.all([
      apiFetch<Summary>('/api/classify/summary'),
      apiFetch<{ rows: Row[] }>(`/api/classify${q}`),
    ])
    summary.value = s
    rows.value = r.rows
    selected.value = new Set()
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
await load()
watch(filter, load)

// 從「7 資料匯入」的捷徑進來時（/reports/classify#seed）直接捲到匯入區塊並閃一下。
// 2026-08-26 使用者：「這類別要放在 7. 的下面」——匯入功能要從「資料匯入」那個
// 入口找得到。但這個區塊本身留在分類頁（那頁要能自己完整運作，不能拆走），
// 所以 7 底下放捷徑連過來，跟 5c-3 業務系統分類對照表同一個做法。
//
// 只捲過去還不夠：這頁上半部是一張長表格，捲到定位時人常常不確定「是這一塊嗎」，
// 所以短暫高亮一下。
const seedHilite = ref(false)
onMounted(() => {
  if (useRoute().hash !== '#seed') return
  nextTick(() => {
    document.getElementById('seed')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    seedHilite.value = true
    setTimeout(() => { seedHilite.value = false }, 2000)
  })
})

const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return rows.value
  return rows.value.filter((r) =>
    [r.hostname, r.ip, r.api_id, r.asset_name, r.asset_serial, r.location]
      .some((v) => (v ?? '').toLowerCase().includes(kw)))
})
const { sortKey, sortDir, toggle, sorted } = useSort(shown, '')

// ===== 批次選取 =====
const allShownSelected = computed(() =>
  sorted.value.length > 0 && sorted.value.every((r) => selected.value.has(r.asset_serial)))

function toggleOne(serial: string) {
  const s = new Set(selected.value)
  if (s.has(serial)) s.delete(serial)
  else s.add(serial)
  selected.value = s
}
function toggleAllShown() {
  const s = new Set(selected.value)
  if (allShownSelected.value) sorted.value.forEach((r) => s.delete(r.asset_serial))
  else sorted.value.forEach((r) => s.add(r.asset_serial))
  selected.value = s
}

const batchCategory = ref('')
const saving = ref(false)

async function applyBatch(clear = false) {
  if (selected.value.size === 0) { showToast('請先勾選要修改的主機', 'error'); return }
  if (!clear && !batchCategory.value) { showToast('請先選一個分類', 'error'); return }
  saving.value = true
  try {
    const res = await apiFetch<{ updated: number }>('/api/classify', {
      method: 'PUT',
      body: { asset_serials: [...selected.value], category: clear ? null : batchCategory.value },
    })
    showToast(clear
      ? `已清除 ${res.updated} 台的分類`
      : `已把 ${res.updated} 台設為「${batchCategory.value}」`)
    await load()
  } catch (err: any) {
    showToast(err?.data?.detail ?? '修改失敗', 'error')
  } finally {
    saving.value = false
  }
}

// ===== 從外部盤點表帶入（預設乾跑）=====
interface SeedResult {
  dry_run: boolean
  source_rows: number
  duplicate_source_rows: number
  matched: number
  matched_in_scope: number
  matched_off_scope: number
  multi_match: number
  multi_match_samples: { hostname: string | null; asset_serials: string[] }[]
  unmatched: number
  no_hostname: number
  invalid_category: number
  unmatched_samples: { hostname: string | null; category: string }[]
  invalid_category_samples: { hostname: string | null; category: string }[]
  valid_categories: string[]
  new_categories: { name: string; group: string }[]
  renamed_categories: { old: string; new: string }[]
}
const seedFile = ref<File | null>(null)
const seedResult = ref<SeedResult | null>(null)
const seeding = ref(false)

function onSeedPick(e: Event) {
  seedFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
  seedResult.value = null
}
async function runSeed(commit: boolean) {
  if (!seedFile.value) { showToast('請先選擇 .xlsx 檔案', 'error'); return }
  seeding.value = true
  try {
    const fd = new FormData()
    fd.append('file', seedFile.value)
    fd.append('commit', commit ? 'true' : 'false')
    seedResult.value = await apiFetch<SeedResult>('/api/classify/seed', { method: 'POST', body: fd })
    if (commit) {
      showToast(`已寫入 ${seedResult.value.matched} 台，其中 ${seedResult.value.matched_in_scope} 台會出現在報表上`)
      await load()
    } else {
      showToast(`試算完成：${seedResult.value.matched_in_scope} 台會進報表，未寫入`)
    }
  } catch (err: any) {
    showToast(err?.data?.detail ?? '匯入失敗', 'error')
  } finally {
    seeding.value = false
  }
}
</script>

<template>
  <div class="page">
    <header class="hd">
      <div>
        <h1>主機分類作業</h1>
        <p class="sub">
          決定每一台主機在部門報告裡屬於哪一個分類——報表頁B／頁C 的
          「核心交易／非核心／測試」三桶與分色，全部看這一欄。
          <b>這是報告專用的分類，跟 CIA 資訊資產清冊無關。</b>
        </p>
      </div>
      <button class="btn" :disabled="loading" @click="load">↻ 重新整理</button>
    </header>

    <p v-if="errorMessage" class="err">{{ errorMessage }}</p>

    <!-- 進度 -->
    <section v-if="summary" class="cards">
      <div class="card">
        <div class="k">已分類</div>
        <div class="v">{{ summary.classified }} <small>/ {{ summary.total }}</small></div>
        <div class="bar"><i :style="{ width: summary.percent + '%' }" /></div>
        <div class="pct">{{ summary.percent }}%</div>
      </div>
      <div class="card" :class="{ hot: summary.unclassified > 0 }">
        <div class="k">未分類</div>
        <div class="v">{{ summary.unclassified }}</div>
        <div class="note">這些台在報表上是灰的，沒有歸屬</div>
      </div>
      <div class="card">
        <div class="k">分類種類</div>
        <div class="v">{{ summary.by_category.length }} <small>/ {{ summary.valid_categories.length }}</small></div>
        <div class="note">已經被用到的分類數 ／ 可用的分類總數</div>
      </div>
    </section>

    <!-- 篩選 -->
    <section class="toolbar">
      <div class="tabs">
        <button :class="{ on: filter === 'unclassified' }" @click="filter = 'unclassified'">未分類</button>
        <button :class="{ on: filter === 'classified' }" @click="filter = 'classified'">已分類</button>
        <button :class="{ on: filter === '' }" @click="filter = ''">全部</button>
      </div>
      <input v-model="keyword" class="search" placeholder="搜尋主機名／IP／APID／資產名稱…">
    </section>

    <!-- 批次列 -->
    <section class="batch" :class="{ active: selected.size > 0 }">
      <span class="cnt">已勾選 <b>{{ selected.size }}</b> 台</span>
      <select v-model="batchCategory" class="sel">
        <option value="">選擇分類…</option>
        <option v-for="c in summary?.valid_categories ?? []" :key="c" :value="c">{{ c }}</option>
      </select>
      <button class="btn primary" :disabled="saving || selected.size === 0" @click="applyBatch(false)">
        套用到勾選的 {{ selected.size }} 台
      </button>
      <button class="btn ghost" :disabled="saving || selected.size === 0" @click="applyBatch(true)">
        清除分類
      </button>
    </section>

    <!-- 清單 -->
    <div class="tbl-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th class="ck"><input type="checkbox" :checked="allShownSelected" @change="toggleAllShown"></th>
            <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="api_id" :active="sortKey" :dir="sortDir" @sort="toggle">APID</SortTh>
            <SortTh k="asset_name" :active="sortKey" :dir="sortDir" @sort="toggle">資產名稱</SortTh>
            <SortTh k="platform" :active="sortKey" :dir="sortDir" @sort="toggle">平台</SortTh>
            <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="toggle">
              環境別<span class="hint" title="來自 CIA 資訊資產清冊，只是判斷時的旁證，跟分類不必一致">（參考）</span>
            </SortTh>
            <SortTh k="location" :active="sortKey" :dir="sortDir" @sort="toggle">機房</SortTh>
            <SortTh k="category" :active="sortKey" :dir="sortDir" @sort="toggle">分類</SortTh>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="9" class="empty">載入中…</td></tr>
          <tr v-else-if="sorted.length === 0"><td colspan="9" class="empty">沒有符合的資料</td></tr>
          <tr
            v-for="r in sorted"
            :key="r.asset_serial"
            :class="{ sel: selected.has(r.asset_serial) }"
          >
            <td class="ck">
              <input
                type="checkbox" :checked="selected.has(r.asset_serial)"
                @change="toggleOne(r.asset_serial)"
              >
            </td>
            <td class="mono">{{ r.hostname || '—' }}</td>
            <td class="mono">{{ r.ip || '—' }}</td>
            <td class="mono">{{ r.api_id || '—' }}</td>
            <td>{{ r.asset_name || '—' }}</td>
            <td>{{ r.platform || '—' }}</td>
            <td>{{ r.environment || '—' }}</td>
            <td>{{ r.location || '—' }}</td>
            <td>
              <span
                v-if="r.category" class="tag" :class="{ inferred: !r.from_asset }"
                :title="r.from_asset ? '逐台設定' : '由 APID 對照表推得，尚未逐台確認'"
              >
                {{ r.category }}<template v-if="!r.from_asset"> ＊</template>
              </span>
              <span v-else class="tag none">未分類</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="legend">＊ ＝ 由 APID 對照表推得的預設值，尚未逐台確認；勾選後套用即可定案。</p>

    <!-- 從外部盤點表帶入 -->
    <section id="seed" class="seed" :class="{ hilite: seedHilite }">
      <h2>從外部盤點表帶入分類</h2>
      <p class="tip">
        用管理員維護的盤點表（例如《系統盤點(全環境)Data.xlsx》）一次帶入。
        只讀 <b>「主機名稱」</b>與<b>「分類」</b>兩欄當對照，
        來源表的環境別／機房／APID <b>一律忽略</b>——那些以《資訊資產清冊》為準。
      </p>
      <p class="tip warn">
        <b>預設只試算不寫入。</b>先按「試算」看對上幾筆、對不上哪些，確認過再按寫入。
      </p>
      <div class="seedrow">
        <input type="file" accept=".xlsx" @change="onSeedPick">
        <button class="btn" :disabled="seeding || !seedFile" @click="runSeed(false)">試算（不寫入）</button>
        <button
          class="btn primary" :disabled="seeding || !seedResult || !seedResult.dry_run"
          @click="runSeed(true)"
        >
          確認寫入
        </button>
      </div>

      <div v-if="seedResult" class="seedres">
        <p>
          來源 <b>{{ seedResult.source_rows }}</b> 列
          <span v-if="seedResult.duplicate_source_rows" class="sub-note">
            （其中 {{ seedResult.duplicate_source_rows }} 列主機名重複）
          </span>
          <span v-if="seedResult.dry_run" class="dry">・試算，尚未寫入</span>
        </p>
        <ul class="seedstat">
          <li>
            <b>{{ seedResult.matched_in_scope }}</b> 台會出現在報表上
            <span class="sub-note">← 寫入後「已分類」就會是這個數字</span>
          </li>
          <li v-if="seedResult.matched_off_scope">
            另有 {{ seedResult.matched_off_scope }} 台是帳外／退役／不列入報表的平台，
            分類照樣寫進去（日後納管合併時就已經有了），但不影響報表數字
          </li>
          <li v-if="seedResult.unmatched">
            <b class="bad">{{ seedResult.unmatched }}</b> 列的主機名在資產庫裡找不到
          </li>
          <li v-if="seedResult.invalid_category">
            <b class="bad">{{ seedResult.invalid_category }}</b> 列的分類名稱不在允許清單裡
          </li>
          <li v-if="seedResult.no_hostname">
            {{ seedResult.no_hostname }} 列沒有填主機名
          </li>
          <li v-if="seedResult.multi_match">
            {{ seedResult.multi_match }} 個主機名對到 2 台以上資產——大多是同一台機器
            同時有 CIA 登記與 vCenter 掃到的兩列，屬正常；但也可能是資產庫重複登記，
            那要到「待複核佇列」處理，不是分類的問題
          </li>
          <li v-if="seedResult.new_categories.length" class="new-cat">
            會<b>新增</b> {{ seedResult.new_categories.length }} 個分類：
            <span v-for="(c, i) in seedResult.new_categories" :key="c.name">
              <b class="mono">{{ c.name }}</b>（{{ c.group }}）<template v-if="i < seedResult.new_categories.length - 1">、</template>
            </span>
          </li>
          <li v-if="seedResult.renamed_categories.length" class="rename-cat">
            會<b>改名</b> {{ seedResult.renamed_categories.length }} 個 0 台的既有分類
            （字母相同、沒有機器在用，視為打字/去識別化造成的字面不同，不是新分類）：
            <span v-for="(c, i) in seedResult.renamed_categories" :key="c.old">
              <span class="mono">{{ c.old }}</span> → <b class="mono">{{ c.new }}</b><template v-if="i < seedResult.renamed_categories.length - 1">、</template>
            </span>
          </li>
        </ul>
        <details v-if="seedResult.unmatched_samples.length">
          <summary>對不上的主機名（前 {{ seedResult.unmatched_samples.length }} 筆）</summary>
          <ul>
            <li v-for="(u, i) in seedResult.unmatched_samples" :key="i">
              {{ u.hostname }} — {{ u.category }}
            </li>
          </ul>
          <p class="tip">對不上代表資產庫裡沒有這個主機名——可能是還沒登記，也可能是名字不同。</p>
        </details>
        <details v-if="seedResult.invalid_category_samples.length">
          <summary>分類名稱不合法（{{ seedResult.invalid_category_samples.length }} 筆）</summary>
          <ul>
            <li v-for="(u, i) in seedResult.invalid_category_samples" :key="i">
              {{ u.hostname }} — 「{{ u.category }}」
            </li>
          </ul>
          <p class="tip">允許的分類：{{ seedResult.valid_categories.join('、') }}</p>
        </details>
      </div>
    </section>
  </div>
</template>

<style scoped>
.seedstat { margin: 6px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.8; }
.sub-note { color: var(--muted); font-size: 11px; }
.bad { color: var(--bad); }
.new-cat { color: var(--brand-dark); }
.rename-cat { color: var(--warn); }
.page { padding: 18px 22px 60px; }
.hd { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 13px; margin: 0; }
.err { color: var(--bad); }

.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 16px 0; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.card.hot { border-color: var(--warn); }
.card.warnbox { border-color: var(--bad); }
.card .k { font-size: 12px; color: var(--muted); }
.card .v { font-size: 26px; font-weight: 700; }
.card .v small { font-size: 14px; color: var(--muted); font-weight: 400; }
.card .note { font-size: 11px; color: var(--muted); margin-top: 4px; line-height: 1.5; }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; margin-top: 6px; }
.bar i { display: block; height: 100%; background: var(--good); }
.pct { font-size: 11px; color: var(--muted); margin-top: 3px; }

.toolbar { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.tabs { display: flex; gap: 6px; }
.tabs button { border: 1px solid var(--line); background: var(--card); border-radius: 6px;
  padding: 5px 12px; cursor: pointer; font-size: 13px; }
.tabs button.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.search { flex: 1; min-width: 220px; max-width: 380px; padding: 6px 10px;
  border: 1px solid var(--line); border-radius: 6px; }

.batch { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 12px; border: 1px dashed var(--line); border-radius: 8px; margin-bottom: 12px;
  opacity: 0.55; }
.batch.active { opacity: 1; border-style: solid; border-color: var(--brand); }
.batch .cnt { font-size: 13px; }
.sel { padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; min-width: 180px; }

.btn { border: 1px solid var(--line); background: var(--card); border-radius: 6px;
  padding: 5px 12px; cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn.ghost { color: var(--bad); }

.tbl-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { padding: 6px 10px; border-bottom: 1px solid var(--line); text-align: left;
  vertical-align: top; }
.tbl tbody tr.sel { background: rgba(0, 0, 0, 0.04); }
.tbl .ck { width: 30px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.empty { text-align: center; color: var(--muted); padding: 24px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px;
  background: var(--good-soft); }
.tag.inferred { background: var(--warn-soft); }
.tag.none { background: var(--line); color: var(--muted); }
.hint { font-weight: 400; color: var(--muted); font-size: 10px; }
.legend { font-size: 11px; color: var(--muted); margin: 6px 2px 0; }

.seed.hilite { box-shadow: 0 0 0 3px var(--brand); transition: box-shadow 0.4s; }
.seed { margin-top: 28px; border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.seed h2 { font-size: 15px; margin: 0 0 8px; }
.tip { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 0 0 6px; }
.tip.warn { color: var(--warn); }
.seedrow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
.seedres { margin-top: 12px; font-size: 13px; }
.seedres .dry { color: var(--warn); }
.seedres ul { max-height: 220px; overflow-y: auto; font-size: 12px; }
</style>
