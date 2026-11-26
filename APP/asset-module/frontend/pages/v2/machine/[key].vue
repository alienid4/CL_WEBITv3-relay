<script setup lang="ts">
// 主機履歷（2.x 藍圖；不在選單，從主機查詢／全站搜尋／連結進入）。網址的 key 可以是代號、資產編號、IP、主機名稱
// （後端與值班卡同一套比對）。橫排頁籤：概要｜影響範圍｜事件與維運｜硬體與位置｜軟體與服務｜更多（帳號與安全、關聯設備、
// 連線關聯、異動紀錄、系統重建資料、資料來源）。操作：停機影響評估（2.006.5：直接切到「影響範圍」頁籤看結果，不建事件；要開事件再按「建立事件」，
// 帶入此主機、類別由使用者選，找碴 C4）、檢測 ▾（單台唯讀健檢／CPU 分析／檔案系統空間，結果卡在主機重點下方；VMware 設備不提供）、
// 更正位置（沿用位置更正，會進 CIA 待更正）、標記下線／報廢（沿用標記下線，原因必填、記 CIA 待異動）、複製 IP、匯出履歷。
// 概要（2.071.x 重整，示意 AI/示意_主機履歷概要重整_2026-11-26.html、設計 AI/設計_主機履歷概要重整_2026-11-26.md）：
//   主機重點 H1～H6 與「要你處理」並排最上面 → 檢測結果 → 身分列 I1～I2 → 虛擬化位置／LPAR 承載 V1～V4 → 卡 A 歸屬與聯絡、卡 B 運作狀態 → 數字卡 N1～N3（彈窗下鑽）。
//   型號、序號、大類、櫃號 U 位、規格、硬體 EOS 明細搬到「硬體與位置 › 總覽」（V2HostHwOverview）。
// 資料：/api/v2/machine（oncall.card＋count_bridge＋rack_layout＋lpar_host）、/api/v2/machine/summary|section（v2_sections）、
//   /api/v2/machine/profile（待 CIA 異動、校正、EOS：「要你處理」與 B4 用）、/api/v2/machine/ops＋/api/v2/routine（B2 維護／事件，跟「事件與維運」頁籤同一支）。
definePageMeta({ layout: 'v2' })
const { isViewer, isAdmin } = useAuth()    // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
import type { V2Badge, V2Status } from '~/types/v2'
interface Light { key: string; hostname: string; ip: string | null; asset_serial: string; system: string | null; where: string; badge: V2Badge; retired: boolean }
interface Sys { api_id: string; name: string | null; sys_class: string | null; severity: string; availability: number | null; ap_owner: string | null; ap_department: string | null; machines: number }
interface M {
  mode: 'machine'; key: string; name: string; badge: V2Badge; status: V2Status; core: { label: string; api_id: string } | null; conclusion: string; vmware: boolean; vmware_legacy?: boolean
  who_is: { hostname: string; ips: string[]; serials: string[]; model: string | null; os: string | null; environment: string | null; is_vm: boolean; asset_status: string | null; hw_serial: string | null; hw_serial_note: string | null; exist_label: string | null; retired?: boolean }
  systems: Sys[]; same_system: { api_id: string; name: string | null; count: number; machines: Light[] }[]
  people: { owner: string; custodian: string; ap_user: string; usage_unit: string; ap_owner: string; ap_department: string; contacts: { name: string; phone: string; proxy: string | null; proxy_phone: string | null; dept: string | null }[] }
  where: { room: string | null; rack: string | null; u_start: number | null; u_end: number | null; rack_raw: string | null; esxi: { node_id: string; label: string; asset_serial: string | null }[]
    rack_info: { kind: string | null; room: string | null; rack?: string; u?: string; u_start?: number; u_end?: number; box_count?: number; others?: number; other_names?: string[]; other_keys?: string[]; position_corrected?: boolean; cia_where?: string | null; rack_cia?: string | null
      box?: { key: string; name: string; members: number; self_is_head: boolean } | null } | null }
  alive: { status: V2Status; last_seen_any: string | null; exist_label: string | null; exist_score: number | null; onboarded: boolean }
  impact: { ok: boolean; reason?: string; link: string; host_count?: number; systems_all: { api_id: string; name: string | null; severity?: string; own: boolean }[]; at_least: number; deps_note: string | null }
  lpar_host?: { hmc: string; hmc_ip: string; hmcs: string[]; frame: string | null; frame_lpars: number; lpar_id: string | null; collected_at: string | null } | null
}
type Res = M | { mode: 'candidates' | 'system'; q: string; candidates: Light[]; total?: number } | { mode: 'none'; q: string; reason: string }

const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const { showToast } = useToast()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const q = computed(() => String(route.params.key || ''))
const res = ref<Res | null>(null)
const err = ref('')
const card = ref<any>(null)
const summary = ref<{ sections: any[]; impact_total: any; tab_state?: any } | null>(null)
const sumErr = ref('')
async function load() {
  res.value = null; err.value = ''; card.value = null; summary.value = null; check.value = null
  try {
    res.value = await apiFetch<Res>('/api/v2/machine', { query: { q: q.value } })
  } catch (e: any) { err.value = `載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
  // 值班電話跟概要一起在伺服器端畫好（找碴 C9：原本先顯示「讀取中…」，載完才變無資料）
  if (res.value?.mode === 'machine') {
    try { card.value = await apiFetch<any>('/api/contacts/card', { query: { asset_serial: (res.value as M).who_is.serials[0] } }) } catch { card.value = { error: true } /* 值班電話讀不到：顯示「讀不到聯絡卡」，跟尚未登錄分開 */ }
  }
}
async function loadSide() {
  if (res.value?.mode !== 'machine') return
  const mm = res.value as M
  try { summary.value = await apiFetch<any>('/api/v2/machine/summary', { query: { key: mm.key } }); sumErr.value = '' } catch (e: any) { sumErr.value = `頁籤筆數載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
  loadPlacement()
  loadOverviewSide(mm)
}
// 虛擬化位置（2.058.24，使用者 11-09：「查的時候要知道他是哪個 VC 管理、哪個機房，以前有這資訊 V2 沒有了，而且要明顯」）。
// 同 1.x 資產頁那一塊：/api/arch/vm-placement（CI 圖 host→runs_on→esxi→member_of→cluster）。機房＝ESXi 登記的機房，
// 沒有就用叢集名前綴推（推導值，畫面標明）。LPAR 歸 HMC 管，不拿 vCenter 資料對。
const placement = ref<any>(null)
// 叢集名前綴認不出機房時 _site_of 會原樣回叢集名（221：WLD02-UAT-CL01）——那不是機房，不可以填進「機房」
const vmRoom = computed(() => placement.value?.esxi_room || placement.value?.esxi_location
  || (placement.value?.site && placement.value.site !== placement.value.cluster ? placement.value.site : null))
// 納管欄用的（2.069.3）：LPAR 只認「同一個箱子的分割區」（count_bridge 角色 lpar），不是任何 AIX；收集狀態 undefined＝後端沒給這個欄位（納管欄會顯示查不到）
const onbLpar = computed(() => Object.values(((m.value as any)?.roles ?? {}) as Record<string, string>).includes('lpar'))
const onbCollect = computed<'fresh' | 'stale' | null | undefined>(() => { const c = (m.value?.alive as any)?.collect; return c ? (c.status ?? null) : undefined })
const isLpar = computed(() => /aix/i.test(`${m.value?.badge.code ?? ''} ${m.value?.badge.label ?? ''}`) || m.value?.badge.code === '400' || onbLpar.value)
async function loadPlacement() {
  placement.value = null
  const mm = res.value?.mode === 'machine' ? res.value as M : null
  if (!mm || !mm.badge.vm || isLpar.value) return
  try { placement.value = await apiFetch<any>(`/api/arch/vm-placement/${encodeURIComponent(mm.who_is.serials[0])}`) } catch { placement.value = { error: true } }
}
// 概要補充：profile（待 CIA 異動、校正、EOS）、事件（進行中／未來排程）、例行時段——三支都是既有 API，失敗各自標「讀不到」
const prof = ref<any>(null)
const ops = ref<any>(null)
const routine = ref<any>(null)
async function loadOverviewSide(mm: M) {
  prof.value = null; ops.value = null; routine.value = null
  const [p, o, r] = await Promise.allSettled([
    apiFetch<any>('/api/v2/machine/profile', { query: { key: mm.key } }),
    apiFetch<any>('/api/v2/machine/ops', { query: { key: mm.key, period: '30d' } }),
    apiFetch<any>('/api/v2/routine', { query: { key: mm.key } }),
  ])
  prof.value = p.status === 'fulfilled' ? p.value : { error: true }
  ops.value = o.status === 'fulfilled' ? o.value : { error: true }
  routine.value = r.status === 'fulfilled' ? r.value : { error: true }
}
const check = ref<any>(null)
await load()          // 概要在伺服器端先畫好
onMounted(loadSide)
watch(q, async () => { await load(); loadSide() })
const m = computed(() => (res.value?.mode === 'machine' ? res.value as M : null))

// 主機關聯探索（host_relations 旗標）：要明確看到開關是開的才出現（isEnabled 對查不到的模組預設回 true，不能用）
const { flags: ffl, ensureLoaded: ensureFfl } = useFeatureFlags()
onMounted(() => { ensureFfl() })
const relOn = computed(() => !!ffl.value.find(f => f.module_key === 'host_relations')?.enabled)
// 頁籤最多 6 個（2026-11-15 畫面重看）：常用 5 個＋「更多」。資料來源（收集摘要、來源對照、vCenter 紀錄、CIA 登記、體檢、原始資料）
// 是診斷，收在「更多」底下；舊網址 ?tab=raw 導到「資料來源」。
const MAIN_TABS = [{ k: 'overview', label: '概要' }, { k: 'impact', label: '影響範圍' }, { k: 'ops', label: '事件與維運' }, { k: 'hw', label: '硬體與位置' }, { k: 'sw', label: '軟體與服務' }]
const MORE_BASE = [{ k: 'sec', label: '帳號與安全' }, { k: 'near', label: '關聯設備' }, { k: 'relation', label: '連線關聯' }, { k: 'chg', label: '異動紀錄' },
  { k: 'rebuild', label: '系統重建資料' }, { k: 'source', label: '資料來源' }]
const MORE_TABS = computed(() => MORE_BASE.filter(t => t.k !== 'relation' || relOn.value))
const ALL_KEYS = computed(() => [...MAIN_TABS, ...MORE_TABS.value].map(t => t.k))
const tab = computed({ get: () => { const t = route.query.tab === 'raw' ? 'source' : String(route.query.tab || ''); return ALL_KEYS.value.includes(t) ? t : 'overview' },
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })
const isMore = computed(() => MORE_TABS.value.some(t => t.k === tab.value))
const topTab = computed({ get: () => (isMore.value ? 'more' : tab.value), set: (v: string) => { if (v !== 'more') tab.value = v; else if (!isMore.value) tab.value = MORE_TABS.value[0].k } })
function withCount(t: { k: string; label: string }) {
  if (t.k === 'overview' || t.k === 'ops' || t.k === 'relation' || !summary.value) return t
  const g = t.k === 'source' ? 'raw' : t.k
  const list = summary.value.sections.filter((s: any) => s.group === g)
  const n = list.reduce((a: number, s: any) => a + (s.count || 0), 0)
  if (t.k === 'impact') return summary.value.impact_total?.known ? { ...t, label: `${t.label} ${summary.value.impact_total.hosts}` } : t
  if (t.k === 'source' || (list.length === 1 && list[0].unknown)) return t
  return { ...t, label: `${t.label} ${n}` }
}
const tabsWithCount = computed(() => [...MAIN_TABS.map(withCount), { k: 'more', label: '更多' }])
const moreWithCount = computed(() => MORE_TABS.value.map(withCount))

// ===== 概要：值 =====
const oncallPhones = computed(() => (card.value?.oncall ?? []).filter((o: any) => o.phone))
const owners = computed(() => {
  const p = m.value?.people
  if (!p) return []
  return [...new Set([p.ap_owner, p.custodian].filter(x => x && x !== '未登錄'))]
})
const apDept = computed(() => { const d = m.value?.people.ap_department; return d && d !== '未登錄' ? d : '' })
const ri = computed(() => m.value?.where.rack_info ?? null)
const typeShort = computed(() => (!m.value ? '' : m.value.badge.vm ? ((m.value.badge as any).vm_label || '虛擬機') : isLpar.value ? 'LPAR' : '實體機'))
const measuredOs = computed(() => (m.value as any)?.measured?.os ?? null)
const hostRoom = computed(() => (m.value?.badge.vm ? vmRoom.value : (ri.value?.room || m.value?.where.room || null)))
const lh = computed(() => m.value?.lpar_host ?? null)
const maint = computed(() => {
  const x = card.value?.maint
  if (!x) return ''
  return [x.vendor?.value, x.phone?.value].filter(Boolean).join(' ')
})
const EOS_LABEL: Record<string, string> = { expired: '已過 EOS', upcoming: '一年內到期', ok: '尚在支援期', unknown: '未公佈' }
const eosTone = (s?: string) => ({ expired: 'bad', upcoming: 'warn', ok: 'good' } as Record<string, string>)[s || ''] ?? 'unk'
const eosText = (e: any) => (e.status === 'unknown' && e.reason_label ? e.reason_label : (EOS_LABEL[e.status] ?? e.status)) + (e.eos_date ? `（${e.eos_date}）` : '')
// B2 維護／事件：「事件與維運」頁籤同一支 API（進行中＝status ongoing 且計次），例行時段只列沒過期的
const ongoing = computed(() => (ops.value?.events ?? []).filter((e: any) => e.status === 'ongoing' && e.countable))
const nextUp = computed(() => (ops.value?.upcoming ?? [])[0] ?? null)
const windows = computed(() => (routine.value?.windows ?? []).filter((w: any) => !w.expired))
// 納管：H6 放 V2HostOnboardAction（唯一一份判斷），B3 與「要你處理」讀它 emit 出來的同一份 plan
const onbPlan = ref<any>(null)
const onbRef = ref<any>(null)

// 「要你處理」：只收要人動手、有處理處的（集中在這一處判斷）
interface Todo { k: string; t: string; b: string; go?: () => void; to?: string; who?: 'admin' | 'user' }
const todos = computed<Todo[]>(() => {
  const mm = m.value
  if (!mm) return []
  const out: Todo[] = []
  const p = prof.value && !prof.value.error ? prof.value : null
  const retired = !!mm.who_is.retired
  if (p?.eos?.hw?.status === 'expired') out.push({ k: 'hweos', t: `硬體已過 EOS${p.eos.hw.eos_date ? `（${p.eos.hw.eos_date}）` : ''}`, b: '看生命週期', go: () => goHw('eos') })
  if (ri.value?.position_corrected) out.push({ k: 'pos', t: `位置與 CIA 登記不同：現場 ${ri.value.rack} 櫃 ${ri.value.u || ''}，CIA 登記 ${ri.value.cia_where || '未填'}`, b: '看位置', go: () => goHw('loc') })
  for (const x of p?.pending ?? []) out.push({ k: `pend-${x.serial}`, t: `待 CIA 異動：${x.asset_status || '未填'} → ${x.pending_status}`, b: '下線與報廢清單', to: '/v2/datafix?tab=offline' })
  if (p?.fix?.hostname) out.push({ k: 'fixh', t: `主機名與實際不同：CIA ${p.fix.hostname.cia || '—'}，實際 ${p.fix.hostname.measured}`, b: '校正清單', to: '/v2/datafix?tab=mismatch' })
  if (p?.fix?.ip) out.push({ k: 'fixi', t: `IP 與實際不同：CIA ${p.fix.ip.cia || '—'}，實際 ${p.fix.ip.measured}`, b: '校正清單', to: '/v2/datafix?tab=mismatch' })
  const pl = onbPlan.value
  if (!retired && !mm.vmware && pl && (pl.phase === 'not_onboarded' || pl.phase === 'failed'))
    out.push({ k: 'onb', t: pl.phase === 'failed' ? `納管沒成功：${pl.collectText}` : '尚未納管，收不到實測規格、軟體與帳號資料', b: pl.canRetry ? '重新納管' : '納管',
      go: (pl.canOnboard || pl.canRetry) ? () => onbRef.value?.openOnboard() : undefined, who: 'admin' })
  if (!retired && card.value && !card.value.error && !oncallPhones.value.length) out.push({ k: 'oncall', t: '尚未登錄值班電話', b: '補登', to: '/v2/settings?st=contacts', who: 'admin' })
  return out
})
const todoAll = ref(false)
const shownTodos = computed(() => (todoAll.value ? todos.value : todos.value.slice(0, 2)))
const todoReady = computed(() => !!prof.value && card.value !== null)
const canAct = (t: Todo) => !isViewer.value && (t.who !== 'admin' || isAdmin.value)

// ===== 數字卡下鑽（彈窗，可排序、搜尋、匯出） =====
const drill = ref<'' | 'same' | 'rack'>('')
const sameRows = computed(() => {
  const mm = m.value
  if (!mm) return []
  const seen = new Map<string, any>()
  seen.set(mm.key, { key: mm.key, hostname: `${mm.name}（本機）`, ip: mm.who_is.ips[0] ?? null, type: `${mm.badge.version_label || mm.badge.label}${mm.badge.vm ? '・虛擬機' : ''}`, where: hostRoom.value || '', system: mm.systems.map(s => s.api_id).join('、') })
  for (const s of mm.same_system) for (const x of s.machines) {
    if (seen.has(x.key)) { seen.get(x.key).system += `、${s.api_id}`; continue }
    seen.set(x.key, { key: x.key, hostname: x.hostname, ip: x.ip, type: `${x.badge.version_label || x.badge.label}${x.badge.vm ? '・虛擬機' : ''}`, where: x.where, system: s.api_id })
  }
  return [...seen.values()]
})
const rackRows = computed(() => (ri.value?.other_names ?? []).map((n, i) => ({ key: ri.value?.other_keys?.[i] ?? n, name: n })))
const machineLink = (r: any) => (r.key && r.key !== m.value?.key ? `/v2/machine/${encodeURIComponent(r.key)}` : null)

// ===== 跳到某一區（硬體總覽的分組卡、概要的虛擬化區），找到後閃一下 =====
async function flash(anchor: string) {
  for (let i = 0; i < 40; i++) {
    await nextTick()
    const el = document.querySelector(`[data-anchor="${anchor}"]`) as HTMLElement | null
    if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.classList.add('hl'); setTimeout(() => el.classList.remove('hl'), 1500); return }
    await new Promise(r => setTimeout(r, 100))      // 硬體總覽是點了才載：等它畫出來
  }
}
async function goHw(anchor?: string) {
  await router.replace({ query: { ...route.query, tab: 'hw', sec: 'overview' } })
  if (anchor) flash(anchor)
}
async function goVirt() {
  if (tab.value !== 'overview') await router.replace({ query: { ...route.query, tab: 'overview' } })
  flash('virt')
}
async function copyText(t: string | undefined | null) {
  if (!t) { showToast('此主機沒有 IP 可複製', 'warn'); return }
  try { await navigator.clipboard.writeText(t); showToast(`已複製 ${t}`, 'success') } catch { showToast(`瀏覽器不允許複製，請手動選取：${t}`, 'warn') }
}

// ===== 操作 =====
const planUrl = computed(() => m.value ? `/v2/incident?new=1&machine=${encodeURIComponent(m.value.key.replace(/^ret:/, ''))}&title=${encodeURIComponent(`${m.value.name} 停機`)}` : '/v2/incident')
const checking = ref(false)
const LIGHT: Record<string, string> = { red: '異常', yellow: '注意', green: '正常', skipped: '未檢測' }
async function runCheck() {
  const ip = (m.value?.who_is.ips ?? [])[0]
  if (!ip) { showToast('此主機沒有 IP，無法即時檢測', 'warn'); return }
  checking.value = true
  try {
    const r = await apiFetch<any>('/api/health-check/run', { method: 'POST', body: { ips: [ip] } })
    check.value = r.items?.[0] ?? null
    if (tab.value !== 'overview') tab.value = 'overview'
  } catch (e: any) { showToast(`即時檢測失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { checking.value = false }
}
// 「檢測 ▾」（2.071.0）：完整健檢＝原本的即時檢測；CPU 分析／檔案系統空間＝只做那一項，結果卡在主機重點下方
const diagRef = ref<any>(null)
const menuEl = ref<HTMLDetailsElement | null>(null)
async function pick(k: 'full' | 'cpu' | 'fs' | 'hist') {
  if (menuEl.value) menuEl.value.open = false
  if (k === 'full') { runCheck(); return }
  // 切頁籤是 router.replace（非同步）：要等路由真的換完、概要掛上去，結果卡才在（Antigravity C10）
  if (tab.value !== 'overview') await router.replace({ query: { ...route.query, tab: 'overview' } })
  await nextTick()
  if (!diagRef.value) { showToast('結果區還沒準備好，請再按一次', 'warn'); return }
  if (k === 'hist') diagRef.value.openHistory()
  else diagRef.value.run(k)
}
// 下拉選單點外面或按 Esc 收起
function closeMenu(e: Event) {
  const el = menuEl.value
  if (!el?.open) return
  if (e instanceof KeyboardEvent ? e.key === 'Escape' : !el.contains(e.target as Node)) el.open = false
}
onMounted(() => { document.addEventListener('click', closeMenu); document.addEventListener('keydown', closeMenu) })
onBeforeUnmount(() => { document.removeEventListener('click', closeMenu); document.removeEventListener('keydown', closeMenu) })
const checkRows = computed(() => {
  const c = check.value
  if (!c) return []
  const disks = (c.disks ?? []).filter((d: any) => (d.use_pct ?? d.pct ?? 0) >= 80).map((d: any) => `${d.mount} ${d.use_pct ?? d.pct}%`).join('、')
  return [
    ['結果', `${LIGHT[c.overall] ?? c.overall}${c.complete === false ? '・未完整檢測' : ''}`],
    ['連線', c.reachable ? '可連線' : `無法連線${c.error ? `：${c.error}` : ''}`],
    ['作業系統', c.os || '無資料'],
    ['開機時間', c.uptime ? (c.uptime.text || `${c.uptime.days ?? ''} 天`) : '無資料'],
    ['CPU', c.cpu ? `${c.cpu.pct ?? c.cpu.used_pct ?? '—'}%` : '無資料'],
    ['記憶體', c.mem ? `${c.mem.pct ?? c.mem.used_pct ?? '—'}%` : '無資料'],
    ['磁碟 80% 以上', disks || '無'],
    ['失敗的服務', (c.failed_units ?? []).join('、') || '無'],
    ['說明', (c.notes ?? []).join('；') || '—'],
    ['檢測時間', c.checked_at || '—'],
  ]
})
const LV: Record<string, string> = { red: '異常', yellow: '注意', green: '正常' }
const gb = (kb: number | null | undefined) => (kb == null ? '—' : `${(kb / 1048576).toFixed(1)} GB`)
const checkDisks = computed(() => (check.value?.disks ?? []).map((d: any, i: number) => ({ ...d, __no: i + 1, pct: d.use_pct ?? d.pct ?? null, ipct: d.inode_pct != null && d.inode_pct >= 0 ? d.inode_pct : null,
  size: `${gb(d.used_kb)} / ${gb(d.size_kb ?? d.total_kb)}`, lv: LV[d.level] ?? d.level ?? '—' })))
const checkDiff = computed(() => {
  const d = check.value?.diff
  if (!d) return ''
  const parts: string[] = []
  if (d.missing_ports?.length) parts.push(`消失的埠：${d.missing_ports.join('、')}`)
  if (d.new_ports?.length) parts.push(`新增的埠：${d.new_ports.join('、')}`)
  if (d.disk_jumps?.length) parts.push(`磁碟漲幅：${d.disk_jumps.map((j: any) => `${j.mount} ${j.from}→${j.to}%`).join('、')}`)
  return parts.length ? `與 ${String(d.baseline_at || '').slice(0, 16)} 的基準相比：${parts.join('；')}` : `與 ${String(d.baseline_at || '').slice(0, 16)} 的基準相比：沒有變化`
})
const showPos = ref(false)
const posDevice = computed(() => {
  const mm = m.value
  if (!mm) return null
  const r = mm.where.rack_info
  return { key: mm.key.replace(/^ret:/, ''), name: mm.name, room: r?.room || mm.where.room || '', rack: r?.kind === 'rack' ? (r.rack ?? null) : null,
    u_start: r?.u_start ?? mm.where.u_start, u_end: r?.u_end ?? mm.where.u_end, position_corrected: r?.position_corrected }
})
const showOffline = ref(false)
async function afterChange() { showPos.value = false; showOffline.value = false; await load(); loadSide() }
</script>

<template>
  <V2Page v-model="topTab" :tabs="m ? tabsWithCount : undefined">
    <template #title>
      <template v-if="m">
        <V2TypeBadge :badge="m.badge" size="l" text />
        <h1 class="h1">{{ m.name }}</h1>
        <V2StatusChip :status="m.status" />
        <span v-if="m.core" class="core">{{ m.core.label }}</span>
        <span v-if="m.badge.note" class="bnote">{{ m.badge.note }}</span>
      </template>
      <h1 v-else class="h1">主機履歷</h1>
    </template>
    <template v-if="m" #conclusion>{{ m.who_is.environment || '環境未填' }}・{{ m.status.label }}<NuxtLink class="old" :to="`/v2/asset/${encodeURIComponent(m.who_is.serials[0])}`">資產明細與編輯 ›</NuxtLink></template>
    <template v-if="m" #actions>
      <button type="button" class="btn danger" @click="tab = 'impact'">停機影響評估</button>
      <details v-if="!m.vmware && !isViewer" ref="menuEl" class="dd">
        <summary class="btn">{{ checking || diagRef?.busy ? '檢測中…' : '檢測 ▾' }}</summary>
        <div class="ddm">
          <button v-if="isAdmin" type="button" :disabled="checking" @click="pick('full')">完整健檢</button>
          <button type="button" :disabled="!!diagRef?.busy" @click="pick('cpu')">CPU 分析</button>
          <button type="button" :disabled="!!diagRef?.busy" @click="pick('fs')">檔案系統空間</button>
          <button type="button" @click="pick('hist')">過去分析</button>
        </div>
      </details>
      <button v-if="!isViewer && !m.badge.vm" type="button" class="btn" @click="showPos = true">更正位置</button>
      <button v-if="!isViewer" type="button" class="btn" @click="showOffline = true">標記下線／報廢</button>
      <button type="button" class="btn ghost" @click="copyText(m.who_is.ips[0])">複製 IP</button>
      <a class="btn ghost" :href="`${apiBase}/api/v2/machine/export.xlsx?key=${encodeURIComponent(m.key)}`">匯出履歷</a>
    </template>

    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!res" class="muted">查詢中…</p>
    <template v-else-if="res.mode === 'none'">
      <p class="msg">查無「{{ res.q }}」：{{ res.reason }}</p>
    </template>
    <template v-else-if="res.mode !== 'machine'">
      <p class="msg">「{{ res.q }}」符合 {{ res.total ?? res.candidates.length }} 台<span v-if="res.mode === 'system'" class="sub">業務系統代號</span>，請選擇：</p>
      <V2Table :rows="res.candidates.map(c => ({ ...c, type: `${c.badge.version_label || c.badge.label}${c.badge.vm ? '・虛擬機' : ''}` }))" prefix="C"
               :cols="[{ k: 'hostname', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'type', label: '類型' }, { k: 'ip', label: 'IP', mono: true }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }]" />
    </template>
    <p v-if="sumErr" class="err">{{ sumErr }}</p>

    <template v-if="m" #tab>
      <div v-if="isMore" class="morebar">
        <button v-for="t in moreWithCount" :key="t.k" type="button" class="mt" :class="{ on: tab === t.k }" @click="tab = t.k">{{ t.label }}</button>
      </div>
      <template v-if="tab === 'overview'">
        <!-- 主機重點 H＋要你處理（並排） -->
        <div class="top2">
          <section class="box hcard" data-anchor="hero">
            <h3>主機重點<span class="no">H</span></h3>
            <dl>
              <dt><span class="ix">H1</span>IP</dt>
              <dd><template v-if="m.who_is.ips.length"><span v-for="(ip, i) in m.who_is.ips" :key="ip" class="ipl" :class="i ? 'ip2' : 'ip1'"><span class="mono">{{ ip }}</span><button type="button" class="lb cp" @click="copyText(ip)">複製</button></span>
                <InfoNote v-if="m.who_is.ips.length > 1">第一個是 CIA 登記的第一個 IP，頁首「複製 IP」複製它；IP 用途看「硬體與位置 › 網路卡／IP」。</InfoNote></template>
                <V2Unknown v-else why="CIA 未填 IP" /></dd>
              <dt><span class="ix">H2</span>OS</dt>
              <dd><template v-if="measuredOs">{{ measuredOs.text }}<span class="sub">實測 {{ String(measuredOs.at || '').slice(0, 16) }}</span>
                  <InfoNote v-if="m.who_is.os && m.who_is.os !== measuredOs.text">CIA 登記為 {{ m.who_is.os }}；畫面以實測為準。</InfoNote></template>
                <template v-else-if="m.who_is.os">{{ m.who_is.os }}<span class="tag warn">CIA 登記・未實測</span></template>
                <template v-else-if="prof?.eos?.os_guess">{{ prof.eos.os_guess }}<span class="tag unk">推測</span></template>
                <V2Unknown v-else why="CIA 未填" /></dd>
              <dt><span class="ix">H3</span>AP 系統</dt>
              <dd><template v-if="m.systems.length"><span v-for="s in m.systems" :key="s.api_id" class="line"><NuxtLink :to="`/v2/system/${encodeURIComponent(s.api_id)}`" class="lnk">{{ s.api_id }} {{ s.name || '' }}</NuxtLink></span></template>
                <V2Unknown v-else why="CIA 未填業務系統" /></dd>
              <dt><span class="ix">H4</span>{{ m.badge.vm ? 'vCenter' : isLpar ? 'HMC' : '虛擬化' }}</dt>
              <dd><template v-if="m.badge.vm">
                  <button v-if="placement?.esxi_vc" type="button" class="lb" @click="goVirt">{{ placement.esxi_vc }}</button>
                  <span v-else-if="!placement" class="muted">讀取中…</span>
                  <V2Unknown v-else :why="placement.error ? '讀不到虛擬化位置' : placement.found ? '舊的 RVTools 匯入沒有 vCenter 欄' : 'vCenter 盤點資料裡沒有這台'" /></template>
                <template v-else-if="isLpar"><button v-if="lh" type="button" class="lb" @click="goVirt">{{ lh.hmc }}</button><V2Unknown v-else why="HMC 收集資料裡沒有這台" /></template>
                <template v-else>實體機</template></dd>
              <dt><span class="ix">H5</span>機房</dt>
              <dd><template v-if="hostRoom"><b>{{ hostRoom }}</b><span v-if="m.badge.vm" class="tag warn">推導</span></template>
                <span v-else-if="m.badge.vm && !placement" class="muted">讀取中…</span>
                <V2Unknown v-else :why="m.badge.vm ? '承載的 ESXi 沒登記機房' : 'CIA 未填機房'" /></dd>
              <dt><span class="ix">H6</span>納管</dt>
              <dd><template v-if="m.vmware">不納管・資料取自 vCenter
                  <span v-if="m.vmware_legacy" class="warn">・早期建立的收集帳號尚未移除<InfoNote>系統已不再對它收集或檢測；帳號要不要移除由管理員決定。</InfoNote></span></template>
                <V2HostOnboardAction v-else ref="onbRef" :ips="m.who_is.ips" :serials="m.who_is.serials" :hostname="m.who_is.hostname" :os="m.who_is.os" :measured-os="measuredOs?.text ?? null"
                  :onboarded="m.alive.onboarded" :collect="onbCollect" :vmware="m.vmware" :os-class="m.badge.os_class ?? null" :badge-code="m.badge.code" :retired="m.who_is.retired" :asset-status="m.who_is.asset_status" :lpar="onbLpar"
                  @plan="(p: any) => (onbPlan = p)" @done="load" /></dd>
            </dl>
          </section>
          <section class="todo" :class="{ ok: todoReady && !todos.length }" data-anchor="todo">
            <h3>要你處理<template v-if="todoReady">（{{ todos.length }} 件）</template></h3>
            <p v-if="!todoReady" class="muted">檢查中…</p>
            <template v-else-if="todos.length">
              <div v-for="(t, i) in shownTodos" :key="t.k" class="it">
                <span class="tno">T{{ i + 1 }}</span><span class="tt">{{ t.t }}</span>
                <span v-if="isViewer && t.who" class="tag unk">需維運人員處理</span>
                <span v-else-if="t.who === 'admin' && !isAdmin" class="tag unk">需管理員處理</span>
                <NuxtLink v-else-if="t.to" class="btn sm pri" :to="t.to">{{ t.b }}</NuxtLink>
                <button v-else-if="t.go && canAct(t)" type="button" class="btn sm pri" @click="t.go">{{ t.b }}</button>
                <span v-else-if="!t.go" class="tag unk">{{ onbPlan?.reason || '目前不能從這裡處理' }}</span>
              </div>
              <button v-if="todos.length > shownTodos.length" type="button" class="lb more" @click="todoAll = true">其餘 {{ todos.length - shownTodos.length }} 件 ›</button>
            </template>
            <p v-else class="it0">沒有要你處理的事</p>
            <p v-if="prof?.error" class="err">待 CIA 異動、校正、EOS 讀不到，這裡可能不完整</p>
          </section>
        </div>

        <V2HostDiag v-if="!m.vmware && !isViewer" ref="diagRef" :ips="m.who_is.ips" />
        <div v-if="check" class="check">
          <div class="ch"><b>即時檢測結果</b><span class="pill" :class="check.overall">{{ LIGHT[check.overall] ?? check.overall }}</span><button type="button" class="lb" @click="check = null">關閉</button>
</div>
          <dl class="kv"><template v-for="[k, v] in checkRows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
          <p v-if="checkDiff" class="sub">{{ checkDiff }}</p>
          <p v-if="(check.readonly_mounts ?? []).length" class="sub">⚠ 唯讀重掛：{{ check.readonly_mounts.map((x: any) => x.mount).join('、') }}</p>
          <V2Table v-if="checkDisks.length" :rows="checkDisks" prefix="K" :max="50" empty="沒有磁碟資料"
                   :cols="[{ k: 'mount', label: '磁碟／掛載' }, { k: 'fs', label: '檔案系統' }, { k: 'pct', label: '使用率 %', mono: true }, { k: 'ipct', label: 'inode %', mono: true }, { k: 'size', label: '已用／總量' }, { k: 'lv', label: '燈號' }]" />
        </div>

        <!-- 身分列 I -->
        <div class="strip" data-anchor="strip">
          <span class="f"><i>I1 環境</i><b>{{ m.who_is.environment || '' }}</b><V2Unknown v-if="!m.who_is.environment" why="CIA 未填" /></span>
          <span class="f"><i>I2 類型</i><b>{{ typeShort }}</b><button type="button" class="lb sm" @click="goHw('id')">細節 ›</button></span>
        </div>

        <!-- 虛擬化位置（VM）／LPAR 承載（LPAR）V1～V4 -->
        <div v-if="m.badge.vm || isLpar" class="vbox" data-anchor="virt">
          <span class="vt">{{ m.badge.vm ? '虛擬化位置' : 'LPAR 承載' }}</span>
          <template v-if="m.badge.vm">
            <template v-if="placement?.found || m.where.esxi.length">
              <span class="it"><span class="ix">V1</span><i>vCenter</i><b v-if="placement?.esxi_vc">{{ placement.esxi_vc }}</b><V2Unknown v-else why="舊的 RVTools 匯入沒有 vCenter 欄，重匯含「VI SDK Server」欄的 RVTools 就會有" /></span>
              <span class="it"><span class="ix">V2</span><i>叢集</i><b v-if="placement?.cluster">{{ placement.cluster }}</b><V2Unknown v-else why="ESXi 沒有掛叢集" /></span>
              <span class="it"><span class="ix">V3</span><i>ESXi</i>
                <template v-if="placement?.found"><NuxtLink v-if="placement.esxi_serial" class="lnk" :to="`/v2/machine/${encodeURIComponent(placement.esxi_serial)}`"><b>{{ placement.esxi }}</b></NuxtLink><b v-else>{{ placement.esxi }}</b></template>
                <template v-else><NuxtLink v-for="e in m.where.esxi" :key="e.node_id" :to="`/v2/machine/${encodeURIComponent(e.asset_serial || e.label)}`" class="lnk"><b>{{ e.label }}</b></NuxtLink></template></span>
              <span class="it"><span class="ix">V4</span><i>機房</i><template v-if="vmRoom"><b>{{ vmRoom }}</b><span class="tag warn">推導</span></template><V2Unknown v-else why="承載的 ESXi 沒登記機房" /><InfoNote>取承載這台 VM 的 ESXi 登記的機房；沒登記時，叢集名稱認得出機房才用。機櫃位置看承載的 ESXi。</InfoNote></span>
            </template>
            <V2Unknown v-else-if="placement?.error" why="讀不到虛擬化位置" />
            <V2Unknown v-else-if="placement" why="vCenter 盤點資料裡沒有這台，到 6 資料匯入重匯 RVTools 就會有" />
            <span v-else class="muted">讀取中…</span>
          </template>
          <template v-else>
            <span class="it"><span class="ix">V1</span><i>HMC</i><template v-if="lh"><b>{{ lh.hmcs.join('、') }}</b><span class="sub">收集 {{ String(lh.collected_at || '').slice(0, 16) }}</span></template><V2Unknown v-else why="HMC 收集資料裡沒有這台（6.2 設備收集的 HMC 收集後才有）" /></span>
            <span class="it"><span class="ix">V2</span><i>實體箱體</i>
              <template v-if="lh?.frame"><b>{{ lh.frame }}</b><span class="sub">同箱體 LPAR {{ lh.frame_lpars }} 個</span></template>
              <template v-else-if="ri?.box"><NuxtLink v-if="!ri.box.self_is_head" class="lnk" :to="`/v2/machine/${encodeURIComponent(ri.box.key)}`"><b>{{ ri.box.name }}</b></NuxtLink><b v-else>{{ ri.box.name }}</b><span class="sub">4-5 同箱體 {{ ri.box.members }} 筆</span></template>
              <V2Unknown v-else why="HMC 與機櫃圖都沒有這台的箱體" /></span>
            <span class="it"><span class="ix">V3</span><i>LPAR ID</i><b v-if="lh?.lpar_id">{{ lh.lpar_id }}</b><V2Unknown v-else why="HMC 收集資料裡沒有這台" /></span>
            <span class="it"><span class="ix">V4</span><i>機房</i><b v-if="ri?.room || m.where.room">{{ ri?.room || m.where.room }}</b><V2Unknown v-else why="CIA 未填機房" /><button type="button" class="lb sm" @click="goHw('loc')">櫃位 ›</button></span>
            <InfoNote>HMC、箱體、LPAR ID 取自 6.2 HMC 收集的結果（3.2 架構圖 PowerVM 同一份），用 LPAR 名稱對主機名；對不到時箱體改用 4-5 機櫃圖的同箱體併板。</InfoNote>
          </template>
        </div>

        <div class="grid2">
          <section class="box" data-anchor="own">
            <h3>歸屬與聯絡<span class="no">A</span></h3>
            <dl>
              <dt><span class="ix">A1</span>值班電話</dt>
              <dd><template v-if="oncallPhones.length"><span v-for="o in oncallPhones" :key="o.api_id" class="line"><a class="mono tel" :href="`tel:${o.phone}`">{{ o.phone }}</a><span class="sub">{{ o.name }}</span></span></template>
                <V2Unknown v-else-if="card?.error" why="讀不到聯絡卡" /><V2Unknown v-else-if="card" why="尚未登錄" /><span v-else class="muted">讀取中…</span>
                <span v-if="card && !card.error && !oncallPhones.length && (apDept || m.people.contacts[0])" class="sub">可聯絡 {{ [apDept, m.people.contacts[0]?.name].filter(Boolean).join('・') }}</span></dd>
              <dt><span class="ix">A2</span>業務系統</dt>
              <dd><template v-if="m.systems.length"><span v-for="s in m.systems" :key="s.api_id" class="line"><NuxtLink :to="`/v2/system/${encodeURIComponent(s.api_id)}`" class="lnk">{{ s.api_id }} {{ s.name || '' }}</NuxtLink>
                <span class="sub">{{ s.sys_class || '系統等級未填' }}・重要度 {{ s.severity }}</span></span></template><V2Unknown v-else why="CIA 未填業務系統" /></dd>
              <dt><span class="ix">A3</span>負責人</dt><dd>{{ owners.join('、') }}<V2Unknown v-if="!owners.length" why="尚未登錄" /><span v-if="apDept" class="sub">{{ apDept }}</span></dd>
              <dt><span class="ix">A4</span>聯絡人</dt>
              <dd><template v-if="m.people.contacts.length"><span v-for="c in m.people.contacts" :key="c.name" class="line">{{ c.name }} <span class="mono">{{ c.phone }}</span><span v-if="c.proxy" class="sub">代理 {{ c.proxy }} {{ c.proxy_phone || '' }}</span><span v-if="c.dept" class="sub">{{ c.dept }}</span></span></template>
                <V2Unknown v-else why="尚未登錄" /></dd>
              <dt><span class="ix">A5</span>維護廠商</dt><dd>{{ maint }}<V2Unknown v-if="!maint && card" why="尚未登錄" /><span v-if="!card" class="muted">讀取中…</span></dd>
            </dl>
          </section>
          <section class="box" data-anchor="onb">
            <h3>運作狀態<span class="no">B</span></h3>
            <dl>
              <dt><span class="ix">B1</span>偵測狀態</dt><dd>{{ m.alive.status.label }}<span v-if="m.alive.last_seen_any" class="sub">最後偵測 {{ m.alive.last_seen_any.slice(0, 16) }}<template v-if="m.alive.exist_label">・{{ m.alive.exist_label }}</template></span></dd>
              <dt><span class="ix">B2</span>維護／事件</dt>
              <dd><template v-if="ops && !ops.error">
                  <button v-if="ongoing.length" type="button" class="lb" @click="tab = 'ops'"><span class="tag warn">進行中：{{ ongoing[0].title }}<template v-if="ongoing.length > 1"> 等 {{ ongoing.length }} 件</template></span></button>
                  <span v-else>目前沒有進行中事件</span>
                  <span v-if="windows.length" class="sub">例行時段 {{ windows.map((w: any) => `${w.start_hhmm}–${w.end_hhmm}`).join('、') }}</span>
                  <span v-if="nextUp" class="sub">下一個排程 {{ nextUp.date }} {{ nextUp.kind_label }} {{ nextUp.title }}</span></template>
                <V2Unknown v-else-if="ops?.error" why="讀不到事件紀錄" /><span v-else class="muted">讀取中…</span></dd>
              <dt><span class="ix">B3</span>納管狀態</dt>
              <dd><template v-if="m.vmware">不納管<span class="sub">VMware 設備・資料取自 vCenter</span></template>
                <template v-else-if="onbPlan">{{ onbPlan.mgmt }}<span v-if="onbPlan.collectText" class="sub">最近收集：{{ onbPlan.collectText }}</span><span v-if="onbPlan.reason" class="sub">{{ onbPlan.reason }}</span>
                  <span v-if="(m.alive as any).collect?.last_collect" class="sub">最後收集 {{ String((m.alive as any).collect.last_collect).slice(0, 16) }}</span>
                  <NuxtLink v-if="!m.alive.onboarded" to="/v2/onboard" class="lnk sm">6.1 納管作業</NuxtLink></template>
                <span v-else class="muted">讀取中…</span></dd>
              <dt><span class="ix">B4</span>生命週期</dt>
              <dd><template v-if="prof && !prof.error">
                  <span class="lc">OS <NuxtLink v-if="prof.eos?.os" class="tag lnk0" :class="eosTone(prof.eos.os.status)" :to="prof.eos.os_link || '/v2/eos'">{{ eosText(prof.eos.os) }}</NuxtLink><span v-else class="tag unk">對照表沒有</span></span>
                  <span class="lc">硬體 <button v-if="m.badge.vm" type="button" class="lb" @click="goHw('eos')">看承載 ESXi</button>
                    <button v-else-if="prof.eos?.hw" type="button" class="tag lnk0 tb" :class="eosTone(prof.eos.hw.status)" @click="goHw('eos')">{{ eosText(prof.eos.hw) }}</button><span v-else class="tag unk">對照表沒有</span></span></template>
                <V2Unknown v-else-if="prof?.error" why="讀不到 EOS" /><span v-else class="muted">讀取中…</span></dd>
            </dl>
          </section>
        </div>

        <!-- 數字卡 N -->
        <div class="kpis">
          <button type="button" class="kpi bad" @click="tab = 'impact'"><b>{{ m.impact.at_least }}</b><span>N1 停機影響業務系統（套{{ m.impact.deps_note ? '，至少' : '' }}）</span></button>
          <button v-if="m.systems.length" type="button" class="kpi" @click="drill = 'same'"><b>{{ sameRows.length }}</b><span>N2 同業務系統主機（含本機）</span></button>
          <button v-if="m.badge.vm" type="button" class="kpi" @click="goVirt"><b class="kb">{{ placement?.esxi || m.where.esxi[0]?.label || '—' }}</b><span>N3 承載的 ESXi</span></button>
          <button v-else-if="ri?.kind === 'rack'" type="button" class="kpi" @click="drill = 'rack'"><b>{{ ri.others }}</b><span>N3 同機櫃其他設備（不含本機）</span></button>
        </div>
      </template>
      <template v-else-if="tab === 'source'">
        <V2CollectSummary v-if="!m.vmware" :mkey="m.key" />
        <V2SourceCompare :mkey="m.key" />
        <V2HostProfile :mkey="m.key" part="diag" />
        <h3 class="h3">原始資料</h3>
        <p v-if="!summary && !sumErr" class="muted">載入中…</p>
        <V2HostGroup v-else-if="summary" :mkey="m.key" group="raw" :secs="summary.sections" :total="summary.impact_total" />
      </template>
      <template v-else-if="tab === 'ops'"><V2HostOps :mkey="m.key" /><V2RoutineWindows :mkey="m.key" /></template>
      <ClientOnly v-else-if="tab === 'relation'"><V2DataState :st="summary?.tab_state?.relation" /><V2HostRelations :machine-key="m.key" /></ClientOnly>
      <template v-else-if="tab === 'hw'">
        <p v-if="!summary && !sumErr" class="muted">載入中…</p>
        <V2HostGroup v-else-if="summary" :mkey="m.key" group="hw" :secs="summary.sections" :total="summary.impact_total" overview
                     :hide="m.badge.vm ? ['location'] : []" :labels="{ hw_spec: '硬體規格（完整）' }">
          <template #overview>
            <V2HostHwOverview :mkey="m.key" :m="m" :placement="placement" :is-lpar="isLpar" :is-viewer="isViewer" @virt="goVirt" @pos="showPos = true" @rack="drill = 'rack'" />
          </template>
        </V2HostGroup>
      </template>
      <template v-else>
        <V2DataState v-if="tab === 'sw' || tab === 'sec' || tab === 'rebuild'" :st="summary?.tab_state?.[tab]" />
        <ClientOnly v-if="tab === 'impact' && m.who_is.serials?.[0]"><V2RelationGraph :node-id="`hw:${m.who_is.serials[0]}`" :height="420" /></ClientOnly>
        <div v-if="tab === 'impact'" class="impbar">
          <NuxtLink :to="planUrl" class="btn">建立事件</NuxtLink>
          <InfoNote>這裡只顯示影響範圍，不會建立事件。按「建立事件」會帶入此主機，類別由你選。</InfoNote>
        </div>
        <p v-if="!summary && !sumErr" class="muted">載入中…</p>
        <V2HostGroup v-else-if="summary" :mkey="m.key" :group="tab" :secs="summary.sections" :total="summary.impact_total" />
      </template>

      <!-- 數字卡下鑽彈窗 -->
      <div v-if="drill" class="mask" @click.self="drill = ''">
        <div class="modal" role="dialog" :aria-label="drill === 'same' ? '同業務系統主機' : '同機櫃其他設備'">
          <div class="mh"><b v-if="drill === 'same'">同業務系統主機（{{ m.systems.map(s => s.api_id).join('、') }}，含本機 {{ sameRows.length }} 台）</b>
            <b v-else>同機櫃其他設備（{{ ri?.room }} {{ ri?.rack }} 櫃，{{ ri?.others }} 台，不含本機）</b>
            <button type="button" class="lb" @click="drill = ''">關閉</button></div>
          <V2Table v-if="drill === 'same'" :rows="sameRows" prefix="S" :search="true" export-name="同業務系統主機"
                   :cols="[{ k: 'hostname', label: '主機名稱', link: machineLink }, { k: 'ip', label: 'IP', mono: true }, { k: 'type', label: '類型' }, { k: 'where', label: '位置' }, { k: 'system', label: '業務系統', mono: true }]" />
          <template v-else>
            <V2Table :rows="rackRows" prefix="K" :search="true" export-name="同機櫃其他設備" empty="這個櫃沒有其他設備"
                     :cols="[{ k: 'name', label: '名稱', link: machineLink }]" />
            <NuxtLink v-if="ri?.kind === 'rack'" class="lnk sm" :to="{ path: '/v2/datacenter', query: { room: ri.room, tab: 'front', rack: ri.rack } }">到機櫃圖 ›</NuxtLink>
          </template>
        </div>
      </div>
      <PositionEditor v-if="showPos && posDevice" :device="posDevice" @saved="afterChange" @close="showPos = false" />
      <OfflineMarkModal v-if="showOffline" :serials="m.who_is.serials" :who="m.name" @done="afterChange" @close="showOffline = false" />
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 28px; font-weight: 700; }
.core { padding: 4px 10px; border-radius: 999px; background: var(--st-missing-bg); color: var(--st-missing); font-size: 13px; }
.bnote { padding: 4px 10px; border-radius: 999px; background: var(--st-stale-bg); color: var(--st-stale); font-size: 13px; }
.old { margin-left: 12px; font-size: 13px; color: var(--link); }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 7px 14px; font-size: 13.5px; font-family: inherit; text-decoration: none; cursor: pointer; display: inline-block; }
.btn.danger { background: var(--v2-danger); border-color: var(--v2-danger); color: var(--type-on); }
.btn.ghost { border-color: var(--border); color: var(--ink-aux); }
.btn.sm { padding: 2px 10px; font-size: 13px; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--card); }
.btn:disabled { opacity: .6; cursor: default; }
.dd { position: relative; display: inline-block; }
.dd > summary { list-style: none; }
.dd > summary::-webkit-details-marker { display: none; }
.ddm { position: absolute; right: 0; top: calc(100% + 4px); z-index: 20; min-width: 150px; display: flex; flex-direction: column; padding: 4px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); box-shadow: var(--shadow); }
.ddm button { border: 0; background: none; text-align: left; padding: 7px 10px; border-radius: 6px; font: inherit; font-size: 13.5px; color: var(--ink); cursor: pointer; }
.ddm button:hover { background: var(--sub); }
.ddm button:disabled { opacity: .5; cursor: default; }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 16px; margin: 0; font-size: 14px; }
.kv dt { color: var(--ink-aux); }
.kv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.sub { margin-left: 8px; font-size: 13px; color: var(--ink-aux); }
.lnk { color: var(--link); text-decoration: none; margin-right: 6px; }
.lnk:hover { text-decoration: underline; }
.lnk.sm { font-size: 13px; margin-left: 8px; }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; }
.lb.sm { font-size: 12.5px; margin-left: 8px; }
.mono { font-family: var(--disp); }
.warn { color: var(--st-stale); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.morebar { display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; margin-bottom: 12px; }
.mt { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13.5px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.mt.on { background: var(--st-online-bg); color: var(--st-online); }
.h3 { margin: 18px 0 8px; font-size: 15px; }
.impbar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.msg { font-size: 15px; margin: 0; }
.check { border: 1px solid var(--border); border-left: 5px solid var(--brand); border-radius: 10px; padding: 12px; margin-bottom: 12px; background: var(--card); }
.ch { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.pill { padding: 2px 10px; border-radius: 999px; font-size: 12px; background: var(--st-unknown-bg); color: var(--st-unknown); }
.pill.green { background: var(--st-online-bg); color: var(--st-online); } .pill.yellow { background: var(--st-stale-bg); color: var(--st-stale); } .pill.red { background: var(--st-missing-bg); color: var(--st-missing); }
/* 概要（2.071.x） */
.top2 { display: grid; grid-template-columns: minmax(300px, 1fr) minmax(300px, 1fr); gap: 12px; margin-bottom: 12px; align-items: start; }
@media (max-width: 760px) { .top2 { grid-template-columns: 1fr; } }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; }
.box h3, .todo h3 { font-size: 14.5px; margin: 0 0 6px; display: flex; align-items: center; gap: 8px; color: var(--brand); }
.box h3 .no { color: var(--muted); font-weight: 400; font-size: 12.5px; }
.box dl { display: grid; grid-template-columns: 118px minmax(0, 1fr); gap: 4px 12px; margin: 0; font-size: 14px; }
.box dt { color: var(--ink-aux); }
.box dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.ix { color: var(--muted); font-size: 11.5px; margin-right: 4px; font-family: var(--disp); }
.hcard dd { font-size: 16px; }
.ipl { display: block; }
.ip1 .mono { font-weight: 700; font-size: 18px; }
.ip2 .mono { font-weight: 600; }
.cp { font-size: 12px; margin-left: 8px; }
.line { display: block; }
.todo { background: var(--card); border: 1px solid var(--border); border-left: 5px solid var(--warn); border-radius: var(--radius); padding: 10px 14px; }
.todo.ok { border-left-color: var(--brand); }
.todo h3 { color: var(--ink); }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); flex-wrap: wrap; font-size: 14px; }
.todo .it:first-of-type { border-top: 0; }
.tno { font-weight: 700; color: var(--ink-aux); min-width: 26px; font-family: var(--disp); }
.tt { flex: 1; min-width: 180px; }
.it0 { margin: 0; font-size: 14px; }
.more { font-size: 13px; margin-top: 4px; }
.strip { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 22px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; margin-bottom: 12px; }
.strip .f i { font-style: normal; color: var(--ink-aux); font-size: 12.5px; margin-right: 6px; }
.strip .f b { font-weight: 600; }
.vbox { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 22px; margin: 0 0 12px; padding: 10px 14px; border: 2px solid var(--brand); border-radius: 12px; background: var(--card); font-size: 15px; }
.vbox .vt { font-weight: 700; color: var(--brand); }
.vbox i { font-style: normal; color: var(--ink-aux); font-size: 13px; margin-right: 6px; }
.vbox b { color: var(--ink); }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; margin-bottom: 12px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 12px; }
.kpi { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 14px; cursor: pointer; text-align: left; font: inherit; color: inherit; }
.kpi:hover { border-color: var(--brand); }
.kpi b { display: block; font-size: 24px; font-family: var(--disp); }
.kpi b.kb { font-size: 18px; }
.kpi span { color: var(--ink-aux); font-size: 13px; }
.kpi.bad b { color: var(--bad); }
.tag { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 10px; font-size: 12px; white-space: nowrap; background: var(--st-unknown-bg); color: var(--st-unknown); vertical-align: 1px; }
.tag.bad { background: var(--bad-soft); color: var(--bad); } .tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.tag.good { background: var(--good-soft); color: var(--good); } .tag.unk { background: var(--st-unknown-bg); color: var(--st-unknown); }
.lnk0 { text-decoration: none; } .tb { border: 0; font-family: inherit; cursor: pointer; }
.lc { margin-right: 14px; }
.tel { color: var(--ink); font-weight: 700; text-decoration: none; }
.mask { position: fixed; inset: 0; background: color-mix(in srgb, var(--ink) 35%, transparent); display: flex; align-items: flex-start; justify-content: center; padding: 60px 16px; z-index: 40; }
.modal { background: var(--card); border-radius: var(--radius); padding: 14px 16px; width: min(820px, 100%); max-height: 80vh; overflow: auto; }
.mh { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
:global([data-anchor].hl) { outline: 3px solid var(--warn); outline-offset: 3px; border-radius: 10px; }
</style>
