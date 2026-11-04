<script setup lang="ts">
// 9.1「主機分類」頁籤（V46，2.048.16 起 V2 原生；取代嵌入的舊頁 pages/reports/classify.vue）。⚠D3（手動設定報表分類）。同一批 API，行為不變：
// GET /api/classify/summary、GET /api/classify?only=（清單與進度同一份 system_report.classify_rows，卡片數字＝點下去的列數）、
// PUT /api/classify（批次設定／清除，一般帳號以上，後端 USER_WRITE）、POST /api/classify/seed（外部盤點表帶入，預設試算、確認才寫；限管理員，後端 ADMIN_WRITE）。
// 這套分類只給報表用（核心交易／非核心／測試三桶與分色），跟 CIA 資訊資產清冊無關；環境別只是判斷時的旁證。
// 篩選在網址：?tab=classify&cf=unclassified|classified|all|cats（cats＝各分類台數）、cc=<分類>（cf=all 時只看這一類）、ck=<關鍵字>。
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const { isAdmin, canWrite } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')

const CF = ['unclassified', 'classified', 'all', 'cats'] as const
const cf = computed(() => (CF.includes(q('cf') as any) ? q('cf') : 'unclassified'))
const only = computed(() => (cf.value === 'unclassified' || cf.value === 'classified' ? cf.value : ''))
const to = (extra: Record<string, string | undefined>) => ({ query: Object.fromEntries(Object.entries({ ...route.query, tab: 'classify', cc: undefined, ...extra }).filter(([, v]) => v !== undefined && v !== '')) })

const summary = ref<any>(null)
const rows = ref<any[]>([])
const err = ref('')
const loading = ref(false)
const picked = ref<Record<string, boolean>>({})
const begin = useLatest()
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const qs = only.value ? `?only=${only.value}` : ''
    const [s, r] = await Promise.all([apiFetch<any>('/api/classify/summary'), cf.value === 'cats' ? Promise.resolve({ rows: [] }) : apiFetch<any>(`/api/classify${qs}`)])
    if (!isLatest()) return
    summary.value = s; rows.value = r.rows; picked.value = {}; err.value = ''
  } catch (e: any) { if (isLatest()) err.value = `資料載入失敗：${errMsg(e)}` } finally { if (isLatest()) loading.value = false }
}
onMounted(load)
watch(cf, load)
// 換搜尋／只看某一類就清掉勾選：看不到的列不該被批次套用（審查 P2）
watch(() => [q('cc'), q('ck')], () => { picked.value = {} })

const ck = ref(q('ck'))
watch(ck, v => { if (q('ck') !== v) router.replace({ query: { ...route.query, ck: v || undefined } }) })
watch(() => route.query.ck, v => { ck.value = typeof v === 'string' ? v : '' })
// 項次照後端順序固定；搜尋／只看某一類只挑出幾列，號碼不重編
const numbered = computed(() => rows.value.map((r: any, i: number) => ({ ...r, __no: i + 1, category_t: r.category ? `${r.category}${r.from_asset ? '' : ' ＊'}` : '未分類' })))
const shown = computed(() => {
  const kw = ck.value.trim().toLowerCase()
  const cc = q('cc')
  return numbered.value.filter((r: any) => (!cc || r.category === cc)
    && (!kw || [r.hostname, r.ip, r.api_id, r.asset_name, r.asset_serial, r.location].some(v => String(v ?? '').toLowerCase().includes(kw))))
})
const cols = [{ k: 'hostname', label: '主機名稱', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'ip', label: 'IP', mono: true },
  { k: 'api_id', label: 'APID', mono: true }, { k: 'asset_name', label: '資產名稱' }, { k: 'platform', label: '平台' },
  { k: 'environment', label: '環境別（參考）', note: '來自 CIA 資訊資產清冊，只是判斷時的旁證，跟分類不必一致' }, { k: 'location', label: '機房' },
  { k: 'category_t', label: '分類', note: '標 ＊＝由 APID 對照表推得的預設值，尚未逐台確認；勾選後套用即可定案。' }]
const catRows = computed(() => (summary.value?.by_category ?? []).map((c: any, i: number) => ({ ...c, __no: i + 1 })))

// ===== 批次（一般帳號以上）=====
const pickedSerials = computed(() => Object.keys(picked.value).filter(k => picked.value[k]))
// 舊頁的「全選」作用在全部符合的列（不只畫出來的這一頁），批次分類幾千台照樣做得到
function pickAllShown(v: boolean) {
  const n = { ...picked.value }
  for (const r of shown.value) { if (v) n[r.asset_serial] = true; else delete n[r.asset_serial] }
  picked.value = n
}
const batchCategory = ref('')
const saving = ref(false)
async function applyBatch(clear = false) {
  if (!pickedSerials.value.length) { showToast('請先勾選要修改的主機', 'error'); return }
  if (!clear && !batchCategory.value) { showToast('請先選一個分類', 'error'); return }
  saving.value = true
  try {
    const res = await apiFetch<any>('/api/classify', { method: 'PUT', body: { asset_serials: pickedSerials.value, category: clear ? null : batchCategory.value } })
    showToast(clear ? `已清除 ${res.updated} 台的分類` : `已把 ${res.updated} 台設為「${batchCategory.value}」`)
    await load(); emit('changed')
  } catch (e: any) { showToast(`修改失敗：${errMsg(e)}`, 'error') } finally { saving.value = false }
}

// ===== 從外部盤點表帶入（預設試算；限管理員）=====
const seedFile = ref<File | null>(null)
const seedResult = ref<any>(null)
const seeding = ref(false)
function onSeedPick(e: Event) { seedFile.value = (e.target as HTMLInputElement).files?.[0] ?? null; seedResult.value = null }
async function runSeed(commit: boolean) {
  if (!seedFile.value) { showToast('請先選擇 .xlsx 檔案', 'error'); return }
  seeding.value = true
  try {
    const fd = new FormData()
    fd.append('file', seedFile.value)
    fd.append('commit', commit ? 'true' : 'false')
    seedResult.value = await apiFetch<any>('/api/classify/seed', { method: 'POST', body: fd })
    if (commit) { showToast(`已寫入 ${seedResult.value.matched} 台，其中 ${seedResult.value.matched_in_scope} 台會出現在報表上`); await load(); emit('changed') }
    else showToast(`試算完成：${seedResult.value.matched_in_scope} 台會進報表，未寫入`)
  } catch (e: any) { showToast(`匯入失敗：${errMsg(e)}`, 'error') } finally { seeding.value = false }
}
</script>

<template>
  <div>
    <div class="bar">
      <span class="muted">決定每一台主機在部門報告裡屬於哪一個分類</span>
      <InfoNote>報表的「核心交易／非核心／測試」三桶與分色，全部看這一欄。這是報告專用的分類，跟 CIA 資訊資產清冊無關。</InfoNote>
      <button type="button" class="btn2" :disabled="loading" @click="load">重新整理</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <div v-if="summary" class="cards">
      <NuxtLink class="card" :class="{ on: cf === 'classified' }" :to="to({ cf: 'classified' })">
        <span class="k">已分類</span><span class="v">{{ summary.classified.toLocaleString() }} <small>/ {{ summary.total.toLocaleString() }}</small></span>
        <span class="pbar"><i :style="{ width: summary.percent + '%' }" /></span><span class="k">{{ summary.percent }}%</span>
      </NuxtLink>
      <NuxtLink class="card" :class="{ on: cf === 'unclassified', hot: summary.unclassified > 0 }" :to="to({ cf: 'unclassified' })">
        <span class="k">未分類 <InfoNote>這些台在報表上是灰的，沒有歸屬。</InfoNote></span><span class="v">{{ summary.unclassified.toLocaleString() }}</span>
      </NuxtLink>
      <NuxtLink class="card" :class="{ on: cf === 'cats' }" :to="to({ cf: 'cats' })">
        <span class="k">分類種類 <InfoNote>已經被用到的分類數 ／ 可用的分類總數。點下去看各分類幾台。</InfoNote></span>
        <span class="v">{{ summary.by_category.length }} <small>/ {{ summary.valid_categories.length }}</small></span>
      </NuxtLink>
      <NuxtLink class="card" :class="{ on: cf === 'all' }" :to="to({ cf: 'all' })"><span class="k">全部</span><span class="v">{{ summary.total.toLocaleString() }}</span></NuxtLink>
    </div>

    <template v-if="cf === 'cats'">
      <V2Table :rows="catRows" prefix="K" row-key="name" unit="類" export-name="主機分類_各分類台數" empty="還沒有任何分類"
               :cols="[{ k: 'name', label: '分類' }, { k: 'count', label: '台數', mono: true, link: (r: any) => to({ cf: 'all', cc: r.name }) }]" />
    </template>
    <template v-else>
      <div class="bar">
        <input v-model="ck" class="search" placeholder="搜尋主機名／IP／APID／資產名稱…" aria-label="搜尋">
        <span v-if="q('cc')" class="chip on">只看「{{ q('cc') }}」<NuxtLink :to="to({ cf: 'all' })" aria-label="取消">✕</NuxtLink></span>
      </div>
      <div v-if="canWrite" class="batch" :class="{ active: pickedSerials.length > 0 }">
        <span>已勾選 <b>{{ pickedSerials.length.toLocaleString() }}</b> 台</span>
        <button type="button" class="lb" :disabled="!shown.length" @click="pickAllShown(true)">全選符合的 {{ shown.length.toLocaleString() }} 台</button>
        <button type="button" class="lb" :disabled="!pickedSerials.length" @click="picked = {}">取消勾選</button>
        <select v-model="batchCategory" class="sel" aria-label="分類">
          <option value="">選擇分類…</option>
          <option v-for="c in summary?.valid_categories ?? []" :key="c" :value="c">{{ c }}</option>
        </select>
        <button type="button" class="btn" :disabled="saving || !pickedSerials.length" @click="applyBatch(false)">套用到勾選的 {{ pickedSerials.length.toLocaleString() }} 台</button>
        <button type="button" class="btn2 danger" :disabled="saving || !pickedSerials.length" @click="applyBatch(true)">清除分類</button>
        <InfoNote>「全選符合的」作用在目前條件下全部的列（不只這一頁）；大量分類也可以用下方「從外部盤點表帶入分類」一次帶入。</InfoNote>
      </div>
      <V2Table v-model:selected="picked" :rows="shown" prefix="A" row-key="asset_serial" unit="台" :max="300" :selectable="canWrite"
               :cols="cols" export-name="主機分類" :empty="loading ? '載入中…' : '沒有符合的資料'" />
    </template>

    <section id="seed" class="seed">
      <h3 class="h3">從外部盤點表帶入分類
        <InfoNote>用管理員維護的盤點表一次帶入。只讀「主機名稱」與「分類」兩欄當對照，來源表的環境別／機房／APID 一律忽略——那些以《資訊資產清冊》為準。預設只試算不寫入：先按「試算」看對上幾筆、對不上哪些，確認過再按寫入。</InfoNote></h3>
      <div v-if="isAdmin" class="bar">
        <input type="file" accept=".xlsx" aria-label="盤點表" @change="onSeedPick">
        <button type="button" class="btn2" :disabled="seeding || !seedFile" @click="runSeed(false)">試算（不寫入）</button>
        <button type="button" class="btn" :disabled="seeding || !seedResult || !seedResult.dry_run" @click="runSeed(true)">確認寫入</button>
      </div>
      <p v-else class="muted" data-test="admin-only">帶入分類限管理員</p>
      <div v-if="seedResult" class="seedres">
        <p>來源 <b>{{ seedResult.source_rows }}</b> 列<span v-if="seedResult.duplicate_source_rows" class="muted">（其中 {{ seedResult.duplicate_source_rows }} 列主機名重複）</span>
          <span v-if="seedResult.dry_run" class="warnt">・試算，尚未寫入</span></p>
        <ul>
          <li><b>{{ seedResult.matched_in_scope }}</b> 台會出現在報表上<InfoNote>寫入後「已分類」就會是這個數字。</InfoNote></li>
          <li v-if="seedResult.matched_off_scope">另有 {{ seedResult.matched_off_scope }} 台是帳外／退役／不列入報表的平台<InfoNote>分類照樣寫進去（日後納管合併時就已經有了），但不影響報表數字。</InfoNote></li>
          <li v-if="seedResult.unmatched"><b class="bad">{{ seedResult.unmatched }}</b> 列的主機名在資產庫裡找不到</li>
          <li v-if="seedResult.invalid_category"><b class="bad">{{ seedResult.invalid_category }}</b> 列的分類名稱不在允許清單裡</li>
          <li v-if="seedResult.no_hostname">{{ seedResult.no_hostname }} 列沒有填主機名</li>
          <li v-if="seedResult.multi_match">{{ seedResult.multi_match }} 個主機名對到 2 台以上資產
            <InfoNote>大多是同一台機器同時有 CIA 登記與 vCenter 掃到的兩列，屬正常；也可能是資產庫重複登記，那要到「重複登記合併」處理，不是分類的問題。</InfoNote></li>
          <li v-if="seedResult.new_categories?.length">會新增 {{ seedResult.new_categories.length }} 個分類：{{ seedResult.new_categories.map((c: any) => `${c.name}（${c.group}）`).join('、') }}</li>
          <li v-if="seedResult.renamed_categories?.length">會改名 {{ seedResult.renamed_categories.length }} 個 0 台的既有分類：{{ seedResult.renamed_categories.map((c: any) => `${c.old} → ${c.new}`).join('、') }}
            <InfoNote>字母相同、沒有機器在用，視為打字／去識別化造成的字面不同，不是新分類。</InfoNote></li>
        </ul>
        <details v-if="seedResult.unmatched_samples?.length"><summary>對不上的主機名（前 {{ seedResult.unmatched_samples.length }} 筆）</summary>
          <ul><li v-for="(x, i) in seedResult.unmatched_samples" :key="i">{{ x.hostname }} — {{ x.category }}</li></ul></details>
        <details v-if="seedResult.invalid_category_samples?.length"><summary>分類名稱不合法（{{ seedResult.invalid_category_samples.length }} 筆）</summary>
          <ul><li v-for="(x, i) in seedResult.invalid_category_samples" :key="i">{{ x.hostname }} — 「{{ x.category }}」</li></ul>
          <p class="muted">允許的分類：{{ seedResult.valid_categories.join('、') }}</p></details>
      </div>
    </section>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 18px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 6px 0 10px; }
.card { display: flex; flex-direction: column; gap: 3px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); color: var(--ink); text-decoration: none; }
.card:hover, .card.on { border-color: var(--brand); }
.card.hot .v { color: var(--warn-text); }
.card .k { font-size: 12px; color: var(--muted); display: flex; gap: 4px; align-items: center; }
.card .v { font-family: var(--disp); font-size: 24px; font-weight: 700; }
.card .v small { font-size: 13px; color: var(--muted); font-weight: 400; }
.pbar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; }
.pbar i { display: block; height: 100%; background: var(--good); }
.search { font-family: inherit; font-size: 13px; flex: 1; min-width: 220px; max-width: 380px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--brand); color: var(--brand-dark); background: var(--mint); display: inline-flex; gap: 6px; }
.chip a { color: inherit; text-decoration: none; }
.batch { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 12px; border: 1px dashed var(--border-strong); border-radius: 10px; margin-bottom: 10px; font-size: 13px; }
.batch.active { border-style: solid; border-color: var(--brand); }
.sel { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; min-width: 160px; }
.btn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2.danger { color: var(--bad); }
.btn:disabled, .btn2:disabled, .lb:disabled { opacity: .45; cursor: default; }
.lb { font-family: inherit; font-size: 12px; padding: 2px 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.seed { margin-top: 18px; border-top: 1px solid var(--line); padding-top: 4px; }
.seedres { font-size: 13px; }
.seedres ul { font-size: 12px; line-height: 1.8; max-height: 220px; overflow-y: auto; }
.bad { color: var(--bad); }
.warnt { color: var(--warn-text); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
