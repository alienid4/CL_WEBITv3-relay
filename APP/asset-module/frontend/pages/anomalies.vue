<script setup lang="ts">
// 1-3 異常報告：把總覽上「問題面」的區塊集中到這裡（使用者 2026-09-10：
// 「這些都是屬於異常報告，放在 1-3」）。總覽負責「掌握了什麼」，異常/待辦來這頁。
// 資料源與總覽同一批端點，計算沿用同一套（口徑要一致，兩頁數字不能打架）。
interface DashboardStats {
  total_ica_count: number
  total_overlap_count: number
  scanned_count: number
  scan_only_count: number
  last_scan_time: string | null
}
interface IssueRow {
  id: number; detected_at: string; hostname: string | null; ip: string | null; issue_type: string
}
interface DataQuality {
  total: number; verified_by_vcenter: number; verified_pct: number
  os_unknown: number; pending_review: number; merged_done: number
  duplicate_groups: number; duplicate_extra_rows: number
}
interface Composition {
  total: number; os_from_facts: number; os_guessed: number; data_quality?: DataQuality
}
interface HealthSummary {
  total: number; clean: number; needs_review: number
  machine_bad: number; data_bad: number; by_issue: Record<string, number>; unverified: number
}
interface MStateItem { hostname: string | null; ip: string | null; state: string }
interface ManageState { scan_time: string | null; items?: MStateItem[] }

const { apiFetch } = useApi()

const stats = ref<DashboardStats | null>(null)
const issues = ref<IssueRow[]>([])
const comp = ref<Composition | null>(null)
const health = ref<HealthSummary | null>(null)
const mstate = ref<ManageState | null>(null)

try { stats.value = await apiFetch<DashboardStats>('/api/dashboard/stats', { params: { environment: '全部' } }) } catch { /* 不擋整頁 */ }
try { issues.value = await apiFetch<IssueRow[]>('/api/issues', { params: { is_read: false } }) } catch { issues.value = [] }
try { comp.value = await apiFetch<Composition>('/api/dashboard/composition') } catch { /* 選用 */ }
try { health.value = await apiFetch<HealthSummary>('/api/health/summary') } catch { /* 選用 */ }
try { mstate.value = await apiFetch<ManageState>('/api/manage-state') } catch { /* 選用 */ }

// 2026-09-12：問題清單併進來當第二分頁（異常報告本來就含 /api/issues 資料，是它的超集）。
const tab = ref<'overview' | 'issues'>('overview')

function n(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : v.toLocaleString()
}
function fmtTime(s: string): string { return s ? s.replace(/^\d{4}-/, '').slice(0, 14) : '' }
function dayOf(t: string) { return (t || '').slice(0, 10) }

// 帳實相符率＝相符 ÷（登記 ∪ 掃到）——分母是聯集，理由見總覽 index.vue 註解。
const consistency = computed(() => {
  const s = stats.value
  if (!s) return 0
  const denom = s.total_ica_count + s.scan_only_count
  return denom ? Math.min(100, (s.total_overlap_count / denom) * 100) : 0
})
const matchPct = computed(() => consistency.value)
const collectPct = computed(() => {
  const c = comp.value
  return c && c.total ? (c.os_from_facts / c.total) * 100 : 0
})
const icaOnlyAll = computed(() => {
  const s = stats.value
  return s ? s.total_ica_count - s.total_overlap_count : 0
})
const diffCount = computed(() => icaOnlyAll.value + (stats.value?.scan_only_count ?? 0))
const scannedAlive = computed(() => stats.value?.scanned_count ?? 0)
const REVIEW_PER_WEEK = 300
const reviewWeeks = computed(() => Math.ceil((comp.value?.data_quality?.pending_review ?? 0) / REVIEW_PER_WEEK))

const topIssues = computed(() =>
  Object.entries(health.value?.by_issue ?? {}).slice(0, 3).map(([k, m]) => ({ k, n: m })))

// 待處理分組（沿用總覽的判定：連續消失/首次消失/異常新增/未登記）
interface IssueGroup { key: string; name: string; sub: string; tone: string; n: number; oldest: string; items: IssueRow[]; to: any }
const issueGroups = computed<IssueGroup[]>(() => {
  const lost = issues.value.filter((r) => r.issue_type === '異常消失')
  const days: Record<string, Set<string>> = {}
  for (const r of lost) {
    const k = `${r.hostname ?? ''}|${r.ip ?? ''}`
    ;(days[k] ??= new Set()).add(dayOf(r.detected_at))
  }
  const latestOf = (rows: IssueRow[]) => {
    const m: Record<string, IssueRow> = {}
    for (const r of rows) {
      const k = `${r.hostname ?? ''}|${r.ip ?? ''}`
      if (!m[k] || r.detected_at > m[k].detected_at) m[k] = r
    }
    return Object.values(m).sort((a, b) => a.detected_at.localeCompare(b.detected_at))
  }
  const repeat = latestOf(lost.filter((r) => (days[`${r.hostname ?? ''}|${r.ip ?? ''}`]?.size ?? 0) >= 2))
  const first = latestOf(lost.filter((r) => (days[`${r.hostname ?? ''}|${r.ip ?? ''}`]?.size ?? 0) < 2))
  const added = latestOf(issues.value.filter((r) => r.issue_type === '異常新增'))
  const unregItems: IssueRow[] = (mstate.value?.items ?? [])
    .filter((i) => i.state === '未登記')
    .map((i, idx) => ({ id: -1 - idx, detected_at: mstate.value?.scan_time ?? '', hostname: i.hostname, ip: i.ip, issue_type: '未登記' }))
  const mk = (key: string, name: string, sub: string, tone: string, items: IssueRow[], to: any): IssueGroup => ({
    key, name, sub, tone, n: items.length, oldest: items.length ? fmtTime(items[0].detected_at) : '—', items, to,
  })
  return [
    mk('repeat', '連續消失（2 期以上）', '兩次以上掃描都沒回應，要查是不是已經退役', 'bad', repeat, { path: '/issues', query: { type: '異常消失' } }),
    mk('first', '本期首次消失', '這一期才掃不到，可能只是當下網路不通', 'warn', first, { path: '/issues', query: { type: '異常消失' } }),
    mk('added', '異常新增', '這次比對多出來的機器', 'warn', added, { path: '/issues', query: { type: '異常新增' } }),
    mk('unreg', '在網路上但未登記', '活著但資產庫查無此機，要納入管理', 'warn', unregItems, { path: '/adopt' }),
  ].filter((g) => g.n > 0)
})
const totalPending = computed(() => issueGroups.value.reduce((s, g) => s + g.n, 0))
const openGroup = ref<string | null>(null)
function toggleIssueGroup(k: string) { openGroup.value = openGroup.value === k ? null : k }

function exportIssuesCsv() {
  const rows = [['群組', '主機名稱', 'IP', '問題類型', '發現時間']]
  for (const g of issueGroups.value) {
    for (const it of g.items) rows.push([g.name, it.hostname ?? '', it.ip ?? '', it.issue_type, it.detected_at])
  }
  const csv = '﻿' + rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `異常報告_${(stats.value?.last_scan_time ?? '').slice(0, 10) || 'export'}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="anom">
    <div class="section-divider">異常報告</div>

    <div class="atabs">
      <button class="atab" :class="{ on: tab === 'overview' }" type="button" @click="tab = 'overview'">異常總覽</button>
      <button class="atab" :class="{ on: tab === 'issues' }" type="button" @click="tab = 'issues'">問題清單</button>
    </div>

    <div v-show="tab === 'overview'">
    <section v-if="stats" class="verdict">
      <div class="v">
        <div class="vk">RECONCILIATION</div>
        <p class="vt">
          帳實相符 <b class="good">{{ matchPct.toFixed(1) }}%</b>：登記 {{ n(stats.total_ica_count) }} 台、
          網路實掃 {{ n(scannedAlive) }} 台，
          <NuxtLink to="/issues" class="vlink">對不起來 <b class="bad">{{ n(diffCount) }}</b> 台</NuxtLink>。
        </p>
      </div>
      <div class="v">
        <div class="vk">DECISION</div>
        <p class="vt">
          需要決策：<b class="bad">{{ n(icaOnlyAll) }}</b> 台登記在案卻掃不到，
          其中 <b class="bad">{{ n(issueGroups.find((g) => g.key === 'repeat')?.n ?? 0) }}</b> 台
          已連續 2 期以上沒回應，該確認是否退役。
        </p>
      </div>
      <div class="v">
        <div class="vk">CAPACITY</div>
        <p class="vt">
          人力缺口：待人工審核 <b class="warn">{{ n(comp?.data_quality?.pending_review ?? 0) }}</b> 筆，
          約需 <b class="warn">{{ reviewWeeks }}</b> 週消化。
          <InfoNote>以每週處理 {{ REVIEW_PER_WEEK }} 筆估算。</InfoNote>
        </p>
      </div>
    </section>

    <section v-if="health && health.total" class="card hbar">
      <div class="hb-main">
        <NuxtLink :to="{ path: '/assets', query: { sort_by: 'health_rank', order: 'asc' } }" class="hb-num">
          <b class="mono">{{ n(health.needs_review) }}</b> 台要查看
        </NuxtLink>
        <span class="hb-sep">·</span>
        <span class="hb-ok"><b class="mono">{{ n(health.clean) }}</b> 台完全沒問題</span>
        <span class="hb-detail">
          機器有異常 <b class="bad">{{ n(health.machine_bad) }}</b> 台、
          資料有問題 <b class="warn">{{ n(health.data_bad) }}</b> 台
        </span>
      </div>
      <div v-if="topIssues.length" class="hb-issues">
        最常卡在：
        <NuxtLink v-for="t in topIssues" :key="t.k"
                  :to="{ path: '/assets', query: { sort_by: 'health_rank', order: 'asc' } }"
                  class="hb-chip">{{ t.k }} <b class="mono">{{ n(t.n) }}</b></NuxtLink>
      </div>
      <p v-if="health.unverified" class="hb-unverified">
        其中 <b class="mono">{{ n(health.unverified) }}</b> 台未經 SSH/WinRM 驗證——多數已有登記/掃描資料，只是沒有機器親口確認過
      </p>
    </section>

    <div class="two">
      <section class="card issues">
        <div class="ihead">
          <div class="ck">待處理</div>
          <span class="ibadge mono">{{ n(totalPending) }}</span>
        </div>
        <p v-if="!issueGroups.length" class="ok">目前沒有待處理的問題。</p>
        <div v-for="g in issueGroups" :key="g.key" class="ig">
          <button type="button" class="igrow" :class="{ open: openGroup === g.key }" @click="toggleIssueGroup(g.key)">
            <i class="dot" :class="g.tone" />
            <span class="ign">{{ g.name }}<em>{{ g.sub }}</em></span>
            <b class="mono" :class="g.tone">{{ n(g.n) }}</b>
            <span class="ot mono">{{ g.oldest }}</span>
            <span class="arw">{{ openGroup === g.key ? '▾' : '▸' }}</span>
          </button>
          <div v-if="openGroup === g.key" class="igbody">
            <NuxtLink v-for="it in g.items.slice(0, 3)" :key="it.id" :to="g.to" class="iitem">
              <span class="ih">{{ it.hostname || '（無主機名）' }}</span>
              <span class="ii mono">{{ it.ip || '—' }}</span>
              <span class="itm mono">{{ fmtTime(it.detected_at) }}</span>
            </NuxtLink>
            <NuxtLink v-if="g.n > 3" :to="g.to" class="imore">還有 {{ n(g.n - 3) }} 筆，開啟完整清單 ›</NuxtLink>
          </div>
        </div>
        <div class="ifoot">
          <NuxtLink to="/issues" class="btn-ghost">開啟完整清單（{{ n(totalPending) }} 筆）›</NuxtLink>
          <button type="button" class="btn-ghost" :disabled="!totalPending" @click="exportIssuesCsv">匯出 CSV</button>
        </div>
      </section>

      <section v-if="comp?.data_quality" class="card clean">
        <div class="ck">資料乾淨度</div>
        <div class="cpct mono">{{ collectPct.toFixed(1) }}<small>%</small></div>
        <p class="csub">{{ n(comp.os_from_facts) }} / {{ n(comp.total) }} 台拿得到機器自報的事實</p>
        <div class="bar sm"><span class="seg" :style="{ width: collectPct + '%', background: 'var(--brand)' }" /></div>
        <NuxtLink v-for="q in [
                    { k: '待人工審核', v: comp.data_quality.pending_review, to: '/import', tone: 'warn', why: '判不準是否同一台，系統不自動合併' },
                    { k: '已由 vCenter 校正', v: comp.data_quality.verified_by_vcenter, to: { path: '/assets', query: { virtual: 'yes' } }, tone: 'good', why: `佔 ${comp.data_quality.verified_pct}%` },
                    { k: '重複登記（組）', v: comp.data_quality.duplicate_groups, to: { path: '/assets', query: { show: 'duplicates' } }, tone: 'warn', why: `清掉可少 ${n(comp.data_quality.duplicate_extra_rows)} 筆` },
                    { k: 'OS 仍未知', v: comp.data_quality.os_unknown, to: { path: '/assets', query: { filter_field: 'os', filter_value: '' } }, tone: 'warn', why: 'vCenter 涵蓋不到，要靠掃描或 SSH' },
                  ]" :key="q.k" :to="q.to" class="qrow">
          <span class="qk">{{ q.k }}<em>{{ q.why }}</em></span>
          <b class="mono" :class="q.tone">{{ n(q.v) }}</b>
          <span class="arw">›</span>
        </NuxtLink>
      </section>
    </div>
    </div>
    <IssuesList v-if="tab === 'issues'" />
  </div>
</template>

<style scoped>
.anom { color: var(--ink); }
.atabs { display: flex; gap: 8px; margin-bottom: 16px; }
.atab { background: transparent; border: 1px solid var(--border-strong); color: var(--ink-aux);
  border-radius: 999px; padding: 6px 16px; font-size: 13px; cursor: pointer; }
.atab:hover { border-color: var(--brand); }
.atab.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); font-weight: 600; }
.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 22px; align-items: start; }
@media (max-width: 1000px) { .two { grid-template-columns: 1fr; } }

.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 24px 26px; }
.ck { font-family: var(--disp); font-size: 10.5px; letter-spacing: 2px; color: var(--muted);
  margin-bottom: 12px; text-transform: uppercase; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--good); flex: none; }
.dot.warn { background: var(--warn); } .dot.bad { background: var(--bad); } .dot.muted { background: var(--border-strong); }
.arw { color: var(--muted); }

/* 結論帶 */
.verdict { display: grid; grid-template-columns: repeat(3, 1fr); background: var(--card);
  border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 20px 0; margin-bottom: 22px; }
.verdict .v { padding: 0 26px; border-left: 1px solid var(--line); }
.verdict .v:first-child { border-left: none; }
.vk { font-family: var(--disp); font-size: 10px; letter-spacing: 2px; color: var(--muted); margin-bottom: 8px; }
.vt { font-size: 14px; line-height: 1.65; margin: 0; color: var(--ink-soft); }
.vt b { font-family: var(--disp); font-size: 19px; font-weight: 600; letter-spacing: -.5px; font-variant-numeric: tabular-nums; }
.vt b.good { color: var(--brand-dark); } .vt b.warn { color: var(--warn-text); } .vt b.bad { color: var(--bad); }
.vlink { color: inherit; text-decoration: none; border-bottom: 1px solid var(--border-strong); }
.vlink:hover { border-bottom-color: var(--brand); color: var(--brand-dark); }

/* 體檢一句話 */
.hbar { margin-bottom: 22px; padding: 18px 26px; }
.hb-main { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.hb-num { text-decoration: none; color: var(--ink); font-size: 14px; }
.hb-num b { font-family: var(--disp); font-size: 26px; font-weight: 600; letter-spacing: -1px; color: var(--warn-text); margin-right: 4px; }
.hb-num:hover b { color: var(--bad); }
.hb-sep { color: var(--border-strong); }
.hb-ok { font-size: 14px; color: var(--ink-aux); }
.hb-ok b { font-family: var(--disp); font-size: 26px; font-weight: 600; letter-spacing: -1px; color: var(--brand-dark); margin-right: 4px; }
.hb-unverified { font-size: 11.5px; color: var(--muted); margin: 8px 0 0; }
.hb-detail { font-size: 12.5px; color: var(--muted); margin-left: auto; }
.hb-detail b { font-family: var(--disp); font-size: 15px; }
.hb-detail b.bad { color: var(--bad); } .hb-detail b.warn { color: var(--warn-text); }
.hb-issues { margin-top: 10px; font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hb-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 11px; border-radius: 20px;
  border: 1px solid var(--border); color: var(--ink-aux); text-decoration: none; font-size: 12px; }
.hb-chip:hover { border-color: var(--brand); color: var(--brand-dark); }

/* 待處理 */
.ihead { display: flex; align-items: center; justify-content: space-between; }
.ihead .ck { margin-bottom: 0; }
.ibadge { font-size: 18px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.ok { font-size: 13px; color: var(--muted); padding: 12px 0 0; }
.ig { border-top: 1px solid var(--line); }
.ig:first-of-type { margin-top: 12px; }
.igrow { display: flex; align-items: center; gap: 10px; width: 100%; padding: 12px 10px; margin: 0 -10px;
  background: none; border: none; cursor: pointer; text-align: left; font-family: inherit; border-radius: 8px; }
.igrow:hover { background: var(--sub); }
.ign { flex: 1; min-width: 0; font-size: 13px; color: var(--ink-soft); }
.ign em { display: block; font-style: normal; font-size: 11.5px; color: var(--muted); line-height: 1.45; }
.igrow b { font-family: var(--disp); font-size: 19px; font-weight: 600; font-variant-numeric: tabular-nums; }
.igrow b.bad { color: var(--bad); } .igrow b.warn { color: var(--warn-text); } .igrow b.good { color: var(--brand-dark); }
.ot { font-size: 11px; color: var(--muted); white-space: nowrap; }
.igbody { background: var(--sub); border-radius: 10px; padding: 6px 10px; margin-bottom: 10px; }
.iitem { display: flex; align-items: baseline; gap: 10px; padding: 7px 0; text-decoration: none;
  color: var(--ink-soft); font-size: 12.5px; border-top: 1px solid var(--line); }
.iitem:first-child { border-top: none; }
.iitem:hover { color: var(--brand-dark); }
.iitem .ih { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.iitem .ii { color: var(--muted); }
.iitem .itm { color: var(--muted); font-size: 11px; }
.imore { display: block; padding: 8px 0 4px; font-size: 12px; color: var(--brand-dark); text-decoration: none; }
.imore:hover { text-decoration: underline; }
.ifoot { display: flex; gap: 8px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }
.btn-ghost { flex: 1; font-family: inherit; font-size: 12px; padding: 9px 12px; border-radius: 10px;
  border: 1px solid var(--border-strong); background: transparent; color: var(--ink-aux);
  text-decoration: none; text-align: center; cursor: pointer; }
.btn-ghost:hover { border-color: var(--brand); color: var(--brand-dark); }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }

/* 資料乾淨度 */
.cpct { font-size: 40px; font-weight: 600; letter-spacing: -2px; line-height: 1; color: var(--brand-dark); }
.cpct small { font-size: 18px; letter-spacing: 0; margin-left: 2px; }
.csub { font-size: 12px; color: var(--muted); margin: 6px 0 10px; }
.bar.sm { height: 6px; background: var(--line); border-radius: 4px; overflow: hidden; margin-bottom: 6px; }
.bar.sm .seg { display: block; height: 100%; }
.qrow { display: flex; align-items: center; gap: 10px; padding: 11px 10px; margin: 0 -10px;
  border-top: 1px solid var(--line); text-decoration: none; color: inherit; border-radius: 8px; }
.qrow:hover { background: var(--sub); }
.qk { flex: 1; min-width: 0; font-size: 13px; color: var(--ink-soft); }
.qk em { display: block; font-style: normal; font-size: 11px; color: var(--muted); }
.qrow b { font-family: var(--disp); font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
.qrow b.good { color: var(--brand-dark); } .qrow b.warn { color: var(--warn-text); }
@media (max-width: 640px) { .verdict { grid-template-columns: 1fr; gap: 18px; } .verdict .v { border-left: none; } }
</style>
