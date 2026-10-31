<script setup lang="ts">
// 2.007.14 使用者：「打開首頁只要看到 V2 版，V1 不要了」→ 根網址直接轉到新版 1.1 今日概況。
// 舊版首頁不再顯示（下面程式保留，新版頁仍連得到的舊版功能頁不受影響）。
definePageMeta({ redirect: '/v2' })
// 首頁（總覽）。2026-10-26 使用者：數字太多、看起來灌水、互相對不起來 → 首頁瘦身，只留三件事：
//   1. 結論：頭條「在管資產（真的存在）」——口徑先不動（EXIST1），等對帳橋的分類看完再決定換不換
//   2. 今天要處理：設備風險（/risk）、每日變化（/changes）、進行中的事件（/incident），每個數字都點得進去
//   3. 異常警示：掃描範圍不完整、來源過期、連續消失、退役但仍在線…
// 「過程數字」（OS 來源 5,150／769／608、帳實相符與對帳明細、CIA 完整率／待補登／疑似下線、
// 平台其他／未知）沒有刪，搬到 7 資料品質 → 對帳橋的「掃描對帳」「資料完整度」分頁，意思不變。
// 以前寫「N 台實際收集」是錯的（那是 OS 欄有值的筆數），到新頁改叫「N 筆有填作業系統」。
interface DashboardStats {
  last_scan_time: string | null
  last_scan_ok: boolean
  failed_segments: string[]
}
interface IssueRow {
  id: number
  detected_at: string
  hostname: string | null
  ip: string | null
  issue_type: string
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const stats = ref<DashboardStats | null>(null)
const issues = ref<IssueRow[]>([])
const loading = ref(false)
const errorMessage = ref('')
const scanning = ref(false)
const scanElapsed = ref(0)
let pollTimer: ReturnType<typeof setInterval> | null = null

async function loadStats() {
  loading.value = true
  errorMessage.value = ''
  try {
    stats.value = await apiFetch<DashboardStats>('/api/dashboard/stats', { params: { environment: '全部' } })
  } catch {
    errorMessage.value = '儀表板資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
async function loadIssues() {
  try {
    issues.value = await apiFetch<IssueRow[]>('/api/issues', { params: { is_read: false } })
  } catch (e: any) {
    issues.value = []
    showToast(`問題清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
async function reloadAll() {
  await Promise.all([loadStats(), loadIssues()])
}
await reloadAll()

// 重新掃描：四態（Idle→Pending→Success/Error），背景跑、輪詢狀態、完成刷新。
// 一定要看到**新的一輪**（started_at 不同）才算數（2026-09-20 公司 198.14：讀到上一輪就報喜）。
function pollScan(startTs: number, prevStartedAt: string | null = null) {
  const TIMEOUT = 120000
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    scanElapsed.value = Math.round((Date.now() - startTs) / 1000)
    if (Date.now() - startTs > TIMEOUT) {
      if (pollTimer) clearInterval(pollTimer)
      scanning.value = false
      showToast('等了 2 分鐘還沒看到這一輪的掃描紀錄。它可能還在背景跑（網段多時會很久），'
                + '稍後重新整理、或到「系統設定 → 掃描排程」看第三關的段數', 'warn', 12000)
      return
    }
    let s: any
    try {
      s = await apiFetch<any>('/api/scan/status')
    } catch {
      return // 讀不到狀態就等下一輪再問；逾時那條會把結果講出來
    }
    if (s.running) return
    if (s.started_at && prevStartedAt && s.started_at === prevStartedAt) return
    if (!s.started_at) return
    if (pollTimer) clearInterval(pollTimer)
    scanning.value = false
    if (s.status === 'ok') {
      showToast(`已重新掃描，找到 ${s.found_count} 台活著的主機`, 'success')
      await reloadAll()
    } else if (s.status === 'failed') {
      showToast(`掃描失敗：${s.error ?? '未知原因，請稍後重試'}`, 'error')
    }
  }, 1500)
}

async function triggerRescan() {
  if (scanning.value) return
  scanning.value = true
  scanElapsed.value = 0
  let prevStartedAt: string | null = null
  try {
    prevStartedAt = (await apiFetch<any>('/api/scan/status'))?.started_at ?? null
  } catch { /* 讀不到就當沒有上一輪，後面照樣等新紀錄 */ }
  try {
    await apiFetch('/api/scan/run', { method: 'POST' })
    showToast('已開始掃描（背景跑，可以離開這頁）', 'info', 6000)
  } catch (err: any) {
    scanning.value = false
    const detail = err?.data?.detail
    showToast(detail ?? '啟動掃描失敗，請稍後重試', detail ? 'warn' : 'error')
    return
  }
  pollScan(Date.now(), prevStartedAt)
}
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
// 開頁時先問一次掃描狀態，只為了決定要不要顯示「掃描中」那顆轉圈
const _init = await apiFetch<any>('/api/scan/status').catch(() => null)
if (_init?.running) {
  scanning.value = true
  pollScan(Date.now())
}

function n(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : v.toLocaleString()
}

// ===== 1 結論：頭條（EXIST1，口徑不動）=====
interface Composition {
  registered_hosts?: number; registered_total?: number; total?: number
  existence?: {
    counted: number; total_machines: number
    stale_sources?: { source: string; label: string; age_days: number | null }[]
  }
}
const comp = ref<Composition | null>(null)
try {
  comp.value = await apiFetch<Composition>('/api/dashboard/composition')
} catch (e: any) {
  errorMessage.value = `頭條數字載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
}
const headline = computed(() =>
  comp.value?.existence?.counted ?? comp.value?.registered_hosts ?? comp.value?.registered_total ?? null)

// 納管四態（只拿數字）：異常警示用「退役但仍在線」「已納管但失聯」、掃描範圍紅燈用 scan_gap
interface ManageState {
  counts: Record<string, number>
  scan_gap?: { n: number; base: number; days: number; segments: number; probed: number }
}
const mstate = ref<ManageState | null>(null)
try {
  mstate.value = await apiFetch<ManageState>('/api/manage-state', { params: { summary: 1 } })
} catch (e: any) {
  console.warn('四態數字載入失敗，異常警示那兩列會顯示「—」', e)
}
// 掃描範圍不完整超過三成就亮紅燈（分母＝現役有登記有 IP 的台數，近 N 天合併涵蓋）
const scanGap = computed(() => {
  const g = mstate.value?.scan_gap
  if (!g || !g.base || !g.n) return null
  const pct = (g.n / g.base) * 100
  return pct >= 30 ? { n: g.n, total: g.base, pct: Math.round(pct), days: g.days, segs: g.segments, probed: g.probed } : null
})
function stateCount(k: string): number | null { return mstate.value ? (mstate.value.counts?.[k] ?? 0) : null }

// ===== 2 今天要處理：不擋首頁（風險清單要算一下），開頁後才抓 =====
interface TodoItem { no: string; label: string; n: number | null; tone: string; to: any }
const risk = ref<any>(null)
const changes = ref<any>(null)
const incidents = ref<any[] | null>(null)
const todoErr = ref<string[]>([])
onMounted(async () => {
  const errs: string[] = []
  await Promise.all([
    apiFetch<any>('/api/risk').then((r) => { risk.value = r }).catch((e: any) => { errs.push(`設備風險：${e?.data?.detail ?? e?.message ?? '載入失敗'}`) }),
    apiFetch<any>('/api/changes').then((r) => { changes.value = r }).catch((e: any) => { errs.push(`每日變化：${e?.data?.detail ?? e?.message ?? '載入失敗'}`) }),
    apiFetch<any[]>('/api/incidents').then((r) => { incidents.value = r }).catch((e: any) => { errs.push(`事件：${e?.data?.detail ?? e?.message ?? '載入失敗'}`) }),
  ])
  todoErr.value = errs
})
const todo = computed<TodoItem[]>(() => {
  const out: TodoItem[] = []
  // 與 v2 首頁同口徑：正式只算 mode==='formal'；未分類（歷史）獨立一項；演練不列入
  const open = (incidents.value ?? []).filter((x) => x.status === 'open' && x.mode === 'formal')
  const openUncls = (incidents.value ?? []).filter((x) => x.status === 'open' && x.mode !== 'formal' && x.mode !== 'drill')
  out.push({ no: '', label: '進行中的事件（正式）', n: incidents.value ? open.length : null, tone: 'bad',
             to: open.length === 1 ? { path: `/incident/${open[0].id}` } : { path: '/incident' } })
  if (openUncls.length) out.push({ no: '', label: '進行中的事件（未分類歷史）', n: openUncls.length, tone: 'warn', to: { path: '/incident' } })
  const r = risk.value
  out.push(
    { no: '', label: '硬體異常（台）', n: r ? r.hardware.count : null, tone: 'bad', to: { path: '/risk', query: { tab: 'hardware' } } },
    { no: '', label: '合約已過期（台）', n: r ? r.contract.counts.expired : null, tone: 'bad', to: { path: '/risk', query: { tab: 'contract' } } },
    { no: '', label: '合約 180 天內到期（台）', n: r ? r.contract.counts.d90 + r.contract.counts.d180 : null, tone: 'warn', to: { path: '/risk', query: { tab: 'contract' } } },
    { no: '', label: r ? `未重開機 ≥ ${r.uptime.threshold} 天（台）` : '未重開機過久（台）', n: r ? r.uptime.count : null, tone: 'warn', to: { path: '/risk', query: { tab: 'uptime' } } },
    { no: '', label: '單點（系統）', n: r ? r.spof.count : null, tone: 'warn', to: { path: '/risk', query: { tab: 'spof' } } },
  )
  const c = changes.value
  for (const cat of (c?.categories ?? []) as { key: string; label: string }[]) {
    const v = c.counts?.[cat.key] ?? 0
    if (v) out.push({ no: '', label: `今日變化：${cat.label}`, n: v, tone: 'warn', to: { path: '/changes', query: { tab: cat.key } } })
  }
  if (!c) out.push({ no: '', label: '今日變化', n: null, tone: 'warn', to: { path: '/changes' } })
  return out.map((x, i) => ({ ...x, no: `T${i + 1}` }))
})

// ===== 3 異常警示 =====
// 「連續消失」：同一台（主機名＋IP）出現在 2 個以上不同掃描日期＝連續多期都沒回來
function dayOf(t: string) { return (t || '').slice(0, 10) }
const alerts = computed<TodoItem[]>(() => {
  const lost = issues.value.filter((r) => r.issue_type === '異常消失')
  const days: Record<string, Set<string>> = {}
  for (const r of lost) (days[`${r.hostname ?? ''}|${r.ip ?? ''}`] ??= new Set()).add(dayOf(r.detected_at))
  const uniq = (rows: IssueRow[]) => new Set(rows.map((r) => `${r.hostname ?? ''}|${r.ip ?? ''}`)).size
  const repeat = lost.filter((r) => (days[`${r.hostname ?? ''}|${r.ip ?? ''}`]?.size ?? 0) >= 2)
  const first = lost.filter((r) => (days[`${r.hostname ?? ''}|${r.ip ?? ''}`]?.size ?? 0) < 2)
  const added = issues.value.filter((r) => r.issue_type === '異常新增')
  return [
    { no: 'A1', label: '連續消失（2 期以上，台）', n: uniq(repeat), tone: 'bad', to: { path: '/issues', query: { type: '異常消失' } } },
    { no: 'A2', label: '本期首次消失（台）', n: uniq(first), tone: 'warn', to: { path: '/issues', query: { type: '異常消失' } } },
    { no: 'A3', label: '異常新增（台）', n: uniq(added), tone: 'warn', to: { path: '/issues', query: { type: '異常新增' } } },
    { no: 'A4', label: '退役但仍在線（台）', n: stateCount('退役但仍在線'), tone: 'bad', to: { path: '/pipeline', query: { stage: 'retired_alive' } } },
    { no: 'A5', label: '已納管但失聯（台）', n: stateCount('已納管但失聯'), tone: 'bad', to: { path: '/manage-state-detail', query: { state: '已納管但失聯' } } },
  ]
})
</script>


<template>
  <div class="dash">
    <V2Banner to="/v2" label="1.1 今日概況" />
    <header class="top">
      <div class="tleft">
        <h1>資產盤點</h1>
        <span class="live"><i class="dot" :class="{ warn: stats && !stats.last_scan_ok }" />LIVE</span>
        <span v-if="stats" class="stamp mono">
          {{ stats.last_scan_time ?? '尚未執行過' }}
          <em>·</em>
          {{ stats.last_scan_ok ? '全網段完成' : `${stats.failed_segments.length} 個網段失敗` }}
        </span>
      </div>
      <div class="tright">
        <button class="btn-primary" :disabled="scanning" @click="triggerRescan">
          <span v-if="scanning" class="spin" />{{ scanning ? `掃描中…（${scanElapsed} 秒）` : '重新掃描' }}
        </button>
      </div>
    </header>

    <!-- 異常警示（橫幅）：數字不可信時要先講，不要讓人照著錯的數字去查機器 -->
    <NuxtLink v-if="scanGap" :to="{ path: '/manage-state-detail', query: { state: '未涵蓋' } }" class="scan-gap">
      <b>⚠ 掃描範圍不完整：{{ scanGap.n.toLocaleString() }} / {{ scanGap.total.toLocaleString() }} 台（{{ scanGap.pct }}%）近 {{ scanGap.days }} 天沒被掃過</b>
      <span>近 {{ scanGap.days }} 天：網路掃描 {{ scanGap.segs }} 個網段、2-1 開始收集探測 {{ scanGap.probed.toLocaleString() }} 個位址——看是哪幾台 →</span>
    </NuxtLink>
    <NuxtLink v-for="x in (comp?.existence?.stale_sources ?? [])" :key="x.source" to="/import" class="scan-gap">
      <b>⚠ {{ x.label }} 資料已 {{ x.age_days }} 天沒更新，請重匯</b>
      <span>台數照最新一次匯入算、不降級；重匯後「在管」才是現況 →</span>
    </NuxtLink>
    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <div v-if="stats || comp" class="grid">
      <!-- 1 結論 -->
      <section class="card concl">
        <div class="ck">結論
          <InfoNote>在管資產＝真的存在的機器（存在可信度達門檻；不含退役、不含疑似下線），CIA 有沒有登記都算（EXIST1）。
            這個口徑先不動：7 資料品質 → 對帳橋把每一筆登記分成實體設備／主機／管理口…，看完分類再決定頭條要不要換。
            帳實相符、OS 來源、CIA 完整率這些過程數字也搬到那裡。</InfoNote>
        </div>
        <NuxtLink :to="{ path: '/existence', query: { bucket: 'counted' } }" class="hl">
          <span class="hn mono">{{ n(headline) }}</span><small class="hu">台</small>
          <span class="hlab">在管資產（真的存在）</span>
        </NuxtLink>
        <NuxtLink to="/count-bridge" class="hlink">台數怎麼算、試算口徑 › 對帳橋</NuxtLink>
      </section>

      <!-- 2 今天要處理 -->
      <section class="card">
        <div class="ck">今天要處理</div>
        <p v-for="e in todoErr" :key="e" class="error-text">{{ e }}</p>
        <NuxtLink v-for="t in todo" :key="t.no" :to="t.to" class="rrow">
          <span class="no mono">{{ t.no }}</span><i class="dot" :class="t.n ? t.tone : 'muted'" />
          <span class="rn">{{ t.label }}</span>
          <b class="mono" :class="t.n ? t.tone : 'muted'">{{ n(t.n) }}</b><span class="arw">›</span>
        </NuxtLink>
      </section>

      <!-- 3 異常警示 -->
      <section class="card">
        <div class="ck">異常警示</div>
        <NuxtLink v-for="a in alerts" :key="a.no" :to="a.to" class="rrow">
          <span class="no mono">{{ a.no }}</span><i class="dot" :class="a.n ? a.tone : 'muted'" />
          <span class="rn">{{ a.label }}</span>
          <b class="mono" :class="a.n ? a.tone : 'muted'">{{ n(a.n) }}</b><span class="arw">›</span>
        </NuxtLink>
      </section>
    </div>
    <div v-else-if="loading" class="skel">
      <div class="sk h1" /><div class="sk big" />
    </div>
  </div>
</template>

<style scoped>
/* 顏色一律取自 main.css 的變數（全站視覺規範 §1：唯一顏色來源，不新增顏色）。 */
.dash { color: var(--ink); }
.top { display: flex; align-items: center; justify-content: space-between; gap: 20px;
  flex-wrap: wrap; margin-bottom: 22px; }
.tleft { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.tleft h1 { font-size: 32px; font-weight: 600; letter-spacing: -1px; margin: 0; color: var(--ink); }
.live { display: inline-flex; align-items: center; gap: 7px; font-family: var(--disp);
  font-size: 10.5px; letter-spacing: 2px; color: var(--muted); }
.stamp { font-size: 12.5px; color: var(--muted); }
.stamp em { font-style: normal; margin: 0 6px; color: var(--muted); }
.tright { display: flex; align-items: center; gap: 14px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }

.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--good); flex: none; }
.dot.warn { background: var(--warn); }
.dot.bad { background: var(--bad); }
.dot.muted { background: var(--border-strong); }
.live .dot { animation: beat 1.8s infinite; }
@keyframes beat { 0%,100% { opacity: 1 } 50% { opacity: .3 } }

.btn-primary { font-family: inherit; font-size: 13px; font-weight: 600; padding: 11px 20px;
  border: none; border-radius: 10px; background: var(--brand); color: #fff; cursor: pointer;
  display: inline-flex; align-items: center; gap: 8px; }
.btn-primary:hover { background: var(--brand-dark); }
.btn-primary:disabled { opacity: .6; cursor: not-allowed; }
.spin { width: 12px; height: 12px; border: 2px solid rgba(255,255,255,.45); border-top-color: #fff;
  border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.error-text { color: var(--bad); font-size: 13px; }

.grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1fr); gap: 22px; align-items: start; }
@media (max-width: 1280px) { .grid { grid-template-columns: 1fr; } }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 24px 26px; }
.ck { font-size: 13px; font-weight: 600; color: var(--ink-soft); margin-bottom: 12px;
  display: flex; align-items: center; gap: 6px; }

.concl { text-align: center; }
.hl { display: flex; flex-direction: column; align-items: center; text-decoration: none; color: inherit; padding: 18px 0 8px; }
.hn { font-size: 56px; font-weight: 600; letter-spacing: -3px; line-height: 1; color: var(--ink); }
.hu { font-size: 16px; color: var(--muted); margin-top: 4px; }
.hlab { font-size: 13px; color: var(--muted); margin-top: 6px; }
.hl:hover .hn { color: var(--brand-dark); }
.hlink { display: inline-block; margin-top: 10px; font-size: 12px; color: var(--brand-dark); text-decoration: none; }
.hlink:hover { text-decoration: underline; }

.rrow { display: flex; align-items: center; gap: 10px; padding: 10px 10px; margin: 0 -10px;
  border-top: 1px solid var(--line); text-decoration: none; color: inherit; border-radius: 8px; }
.rrow:first-of-type { border-top: none; }
.rrow:hover { background: var(--sub); }
.no { font-size: 11px; color: var(--muted); min-width: 26px; }
.rn { font-size: 13.5px; color: var(--ink-soft); flex: 1; min-width: 0; }
.rrow b { font-size: 19px; font-weight: 600; letter-spacing: -.5px; }
.rrow b.warn { color: var(--warn-text); } .rrow b.bad { color: var(--bad); } .rrow b.muted { color: var(--muted); }
.arw { color: var(--muted); font-size: 15px; }
.rrow:hover .arw { color: var(--brand-dark); }

.skel { display: flex; flex-direction: column; gap: 18px; }
.sk { background: var(--line); border-radius: 12px; }
.sk.h1 { height: 38px; width: 260px; }
.sk.big { height: 320px; }
.scan-gap { display: block; margin: 8px 0 12px; padding: 10px 14px; border-radius: 10px; background: var(--bad-soft);
  border: 1px solid var(--bad); color: var(--bad); text-decoration: none; }
.scan-gap b { display: block; font-size: 14px; }
.scan-gap span { font-size: 12px; }
</style>
