<script setup lang="ts">
// 1.2「異常報告」頁籤（V18，2.047.3 V2 原生；取代嵌入的舊 pages/anomalies.vue）。同一批 API、同一套計算：
// /api/dashboard/stats（帳實相符）、/api/issues（未讀異常）、/api/dashboard/composition（資料乾淨度）、/api/health/summary（體檢）、/api/manage-state（未登記）。
// 舊頁的「問題清單」分頁就是 1.2 的「掃描異常」頁籤，這裡不再重複一份。
// 待處理四組（連續消失／本期首次消失／異常新增／在網路上但未登記）：台數點下去在本頁列出是哪幾台（?ag=組別，ag=all＝全部），表格自帶匯出（取代舊頁的 CSV）。
interface DashboardStats { total_ica_count: number; total_overlap_count: number; scanned_count: number; scan_only_count: number; last_scan_time: string | null
  covered_ica_machines?: number; covered_missing_machines?: number; uncovered_ica_machines?: number; match_pct_covered?: number | null }
interface IssueRow { id: number; detected_at: string; hostname: string | null; ip: string | null; issue_type: string }
interface DataQuality { total: number; verified_by_vcenter: number; verified_pct: number; os_unknown: number; pending_review: number; merged_done: number; duplicate_groups: number; duplicate_extra_rows: number }
interface Composition { total: number; os_from_facts: number; os_guessed: number; data_quality?: DataQuality }
interface HealthSummary { total: number; clean: number; needs_review: number; machine_bad: number; data_bad: number; by_issue: Record<string, number>; unverified: number; incomplete_coverage?: number }
interface ManageState { scan_time: string | null; items?: { hostname: string | null; ip: string | null; state: string }[] }

const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const stats = ref<DashboardStats | null>(null)
const issues = ref<IssueRow[]>([])
const comp = ref<Composition | null>(null)
const health = ref<HealthSummary | null>(null)
const mstate = ref<ManageState | null>(null)
const errs = ref<string[]>([])
const loading = ref(true)
const begin = useLatest()
async function load() {
  const isLatest = begin()
  loading.value = true
  const why = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
  const out: string[] = []
  const [s, i, c, h, m] = await Promise.all([
    apiFetch<DashboardStats>('/api/dashboard/stats', { params: { environment: '全部' } }).catch((e) => { out.push(`帳實相符：${why(e)}`); return null }),
    apiFetch<IssueRow[]>('/api/issues', { params: { is_read: false } }).catch((e) => { out.push(`掃描異常：${why(e)}`); return null }),
    apiFetch<Composition>('/api/dashboard/composition').catch((e) => { out.push(`資料乾淨度：${why(e)}`); return null }),
    apiFetch<HealthSummary>('/api/health/summary').catch((e) => { out.push(`體檢：${why(e)}`); return null }),
    apiFetch<ManageState>('/api/manage-state').catch((e) => { out.push(`未登記：${why(e)}`); return null }),
  ])
  if (!isLatest()) return
  stats.value = s; issues.value = i ?? []; comp.value = c; health.value = h; mstate.value = m
  errs.value = out
  loading.value = false
}
onMounted(load)

const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())
const fmtTime = (s: string) => (s ? s.replace(/^\d{4}-/, '').slice(0, 14) : '')
const dayOf = (t: string) => (t || '').slice(0, 10)
const url = (path: string, query: Record<string, string>) => { const q = new URLSearchParams(query).toString(); return q ? `${path}?${q}` : path }
// 舊頁連到 V1 的地方，改成 V2 對應頁（舊 → 新；vCenter 校正、要查看、體檢項目 2.047.4 起改本頁下鑽）：/adopt → 6.1 納管作業；/import → ⚙ 資料匯入；
// /assets?show=duplicates → 2.1 重複登記；/assets?filter os 空 → 2.1 OS 認不出（os_source=unknown）；/count-bridge → 9.2 數量對帳
const V2 = { adopt: '/v2/onboard', import: '/v2/settings?st=import', dups: '/v2/hosts?tab=dups', osUnknown: '/v2/hosts?os_source=unknown',
  reconcile: '/v2/reconcile' }

// 帳實相符率（口徑同總覽：分母只算這次掃描有涵蓋的網段；舊後端沒有就退回舊口徑）
const matchPct = computed(() => {
  const s = stats.value
  if (!s) return 0
  if (s.match_pct_covered !== undefined) return s.match_pct_covered ?? 0
  const d = s.total_ica_count + s.scan_only_count
  return d ? Math.min(100, (s.total_overlap_count / d) * 100) : 0
})
const icaOnly = computed(() => { const s = stats.value; return s ? (s.covered_missing_machines !== undefined ? s.covered_missing_machines : s.total_ica_count - s.total_overlap_count) : 0 })
const diffCount = computed(() => icaOnly.value + (stats.value?.scan_only_count ?? 0))
const collectPct = computed(() => { const c = comp.value; return c && c.total ? (c.os_from_facts / c.total) * 100 : 0 })
const REVIEW_PER_WEEK = 300
const reviewWeeks = computed(() => Math.ceil((comp.value?.data_quality?.pending_review ?? 0) / REVIEW_PER_WEEK))

// 待處理分組（沿用總覽的判定：連續消失／首次消失／異常新增／未登記）
interface Group { key: string; name: string; sub: string; tone: string; items: IssueRow[]; to: string }
const groups = computed<Group[]>(() => {
  const lost = issues.value.filter(r => r.issue_type === '異常消失')
  const days: Record<string, Set<string>> = {}
  const keyOf = (r: IssueRow) => `${r.hostname ?? ''}|${r.ip ?? ''}`
  for (const r of lost) (days[keyOf(r)] ??= new Set()).add(dayOf(r.detected_at))
  const latestOf = (rows: IssueRow[]) => {
    const m: Record<string, IssueRow> = {}
    for (const r of rows) if (!m[keyOf(r)] || r.detected_at > m[keyOf(r)].detected_at) m[keyOf(r)] = r
    return Object.values(m).sort((a, b) => a.detected_at.localeCompare(b.detected_at))
  }
  const unreg = (mstate.value?.items ?? []).filter(i => i.state === '未登記')
    .map((i, idx) => ({ id: -1 - idx, detected_at: mstate.value?.scan_time ?? '', hostname: i.hostname, ip: i.ip, issue_type: '未登記' }))
  return [
    { key: 'repeat', name: '連續消失（2 期以上）', sub: '兩次以上掃描都沒回應，要查是不是已經退役', tone: 'prod', items: latestOf(lost.filter(r => (days[keyOf(r)]?.size ?? 0) >= 2)), to: url('/v2/changes', { tab: 'issues', type: '異常消失' }) },
    { key: 'first', name: '本期首次消失', sub: '這一期才掃不到，可能只是當下網路不通', tone: 'test', items: latestOf(lost.filter(r => (days[keyOf(r)]?.size ?? 0) < 2)), to: url('/v2/changes', { tab: 'issues', type: '異常消失' }) },
    { key: 'added', name: '異常新增', sub: '這次比對多出來的機器', tone: 'test', items: latestOf(issues.value.filter(r => r.issue_type === '異常新增')), to: url('/v2/changes', { tab: 'issues', type: '異常新增' }) },
    { key: 'unreg', name: '在網路上但未登記', sub: '活著但資產庫查無此機，要納入管理', tone: 'test', items: unreg, to: V2.adopt },
  ].filter(g => g.items.length)
})
const totalPending = computed(() => groups.value.reduce((s, g) => s + g.items.length, 0))
const groupRows = computed(() => groups.value.map((g, i) => ({ ...g, __no: i + 1, n: g.items.length, oldest: fmtTime(g.items[0]?.detected_at ?? '') })))
const ag = computed(() => (typeof route.query.ag === 'string' ? route.query.ag : ''))
const openGroup = (k: string | undefined) => router.replace({ query: { ...route.query, ag: k } })
const drillRows = computed(() => {
  const gs = ag.value === 'all' ? groups.value : groups.value.filter(g => g.key === ag.value)
  return gs.flatMap(g => g.items.map(it => ({ ...it, group: g.name, to: g.to }))).map((r, i) => ({ ...r, __no: i + 1, key: `${r.group}|${r.id}|${i}` }))
})
const drillTitle = computed(() => (ag.value === 'all' ? '全部待處理' : groups.value.find(g => g.key === ag.value)?.name ?? ''))

const qualityRows = computed(() => {
  const q = comp.value?.data_quality
  if (!q) return []
  return [
    { k: '待人工審核', v: q.pending_review, why: '判不準是否同一台，系統不自動合併', to: V2.import },
    { k: '已由 vCenter 校正', v: q.verified_by_vcenter, why: `佔 ${q.verified_pct}%`, to: '', ad: 'vc' },
    { k: '疑似重複登記（組）', v: q.duplicate_groups, why: `清掉可少 ${n(q.duplicate_extra_rows)} 筆（一機多系統／VIP 分列不算重複）`, to: V2.dups },
    { k: 'OS 仍未知', v: q.os_unknown, why: 'vCenter 涵蓋不到，要靠掃描或 SSH', to: V2.osUnknown },
  ].map((r, i) => ({ ...r, __no: i + 1 }))
})
const issueRows = computed(() => Object.entries(health.value?.by_issue ?? {}).map(([k, m], i) => ({ __no: i + 1, k, n: m })))
// ---- 伺服器端下鑽（2.047.4，總管：數字＝點下去的筆數，資料來源跟算數字的同一份）
// ?ad=vc（已由 vCenter 校正）｜health（要查看）｜issue:<項目>（體檢最常卡在）
const ad = computed(() => (typeof route.query.ad === 'string' ? route.query.ad : ''))
const openAd = (k: string | undefined) => router.replace({ query: { ...route.query, ad: ad.value === k ? undefined : k } })
const adRows = ref<any[]>([])
const adErr = ref('')
const adLoading = ref(false)
const beginAd = useLatest()
watch(ad, async (k) => {
  const isLatest = beginAd()
  adRows.value = []
  adLoading.value = false
  if (!k) return
  adLoading.value = true
  try {
    const r = k === 'vc' ? await apiFetch<{ items: any[] }>('/api/dashboard/vcenter-verified')
      : await apiFetch<{ items: any[] }>('/api/health/drill', { query: k.startsWith('issue:') ? { issue: k.slice(6) } : {} })
    if (!isLatest()) return
    adRows.value = r.items.map((x, i) => ({ ...x, __no: i + 1, key: `${x.asset_serial ?? ''}|${x.id ?? i}`, issues_text: (x.issues ?? []).join('、') }))
    adErr.value = ''
  } catch (e: any) { if (isLatest()) adErr.value = `明細載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { if (isLatest()) adLoading.value = false }
}, { immediate: true })
const adTitle = computed(() => (ad.value === 'vc' ? '已由 vCenter 校正' : ad.value === 'health' ? '要查看' : ad.value.startsWith('issue:') ? `體檢卡在「${ad.value.slice(6)}」` : ''))
const adCols = computed(() => [{ k: 'hostname', label: '主機名稱', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null), text: (r: any) => r.hostname || r.asset_serial || '（無主機名）' },
  { k: 'ip', label: 'IP', mono: true }, { k: 'environment', label: '環境' },
  ...(ad.value === 'vc' ? [{ k: 'os', label: 'OS' }, { k: 'asset_status', label: '資產狀態' }] : [{ k: 'headline', label: '最嚴重的一項' }, { k: 'issues_text', label: '全部項目' }])])
</script>

<template>
  <div>
    <p v-for="e in errs" :key="e" class="err">載入失敗——{{ e }}</p>
    <p v-if="loading" class="muted">載入中…</p>
    <template v-else>
      <div v-if="stats" class="verdict">
        <div class="v"><span class="vk">帳實相符</span>
          <p>帳實相符 <b class="good">{{ matchPct.toFixed(1) }}%</b><InfoNote>只算這次掃描有涵蓋的網段：一致 ÷（有涵蓋的登記台數＋掃到沒登記）。這次沒掃到的網段不算「對不上」，另列「未涵蓋」。</InfoNote>：
            登記 {{ n(stats.covered_ica_machines ?? stats.total_ica_count) }} 台（有涵蓋）、網路實掃 {{ n(stats.scanned_count) }} 台，
            <NuxtLink to="/v2/changes?tab=issues">對不起來 <b class="bad">{{ n(diffCount) }}</b> 台</NuxtLink>。</p></div>
        <div class="v"><span class="vk">要決定</span>
          <p><b class="bad">{{ n(icaOnly) }}</b> 台登記在案、有涵蓋卻掃不到<template v-if="stats.uncovered_ica_machines !== undefined">（<NuxtLink :to="V2.reconcile">未涵蓋 {{ n(stats.uncovered_ica_machines) }} 台</NuxtLink>另計）</template>，
            其中 <button type="button" class="lb big" @click="openGroup('repeat')">{{ n(groups.find(g => g.key === 'repeat')?.items.length ?? 0) }}</button> 台已連續 2 期以上沒回應，該確認是否退役。</p></div>
        <div class="v"><span class="vk">人力</span>
          <p>待人工審核 <b class="warn">{{ n(comp?.data_quality?.pending_review ?? 0) }}</b> 筆，約需 <b class="warn">{{ reviewWeeks }}</b> 週消化<InfoNote>以每週處理 {{ REVIEW_PER_WEEK }} 筆估算。</InfoNote></p></div>
      </div>

      <div v-if="health && health.total" class="bar">
        <button type="button" class="lb" data-testid="anom-health" @click="openAd('health')"><b class="big warn">{{ n(health.needs_review) }}</b> 台要查看</button>
        <span><b class="big good">{{ n(health.clean) }}</b> 台完全沒問題</span>
        <span class="muted">機器有異常 {{ n(health.machine_bad) }} 台、資料有問題 {{ n(health.data_bad) }} 台</span>
        <InfoNote>「要查看」點下去在下面列出是哪幾台（跟這個數字同一份體檢計算）。<template v-if="health.unverified">其中 {{ n(health.unverified) }} 台未經 SSH／WinRM 驗證——多數已有登記／掃描資料，只是沒有機器親口確認過。</template><template v-if="health.incomplete_coverage">其中 {{ n(health.incomplete_coverage) }} 台有體檢項目未驗或不適用（例如尚無帳號盤點）——「完全沒問題」只代表已驗的項目沒發現問題。</template></InfoNote>
      </div>

      <div class="two">
        <section class="box">
          <h3 class="h3">待處理 {{ n(totalPending) }}<button v-if="totalPending" type="button" class="lb" @click="openGroup(ag === 'all' ? undefined : 'all')">全部列出</button></h3>
          <V2Table :rows="groupRows" prefix="G" row-key="key" unit="組" export-name="異常報告_待處理分組" empty="目前沒有待處理的問題"
                   :cols="[{ k: 'name', label: '組別' }, { k: 'sub', label: '說明' }, { k: 'n', label: '台數', mono: true, tone: (r: any) => r.tone }, { k: 'oldest', label: '最早', mono: true }]">
            <template #cell-n="{ row }"><button type="button" class="lb" data-testid="anom-group-n" @click="openGroup(ag === row.key ? undefined : row.key)">{{ n(row.n) }}</button></template>
          </V2Table>
        </section>
        <section v-if="comp?.data_quality" class="box">
          <h3 class="h3">資料乾淨度 {{ collectPct.toFixed(1) }}%<InfoNote>{{ n(comp.os_from_facts) }} / {{ n(comp.total) }} 台拿得到機器自報的事實。</InfoNote></h3>
          <V2Table :rows="qualityRows" prefix="Q" row-key="k" unit="項" export-name="異常報告_資料乾淨度"
                   :cols="[{ k: 'k', label: '項目', link: (r: any) => r.to || null }, { k: 'v', label: '數量', mono: true }, { k: 'why', label: '說明' }]">
            <template #cell-v="{ row }"><button v-if="row.ad" type="button" class="lb" data-testid="anom-vc" @click="openAd(row.ad)">{{ n(row.v) }}</button><span v-else>{{ n(row.v) }}</span></template>
          </V2Table>
        </section>
      </div>
      <section v-if="issueRows.length" class="box">
        <h3 class="h3">體檢最常卡在<InfoNote>台數點下去在下面列出是哪幾台（跟這個數字同一份體檢計算）。</InfoNote></h3>
        <V2Table :rows="issueRows" prefix="P" row-key="k" unit="項" export-name="異常報告_體檢問題"
                 :cols="[{ k: 'k', label: '問題' }, { k: 'n', label: '台數', mono: true }]">
          <template #cell-n="{ row }"><button type="button" class="lb" @click="openAd(`issue:${row.k}`)">{{ n(row.n) }}</button></template>
        </V2Table>
      </section>

      <section v-if="ad && adTitle" class="box" data-testid="anom-ad">
        <h3 class="h3">{{ adTitle }} {{ adLoading ? '…' : adRows.length }} 台<button type="button" class="lb" @click="openAd(undefined)">關閉</button></h3>
        <p v-if="adErr" class="err">{{ adErr }}</p>
        <V2Table v-else :rows="adRows" prefix="H" row-key="key" unit="台" :export-name="`異常報告_${adTitle}`" :empty="adLoading ? '載入中…' : '沒有資料'" :cols="adCols" />
      </section>

      <section v-if="ag && drillTitle" class="box" data-testid="anom-drill">
        <h3 class="h3">{{ drillTitle }} {{ drillRows.length }} 台<button type="button" class="lb" @click="openGroup(undefined)">關閉</button></h3>
        <V2Table :rows="drillRows" prefix="D" row-key="key" unit="台" :export-name="`異常報告_${drillTitle}`" empty="沒有資料"
                 :cols="[{ k: 'group', label: '組別' }, { k: 'hostname', label: '主機名稱', link: (r: any) => r.to, text: (r: any) => r.hostname || '（無主機名）' }, { k: 'ip', label: 'IP', mono: true }, { k: 'issue_type', label: '問題類型' }, { k: 'detected_at', label: '發現時間', mono: true }]" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.verdict { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 12px; }
@media (max-width: 900px) { .verdict { grid-template-columns: 1fr; } }
.v { padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); font-size: 13px; line-height: 1.7; }
.v p { margin: 4px 0 0; }
.vk { font-size: 12px; color: var(--muted); }
.v b, .big { font-family: var(--disp); font-size: 18px; font-weight: 600; }
.good { color: var(--brand-dark); } .warn { color: var(--warn-text); } .bad { color: var(--bad); }
.bar { display: flex; gap: 14px; align-items: baseline; flex-wrap: wrap; margin: 6px 0 12px; font-size: 13px; }
.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: start; }
@media (max-width: 1000px) { .two { grid-template-columns: 1fr; } }
.box { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.h3 { margin: 0 0 8px; font-size: 15px; display: flex; gap: 8px; align-items: center; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; }
.lb.big { font-family: var(--disp); font-size: 18px; font-weight: 600; color: var(--bad); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
