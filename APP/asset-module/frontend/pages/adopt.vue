<script setup lang="ts">
import { matchesAll, otherTabHits, searchTerms } from '~/composables/adoptSearch'
// 納入管理＝把機器帶進系統的單一入口。頁面分兩段，順序就是實際工作順序：
//   ①「開始收集」（決策 C4）：貼網段或 IP 清單按一下，系統自己選路
//      （22→SSH／445→WinRM／活著但進不去→Push Agent／完全不通→只能匯入），
//      給一張分得出「收到了」跟「要人工處理」的結果表。
//   ②「登記成資產」：掃到了但沒登記的主機，補上業務欄位建成資產。
// 表單依 /api/assets/field-meta 動態分區（功能分類）＋標層：
//   tech(技術層) 有值→灰底唯讀自動帶；tech 沒值→可手動補；biz(業務層) 一律讓使用者填。
interface FieldMeta { label: string; category: string; layer: 'tech' | 'biz'; required?: boolean; options?: string[]; new?: boolean; help?: string }
interface Meta { categories: { key: string; label: string }[]; fields: Record<string, FieldMeta> }
interface ScanHost {
  ip: string; hostname: string | null; device_model: string | null; is_vm: number; segment: string | null
  // S16 被動指紋：讓人在納管前先認得出這台大概是什麼
  mac?: string | null; mac_vendor?: string | null; open_ports?: string | null; ttl?: number | null; os_guess?: string | null
}
interface DispatchRow {
  ip: string; alive: number; open_ports: string | null; route: string; status: string
  asset_serial: string | null; hostname: string | null; registered: number; message: string
  // 資產名稱（2026-10-16）：有些列主機名稱是空的，認不出是哪一台，但資產名稱常常有值。
  // 後端 latest_run／run_dispatch 現查資產登記帶出來，不存在探測結果表裡
  asset_name?: string | null
  not_onboardable_kind?: string | null
  // 落在哪個頁簽——後端 collect_dispatch.dispatch_tab() 算的，前端不另判
  tab?: string
  // 這台上次一次「系統去建帳號」的紀錄（2026-10-18，前端從 onboard_audit 批量查回來，
  // 不是這支 API 原本就有的欄位）。查不到就是 null，畫面顯示「—」，不可以顯示成空字串
  // 讓人以為是「查過、沒有」。
  last_onboard_at?: string | null
  last_onboard_user?: string | null
  // 最後一次納管嘗試失敗的話：時間＋短原因＋完整訊息（後端 onboard_failures.last_attempts，
  // 任何入口——網頁一鍵、一次納管、重新納管、本機驗證、取得納管指令、排程——都算）。
  // 後來成功了就是 null，標籤自動消失。
  last_fail_at?: string | null
  last_fail_short?: string | null
  last_fail_msg?: string | null
  // 大類（後端 os_class，含「已納管主機以收集到的 OS 為準」）與大型主機頁簽裡能不能佈帳號
  os_class?: string | null
  mainframe_onboardable?: boolean
  // 人工指定過的話，留痕在這裡（誰／何時／原因／機器原本判什麼）。後端 summarize 寫回
  os_override?: { by: string | null; at: string; reason: string | null
                  auto_at_time: string | null; os_type: string; tab: string } | null
  // 不納管的那一列「該走哪條收集路徑」——後端 collect_dispatch.nok_guidance() 算的
  guide?: { kind: string; how: string | null; page: string | null
            page_label: string | null; note?: string } | null
  // 固定編號：這一輪結果裡的序號（排序／篩選都不變，口頭講「第 37 號」對得上）
  no?: number
  // 「活著但進不去」的機器判斷是什麼（2026-10-19，後端 host_kind，規則在 host_kind_rules.json）。
  // 只有 route=agent 的列有；confidence＝confirmed／likely／unknown（確定／推測／不明）
  kind_guess?: { kind: string; label: string; confidence: string; confidence_label: string
                 evidence: string[]; action: string } | null
  ttl?: number | null
  // 另有納管方式的網路設備／儲存設備／設備：廠牌（2026-10-19 使用者「網路設備要寫廠牌」，後端
  // host_kind.vendor_judge）。text＝「網路設備・Cisco」「設備・廠牌不明」；confidence 同上
  vendor?: { vendor: string | null; category: string | null; confidence: string
             confidence_label: string; evidence: string[]; text: string; moved_from?: string } | null
  // 「判斷」欄排序用（label＋信心，前端組的）
  kind_sort?: string
  // 合併檢視（v1.468.4）：這個位址最新一次結果是哪一輪、什麼時候收的、是否超過「舊」的天數
  run_id?: number
  collected_at?: string | null
  stale?: boolean
  // 機器標籤（v1.468.6，asset_tags，掛在「台」上；前端整批查回來）
  tags?: string[]
  tags_sort?: string
  // Windows 納管（勾選才收，2026-10-22，後端 win_onboard）：opted_in＝收集會登入這台；
  // is_admin＝最近一次納管時收集帳號是不是本機管理員（1／0／null 不明）
  win?: WinState | null
}
interface WinState {
  opted_in: boolean; is_admin: number | null; last_ok: number | null; last_message: string | null
  last_at: string | null; onboarded_by: string | null; onboarded_at: string | null
  revoked_by: string | null; revoked_at: string | null
}
interface DispatchOut {
  run?: { targets_raw: string | null; target_count: number; status: string; started_at: string | null; finished_at: string | null } | null
  total: number; collected: number; needs_action: number; empty_addresses?: number
  by_status: Record<string, number>; by_route: Record<string, number>
  // 不納管的再依類別拆開。這一欄出現前跑的 run 會是空的（後端留 NULL，不填空）
  not_onboardable_by_kind?: Record<string, number>
  // 頁簽→台數（互斥、加總＝total）。畫面數字直接數每列的 tab，跟下鑽同一份
  by_tab?: Record<string, number>
  // 合併檢視表頭（v1.468.4）：合併了幾次收集、最早／最新、幾列算舊
  merged?: { mode: string; run_id: number | null; run_count: number; run_total: number | null
             earliest: string | null; latest: string | null; stale_days: number; stale_count: number }
  // 剛跑完的那一輪自己的數字（POST /api/collect/dispatch 才有）
  this_run?: { total: number; collected: number; needs_action: number; skipped?: number | null }
  results: DispatchRow[]
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const { copy } = useClipboard()

const meta = ref<Meta | null>(null)
const hosts = ref<ScanHost[]>([])
const { sortKey, sortDir, toggle, sorted: sortedAll } = useSort(hosts, 'ip')
// 未登記主機搜尋（2026-10-22 使用者：「每個頁面都要有」）：空白隔開＝每個字都要有
const uQ = ref('')
const sorted = computed(() => {
  const ts = uQ.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!ts.length) return sortedAll.value
  return sortedAll.value.filter((h: any) => {
    const hay = [h.ip, h.hostname, h.os_guess, h.mac_vendor, h.mac, h.open_ports, h.segment].join(' ').toLowerCase()
    return ts.every((t) => hay.includes(t))
  })
})
const { exportTable, busy: exportBusy } = useTableExport()
function exportUnregistered() {
  exportTable('未登記主機', ['IP', '主機名稱', '研判', '網卡廠牌', 'MAC', '開放埠', '網段', '是否 VM', '上次收集結果'],
    sorted.value.map((h: any) => [h.ip, h.hostname, h.os_guess, h.mac_vendor, h.mac, h.open_ports, h.segment,
      h.is_vm ? 'VM' : '', STATUS_LABEL[dispatchByIp.value[h.ip]?.status] || dispatchByIp.value[h.ip]?.status || '']))
}
const selected = ref<ScanHost | null>(null)
const form = reactive<Record<string, any>>({})

// 一鍵納管用共用元件 OnboardModal，這裡只管開關與成功後移除該筆
const onboardHost = ref<ScanHost | null>(null)
function openOnboard(h: ScanHost) { onboardHost.value = h }
function onOnboarded() {
  const ip = onboardHost.value!.ip
  hosts.value = hosts.value.filter((x) => x.ip !== ip)
  // 從收集結果表點納管成功的，那一列要當場翻成「已收到」——
  // 不然畫面還停在「待佈身分」，人會以為沒成功又點一次
  const row = dispatch.value?.results.find((r) => r.ip === ip)
  if (row && dispatch.value) {
    row.status = 'collected'
    row.message = '已佈好收集帳號，現在收得到'
    dispatch.value.collected += 1
    dispatch.value.needs_action -= 1
  }
  onboardHost.value = null
  loadAudit()
  // 這裡是就地改 row 的欄位，dispatch.value.results 陣列參考沒變，上面的 watch 不會自己觸發——
  // 剛納管這一筆的「上次納管時間／帳號」要補查，不然要等下一次整張重新整理才會出現
  loadAuditByIp((dispatch.value?.results ?? []).filter((r) => !isEmptyAddr(r)).map((r) => r.ip))
  loadManaged()   // 剛納管（或重新納管）的那台要出現在／更新於下面的「已納管主機」表
}
const saving = ref(false)
const submitAttempted = ref(false)

function isMissing(key: string, required?: boolean) {
  return submitAttempted.value && required && !form[key]
}

// ===== 納管紀錄 =====
// 納管失敗時原本只看得到那一瞬間的 toast，關掉就沒了——但每一次嘗試其實都有寫進
// onboard_audit（含階段、訊息、腳本輸出），只是從來沒有畫面（使用者 2026-08-16 問
// 「失敗怎麼看 LOG」才發現）。沒有畫面等於要人 SSH 進去翻 DB 或 journalctl，
// 跟這個系統的定位相反。
interface OnboardAudit {
  id: number; target_ip: string; platform: string | null; login_user: string | null
  trigger: string; triggered_by: string | null; ok: number
  stage: string | null; message: string | null; output: string | null; created_at: string
}
const audits = ref<OnboardAudit[]>([])
const showAudit = ref(false)
const openOutput = ref<number | null>(null)
const { sortKey: aSortKey, sortDir: aSortDir, toggle: aToggle, sorted: aSorted } =
  useSort(audits, 'id')
async function loadAudit() {
  try { audits.value = await apiFetch<OnboardAudit[]>('/api/onboard/audit') } catch { /* 沒紀錄就空著 */ }
}
// 紅框只攤開「還沒解決」的失敗：同一台之後已經納管成功過，那筆失敗就是過去式。
// 2026-09-11 實際踩到：221 在 14:37 帳密錯失敗、14:51 重新納管成功，紅框卻一直掛著
// 14:37 那筆，讓人以為還沒弄好。舊紀錄照樣留在「看全部」裡，不刪。
// audits 是新到舊排序（id 大的在前），所以「之後成功」＝前面有同 IP 的成功紀錄。
const lastFail = computed(() => {
  const okLater = new Set<string>()
  for (const a of audits.value) {
    if (a.ok) { okLater.add(a.target_ip); continue }
    if (!okLater.has(a.target_ip)) return a
  }
  return null
})

// ===== ①收集入口（決策 C4）=====
const targets = ref('')

// ===== 從網段配置表挑要收集的範圍（2026-09-15 使用者）=====
// 「我雖然是測試網段，但我不知道要從哪裡開始收集。目前拿的是全網的網段，
//   所以我應該要有一個『測試網段』的選擇功能……選機房、有哪些網段可以選。」
// 網段配置表裡本來就有機房／環境／用途，先選環境再選機房，最後勾網段——
// 不用背 10.99.x 是哪一段。資安註記排除的照列但不給勾，並寫明原因（不是靜默消失）。
interface SegItem { cidr: string; label: string; scan_excluded: boolean; scan_note: string | null }
interface SegEnvNode { environment: string; segments: SegItem[] }
interface SegLocNode { location: string; environments: SegEnvNode[] }

const segTree = ref<SegLocNode[]>([])
const segEnv = ref('測試')          // 預設測試——正式/備援要納管得走 PAM，不是這裡的主戰場
const segLoc = ref('')
const segSel = ref<Set<string>>(new Set())
const segOpen = ref(true)
// 網段表的 scan_excluded 註記寫的是「**建議**排除掃描」（UAT環境／員工電腦／重複設備IP），
// 那是給弱點掃描的建議，不等於禁止資產收集。221 實測：內湖 30 個測試段有 29 段帶這個註記，
// 全部鎖死的話這個挑選器等於不能用（只剩 1 段）。
// 所以預設只列可收集的，要勾那些得自己打開開關，而且每一列都寫明原因——
// 讓人看得到理由再決定，不是替他決定，也不是靜默忽略註記。
const segShowExcluded = ref(false)

async function loadSegTree() {
  try {
    const r = await apiFetch<{ tree: SegLocNode[] }>('/api/segments/tree')
    segTree.value = r.tree ?? []
    if (!segEnvs.value.includes(segEnv.value)) segEnv.value = segEnvs.value[0] ?? ''
  } catch { segTree.value = [] }   // 還沒匯網段配置表就不顯示這一區，不擋整頁
}

const segEnvs = computed(() => {
  const s = new Set<string>()
  for (const l of segTree.value) for (const e of l.environments) s.add(e.environment)
  const order = ['測試', '正式', '備援']
  return [...order.filter((x) => s.has(x)), ...[...s].filter((x) => !order.includes(x))]
})
const segLocs = computed(() =>
  segTree.value.filter((l) => l.environments.some((e) => e.environment === segEnv.value))
    .map((l) => l.location))
const segList = computed<SegItem[]>(() => {
  const loc = segTree.value.find((l) => l.location === segLoc.value)
  return loc?.environments.find((e) => e.environment === segEnv.value)?.segments ?? []
})
const segVisible = computed(() =>
  segShowExcluded.value ? segList.value : segList.value.filter((s) => !s.scan_excluded))
const segPickable = computed(() => segVisible.value)
const segExcludedCount = computed(() => segList.value.filter((s) => s.scan_excluded).length)
watch([segEnv, segLocs], () => {
  if (!segLocs.value.includes(segLoc.value)) segLoc.value = segLocs.value[0] ?? ''
})
function toggleSeg(cidr: string) {
  const s = new Set(segSel.value)
  if (s.has(cidr)) s.delete(cidr); else s.add(cidr)
  segSel.value = s
}
function toggleAllSegs() {
  segSel.value = segPickable.value.every((s) => segSel.value.has(s.cidr))
    ? new Set()
    : new Set(segPickable.value.map((s) => s.cidr))
}
// 加進上面的輸入框（不是直接開跑）：使用者還能自己加減、也看得到「約幾台」再決定
function addSelectedSegs() {
  if (!segSel.value.size) { showToast('先勾要收集的網段', 'warn'); return }
  const have = new Set(targets.value.split(/[\s,;]+/).filter(Boolean))
  const add = [...segSel.value].filter((c) => !have.has(c))
  targets.value = [...targets.value.split(/[\s,;]+/).filter(Boolean), ...add].join('\n')
  showToast(`已加入 ${add.length} 個網段`, 'success')
  segSel.value = new Set()
}
const dispatching = ref(false)
// 同「一鍵納管」那顆的問題：只變灰看不出在不在跑。這裡台數是算得出來的，
// 所以講得更具體——幾台、跑多久、每台大概多久。仍然不做假的百分比進度：
// 後端是一次呼叫，中途沒有真的可回報的節點。
const dElapsed = ref(0)
let dTicker: ReturnType<typeof setInterval> | null = null
// 目標數量：本地照 parse 規則粗估（CIDR 展開、a.b.c.d-e 範圍、單一 IP）。
// 只用來讓人心裡有數，真正的解析與上限在後端。
// 單一 IP 與網段／範圍分開算（v1.471.2）：「只掃已登記」只篩網段／範圍，直接打的單一 IP 一律照收
const targetSplit = computed(() => {
  let ranges = 0, singles = 0
  for (const tok of targets.value.split(/[\s,;]+/)) {
    if (!tok) continue
    const cidr = tok.match(/^\d+\.\d+\.\d+\.\d+\/(\d+)$/)
    if (cidr && cidr[1] === '32') { singles += 1; continue }     // /32＝指名一台，算單一 IP
    if (cidr) { const bits = 32 - Number(cidr[1]); ranges +=Math.max(2 ** bits - 2, 1); continue }
    const range = tok.match(/^\d+\.\d+\.\d+\.(\d+)-(\d+)$/)
    if (range) { ranges += Math.max(Number(range[2]) - Number(range[1]) + 1, 0); continue }
    singles += 1
  }
  return { ranges, singles }
})
const targetCount = computed(() => targetSplit.value.ranges + targetSplit.value.singles)
// 範圍被「只掃已登記」篩空時的提示（不是失敗）
const dispatchNotice = ref('')
watch(targets, () => { dispatchNotice.value = '' })
// 大量收集前先確認的門檻（位址數）
const CONFIRM_OVER = 256
const dispatch = ref<DispatchOut | null>(null)

// ===== 每一列「上次納管時間／用哪個帳號」(2026-10-18) =====
// 這張結果表是「這一輪探測到什麼」，納管紀錄（onboard_audit）記的是「系統什麼時候
// 幫它建過帳號」——兩者本來各自成表，使用者要知道「這台上次是誰、什麼時候建的」
// 得切去下面的納管紀錄表自己拿 IP 找。批量查「這批 IP 各自最後一次」比對每個 IP
// 各打一次 API 便宜，後端一次查完回一個 {ip: 最後一筆} 的 map。查不到的 IP
// 不在回傳裡——那兩欄顯示「—」，不可以顯示成空字串讓人以為是「查過、沒有紀錄」。
// 2026-10-18 起同一支 API 也帶「上次失敗」（fail：時間＋短原因＋完整訊息），
// 待納管表、已納管表、一次納管視窗三處讀的是同一份（後端 onboard_failures.last_attempts）。
interface LastAttempt {
  created_at: string; login_user: string | null; platform: string | null; ok: number
  fail?: { at: string; short: string; message: string; stage: string | null
           trigger: string | null; fail_count: number } | null
}
const auditByIp = ref<Record<string, LastAttempt>>({})
async function fetchLastAttempts(ips: (string | null | undefined)[]): Promise<Record<string, LastAttempt>> {
  const uniq = [...new Set(ips.filter((x): x is string => Boolean(x)))]
  if (!uniq.length) return {}
  // POST：一次幾百上千個 IP，放網址會太長（414）
  return await apiFetch<Record<string, LastAttempt>>(
    '/api/onboard/audit/latest-by-ip', { method: 'POST', body: { ips: uniq } })
}
async function loadAuditByIp(ips: (string | null | undefined)[]) {
  try { auditByIp.value = await fetchLastAttempts(ips) }
  catch { /* 查不到就讓那幾欄維持「—」，不擋整張結果表 */ }
}
/** 「上次失敗 10/18 20:50：沒有 sudo」的日期時間那段（年份與秒數省略，標籤要短） */
function failWhen(at: string | null | undefined): string {
  const m = (at || '').match(/^\d{4}-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/)
  return m ? `${m[1]}/${m[2]} ${m[3]}` : (at || '')
}
function failTitle(f: { at?: string | null; short?: string | null; message?: string | null } | null | undefined): string {
  if (!f) return ''
  return `上次納管失敗 ${f.at ?? ''}（${f.short ?? ''}）\n${f.message ?? ''}`
}
// 空位址（沒回應又沒登記＝那裡沒有機器）不進任何頁簽（2026-10-18 使用者：「這兩筆對我來說沒意義」）。
// 後端把它們歸 tab=empty；舊版後端沒有這一格，就用同一個條件自己認。
function isEmptyAddr(r: DispatchRow): boolean {
  return r.tab === 'empty' || (r.status === 'import_only' && !r.registered)
}
const emptyAddrCount = computed(() => (dispatch.value?.results ?? []).filter(isEmptyAddr).length)
// 用 dispatch.value?.results（陣列參考）當依據：每次重新整理／輪詢完成都會換一個新陣列，
// watch 就會重新批量查一次；onOnboarded() 之後另外手動補查一次（見下方），因為那裡是
// 就地改欄位、陣列參考不會變，watch 不會自己觸發。空位址不查（沒有機器，不會有納管紀錄）。
watch(() => dispatch.value?.results,
  (rows) => { loadAuditByIp((rows ?? []).filter((r) => !isEmptyAddr(r)).map((r) => r.ip)) },
  { immediate: true })

// ===== 機器標籤（v1.468.6，2026-10-19 使用者：「我納管失敗的，可同時幫它們上備註標籤嗎」）=====
// 同一份 asset_tags（資產頁的標籤），整批查：POST /api/asset-tags/by-serial。沒登記的沒有標籤。
const tagsBySerial = ref<Record<string, { tag: string; note: string | null; created_by: string | null; created_at: string | null }[]>>({})
async function loadTags() {
  const serials = [...new Set([
    ...(dispatch.value?.results ?? []).map((r) => r.asset_serial),
    ...managed.value.map((m) => m.asset_serial), ...managedStale.value.map((m) => m.asset_serial),
  ].filter(Boolean) as string[])]
  if (!serials.length) { tagsBySerial.value = {}; return }
  try {
    tagsBySerial.value = (await apiFetch<any>('/api/asset-tags/by-serial',
      { method: 'POST', body: { serials } })).by_serial ?? {}
  } catch (e: any) {
    showToast(`標籤讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'warn')
  }
}
function tagsOf(serial: string | null | undefined): string[] {
  return serial ? (tagsBySerial.value[serial] ?? []).map((t) => t.tag) : []
}
function tagTitle(serial: string | null | undefined, tag: string): string {
  const t = serial ? (tagsBySerial.value[serial] ?? []).find((x) => x.tag === tag) : null
  return t ? `${tag}\n${t.note || '（沒有備註）'}\n${t.created_by ?? '—'} 於 ${t.created_at ?? '—'}\n點一下只看有這個標籤的` : tag
}
//: 依標籤篩（點表格裡的標籤）。篩選條寫明在看哪個標籤，一鍵清除
const dTag = ref('')
// 納管前資訊面板（v1.468.8；null＝沒開）
const preinfoIp = ref<string | null>(null)
// 預檢（只看不動，v1.468.9；null＝沒開）
const precheckIps = ref<string[] | null>(null)
// 加標籤視窗（null＝沒開）
const tagRows = ref<{ ip: string; asset_serial?: string | null; hostname?: string | null }[] | null>(null)

// 舊版後端沒有 tab 就退回照 route 分（不會比以前更糟）。
// 項次只編「真的有機器的列」（空位址不列），編號以後端給的順序為準，篩選／排序都不重編。
const dispatchRows = computed<DispatchRow[]>(() =>
  (dispatch.value?.results ?? []).filter((r) => !isEmptyAddr(r)).map((r, i) => {
    const a = auditByIp.value[r.ip]
    return {
      ...r, no: i + 1, tab: r.tab || r.route,
      kind_sort: r.vendor ? `${r.vendor.text}（${r.vendor.confidence_label}）`
        : r.kind_guess ? `${r.kind_guess.label}（${r.kind_guess.confidence_label}）` : '',
      tags: tagsOf(r.asset_serial), tags_sort: tagsOf(r.asset_serial).join(' '),
      last_onboard_at: a?.created_at ?? null, last_onboard_user: a?.login_user ?? null,
      last_fail_at: a?.fail?.at ?? null, last_fail_short: a?.fail?.short ?? null,
      last_fail_msg: a?.fail?.message ?? null,
    }
  }))
const { sortKey: dSortKey, sortDir: dSortDir, toggle: dToggle, sorted: dSorted } =
  useSort(dispatchRows, 'ip')

// ===== 走哪條路：篩選（A14）=====
// 掃一個 /24 會有一兩百列，其中大半是「只能匯入」——完全不通的空 IP，
// 沒按鈕也沒下一步，只會把真正能動的那幾台淹掉。預設收起來。
//
// 但**絕不靜默藏**：藏了幾台一定要寫在畫面上。不然「只有 3 台」會被讀成
// 「這個網段只有 3 台機器」，那比不篩還糟。
//
// 2026-10-13：頁簽不再照 route 分。「SSH 收集」裡混著儲存設備、Aruba 等網路設備、
// VMware Photon OS——都開 22 所以 route=ssh，但結果欄全是「不納管」。
// 現在照後端算好的 `tab`（collect_dispatch.dispatch_tab）：不納管的依類別拆成
// 儲存設備／網路設備／ESXi／其他不納管，SSH 收集只剩真正能走 SSH 收集的。
// 每台只落一個頁簽，各頁簽加總＝全部（tests/asset_module/test_adopt_tabs.py 釘住）。
type DTab = 'ssh' | 'winrm' | 'agent' | 'mainframe' | 'storage' | 'network' | 'esxi' | 'ocp' | 'nok_other' | 'import'
const ACTIONABLE_TABS: DTab[] = ['ssh', 'winrm', 'agent']
// 「動得了的」：上面三個頁簽，加上大型主機頁簽裡可佈帳號的那幾台（AIX；AS400 不行）。
// 能不能是後端逐列算好的 mainframe_onboardable（大類清單在 os_class_groups.json），前端不背清單。
function isActionable(r: DispatchRow): boolean {
  return ACTIONABLE_TABS.includes(r.tab as DTab) || (r.tab === 'mainframe' && Boolean(r.mainframe_onboardable))
}
// 不納管的那幾個頁簽（只拿來算「已收起幾台另有納管方式」；頁簽名稱與順序讀後端 catTabs）
const NOK_TABS: { tab: DTab }[] = [
  { tab: 'storage' }, { tab: 'network' }, { tab: 'esxi' }, { tab: 'ocp' }, { tab: 'nok_other' },
]
const dRoute = ref<'actionable' | 'all' | DTab>('actionable')

// ===== 上面那排數字卡要點得下去（2026-10-16）=====
// 使用者：「我發現我不能點數字框」。鐵規則是**每個數字都要能點進去看是哪幾筆**，
// 這排卡從第一版（edfb1a7）到 1.431.0 一直是純顯示——從來沒能點過，不是哪次改壞的。
//
// ⚠️ 卡片跟底下那排頁簽是**兩種不同的切法**：卡片依「結果狀態」（收到了沒／要不要人做事），
// 頁簽依「走哪條路／是什麼類別」。兩個同時套會出現「已納管 12」點下去只有 0 筆
// （已納管 ∩ 儲存設備），那正是「為什麼只有 3 筆」最惡劣的版本。
//
// 所以定死一條規矩：**同一時間只有一種切法生效**。
//   點卡片 → 頁簽自動回「全部」、套上狀態篩選；於是**卡片上的數字＝表格的列數**，永遠相等。
//   點頁簽 → 狀態篩選自動清掉（setRoute 一支處理，不要各寫各的）。
// 再加一條畫面上的保險：有篩選時表格上方一定有一條寫明「現在在看什麼」＋一鍵清除的橫條。
// 2026-10-18 使用者（「這兩筆對我來說沒意義」）：拿掉「這次探測的位址」與「空位址」兩張卡——
// 位址總數留在右上角「共 N 個位址」與那裡的註；空位址不再列進任何頁簽（見 isEmptyAddr）。
type DStatusKey = 'collected' | 'not_onboardable' | 'needs_action'
  | 'needs_agent' | 'import_registered'
  // 2026-10-18：這張卡的「已納管」跟首頁「對帳明細」的「已納管」其實是兩件事——
  // 這裡只代表「這批 IP 這次 SSH/WinRM 連得上」，不要求在 CIA 有登記（見 collect_dispatch
  // ._known_asset 的註解：純粹補顯示用，不是過濾條件）。卡片文字已改名承認這點，
  // 這兩個交叉引用數字是接回首頁「已納管」（CIA 已登記＋連得上）的橋——
  // 判斷用這批結果自己帶的 `registered` 欄位（後端 dispatch 時就查過 CIA），不自己另寫一套。
  | 'collected_registered' | 'collected_unregistered'
// 判定條件照抄後端 collect_dispatch.summarize()，不自己另編一套：
//   collected＝SUCCESS_STATUSES；empty＝import_only 而且資產庫沒登記（那個位址上沒機器）；
//   needs_action＝總數扣掉已納管／另有納管方式／空位址（所以它是個「其餘」，
//   「待裝 Agent」「已登記但只能匯入」都包含在裡面，兩張卡的數字會跟它重疊——這是對的，
//   它們本來就是 needs_action 的細分，不是互斥的七堆）。
const D_STATUS_PICK: Record<DStatusKey, (r: DispatchRow) => boolean> = {
  collected: (r) => r.status === 'collected',
  not_onboardable: (r) => r.status === 'not_onboardable',
  needs_agent: (r) => r.status === 'needs_agent',
  import_registered: (r) => r.status === 'import_only' && Boolean(r.registered),
  needs_action: (r) => r.status !== 'collected' && r.status !== 'not_onboardable'
    && !(r.status === 'import_only' && !r.registered),
  collected_registered: (r) => r.status === 'collected' && Boolean(r.registered),
  collected_unregistered: (r) => r.status === 'collected' && !r.registered,
}
const D_STATUS_LABEL: Record<DStatusKey, string> = {
  collected: '收集連上幾台（各位址最新一次）', not_onboardable: '另有納管方式',
  needs_action: '要人工處理', needs_agent: '要請人裝 Agent',
  import_registered: '已登記但只能匯入',
  collected_registered: 'CIA 已登記', collected_unregistered: 'CIA 待補登',
}
const dStatus = ref<DStatusKey | null>(null)
// 數字跟下鑽用同一份：卡片上的數字直接數列，跟 dVisible 的篩選條件一模一樣。
// （後端 summarize 也算了一份，但畫面只認這一份——兩份只要有一處對不上就會變成
//   「寫 12 點進去 11 筆」，那比不能點更傷信任。）
const dStatusCount = computed(() => {
  const c = {} as Record<DStatusKey, number>
  for (const k of Object.keys(D_STATUS_PICK) as DStatusKey[]) {
    c[k] = dispatchRows.value.filter(D_STATUS_PICK[k]).length
  }
  return c
})
/** 點頁簽：一律先清掉狀態篩選（兩種切法不同時生效） */
function setRoute(r: 'actionable' | 'all' | DTab) { dStatus.value = null; dRoute.value = r }
/** 點卡片：切到「全部」頁簽再套狀態篩選；再點同一張＝取消（回到預設的「動得了的」） */
function pickStatus(k: DStatusKey) {
  if (dStatus.value === k) { setRoute('actionable'); return }
  dRoute.value = 'all'
  dStatus.value = k
}
function clearStatus() { setRoute('actionable') }

// ===== 「下一步」那一欄收合（2026-10-16）=====
// 使用者：「下一步敘述太長了。我有興趣用…打開」。後端給的是一整句
// 「<為什麼>——<請做什麼>」，例如
//   「活著（開放埠 無）但 22／445 都不通，系統進不去——請按「取得安裝包」…」
// 整段攤在表格裡會把整張表撐開，掃不動。
//
// 收合的短句**不是把原字串 cut 到 N 個字**——那會切出半句話。短句從**語意來源**組：
// 結果狀態（＋走哪條路）→ 一句「要做什麼」。原因留給展開看。
// 不納管的那幾列本來就走 guide（指路）分支，短句早就有了，這裡不碰。
//
// ⚠️ 展開看到的是**一字不少的 r.message**（括號裡那段英文錯誤訊息是查問題的依據，
//    不可以省）。排序用的也仍然是 r.message，收合不影響排序。
const NEXT_SHORT: Record<string, string> = {
  collected: '已收到，不用動作',
  needs_agent: '請裝 Push Agent',
  needs_credential: '請執行納管',
  onboard_failed: '納管失敗，看原因',
  failed: '收集失敗，看原因',
  import_only: '只能靠檔案匯入',
  needs_winrm_port: '請開 WinRM（5985）',
  needs_ssh_port: '請開 SSH（22）',
  win_pending: '勾選後按納管',
}
/** 一句「要做什麼」。組不出來才退回截原句，而且是切在「——」之後的動作那半句 */
function nextShort(r: DispatchRow): string {
  // 人工搬到 Windows 的（例：22／445／5985 都開，機器判 SSH）也是 WinRM 那條路（2026-10-22）
  if ((r.status === 'needs_credential' || r.status === 'onboard_failed')
      && (r.route === 'winrm' || r.tab === 'winrm')) return '請設定 WinRM 憑證'
  if (r.status === 'import_only' && r.registered) return '請確認是否關機或換 IP'
  // 判斷不出是什麼機器：先確認，不要直接叫人裝 Agent（Push Agent 只支援 Linux／Unix）
  if (r.status === 'needs_agent' && isKindUnconfirmed(r)) return '先確認是什麼機器'
  const s = NEXT_SHORT[r.status]
  if (s) return s
  // 最後手段：後端一律寫成「<為什麼>——<請做什麼>」，取後半段（動作）而不是前半段
  const m = (r.message || '').split(/——|──/)
  const tail = (m.length > 1 ? m[m.length - 1] : m[0]).trim()
  return tail.length > 24 ? `${tail.slice(0, 24)}…` : tail
}
/** 判斷不出是什麼機器（安裝包仍給，但標「未確認」） */
function isKindUnconfirmed(r: DispatchRow): boolean {
  return r.kind_guess?.action === 'agent_unconfirmed'
}
/** 判斷欄滑過的完整依據 */
function kindTitle(r: DispatchRow): string {
  const g = r.kind_guess
  if (!g) return ''
  return `判斷：${g.label}（${g.confidence_label}）\n依據：\n・${g.evidence.join('\n・')}`
}
/** 廠牌滑過的完整依據 */
function vendorTitle(r: DispatchRow): string {
  const v = r.vendor
  if (!v) return ''
  const moved = v.moved_from ? '\n（依廠牌改放「網路設備」頁簽；錯了請按「改分類」）' : ''
  const g = r.kind_guess ? `\n\n${kindTitle(r)}` : ''
  return `廠牌：${v.vendor ?? '不明'}（${v.confidence_label}）\n依據：\n・${v.evidence.join('\n・')}${moved}${g}`
}
// 有任何一列帶判斷或廠牌才顯示「判斷」欄（Push Agent 頁簽、被判成 Windows 搬到 WinRM 的那幾台、
// 另有納管方式的網路設備／儲存設備／設備）
const showKindCol = computed(() => dVisible.value.some((r) => r.kind_guess || r.vendor))
const dColspan = computed(() => 15 + (dShowTabCol.value ? 1 : 0) + (showKindCol.value ? 1 : 0))
/** 短句之外還有沒有沒講到的內容？沒有就不給「…」，免得點開一片空白 */
function hasMore(r: DispatchRow): boolean {
  const full = (r.message || '').trim()
  return Boolean(full) && full !== nextShort(r)
}
// 展開是**逐列**的，而且就展在同一格裡（往下長一行，不會把表格推寬）
const msgOpen = ref<Set<string>>(new Set())
function toggleMsg(ip: string) {
  const s = new Set(msgOpen.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  msgOpen.value = s
}
// ===== 依「結果」過濾＋隱藏已納管（2026-10-18 使用者：「我想過濾已納管的，用過濾器或打勾」）=====
// 已納管的有自己的頁簽（上面「已納管主機」），這張表預設把它們收起來（打勾＝隱藏，每個人
// 自己的瀏覽器記住）。另有一排結果篩選：全部／未納管／已納管／上次失敗；點「結果」欄的標籤也一樣。
// 篩掉幾台**一定寫出來**：表格上方「顯示 X ／ 總 Y」，不靜默藏。
// 頁簽上的數字跟著這兩個篩選算（點下去看到幾列，頁簽就寫幾）；上面那排狀態卡是明確的下鑽，
// 點卡片時不套這兩個篩選（卡片數字＝表格列數，見 pickStatus）。
// 2026-10-22 加待佈身分／待開 WinRM／待開 SSH（使用者：「待開 WinRM 這一類匯出 Excel 交給 Windows 管理單位」
// ——以前沒有這個篩選）。篩完按「匯出 Excel」就是那份清單。
type DResult = 'all' | 'not_onboarded' | 'onboarded' | 'failed' | 'stale'
  | 'needs_credential' | 'needs_winrm_port' | 'needs_ssh_port' | 'win_pending'
const D_RESULT_LABEL: Record<DResult, string> = {
  all: '全部', not_onboarded: '未納管', onboarded: '已納管', failed: '上次失敗', stale: '舊結果',
  needs_credential: '待佈身分', needs_winrm_port: '待開 WinRM', needs_ssh_port: '待開 SSH',
  win_pending: '待納管',
}
const D_RESULT_PICK: Record<DResult, (r: DispatchRow) => boolean> = {
  all: () => true,
  needs_credential: (r) => r.status === 'needs_credential',
  win_pending: (r) => r.status === 'win_pending',
  needs_winrm_port: (r) => r.status === 'needs_winrm_port',
  needs_ssh_port: (r) => r.status === 'needs_ssh_port',
  not_onboarded: (r) => r.status !== 'collected',
  onboarded: (r) => r.status === 'collected',
  failed: (r) => Boolean(r.last_fail_at),
  // 合併檢視（v1.468.4）：這個位址最新一次結果超過 N 天（N 在 6 系統設定 → 掃描排程）
  stale: (r) => Boolean(r.stale),
}
const D_RESULT_KEYS: DResult[] = ['all', 'not_onboarded', 'onboarded', 'failed', 'stale',
  'needs_credential', 'needs_winrm_port', 'needs_ssh_port', 'win_pending']
const dResult = ref<DResult>('all')
const HIDE_KEY = 'adopt.hideOnboarded'
const dHideOnboarded = ref(true)   // 預設隱藏（已納管有自己的頁簽）
onMounted(() => {
  try {
    const v = localStorage.getItem(HIDE_KEY)
    if (v === '0') dHideOnboarded.value = false
  } catch { /* 無痕／被封鎖：用預設（隱藏），可接受的降級 */ }
})
watch(dHideOnboarded, (v) => {
  try { localStorage.setItem(HIDE_KEY, v ? '1' : '0') } catch { /* 記不住只是下次回到預設，不影響畫面 */ }
})
/** 這一列有沒有通過「結果篩選＋隱藏已納管」。選了「已納管」就不再套隱藏（不然永遠 0 筆）。 */
function dResultOk(r: DispatchRow): boolean {
  if (!D_RESULT_PICK[dResult.value](r)) return false
  if (dHideOnboarded.value && dResult.value === 'all' && r.status === 'collected') return false
  return true
}
function pickResult(k: DResult) { dResult.value = dResult.value === k && k !== 'all' ? 'all' : k }
// 數字跟下鑽用同一份：頁簽數字直接數 tab（套同樣的結果篩選），跟 dVisible 的篩選條件一模一樣
const dRouteCount = computed(() => {
  const c: Record<string, number> = {}
  for (const r of dispatchRows.value) if (dResultOk(r)) c[r.tab!] = (c[r.tab!] ?? 0) + 1
  return c
})
const dAllCount = computed(() => dispatchRows.value.filter(dResultOk).length)
const dActionableCount = computed(() => dispatchRows.value.filter((r) => isActionable(r) && dResultOk(r)).length)
const dNokCount = computed(() =>
  NOK_TABS.reduce((n, t) => n + (dRouteCount.value[t.tab] ?? 0), 0))

// ===== SSH 這條路裡其實是 Windows 的那幾台（2026-10-13）=====
// 這個頁簽原本寫「SSH 收集（Linux）」，但那個「（Linux）」是標籤自己加的——
// choose_route() **刻意只決定走哪條路、從不判平台**（22 開著就走 SSH，即使那是
// Windows，因為服務盤點／帳號盤點只有 SSH 這條路）。於是裝了 OpenSSH 的 Windows
// （開放埠長得像 `22 445 3389 5985`）全被算進「SSH 收集（Linux）」，使用者原話：
// 「這一看就是 WINDOWS 的只是她有安裝 openssh」。拿 Linux 腳本去打它一定失敗，
// 而且失敗訊息長得像帳號問題（帳號進不去），害人去查錯方向。
//
// 所以修的是標籤不是路由：route 照舊走 ssh，只在畫面上標出哪幾台是 Windows，
// 讓人按「⚡ 一鍵納管」前就知道別選 Linux 腳本。
//
// 埠號定義跟後端 backend/fingerprint.py 的 WINDOWS_ONLY_PORTS 對齊，不自己發明一套。
// ⚠️ **445 不算**：Linux 裝 Samba 一樣會開 445，拿它當 Windows 證據會把檔案
// 伺服器誤判成 Windows。
const WINDOWS_ONLY_PORTS = [3389, 5985]
function isWindowsOnSsh(r: DispatchRow): boolean {
  if (r.tab !== 'ssh' || !r.open_ports) return false
  const ports = r.open_ports.split(',').map((p) => Number(p.trim()))
  return WINDOWS_ONLY_PORTS.some((p) => ports.includes(p))
}
const dSshWindowsCount = computed(() => dispatchRows.value.filter(isWindowsOnSsh).length)

const dHiddenCount = computed(() =>
  dRoute.value === 'actionable' ? (dRouteCount.value.import ?? 0) : 0)
const dHiddenNok = computed(() => dRoute.value === 'actionable' ? dNokCount.value : 0)
// 目前頁簽（或狀態卡）裡的全部列——還沒套結果篩選／隱藏已納管。「總 Y」就是它
const dScoped = computed(() => {
  // 狀態篩選生效時頁簽一定是「全部」（pickStatus 保證），所以兩種切法不會相乘；
  // 這裡仍先攔截，確保「卡片數字＝表格列數」不會被任何別的路徑破壞。
  if (dStatus.value) return dSorted.value.filter(D_STATUS_PICK[dStatus.value])
  if (dRoute.value === 'all') return dSorted.value
  if (dRoute.value === 'actionable') return dSorted.value.filter(isActionable)
  return dSorted.value.filter((r) => r.tab === dRoute.value)
})
// 結果篩選各自幾台（在目前頁簽裡數；點下去看到的列數＝這個數字）
const dResultCount = computed(() => {
  const c = {} as Record<DResult, number>
  for (const k of Object.keys(D_RESULT_PICK) as DResult[]) c[k] = dScoped.value.filter(D_RESULT_PICK[k]).length
  return c
})
// 被「隱藏已納管」收起來的台數（寫在勾選框旁邊，不靜默藏）
const dHiddenOnboarded = computed(() =>
  (!dStatus.value && dHideOnboarded.value && dResult.value === 'all')
    ? dScoped.value.filter((r) => r.status === 'collected').length : 0)
// Q83 搜尋框：在目前頁簽／狀態篩選「之上」再篩一層，只比對畫面看得到的欄位。
// 項次 r.no 是 dispatchRows 依預設順序配好的，篩選／重排都不重編（使用者要能說「第 3 項」）。
// 「全選」也只選搜尋後看得到的（沿用 dVisible），不會勾到看不見的列。
const dSearch = ref('')
function dRowText(r: DispatchRow): string {
  return [r.no, r.ip, r.hostname, r.asset_name, r.open_ports, ROUTE_LABEL[r.route] || r.route,
    r.guide?.how, STATUS_LABEL[r.status] || r.status, r.message, r.last_fail_short, r.kind_sort, r.tags_sort]
    .filter((v) => v !== null && v !== undefined).join(' ').toLowerCase()
}
const dSearchTerms = computed(() => searchTerms(dSearch.value))
// v1.468.4（2026-10-19 使用者：「我都勾選 SSH 了，為什麼 Push Agent、只能匯入…都在裡面？」）：
// 搜尋只在**目前頁簽**（含結果篩選、隱藏已納管）裡找；別的頁簽也有符合的，另外列
// 「其他頁簽還有 N 筆符合：…」，每段點得下去（跳過去、搜尋字保留）。推翻 10-18 的「一輸入就跨全部頁簽」。
const dSearching = computed(() => dSearchTerms.value.length > 0)
const dRowSearchText = (r: DispatchRow) => dRowText(r) + ' ' + tabLabel(r.tab).toLowerCase()
// 狀態卡是明確的下鑽：不套結果篩選（卡片數字＝列數）；頁簽則套
const dBase = computed(() => (dStatus.value ? dScoped.value : dScoped.value.filter(dResultOk))
  .filter((r) => !dTag.value || (r.tags ?? []).includes(dTag.value)))
const dVisible = computed(() => dSearching.value
  ? dBase.value.filter((r) => matchesAll(dRowSearchText(r), dSearchTerms.value))
  : dBase.value)
// 待納管匯出（2026-10-22 使用者：「每個頁面都要有」）：目前頁簽＋搜尋＋篩選後看得到的那幾筆
function exportPending() {
  const tabName = dRoute.value === 'actionable' ? '動得了的' : dRoute.value === 'all' ? '全部' : tabLabel(dRoute.value)
  exportTable(`待納管_${tabName}`,
    ['#', 'IP', '主機名稱', '資產名稱', '開放埠', '走哪條路', '結果', '上次納管時間', '納管帳號',
     '上次收集', '說明', '上次失敗時間', '上次失敗原因'],
    dVisible.value.map((r: any) => [r.no, r.ip, r.hostname, r.asset_name, r.open_ports, r.route,
      STATUS_LABEL[r.status] || r.status, r.last_onboard_at, r.last_onboard_user, r.collected_at,
      r.message, r.last_fail_at, r.last_fail_short]))
}
const dOtherHits = computed(() => otherTabHits(dispatchRows.value,
  new Set(dScoped.value.map((r) => r.ip)), new Set(dVisible.value.map((r) => r.ip)),
  dSearchTerms.value, dRowSearchText))
/** 跳到別的頁簽看符合的列：搜尋字保留，結果篩選回「全部」 */
function jumpToHits(tab: string) {
  const q = dSearch.value
  setRoute(tab as DTab)
  dResult.value = 'all'
  dSearch.value = q
}
/** 同一頁簽被結果篩選／隱藏已納管收起來的符合列：放開篩選 */
function showHiddenHits() { dResult.value = 'all'; dHideOnboarded.value = false }
// 「所在頁簽」欄只在「全部」頁簽顯示（其他頁簽每列都同一個頁簽，多一欄只是雜訊）
const dShowTabCol = computed(() => dRoute.value === 'all' && !dStatus.value)
// 搜尋 0 筆、但資產庫裡有：講出來並給連結（這台還沒掃過，不是不存在）
const dAssetHits = ref<{ asset_serial: string; hostname: string | null; ip: string | null }[]>([])
const dAssetHitsQ = ref('')
let dAssetTimer: ReturnType<typeof setTimeout> | null = null
watch([dSearch, () => dVisible.value.length], () => {
  if (dAssetTimer) clearTimeout(dAssetTimer)
  const q = dSearch.value.trim()
  if (!q || dVisible.value.length) { dAssetHits.value = []; dAssetHitsQ.value = ''; return }
  dAssetTimer = setTimeout(async () => {
    try {
      const rows = await apiFetch<any[]>('/api/assets', { query: { q, limit: 50 } })
      if (dSearch.value.trim() !== q) return
      dAssetHits.value = (rows || []).map((h) => ({ asset_serial: h.asset_serial, hostname: h.hostname, ip: h.ip }))
      dAssetHitsQ.value = q
    } catch (e: any) {
      // 查不到資產庫不擋搜尋本身，但要講出來（不靜默當成「資產裡也沒有」）
      dAssetHits.value = []
      dAssetHitsQ.value = ''
      showToast(`查資產庫失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
    }
  }, 400)
})

// 批次試密碼（方案B）：只有「要憑證的 SSH 那批」勾得起來——其他路（Agent／
// 只能匯入）給帳密也沒用。勾選狀態不跨頁刷新保留，重新收集一次就清空重選。
// ⚠️ 這個條件同時也是「⚡ 一次納管」的可勾條件，是防誤納管的第一道鎖：
// probeSelected 只可能被通過 canProbe 的列填進去（見下面 pickMode 的說明）。
// 2026-10-18：「納管失敗」的也要能重試（使用者：AIX 09/22 失敗那台沒有一鍵納管、也勾不了）。
// fail-closed：VMware／儲存／網路／其他不納管、AS400、上次被 VMware 天條擋下的，一律不給。
// 這只是畫面這一道；真正的鎖在後端（onboard_engine 探測到 VMware 就拒絕、批次白名單讀資產 OS）。
const NO_ONBOARD_TABS = ['esxi', 'storage', 'network', 'ocp', 'nok_other', 'import', 'agent']
function canProbe(r: DispatchRow): boolean {
  if (r.status !== 'needs_credential' && r.status !== 'onboard_failed') return false
  if (r.route !== 'ssh' || r.guide) return false
  if (NO_ONBOARD_TABS.includes(r.tab || '')) return false
  // 歸在 Windows 頁簽（人工搬過去的）：開著 22 也不給一鍵納管——那是 Linux 建帳號的腳本
  //（2026-10-22 使用者：「2.38 有 5985 代表是 Windows，搬到 Windows 就不應該有一鍵納管」）
  if (r.tab === 'winrm') return false
  if (r.tab === 'mainframe' && !r.mainframe_onboardable) return false
  if (r.last_fail_short === 'VMware 不可納管') return false
  return true
}
const probeSelected = ref<Set<string>>(new Set())
// 「全選」只選看得見的——篩到 WinRM 時卻幫你勾了看不見的 SSH 那批，會很難解釋
const probeableRows = computed(() => dVisible.value.filter(canProbe))
const allProbeableSelected = computed(() =>
  probeableRows.value.length > 0 && probeableRows.value.every((r) => probeSelected.value.has(r.ip)))
function toggleProbe(ip: string) {
  const s = new Set(probeSelected.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  probeSelected.value = s
}
function toggleAllProbeable() {
  probeSelected.value = allProbeableSelected.value ? new Set() : new Set(probeableRows.value.map((r) => r.ip))
}
const showBatchProbe = ref(false)

// ===== Windows 納管：勾選才收（2026-10-22）=====
// 2-1 開始收集預設**不登入** Windows（結果「待納管」）；在 WinRM 頁簽勾選按「納管」才登入＋驗本機
// 管理員＋收一輪全部（WinOnboardModal 逐台送 /api/onboard/windows）。已納管的可「重新納管」「取消納管」。
// 自己一組勾選（winSelected），跟 Linux 的 probeSelected 分開——送出的永遠只有各自那一組（同 pickMode 的鎖 1）。
// 可勾條件：在 WinRM 頁簽、開著 5985（WinRM 本尊）或路由就是 WinRM、而且不是另有納管方式／待開埠。
// VMware 天條在後端（win_onboard.vmware_refusal）再擋一次，這裡只是畫面這一道。
const WIN_NO_STATUS = ['not_onboardable', 'needs_winrm_port', 'needs_ssh_port', 'import_only', 'needs_agent']
function canWin(r: DispatchRow): boolean {
  if (r.tab !== 'winrm' || WIN_NO_STATUS.includes(r.status) || r.guide) return false
  // 走 SSH 收得到的（裝了 OpenSSH 的 Windows）不另外給 Windows 納管；已 Windows 納管的才可重新納管
  if (r.status === 'collected' && !r.win?.opted_in) return false
  const ports = (r.open_ports || '').split(',').map((p) => p.trim())
  return r.route === 'winrm' || ports.includes('5985')
}
const winSelected = ref<Set<string>>(new Set())
const winRows = computed(() => dVisible.value.filter(canWin))
const allWinSelected = computed(() =>
  winRows.value.length > 0 && winRows.value.every((r) => winSelected.value.has(r.ip)))
function toggleWin(ip: string) {
  const s = new Set(winSelected.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  winSelected.value = s
}
function toggleAllWin() {
  winSelected.value = allWinSelected.value ? new Set() : new Set(winRows.value.map((r) => r.ip))
}
// 勾選的拆成「還沒納管」與「已納管（重新納管）」兩顆按鈕
const winPickNew = computed(() => winRows.value.filter((r) => winSelected.value.has(r.ip) && !r.win?.opted_in))
const winPickRe = computed(() => winRows.value.filter((r) => winSelected.value.has(r.ip) && r.win?.opted_in))
// 視窗要跑的那幾台（null＝沒開）
const winOnboardRows = ref<{ ip: string; hostname?: string | null; opted_in?: boolean }[] | null>(null)
function openWinOnboard(rows: { ip: string; hostname?: string | null; win?: WinState | null; opted_in?: boolean }[]) {
  winOnboardRows.value = rows.map((r) => ({ ip: r.ip, hostname: r.hostname, opted_in: r.opted_in ?? Boolean(r.win?.opted_in) }))
}
const winRevoke = ref<{ ip: string; hostname?: string | null } | null>(null)
const winPermRef = ref<{ load: () => void } | null>(null)
async function onWinOnboarded(okIps: string[]) {
  const ok = new Set(okIps)
  winSelected.value = new Set([...winSelected.value].filter((ip) => !ok.has(ip)))
  await refreshDispatchQuiet()   // 結果與頁簽數字都由後端算（納管紀錄疊回「已納管」）
  loadAudit()
  loadManaged()
  winPermRef.value?.load()
}
async function onWinRevoked() {
  winRevoke.value = null
  await refreshDispatchQuiet()
  loadManaged()
  winPermRef.value?.load()
}
function adminText(v: number | null | undefined) { return v === 1 ? '是' : v === 0 ? '否' : '不明' }
function winTitle(w: WinState): string {
  return `Windows 納管：${w.onboarded_by ?? '不詳'} 於 ${w.onboarded_at ?? '不詳'}；最近一次 ${w.last_at ?? '—'}`
}
// 權限面板裡點某台 IP：在這個頁簽搜尋它
function pickWinIp(ip: string) { dSearch.value = ip }
function openWinOnboardManaged() {
  openWinOnboard(managedWinPicked.value.map((ip) => ({
    ip, hostname: managedAll.value.find((x) => x.ip === ip)?.hostname ?? null, opted_in: true })))
}

// ===== 「那它走哪條路」：不納管 ≠ 不管（2026-10-16）=====
// 使用者：「網路設備／ESXi／儲存 這些都有它獨立納管方式」「不是靠 Linux 方式納管」。
// 以前那三台 ESXi 的「走哪條路」寫 Push Agent（route 是探測事實）、「下一步」寫
// 「不納管」——兩個都在誤導。現在改成**指路**：該走哪一頁就寫哪一頁，並附上選單編號。
//
// ⚠️ 選單編號**現查不寫死**：編號是 `numberNav` 依「使用者存的版面＋功能開關」
// 當場算出來的（預設 2-4 SAN 收集／2-5 HMC 收集，但使用者在「選單編輯」調過順序、
// 或某個 mod 被關掉，編號就會變）。寫死會變成叫人去看一個不存在的編號。
const { visibleNav, loadNavLayout } = useVisibleNav()
onMounted(loadNavLayout)
function navNoOf(to: string): string {
  for (const g of visibleNav.value) for (const it of g.items) if (it.to === to) return it.no || ''
  return ''
}
/** 給人看的「X-Y 頁名」；那一頁現在看不到（功能開關關掉）就只回頁名。 */
function guideWhere(r: DispatchRow): string {
  const g = r.guide
  if (!g?.page || !g.page_label) return ''
  const no = navNoOf(g.page)
  return no ? `${no} ${g.page_label}` : g.page_label
}

// ===== 手動搬到別的頁簽（2026-10-16）=====
// 使用者在公司機 1.428.0：「我還是沒看到可以移動到其他組別的方式」「圖二不能勾選，不能全選」。
// 實例：`SVR019-203` 是一般伺服器，但資產登記的「作業系統」欄寫「儲存設備」，
// 於是被歸到儲存設備頁簽。自動分類一定有錯的時候，人要能自己改。
//
// 寫的是**既有的** os_type_override 表（後端 os_override.py，2026-09-18 就做好了：
// 重匯 CIA 不沖掉、留誰／何時／原因／機器原判、取消是軟刪除）。資產明細頁那個入口
// 保留，兩邊寫同一張表——這裡只是把入口也放到使用者真正看到問題的地方。
//
// ⚠️ 安全邊界（使用者拍板）：搬成「SSH 收集」只影響顯示與試連，**不會**讓系統自動
// 去那台建本機帳號；建帳號維持要人按「⚡ 一鍵納管」，而那條路的白名單讀資產登記的
// 作業系統欄，完全不看這張表（tests/asset_module/test_adopt_move.py 釘住）。
// 分類頁簽：**全站唯一一份在後端**（collect_dispatch.CATEGORY_TABS，GET /api/adopt/move-choices 的 tabs）。
// 上面那排頁簽、待納管／批次／已納管的「改分類」選單、資產明細頁的「搬移分類」全部讀這一份
// （2026-10-18 使用者：「這裡有幾個選項，那裡分類就應該有幾個選項」）。
// Push Agent／只能匯入是連線狀態，選單裡列出但不能選（movable=false，附 why_not）。
interface TabChoice { os_type: string; label: string; onboardable: boolean }
interface CatTab { tab: string; label: string; movable: boolean; why_not: string | null; choices: TabChoice[] }
const catTabs = ref<CatTab[]>([])
const catTabsError = ref('')
async function loadCatTabs() {
  try {
    const r = await apiFetch<{ tabs: CatTab[] }>('/api/adopt/move-choices')
    catTabs.value = r.tabs || []
    catTabsError.value = ''
  } catch (e: any) {
    catTabs.value = []
    catTabsError.value = `讀不到分類頁簽：${e?.data?.detail ?? e?.message ?? e}`
  }
}
onMounted(loadCatTabs)
function tabLabel(t: string | null | undefined): string {
  return catTabs.value.find((c) => c.tab === t)?.label ?? (t || '')
}

// 認不出是哪一台的搬不了：os_override 要「主機名＋IP」或「資產序號」才認台
//（machine_key；缺了就會變成所有未登記 IP 共用一個鍵，一改全改）。
// **不靜默 disable**：按鈕 title 會寫明為什麼不能搬。
function canMove(r: DispatchRow): boolean {
  return Boolean((r.hostname && r.ip) || r.asset_serial)
}
function whyCannotMove(r: DispatchRow): string {
  return canMove(r) ? '' : '這個 IP 資產庫沒有登記（沒有主機名也沒有序號）——認不出是哪一台，'
    + '搬了會套到所有未登記的位址上。請先在「登記成資產」把它登記起來。'
}

// ===== 勾選要拿來做什麼：畫面上明說，不再由「你在哪個頁簽」暗示（2026-10-16 第二版）=====
//
// 使用者在公司機 1.429.0 勾了 4 台 Windows+OpenSSH 想一次搬到 WinRM 收集：
// 「**我沒辦法一次改**」。因為第一版把勾選的用途綁死在頁簽上——能動的三頁（SSH／
// WinRM／Push Agent）的勾選固定是「試密碼／一次納管」，所以那三頁沒有批次搬移。
//
// 現在改成**明寫的模式切換**（「勾選後要做什麼：🔑 佈收集帳號 ／ 📁 搬移分類」），
// 每一個頁簽都切得過去，所以每一頁都能批次搬移。
//
// 防誤納管（使用者拍板的紅線：「勾了一批想搬移，結果按到一次納管」絕對不能發生）
// 靠三道獨立的鎖，不是靠按鈕 disabled：
//   1. **兩組勾選、兩個 Set**：搬移勾的進 `moveSelected`，試密碼勾的進 `probeSelected`，
//      而「⚡ 一次納管」送出的永遠只有 `probeSelected`。搬移模式下那個 Set 必定是空的。
//   2. **按鈕按模式整個不存在**：搬移模式下「🔑 批次試密碼」「⚡ 一次納管」**不 render**
//      （v-if，不是 disabled）——沒有東西可以誤按。
//   3. **換模式／換頁簽一律清空兩個 Set**：勾選不會從一個用途漂到另一個用途，
//      也不會留著看不見的勾選（看不見的勾選是最容易誤送的）。
//   另外第一道鎖的可勾條件本身就不同：試密碼只有 `needs_credential + route=ssh`
//   勾得起來（canProbe），搬移只要認得出是哪一台就能勾（canMove）。
//
// 每一列的「改分類」按鈕在所有頁簽、所有模式都在，單台搬移不受模式影響。
type PickMode = 'onboard' | 'move' | 'tag'
const pickMode = ref<PickMode>('onboard')
//: 這一頁有沒有「佈收集帳號」做得到的對象。沒有就不給切過去（並寫明原因，不靜默 disable）
const canOnboardHere = computed(() => probeableRows.value.length > 0 || winRows.value.length > 0)
//: 實際生效的模式：選了「佈收集帳號」但這一頁根本沒有那種機器時自動落回搬移，
//: 不然在儲存設備頁會看到一個什麼都做不了的空工具列。
const effPickMode = computed<PickMode>(() =>
  (pickMode.value === 'onboard' && !canOnboardHere.value) ? 'move' : pickMode.value)
const moveMode = computed(() => effPickMode.value === 'move')
// 🏷 加標籤（v1.468.6）：第三個模式、第三個 Set。每一列都勾得起來（沒登記的照樣能勾，
// 送出後該列回「未登記，無法加標籤」——不靜默略過）
const tagMode = computed(() => effPickMode.value === 'tag')
const onboardMode = computed(() => effPickMode.value === 'onboard')
const tagSelected = ref<Set<string>>(new Set())
const allTagSelected = computed(() => dVisible.value.length > 0 && dVisible.value.every((r) => tagSelected.value.has(r.ip)))
function toggleTag(ip: string) {
  const s = new Set(tagSelected.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  tagSelected.value = s
}
function toggleAllTag() {
  tagSelected.value = allTagSelected.value ? new Set() : new Set(dVisible.value.map((r) => r.ip))
}
function openTagFor(ips: Set<string>) {
  tagRows.value = dispatchRows.value.filter((r) => ips.has(r.ip))
    .map((r) => ({ ip: r.ip, asset_serial: r.asset_serial, hostname: r.hostname }))
}
const moveSelected = ref<Set<string>>(new Set())
const movableRows = computed(() => dVisible.value.filter(canMove))
const allMovableSelected = computed(() =>
  movableRows.value.length > 0 && movableRows.value.every((r) => moveSelected.value.has(r.ip)))
function toggleMove(ip: string) {
  const s = new Set(moveSelected.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  moveSelected.value = s
}
function toggleAllMovable() {
  moveSelected.value = allMovableSelected.value ? new Set() : new Set(movableRows.value.map((r) => r.ip))
}
//: 清空兩個 Set（鎖 3）。換頁簽、換模式、搬完都走這一支，不要各寫各的。
function clearPicks() {
  moveSelected.value = new Set()
  probeSelected.value = new Set()
  winSelected.value = new Set()
  tagSelected.value = new Set()
}
// 換頁簽就清空，並把模式帶回預設（能佈帳號的頁簽預設佈帳號，其餘自動落回搬移）
watch(dRoute, () => { clearPicks(); pickMode.value = 'onboard' })
// 換狀態篩選同理：看不見的勾選是最容易誤送的，所以換一個切法就全部清掉
watch(dStatus, () => { clearPicks(); pickMode.value = 'onboard' })
// 換模式也清空：勾選絕對不可以跨用途帶過去
watch(effPickMode, clearPicks)
// 換結果篩選／隱藏已納管也清空：被篩掉的列不可以留著看不見的勾選
watch([dResult, dHideOnboarded], clearPicks)

// 搬移對話框。rows 是這次要搬的那幾列（單台＝一列，批次＝勾選的那些）
const moveRows = ref<DispatchRow[]>([])
// 選單的值是「頁簽|子類別」（大型主機分 AIX／AS400），其他頁簽只有一個子類別
const moveTab = ref('')
const moveReason = ref('')
const moveBusy = ref(false)
function openMove(rows: DispatchRow[]) {
  moveRows.value = rows.filter(canMove)
  moveTab.value = ''
  moveReason.value = ''
}
interface MoveOpt { value: string; label: string; disabled: boolean; note: string }
// 選項＝頁簽列的每一個分類頁簽，一個不少、同順序（大型主機展開成 AIX／AS400 兩項）。
// 這批全在同一個頁簽（而且那個頁簽只有一個子類別）就標「目前在這」不給選——但**還是列出來**。
const moveTargetChoices = computed<MoveOpt[]>(() => {
  const tabs = new Set(moveRows.value.map((r) => r.tab))
  const only = tabs.size === 1 ? [...tabs][0] : null
  const out: MoveOpt[] = []
  for (const t of catTabs.value) {
    if (!t.movable) {
      out.push({ value: `${t.tab}|`, label: t.label, disabled: true, note: '（連線狀態，不能指定）' })
      continue
    }
    for (const c of t.choices) {
      const multi = t.choices.length > 1
      const here = t.tab === only && !multi
      out.push({
        value: `${t.tab}|${c.os_type}`,
        label: multi ? `${t.label} — ${c.label}` : t.label,
        disabled: here,
        note: here ? '（目前在這）' : (c.onboardable ? '（會去試連）' : '（另有納管方式，不試連）'),
      })
    }
  }
  return out
})
const moveWhyNot = computed(() => catTabs.value.filter((t) => !t.movable && t.why_not)
  .map((t) => `${t.label}：${t.why_not}`))
async function submitMove(restore = false) {
  if (!moveRows.value.length) return
  if (!restore && !moveTab.value) return
  moveBusy.value = true
  try {
    const out = await apiFetch<{ moved: string[]; failed: { ip: string; error: string }[] }>(
      '/api/adopt/move-tab', {
        method: 'POST',
        body: {
          targets: moveRows.value.map((r) => ({
            ip: r.ip, hostname: r.hostname, asset_serial: r.asset_serial,
          })),
          tab: restore ? null : moveTab.value.split('|')[0],
          os_type: restore ? null : (moveTab.value.split('|')[1] || null),
          reason: moveReason.value.trim() || null,
        },
      })
    const label = restore ? '機器自動判定'
      : (moveTargetChoices.value.find((c) => c.value === moveTab.value)?.label ?? moveTab.value)
    if (out.moved.length) showToast(`已搬 ${out.moved.length} 台到「${label}」`, 'success')
    // 失敗的逐筆講，不要只說「部分失敗」
    for (const f of out.failed) showToast(`${f.ip} 搬不了：${f.error}`, 'error')
    moveRows.value = []
    clearPicks()
    await refreshDispatchQuiet()   // 頁簽數字與每一列的歸屬都由後端算，前端不自己挪列
    loadManaged()                  // 已納管表的 OS 欄也讀同一張人工指定表
    loadFailures()                 // 納管失敗清單的作業系統大類也讀同一張
  } catch (e: any) {
    showToast(`搬移失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  } finally {
    moveBusy.value = false
  }
}
function overrideTitle(r: DispatchRow): string {
  const o = r.os_override
  if (!o) return ''
  const from = o.auto_at_time ? `機器原本判「${o.auto_at_time}」，` : ''
  return `人工指定：${from}改成「${o.os_type}」（${tabLabel(o.tab)} 頁簽）`
    + `　${o.by ?? '不詳'} 於 ${o.at}${o.reason ? `　原因：${o.reason}` : ''}`
}

// ===== 勾選多台一次納管（使用者 2026-09-11）=====
// 兩張表各自勾：上面「收集結果」要憑證的那批（跟批次試密碼共用同一組勾選），
// 下面「登記成資產」的候選清單。兩邊都送進同一個 BatchOnboardModal，
// 每台走的是跟「⚡ 一鍵納管」同一支 API——等於幫你每台各點一次。
const adoptSelected = ref<Set<string>>(new Set())
const allAdoptSelected = computed(() =>
  sorted.value.length > 0 && sorted.value.every((h) => adoptSelected.value.has(h.ip)))
function toggleAdopt(ip: string) {
  const s = new Set(adoptSelected.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  adoptSelected.value = s
}
function toggleAllAdopt() {
  adoptSelected.value = allAdoptSelected.value ? new Set() : new Set(sorted.value.map((h) => h.ip))
}
const batchOnboardIps = ref<string[] | null>(null)
function onBatchOnboarded(okIps: string[]) {
  const ok = new Set(okIps)
  hosts.value = hosts.value.filter((h) => !ok.has(h.ip))
  for (const r of dispatch.value?.results ?? []) {
    if (ok.has(r.ip) && r.status !== 'collected' && dispatch.value) {
      r.status = 'collected'
      r.message = '已佈好收集帳號，現在收得到'
      dispatch.value.collected += 1
      dispatch.value.needs_action -= 1
    }
  }
  probeSelected.value = new Set([...probeSelected.value].filter((ip) => !ok.has(ip)))
  adoptSelected.value = new Set([...adoptSelected.value].filter((ip) => !ok.has(ip)))
  managedSelected.value = new Set([...managedSelected.value].filter((ip) => !ok.has(ip)))
  loadAudit()
  loadManaged()
}

// ===== 已納管主機（使用者 2026-09-11：「重新納管、取消納管、已納管、編輯應該放在手動登記旁邊」→ YES）=====
// 上面「登記成資產」只列還沒登記的主機，已納管的根本不在那裡，所以另開一張表。
// 資料用 /api/manage-state（儀表板四格與資產清單共用的同一份四態判定），不另算一套。
// 用途：一千多台要補佈 v1.93 起才有的帳號盤點輔助程式——全選、輸入一次密碼就好。
interface ManagedHost {
  asset_serial: string | null; hostname: string | null; ip: string
  state: string; collect_checked_at: string | null; os_type?: string; root_key?: boolean
  // 人工指定過 OS 類型的話（後端 manage_state 帶出來，跟待納管表同一張 os_type_override）
  os_override?: { by: string | null; at: string; reason: string | null; auto_at_time: string | null } | null
  // 前端補：固定項次、上次失敗（同待納管表那一份 last_attempts）
  no?: number; last_fail_at?: string | null; last_fail_short?: string | null; last_fail_msg?: string | null
  tags?: string[]; tags_sort?: string
}
const managed = ref<ManagedHost[]>([])
// 「我沒有密碼：取得納管指令」那個視窗要處理的主機（null＝沒開）
const pasteIps = ref<string[] | null>(null)
// 已納管但失聯（連上過、最近 7 天沒連上）：不算進「已納管」台數（跟首頁同一把尺），
// 但一樣列在這張表、一樣能勾選重新納管——最需要重新納管的往往就是這批（v1.457.1）
const managedStale = ref<ManagedHost[]>([])
// 已納管主機也看得到「上次失敗」：重新納管失敗了，這台雖然還收得到（舊帳號還在），
// 但那次重新納管沒做成——要提醒（使用者 2026-10-18）。同一支 last_attempts。
const mAuditByIp = ref<Record<string, LastAttempt>>({})
async function loadManagedAudit() {
  try { mAuditByIp.value = await fetchLastAttempts([...managed.value, ...managedStale.value].map((m) => m.ip)) }
  catch { /* 查不到就讓那一欄維持「—」，不擋整張表 */ }
}
const managedAll = computed<ManagedHost[]>(() => [...managed.value, ...managedStale.value].map((m, i) => {
  const f = m.ip ? mAuditByIp.value[m.ip]?.fail : null
  return { ...m, no: i + 1, last_fail_at: f?.at ?? null, last_fail_short: f?.short ?? null,
           last_fail_msg: f?.message ?? null,
           tags: tagsOf(m.asset_serial), tags_sort: tagsOf(m.asset_serial).join(' ') }
}))
// 已納管表的「改分類」：沿用待納管那個搬移視窗與同一支 /api/adopt/move-tab（寫同一張
// os_type_override；只影響顯示與統計，不動納管白名單——test_adopt_move.py 釘住）。
// 已納管表沒有頁簽，所以把這台目前的 OS 類型換成頁簽名，讓視窗講得出「目前在哪一類」。
const OS_TYPE_TAB: Record<string, string> = {
  Linux: 'ssh', Windows: 'winrm', AIX: 'mainframe', 'IBM i': 'mainframe', 'Storage/SAN': 'storage',
  '網路設備': 'network', VMware: 'esxi', '設備': 'nok_other', 'OpenShift 節點': 'ocp',
  '管理韌體(BMC)': 'nok_other',
}
function openMoveManaged(m: ManagedHost) {
  openMove([{
    ip: m.ip, alive: 1, open_ports: null, route: 'ssh', status: 'collected',
    asset_serial: m.asset_serial, hostname: m.hostname, registered: m.asset_serial ? 1 : 0,
    message: `已納管主機，目前 OS 類型：${m.os_type || '未填'}`,
    tab: OS_TYPE_TAB[m.os_type || ''] || '', no: m.no,
    os_override: m.os_override ? { ...m.os_override, os_type: m.os_type || '', tab: OS_TYPE_TAB[m.os_type || ''] || '' } : null,
  }])
}
const managedKw = ref('')
const managedOs = ref('')   // '' ＝全部；Linux／Windows／AIX 二次篩選（同一種 OS 一次補佈）
async function loadManaged() {
  try {
    // EXIST1（2026-10-18）：已納管全站一個定義（收集帳號 7 天內連得上，CIA 無關），
    // 讀首頁同一份逐台代表清單（state=已納管），台數保證跟首頁「已納管」一樣——以前逐筆濾，
    // 同一台登記多筆會多算。
    const r = await apiFetch<{ items: ManagedHost[] }>('/api/manage-state', { params: { state: '已納管' } })
    // 不濾掉沒有 IP 的那幾台——濾掉台數就會比首頁少（沒 IP 的照列，只是不能勾選重新納管）
    managed.value = r.items || []
    const st = await apiFetch<{ items: ManagedHost[] }>('/api/manage-state', { params: { state: '已納管但失聯' } })
    managedStale.value = st.items || []
    saveAdoptCache({ managed: managed.value, managedStale: managedStale.value })
  } catch { managed.value = []; managedStale.value = [] }
  mLoaded.value = true
  loadManagedAudit()
  loadManagedWin()
}
// 已納管表裡哪幾台是「Windows 納管」的（2026-10-22）：那幾台的重新納管／取消納管走 Windows 那套
// （不要密碼、目標機不動），不可以打開 Linux 的一鍵納管／取消納管視窗。沒給 IP＝後端列全部納管中的。
const managedWin = ref<Record<string, WinState>>({})
async function loadManagedWin() {
  try {
    const r = await apiFetch<{ items: Record<string, WinState> }>('/api/onboard/windows/state')
    managedWin.value = r.items || {}
  } catch (e: any) {
    managedWin.value = {}
    showToast(`讀不到 Windows 納管狀態：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  }
}
function isWinManaged(ip: string | null | undefined): boolean {
  return Boolean(ip && managedWin.value[ip]?.opted_in)
}
// 已納管表勾選的拆兩堆：Windows 納管的走 Windows 重新納管（不要密碼），其餘照舊走 Linux 批次
const managedWinPicked = computed(() => [...managedSelected.value].filter((ip) => isWinManaged(ip)))
const managedLinuxPicked = computed(() => [...managedSelected.value].filter((ip) => !isWinManaged(ip)))
// ===== 先顯示上一次的資料，背景更新（2026-10-22 使用者：「要不要預設上一次資料，重整再更新」）=====
// 頁簽數字以前要等清單讀完才出來，讀完前顯示 0——0 是錯的訊息。現在：沒讀完又沒有上次資料 → 「…」；
// 有上次資料 → 先顯示、標「上次資料 幾點，更新中」，新的回來就換掉。只存在這台瀏覽器（localStorage）。
const ADOPT_CACHE_KEY = 'adopt.lastData.v1'
const mLoaded = ref(false)
const fLoaded = ref(false)
const cacheAt = ref<string | null>(null)   // 正在顯示的舊資料是幾點的；null＝畫面已是最新
function readAdoptCache(): any {
  try { return JSON.parse(localStorage.getItem(ADOPT_CACHE_KEY) || 'null') } catch { return null }
}
function saveAdoptCache(part: Record<string, any>) {
  try {
    localStorage.setItem(ADOPT_CACHE_KEY, JSON.stringify({ ...(readAdoptCache() || {}), ...part, at: new Date().toISOString() }))
  } catch { /* 無痕／空間滿：不記上次資料是可接受的降級 */ }
}
onMounted(() => {
  const c = readAdoptCache()
  if (!c) return
  if (!mLoaded.value && c.managed) { managed.value = c.managed; managedStale.value = c.managedStale || [] }
  if (!fLoaded.value && c.failures) { failures.value = c.failures; if (c.failSummary) failSummary.value = c.failSummary }
  if (!(mLoaded.value && fLoaded.value)) cacheAt.value = c.at
})
watch([mLoaded, fLoaded], ([m, f]) => { if (m && f) cacheAt.value = null })
const cacheAtText = computed(() => cacheAt.value ? new Date(cacheAt.value).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' }) : '')
// ===== 頁簽（2026-10-18 使用者：這頁一長條找不到東西 → 拆三個頁簽）=====
// 開始收集／待納管（預設）／已納管主機。用 v-show 不用 v-if：切頁簽不能把勾選、搜尋、
// CollectLog 的 ref、輪詢中的進度弄丟。網址帶 #collect／#pending／#onboarded 可直接開到那一頁；
// 舊的 #managed-hosts（以前的跳轉錨點）視同 #onboarded。上次看哪一頁記在 localStorage。
// unregistered／failures（2026-10-22）：以前接在待納管表格下面，要往下拉才看得到
type AdoptTab = 'collect' | 'pending' | 'unregistered' | 'failures' | 'onboarded'
const ADOPT_TABS: AdoptTab[] = ['collect', 'pending', 'unregistered', 'failures', 'onboarded']
const TAB_KEY = 'adopt.tab'
const route = useRoute()
const router = useRouter()
function tabFromHash(h: string | undefined): AdoptTab | null {
  const t = (h || '').replace(/^#/, '')
  if (t === 'managed-hosts') return 'onboarded'
  return (ADOPT_TABS as string[]).includes(t) ? (t as AdoptTab) : null
}
// SSR 拿不到 hash（瀏覽器不送），一律先畫預設頁簽，掛載後再依 hash／上次紀錄切——避免 hydration 不一致
const activeTab = ref<AdoptTab>('pending')
function setTab(t: AdoptTab) {
  activeTab.value = t
  try { localStorage.setItem(TAB_KEY, t) } catch { /* 無痕／被封鎖：不記上次頁簽是可接受的降級 */ }
  if (route.hash !== `#${t}`) router.replace({ hash: `#${t}`, query: route.query }).catch(() => { /* 同頁換 hash 失敗不影響畫面 */ })
}
onMounted(() => {
  const fromHash = tabFromHash(route.hash)
  if (fromHash) { activeTab.value = fromHash; return }   // 網址指定的優先
  try {
    const saved = localStorage.getItem(TAB_KEY)
    if (saved && (ADOPT_TABS as string[]).includes(saved)) activeTab.value = saved as AdoptTab
  } catch { /* 讀不到就用預設「待納管」 */ }
})
watch(() => route.hash, (h) => { const t = tabFromHash(h); if (t) activeTab.value = t })
function mRowKey(m: ManagedHost) { return m.ip || `${m.asset_serial ?? ''}|${m.hostname ?? ''}` }
onMounted(loadManaged)
onMounted(loadSegTree)   // 網段配置表（開始收集要用它挑範圍）
// 各 OS 現有幾台（篩選鈕上顯示數字，也決定哪些鈕要出現——沒有 AIX 就不長 AIX 鈕）
const managedOsCounts = computed(() => {
  const c: Record<string, number> = {}
  for (const m of managedAll.value) c[m.os_type || '未填'] = (c[m.os_type || '未填'] || 0) + 1
  return c
})
//: 已納管表依標籤篩（點表格裡的標籤）
const mTag = ref('')
const managedShown = computed(() => {
  const k = managedKw.value.trim().toLowerCase()
  const os = managedOs.value
  return managedAll.value.filter((m) => {
    if (os && (m.os_type || '未填') !== os) return false
    if (mTag.value && !(m.tags ?? []).includes(mTag.value)) return false
    if (!k) return true
    return [m.ip, m.hostname, m.asset_serial].some((v) => (v || '').toLowerCase().includes(k))
  })
})
const { sortKey: mKey, sortDir: mDir, toggle: mToggle, sorted: mSorted } = useSort(managedShown, 'ip')
const managedSelected = ref<Set<string>>(new Set())
// 全選只選看得見的（搜尋篩過之後的那批）——同上面兩張表的規則
const allManagedSelected = computed(() =>
  mSorted.value.some((m) => m.ip) && mSorted.value.every((m) => !m.ip || managedSelected.value.has(m.ip)))
function toggleManaged(ip: string) {
  const s = new Set(managedSelected.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  managedSelected.value = s
}
function toggleAllManaged() {
  managedSelected.value = allManagedSelected.value ? new Set() : new Set(mSorted.value.filter((m) => m.ip).map((m) => m.ip))
}

// ===== 已納管主機：勾選多台「收集全部」（2026-10-20 使用者：「我要怎麼將已收納主機都重新收集一次」）=====
// 跟資產頁那顆同六樣、同紀錄（kind=post_onboard），差別是排進背景隊伍逐台跑（後端一條執行緒，
// 跟單台納管共用同時上限）。要資產序號才收得了——沒登記的那幾台勾了也不會送。
interface PostQueue {
  running: boolean; total: number; done: number; ok: number; failed: number; deferred: number
  pending: number; current: string | null; started_at: string | null; finished_at: string | null
  error?: string | null
  current_progress?: { label?: string; step?: number; total?: number } | null
}
const pq = ref<PostQueue | null>(null)
const pqBusy = ref(false)
let pqTimer: ReturnType<typeof setInterval> | null = null
const selectedSerials = computed(() =>
  managedAll.value.filter((m) => m.ip && managedSelected.value.has(m.ip) && m.asset_serial).map((m) => m.asset_serial as string))
const serialHost = computed<Record<string, string>>(() => {
  const o: Record<string, string> = {}
  for (const m of managedAll.value) if (m.asset_serial) o[m.asset_serial] = `${m.hostname || m.ip || m.asset_serial}（#${m.no}）`
  return o
})
async function loadPostQueue() {
  try { pq.value = await apiFetch<PostQueue>('/api/onboard/post-collect-queue') }
  catch (e: any) { showToast(`收集進度查不到：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
  if (pq.value?.running && !pqTimer) pqTimer = setInterval(() => { loadPostQueue() }, 3000)
  if (!pq.value?.running && pqTimer) { clearInterval(pqTimer); pqTimer = null; logRef.value?.reload?.() }
}
async function collectAllSelected() {
  const n = selectedSerials.value.length
  if (!n) return
  const skipped = managedSelected.value.size - n
  if (!confirm(`對勾選的 ${n} 台各收一輪全部（硬體規格／服務／軟體／帳號／OS 重建／組態檢核）？\n\n`
    + '・唯讀收集，不會改動目標機。\n・背景逐台排隊跑，一台約數十秒到幾分鐘；可以離開這頁。'
    + (skipped > 0 ? `\n・另有 ${skipped} 台沒有資產序號，收不了，不會送。` : ''))) return
  pqBusy.value = true
  try {
    const r = await apiFetch<PostQueue & { added: number; already: number }>('/api/onboard/post-collect-batch',
      { method: 'POST', body: { asset_serials: selectedSerials.value } })
    pq.value = r
    showToast(`已排入 ${r.added} 台${r.already ? `（${r.already} 台本來就在排隊）` : ''}`, 'success')
    loadPostQueue()
  } catch (e: any) {
    showToast(`收集全部沒有送出：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { pqBusy.value = false }
}
onMounted(loadPostQueue)
onBeforeUnmount(() => { if (pqTimer) clearInterval(pqTimer) })

// 匯出 Excel（2026-10-20 使用者：「把納管主機 整理EXCEL匯出」）：照目前的搜尋／OS／標籤篩選，
// 後端匯出篩完的**全部**列（不是畫面剛好畫出來的那幾列），項次跟畫面「#」同一個號碼。
const rtCfg = useRuntimeConfig()
const managedExportUrl = computed(() => {
  const q = new URLSearchParams()
  if (managedKw.value.trim()) q.set('kw', managedKw.value.trim())
  if (managedOs.value) q.set('os', managedOs.value)
  if (mTag.value) q.set('tag', mTag.value)
  const base = `${rtCfg.public.apiBase}/api/onboard/managed/export`
  const s = q.toString()
  return s ? `${base}?${s}` : base
})
const revokeIp = ref<string | null>(null)
function onRevoked() {
  revokeIp.value = null
  loadManaged()
  loadAudit()
}

const ROUTE_LABEL: Record<string, string> = {
  ssh: 'SSH 收集', winrm: 'WinRM 收集', agent: 'Push Agent', import: '只能匯入',
}
const STATUS_LABEL: Record<string, string> = {
  // 「不納管」會被讀成「這台不用管了」——實際上是不走 Linux 那套（SSH 建本機帳號），
  // 各自另有收集路徑（下一步欄會寫是哪一條）。跟後端 collect_dispatch.STATUS_LABEL 同一個字。
  collected: '已納管', not_onboardable: '另有納管方式', needs_credential: '待佈身分', needs_agent: '待裝 Agent',
  import_only: '只能匯入', failed: '收集失敗',
  // 2026-10-22：Windows 勾選才收——還沒納管的不登入，列成「待納管」
  win_pending: '待納管',
  // 2026-10-18：漏了這個，畫面直接露出 onboard_failed。後端每個狀態都要有中文
  // （test_adopt_failed_retry 逐一比對 collect_dispatch.STATUS_LABEL，少一個就紅）
  onboard_failed: '納管失敗',
  // 2026-10-19：進不去的機器判斷是 Windows／AIX 時（Push Agent 只支援 Linux／Unix）
  needs_winrm_port: '待開 WinRM', needs_ssh_port: '待開 SSH',
}
// 不納管的類別（後端 onboard_eligibility 的 kind）→ 方塊底下的短標籤。
// 這裡刻意用短字而不是後端給人看的完整標籤（「設備（不在可納管作業系統清單）」）——
// 方塊底下塞不進一句話，長標籤留給逐列的 message。
const NOK_KIND_LABEL: Record<string, string> = {
  storage: '儲存', network: '網路', esxi: 'ESXi', appliance: '設備', immutable: 'OpenShift', banner: 'banner 自報',
}
// 顯示順序固定，不跟著台數多寡跳動：使用者在找「儲存幾台」，
// 每次重跑都換位置的話他得重新找一次。不在這張表裡的類別排最後。
// 方塊底下的類別小計點下去＝跳到對應頁簽（下鑽）。不在這張表的類別都在「其他不納管」
const NOK_KIND_TAB: Record<string, DTab> = { storage: 'storage', network: 'network', esxi: 'esxi', immutable: 'ocp' }
const NOK_KIND_ORDER = ['storage', 'network', 'esxi', 'appliance', 'immutable', 'banner']

// 「不納管」是一個合計數字，使用者看不出其中幾台是儲存設備（2026-10-13
// 原話：「是 storage 你怎麼抓不出來，可把 storage 獨立分類出來」）。
// 數字 0 的類別不列——沒有就是沒有，列一排 0 等於要人自己過濾。
const nokKinds = computed<{ kind: string; label: string; n: number }[]>(() => {
  const m = dispatch.value?.not_onboardable_by_kind ?? {}
  const keys = Object.keys(m).filter((k) => (m[k] ?? 0) > 0)
  keys.sort((a, b) => {
    const ia = NOK_KIND_ORDER.indexOf(a), ib = NOK_KIND_ORDER.indexOf(b)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b)
  })
  return keys.map((k) => ({ kind: k, label: NOK_KIND_LABEL[k] || k, n: m[k] ?? 0 }))
})

// 綠＝這台現在收得到；黃＝人做一件事就能收到；灰＝這條路走不通，只能靠匯入
const STATUS_TONE: Record<string, string> = {
  collected: 'ok', needs_credential: 'warn', needs_agent: 'warn',
  failed: 'bad', import_only: 'off', not_onboardable: 'off', onboard_failed: 'bad',
  needs_winrm_port: 'warn', needs_ssh_port: 'warn', win_pending: 'warn',
}

// 下面的主機清單要看得出「這台上次收集的結果」（2026-09-16 使用者：
// 「收集完後，失敗或成功我也不曉得，因為在下面的主機我也看不出來」）。
// 上面的結果表是「這一輪跑了什麼」，下面的候選清單是「還有哪些要處理」——
// 兩張表對不起來時，人就得自己用 IP 一筆一筆比對。
const dispatchByIp = computed<Record<string, any>>(() => {
  const m: Record<string, any> = {}
  for (const r of dispatch.value?.results ?? []) m[r.ip] = r
  return m
})
const lastRunAt = computed(() => dispatch.value?.run?.finished_at || dispatch.value?.run?.started_at || '')

// 失敗要留在畫面上（2026-09-15 使用者：「失敗都沒有任何輸出，根本不知道錯在哪裡」）——
// toast 幾秒就消失，而且等很久的請求可能被網路中斷、後端其實還在跑。
const dispatchError = ref('')
const logRef = ref<{ reload: () => void } | null>(null)
async function reloadLatest() {
  try {
    dispatch.value = await apiFetch<DispatchOut>('/api/collect/dispatch/latest')
    dispatchError.value = ''
    showToast('已載入上次的收集結果', 'success')
  } catch (e: any) {
    showToast(`載入失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  }
}

// 搬移之後重讀：頁簽歸屬與數字全由後端 summarize 算，前端不自己把列搬來搬去——
// 不然畫面上的數字跟下次重新整理會不一樣。不發 toast（搬移自己已經報過了）。
async function refreshDispatchQuiet() {
  try { dispatch.value = await apiFetch<DispatchOut>('/api/collect/dispatch/latest') } catch { /* 讀不到就維持畫面 */ }
}

// 自動分批（2026-09-16 使用者：「你應該幫我自動分」「你有分批嗎?」）。
// 超過一批的量後端會在背景跑並馬上回 {started:true}，這裡改成輪詢進度——
// 以前同步等結果，1270 台一定被 HTTP 逾時切斷，畫面顯示「收集沒有完成（跑了 0 秒）」。
const batchInfo = ref<{ gap_seconds: number; batch_size: number; hard_max: number
                         skip_fresh_hours?: number; skip_empty_hours?: number
                         registered_count?: number } | null>(null)
// 「全部重收」（v1.468.4）：預設不勾＝只收新的（上次已納管／空位址、在時間窗內的不重探）
const dFull = ref(false)
// 「只掃已登記的 IP」（v1.468.5）：公司整片網段九成是空位址，白天只看登記的機器就夠
// 白天預設勾（使用者 2026-10-19）；整片網段的新機器發現交給每週六晚上那一輪
const dRegOnly = ref(true)
const progress = ref<{ batch_done: number; batch_total: number; done_count: number; total: number } | null>(null)
let pollTimer: any = null
// 這一輪的心跳狀態（2026-10-20）：後端 run_liveness 算的，前端不另判
//   live＝真的在跑；stale＝超過 N 分鐘沒進度（服務重啟殺掉了執行緒之類），講「可能已中斷」
const runInfo = ref<any>(null)
const staleRun = ref<any>(null)
const canAdmin = ref(false)
const stopBusy = ref(false)
function hhmm(ts?: string | null) { return ts ? String(ts).slice(11, 16) : '' }
function mmdd(ts?: string | null) { return ts ? String(ts).slice(5, 10).replace('-', '/') : '' }
// 頁簽上的字：講得出在跑什麼（不再是光禿禿的「收集中…」）
const collectBusyText = computed(() => {
  if (dispatching.value) {
    if (runInfo.value?.stop_requested) return '停止中：目前那台收完就停'
    return busyCore.value
  }
  if (staleRun.value) return `可能已中斷（最後進度 ${hhmm(staleRun.value.last_progress_at)}）`
  if (pq.value?.running) return `納管後收集 ${Math.max(0, pq.value.total - pq.value.done)} 台排隊中`
  return ''
})
// 按鈕與頁簽同一句（2026-10-20 使用者：「應該顯示『收集中：幾台』」）：一律帶台數，逐台更新
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
// 這次（或上次）的範圍：講清楚「收了哪些」，免得把下面全部網段的涵蓋率讀成這次的範圍
const runScope = computed(() => {
  const r = runInfo.value
  if (!r?.target_count && !dispatching.value) return ''
  const n = r?.target_count ?? targetCount.value
  const raw = String(r?.targets_raw ?? targets.value ?? '').replace(/\s+/g, ' ').trim()
  return `${dispatching.value ? '這次' : '上次'}範圍：${n} 個位址（${raw.length > 80 ? raw.slice(0, 80) + '…' : raw}）`
})
const pqPanelOpen = ref(false)
function goBusy() {
  // 點頁簽上的進度字 → 帶到那件事的進度條
  if (!dispatching.value && !staleRun.value && pq.value?.running) {
    setTab('collect')
    pqPanelOpen.value = true
    nextTick(() => document.querySelector('[data-test="pq-panel"]')?.scrollIntoView({ block: 'center' }))
    return
  }
  setTab('collect')
  nextTick(() => document.querySelector('[data-test="dispatch-progress"]')?.scrollIntoView({ block: 'center' }))
}
// ===== 收完的結果卡（2026-10-20 使用者：「寫收集完了，我也不知道有沒有成功還是失敗」）=====
const runSum = ref<any>(null)
const runSumOpen = ref<string>('')
async function loadRunSummary(runId?: number | null) {
  if (!runId) return
  try {
    runSum.value = await apiFetch(`/api/collect/dispatch/${runId}/summary`)
    runSumOpen.value = runSum.value.total <= 5 ? '*' : ''
  } catch (e: any) {
    showToast(`這一輪的結果讀不到：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
function runSumLine(sm: any): string {
  if (!sm) return ''
  const parts = sm.buckets.map((b: any) => `${b.label} ${sm.counts[b.key] ?? 0}`)
  return `完成：${sm.total} 台 — ${parts.join('・')}`
}
const runSumRows = computed<any[]>(() => {
  const sm = runSum.value
  if (!sm) return []
  return runSumOpen.value === '*' ? sm.rows : sm.rows.filter((r: any) => r.bucket === runSumOpen.value)
})
const { sortKey: rsSortKey, sortDir: rsSortDir, toggle: rsToggle, sorted: rsSorted } = useSort(runSumRows, 'no')
async function finishRun(runId?: number | null) {
  await loadRunSummary(runId)
  if (runSum.value) {
    const c = runSum.value.counts
    showToast(runSumLine(runSum.value), c.failed || c.needs_credential ? 'warn' : 'success', 12000)
  }
}

// ===== 納管後收集排隊：哪幾台、能取消（2026-10-20 使用者：「哪 77 台？能取消嗎？」）=====
const pqSel = ref<Set<string>>(new Set())
const pqItems = computed<any[]>(() => pq.value?.items ?? [])
const { sortKey: pqSortKey, sortDir: pqSortDir, toggle: pqToggle, sorted: pqSorted } = useSort(pqItems, 'no')
function pqToggleSel(sn: string) {
  const s = new Set(pqSel.value)
  if (s.has(sn)) s.delete(sn); else s.add(sn)
  pqSel.value = s
}
async function pqCancel(all: boolean) {
  const list = all ? null : [...pqSel.value]
  const n = all ? pqItems.value.length : list!.length
  if (!n) { showToast('先勾要取消的主機', 'warn'); return }
  if (!confirm(`取消 ${n} 台的納管後收集？\n\n・排隊中的直接移出，這次不收。\n・正在收的那台（不論是從哪個入口觸發的）：目前這一樣收完就停。\n・每一台都會在紀錄裡留「已取消」。`)) return
  try {
    pq.value = await apiFetch('/api/onboard/post-collect-queue/cancel', { method: 'POST', body: { asset_serials: list } })
    pqSel.value = new Set()
    showToast(`已取消 ${n} 台`, 'success')
    loadPostQueue()
    logRef.value?.reload()
  } catch (e: any) {
    showToast(`取消失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error', 10000)
  }
}
async function pqRequeueLost() {
  try {
    const r = await apiFetch<any>('/api/onboard/post-collect-queue/requeue-lost', { method: 'POST' })
    showToast(`已重新排入 ${r.added} 台`, 'success')
    loadPostQueue()
  } catch (e: any) {
    showToast(`重新排入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error', 10000)
  }
}

function applyRun(r: any) {
  const run = r?.run
  runInfo.value = run ?? null
  if (r && 'can_admin' in r) canAdmin.value = !!r.can_admin
  staleRun.value = run?.status === 'running' && run?.liveness === 'stale' ? run : null
}
async function stopDispatch() {
  const run = runInfo.value
  if (!run?.id || stopBusy.value) return
  if (!confirm(`停止這一輪收集？\n\n・目前那一台收完就停（最多等目前那台約 3 分鐘），剩下的台與批都不跑。\n・已經收到的結果都會留著。`)) return
  stopBusy.value = true
  try {
    await apiFetch(`/api/collect/dispatch/${run.id}/stop`, { method: 'POST' })
    runInfo.value = { ...run, stop_requested: true }
    showToast('已要求停止：目前那一台收完就停（最多約 3 分鐘）', 'info', 8000)
  } catch (e: any) {
    showToast(`停止失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error', 10000)
  } finally { stopBusy.value = false }
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
  } catch (e: any) {
    showToast(`標記失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error', 10000)
  }
}

// ===== 收集涵蓋率（2026-10-20 使用者：「我要怎麼知道哪些已經收集過、哪些完全沒收集過」）=====
// 後端 dispatch_coverage 算（同一份「每個位址最新一次結果」），前端只顯示
interface CovSeg { no: number; cidr: string; start: number; location: string | null; environment: string | null
  purpose: string | null; in_table: boolean; total: number; collected: number; alive: number
  last_at: string | null; never: boolean; pct?: number }
const cov = ref<{ segments: CovSeg[]; summary: any } | null>(null)
const covOpen = ref(false)
const covOnly = ref<'' | 'never' | 'partial' | 'full'>('')
const covErr = ref('')
async function loadCoverage() {
  try { cov.value = await apiFetch('/api/collect/dispatch/coverage'); covErr.value = '' }
  catch (e: any) { covErr.value = `涵蓋率讀不到：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(loadCoverage)
const covByCidr = computed<Record<string, CovSeg>>(() => {
  const m: Record<string, CovSeg> = {}
  for (const s of cov.value?.segments ?? []) m[s.cidr] = s
  return m
})
const covRows = computed<CovSeg[]>(() => (cov.value?.segments ?? [])
  .map((s) => ({ ...s, pct: s.total ? Math.round(s.collected * 1000 / s.total) / 10 : 0 }))
  .filter((s) => !covOnly.value || (covOnly.value === 'never' ? s.never
    : covOnly.value === 'full' ? s.collected >= s.total : (s.collected > 0 && s.collected < s.total))))
const { sortKey: cSortKey, sortDir: cSortDir, toggle: cToggle, sorted: cSorted } = useSort(covRows, 'no')
const covFilters = computed<[any, string, number][]>(() => {
  const m = cov.value?.summary ?? {}
  return [['', '全部', m.segments ?? 0], ['never', '從未收集', m.never ?? 0], ['partial', '部分', m.partial ?? 0], ['full', '全收', m.full ?? 0]]
})
const covSeg = ref<any>(null)
const covSegBusy = ref(false)
async function openCovSeg(cidr: string) {
  covSegBusy.value = true
  try { covSeg.value = await apiFetch('/api/collect/dispatch/coverage/segment', { query: { cidr } }) }
  catch (e: any) { showToast(`網段明細讀不到：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
  finally { covSegBusy.value = false }
}
const covItems = computed<any[]>(() => covSeg.value?.items ?? [])
const { sortKey: ciSortKey, sortDir: ciSortDir, toggle: ciToggle, sorted: ciSorted } = useSort(covItems, 'no')

// 納管失敗清單
const runtimeConfig = useRuntimeConfig()
const failures = ref<any[]>([])
const failSummary = ref<{ by_reason: Record<string, number>; by_os_class?: Record<string, number>; credential: number; excluded_elsewhere?: number }>(
  { by_reason: {}, credential: 0 })
const { sortKey: fSortKey, sortDir: fSortDir, toggle: fToggle, sorted: fSorted } = useSort(failures, 'no')
// 作業系統大類可複選、失敗原因單選；匯出只匯出選的作業系統（2026-10-22 使用者：「我可以自己選 OS 匯出」）
const fOs = ref<Set<string>>(new Set())
const fReason = ref('')
// 搜尋框（2026-10-22 使用者：「沒搜尋」）：欄位跟後端 onboard_failures.SEARCH_KEYS 同一組，匯出也帶 q
const fQ = ref('')
const fSearchKeys = ['ip', 'hostname', 'asset_serial', 'physical_location', 'department', 'contact',
  'custodian', 'os', 'os_class', 'reason', 'next_step']
function fMatch(f: any): boolean {
  const ts = fQ.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!ts.length) return true
  const hay = fSearchKeys.map((k) => String(f[k] ?? '')).join(' ').toLowerCase()
  return ts.every((t) => hay.includes(t))
}
function toggleFailOs(k: string) {
  const n = new Set(fOs.value)
  n.has(k) ? n.delete(k) : n.add(k)
  fOs.value = n
}
const fShown = computed(() => fSorted.value.filter((f: any) =>
  (!fOs.value.size || fOs.value.has(f.os_class)) && (!fReason.value || f.reason === fReason.value)
  && fMatch(f)))
const failExportUrl = computed(() => {
  const base = `${runtimeConfig.public.apiBase}/api/onboard/failures/export`
  const q: string[] = []
  if (fOs.value.size) q.push(`os_class=${encodeURIComponent([...fOs.value].join(','))}`)
  if (fReason.value) q.push(`reason=${encodeURIComponent(fReason.value)}`)
  if (fQ.value.trim()) q.push(`q=${encodeURIComponent(fQ.value.trim())}`)
  return q.length ? `${base}?${q.join('&')}` : base
})
// 納管失敗清單的「改分類」：沿用 openMove（同一支 /api/adopt/move-tab）。大類換成頁簽名，視窗才講得出「目前在哪」
const FAIL_CLASS_TAB: Record<string, string> = { Linux: 'ssh', Windows: 'winrm', AIX: 'mainframe' }
function failMoveRow(f: any): DispatchRow {
  return {
    ip: f.ip, alive: 1, open_ports: null, route: 'ssh', status: 'onboard_failed',
    asset_serial: f.asset_serial, hostname: f.hostname, registered: f.asset_serial ? 1 : 0,
    message: `納管失敗，目前作業系統大類：${f.os_class}`,
    tab: f.os_override?.tab || FAIL_CLASS_TAB[f.os_class] || '', no: f.no,
    os_override: f.os_override ? { ...f.os_override } : null,
  } as DispatchRow
}
async function loadFailures() {
  try {
    const r = await apiFetch<any>('/api/onboard/failures')
    // 項次照後端原順序編一次、固定不變：重新排序後同一台還是同一個號碼
    failures.value = (r.items ?? []).map((f: any, i: number) => ({ ...f, no: i + 1 }))
    failSummary.value = r.summary ?? { by_reason: {}, credential: 0 }
    fLoaded.value = true
    saveAdoptCache({ failures: failures.value, failSummary: failSummary.value })
  } catch (e: any) {
    // 讀不到：有上次資料就留著（畫面標著是幾點的），沒有就維持「…」，不顯示成 0
    showToast(`納管失敗清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
onMounted(loadFailures)

async function loadBatchInfo() {
  try { batchInfo.value = await apiFetch('/api/collect/dispatch/gap') } catch { /* 舊版後端沒有這支 */ }
}
onMounted(loadBatchInfo)

// 決策 WEEKLY-RANGE1（2026-10-20）：每週整片收集開著卻沒有範圍 → 這裡亮一小行紅字，不讓系統自己猜範圍
const weeklyNoRange = ref(false)
async function loadWeeklyRange() {
  try {
    const w = await apiFetch<any>('/api/collect/dispatch/weekly')
    weeklyNoRange.value = !!w?.range_missing
  } catch (e: any) {
    // 讀不到就不亮（不能因此擋住收集），但要講出來
    showToast(`每週整片收集狀態讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'warn')
  }
}
onMounted(loadWeeklyRange)

async function saveGap(minutes: number) {
  try {
    await apiFetch('/api/collect/dispatch/gap',
                   { method: 'PUT', body: { gap_seconds: Math.round(minutes * 60) } })
    await loadBatchInfo()
    showToast(`批次間隔已改成 ${minutes} 分鐘`, 'success')
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message}`, 'error')
  }
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function pollUntilDone() {
  // 進度用輕量那支（只有 run 那一列）；跑完才撈整份合併結果
  const pr = await apiFetch<any>('/api/collect/dispatch/progress')
  const prun = pr?.run
  if (!prun) return
  applyRun(pr)
  progress.value = {
    batch_done: prun.batch_done ?? 0, batch_total: prun.batch_total ?? 1,
    done_count: prun.done_count ?? 0, total: prun.target_count ?? 0,
  }
  if (prun.status === 'running' && prun.liveness !== 'stale') return
  const r = await apiFetch<any>('/api/collect/dispatch/latest')
  const run = r?.run ?? prun
  if (r?.results?.length) dispatch.value = r
  stopPolling()
  dispatching.value = false
  if (dTicker) { clearInterval(dTicker); dTicker = null }
  loadCoverage()
  if (run.status === 'running') {
    // 心跳逾時：不再講「收集中」，講「可能已中斷」（見 staleRun 那一條橫幅）
    showToast(`這一輪超過 ${run.stale_minutes} 分鐘沒有進度，可能已中斷`, 'warn', 12000)
  } else if (run.status === 'stopped' || run.status === 'interrupted') {
    showToast(`${run.error || '已停止'}；已收到的結果都留著`, 'warn', 12000)
  } else if (run.status === 'failed') {
    dispatchError.value = `收集沒有完成：${run.error || '後端沒有留下原因'}`
    showToast('收集失敗，看下方「開始收集紀錄」', 'error', 12000)
  }
  await finishRun(run.id)
  await load()
  logRef.value?.reload()
}

async function runDispatch() {
  if (dispatching.value) return
  dispatchError.value = ''
  dispatchNotice.value = ''
  // 空白絕不代表全部（2026-10-20 公司機：勾了網段沒按「加入範圍」→ 框空著 → 跑了全部登記 IP）
  if (!targets.value.trim()) {
    if (segSel.value.size) showToast(`勾了 ${segSel.value.size} 段還沒按「加入範圍」——先按「加入範圍」再開始收集`, 'warn', 10000)
    else showToast('請先輸入網段或 IP（空白不代表全部）', 'warn')
    return
  }
  if (segSel.value.size) {
    showToast(`還有 ${segSel.value.size} 段勾了沒加入範圍——要收就先按「加入範圍」，不收就取消勾選`, 'warn', 10000)
    return
  }
  if (targetCount.value > CONFIRM_OVER) {
    const bs = batchInfo.value?.batch_size ?? 1024
    const gapMin = (batchInfo.value?.gap_seconds ?? 120) / 60
    const batches = Math.ceil(targetCount.value / bs)
    const mins = Math.ceil(targetCount.value * 5 / 64 / 60 + (batches - 1) * gapMin)
    const scope = dRegOnly.value && targetSplit.value.ranges ? `最多 ${targetCount.value} 個位址（網段裡只收有登記的，實際會少很多）` : `${targetCount.value} 個位址`
    if (!confirm(`這次會收 ${scope}，分 ${batches} 批，探測約 ${mins} 分鐘起跳（連得進去的還要另外收）。確定？`)) return
  }
  dispatching.value = true
  dElapsed.value = 0
  dTicker = setInterval(() => { dElapsed.value += 1 }, 1000)
  showToast('開始收集：探測中，連得上的會直接進去收，可能要一段時間', 'info')
  runSum.value = null
  progress.value = null
  // 同步的小收集也逐台看得到進度（POST 還沒回來時每 2 秒問一次輕量進度）
  let syncTimer: ReturnType<typeof setInterval> | null = setInterval(async () => {
    try {
      const pr = await apiFetch<any>('/api/collect/dispatch/progress')
      const run = pr?.run
      if (run?.status === 'running') {
        applyRun(pr)
        progress.value = { batch_done: run.batch_done ?? 0, batch_total: run.batch_total ?? 1,
                           done_count: run.done_count ?? 0, total: run.target_count ?? 0 }
      }
    } catch { /* 進度問不到不影響收集本身；POST 回來就有結果 */ }
  }, 2000)
  const stopSync = () => { if (syncTimer) { clearInterval(syncTimer); syncTimer = null } }
  try {
    const out = await apiFetch<any>('/api/collect/dispatch', {
      method: 'POST', body: { targets: targets.value, full: dFull.value, registered_only: dRegOnly.value },
    })
    stopSync()
    if (out?.started) {
      staleRun.value = null
      // 背景跑：畫面改成顯示進度，每 5 秒問一次後端跑到哪。這一頁可以離開，回來還看得到。
      progress.value = { batch_done: 0, batch_total: out.batch_total, done_count: 0, total: out.total }
      showToast(`${out.total} 台已自動分成 ${out.batch_total} 批（每批最多 ${out.batch_size} 台、`
        + `間隔 ${Math.round(out.gap_seconds / 60)} 分鐘），背景執行中`, 'info', 14000)
      pollTimer = setInterval(() => { pollUntilDone().catch(() => {}) }, 5000)
      return           // 還在跑，不要收掉 dispatching
    }
    dispatch.value = out
    loadCoverage()
    // 數字講「這一輪」的（out.this_run），表格是合併檢視（各位址最新一次的結果）
    const d = out.this_run ?? out
    dispatching.value = false
    if (dTicker) { clearInterval(dTicker); dTicker = null }
    applyRun(out)
    await finishRun(d.run_id)
    await load()   // 有的機器狀態變了，候選清單要跟著更新
    logRef.value?.reload()
  } catch (e: any) {
    stopSync()
    const det = e?.data?.detail
    dispatching.value = false
    if (dTicker) { clearInterval(dTicker); dTicker = null }
    if (det && typeof det === 'object' && det.code === 'no_registered_in_range') {
      // 篩選把範圍篩空了——不是失敗，講原因＋給一鍵改法
      dispatchNotice.value = det.message
      return
    }
    const why = (typeof det === 'string' ? det : det?.message) || e?.message || '沒有回應'
    dispatchError.value = `收集沒有完成（跑了 ${dElapsed.value} 秒）：${why}`
    showToast(`收集失敗：${why}`, 'error')
    dispatching.value = false
    if (dTicker) { clearInterval(dTicker); dTicker = null }
    logRef.value?.reload()
  }
}
onBeforeUnmount(() => { if (dTicker) clearInterval(dTicker); stopPolling() })

// 重新整理回來時，如果後端還在跑，接著輪詢（不然使用者會以為斷了）
onMounted(async () => {
  try {
    const r = await apiFetch<any>('/api/collect/dispatch/latest')
    applyRun(r)
    if (r?.run?.status === 'running' && r.run.liveness !== 'stale') {
      dispatching.value = true
      progress.value = { batch_done: r.run.batch_done ?? 0, batch_total: r.run.batch_total ?? 1,
                         done_count: r.run.done_count ?? 0, total: r.run.target_count ?? 0 }
      pollTimer = setInterval(() => { pollUntilDone().catch(() => {}) }, 5000)
    }
  } catch { /* 沒跑過就算了 */ }
})

// 要請人裝 agent 的那批：產安裝包給他帶過去
const pkg = ref<any>(null)
const pkgBusy = ref(false)
const pkgCopied = ref(false)
async function getPackage(ip: string) {
  if (pkgBusy.value) return
  pkgBusy.value = true
  try {
    pkg.value = await apiFetch<any>('/api/collect/agent-package', { method: 'POST', body: { ip } })
  } catch (e: any) {
    showToast(`取得安裝包失敗：${e?.data?.detail || '請稍後重試'}`, 'error')
  } finally {
    pkgBusy.value = false
  }
}
async function copyPkgScript() {
  try {
    const ok = await copy(pkg.value?.files?.['bootstrap_watcher.sh'] ?? '')
  if (!ok) showToast('複製失敗，請直接選取下方腳本內容複製', 'warn', 8000)
    pkgCopied.value = true
    setTimeout(() => (pkgCopied.value = false), 2000)
  } catch { showToast('複製失敗，請手動選取', 'warn') }
}

async function load() {
  ;[meta.value, hosts.value] = await Promise.all([
    apiFetch<Meta>('/api/field-meta'),
    apiFetch<ScanHost[]>('/api/scan/unregistered'),
  ])
}
// 三支同時讀，不排隊（2026-10-22 使用者選「A＋C」）：總等待＝最慢那一支，不是三支加起來
await Promise.all([
  load(),
  // 上一次的結果還原回來——這動作要等數十秒，做完一重新整理就沒了等於白等
  apiFetch<DispatchOut>('/api/collect/dispatch/latest').then((v) => { dispatch.value = v })
    .catch(() => { /* 沒跑過就空著 */ }),
  loadAudit(),
])
// 標籤：收集結果或已納管清單換了就整批重查（v1.468.6）
watch([() => dispatch.value?.results, managed, managedStale], () => { loadTags() }, { immediate: true })
function onTagged() { loadTags() }
if (dispatch.value && !dispatch.value.results.length) dispatch.value = null

function fieldsOf(catKey: string) {
  if (!meta.value) return []
  return Object.entries(meta.value.fields)
    .filter(([, f]) => f.category === catKey)
    .map(([key, f]) => ({ key, ...f }))
}

function openAdopt(host: ScanHost) {
  selected.value = host
  Object.keys(form).forEach((k) => delete form[k])
  // 技術層：從掃描結果自動帶入現有的（其餘 OS/序號/MAC 待 facts 收集，先空著可手補）
  form.ip = host.ip
  form.hostname = host.hostname ?? ''
  form.subnet = host.segment ?? ''
  if (host.mac) form.mac = host.mac
  if (host.is_vm) form.is_vm = 'VM'
}

async function submitAdopt() {
  submitAttempted.value = true
  if (!form.asset_purpose || !form.environment) {
    showToast('請填紅框的必填欄位：資產用途、環境別', 'warn')
    return
  }
  saving.value = true
  try {
    await apiFetch('/api/assets/adopt', { method: 'POST', body: { fields: { ...form } } })
    showToast(`已納入管理：${selected.value?.ip}`, 'success')
    hosts.value = hosts.value.filter((h) => h.ip !== selected.value?.ip)
    selected.value = null
  } catch (err: any) {
    showToast(`納入失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> 資產盤點 → <b>納入管理</b></div>
    <h1 class="page-title">納入管理</h1>
    <p class="page-sub">把機器帶進系統。先「開始收集」讓系統自己找路進去，再把還沒登記的補成資產。</p>
    <!-- 頁簽（2026-10-18）：這頁原本一長條，拆三頁。#collect／#pending／#onboarded 可直接連進來 -->
    <div v-if="!selected" class="tabs" role="tablist">
      <button type="button" role="tab" class="tab" :class="{ active: activeTab === 'collect' }"
              :aria-selected="activeTab === 'collect'" @click="setTab('collect')">
        開始收集<span v-if="collectBusyText" class="tab-busy" :class="{ stale: staleRun && !dispatching }"
                       data-test="collect-busy" title="點一下看進度" @click.stop="goBusy">　{{ collectBusyText }}</span>
      </button>
      <button type="button" role="tab" class="tab" :class="{ active: activeTab === 'pending' }"
              :aria-selected="activeTab === 'pending'" @click="setTab('pending')">
        待納管
      </button>
      <button type="button" role="tab" class="tab" :class="{ active: activeTab === 'unregistered' }"
              :aria-selected="activeTab === 'unregistered'" @click="setTab('unregistered')"
              title="掃到了、CIA 沒登記的主機">
        未登記主機 {{ hosts.length }}
      </button>
      <button type="button" role="tab" class="tab" :class="{ active: activeTab === 'failures' }"
              :aria-selected="activeTab === 'failures'" @click="setTab('failures')"
              title="每台最後一次納管失敗、現在仍然進不去的；可匯出 Excel 給同事">
        納管失敗 {{ fLoaded || cacheAt ? failures.length : '…' }}
      </button>
      <button type="button" role="tab" class="tab" :class="{ active: activeTab === 'onboarded' }"
              :aria-selected="activeTab === 'onboarded'" @click="setTab('onboarded')"
              title="台數跟首頁「已納管」同一個數（收集帳號 7 天內連得上）">
        已納管主機 {{ mLoaded || cacheAt ? managed.length : '…' }}<span v-if="managedStale.length" class="tab-sub">（＋失聯 {{ managedStale.length }}）</span>
      </button>
      <span v-if="cacheAt" class="muted small" data-test="adopt-cache-note">上次資料（{{ cacheAtText }}），更新中…</span>
      <InfoNote><b>開始收集</b>：挑範圍、按開始收集，看收集紀錄與納管紀錄。<br><b>待納管</b>：最近一次收集的結果——一鍵納管、批次試密碼、改分類都在這。<br><b>未登記主機</b>：掃到了、CIA 沒登記的主機，可一鍵納管或手動登記。<br><b>納管失敗</b>：現在仍然進不去的主機與原因、窗口，可匯出 Excel 給同事。<br><b>已納管主機</b>：台數跟首頁「已納管」同一個數；重新納管／取消納管／編輯。</InfoNote>
    </div>

    <div v-show="activeTab === 'collect'">
    <!-- ===== ①收集入口：一個動作，系統自己選路 ===== -->
    <section v-if="!selected" class="card">
      <div class="sec-head"><span class="t">開始收集 <InfoNote>輸入要納進來的範圍，按一下就好——<b>不用自己判斷該走哪一條</b>。系統會逐台探測，能走 SSH 的走 SSH、只有 Windows 管道的走 WinRM、進不去但活著的先判斷是什麼機器（Linux／Unix 產 Push Agent 安裝包、Windows 請開 WinRM、AIX 請開 SSH）、完全沒回應的列進「只能靠匯入」。<br><br>已經有資料在 Excel／RVTools 裡的走「資料匯入」，那是既有資料進場，不是收集。</InfoNote></span></div>
      <div v-if="weeklyNoRange" class="weekly-norange" data-test="weekly-range-missing">
        每週整片收集還沒有範圍：先手動跑一次整片網段，或在<NuxtLink to="/settings?tab=schedule">系統設定填範圍</NuxtLink>
      </div>
      <!-- 從網段配置表挑：先環境、再機房、最後勾網段（2026-09-15 使用者要的順序） -->
      <div v-if="segTree.length" class="segpick">
        <div class="sp-head">
          <b>從網段配置表挑</b>
          <button class="lnk" type="button" @click="segOpen = !segOpen">{{ segOpen ? '收合' : '展開' }}</button>
          <InfoNote>網段配置表裡有機房、環境、用途，所以這裡可以先選「測試」再選機房，看有哪些網段可以收——不用自己背哪一段是哪裡。勾好按「加入範圍」會填進下面的輸入框，你還可以自己增減，確認台數後再按「開始收集」。<br><br>資安註記<b>排除掃描</b>的網段照列出來但不能勾，並寫明原因——不是偷偷不見。</InfoNote>
        </div>
        <!-- 版面收成一行（2026-09-16 使用者：「這個頁面字太多了。像這個五行其實可以縮為一行就好，
             用橫的、不要用直的，這樣可以大幅縮減這個畫面」）。
             環境／機房／排除開關／全選／加入範圍原本各佔一行，現在同一列；
             網段清單改多欄，一段一格而不是一段一行。 -->
        <div v-if="segOpen" class="sp-body">
          <div class="sp-bar">
            <span class="sp-lbl">環境</span>
            <button v-for="e in segEnvs" :key="e" type="button" class="chip"
                    :class="{ on: segEnv === e }" @click="segEnv = e">{{ e }}</button>

            <span class="sp-lbl">機房</span>
            <select v-model="segLoc" class="sp-sel">
              <option v-for="l in segLocs" :key="l" :value="l">{{ l }}</option>
            </select>
            <span v-if="!segLocs.length" class="dim small">這個環境沒有登記的網段</span>

            <label class="sp-ex" :title="'網段表的註記是「建議」排除掃描（UAT／員工電腦／重複設備 IP），不等於不能做資產收集。打開後每一段都會寫出被註記的原因。'">
              <input v-model="segShowExcluded" type="checkbox" />
              含建議排除（{{ segExcludedCount }}）
            </label>

            <label v-if="segVisible.length" class="sp-ex">
              <input type="checkbox" :disabled="!segPickable.length"
                     :checked="segPickable.length > 0 && segPickable.every((s) => segSel.has(s.cidr))"
                     @change="toggleAllSegs" />
              全選（{{ segPickable.length }}）
            </label>

            <span class="spacer" />
            <button class="btn small primary" type="button" :disabled="!segSel.size" @click="addSelectedSegs">
              加入範圍（{{ segSel.size }} 段）
            </button>
          </div>

          <div v-if="segVisible.length" class="sp-grid">
            <label v-for="s in segVisible" :key="s.cidr" class="sp-cell" :class="{ ex: s.scan_excluded }"
                   :title="s.scan_excluded ? `網段表註記：${s.scan_note || '建議排除掃描'}` : (s.label || '')">
              <input type="checkbox" :checked="segSel.has(s.cidr)" @change="toggleSeg(s.cidr)" />
              <span class="mono">{{ s.cidr }}</span>
              <span class="dim">{{ s.label.replace(s.cidr, '').trim() }}</span>
              <span v-if="covByCidr[s.cidr]" class="covbadge" :class="{ never: covByCidr[s.cidr].never }">{{
                covByCidr[s.cidr].never ? '從未收集' : `上次收集 ${mmdd(covByCidr[s.cidr].last_at)}` }}</span>
              <span v-if="s.scan_excluded" class="exdot" title="網段表建議排除掃描">建議排除</span>
            </label>
          </div>
        </div>
      </div>
      <textarea v-model="targets" class="targets" :disabled="dispatching"
                placeholder="每行一項，可混用：&#10;10.99.1.0/24&#10;10.99.2.10-20&#10;10.99.3.5" />
      <div class="actions">
        <button class="btn" :disabled="dispatching || !targets.trim()" data-test="dispatch-start" @click="runDispatch">
          {{ dispatching ? busyCore : '開始收集' }}
        </button>
        <button v-if="dispatching && canAdmin && runInfo?.id && runInfo?.status === 'running'" class="btn small"
                type="button" data-test="dispatch-stop" :disabled="stopBusy || runInfo?.stop_requested" @click="stopDispatch">
          {{ runInfo?.stop_requested ? '停止中…' : '停止收集' }}
        </button>
        <!-- 批次間隔可設定（2026-09-16 使用者：「預設每個網段間距2分鐘」「可以設定改幾分鐘」）。
             間隔是刻意的：一次對上千台送封包對網路不客氣，也容易被 SOC 當成掃描行為。 -->
        <!-- 只收新的／全部重收（v1.468.4，2026-10-19 使用者：「收集一次要很久」）。
             預設只收新的：上次已納管或空位址、在時間窗內的位址不重探（合併檢視照樣看得到上次結果） -->
        <label v-if="!dispatching" class="ahint" style="cursor:pointer">
          <input v-model="dRegOnly" type="checkbox" /> 只掃已登記的 IP<template v-if="batchInfo?.registered_count != null">（{{ batchInfo.registered_count }} 個）</template>
          <InfoNote>勾了＝網段／範圍裡只探資產庫裡<b>現役資產</b>的 IP（退役不算）。<b>單一 IP 不受「只掃已登記」限制</b>——直接打的 IP 一律照收（例如還沒登記的測試機）。框是空的不會開跑（空白不代表全部）。<br><br>整片網段多數位址是空的，白天要看登記的機器收不收得到用這個最快；要發現網段裡<b>沒登記的新機器</b>，不要勾，照常輸入網段（至少一個 /24 的整片範圍會記成每週六晚上自動跑的範圍，見 6 系統設定 → 掃描排程）。</InfoNote>
        </label>
        <label v-if="!dispatching" class="ahint" style="cursor:pointer">
          <input v-model="dFull" type="checkbox" /> 全部重收
          <InfoNote>不勾＝<b>只收新的</b>：上次已納管、而且在 <b>{{ batchInfo?.skip_fresh_hours ?? 24 }}</b> 小時內的位址，以及空位址（沒回應也沒登記）在 <b>{{ batchInfo?.skip_empty_hours ?? 168 }}</b> 小時內、之後存活掃描也沒看到活著的，這次不重探——下方表格照樣顯示它們上次的結果與時間。其餘（待佈身分、納管失敗、進不去、已登記卻沒回應…）一律重探。只輸入一個位址時一定重探。<br><br>勾了＝每個位址都重探一次。時間窗、一批幾台、批次間隔、幾天算舊，都在 6 系統設定 → 掃描排程。</InfoNote>
        </label>
        <span v-if="batchInfo && !dispatching" class="ahint">
          超過 {{ batchInfo.batch_size }} 台自動分批，每批間隔
          <input type="number" min="0" max="60" step="0.5" class="gapin"
                 :value="batchInfo.gap_seconds / 60"
                 @change="saveGap(Number(($event.target as HTMLInputElement).value))" /> 分鐘
        </span>
        <span v-if="!dispatching" class="ahint">
          <template v-if="targetCount">目前輸入約 <b>{{ targetCount }}</b> 台<template v-if="dRegOnly && targetSplit.ranges">（網段 {{ targetSplit.ranges }} 個位址只掃有登記的<template v-if="targetSplit.singles">＋單一 IP {{ targetSplit.singles }} 個照收</template>）</template>　</template>
          <InfoNote>一批最多 {{ batchInfo?.batch_size ?? 1024 }} 台，超過自動分批；整個網段的例行掃描請走掃描排程。</InfoNote>
        </span>
      </div>
      <!-- 失敗留在畫面上，講得出原因＋下一步 -->
      <div v-if="dispatchError && !dispatching" class="d-err">
        <b>✕ {{ dispatchError }}</b>
        <div class="small">常見原因：範圍太大、網頁等太久被中斷（後端可能還在跑，跑完會存下來）；或後端出錯。先按「載入上次結果」看有沒有存到；還是不清楚，打開下方「開始收集紀錄」看細節或下載分析檔。</div>
        <button class="btn small" type="button" @click="reloadLatest">載入上次結果</button>
      </div>
      <!-- 納管後自動收集的結果也要在「納管的地方」看得到（2026-09-17 使用者：
           「我認為我納管後，所有的資料都要跟著變動」——資料有沒有真的跟著變，
           得看得到才算數）。 -->
      <div v-if="runScope" class="ahint run-scope" data-test="run-scope">{{ runScope }}</div>
      <!-- 收完的結果卡：留到下一次收集 -->
      <div v-if="runSum && !dispatching" class="runsum" data-test="run-summary">
        <b>完成：{{ runSum.total }} 台</b> —
        <template v-for="(b, i) in runSum.buckets" :key="b.key">
          <span v-if="i">・</span>
          <a class="dl" href="#" :class="{ on: runSumOpen === b.key, bad: b.key === 'failed' && runSum.counts.failed }"
             @click.prevent="runSumOpen = runSumOpen === b.key ? '' : b.key">{{ b.label }} {{ runSum.counts[b.key] }}</a>
        </template>
        <a v-if="runSum.total > 5" class="dl small" href="#" @click.prevent="runSumOpen = runSumOpen === '*' ? '' : '*'">　全部列出</a>
        <table v-if="runSumOpen && runSumRows.length" class="tbl runsum-tbl">
          <thead><tr>
            <SortTh k="no" :active="rsSortKey" :dir="rsSortDir" @sort="rsToggle">#</SortTh>
            <SortTh k="ip" :active="rsSortKey" :dir="rsSortDir" @sort="rsToggle">IP</SortTh>
            <SortTh k="hostname" :active="rsSortKey" :dir="rsSortDir" @sort="rsToggle">主機名稱</SortTh>
            <SortTh k="status_label" :active="rsSortKey" :dir="rsSortDir" @sort="rsToggle">結果</SortTh>
            <SortTh k="message" :active="rsSortKey" :dir="rsSortDir" @sort="rsToggle">原因</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in rsSorted" :key="r.ip">
              <td>{{ r.no }}</td><td class="mono">{{ r.ip }}</td><td>{{ r.hostname || '' }}</td>
              <td>{{ r.status_label }}</td><td class="small">{{ (r.message || '').slice(0, 160) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <!-- 納管後收集排隊：哪幾台、取消 -->
      <div v-if="pq && (pqItems.length || pq.lost?.length) && (pqPanelOpen || pq.lost?.length)" class="pqpanel" data-test="pq-panel">
        <div v-if="pq.lost?.length" class="d-err">
          <b>服務重啟：{{ pq.lost.length }} 台排隊被清掉，沒有收到</b>
          <button class="btn small" type="button" data-test="pq-requeue" @click="pqRequeueLost">重新排入</button>
        </div>
        <template v-if="pqPanelOpen && pqItems.length">
          <div class="pq-head">
            <b>納管後收集：{{ pqItems.length }} 台（正在收 {{ pqItems.filter((i) => i.state === 'running').length }}、排隊 {{ pqItems.filter((i) => i.state === 'queued').length }}）</b>
            <template v-if="pq.can_admin">
              <button class="btn small" type="button" :disabled="!pqSel.size" data-test="pq-cancel-sel" @click="pqCancel(false)">取消勾選的（{{ pqSel.size }}）</button>
              <button class="btn small" type="button" data-test="pq-cancel-all" @click="pqCancel(true)">全部取消排隊</button>
            </template>
            <button class="btn small" type="button" @click="pqPanelOpen = false">收合</button>
          </div>
          <table class="tbl runsum-tbl">
            <thead><tr>
              <th></th>
              <SortTh k="no" :active="pqSortKey" :dir="pqSortDir" @sort="pqToggle">#</SortTh>
              <SortTh k="ip" :active="pqSortKey" :dir="pqSortDir" @sort="pqToggle">IP</SortTh>
              <SortTh k="hostname" :active="pqSortKey" :dir="pqSortDir" @sort="pqToggle">主機名稱</SortTh>
              <SortTh k="source_label" :active="pqSortKey" :dir="pqSortDir" @sort="pqToggle">來源</SortTh>
              <SortTh k="state" :active="pqSortKey" :dir="pqSortDir" @sort="pqToggle">狀態</SortTh>
              <SortTh k="since" :active="pqSortKey" :dir="pqSortDir" @sort="pqToggle">排入時間</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="it in pqSorted" :key="it.asset_serial">
                <td><input type="checkbox" :checked="pqSel.has(it.asset_serial)" @change="pqToggleSel(it.asset_serial)" /></td>
                <td>{{ it.no }}</td><td class="mono">{{ it.ip || '' }}</td><td>{{ it.hostname || it.asset_serial }}</td>
                <td>{{ it.source_label }}</td>
                <td>{{ it.state === 'running' ? `正在收${it.job_label ? '：' + it.job_label : ''}${it.cancel_requested ? '（取消中）' : ''}` : '排隊中' }}</td>
                <td>{{ it.since || '' }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </div>
      <div v-if="dispatchNotice && !dispatching" class="d-info" data-test="dispatch-notice">
        <b>ⓘ 沒有開跑：{{ dispatchNotice }}</b>
        <button class="btn small" type="button" data-test="dispatch-unfilter" @click="dRegOnly = false; runDispatch()">取消「只掃已登記」再收一次</button>
      </div>
      <div v-if="staleRun && !dispatching" class="d-err" data-test="dispatch-stale">
        <b>⚠ 可能已中斷：最後進度 {{ hhmm(staleRun.last_progress_at) }}，第 {{ staleRun.batch_done }}/{{ staleRun.batch_total }} 批</b>
        <InfoNote>這一輪（{{ staleRun.started_at }} 開始）超過 {{ staleRun.stale_minutes }} 分鐘沒有任何進度。常見原因是服務重啟（例如裝 patch）把背景收集停掉了。已收到的結果都留著；確認不會再動就標記為已中斷，再重新開始收集。幾分鐘算逾時在 6 系統設定 → 掃描排程。</InfoNote>
        <button v-if="canAdmin" class="btn small" type="button" data-test="dispatch-mark-interrupted" @click="markInterrupted">標記為已中斷</button>
      </div>
      <CollectLog ref="logRef" :kinds="['dispatch', 'post_onboard', 'post_onboard_queue']" title="開始收集／納管後自動收集紀錄" />
      <!-- 收集涵蓋率：哪些網段收過、哪些從未收集（2026-10-20） -->
      <div class="cov" data-test="coverage">
        <button type="button" class="cov-head" @click="covOpen = !covOpen">
          {{ covOpen ? '▾' : '▸' }} 全部已知網段的收集涵蓋率（不是這次的範圍）
          <template v-if="cov?.summary">：{{ cov.summary.segments }} 個網段，
            <span class="cov-never-txt">從未收集 {{ cov.summary.never }}</span>、部分 {{ cov.summary.partial }}、全收 {{ cov.summary.full }}</template>
        </button>
        <InfoNote>每個位址看它<b>最新一次</b>的收集結果（跟待納管表同一份）。「已收集」＝任何一輪探測過這個位址（沒回應的空位址也算收過）；「從未收集」＝沒有任何一輪探測過。網段來自網段配置表，另加收過但配置表沒有的 /24。</InfoNote>
        <div v-if="covErr" class="d-err"><b>{{ covErr }}</b></div>
        <div v-if="covOpen && cov" class="cov-body">
          <div class="cov-filter">
            <button v-for="f in covFilters"
                    :key="f[0]" type="button" class="osbtn" :class="{ on: covOnly === f[0] }"
                    @click="covOnly = f[0]">{{ f[1] }} {{ f[2] }}</button>
          </div>
          <table class="tbl cov-tbl">
            <thead><tr>
              <SortTh k="no" :active="cSortKey" :dir="cSortDir" @sort="cToggle">#</SortTh>
              <SortTh k="start" :active="cSortKey" :dir="cSortDir" @sort="cToggle">網段</SortTh>
              <SortTh k="location" :active="cSortKey" :dir="cSortDir" @sort="cToggle">機房</SortTh>
              <SortTh k="environment" :active="cSortKey" :dir="cSortDir" @sort="cToggle">環境</SortTh>
              <SortTh k="collected" :active="cSortKey" :dir="cSortDir" @sort="cToggle">已收集／總位址</SortTh>
              <SortTh k="pct" :active="cSortKey" :dir="cSortDir" @sort="cToggle">%</SortTh>
              <SortTh k="alive" :active="cSortKey" :dir="cSortDir" @sort="cToggle">活著</SortTh>
              <SortTh k="last_at" :active="cSortKey" :dir="cSortDir" @sort="cToggle">最近收集</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="s in cSorted" :key="s.cidr" :class="{ 'cov-never': s.never }">
                <td>{{ s.no }}</td>
                <td><a class="dl mono" href="#" @click.prevent="openCovSeg(s.cidr)">{{ s.cidr }}</a>
                  <span v-if="!s.in_table" class="muted small">（配置表沒有）</span></td>
                <td>{{ s.location || '—' }}</td>
                <td>{{ s.environment || '—' }}</td>
                <td><a class="dl" href="#" @click.prevent="openCovSeg(s.cidr)">{{ s.collected }}／{{ s.total }}</a></td>
                <td>{{ s.pct }}</td>
                <td>{{ s.alive }}</td>
                <td>{{ s.never ? '從未收集' : s.last_at }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="covSeg" class="cov-detail" data-test="coverage-detail">
            <b class="mono">{{ covSeg.cidr }}</b>　已收集 {{ covSeg.collected }}／{{ covSeg.total }}
            <span v-if="!covSeg.listed_all" class="muted small">（網段太大，只列收集過的位址）</span>
            <button type="button" class="btn small" @click="covSeg = null">關閉</button>
            <table class="tbl cov-tbl">
              <thead><tr>
                <SortTh k="no" :active="ciSortKey" :dir="ciSortDir" @sort="ciToggle">#</SortTh>
                <SortTh k="ip" :active="ciSortKey" :dir="ciSortDir" @sort="ciToggle">IP</SortTh>
                <SortTh k="hostname" :active="ciSortKey" :dir="ciSortDir" @sort="ciToggle">主機名稱</SortTh>
                <SortTh k="status" :active="ciSortKey" :dir="ciSortDir" @sort="ciToggle">上次結果</SortTh>
                <SortTh k="collected_at" :active="ciSortKey" :dir="ciSortDir" @sort="ciToggle">上次收集</SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="it in ciSorted" :key="it.ip" :class="{ 'cov-never': it.never }">
                  <td>{{ it.no }}</td>
                  <td class="mono">{{ it.ip }}</td>
                  <td>{{ it.hostname || '' }}</td>
                  <td>{{ it.never ? '' : (STATUS_LABEL[it.status] || it.status) }}</td>
                  <td>{{ it.never ? '從未收集' : it.collected_at }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else-if="covSegBusy" class="muted small">載入中…</div>
        </div>
      </div>
      <!-- 執行中：講得出「在做什麼、幾台、大概多久」，不做假的百分比 -->
      <div v-if="dispatching" class="working" data-test="dispatch-progress">
        <span class="dots"><i /><i /><i /></span>
        <div class="w-txt">
          正在逐台探測（{{ targetCount || '?' }} 台），能進去的會直接連進去收。
          <br>沒回應的每台約 5 秒（要等它逾時），所以全部沒回應時最久約
          <b>{{ Math.ceil((targetCount || 1) * 5 / 64) }} 秒</b>——同時跑 64 台，不是一台一台等。
          <br>做完會存下來，<b>中途關掉頁面也不會白跑</b>，回來還看得到結果。
        </div>
      </div>
    </section>

    <!-- ===== 納管紀錄：失敗了要看得到為什麼，不用 SSH 進去翻 ===== -->
    <section v-if="!selected && audits.length" class="card">
      <div class="sec-head">
        <span class="t">納管紀錄 <InfoNote>這裡只留帳號名與成敗，<b>永遠不含密碼</b>。要更底層的伺服器日誌：<code>journalctl -u webit3-api -n 200</code>。</InfoNote></span>
        <button class="link-btn" type="button" @click="showAudit = !showAudit">
          {{ showAudit ? '收起' : `看全部（${audits.length} 筆）` }}
        </button>
      </div>

      <!-- 最近一次失敗直接攤開，不用先展開再找 -->
      <div v-if="lastFail && !showAudit" class="lastfail">
        <div class="lf-head">
          最近一次失敗 · {{ lastFail.created_at }} · <span class="mono">{{ lastFail.target_ip }}</span>
          <span class="pill bad">{{ lastFail.stage || '未知階段' }}</span>
          <InfoNote>階段的意思：<b>connect</b> ＝ 還沒進到目標機（帳密錯／連不上／缺收集金鑰）；<b>execute</b> ＝ 進去了但腳本沒跑完（多半是權限或 sudo）。</InfoNote>
        </div>
        <p class="lf-msg">{{ lastFail.message }}</p>
        <pre v-if="lastFail.output" class="outbox">{{ lastFail.output }}</pre>
      </div>

      <div v-if="showAudit" class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="created_at" :active="aSortKey" :dir="aSortDir" @sort="aToggle">時間</SortTh>
            <SortTh k="target_ip" :active="aSortKey" :dir="aSortDir" @sort="aToggle">目標</SortTh>
            <SortTh k="platform" :active="aSortKey" :dir="aSortDir" @sort="aToggle">平台</SortTh>
            <SortTh k="login_user" :active="aSortKey" :dir="aSortDir" @sort="aToggle">登入帳號</SortTh>
            <SortTh k="trigger" :active="aSortKey" :dir="aSortDir" @sort="aToggle">觸發</SortTh>
            <SortTh k="ok" :active="aSortKey" :dir="aSortDir" @sort="aToggle">結果</SortTh>
            <SortTh k="stage" :active="aSortKey" :dir="aSortDir" @sort="aToggle">階段</SortTh>
            <SortTh k="message" :active="aSortKey" :dir="aSortDir" @sort="aToggle">訊息</SortTh>
            <th>輸出</th>
          </tr></thead>
          <tbody>
            <template v-for="a in aSorted" :key="a.id">
              <tr>
                <td class="small">{{ a.created_at }}</td>
                <td class="mono">{{ a.target_ip }}</td>
                <td>{{ a.platform || '—' }}</td>
                <td>{{ a.login_user || '—' }}</td>
                <td class="small">{{ a.trigger === 'auto' ? '排程' : (a.triggered_by || '手動') }}</td>
                <td><span class="pill" :class="a.ok ? 'ok' : 'bad'">{{ a.ok ? '成功' : '失敗' }}</span></td>
                <td class="small">{{ a.stage || '—' }}</td>
                <td class="msg">{{ a.message }}</td>
                <td>
                  <button v-if="a.output" class="link-btn" type="button"
                          @click="openOutput = openOutput === a.id ? null : a.id">
                    {{ openOutput === a.id ? '收起' : '看輸出' }}
                  </button>
                  <span v-else class="muted small">—</span>
                </td>
              </tr>
              <tr v-if="openOutput === a.id">
                <td colspan="9"><pre class="outbox">{{ a.output }}</pre></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>

    </div>

    <div v-show="activeTab === 'pending'">
    <!-- 結果：一張表分得出「收到了」跟「要人工處理」 -->
    <section v-if="dispatch && dispatch.results.length && !selected" class="card">
      <div class="sec-head">
        <span class="t">收集結果</span>
        <span v-if="dispatch.merged && dispatch.merged.mode === 'merged'" class="muted small">
          合併 {{ dispatch.merged.run_count }} 次收集、最早 {{ dispatch.merged.earliest || '—' }}、最新 {{ dispatch.merged.latest || '—' }}｜共 {{ dispatch.total }} 個位址
          <a v-if="dispatch.merged.stale_count" class="dl" href="#" :title="`最新一次結果超過 ${dispatch.merged.stale_days} 天的位址（點一下只看這些）`"
             @click.prevent="pickResult('stale')">｜舊 {{ dispatch.merged.stale_count }}</a>
          <InfoNote>每個位址顯示它<b>最新一次</b>的收集結果，跨所有收集——收一台不會把其他位址的結果藏掉。每列「上次收集」是那個位址自己的時間；超過 <b>{{ dispatch.merged.stale_days }}</b> 天的標「舊」（天數在 6 系統設定 → 掃描排程）。<br><br>共 <b>{{ dispatch.total }}</b> 個位址，其中 <b>{{ emptyAddrCount }}</b> 個是空位址（沒回應、資產庫也沒登記），不列進下面任何頁簽；表格只列真的有機器的 <b>{{ dispatchRows.length }}</b> 台。</InfoNote>
        </span>
        <span v-else-if="dispatch.run" class="muted small">
          {{ dispatch.run.finished_at || dispatch.run.started_at }}｜共 {{ dispatch.run.target_count }} 個位址
          <InfoNote>這次探測了 <b>{{ dispatch.total }}</b> 個位址，其中 <b>{{ emptyAddrCount }}</b> 個是空位址（沒回應、資產庫也沒登記——那裡沒有機器，不用處理），不列進下面任何頁簽；表格只列真的有機器的 <b>{{ dispatchRows.length }}</b> 台。</InfoNote>
        </span>
      </div>
      <!-- 使用者 2026-09-16：「為什麼是 508，還要人工處理?」——他貼的是兩個 /24，
           508 是**位址數**不是機器數，而且全部沒回應卻被算成「要人工處理」。
           掃一個網段本來就大部分是空的，所以空位址要單獨一格、不算待辦。 -->
      <!-- 每一張卡都點得下去＝下鑽（鐵規則：數字不能只有數字，要看得到是哪幾筆）。
           點了就切到「全部」頁簽再套狀態篩選，所以**卡片數字永遠等於表格列數**；
           被選中的卡有明顯選取框，表格上方另有一條寫明現在在看什麼＋一鍵清除。 -->
      <div class="tiles">
        <div class="tile ok clk" role="button" tabindex="0" :class="{ sel: dStatus === 'collected' }"
             :aria-pressed="dStatus === 'collected'" title="點一下只看這一類；再點一下取消"
             @click="pickStatus('collected')" @keydown.enter.prevent="pickStatus('collected')"
             @keydown.space.prevent="pickStatus('collected')">
          <div class="t-num mono">{{ dStatusCount.collected }}</div>
          <!-- 這張卡是「這一批探測」連上幾台；全站「已納管」（首頁同一個數字，EXIST1：收集帳號
               7 天內連得上、CIA 無關）在下方「已納管主機」表。CIA 有沒有登記另外拆，不影響已納管。 -->
          <div class="t-lbl">收集連上幾台（各位址最新一次）
            <span class="hint">
              <a class="dl" href="#" title="這幾台在 CIA 有登記（以這次探測當下的登記狀態為準）"
                 @click.stop.prevent="pickStatus('collected_registered')">CIA 已登記 {{ dStatusCount.collected_registered }}</a>
              ・
              <a class="dl" href="#" title="這幾台連得上（一樣算已納管），但 CIA 沒登記——CIA 待補登，可到首頁匯出「CIA 補登清單」（以這次探測當下的登記狀態為準）"
                 @click.stop.prevent="pickStatus('collected_unregistered')">CIA 待補登 {{ dStatusCount.collected_unregistered }}</a>
              ・
              <NuxtLink class="dl" :to="{ path: '/manage-state-detail', query: { state: '已納管' } }"
                        title="全站已納管（收集帳號 7 天內連得上），跟首頁同一個數字" @click.stop>全站已納管 {{ managed.length }}</NuxtLink>
            </span>
          </div>
        </div>
        <!-- 合計數字看不出其中幾台是儲存設備，所以底下把各類別拆開
             （2026-10-13 使用者：「是 storage 你怎麼抓不出來，可把 storage 獨立分類出來」）。
             舊的 run 這一欄是 NULL，要講「類別未留存」而不是裝作沒有這一類。
             ⚠️ 類別小字原本就能點（跳對應頁簽），行為不變——用 @click.stop 擋住卡片本身的
             下鑽，不然點「儲存 3」會同時觸發兩種切法。 -->
        <div class="tile clk" role="button" tabindex="0" :class="{ sel: dStatus === 'not_onboardable' }"
             :aria-pressed="dStatus === 'not_onboardable'" title="點一下只看這一類；再點一下取消"
             @click="pickStatus('not_onboardable')" @keydown.enter.prevent="pickStatus('not_onboardable')"
             @keydown.space.prevent="pickStatus('not_onboardable')">
          <div class="t-num mono">{{ dStatusCount.not_onboardable }}</div>
          <div class="t-lbl">另有納管方式
            <span v-if="nokKinds.length" class="hint">
              <template v-for="(k, i) in nokKinds" :key="k.kind"><template v-if="i">・</template><a
                class="dl" href="#" @click.stop.prevent="setRoute(NOK_KIND_TAB[k.kind] || 'nok_other')">{{ k.label }} {{ k.n }}</a></template>
            </span>
            <span v-else-if="dStatusCount.not_onboardable > 0" class="hint">類別未留存</span>
          </div>
        </div>
        <div class="tile warn clk" role="button" tabindex="0" :class="{ sel: dStatus === 'needs_action' }"
             :aria-pressed="dStatus === 'needs_action'" title="點一下只看這一類；再點一下取消"
             @click="pickStatus('needs_action')" @keydown.enter.prevent="pickStatus('needs_action')"
             @keydown.space.prevent="pickStatus('needs_action')">
          <div class="t-num mono">{{ dStatusCount.needs_action }}</div><div class="t-lbl">要人工處理</div>
        </div>
        <div class="tile clk" role="button" tabindex="0" :class="{ sel: dStatus === 'needs_agent' }"
             :aria-pressed="dStatus === 'needs_agent'" title="點一下只看這一類；再點一下取消"
             @click="pickStatus('needs_agent')" @keydown.enter.prevent="pickStatus('needs_agent')"
             @keydown.space.prevent="pickStatus('needs_agent')">
          <div class="t-num mono">{{ dStatusCount.needs_agent }}</div><div class="t-lbl">要請人裝 Agent</div>
        </div>
        <div class="tile clk" role="button" tabindex="0" :class="{ sel: dStatus === 'import_registered' }"
             :aria-pressed="dStatus === 'import_registered'" title="點一下只看這一類；再點一下取消"
             @click="pickStatus('import_registered')" @keydown.enter.prevent="pickStatus('import_registered')"
             @keydown.space.prevent="pickStatus('import_registered')">
          <div class="t-num mono">{{ dStatusCount.import_registered }}</div><div class="t-lbl">已登記但只能匯入</div>
        </div>
      </div>
      <!-- 現在在看什麼：有狀態篩選時一定看得到，而且就在表格正上方。
           不給這條，使用者會在「頁簽＋搜尋＋排序」之上再多一層看不見的篩選，
           然後問「為什麼只有 3 筆」。 -->
      <div v-if="dStatus" class="filterbar">
        <span class="fb-l">正在看</span>
        <span class="fb-v">{{ D_STATUS_LABEL[dStatus] }}</span>
        <span class="fb-n">{{ dVisible.length }} 筆</span>
        <button class="fb-x" @click="clearStatus()">✕ 清除篩選</button>
        <span class="muted small">這是「結果狀態」的切法；點下面任一個頁簽就會換回「走哪條路」的切法</span>
      </div>
      <!-- 走哪條路：預設只看「動得了」的（A14）-->
      <div class="routebar">
        <button class="rchip" :class="{ on: !dStatus && dRoute === 'actionable' }" @click="setRoute('actionable')">
          動得了的 {{ dActionableCount }}
        </button>
        <!-- 分類頁簽：名稱與順序讀後端 catTabs（collect_dispatch.CATEGORY_TABS）——跟「改分類」選單
             同一份，頁簽有幾個、選單就有幾個（2026-10-18）。位置固定，0 也列。
             SSH 收集不寫「（Linux）」：走 SSH 不代表是 Linux，見 isWindowsOnSsh() 的註解。
             大型主機（AIX／AS400）：AIX 照舊走 SSH 佈帳號，AS400／IBM i 不納管（指路 HMC） -->
        <button v-for="t in catTabs" :key="t.tab" class="rchip" :class="{ on: !dStatus && dRoute === t.tab }"
                @click="setRoute(t.tab as DTab)">
          {{ t.label }} {{ dRouteCount[t.tab] ?? 0 }}
          <span v-if="t.tab === 'ssh' && dSshWindowsCount" class="rchip-sub">（含 Windows {{ dSshWindowsCount }}）</span>
        </button>
        <span v-if="catTabsError" class="small" style="color: var(--bad)">{{ catTabsError }}</span>
        <button class="rchip" :class="{ on: !dStatus && dRoute === 'all' }" @click="setRoute('all')">
          全部 {{ dAllCount }}
        </button>
        <InfoNote>「只能匯入」＝資產庫<b>有登記</b>、但這次完全不通（沒開 22、沒開 445、也沒回應）的機器，這套系統對它做不了任何事，只能靠 Excel／vCenter 匯入它的資料。沒登記又沒回應的是空位址（那裡沒有機器），不列在任何頁簽裡，數量寫在右上角的註。<br><br>預設不顯示「只能匯入」，是因為那些列沒有按鈕也沒有下一步，只會把真正動得了的幾台淹掉。<b>數量還是列在上面</b>——「動得了的只有 3 台」跟「這個網段只有 3 台機器」是完全不同的兩件事，不能讓人看錯。</InfoNote>
      </div>
      <!-- 全部沒回應時一定要講清楚：不然表格空空的，看起來像「系統壞了」 -->
      <p v-if="dispatch.total && (dispatch.by_status.import_only || 0) === dispatch.total" class="d-allnone">
        這次 <b>{{ dispatch.total }}</b> 個位址<b>全部沒有回應</b>（22／445／5985 等都不通）——不是系統壞了，
        是這段沒有機器、或防火牆擋掉收集器。資產庫有登記的那幾台列在「只能匯入」。
      </p>
      <p v-if="dHiddenCount || dHiddenNok" class="muted small hidnote">
        已收起 <b>{{ dHiddenCount }}</b> 台「只能匯入」的位址（掃不到、動不了）<template v-if="dHiddenNok">、<b>{{ dHiddenNok }}</b> 台另有納管方式</template>
      </p>

      <!-- 勾選要拿來做什麼：**每一個頁簽都切得過去**，所以每一頁都能批次搬移
           （使用者 1.429.0：「我沒辦法一次改」——4 台 Windows+OpenSSH 要一次搬到 WinRM）。
           兩個模式的按鈕**不同時存在**（v-if 不是 disabled），勾選也不跨模式帶——見 pickMode 註解 -->
      <div v-if="dVisible.length" class="pickmode">
        <span class="pm-l">勾選後要做什麼：</span>
        <button class="pm" :class="{ on: onboardMode }" :disabled="!canOnboardHere"
                :title="canOnboardHere ? '勾要佈收集帳號的那幾台（試密碼／一次納管）'
                  : '這一頁沒有「待佈身分而且走 SSH」的機器，佈收集帳號對它們沒有作用'"
                @click="pickMode = 'onboard'">{{ dRoute === 'winrm' ? '🪟 Windows 納管' : '🔑 佈收集帳號' }}</button>
        <button class="pm" :class="{ on: moveMode }" @click="pickMode = 'move'">📁 搬移分類</button>
        <button class="pm" :class="{ on: tagMode }" @click="pickMode = 'tag'">🏷 加標籤</button>
        <span v-if="!canOnboardHere" class="muted small">（這一頁沒有要佈帳號的機器）</span>
        <span class="muted small">換模式會清空已勾選的——避免勾了一個用途、送去另一個</span>
      </div>

      <!-- 批次試密碼：勾好要憑證的那批，一次比對 A/B 兩組密碼哪一組通 -->
      <div v-if="onboardMode && probeableRows.length" class="probebar" :class="{ active: probeSelected.size > 0 }">
        <label class="pb-all">
          <input type="checkbox" :checked="allProbeableSelected" @change="toggleAllProbeable" />
          全選要憑證的 {{ probeableRows.length }} 台
        </label>
        <span class="muted small">已勾 <b>{{ probeSelected.size }}</b> 台</span>
        <button class="btn small ghost" :disabled="probeSelected.size === 0"
                @click="showBatchProbe = true">🔑 批次試密碼</button>
        <button class="btn small ghost" :disabled="probeSelected.size === 0 || probeSelected.size > 20"
                :title="probeSelected.size > 20 ? '預檢會真的登入，一次最多 20 台' : '唯讀登入一次看這台是什麼、能不能納管，不改任何東西'"
                @click="precheckIps = [...probeSelected]">🔍 預檢（只看不動）</button>
        <button class="btn small primary" :disabled="probeSelected.size === 0"
                @click="batchOnboardIps = [...probeSelected]">⚡ 勾選的一次納管</button>
        <span class="muted small">不確定密碼就先「批次試密碼」；確定了直接一次納管</span>
      </div>
      <!-- Windows 納管（勾選才收，2026-10-22）：自己一組勾選，送出的只有 winSelected -->
      <div v-if="onboardMode && winRows.length" class="probebar" :class="{ active: winSelected.size > 0 }">
        <label class="pb-all">
          <input type="checkbox" :checked="allWinSelected" @change="toggleAllWin" />
          全選 Windows {{ winRows.length }} 台
        </label>
        <span class="muted small">已勾 <b>{{ winSelected.size }}</b> 台</span>
        <button class="btn small primary" :disabled="!winPickNew.length" data-test="win-onboard"
                @click="openWinOnboard(winPickNew)">⚡ 勾選的納管（{{ winPickNew.length }}）</button>
        <button class="btn small ghost" :disabled="!winPickRe.length" data-test="win-reonboard"
                @click="openWinOnboard(winPickRe)">↻ 勾選的重新納管（{{ winPickRe.length }}）</button>
        <InfoNote>Windows 預設不登入、不收集（結果「待納管」）。勾選按「納管」才登入一次、記下收集帳號是不是本機管理員、在背景收一輪全部；之後的開始收集（手動／排程）才會收這幾台。不用輸入密碼（用系統設定的 WinRM 收集憑證），目標機上什麼都不改。</InfoNote>
      </div>

      <!-- 批次加標籤（v1.468.6）：每一列都勾得起來；沒登記的送出後回「未登記，無法加標籤」 -->
      <div v-if="tagMode && dVisible.length" class="probebar" :class="{ active: tagSelected.size > 0 }">
        <label class="pb-all">
          <input type="checkbox" :checked="allTagSelected" @change="toggleAllTag" />
          全選這一頁的 {{ dVisible.length }} 台
        </label>
        <span class="muted small">已勾 <b>{{ tagSelected.size }}</b> 台</span>
        <button class="btn small primary" :disabled="tagSelected.size === 0" @click="openTagFor(tagSelected)">🏷 幫勾選的加標籤</button>
      </div>
      <!-- 批次搬移：每一個頁簽都有（切到「搬移分類」模式就出現） -->
      <div v-if="moveMode && movableRows.length" class="probebar" :class="{ active: moveSelected.size > 0 }">
        <label class="pb-all">
          <input type="checkbox" :checked="allMovableSelected" @change="toggleAllMovable" />
          全選這一頁的 {{ movableRows.length }} 台
        </label>
        <span class="muted small">已勾 <b>{{ moveSelected.size }}</b> 台</span>
        <button class="btn small primary" :disabled="moveSelected.size === 0"
                @click="openMove(dVisible.filter((r) => moveSelected.has(r.ip)))">📁 批次搬移分類</button>
        <span class="muted small">分類判錯了（例如裝了 OpenSSH 的 Windows 被排在 SSH 收集）就整批搬到對的頁簽；重新收集不會蓋回去</span>
      </div>
      <!-- 一台都勾不了時要講原因，不要只留一個空工具列讓人以為畫面壞了 -->
      <p v-else-if="moveMode && dVisible.length" class="muted small hidnote">
        這一頁 <b>{{ dVisible.length }}</b> 台都還認不出是哪一台（資產庫沒登記、沒有主機名也沒有序號），
        所以不能勾、也不能搬——請先在下面「登記成資產」把它們登記起來。
      </p>
      <WinPermPanel v-if="dRoute === 'winrm' && !dStatus" ref="winPermRef" @pick-ip="pickWinIp" />
      <div class="d-searchbar">
        <input v-model="dSearch" class="mkw d-search" type="search" placeholder="搜尋 IP／主機名稱／資產名稱／埠／結果" />
        <button type="button" class="btn small" :disabled="exportBusy" data-test="pending-export"
                @click="exportPending">⬇ 匯出 Excel（{{ dVisible.length }} 筆）</button>
        <label v-if="!dStatus" class="d-hide">
          <input v-model="dHideOnboarded" type="checkbox" :disabled="dResult !== 'all'" />
          隱藏已納管<template v-if="dHiddenOnboarded">（{{ dHiddenOnboarded }} 台）</template>
        </label>
        <span v-if="!dStatus" class="d-res">
          <!-- 待佈身分／待開 WinRM／待開 SSH 只在這個頁簽有的時候出現，0 筆不佔位置 -->
          <button v-for="k in D_RESULT_KEYS.filter((x) => D_RESULT_KEYS.indexOf(x) < 5 || dResultCount[x] || dResult === x)" :key="k" type="button"
                  class="osbtn" :class="{ on: dResult === k }" @click="pickResult(k)">
            {{ D_RESULT_LABEL[k] }} {{ dResultCount[k] }}
          </button>
        </span>
        <span class="fb-n mono">顯示 {{ dVisible.length }} ／ 總 {{ dScoped.length }}</span>
        <InfoNote>搜尋只找<b>目前這個頁簽</b>（也套結果篩選與「隱藏已納管」）。別的頁簽也有符合的，表格上方會寫「其他頁簽還有 N 筆符合」，點頁簽名就跳過去、搜尋字保留；在「全部」頁簽才會多一欄「所在頁簽」。空白隔開＝每個字都要有。項次編號不會因為搜尋、篩選或排序而改變。<br><br>「隱藏已納管」預設打勾：已納管的有自己的「已納管主機」頁簽；這台瀏覽器會記住你的選擇。「上次失敗」＝這台最後一次納管（任何方式）失敗了；之後成功就不再算。點表格「結果」欄的標籤也可以篩。<br><br>全選只會勾到現在看得到的列。</InfoNote>
      </div>
      <p v-if="dTag" class="d-otherhits">只看標籤「<b>{{ dTag }}</b>」的列
        <a class="dl" href="#" @click.prevent="dTag = ''">清除</a></p>
      <!-- 其他頁簽也有符合的（v1.468.4）：目前頁簽 0 筆時放大顯示 -->
      <p v-if="dSearching && (dOtherHits.total || dOtherHits.hiddenHere)" class="d-otherhits"
         :class="{ strong: !dVisible.length }">
        <template v-if="dOtherHits.total">
          其他頁簽還有 <b>{{ dOtherHits.total }}</b> 筆符合：
          <template v-for="(h, i) in dOtherHits.tabs" :key="h.tab">
            <span v-if="i">、</span><a class="dl" href="#" @click.prevent="jumpToHits(h.tab)">{{ tabLabel(h.tab) }} {{ h.n }}</a>
          </template>
        </template>
        <template v-if="dOtherHits.hiddenHere">
          <span v-if="dOtherHits.total">；</span>這個頁簽被篩選收起來的還有
          <a class="dl" href="#" @click.prevent="showHiddenHits">{{ dOtherHits.hiddenHere }} 筆</a>
        </template>
      </p>
      <p v-if="dSearching && !dVisible.length && !dOtherHits.total && !dOtherHits.hiddenHere && dAssetHits.length && dAssetHitsQ === dSearch.trim()" class="d-assethint">
        資產裡有 <b>{{ dAssetHits.length >= 50 ? '50+' : dAssetHits.length }}</b> 筆、還沒掃過
        <NuxtLink v-if="dAssetHits.length === 1" class="dl" :to="`/assets/${dAssetHits[0].asset_serial}`">
          {{ dAssetHits[0].hostname || dAssetHits[0].ip || dAssetHits[0].asset_serial }}</NuxtLink>
        <NuxtLink v-else class="dl" :to="{ path: '/assets', query: { q: dSearch.trim() } }">看是哪幾筆</NuxtLink>
        <InfoNote>這次收集的結果裡沒有它——它的 IP 不在掃過的範圍。到「開始收集」把它的 IP 加進去收集一次，就會出現在這張表、可以一鍵納管。</InfoNote>
      </p>
      <!-- 左右拉霸（2026-10-22 使用者：「改分類在最右邊，我要縮小才看到」）：以前橫向捲軸在上千列的
           表格最底下，等於看不到。表格自己限高、自己捲，捲軸永遠在畫面內；標題列與「操作」欄釘住 -->
      <div class="tbl-wrap d-scroll">
        <table>
          <thead><tr>
            <!-- 同一欄、兩種用途，由上面那排**明寫的模式**決定（見 pickMode 註解）：
                 搬移分類＝選要搬的；佈收集帳號＝選要試密碼／納管的。兩組勾選各自獨立 -->
            <th class="ck">
              <input v-if="tagMode" type="checkbox" title="全選這一頁"
                     :checked="allTagSelected" @change="toggleAllTag" />
              <input v-else-if="moveMode" type="checkbox" title="全選這一頁可搬移的"
                     :checked="allMovableSelected" :disabled="!movableRows.length"
                     @change="toggleAllMovable" />
              <input v-else-if="probeableRows.length || !winRows.length" type="checkbox" title="全選要憑證的"
                     :checked="allProbeableSelected"
                     :disabled="!probeableRows.length" @change="toggleAllProbeable" />
              <input v-else type="checkbox" title="全選 Windows"
                     :checked="allWinSelected" @change="toggleAllWin" />
            </th>
            <SortTh k="no" :active="dSortKey" :dir="dSortDir" @sort="dToggle">#</SortTh>
            <SortTh v-if="dShowTabCol" k="tab" :active="dSortKey" :dir="dSortDir" @sort="dToggle">所在頁簽</SortTh>
            <SortTh k="ip" :active="dSortKey" :dir="dSortDir" @sort="dToggle">IP</SortTh>
            <SortTh k="hostname" :active="dSortKey" :dir="dSortDir" @sort="dToggle">主機名稱</SortTh>
            <!-- 資產名稱：主機名稱是空的那幾列靠它認台（2026-10-16 使用者：「加入資產名稱欄位」）。
                 例如「技術中台」——主機名沒登記但資產名稱有值，光看 IP 認不出是哪一台 -->
            <SortTh k="asset_name" :active="dSortKey" :dir="dSortDir" @sort="dToggle">資產名稱</SortTh>
            <SortTh k="tags_sort" :active="dSortKey" :dir="dSortDir" @sort="dToggle">標籤</SortTh>
            <SortTh k="open_ports" :active="dSortKey" :dir="dSortDir" @sort="dToggle">開放埠</SortTh>
            <SortTh k="route" :active="dSortKey" :dir="dSortDir" @sort="dToggle">走哪條路</SortTh>
            <SortTh v-if="showKindCol" k="kind_sort" :active="dSortKey" :dir="dSortDir" @sort="dToggle">判斷 <InfoNote>活著但 22／445／5985 都進不去的機器，系統判斷它是什麼機器。<b>Push Agent 只支援 Linux／Unix</b>：判斷是 Windows 的改請開 WinRM（5985），放到「WinRM 收集」頁簽；AIX 改請開 SSH（22）；設備／VMware 走它自己的納管方式。<br><br><b>確定</b>＝有人工指定、收集到的作業系統或資產登記；<b>推測</b>＝只有開放埠／TTL／上次掃描的線索（線索互相矛盾也算推測）；<b>不明</b>＝什麼線索都沒有——請先確認是什麼機器，安裝包仍可取得，但只適用 Linux／Unix。<br><br><b>另有納管方式的網路設備／儲存設備／設備</b>寫廠牌：SSH banner 自報或資產登記寫明的＝確定；只有 MAC 廠商的＝推測（Cisco／HPE／Dell 也做伺服器）；都沒有寫「廠牌不明」。<br><br>滑過看依據。判斷錯了按「改分類」。</InfoNote></SortTh>
            <SortTh k="status" :active="dSortKey" :dir="dSortDir" @sort="dToggle">結果</SortTh>
            <!-- 這台上次「系統去建帳號」是什麼時候、用哪個帳號（2026-10-18，資料來自
                 onboard_audit，批量查回來；查不到就是這台還沒被系統納管過） -->
            <SortTh k="last_onboard_at" :active="dSortKey" :dir="dSortDir" @sort="dToggle">上次納管時間</SortTh>
            <SortTh k="last_onboard_user" :active="dSortKey" :dir="dSortDir" @sort="dToggle">納管帳號</SortTh>
            <SortTh k="collected_at" :active="dSortKey" :dir="dSortDir" @sort="dToggle">上次收集</SortTh>
            <SortTh k="message" :active="dSortKey" :dir="dSortDir" @sort="dToggle">下一步</SortTh>
            <th class="ops-h">操作</th>
            <!-- 上次失敗（2026-10-18）：最後一次納管嘗試（任何方式）失敗才有；之後成功就消失。
                 2026-10-22 使用者：「上次失敗放在最後面，這樣我納管跟改分類才會比較好看到」 -->
            <SortTh k="last_fail_at" :active="dSortKey" :dir="dSortDir" @sort="dToggle">上次失敗</SortTh>
          </tr></thead>
          <tbody>
            <!-- 一列資料可能長出兩個 <tr>（本體＋展開的完整說明），所以用 template v-for
                 把兩個 tr 包在同一次迴圈裡。**不可以**改成「tr v-for 後面再接一個兄弟 tr」
                 ——那是 test_frontend_guards 擋的第一種寫法（作用域不同，整頁會空白）。 -->
            <template v-for="r in dVisible" :key="r.ip">
            <tr>
              <td class="ck">
                <input v-if="tagMode" type="checkbox" :checked="tagSelected.has(r.ip)" @change="toggleTag(r.ip)" />
                <template v-else-if="moveMode">
                  <input v-if="canMove(r)" type="checkbox" :checked="moveSelected.has(r.ip)"
                         @change="toggleMove(r.ip)" />
                  <!-- 勾不了的要講為什麼，不要給一個空格子讓人以為畫面壞了 -->
                  <span v-else class="muted" :title="whyCannotMove(r)">—</span>
                </template>
                <input v-else-if="canProbe(r)" type="checkbox" :checked="probeSelected.has(r.ip)"
                       @change="toggleProbe(r.ip)" />
                <input v-else-if="canWin(r)" type="checkbox" :checked="winSelected.has(r.ip)"
                       @change="toggleWin(r.ip)" />
              </td>
              <td class="mono small dim">{{ r.no }}</td>
              <!-- 「全部」頁簽：這一列在哪個頁簽（點一下＝跳過去，搜尋字保留） -->
              <td v-if="dShowTabCol">
                <button type="button" class="tabtag" :title="`跳到「${tabLabel(r.tab)}」頁簽`"
                        @click="jumpToHits(r.tab as string)">{{ tabLabel(r.tab) }}</button>
              </td>
              <td class="mono">
                <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">{{ r.ip }}</NuxtLink>
                <template v-else>{{ r.ip }}</template>
                <button type="button" class="infobtn" title="納管前資訊：這台已知的全部線索（只讀，不連線）"
                        @click="preinfoIp = r.ip">ⓘ</button>
              </td>
              <td class="hname">{{ r.hostname || (r.registered ? '—' : '(未登記)') }}</td>
              <!-- 沒值跟「主機名稱」一樣寫「—」。太長的截在欄寬內，滑過看得到全名 -->
              <td class="aname" :title="r.asset_name || ''">{{ r.asset_name || '—' }}</td>
              <td>
                <button v-for="t in r.tags" :key="t" type="button" class="tagchip" :class="{ on: dTag === t }"
                        :title="tagTitle(r.asset_serial, t)" @click="dTag = dTag === t ? '' : t">{{ t }}</button>
                <span v-if="!r.tags?.length" class="muted small">—</span>
              </td>
              <!-- 天條二：埠點下去看得到「還有誰也在跑它」 -->
              <td class="mono small">
                <template v-if="r.open_ports">
                  <NuxtLink v-for="p in r.open_ports.split(',')" :key="p" class="dl port"
                            :to="`/services?port=${p}`">{{ p }}</NuxtLink>
                </template>
                <template v-else>—</template>
              </td>
              <!-- 走 ssh 但開了 3389／5985：標出來，不然人會照頁簽拿 Linux 腳本去打 -->
              <td>
                <!-- 不納管的不寫探測到的 route（ESXi 寫「Push Agent」會像在叫人去裝 agent）。
                     route 是探測事實、沒有被改掉，留在 tooltip 裡查得到 -->
                <template v-if="r.guide">
                  <span v-if="r.guide.how" class="route-alt"
                        :title="`探測到的路由：${ROUTE_LABEL[r.route] || r.route}（開放埠推出來的事實）。這台不走 Linux 那套（SSH 建本機帳號），要走「${r.guide.how}」。`">{{ r.guide.how }}</span>
                  <span v-else class="muted"
                        :title="`探測到的路由：${ROUTE_LABEL[r.route] || r.route}（開放埠推出來的事實）。${r.guide.note ?? ''}`">目前不收</span>
                </template>
                <!-- 判斷是 Windows／AIX 的進不去的機器：不寫 Push Agent（那條路它走不了），寫它該走的路 -->
                <span v-else-if="r.status === 'needs_winrm_port'" class="route-alt"
                      :title="`探測到的路由：${ROUTE_LABEL[r.route] || r.route}（開放埠推出來的事實）。判斷是 Windows，Push Agent 裝不上，要開 WinRM（5985）走 WinRM 收集。`">WinRM（待開 5985）</span>
                <span v-else-if="r.status === 'needs_ssh_port'" class="route-alt"
                      :title="`探測到的路由：${ROUTE_LABEL[r.route] || r.route}（開放埠推出來的事實）。判斷是 AIX，Push Agent 裝不上，要開 SSH（22）走一鍵納管。`">SSH（待開 22）</span>
                <template v-else>{{ ROUTE_LABEL[r.route] || r.route }}</template>
                <span v-if="isWindowsOnSsh(r)" class="wintag"
                      title="開了 3389／5985（只有 Windows 會開）——這台是裝了 OpenSSH 的 Windows，走 SSH 收集沒錯，但納管請選 Windows 腳本">Windows+OpenSSH</span>
              </td>
              <td v-if="showKindCol">
                <span v-if="r.vendor" class="kindtag" :class="`kc-${r.vendor.confidence}`"
                      :title="vendorTitle(r)">{{ r.vendor.text }}<span class="kconf">{{ r.vendor.confidence_label }}</span></span>
                <span v-else-if="r.kind_guess" class="kindtag" :class="`kc-${r.kind_guess.confidence}`"
                      :title="kindTitle(r)">{{ r.kind_guess.kind === 'unknown' ? '' : r.kind_guess.label }}<span class="kconf">{{ r.kind_guess.confidence_label }}</span></span>
                <span v-else class="muted small">—</span>
              </td>
              <td>
                <!-- 點結果標籤＝依結果篩選（已納管／未納管） -->
                <button type="button" class="pill pill-btn" :class="STATUS_TONE[r.status]"
                        :title="r.status === 'collected' ? '點一下只看已納管的' : '點一下只看未納管的'"
                        @click="pickResult(r.status === 'collected' ? 'onboarded' : 'not_onboarded')">{{ STATUS_LABEL[r.status] || r.status }}</button>
                <!-- 人改過的要看得出來：滑過看得到誰、何時、從哪類改到哪類（2026-10-16） -->
                <span v-if="r.os_override" class="ovtag" :title="overrideTitle(r)">人工指定</span>
                <span v-if="r.win?.opted_in" class="ovtag" :class="{ 'win-noadm': r.win.is_admin === 0 }"
                      :title="winTitle(r.win)">本機管理員：{{ adminText(r.win.is_admin) }}</span>
              </td>
              <td class="mono small">{{ r.last_onboard_at || '—' }}</td>
              <td class="mono small">{{ r.last_onboard_user || '—' }}</td>
              <!-- 上次收集（v1.468.4 合併檢視）：這個位址自己最新一次結果的時間；超過 N 天標「舊」 -->
              <td class="mono small" :title="r.run_id ? `第 ${r.run_id} 次收集` : ''">
                {{ r.collected_at || '—' }}
                <button v-if="r.stale" type="button" class="staletag" title="結果超過設定天數，點一下只看舊結果"
                        @click="pickResult('stale')">舊</button>
              </td>
              <!-- 下一步：不納管的要指路。「不納管」是結論不是下一步——
                   使用者原話「這些都有它獨立納管方式」 -->
              <!-- 預設只給一句「要做什麼」，原因／錯誤訊息點「…」才展開（使用者 2026-10-16：
                   「下一步敘述太長了。我有興趣用…打開」）。展開的內容放在**下面整列**
                   （見 tr.msg-row），不塞在這一格裡——塞在格子裡會被欄寬擠成又窄又高一條，
                   而且會把欄寬推來推去。放整列就只是多長一行，欄位位置完全不動。 -->
              <td class="msg">
                <div class="msg-line">
                  <template v-if="r.guide?.page">
                    走 <NuxtLink class="dl" :to="r.guide.page">{{ guideWhere(r) }}</NuxtLink>
                    <span class="muted small"> — 不走 Linux 那套（SSH 建本機帳號）</span>
                  </template>
                  <template v-else-if="r.guide">
                    <b>目前不收</b>
                    <span class="muted small"> — {{ r.guide.note }}</span>
                  </template>
                  <template v-else>{{ nextShort(r) }}</template>
                  <button v-if="hasMore(r)" class="msg-x" :aria-expanded="msgOpen.has(r.ip)"
                          :title="msgOpen.has(r.ip) ? '收回' : '展開完整說明（原因／錯誤訊息）'"
                          @click="toggleMsg(r.ip)">{{ msgOpen.has(r.ip) ? '收回' : '…' }}</button>
                </div>
              </td>
              <td class="ops">
                <button v-if="r.status === 'needs_agent'" class="btn small"
                        :class="isKindUnconfirmed(r) ? 'ghost' : 'primary'"
                        :title="isKindUnconfirmed(r) ? '還不確定是什麼機器——Push Agent 只適用 Linux／Unix，先確認再交給對方安裝' : ''"
                        :disabled="pkgBusy" @click="getPackage(r.ip)">取得安裝包{{ isKindUnconfirmed(r) ? '（未確認）' : '' }}</button>
                <span v-else-if="r.status === 'needs_winrm_port' || r.status === 'needs_ssh_port'" class="muted small"
                      :title="r.message">待對方開埠</span>
                <!-- Windows：已納管的 重新納管／取消納管；還沒的 納管（2026-10-22 勾選才收） -->
                <template v-else-if="r.win?.opted_in">
                  <span class="small" style="color: var(--brand-dark)">✓ 已納管</span>
                  <button class="btn small ghost" type="button" @click="openWinOnboard([r])">↻ 重新納管</button>
                  <button class="btn small ghost danger-t" type="button" @click="winRevoke = { ip: r.ip, hostname: r.hostname }">取消納管</button>
                </template>
                <button v-else-if="canWin(r)" class="btn small primary" type="button"
                        @click="openWinOnboard([r])">⚡ 納管</button>
                <button v-else-if="canProbe(r)"
                        class="btn small primary"
                        @click="openOnboard({ ip: r.ip, os_guess: null } as any)">⚡ 一鍵納管</button>
                <NuxtLink v-else-if="r.status === 'import_only'" class="btn small ghost" to="/import">去匯入</NuxtLink>
                <span v-else-if="r.status === 'collected'" class="small" style="color: var(--brand-dark)">✓ 已納管</span>
                <!-- 操作欄：這條路這一頁做不了，不是「不管它」——所以寫「另有路徑」 -->
                <span v-else-if="r.status === 'not_onboardable'" class="muted small"
                      :title="r.message">{{ r.guide && !r.guide.how ? '目前不收' : '另有收集路徑' }}</span>
                <span v-else class="muted small">—</span>
                <!-- 每一列都有：使用者「我還是沒看到可以移動到其他組別的方式」（2026-10-16）。
                     不能只留在資產明細頁——他是在這一頁看到分類錯的 -->
                <button class="btn small ghost" :disabled="!canMove(r)"
                        :title="canMove(r) ? '這台被分到哪一類不對？搬到對的頁簽（重新收集不會蓋回去）' : whyCannotMove(r)"
                        @click="openMove([r])">改分類</button>
              </td>
              <td>
                <button v-if="r.last_fail_at" type="button" class="failtag" :title="failTitle({ at: r.last_fail_at, short: r.last_fail_short, message: r.last_fail_msg })"
                        @click="pickResult('failed')">上次失敗 {{ failWhen(r.last_fail_at) }}：{{ r.last_fail_short }}</button>
                <span v-else class="muted small">—</span>
              </td>
            </tr>
            <!-- 展開的完整說明：整列寬，一字不少（括號裡那段英文錯誤訊息是查問題的依據，
                 不可以省）。放整列而不是塞回那一格，欄寬完全不受影響 -->
            <tr v-if="msgOpen.has(r.ip) && hasMore(r)" class="msg-row">
              <td :colspan="dColspan">
                <div class="msg-full">
                  <span class="mf-ip mono">{{ r.ip }}</span>{{ r.message }}
                </div>
              </td>
            </tr>
            </template>
            <!-- 0 筆也要有反應：點了一張數字 0 的卡，表格空空的卻什麼都沒說，
                 跟「點不下去」一樣糟（使用者的規矩：不知道要顯示成不知道，不能用沒反應代替）。
                 上面那條篩選列已經寫明現在在看哪一類，這裡只補一句最短的 -->
            <tr v-if="!dVisible.length"><td :colspan="dColspan" class="d-empty">{{ dSearch.trim() ? '找不到符合的' : (dScoped.length ? `這一類 ${dScoped.length} 台都被上方篩選收起來了` : '這一類沒有') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    </div>

    <!-- 未登記主機／納管失敗各自一個頁簽（2026-10-22 使用者：「我不想往下拉」）-->
    <div v-show="activeTab === 'unregistered'">
    <div v-if="!selected" class="sec-head" style="margin-top:26px"><span class="t">登記成資產</span>
      <span v-if="hosts.length" class="acts">
        <input v-model="uQ" type="search" class="cell-input" style="width:260px" placeholder="搜尋 IP／主機名稱／研判／網段…" />
        <button type="button" class="btn small" :disabled="exportBusy" @click="exportUnregistered">⬇ 匯出 Excel（{{ sorted.length }} 台）</button>
      </span>
    </div>
    <div v-if="hosts.length === 0 && !selected" class="empty">
      目前沒有「掃到了、CIA 沒登記」的主機——都納管完了，或還沒掃描。
    </div>

    <!-- 勾選列：沒勾東西時安靜，勾了才亮起來（同批次試密碼那一列） -->
    <div v-if="!selected && hosts.length" class="probebar" :class="{ active: adoptSelected.size > 0 }">
      <label class="pb-all">
        <input type="checkbox" :checked="allAdoptSelected" @change="toggleAllAdopt" />
        全選 {{ sorted.length }} 台
      </label>
      <span class="muted small">已勾 <b>{{ adoptSelected.size }}</b> 台</span>
      <!-- ONBOARD-FLOW1（2026-10-18）：兩個對等的動作，跑的是同一支 webit3_onboard.sh -->
      <button class="btn small primary" :disabled="adoptSelected.size === 0"
              @click="batchOnboardIps = [...adoptSelected]">我有密碼：網頁一鍵納管</button>
      <button class="btn small primary" :disabled="adoptSelected.size === 0"
              @click="pasteIps = [...adoptSelected]">我沒有密碼：取得納管指令</button>
    </div>

    <!-- 上次收集摘要：讓「登記成資產」這一區跟上面的結果表對得起來（2026-09-16 使用者） -->
    <p v-if="!selected && dispatch && dispatch.results.length" class="lastsum">
      上次收集 <span class="mono">{{ lastRunAt || '—' }}</span>：
      收到 <b class="ok-t">{{ dispatch.collected }}</b>、
      要處理 <b class="warn-t">{{ dispatch.needs_action }}</b>、
      另有納管方式 {{ dispatch.by_status?.not_onboardable || 0 }}、
      空位址 {{ dispatch.empty_addresses || 0 }}
      <span class="muted sm">／共探測 {{ dispatch.total }} 個位址</span>
    </p>

    <!-- 候選清單 -->
    <div v-if="!selected && hosts.length" class="tbl-wrap">
      <table>
        <thead><tr>
          <th class="ck"><input type="checkbox" :checked="allAdoptSelected" @change="toggleAllAdopt" /></th>
          <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
          <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
          <SortTh k="os_guess" :active="sortKey" :dir="sortDir" @sort="toggle">研判（掃描指紋） <InfoNote><b>未登入主機、純從網路探測</b>推得的線索，用來認出這台大概是什麼；真正的主機名／序號要納管後由 facts 收集。</InfoNote></SortTh>
          <th>上次收集 <InfoNote>這台在<b>最近一次「開始收集」</b>裡的結果。空白＝那一輪沒有掃到這台（不在範圍內）。<br><br>滑過看失敗原因。</InfoNote></th>
          <th>操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="h in sorted" :key="h.ip">
            <td class="ck"><input type="checkbox" :checked="adoptSelected.has(h.ip)" @change="toggleAdopt(h.ip)" /></td>
            <td class="mono">{{ h.ip }}</td>
            <td>{{ h.hostname || '(反解不到)' }}</td>
            <td>
              <div class="fp">
                <span v-if="h.is_vm" class="tag vm">VM</span>
                <span v-if="h.mac_vendor" class="tag vendor">{{ h.mac_vendor }}</span>
                <span v-if="h.os_guess" class="tag os">{{ h.os_guess }}</span>
                <span v-if="!h.mac_vendor && !h.os_guess && !h.is_vm" class="muted">— 線索不足 —</span>
              </div>
              <div class="fp-sub muted">
                <span v-if="h.open_ports">埠 {{ h.open_ports }}</span>
                <span v-if="h.mac" class="mono">{{ h.mac }}</span>
                <span v-if="h.segment">{{ h.segment }}</span>
              </div>
            </td>
            <td class="lastres">
              <template v-if="dispatchByIp[h.ip]">
                <span class="pill" :class="STATUS_TONE[dispatchByIp[h.ip].status]"
                      :title="dispatchByIp[h.ip].message || ''">
                  {{ STATUS_LABEL[dispatchByIp[h.ip].status] || dispatchByIp[h.ip].status }}
                </span>
                <div v-if="dispatchByIp[h.ip].message" class="resmsg" :title="dispatchByIp[h.ip].message">
                  {{ dispatchByIp[h.ip].message }}
                </div>
              </template>
              <span v-else class="muted sm" title="最近一次收集沒有涵蓋這台">—</span>
            </td>
            <td class="ops">
              <button class="btn small primary" @click="openOnboard(h)" title="系統自動進去建收集帳號">⚡ 一鍵納管</button>
              <button class="btn small ghost" @click="openAdopt(h)" title="只登記成資產、不建收集帳號">手動登記</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    </div>

    <div v-show="activeTab === 'failures'">
    <!-- 納管失敗清單（2026-09-17 使用者：「這些帳號納管失敗，我以後要怎麼查那些納管失敗過，
         他的 root 密碼可能不是預設密碼」）。納管紀錄是流水帳，這裡給的是**現況**：
         每台最後一次的結果，只列現在仍然進不去的，並帶部門窗口（要去找誰問密碼）。 -->
    <section v-if="!selected && failures.length" class="card">
      <div class="sec-head">
        <span class="t">納管失敗清單（{{ failures.length }} 台<template v-if="fOs.size || fReason || fQ.trim()">，顯示 {{ fShown.length }} 台</template>）</span>
        <span class="acts">
          <input v-model="fQ" type="search" class="cell-input" style="width:260px"
                 placeholder="搜尋 IP／主機名稱／機房／單位／窗口…" />
          <a class="btn small" :href="failExportUrl">⬇ 匯出 Excel{{ (fOs.size || fReason || fQ.trim()) ? `（${fShown.length} 台）` : '' }}</a>
        </span>
      </div>
      <!-- 作業系統大類（2026-10-22 使用者：「把 OS 分類出來」「我可以自己選 OS 匯出」）：可複選，匯出跟著選 -->
      <p class="fsum">
        <span class="dim">作業系統：</span>
        <button v-for="(n, k) in failSummary.by_os_class" :key="k" type="button" class="fchip"
                :class="{ on: fOs.has(String(k)) }" @click="toggleFailOs(String(k))">
          {{ k }} <b>{{ n }}</b>
        </button>
        <button v-if="fOs.size" type="button" class="btn small ghost" @click="fOs = new Set()">全部</button>
      </p>
      <p class="fsum">
        <span class="dim">失敗原因：</span>
        <button v-for="(n, k) in failSummary.by_reason" :key="k" type="button" class="fchip"
                :class="{ hot: k === '帳密不對', on: fReason === k }" @click="fReason = fReason === k ? '' : String(k)">
          {{ k }} <b>{{ n }}</b>
        </button>
        <span v-if="failSummary.credential" class="dim">
          　← 這 {{ failSummary.credential }} 台的密碼不是預設值，要跟窗口要正確密碼或走 PAM
        </span>
        <span v-if="failSummary.excluded_elsewhere" class="dim">
          　另有 {{ failSummary.excluded_elsewhere }} 台在 2-1 歸「另有納管方式」（OCP／ESXi／設備等），不是待辦，沒列在這裡、也不匯出
        </span>
      </p>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="no" :active="fSortKey" :dir="fSortDir" @sort="fToggle">#</SortTh>
            <SortTh k="ip" :active="fSortKey" :dir="fSortDir" @sort="fToggle">IP</SortTh>
            <SortTh k="hostname" :active="fSortKey" :dir="fSortDir" @sort="fToggle">主機名稱</SortTh>
            <SortTh k="os_class" :active="fSortKey" :dir="fSortDir" @sort="fToggle">作業系統大類</SortTh>
            <SortTh k="physical_location" :active="fSortKey" :dir="fSortDir" @sort="fToggle">機房</SortTh>
            <SortTh k="department" :active="fSortKey" :dir="fSortDir" @sort="fToggle">使用單位（部門）</SortTh>
            <SortTh k="contact" :active="fSortKey" :dir="fSortDir" @sort="fToggle">窗口</SortTh>
            <SortTh k="reason" :active="fSortKey" :dir="fSortDir" @sort="fToggle">失敗原因</SortTh>
            <SortTh k="next_step" :active="fSortKey" :dir="fSortDir" @sort="fToggle">下一步</SortTh>
            <SortTh k="fail_count" :active="fSortKey" :dir="fSortDir" @sort="fToggle" class="num">失敗次數</SortTh>
            <SortTh k="last_tried_at" :active="fSortKey" :dir="fSortDir" @sort="fToggle">最後一次</SortTh>
            <th>操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="f in fShown" :key="f.ip">
              <td class="num dim">{{ f.no }}</td>
              <td class="mono">
                <NuxtLink v-if="f.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(f.asset_serial)}`">{{ f.ip }}</NuxtLink>
                <template v-else>{{ f.ip }}</template>
              </td>
              <td>{{ f.hostname || '—' }}</td>
              <td :title="f.os_override ? `人工指定：${f.os_override.os_type}　${f.os_override.by ?? '不詳'} 於 ${f.os_override.at}` : (f.os || '')">
                {{ f.os_class }}<span v-if="f.os_override" class="dim small"> ✎</span></td>
              <td>{{ f.physical_location || '—' }}</td>
              <td>{{ f.department || '—' }}</td>
              <td>{{ f.contact || '—' }}<span v-if="f.custodian && f.custodian !== f.contact" class="dim small">／{{ f.custodian }}</span></td>
              <td><span class="fchip" :class="{ hot: f.reason === '帳密不對' }">{{ f.reason }}</span></td>
              <td class="small" :title="f.message || ''">{{ f.next_step }}</td>
              <td class="num">{{ f.fail_count }}</td>
              <td class="mono small dim">{{ f.last_tried_at }}</td>
              <!-- 每一列都能改分類（2026-10-22 使用者：「原來是 LINUX 可手動調整到我指定群，每一個都要可以調」）：
                   同 2-1 那顆，同一個視窗、同一張人工指定表 -->
              <td class="ops">
                <button class="btn small ghost" :disabled="!canMove(failMoveRow(f))"
                        :title="canMove(failMoveRow(f)) ? '這台被分到哪一類不對？改到你指定的分類' : whyCannotMove(failMoveRow(f))"
                        @click="openMove([failMoveRow(f)])">改分類</button>
              </td>
            </tr>
            <tr v-if="!fShown.length"><td colspan="12" class="d-empty">這個篩選沒有符合的</td></tr>
          </tbody>
        </table>
      </div>
    </section>
    <div v-if="!selected && !failures.length" class="empty">目前沒有納管失敗、仍然進不去的主機。</div>
    </div>

    <div v-show="activeTab === 'onboarded'">
    <!-- ===== 已納管主機：重新納管／取消納管／編輯都在這裡，跟上面的「手動登記」同一種操作方式 ===== -->
    <div v-if="!selected" id="managed-hosts" class="sec-head" style="margin-top:4px">
      <span class="t">已納管主機 {{ managed.length }}<span v-if="managedStale.length" class="muted small">　＋已納管但失聯 {{ managedStale.length }}</span>
        <InfoNote>系統收得到資料的主機。<br><br><b>↻ 重新納管</b>＝再跑一次納管腳本：補佈帳號盤點輔助程式、修回被動過的帳號或金鑰。帳號在就不重建、金鑰在就不重加，動手前會先備份並留 <code>restore.sh</code>。<br><b>取消納管</b>＝把這台上的收集帳號、金鑰、sudo 規則收回來。<br><br>要補佈一大批：全選（或先搜尋篩出一批再全選）→「↻ 勾選的一次重新納管」，密碼只要輸入一次。</InfoNote>
      </span>
    </div>
    <div v-if="!selected && managedAll.length" class="probebar" :class="{ active: managedSelected.size > 0 }">
      <label class="pb-all">
        <input type="checkbox" :checked="allManagedSelected" @change="toggleAllManaged" />
        全選 {{ mSorted.length }} 台
      </label>
      <span class="muted small">已勾 <b>{{ managedSelected.size }}</b> 台</span>
      <button class="btn small primary" :disabled="managedLinuxPicked.length === 0"
              @click="batchOnboardIps = managedLinuxPicked">↻ 勾選的一次重新納管</button>
      <!-- Windows 納管的那幾台另一顆（不要密碼）；Linux 那顆不會送到它們 -->
      <button v-if="managedWinPicked.length" class="btn small ghost" data-test="managed-win-reonboard"
              @click="openWinOnboardManaged">↻ 勾選的 Windows 重新納管（{{ managedWinPicked.length }}）</button>
      <button class="btn small ghost" :disabled="managedSelected.size === 0"
              @click="tagRows = managedAll.filter((m) => m.ip && managedSelected.has(m.ip)).map((m) => ({ ip: m.ip, asset_serial: m.asset_serial, hostname: m.hostname }))">🏷 加標籤</button>
      <button class="btn small" :disabled="selectedSerials.length === 0 || pqBusy" data-test="collect-all"
              @click="collectAllSelected">{{ pqBusy ? '送出中…' : `收集全部（已勾 ${selectedSerials.length} 台）` }}</button>
      <InfoNote>對勾選的每一台收一輪全部：硬體規格／服務／軟體／帳號／OS 重建／組態檢核（同資產頁「收集全部」）。唯讀，不改目標機。背景逐台排隊跑，不會同時轟一大批；每一台的結果寫進「開始收集」頁簽的「開始收集／納管後自動收集紀錄」。沒有資產序號的主機收不了，不會送出。</InfoNote>
      <a class="btn small ghost" :href="managedExportUrl" data-test="managed-export">⬇ 匯出 Excel</a>
      <span v-if="mTag" class="muted small">只看標籤「<b>{{ mTag }}</b>」 <a class="dl" href="#" @click.prevent="mTag = ''">清除</a></span>
      <span class="osfilter">
        <button class="osbtn" :class="{ on: managedOs === '' }" @click="managedOs = ''">全部 {{ managedAll.length }}</button>
        <button v-for="os in Object.keys(managedOsCounts)" :key="os" v-show="managedOsCounts[os]"
                class="osbtn" :class="{ on: managedOs === os }"
                @click="managedOs = managedOs === os ? '' : os">{{ os }} {{ managedOsCounts[os] }}</button>
      </span>
      <input v-model="managedKw" class="mkw" type="search" placeholder="搜尋 IP／主機名稱／序號" />
    </div>
    <div v-if="!selected && pq && pq.total > 0" class="pqbar" data-test="collect-all-progress">
      <span v-if="pq.running" class="dots"><i /><i /><i /></span>
      <b>收集全部 {{ pq.done }}／{{ pq.total }}</b>
      <template v-if="pq.running && pq.current">
        　目前：{{ serialHost[pq.current] || pq.current }}
        <template v-if="pq.current_progress?.label">（{{ pq.current_progress.label }} {{ pq.current_progress.step }}／{{ pq.current_progress.total }}）</template>
      </template>
      <template v-else-if="!pq.running">　已跑完 {{ pq.finished_at }}</template>
      <b v-if="pq.error" class="pq-err">　隊伍中斷：{{ pq.error }}</b>
      <span class="muted small">　成功
        <a class="dl" href="#" @click.prevent="setTab('collect')">{{ pq.ok }}</a>
        ／有項目沒收到 <a class="dl" href="#" @click.prevent="setTab('collect')">{{ pq.failed }}</a>
        ／已在收、跑完補一輪 <a class="dl" href="#" @click.prevent="setTab('collect')">{{ pq.deferred }}</a>
      </span>
      <InfoNote>數字點下去到「開始收集」頁簽的紀錄，每一台、每一樣的成敗與原因都在那裡。</InfoNote>
    </div>
    <div v-if="!selected && !managedAll.length" class="empty">還沒有已納管的主機。</div>
    <div v-if="!selected && managedAll.length" class="tbl-wrap">
      <table>
        <thead><tr>
          <th class="ck"><input type="checkbox" :checked="allManagedSelected" @change="toggleAllManaged" /></th>
          <SortTh k="no" :active="mKey" :dir="mDir" @sort="mToggle">#</SortTh>
          <SortTh k="ip" :active="mKey" :dir="mDir" @sort="mToggle">IP</SortTh>
          <SortTh k="hostname" :active="mKey" :dir="mDir" @sort="mToggle">主機名稱</SortTh>
          <SortTh k="os_type" :active="mKey" :dir="mDir" @sort="mToggle">OS</SortTh>
          <SortTh k="asset_serial" :active="mKey" :dir="mDir" @sort="mToggle">資產序號</SortTh>
          <SortTh k="tags_sort" :active="mKey" :dir="mDir" @sort="mToggle">標籤</SortTh>
          <SortTh k="collect_checked_at" :active="mKey" :dir="mDir" @sort="mToggle">最後確認收得到</SortTh>
          <SortTh k="last_fail_at" :active="mKey" :dir="mDir" @sort="mToggle">上次失敗</SortTh>
          <th>操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="m in mSorted" :key="mRowKey(m)">
            <td class="ck"><input type="checkbox" :disabled="!m.ip" :checked="!!m.ip && managedSelected.has(m.ip)" @change="toggleManaged(m.ip)" /></td>
            <td class="mono small dim">{{ m.no }}</td>
            <td class="mono">{{ m.ip || '—' }}</td>
            <td>{{ m.hostname || '—' }}</td>
            <td class="small">{{ m.os_type || '—' }}
              <span v-if="m.os_override" class="ovtag"
                    :title="`人工指定：${m.os_override.auto_at_time ? `機器原本判「${m.os_override.auto_at_time}」，` : ''}${m.os_override.by ?? '不詳'} 於 ${m.os_override.at}${m.os_override.reason ? `　原因：${m.os_override.reason}` : ''}`">人工指定</span>
            </td>
            <td class="mono small">
              <NuxtLink v-if="m.asset_serial" class="dl" :to="`/assets/${m.asset_serial}`">{{ m.asset_serial }}</NuxtLink>
              <template v-else>—</template>
            </td>
            <td>
              <button v-for="t in m.tags" :key="t" type="button" class="tagchip" :class="{ on: mTag === t }"
                      :title="tagTitle(m.asset_serial, t)" @click="mTag = mTag === t ? '' : t">{{ t }}</button>
              <span v-if="!m.tags?.length" class="muted small">—</span>
            </td>
            <td class="small">{{ m.collect_checked_at || '—' }}</td>
            <td>
              <span v-if="m.last_fail_at" class="failtag" :title="failTitle({ at: m.last_fail_at, short: m.last_fail_short, message: m.last_fail_msg })">上次失敗 {{ failWhen(m.last_fail_at) }}：{{ m.last_fail_short }}</span>
              <span v-else class="muted small">—</span>
            </td>
            <td class="ops">
              <span v-if="m.state === '已納管但失聯'" class="pill warn" title="連上過，但最近 7 天收集帳號沒連上——不算進已納管台數">已納管但失聯</span>
              <span v-else class="pill ok">✓ 已納管</span>
              <span v-if="m.root_key" class="pill warn" title="這台沒有可用的 sudo（或是 AIX），需要 root 的項目用專用的 root 收集金鑰收（決策 ROOTKEY1，暫時，待收斂）">root 金鑰（暫時）</span>
              <!-- Windows 納管的那幾台走 Windows 那套（不要密碼、目標機不動），不開 Linux 的視窗 -->
              <template v-if="m.ip && isWinManaged(m.ip)">
                <span class="pill" :class="managedWin[m.ip]?.is_admin === 0 ? 'warn' : 'ok'"
                      title="Windows 納管：最近一次納管時收集帳號是不是本機管理員">本機管理員：{{ adminText(managedWin[m.ip]?.is_admin) }}</span>
                <button class="btn small ghost" type="button"
                        @click="openWinOnboard([{ ip: m.ip, hostname: m.hostname, opted_in: true }])">↻ 重新納管</button>
                <button class="btn small ghost danger-t" type="button" @click="winRevoke = { ip: m.ip, hostname: m.hostname }">取消納管</button>
              </template>
              <template v-else-if="m.ip">
                <button class="btn small ghost" type="button"
                        @click="openOnboard({ ip: m.ip, os_guess: null } as any)">↻ 重新納管</button>
                <button class="btn small ghost danger-t" type="button" @click="revokeIp = m.ip">取消納管</button>
              </template>
              <NuxtLink v-if="m.asset_serial" class="btn small ghost" :to="`/assets/${m.asset_serial}?edit=1`">✎ 編輯</NuxtLink>
              <!-- 改分類（2026-10-18）：同待納管表那顆，同一個視窗、同一張人工指定表。
                   只改系統這邊的分類（顯示與統計），不動納管白名單、不建帳號 -->
              <button class="btn small ghost" type="button"
                      :disabled="!((m.hostname && m.ip) || m.asset_serial)"
                      :title="((m.hostname && m.ip) || m.asset_serial) ? '這台被分到哪一類不對？改成對的分類（重匯 CIA 不會蓋回去）' : '沒有主機名也沒有序號，認不出是哪一台，不能改'"
                      @click="openMoveManaged(m)">改分類</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    </div>

    <!-- ===== 搬到別的頁簽（2026-10-16）===== -->
    <div v-if="moveRows.length" class="modal-mask" @click.self="moveRows = []">
      <div class="modal">
        <div class="mhead">
          改分類 — {{ moveRows.length === 1 ? moveRows[0].ip : `勾選的 ${moveRows.length} 台` }}
          <InfoNote>分類是系統依資產登記的「作業系統」欄與設備機型自動判的，總會有少數判錯——例如一般伺服器的作業系統欄被登記成「儲存設備」，就會被歸到儲存設備頁簽。這裡改的是<b>系統這一邊的分類</b>，<b>不會去動資產清冊的原始資料</b>，所以重匯 CIA 也不會把你的指定沖掉。<br><br>搬成「SSH 收集」<b>只影響顯示與試連</b>（唯讀登入試一次）——系統<b>不會</b>自己跑去那台建帳號；要建帳號仍然得由人按「⚡ 一鍵納管」。</InfoNote>
        </div>
        <p v-if="moveRows.length === 1" class="mhint">
          目前在「{{ tabLabel(moveRows[0].tab) }}」頁簽。
          <template v-if="moveRows[0].os_override">這是人工指定的（{{ moveRows[0].os_override!.by ?? '不詳' }} 於 {{ moveRows[0].os_override!.at }}）。</template>
          <template v-else>{{ moveRows[0].message }}</template>
        </p>
        <p v-else class="mhint">
          {{ moveRows.map((r) => r.ip).join('、') }}
        </p>
        <label class="fl">搬到哪個頁簽
          <select v-model="moveTab">
            <option value="">（請選）</option>
            <option v-for="c in moveTargetChoices" :key="c.value" :value="c.value" :disabled="c.disabled">
              {{ c.label }}{{ c.note }}
            </option>
          </select>
        </label>
        <p v-if="catTabsError" class="mhint" style="color: var(--bad)">{{ catTabsError }}</p>
        <p v-if="moveWhyNot.length" class="muted small">
          <InfoNote><template v-for="w in moveWhyNot" :key="w">{{ w }}<br></template></InfoNote>
        </p>
        <label class="fl">為什麼（選填，會跟著紀錄留下來）
          <textarea v-model="moveReason" rows="2"
                    placeholder="例：這台是一般伺服器，資產的作業系統欄登記錯了" />
        </label>
        <div class="macts">
          <button class="btn primary" :disabled="!moveTab || moveBusy" @click="submitMove(false)">
            {{ moveBusy ? '搬移中…' : '搬過去' }}
          </button>
          <!-- 取消指定是軟刪除，紀錄不會消失 -->
          <button v-if="moveRows.some((r) => r.os_override)" class="btn ghost"
                  :disabled="moveBusy" @click="submitMove(true)">還原成機器判定</button>
          <button class="btn ghost" :disabled="moveBusy" @click="moveRows = []">取消</button>
        </div>
      </div>
    </div>

    <!-- Push Agent 安裝包：交給「要請人裝」的那位 -->
    <div v-if="pkg" class="modal-mask" @click.self="pkg = null">
      <div class="modal wide">
        <div class="mhead">Push Agent 安裝包 — {{ pkg.ip }} <InfoNote>這台系統進不去，改由主機自己每天回報，<b>collector 完全不主動連進去</b>——防火牆只需要開「主機 → collector」單向。</InfoNote></div>
        <p v-if="pkg.created_asset" class="mhint">已為這台建立資產 <code>{{ pkg.asset_serial }}</code>，業務欄位請稍後補上。</p>
        <ol class="steps">
          <li>複製下方腳本，存成 <code>/tmp/bootstrap_watcher.sh</code> 交給該機管理者。</li>
          <li>請他以 root 執行一行：<code>{{ pkg.install_command }}</code></li>
          <li>裝完當天起每天自動回報到 <code>{{ pkg.collector_url }}</code>，之後不需要再用 root 登入這台。</li>
        </ol>
        <textarea class="cmdbox" readonly :value="pkg.files['bootstrap_watcher.sh']"
                  @focus="($event.target as HTMLTextAreaElement).select()" />
        <div class="macts">
          <button class="btn primary" @click="copyPkgScript">{{ pkgCopied ? '✓ 已複製' : '複製安裝腳本' }}</button>
          <button class="btn ghost" @click="pkg = null">關閉</button>
        </div>
      </div>
    </div>

    <!-- Windows 納管／重新納管、取消納管（2026-10-22 勾選才收） -->
    <WinOnboardModal v-if="winOnboardRows" :rows="winOnboardRows"
                     @done="onWinOnboarded" @close="winOnboardRows = null" />
    <WinRevokeModal v-if="winRevoke" :ip="winRevoke.ip" :hostname="winRevoke.hostname"
                    @done="onWinRevoked" @close="winRevoke = null" />

    <!-- 取消納管：共用元件（跟資產詳細頁同一個） -->
    <RevokeModal v-if="revokeIp" :ip="revokeIp" platform="linux"
                 @done="onRevoked" @close="revokeIp = null" />

    <!-- 一鍵納管：共用元件 -->
    <OnboardModal v-if="onboardHost" :ip="onboardHost.ip" :os-guess="onboardHost.os_guess"
                  @done="onOnboarded" @tagged="onTagged" @close="onboardHost = null" />

    <!-- 勾選多台一次納管：每台走跟一鍵納管同一支 API -->
    <BatchOnboardModal v-if="batchOnboardIps" :ips="batchOnboardIps"
                       @done="onBatchOnboarded" @tagged="onTagged" @close="batchOnboardIps = null" />

    <!-- 納管前資訊（v1.468.7）：只讀，不連線、不登入 -->
    <PreinfoPanel v-if="preinfoIp" :ip="preinfoIp" @close="preinfoIp = null"
                  @pick-tag="(t: string) => { dTag = t; preinfoIp = null }"
                  @precheck="(ip: string) => { precheckIps = [ip] }" />
    <!-- 預檢（只看不動，v1.468.9）：唯讀登入、同一份平台白名單；不建帳號 -->
    <PrecheckModal v-if="precheckIps" :ips="precheckIps" @close="precheckIps = null" />
    <!-- 加標籤（v1.468.6）：同一份機器標籤；沒登記的列回「未登記，無法加標籤」 -->
    <TagBatchModal v-if="tagRows" :rows="tagRows" @done="onTagged" @close="tagRows = null; tagSelected = new Set()" />

    <!-- 批次試密碼：只探測哪組密碼通，不建帳號、不跑腳本 -->
    <PasteOnboardModal v-if="pasteIps" :ips="pasteIps" @close="pasteIps = null" />
    <BatchProbeModal v-if="showBatchProbe" :ips="[...probeSelected]"
                     @close="showBatchProbe = false" />

    <!-- 納入管理表單 -->
    <div v-if="selected && meta" class="form-card">
      <div class="form-head">
        <div><b>納入管理 — {{ selected.ip }}</b><span class="muted"> {{ selected.hostname ? '（' + selected.hostname + '）' : '' }}</span></div>
        <button class="btn ghost small" @click="selected = null">← 回清單</button>
      </div>

      <template v-for="cat in meta.categories" :key="cat.key">
        <div class="sec-head">
          <span class="t">{{ cat.label }}</span>
        </div>
        <div class="grid">
          <div v-for="f in fieldsOf(cat.key)" :key="f.key" class="f">
            <label :title="f.help || undefined">
              {{ f.label }}<span v-if="f.required" class="req">必填</span>
              <span class="layer" :class="f.layer">{{ f.layer === 'tech' ? '自動' : '手填' }}</span>
            </label>
            <!-- tech 有值→唯讀灰底 -->
            <div v-if="f.layer === 'tech' && form[f.key]" class="ro">{{ form[f.key] }}</div>
            <!-- tech 沒值→可手動補 -->
            <input v-else-if="f.layer === 'tech'" v-model="form[f.key]" class="in" placeholder="未取得（待 facts 收集，可手動補）" />
            <!-- biz select -->
            <select v-else-if="f.options" v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }">
              <option value="">—</option>
              <option v-for="o in f.options" :key="o" :value="o">{{ o }}</option>
            </select>
            <!-- biz input -->
            <input v-else v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }" />
          </div>
        </div>
      </template>

      <div class="actions">
        <button class="btn" :disabled="saving" @click="submitAdopt">{{ saving ? '登記中…' : '納入管理' }}</button>
        <button class="btn ghost" @click="selected = null">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fsum { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; margin: 0 0 8px; }
.fchip { font-size: 12px; padding: 1px 9px; border-radius: 10px; background: rgba(0,0,0,.05);
         color: var(--ink-soft); white-space: nowrap; }
.fchip.hot { background: var(--warn-soft); color: var(--warn-text); }
button.fchip { border: 1px solid transparent; cursor: pointer; font: inherit; font-size: 12px; }
.fchip.on { border-color: var(--accent, #0c6b37); background: var(--accent-soft, rgba(12,107,55,.12)); color: var(--ink); }
.lastsum { font-size: 13px; margin: 0 0 8px; padding: 7px 12px; border-radius: 6px;
           background: rgba(0,0,0,.04); }
.lastsum .ok-t { color: var(--brand-dark); }
.lastsum .warn-t { color: var(--warn-text); }
.lastres { max-width: 230px; }
.lastres .resmsg { font-size: 11px; color: var(--muted); margin-top: 2px;
                   overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sm { font-size: 11.5px; }
/* 網段挑選：橫排一行＋多欄清單（2026-09-16 使用者要求縮減畫面高度） */
.sp-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sp-bar .spacer { flex: 1; }
.sp-bar .sp-lbl { color: var(--muted); font-size: 12.5px; }
.sp-bar .sp-ex { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.sp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 2px 12px;
           max-height: 190px; overflow: auto; padding: 6px 8px; border: 1px solid var(--border);
           border-radius: 6px; }
.sp-cell { display: flex; align-items: baseline; gap: 6px; font-size: 12px; padding: 1px 0;
           cursor: pointer; min-width: 0; }
.sp-cell .dim { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sp-cell.ex { opacity: .75; }
.exdot { font-size: 10px; padding: 0 5px; border-radius: 7px; background: var(--warn-soft);
         color: var(--warn-text); white-space: nowrap; }
.gapin { width: 56px; padding: 2px 6px; border: 1px solid var(--border-strong); border-radius: 4px;
         background: var(--card); color: var(--ink); font-size: 12px; }
.d-searchbar { display: flex; align-items: center; gap: 8px; margin: 10px 0 8px; }
.d-searchbar .d-search { margin-left: 0; }
.d-searchbar .fb-n { color: var(--brand-dark); font-weight: 600; font-size: 12px; }
.d-searchbar { flex-wrap: wrap; }
.d-hide { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink-soft); cursor: pointer; }
.d-res { display: inline-flex; gap: 6px; flex-wrap: wrap; }
/* 「上次失敗 10/18 20:50：沒有 sudo」——點了＝只看上次失敗的；滑過看得到完整訊息 */
.d-otherhits { font-size: 12px; color: var(--ink-soft); margin: 6px 0; }
.d-otherhits.strong { font-size: 13.5px; font-weight: 700; color: var(--ink); background: var(--mint);
  border: 1px solid var(--border-strong); border-radius: 6px; padding: 8px 12px; }
.infobtn { font-family: inherit; font-size: 12px; margin-left: 4px; padding: 0 4px; border: none; background: none;
  color: var(--brand); cursor: pointer; }
.tagchip { font-family: inherit; font-size: 10.5px; padding: 1px 7px; margin: 1px 3px 1px 0; border-radius: 999px;
  border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink-soft); cursor: pointer; white-space: nowrap; }
.tagchip.on { border-color: var(--brand); background: var(--mint); color: var(--ink); font-weight: 700; }
.staletag { font-family: inherit; font-size: 10.5px; font-weight: 700; padding: 0 5px; margin-left: 4px;
  border-radius: 3px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink-aux);
  cursor: pointer; }
.failtag { display: inline-block; font-family: inherit; font-size: 10.5px; font-weight: 700; white-space: nowrap;
  padding: 1px 6px; border-radius: 3px; border: 1px solid var(--bad); background: var(--bad-soft);
  color: var(--bad); cursor: pointer; }
button.pill-btn { font-family: inherit; border: none; cursor: pointer; }
.routebar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 10px 0 0; }
.rchip { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft);
         border-radius: 999px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
.rchip:hover { border-color: var(--border-strong); }
.rchip.on { border-color: var(--brand); background: var(--mint); color: var(--ink); font-weight: 600; }
/* 「（含 Windows N）」是附註不是主要數字：比頁簽本體淡一階，不跟計數搶眼 */
.rchip-sub { font-size: 11px; font-weight: 400; color: var(--muted); }
.tabtag { font-size: 11px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border);
          background: var(--sub); color: var(--ink-soft); cursor: pointer; white-space: nowrap; }
.tabtag:hover { border-color: var(--brand); color: var(--brand-dark); }
.d-assethint { margin: 6px 0; padding: 6px 10px; border-radius: 8px; background: var(--mint); color: var(--ink);
               font-size: 13px; }
.rchip.on .rchip-sub { color: var(--ink-soft); }
/* 用警示色（不是品牌綠）：這是「別照頁簽直覺做」的提醒，要看得見 */
.wintag { display: inline-block; margin-left: 6px; font-size: 10.5px; font-weight: 700;
          padding: 1px 6px; border-radius: 3px; white-space: nowrap;
          background: var(--warn-soft); color: var(--warn-text); cursor: help; }
.hidnote { margin: 8px 0 0; }
/* 「判斷」欄（2026-10-19）：確定＝品牌色、推測＝警示色、不明＝灰 */
.kindtag { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; cursor: help; font-size: 12.5px; }
.kindtag .kconf { font-size: 10.5px; font-weight: 700; padding: 0 5px; border-radius: 3px; }
.kc-confirmed .kconf { background: var(--mint); color: var(--brand-dark); }
.kc-likely .kconf { background: var(--warn-soft); color: var(--warn-text); }
.kc-unknown .kconf { background: var(--sub); color: var(--muted); }
/* 「該走哪條收集路徑」：跟探測到的 route 用同一個欄位，所以要看得出是另一種東西 */
.route-alt { color: var(--brand-dark); font-weight: 600; }
.why { margin-left: 6px; font-size: 10.5px; color: var(--muted); cursor: help;
       text-decoration: underline dotted; }
/* 人工指定：用品牌色（這是「人改過」的事實，不是警示）。cursor: help 提示滑過有說明 */
.ovtag { display: inline-block; margin-left: 6px; font-size: 10.5px; font-weight: 700;
         padding: 1px 6px; border-radius: 3px; white-space: nowrap;
         background: var(--mint); color: var(--brand-dark); cursor: help; }
/* Windows 納管：收集帳號不是本機管理員（服務／IIS／排程可能讀不完整） */
.ovtag.win-noadm { background: var(--warn-soft); color: var(--warn-text); }
/* 搬移對話框的欄位 */
.modal .fl { display: block; font-size: 12px; color: var(--ink-soft); margin-bottom: 14px; }
.modal .fl select, .modal .fl textarea { display: block; width: 100%; margin-top: 6px;
  padding: 7px 9px; font: inherit; font-size: 12.5px; border: 1px solid var(--border-strong);
  border-radius: 6px; background: var(--card); color: var(--ink); }
.modal .fl textarea { resize: vertical; }

/* 批次試密碼的勾選列：沒勾東西時安靜，勾了才亮起來 */
.tabs { display: flex; align-items: center; gap: 4px; margin: 0 0 14px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab { padding: 8px 16px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tab:hover { color: var(--ink); }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.tab-sub { font-size: 12px; font-weight: 400; color: var(--ink-aux); }
.tab-busy { font-size: 12px; color: var(--warn-text); cursor: pointer; text-decoration: underline dotted; }
.tab-busy.stale { color: var(--bad); }
.run-scope { display: block; margin: 6px 0 0; }
.runsum { margin: 10px 0; padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.runsum a.on { font-weight: 700; }
.runsum a.bad { color: var(--bad); }
.runsum-tbl { width: 100%; font-size: 13px; margin-top: 6px; }
.pqpanel { margin: 10px 0; }
.pq-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.d-info { margin: 10px 0; padding: 9px 12px; border-radius: 6px; background: var(--mint); display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.cov { margin: 12px 0; }
.cov-head { background: none; border: 0; padding: 4px 0; font-weight: 600; color: var(--ink); cursor: pointer; }
.cov-never-txt { color: var(--bad); }
.cov-body { margin-top: 6px; }
.cov-filter { display: flex; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
.cov-tbl { width: 100%; font-size: 13px; }
.cov-tbl tr.cov-never td { color: var(--bad); }
.cov-detail { margin-top: 10px; padding: 8px; border: 1px solid var(--border); border-radius: 8px; max-height: 420px; overflow: auto; }
.covbadge { font-size: 11px; color: var(--ink-aux); margin-left: 4px; white-space: nowrap; }
.covbadge.never { color: var(--bad); }
.probebar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 8px 12px; margin-bottom: 8px; border: 1px solid var(--border-strong);
  border-radius: 8px; background: var(--card); }
.probebar.active { border-color: var(--brand); background: var(--mint); }
.pq-err { color: var(--bad); }
.pqbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 6px 12px;
  margin-bottom: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); }
.pb-all { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-soft); cursor: pointer; }
/* 勾選用途的模式切換：看得見「現在勾的是要做什麼」，不靠「你在哪個頁簽」去猜 */
.pickmode { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.pickmode .pm-l { font-size: 12px; color: var(--ink-soft); }
.pm { font-family: inherit; font-size: 12px; padding: 5px 12px; cursor: pointer;
  border: 1px solid var(--border-strong); border-radius: 999px;
  background: var(--card); color: var(--ink-soft); }
.pm.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); font-weight: 600; }
.pm:disabled { opacity: .45; cursor: not-allowed; }
td.ck, th.ck { width: 28px; text-align: center; }
.mkw { margin-left: auto; min-width: 240px; font-family: inherit; font-size: 12px; padding: 6px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.osfilter { display: inline-flex; gap: 6px; flex-wrap: wrap; }
.osbtn { font-family: inherit; font-size: 12px; padding: 5px 10px; cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 4px; }
.osbtn.on { border-color: var(--brand); background: var(--mint); color: var(--brand); font-weight: 600; }
.ops .btn, .ops .pill { text-decoration: none; }
.btn.small.ghost.danger-t { color: var(--bad); border-color: var(--bad); }

.working { display: flex; gap: 10px; align-items: flex-start; margin-top: 10px;
  padding: 9px 11px; border: 1px solid var(--border-strong); background: var(--mint); }
.w-txt { font-size: 11px; color: var(--muted); line-height: 1.7; }
.w-txt b { color: var(--ink-soft); }
.dots { display: inline-flex; gap: 3px; padding-top: 5px; flex-shrink: 0; }
.dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--brand);
  animation: blink 1.2s infinite ease-in-out; }
.dots i:nth-child(2) { animation-delay: .2s; }
.dots i:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }

.link-btn { background: none; border: none; color: var(--brand-dark); font-family: inherit;
  font-size: 11.5px; cursor: pointer; padding: 0; text-decoration: underline; }
.lastfail { border: 1px solid var(--bad); background: var(--bad-soft); padding: 10px 12px; }
.lf-head { font-size: 12px; font-weight: 700; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.lf-msg { font-size: 12px; color: var(--ink); line-height: 1.6; margin: 6px 0; }
.outbox { font-family: ui-monospace, Consolas, monospace; font-size: 10.5px; line-height: 1.5;
  background: var(--card); border: 1px solid var(--border-strong); padding: 8px;
  max-height: 240px; overflow: auto; white-space: pre-wrap; word-break: break-all; margin: 6px 0; }

.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong); padding: 8px 14px;
  font-size: 12.5px; color: var(--ink-soft); display: flex; gap: 8px; align-items: center; margin-bottom: 14px; }
.breadcrumb-bar .pin, .breadcrumb-bar b { color: var(--brand-dark); }
.page-title { font-size: 17px; font-weight: 700; margin: 0 0 4px; }
.page-sub { font-size: 12px; color: var(--muted); margin: 0 0 16px; }
.empty { border: 1px solid var(--border); background: var(--card); padding: 30px; text-align: center; color: var(--muted); font-size: 13px; }
.hint { font-size: 11.5px; color: var(--muted); margin: 0 0 10px; line-height: 1.5; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .75; margin: 2px 0 0; }
.ops { display: flex; gap: 6px; white-space: nowrap; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.small.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 380px; max-width: 92vw; background: var(--card-solid, #12211c);
  border: 1px solid var(--border-strong); border-radius: 12px; padding: 22px 24px; }
.modal .mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand-dark); }
.modal .mhint { font-size: 11.5px; color: var(--muted); line-height: 1.6; margin: 0 0 16px; }
.modal .mhint code { color: var(--brand-dark); }
.modal .mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
.modal .mf input, .modal .mf select { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.modal .mguess { font-size: 11px; color: var(--brand-dark); }
.modal .macts { display: flex; gap: 10px; margin-top: 6px; }
.hint b { color: var(--ink-soft); }
.fp { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.fp .tag { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 3px; white-space: nowrap; }
.fp .tag.vm { background: var(--warn-soft); color: var(--warn-text); }
.fp .tag.vendor { background: var(--mint-deep); color: var(--brand-dark); }
.fp .tag.os { background: var(--good-soft); color: var(--brand-dark); }
.fp-sub { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; margin-top: 4px; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
.d-scroll { max-height: calc(100vh - 240px); overflow: auto; }
.d-scroll thead th { position: sticky; top: 0; z-index: 2; }
.d-scroll th.ops-h, .d-scroll td.ops { position: sticky; right: 0; background: var(--card, #fff);
  box-shadow: -2px 0 4px rgba(0, 0, 0, .06); z-index: 1; }
.d-scroll thead th.ops-h { z-index: 3; background: var(--mint); }
/* .ops 平常是 flex；釘住時要維持表格儲存格，不然白底只蓋到按鈕那一小塊，後面欄位的字會從下面露出來 */
.d-scroll td.ops { display: table-cell; vertical-align: middle; }
.d-scroll td.ops > * { margin-right: 6px; vertical-align: middle; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 560px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.form-card { border: 1px solid var(--border); background: var(--card); padding: 18px 20px; }
.form-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.sec-head { margin: 16px 0 8px; }
.sec-head .t { font-size: 12px; font-weight: 700; color: var(--brand-dark); text-transform: uppercase; letter-spacing: .04em; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 14px; }
.f label { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
.f .req { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 2px; background: var(--bad); color: var(--ink); }
.f .in.missing { border: 2px solid var(--bad); background: var(--bad-soft); }
.f .layer { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 2px; }
.f .layer.tech { background: var(--good-soft); color: var(--brand-dark); }
.f .layer.biz { background: var(--warn-soft); color: var(--warn-text); }
.f .ro { font-size: 13px; font-weight: 700; background: var(--mint); border: 1px solid var(--border); padding: 7px 10px; }
.f .in { width: 100%; font-family: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.actions { display: flex; gap: 10px; margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px; border: none; background: var(--brand); color: #fff; cursor: pointer; }
.btn:hover:not(:disabled) { background: var(--brand-dark); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.small { padding: 5px 10px; font-size: 11.5px; }

/* ===== 收集入口 ===== */
.card { border: 1px solid var(--border); background: var(--card); padding: 16px 18px; margin-bottom: 18px; }
.card .sec-head { display: flex; justify-content: space-between; align-items: baseline; margin: 0 0 10px; }
.small { font-size: 11px; }
.dl { color: var(--brand-dark); text-decoration: none; border-bottom: 1px dotted var(--brand); }
.dl:hover { color: var(--brand-dark); }
.dl.port { margin-right: 6px; }
.segpick { border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; margin-bottom: 10px; }
.sp-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.sp-head .lnk { background: none; border: none; color: var(--brand-dark); text-decoration: underline; cursor: pointer; font-size: 12px; }
.sp-body { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.sp-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sp-lbl { font-size: 12px; color: var(--ink-soft); min-width: 30px; }
.sp-sel { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 5px;
          background: var(--card); color: var(--ink); }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 12px; border: 1px solid var(--border-strong);
        background: var(--card); color: var(--ink); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); font-weight: 600; }
.sp-list { display: flex; flex-direction: column; gap: 3px; max-height: 210px; overflow-y: auto;
           border-top: 1px solid var(--border); padding-top: 6px; }
.sp-item { display: flex; align-items: center; gap: 7px; font-size: 13px; padding: 2px 0; }
.sp-item.all { font-weight: 600; }
.sp-item.ex { opacity: .65; }
.sp-item .exmark { font-size: 11px; color: var(--warn-text); }
.sp-ex { font-size: 12px; color: var(--ink-soft); display: flex; align-items: center; gap: 5px; }
.sp-acts { display: flex; align-items: center; gap: 8px; }
.targets { width: 100%; min-height: 84px; font-family: ui-monospace, Consolas, monospace;
  font-size: 12px; line-height: 1.6; padding: 9px 11px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); resize: vertical; }
.targets:disabled { opacity: .6; }
.d-err { margin: 10px 0; padding: 9px 12px; border-radius: 6px; background: var(--warn-soft);
         color: var(--warn-text); border: 1px solid rgba(176,106,0,.3); display: flex;
         flex-direction: column; gap: 6px; align-items: flex-start; }
.d-allnone { margin: 8px 0; padding: 8px 12px; border-radius: 6px; font-size: 13px;
             background: rgba(0,128,106,.06); border: 1px solid rgba(0,128,106,.18); }
.card .actions { display: flex; gap: 12px; align-items: center; margin-top: 12px; padding-top: 0; border-top: none; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px; }
.tile { border: 1px solid var(--border); background: var(--mint); padding: 10px 12px; }
.tile.ok { border-color: var(--good); }
.tile.warn { border-color: var(--warn); }
.tile .t-num { font-size: 22px; font-weight: 700; line-height: 1.1; }
.tile.ok .t-num { color: var(--brand-dark); }
.tile.warn .t-num { color: var(--warn-text); }
.tile .t-lbl { font-size: 11px; color: var(--muted); margin-top: 2px; }
/* 點得下去：游標、hover 都要讓人看得出這不是純顯示 */
.tile.clk { cursor: pointer; user-select: none; transition: border-color .12s, box-shadow .12s; }
.tile.clk:hover { border-color: var(--brand); }
.tile.clk:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
/* 選取狀態要「一眼看得出來」——粗框＋光暈，不只是換底色 */
.tile.clk.sel { border-color: var(--brand); border-width: 2px; padding: 9px 11px;
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--brand) 22%, transparent); }
.tile.clk.sel .t-lbl { color: var(--ink); font-weight: 600; }
/* 現在在看什麼：就在表格正上方，寫明篩選＋筆數＋一鍵清除 */
.filterbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 10px;
  padding: 7px 10px; border: 1px solid var(--brand); background: var(--mint); font-size: 12.5px; }
.filterbar .fb-l { color: var(--muted); font-size: 11.5px; }
.filterbar .fb-v { font-weight: 700; color: var(--ink); }
.filterbar .fb-n { color: var(--brand-dark); font-weight: 600; }
.filterbar .fb-x { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft);
  padding: 2px 9px; font-size: 11.5px; cursor: pointer; }
.filterbar .fb-x:hover { border-color: var(--brand); color: var(--ink); }
.d-empty { text-align: center; color: var(--muted); padding: 18px 8px; }
/* 多一欄「資產名稱」之後整張表會變寬，所以這兩欄給上限、超過就截（滑過看全名）。
   「下一步」同時從攤開改成收合，整體寬度比原本還窄，桌面不會橫向爆出去。 */
.hname, .aname { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pill { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; white-space: nowrap; }
.pill.ok { background: var(--good-soft); color: var(--brand-dark); }
.pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
.pill.off { background: var(--mint-deep); color: var(--muted); }
/* 收合後這一欄只放一句短的，所以不再需要 260px 的保底寬度——表格掃得動了 */
.msg { font-size: 11.5px; color: var(--ink-soft); line-height: 1.5; min-width: 150px; }
.msg-line { display: flex; align-items: baseline; gap: 4px; flex-wrap: wrap; }
.msg-x { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft);
  padding: 0 6px; font-size: 11px; line-height: 1.5; cursor: pointer; flex: none; }
.msg-x:hover { border-color: var(--brand); color: var(--ink); }
/* 展開的完整說明佔整列，所以讀得順；欄寬不受影響（見樣板 tr.msg-row 的註解） */
.msg-row > td { padding: 0 8px 8px; }
.msg-full { padding: 8px 10px; border-left: 3px solid var(--brand);
  background: var(--mint); color: var(--ink); font-size: 12px; line-height: 1.7;
  white-space: pre-wrap; word-break: break-word; }
.msg-full .mf-ip { color: var(--muted); margin-right: 8px; }
.modal.wide { width: 620px; }
.steps { font-size: 12px; color: var(--ink-soft); line-height: 1.8; margin: 0 0 12px; padding-left: 20px; }
.steps code { font-family: ui-monospace, Consolas, monospace; color: var(--brand-dark); }
.cmdbox { width: 100%; height: 180px; font-family: ui-monospace, Consolas, monospace;
  font-size: 10px; line-height: 1.4; padding: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); border-radius: 6px; resize: vertical; }
.ahint { font-size: 10.5px; color: var(--muted); }
.weekly-norange { margin: 0 0 10px; padding: 6px 10px; border: 1px solid var(--bad); background: var(--bad-soft);
  color: var(--bad); font-size: 12.5px; font-weight: 700; border-radius: 6px; }
.weekly-norange a { color: var(--bad); text-decoration: underline; }
</style>
