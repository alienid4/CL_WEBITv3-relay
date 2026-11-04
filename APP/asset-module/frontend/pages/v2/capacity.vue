<script setup lang="ts">
// 10.3 容量分析（ACT-04）：證券業分時段容量——每台主機 × 時段（預設＝使用者提供：09:00–09:30 開盤時段（重點）、09:30–13:30 盤中，其餘為盤前／盤後；可改）× 日型（交易日／非交易日）。
// 容量判斷以『尖峰日』為準（全期最大、前 5 尖峰日），不以平均為準；大量交易日不是事先定義的日型，而是事前／事後的『交易日情境標記』（見「交易日情境」頁籤）。
// 唯讀「依據＋限制」：不自動調任何資源；沒有全天平均（早盤 90%＋離峰 10% 不可被平均稀釋）；覆蓋不足寫「覆蓋不足」、沒收集寫「未收集」，不是 0 也不是空白。
// 頁籤：主機×時段｜單機明細｜交易日情境｜限制與缺口｜封存與保存（日摘要封存 2 年；清理只有管理員手動、先預覽）｜時段設定（管理員可改）｜資料來源。資料：/api/v2/capacity/*（capacity_analysis.py；唯一歷史來源＝使用者上傳的 NMON 逐分鐘檔）。
// 網址即狀態：?tab=&day=&metric=&stat=&host=&ev=&demo=1（示範資料是記憶體內模擬，絕不混進真實盤點）。
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isAdmin, isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const data = ref<any>(null)
const err = ref('')
const loading = ref(false)
const demo = computed(() => q('demo') === '1')
const from = ref(q('from'))
const to = ref(q('to'))
async function load() {
  loading.value = true
  try {
    data.value = demo.value ? await apiFetch<any>('/api/v2/capacity/demo')
      : await apiFetch<any>('/api/v2/capacity/overview', { query: { ...(q('from') ? { from: q('from') } : {}), ...(q('to') ? { to: q('to') } : {}) } })
    err.value = ''
  } catch (e: any) { data.value = null; err.value = `容量分析載入失敗：${errMsg(e)}` } finally { loading.value = false }      // 失敗就清掉舊資料：避免舊示範列離開示範後被當成真實結果
}
await load()
watch(() => [q('demo'), q('from'), q('to')].join('|'), load)

const hosts = computed<CapHost[]>(() => data.value?.hosts ?? [])
const slots = computed<any[]>(() => data.value?.config?.slots ?? [])
// 重點時段（開盤 09:00–09:30）排最前面、名稱加★；總覽先看開盤格
const orderedSlots = computed(() => [...slots.value].sort((a: any, b: any) => Number(!!b.priority) - Number(!!a.priority)))
const slotKeys = computed(() => [...orderedSlots.value.map((s: any) => s.key), data.value?.other_key ?? '_other'])
const slotLabels = computed(() => ({ ...Object.fromEntries(slots.value.map((s: any) => [s.key, (s.priority ? '★ ' : '') + s.label])), [data.value?.other_key ?? '_other']: data.value?.other_label ?? '其他時間' }) as Record<string, string>)
const metrics = computed<any[]>(() => data.value?.metrics ?? [])
const metricLabels = computed(() => Object.fromEntries(metrics.value.map((m: any) => [m.key, m.label])) as Record<string, string>)
const day = computed(() => (['normal', 'off'].includes(q('day')) ? q('day') : 'normal'))
const metric = computed(() => (metrics.value.some((m: any) => m.key === q('metric')) ? q('metric') : 'cpu_busy'))
const stat = computed(() => capStat(q('stat')))
const unit = computed(() => metrics.value.find((m: any) => m.key === metric.value)?.unit ?? '%')
const ev = computed(() => (['adequate', 'limited', 'insufficient'].includes(q('ev')) ? q('ev') : ''))
const sum = computed(() => capSummary(hosts.value))
const statLabel = CAP_STAT_LABEL
const kindLabel = MARKER_KIND_LABEL

const TAB_KEYS = ['matrix', 'detail', 'markers', 'limits', 'archive', 'config', 'sources']
const TABS = computed(() => [{ k: 'matrix', label: `主機×時段 ${hosts.value.length}` }, { k: 'detail', label: '單機明細' }, { k: 'markers', label: `交易日情境 ${markers.value.length}` },
  { k: 'limits', label: `限制與缺口 ${limitRows.value.length}` }, { k: 'archive', label: '封存與保存' }, { k: 'config', label: '時段設定' }, { k: 'sources', label: '資料來源' }])
const tab = computed({ get: () => (TAB_KEYS.includes(q('tab')) ? q('tab') : 'matrix'), set: (v: string) => go({ tab: v }) })
const url = (p: Record<string, string>) => `/v2/capacity?${new URLSearchParams({ ...(demo.value ? { demo: '1' } : {}), ...(q('from') ? { from: q('from') } : {}), ...(q('to') ? { to: q('to') } : {}), day: day.value, metric: metric.value, stat: stat.value, ...p }).toString()}`

// ---- 主機 × 時段 ----
const matrixRows = computed(() => capMatrixRows(hosts.value.filter(h => !ev.value || h.evidence === ev.value), slotKeys.value, day.value, metric.value, stat.value, unit.value))
const matrixCols = computed(() => [
  { k: 'host', label: '主機', link: (r: any) => url({ tab: 'detail', host: r.host }) },
  { k: 'evidence', label: '資料條件', link: (r: any) => url({ tab: 'limits', host: r.host }) },
  { k: 'days', label: '該日型有資料天數', mono: true },
  ...slotKeys.value.map(k => ({ k: `s_${k}`, label: slotLabels.value[k], mono: true, text: (r: any) => r.text[k], link: (r: any) => url({ tab: 'detail', host: r.host }) })),
  { k: 'limits_n', label: '限制（項）', mono: true, link: (r: any) => url({ tab: 'limits', host: r.host }) },
])

// ---- 單機明細 ----
const selHost = computed(() => hosts.value.find(h => h.host === q('host')) ?? hosts.value[0])
const detailRows = computed(() => capDetailRows(selHost.value, day.value, slotLabels.value, metricLabels.value))
const peakRows = computed(() => capPeakRows(selHost.value, day.value, metric.value, slotLabels.value))
const peakCols = [{ k: 'slot', label: '時段' }, { k: 'source', label: '來源' }, { k: 'rank', label: '名次', mono: true }, { k: 'date', label: '日期', mono: true }, { k: 'max', label: '當天該時段最大值', mono: true },
  { k: 'p95', label: 'P95', mono: true }, { k: 'avg', label: '平均', mono: true }, { k: 'samples', label: '樣本數', mono: true }, { k: 'annotated', label: '標註（事前預告／事後補標）' }]
const annotRows = computed(() => capAnnotRows(selHost.value, day.value, metric.value, slotLabels.value))
const annotCols = [{ k: 'slot', label: '時段' }, { k: 'group', label: '對照組' }, { k: 'days', label: '天數', mono: true }, { k: 'max', label: '期間最大值', mono: true }]
const detailCols = [
  { k: 'source', label: '來源' }, { k: 'slot', label: '時段' }, { k: 'metric', label: '指標' }, { k: 'samples', label: '樣本數', mono: true }, { k: 'expected', label: '期望樣本數', mono: true },
  { k: 'days', label: '有資料天數', mono: true }, { k: 'coverage', label: '覆蓋率 %', mono: true }, { k: 'gap', label: '時段內最大連續缺口（分）', mono: true }, { k: 'avg', label: '平均', mono: true }, { k: 'p95', label: 'P95', mono: true },
  { k: 'max', label: '最大值', mono: true }, { k: 'max_at', label: '最大值發生時間', mono: true },
  { k: 'median', label: '中位數', mono: true }, { k: 'over_sort', label: '超標分鐘（> 門檻）', min: 160, text: (r: any) => r.over }, { k: 'status', label: '狀態' },
]

// ---- 限制與缺口 ----
const limitRows = computed(() => capLimitRows(hosts.value, data.value?.static_limits ?? []).filter((r: any) => !q('host') || r.host === q('host')))
const limitCols = [{ k: 'host', label: '主機', link: (r: any) => url({ tab: 'detail', host: r.host }) }, { k: 'scope', label: '範圍' }, { k: 'code', label: '代碼', mono: true }, { k: 'text', label: '限制' }]

// ---- 資料來源 ----
const sourceRows = computed(() => (data.value?.sources ?? []).map((s: any, i: number) => ({ __no: i + 1, label: s.label, hosts: s.hosts ?? '—', rows: s.rows ?? '—', range: s.from ? `${String(s.from).slice(0, 10)} ～ ${String(s.to).slice(0, 10)}` : '—',
  usable: s.usable_for_slots ? '可（逐取樣點）' : '不可', note: s.available ? s.note : '此資料表在這個資料庫不存在' })))
const sourceCols = [{ k: 'label', label: '來源' }, { k: 'hosts', label: '主機／卷宗數', mono: true }, { k: 'rows', label: '筆數', mono: true }, { k: 'range', label: '涵蓋期間', mono: true }, { k: 'usable', label: '能做時段分析' }, { k: 'note', label: '說明' }]

// ---- 交易日情境標記（來源由伺服器時間自動判定，使用者不能選；開盤後預告欄位鎖定，只能新增修訂版）----
const markers = ref<any[]>([])
const mopt = ref<any>({ types: [], reasons: [], levels: [], default_ranges: [], kinds: {}, server_now: '', today_kind: 'pre_announce' })
const msel = ref<any>(null)
const maudit = ref<any[]>([])
const mform = reactive<any>({ trade_date: '', rangeMode: 'default', rangesText: '', mtype: '', reason_cat: '', expected_level: '', basis: '', note: '', market_note: '', market_source: '' })
const mReviseOf = ref<number | null>(null)
const mVoidReason = ref('')
const mEvents = ref('')
const mBusy = ref(false)
async function loadMarkers() {
  try {
    const r = await apiFetch<any>('/api/v2/capacity/markers')
    markers.value = r.markers
    mopt.value = r.options
    if (!mform.mtype) { mform.mtype = r.options.types[0] ?? ''; mform.reason_cat = r.options.reasons[0] ?? '' }
    if (!mform.trade_date) mform.trade_date = String(r.options.server_now || '').slice(0, 10)
  } catch (e: any) { showToast(`交易日情境載入失敗：${errMsg(e)}`, 'error') }
}
if (!demo.value) await loadMarkers()
const mRows = computed(() => markers.value.map((m: any, i: number) => ({ ...m, __no: markers.value.length - i, ranges_text: markerRanges(m), events_n: m.event_ids.length })))
const mCols = [
  { k: 'trade_date', label: '交易日', mono: true, link: (r: any) => url({ tab: 'markers', mk: String(r.id) }) }, { k: 'ranges_text', label: '時段範圍（左閉右開）', mono: true }, { k: 'mtype', label: '類型' },
  { k: 'reason_cat', label: '原因類別' }, { k: 'expected_level', label: '預期程度' }, { k: 'origin_kind_label', label: '首次標記來源（分析用）' }, { k: 'kind_label', label: '這一版的來源' }, { k: 'status_label', label: '狀態' },
  { k: 'created_by', label: '建立人' }, { k: 'created_at', label: '建立時間', mono: true }, { k: 'basis', label: '依據說明', brief: (r: any) => (r.basis.length > 24 ? r.basis.slice(0, 24) + '…' : r.basis) },
  { k: 'events_n', label: '連結事件（筆）', mono: true },
]
const mTimeline = computed(() => [...markers.value].filter((m: any) => m.status !== 'voided').sort((a: any, b: any) => (a.trade_date < b.trade_date ? -1 : 1)).slice(-40))
const mKindPreview = computed(() => markerKindPreview(mform.trade_date, mopt.value.server_now || '', mopt.value.today_kind || 'pre_announce'))
const mSelected = computed(() => markers.value.find((m: any) => String(m.id) === q('mk')) ?? null)
watch(mSelected, async (m: any) => {
  maudit.value = []
  if (m) { try { maudit.value = (await apiFetch<any>(`/api/v2/capacity/markers/${m.id}/audit`)).audit } catch (e: any) { showToast(`稽核紀錄載入失敗：${errMsg(e)}`, 'error') } }
}, { immediate: true })
function mBody() {
  return { trade_date: mform.trade_date, ranges: mform.rangeMode === 'default' ? undefined : markerRangesFromText(mform.rangesText), mtype: mform.mtype, reason_cat: mform.reason_cat,
    expected_level: mform.expected_level, basis: mform.basis, note: mform.note, market_note: mform.market_note, market_source: mform.market_source }
}
function mStartRevise(m: any) {
  mReviseOf.value = m.id
  Object.assign(mform, { trade_date: m.trade_date, rangeMode: 'custom', rangesText: (m.ranges ?? []).map((x: any) => `${x.start}-${x.end}`).join('\n'), mtype: m.mtype, reason_cat: m.reason_cat,
    expected_level: m.expected_level, basis: m.basis, note: m.note, market_note: m.market_note, market_source: m.market_source })
}
async function mSubmit() {
  mBusy.value = true
  try {
    const r = mReviseOf.value ? await guardedFetch<any>(`/api/v2/capacity/markers/${mReviseOf.value}/revise`, { method: 'POST', body: mBody() }) : await guardedFetch<any>('/api/v2/capacity/markers', { method: 'POST', body: mBody() })
    showToast(`已${mReviseOf.value ? '新增修訂版' : '新增'}（來源：${MARKER_KIND_LABEL[r.source_kind]}，由伺服器時間判定）`, 'success')
    mReviseOf.value = null
    await loadMarkers()
    go({ tab: 'markers', mk: String(r.id) })
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { mBusy.value = false }
}
async function mVoid(m: any) {
  if (!mVoidReason.value.trim()) { showToast('作廢要寫原因', 'error'); return }
  try { await guardedFetch(`/api/v2/capacity/markers/${m.id}/void`, { method: 'POST', body: { reason: mVoidReason.value } }); mVoidReason.value = ''; showToast('已作廢（原文保留）', 'success'); await loadMarkers() } catch (e: any) { showToast(`作廢失敗：${errMsg(e)}`, 'error') }
}
async function mLinkEvents(m: any) {
  try { await guardedFetch(`/api/v2/capacity/markers/${m.id}/events`, { method: 'PUT', body: { event_ids: mEvents.value.split(/[,，\s]+/).filter(Boolean).map(Number) } }); showToast('已更新連結的事件應變', 'success'); await loadMarkers() } catch (e: any) { showToast(`連結失敗：${errMsg(e)}`, 'error') }
}

// ---- 時段設定（管理員）----
const form = reactive<{ slots: any[]; heavy: string; holidays: string; params: Record<string, number>; mtypes: string; mreasons: string }>({ slots: [], heavy: '', holidays: '', params: {}, mtypes: '', mreasons: '' })
function fillForm() {
  const c = data.value?.config
  if (!c) return
  form.slots = c.slots.map((s: any) => ({ ...s }))
  form.heavy = capDatesToText(c.heavy_days)
  form.holidays = capDatesToText(c.holidays)
  form.params = { ...c.params }
  form.mtypes = (c.marker_types ?? []).join('、')
  form.mreasons = (c.marker_reasons ?? []).join('、')
}
fillForm()
watch(() => data.value?.config, fillForm)
const saving = ref(false)
async function save() {
  saving.value = true
  try {
    await guardedFetch('/api/v2/capacity/config', { method: 'PUT', body: { slots: form.slots, heavy_days: capTextToDates(form.heavy), holidays: capTextToDates(form.holidays), params: form.params,
      marker_types: form.mtypes.split(/[、,，]/).map(x => x.trim()).filter(Boolean), marker_reasons: form.mreasons.split(/[、,，]/).map(x => x.trim()).filter(Boolean) } })
    showToast('已儲存設定', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { saving.value = false }
}
const addSlot = () => form.slots.push({ key: `s${form.slots.length + 1}`, label: '', start: '09:00', end: '10:00' })
const delSlot = (i: number) => form.slots.splice(i, 1)
const PARAM_LABELS: Record<string, string> = { min_coverage_pct: '最低覆蓋率（%）', min_days: '一般交易日至少天數', target_span_days: '目標觀察天數', high_pct: '視覺提示線（%）', wu_read_interval_hours: 'WhatsUp 讀取最短間隔（小時，預設 24，不輪詢）', wu_manual_cooldown_hours: 'WhatsUp 手動讀取最短冷卻（小時）', wu_max_hosts: 'WhatsUp 一次最多台數', overview_max_hosts: '總覽一次最多算幾台' }
// ---- 封存與保存（日摘要封存，保存 2 年；清理只有管理員手動，先預覽）----
const arch = ref<any>(null)
const archError = ref('')
const arOne = ref<any>(null)
const arBusy = ref(false)
const arMonths = ref(24)
const arRun = reactive({ day: '', host: '' })
const arPv = ref<any>(null)
async function loadArchive() {
  try {
    arch.value = await apiFetch<any>('/api/v2/capacity/archive', { query: { limit: 2000 } })
    archError.value = ''
    arMonths.value = arch.value.settings.retention_months
  } catch (e: any) { arch.value = null; archError.value = `封存資料載入失敗：${errMsg(e)}` }
}
watch(() => [q('tab'), demo.value].join('|'), () => { if (tab.value === 'archive' && !demo.value && !arch.value && !archError.value) loadArchive() }, { immediate: true })
const AR_STAGES: Record<string, string> = { provisional: '暫定', final: '完成（T+1）', revision: '修訂版' }
const arStage = computed(() => (Object.keys(AR_STAGES).includes(q('ast')) ? q('ast') : ''))
const arOnlyProt = computed(() => q('apr') === '1')
const arLatest = computed(() => q('alt') === '1')
const arAll = computed(() => (arch.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1,
  src_text: (r.sources ?? []).join('、') || '—', itv_text: r.granularity?.interval_s ? `${r.granularity.interval_s} 秒（${(r.granularity.kinds ?? []).join('／')}）` : '—',
  asset_text: r.identity?.asset_serial || (r.identity?.mapped === false ? '未對應' : '—'), purpose_text: [r.identity?.environment, r.identity?.purpose, r.identity?.standby ? '備援／待命' : ''].filter(Boolean).join('・') || '—',
  prot_text: r.protected ? '被標記引用，不可清理' : '—', cells_text: `${r.ok_cells}／${r.cells_n}`, nodata_text: r.no_data_cells + r.low_coverage_cells })))
const arCount = (f: (r: any) => boolean) => arAll.value.filter(f).length
const arRows = computed(() => arAll.value.filter((r: any) => (!arStage.value || r.stage === arStage.value) && (!arOnlyProt.value || r.protected) && (!arLatest.value || r.is_latest)))
const arCols = [
  { k: 'host', label: '主機' }, { k: 'day', label: '交易日', mono: true, link: (r: any) => url({ tab: 'archive', ar: String(r.id) }) }, { k: 'version', label: '版本', mono: true },
  { k: 'stage_label', label: '階段' }, { k: 'src_text', label: '來源' }, { k: 'itv_text', label: '取樣粒度（類型）' }, { k: 'asset_text', label: '當時的資產', mono: true },
  { k: 'purpose_text', label: '環境・用途' }, { k: 'cells_text', label: '有效格／全部格', mono: true }, { k: 'nodata_text', label: '無資料或覆蓋不足（格）', mono: true },
  { k: 'prot_text', label: '標記引用' }, { k: 'created_at', label: '封存時間', mono: true }, { k: 'created_by', label: '封存者' },
]
watch(() => q('ar'), async (id: string) => {
  arOne.value = null
  if (!id) return
  try { arOne.value = await apiFetch<any>(`/api/v2/capacity/archive/${id}`) } catch (e: any) { showToast(`封存明細載入失敗：${errMsg(e)}`, 'error') }
}, { immediate: true })
const arCellRows = computed(() => (arOne.value?.summary?.cells ?? []).map((c: any, i: number) => ({ __no: i + 1, slot: c.slot_label, metric: metricLabels.value[c.metric] ?? c.metric, source: c.source, samples: c.samples, expected: c.expected ?? '—',
  coverage: c.coverage_pct ?? '—', missing: c.missing_pct ?? '—', avg: c.avg ?? '—', p95: c.p95 ?? (c.bucket ? '不可得' : '—'), max: c.max ?? '—', max_at: c.max_at ?? '—', status: capStatusText(c.status),
  median: 'median_note' in c ? capMedianText(c) : '舊版未記錄', over: 'over_text' in c ? capOverText(c) : '舊版未記錄' })))
const arCellCols = [{ k: 'slot', label: '時段' }, { k: 'metric', label: '指標' }, { k: 'source', label: '來源' }, { k: 'samples', label: '樣本數', mono: true }, { k: 'expected', label: '期望樣本數', mono: true },
  { k: 'coverage', label: '覆蓋率 %', mono: true }, { k: 'missing', label: '缺值率 %', mono: true }, { k: 'avg', label: '平均', mono: true }, { k: 'p95', label: 'P95', mono: true }, { k: 'max', label: '最大值', mono: true },
  { k: 'max_at', label: '最大值發生時間', mono: true }, { k: 'median', label: '中位數', mono: true }, { k: 'over', label: '超標分鐘（> 門檻）', min: 160 }, { k: 'status', label: '狀態' }]
const arAuditRows = computed(() => (arch.value?.settings?.audit ?? []).map((a: any, i: number) => ({ __no: i + 1, at: a.at, actor: a.actor || '—', action: a.action, detail: JSON.stringify(a.detail) })))
const arAuditCols = [{ k: 'at', label: '時間', mono: true }, { k: 'actor', label: '操作者' }, { k: 'action', label: '動作' }, { k: 'detail', label: '內容' }]
async function arSaveRetention() {
  arBusy.value = true
  try { await guardedFetch('/api/v2/capacity/archive/settings', { method: 'PUT', body: { retention_months: Number(arMonths.value) } }); showToast('已儲存保存月數', 'success'); arPv.value = null; await loadArchive() } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { arBusy.value = false }
}
async function arRunNow() {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(arRun.day)) { showToast('請選要封存的交易日', 'error'); return }
  arBusy.value = true
  try {
    const r = await guardedFetch<any>('/api/v2/capacity/archive/run', { method: 'POST', body: { day: arRun.day, host: arRun.host || undefined, reason: '人工封存' } })
    const w = r.results.filter((x: any) => x.result === 'written').length
    showToast(`${arRun.day}：新增 ${w} 版，${r.results.length - w} 台沒變動或沒資料`, 'success')
    await loadArchive()
  } catch (e: any) { showToast(`封存失敗：${errMsg(e)}`, 'error') } finally { arBusy.value = false }
}
async function arPreview() {
  arBusy.value = true
  try { arPv.value = await guardedFetch<any>('/api/v2/capacity/archive/purge/preview', { method: 'POST' }); await loadArchive() } catch (e: any) { arPv.value = null; showToast(`預覽失敗：${errMsg(e)}`, 'error') } finally { arBusy.value = false }
}
async function arPurge() {
  const pv = arPv.value
  if (!pv || pv.blocked || !pv.rows) return
  if (!window.confirm(`確定刪除 ${pv.cutoff} 以前的 ${pv.rows} 筆日摘要（${pv.host_days} 個主機日）？被標記引用的 ${pv.protected_rows} 筆不會刪。這個動作無法復原。`)) return
  arBusy.value = true
  try {
    const r = await guardedFetch<any>('/api/v2/capacity/archive/purge', { method: 'POST', body: { expected_rows: pv.rows, cutoff: pv.cutoff, confirm: true } })
    showToast(`已清理 ${r.deleted_rows} 筆（稽核已記錄）`, 'success')
    arPv.value = null
    await loadArchive()
  } catch (e: any) { showToast(`清理失敗：${errMsg(e)}`, 'error') } finally { arBusy.value = false }
}
const capStatusText = (s: string) => ({ ok: '可用', low_coverage: '覆蓋不足', no_data: '無資料', not_collected: '未收集', coarse_interval: '間隔太粗', few_days: '天數不足', no_count: '缺筆數' } as Record<string, string>)[s] ?? s
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">10.3 容量分析</h1><span v-if="demo" class="demo">示範資料（模擬，非真實主機）</span></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <template v-else-if="data">
        <div class="bar">
          <button type="button" class="chip" :class="{ on: !ev }" @click="go({ ev: undefined })">全部 {{ sum.total }} 台</button>
          <button type="button" class="chip" :class="{ on: ev === 'adequate' }" @click="go({ ev: 'adequate', tab: 'matrix' })">無額外資料限制 {{ sum.adequate }}</button>
          <button type="button" class="chip" :class="{ on: ev === 'limited' }" @click="go({ ev: 'limited', tab: 'matrix' })">有限制、只能參考 {{ sum.limited }}</button>
          <button type="button" class="chip" :class="{ on: ev === 'insufficient' }" @click="go({ ev: 'insufficient', tab: 'matrix' })">覆蓋不足 {{ sum.insufficient }}</button>
          <InfoNote>每台主機的歷史來自「10.1 月報」上傳的 NMON 檔；目前沒有自動收集的 CPU／記憶體／IO 歷史。這頁只列依據與限制，不建議增減資源、不自動調整。容量看尖峰日（全期最大、前 5 尖峰日）；沒有「全天平均」，因為平均會把開盤尖峰稀釋掉。大量交易日無法事先定義，系統以觀測到的尖峰日代替；沒發生過極端日就不代表極端日承載能力。業務延遲、交易量系統沒有收集，無法證明減配不影響交易。</InfoNote>
        </div>
        <p v-for="(w, i) in data.config.warnings || []" :key="i" class="warn">{{ w }}</p>
        <p v-if="data.truncated" class="warn">目前只算前 {{ data.hosts.length }} 台（共 {{ data.hosts_total }} 台），避免一次計算太久；單台請從下拉選或網址帶 host=。</p>
        <p class="muted" data-slots-source>時段：{{ data.config.slots_source }}（開盤時段 09:00–09:30 最重要，盤中 09:30–13:30；其餘為盤前／盤後）。
          <template v-for="u in data.config.unprovided || []" :key="u"><b>待使用者提供：</b>{{ u }}。</template></p>
      </template>
    </template>
    <template #tab>
      <template v-if="!data" />
      <template v-else-if="tab === 'matrix'">
        <div class="filters">
          <span class="lb">日型</span>
          <button v-for="d in data.day_types" :key="d.key" type="button" class="chip" :class="{ on: day === d.key }" @click="go({ day: d.key })">{{ d.label }}</button>
          <span class="lb">指標</span>
          <select class="sel" :value="metric" @change="go({ metric: ($event.target as HTMLSelectElement).value })"><option v-for="m in metrics" :key="m.key" :value="m.key">{{ m.label }}</option></select>
          <span class="lb">統計</span>
          <button v-for="s in (['max', 'top5', 'p95', 'avg'] as const)" :key="s" type="button" class="chip" :class="{ on: stat === s }" @click="go({ stat: s })">{{ statLabel[s] }}</button>
          <template v-if="!demo">
            <span class="lb">期間</span>
            <input v-model="from" type="date" class="sel" @change="go({ from: from || undefined })"> ～ <input v-model="to" type="date" class="sel" @change="go({ to: to || undefined })">
          </template>
          <button type="button" class="chip" :class="{ on: demo }" @click="go({ demo: demo ? undefined : '1' })">{{ demo ? '離開示範' : '看示範資料' }}</button>
        </div>
        <div v-if="!hosts.length" class="empty">
          <p>還沒有任何 NMON 歷史資料，所以沒有可分析的主機。</p>
          <p>下一步：到「10.1 月報」上傳主機的 NMON 檔（逐分鐘），這裡就會出現；或先看「示範資料」了解長相。</p>
          <NuxtLink to="/v2/report" class="lnk">到 10.1 月報上傳 NMON ›</NuxtLink>
          <button type="button" class="chip" @click="go({ demo: '1' })">看示範資料</button>
        </div>
        <template v-else>
          <p class="muted">{{ statLabel[stat] }}・{{ metricLabels[metric] }}・{{ data.day_types.find((d: any) => d.key === day)?.label }}。「覆蓋不足」＝該格資料量低於 {{ data.config.params.min_coverage_pct }}%，不給平均／P95。點格子看明細。
            <InfoNote>時段與休市日可在「時段設定」改；大量交易日請用「交易日情境」事前預告／事後補標，不是分析前提。週六日與休市日歸「非交易日」，不混進交易日。「其他時間」是沒落在任何時段的時間，只供對照。</InfoNote></p>
          <V2Table :rows="matrixRows" :cols="matrixCols" prefix="H" empty="沒有符合的主機" />
        </template>
      </template>
      <template v-else-if="tab === 'detail'">
        <div class="filters">
          <span class="lb">主機</span>
          <select class="sel" :value="selHost?.host" @change="go({ host: ($event.target as HTMLSelectElement).value })"><option v-for="h in hosts" :key="h.host" :value="h.host">{{ h.host }}</option></select>
          <span class="lb">日型</span>
          <button v-for="d in data.day_types" :key="d.key" type="button" class="chip" :class="{ on: day === d.key }" @click="go({ day: d.key })">{{ d.label }}</button>
        </div>
        <p v-if="!selHost" class="muted">沒有主機資料。</p>
        <template v-else>
          <p class="muted">{{ selHost.evidence_label }}・資料 {{ selHost.data_days }} 天（{{ selHost.from }} ～ {{ selHost.to }}）・取樣間隔 {{ selHost.interval_s ?? '未知' }} 秒・交易日 {{ selHost.days_by_type.normal }}／非交易日 {{ selHost.days_by_type.off }} 天（標註的大量交易日 {{ (selHost.annotated_days || []).length }} 天，含在交易日內）
            <span v-if="selHost.peak_cpu">・CPU 最高時段：{{ selHost.peak_cpu.slot_label }}（{{ selHost.peak_cpu.basis === 'p95' ? 'P95' : '最大值' }} {{ selHost.peak_cpu.value }}%）</span>
            ・資料來源：{{ selHost.source_label }}・用途對應：{{ selHost.context?.mapped ? '已人工對應' : '未對應' }}</p>
          <h3 class="h3">尖峰日排名（{{ metricLabels[metric] }}，每個時段前 5 天）</h3>
          <V2Table :rows="peakRows" :cols="peakCols" prefix="P" empty="這個日型沒有尖峰日資料" :max="200" />
          <template v-if="annotRows.length">
            <h3 class="h3">標註日 vs 其他交易日</h3>
            <V2Table :rows="annotRows" :cols="annotCols" prefix="A" empty="無" :max="100" />
          </template>
          <h3 class="h3">格子明細</h3>
          <V2Table :rows="detailRows" :cols="detailCols" prefix="D" empty="這個日型沒有資料" :max="200" />
          <p class="muted"><NuxtLink :to="url({ tab: 'limits', host: selHost.host })" class="lnk">這台的 {{ selHost.limits.length }} 項限制 ›</NuxtLink></p>
        </template>
      </template>
      <template v-else-if="tab === 'markers'">
        <p v-if="demo" class="muted">示範模式不顯示交易日情境標記。</p>
        <template v-else>
          <p class="muted" data-marker-intro>大量交易日無法事先定義：知道某天會大漲大跌時，在這裡留下標記，日後回測才有依據。<b>來源由伺服器時間自動判定、不能選</b>：交易日 09:00 前建立＝事前預告；09:00–13:30＝盤中標記；13:30 之後＝事後補標。
            開盤後預告欄位鎖定，只能新增修訂版；作廢保留原文（限管理員）。新增或修改標記不會觸發任何取數。
            <InfoNote>分析頁的「標註日」只讀有效的事前預告＋事後補標（盤中標記不納入），兩者分開顯示。市場背景（大盤漲跌幅、成交量）只收你自己輸入的文字與來源說明，系統不會自動抓。</InfoNote></p>
          <p v-if="!isAdmin" class="warn" data-marker-readonly>只有管理員群組可以新增或修改標記；你可以查看清單、時間軸與歷史。</p>
          <section v-if="isAdmin" class="mform">
            <h3 class="h3">{{ mReviseOf ? `新增 #${mReviseOf} 的修訂版` : '新增標記' }}</h3>
            <div class="mgrid">
              <label>交易日期<input v-model="mform.trade_date" type="date" class="inp" :disabled="!!mReviseOf"></label>
              <label>時段範圍<select v-model="mform.rangeMode" class="sel"><option value="default">預設（開盤 09:00–09:30、盤中 09:30–13:30）</option><option value="custom">自訂</option></select></label>
              <label v-if="mform.rangeMode === 'custom'">自訂時段（每行 HH:MM-HH:MM，左閉右開）<textarea v-model="mform.rangesText" class="ta" rows="2" /></label>
              <label>類型<select v-model="mform.mtype" class="sel"><option v-for="t in mopt.types" :key="t" :value="t">{{ t }}</option></select></label>
              <label>預告原因類別<select v-model="mform.reason_cat" class="sel"><option v-for="t in mopt.reasons" :key="t" :value="t">{{ t }}</option></select></label>
              <label>預期程度（選填）<select v-model="mform.expected_level" class="sel"><option value="">—</option><option v-for="t in mopt.levels" :key="t" :value="t">{{ t }}</option></select></label>
              <label class="wide">依據說明（必填）<textarea v-model="mform.basis" class="ta" rows="2" /></label>
              <label>備註<input v-model="mform.note" class="inp"></label>
              <label>市場背景（自行輸入，選填）<input v-model="mform.market_note" class="inp"></label>
              <label>背景來源說明<input v-model="mform.market_source" class="inp"></label>
            </div>
            <p class="muted" data-kind-preview>來源（由伺服器時間自動判定，不能選）：<b>{{ kindLabel[mKindPreview] }}</b>
              <button type="button" class="pbtn" :disabled="mBusy" data-marker-submit @click="mSubmit">{{ mBusy ? '儲存中…' : mReviseOf ? '新增修訂版' : '新增標記' }}</button>
              <button v-if="mReviseOf" type="button" class="chip" @click="mReviseOf = null">取消修訂</button></p>
          </section>
          <h3 class="h3">時間軸</h3>
          <div class="tline" data-timeline>
            <button v-for="m in mTimeline" :key="m.id" type="button" class="chip" :class="{ on: String(m.id) === q('mk') }" @click="go({ mk: String(m.id) })">{{ m.trade_date.slice(5) }}・{{ m.kind_label }}・{{ m.mtype }}</button>
            <span v-if="!mTimeline.length" class="muted">還沒有任何標記。</span>
          </div>
          <V2Table :rows="mRows" :cols="mCols" prefix="M" empty="還沒有任何標記：知道某天會大漲大跌時，按上面的「新增標記」" :max="100" />
          <section v-if="mSelected" class="mdetail" data-marker-detail>
            <h3 class="h3">#{{ mSelected.id }} {{ mSelected.trade_date }}・{{ mSelected.kind_label }}・{{ mSelected.status_label }}</h3>
            <p class="muted">依據：{{ mSelected.basis }}<template v-if="mSelected.market_note">　市場背景：{{ mSelected.market_note }}（來源：{{ mSelected.market_source || '未填' }}）</template>
              <template v-if="mSelected.supersedes_id">　修訂自 #{{ mSelected.supersedes_id }}</template><template v-if="mSelected.voided_at">　作廢：{{ mSelected.voided_by }} {{ mSelected.voided_at }}，原因：{{ mSelected.void_reason }}</template>
              <template v-if="mSelected.locked && mSelected.status === 'active'">　（交易日 09:00 已過，預告欄位鎖定：要更正請新增修訂版）</template></p>
            <p v-if="isAdmin && mSelected.status === 'active'">
              <button type="button" class="chip" @click="mStartRevise(mSelected)">新增修訂版</button>
              <label class="inl">連結事件應變編號（逗號分隔，不改變來源）<input v-model="mEvents" class="inp narrow" placeholder="例：12, 15"></label>
              <button type="button" class="chip" @click="mLinkEvents(mSelected)">更新連結</button>
              <template v-if="isAdmin"><input v-model="mVoidReason" class="inp narrow" placeholder="作廢原因（必填）"><button type="button" class="chip" data-marker-void @click="mVoid(mSelected)">作廢</button></template>
            </p>
            <h3 class="h3">稽核紀錄</h3>
            <ul class="audit"><li v-for="(a, i) in maudit" :key="i"><span class="mono">{{ a.at }}</span>　{{ a.actor || '—' }}　{{ a.action }}<template v-if="a.before || a.after">　前：{{ JSON.stringify(a.before) }}　後：{{ JSON.stringify(a.after) }}</template></li></ul>
          </section>
        </template>
      </template>
      <template v-else-if="tab === 'limits'">
        <p class="muted">{{ q('host') ? `只看 ${q('host')}` : '全部主機' }}。限制＝這份分析「不能」支撐的結論；有限制就不能據以增減配。
          <button v-if="q('host')" type="button" class="chip" @click="go({ host: undefined })">看全部主機</button></p>
        <V2Table :rows="limitRows" :cols="limitCols" prefix="L" empty="沒有限制（不太可能：業務延遲證據永遠缺）" :max="200" />
      </template>
      <template v-else-if="tab === 'config'">
        <p class="muted">時段：{{ data.config.slots_source }}。重點時段（★）＝開盤 09:00–09:30。盤後批次的起訖時間使用者尚未提供，請有了再新增。</p>
        <table class="cfg">
          <thead><tr><th>項次</th><th>代號</th><th>名稱</th><th>開始 HH:MM</th><th>結束 HH:MM</th><th /></tr></thead>
          <tbody>
            <tr v-for="(s, i) in form.slots" :key="i">
              <td>{{ i + 1 }}</td>
              <td><input v-model="s.key" class="inp" :disabled="isViewer || !isAdmin"></td>
              <td><input v-model="s.label" class="inp" :disabled="isViewer || !isAdmin"></td>
              <td><input v-model="s.start" class="inp" :disabled="isViewer || !isAdmin"></td>
              <td><input v-model="s.end" class="inp" :disabled="isViewer || !isAdmin"></td>
              <td><button v-if="isAdmin" type="button" class="chip" @click="delSlot(i)">刪除</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="isAdmin"><button type="button" class="chip" @click="addSlot">新增時段</button></p>
        <div class="two">
          <label>舊的手動標註清單（選填，每行：日期 備註）<InfoNote>不是分析前提，留空沒有影響。建議改用「交易日情境」頁籤留下事前預告／事後補標（有來源判定、鎖定與稽核）。這份舊清單只是純標註，會在對照中標「舊清單標註」。</InfoNote>
            <textarea v-model="form.heavy" class="ta" rows="6" :disabled="isViewer || !isAdmin" /></label>
          <label>休市日（每行：日期 備註）<InfoNote>週六日自動算非交易日；平日休市請列在這裡。</InfoNote>
            <textarea v-model="form.holidays" class="ta" rows="6" :disabled="isViewer || !isAdmin" /></label>
        </div>
        <div class="two">
          <label>交易日情境的類型清單（用「、」分隔，必須含「大量交易日」）<input v-model="form.mtypes" class="inp" :disabled="isViewer || !isAdmin"></label>
          <label>預告原因類別清單（用「、」分隔）<input v-model="form.mreasons" class="inp" :disabled="isViewer || !isAdmin"></label>
        </div>
        <div class="params"><label v-for="(label, k) in PARAM_LABELS" :key="k">{{ label }}<input v-model.number="form.params[k]" type="number" class="inp" :disabled="isViewer || !isAdmin"></label></div>
        <p v-if="isAdmin"><button type="button" class="pbtn" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存設定' }}</button></p>
        <p v-else class="muted">只有管理員可以修改。</p>
      </template>
      <template v-else-if="tab === 'archive'">
        <p v-if="demo" class="muted">示範模式不顯示封存資料。</p>
        <p v-else-if="archError" class="err">{{ archError }}</p>
        <template v-else-if="arch">
          <p class="muted" data-archive-intro>保存 {{ arch.settings.retention_months }} 個月（{{ arch.settings.cutoff }} 以前的日摘要過期）・已封存 {{ arch.settings.rows }} 筆（{{ arch.settings.host_days }} 個主機日）{{ arch.settings.oldest ? `・${arch.settings.oldest} ～ ${arch.settings.newest}` : '' }}
            <InfoNote>收盤後把每台主機當天的分析結果凍結成「日摘要」：暫定版 → 隔天（T+1）完成版 → 之後來源資料有變才新增修訂版，舊版永遠保留、不覆寫。缺資料不補 0（沒有樣本寫「無資料」，avg／P95／最大值留空）。每版記來源、取樣粒度、演算法版本、當時的資產身分與時段設定。自動封存只在平日 08:30–15:00 以外執行。清理只有管理員手動：先預覽影響筆數，清理必須對得上預覽並留稽核；被交易日情境標記或下方「封存引用」引用的日摘要（含舊版與作廢的）不會被刪。排程不會清理。每格的中位數只在覆蓋足夠的逐點資料才有；超標分鐘依來源自己的取樣間隔換算，缺的不當成沒超標，給不出精確值時寫範圍或標示無法判定。</InfoNote></p>
          <div class="bar">
            <button type="button" class="chip" :class="{ on: !arStage && !arOnlyProt }" @click="go({ ast: undefined, apr: undefined })">全部 {{ arAll.length }}</button>
            <button v-for="(lb, k) in AR_STAGES" :key="k" type="button" class="chip" :class="{ on: arStage === k }" @click="go({ ast: k, apr: undefined })">{{ lb }} {{ arCount((r: any) => r.stage === k) }}</button>
            <button type="button" class="chip" :class="{ on: arOnlyProt }" @click="go({ apr: arOnlyProt ? undefined : '1', ast: undefined })">被標記引用 {{ arCount((r: any) => r.protected) }}</button>
            <button type="button" class="chip" :class="{ on: arLatest }" @click="go({ alt: arLatest ? undefined : '1' })">只看每個主機日的最新版</button>
          </div>
          <V2Table :rows="arRows" :cols="arCols" prefix="F" empty="還沒有任何日摘要：收盤後（平日 15:00 以後）有 NMON 資料的交易日會自動封存；管理員也可以在下面手動補封存" :max="100" />
          <section v-if="arOne" class="mdetail" data-archive-detail>
            <h3 class="h3">#{{ arOne.id }} {{ arOne.host }}・{{ arOne.day }}・第 {{ arOne.version }} 版・{{ arOne.stage_label }}</h3>
            <p class="muted">演算法 {{ arOne.algo_version }}・來源 {{ (arOne.sources || []).join('、') }}・封存原因：{{ arOne.reason || '—' }}・內容雜湊 <span class="mono">{{ String(arOne.content_hash).slice(0, 12) }}</span>
              ・當時資產：{{ arOne.identity?.asset_serial || '未對應' }}（{{ [arOne.identity?.environment, arOne.identity?.purpose].filter(Boolean).join('・') || '環境／用途未知' }}）
              <template v-if="arOne.protected">・<b>被標記引用，不可清理</b>：{{ (arOne.protected_reasons || []).join('；') }}</template></p>
            <V2Table :rows="arCellRows" :cols="arCellCols" prefix="G" empty="這一版沒有格子" :max="200" />
          </section>
          <h3 class="h3">保存與清理</h3>
          <p v-if="!isAdmin" class="warn" data-archive-readonly>只有管理員群組可以手動補封存、改保存月數與清理；你可以查看全部封存內容與稽核紀錄。</p>
          <template v-else>
            <p class="filters">
              <label class="inl">保存月數（預設 24）<input v-model.number="arMonths" type="number" min="1" max="120" class="inp narrow"></label>
              <button type="button" class="pbtn" :disabled="arBusy" data-archive-save @click="arSaveRetention">儲存保存月數</button>
              <label class="inl">補封存日期<input v-model="arRun.day" type="date" class="inp narrow"></label>
              <label class="inl">主機（空白＝全部）<input v-model="arRun.host" class="inp narrow"></label>
              <button type="button" class="chip" :disabled="arBusy" data-archive-run @click="arRunNow">立即封存</button>
            </p>
            <p class="filters">
              <button type="button" class="chip" :disabled="arBusy" data-archive-preview @click="arPreview">預覽可清理的筆數</button>
              <template v-if="arPv">
                <span data-archive-pv>截止 {{ arPv.cutoff }} 以前：可清理 <b>{{ arPv.rows }}</b> 筆（{{ arPv.host_days }} 個主機日），因標記引用而保留 {{ arPv.protected_rows }} 筆</span>
                <span v-if="arPv.blocked" class="err">{{ arPv.blocked }}</span>
                <button v-else type="button" class="pbtn" :disabled="arBusy || !arPv.rows" data-archive-purge @click="arPurge">確認清理 {{ arPv.rows }} 筆</button>
              </template>
            </p>
          </template>
          <V2CapacityCitations :is-admin="isAdmin" @changed="loadArchive" />
          <h3 class="h3">最近的封存稽核</h3>
          <V2Table :rows="arAuditRows" :cols="arAuditCols" prefix="Z" empty="還沒有稽核紀錄" :max="20" />
        </template>
      </template>
      <template v-else-if="tab === 'sources'">
        <p class="warn">WhatsUp 自動取數尚未啟用：沒有任何排程或按鈕會去讀 WhatsUp；目前只有 NMON 上傳的歷史可分析。</p>
        <p v-if="demo" class="muted">示範模式不盤點真實資料來源。</p>
        <V2Table v-else :rows="sourceRows" :cols="sourceCols" prefix="S" empty="無資料" :max="50" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.demo { padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 12px; }
.bar, .filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; margin-bottom: 8px; }
.lb { color: var(--ink-aux); font-size: 12px; margin-left: 6px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.sel, .inp { font-family: inherit; font-size: 13px; padding: 3px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.inp { width: 100%; box-sizing: border-box; }
.ta { width: 100%; box-sizing: border-box; font-family: inherit; font-size: 13px; border: 1px solid var(--border-strong); border-radius: 8px; padding: 6px 8px; background: var(--card); color: var(--ink); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.warn { padding: 6px 12px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.empty { padding: 24px; border: 1px dashed var(--border-strong); border-radius: var(--radius); color: var(--ink-aux); }
.lnk { color: var(--link); margin-right: 12px; }
.cfg { width: 100%; border-collapse: collapse; font-size: 13px; }
.cfg th, .cfg td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--line); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 12px 0; }
.two label, .params label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--ink-aux); }
.params { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; margin-bottom: 12px; }
.pbtn { font-family: inherit; font-size: 13px; padding: 6px 16px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: #fff; cursor: pointer; }
.pbtn:disabled { opacity: .6; }
.mgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; margin-bottom: 8px; }
.mgrid label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--ink-aux); }
.mgrid .wide { grid-column: 1 / -1; }
.tline { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.inl { display: inline-flex; gap: 6px; align-items: center; margin: 0 8px; font-size: 13px; }
.narrow { width: 180px; }
.audit { font-size: 12.5px; color: var(--ink-aux); padding-left: 18px; }
.h3 { margin: 14px 0 6px; font-size: 15px; font-weight: 700; }
@media (max-width: 720px) { .two { grid-template-columns: 1fr; } }
</style>
