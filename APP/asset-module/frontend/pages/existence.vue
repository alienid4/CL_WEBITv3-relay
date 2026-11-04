<script setup lang="ts">
// 存在可信度下鑽頁（EXIST1，2026-10-18）。
// 首頁「在管 N 台」「CIA 完整率」「CIA 待補登」「疑似下線」都點進這裡；
// 數字跟清單讀同一份 existence.compute()（/api/existence/summary 與 /machines），加總＝下鑽。
interface Machine {
  key: string; asset_serial: string | null; serials: string[]
  hostname: string | null; ip: string | null; os: string | null
  mac: string | null; hw_serial: string | null; vcenter: string | null
  has_cia: boolean; level: string; level_label: string; score: number
  evidence: string[]; last_seen_any: string | null
  onboarded: boolean; onboarded_stale: boolean; counted: boolean
  suspect_offline: boolean; retired: boolean; pending_confirm: boolean
  suggested_owner: string | null
  stale_evidence?: string[]
}
interface SourceSnap { source: string; label: string; latest: string | null; age_days: number | null; stale: boolean }
interface Summary {
  total_machines: number; counted: number; suspect_offline: number; retired: number
  cia_registered: number; cia_pending: number; cia_complete_pct: number | null
  pending_confirm: number; onboarded: number; onboarded_stale: number
  by_level: Record<string, number>; level_score: Record<string, number>
  level_label: Record<string, string>; count_threshold: number
  onboarded_days: number; collect_evidence_days: number
  sources?: SourceSnap[]; stale_sources?: SourceSnap[]; source_stale_days?: number
}
interface SummaryOut { summary: Summary; as_of: string; buckets: Record<string, string> }
interface ListOut { bucket: string; bucket_label: string; level: string | null; total: number; items: Machine[] }

const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const { showToast } = useToast()
const rt = useRuntimeConfig()

const bucket = computed(() => String(route.query.bucket || 'counted'))
const level = computed(() => (route.query.level ? String(route.query.level) : null))
const sum = ref<SummaryOut | null>(null)
const list = ref<ListOut | null>(null)
const loading = ref(true)
const loadError = ref('')

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [s, l] = await Promise.all([
      apiFetch<SummaryOut>('/api/existence/summary'),
      apiFetch<ListOut>('/api/existence/machines',
        { params: level.value ? { bucket: bucket.value, level: level.value } : { bucket: bucket.value } }),
    ])
    sum.value = s
    list.value = l
  } catch (e: any) {
    loadError.value = e?.data?.detail ?? e?.message ?? '載入失敗'
    showToast(`載入失敗：${loadError.value}`, 'error', 12000)
  } finally {
    loading.value = false
  }
}
await load()
watch([bucket, level], load)

function pick(b: string, lv: string | null = null) {
  router.push({ path: '/existence', query: lv ? { bucket: b, level: lv } : { bucket: b } })
}

const s = computed(() => sum.value?.summary)
const cards = computed(() => {
  const x = s.value
  if (!x) return []
  return [
    { b: 'counted', label: '計入台數', n: x.counted },
    { b: 'cia_registered', label: 'CIA 有登記', n: x.cia_registered },
    { b: 'cia_pending', label: 'CIA 待補登', n: x.cia_pending },
    { b: 'pending_confirm', label: '只有 DY（待確認）', n: x.pending_confirm },
    { b: 'suspect_offline', label: '疑似下線', n: x.suspect_offline },
    { b: 'retired', label: '已退役（備查）', n: x.retired },
    { b: 'onboarded', label: '已納管', n: x.onboarded },
    { b: 'onboarded_stale', label: '已納管但失聯', n: x.onboarded_stale },
  ]
})
const LEVELS = ['onboarded', 'inventory', 'collect_stale', 'scan', 'cia_only', 'none']
const EV: Record<string, string> = {
  onboarded: '已納管', 'inv:dynassets': 'DY', 'inv:vcenter': 'vCenter',
  collect_stale: '收集(>7天)', scan: '掃描',
}

// 項次：以後端回來的順序為準、固定不變（重排後同一台仍是同一號）
const rows = computed(() => (list.value?.items ?? []).map((m, i) => ({
  ...m, no: i + 1, cia: m.has_cia ? 1 : 0,
  ev: m.evidence.map(e => (EV[e] || e) + ((m.stale_evidence ?? []).includes(e.replace('inv:', '')) ? '（來源過期）' : '')).join('、'),
})))
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'no')
const exportHref = computed(() => `${rt.public.apiBase}/api/existence/cia-pending/export`)
</script>

<template>
  <div class="page">
    <V2Banner to="/v2/reconcile?tab=evidence" label="9.2 數量對帳・偵測證據" />
    <div class="hd">
      <h1>存在可信度</h1>
      <span v-if="s?.cia_complete_pct !== undefined" class="kpi">
        CIA 完整率 <b class="mono">{{ s?.cia_complete_pct === null ? '—' : `${s?.cia_complete_pct}%` }}</b>
      </span>
      <InfoNote>
        台數＝<b>真的存在</b>的機器，跟 CIA 有沒有登記無關（決策 EXIST1）。<br>
        存在可信度取這台所有證據裡最高的一項：已納管（收集帳號 {{ s?.onboarded_days }} 天內連得上）100、
        在 DY／vCenter 最新一次匯入裡 90、在所屬網段最近一次掃描活著或收集帳號 {{ s?.collect_evidence_days }} 天內連上過 70、
        只有 CIA 60、什麼都沒有 0。≥ {{ s?.count_threshold }} 才計入台數，沒達到＝疑似下線。<br>
        匯入／掃描看「最新一次有沒有它」，不看幾天前；來源本身超過 {{ s?.source_stale_days }} 天沒更新只提醒重匯，不把機器降級。<br><br>
        CIA 完整率＝計入的機器裡 CIA 有登記的比例。業務欄位（負責人／AP ID／用途／環境）只從 CIA 來。<br>
        「只有 DY」＝沒有 CIA、沒掃到、沒納管，只有 DY 存活清單說它在：計入，但要確認。<br>
        計算時間：{{ sum?.as_of }}
      </InfoNote>
      <span class="spacer" />
      <a class="btn small" :href="exportHref">⬇ CIA 補登清單</a>
    </div>

    <p v-if="loadError" class="dim">{{ loadError }}</p>
    <div v-for="x in (s?.stale_sources ?? [])" :key="x.source" class="stale">
      ⚠ {{ x.label }} 資料已 {{ x.age_days }} 天沒更新，請重匯（機器不降級，依據標「來源過期」）
    </div>

    <div v-if="s" class="cards">
      <button v-for="c in cards" :key="c.b" class="card" :class="{ on: bucket === c.b && !level }"
              @click="pick(c.b)">
        <span class="n mono">{{ c.n.toLocaleString() }}</span><span class="l">{{ c.label }}</span>
      </button>
    </div>
    <div v-if="s" class="levels">
      <span class="ll">依證據</span>
      <button v-for="lv in LEVELS" :key="lv" class="chip" :class="{ on: level === lv }"
              :title="s.level_label[lv]" @click="pick('all', lv)">
        {{ s.level_score[lv] }}・{{ s.level_label[lv] }} <b class="mono">{{ (s.by_level[lv] ?? 0).toLocaleString() }}</b>
      </button>
    </div>

    <p v-if="loading" class="dim">載入中…</p>
    <template v-else-if="list">
      <div class="lh">
        <b>{{ list.bucket_label }}</b><template v-if="list.level">・{{ s?.level_label[list.level] }}</template>
        <span class="dim">{{ list.total.toLocaleString() }} 台</span>
      </div>
      <p v-if="!rows.length" class="dim">沒有符合的機器。</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">#</SortTh>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
              <SortTh k="cia" :active="sortKey" :dir="sortDir" @sort="toggle">CIA</SortTh>
              <SortTh k="score" :active="sortKey" :dir="sortDir" @sort="toggle">存在可信度</SortTh>
              <SortTh k="ev" :active="sortKey" :dir="sortDir" @sort="toggle">依據</SortTh>
              <SortTh k="last_seen_any" :active="sortKey" :dir="sortDir" @sort="toggle">最後看到</SortTh>
              <SortTh k="os" :active="sortKey" :dir="sortDir" @sort="toggle">作業系統</SortTh>
              <SortTh k="suggested_owner" :active="sortKey" :dir="sortDir" @sort="toggle">窗口線索</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.key">
              <td class="mono">{{ r.no }}</td>
              <td>{{ r.hostname || '(無主機名)' }}<span v-if="r.pending_confirm" class="pill warn">待確認</span></td>
              <td class="mono">{{ r.ip || '—' }}</td>
              <td class="mono">
                <NuxtLink v-if="r.asset_serial" :to="`/assets/${r.asset_serial}`" class="dl">{{ r.asset_serial }}</NuxtLink>
                <span v-else class="dim">資產庫沒有</span>
              </td>
              <td>
                <span v-if="r.has_cia" class="pill ok">有登記</span>
                <span v-else class="pill off">CIA 待補登</span>
              </td>
              <td class="mono" :title="r.level_label">{{ r.score }}</td>
              <td>{{ r.ev || '—' }}</td>
              <td class="mono">{{ r.last_seen_any || '—' }}</td>
              <td class="ell" :title="r.os || ''">{{ r.os || '—' }}</td>
              <td class="ell" :title="r.suggested_owner || ''">{{ r.suggested_owner || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hd h1 { margin: 0; }
.kpi { font-size: 14px; color: var(--ink-soft); }
.kpi b { color: var(--brand-dark); font-size: 18px; font-family: var(--disp); }
.spacer { flex: 1; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin: 12px 0 8px; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 12px;
        background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        cursor: pointer; text-align: left; }
.card:hover { border-color: var(--border-strong); }
.card.on { border-color: var(--brand); background: var(--mint); }
.card .n { font-size: 20px; font-weight: 700; color: var(--ink); font-family: var(--disp); }
.card .l { font-size: 12.5px; color: var(--ink-soft); }
.levels { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 12px; font-size: 12.5px; }
.ll { color: var(--muted); }
.chip { border: 1px solid var(--border); background: var(--sub); border-radius: 999px; padding: 3px 10px;
        cursor: pointer; color: var(--ink-soft); font-size: 12px; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.lh { display: flex; gap: 8px; align-items: baseline; margin-bottom: 6px; }
.pill { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 999px; font-size: 11.5px;
        border: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.pill.ok { color: var(--brand-dark); border-color: var(--brand); background: var(--mint); }
.pill.off { color: var(--warn-text); border-color: var(--warn); background: var(--warn-soft); }
.pill.warn { color: var(--warn-text); border-color: var(--warn); background: var(--warn-soft); }
.ell { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dl { color: var(--brand-dark); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.dim { color: var(--muted); }
.stale { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn);
         background: var(--warn-soft); color: var(--warn-text); font-size: 13px; font-weight: 600; }
</style>
