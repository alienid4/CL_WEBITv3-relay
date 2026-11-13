<script setup lang="ts">
// 6.1「開始收集」V2 原生（批 23，V17；原本嵌入舊頁 pages/adopt.vue only="collect"）。⚠D1 D2：純搬移。
// 端點、body、確認句、誰看得到哪顆按鈕一律照舊頁；載入時只讀（GET），不會自己開跑任何收集。
// ESXi／vCenter 天條全在後端（collect_dispatch／onboard_eligibility）：這裡只照後端給的狀態與原因顯示，不另判、不給納管入口。
import { addSegments, asCovOnly, covRows, dispatchGate, lastUnresolvedFail, pqPrune, pqToSend, runSumRows, STATUS_LABEL, targetSplit, type CovSeg, type OnboardAudit } from '~/utils/adoptCollect'

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }
const hhmm = (ts?: string | null) => (ts ? String(ts).slice(11, 16) : '')
const mmdd = (ts?: string | null) => (ts ? String(ts).slice(5, 10).replace('-', '/') : '')

// ===== 範圍輸入 =====
const targets = ref('')
const sp = computed(() => targetSplit(targets.value))
const targetCount = computed(() => sp.value.ranges + sp.value.singles)

// ===== 從網段配置表挑（先環境、再機房、最後勾網段；建議排除的照列，要自己打開才看得到）=====
interface SegItem { cidr: string; label: string; scan_excluded: boolean; scan_note: string | null }
interface SegLocNode { location: string; environments: { environment: string; segments: SegItem[] }[] }
const segTree = ref<SegLocNode[]>([])
const segEnv = ref('測試')
const segLoc = ref('')
const segSel = ref<Record<string, boolean>>({})
const segShowExcluded = ref(false)
async function loadSegTree() {
  try {
    const r = await apiFetch<{ tree: SegLocNode[] }>('/api/segments/tree')
    segTree.value = r.tree ?? []
    if (!segEnvs.value.includes(segEnv.value)) segEnv.value = segEnvs.value[0] ?? ''
  } catch { segTree.value = [] }   // 還沒匯網段配置表就不顯示這一區，不擋整頁（同舊頁）
}
const segEnvs = computed(() => {
  const s = new Set<string>()
  for (const l of segTree.value) for (const e of l.environments) s.add(e.environment)
  const order = ['測試', '正式', '備援']
  return [...order.filter(x => s.has(x)), ...[...s].filter(x => !order.includes(x))]
})
const segLocs = computed(() => segTree.value.filter(l => l.environments.some(e => e.environment === segEnv.value)).map(l => l.location))
const segList = computed<SegItem[]>(() => segTree.value.find(l => l.location === segLoc.value)?.environments.find(e => e.environment === segEnv.value)?.segments ?? [])
const segVisible = computed(() => (segShowExcluded.value ? segList.value : segList.value.filter(s => !s.scan_excluded)))
const segExcludedCount = computed(() => segList.value.filter(s => s.scan_excluded).length)
watch([segEnv, segLocs], () => { if (!segLocs.value.includes(segLoc.value)) segLoc.value = segLocs.value[0] ?? '' })
const segPickedList = computed(() => Object.keys(segSel.value).filter(k => segSel.value[k]))
const segRows = computed(() => segVisible.value.map(s => ({ ...s, key: s.cidr, use: s.label.replace(s.cidr, '').trim(),
  cov: covByCidr.value[s.cidr] ? (covByCidr.value[s.cidr].never ? '從未收集' : `上次收集 ${mmdd(covByCidr.value[s.cidr].last_at)}`) : '',
  ex: s.scan_excluded ? `建議排除：${s.scan_note || '建議排除掃描'}` : '' })))
function addSelectedSegs() {
  if (!segPickedList.value.length) { showToast('先勾要收集的網段', 'warn'); return }
  const r = addSegments(targets.value, segPickedList.value)
  targets.value = r.text
  showToast(`已加入 ${r.added} 個網段`, 'success')
  segSel.value = {}
}

// ===== 批次設定、每週整片收集（只讀）=====
const batchInfo = ref<{ gap_seconds: number; batch_size: number; hard_max: number; skip_fresh_hours?: number; skip_empty_hours?: number; registered_count?: number } | null>(null)
async function loadBatchInfo() { try { batchInfo.value = await apiFetch('/api/collect/dispatch/gap') } catch { /* 舊版後端沒有這支（同舊頁）：不顯示批次說明 */ } }
const weeklyNoRange = ref(false)
async function loadWeeklyRange() {
  try { const w = await apiFetch<any>('/api/collect/dispatch/weekly'); weeklyNoRange.value = !!w?.range_missing }
  catch (e: any) { showToast(`每週整片收集狀態讀取失敗：${errMsg(e)}`, 'warn') }
}
const dFull = ref(false)
const dRegOnly = ref(true)

// ===== 這一輪的狀態（心跳由後端 run_liveness 算）=====
const dispatching = ref(false)
const progress = ref<{ batch_done: number; batch_total: number; done_count: number; total: number } | null>(null)
const runInfo = ref<any>(null)
const staleRun = ref<any>(null)
const canAdmin = ref(false)
const dElapsed = ref(0)
let pollTimer: ReturnType<typeof setInterval> | null = null
let dTicker: ReturnType<typeof setInterval> | null = null
let syncTimer: ReturnType<typeof setInterval> | null = null
// 離開頁面後不再開新的計時器（批 23 審查 P2：await 回來時元件可能已經卸載）
let unmounted = false
function every(fn: () => void, ms: number): ReturnType<typeof setInterval> | null { return unmounted ? null : setInterval(fn, ms) }
function applyRun(r: any) {
  const run = r?.run
  runInfo.value = run ?? null
  if (r && 'can_admin' in r) canAdmin.value = !!r.can_admin
  staleRun.value = run?.status === 'running' && run?.liveness === 'stale' ? run : null
}
const busyCore = computed(() => {
  const pg = progress.value
  const done = pg?.done_count ?? 0
  const tot = pg?.total || targetCount.value || 0
  if (pg && pg.batch_total > 1) {
    const nb = runInfo.value?.next_batch_at
    return `收集中：第 ${Math.min(pg.batch_done + 1, pg.batch_total)}/${pg.batch_total} 批・${done}/${tot} 台` + (nb ? `，下一批 ${hhmm(nb)}` : '')
  }
  return `收集中：${done}/${tot} 台・${dElapsed.value} 秒`
})
const runScope = computed(() => {
  const r = runInfo.value
  if (!r?.target_count && !dispatching.value) return ''
  const n = r?.target_count ?? targetCount.value
  const raw = String(r?.targets_raw ?? targets.value ?? '').replace(/\s+/g, ' ').trim()
  return `${dispatching.value ? '這次' : '上次'}範圍：${n} 個位址（${raw.length > 80 ? raw.slice(0, 80) + '…' : raw}）`
})
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }
const logRef = ref<{ reload: () => void } | null>(null)

// ===== 收完的結果卡（這個畫面跑完的那一輪）=====
const runSum = ref<any>(null)
const runSumOpen = ref('')
async function loadRunSummary(runId?: number | null) {
  if (!runId) return
  try { runSum.value = await apiFetch(`/api/collect/dispatch/${runId}/summary`); runSumOpen.value = runSum.value.total <= 5 ? '*' : '' }
  catch (e: any) { showToast(`這一輪的結果讀不到：${errMsg(e)}`, 'error') }
}
const runSumShown = computed(() => runSumRows(runSum.value, runSumOpen.value).map((r: any) => ({ ...r, __no: r.no, key: r.ip, msg: String(r.message || '').slice(0, 160) })))

// 背景那一輪跑完：撈結果卡、涵蓋率、紀錄（舊頁 pollUntilDone 同一套；只讀）
async function pollUntilDone() {
  const pr = await apiFetch<any>('/api/collect/dispatch/progress')
  const prun = pr?.run
  pollErr.value = ''
  if (!prun) return
  applyRun(pr)
  progress.value = { batch_done: prun.batch_done ?? 0, batch_total: prun.batch_total ?? 1, done_count: prun.done_count ?? 0, total: prun.target_count ?? 0 }
  if (prun.status === 'running' && prun.liveness !== 'stale') return
  const r = await apiFetch<any>('/api/collect/dispatch/latest')
  const run = r?.run ?? prun
  stopPolling()
  dispatching.value = false
  if (dTicker) { clearInterval(dTicker); dTicker = null }
  loadCoverage()
  if (run.status === 'running') showToast(`這一輪超過 ${run.stale_minutes} 分鐘沒有進度，可能已中斷`, 'warn', 12000)
  else if (run.status === 'stopped' || run.status === 'interrupted') showToast(`${run.error || '已停止'}；已收到的結果都留著`, 'warn', 12000)
  else if (run.status === 'failed') { dispatchError.value = `收集沒有完成：${run.error || '後端沒有留下原因'}`; showToast('收集失敗，看下方「開始收集紀錄」', 'error', 12000) }
  await finishRun(run.id)
  logRef.value?.reload()
}
const dispatchError = ref('')
async function finishRun(runId?: number | null) {
  await loadRunSummary(runId)
  if (runSum.value) {
    const c = runSum.value.counts
    const parts = runSum.value.buckets.map((b: any) => `${b.label} ${c[b.key] ?? 0}`)
    showToast(`完成：${runSum.value.total} 台 — ${parts.join('・')}`, c.failed || c.needs_credential ? 'warn' : 'success', 12000)
  }
}
// 進度問不到：照舊頁繼續每 5 秒問（不停掉、不改狀態），但把原因留在畫面上（舊頁是靜默吞掉）
const pollErr = ref('')
function pollFail(e: any) { pollErr.value = `進度暫時查不到（會繼續問）：${errMsg(e)}` }

// ===== 第 2 片（⚠D2）：開始收集、批次間隔、載入上次結果、停止、標記已中斷——端點、body、確認句照舊頁 =====
const dispatchNotice = ref('')
watch(targets, () => { dispatchNotice.value = '' })   // 只清提示字，不送任何東西（同舊頁）
const stopBusy = ref(false)
async function saveGap(minutes: number) {
  try {
    await apiFetch('/api/collect/dispatch/gap', { method: 'PUT', body: { gap_seconds: Math.round(minutes * 60) } })
    await loadBatchInfo()
    showToast(`批次間隔已改成 ${minutes} 分鐘`, 'success')
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') }
}
async function reloadLatest() {
  try {
    const r = await apiFetch<any>('/api/collect/dispatch/latest')
    applyRun(r)
    dispatchError.value = ''
    showToast('已載入上次的收集結果', 'success')
  } catch (e: any) { showToast(`載入失敗：${errMsg(e)}`, 'error') }
}
async function runDispatch() {
  if (dispatching.value) return
  dispatchError.value = ''
  dispatchNotice.value = ''
  const g = dispatchGate({ targets: targets.value, segPicked: segPickedList.value.length, regOnly: dRegOnly.value,
    batchSize: batchInfo.value?.batch_size, gapSeconds: batchInfo.value?.gap_seconds })
  if (g.kind === 'warn') { showToast(g.text, 'warn', 10000); return }
  if (g.kind === 'confirm' && !confirm(g.text)) return
  dispatching.value = true
  dElapsed.value = 0
  pollErr.value = ''
  dTicker = every(() => { dElapsed.value += 1 }, 1000)
  showToast('開始收集：探測中，連得上的會直接進去收，可能要一段時間', 'info')
  runSum.value = null
  progress.value = null
  // 同步的小收集也逐台看得到進度（POST 還沒回來時每 2 秒問一次輕量進度；只讀）
  syncTimer = every(async () => {
    try {
      const pr = await apiFetch<any>('/api/collect/dispatch/progress')
      const run = pr?.run
      if (run?.status === 'running') {
        applyRun(pr)
        progress.value = { batch_done: run.batch_done ?? 0, batch_total: run.batch_total ?? 1, done_count: run.done_count ?? 0, total: run.target_count ?? 0 }
      }
    } catch (e: any) { pollFail(e) }
  }, 2000)
  const stopSync = () => { if (syncTimer) { clearInterval(syncTimer); syncTimer = null } }
  try {
    const out = await apiFetch<any>('/api/collect/dispatch', {
      method: 'POST', body: { targets: targets.value, full: dFull.value, registered_only: dRegOnly.value },
    })
    stopSync()
    if (out?.started) {
      staleRun.value = null
      progress.value = { batch_done: 0, batch_total: out.batch_total, done_count: 0, total: out.total }
      showToast(`${out.total} 台已自動分成 ${out.batch_total} 批（每批最多 ${out.batch_size} 台、`
        + `間隔 ${Math.round(out.gap_seconds / 60)} 分鐘），背景執行中`, 'info', 14000)
      pollTimer = every(() => { pollUntilDone().catch(pollFail) }, 5000)
      return
    }
    loadCoverage()
    const d = out.this_run ?? out
    dispatching.value = false
    if (dTicker) { clearInterval(dTicker); dTicker = null }
    applyRun(out)
    await finishRun(d.run_id)
    logRef.value?.reload()
  } catch (e: any) {
    stopSync()
    const det = e?.data?.detail
    dispatching.value = false
    if (dTicker) { clearInterval(dTicker); dTicker = null }
    if (det && typeof det === 'object' && det.code === 'no_registered_in_range') { dispatchNotice.value = det.message; return }
    const why = (typeof det === 'string' ? det : det?.message) || e?.message || '沒有回應'
    dispatchError.value = `收集沒有完成（跑了 ${dElapsed.value} 秒）：${why}`
    showToast(`收集失敗：${why}`, 'error')
    logRef.value?.reload()
  }
}
function retryUnfiltered() { dRegOnly.value = false; runDispatch() }
async function stopDispatch() {
  const run = runInfo.value
  if (!run?.id || stopBusy.value) return
  if (!confirm(`停止這一輪收集？\n\n・目前那一台收完就停（最多等目前那台約 3 分鐘），剩下的台與批都不跑。\n・已經收到的結果都會留著。`)) return
  stopBusy.value = true
  try {
    await apiFetch(`/api/collect/dispatch/${run.id}/stop`, { method: 'POST' })
    runInfo.value = { ...run, stop_requested: true }
    showToast('已要求停止：目前那一台收完就停（最多約 3 分鐘）', 'info', 8000)
  } catch (e: any) { showToast(`停止失敗：${errMsg(e)}`, 'error', 10000) } finally { stopBusy.value = false }
}
async function markInterrupted() {
  const run = staleRun.value
  if (!run?.id) return
  if (!confirm(`把這一輪標記為已中斷？\n\n最後進度 ${run.last_progress_at}，第 ${run.batch_done}/${run.batch_total} 批。已收到的結果會留著。`)) return
  try {
    await apiFetch(`/api/collect/dispatch/${run.id}/mark-interrupted`, { method: 'POST' })
    staleRun.value = null
    showToast('已標記為已中斷', 'success')
    logRef.value?.reload()
    reloadLatest()
  } catch (e: any) { showToast(`標記失敗：${errMsg(e)}`, 'error', 10000) }
}

// ===== 納管後收集排隊（第 3 片 ⚠D1：取消、重新排入——端點、body、確認句照舊頁；按鈕只在後端回 can_admin 時出現）=====
const pq = ref<any>(null)
let pqTimer: ReturnType<typeof setInterval> | null = null
const pqPanelOpen = ref(false)
async function loadPostQueue() {
  try { pq.value = await apiFetch('/api/onboard/post-collect-queue') }
  catch (e: any) { showToast(`收集進度查不到：${errMsg(e)}`, 'error') }
  if (pq.value?.running && !pqTimer) pqTimer = every(() => { loadPostQueue() }, 3000)
  if (!pq.value?.running && pqTimer) { clearInterval(pqTimer); pqTimer = null; logRef.value?.reload?.() }
}
const pqItems = computed<any[]>(() => pq.value?.items ?? [])
const pqSel = ref<Record<string, boolean>>({})
// 隊伍刷新（每 3 秒）後，已經離開隊伍的勾選自動丟掉：送出去的＝畫面上還在、而且勾了的（比舊頁收緊）
watch(pqItems, (items) => { pqSel.value = pqPrune(pqSel.value, items) })
const pqPicked = computed(() => pqToSend(pqSel.value, pqItems.value))
async function pqCancel(all: boolean) {
  const list = all ? null : pqPicked.value
  const n = all ? pqItems.value.length : list!.length
  if (!n) { showToast('先勾要取消的主機', 'warn'); return }
  if (!confirm(`取消 ${n} 台的納管後收集？\n\n・排隊中的直接移出，這次不收。\n・正在收的那台（不論是從哪個入口觸發的）：目前這一樣收完就停。\n・每一台都會在紀錄裡留「已取消」。`)) return
  try {
    pq.value = await apiFetch('/api/onboard/post-collect-queue/cancel', { method: 'POST', body: { asset_serials: list } })
    pqSel.value = {}
    showToast(`已取消 ${n} 台`, 'success')
    loadPostQueue()
    logRef.value?.reload()
  } catch (e: any) { showToast(`取消失敗：${errMsg(e)}`, 'error', 10000) }
}
async function pqRequeueLost() {
  try {
    const r = await apiFetch<any>('/api/onboard/post-collect-queue/requeue-lost', { method: 'POST' })
    showToast(`已重新排入 ${r.added} 台`, 'success')
    loadPostQueue()
  } catch (e: any) { showToast(`重新排入失敗：${errMsg(e)}`, 'error', 10000) }
}
const pqRows = computed(() => pqItems.value.map((it: any) => ({ ...it, __no: it.no, key: it.asset_serial, name: it.hostname || it.asset_serial,
  state_text: it.state === 'running' ? `正在收${it.job_label ? '：' + it.job_label : ''}${it.cancel_requested ? '（取消中）' : ''}` : '排隊中' })))

// ===== 收集涵蓋率（全部已知網段；網址 cvo＝篩、cvs＝看哪一段）=====
const cov = ref<{ segments: CovSeg[]; summary: any } | null>(null)
const covErr = ref('')
const covOpen = ref(false)
async function loadCoverage() {
  try { cov.value = await apiFetch('/api/collect/dispatch/coverage'); covErr.value = '' }
  catch (e: any) { covErr.value = `涵蓋率讀不到：${errMsg(e)}` }
}
const covByCidr = computed<Record<string, CovSeg>>(() => Object.fromEntries((cov.value?.segments ?? []).map(s => [s.cidr, s])))
const covOnly = computed(() => asCovOnly(q('cvo')))
const covShown = computed(() => covRows(cov.value?.segments ?? [], covOnly.value).map((s: any) => ({ ...s, __no: s.no, key: s.cidr,
  where: s.in_table ? '' : '配置表沒有', got: `${s.collected}／${s.total}`, last: s.never ? '從未收集' : s.last_at })))
const covFilters = computed<[string, string, number][]>(() => {
  const m = cov.value?.summary ?? {}
  return [['', '全部', m.segments ?? 0], ['never', '從未收集', m.never ?? 0], ['partial', '部分', m.partial ?? 0], ['full', '全收', m.full ?? 0]]
})
const covSeg = ref<any>(null)
const covSegBusy = ref(false)
const covSegLink = (r: any) => `/v2/onboard?${new URLSearchParams({ ...Object.fromEntries(Object.entries(route.query).filter(([, v]) => typeof v === 'string')) as Record<string, string>, tab: 'collect', cvs: r.cidr }).toString()}`
async function openCovSeg(cidr: string) {
  if (!cidr) { covSeg.value = null; return }
  covSegBusy.value = true
  try { covSeg.value = await apiFetch('/api/collect/dispatch/coverage/segment', { query: { cidr } }) }
  catch (e: any) { showToast(`網段明細讀不到：${errMsg(e)}`, 'error') }
  finally { covSegBusy.value = false }
}
watch(() => q('cvs'), (c) => { if (c) covOpen.value = true; openCovSeg(c) })
const covItems = computed(() => (covSeg.value?.items ?? []).map((it: any) => ({ ...it, __no: it.no, key: it.ip,
  st: it.never ? '' : (STATUS_LABEL[it.status] || it.status), at: it.never ? '從未收集' : it.collected_at })))

// ===== 納管紀錄（只有帳號名與成敗，永遠不含密碼）=====
const audits = ref<(OnboardAudit & Record<string, any>)[]>([])
const showAudit = ref(false)
const openOutput = ref<number | null>(null)
async function loadAudit() { try { audits.value = await apiFetch('/api/onboard/audit') } catch { /* 沒紀錄就空著（同舊頁）：這一區整塊不顯示 */ } }
const lastFail = computed(() => lastUnresolvedFail(audits.value))
const aOut = (r: any) => r.output as string | null
const aId = (r: any) => r.id as number
function toggleOut(r: any) { openOutput.value = openOutput.value === r.id ? null : r.id }
const auditRows = computed(() => audits.value.map((a: any, i: number) => ({ ...a, __no: i + 1, key: String(a.id),
  who: a.trigger === 'auto' ? '排程' : (a.triggered_by || '手動'), res: a.ok ? '成功' : '失敗' })))

// 載入：全部只讀（同舊頁 onMounted 那幾支）；後端還在跑的那一輪就接著問進度（GET）
onMounted(async () => {
  loadSegTree(); loadBatchInfo(); loadWeeklyRange(); loadCoverage(); loadAudit(); loadPostQueue()
  if (q('cvs')) { covOpen.value = true; openCovSeg(q('cvs')) }
  try {
    const r = await apiFetch<any>('/api/collect/dispatch/latest')
    applyRun(r)
    if (r?.run?.status === 'running' && r.run.liveness !== 'stale') {
      dispatching.value = true
      progress.value = { batch_done: r.run.batch_done ?? 0, batch_total: r.run.batch_total ?? 1, done_count: r.run.done_count ?? 0, total: r.run.target_count ?? 0 }
      pollTimer = every(() => { pollUntilDone().catch(pollFail) }, 5000)
    }
  } catch { /* 沒跑過就沒有上一輪（同舊頁）：畫面不顯示範圍與進度 */ }
})
onBeforeUnmount(() => {
  unmounted = true
  stopPolling()
  for (const t of [dTicker, syncTimer, pqTimer]) if (t) clearInterval(t)
  dTicker = syncTimer = pqTimer = null
})
</script>

<template>
<div>
  <p v-if="weeklyNoRange" class="err" data-test="weekly-range-missing">每週整片收集還沒有範圍：先手動跑一次整片網段，或在<NuxtLink to="/v2/settings?st=identity&sub=schedule">系統設定填範圍</NuxtLink></p>

  <!-- 範圍 -->
  <section class="card">
    <div class="bar">
      <b>開始收集</b>
      <InfoNote>輸入要納進來的範圍，按一下就好——不用自己判斷該走哪一條。系統會逐台探測，能走 SSH 的走 SSH、只有 Windows 管道的走 WinRM、進不去但活著的先判斷是什麼機器（Linux／Unix 產 Push Agent 安裝包、Windows 請開 WinRM、AIX 請開 SSH）、完全沒回應的列進「只能靠匯入」。ESXi／vCenter 一律不納管、不建帳號，結果寫「另有納管方式」。<br><br>已經有資料在 Excel／RVTools 裡的走 <NuxtLink to="/v2/settings?st=import">⚙ 資料匯入</NuxtLink>，那是既有資料進場，不是收集。</InfoNote>
    </div>
    <div v-if="segTree.length" class="bar">
      <b>從網段配置表挑</b>
      <span class="muted">環境</span>
      <button v-for="e in segEnvs" :key="e" type="button" class="chip" :class="{ on: segEnv === e }" @click="segEnv = e">{{ e }}</button>
      <span class="muted">機房</span>
      <select v-model="segLoc" class="fsel" aria-label="機房"><option v-for="l in segLocs" :key="l" :value="l">{{ l }}</option></select>
      <label class="muted"><input v-model="segShowExcluded" type="checkbox"> 含建議排除（{{ segExcludedCount }}）</label>
      <button type="button" class="btn2" :disabled="!segPickedList.length" @click="addSelectedSegs">加入範圍（{{ segPickedList.length }} 段）</button>
      <InfoNote>網段配置表裡有機房、環境、用途，所以可以先選「測試」再選機房。勾好按「加入範圍」會填進下面的輸入框，你還可以自己增減，確認台數後再按「開始收集」。資安註記「建議排除掃描」的網段預設不列，打開「含建議排除」才看得到，並寫明原因。</InfoNote>
    </div>
    <V2Table v-if="segTree.length && segRows.length" v-model:selected="segSel" :rows="segRows" prefix="N" row-key="cidr" selectable header-clears-all :max="60" empty="這個環境沒有登記的網段"
             :cols="[{ k: 'cidr', label: '網段', mono: true }, { k: 'use', label: '用途' }, { k: 'cov', label: '收集' }, { k: 'ex', label: '註記' }]" />
    <textarea v-model="targets" class="targets" :disabled="dispatching" aria-label="收集範圍" placeholder="每行一項，可混用：&#10;10.99.1.0/24&#10;10.99.2.10-20&#10;10.99.3.5" />
    <div class="bar">
      <button type="button" class="btn" :disabled="dispatching || !targets.trim()" data-test="dispatch-start" @click="runDispatch">{{ dispatching ? busyCore : '開始收集' }}</button>
      <button v-if="dispatching && canAdmin && runInfo?.id && runInfo?.status === 'running'" type="button" class="btn2" data-test="dispatch-stop" :disabled="stopBusy || runInfo?.stop_requested" @click="stopDispatch">{{ runInfo?.stop_requested ? '停止中…' : '停止收集' }}</button>
      <label class="muted"><input v-model="dRegOnly" type="checkbox" :disabled="dispatching"> 只掃已登記的 IP<template v-if="batchInfo?.registered_count != null">（{{ batchInfo.registered_count }} 個）</template></label>
      <InfoNote>勾了＝網段／範圍裡只探資產庫裡現役資產的 IP（退役不算）。單一 IP 不受限制——直接打的 IP 一律照收。框是空的不會開跑（空白不代表全部）。要發現網段裡沒登記的新機器，不要勾（至少一個 /24 的整片範圍會記成每週六晚上自動跑的範圍）。</InfoNote>
      <label class="muted"><input v-model="dFull" type="checkbox" :disabled="dispatching"> 全部重收</label>
      <InfoNote>不勾＝只收新的：上次已納管、而且在 {{ batchInfo?.skip_fresh_hours ?? 24 }} 小時內的位址，以及空位址在 {{ batchInfo?.skip_empty_hours ?? 168 }} 小時內的，這次不重探。其餘一律重探；只輸入一個位址時一定重探。勾了＝每個位址都重探一次。時間窗、一批幾台、批次間隔在 ⚙ 系統設定 → 掃描排程。</InfoNote>
      <span v-if="batchInfo && !dispatching" class="muted">超過 {{ batchInfo.batch_size }} 台自動分批，每批間隔 <input type="number" min="0" max="60" step="0.5" class="gapin" aria-label="批次間隔（分鐘）" :value="batchInfo.gap_seconds / 60" @change="saveGap(Number(($event.target as HTMLInputElement).value))"> 分鐘</span>
      <span v-if="targetCount && !dispatching" class="muted">目前輸入約 <b>{{ targetCount }}</b> 台<template v-if="dRegOnly && sp.ranges">（網段 {{ sp.ranges }} 個位址只掃有登記的<template v-if="sp.singles">＋單一 IP {{ sp.singles }} 個照收</template>）</template></span>
    </div>
    <div v-if="dispatchError && !dispatching" class="bar">
      <b class="err">✕ {{ dispatchError }}</b>
      <button type="button" class="btn2" @click="reloadLatest">載入上次結果</button>
      <InfoNote>常見原因：範圍太大、網頁等太久被中斷（後端可能還在跑，跑完會存下來）；或後端出錯。先按「載入上次結果」看有沒有存到；還是不清楚，打開下方「開始收集紀錄」看細節或下載分析檔。</InfoNote>
    </div>
    <div v-if="dispatchNotice && !dispatching" class="bar" data-test="dispatch-notice">
      <b>ⓘ 沒有開跑：{{ dispatchNotice }}</b>
      <button type="button" class="btn2" data-test="dispatch-unfilter" @click="retryUnfiltered">取消「只掃已登記」再收一次</button>
    </div>
    <p v-if="runScope" class="muted" data-test="run-scope">{{ runScope }}</p>
    <div v-if="dispatching" class="bar" data-test="dispatch-progress">
      <b>{{ busyCore }}</b>
      <span v-if="pollErr" class="err">{{ pollErr }}</span>
      <InfoNote>沒回應的每台約 5 秒（要等它逾時），同時跑 64 台。做完會存下來，中途關掉頁面也不會白跑，回來還看得到結果。</InfoNote>
    </div>
    <div v-if="staleRun && !dispatching" class="bar" data-test="dispatch-stale">
      <b class="err">⚠ 可能已中斷：最後進度 {{ hhmm(staleRun.last_progress_at) }}，第 {{ staleRun.batch_done }}/{{ staleRun.batch_total }} 批</b>
      <InfoNote>這一輪（{{ staleRun.started_at }} 開始）超過 {{ staleRun.stale_minutes }} 分鐘沒有任何進度。常見原因是服務重啟把背景收集停掉了。已收到的結果都留著；確認不會再動就標記為已中斷，再重新開始收集。</InfoNote>
      <button v-if="canAdmin" type="button" class="btn2" data-test="dispatch-mark-interrupted" @click="markInterrupted">標記為已中斷</button>
    </div>
  </section>

  <!-- 這一輪的結果卡 -->
  <section v-if="runSum && !dispatching" class="card" data-test="run-summary">
    <div class="bar">
      <b>完成：{{ runSum.total }} 台</b>
      <button type="button" class="chip" :class="{ on: runSumOpen === '*' }" @click="runSumOpen = runSumOpen === '*' ? '' : '*'">全部 {{ runSum.total }}</button>
      <button v-for="b in runSum.buckets" :key="b.key" type="button" class="chip" :class="{ on: runSumOpen === b.key }" @click="runSumOpen = runSumOpen === b.key ? '' : b.key">{{ b.label }} {{ runSum.counts[b.key] }}</button>
      <InfoNote>這一輪自己的結果（不是合併檢視）。結果與原因都是後端給的原文；ESXi／vCenter 在「其他」堆裡，結果寫「另有納管方式」，這裡沒有納管入口。</InfoNote>
    </div>
    <V2Table v-if="runSumOpen" :rows="runSumShown" prefix="R" row-key="ip" empty="這一堆沒有"
             :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'hostname', label: '主機名稱' }, { k: 'status_label', label: '結果' }, { k: 'msg', label: '原因' }]" />
  </section>

  <!-- 納管後收集排隊 -->
  <section v-if="pq && (pqItems.length || pq.lost?.length)" class="card" data-test="pq-panel">
    <div class="bar">
      <b v-if="pq.lost?.length" class="err">服務重啟：{{ pq.lost.length }} 台排隊被清掉，沒有收到</b>
      <button v-if="pq.lost?.length" type="button" class="btn2" data-test="pq-requeue" @click="pqRequeueLost">重新排入</button>
      <b v-if="pqItems.length">納管後收集：{{ pqItems.length }} 台（正在收 {{ pqItems.filter((i: any) => i.state === 'running').length }}、排隊 {{ pqItems.filter((i: any) => i.state === 'queued').length }}）</b>
      <button v-if="pqItems.length" type="button" class="btn2" @click="pqPanelOpen = !pqPanelOpen">{{ pqPanelOpen ? '收合' : '看是哪幾台' }}</button>
      <template v-if="pqPanelOpen && pq.can_admin">
        <button type="button" class="btn2" :disabled="!pqPicked.length" data-test="pq-cancel-sel" @click="pqCancel(false)">取消勾選的（{{ pqPicked.length }}）</button>
        <button type="button" class="btn2" data-test="pq-cancel-all" @click="pqCancel(true)">全部取消排隊</button>
      </template>
    </div>
    <V2Table v-if="pqPanelOpen && pqItems.length" v-model:selected="pqSel" :rows="pqRows" prefix="Q" row-key="asset_serial" selectable header-clears-all empty="沒有排隊"
             :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'name', label: '主機名稱' }, { k: 'source_label', label: '來源' }, { k: 'state_text', label: '狀態' }, { k: 'since', label: '排入時間', mono: true }]" />
  </section>

  <CollectLog ref="logRef" :kinds="['dispatch', 'post_onboard', 'post_onboard_queue']" title="開始收集／納管後自動收集紀錄" />

  <!-- 收集涵蓋率 -->
  <section class="card" data-test="coverage">
    <div class="bar">
      <button type="button" class="btn2" @click="covOpen = !covOpen">{{ covOpen ? '▾' : '▸' }} 全部已知網段的收集涵蓋率</button>
      <span v-if="cov?.summary" class="muted">{{ cov.summary.segments }} 個網段，從未收集 {{ cov.summary.never }}、部分 {{ cov.summary.partial }}、全收 {{ cov.summary.full }}</span>
      <InfoNote>不是這次的範圍。每個位址看它最新一次的收集結果（跟待納管表同一份）。「已收集」＝任何一輪探測過這個位址（沒回應的空位址也算收過）；「從未收集」＝沒有任何一輪探測過。網段來自網段配置表，另加收過但配置表沒有的 /24。</InfoNote>
    </div>
    <p v-if="covErr" class="err">{{ covErr }}</p>
    <template v-if="covOpen && cov">
      <div class="bar">
        <button v-for="f in covFilters" :key="f[0]" type="button" class="chip" :class="{ on: covOnly === f[0] }" @click="go({ cvo: f[0] || undefined })">{{ f[1] }} {{ f[2] }}</button>
      </div>
      <V2Table :rows="covShown" prefix="C" row-key="cidr" empty="沒有符合的網段"
               :cols="[{ k: 'cidr', label: '網段', mono: true, link: covSegLink }, { k: 'where', label: '配置表' }, { k: 'location', label: '機房' }, { k: 'environment', label: '環境' }, { k: 'got', label: '已收集／總位址', mono: true, link: covSegLink, text: (r: any) => r.got }, { k: 'pct', label: '%', mono: true }, { k: 'alive', label: '活著', mono: true }, { k: 'last', label: '最近收集', mono: true }]" />
      <div v-if="covSeg" data-test="coverage-detail">
        <div class="bar">
          <b class="mono">{{ covSeg.cidr }}</b><span class="muted">已收集 {{ covSeg.collected }}／{{ covSeg.total }}<template v-if="!covSeg.listed_all">（網段太大，只列收集過的位址）</template></span>
          <button type="button" class="btn2" @click="go({ cvs: undefined })">關閉</button>
        </div>
        <V2Table :rows="covItems" prefix="A" row-key="ip" empty="這一段沒有位址"
                 :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'hostname', label: '主機名稱' }, { k: 'st', label: '上次結果' }, { k: 'at', label: '上次收集', mono: true }]" />
      </div>
      <p v-else-if="covSegBusy" class="muted">載入中…</p>
    </template>
  </section>

  <!-- 納管紀錄 -->
  <section v-if="audits.length" class="card">
    <div class="bar">
      <b>納管紀錄</b>
      <InfoNote>這裡只留帳號名與成敗，永遠不含密碼。階段：connect＝還沒進到目標機（帳密錯／連不上／缺收集金鑰）；execute＝進去了但腳本沒跑完（多半是權限或 sudo）。</InfoNote>
      <button type="button" class="btn2" @click="showAudit = !showAudit">{{ showAudit ? '收起' : `看全部（${audits.length} 筆）` }}</button>
    </div>
    <div v-if="lastFail && !showAudit" data-test="last-fail">
      <p><b>最近一次失敗</b>　{{ lastFail.created_at }}・<span class="mono">{{ lastFail.target_ip }}</span>・{{ lastFail.stage || '未知階段' }}</p>
      <p class="err">{{ lastFail.message }}</p>
      <pre v-if="lastFail.output" class="outbox">{{ lastFail.output }}</pre>
    </div>
    <template v-if="showAudit">
      <V2Table :rows="auditRows" prefix="L" row-key="key" empty="沒有納管紀錄"
               :cols="[{ k: 'created_at', label: '時間', mono: true }, { k: 'target_ip', label: '目標', mono: true }, { k: 'platform', label: '平台' }, { k: 'login_user', label: '登入帳號' }, { k: 'who', label: '觸發' }, { k: 'res', label: '結果' }, { k: 'stage', label: '階段' }, { k: 'message', label: '訊息' }, { k: 'output', label: '輸出' }]">
        <template #cell-output="{ row }">
          <button v-if="aOut(row)" type="button" class="lb" @click="toggleOut(row)">{{ openOutput === aId(row) ? '收起' : '看輸出' }}</button>
        </template>
      </V2Table>
      <pre v-if="openOutput != null" class="outbox">{{ audits.find((a: any) => a.id === openOutput)?.output }}</pre>
    </template>
  </section>
</div>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; margin-bottom: 12px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; font-family: inherit; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; cursor: default; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0; }
.fsel { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); }
.targets { width: 100%; min-height: 84px; font-family: ui-monospace, monospace; font-size: 13px; padding: 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); margin: 8px 0; box-sizing: border-box; }
.outbox { white-space: pre-wrap; font-size: 12px; background: var(--sub); border: 1px solid var(--line); border-radius: 8px; padding: 8px; max-height: 320px; overflow: auto; }
.muted { color: var(--muted); font-size: 12px; }
.gapin { width: 56px; font-family: inherit; font-size: 13px; padding: 2px 4px; border: 1px solid var(--border-strong); border-radius: 6px; }
.mono { font-family: ui-monospace, monospace; }
.err { color: var(--bad); }
</style>
