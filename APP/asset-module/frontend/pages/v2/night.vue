<script setup lang="ts">
// 夜間速查（Q98，2.010.x）：半夜被叫醒，迷迷糊糊也能用——打一個 IP／主機名／資產序號／系統代號，或直接貼整段告警文字，
// 一頁看「這是誰、找誰」「現在的狀態」「它停掉會連累誰」。全部唯讀，沒有任何會改資料的按鈕。
// 資料（都是既有 API，沒有新端點）：
//   /api/oncall            解析輸入＋資料庫裡的一切（身分、系統、位置、負責人、影響摘要）——立刻出
//   /api/contacts/card     值班電話（各系統登記的）——跟上面同時送，回來就補進 ① 的電話區
//   /api/health-check/run  即時檢測（SSH／WinRM 唯讀），只給 ② 用；VMware 設備（ESXi／vCenter）絕不呼叫（天條）
//   /api/blast/resolve     查無這個 IP 時，推導它落在哪個網段／機房
//   /api/night/settings    「自動即時檢測」開關（W221-N1，預設開）；關時查主機只讀資料庫，要按「立即檢測」才連線
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）

interface Sys { api_id: string; name: string | null; ap_department: string | null; ap_owner: string | null; severity: string; machines: number }
interface Cand { key: string; asset_serial: string; hostname: string | null; ip: string | null; api_id: string | null; os: string | null; asset_status: string | null; retired: boolean }
interface Contact { name: string; phone: string; proxy: string | null; proxy_phone: string | null; dept: string | null; job: string | null }
interface Card {
  key: string
  what: { hostname: string | null; ips: string[]; serials: string[]; os: string | null; environment: string; is_vm: boolean; asset_status: string | null; retired: boolean; vmware: string | null }
  systems: Sys[]
  where: { room: string; rack: string | null; u_start: number | null; u_end: number | null; esxi: { label: string }[] }
  who: { owner: string; custodian: string; ap_user: string; ap_owner: string; ap_department: string; contacts: Contact[] }
  impact: { ok: boolean; node_id?: string; reason?: string; systems?: number; major?: number; host_count?: number; system_list?: { api_id: string; name: string | null; severity: string }[]; deps_available?: boolean }
}
interface Out { mode: 'machine' | 'system' | 'candidates' | 'none'; q: string; matched_by?: string; reason?: string; card?: Card; system?: Sys; candidates?: Cand[]; total?: number }
interface Oncall { api_id: string; name: string | null; phone: string | null; note: string | null; updated_at?: string | null }
interface CCard { people: { owners: { name: string; department?: string }[]; custodians: string[]; ap_users: string[] }; maint: { vendor?: { value: string | null }; phone?: { value: string | null }; contact?: { value: string | null } }; oncall: Oncall[] }

const { apiFetch } = useApi()
import { decideLive, runLiveCheck, fetchPhones, fetchDownImpact } from '~/composables/useNightLive'
const route = useRoute()
const router = useRouter()

// ===== 深色／淺色（預設深色；有存過就照存的；沒存過且手機明講要淺色才用淺色）=====
const THEME_KEY = 'webit-night-theme'
const theme = ref<'dark' | 'light'>('dark')
// 伺服器端先送深色（頁面一出來就是暗的，不閃白）；進到瀏覽器後由 applyTheme 接手改 <html> 的屬性
if (import.meta.server) useHead({ htmlAttrs: { 'data-night': 'dark' } })
function applyTheme() {
  const el = document.documentElement
  if (theme.value === 'dark') el.setAttribute('data-night', 'dark')
  else el.removeAttribute('data-night')
}
onBeforeUnmount(() => { document.documentElement.removeAttribute('data-night') })   // 離開這頁，全站回到白綠
function initTheme() {
  let saved: string | null = null
  try { saved = localStorage.getItem(THEME_KEY) } catch { /* 瀏覽器不讓讀：照預設深色 */ }
  if (saved === 'dark' || saved === 'light') theme.value = saved
  else { try { if (window.matchMedia('(prefers-color-scheme: light)').matches) theme.value = 'light' } catch { /* 讀不到偏好：維持深色 */ } }
  applyTheme()
}
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyTheme()
  try { localStorage.setItem(THEME_KEY, theme.value) } catch { /* 存不了只是下次不記得 */ }
}

// ===== 最近查過 =====
const RECENT_KEY = 'webit-night-recent'
const recent = ref<string[]>([])
function loadRecent() {
  try { const a = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); recent.value = Array.isArray(a) ? a.filter((x: any) => typeof x === 'string').slice(0, 6) : [] } catch { recent.value = [] /* 讀不到就當沒有 */ }
}
function remember(v: string) {
  recent.value = [v, ...recent.value.filter(x => x !== v)].slice(0, 6)
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(recent.value)) } catch { /* 存不了只是下次不記得 */ }
}

// ===== 從輸入抓出要查的東西（整段告警也行）=====
// 單一詞（沒有空白）直接照原樣查；一段文字裡抓到「剛好一個有效的 IP 或系統代號」才自動查；
// 抓到多個、或有無效的（如 300.1.1.1、第五段）就列成可編輯的選項，要點一個才查，不默默拿第一個。
interface Tok { v: string; kind: 'ip' | 'code' | 'bad'; why?: string }
const IPLIKE = /(?<![\d.])\d{1,4}(?:\.\d{1,4}){3,4}(?![\d])/g
const SYSCODE = /(?<![A-Za-z0-9-])[A-Za-z]{1,3}-\d{2,4}(?![A-Za-z0-9-])/g
function checkIp(v: string): string | null {
  const parts = v.split('.')
  if (parts.length !== 4) return '不是 4 段的 IPv4'
  for (const x of parts) {
    if (!/^\d{1,3}$/.test(x) || Number(x) > 255) return `「${x}」超出 0 到 255`
    if (x.length > 1 && x.startsWith('0')) return `「${x}」前導零，寫法不明確`
  }
  return null
}
const IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/
function tokens(text: string): Tok[] {
  const out: Tok[] = []
  const seen = new Set<string>()
  const push = (t: Tok) => { if (!seen.has(t.v)) { seen.add(t.v); out.push(t) } }
  const masked = text.replace(IPLIKE, (m) => { const why = checkIp(m); push(why ? { v: m, kind: 'bad', why } : { v: m, kind: 'ip' }); return ' ' })
  for (const m of masked.matchAll(SYSCODE)) push({ v: m[0], kind: 'code' })
  return out
}

// ===== 狀態 =====
const q = ref('')
const picked = ref('')           // 實際拿去查的值
const picks = ref(false)         // 這一台是從候選清單點選的（用資產序號查，不是用打的字再解析一次）
const autoNote = ref('')
const busy = ref(false)
const err = ref('')
const out = ref<Out | null>(null)
const segHint = ref<{ location?: string; environment?: string; purpose?: string } | null>(null)
const cc = ref<CCard | null>(null)
const ccState = ref<'idle' | 'loading' | 'ok' | 'fail'>('idle')
let seq = 0
const choices = ref<Tok[]>([])          // 一段文字裡抓到好幾個、或有無效的：列出來等人點
const addTxt = ref('')

type Light = { c: 'g' | 'y' | 'r' | 'n' | 'w'; e: string; t: string; v: string; s: string }   // s＝這一燈檢查了什麼
type LiveState = 'idle' | 'manual' | 'loading' | 'ok' | 'timeout' | 'error' | 'denied' | 'unreachable' | 'notprobed' | 'vmware'
interface Live { state: LiveState; at: string; got: number; msg: string; lights: Light[]; alerts: { bad: boolean; text: string }[] }
const mk = (state: LiveState, msg = '', extra: Partial<Live> = {}): Live => ({ state, at: '', got: 0, msg, lights: [], alerts: [], ...extra })
const live = ref<Live>(mk('idle'))
// 「N 分鐘前」用瀏覽器自己的時鐘算「收到結果之後過了多久」，不拿伺服器時間去減瀏覽器時間（兩台時鐘不一定對得上）
const nowMs = ref(Date.now())
let tick: ReturnType<typeof setInterval> | null = null
onMounted(() => { tick = setInterval(() => { nowMs.value = Date.now() }, 20000) })
onBeforeUnmount(() => { if (tick) clearInterval(tick) })
function ageText(got: number): string {
  if (!got) return ''
  const m = Math.floor((nowMs.value - got) / 60000)
  return m < 1 ? '剛檢測' : `${m} 分鐘前檢測`
}

const card = computed(() => out.value?.mode === 'machine' ? out.value.card ?? null : null)
const isVmware = computed(() => !!card.value?.what.vmware)

function errText(e: any): string {
  const s = e?.statusCode ?? e?.response?.status
  const d = e?.data?.detail
  return d ? String(d) : s ? `伺服器回 ${s}` : (e?.message ?? '未知錯誤')
}

async function run(raw: string, fromNav = false, fromPick = false) {
  const text = raw.trim()
  if (!text) return
  let v = text
  autoNote.value = ''; choices.value = []
  if (/\s/.test(text)) {
    const toks = tokens(text)
    const good = toks.filter(t => t.kind !== 'bad')
    if (toks.some(t => t.kind === 'bad') || good.length > 1) {
      // 不確定該查哪一個：停下來，列出抓到的（無效的標原因），等人點
      ++seq; out.value = null; err.value = ''; busy.value = false; choices.value = toks
      autoNote.value = `從文字裡抓到 ${toks.length} 項，點一個來查`
      q.value = raw
      return
    }
    if (good.length === 1) { v = good[0].v; autoNote.value = `已從文字中抓出：${v}` }
    else autoNote.value = '沒抓到有效的 IP 或系統代號，照整串文字去查'
  }
  const my = ++seq
  q.value = v === text ? v : raw
  picked.value = v
  picks.value = fromPick
  down.value = { state: 'idle', n: 0, kinds: '' }
  busy.value = true; err.value = ''; out.value = null; segHint.value = null; cc.value = null; ccState.value = 'idle'
  live.value = mk('idle')
  remember(v)
  if (!fromNav) router.replace({ query: { q: v } })
  let r: Out
  try {
    r = await apiFetch<Out>('/api/oncall', { query: { q: v } })
  } catch (e: any) {
    if (my !== seq) return
    err.value = `查詢失敗：${errText(e)}`; busy.value = false; return
  }
  if (my !== seq) return
  out.value = r
  busy.value = false
  if (r.mode === 'none') { void nearSegment(v, my); return }
  if (r.mode === 'system' && r.system) { void loadPhones({ api_id: r.system.api_id }, my); return }
  if (r.mode !== 'machine' || !r.card) return
  void loadPhones({ asset_serial: r.card.what.serials[0] }, my)
  void liveCheck(r.card, my, false)
  void loadDown(r.card.impact.node_id, my)
}

async function nearSegment(v: string, my: number) {
  if (!IPV4.test(v)) return
  try {
    const r = await apiFetch<{ status: string; segment?: { location?: string; environment?: string; purpose?: string } | null }>('/api/blast/resolve', { query: { q: v } })
    if (my === seq && r.status === 'unregistered') segHint.value = r.segment ?? null
  } catch { /* 推導不出網段只是少一行提示，查無本身的結論不變 */ }
}

async function loadPhones(query: Record<string, string>, my: number) {
  ccState.value = 'loading'
  try {
    const r = (await fetchPhones(apiFetch, query)) as CCard
    if (my !== seq) return
    cc.value = r; ccState.value = 'ok'
  } catch {
    if (my === seq) ccState.value = 'fail'
  }
}

// ===== ② 即時檢測 =====
const TIMEOUT_MS = 14000
const unknownLights = (why: string): Light[] => ['服務', '空間', '重開機'].map(t => ({ c: 'n', e: '❔', t, v: '無法確認', s: why }))
async function liveCheck(c: Card, my = seq, manual = true) {
  // 天條：VMware 設備不連線，連呼叫都不發（c.what.vmware 先於開關判斷）；自動模式每次重讀設定、讀取失敗 fail-closed
  const dec = await decideLive(apiFetch, { isVmware: !!c.what.vmware, manual })
  if (dec.action === 'vmware') { live.value = mk('vmware'); return }
  if (dec.action === 'manual') { if (my === seq) live.value = mk('manual', dec.msg); return }   // 不自動檢測：只讀資料庫，等人按「立即檢測」
  const ips = c.what.ips.filter(i => IPV4.test(i)).slice(0, 3)
  if (!ips.length) { live.value = mk('error', '這台沒有登記 IP，沒有做即時檢測', { lights: unknownLights('沒有檢測') }); return }
  live.value = mk('loading')
  try {
    const r = await runLiveCheck(apiFetch, ips, TIMEOUT_MS)
    if (my !== seq) return
    const items = r.items ?? []
    const it = items.find(x => x.reachable) ?? items[0]
    if (!it) { live.value = mk('error', '檢測沒有回傳結果', { lights: unknownLights('沒有結果') }); return }
    live.value = interpret(it)
  } catch (e: any) {
    if (my !== seq) return
    const status = e?.statusCode ?? e?.response?.status
    const timedOut = e?.name === 'TimeoutError' || e?.name === 'AbortError' || /timeout|aborted/i.test(String(e?.message ?? ''))
    if (timedOut) live.value = mk('timeout', `${TIMEOUT_MS / 1000} 秒內沒有回應`, { lights: unknownLights('逾時，沒有結果') })
    else if (status === 401 || status === 403) live.value = mk('denied', `權限不足：${errText(e)}`, { lights: unknownLights('沒有權限，沒有檢測') })
    else live.value = mk('error', `檢測失敗：${errText(e)}`, { lights: unknownLights('檢測失敗，沒有結果') })
  }
}

const p2 = (n: number) => String(n).padStart(2, '0')
const fmtDT = (d: Date) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
function parseSrv(t: string): Date | null { const d = new Date(String(t).replace(' ', 'T')); return Number.isNaN(d.getTime()) ? null : d }

function interpret(it: any): Live {
  const atFull = String(it.checked_at ?? '')
  const base = { at: atFull.slice(0, 16), got: Date.now() }
  if (it.vmware || (it.not_probed && /VMware/.test(String(it.not_probed)))) return mk('vmware', '', base)
  if (it.not_probed) return mk('notprobed', `沒有實際連線到這台：${String(it.not_probed)}`, { ...base, lights: unknownLights('沒有檢測') })
  if (!it.reachable) return mk('unreachable', `連不上這台：${String(it.error || it.notes?.[0] || '沒有回應')}`, { ...base, lights: unknownLights('連不上，沒有結果') })
  const alerts: { bad: boolean; text: string }[] = []
  const miss = new Set<string>((it.coverage?.missing ?? []).map((m: any) => m.key))
  // 服務：停掉的服務單元＋比基準少掉的監聽埠
  const failed: string[] = it.failed_units ?? []
  const missing: (string | number)[] = it.diff?.missing_ports ?? []
  let svc: Light
  if (failed.length || missing.length) {
    const parts = []
    if (failed.length) parts.push(`${failed.length} 個停掉`)
    if (missing.length) parts.push(`少 ${missing.length} 個埠`)
    svc = { c: 'r', e: '🔴', t: '服務', v: parts.join('・'), s: '服務單元、監聽埠基準' }
    if (failed.length) alerts.push({ bad: true, text: `停掉的服務：${failed.slice(0, 6).join('、')}${failed.length > 6 ? ' 等' : ''}` })
    if (missing.length) alerts.push({ bad: true, text: `比平常基準少了監聽埠：${missing.slice(0, 8).join('、')}${missing.length > 8 ? ' 等' : ''}` })
  } else if (miss.has('services')) {
    svc = { c: 'n', e: '❔', t: '服務', v: '無法確認', s: '這一輪沒查到服務狀態' }
  } else {
    svc = { c: 'g', e: '✅', t: '服務', v: '正常', s: it.diff ? '已檢查：服務單元、監聽埠基準' : '已檢查：服務單元（沒有埠基準可比）' }
    if (!it.diff) alerts.push({ bad: false, text: '尚無監聽埠基準，只比對了服務是否停止' })
  }
  if (it.diff?.baseline_at) {
    const bd = parseSrv(it.diff.baseline_at)
    if (bd) {
      const days = Math.floor((Date.now() - bd.getTime()) / 86400000)
      alerts.push({ bad: false, text: `${days > 14 ? '基準資料較舊：' : ''}監聽埠基準取自 ${fmtDT(bd)}（${days} 天前）` })
    }
  }
  // 空間：最滿的掛載點（空間或 inode 取大者）；唯讀掛載也算
  const disks: any[] = it.disks ?? []
  let sp: Light
  if (!disks.length || miss.has('disks')) sp = { c: 'n', e: '❔', t: '空間', v: '無法確認', s: '這一輪沒查到磁碟空間' }
  else {
    const worst = disks.reduce((a, b) => Math.max(b.use_pct ?? 0, b.inode_pct ?? 0) > Math.max(a.use_pct ?? 0, a.inode_pct ?? 0) ? b : a)
    const wp = Math.max(worst.use_pct ?? 0, worst.inode_pct ?? 0)
    const lv = disks.some(d => d.level === 'red') ? 'r' : disks.some(d => d.level === 'yellow') ? 'y' : 'g'
    const what = `${disks.length} 個掛載點的空間與 inode`
    sp = lv === 'g' ? { c: 'g', e: '✅', t: '空間', v: `最高 ${wp}%`, s: `已檢查：${what}` } : { c: lv, e: lv === 'r' ? '🔴' : '🟡', t: '空間', v: `${worst.mount} ${wp}%`, s: `檢查：${what}` }
    for (const d of disks.filter(x => x.level === 'red' || x.level === 'yellow').slice(0, 4)) {
      const ino = d.inode_pct != null && d.inode_pct >= 0 ? `，檔案數（inode）${d.inode_pct}%` : ''
      alerts.push({ bad: d.level === 'red', text: `${d.mount} 使用 ${d.use_pct}%${ino}` })
    }
  }
  const ro: any[] = it.readonly_mounts ?? []
  if (ro.length) { sp = { c: 'r', e: '🔴', t: '空間', v: '唯讀掛載', s: '掛載狀態' }; alerts.push({ bad: true, text: `變成唯讀的掛載：${ro.map(x => x.mount).join('、')}` }) }
  // 重開機：開機不到 10 分鐘＝黃燈；顯示開機時間與過了多久，不判斷是計畫內還是計畫外
  const up = it.uptime
  let rb: Light
  const ck = parseSrv(atFull)
  const bootStr = up && ck ? fmtDT(new Date(ck.getTime() - (up.uptime_sec ?? 0) * 1000)) : ''
  if (!up || miss.has('uptime')) rb = { c: 'n', e: '❔', t: '重開機', v: '無法確認', s: '這一輪沒查到開機時間' }
  else if (up.just_rebooted) {
    const m = Math.max(1, Math.round((up.uptime_sec ?? 0) / 60))
    rb = { c: 'y', e: '🟡', t: '重開機', v: `${m} 分鐘前`, s: `開機時間 ${bootStr}` }
    alerts.push({ bad: false, text: `開機時間 ${bootStr}（檢測時已開 ${m} 分鐘）` })
  } else {
    const d = up.days ?? 0
    rb = { c: 'g', e: '✅', t: '重開機', v: d >= 1 ? `已開 ${Math.floor(d)} 天` : `已開 ${Math.max(1, Math.round((up.uptime_sec ?? 0) / 3600))} 小時`, s: `開機時間 ${bootStr}` }
  }
  if (it.complete === false && it.coverage?.missing?.length) alerts.push({ bad: false, text: `這一輪檢測不完整，沒查到：${it.coverage.missing.map((m: any) => m.name).join('、')}` })
  return mk('ok', '', { ...base, lights: [svc, sp, rb], alerts })
}

// ===== ① 電話 =====
const telHref = (p: string) => 'tel:' + p.replace(/[^0-9+#*,;]/g, '')
const hasDigits = (p: string | null | undefined) => !!p && /\d{3,}/.test(p) && p !== '未登錄'
const dutyPhones = computed(() => (cc.value?.oncall ?? []).filter(o => hasDigits(o.phone)))
const dutyMissing = computed(() => (cc.value?.oncall ?? []).filter(o => !hasDigits(o.phone)))
// 退路：人員通訊錄裡的電話（負責人、代理人）、維護廠商電話
const fallbackPhones = computed(() => {
  const rows: { label: string; phone: string }[] = []
  for (const p of card.value?.who.contacts ?? []) {
    if (hasDigits(p.phone)) rows.push({ label: `${p.name}${p.job ? `（${p.job}）` : ''}`, phone: p.phone })
    if (p.proxy && hasDigits(p.proxy_phone)) rows.push({ label: `${p.proxy}（${p.name} 的代理人）`, phone: p.proxy_phone as string })
  }
  const mp = cc.value?.maint?.phone?.value
  if (hasDigits(mp)) rows.push({ label: `維護廠商${cc.value?.maint?.vendor?.value ? ` ${cc.value.maint.vendor.value}` : ''}`, phone: mp as string })
  return rows
})
const namesNoPhone = computed(() => {
  const c = card.value
  if (!c) return [] as string[]
  const phoned = new Set((c.who.contacts ?? []).filter(p => hasDigits(p.phone)).map(p => p.name))
  const seen = new Set<string>()
  const out: string[] = []
  const add = (role: string, n: string | null | undefined) => {
    if (!n || n === '未登錄' || phoned.has(n) || seen.has(n)) return
    seen.add(n); out.push(`${role}　${n}`)
  }
  for (const p of c.who.contacts ?? []) add('聯絡人', p.name)
  add('負責人', c.who.owner)
  add('保管人', c.who.custodian)
  add('系統窗口', c.who.ap_owner)
  return out
})
const noAnyPhone = computed(() => ccState.value === 'ok' && !dutyPhones.value.length && !fallbackPhones.value.length)

// ===== ③ 影響 =====
// 它自己登記的系統（停掉就直接受影響）＋圖譜算出來上下游的系統，合併後重大排前面
const allSystems = computed(() => {
  const m = new Map<string, { api_id: string; name: string | null; severity: string }>()
  for (const s of card.value?.systems ?? []) m.set(s.api_id, { api_id: s.api_id, name: s.name, severity: s.severity })
  for (const s of card.value?.impact.system_list ?? []) if (!m.has(s.api_id)) m.set(s.api_id, s)
  return [...m.values()].sort((a, b) => (a.severity === '重大' ? 0 : 1) - (b.severity === '重大' ? 0 : 1))
})
const topSystems = computed(() => allSystems.value.slice(0, 5))
// 它依賴的下游（反向）：只數件數與種類，明細在完整關聯圖
const down = ref<{ state: 'idle' | 'loading' | 'ok' | 'fail'; n: number; kinds: string }>({ state: 'idle', n: 0, kinds: '' })
const KIND: Record<string, string> = { hw: '主機', esxi: 'ESXi 主機', ds: '資料儲存', cluster: '叢集', bizsys: '業務系統', sys: '系統' }
async function loadDown(nodeId: string | undefined, my: number) {
  if (!nodeId) { down.value = { state: 'fail', n: 0, kinds: '' }; return }
  down.value = { state: 'loading', n: 0, kinds: '' }
  try {
    const r = await fetchDownImpact(apiFetch, nodeId)
    if (my !== seq) return
    const cnt: Record<string, number> = {}
    for (const d of r.dependencies ?? []) { const k = d.node_id.split(':')[0]; if (KIND[k]) cnt[k] = (cnt[k] ?? 0) + 1 }
    const n = Object.values(cnt).reduce((a, b) => a + b, 0)
    down.value = { state: 'ok', n, kinds: Object.entries(cnt).map(([k, v]) => `${KIND[k]} ${v}`).join('、') }
  } catch { if (my === seq) down.value = { state: 'fail', n: 0, kinds: '' } }
}
const lockRoom = computed(() => { const r = card.value?.where.room; return r && r !== '未填' ? r : '機房未填' })
const lockEnv = computed(() => { const e = card.value?.what.environment; return e && e !== '未填' ? e : '環境未填' })
const phoneAge = (o: { updated_at?: string | null }) => (o.updated_at ? `登記更新 ${String(o.updated_at).slice(0, 16)}` : '更新時間未記錄')
const impactLink = computed(() => card.value ? `/v2/machine/${encodeURIComponent(card.value.what.hostname || card.value.key)}?tab=impact` : '#')
const where = computed(() => {
  const w = card.value?.where
  if (!w) return ''
  const bits = [w.room !== '未填' ? w.room : '', w.rack ? `機櫃 ${w.rack}${w.u_start ? ` ${w.u_start}${w.u_end && w.u_end !== w.u_start ? `-${w.u_end}` : ''}U` : ''}` : '', w.esxi?.length ? `跑在 ${w.esxi[0].label}` : '']
  return bits.filter(Boolean).join('・')
})
const sysLine = computed(() => (card.value?.systems ?? []).map(s => `${s.api_id}${s.name ? ` ${s.name}` : ''}`).join('、'))

function pick(c: Cand) { void run(c.asset_serial, false, true) }
function choose(t: Tok) { if (t.kind !== 'bad') void run(t.v) }
function dropChoice(t: Tok) { choices.value = choices.value.filter(x => x.v !== t.v) }
function addChoice() {
  const v = addTxt.value.trim()
  if (!v) return
  const why = /^\d+(\.\d+)+$/.test(v) ? checkIp(v) : null
  if (!choices.value.some(x => x.v === v)) choices.value = [...choices.value, why ? { v, kind: 'bad', why } : { v, kind: IPV4.test(v) ? 'ip' : 'code' }]
  addTxt.value = ''
}
function onPaste() { setTimeout(() => { if (/\s/.test(q.value.trim()) && tokens(q.value).length) void run(q.value) }, 0) }

onMounted(() => {
  initTheme(); loadRecent()
  const qq = String(route.query.q ?? '').trim()
  if (qq) void run(qq, true)
})
watch(() => route.query.q, (n) => { const s = String(n ?? '').trim(); if (s && s !== picked.value) void run(s, true) })
</script>

<template>
  <div class="night">
    <header class="nhead">
      <h1 class="nh1">🌙 夜間速查</h1>
      <InfoNote>輸入 IP、主機名稱、資產序號或系統代號，或直接貼上整段簡訊、WhatsUp 告警，一段文字裡只有一個有效的 IP 或系統代號時會自動查；抓到好幾個或有無效的，會列出來讓你點一個。畫面上的時間都是伺服器時間（Asia/Taipei）。全部只讀取、不會改任何資料。② 的即時檢測是用收集帳號登入那台唯讀檢查，VMware 設備（ESXi、vCenter）不做即時檢測。硬體故障（硬碟、記憶體）由 WhatsUp 負責，這裡不掃。</InfoNote>
      <button type="button" class="themebtn" :aria-label="theme === 'dark' ? '切換成淺色' : '切換成深色'" @click="toggleTheme">{{ theme === 'dark' ? '☀ 淺色' : '🌙 深色' }}</button>
    </header>

    <form class="ask" @submit.prevent="run(q)">
      <input v-model="q" class="in" type="text" inputmode="text" enterkeyhint="search" autocomplete="off" autocapitalize="off" spellcheck="false"
             placeholder="貼上告警文字、或輸入 IP／主機名稱" aria-label="IP、主機名稱、資產序號或系統代號" @paste="onPaste">
      <button type="submit" class="go" :disabled="busy">{{ busy ? '查詢中…' : '查' }}</button>
    </form>
    <div v-if="autoNote" class="hint" role="status">{{ autoNote }}</div>
    <div v-if="recent.length" class="chips" aria-label="最近查過">
      <button v-for="r in recent" :key="r" type="button" class="chip" @click="run(r)">{{ r }}</button>
    </div>

    <section v-if="choices.length" class="card">
      <h2>點一個來查（可移除、可補一個）</h2>
      <div class="chips">
        <span v-for="t in choices" :key="t.v" class="tok" :class="t.kind">
          <button type="button" class="chip" :disabled="t.kind === 'bad'" @click="choose(t)">{{ t.kind === 'ip' ? 'IP' : t.kind === 'code' ? '系統' : '無效' }}　{{ t.v }}</button>
          <button type="button" class="x" :aria-label="`移除 ${t.v}`" @click="dropChoice(t)">×</button>
        </span>
      </div>
      <div v-for="t in choices.filter(x => x.kind === 'bad')" :key="'w' + t.v" class="sub">{{ t.v }}：{{ t.why }}</div>
      <form class="addrow" @submit.prevent="addChoice"><input v-model="addTxt" class="addin" type="text" autocomplete="off" placeholder="補一個 IP 或系統代號" aria-label="補一個 IP 或系統代號"><button type="submit" class="chip">加入</button></form>
    </section>

    <div v-if="err" class="alert bad" role="alert">{{ err }} <button type="button" class="retry" @click="run(picked)">再查一次</button></div>

    <!-- 查無 -->
    <section v-if="out?.mode === 'none'" class="card">
      <h2>查詢結果</h2>
      <div class="big">系統查無 {{ picked }}</div>
      <div class="sub">資產庫裡找不到（IP、主機名、資產序號、系統代號都比對過）。可能是新機器還沒登記，或打錯了。</div>
      <div v-if="segHint" class="alert">最接近的網段：{{ segHint.location || '機房未填' }}{{ segHint.purpose ? `・${segHint.purpose}` : '' }}{{ segHint.environment ? `・${segHint.environment}` : '' }}</div>
    </section>

    <!-- 對到多台 -->
    <section v-else-if="out?.mode === 'candidates'" class="card">
      <h2>對到 {{ out.total ?? out.candidates?.length }} 台，點一台</h2>
      <button v-for="c in out.candidates" :key="c.key" type="button" class="pick" @click="pick(c)">
        <b>{{ c.hostname || c.asset_serial }}</b>　{{ c.ip || '無 IP' }}　<span class="sub">{{ c.api_id || '未登記系統' }}・{{ c.os || '作業系統未填' }}{{ c.retired ? '・已退役' : '' }}</span>
      </button>
      <div v-if="(out.total ?? 0) > (out.candidates?.length ?? 0)" class="sub">只列前 {{ out.candidates?.length }} 台，請輸入更完整的名稱縮小。</div>
    </section>

    <!-- 系統代號：先看系統值班電話，再點一台 -->
    <template v-else-if="out?.mode === 'system' && out.system">
      <section class="card">
        <h2>這是業務系統</h2>
        <div class="big">{{ out.system.api_id }} {{ out.system.name || '' }} <span class="pill" :class="out.system.severity === '重大' ? 'bad' : ''">{{ out.system.severity }}</span></div>
        <div class="sub">{{ out.system.machines }} 台主機・窗口 {{ out.system.ap_owner || '未登錄' }}（{{ out.system.ap_department || '部門未登錄' }}）</div>
        <div v-if="ccState === 'loading'" class="skel" style="height:52px;margin-top:10px" />
        <template v-else-if="ccState === 'ok'">
          <a v-for="o in dutyPhones" :key="o.api_id" class="call" :href="telHref(o.phone as string)"><span>值班電話<br><small class="sub">{{ o.phone }}{{ o.note ? `・${o.note}` : '' }}・{{ phoneAge(o) }}</small></span><b>撥打</b></a>
          <div v-if="!dutyPhones.length" class="call none"><span>沒有登記值班電話</span></div>
        </template>
        <div v-else-if="ccState === 'fail'" class="alert">電話資料讀取失敗，請重新查一次。</div>
      </section>
      <section class="card">
        <h2>點一台看狀態（共 {{ out.candidates?.length }} 台）</h2>
        <button v-for="c in (out.candidates ?? []).slice(0, 30)" :key="c.key" type="button" class="pick" @click="pick(c)">
          <b>{{ c.hostname || c.asset_serial }}</b>　{{ c.ip || '無 IP' }}　<span class="sub">{{ c.os || '作業系統未填' }}</span>
        </button>
        <div v-if="(out.candidates?.length ?? 0) > 30" class="sub">只列前 30 台，其餘請直接輸入主機名稱或 IP。</div>
      </section>
    </template>

    <!-- 一台機器：三張卡 -->
    <template v-else-if="card">
      <div class="lock" role="status">你正在看：<b>{{ card.what.hostname || card.what.serials[0] }}</b>／{{ card.what.ips.join('、') || '無 IP' }}／{{ lockEnv }}／{{ lockRoom }}<span class="sub">　資產序號 {{ card.what.serials[0] }}{{ picks ? '・從候選清單選定' : '' }}</span></div>
      <section class="card" aria-live="polite">
        <h2>① 這是誰、找誰</h2>
        <div class="big">{{ card.what.hostname || card.what.serials[0] }}
          <span class="pill">{{ isVmware ? 'VMware 設備' : (card.what.os || '作業系統未填') }}{{ card.what.environment && card.what.environment !== '未填' ? `・${card.what.environment}` : '' }}</span>
          <span v-if="card.what.retired" class="pill bad">已退役</span>
        </div>
        <div class="sub">{{ card.what.ips.join('、') || '無 IP' }}<template v-if="sysLine">・{{ sysLine }}</template><template v-else>・未登記業務系統</template><template v-if="where">・{{ where }}</template></div>

        <div v-if="ccState === 'loading' || ccState === 'idle'" class="skel" style="height:56px;margin-top:10px" />
        <template v-else-if="ccState === 'ok'">
          <a v-for="o in dutyPhones" :key="o.api_id" class="call" :href="telHref(o.phone as string)">
            <span>值班　{{ o.name || o.api_id }}<br><small class="sub">{{ o.phone }}{{ o.note ? `・${o.note}` : '' }}・{{ phoneAge(o) }}</small></span><b>撥打</b></a>
          <template v-if="!dutyPhones.length">
            <div class="call none" role="alert"><span>沒有登記值班電話</span></div>
            <div v-if="fallbackPhones.length" class="sub" style="margin-top:8px">退路：</div>
          </template>
          <template v-else-if="fallbackPhones.length"><div class="sub" style="margin-top:10px">其他聯絡人：</div></template>
          <a v-for="f in fallbackPhones" :key="f.label + f.phone" class="call" :href="telHref(f.phone)"><span>{{ f.label }}<br><small class="sub">{{ f.phone }}・更新時間未記錄</small></span><b>撥打</b></a>
          <div v-if="noAnyPhone" class="alert bad">這台沒有任何電話資料：值班、負責人、保管人、維護廠商都沒登記電話。</div>
          <ul v-if="namesNoPhone.length || dutyMissing.length" class="nophone">
            <li v-for="n in namesNoPhone" :key="n">{{ n }}（電話未登錄）</li>
            <li v-for="o in dutyMissing" :key="o.api_id">{{ o.api_id }} {{ o.name || '' }} 沒登記值班電話</li>
          </ul>
        </template>
        <div v-else class="alert">電話資料讀取失敗。<button type="button" class="retry" @click="run(picked)">再查一次</button></div>
      </section>

      <section class="card" aria-live="polite">
        <h2>② 現在的狀態 <span v-if="live.state === 'loading'" class="pill">即時檢測中…</span><span v-else-if="live.at" class="pill">檢測於 {{ live.at }}（伺服器時間 Asia/Taipei）・{{ ageText(live.got) }}</span></h2>
        <div v-if="live.state === 'vmware'" class="alert">VMware 設備（ESXi、vCenter）不做即時檢測，這裡只有登記資料；狀態資料在 vCenter。</div>
        <div v-else-if="live.state === 'loading'" class="lights">
          <div v-for="t in ['服務', '空間', '重開機']" :key="t" class="lt wait"><div class="e skel sk-e" /><div class="t">{{ t }}</div><div class="v">檢查中</div></div>
        </div>
        <div v-else-if="live.state === 'manual'" class="alert" data-testid="live-manual">
          {{ live.msg || '已設定為不自動檢測（可按「立即檢測」）' }}
          <button v-if="!isViewer" type="button" class="retry" @click="liveCheck(card!)">立即檢測</button>
        </div>
        <template v-else-if="live.state !== 'idle'">
          <div class="lights">
            <div v-for="l in live.lights" :key="l.t" class="lt" :class="l.c"><div class="e" aria-hidden="true">{{ l.e }}</div><div class="t">{{ l.t }}</div><div class="v">{{ l.v }}</div><div class="ls">{{ l.s }}</div></div>
          </div>
          <div v-for="a in live.alerts" :key="a.text" class="alert" :class="{ bad: a.bad }">{{ a.text }}</div>
          <div v-if="live.state !== 'ok'" class="alert bad" role="alert">
            <template v-if="live.state === 'timeout'">連不上／逾時：{{ live.msg }}</template>
            <template v-else>{{ live.msg }}</template>
            <button v-if="!isViewer && live.state !== 'notprobed' && live.state !== 'denied'" type="button" class="retry" @click="liveCheck(card!)">重新檢測</button>
          </div>
        </template>
      </section>

      <section class="card">
        <h2>③ 它停掉會連累誰（上游：依賴它的）</h2>
        <template v-if="card.impact.ok">
          <div class="big">{{ allSystems.length }} 個業務系統・{{ card.impact.host_count ?? 0 }} 台相依主機</div>
          <ul v-if="topSystems.length" class="syslist">
            <li v-for="s in topSystems" :key="s.api_id"><b>{{ s.api_id }}</b> {{ s.name && s.name !== s.api_id ? s.name : '' }} <span class="pill" :class="s.severity === '重大' ? 'bad' : ''">{{ s.severity }}</span></li>
          </ul>
          <div v-if="allSystems.length > 5" class="sub">只列最重要的 5 個，共 {{ allSystems.length }} 個。</div>
          <div v-if="!allSystems.length" class="sub">這台沒有登記業務系統，圖譜裡也沒有任何系統或主機依賴它。</div>
          <div v-if="card.impact.deps_available === false && allSystems.length" class="alert">系統之間的上下游沒有資料，這裡只算到直接掛在這台上的，實際可能更多。</div>
        </template>
        <div v-else class="alert">算不出影響範圍：{{ card.impact.reason }}</div>
        <div class="sub dir">它依賴的下游：<template v-if="down.state === 'loading'">計算中…</template><template v-else-if="down.state === 'ok'">{{ down.n }} 個元件{{ down.kinds ? `（${down.kinds}）` : '' }}</template><template v-else>讀不到</template></div>
        <div class="alert">備援（HA）、流量、負載分擔的資料，系統裡沒有。以上只是登記與連線關係，不能據此判斷停掉是否安全。</div>
        <NuxtLink class="link" :to="impactLink">看完整關聯圖</NuxtLink>
      </section>
    </template>
  </div>
</template>

<style scoped>
.night { min-width: 0; max-width: 720px; margin: 0 auto; font-size: 17px; line-height: 1.55; padding-bottom: 60px; }
.nhead { display: flex; align-items: center; gap: 10px; margin: 2px 0 12px; }
.nh1 { font-family: var(--disp); font-size: 22px; margin: 0; flex: 1; }
.themebtn { border: 1px solid var(--border); background: var(--card); color: var(--ink-aux); border-radius: 999px; padding: 6px 14px; font: inherit; font-size: 14px; cursor: pointer; min-height: 40px; }
.ask { display: block; }
.in { width: 100%; font: inherit; font-size: 22px; padding: 16px; border-radius: 18px !important; border: 2px solid var(--border-strong) !important; }
.in:focus { outline: none; border-color: var(--brand) !important; }
.go { width: 100%; margin-top: 10px; font: inherit; font-size: 22px; font-weight: 700; padding: 16px; border-radius: 18px; border: 0; background: var(--brand); color: var(--type-on); cursor: pointer; min-height: 56px; }
.go:disabled { opacity: .6; }
.hint { color: var(--muted); font-size: 14px; margin-top: 8px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.chip { font: inherit; font-size: 16px; padding: 8px 14px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink); cursor: pointer; min-height: 40px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 18px; padding: 16px; margin-top: 12px; }
.card h2 { margin: 0 0 8px; font-size: 15px; color: var(--muted); font-weight: 600; letter-spacing: .04em; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.big { font-family: var(--disp); font-size: 26px; font-weight: 700; line-height: 1.25; word-break: break-word; }
.sub { color: var(--muted); font-size: 15px; }
.call { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 10px; padding: 14px 16px; border-radius: 14px; background: var(--sub); text-decoration: none; color: var(--ink); font-size: 20px; font-weight: 600; min-height: 64px; }
.call b { flex: none; white-space: nowrap; background: var(--good); color: var(--type-on); border-radius: 999px; padding: 8px 18px; font-size: 18px; }
.call.none { color: var(--bad); border: 1px dashed var(--bad); font-weight: 700; }
.lights { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.lt { border-radius: 14px; padding: 12px 6px; text-align: center; background: var(--sub); border: 2px solid var(--border); min-width: 0; }
.lt .e { font-size: 30px; line-height: 1.2; }
.lt .t { font-size: 14px; color: var(--muted); }
.lt .v { font-family: var(--disp); font-size: 17px; font-weight: 700; word-break: break-word; }
.lt.g { border-color: var(--good); } .lt.y { border-color: var(--warn); } .lt.r { border-color: var(--bad); } .lt.n { border-style: dashed; }
.lt.wait { opacity: .75; }
.pill { display: inline-block; max-width: 100%; white-space: normal; word-break: break-word; border-radius: 14px; padding: 2px 12px; font-size: 14px; border: 1px solid var(--border-strong); color: var(--muted); font-weight: 500; letter-spacing: 0; }
.pill.bad { border-color: var(--bad); color: var(--bad); }
.alert { border-radius: 14px; padding: 12px 14px; margin-top: 12px; border: 1px solid var(--warn); color: var(--warn-text); background: var(--warn-soft); }
.alert.bad { border-color: var(--bad); color: var(--bad); background: var(--bad-soft); }
.retry { margin-left: 8px; font: inherit; font-size: 15px; border: 1px solid currentColor; background: transparent; color: inherit; border-radius: 999px; padding: 4px 14px; cursor: pointer; min-height: 36px; }
.link { display: block; margin-top: 12px; text-align: center; padding: 14px; border-radius: 14px; border: 1px solid var(--border-strong); color: var(--ink); text-decoration: none; background: var(--sub); font-size: 17px; min-height: 48px; }
.pick { display: block; width: 100%; text-align: left; font: inherit; padding: 14px; border-radius: 14px; border: 1px solid var(--border); background: var(--sub); color: var(--ink); margin-top: 8px; cursor: pointer; min-height: 56px; }
ul { margin: 8px 0 0; padding-left: 20px; }
li { margin: 4px 0; }
.syslist { list-style: none; padding: 0; }
.syslist li { padding: 8px 0; border-bottom: 1px solid var(--line); }
.lock { margin-top: 12px; padding: 10px 14px; border-radius: 14px; border: 2px solid var(--brand); background: var(--mint); font-size: 16px; word-break: break-word; }
.ls { font-size: 12px; color: var(--muted); margin-top: 2px; }
.tok { display: inline-flex; align-items: center; gap: 2px; }
.tok.bad .chip { border-color: var(--bad); color: var(--bad); }
.x { border: 0; background: transparent; color: var(--muted); font-size: 22px; min-width: 36px; min-height: 40px; cursor: pointer; }
.addrow { display: flex; gap: 8px; margin-top: 10px; }
.addin { flex: 1; font: inherit; padding: 10px 12px; border-radius: 12px !important; min-width: 0; }
.dir { margin-top: 10px; }
.nophone { color: var(--muted); font-size: 15px; }
.skel { background: linear-gradient(90deg, var(--sub) 25%, var(--mint-deep) 50%, var(--sub) 75%); background-size: 200% 100%; animation: shim 1.2s linear infinite; border-radius: 12px; }
.sk-e { height: 34px; width: 34px; margin: 0 auto; border-radius: 50%; }
@keyframes shim { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .skel { animation: none; } }
@media (max-width: 420px) { .big { font-size: 23px; } .lt .v { font-size: 15px; } }
</style>
