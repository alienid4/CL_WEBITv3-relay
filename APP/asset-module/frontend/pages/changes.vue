<script setup lang="ts">
// 每日變化報告（v1.500.0，2026-10-23）。
// 每晚一份「每台機器的關鍵事實」快照，今天對前一份做差異。分頁數字＝點進去的筆數（同一份計算，
// verify_numbers 釘住）。早上摘要信的設定也在這頁（限管理員改）。
interface Item {
  key: string; hostname: string | null; ips: string | null; serial: string | null
  before: any; after: any; detail?: string | null; segment?: string | null; new?: boolean
}
interface Seg { segment: string; collected: number; gap: number; whole: boolean; items: string[] }
interface Cat { key: string; label: string }
interface ChangesOut {
  date: string | null; prev_date: string | null; dates: string[]; categories: Cat[]
  items: Record<string, Item[]>; counts: Record<string, number>; segments: Seg[]
  reason: string | null; cutoff?: string; machines?: number
  snapshot?: { created_at: string; trigger: string; machines: number; storage_threshold: number | null } | null
  settings: { gap_days: number; count_change_pct: number; count_change_min: number; uptime_days: number; retain_days: number }
}

const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const { showToast } = useToast()

const data = ref<ChangesOut | null>(null)
const loadError = ref('')
const loading = ref(true)
const tab = computed(() => String(route.query.tab || 'overview'))
const day = computed(() => (route.query.date ? String(route.query.date) : undefined))

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await apiFetch<ChangesOut>('/api/changes', { params: day.value ? { date: day.value } : {} })
  } catch (e: any) {
    loadError.value = e?.data?.detail ?? e?.message ?? '載入失敗'
    showToast(`變化報告載入失敗：${loadError.value}`, 'error', 12000)
  } finally {
    loading.value = false
  }
}
await load()
watch(day, load)

function go(t: string, extra: Record<string, string | undefined> = {}) {
  router.push({ query: { ...route.query, tab: t, seg: undefined, ...extra } })
}
function pickDate(e: Event) {
  router.push({ query: { ...route.query, date: (e.target as HTMLSelectElement).value } })
}

const snapping = ref(false)
async function snapNow() {
  snapping.value = true
  try {
    const r = await apiFetch<{ snap_date: string; machines: number }>('/api/changes/snapshot', { method: 'POST' })
    showToast(`已寫入 ${r.snap_date} 快照：${r.machines.toLocaleString()} 台`, 'success')
    router.push({ query: { ...route.query, date: undefined } })
    await load()
  } catch (e: any) {
    showToast(`寫快照失敗：${e?.data?.detail ?? e?.message ?? ''}`, 'error', 12000)
  } finally {
    snapping.value = false
  }
}

// ── 分類分頁 ──
const cats = computed(() => (data.value?.categories ?? []))
const seg = computed(() => (route.query.seg ? String(route.query.seg) : ''))
const rows = computed(() => {
  const list = data.value?.items?.[tab.value] ?? []
  return list.map((x, i) => ({ ...x, no: i + 1, before: x.before ?? '', after: x.after ?? '' }))
    .filter(x => tab.value !== 'collect_gap' || !seg.value || (x.detail === seg.value))
})
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'no')
const segRows = computed(() => (data.value?.segments ?? []).map((s, i) => ({ ...s, no: `S${i + 1}`, n: i + 1, whole_n: s.whole ? 1 : 0 })))
const segSort = useSort(segRows, 'n')
const curLabel = computed(() => cats.value.find(c => c.key === tab.value)?.label ?? '')

</script>

<template>
  <div class="page">
    <V2Banner to="/v2/changes" label="1.2 異動與異常" />
    <div class="hd">
      <h1>每日變化報告</h1>
      <select v-if="data?.dates?.length" class="sel" :value="data?.date ?? ''" @change="pickDate">
        <option v-for="d in data?.dates" :key="d" :value="d">{{ d }}</option>
      </select>
      <span v-if="data?.prev_date" class="dim">對比 {{ data.prev_date }}</span>
      <InfoNote>
        每晚 {{ form?.snapshot_time ?? '05:30' }} 存一份每台機器的關鍵事實（同一台＝台數口徑 machine_key），今天對前一份做差異。<br>
        收集中斷＝收過、但 {{ data?.settings.gap_days }} 天以上沒成功收到；整段中斷＝那個網段收過的機器全部中斷。<br>
        軟體／服務／帳號數大幅變化＝變化 ≥ {{ data?.settings.count_change_min }} 筆且 ≥ {{ data?.settings.count_change_pct }}%。
        開機過久＝收集當時開機 &gt; {{ data?.settings.uptime_days }} 天、前一份還沒超過。<br>
        改主機名或 IP（同一個資產序號）算「主機名／IP 改變」，不算新增＋消失。快照保留 {{ data?.settings.retain_days }} 天。
        <template v-if="data?.snapshot"><br>這份快照：{{ data.snapshot.created_at }}（{{ data.snapshot.trigger === 'schedule' ? '排程' : '手動' }}）</template>
      </InfoNote>
      <span class="spacer" />
      <button class="btn small" :disabled="snapping" @click="snapNow">{{ snapping ? '寫入中…' : '立即寫一份快照' }}</button>
    </div>
    <p v-if="loadError" class="err">{{ loadError }}</p>
    <p v-if="data?.reason" class="note">{{ data.reason }}</p>

    <div class="tabs" role="tablist">
      <button class="tab" :class="{ active: tab === 'overview' }" @click="go('overview')">總覽</button>
      <button v-for="c in cats" :key="c.key" class="tab" :class="{ active: tab === c.key }" @click="go(c.key)">
        {{ c.label }} <b class="mono">{{ data?.counts?.[c.key] ?? 0 }}</b>
      </button>
      <button class="tab" :class="{ active: tab === 'mail' }" @click="go('mail')">摘要信設定</button>
      <button class="tab" :class="{ active: tab === 'mail-log' }" @click="go('mail-log')">寄送紀錄</button>
    </div>

    <p v-if="loading" class="dim">載入中…</p>

    <!-- 總覽：每一類的數字，點了進分頁 -->
    <div v-else-if="tab === 'overview' && data" class="cards">
      <button v-for="(c, i) in cats" :key="c.key" class="card" @click="go(c.key)">
        <span class="no mono">{{ i + 1 }}</span>
        <span class="n mono">{{ (data.counts[c.key] ?? 0).toLocaleString() }}</span>
        <span class="l">{{ c.label }}</span>
      </button>
      <button class="card" @click="go('collect_gap')">
        <span class="no mono">{{ cats.length + 1 }}</span>
        <span class="n mono">{{ data.segments.filter(s => s.whole).length }}</span>
        <span class="l">整段收集中斷的網段</span>
      </button>
      <NuxtLink class="card" to="/existence?bucket=counted">
        <span class="no mono">{{ cats.length + 2 }}</span>
        <span class="n mono">{{ (data.machines ?? 0).toLocaleString() }}</span>
        <span class="l">這份快照的現役台數</span>
      </NuxtLink>
    </div>

    <!-- 摘要信設定與寄送紀錄：2.x 第四批起只在 ⚙ 系統設定「通知設定」（設定只有一個入口） -->
    <div v-else-if="tab === 'mail' || tab === 'mail-log'" class="panel">
      摘要信設定與寄送紀錄已移到 <NuxtLink to="/v2/settings?st=notify">⚙ 系統設定・通知設定</NuxtLink>。
    </div>

    <!-- 各分類 -->
    <div v-else-if="data" class="panel">
      <template v-if="tab === 'collect_gap' && segRows.length">
        <div class="lh"><b>依網段</b><span class="dim">{{ segRows.length }} 段</span></div>
        <div class="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh k="n" :active="segSort.sortKey.value" :dir="segSort.sortDir.value" @sort="segSort.toggle">#</SortTh>
              <SortTh k="segment" :active="segSort.sortKey.value" :dir="segSort.sortDir.value" @sort="segSort.toggle">網段</SortTh>
              <SortTh k="gap" :active="segSort.sortKey.value" :dir="segSort.sortDir.value" @sort="segSort.toggle">中斷台數</SortTh>
              <SortTh k="collected" :active="segSort.sortKey.value" :dir="segSort.sortDir.value" @sort="segSort.toggle">收過的台數</SortTh>
              <SortTh k="whole_n" :active="segSort.sortKey.value" :dir="segSort.sortDir.value" @sort="segSort.toggle">整段中斷</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="s in segSort.sorted.value" :key="s.segment" :class="{ on: seg === s.segment }">
                <td class="mono">{{ s.no }}</td>
                <td class="mono">{{ s.segment }}</td>
                <td><button class="lnk mono" @click="go('collect_gap', { seg: s.segment })">{{ s.gap }}</button></td>
                <td class="mono">{{ s.collected }}</td>
                <td><span v-if="s.whole" class="pill bad">整段</span><span v-else class="dim">—</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="lh"><b>機器</b><span v-if="seg" class="dim">網段 {{ seg }}・<button class="lnk" @click="go('collect_gap')">看全部</button></span></div>
      </template>
      <div class="lh"><b>{{ curLabel }}</b><span class="dim">{{ rows.length.toLocaleString() }} 筆</span></div>
      <p v-if="!rows.length" class="dim">這一天沒有。</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">#</SortTh>
            <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">{{ tab === 'storage_low' ? 'Datastore' : '主機名稱' }}</SortTh>
            <SortTh v-if="tab !== 'storage_low'" k="ips" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh v-if="tab !== 'storage_low'" k="serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
            <SortTh k="before" :active="sortKey" :dir="sortDir" @sort="toggle">原本</SortTh>
            <SortTh k="after" :active="sortKey" :dir="sortDir" @sort="toggle">{{ tab === 'collect_gap' ? '最後成功收集' : '現在' }}</SortTh>
            <SortTh k="detail" :active="sortKey" :dir="sortDir" @sort="toggle">說明</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.key">
              <td class="mono">{{ r.no }}</td>
              <td>
                <NuxtLink v-if="tab === 'storage_low'" to="/storage" class="dl">{{ r.hostname }}</NuxtLink>
                <NuxtLink v-else :to="`/oncall?q=${encodeURIComponent(r.serial || r.hostname || '')}`" class="dl">{{ r.hostname || '(無主機名)' }}</NuxtLink>
                <span v-if="r.new" class="pill bad">新</span>
              </td>
              <td v-if="tab !== 'storage_low'" class="mono ell" :title="r.ips || ''">{{ r.ips || '—' }}</td>
              <td v-if="tab !== 'storage_low'" class="mono">
                <NuxtLink v-if="r.serial" :to="`/assets/${r.serial}`" class="dl">{{ r.serial }}</NuxtLink>
                <span v-else class="dim">資產庫沒有</span>
              </td>
              <td class="ell" :title="String(r.before)">{{ r.before === '' ? '—' : r.before }}</td>
              <td class="ell" :title="String(r.after)">{{ r.after === '' ? '—' : r.after }}</td>
              <td class="ell" :title="r.detail || ''">{{ r.detail || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hd h1 { margin: 0; }
.spacer { flex: 1; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: default; }
.tbl-wrap { overflow-x: auto; }
.tbl-wrap table { width: 100%; font-size: 13px; }
.sel { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.tabs { display: flex; align-items: center; gap: 4px; margin: 10px 0 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab { padding: 8px 12px; cursor: pointer; font-size: 13.5px; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; font-family: inherit; }
.tab:hover { color: var(--ink); }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.tab b { font-family: var(--disp); margin-left: 2px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; }
.card { position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 12px;
        background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer;
        text-align: left; text-decoration: none; font-family: inherit; }
.card:hover { border-color: var(--border-strong); }
.card .no { position: absolute; top: 6px; right: 10px; font-size: 11px; color: var(--muted); }
.card .n { font-size: 22px; font-weight: 700; color: var(--ink); font-family: var(--disp); }
.card .l { font-size: 12.5px; color: var(--ink-soft); }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.lh { display: flex; gap: 8px; align-items: baseline; margin: 6px 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 16px; font-size: 13px; color: var(--ink-soft); margin-bottom: 10px; }
.grid label { display: flex; align-items: center; gap: 6px; }
.grid .wide { grid-column: 1 / -1; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 120px; }
.wide-in { flex: 1; width: auto; }
.num { width: 70px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.pv { background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; font-size: 13px; }
.pv pre { white-space: pre-wrap; font-family: inherit; margin: 6px 0 0; }
.pill { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; border: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.pill.ok { color: var(--brand-dark); border-color: var(--brand); background: var(--mint); }
.pill.bad { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); }
.ell { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dl, .lnk { color: var(--brand-dark); text-decoration: none; background: none; border: 0; padding: 0; cursor: pointer; font-family: inherit; font-size: inherit; }
.dl:hover, .lnk:hover { text-decoration: underline; }
tr.on td { background: var(--mint); }
.dim { color: var(--muted); }
.err { color: var(--bad); }
.note { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
</style>
