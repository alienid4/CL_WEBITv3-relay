<script setup lang="ts">
// 1.5 每日檢查（2.060.x 試辦）：xServer 每日檢查紀錄。設計：docs/設計_每日檢查自動化_2026-11-11.md（含 GPT 審查與挑剔使用者審查）。
// 系統 06:30 用已匯入的資料產生當天快照（判定＋數值＋資料時間＋來源批次），值班逐列確認後送出；送出後唯讀，只能更正（留紀錄）。
// 判定五種：正常／異常／無資料／前一日參考（永遠要人勾）／人工。舊資料、不完整、未設定一律無資料。
// 版面（挑剔使用者審查 10 點）：最上一行「能不能送出」＋08:30 倒數＋跳到未確認；卡片分「系統判定」「確認進度」兩組互斥（各自加總＝全部）；
// 表格預設依判定分組排（異常→無資料→前一日參考→人工→正常），項次＝檢查表順序固定不變；每一列點了看計算明細（?row=）；
// 換篩選／日期／快照就清掉勾選；批次確認先預覽；每列寫資料「N 分鐘前」與新鮮／過期；紀錄用白話。
import { downloadVia } from '~/utils/importV2'
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer, isAdmin } = useAuth()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const setQ = (patch: Record<string, string | undefined>) => {
  const n: Record<string, any> = { ...route.query, ...patch }
  for (const k of Object.keys(n)) if (n[k] === undefined || n[k] === '') delete n[k]
  router.replace({ query: n })
}
const TABS = [{ k: 'today', label: '每日檢查' }, { k: 'history', label: '紀錄' }, { k: 'settings', label: '設定' }]
const tab = computed({ get: () => (TABS.some(t => t.k === q('tab')) ? q('tab') : 'today'), set: (v: string) => setQ({ tab: v, v: undefined, row: undefined }) })
const RELAY = '/v2/settings?st=integrations&sub=relay'

const d = ref<any>(null); const err = ref(''); const busy = ref(false)
async function load() {
  try {
    const query: Record<string, string> = {}
    if (q('day')) query.day = q('day')
    if (q('run')) query.run_id = q('run')
    d.value = await apiFetch<any>('/api/daily-check', { query }); err.value = ''
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
await load()
watch(() => [q('day'), q('run')], load)
const run = computed(() => d.value?.run ?? null)
const submitted = computed(() => run.value?.status === 'submitted')
// 2.066.11 使用者 11-14：每日檢查是看板（看主機當天狀態），預設不走勾選／確認／送出；設定頁「值班簽核」打開才出現
const so = computed(() => !!d.value?.signoff)
const th = computed(() => d.value?.thresholds ?? {})

// ===== 時間：資料「N 分鐘前」、新鮮／過期、08:30 倒數 =====
const nowTick = ref(Date.now())
let timer: any = null
onMounted(() => { timer = setInterval(() => { nowTick.value = Date.now() }, 30000) })
onBeforeUnmount(() => clearInterval(timer))
const toMs = (t: string | null | undefined) => (t ? new Date(String(t).replace(' ', 'T')).getTime() : NaN)
function ago(t: string | null | undefined): string {
  const ms = nowTick.value - toMs(t)
  if (Number.isNaN(ms)) return ''
  const m = Math.max(0, Math.round(ms / 60000))
  return m < 60 ? `${m} 分鐘前` : m < 1440 ? `${Math.floor(m / 60)} 小時前` : `${Math.floor(m / 1440)} 天前`
}
/** 依來源的新鮮度門檻判斷（告警快照用分鐘、效能／磁碟用小時；AD 對時由來源自己判）。 */
function freshness(r: any): string {
  if (!r.data_at) return r.verdict === 'manual' ? '' : '沒有資料時間'
  if (r.verdict === 'nodata' && /太舊/.test(r.reason || '')) return '過期'
  const ms = nowTick.value - toMs(r.data_at)
  if (/告警快照/.test(r.source || '')) return ms <= (th.value.alert_fresh_min ?? 10) * 60000 ? '新鮮' : '已超過門檻（以快照當下為準）'
  if (/上次輪循/.test(r.source || '')) return ms <= (th.value.lp_fresh_min ?? 35) * 60000 ? '新鮮' : '已超過門檻（以快照當下為準）'
  if (/vROps/.test(r.source || '')) return ms <= (th.value.vm_fresh_min ?? 120) * 60000 ? '新鮮' : '已超過門檻（以快照當下為準）'
  if (/效能|磁碟/.test(r.source || '')) return ms <= (th.value.perf_fresh_h ?? 30) * 3600000 ? '新鮮（前一日）' : '已超過門檻（以快照當下為準）'
  return ''
}
const countdown = computed(() => {
  const day = run.value?.day || d.value?.day
  if (!day || day !== String(d.value?.now || '').slice(0, 10)) return ''
  const left = Math.round((toMs(`${day} 08:30:00`) - nowTick.value) / 60000)
  return left > 0 ? `距 08:30 開盤 ${Math.floor(left / 60) ? `${Math.floor(left / 60)} 小時 ` : ''}${left % 60} 分` : '已過 08:30 開盤'
})

// ===== 卡片：系統判定／確認進度兩組，各自互斥、加總＝全部 =====
const c = computed(() => d.value?.counts ?? {})
const SYS = [{ k: 'bad', label: '異常', tone: 'bad' }, { k: 'nodata', label: '無資料', tone: 'warn' }, { k: 'ref', label: '前一日參考', tone: '' },
  { k: 'manual', label: '人工', tone: '' }, { k: 'ok', label: '正常', tone: 'good' }]
const CONF = [{ k: 'unconfirmed', label: '未確認', tone: 'warn' }, { k: 'result_bad', label: '確認為異常', tone: 'bad' }, { k: 'result_ok', label: '確認為正常', tone: 'good' }]
const GROUP: Record<string, number> = { bad: 1, nodata: 2, ref: 3, manual: 4, ok: 5 }
const VTONE: Record<string, string> = { ok: 'prod', bad: 'test', nodata: 'none', ref: 'dr', manual: 'none' }
const rows = computed(() => (d.value?.rows ?? []).map((r: any) => ({ ...r, __no: r.no, gsort: GROUP[r.verdict] ?? 9,
  at: (r.data_at || '').slice(0, 16), age: ago(r.data_at), fr: freshness(r),
  who: r.confirmed_by ? `${r.confirmed_by} ${(r.confirmed_at || '').slice(11, 16)}` : '', vt: r.value_text || (r.verdict === 'manual' ? '人工檢查' : `無資料：${r.reason || '—'}`) })))
const shown = computed(() => {
  const v = q('v')
  if (!v) return rows.value
  if (v === 'unconfirmed') return rows.value.filter((r: any) => !r.result)
  if (v === 'result_bad') return rows.value.filter((r: any) => r.result === 'bad')
  if (v === 'result_ok') return rows.value.filter((r: any) => r.result === 'ok')
  return rows.value.filter((r: any) => r.verdict === v)
})
const blocks = computed(() => SYS.map(s => { const g = rows.value.filter((r: any) => r.verdict === s.k); const done = g.filter((r: any) => r.result).length
  return { ...s, n: g.length, done, pct: g.length ? Math.round((done * 100) / g.length) : 100 } }))
const rowLink = (r: any) => `/v2/dailycheck?${new URLSearchParams({ ...(route.query as any), tab: 'today', row: String(r.id) }).toString()}`
const cols = [
  { k: 'code', label: '項目代號', mono: true }, { k: 'section', label: '區塊' }, { k: 'name', label: '項目' },
  { k: 'verdict_label', label: '系統判定', tone: (r: any) => VTONE[r.verdict], link: rowLink },
  { k: 'vt', label: '數值', link: rowLink }, { k: 'at', label: '資料時間', mono: true, link: (r: any) => (r.data_at ? rowLink(r) : null) },
  { k: 'age', label: '多久前', link: (r: any) => (r.data_at ? rowLink(r) : null) }, { k: 'fr', label: '新鮮度' },
]
const SIGN_COLS = [{ k: 'result_label', label: '確認' }, { k: 'note', label: '異常說明' }, { k: 'who', label: '確認人' }]
const colsNow = computed(() => (so.value ? [...cols, ...SIGN_COLS] : cols))
const exportCols = [{ k: 'reason', label: '系統說明' }, { k: 'source', label: '來源' }, { k: 'batch', label: '來源批次' }]

// ===== 勾選：換篩選／日期／快照就清掉（看不到的列不能被送出）=====
const picked = ref<Record<string, boolean>>({})
watch(() => [q('v'), q('day'), q('run'), tab.value], () => { picked.value = {} })
const pickedIds = computed(() => Object.keys(picked.value).filter(k => picked.value[k]).map(Number))
const res = ref<'ok' | 'bad'>('ok'); const note = ref('')
const vmPick = computed(() => { const r = rows.value.filter((x: any) => pickedIds.value.includes(x.id)); return r.length === 1 && r[0].extra === 'vmware' ? r[0] : null })
// 2.064.0 VMware 預填列（rule=vmware）：一定要勾「已檢視 Error Log／Mail」；結果另選；異常單號選填；手填跟預填不同＝覆寫要原因
const vmPre = computed(() => vmPick.value?.rule === 'vmware')
const vmPicked = computed(() => rows.value.filter((x: any) => pickedIds.value.includes(x.id)).some((x: any) => x.rule === 'vmware'))
const vm = reactive({ esxi_count: '', lun_free_gb: '', has_alert: false, alert_note: '', reviewed: false, ticket: '', override_reason: '' })
watch(() => vmPick.value?.id, () => Object.assign(vm, { esxi_count: '', lun_free_gb: '', has_alert: false, alert_note: '', reviewed: false, ticket: '', override_reason: '' }))
const vmSys = computed(() => { const st = vmPick.value?.stats; return st?.vm ? { esxi_count: st.seen, lun_free_gb: st.ds_min_free_gb, has_alert: !!st.alerts_open } : null })
const vmOverride = computed(() => {
  const s = vmSys.value
  if (!s) return []
  const o: string[] = []
  if (vm.esxi_count && Number(vm.esxi_count) !== s.esxi_count) o.push(`ESXi 台數 ${s.esxi_count}→${vm.esxi_count}`)
  if (vm.lun_free_gb && Number(vm.lun_free_gb) !== s.lun_free_gb) o.push(`LUN 最小剩餘 ${s.lun_free_gb ?? '—'}→${vm.lun_free_gb}`)
  if (vm.has_alert !== s.has_alert && (vm.has_alert || vm.alert_note)) o.push(`有警示 ${s.has_alert ? '是' : '否'}→${vm.has_alert ? '是' : '否'}`)
  return o
})
const vmErr = computed(() => {
  if (vmPicked.value && !vmPick.value) return 'VMware 預填列一次確認一列（要勾已檢視）'
  if (!vmPick.value) return ''
  if (vmPre.value && !vm.reviewed) return '要先勾「已檢視 Error Log／Mail」'
  if (vmPre.value && res.value === 'ok' && vmPick.value?.stats?.has_issue && !note.value.trim()) return '預填有異常，確認為正常要寫說明'
  if (vmOverride.value.length && !vm.override_reason.trim()) return `跟系統預填不同（${vmOverride.value.join('、')}），要寫覆寫原因`
  if (vm.esxi_count && !/^\d+$/.test(vm.esxi_count)) return 'ESXi 台數要是整數'
  if (vm.lun_free_gb && !/^\d+(\.\d+)?$/.test(vm.lun_free_gb)) return 'LUN 最小剩餘要是數字（GB）'
  if (vm.has_alert && !vm.alert_note.trim()) return '勾了「有警示」要寫警示說明'
  return ''
})
async function doConfirm() {
  if (!pickedIds.value.length) return
  if (res.value === 'bad' && !note.value.trim()) { showToast('異常要寫說明', 'error'); return }
  if (vmErr.value) { showToast(vmErr.value, 'error'); return }
  if (!window.confirm(`把 ${pickedIds.value.length} 列確認為「${res.value === 'ok' ? '正常' : '異常'}」？每一列都留紀錄。`)) return
  busy.value = true
  try {
    const extra = !vmPick.value ? null : vmPre.value ? { ...vm, has_alert: vm.has_alert ? true : undefined } : { esxi_count: vm.esxi_count, lun_free_gb: vm.lun_free_gb, has_alert: vm.has_alert, alert_note: vm.alert_note }
    await guardedFetch('/api/daily-check/confirm', { method: 'POST', body: { row_ids: pickedIds.value, result: res.value, note: note.value || null, extra } })
    showToast(`已確認 ${pickedIds.value.length} 列`, 'success'); picked.value = {}; note.value = ''; await load()
  } catch (e: any) { showToast(`確認失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
// 批次確認：先預覽（會確認哪幾列、資料截至何時、哪些不會被確認與原因）
const showPv = ref(false)
const pvRows = computed(() => rows.value.filter((r: any) => !r.result).map((r: any) => ({ ...r,
  take: r.verdict === 'ok' && r.fresh ? '會確認' : '不會確認',
  why: r.verdict === 'ok' ? (r.fresh ? `資料 ${r.at}（${r.age}）` : '資料不夠新') : `${r.verdict_label}：要逐列確認` })))
const freshOk = computed(() => pvRows.value.filter((r: any) => r.take === '會確認').length)
async function doFreshOk() {
  if (!run.value) return
  busy.value = true
  try { const r = await guardedFetch<any>('/api/daily-check/confirm-fresh-ok', { method: 'POST', body: { run_id: run.value.id } }); showToast(`已確認 ${r.updated} 列`, 'success'); showPv.value = false; await load() } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function doSubmit() {
  if (!run.value || !window.confirm(`送出今天的每日檢查？確認為異常 ${c.value.result_bad} 列、系統無資料 ${c.value.nodata} 列。送出後只能用「更正」改。`)) return
  busy.value = true
  try { await guardedFetch('/api/daily-check/submit', { method: 'POST', body: { run_id: run.value.id } }); showToast('已送出', 'success'); await load() } catch (e: any) { showToast(`送出失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function doCorrect() {
  if (pickedIds.value.length !== 1) { showToast('更正一次一列', 'error'); return }
  if (!note.value.trim()) { showToast('更正要寫原因', 'error'); return }
  if (!window.confirm(`更正這一列為「${res.value === 'ok' ? '正常' : '異常'}」？原本的結果留在紀錄裡。`)) return
  busy.value = true
  if (vmErr.value) { showToast(vmErr.value, 'error'); return }
  const cx = vmPick.value ? (vmPre.value ? { ...vm, has_alert: vm.has_alert ? true : undefined } : { esxi_count: vm.esxi_count, lun_free_gb: vm.lun_free_gb, has_alert: vm.has_alert, alert_note: vm.alert_note }) : null
  try { await guardedFetch('/api/daily-check/correct', { method: 'POST', body: { row_id: pickedIds.value[0], result: res.value, note: note.value, extra: cx } }); showToast('已更正', 'success'); picked.value = {}; note.value = ''; await load() } catch (e: any) { showToast(`更正失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function doGenerate() {
  if (run.value && !window.confirm(so.value ? '重新產生今天的快照？會用現在已匯入的資料再算一次，已確認的內容留在舊的那一次（紀錄頁可查），新的要重新確認。' : '用現在已匯入的資料重新產生今天的每日檢查？舊的那一次留在紀錄頁。')) return
  busy.value = true
  try { await guardedFetch<any>('/api/daily-check/generate', { method: 'POST' }); showToast('已產生', 'success'); setQ({ run: undefined, day: undefined }); await load() } catch (e: any) { showToast(`產生失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function doExport(id: number) {
  try { await downloadVia(apiBase, `/api/daily-check/${id}/export.xlsx`, `xServer每日檢查紀錄_${id}.xlsx`) } catch (e: any) { showToast(`匯出失敗：${e?.message ?? '請稍後重試'}`, 'error') }
}
// 能不能送出：完成度、擋住的原因
const done = computed(() => (c.value.total ?? 0) - (c.value.unconfirmed ?? 0))
const blockers = computed(() => {
  if (!run.value) return ['今天還沒有快照']
  if (submitted.value) return []
  const b: string[] = []
  if (run.value.superseded) b.push('這一次已作廢，請到最新那一次')
  if (c.value.unconfirmed) b.push(`還有 ${c.value.unconfirmed} 列沒確認`)
  return b
})
function goUnconfirmed() { setQ({ tab: 'today', v: 'unconfirmed', row: undefined }) }

// ===== 計算明細（每一列的數字、時間、批次、0 都看得到怎麼來的）=====
const lpMode = computed(() => (d.value?.perf_source ?? 'lastpoll') !== 'w3')
// 2.062.0（S13）上次輪循：CPU／記憶體／硬碟／輪詢改看 WhatsUp 群組「上次輪循」當下值（正本 1-3～1-7）
const LP_TEXT = computed<Record<string, string>>(() => ({
  perf_cpu: `WhatsUp 群組（設定的 1-3 群組，含子群組）每台「上次輪循」CPU 平均值，達 ${th.value.cpu ?? 75}% 以上（需低於 ${th.value.cpu ?? 75}%）的台數。距上次輪詢（加上批次到產生快照的時間）超過 ${th.value.poll_min ?? 60} 分鐘的值算舊值、沒有值的算無資料，都不算正常。`,
  perf_mem: `同一個群組每台「上次輪循」記憶體平均值，達 ${th.value.mem ?? 90}% 以上（需低於 ${th.value.mem ?? 90}%）的台數（虛擬記憶體／SWAP 項目不算）。`,
  disk: `WhatsUp 群組（設定的 1-6 群組）每顆碟「上次輪循」使用率：≥ ${th.value.disk_alert ?? 95}%（或單台例外門檻）＝異常；${th.value.disk_list ?? 85}～${th.value.disk_alert ?? 95}%＝參考（列出，不算異常）。SWAP 與假磁碟不算。`,
  poll: `設定的 1-7 群組（可多個，同一台只算一次）在 CPU／記憶體／磁碟三份報表的「上次輪循」，任一份超過 ${th.value.poll_min ?? 60} 分鐘（加上批次到產生快照的時間）＝列出。沒有輪詢時間的設備算無資料；有這種設備時整列不判正常，列為參考要人確認。`,
  swap: 'WhatsUp 的 Linux_SWAP 是自訂效能工具，沒有 API；改用最近一次納管健康檢查的 SWAP（範圍＝設定的 1-5 群組成員 IP），要人確認。',
}))
const RULE_TEXT = computed<Record<string, string>>(() => ({
  wug_down: `最近一次「完整」的 WhatsUp 告警掃描（看到的台數要達群組總數 98% 以上、且在 ${th.value.alert_fresh_min ?? 10} 分鐘內）裡，還沒結束、狀態 Down 的設備台數；0 台＝正常。`,
  wug_maint: '同一次完整掃描裡，狀態「維護中」的設備台數（只記錄，不判異常）。',
  perf_cpu: `前一日每台每個時間桶的 CPU 最高值，超過 ${th.value.cpu ?? 75}% 的台數（範圍＝設定的 1-xServer IP 清單）。前一日資料，一定要人確認。`,
  perf_mem: `前一日每台每個時間桶的記憶體最高值，超過 ${th.value.mem ?? 90}% 的台數。前一日資料，一定要人確認。`,
  swap: `WhatsUp 磁碟資料裡名稱像 SWAP（Swap space／Virtual memory）的那一顆，前一日最後一點超過 ${th.value.swap ?? 90}% 的台數；整個範圍都沒有 SWAP 項目＝無資料。前一日資料，一定要人確認。`,
  disk: `前一日每顆碟最後一個時間點的使用率，超過 ${th.value.disk ?? 85}%（或單台例外門檻）的碟數。前一日資料，一定要人確認。`,
  poll: `前一日每台最後一個輪詢時間距收集時間超過 ${th.value.poll_min ?? 60} 分鐘的台數。前一日資料，一定要人確認。`,
  banqiao: '最近一次完整告警掃描裡，這台有開著的 Down／維護事件＝異常；告警快照只列有問題的設備，沒有逐台 Up 紀錄，所以其他情況一律無資料。',
  ad_time: 'AD 對時中繼（9.26 跑 w32tm /monitor）：全部 DC 時間差都在門檻內＝正常；有超過＝異常；資料太舊或有 DC 沒回應＝無資料。',
  vmware: 'vROps（跳板機 每天 06:00／06:20／07:30 讀一次，只讀 vROps，不連 ESXi／vCenter）：應有 ESXi 清單（設定）對 vROps 看到的，列出缺、多、斷線、維護；資料存放區剩餘低於門檻的（本機碟＝VMFS 且只掛 1 台才排除，判不出的列出不排除）；目前未解警示（vROps 靜音、已認領、清單靜音分開列）。收集器停收、資料過期、0 筆一律無資料。永遠要人看 Error Log／Mail 後確認。',
  ...(lpMode.value ? LP_TEXT.value : {}),
}))
const detailRow = computed(() => rows.value.find((r: any) => String(r.id) === q('row')) ?? null)
// VMware 列：明細（ESXi／資料存放區／警示）可依「類別＋狀態」篩；列上每個數字都點得到對應的明細
const isVm = computed(() => !!detailRow.value?.stats?.vm)
const vmF = ref<{ kind: string, state: string }>({ kind: '', state: '' })
watch(() => q('row'), () => { vmF.value = { kind: '', state: '' } })
const vmPills = computed(() => {
  const st = detailRow.value?.stats
  if (!st?.vm) return []
  return [['ESXi', '缺', st.missing], ['ESXi', '異常', st.abnormal], ['ESXi', '維護', st.maint], ['ESXi', '舊值', st.stale_hosts], ['ESXi', '正常', st.connected],
    ['資料存放區', '低於門檻', st.ds_low], ['資料存放區', '無剩餘值', st.ds_nofree], ['資料存放區', '舊值', st.ds_stale], ['資料存放區', '本機碟（排除）', st.ds_local], ['資料存放區', '已知例外', st.ds_exempt ?? 0], ['資料存放區', '正常', st.ds_ok],
    ['警示', '未解', st.alerts_open], ['警示', '已認領', st.alerts_owned], ['警示', '清單靜音', st.alerts_muted], ['警示', 'vROps 靜音', st.alerts_suppressed],
    ['無法歸屬 vCenter', '無法歸屬', (st.unassigned_alerts ?? 0) + (st.unassigned_ds ?? 0)]]
    .map(([kind, state, n]) => ({ kind, state, n }))
})
const detailRows = computed(() => (detailRow.value?.detail ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1 }))
  .filter((x: any) => !isVm.value || ((!vmF.value.kind || x.kind === vmF.value.kind) && (!vmF.value.state || x.state === vmF.value.state))))
const detailCols = computed(() => {
  if (isVm.value) return [{ k: 'kind', label: '類別' }, { k: 'name', label: '名稱' },
    { k: 'state', label: '狀態', tone: (r: any) => (['缺', '異常', '低於門檻', '未解', '無法歸屬'].includes(r.state) ? 'test' : ['舊值', '無剩餘值'].includes(r.state) ? 'none' : r.state === '正常' ? 'prod' : 'dr') },
    { k: 'presence', label: '應有／類型／等級' }, { k: 'what', label: '內容' }, { k: 'since', label: '採集時間', mono: true }, { k: 'res_id', label: 'vROps ID', mono: true }]
  const base = [{ k: 'name', label: '主機', link: (r: any) => (r.serial ? `/v2/machine/${encodeURIComponent(r.serial)}` : r.ip ? `/v2/machine/${encodeURIComponent(r.ip)}` : null) },
    { k: 'ip', label: 'IP', mono: true }]
  // 上次輪循列（有 stats）：每台一筆，多「狀態」「資產」「群組」
  if (detailRow.value?.stats) return [...base, { k: 'state', label: '狀態', tone: (r: any) => ({ 異常: 'test', 參考: 'dr', 舊值: 'none', 無資料: 'none', 正常: 'prod' } as Record<string, string>)[r.state] },
    { k: 'what', label: '內容' }, { k: 'asset', label: '資產' }, { k: 'src', label: '群組' }, { k: 'since', label: '上次輪詢', mono: true }]
  return [...base, { k: 'what', label: '內容' }, { k: 'since', label: '時間', mono: true }]
})
// 手填 1-xServer 清單 vs 群組（並行比對）：兩邊各差哪些 IP，可下鑽
const cmpRows = computed(() => {
  const st = detailRow.value?.stats
  if (!st || (!st.manual_only && !st.group_only)) return []
  return [...(st.manual_only ?? []).map((ip: string) => ({ ip, side: '手填有、群組沒有' })), ...(st.group_only ?? []).map((ip: string) => ({ ip, side: '群組有、手填沒有' }))]
    .map((x: any, i: number) => ({ ...x, __no: i + 1 }))
})
const cmpSide = ref('')
const cmpShown = computed(() => (cmpSide.value ? cmpRows.value.filter((r: any) => r.side === cmpSide.value) : cmpRows.value))

// ===== 紀錄（白話）=====
const ACT: Record<string, string> = { generate: '產生快照', confirm: '確認', confirm_fresh_ok: '批次確認新鮮的正常列', submit: '送出', correct: '更正', superseded: '作廢' }
const TRIG: Record<string, string> = { schedule: '排程', manual: '人工', vm_refresh: 'VMware 補抓' }
const RES: Record<string, string> = { ok: '正常', bad: '異常' }
const codeOf = (id: number | null) => rows.value.find((r: any) => r.id === id)?.code ?? (id ? `#${id}` : '')
function evText(e: any): string {
  const x = e.detail || {}
  if (e.action === 'generate') return `${TRIG[x.trigger] ?? '人工'}產生，共 ${x.rows ?? '—'} 列`
  if (e.action === 'superseded') return `這一次已作廢：被第 ${x.by_run} 次（${TRIG[x.trigger] ?? x.trigger}）取代`
  if (e.action === 'confirm') return `${codeOf(e.row_id)}：${RES[x.from] ?? '未確認'} → ${RES[x.to] ?? x.to}${x.note ? `（${x.note}）` : ''}`
  if (e.action === 'confirm_fresh_ok') return `確認 ${x.count ?? 0} 列為正常（${(x.rows ?? []).join('、') || '—'}；資料時間 ${(x.data_at ?? []).join('、') || '—'}）`
  if (e.action === 'submit') return '送出這次檢查'
  if (e.action === 'correct') return `${codeOf(e.row_id)}：${RES[x.from] ?? '未確認'}（${x.from_note || '無說明'}）→ ${RES[x.to] ?? x.to}，原因：${x.note || '—'}`
  return e.action
}
const ev = ref<any[]>([])
async function loadEvents() {
  if (!run.value) { ev.value = []; return }
  try { ev.value = (await apiFetch<any>('/api/daily-check/events', { query: { run_id: run.value.id } })).items.map((e: any, i: number) => ({ ...e, __no: i + 1, act: ACT[e.action] ?? e.action, txt: evText(e), rowc: codeOf(e.row_id) })) } catch (e: any) { showToast(`紀錄載入失敗：${errMsg(e)}`, 'error') }
}
watch([tab, run], () => { if (tab.value === 'history') loadEvents() }, { immediate: true })
const runRows = computed(() => (d.value?.runs ?? []).map((r: any) => ({ ...r, __no: r.id, trig: TRIG[r.trigger] ?? '人工',
  st: r.status === 'submitted' ? `已送出（${r.submitted_by} ${(r.submitted_at || '').slice(11, 16)}）` : r.superseded ? '已作廢（之後重新產生過）' : '未送出' })))

// ===== 設定 =====
const cfg = ref<any>(null); const form = reactive<Record<string, any>>({})
async function loadCfg() {
  try {
    cfg.value = (await apiFetch<any>('/api/daily-check/settings')).settings
    const s = cfg.value
    // 非管理員拿到的是精簡設定（沒有 IP、主機、收件人、例外）：那幾欄留空、不顯示
    Object.assign(form, { enabled: s.enabled, signoff: !!s.signoff, snapshot_time: s.snapshot_time, remind_time: s.remind_time, escalate_time: s.escalate_time,
      notify_stop_time: s.notify_stop_time, cpu: s.cpu, mem: s.mem, disk: s.disk, swap: s.swap, poll_min: s.poll_min, alert_fresh_min: s.alert_fresh_min, perf_fresh_h: s.perf_fresh_h,
      xserver_ips: (s.xserver_ips ?? []).join('\n'), banqiao: (s.banqiao ?? []).map((b: any) => `${b.name},${b.ip}`).join('\n'), vcenters: (s.vcenters ?? []).join('\n'),
      holidays: (s.holidays ?? []).join('\n'), escalate_to: (s.escalate_to ?? []).join('\n'),
      disk_exceptions: (s.disk_exceptions ?? []).map((x: any) => `${x.ip},${x.threshold},${x.expires},${x.reason}`).join('\n'),
      daily_check_perf_source: s.daily_check_perf_source ?? 'lastpoll', lp_fresh_min: s.lp_fresh_min, disk_list: s.disk_list, disk_alert: s.disk_alert,
      wug_member_jump_pct: s.wug_member_jump_pct,
      g_cpu_path: s.dc_group_cpu_mem?.path ?? '', g_cpu_id: s.dc_group_cpu_mem?.id ?? '', g_swap_path: s.dc_group_swap?.path ?? '', g_swap_id: s.dc_group_swap?.id ?? '',
      g_disk_path: s.dc_group_disk?.path ?? '', g_disk_id: s.dc_group_disk?.id ?? '',
      g_poll: (s.dc_group_poll ?? []).map((g: any) => (g.id ? `${g.path},${g.id}` : g.path)).join('\n'),
      vm_lun_threshold: s.vm_lun_threshold, vm_lun_unit: s.vm_lun_unit ?? 'GB', vm_lun_op: s.vm_lun_op ?? '<', vm_lun_used_pct_max: s.vm_lun_used_pct_max ?? 90, vm_alert_levels: [...(s.vm_alert_levels ?? [])],
      vm_fresh_min: s.vm_fresh_min, vm_adapter_stale_min: s.vm_adapter_stale_min, vm_record_stale_min: s.vm_record_stale_min,
      vm_local_patterns: (s.vm_local_patterns ?? []).join('\n'),
      vm_mute_max_days: s.vm_mute_max_days })
    vmMutes.value = (s.vm_alert_mutes ?? []).map((x: any) => ({ name: x.name ?? x.key ?? '', res_id: x.res_id ?? '', expires: x.expires ?? '', approver: x.approver ?? '', reason: x.reason ?? '' }))
    vmVcs.value = (s.vm_vcenters ?? []).map((v: any) => ({ name: v.name, vcid: v.vcid, adapter_id: v.adapter_id, expected: (v.expected ?? []).join('\n') }))
  } catch (e: any) { showToast(`設定載入失敗：${errMsg(e)}`, 'error') }
}
// ===== 2.064.0 VMware（vROps）：每座 vCenter 的識別、應有 ESXi 清單；最新批次、看到的 vCenter、收集器、設定改動紀錄 =====
const vmVcs = ref<any[]>([])
// 警示靜音：一筆一列各自的欄位（名稱可能含逗號，不用逗號拆）
const vmMutes = ref<any[]>([])
const VM_LEVELS = ['CRITICAL', 'IMMEDIATE', 'WARNING', 'INFORMATION']
const vmst = ref<any>(null)
async function loadVm() {
  try { vmst.value = await apiFetch<any>('/api/daily-check/vmware') } catch (e: any) { showToast(`VMware 狀態載入失敗：${errMsg(e)}`, 'error') }
}
watch(tab, (t) => { if (t === 'settings') loadVm() }, { immediate: true })
const vmSeen = computed(() => (vmst.value?.vcenters ?? []).map((x: any) => ({ ...x, used: vmVcs.value.find((v: any) => v.vcid === x.vcid)?.name ?? '' })))
const vmAdapters = computed(() => (vmst.value?.adapters ?? []))
const vmLog = computed(() => (vmst.value?.log ?? []).map((x: any) => ({ ...x, txt: `${JSON.stringify(x.before)} → ${JSON.stringify(x.after)}`.slice(0, 300) })))
function useVcid(x: any) {
  const blank = vmVcs.value.find((v: any) => !v.vcid)
  if (blank) blank.vcid = x.vcid
  else vmVcs.value.push({ name: x.adapter || x.example || x.vcid, vcid: x.vcid, adapter_id: '', expected: '' })
}
watch(tab, (t) => { if (t === 'settings' && !cfg.value) loadCfg() }, { immediate: true })
// ===== 2.062.0（S13）WhatsUp 上次輪循：最新批次、各群組台數／IP 數、成員核准 =====
const lpst = ref<any>(null)
async function loadLp() {
  try { lpst.value = await apiFetch<any>('/api/daily-check/lastpoll') } catch (e: any) { showToast(`上次輪循狀態載入失敗：${errMsg(e)}`, 'error') }
}
watch(tab, (t) => { if (t === 'settings') loadLp() }, { immediate: true })
const lpGroups = computed(() => (lpst.value?.groups ?? []).map((g: any) => ({ ...g, st: g.ok ? (g.scope_ok ? '已確認' : '範圍未確認') : `收集失敗：${g.error || '—'}`,
  appr: g.approved_at ? `${(g.approved_at || '').slice(0, 16)} ${g.approved_by || ''}（${g.approved_count} 台）` : '沒有' })))
const lpApprovals = computed(() => (lpst.value?.approvals ?? []).map((a: any, i: number) => ({ ...a, __no: i + 1, how: a.auto ? '自動（第一次）' : '管理員' })))
// 核准前先列出新增／移除的設備（可下鑽），看過才能按核准
const diff = ref<any>(null); const diffSide = ref('')
async function showDiff(g: any) {
  try { diff.value = await apiFetch<any>('/api/daily-check/lastpoll/diff', { query: { path: g.path } }); diffSide.value = '' } catch (e: any) { showToast(`成員差異載入失敗：${errMsg(e)}`, 'error') }
}
const diffRows = computed(() => (diff.value?.rows ?? []).filter((r: any) => !diffSide.value || r.change === diffSide.value).map((r: any, i: number) => ({ ...r, __no: i + 1 })))
async function approveMembers() {
  const g = diff.value
  if (!g || !window.confirm(`核准「${g.path}」目前 ${g.members} 台成員（新增 ${g.added}、移除 ${g.removed}）？之後跟這一份比。`)) return
  try { await guardedFetch('/api/daily-check/approve-members', { method: 'POST', body: { path: g.path } }); showToast('已核准', 'success'); diff.value = null; await loadLp() } catch (e: any) { showToast(`核准失敗：${errMsg(e)}`, 'error') }
}
function useGroup(g: any, which: 'cpu' | 'swap' | 'disk') {
  form[`g_${which}_path`] = g.path; form[`g_${which}_id`] = g.id || ''
}
const grpLine = (l: string) => { const i = l.lastIndexOf(','); const id = i > 0 ? l.slice(i + 1).trim() : ''; return /^[A-Za-z0-9_-]*$/.test(id) && i > 0 ? { path: l.slice(0, i).trim(), id } : { path: l.trim(), id: '' } }
const lines = (s: string) => String(s || '').split('\n').map(x => x.trim()).filter(Boolean)
async function saveCfg() {
  const patch = { ...form, xserver_ips: lines(form.xserver_ips), vcenters: lines(form.vcenters), escalate_to: lines(form.escalate_to), holidays: lines(form.holidays),
    banqiao: lines(form.banqiao).map(l => { const [name, ip] = l.split(','); return { name: (name || '').trim(), ip: (ip || name || '').trim() } }),
    disk_exceptions: lines(form.disk_exceptions).map(l => { const [ip, threshold, expires, ...r] = l.split(','); return { ip: ip?.trim(), threshold: Number(threshold), expires: expires?.trim(), reason: r.join(',').trim() } }),
    dc_group_cpu_mem: { path: form.g_cpu_path, id: form.g_cpu_id }, dc_group_swap: { path: form.g_swap_path, id: form.g_swap_id },
    dc_group_disk: { path: form.g_disk_path, id: form.g_disk_id }, dc_group_poll: lines(form.g_poll).map(grpLine) } as Record<string, any>
  for (const k of ['g_cpu_path', 'g_cpu_id', 'g_swap_path', 'g_swap_id', 'g_disk_path', 'g_disk_id', 'g_poll']) delete patch[k]
  if (cfg.value?.limited) { for (const k of Object.keys(patch)) if (k.startsWith('vm_') && !['vm_lun_threshold', 'vm_lun_unit', 'vm_lun_op', 'vm_lun_used_pct_max', 'vm_alert_levels', 'vm_fresh_min', 'vm_adapter_stale_min', 'vm_record_stale_min'].includes(k)) delete patch[k] }
  else {
    patch.vm_vcenters = vmVcs.value.filter((v: any) => String(v.name || '').trim()).map((v: any) => ({ name: v.name, vcid: v.vcid, adapter_id: v.adapter_id, expected: lines(String(v.expected || '').replace(/,/g, '\n')) }))
    patch.vm_local_patterns = lines(form.vm_local_patterns)
    patch.vm_alert_mutes = vmMutes.value.filter((m: any) => String(m.name || '').trim()).map((m: any) => ({ ...m }))
  }
  try { await guardedFetch('/api/daily-check/settings', { method: 'PUT', body: { patch } }); showToast('設定已儲存', 'success'); await loadCfg(); await load() } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') }
}
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">1.5 每日檢查</h1>
      <span class="trial">試辦第 {{ Math.min(d?.trial_day ?? 0, 5) }}/5 個工作天<InfoNote>跟現行手填的「xServer 每日檢查紀錄」並行 5 個工作天，確認結果一致再改用本頁；試辦期間以手填表為準。系統只預填，結論由人確認；抓不到的一律寫「無資料」，不會顯示正常。從第一次產生快照那天起算週一到週五。</InfoNote></span></template>
    <template #conclusion>
      <template v-if="run">{{ run.day }}・{{ !so ? `快照 ${(run.created_at || '').slice(11, 16)}` : submitted ? `已送出（${run.submitted_by} ${(run.submitted_at || '').slice(11, 16)}）` : '未送出' }}
        <InfoNote>快照產生於 {{ run.created_at }}（{{ run.trigger === 'schedule' ? '排程' : `人工：${run.created_by || '—'}` }}）。畫面只顯示這一刻的內容，不即時重算。
          排程 {{ d.times.snapshot_time }} 產生、{{ d.times.remind_time }} 還沒送出提醒、{{ d.times.escalate_time }} 升級（排程{{ d.enabled ? '已開啟' : '未開啟，管理員到「設定」打開' }}）。</InfoNote></template>
      <span v-else-if="d">{{ d.day }} 還沒有產生每日檢查</span>
    </template>
    <template #actions>
      <button v-if="!isViewer && tab === 'today'" type="button" class="btn ghost" :disabled="busy" @click="doGenerate">{{ run ? '重新產生' : '立即產生' }}</button>
      <button v-if="run && !isViewer" type="button" class="btn ghost" @click="doExport(run.id)">匯出 Excel</button>
    </template>
    <p v-if="err" class="err">{{ err }}</p>
    <!-- 最上一行：完成度、擋住的原因、08:30 倒數、跳到未確認 -->
    <div v-if="run && so" class="top" data-testid="dc-top">
      <b>完成 {{ done }}/{{ c.total }}</b>
      <span v-if="submitted" class="okt">已送出</span>
      <span v-else-if="!blockers.length" class="okt">可以送出</span>
      <span v-for="b in blockers" :key="b" class="blk">不能送出：{{ b }}</span>
      <span v-if="countdown" class="cd">{{ countdown }}</span>
      <button v-if="c.unconfirmed" type="button" class="lb" @click="goUnconfirmed">前往未確認項目 ›</button>
    </div>
    <p v-if="d?.lp_alert" class="err" data-testid="dc-lp-alert">{{ d.lp_alert }}</p>
    <p v-if="d?.vm_newer" class="warn" data-testid="dc-vm-newer">有較新的 VMware 資料（{{ d.vm_newer.collected_at.slice(11, 16) }}）<template v-if="!isViewer">，<button type="button" class="lb" :disabled="busy" @click="doGenerate">重新產生</button></template>
      <InfoNote>這次快照已經有人確認過，系統不會自動換掉；要用新的資料就重新產生（新的一次要重新確認，舊的那次標「已作廢」留在紀錄）。</InfoNote></p>
    <p v-if="run?.superseded" class="warn">這一次已作廢（之後重新產生過），只能看、不能送出。</p>
    <p v-for="w in (run?.warnings?.length ? run.warnings : d?.warnings_now ?? [])" :key="w" class="warn">{{ w }}</p>
    <div v-if="run" class="cgroups">
      <div class="cg">
        <div class="cgh">系統判定<span class="muted">共 {{ c.total }} 列<template v-if="SYS.some(s => s.k === q('v'))">・目前顯示 {{ shown.length }} 列</template></span><InfoNote>系統用已匯入的資料自己判的結果；五類互斥，加起來＝全部列數。</InfoNote></div>
        <div class="cards">
          <NuxtLink v-for="k in SYS" :key="k.k" class="kc" :class="[k.tone, { on: q('v') === k.k }]" :to="{ query: { ...route.query, tab: 'today', row: undefined, v: q('v') === k.k ? undefined : k.k } }">
            <span class="kl">{{ k.label }}</span><b class="kv mono">{{ c[k.k] ?? 0 }}<small> 列</small></b>
          </NuxtLink>
        </div>
      </div>
      <div v-if="so" class="cg">
        <div class="cgh">確認進度<span class="muted">共 {{ c.total }} 列<template v-if="CONF.some(s => s.k === q('v'))">・目前顯示 {{ shown.length }} 列</template></span><InfoNote>值班確認的結果；三類互斥，加起來＝全部列數。</InfoNote></div>
        <div class="cards three">
          <NuxtLink v-for="k in CONF" :key="k.k" class="kc" :class="[k.tone, { on: q('v') === k.k }]" :to="{ query: { ...route.query, tab: 'today', row: undefined, v: q('v') === k.k ? undefined : k.k } }">
            <span class="kl">{{ k.label }}</span><b class="kv mono">{{ c[k.k] ?? 0 }}<small> 列</small></b>
          </NuxtLink>
        </div>
      </div>
    </div>

    <template #tab>
      <!-- 每日檢查 -->
      <template v-if="tab === 'today'">
        <p v-if="!run" class="muted">還沒有快照。<template v-if="!isViewer">按右上「立即產生」用目前已匯入的資料產生一次。</template></p>
        <template v-else>
          <div v-if="so" class="bar blocks"><span class="muted">各區完成度：</span>
            <span v-for="b in blocks.filter(x => x.n)" :key="b.k" class="pill">{{ b.label }} {{ b.done }}/{{ b.n }}（{{ b.pct }}%）</span></div>
          <div v-if="so && !isViewer" class="actbar">
            <span>勾選 <b>{{ pickedIds.length }}</b> 列，</span>
            <select v-model="res" aria-label="確認結果"><option value="ok">正常</option><option value="bad">異常</option></select>
            <input v-model="note" class="note" list="dc-phrases" :placeholder="submitted ? '更正原因（必填）' : '異常說明（異常必填）'" aria-label="說明">
            <datalist id="dc-phrases"><option v-for="p in d.phrases" :key="p" :value="p" /></datalist>
            <button v-if="!submitted" type="button" class="btn" :disabled="busy || !pickedIds.length || !!vmErr" @click="doConfirm">確認</button>
            <button v-else type="button" class="btn" :disabled="busy || pickedIds.length !== 1" @click="doCorrect">更正</button>
            <button v-if="!submitted" type="button" class="btn ghost" :disabled="busy" @click="showPv = !showPv">確認新鮮的正常列（{{ freshOk }}）…</button>
            <span class="sp" />
            <button v-if="!submitted" type="button" class="btn" :disabled="busy || c.unconfirmed > 0" @click="doSubmit">送出</button>
            <InfoNote>「確認新鮮的正常列」會先列出會確認哪幾列、資料截至何時、哪些不會被確認與原因，再按確認。異常、無資料、前一日參考、人工列一定要逐列確認。全部確認完才能送出；送出後只能一列一列更正（要寫原因），原本的結果留在紀錄。換篩選、日期或快照時勾選會清掉。</InfoNote>
          </div>
          <div v-if="so && vmPick && !submitted" class="vmbox" data-testid="dc-vm">
            <label v-if="vmPre" class="chk"><input v-model="vm.reviewed" type="checkbox" aria-label="已檢視 Error Log／Mail"> 已檢視 Error Log／Mail</label>
            <label v-if="vmPre">異常單號<input v-model="vm.ticket" aria-label="異常單號"></label>
            <label>ESXi 台數（台）<input v-model="vm.esxi_count" inputmode="numeric" :placeholder="vmSys ? `預填 ${vmSys.esxi_count}` : ''" aria-label="ESXi 台數"></label>
            <label>LUN 最小剩餘（GB）<input v-model="vm.lun_free_gb" inputmode="decimal" :placeholder="vmSys && vmSys.lun_free_gb != null ? `預填 ${vmSys.lun_free_gb}` : ''" aria-label="LUN 最小剩餘 GB"></label>
            <label class="chk"><input v-model="vm.has_alert" type="checkbox"> 有警示</label>
            <label v-if="vm.has_alert">警示說明（必填）<input v-model="vm.alert_note" aria-label="警示說明"></label>
            <label v-if="vmOverride.length">覆寫原因（必填）<input v-model="vm.override_reason" aria-label="覆寫原因"></label>
            <span v-if="vmErr" class="err">{{ vmErr }}</span>
            <InfoNote>系統預填的列：「已檢視」一定要勾（Error Log、Mail 異常通知系統看不到）；結果用左邊的正常／異常；異常單號選填。ESXi 台數、LUN 最小剩餘、有警示留空＝照系統預填；填了跟預填不同就是覆寫，要寫原因，原值、新值、原因、誰、何時都留紀錄並寫進 Excel。系統無資料時這幾欄就是手填的退路。</InfoNote>
          </div>
          <div v-if="so && showPv && !submitted" class="pv" data-testid="dc-preview">
            <div class="bar"><b>會確認 {{ freshOk }} 列為正常</b><span class="muted">只限系統判正常、資料在門檻內、還沒確認的列；其他未確認的列列在下面與原因</span>
              <button type="button" class="btn" :disabled="busy || !freshOk" @click="doFreshOk">確認這 {{ freshOk }} 列</button><button type="button" class="lb" @click="showPv = false">取消</button></div>
            <V2Table :rows="pvRows" prefix="" row-key="id" unit="列" default-sort="take"
                     :cols="[{ k: 'code', label: '項目代號', mono: true }, { k: 'name', label: '項目' }, { k: 'take', label: '這次' }, { k: 'why', label: '資料截至／原因' }]" />
          </div>
          <V2Table v-model:selected="picked" :selectable="so && !isViewer" :rows="shown" :cols="colsNow" :export-cols="exportCols" prefix="" row-key="id" unit="列" :max="100" default-sort="gsort" empty="沒有符合的列" />
          <div v-if="detailRow" class="pv" data-testid="dc-detail">
            <div class="bar"><b>{{ detailRow.code }} {{ detailRow.name }}</b><button type="button" class="lb" @click="setQ({ row: undefined })">收起</button></div>
            <dl class="kv">
              <dt>系統判定</dt><dd>{{ detailRow.verdict_label }}</dd>
              <dt>數值</dt><dd>{{ detailRow.value_text || '—' }}</dd>
              <dt>怎麼算</dt><dd>{{ detailRow.rule ? (RULE_TEXT[detailRow.rule] ?? detailRow.rule) : '人工檢查項目，系統不判定' }}</dd>
              <dt>{{ detailRow.verdict === 'nodata' ? '為什麼沒有資料' : '系統說明' }}</dt><dd>{{ detailRow.reason || '—' }}</dd>
              <dt>資料時間</dt><dd>{{ detailRow.data_at || '—' }}<template v-if="detailRow.data_at">（{{ detailRow.age }}・{{ detailRow.fr || '—' }}）</template></dd>
              <dt>來源</dt><dd>{{ detailRow.source || '—' }}</dd>
              <dt>來源批次</dt><dd><NuxtLink v-if="detailRow.batch" class="lnk mono" :to="RELAY">{{ detailRow.batch }}</NuxtLink><template v-else>—</template></dd>
              <template v-if="isVm">
                <dt>vCenter</dt><dd>{{ detailRow.stats.vcenter }}・收集器 {{ detailRow.stats.adapter }}（最後收集 {{ detailRow.stats.adapter_last || '—' }}）</dd>
                <dt>資料存放區門檻</dt><dd>剩餘 {{ detailRow.stats.op }} {{ detailRow.stats.threshold }} {{ detailRow.stats.unit }}</dd>
              </template>
              <template v-else-if="detailRow.stats">
                <dt>範圍</dt><dd>{{ detailRow.stats.group || (detailRow.stats.groups || []).join('、') }}・{{ detailRow.stats.scope }} 台（有資料 {{ detailRow.stats.with_data }}・無資料 {{ detailRow.stats.no_data }}<template v-if="detailRow.stats.stale">，其中舊值 {{ detailRow.stats.stale }}</template>・異常 {{ detailRow.stats.bad }}<template v-if="detailRow.stats.asset_unmatched">・資產對不到 {{ detailRow.stats.asset_unmatched }}</template>）</dd>
              </template>
            </dl>
            <template v-if="cmpRows.length">
              <div class="bar"><b>手填清單比對</b>
                <button type="button" class="pill" :class="{ on: cmpSide === '手填有、群組沒有' }" @click="cmpSide = cmpSide === '手填有、群組沒有' ? '' : '手填有、群組沒有'">手填有、群組沒有 {{ detailRow.stats.manual_only?.length ?? 0 }}</button>
                <button type="button" class="pill" :class="{ on: cmpSide === '群組有、手填沒有' }" @click="cmpSide = cmpSide === '群組有、手填沒有' ? '' : '群組有、手填沒有'">群組有、手填沒有 {{ detailRow.stats.group_only?.length ?? 0 }}</button>
                <InfoNote>過渡期並行比對：設定頁的 1-xServer 手填 IP 清單 vs. WhatsUp 群組成員的 IP。對得上之後手填清單就可以清掉。</InfoNote></div>
              <V2Table :rows="cmpShown" prefix="C" row-key="__no" unit="個 IP" :cols="[{ k: 'ip', label: 'IP', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.ip)}` }, { k: 'side', label: '差在哪' }]" />
            </template>
            <div v-if="isVm" class="bar" data-testid="dc-vm-pills">
              <button v-for="p in vmPills" :key="p.kind + p.state" type="button" class="pill" :class="{ on: vmF.kind === p.kind && vmF.state === p.state }"
                      @click="vmF = vmF.kind === p.kind && vmF.state === p.state ? { kind: '', state: '' } : { kind: String(p.kind), state: String(p.state) }">{{ p.kind }}・{{ p.state }} {{ p.n }}</button>
            </div>
            <V2Table v-if="detailRows.length" :rows="detailRows" :cols="detailCols" prefix="D" row-key="__no" unit="筆" />
            <p v-else-if="detailRow.rule" class="muted">沒有逐台明細：{{ detailRow.verdict === 'ok' ? '判正常卻沒有證據明細，這是系統問題，請回報' : detailRow.reason || '—' }}</p>
          </div>
        </template>
      </template>

      <!-- 紀錄 -->
      <template v-else-if="tab === 'history'">
        <div class="bar"><label>日期 <input type="date" :value="q('day') || d?.day" aria-label="日期" @change="setQ({ day: ($event.target as HTMLInputElement).value, run: undefined })"></label>
          <InfoNote>同一天重新產生過會有好幾次；點產生時間切換。每一次產生、確認、批次確認、送出、更正都在下面的紀錄，只增不改；點項目代號看那一列。</InfoNote></div>
        <V2Table :rows="runRows" prefix="R" row-key="id" unit="次" empty="這一天沒有每日檢查"
                 :cols="[{ k: 'created_at', label: '產生時間', mono: true, link: (r: any) => `/v2/dailycheck?tab=history&day=${encodeURIComponent(d?.day || '')}&run=${r.id}` }, { k: 'trig', label: '觸發' }, { k: 'created_by', label: '產生者' }, { k: 'st', label: '狀態' }]" />
        <h3 class="h3">第 {{ run?.id ?? '—' }} 次的紀錄</h3>
        <V2Table :rows="ev" prefix="E" row-key="id" unit="筆" empty="沒有紀錄"
                 :cols="[{ k: 'at', label: '時間', mono: true }, { k: 'by', label: '誰' }, { k: 'act', label: '動作' }, { k: 'rowc', label: '項目代號', mono: true, link: (r: any) => (r.row_id ? `/v2/dailycheck?tab=today&day=${encodeURIComponent(d?.day || '')}&run=${run?.id}&row=${r.row_id}` : null) }, { k: 'txt', label: '內容' }]" />
      </template>

      <!-- 設定 -->
      <template v-else-if="tab === 'settings'">
        <p v-if="!cfg" class="muted">讀取中…</p>
        <div v-else class="form">
          <label class="chk"><input v-model="form.enabled" type="checkbox" :disabled="!isAdmin"> 排程開啟（自動產生、提醒、升級）</label>
          <label class="chk"><input v-model="form.signoff" type="checkbox" :disabled="!isAdmin"> 值班簽核<InfoNote>關（預設）＝看板：只看各項狀態，沒有勾選、確認、送出，也不發「還沒送出」的提醒與升級。開＝逐列確認後送出，07:45 提醒、08:15 升級。</InfoNote></label>
          <div class="grid">
            <label>產生時間<input v-model="form.snapshot_time" :disabled="!isAdmin"></label><label>提醒時間<input v-model="form.remind_time" :disabled="!isAdmin"></label>
            <label>升級時間<input v-model="form.escalate_time" :disabled="!isAdmin"></label><label>通知截止時間<input v-model="form.notify_stop_time" :disabled="!isAdmin"></label>
            <label>CPU 門檻 %<input v-model="form.cpu" type="number" :disabled="!isAdmin"></label><label>記憶體門檻 %<input v-model="form.mem" type="number" :disabled="!isAdmin"></label>
            <label>硬碟門檻 %<input v-model="form.disk" type="number" :disabled="!isAdmin"></label><label>SWAP 門檻 %<input v-model="form.swap" type="number" :disabled="!isAdmin"></label><label>輪詢未回報（分）<input v-model="form.poll_min" type="number" :disabled="!isAdmin"></label>
            <label>告警快照新鮮度（分）<input v-model="form.alert_fresh_min" type="number" :disabled="!isAdmin"></label><label>效能資料新鮮度（時）<input v-model="form.perf_fresh_h" type="number" :disabled="!isAdmin"></label>
          </div>
          <h3 class="h3">WhatsUp 上次輪循<InfoNote>CPU／記憶體／硬碟／輪詢四列看 WhatsUp 群組的「上次輪循」當下值（同人工檢查表 1-3～1-7）。9.26 每 30 分鐘送一批；每日檢查只用最新「完整」的一批，超過新鮮度就是無資料。「舊規則」＝前一日 W3 資料（過渡期保留，可切回）。</InfoNote></h3>
          <div class="grid">
            <label>資料來源<select v-model="form.daily_check_perf_source" :disabled="!isAdmin"><option value="lastpoll">上次輪循</option><option value="w3">舊規則（W3 前一日）</option></select></label>
            <label>批次新鮮度（分）<input v-model="form.lp_fresh_min" type="number" :disabled="!isAdmin"></label>
            <label>成員跳動門檻 %<InfoNote>群組成員跟上一次核准的比，新增＋移除超過這個比例（或成員 0 台）→ 那幾列「範圍未確認」，管理員在下面核准後才恢復判定。</InfoNote><input v-model="form.wug_member_jump_pct" type="number" :disabled="!isAdmin"></label>
            <label>硬碟列出 %<input v-model="form.disk_list" type="number" :disabled="!isAdmin"></label>
            <label>硬碟異常 %<input v-model="form.disk_alert" type="number" :disabled="!isAdmin"></label>
          </div>
          <template v-if="!cfg.limited">
            <div class="grid">
              <label>1-3／1-4 CPU、記憶體 群組<input v-model="form.g_cpu_path" :disabled="!isAdmin" aria-label="CPU 記憶體群組路徑"></label><label>群組 ID<input v-model="form.g_cpu_id" :disabled="!isAdmin" aria-label="CPU 記憶體群組 ID"></label><span />
              <label>1-5 SWAP 群組<input v-model="form.g_swap_path" :disabled="!isAdmin" aria-label="SWAP 群組路徑"></label><label>群組 ID<input v-model="form.g_swap_id" :disabled="!isAdmin" aria-label="SWAP 群組 ID"></label><span />
              <label>1-6 硬碟 群組<input v-model="form.g_disk_path" :disabled="!isAdmin" aria-label="硬碟群組路徑"></label><label>群組 ID<input v-model="form.g_disk_id" :disabled="!isAdmin" aria-label="硬碟群組 ID"></label><span />
            </div>
            <label>1-7 輪詢 群組（一行一個：完整路徑,群組ID）<InfoNote>路徑與 ID 從下面「最新完整批次」那張表抄（或按該列的「套用」）。填了 ID，收集端回報的 ID 不一樣（群組被搬移或重建）那一列就停判；只填名稱也可以，但同名群組會被收集端拒收。</InfoNote><textarea v-model="form.g_poll" rows="3" :disabled="!isAdmin" /></label>
          </template>
          <div v-if="lpst" class="lpbox" data-testid="dc-lastpoll">
            <div class="bar">
              <span>最新一批 <b class="mono">{{ lpst.latest?.collected_at ?? '沒有' }}</b><template v-if="lpst.latest">（{{ lpst.latest.age_min }} 分鐘前・{{ lpst.latest.complete ? '完整' : '不完整' }}）</template></span>
              <span>最新完整 <b class="mono">{{ lpst.latest_complete?.collected_at ?? '沒有' }}</b><template v-if="lpst.latest_complete">（{{ lpst.latest_complete.age_min }} 分鐘前）</template></span>
              <button type="button" class="lb" @click="loadLp">重新整理</button>
            </div>
            <p v-for="p in (lpst.latest && !lpst.latest.complete ? lpst.latest.problems : [])" :key="p" class="warn">不完整：{{ p }}</p>
            <V2Table :rows="lpGroups" prefix="G" row-key="__no" unit="個群組" empty="還沒有完整的批次"
                     :cols="[{ k: 'path', label: '群組（完整路徑）' }, { k: 'id', label: 'ID', mono: true }, { k: 'members', label: '台數', mono: true }, { k: 'ips', label: 'IP 數', mono: true },
                             { k: 'cpu', label: 'CPU 筆數', mono: true }, { k: 'mem', label: '記憶體筆數', mono: true }, { k: 'disk', label: '磁碟筆數', mono: true },
                             { k: 'st', label: '範圍', tone: (r: any) => (r.scope_ok ? 'prod' : 'test') }, { k: 'change_pct', label: '跟核准差 %', mono: true }, { k: 'appr', label: '上次核准' }, { k: 'act', label: '' }]">
              <template #cell-act="{ row }">
                <span class="acts">
                  <button v-if="isAdmin && row.ok" type="button" class="lb" @click="showDiff(row)">核准目前成員…</button>
                  <template v-if="isAdmin && !cfg.limited"><button type="button" class="lb" @click="useGroup(row, 'cpu')">套用 1-3</button><button type="button" class="lb" @click="useGroup(row, 'swap')">1-5</button><button type="button" class="lb" @click="useGroup(row, 'disk')">1-6</button></template>
                </span>
              </template>
            </V2Table>
            <div v-if="diff" class="pv" data-testid="dc-member-diff">
              <div class="bar"><b>{{ diff.path }}</b><span>目前 {{ diff.members }} 台・上次核准 {{ diff.approved ? `${(diff.approved.at || '').slice(0, 16)}（${diff.approved.count} 台）` : '沒有' }}</span>
                <button type="button" class="pill" :class="{ on: diffSide === '新增' }" @click="diffSide = diffSide === '新增' ? '' : '新增'">新增 {{ diff.added }}</button>
                <button type="button" class="pill" :class="{ on: diffSide === '移除' }" @click="diffSide = diffSide === '移除' ? '' : '移除'">移除 {{ diff.removed }}</button>
                <span class="sp" /><button type="button" class="btn" @click="approveMembers">核准這 {{ diff.members }} 台</button><button type="button" class="lb" @click="diff = null">取消</button></div>
              <V2Table :rows="diffRows" prefix="M" row-key="__no" unit="台" empty="跟上次核准一樣，沒有新增或移除"
                       :cols="[{ k: 'change', label: '變動', tone: (r: any) => (r.change === '新增' ? 'dr' : 'test') }, { k: 'name', label: '設備' }, { k: 'ip', label: 'IP', mono: true, link: (r: any) => (r.ip ? `/v2/machine/${encodeURIComponent(r.ip)}` : null) }, { k: 'device_id', label: 'WhatsUp ID', mono: true }]" />
            </div>
            <V2Table v-if="lpApprovals.length" :rows="lpApprovals" prefix="A" row-key="id" unit="筆" :max="10"
                     :cols="[{ k: 'at', label: '核准時間', mono: true }, { k: 'group_path', label: '群組' }, { k: 'count', label: '台數', mono: true }, { k: 'how', label: '方式' }, { k: 'by', label: '誰' }, { k: 'batch_id', label: '批次', mono: true }]" />
          </div>
          <h3 class="h3">VMware（vROps 預填）<InfoNote>正本 2-1～2-4：跳板機 每天 06:00／06:20／07:30 讀 vROps（只讀，不連 ESXi／vCenter），每座 vCenter 一列系統預填、人確認。資料存放區門檻：剩餘小於 600 GB（正本「LUN 低於 600」＝資料存放區，使用者 11-13 確認；等於 600 不算），或用量（已用÷容量，一位小數）≥ 90%（11-14 安全網，90.0 算）；任一成立就列。5.2 儲存容量、首頁、1.4、每日異動用同一份設定。</InfoNote></h3>
          <div class="grid">
            <label>資料存放區門檻<input v-model="form.vm_lun_threshold" type="number" :disabled="!isAdmin" aria-label="資料存放區門檻"></label>
            <label>單位<select v-model="form.vm_lun_unit" :disabled="!isAdmin" aria-label="資料存放區門檻單位"><option>GB</option><option>TB</option></select></label>
            <label>比較<select v-model="form.vm_lun_op" :disabled="!isAdmin" aria-label="資料存放區門檻比較"><option value="<">剩餘 &lt; 門檻</option><option value="<=">剩餘 ≤ 門檻</option></select></label>
            <label>或用量 ≥（%）<input v-model="form.vm_lun_used_pct_max" type="number" min="1" max="100" :disabled="!isAdmin" aria-label="資料存放區用量上限"></label>
            <label>批次新鮮度（分）<input v-model="form.vm_fresh_min" type="number" :disabled="!isAdmin"></label>
            <label>收集器停收（分）<InfoNote>vROps 的 vCenter 收集器最後收集比批次時間早超過這麼久＝停收，那一座無資料。</InfoNote><input v-model="form.vm_adapter_stale_min" type="number" :disabled="!isAdmin"></label>
            <label>單筆舊值（分）<input v-model="form.vm_record_stale_min" type="number" :disabled="!isAdmin"></label>
          </div>
          <div class="bar"><span>警示等級</span><label v-for="lv in VM_LEVELS" :key="lv" class="chk"><input v-model="form.vm_alert_levels" type="checkbox" :value="lv" :disabled="!isAdmin"> {{ lv }}</label></div>
          <template v-if="!cfg.limited">
            <div class="vcs" data-testid="dc-vm-vcenters">
              <div v-for="(v, i) in vmVcs" :key="i" class="vc">
                <b class="mono">V{{ i + 1 }}</b>
                <label>名稱<input v-model="v.name" :disabled="!isAdmin" aria-label="vCenter 名稱"></label>
                <label>vCenter 識別（vcid）<input v-model="v.vcid" :disabled="!isAdmin" aria-label="vCenter 識別"></label>
                <label>收集器 ID（選填）<input v-model="v.adapter_id" :disabled="!isAdmin" aria-label="收集器 ID"></label>
                <label>應有 ESXi（一行一台，名稱或 IP）<InfoNote>負責人：使用者（主機組）。這是母體：vROps 沒看到的列「缺」、清單沒有的列「多」。每次改動都留紀錄（下面「VMware 設定改動紀錄」）。</InfoNote><textarea v-model="v.expected" rows="3" :disabled="!isAdmin" /></label>
                <button v-if="isAdmin" type="button" class="lb" @click="vmVcs.splice(i, 1)">移除</button>
              </div>
              <button v-if="isAdmin" type="button" class="lb" @click="vmVcs.push({ name: '', vcid: '', adapter_id: '', expected: '' })">＋ 新增一座 vCenter</button>
            </div>
            <div class="vcs" data-testid="dc-vm-mutes">
              <span>警示靜音<InfoNote>靜音的警示照樣列在明細（標「清單靜音」），只是不算未解。比對：警示名稱相同，資源 ID 有填就只靜音那一個（空＝這個名稱全部）。理由、核准人必填；到期日最多今天＋{{ form.vm_mute_max_days ?? 90 }} 天，到了自動恢復成未解；存設定時已逾期的會自動移出並留紀錄。</InfoNote></span>
              <div v-for="(m, i) in vmMutes" :key="i" class="vc mute">
                <b class="mono">S{{ i + 1 }}</b>
                <label>警示名稱<input v-model="m.name" :disabled="!isAdmin" aria-label="警示名稱"></label>
                <label>vROps 資源 ID（選填）<input v-model="m.res_id" :disabled="!isAdmin" aria-label="資源 ID"></label>
                <label>到期日<input v-model="m.expires" type="date" :disabled="!isAdmin" aria-label="到期日"></label>
                <label>核准人<input v-model="m.approver" :disabled="!isAdmin" aria-label="核准人"></label>
                <label>理由<input v-model="m.reason" :disabled="!isAdmin" aria-label="理由"></label>
                <button v-if="isAdmin" type="button" class="lb" @click="vmMutes.splice(i, 1)">移除</button>
              </div>
              <button v-if="isAdmin" type="button" class="lb" @click="vmMutes.push({ name: '', res_id: '', expires: '', approver: '', reason: '' })">＋ 新增靜音</button>
              <label>靜音最長天數<input v-model="form.vm_mute_max_days" type="number" :disabled="!isAdmin" aria-label="靜音最長天數"></label>
            </div>
            <label>本機碟名稱規則（一行一條，規則運算式）<InfoNote>只輔助說明：本機碟以「類型 VMFS 且只掛 1 台 ESXi」判斷；類型 VMFS、只掛 1 台，且名稱規則命中或 vROps 標為本機才排除；判不出的照樣檢查門檻並列出。</InfoNote><textarea v-model="form.vm_local_patterns" rows="2" :disabled="!isAdmin" /></label>
          </template>
          <div v-if="vmst" class="lpbox" data-testid="dc-vmware">
            <div class="bar">
              <span>最新一批 <b class="mono">{{ vmst.latest?.collected_at ?? '沒有' }}</b><template v-if="vmst.latest">（{{ vmst.latest.age_min }} 分鐘前・{{ vmst.latest.complete ? '完整' : '不完整' }}）</template></span>
              <span>最新完整 <b class="mono">{{ vmst.latest_complete?.collected_at ?? '沒有' }}</b></span>
              <button type="button" class="lb" @click="loadVm">重新整理</button>
            </div>
            <p v-for="p in (vmst.latest && !vmst.latest.complete ? vmst.latest.problems : [])" :key="p" class="warn">不完整：{{ p }}</p>
            <template v-if="!vmst.limited">
              <V2Table :rows="vmSeen" prefix="V" row-key="__no" unit="座" empty="還沒有完整的批次"
                       :cols="[{ k: 'vcid', label: 'vCenter 識別（vcid）', mono: true }, { k: 'adapter', label: '收集器' }, { k: 'hosts', label: 'ESXi', mono: true }, { k: 'datastores', label: '資料存放區', mono: true }, { k: 'example', label: '例子' }, { k: 'used', label: '設定裡的' }, { k: 'act', label: '' }]">
                <template #cell-act="{ row }"><button v-if="isAdmin && !row.used" type="button" class="lb" @click="useVcid(row)">套用</button></template>
              </V2Table>
              <V2Table :rows="vmAdapters" prefix="K" row-key="__no" unit="個收集器" empty="沒有收集器資料"
                       :cols="[{ k: 'name', label: '收集器（vCenter）' }, { k: 'adapter_id', label: '收集器 ID', mono: true }, { k: 'vcid', label: 'vcid', mono: true }, { k: 'last_collected', label: '最後收集', mono: true }, { k: 'message', label: '訊息' }]" />
              <V2Table v-if="vmLog.length" :rows="vmLog" prefix="L" row-key="id" unit="筆" :max="10"
                       :cols="[{ k: 'at', label: 'VMware 設定改動時間', mono: true }, { k: 'by', label: '誰' }, { k: 'key', label: '設定' }, { k: 'txt', label: '改前 → 改後' }]" />
            </template>
          </div>
          <p v-if="cfg.limited" class="muted">IP 清單、主機清單、收件人、例外門檻只有管理員看得到。</p>
          <template v-else>
          <label>不上班日（一行一個 YYYY-MM-DD）<InfoNote>週六、週日本來就不跑；這裡填國定假日、颱風假等，排程當天不產生、不提醒、不升級。</InfoNote><textarea v-model="form.holidays" rows="3" :disabled="!isAdmin" /></label>
          <label>1-xServer 範圍（一行一個 IP）<InfoNote>CPU／記憶體／硬碟／輪詢只看這些 IP；空白＝自動用第一類業務系統的正式環境主機（業務系統分級與資產環境欄位，隨資料更新）。存在系統設定，不進版控。</InfoNote><textarea v-model="form.xserver_ips" rows="4" :disabled="!isAdmin" /></label>
          <label>板橋交易所主機（一行一台：名稱,IP）<textarea v-model="form.banqiao" rows="4" :disabled="!isAdmin" /></label>
          <label>vCenter（一行一座，VMware 人工列一座一列）<InfoNote>下面「VMware（vROps 預填）」有設定 vCenter 時改用那邊（系統預填、人確認）；這裡只在沒設定時當人工列的名稱。</InfoNote><textarea v-model="form.vcenters" rows="3" :disabled="!isAdmin" /></label>
          <label>硬碟單台例外（一行一台：IP,門檻,到期日YYYY-MM-DD,原因）<InfoNote>門檻最多 95%，原因與到期日必填；過期自動失效，快照會寫警示。</InfoNote><textarea v-model="form.disk_exceptions" rows="3" :disabled="!isAdmin" /></label>
          <label>升級通知收件人（一行一個信箱）<InfoNote>升級時寄信給這些人（用系統的寄信設定，每一封都留寄送紀錄）；空的時候升級只送到 Teams 頻道，畫面會一直顯示警示。</InfoNote><textarea v-model="form.escalate_to" rows="2" :disabled="!isAdmin" /></label>
          </template>
          <div v-if="isAdmin"><button type="button" class="btn" @click="saveCfg">儲存設定</button></div>
        </div>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.trial { padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; display: inline-flex; gap: 4px; align-items: center; }
.top { display: flex; gap: 10px 16px; align-items: center; flex-wrap: wrap; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); font-size: 14px; }
.okt { color: var(--good); } .blk { color: var(--warn-text); } .cd { color: var(--ink-soft); }
.warn { margin: 0; color: var(--warn-text); font-size: 13px; }
.err { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.cgroups { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 3fr); gap: 14px; }
@media (max-width: 1100px) { .cgroups { grid-template-columns: 1fr; } }
.cgh { display: flex; gap: 8px; align-items: center; font-weight: 700; font-size: 13.5px; margin-bottom: 6px; }
.cards { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.cards.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.kc { display: flex; flex-direction: column; gap: 4px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kl { font-size: 12.5px; color: var(--ink-soft); }
.kv { font-size: 22px; color: var(--brand-dark); }
.kv small { font-size: 12px; color: var(--muted); }
.kc.bad .kv { color: var(--bad); } .kc.warn .kv { color: var(--warn-text); } .kc.good .kv { color: var(--good); }
.actbar, .bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0; font-size: 13px; }
.actbar { padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); }
.actbar select, .actbar input, .vmbox input, .form input, .form textarea { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.actbar .note { width: 260px; max-width: 100%; }
.pill { font-size: 12px; padding: 1px 9px; border-radius: 999px; border: 1px solid var(--border-strong); color: var(--ink-aux); }
.vmbox { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; padding: 8px 10px; border: 1px dashed var(--border-strong); border-radius: 8px; font-size: 13px; }
.vmbox label { display: flex; flex-direction: column; gap: 3px; color: var(--ink-soft); }
.vmbox .chk { flex-direction: row; align-items: center; }
.pv { margin-top: 10px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--sub); }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 4px 12px; margin: 0 0 8px; font-size: 13.5px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.sp { flex: 1; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.btn:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; }
.lnk { color: var(--link); }
.form { display: flex; flex-direction: column; gap: 10px; max-width: 900px; font-size: 13px; }
.form label { display: flex; flex-direction: column; gap: 4px; color: var(--ink-soft); }
.form .chk { flex-direction: row; align-items: center; gap: 6px; }
.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.form select { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.lpbox { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--sub); }
.acts { display: inline-flex; gap: 8px; flex-wrap: wrap; }
button.pill { background: var(--card); font-family: inherit; cursor: pointer; }
.pill.on { border-color: var(--brand); color: var(--brand-dark); }
.pend { margin-left: 6px; padding: 0 6px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 11.5px; }
.vcs { display: flex; flex-direction: column; gap: 8px; }
.vc.mute { grid-template-columns: 40px repeat(5, minmax(0, 1fr)) auto; }
.vc { display: grid; grid-template-columns: 40px repeat(3, minmax(0, 1fr)) minmax(0, 2fr) auto; gap: 8px; align-items: start; padding: 8px 10px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); }
@media (max-width: 900px) { .vc, .vc.mute { grid-template-columns: 1fr; } }
</style>
