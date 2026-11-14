<script setup lang="ts">
// 9.4 CIA 補登（P2 VM 補登，試辦）。設計與走查：docs/設計_CIA補登P2_2026-11-11.md。
// 母體＝2.1「未登錄 CIA」裡的虛擬機；分類→同組綁→平均分給各類負責人（AD 名單挑人、每人上限／暫停）→每人一個 Excel（檔案登記：現行／作廢、標記已寄）
// →交回在 ⚙ 資料匯入→通過的彙整成補登包交 CIA 管理人→CIA 匯入後自動標已登記（找不到先「待確認」，連續兩次才結案）。
// 數字全由後端算；卡片／進度矩陣／催收表＝下鑽由 verify_numbers 釘住；每個數字（含 0）都點得進去。輪次放網址（round=），每個連結都帶著。
import { downloadVia } from '~/utils/importV2'
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`)
const t16 = (v: string | null | undefined) => (v ? String(v).slice(0, 16) : '—')
const IMPORT_LINK = '/v2/settings?st=import&src=cia_reg'
const NONE = '__none__'     // 網址裡代表「未分派」

const sum = ref<any>(null); const items = ref<any[] | null>(null); const pv = ref<any>(null); const batches = ref<any[] | null>(null)
const mpv = ref<any>(null); const samples = ref<any[] | null>(null); const chaseRows0 = ref<any[] | null>(null); const fileList = ref<any[] | null>(null)
const err = reactive<Record<string, string>>({})
const roundQ = computed(() => (q('round') ? Number(q('round')) : undefined))
async function loadSummary() {
  try { sum.value = await apiFetch<any>('/api/cia-reg/summary', { query: { round_id: roundQ.value } }); err.sum = '' } catch (e: any) { err.sum = `載入失敗：${errMsg(e)}` }
}
const TABS = [{ k: 'progress', label: '進度' }, { k: 'chase', label: '催收' }, { k: 'files', label: '檔案' }, { k: 'items', label: '清單' }, { k: 'make', label: '分派' },
  { k: 'backtest', label: '建議準確率' }, { k: 'merge', label: '彙整交 CIA' }, { k: 'srv', label: '彙整表比對' }, { k: 'phys', label: '實體機' }, { k: 'settings', label: '分派設定' }]
const tab = computed({ get: () => (TABS.some(t => t.k === q('tab')) ? q('tab') : 'progress'), set: (v: string) => router.replace(u(v)) })
// 網址字串；輪次（round）自動帶著，除非 extra 明確給 round
const u = (t: string, extra: Record<string, string | number | undefined> = {}): string =>
  '/v2/ciareg?' + new URLSearchParams(Object.entries({ tab: t, round: q('round') || undefined, ...extra }).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString()
async function load(t: string) {
  try {
    if (t === 'items') {
      const query: Record<string, string | number | undefined> = { round_id: roundQ.value }
      if (q('st')) query.status = q('st')
      if (q('cat')) query.category = q('cat')
      if (q('who')) query.assignee = q('who') === NONE ? '' : q('who')
      if (q('m')) query.metric = q('m')
      if (q('conf')) query.conf = q('conf')
      if (q('batch')) query.batch_id = q('batch')
      items.value = (await apiFetch<any>('/api/cia-reg/items', { query })).items
    }
    if (t === 'chase') chaseRows0.value = (await apiFetch<any>('/api/cia-reg/chase', { query: { round_id: roundQ.value } })).items
    if (t === 'files') fileList.value = (await apiFetch<any>('/api/cia-reg/files')).items
    if (t === 'merge') { batches.value = (await apiFetch<any>('/api/cia-reg/batches')).items; mpv.value = await apiFetch<any>('/api/cia-reg/merge/preview') }
    if (t === 'backtest' && q('basis')) samples.value = (await apiFetch<any>('/api/cia-reg/backtest', { query: { basis: q('basis'), outcome: q('oc') || undefined } })).items
    err[t] = ''
  } catch (e: any) { err[t] = `載入失敗：${errMsg(e)}` }
}
await Promise.all([loadSummary(), load(tab.value)])
watch(() => [q('round')], () => loadSummary())
watch(() => [tab.value, q('round'), q('st'), q('cat'), q('who'), q('m'), q('conf'), q('batch'), q('basis'), q('oc')], () => load(tab.value))
async function refreshAll() { await Promise.all([loadSummary(), load(tab.value)]) }
const names = computed<Record<string, string>>(() => {
  const m: Record<string, string> = {}
  for (const lst of Object.values(sum.value?.settings?.assignees ?? {}) as any[]) for (const p of lst) m[p.key] = p.name
  return m
})
const whoLabel = (k: string) => (k === NONE ? '（未分派）' : names.value[k] ?? k)

// ===== 輪次 =====
const rounds = computed<any[]>(() => sum.value?.rounds ?? [])
const curRound = computed(() => rounds.value.find((r: any) => String(r.id) === q('round')) ?? null)
const roundLabel = (r: any) => `第 ${r.id} 輪・${t16(r.created_at)}・${(r.categories ?? []).join('、')}・截止 ${r.deadline ?? '未設'}・完成 ${pct(r.completion)}`
function pickRound(v: string) { router.replace(u(tab.value, { round: v || undefined })) }
const dlEdit = ref(''); const dlBusy2 = ref(false)
watch(curRound, (r) => { dlEdit.value = r?.deadline ?? '' }, { immediate: true })
async function saveDeadline() {
  if (!curRound.value) return
  dlBusy2.value = true
  try { await guardedFetch<any>(`/api/cia-reg/rounds/${curRound.value.id}/deadline`, { method: 'PUT', body: { deadline: dlEdit.value || null } }); showToast('截止日已更新', 'success'); await refreshAll() } catch (e: any) { showToast(`更新失敗：${errMsg(e)}`, 'error') } finally { dlBusy2.value = false }
}

// ===== 卡片 =====
const c = computed(() => sum.value?.counts ?? null)
const CARD_KEYS: [string, string, string?][] = [['assigned', '已分派、待交回'], ['bounced', '退回重填', 'warn'], ['passed', '通過、待彙整', 'good'], ['claim', '說不登記（待你確認）', 'warn'],
  ['merged', '已彙整、等 CIA 登'], ['registered', '已登記', 'good'], ['unassigned', '待分派（沒分出去）', 'warn'], ['review', '待確認（身分）', 'warn'], ['missing', '待確認（找不到）', 'warn']]
const CARDS = computed(() => CARD_KEYS.map(([k, label, tone]) => ({ k, label, tone, v: c.value?.[k], to: u('items', { st: k }), on: tab.value === 'items' && q('st') === k && !q('m') })))

// ===== 進度 =====
const METRIC_COLS = ['total', 'returned', 'passed', 'bounced', 'remaining']
const progRows = computed(() => (sum.value?.progress ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1 })))
const progCols = computed(() => [{ k: 'category', label: '類別', link: (r: any) => u('items', { cat: r.category }) }, { k: 'assignee_label', label: '負責人', link: (r: any) => u('items', { who: r.assignee || NONE }) },
  ...METRIC_COLS.map(m => ({ k: m, label: sum.value?.metrics?.[m] ?? m, mono: true, link: (r: any) => u('items', { cat: r.category, who: r.assignee || NONE, m }) }))])
const fileRows = computed(() => (sum.value?.files ?? []).map((f: any, i: number) => ({ ...f, __no: i + 1 })))
const fileCols = [{ k: 'assignee_name', label: '負責人', link: (r: any) => u('items', { who: r.assignee, m: 'remaining' }) }, { k: 'items', label: '待填台數', mono: true, link: (r: any) => u('items', { who: r.assignee, m: 'remaining' }) },
  { k: 'parts', label: '檔數', mono: true, link: (r: any) => u('files', { who: r.assignee }) }, { k: 'bounced', label: '其中退回', mono: true, link: (r: any) => u('items', { who: r.assignee, st: 'bounced' }) }, { k: 'dl', label: '下載' }]
const dlBusy = ref('')
async function dl(path: string, name: string, key: string) {
  dlBusy.value = key
  try { await downloadVia(apiBase, path, name); if (tab.value === 'files') await load('files') } catch (e: any) { showToast(`下載失敗：${e?.message ?? '請稍後重試'}`, 'error') } finally { dlBusy.value = '' }
}
const dlPerson = (who: string, part: number, bounced = false) => dl(`/api/cia-reg/files/${encodeURIComponent(who)}.xlsx?part=${part}${bounced ? '&bounced=true' : ''}`, `CIA補登_${whoLabel(who)}_${part}.xlsx`, `${who}-${part}-${bounced}`)
const dlZip = (who?: string) => dl(`/api/cia-reg/files.zip${who ? `?assignee=${encodeURIComponent(who)}` : ''}`, `CIA補登_${who ? whoLabel(who) : '全部'}.zip`, `zip-${who ?? ''}`)

// ===== 催收 =====
const CHASE_M: [string, string, string][] = [['due', '應交', 'due'], ['returned', '已收', 'returned'], ['bounced', '退回', 'bounced'], ['passed', '通過', 'passed'], ['remaining', '還差', 'remaining'], ['resend', '待重寄', 'resend']]
const chaseRows = computed(() => (chaseRows0.value ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, gen: t16(r.last_generated), sent: t16(r.last_sent), dl2: r.deadline ?? '未設' })))
const chaseCols = [{ k: 'assignee_name', label: '負責人', link: (r: any) => u('items', { who: r.assignee }) },
  ...CHASE_M.map(([k, label, m]) => ({ k, label, mono: true, link: (r: any) => u('items', { who: r.assignee, m }) })),
  { k: 'gen', label: '檔案產生', mono: true, link: (r: any) => u('files', { who: r.assignee }) }, { k: 'sent', label: '標記已寄', mono: true, link: (r: any) => u('files', { who: r.assignee }) },
  { k: 'dl2', label: '截止日', mono: true }, { k: 'overdue_days', label: '逾期天數', mono: true, link: (r: any) => u('items', { who: r.assignee, m: 'remaining' }) }, { k: 'next', label: '下一步' }]

// ===== 檔案 =====
const fRows = computed(() => (fileList.value ?? []).filter((f: any) => !q('who') || f.assignee === q('who')).filter((f: any) => !q('fst') || f.state === q('fst'))
  .map((f: any) => ({ ...f, __no: f.id, name: f.file_name, rd: (f.rounds ?? []).map((x: number) => `第 ${x} 輪`).join('、'), fno: `${f.part}/${f.parts}${f.bounced ? '（退回檔）' : ''}`,
    ver: `v${f.max_ver}`, gen: `${t16(f.generated_at)}（${f.via}${f.generated_by ? `・${f.generated_by}` : ''}）`, sent: f.sent_at ? `${t16(f.sent_at)}・${f.sent_by ?? ''}` : '—',
    st: f.state === '作廢' ? `作廢：${f.void_why}` : '現行', sha: (f.sha256 || '').slice(0, 12) })))
const fCols = [{ k: 'assignee_name', label: '負責人', link: (r: any) => u('files', { who: r.assignee }) }, { k: 'name', label: '檔名' }, { k: 'rd', label: '輪次' }, { k: 'fno', label: '檔號' },
  { k: 'ver', label: '版本', mono: true }, { k: 'machines', label: '台數', mono: true, link: (r: any) => u('items', { who: r.assignee }) }, { k: 'gen', label: '產生（下載）', mono: true },
  { k: 'sent', label: '已寄', mono: true }, { k: 'st', label: '狀態' }, { k: 'sha', label: 'sha256', mono: true }, { k: 'act', label: '' }]
async function markSent(f: any) {
  if (!window.confirm(`標記「${f.file_name}」已寄給 ${f.assignee_name}？`)) return
  try { await guardedFetch<any>(`/api/cia-reg/files/${f.id}/sent`, { method: 'POST' }); showToast('已記錄寄出時間', 'success'); await Promise.all([load('files'), loadSummary()]) } catch (e: any) { showToast(`記錄失敗：${errMsg(e)}`, 'error') }
}

// ===== 清單 =====
const ST_LABEL = computed<Record<string, string>>(() => ({ open: '未結案', ...(sum.value?.status_labels ?? {}) }))
const M_LABEL: Record<string, string> = { due: '應交', resend: '待重寄' }
const CONF: Record<string, string> = { high: '高', mid: '中', low: '低' }
// 目前生效的條件：每個一顆「✕」可單獨拿掉（網址同步）
const chips = computed(() => {
  const out: { k: string; label: string }[] = []
  if (q('st')) out.push({ k: 'st', label: `狀態：${ST_LABEL.value[q('st')] ?? q('st')}` })
  if (q('cat')) out.push({ k: 'cat', label: `類別：${q('cat')}` })
  if (q('who')) out.push({ k: 'who', label: `負責人：${whoLabel(q('who'))}` })
  if (q('m')) out.push({ k: 'm', label: `指標：${sum.value?.metrics?.[q('m')] ?? M_LABEL[q('m')] ?? q('m')}` })
  if (q('conf')) out.push({ k: 'conf', label: `信心：${CONF[q('conf')] ?? q('conf')}` })
  if (q('batch')) out.push({ k: 'batch', label: `補登第 ${q('batch')} 批` })
  return out
})
const keep = (drop: string) => Object.fromEntries(['st', 'cat', 'who', 'm', 'conf', 'batch'].filter(k => k !== drop).map(k => [k, q(k) || undefined]))
const itemRows = computed(() => (items.value ?? []).map((i: any) => ({
  ...i, __no: i.id, who: i.assignee ? (i.assignee_name ?? i.assignee) : '（未分派）', apid: i.apid_suggest ? `${i.apid_suggest}（${i.conf_label}）` : `—（${i.conf_label || '—'}）`,
  chk: (i.check ?? []).join('；') || (i.note ?? ''), ver: `v${i.assign_ver}`, rd: `第 ${i.round_id} 輪`,
})))
const itemCols = [
  { k: 'track_code', label: '追蹤碼', mono: true }, { k: 'hostname', label: '主機名稱', link: (r: any) => `/v2/hosts?q=${encodeURIComponent(r.hostname ?? '')}` },
  { k: 'ip', label: 'IP', mono: true }, { k: 'rd', label: '輪次', link: (r: any) => u('items', { round: r.round_id }) }, { k: 'category', label: '類別', link: (r: any) => u('items', { cat: r.category }) },
  { k: 'group_key', label: '組' }, { k: 'who', label: '負責人', link: (r: any) => u('items', { who: r.assignee || NONE }) }, { k: 'ver', label: '版本', mono: true },
  { k: 'status_label', label: '狀態', link: (r: any) => u('items', { st: r.status }) }, { k: 'apid', label: '建議 APID' }, { k: 'basis_text', label: '依據' }, { k: 'chk', label: '退回原因／說明' },
  { k: 'cia_serial', label: 'CIA 序號', mono: true }, { k: 'ev', label: '紀錄' },
]
const picked = ref<Record<string, boolean>>({})
const pickedIds = computed(() => Object.keys(picked.value).filter(k => picked.value[k]).map(Number))
type Act = 'reassign' | 'withdraw' | 'claim_ok' | 'claim_no' | 'miss_gone' | 'miss_back' | 'restore'
const act = ref<Act>('reassign')
const ACT_OK: Record<Act, string[]> = { reassign: ['review', 'unassigned', 'assigned', 'bounced', 'claim'], withdraw: ['review', 'unassigned', 'assigned', 'bounced', 'claim', 'passed'],
  claim_ok: ['claim'], claim_no: ['claim'], miss_gone: ['missing'], miss_back: ['missing'], restore: ['gone'] }
const ACT_LABEL: Record<Act, string> = { reassign: '改派', withdraw: '撤出', claim_ok: '確認不登記（撤出）', claim_no: '不接受不登記（退回重填）', miss_gone: '確認資產已消失', miss_back: '找到了（恢復原狀態）', restore: '資產已消失→恢復' }
const canPick = (r: any) => ACT_OK[act.value].includes(r.status)
watch(act, () => { const ok = new Set(itemRows.value.filter(canPick).map((r: any) => String(r.id))); picked.value = Object.fromEntries(Object.entries(picked.value).filter(([k, v]) => v && ok.has(k))) })
const actWho = ref(''); const actNote = ref(''); const actBusy = ref(false)
const people = computed<{ key: string; name: string; warn?: string }[]>(() => {
  const m = new Map<string, any>()
  for (const lst of Object.values(sum.value?.settings?.assignees ?? {}) as any[]) for (const p of lst) if (!m.has(p.key)) m.set(p.key, p)
  return [...m.values()].sort((a, b) => a.name.localeCompare(b.name))
})
async function applyAct() {
  const ids = pickedIds.value
  if (!ids.length) return
  let path = '/api/cia-reg/items/reassign'; const body: any = { ids, note: actNote.value || null }
  if (act.value === 'reassign') { if (!actWho.value) { showToast('要選改派給誰', 'error'); return } body.assignee = actWho.value }
  else if (act.value === 'withdraw') { if (!actNote.value.trim()) { showToast('撤出要寫原因', 'error'); return } path = '/api/cia-reg/items/withdraw' }
  else if (act.value === 'restore') { if (!actNote.value.trim()) { showToast('恢復要寫原因', 'error'); return } path = '/api/cia-reg/items/restore' }
  else if (act.value === 'miss_gone' || act.value === 'miss_back') { path = '/api/cia-reg/items/missing'; body.gone = act.value === 'miss_gone' }
  else { path = '/api/cia-reg/items/claim'; body.accept = act.value === 'claim_ok' }
  let extra = act.value === 'reassign' ? `分派版本 +1，這 ${ids.length} 台在舊檔裡的列交回時會被拒收，請重寄新檔。` : act.value === 'miss_gone' ? '之後要恢復可用「資產已消失→恢復」。' : ''
  if (act.value === 'reassign') {
    try { const w = (await apiFetch<any>('/api/cia-reg/reassign-check', { method: 'GET', query: { assignee: actWho.value, n: ids.length } })).warning; if (w) extra += `
⚠ ${w}（仍可改派，會記在紀錄裡）` } catch (e: any) { extra += `
（檢查上限失敗：${errMsg(e)}）` }
  }
  if (!window.confirm(`${ACT_LABEL[act.value]}：${ids.length} 台？${extra}每一台都會留紀錄。`)) return
  actBusy.value = true
  try {
    const r = await guardedFetch<any>(path, { method: 'POST', body })
    showToast(`已處理 ${r.done} 台${r.skipped?.length ? `，${r.skipped.length} 台沒處理（${r.skipped[0].why}…）` : ''}${r.warning ? `（警示：${r.warning}）` : ''}`, r.skipped?.length || r.warning ? 'warn' : 'success')
    picked.value = {}; actNote.value = ''
    await refreshAll()
  } catch (e: any) { showToast(`處理失敗：${errMsg(e)}`, 'error') } finally { actBusy.value = false }
}
const evFor = ref<any>(null); const evRows = ref<any[]>([])
async function showEvents(r: any) {
  evFor.value = r
  try {
    evRows.value = (await apiFetch<any>(`/api/cia-reg/items/${r.id}/events`)).items.map((e: any, i: number) => ({
      ...e, __no: i + 1, at2: (e.at || '').slice(0, 19), who2: e.assignee ? `${whoLabel(e.assignee)}（v${e.assign_ver}）` : '—',
      src: e.source_file ? `${e.source_file}（${(e.source_sha256 || '').slice(0, 12)}…）` : '—' }))
  } catch (e: any) { showToast(`紀錄載入失敗：${errMsg(e)}`, 'error') }
}
const evCols = [{ k: 'at2', label: '時間', mono: true }, { k: 'action_label', label: '動作' }, { k: 'to_label', label: '變成' }, { k: 'by', label: '誰' },
  { k: 'who2', label: '負責人（版本）' }, { k: 'note', label: '說明' }, { k: 'src', label: '依據檔案' }]

// ===== 分派 =====
const pvBusy = ref(false); const mkBusy = ref(false); const newDeadline = ref('')
async function doPreview() {
  pvBusy.value = true
  try { pv.value = await apiFetch<any>('/api/cia-reg/preview'); err.make = '' } catch (e: any) { err.make = `預覽失敗：${errMsg(e)}` } finally { pvBusy.value = false }
}
const pvShow = computed(() => q('show') || 'round')
const spreadRows = computed(() => (pv.value?.spread ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, capT: r.cap ?? '不限', st: r.paused ? '暫停' : (r.over ? '超過上限' : (r.warn ?? '')) })))
const spreadCols = [{ k: 'name', label: '負責人' }, { k: 'existing', label: '手上還沒交完', mono: true, link: (r: any) => u('items', { who: r.key, m: 'remaining' }) },
  { k: 'new', label: '這次新增', mono: true, link: (r: any) => u('make', { show: 'round', pwho: r.key }) }, { k: 'total', label: '合計', mono: true }, { k: 'capT', label: '上限', mono: true }, { k: 'st', label: '提醒' }]
const candCols = [{ k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true }, { k: 'category', label: '類別' }, { k: 'category_why', label: '分類依據' },
  { k: 'apid_t', label: '建議 APID' }, { k: 'conf_label', label: '信心' }, { k: 'basis_text', label: '依據' }, { k: 'group_key', label: '組' }, { k: 'who', label: '分給' }, { k: 'observed', label: '觀測狀態（參考）' }]
const mapCand = (rows: any[]) => rows.map((r: any, i: number) => ({ ...r, __no: i + 1, apid_t: r.apid ? `${r.apid} ${r.apid_name ?? ''}` : '—',
  who: r.review ? `待確認：${r.review}` : (r.assignee ? (r.assignee_name ?? r.assignee) : `（沒分出去：${r.unassigned_why ?? ''}）`) }))
const pvRows = computed(() => pvShow.value === 'vm'
  ? mapCand([...(pv.value?.rows ?? []), ...(pv.value?.not_pilot_rows ?? []), ...(pv.value?.excluded ?? []).map((x: any) => ({ ...x, review: `排除：${x.reason}` }))])
  : mapCand(pvShow.value === 'notpilot' ? (pv.value?.not_pilot_rows ?? []) : (pv.value?.rows ?? [])
  .filter((r: any) => !q('pconf') || r.conf === q('pconf')).filter((r: any) => !q('pwho') || r.assignee === q('pwho'))))
const exRows = computed(() => (pv.value?.excluded ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1 })))
const exCols = [{ k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true }, { k: 'reason', label: '為什麼不在這次名單' }]
async function doCreate() {
  if (!pv.value?.in_round) return
  const over = (pv.value.spread ?? []).filter((x: any) => x.over).map((x: any) => x.name)
  if (!window.confirm(`建立分派：${pv.value.in_round} 台（試辦類別：${pv.value.pilot.join('、')}；截止日 ${newDeadline.value || '未設'}）？${over.length ? `\n注意：${over.join('、')} 超過上限。` : ''}只記錄分派，不改任何資產資料。`)) return
  mkBusy.value = true
  try {
    const r = await guardedFetch<any>('/api/cia-reg/rounds', { method: 'POST', body: { fingerprint: pv.value.fingerprint, deadline: newDeadline.value || null } })
    showToast(`已分派第 ${r.round_id} 輪：${r.items} 台`, 'success')
    pv.value = null
    router.replace(u('chase', { round: r.round_id }))
    await refreshAll()
  } catch (e: any) {
    showToast(`分派失敗：${errMsg(e)}`, 'error')
    if (e?.statusCode === 409 || e?.response?.status === 409) await doPreview()
  } finally { mkBusy.value = false }
}

// ===== 建議準確率（留一回測）=====
const bt = computed(() => sum.value?.backtest ?? null)
const btRows = computed(() => [...(bt.value?.bases ?? []), { id: 'prefill', label: '實際會預填（高信心）', ...(bt.value?.prefill ?? {}), wrong: (bt.value?.prefill?.predicted ?? 0) - (bt.value?.prefill?.correct ?? 0), coverage: bt.value?.population ? (bt.value.prefill.predicted / bt.value.population) : null, eligible: null }]
  .map((b: any, i: number) => ({ ...b, __no: i + 1, prec: pct(b.precision), cov: pct(b.coverage), ok: b.eligible === null ? '—' : b.eligible ? '可預填' : '只當建議' })))
const btCols = [{ k: 'label', label: '依據' }, { k: 'predicted', label: '有預測', mono: true, link: (r: any) => u('backtest', { basis: r.id }) },
  { k: 'correct', label: '對', mono: true, link: (r: any) => u('backtest', { basis: r.id, oc: 'correct' }) },
  { k: 'wrong', label: '錯', mono: true, link: (r: any) => u('backtest', { basis: r.id, oc: 'wrong' }) },
  { k: 'prec', label: '準確率', mono: true }, { k: 'cov', label: '涵蓋率', mono: true }, { k: 'ok', label: '用法' }]
const smpRows = computed(() => (samples.value ?? []).map((s: any) => ({ ...s, __no: s.no, oc: ({ correct: '對', wrong: '錯', none: '沒預測' } as any)[s.outcome] })))
const smpCols = [{ k: 'hostname', label: '主機名稱' }, { k: 'truth', label: 'CIA 上的 APID' }, { k: 'pred', label: '這個依據猜的' }, { k: 'oc', label: '結果' }, { k: 'all_pred', label: '各依據的猜測' }]

// ===== 彙整 =====
const mBusy = ref(false)
async function doMerge() {
  if (!mpv.value?.items) return
  if (!window.confirm(`彙整 ${mpv.value.items} 台通過的，切成 ${mpv.value.packages.length} 包交 CIA 管理人？`)) return
  mBusy.value = true
  try {
    const r = await guardedFetch<any>('/api/cia-reg/merge', { method: 'POST', body: { fingerprint: mpv.value.fingerprint } })
    showToast(`已彙整補登第 ${r.batch_id} 批：${r.items} 台、${r.packages} 包`, 'success')
    await refreshAll()
  } catch (e: any) { showToast(`彙整失敗：${errMsg(e)}`, 'error'); await load('merge') } finally { mBusy.value = false }
}
const bRows = computed(() => (batches.value ?? []).flatMap((b: any) => b.packages.map((p: any) => ({ ...p, __no: `${b.id}-${p.pkg_no}`, bid: b.id, name: `第 ${b.id} 批・第 ${p.pkg_no} 包`,
  at: t16(b.created_at), by: b.created_by, cia: b.cia_data_date ?? '—', reg: b.registered }))))
const bCols = [{ k: 'name', label: '補登包', link: (r: any) => u('items', { batch: r.bid }) }, { k: 'unit', label: '負責單位' }, { k: 'rows', label: '台數', mono: true, link: (r: any) => u('items', { batch: r.bid }) },
  { k: 'at', label: '彙整時間', mono: true }, { k: 'by', label: '誰' }, { k: 'cia', label: 'CIA 資料日', mono: true },
  { k: 'reg', label: '這批已登記', mono: true, link: (r: any) => u('items', { batch: r.bid, st: 'registered' }) }, { k: 'dl', label: 'Excel' }]

// ===== 分派設定（負責人從 AD 名單挑）=====
const cats = computed<string[]>(() => sum.value?.settings?.categories ?? [])
const assignees = ref<Record<string, any[]>>({}); const pilot = ref<string[]>([]); const setBusy = ref(false)
watch(sum, (s) => {
  if (!s?.settings) return
  assignees.value = Object.fromEntries(Object.entries(s.settings.assignees).map(([k, v]: any) => [k, v.map((p: any) => ({ ...p }))]))
  pilot.value = [...s.settings.pilot]
}, { immediate: true })
const pq = ref(''); const pres = ref<any[]>([]); const pcat = ref('WIN'); const adLoaded = ref(true)
async function searchPeople() {
  try { const r = await apiFetch<any>('/api/cia-reg/people', { query: { q: pq.value } }); pres.value = r.items; adLoaded.value = r.ad_loaded } catch (e: any) { showToast(`查詢失敗：${errMsg(e)}`, 'error') }
}
function addPerson(p: any) {
  const lst = assignees.value[pcat.value] ?? (assignees.value[pcat.value] = [])
  if (lst.some((x: any) => x.key === p.key)) { showToast(`${p.name} 已在 ${pcat.value}`, 'warn'); return }
  lst.push({ key: p.key, name: p.name, email: p.email, cap: null, paused: false })
}
const pplRows = computed(() => cats.value.flatMap(k => (assignees.value[k] ?? []).map((p: any, i: number) => ({ ...p, __no: `${k}-${i + 1}`, cat: k, idx: i }))))
const pplCols = [{ k: 'cat', label: '類別' }, { k: 'name', label: '姓名' }, { k: 'key', label: '員編', mono: true }, { k: 'email', label: '信箱' },
  { k: 'capE', label: '上限（台）' }, { k: 'pausedE', label: '暫停' }, { k: 'warn', label: '提醒' }, { k: 'rm', label: '' }]
async function saveSettings() {
  setBusy.value = true
  try {
    const body = Object.fromEntries(cats.value.map(k => [k, (assignees.value[k] ?? []).map((p: any) => ({ key: p.key, name: p.name, email: p.email, cap: p.cap === '' ? null : p.cap, paused: !!p.paused }))]))
    await guardedFetch<any>('/api/cia-reg/settings', { method: 'PUT', body: { assignees: body, pilot: pilot.value } })
    showToast('已儲存；到「分派」重新預覽會用新的設定', 'success')
    pv.value = null
    await loadSummary()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { setBusy.value = false }
}
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">9.4 CIA 補登 <span class="pilot">試辦</span></h1></template>
    <template #conclusion>
      <template v-if="c">{{ curRound ? `第 ${curRound.id} 輪：` : '全部輪次：' }}已分派 <b class="mono">{{ n(c.assigned) }}</b> 台・退回 <b class="mono">{{ n(c.bounced) }}</b>・通過 <b class="mono">{{ n(c.passed) }}</b>・已登記 <b class="mono">{{ n(c.registered) }}</b>・待重寄 <NuxtLink class="mono" :to="u('items', { m: 'resend' })">{{ n(sum?.resend) }}</NuxtLink>
        <InfoNote>把 2.1「未登錄 CIA」裡的虛擬機分給各類負責人補資料，通過系統檢查的彙整成補登包交 CIA 管理人。試辦：一次只開一類（分派設定裡選），回測與第一批交回看過再擴大。
          系統不改 CIA、也不改資產資料：負責人填的 APID 等資料不會先寫回 WEBIT3，所以這些機器在 CIA 登好、下一次匯入 CIA 之前，2.1 仍會列在「未登錄 CIA」。
          每人的 Excel 含擁有者、使用者等個人欄位（只放 CIA 需要的欄），直接以 Excel 寄給負責人（不加密、不設期限，已知風險）；寄出後請刪除寄件備份，交回的檔上傳後也請刪除。
          CIA 匯入後自動核對：找不到的機器先標「待確認（找不到）」，連續兩份不同的 CIA 檔都找不到才自動結案「資產已消失」（同一份檔重匯不重複計次；一次匯入讓兩成以上同時找不到時當可疑匯入、不計次並在上方警示）；結案後仍可在清單「資產已消失→恢復」。</InfoNote>
      </template>
      <span v-else-if="err.sum" class="err">{{ err.sum }}</span>
    </template>
    <template #actions>
      <NuxtLink class="btn ghost" :to="IMPORT_LINK">上傳負責人交回的檔</NuxtLink>
      <NuxtLink v-if="!isViewer" class="btn" :to="u('make')">分派</NuxtLink>
    </template>
    <div class="bar">
      <select :value="q('round')" aria-label="輪次" class="sel" @change="pickRound(($event.target as HTMLSelectElement).value)">
        <option value="">全部輪次</option><option v-for="r in rounds" :key="r.id" :value="String(r.id)">{{ roundLabel(r) }}</option></select>
      <template v-if="curRound">
        <span class="muted">建立 {{ t16(curRound.created_at) }}・{{ curRound.created_by ?? '—' }}・{{ n(curRound.items) }} 台・CIA 資料日 {{ curRound.cia_data_date ?? '—' }}</span>
        <label v-if="!isViewer" class="chk">截止日 <input v-model="dlEdit" type="date" class="inp"></label>
        <button v-if="!isViewer" type="button" class="mini" :disabled="dlBusy2" @click="saveDeadline">存截止日</button>
        <InfoNote>這一輪建立當下的設定快照：試辦類別 {{ (curRound.snapshot?.pilot ?? []).join('、') || '—' }}；有負責人的類別 {{ (curRound.snapshot?.categories_with_people ?? []).join('、') || '—' }}。完成率＝通過（含已彙整、已登記）÷（全部 − 撤出 − 資產已消失）。</InfoNote>
      </template>
    </div>
    <div v-if="c" class="cards">
      <NuxtLink v-for="k in CARDS" :key="k.k" class="kc" :class="[{ on: k.on }, k.tone]" :to="k.to">
        <span class="kl">{{ k.label }}</span><b class="kv mono">{{ n(k.v) }}<small> 台</small></b>
      </NuxtLink>
    </div>
    <p v-if="sum?.last_reconcile?.warning" class="warn">{{ t16(sum.last_reconcile.at) }} 核對：{{ sum.last_reconcile.warning }}</p>
    <p class="src muted">
      <span v-if="sum?.last_reconcile">上次自動核對 {{ t16(sum.last_reconcile.at) }}・CIA 檔 {{ sum.last_reconcile.file ?? '—' }}・已登記 {{ n(sum.last_reconcile.registered) }}・找不到 {{ n(sum.last_reconcile.missing) }}・資產已消失 {{ n(sum.last_reconcile.gone) }}</span>
      <span v-else>還沒有自動核對紀錄</span>
      <span>・CIA 上次匯入 {{ t16(sum?.cia_import?.at) }}（資料日 {{ sum?.cia_import?.data_date ?? '—' }}）</span>
      <NuxtLink class="lnk" :to="IMPORT_LINK">資料匯入</NuxtLink>
    </p>

    <template #tab>
      <p v-if="err[tab]" class="err">{{ err[tab] }}</p>

      <template v-if="tab === 'progress'">
        <h3 class="h3">類別 × 負責人<InfoNote>分到＝分給這個人的台數（含已通過、已登記）；交回＝至少交回過一次；通過＝通過檢查（含已彙整、已登記）；退回＝目前退回重填；還差＝還沒通過也沒撤出。每個數字（含 0）點下去就是那幾台。</InfoNote></h3>
        <V2Table :rows="progRows" :cols="progCols" prefix="G" row-key="__no" unit="格" empty="還沒有分派（先到「分派設定」挑負責人，再到「分派」）" />
        <h3 class="h3">每人的檔
          <button v-if="!isViewer && fileRows.length" type="button" class="mini" :disabled="dlBusy === 'zip-'" @click="dlZip()">{{ dlBusy === 'zip-' ? '打包中…' : '全部打包 ZIP' }}</button>
          <InfoNote>每人一個 Excel，最多 {{ sum?.settings?.per_person_cap ?? 150 }} 台一個檔，同一組不拆到兩個檔。ZIP 內含每人的檔與「派送清冊」（負責人、檔名、台數、sha256）。每次下載都會登記在「檔案」頁籤，改派或退回後舊檔會變「作廢」。</InfoNote></h3>
        <V2Table :rows="fileRows" :cols="fileCols" prefix="D" row-key="assignee" unit="人" empty="目前沒有待填的檔">
          <template #cell-dl="{ row }">
            <template v-if="!isViewer">
              <button v-for="p in (row as any).parts" :key="p" type="button" class="mini" :disabled="dlBusy === `${(row as any).assignee}-${p}-false`" @click="dlPerson((row as any).assignee, p)">{{ (row as any).parts > 1 ? `第 ${p} 檔` : '下載' }}</button>
              <button v-if="(row as any).bounced" type="button" class="mini" @click="dlPerson((row as any).assignee, 1, true)">退回檔</button>
              <button type="button" class="mini" @click="dlZip((row as any).assignee)">ZIP</button>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </V2Table>
      </template>

      <template v-else-if="tab === 'chase'">
        <h3 class="h3">催收{{ curRound ? `（第 ${curRound.id} 輪）` : '（全部輪次）' }}<InfoNote>每位負責人一列：應交＝分給他、沒撤出也沒消失的台數；已收＝至少交回過一次；待重寄＝改派或退回後還沒寄出新檔的台。檔案產生／標記已寄看「檔案」頁籤；寄出後在那裡按「標記已寄」。截止日在上方輪次選單旁設定，逾期天數＝今天減截止日（還有沒交完的才算）。每個數字點下去就是那幾台。</InfoNote></h3>
        <V2Table :rows="chaseRows" :cols="chaseCols" prefix="C" row-key="assignee" unit="人" empty="還沒有分派" />
      </template>

      <template v-else-if="tab === 'files'">
        <div class="bar"><b>產過的檔</b>
          <NuxtLink v-for="s in ['現行', '作廢']" :key="s" class="chip" :class="{ on: q('fst') === s }" :to="u('files', { who: q('who') || undefined, fst: q('fst') === s ? undefined : s })">{{ s }}</NuxtLink>
          <NuxtLink v-if="q('who')" class="chip on" :to="u('files', { fst: q('fst') || undefined })">負責人：{{ whoLabel(q('who')) }} ✕</NuxtLink>
          <span class="sp" /><NuxtLink class="lnk" :to="u('items', { m: 'resend' })">待重寄 {{ n(sum?.resend) }} 台</NuxtLink>
          <InfoNote>每下載一次（單檔或 ZIP）就登記一筆：哪一輪、第幾個檔、檔裡各台的分派版本、sha256、誰在什麼時候產生。現行＝檔裡每一台現在仍分給這個人、版本沒變、而且沒有更新的檔；改派、版本變了或有更新的檔就變「作廢」，交回時那些列也會拒收。寄出後按「標記已寄」記下時間；待重寄＝還沒被任何「現行且已寄」的檔涵蓋的台。</InfoNote></div>
        <V2Table :rows="fRows" :cols="fCols" prefix="F" row-key="id" unit="個檔" empty="還沒有產過檔">
          <template #cell-act="{ row }"><button v-if="!isViewer && (row as any).state === '現行' && !(row as any).sent_at" type="button" class="mini" @click="markSent(row)">標記已寄</button></template>
        </V2Table>
      </template>

      <template v-else-if="tab === 'items'">
        <div class="bar"><span class="muted">共 {{ n(itemRows.length) }} 台（分頁顯示；匯出＝全部）</span>
          <NuxtLink v-for="ch in chips" :key="ch.k" class="chip on" :to="u('items', keep(ch.k))" :title="`拿掉「${ch.label}」`">{{ ch.label }} ✕</NuxtLink>
          <NuxtLink v-if="chips.length" class="lnk" :to="u('items')">全部清除</NuxtLink></div>
        <div class="bar filters">
          <NuxtLink v-for="s in ['assigned', 'bounced', 'passed', 'claim', 'merged', 'registered', 'unassigned', 'review', 'missing', 'excluded', 'gone']" :key="s" class="chip" :class="{ on: q('st') === s }"
                    :to="u('items', { ...keep('st'), st: q('st') === s ? undefined : s })">{{ ST_LABEL[s] ?? s }}</NuxtLink>
          <select :value="q('cat')" aria-label="類別" @change="router.replace(u('items', { ...keep('cat'), cat: ($event.target as HTMLSelectElement).value || undefined }))">
            <option value="">全部類別</option><option v-for="x in cats" :key="x" :value="x">{{ x }}</option></select>
          <select :value="q('who')" aria-label="負責人" @change="router.replace(u('items', { ...keep('who'), who: ($event.target as HTMLSelectElement).value || undefined }))">
            <option value="">全部負責人</option><option :value="NONE">（未分派）</option><option v-for="p in people" :key="p.key" :value="p.key">{{ p.name }}</option></select>
          <select :value="q('conf')" aria-label="信心" @change="router.replace(u('items', { ...keep('conf'), conf: ($event.target as HTMLSelectElement).value || undefined }))">
            <option value="">全部信心</option><option value="high">高</option><option value="mid">中</option><option value="low">低</option></select>
        </div>
        <form v-if="!isViewer" class="actbar" @submit.prevent="applyAct">
          <span>勾選 <b>{{ pickedIds.length }}</b> 台，</span>
          <select v-model="act" aria-label="動作"><option value="reassign">改派</option><option value="withdraw">撤出（不補登了）</option>
            <option value="claim_ok">確認不登記（撤出）</option><option value="claim_no">不接受不登記（退回重填）</option>
            <option value="miss_gone">找不到：確認資產已消失</option><option value="miss_back">找不到：找到了（恢復）</option><option value="restore">資產已消失→恢復</option></select>
          <select v-if="act === 'reassign'" v-model="actWho" aria-label="改派給"><option value="">改派給…</option><option v-for="p in people" :key="p.key" :value="p.key">{{ p.name }}{{ p.warn ? `（${p.warn}）` : '' }}</option></select>
          <input v-model="actNote" class="note" :placeholder="act === 'withdraw' || act === 'restore' ? '原因（必填）' : '說明（選填）'" aria-label="說明">
          <button type="submit" class="btn" :disabled="actBusy || !pickedIds.length">{{ actBusy ? '處理中…' : '套用' }}</button>
          <InfoNote>不適用這個動作的列不能勾。改派：分派版本 +1，那幾台在舊檔裡的列交回時拒收，請把新檔給新負責人（「檔案」頁籤會標舊檔作廢、待重寄會多出那幾台）。已通過以後不能改派。每一次異動都留紀錄。</InfoNote>
        </form>
        <V2Table v-model:selected="picked" :selectable="!isViewer" :can-select="canPick" :rows="itemRows" :cols="itemCols" prefix="R" row-key="id" :max="200" empty="沒有符合的機器">
          <template #cell-ev="{ row }"><button type="button" class="mini" @click="showEvents(row)">紀錄</button></template>
        </V2Table>
        <div v-if="evFor" class="evbox">
          <div class="bar"><b>{{ evFor.track_code }}　{{ evFor.hostname }}</b><span class="muted">{{ evFor.ip }}</span><button type="button" class="mini" @click="evFor = null">關閉</button></div>
          <V2Table :rows="evRows" :cols="evCols" prefix="E" row-key="id" unit="筆紀錄" empty="沒有紀錄" />
        </div>
      </template>

      <template v-else-if="tab === 'make'">
        <div class="bar">
          <button type="button" class="btn" :disabled="pvBusy" @click="doPreview">{{ pvBusy ? '計算中…' : (pv ? '重新預覽' : '預覽分派') }}</button>
          <span class="muted">試辦類別：{{ (sum?.settings?.pilot ?? []).join('、') || '—' }}・<NuxtLink class="lnk" :to="u('settings')">改</NuxtLink></span>
          <InfoNote>預覽不寫入任何東西。確認時系統會重算一次，預覽之後資料或設定變了會請你重新預覽。同一台已在進行中的補登不會重複分派。同組一定給同一人；加上去會超過某人上限就給別人，誰都放不下就留在「待分派」並寫原因（組不拆）。</InfoNote>
        </div>
        <template v-if="pv">
          <p v-for="w in pv.warnings" :key="w" class="warn">{{ w }}</p>
          <p v-if="pv.no_owner_categories.length" class="warn">{{ pv.no_owner_categories.join('、') }} 有機器沒分出去。<NuxtLink :to="u('settings')">到分派設定</NuxtLink></p>
          <div class="funnel">
            <NuxtLink to="/v2/hosts?tab=no_cia">2.1 未登錄 CIA <b class="mono">{{ n(pv.nocia) }}</b></NuxtLink><NuxtLink :to="u('make', { show: 'vm' })">→ 虛擬機 <b class="mono">{{ n(pv.vm) }}</b></NuxtLink>
            <NuxtLink :to="u('make', { show: 'excluded' })">→ 排除 <b class="mono">{{ n(pv.excluded.length) }}</b></NuxtLink>
            <NuxtLink :to="u('make', { show: 'round' })">→ 可分派 <b class="mono">{{ n(pv.assignable) }}</b></NuxtLink>
            <NuxtLink :to="u('make', { show: 'notpilot' })">（不在試辦類別 <b class="mono">{{ n(pv.not_pilot) }}</b>）</NuxtLink>
            <NuxtLink :to="u('make', { show: 'round' })">→ 這次 <b class="mono">{{ n(pv.in_round) }}</b></NuxtLink>
            <span class="muted">非虛擬機 {{ n(pv.nonvm) }} 台留給第三批（實體機）</span>
          </div>
          <div class="bar"><span>建議 APID 信心：</span>
            <NuxtLink v-for="(v, k) in CONF" :key="k" class="chip" :class="{ on: q('pconf') === k }" :to="u('make', { show: 'round', pconf: q('pconf') === k ? undefined : k })">{{ v }} {{ pv.conf[v] }}</NuxtLink>
            <span v-if="pv.review" class="muted">・身分待確認 {{ pv.review }} 台（不分派）</span>
            <span class="sp" />
            <label v-if="!isViewer" class="chk">截止日 <input v-model="newDeadline" type="date" class="inp"></label>
            <button v-if="!isViewer" type="button" class="btn" :disabled="mkBusy || !pv.in_round" @click="doCreate">{{ mkBusy ? '分派中…' : `確認分派 ${pv.in_round} 台` }}</button></div>
          <h3 class="h3">分配結果（每人）<InfoNote>手上還沒交完＝之前輪次分給他、還在已分派或退回的台；合計超過上限會標「超過上限」。暫停的人這次不分。</InfoNote></h3>
          <V2Table :rows="spreadRows" :cols="spreadCols" prefix="P" row-key="key" unit="人" empty="沒有人分到" />
          <template v-if="pvShow === 'excluded'"><h3 class="h3">排除清單</h3><V2Table :rows="exRows" :cols="exCols" prefix="X" row-key="key" unit="台" empty="沒有排除" /></template>
          <template v-else><h3 class="h3">{{ pvShow === 'notpilot' ? '不在試辦類別' : pvShow === 'vm' ? '未登錄 CIA 的虛擬機（全部）' : '這次分派' }}<NuxtLink v-if="q('pwho')" class="chip on" :to="u('make', { show: 'round' })">只看 {{ whoLabel(q('pwho')) }} ✕</NuxtLink></h3>
            <V2Table :rows="pvRows" :cols="candCols" prefix="C" row-key="key" unit="台" :max="200" empty="沒有機器" /></template>
        </template>
      </template>

      <template v-else-if="tab === 'backtest'">
        <h3 class="h3">留一回測（CIA 已登記、APID 已知的虛擬機 {{ n(bt?.population) }} 台）<InfoNote>拿 CIA 上已經有 APID 的虛擬機，先把它的 APID 遮起來，用各個依據去猜，看猜對幾台。準確率 ≥ {{ pct(bt?.precision_min) }} 且至少 {{ bt?.min_predictions }} 次預測的依據才「可預填」（Excel 裡 APID 預先填好、黃底，仍要負責人確認）；其他依據只當建議。命名、網段、cluster 彼此相關，所以用實測準確率，不把它們當獨立證據相乘。
          這裡的數字用目前資料庫即時算。</InfoNote></h3>
        <V2Table :rows="btRows" :cols="btCols" prefix="B" row-key="id" unit="個依據" />
        <template v-if="q('basis')">
          <div class="bar"><NuxtLink :to="u('backtest')">‹ 收起</NuxtLink><b>{{ btRows.find((r: any) => r.id === q('basis'))?.label }}・{{ ({ correct: '猜對', wrong: '猜錯' } as any)[q('oc')] ?? '有預測' }}</b></div>
          <V2Table :rows="smpRows" :cols="smpCols" prefix="S" row-key="__no" unit="台" :max="200" empty="沒有" />
        </template>
      </template>

      <template v-else-if="tab === 'merge'">
        <div v-if="mpv" class="bar"><b>通過、還沒彙整 <NuxtLink :to="u('items', { st: 'passed' })">{{ n(mpv.items) }}</NuxtLink> 台</b><span class="muted">會切成 {{ mpv.packages.length }} 包（依負責單位，每包最多 300 台）</span><span class="sp" />
          <button v-if="!isViewer" type="button" class="btn" :disabled="mBusy || !mpv.items" @click="doMerge">{{ mBusy ? '彙整中…' : '彙整成補登包' }}</button>
          <InfoNote>先彙整已通過的，不用等全部人交回（分批送）。補登包照 CIA 修正包的六張工作表：說明、修正明細（補登批沒有）、補登清單（CIA 硬體頁 30 欄，資產序號留給 CIA 給號）、待填檢查、選項清單、修正後全表（補登批沒有）。</InfoNote></div>
        <V2Table :rows="bRows" :cols="bCols" prefix="K" row-key="__no" unit="包" empty="還沒有彙整過">
          <template #cell-dl="{ row }"><button v-if="!isViewer" type="button" class="mini" :disabled="dlBusy === `b${(row as any).bid}-${(row as any).pkg_no}`" @click="dl(`/api/cia-reg/batches/${(row as any).bid}/packages/${(row as any).pkg_no}.xlsx`, `CIA補登包_第${(row as any).bid}批_第${(row as any).pkg_no}包.xlsx`, `b${(row as any).bid}-${(row as any).pkg_no}`)">下載</button><span v-else class="muted">—</span></template>
        </V2Table>
      </template>

      <!-- 2.060.20（S10）：伺服器資料彙整表 vs CIA／實掃（只當證據，不改資產；匯入在 ⚙ 資料匯入） -->
      <V2SrvSheet v-else-if="tab === 'srv'" />

      <!-- 實體機補登（S1，2.061.2） -->
      <template v-else-if="tab === 'phys'"><V2CiaPhysical /></template>

      <template v-else-if="tab === 'settings'">
        <h3 class="h3">試辦類別<InfoNote>試辦期一次只開少數類別；回測與第一批交回看過再擴大。分類順序：DB → AS400 → AIX → NETWORK → WIN → LINUX → 無法判斷（先符合先分走）。</InfoNote></h3>
        <div class="bar"><label v-for="k in cats" :key="k" class="chk"><input v-model="pilot" type="checkbox" :value="k" :disabled="isViewer"> {{ k }}</label></div>
        <h3 class="h3">各類負責人<InfoNote>從 AD 人員名單挑（員編唯一、要啟用、要有信箱）；之後 AD 停用或找不到會在「提醒」標出來。同一人可負責多類。上限＝這個人手上（含之前輪次沒交完的）最多幾台，空白＝不限；暫停＝這次不分給他（例如休假）。名單存系統設定、不進版控。</InfoNote></h3>
        <div v-if="!isViewer" class="bar">
          <select v-model="pcat" aria-label="加到哪一類"><option v-for="k in cats" :key="k" :value="k">{{ k }}</option></select>
          <input v-model="pq" class="inp" placeholder="姓名、員編、信箱或部門" aria-label="找人" @keyup.enter="searchPeople">
          <button type="button" class="mini" @click="searchPeople">從 AD 找</button>
          <span v-if="!adLoaded" class="warn">還沒匯入 AD 人員名單（⚙ 資料匯入 → AD 人員名單），先匯入才能挑人</span>
        </div>
        <div v-if="pres.length" class="presults">
          <button v-for="p in pres" :key="p.key" type="button" class="mini" @click="addPerson(p)">＋ {{ p.name }}（{{ p.key }}・{{ p.dept }}）</button>
        </div>
        <V2Table :rows="pplRows" :cols="pplCols" prefix="A" row-key="__no" unit="人" empty="還沒有負責人">
          <template #cell-capE="{ row }"><input v-model.number="assignees[(row as any).cat][(row as any).idx].cap" type="number" min="0" class="num" :disabled="isViewer" :aria-label="`${(row as any).name} 上限`"></template>
          <template #cell-pausedE="{ row }"><input v-model="assignees[(row as any).cat][(row as any).idx].paused" type="checkbox" :disabled="isViewer" :aria-label="`${(row as any).name} 暫停`"></template>
          <template #cell-rm="{ row }"><button v-if="!isViewer" type="button" class="mini" @click="assignees[(row as any).cat].splice((row as any).idx, 1)">移除</button></template>
        </V2Table>
        <div v-if="!isViewer" class="bar"><button type="button" class="btn" :disabled="setBusy" @click="saveSettings">{{ setBusy ? '儲存中…' : '儲存設定' }}</button></div>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.pilot { font-size: 12px; font-weight: 600; color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 2px 8px; }
.h3 { margin: 16px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.cards { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 900px) { .cards { grid-template-columns: 1fr; } }
.kc { display: flex; flex-direction: column; gap: 4px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kl { font-size: 13px; color: var(--ink-soft); }
.kv { font-size: 28px; color: var(--brand-dark); }
.kv small { font-size: 13px; color: var(--muted); }
.kc.good .kv { color: var(--good); } .kc.warn .kv { color: var(--warn-text); }
.src { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 0; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.funnel { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--sub); }
.funnel a { color: var(--link); text-decoration: none; }
.sp { flex: 1; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; display: inline-block; }
.btn:hover:not(:disabled) { background: var(--brand-hover); }
.btn:disabled { opacity: .6; cursor: default; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; margin-right: 4px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.lnk { color: var(--link); font-size: 13px; }
.chk { font-size: 13px; color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 0 0 6px; }
.sel, .filters select, .actbar select, .actbar .note, .inp, .num, .bar select { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.sel { max-width: 100%; }
.num { width: 80px; }
.inp { width: 220px; max-width: 100%; }
.actbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; }
.actbar .note { width: 260px; max-width: 100%; }
.presults { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.evbox { margin-top: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--sub); }
</style>
