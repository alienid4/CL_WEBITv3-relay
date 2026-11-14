<script setup lang="ts">
// 9.3 CIA 修正包（2.060.0，P1 寫法統一）。設計與走查：docs/設計_CIA修正包與補登_2026-11-10.md「畫面」一節。
// 一個批次＝一次「產生修正包」，依負責單位＋資產群組切成多包（每包最多 300 列、同一台不拆兩包），一包一個 Excel 交 CIA 管理人。
// 管理人勾好的檔在 ⚙ 資料匯入 →「CIA 修正包（管理人交回）」上傳（新頁上線檢查表第 6 條：這頁只顯示來源與時間＋連結）。
// CIA 清冊每次匯入完成，系統自動核對：已套用／CIA 已另改／資產已消失。數字全由後端算；卡片＝下鑽由 verify_numbers 釘住。
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
const IMPORT_LINK = '/v2/settings?st=import&src=cia_fix'

const sum = ref<any>(null); const batches = ref<any[] | null>(null); const items = ref<any[] | null>(null)
const rules = ref<any>(null); const pv = ref<any>(null); const facets = ref<any>(null)
const err = reactive<Record<string, string>>({})
async function loadSummary() {
  try { sum.value = await apiFetch<any>('/api/cia-fix/summary'); err.sum = '' } catch (e: any) { err.sum = `載入失敗：${errMsg(e)}` }
}
const TABS = [{ k: 'batches', label: '批次' }, { k: 'items', label: '逐筆明細' }, { k: 'make', label: '產生修正包' }, { k: 'rules', label: '規則設定' },
  { k: 'dup', label: '同機多筆' }, { k: 'rack', label: '機櫃編號' }, { k: 'open', label: '未結案總覽' }]
const tab = computed({ get: () => (TABS.some(t => t.k === q('tab')) ? q('tab') : 'batches'), set: (v: string) => router.replace({ query: { tab: v } }) })
// 網址字串（V2Table 的 link 要字串）；條件都放網址，可以直接分享
const u = (t: string, extra: Record<string, string | number | undefined> = {}): string =>
  '/v2/ciafix?' + new URLSearchParams(Object.entries({ tab: t, ...extra }).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString()
async function load(t: string, force = false) {
  try {
    if (t === 'batches' && (force || !batches.value)) batches.value = (await apiFetch<any>('/api/cia-fix/batches')).items
    if (t === 'items') {
      const query: Record<string, string> = {}
      if (q('st')) query.status = q('st')
      if (q('batch')) query.batch_id = q('batch')
      if (q('pkg')) query.pkg_no = q('pkg')
      if (q('unit')) query.unit = q('unit')
      if (q('rule')) query.rule_id = q('rule')
      if (q('upload')) query.upload = q('upload')
      const r = await apiFetch<any>('/api/cia-fix/items', { query })
      items.value = r.items; facets.value = r.facets
    }
    if (t === 'rules' && (force || !rules.value)) rules.value = await apiFetch<any>('/api/cia-fix/rules')
    err[t] = ''
  } catch (e: any) { err[t] = `載入失敗：${errMsg(e)}` }
}
await Promise.all([loadSummary(), load(tab.value)])
watch(() => [tab.value, q('st'), q('batch'), q('pkg'), q('unit'), q('rule'), q('upload')], () => load(tab.value))
async function refreshAll() { await Promise.all([loadSummary(), load('batches', true), tab.value === 'items' ? load('items') : Promise.resolve()]) }

// ===== 卡片（點了＝下面那一份清單）=====
const c = computed(() => sum.value?.counts ?? null)
const CARDS = computed(() => [
  { k: 'open_batches', label: '未結案批次', unit: '批', v: c.value?.open_batches, to: u('batches', { open: 1 }), on: tab.value === 'batches' && q('open') === '1' },
  { k: 'pending', label: '待決定', unit: '筆', v: c.value?.pending, to: u('items', { st: 'pending' }), on: tab.value === 'items' && q('st') === 'pending' },
  { k: 'agreed', label: '已同意、等 CIA 套用', unit: '筆', v: c.value?.agreed, to: u('items', { st: 'agreed' }), on: tab.value === 'items' && q('st') === 'agreed' },
  { k: 'applied', label: '已套用', unit: '筆', v: c.value?.applied, to: u('items', { st: 'applied' }), on: tab.value === 'items' && q('st') === 'applied', tone: 'good' },
  { k: 'rejected', label: '不同意', unit: '筆', v: c.value?.rejected, to: u('items', { st: 'rejected' }), on: tab.value === 'items' && q('st') === 'rejected' },
  { k: 'cia_other', label: 'CIA 已另改', unit: '筆', v: c.value?.cia_other, to: u('items', { st: 'cia_other' }), on: tab.value === 'items' && q('st') === 'cia_other', tone: 'warn', sub: '待人確認：CIA 改成了別的值' },
  { k: 'gone', label: '資產已消失', unit: '筆', v: c.value?.gone, to: u('items', { st: 'gone' }), on: tab.value === 'items' && q('st') === 'gone', tone: 'warn', sub: '待人確認：CIA 清冊裡找不到這台' },
  { k: 'withdrawn', label: '已撤回', unit: '筆', v: c.value?.withdrawn, to: u('items', { st: 'withdrawn' }), on: tab.value === 'items' && q('st') === 'withdrawn', tone: 'mute' },
])
const recon = computed(() => sum.value?.last_reconcile ?? null)

// ===== 批次 =====
const batchRows = computed(() => (batches.value ?? []).filter((b: any) => q('open') !== '1' || b.is_open).map((b: any) => ({
  ...b, __no: b.id, name: `第 ${b.id} 批：${b.title ?? ''}`, at: (b.created_at || '').slice(0, 16), cia: b.cia_data_date ?? '—',
  pending: b.counts.pending, agreed: b.counts.agreed, applied: b.counts.applied, rejected: b.counts.rejected,
  confirm: b.counts.cia_other + b.counts.gone, withdrawn: b.counts.withdrawn, state: b.is_open ? '未結案' : '已結案',
})))
const batchCols = [
  { k: 'name', label: '批次', link: (r: any) => u('batches', { batch: r.id }) }, { k: 'at', label: '產生時間', mono: true }, { k: 'created_by', label: '產生者' },
  { k: 'cia', label: 'CIA 匯出日期', mono: true }, { k: 'item_count', label: '筆數', mono: true, link: (r: any) => u('items', { batch: r.id }) },
  { k: 'pkg_count', label: '包數', mono: true, link: (r: any) => u('batches', { batch: r.id }) },
  { k: 'pending', label: '待決定', mono: true, link: (r: any) => (r.pending ? u('items', { batch: r.id, st: 'pending' }) : null) },
  { k: 'agreed', label: '同意', mono: true, link: (r: any) => (r.agreed ? u('items', { batch: r.id, st: 'agreed' }) : null) },
  { k: 'applied', label: '已套用', mono: true, link: (r: any) => (r.applied ? u('items', { batch: r.id, st: 'applied' }) : null) },
  { k: 'rejected', label: '不同意', mono: true, link: (r: any) => (r.rejected ? u('items', { batch: r.id, st: 'rejected' }) : null) },
  { k: 'confirm', label: '待人確認', mono: true, link: (r: any) => (r.confirm ? u('items', { batch: r.id, st: 'confirm' }) : null) },
  { k: 'withdrawn', label: '已撤回', mono: true, link: (r: any) => (r.withdrawn ? u('items', { batch: r.id, st: 'withdrawn' }) : null) },
  { k: 'state', label: '狀態' },
]
const oneBatch = computed(() => (batches.value ?? []).find((b: any) => String(b.id) === q('batch')) ?? null)
const pkgRows = computed(() => (oneBatch.value?.packages ?? []).map((p: any) => ({ ...p, __no: p.pkg_no, name: `第 ${p.pkg_no} 包`, closed: p.rows - p.open_rows })))
const pkgCols = [
  { k: 'name', label: '包', link: (r: any) => u('items', { batch: q('batch'), pkg: r.pkg_no }) }, { k: 'unit', label: '負責單位' },
  { k: 'rows', label: '筆數', mono: true, link: (r: any) => u('items', { batch: q('batch'), pkg: r.pkg_no }) }, { k: 'assets', label: '台數', mono: true },
  { k: 'open_rows', label: '未結案', mono: true, link: (r: any) => (r.open_rows ? u('items', { batch: q('batch'), pkg: r.pkg_no, st: 'open' }) : null) },
  { k: 'closed', label: '已結案', mono: true }, { k: 'dl', label: '修正包 Excel' },
]
const dlBusy = ref('')
async function download(bid: number, pno: number) {
  dlBusy.value = `${bid}-${pno}`
  try { await downloadVia(apiBase, `/api/cia-fix/batches/${bid}/packages/${pno}.xlsx`, `CIA修正包_第${bid}批_第${pno}包.xlsx`) } catch (e: any) { showToast(`下載失敗：${e?.message ?? '請稍後重試'}`, 'error') } finally { dlBusy.value = '' }
}

// ===== 逐筆明細 =====
const ST_LABEL: Record<string, string> = { open: '未結案（待決定＋同意）', confirm: '待人確認（CIA 已另改＋資產已消失）' }
const itemTitle = computed(() => {
  const bits: string[] = []
  if (q('batch')) bits.push(`第 ${q('batch')} 批`)
  if (q('pkg')) bits.push(`第 ${q('pkg')} 包`)
  if (q('unit')) bits.push(q('unit'))
  if (q('rule')) bits.push(facets.value?.rules?.find((r: any) => r.id === q('rule'))?.label ?? q('rule'))
  if (q('st')) bits.push(ST_LABEL[q('st')] ?? sum.value?.status_labels?.[q('st')] ?? q('st'))
  if (q('upload')) bits.push(`這次交回記錄的（${q('upload').slice(0, 8)}…）`)
  return bits.join('・') || '全部'
})
const itemRows = computed(() => (items.value ?? []).map((i: any) => ({
  ...i, __no: i.id, newv: i.new_value ?? '（請人填正確值）', who: i.decided_by ? `${i.decided_by} ${(i.decided_at || '').slice(0, 16)}` : '—',
  basis: i.closed_file ? `${i.closed_file}（${(i.closed_at || '').slice(0, 16)}）` : (i.closed_at ? (i.closed_at || '').slice(0, 16) : '—'),
})))
const itemCols = [
  { k: 'track_code', label: '追蹤碼', mono: true }, { k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` },
  { k: 'hostname', label: '主機名稱' }, { k: 'field_label', label: '欄位' }, { k: 'old_value', label: '原值' }, { k: 'newv', label: '新值' },
  { k: 'rule_label', label: '規則' }, { k: 'status_label', label: '狀態' }, { k: 'reject_code', label: '不同意原因' }, { k: 'who', label: '決定' },
  { k: 'basis', label: '結案依據' }, { k: 'ev', label: '紀錄' },
]
function setFilter(k: string, v: string) {
  const cur: Record<string, string | undefined> = { batch: q('batch'), pkg: q('pkg'), unit: q('unit'), rule: q('rule'), st: q('st'), upload: q('upload') }
  cur[k] = v || undefined
  if (k === 'batch') cur.pkg = undefined           // 換批次，包號歸零
  router.replace(u('items', cur))
}
const pkgOpts = computed(() => (facets.value?.packages ?? []).filter((p: any) => !q('batch') || String(p.batch_id) === q('batch')))
const picked = ref<Record<string, boolean>>({})
const pickedIds = computed(() => Object.keys(picked.value).filter(k => picked.value[k]).map(Number))
const act = ref<'agree' | 'reject' | 'withdraw' | 'reopen'>('agree')
const ACT_OK: Record<string, string[]> = { agree: ['pending', 'agreed', 'rejected'], reject: ['pending', 'agreed', 'rejected'], withdraw: ['pending', 'agreed'], reopen: ['rejected', 'withdrawn', 'cia_other'] }
const ACT_LABEL: Record<string, string> = { agree: '標同意', reject: '標不同意', withdraw: '撤回', reopen: '重開' }
const canPick = (r: any) => ACT_OK[act.value].includes(r.status)
// 換動作時，勾著但這個動作不適用的列自動取消勾選（看不到的列不能被送出）
watch(act, () => { const ok = new Set(itemRows.value.filter(canPick).map((r: any) => String(r.id))); picked.value = Object.fromEntries(Object.entries(picked.value).filter(([k, v]) => v && ok.has(k))) })
const actCode = ref(''); const actNote = ref(''); const actBusy = ref(false)
const rejectCodes = computed<string[]>(() => rules.value?.reject_codes ?? [])
async function ensureRules() { if (!rules.value) await load('rules') }
watch(act, (a) => { if (a === 'reject') ensureRules() })
async function applyAct() {
  if (!pickedIds.value.length) return
  const ids = pickedIds.value
  let path = '/api/cia-fix/items/decide'; let body: any = { ids, note: actNote.value || null }
  if (act.value === 'agree') body = { ...body, decision: '同意' }
  else if (act.value === 'reject') { if (!actCode.value) { showToast('不同意要選原因', 'error'); return } body = { ...body, decision: '不同意', reject_code: actCode.value } }
  else { if (!actNote.value.trim()) { showToast('要填原因', 'error'); return } path = `/api/cia-fix/items/${act.value}` }
  if (!window.confirm(`${ACT_LABEL[act.value]}：${ids.length} 筆？${act.value === 'reject' ? `（原因：${actCode.value}）` : ''}每一筆都會留紀錄。`)) return
  actBusy.value = true
  try {
    const r = await guardedFetch<any>(path, { method: 'POST', body })
    showToast(`已處理 ${r.updated} 筆${r.skipped?.length ? `，${r.skipped.length} 筆沒處理（${r.skipped[0].why}…）` : ''}`, r.skipped?.length ? 'warn' : 'success', r.skipped?.length ? 10000 : undefined)
    picked.value = {}; actNote.value = ''
    await refreshAll()
  } catch (e: any) { showToast(`處理失敗：${errMsg(e)}`, 'error') } finally { actBusy.value = false }
}
const evFor = ref<any>(null); const evRows = ref<any[]>([])
async function showEvents(r: any) {
  evFor.value = r
  try {
    evRows.value = (await apiFetch<any>(`/api/cia-fix/items/${r.id}/events`)).items.map((e: any, i: number) => ({
      ...e, __no: i + 1, at2: (e.at || '').slice(0, 19), chg: `${e.from_label ?? '—'} → ${e.to_label}`, src: e.source_file ? `${e.source_file}（${(e.source_sha256 || '').slice(0, 12)}…）` : '—',
    }))
  } catch (e: any) { showToast(`紀錄載入失敗：${errMsg(e)}`, 'error') }
}
const evCols = [{ k: 'at2', label: '時間', mono: true }, { k: 'action_label', label: '動作' }, { k: 'chg', label: '狀態' }, { k: 'by', label: '誰' },
  { k: 'reject_code', label: '不同意原因' }, { k: 'note', label: '備註' }, { k: 'src', label: '依據檔案' }, { k: 'cia_value', label: '當時 CIA 值' }]

// ===== 產生修正包 =====
const resend = ref(false); const pvBusy = ref(false); const mkBusy = ref(false)
async function doPreview() {
  pvBusy.value = true
  try { pv.value = await apiFetch<any>('/api/cia-fix/preview', { query: { resend_rejected: resend.value } }); err.make = '' } catch (e: any) { err.make = `預覽失敗：${errMsg(e)}` } finally { pvBusy.value = false }
}
watch(resend, () => { if (pv.value) doPreview() })
const pvRule = computed(() => q('rule'))
const pvRuleRows = computed(() => (pv.value?.rules ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, fields_t: r.fields.join('、') })))
const pvRuleCols = [{ k: 'label', label: '規則' }, { k: 'fields_t', label: '欄位' },
  { k: 'count', label: '這次會列', mono: true, link: (r: any) => (r.count ? u('make', { rule: r.rule_id }) : null) },
  { k: 'in_open', label: '已在未結案批次', mono: true }, { k: 'was_rejected', label: '曾被不同意（不再送）', mono: true }]
const pvPkgRows = computed(() => (pv.value?.packages ?? []).map((p: any) => ({ ...p, __no: p.pkg_no, name: `第 ${p.pkg_no} 包` })))
const pvPkgCols = [{ k: 'name', label: '包' }, { k: 'unit', label: '負責單位' }, { k: 'rows', label: '筆數', mono: true }, { k: 'assets', label: '台數', mono: true }]
const pvRows = computed(() => (pv.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, newv: r.new_value ?? '（請人填正確值）', pkg: `第 ${r.pkg_no} 包` }))
  .filter((r: any) => !pvRule.value || r.rule_id === pvRule.value))
const pvCols = [{ k: 'asset_serial', label: '資產序號', mono: true }, { k: 'hostname', label: '主機名稱' }, { k: 'field_label', label: '欄位' },
  { k: 'old_value', label: '原值' }, { k: 'newv', label: '新值' }, { k: 'rule_label', label: '規則' }, { k: 'unit', label: '負責單位' }, { k: 'asset_group', label: '資產群組' }, { k: 'pkg', label: '包' }]
async function doCreate() {
  if (!pv.value?.total) return
  if (!window.confirm(`產生修正包：${pv.value.total} 筆、${pv.value.packages.length} 包？（只記錄建議，不會改任何資產資料）`)) return
  mkBusy.value = true
  try {
    const r = await guardedFetch<any>('/api/cia-fix/batches', { method: 'POST', body: { fingerprint: pv.value.fingerprint, resend_rejected: resend.value } })
    showToast(`已產生第 ${r.batch_id} 批：${r.items} 筆、${r.packages} 包`, 'success')
    pv.value = null
    await refreshAll()
    router.replace(u('batches', { batch: r.batch_id }))
  } catch (e: any) {
    showToast(`產生失敗：${errMsg(e)}`, 'error')
    if (e?.statusCode === 409 || e?.response?.status === 409) await doPreview()
  } finally { mkBusy.value = false }
}

// ===== 同機多筆（S3，2.061.1）：案件清單共用 V2CiaCases，逐欄確認在 V2CiaDupDetail =====
// 分類＝2.060.7 重複登記分類（堆疊／機箱元件不進來）；主會話 11-11 定：預設只看疑似真重複，篩選可看其他兩類
const DUP_CATS = [{ k: 'suspect', label: '疑似真重複' }, { k: 'multi_system', label: '一機多系統' }, { k: 'split', label: 'VIP／用途分列' }]
const dupCols = [{ k: 'n', label: '筆數', mono: true }, { k: 'ndiff', label: '不同欄位', mono: true }, { k: 'kindl', label: '分類' }, { k: 'keep', label: '保留' }]
const dupRow = (x: any) => ({ n: x.detail?.serials?.length ?? 0, ndiff: x.detail?.diffs?.length ?? 0, kindl: x.detail?.dup_kind_label ?? '—',
  keep: x.decision?.keep ?? x.detail?.keep ?? '—' })

// ===== 規則設定 =====
const ruleRows = computed(() => (rules.value?.rules ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, fields_t: r.fields.join('、'),
  how: r.kind === 'setting_map' ? '對照表在下方（系統設定，不進版控）' : r.kind === 'value_map' ? Object.entries(r.map ?? {}).filter(([k, v]) => k !== v).map(([k, v]) => `${k}→${v}`).join('、')
    : r.kind === 'blank_text' ? `符合 ${(r.patterns ?? []).join('、')}＋下方文字清單 → 新值留空` : '格式不對就列出、新值留空' })))
const ruleCols = [{ k: 'label', label: '規則' }, { k: 'fields_t', label: '欄位' }, { k: 'how', label: '怎麼判' }, { k: 'reason', label: '給管理人看的原因' }]
const alias = ref<Record<string, string>>({}); const tplText = ref(''); const setBusy = ref(false)
watch(rules, (r) => { if (r) { alias.value = { ...(r.settings?.room_alias ?? {}) }; tplText.value = (r.settings?.template_texts ?? []).join('\n') } }, { immediate: true })
// 2.060.16（11-11 .14 實測：輸入時整頁跳動，點擊落到「儲存設定」把誤填的一格存進去）：欄位改成離開才更新（.lazy）、
// 儲存前列出「這次會改哪些寫法、各幾筆」確認，按鈕旁顯示未儲存幾項——存之前看得到、按錯可以取消。
const savedAlias = computed<Record<string, string>>(() => rules.value?.settings?.room_alias ?? {})
const aliasDiff = computed(() => {
  const cnt: Record<string, number> = Object.fromEntries((rules.value?.locations ?? []).map((l: any) => [l.value, l.count]))
  const keys = new Set([...Object.keys(savedAlias.value), ...Object.keys(alias.value)])
  return [...keys].map(k => ({ k, from: (savedAlias.value[k] ?? '').trim(), to: (alias.value[k] ?? '').trim(), n: cnt[k] ?? 0 }))
    .filter(d => d.from !== d.to).sort((a, b) => a.k.localeCompare(b.k))
})
const norm = (xs: string[]) => xs.map(x => x.trim()).filter(Boolean).join('\n')
const tplDirty = computed(() => norm(tplText.value.split('\n')) !== norm(rules.value?.settings?.template_texts ?? []))
const locRows = computed(() => (rules.value?.locations ?? []).map((l: any, i: number) => ({ ...l, __no: i + 1 })))
const locCols = [{ k: 'value', label: 'CIA 現在的寫法' }, { k: 'count', label: '筆數', mono: true }, { k: 'to', label: '統一成（留空＝不改）' }]
const stds = computed(() => [...new Set(Object.values(alias.value).filter(Boolean))].sort())
async function saveSettings() {
  if (!aliasDiff.value.length && !tplDirty.value) { showToast('沒有變更', 'info'); return }
  const lines = aliasDiff.value.map(d => `・${d.k}（${d.n} 筆）：${d.from || '（不改）'} → ${d.to || '（不改）'}`)
  if (!window.confirm(`儲存機房對照${tplDirty.value ? '與範本提示文字' : ''}？\n${lines.join('\n')}${tplDirty.value ? '\n・範本提示文字有改' : ''}`)) return
  setBusy.value = true
  try {
    await guardedFetch<any>('/api/cia-fix/settings', { method: 'PUT', body: { room_alias: alias.value, template_texts: tplText.value.split('\n') } })
    showToast('已儲存；按「產生修正包 → 預覽」會用新的設定重算', 'success')
    await load('rules', true); pv.value = null
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { setBusy.value = false }
}
watch(tab, (t) => { if (t === 'make') ensureRules() })
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">9.3 CIA 修正包</h1></template>
    <template #conclusion>
      <template v-if="c">待決定 <b class="mono">{{ n(c.pending) }}</b> 筆・已同意等套用 <b class="mono">{{ n(c.agreed) }}</b> 筆・已套用 <b class="mono">{{ n(c.applied) }}</b> 筆<template v-if="c.needs_confirm">・待人確認 <b class="mono">{{ n(c.needs_confirm) }}</b> 筆</template>
        <InfoNote>系統依規則找出 CIA 裡寫法不一致的格子（機房多種寫法、「沒有」寫成 NA／N/A／無、範本提示文字、IP 格式錯），產生修正包交 CIA 管理人；
          管理人同意的在 CIA 修改，之後從 CIA 匯出的就是乾淨的。系統不改 CIA、也不改資產資料，只記錄建議、決定與核對結果。
          一個批次依負責單位＋資產群組切成多包，每包最多 300 筆、同一台不拆兩包。同一格同時只會出現在一包裡。筆＝一個要修正的格子。</InfoNote>
      </template>
      <span v-else-if="err.sum" class="err">{{ err.sum }}</span>
    </template>
    <template #actions>
      <NuxtLink class="btn ghost" :to="IMPORT_LINK">上傳管理人交回的檔</NuxtLink>
      <NuxtLink v-if="!isViewer" class="btn" :to="u('make')">產生修正包</NuxtLink>
    </template>
    <div v-if="c" class="cards">
      <NuxtLink v-for="k in CARDS" :key="k.k" class="kc" :class="[{ on: k.on }, k.tone]" :to="k.to">
        <span class="kl">{{ k.label }}</span>
        <b class="kv mono">{{ n(k.v) }}<small> {{ k.unit }}</small></b>
        <span v-if="k.sub" class="ks">{{ k.sub }}</span>
      </NuxtLink>
    </div>
    <p v-if="recon?.warn || sum?.cia_import?.warn" class="warn">{{ sum?.cia_import?.warn ?? recon.warn }}</p>
    <p class="src muted">
      <span v-if="recon">上次自動核對 {{ (recon.at || '').slice(0, 16) }}・CIA 檔 {{ recon.file ?? '—' }}（資料日期 {{ recon.data_date ?? '—' }}・{{ n(recon.rows) }} 筆）・已套用 {{ n(recon.applied) }}・CIA 已另改 {{ n(recon.cia_other) }}・資產已消失 {{ n(recon.gone) }}</span>
      <span v-else>還沒有自動核對紀錄</span>
      <span>・CIA 上次匯入 {{ sum?.cia_import?.at ? `${String(sum.cia_import.at).slice(0, 16)}（${sum.cia_import.file ?? '—'}）` : '—' }}</span>
      <NuxtLink class="lnk" :to="IMPORT_LINK">資料匯入</NuxtLink>
      <InfoNote>每次在「資料匯入」匯入 CIA 資產清冊完成，系統自動核對未結案的每一筆：CIA 現值＝新值 → 已套用；新值空白（請人填）的，改成規則不再抓到的值 → 已套用；
        既不是原值也不是新值 → CIA 已另改；資產不見 → 資產已消失。每一筆都記下是哪一份 CIA 匯入檔（檔名＋雜湊）。管理人交回的勾選檔也在「資料匯入」上傳。</InfoNote>
    </p>

    <template #tab>
      <p v-if="err[tab]" class="err">{{ err[tab] }}</p>

      <!-- 批次 -->
      <template v-if="tab === 'batches'">
        <template v-if="oneBatch">
          <div class="bar"><NuxtLink :to="u('batches')">‹ 全部批次</NuxtLink><b>第 {{ oneBatch.id }} 批：{{ oneBatch.title }}</b>
            <span class="muted">{{ (oneBatch.created_at || '').slice(0, 16) }}・{{ oneBatch.created_by ?? '—' }}・CIA 匯出日期 {{ oneBatch.cia_data_date ?? '—' }}・{{ n(oneBatch.item_count) }} 筆／{{ n(oneBatch.pkg_count) }} 包</span>
            <InfoNote>CIA 匯出日期＝產包當下最後一次匯入的 CIA 清冊（{{ oneBatch.cia_import_file ?? '—' }}）。規則檔雜湊 {{ (oneBatch.rules_sha256 || '').slice(0, 12) }}…、設定雜湊 {{ (oneBatch.settings_sha256 || '').slice(0, 12) }}…，產包時的規則與設定快照存在系統裡，日後查得到當時怎麼判的。
              Excel 六張工作表：說明、修正明細（管理人只能改「同意」「不同意原因」「備註」三欄）、補登清單與待填檢查（下一批用，這批只有表頭）、選項清單、修正後全表（只當參考）。</InfoNote></div>
          <V2Table :rows="pkgRows" :cols="pkgCols" prefix="P" row-key="pkg_no" unit="包" empty="這批沒有包">
            <template #cell-dl="{ row }">
              <button v-if="!isViewer" type="button" class="mini" :disabled="dlBusy === `${oneBatch.id}-${(row as any).pkg_no}`" @click="download(oneBatch.id, (row as any).pkg_no)">{{ dlBusy === `${oneBatch.id}-${(row as any).pkg_no}` ? '下載中…' : '下載' }}</button>
              <span v-else class="muted">—</span>
            </template>
          </V2Table>
        </template>
        <template v-else>
          <div v-if="q('open') === '1'" class="bar"><NuxtLink :to="u('batches')">‹ 全部批次</NuxtLink><b>未結案批次</b></div>
          <V2Table :rows="batchRows" :cols="batchCols" prefix="B" row-key="id" unit="批" :empty="q('open') === '1' ? '沒有未結案的批次' : '還沒有產生過修正包（按右上「產生修正包」）'" />
        </template>
      </template>

      <!-- 逐筆明細 -->
      <template v-else-if="tab === 'items'">
        <div class="bar"><b>{{ itemTitle }}</b><span class="muted">{{ n(itemRows.length) }} 筆</span>
          <NuxtLink v-for="s in ['pending', 'agreed', 'applied', 'rejected', 'confirm', 'withdrawn']" :key="s" class="chip" :class="{ on: q('st') === s }"
                    :to="u('items', { batch: q('batch'), pkg: q('pkg'), unit: q('unit'), rule: q('rule'), upload: q('upload'), st: q('st') === s ? undefined : s })">{{ s === 'confirm' ? '待人確認' : sum?.status_labels?.[s] ?? s }}</NuxtLink>
          <NuxtLink v-if="q('batch') || q('pkg') || q('st') || q('unit') || q('rule') || q('upload')" class="lnk" :to="u('items')">清除條件</NuxtLink></div>
        <div v-if="facets" class="bar filters">
          <select :value="q('batch')" aria-label="批次" @change="setFilter('batch', ($event.target as HTMLSelectElement).value)">
            <option value="">全部批次</option><option v-for="b in facets.batches" :key="b" :value="String(b)">第 {{ b }} 批</option></select>
          <select :value="q('pkg')" aria-label="包" :disabled="!q('batch')" @change="setFilter('pkg', ($event.target as HTMLSelectElement).value)">
            <option value="">{{ q('batch') ? '全部包' : '先選批次' }}</option><option v-for="p in pkgOpts" :key="`${p.batch_id}-${p.pkg_no}`" :value="String(p.pkg_no)">第 {{ p.pkg_no }} 包（{{ p.unit }}）</option></select>
          <select :value="q('unit')" aria-label="負責單位" @change="setFilter('unit', ($event.target as HTMLSelectElement).value)">
            <option value="">全部單位</option><option v-for="x in facets.units" :key="x" :value="x">{{ x }}</option></select>
          <select :value="q('rule')" aria-label="規則" @change="setFilter('rule', ($event.target as HTMLSelectElement).value)">
            <option value="">全部規則</option><option v-for="x in facets.rules" :key="x.id" :value="x.id">{{ x.label }}</option></select>
        </div>
        <form v-if="!isViewer" class="actbar" @submit.prevent="applyAct">
          <span>勾選 <b>{{ pickedIds.length }}</b> 筆，</span>
          <select v-model="act" aria-label="動作">
            <option value="agree">標同意</option><option value="reject">標不同意</option><option value="withdraw">撤回（不送了）</option><option value="reopen">重開（回到待決定）</option>
          </select>
          <select v-if="act === 'reject'" v-model="actCode" aria-label="不同意原因"><option value="">選原因</option><option v-for="x in rejectCodes" :key="x" :value="x">{{ x }}</option></select>
          <input v-model="actNote" class="note" :placeholder="act === 'withdraw' || act === 'reopen' ? '原因（必填，稽核會留）' : '備註（選填）'" aria-label="原因">
          <button type="submit" class="btn" :disabled="actBusy || !pickedIds.length">{{ actBusy ? '處理中…' : '套用' }}</button>
          <InfoNote>選了動作之後，不適用這個動作的列不能勾（例：已套用的不能撤回）。按「套用」會先跳確認視窗寫明筆數。平常由管理人在 Excel 勾選、在「資料匯入」交回；這裡是補登個別幾筆用（例如管理人用口頭或信件回覆）。已套用、資產已消失的不能改。
            重開只限不同意／已撤回／CIA 已另改，而且 CIA 現值仍是原值、沒有別包正在處理同一格。每一次異動都留紀錄（點「紀錄」看），不會覆寫。</InfoNote>
        </form>
        <V2Table v-model:selected="picked" :selectable="!isViewer" :can-select="canPick" :rows="itemRows" :cols="itemCols" prefix="F" row-key="id" :max="200" empty="沒有符合的項目">
          <template #cell-ev="{ row }"><button type="button" class="mini" @click="showEvents(row)">紀錄</button></template>
        </V2Table>
        <div v-if="evFor" class="evbox">
          <div class="bar"><b>{{ evFor.track_code }}　{{ evFor.asset_serial }}　{{ evFor.field_label }}</b><span class="muted">{{ evFor.old_value }} → {{ evFor.new_value ?? '（請人填正確值）' }}</span>
            <button type="button" class="mini" @click="evFor = null">關閉</button></div>
          <V2Table :rows="evRows" :cols="evCols" prefix="E" row-key="id" unit="筆紀錄" empty="沒有紀錄" />
        </div>
      </template>

      <!-- 同機多筆（S3） -->
      <template v-else-if="tab === 'dup'">
        <V2CiaCases kind="dup" prefix="D" tab="dup" page="/v2/ciafix" scan-path="/api/cia-case/dup/scan" handoff-path="/api/cia-case/dup/handoff"
                    handoff-label="交出成修正包" :extra-cols="dupCols" :map-row="dupRow" :categories="DUP_CATS" default-category="suspect" @handed="refreshAll">
          <template #note><InfoNote>同一台（全站同一把尺 machine_key）有 2 筆以上 CIA 登記的，一組一案。堆疊交換器／機箱元件共用管理 IP 的不算；系統自建的 DYN／VC／AUTO／ADOPT 不算。
            逐案看各筆哪些欄位不同、每欄取哪一筆的值，確認後按「交出成修正包」：保留筆要改的格＋其他每筆「請停用或併入」進 9.3 修正包，交 CIA 管理人。WEBIT3 不刪任何一筆。</InfoNote></template>
          <template #detail="{ case: cc, reload }"><V2CiaDupDetail :c="cc" @done="reload" /></template>
        </V2CiaCases>
      </template>

      <!-- 機櫃編號統一（S2） -->
      <template v-else-if="tab === 'rack'"><V2CiaRack /></template>

      <!-- 未結案總覽（S4） -->
      <template v-else-if="tab === 'open'"><V2CiaOpen /></template>

      <!-- 產生修正包 -->
      <template v-else-if="tab === 'make'">
        <div class="bar">
          <button type="button" class="btn" :disabled="pvBusy" @click="doPreview">{{ pvBusy ? '計算中…' : (pv ? '重新預覽' : '預覽第一批：寫法統一') }}</button>
          <label class="chk"><input v-model="resend" type="checkbox"> 曾被不同意的也重送</label>
          <span class="muted">依據 CIA 上次匯入 {{ sum?.cia_import?.at ? String(sum.cia_import.at).slice(0, 16) : '—' }}（{{ sum?.cia_import?.file ?? '—' }}・資料日期 {{ sum?.cia_import?.data_date ?? '—' }}・{{ n(sum?.cia_import?.rows) }} 筆）</span>
          <InfoNote>預覽不寫入任何東西：列出各規則會找出幾筆、會切成幾包、每一筆的原值與新值。確認產生時系統會重算一次，預覽之後資料或設定變了會請你重新預覽。
            已經在未結案批次裡的格子不會重複列；曾被管理人不同意的同一筆建議預設不再送，勾「曾被不同意的也重送」才會列。</InfoNote>
        </div>
        <template v-if="pv">
          <p v-if="pv.cia_import?.warn" class="warn">{{ pv.cia_import.warn }}（只是提醒，仍可產包）</p>
          <p v-if="pv.room_alias_empty" class="warn">機房寫法對照還沒設定，這次不會列任何機房項目。<NuxtLink :to="u('rules')">到規則設定填</NuxtLink></p>
          <div class="bar"><b>會產生 {{ n(pv.total) }} 筆（{{ n(pv.assets) }} 台）、{{ n(pv.packages.length) }} 包</b>
            <span class="muted">每包最多 {{ pv.max_rows }} 筆・已在未結案批次 {{ n(pv.in_open) }} 筆・曾被不同意 {{ n(pv.was_rejected) }} 筆</span>
            <span class="sp" />
            <button v-if="!isViewer" type="button" class="btn" :disabled="mkBusy || !pv.total" @click="doCreate">{{ mkBusy ? '產生中…' : '確認產生' }}</button></div>
          <h3 class="h3">各規則</h3>
          <V2Table :rows="pvRuleRows" :cols="pvRuleCols" prefix="R" row-key="rule_id" unit="條規則" />
          <h3 class="h3">切包</h3>
          <V2Table :rows="pvPkgRows" :cols="pvPkgCols" prefix="P" row-key="pkg_no" unit="包" empty="沒有要修正的項目" />
          <h3 class="h3">逐筆<template v-if="pvRule">（{{ pvRuleRows.find((r: any) => r.rule_id === pvRule)?.label }}）<NuxtLink class="lnk" :to="u('make')">看全部</NuxtLink></template></h3>
          <V2Table :rows="pvRows" :cols="pvCols" prefix="V" row-key="__no" empty="沒有要修正的項目" />
        </template>
      </template>

      <!-- 規則設定 -->
      <template v-else-if="tab === 'rules' && rules">
        <h3 class="h3">規則<InfoNote>規則結構放在系統的規則檔（cia_fix_rules.json），含真實機房名稱的對照與範本提示文字放系統設定（下方），不進版控。每包上限 {{ rules.max_rows }} 筆。
          同一格只會被第一條符合的規則收。比對不分大小寫、不分全半形、忽略空白。</InfoNote></h3>
        <V2Table :rows="ruleRows" :cols="ruleCols" prefix="S" row-key="id" unit="條規則" />
        <h3 class="h3">機房寫法對照<InfoNote>左邊是 CIA 現在出現過的每一種寫法與筆數，在右邊填它該統一成的標準名稱（例：01_甲機房）；已經是標準寫法的可以填自己或留空。
          標準名稱也會出現在修正包的「選項清單」，建議 CIA 那邊也設成下拉。只有管理員能儲存。</InfoNote></h3>
        <V2Table :rows="locRows" :cols="locCols" prefix="L" row-key="value" unit="種寫法" empty="CIA 還沒有任何機房資料">
          <template #cell-to="{ row }"><input v-model.lazy="alias[(row as any).value]" class="inp" list="cia-fix-stds" :disabled="isViewer" :aria-label="`${(row as any).value} 統一成`"></template>
        </V2Table>
        <datalist id="cia-fix-stds"><option v-for="s in stds" :key="s" :value="s" /></datalist>
        <h3 class="h3">範本提示文字<InfoNote>使用單位欄裡出現這些文字（一行一個）就列出來、新值留空請管理人填正確單位。規則檔另有通用的寫法（例：「請選擇…」）。</InfoNote></h3>
        <textarea v-model="tplText" class="ta" rows="4" :disabled="isViewer" aria-label="範本提示文字" />
        <div v-if="!isViewer" class="bar"><button type="button" class="btn" :disabled="setBusy" @click="saveSettings">{{ setBusy ? '儲存中…' : '儲存設定' }}</button>
          <span v-if="aliasDiff.length || tplDirty" class="warn">未儲存 {{ aliasDiff.length + (tplDirty ? 1 : 0) }} 項</span></div>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 16px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 900px) { .cards { grid-template-columns: 1fr; } }
.kc { display: flex; flex-direction: column; gap: 4px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kl { font-size: 13px; color: var(--ink-soft); }
.kv { font-size: 28px; color: var(--brand-dark); }
.kv small { font-size: 13px; color: var(--muted); }
.ks { font-size: 12px; color: var(--ink-aux); }
.kc.good .kv { color: var(--good); } .kc.mute .kv { color: var(--ink-aux); } .kc.warn .kv { color: var(--warn-text); }
.filters select { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.src { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 0; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sp { flex: 1; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; display: inline-block; }
.btn:hover:not(:disabled) { background: var(--brand-hover); }
.btn:disabled { opacity: .6; cursor: default; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.lnk { color: var(--link); font-size: 13px; }
.chk { font-size: 12px; color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.actbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; }
.actbar select, .actbar .note, .inp, .ta { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.actbar .note { width: 260px; max-width: 100%; }
.inp { width: 220px; max-width: 100%; }
.ta { width: 100%; max-width: 640px; }
.evbox { margin-top: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--sub); }
</style>
