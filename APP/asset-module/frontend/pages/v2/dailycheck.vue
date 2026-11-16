<script setup lang="ts">
// 1.5 每日檢查（2.060.x 試辦）：xServer 每日檢查紀錄。設計：docs/設計_每日檢查自動化_2026-11-11.md（含 GPT 審查與挑剔使用者審查）。
// 系統 06:30 用已匯入的資料產生當天快照（判定＋數值＋資料時間＋來源批次），值班逐列確認後送出；送出後唯讀，只能更正（留紀錄）。
// 判定五種：正常／異常／無資料／前一日參考（永遠要人勾）／人工。舊資料、不完整、未設定一律無資料。
// 版面（挑剔使用者審查 10 點）：最上一行「能不能送出」＋08:30 倒數＋跳到未確認；卡片分「系統判定」「確認進度」兩組互斥（各自加總＝全部）；
// 表格預設依判定分組排（異常→無資料→前一日參考→人工→正常），項次＝檢查表順序固定不變；每一列點了看計算明細（?row=）；
// 換篩選／日期／快照就清掉勾選；批次確認先預覽；每列寫資料「N 分鐘前」與新鮮／過期；紀錄用白話。
// 2.066.x 三層分離（CLAUDE.md「每一頁分三層」，本頁是樣板）：①今天＝一行結論→要你處理→數字卡→主表→點列看依據；
// 「資料來源」頁籤＝每種資料有沒有進來（六種狀態）＋核准群組成員；②設定搬到 ⚙ 系統設定 › 每日檢查（?tab=settings 轉過去）；
// ③批次編號、收集器、原始值收在「更多」。
import { downloadVia } from '~/utils/importV2'
import { buildSources, srcKeyOf, warningSub, SRC_NEEDS_LOOK } from '~/utils/dailycheckSources'
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
const TABS = [{ k: 'today', label: '今天' }, { k: 'sources', label: '資料來源' }, { k: 'history', label: '紀錄' }]
// 舊網址 ?tab=settings（書籤、Teams 卡片連結）→ ⚙ 系統設定 › 每日檢查
if (q('tab') === 'settings') await navigateTo({ path: '/v2/settings', query: { st: 'dailycheck', sub: 'thr' } }, { replace: true })
const tab = computed({ get: () => (TABS.some(t => t.k === q('tab')) ? q('tab') : 'today'), set: (v: string) => setQ({ tab: v, v: undefined, row: undefined, src: undefined }) })
/** ⚙ 系統設定 › 每日檢查 的子頁籤；從某一列過去就帶 from，設定頁「‹ 回 1.5」回到那一列 */
const setLink = (sub: string, fromRow?: number | string | null) => `/v2/settings?st=dailycheck&sub=${sub}${fromRow ? `&from=${encodeURIComponent(String(fromRow))}` : ''}`
/** 「資料來源」頁籤並打開某一個來源 */
const srcLink = (k: string) => { const n: Record<string, string> = { ...(route.query as any), tab: 'sources', src: k }; delete n.row; delete n.v; return `/v2/dailycheck?${new URLSearchParams(n).toString()}` }
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
  if (/告警快照/.test(r.source || '')) return ms <= (th.value.alert_fresh_min ?? 10) * 60000 ? '新鮮' : '資料偏舊'
  if (/上次輪循/.test(r.source || '')) return ms <= (th.value.lp_fresh_min ?? 35) * 60000 ? '新鮮' : '資料偏舊'
  if (/vROps/.test(r.source || '')) return ms <= (th.value.vm_fresh_min ?? 120) * 60000 ? '新鮮' : '資料偏舊'
  if (/Unity/.test(r.source || '')) return ms <= (th.value.storage_fresh_h ?? 30) * 3600000 ? '新鮮' : '資料偏舊'
  if (/效能|磁碟/.test(r.source || '')) return ms <= (th.value.perf_fresh_h ?? 30) * 3600000 ? '新鮮' : '資料偏舊'
  return ''
}
const countdown = computed(() => {
  const day = run.value?.day || d.value?.day
  if (!day || day !== String(d.value?.now || '').slice(0, 10)) return ''
  const left = Math.round((toMs(`${day} 08:30:00`) - nowTick.value) / 60000)
  return left > 0 ? `距 08:30 開盤 ${Math.floor(left / 60) ? `${Math.floor(left / 60)} 小時 ` : ''}${left % 60} 分` : '已過 08:30 開盤'
})

/** 表格上只顯示一句結果：括號、分號後面的補充（範圍、有資料幾台、舊值幾台）都收在點進去的明細。 */
const shortName = (n: string) => String(n || '').replace(/（[^（）]*）\s*$/, '').trim() || String(n || '')
function headline(r: any): string {
  if (!r.value_text) return r.verdict === 'manual' ? '人工檢查' : '沒有資料'
  return String(r.value_text).split(/[（(；;]/)[0].trim() || String(r.value_text)
}
// ===== 卡片：系統判定／確認進度兩組，各自互斥、加總＝全部 =====
const c = computed(() => d.value?.counts ?? {})
const SYS = [{ k: 'bad', label: '異常', tone: 'bad' }, { k: 'nodata', label: '無資料', tone: 'warn' }, { k: 'ref', label: '前一日參考', tone: '' },
  { k: 'manual', label: '要人工看', tone: '' }, { k: 'ok', label: '正常', tone: 'good' }]
const CONF = [{ k: 'unconfirmed', label: '未確認', tone: 'warn' }, { k: 'result_bad', label: '確認為異常', tone: 'bad' }, { k: 'result_ok', label: '確認為正常', tone: 'good' }]
const GROUP: Record<string, number> = { bad: 1, nodata: 2, ref: 3, manual: 4, ok: 5 }
const VTONE: Record<string, string> = { ok: 'prod', bad: 'test', nodata: 'none', ref: 'dr', manual: 'none' }
const rows = computed(() => (d.value?.rows ?? []).map((r: any) => ({ ...r, __no: r.no, gsort: GROUP[r.verdict] ?? 9,
  at: (r.data_at || '').slice(0, 16), age: ago(r.data_at), fr: freshness(r),
  who: r.confirmed_by ? `${r.confirmed_by} ${(r.confirmed_at || '').slice(11, 16)}` : '', vt: r.value_text || (r.verdict === 'manual' ? '人工檢查' : `無資料：${r.reason || '—'}`),
  head: headline(r), old: /過期|超過門檻/.test(freshness(r)) })))
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
  { k: 'name', label: '項目' },
  { k: 'verdict_label', label: '判定', tone: (r: any) => VTONE[r.verdict], link: rowLink },
  { k: 'head', label: '結果', link: rowLink },
  { k: 'at', label: '資料時間' },
]
const SIGN_COLS = [{ k: 'result_label', label: '確認' }, { k: 'note', label: '異常說明' }, { k: 'who', label: '確認人' }]
const colsNow = computed(() => (so.value ? [...cols, ...SIGN_COLS] : cols))
const exportCols = [{ k: 'code', label: '項目代號' }, { k: 'section', label: '區塊' }, { k: 'vt', label: '完整數值' }, { k: 'age', label: '多久前' }, { k: 'fr', label: '新鮮度' }, { k: 'reason', label: '系統說明' }, { k: 'source', label: '來源' }, { k: 'batch', label: '來源批次' }]

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
  banqiao: `上次輪循查這台在 WhatsUp 的狀態：Up、沒有停止的監控、最後輪循在 ${th.value.poll_min ?? 60} 分鐘內＝正常；Down 或有停止的監控＝異常；維護中＝參考；跟告警快照對不上或查不到＝無資料。`,
  storage_unity: `Dell Unity 中繼收集（中繼機 unity_collect.ps1 v2 每天讀一次，只發 GET）：最新一批在 ${th.value.storage_fresh_h ?? 30} 小時內、系統健康值是 OK、告警讀到最後一頁且有效 0 筆（Inactive＝已解除）＝正常；健康不是 OK 或有有效告警＝異常；資料太舊、健康未知、告警沒讀完或有狀態不確定的告警＝無資料。明細列出健康原始值與每筆有效／不確定告警。`,
  ad_time: 'AD 對時中繼（9.26 跑 w32tm /monitor）：全部 DC 時間差都在門檻內＝正常；有超過＝異常；資料太舊或有 DC 沒回應＝無資料。',
  vmware: 'vROps（跳板機 每天 06:00／06:20／07:30 讀一次，只讀 vROps，不連 ESXi／vCenter）：應有 ESXi 清單（設定）對 vROps 看到的，列出缺、多、斷線、維護；資料存放區剩餘低於門檻的（本機碟＝VMFS 且只掛 1 台才排除，判不出的列出不排除）；目前未解警示（vROps 靜音、已認領、清單靜音分開列）。收集器停收、資料過期、0 筆一律無資料。永遠要人看 Error Log／Mail 後確認。',
  ...(lpMode.value ? LP_TEXT.value : {}),
}))
const detailRow = computed(() => rows.value.find((r: any) => String(r.id) === q('row')) ?? null)
// 點一列後明細出現在表格下面，常在畫面外：自動捲到明細（使用者 2026-11-15：「點下去好像沒反應」）
const detailEl = ref<HTMLElement | null>(null)
watch(() => detailRow.value?.id, (v) => { if (v != null) nextTick(() => detailEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })) })
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
    { k: 'presence', label: '說明' }, { k: 'what', label: '內容' }, { k: 'since', label: '採集時間', mono: true }]
  const base = [{ k: 'name', label: '主機', link: (r: any) => (r.serial ? `/v2/machine/${encodeURIComponent(r.serial)}` : r.ip ? `/v2/machine/${encodeURIComponent(r.ip)}` : null) },
    { k: 'ip', label: 'IP', mono: true }]
  // 上次輪循列（有 stats）：每台一筆，多「狀態」「資產」「群組」
  if (detailRow.value?.stats) return [...base, { k: 'state', label: '狀態', tone: (r: any) => ({ 異常: 'test', 參考: 'dr', 舊值: 'none', 無資料: 'none', 正常: 'prod' } as Record<string, string>)[r.state] },
    { k: 'what', label: '內容' }, { k: 'asset', label: '資產' }, { k: 'src', label: '群組' }, { k: 'since', label: '上次輪詢', mono: true }]
  return [...base, { k: 'what', label: '狀態' }, { k: 'since', label: '最後輪循', mono: true }]
})
// 證據（WhatsUp 設備 ID、查詢時間、批次…）：不攤在表上，放判斷依據與匯出（使用者 11-15：旁白太多）
const detailEv = computed(() => (detailRow.value?.detail ?? []).map((x: any) => x.ev).filter(Boolean))
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
const ACT: Record<string, string> = { generate: '產生快照', confirm: '確認', confirm_fresh_ok: '一次確認正常項目', submit: '送出', correct: '更正', superseded: '作廢' }
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
  st: r.status === 'submitted' ? `已送出 ${r.submitted_by} ${(r.submitted_at || '').slice(11, 16)}` : r.superseded ? '已作廢' : '未送出' })))

// ===== 2.064.0 VMware（vROps）／2.062.0 WhatsUp 上次輪循：批次狀態（「資料來源」頁籤與「要你處理」都用）=====
const vmst = ref<any>(null)
async function loadVm() {
  try { vmst.value = await apiFetch<any>('/api/daily-check/vmware') } catch (e: any) { showToast(`VMware 狀態載入失敗：${errMsg(e)}`, 'error') }
}
const lpst = ref<any>(null)
async function loadLp() {
  try { lpst.value = await apiFetch<any>('/api/daily-check/lastpoll') } catch (e: any) { showToast(`上次輪循狀態載入失敗：${errMsg(e)}`, 'error') }
}
onMounted(() => { loadLp(); loadVm() })
const vmSeen = computed(() => (vmst.value?.vcenters ?? []).map((x: any, i: number) => ({ ...x, __no: x.__no ?? i + 1 })))
const vmAdapters = computed(() => (vmst.value?.adapters ?? []))
const vmLog = computed(() => (vmst.value?.log ?? []).map((x: any) => ({ ...x, txt: `${JSON.stringify(x.before)} → ${JSON.stringify(x.after)}`.slice(0, 300) })))
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

// ===== 資料來源（六種狀態；純函式在 utils/dailycheckSources.ts）=====
// vm_pending 只有設定了 vCenter 清單才有值（後端 /api/daily-check）
const vmConfigured = computed(() => d.value?.vm_pending != null)
const sources = computed(() => buildSources({ rows: d.value?.rows ?? [], lpst: lpst.value, vmst: vmst.value, perfSource: d.value?.perf_source,
  vmConfigured: vmConfigured.value, lpFreshMin: th.value.lp_fresh_min, nowHM: String(d.value?.now || '').slice(11, 16), nowYMD: String(d.value?.now || '').slice(0, 10), lpAlert: d.value?.lp_alert }))
const srcRow = computed(() => sources.value.find(s => s.key === q('src')) ?? null)
const srcRowsOf = computed(() => (srcRow.value ? rows.value.filter((r: any) => srcKeyOf(r.source) === srcRow.value!.key) : []))
const srcBatches = computed(() => [...new Set(srcRowsOf.value.map((r: any) => r.batch).filter(Boolean))])

// ===== 要你處理：每件一顆按鈕，直達處理的地方 =====
interface Todo { text: string; label: string; to?: string; fn?: () => void }
const todos = computed<Todo[]>(() => {
  const t: Todo[] = []
  const canGen = !isViewer.value
  if (d.value && !run.value) t.push({ text: `${d.value.day} 還沒有產生每日檢查`, label: canGen ? '立即產生' : '看資料來源', ...(canGen ? { fn: doGenerate } : { to: srcLink('alert') }) })
  if (run.value?.superseded) t.push({ text: '這一次已作廢，只能看', label: '到最新那一次', fn: () => setQ({ run: undefined, day: undefined }) })
  if (d.value?.vm_newer) t.push({ text: `有較新的 VMware 資料（${String(d.value.vm_newer.collected_at || '').slice(11, 16)}），這次快照已有人確認過，不會自動換`, label: canGen ? '重新產生' : '看資料來源', ...(canGen ? { fn: doGenerate } : { to: srcLink('vmware') }) })
  const unscoped = (lpst.value?.groups ?? []).filter((g: any) => g.ok && !g.scope_ok)
  for (const g of unscoped) t.push({ text: `WhatsUp 群組「${g.path}」成員有變動，相關項目暫停判定`, label: '去核准', to: srcLink('lastpoll') })
  if (!unscoped.length && rows.value.some((r: any) => /範圍未確認/.test(r.reason || ''))) t.push({ text: 'WhatsUp 群組成員待核准，相關項目暫停判定', label: '去核准', to: srcLink('lastpoll') })
  if (vmst.value?.latest && !vmConfigured.value) t.push({ text: `VMware 資料已經收到（${String(vmst.value.latest.collected_at || '').slice(11, 16)}），還沒設定 vCenter 清單`, label: '去設定', to: setLink('vm') })
  for (const s of sources.value) {
    if (SRC_NEEDS_LOOK.includes(s.status)) t.push({ text: `${s.name}：${s.status}`, label: '看原因', to: srcLink(s.key) })
    else if (s.status === '待設定' && s.act === 'settings') t.push({ text: `${s.name}：待設定`, label: '去設定', to: setLink(s.sub) })
  }
  if (d.value?.lp_alert && !sources.value.some(s => s.key === 'lastpoll')) t.push({ text: String(d.value.lp_alert), label: '看原因', to: srcLink('lastpoll') })
  for (const w of (run.value?.warnings?.length ? run.value.warnings : d.value?.warnings_now ?? [])) t.push({ text: String(w), label: '去設定', to: setLink(warningSub(String(w))) })
  return t
})
/** 依據旁的「改門檻」去哪個子頁籤 */
const ruleSub = (rule: string | null | undefined) => (rule === 'vmware' ? 'vm' : rule === 'banqiao' ? 'list' : 'thr')
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">1.5 每日檢查</h1></template>
    <template #conclusion>
      <span data-testid="dc-conclusion"><template v-if="run">{{ run.day }}・共 {{ c.total ?? 0 }} 項：異常 {{ c.bad ?? 0 }}、無資料 {{ c.nodata ?? 0 }}、要你處理 {{ todos.length }} 件
        <InfoNote>快照產生於 {{ run.created_at }}（{{ run.trigger === 'schedule' ? '排程' : `人工：${run.created_by || '—'}` }}），畫面是那一刻的結果，不即時重算。
          排程 {{ d.times.snapshot_time }} 產生<template v-if="so">、{{ d.times.remind_time }} 還沒送出提醒、{{ d.times.escalate_time }} 升級</template>（排程{{ d.enabled ? '已開啟' : '未開啟' }}）。</InfoNote></template>
      <template v-else-if="d">{{ d.day }} 還沒有產生每日檢查・要你處理 {{ todos.length }} 件</template></span>
    </template>
    <template #actions>
      <button v-if="!isViewer && tab === 'today'" type="button" class="btn ghost" :disabled="busy" @click="doGenerate">{{ run ? '重新產生' : '立即產生' }}</button>
      <InfoNote v-if="!isViewer && tab === 'today'">用現在最新的資料把全部項目重算一次；原本那一次不會消失，留在「紀錄」裡<template v-if="so">（已確認的內容留在舊的那一次，新的要重新確認）</template>。</InfoNote>
      <button v-if="run && !isViewer" type="button" class="btn ghost" @click="doExport(run.id)">匯出</button>
      <NuxtLink class="lnk set" :to="setLink('thr', q('row') || null)" data-testid="dc-settings-link">⚙ 設定</NuxtLink>
    </template>
    <p v-if="err" class="err">{{ err }}</p>
    <!-- 值班簽核（設定打開才有）：完成度、擋住的原因、08:30 倒數、跳到未確認 -->
    <div v-if="run && so" class="top" data-testid="dc-top">
      <b>完成 {{ done }}/{{ c.total }}</b>
      <span v-if="submitted" class="okt">已送出</span>
      <span v-else-if="!blockers.length" class="okt">可以送出</span>
      <span v-for="b in blockers" :key="b" class="blk">不能送出：{{ b }}</span>
      <span v-if="countdown" class="cd">{{ countdown }}</span>
      <button v-if="c.unconfirmed" type="button" class="lb" @click="goUnconfirmed">前往未確認項目 ›</button>
    </div>

    <template #tab>
      <!-- 今天 -->
      <template v-if="tab === 'today'">
        <div class="todo" :class="{ ok: !todos.length }" data-testid="dc-todo">
          <b v-if="todos.length">要你處理（{{ todos.length }} 件）</b><b v-else>沒有要你處理的事</b>
          <div v-for="(t, i) in todos" :key="i" class="it">
            <span class="n mono">{{ i + 1 }}</span><span>{{ t.text }}</span><span class="sp" />
            <NuxtLink v-if="t.to" class="btn" :to="t.to">{{ t.label }}</NuxtLink>
            <button v-else type="button" class="btn" :disabled="busy" @click="t.fn && t.fn()">{{ t.label }}</button>
          </div>
        </div>
        <template v-if="run">
          <div class="cgroups">
            <div class="cg">
              <div class="cgh">系統判定<span class="muted">共 {{ c.total }} 項<template v-if="SYS.some(s => s.k === q('v'))">・目前顯示 {{ shown.length }} 項</template></span>
                <InfoNote>五種分開算，加起來＝全部項目。異常＝超過門檻。無資料＝資料沒進來、太舊或不完整，系統不判。前一日參考＝只有前一天的資料，要人看。要人工看＝系統看不到，要人去確認。正常＝有資料而且沒超過門檻。</InfoNote></div>
              <div class="cards">
                <NuxtLink v-for="k in SYS" :key="k.k" class="kc" :class="[k.tone, { on: q('v') === k.k }]" :to="{ query: { ...route.query, tab: 'today', row: undefined, v: q('v') === k.k ? undefined : k.k } }">
                  <span class="kl">{{ k.label }}</span><b class="kn mono">{{ c[k.k] ?? 0 }}<small> 項</small></b>
                </NuxtLink>
              </div>
            </div>
            <div v-if="so" class="cg">
              <div class="cgh">確認進度<span class="muted">共 {{ c.total }} 列<template v-if="CONF.some(s => s.k === q('v'))">・目前顯示 {{ shown.length }} 列</template></span><InfoNote>值班確認的結果；三類互斥，加起來＝全部列數。</InfoNote></div>
              <div class="cards three">
                <NuxtLink v-for="k in CONF" :key="k.k" class="kc" :class="[k.tone, { on: q('v') === k.k }]" :to="{ query: { ...route.query, tab: 'today', row: undefined, v: q('v') === k.k ? undefined : k.k } }">
                  <span class="kl">{{ k.label }}</span><b class="kn mono">{{ c[k.k] ?? 0 }}<small> 列</small></b>
                </NuxtLink>
              </div>
            </div>
          </div>
          <div v-if="so" class="bar blocks"><span class="muted">各區完成度：</span>
            <span v-for="b in blocks.filter(x => x.n)" :key="b.k" class="pill">{{ b.label }} {{ b.done }}/{{ b.n }}（{{ b.pct }}%）</span></div>
          <div v-if="so && !isViewer" class="actbar">
            <span>勾選 <b>{{ pickedIds.length }}</b> 列，</span>
            <select v-model="res" aria-label="確認結果"><option value="ok">正常</option><option value="bad">異常</option></select>
            <input v-model="note" class="note" list="dc-phrases" :placeholder="submitted ? '更正原因（必填）' : '異常說明（異常必填）'" aria-label="說明">
            <datalist id="dc-phrases"><option v-for="p in d.phrases" :key="p" :value="p" /></datalist>
            <button v-if="!submitted" type="button" class="btn" :disabled="busy || !pickedIds.length || !!vmErr" @click="doConfirm">確認</button>
            <button v-else type="button" class="btn" :disabled="busy || pickedIds.length !== 1" @click="doCorrect">更正</button>
            <button v-if="!submitted" type="button" class="btn ghost" :disabled="busy" @click="showPv = !showPv">一次確認 {{ freshOk }} 筆正常項目</button>
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
          <V2Table v-model:selected="picked" :selectable="so && !isViewer" :rows="shown" :cols="colsNow" :export-cols="exportCols" :search="true" prefix="" row-key="id" unit="項" :max="100" default-sort="gsort" empty="沒有符合的項目">
            <template #cell-name="{ row }"><b>{{ shortName(row.name) }}</b><div class="sm">{{ row.section }}</div></template>
            <template #cell-at="{ row }"><template v-if="row.data_at">{{ row.at.slice(5) }}<div class="sm">{{ row.age }}<span v-if="row.old" class="oldtag">資料偏舊</span></div></template><template v-else>—</template></template>
          </V2Table>
          <div v-if="detailRow" ref="detailEl" class="pv" data-testid="dc-detail">
            <div class="bar"><b>{{ detailRow.name }}</b><span class="tag" :class="'tag-' + VTONE[detailRow.verdict]">{{ detailRow.verdict_label }}</span><span>{{ detailRow.head }}</span><span class="sp" /><button type="button" class="lb" @click="setQ({ row: undefined })">收起</button></div>
            <template v-if="cmpRows.length">
              <div class="bar"><b>手填清單比對</b>
                <button type="button" class="pill" :class="{ on: cmpSide === '手填有、群組沒有' }" @click="cmpSide = cmpSide === '手填有、群組沒有' ? '' : '手填有、群組沒有'">手填有、群組沒有 {{ detailRow.stats.manual_only?.length ?? 0 }}</button>
                <button type="button" class="pill" :class="{ on: cmpSide === '群組有、手填沒有' }" @click="cmpSide = cmpSide === '群組有、手填沒有' ? '' : '群組有、手填沒有'">群組有、手填沒有 {{ detailRow.stats.group_only?.length ?? 0 }}</button>
                <InfoNote>設定裡的 1-xServer 手填 IP 清單 vs. WhatsUp 群組成員的 IP；對得上之後手填清單就可以清掉。</InfoNote></div>
              <V2Table :rows="cmpShown" prefix="C" row-key="__no" unit="個 IP" :cols="[{ k: 'ip', label: 'IP', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.ip)}` }, { k: 'side', label: '差在哪' }]" />
            </template>
            <div v-if="isVm" class="bar" data-testid="dc-vm-pills">
              <button v-for="p in vmPills" :key="p.kind + p.state" type="button" class="pill" :class="{ on: vmF.kind === p.kind && vmF.state === p.state }"
                      @click="vmF = vmF.kind === p.kind && vmF.state === p.state ? { kind: '', state: '' } : { kind: String(p.kind), state: String(p.state) }">{{ p.kind }}・{{ p.state }} {{ p.n }}</button>
            </div>
            <V2Table v-if="detailRows.length" :rows="detailRows" :cols="detailCols" :export-cols="[{ k: 'ev', label: '證據' }]" prefix="D" row-key="__no" unit="筆" />
            <p v-else-if="detailRow.rule" class="muted">沒有逐台明細：{{ detailRow.verdict === 'ok' ? '這一項沒有逐台資料' : detailRow.reason || '—' }}</p>
            <details class="more">
              <summary>判斷依據與資料來源</summary>
              <dl class="kv">
              <template v-if="detailRow.value_text && detailRow.value_text !== detailRow.head"><dt>完整數值</dt><dd>{{ detailRow.value_text }}</dd></template>
              <template v-if="detailEv.length"><dt>證據</dt><dd><div v-for="(e, i) in detailEv" :key="i">{{ e }}</div></dd></template>
              <dt>判斷依據</dt><dd>{{ detailRow.rule ? (RULE_TEXT[detailRow.rule] ?? detailRow.rule) : '人工檢查項目，系統不判定' }}
                <template v-if="isVm">（資料存放區門檻：剩餘 {{ detailRow.stats.op }} {{ detailRow.stats.threshold }} {{ detailRow.stats.unit }}）</template>
                <NuxtLink v-if="isAdmin && detailRow.rule" class="lnk" :to="setLink(ruleSub(detailRow.rule), detailRow.id)" data-testid="dc-edit-threshold">改門檻</NuxtLink></dd>
              <dt>{{ detailRow.verdict === 'nodata' ? '為什麼沒有資料' : '系統說明' }}</dt><dd>{{ detailRow.reason || '—' }}</dd>
              <dt>資料時間</dt><dd>{{ detailRow.data_at || '—' }}<template v-if="detailRow.data_at">（{{ detailRow.age }}・{{ detailRow.fr || '—' }}）</template>
                <NuxtLink v-if="srcKeyOf(detailRow.source)" class="lnk" :to="srcLink(srcKeyOf(detailRow.source))" data-testid="dc-row-source">看這個資料來源</NuxtLink></dd>
              <template v-if="detailRow.stats && !isVm">
                <dt>範圍</dt><dd>{{ detailRow.stats.group || (detailRow.stats.groups || []).join('、') }}・{{ detailRow.stats.scope }} 台（有資料 {{ detailRow.stats.with_data }}・無資料 {{ detailRow.stats.no_data }}<template v-if="detailRow.stats.stale">，其中舊值 {{ detailRow.stats.stale }}</template>・異常 {{ detailRow.stats.bad }}<template v-if="detailRow.stats.asset_unmatched">・資產對不到 {{ detailRow.stats.asset_unmatched }}</template>）</dd>
              </template>
            </dl>
              <dl class="kv">
                <dt>來源</dt><dd>{{ detailRow.source || '—' }}</dd>
                <dt>來源批次</dt><dd><NuxtLink v-if="detailRow.batch" class="lnk mono" :to="RELAY">{{ detailRow.batch }}</NuxtLink><template v-else>—</template></dd>
                <template v-if="isVm"><dt>vCenter</dt><dd>{{ detailRow.stats.vcenter }}・收集器 {{ detailRow.stats.adapter }}（最後收集 {{ detailRow.stats.adapter_last || '—' }}）</dd></template>
              </dl>
            </details>
          </div>
        </template>
      </template>

      <!-- 資料來源 -->
      <template v-else-if="tab === 'sources'">
        <div class="bar" data-testid="dc-sources"><span class="muted">每一種資料有沒有進來、多久前、要不要處理</span>
          <InfoNote>狀態只有六種：正常、過期（太久沒更新）、失敗（收集出錯）、只收到一半（「下一步」寫缺哪一部分、影響哪幾項）、沒收到、待設定。上次輪循與 VMware 看即時的批次；其他來源以這次快照當下為準，重新產生才會用最新資料重算。</InfoNote>
          <span class="sp" /><button type="button" class="lb" @click="loadLp(); loadVm(); load()">重新整理</button></div>
        <V2Table :rows="sources" prefix="D" row-key="key" unit="個來源" empty="還沒有資料來源"
                 :cols="[{ k: 'name', label: '資料來源', link: (r: any) => srcLink(r.key) }, { k: 'status', label: '狀態', tone: (r: any) => r.tone, link: (r: any) => srcLink(r.key) }, { k: 'at', label: '最後收到', mono: true }, { k: 'next', label: '下一步' }]" />
        <div v-if="srcRow" class="pv" data-testid="dc-src-detail">
          <div class="bar"><b>D{{ srcRow.__no }} {{ srcRow.name }}</b><span class="sp" /><button type="button" class="lb" @click="setQ({ src: undefined })">收起</button></div>
          <dl class="kv">
            <dt>狀態</dt><dd>{{ srcRow.status }}</dd>
            <dt>最後收到</dt><dd>{{ srcRow.at || '—' }}</dd>
            <dt>下一步</dt><dd>{{ srcRow.next }}
              <NuxtLink v-if="srcRow.act === 'setvm'" class="btn" :to="setLink('vm')">去設定 vCenter 清單</NuxtLink>
              <NuxtLink v-else-if="srcRow.act === 'settings'" class="btn" :to="setLink(srcRow.sub)">去設定</NuxtLink></dd>
          </dl>
          <!-- WhatsUp 上次輪循：群組範圍與核准（要處理的事，不是設定） -->
          <div v-if="srcRow.key === 'lastpoll' && lpst" class="lpbox" data-testid="dc-lastpoll">
            <V2Table :rows="lpGroups" prefix="G" row-key="__no" unit="個群組" empty="還沒有完整的批次"
                     :export-cols="[{ k: 'id', label: 'ID' }, { k: 'cpu', label: 'CPU 筆數' }, { k: 'mem', label: '記憶體筆數' }, { k: 'disk', label: '磁碟筆數' }]"
                     :cols="[{ k: 'path', label: 'WhatsUp 群組' }, { k: 'members', label: '台數', mono: true }, { k: 'ips', label: 'IP 數', mono: true },
                             { k: 'st', label: '範圍', tone: (r: any) => (r.scope_ok ? 'prod' : 'test') }, { k: 'change_pct', label: '成員變動 %', mono: true }, { k: 'appr', label: '上次核准' }, { k: 'act', label: '動作' }]">
              <template #cell-act="{ row }">
                <button v-if="isAdmin && row.ok" type="button" class="lb" @click="showDiff(row)">核准目前成員…</button>
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
            <details class="more">
              <summary>更多</summary>
              <div class="bar">
                <span>最新一批 <b class="mono">{{ lpst.latest?.collected_at ?? '沒有' }}</b><template v-if="lpst.latest">（{{ lpst.latest.age_min }} 分鐘前・{{ lpst.latest.complete ? '完整' : '不完整' }}・<span class="mono">{{ lpst.latest.batch_id }}</span>）</template></span>
                <span>最新完整 <b class="mono">{{ lpst.latest_complete?.collected_at ?? '沒有' }}</b><template v-if="lpst.latest_complete">（<span class="mono">{{ lpst.latest_complete.batch_id }}</span>）</template></span>
              </div>
              <p v-for="p in (lpst.latest && !lpst.latest.complete ? lpst.latest.problems : [])" :key="p" class="warn">不完整：{{ p }}</p>
              <V2Table v-if="lpApprovals.length" :rows="lpApprovals" prefix="A" row-key="id" unit="筆" :max="10"
                       :cols="[{ k: 'at', label: '核准時間', mono: true }, { k: 'group_path', label: '群組' }, { k: 'count', label: '台數', mono: true }, { k: 'how', label: '方式' }, { k: 'by', label: '誰' }, { k: 'batch_id', label: '批次', mono: true }]" />
            </details>
          </div>
          <!-- VMware：系統看到的 vCenter；清單在設定 -->
          <div v-else-if="srcRow.key === 'vmware' && vmst" class="lpbox" data-testid="dc-vmware">
            <template v-if="!vmst.limited">
              <V2Table :rows="vmSeen" prefix="V" row-key="__no" unit="座" empty="還沒有完整的批次"
                       :cols="[{ k: 'adapter', label: '收集器' }, { k: 'hosts', label: 'ESXi', mono: true }, { k: 'datastores', label: '資料存放區', mono: true }, { k: 'example', label: '例子' }]" />
              <div v-if="srcRow.act !== 'setvm'"><NuxtLink class="lnk" :to="setLink('vm')">vCenter 清單在設定</NuxtLink></div>
            </template>
            <details class="more">
              <summary>更多</summary>
              <div class="bar">
                <span>最新一批 <b class="mono">{{ vmst.latest?.collected_at ?? '沒有' }}</b><template v-if="vmst.latest">（{{ vmst.latest.age_min }} 分鐘前・{{ vmst.latest.complete ? '完整' : '不完整' }}・<span class="mono">{{ vmst.latest.batch_id }}</span>）</template></span>
                <span>最新完整 <b class="mono">{{ vmst.latest_complete?.collected_at ?? '沒有' }}</b></span>
              </div>
              <p v-for="p in (vmst.latest && !vmst.latest.complete ? vmst.latest.problems : [])" :key="p" class="warn">不完整：{{ p }}</p>
              <template v-if="!vmst.limited">
                <V2Table :rows="vmSeen" prefix="V" row-key="__no" unit="座" :cols="[{ k: 'vcid', label: 'vCenter 識別（vcid）', mono: true }, { k: 'adapter', label: '收集器' }]" />
                <V2Table :rows="vmAdapters" prefix="K" row-key="__no" unit="個收集器" empty="沒有收集器資料"
                         :cols="[{ k: 'name', label: '收集器（vCenter）' }, { k: 'adapter_id', label: '收集器 ID', mono: true }, { k: 'vcid', label: 'vcid', mono: true }, { k: 'last_collected', label: '最後收集', mono: true }, { k: 'message', label: '訊息' }]" />
                <V2Table v-if="vmLog.length" :rows="vmLog" prefix="L" row-key="id" unit="筆" :max="10"
                         :cols="[{ k: 'at', label: 'VMware 設定改動時間', mono: true }, { k: 'by', label: '誰' }, { k: 'key', label: '設定' }, { k: 'txt', label: '改前 → 改後' }]" />
              </template>
            </details>
          </div>
          <!-- 其他來源：這次快照裡用到它的項目（影響哪幾項） -->
          <template v-else-if="srcRowsOf.length">
            <V2Table :rows="srcRowsOf" prefix="" row-key="id" unit="項"
                     :cols="[{ k: 'code', label: '項目代號', mono: true, link: rowLink }, { k: 'name', label: '項目' }, { k: 'verdict_label', label: '判定', tone: (r: any) => VTONE[r.verdict] }, { k: 'at', label: '資料時間', mono: true }, { k: 'reason', label: '系統說明' }]" />
            <details v-if="srcBatches.length" class="more">
              <summary>更多</summary>
              <div class="bar"><span>來源批次</span><NuxtLink v-for="b in srcBatches" :key="String(b)" class="lnk mono" :to="RELAY">{{ b }}</NuxtLink></div>
            </details>
          </template>
        </div>
      </template>

      <!-- 紀錄 -->
      <template v-else-if="tab === 'history'">
        <div class="bar"><label>日期 <input type="date" :value="q('day') || d?.day" aria-label="日期" @change="setQ({ day: ($event.target as HTMLInputElement).value, run: undefined })"></label>
          <InfoNote>同一天重新產生過會有好幾次；點產生時間切換。每一次產生、確認、批次確認、送出、更正都在下面的紀錄，只增不改；點項目代號看那一列。</InfoNote></div>
        <V2Table :rows="runRows" prefix="R" row-key="id" unit="次" empty="這一天沒有每日檢查"
                 :cols="[{ k: 'created_at', label: '產生時間', mono: true, link: (r: any) => `/v2/dailycheck?tab=history&day=${encodeURIComponent(d?.day || '')}&run=${r.id}` }, { k: 'trig', label: '觸發' }, { k: 'created_by', label: '產生者' }, { k: 'st', label: '狀態' }]" />
        <h3 class="h3">{{ (run?.created_at || run?.generated_at || '').slice(5, 16) || '—' }} 產生的紀錄</h3>
        <V2Table :rows="ev" prefix="E" row-key="id" unit="筆" empty="沒有紀錄"
                 :cols="[{ k: 'at', label: '時間', mono: true }, { k: 'by', label: '誰' }, { k: 'act', label: '動作' }, { k: 'rowc', label: '項目代號', mono: true, link: (r: any) => (r.row_id ? `/v2/dailycheck?tab=today&day=${encodeURIComponent(d?.day || '')}&run=${run?.id}&row=${r.row_id}` : null) }, { k: 'txt', label: '內容' }]" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.top { display: flex; gap: 10px 16px; align-items: center; flex-wrap: wrap; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); font-size: 14px; }
.okt { color: var(--good); } .blk { color: var(--warn-text); } .cd { color: var(--ink-soft); }
.warn { margin: 0; color: var(--warn-text); font-size: 13px; }
.err { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; }
.sm { color: var(--ink-aux); font-size: 12px; font-weight: 400; }
.oldtag { margin-left: 6px; padding: 0 6px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); }
.pv .bar .tag { padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.cgroups { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 3fr); gap: 14px; }
@media (max-width: 1100px) { .cgroups { grid-template-columns: 1fr; } }
.cgh { display: flex; gap: 8px; align-items: center; font-weight: 700; font-size: 13.5px; margin-bottom: 6px; }
.cards { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.cards.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.kc { display: flex; flex-direction: column; gap: 4px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kl { font-size: 12.5px; color: var(--ink-soft); }
.kn { font-size: 22px; color: var(--brand-dark); }
.kn small { font-size: 12px; color: var(--muted); }
.kc.bad .kn { color: var(--bad); } .kc.warn .kn { color: var(--warn-text); } .kc.good .kn { color: var(--good); }
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
.todo { display: flex; flex-direction: column; gap: 4px; padding: 10px 14px; margin-bottom: 12px; background: var(--card); border: 1px solid var(--border); border-left: 5px solid var(--warn); border-radius: var(--radius); font-size: 13.5px; }
.todo.ok { border-left-color: var(--good); }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 4px 0; border-top: 1px solid var(--line); }
.todo .n { font-weight: 700; white-space: nowrap; }
a.btn { text-decoration: none; display: inline-block; }
.set { font-size: 13px; }
.more { margin-top: 8px; font-size: 13px; color: var(--ink-soft); }
.more summary { cursor: pointer; color: var(--link); }
</style>
