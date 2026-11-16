// 6.1「開始收集」V2 原生（批 23，V17）：從舊頁 pages/adopt.vue 抽出來的純規則，vitest 釘住。
// 純搬移——每一條都跟舊頁同一套算法，只是搬到可以測的地方。
import { v2LegacyTarget } from './v2LegacyMap'

/** 目標數量粗估（舊頁 targetSplit 同一套）：CIDR 展開、a.b.c.d-e 範圍、單一 IP；/32＝單一 IP。
 *  只用來讓人心裡有數，真正的解析與上限在後端。 */
export function targetSplit(text: string): { ranges: number; singles: number } {
  let ranges = 0, singles = 0
  for (const tok of String(text ?? '').split(/[\s,;]+/)) {
    if (!tok) continue
    const cidr = tok.match(/^\d+\.\d+\.\d+\.\d+\/(\d+)$/)
    if (cidr && cidr[1] === '32') { singles += 1; continue }
    if (cidr) { const bits = 32 - Number(cidr[1]); ranges += Math.max(2 ** bits - 2, 1); continue }
    const range = tok.match(/^\d+\.\d+\.\d+\.(\d+)-(\d+)$/)
    if (range) { ranges += Math.max(Number(range[2]) - Number(range[1]) + 1, 0); continue }
    singles += 1
  }
  return { ranges, singles }
}

/** 「加入範圍」：把勾的網段接在輸入框後面，已經有的不重複加（舊頁 addSelectedSegs） */
export function addSegments(text: string, picked: Iterable<string>): { text: string; added: number } {
  const cur = String(text ?? '').split(/[\s,;]+/).filter(Boolean)
  const have = new Set(cur)
  const add = [...picked].filter(c => !have.has(c))
  return { text: [...cur, ...add].join('\n'), added: add.length }
}

export interface OnboardAudit { id: number; target_ip: string; ok: number; [k: string]: any }
/** 紅框只攤開「還沒解決」的失敗：同一台之後已經成功過就不算（audits 新到舊；舊頁 lastFail） */
export function lastUnresolvedFail<T extends OnboardAudit>(audits: T[]): T | null {
  const okLater = new Set<string>()
  for (const a of audits) {
    if (a.ok) { okLater.add(a.target_ip); continue }
    if (!okLater.has(a.target_ip)) return a
  }
  return null
}

export interface CovSeg { cidr: string; total: number; collected: number; never: boolean; [k: string]: any }
export type CovOnly = '' | 'never' | 'partial' | 'full'
/** 涵蓋率表：補 %（一位小數）＋依「從未／部分／全收」篩（舊頁 covRows） */
export function covRows<T extends CovSeg>(segments: T[], only: CovOnly): (T & { pct: number })[] {
  return segments
    .map(s => ({ ...s, pct: s.total ? Math.round(s.collected * 1000 / s.total) / 10 : 0 }))
    .filter(s => !only || (only === 'never' ? s.never
      : only === 'full' ? s.collected >= s.total : (s.collected > 0 && s.collected < s.total)))
}
export const asCovOnly = (v: string): CovOnly => (['never', 'partial', 'full'].includes(v) ? v as CovOnly : '')

/** 這一輪結果卡：點哪一堆列哪一堆，'*'＝全部（舊頁 runSumRows） */
export function runSumRows(sum: { rows: any[] } | null | undefined, open: string): any[] {
  if (!sum) return []
  return open === '*' ? sum.rows : sum.rows.filter((r: any) => r.bucket === open)
}
export function runSumLine(sm: any): string {
  if (!sm) return ''
  const parts = sm.buckets.map((b: any) => `${b.label} ${sm.counts[b.key] ?? 0}`)
  return `完成：${sm.total} 台 — ${parts.join('・')}`
}

/** 開始收集前的檢查（舊頁 runDispatch 開頭，順序一樣）：
 *  空白絕不代表全部 → 勾了網段沒加入範圍 → 超過門檻要確認。回傳要做什麼，畫面照做。 */
export const CONFIRM_OVER = 256
export type DispatchGate =
  | { kind: 'warn'; text: string }
  | { kind: 'confirm'; text: string }
  | { kind: 'go' }
export function dispatchGate(o: { targets: string; segPicked: number; regOnly: boolean; batchSize?: number | null; gapSeconds?: number | null }): DispatchGate {
  if (!o.targets.trim()) {
    return { kind: 'warn', text: o.segPicked ? `勾了 ${o.segPicked} 段還沒按「加入範圍」——先按「加入範圍」再開始收集` : '請先輸入網段或 IP（空白不代表全部）' }
  }
  if (o.segPicked) return { kind: 'warn', text: `還有 ${o.segPicked} 段勾了沒加入範圍——要收就先按「加入範圍」，不收就取消勾選` }
  const sp = targetSplit(o.targets)
  const n = sp.ranges + sp.singles
  if (n > CONFIRM_OVER) {
    const bs = o.batchSize ?? 1024
    const gapMin = (o.gapSeconds ?? 120) / 60
    const batches = Math.ceil(n / bs)
    const mins = Math.ceil(n * 5 / 64 / 60 + (batches - 1) * gapMin)
    const scope = o.regOnly && sp.ranges ? `最多 ${n} 個位址（網段裡只收有登記的，實際會少很多）` : `${n} 個位址`
    return { kind: 'confirm', text: `這次會收 ${scope}，分 ${batches} 批，探測約 ${mins} 分鐘起跳（連得進去的還要另外收）。確定？` }
  }
  return { kind: 'go' }
}

/** 納管後收集排隊的勾選：送出去的＝目前隊伍裡還在、而且勾了的（隊伍刷新後離開的不會被送出） */
export function pqToSend(sel: Record<string, boolean>, items: { asset_serial: string }[]): string[] {
  return items.filter(it => sel[it.asset_serial]).map(it => it.asset_serial)
}
export function pqPrune(sel: Record<string, boolean>, items: { asset_serial: string }[]): Record<string, boolean> {
  const keep = new Set(items.map(it => it.asset_serial))
  const out: Record<string, boolean> = {}
  for (const [k, v] of Object.entries(sel)) if (v && keep.has(k)) out[k] = true
  return out
}

/** 收集結果狀態的中文（舊頁 STATUS_LABEL 同一份；舊頁註：跟後端 collect_dispatch.STATUS_LABEL 同一個字） */
export const STATUS_LABEL: Record<string, string> = {
  collected: '已納管', not_onboardable: '另有納管方式', needs_credential: '待佈身分', needs_agent: '待裝 Agent',
  import_only: '只能匯入', failed: '收集失敗', win_pending: '待納管', onboard_failed: '納管失敗',
  needs_winrm_port: '待開 WinRM', needs_ssh_port: '待開 SSH',
}

/** 手動登記的預帶值（舊頁 openAdopt）：技術層從掃描結果帶入，其餘待收集、可手補 */
export function adoptPrefill(h: { ip: string; hostname?: string | null; segment?: string | null; mac?: string | null; is_vm?: number | null }): Record<string, any> {
  const f: Record<string, any> = { ip: h.ip, hostname: h.hostname ?? '', subnet: h.segment ?? '' }
  if (h.mac) f.mac = h.mac
  if (h.is_vm) f.is_vm = 'VM'
  return f
}

/** 未登記主機搜尋（舊頁 sorted）：空白隔開＝每個字都要有；比對 IP、主機名、研判、網卡廠牌、MAC、開放埠、網段 */
export function filterUnreg<T extends Record<string, any>>(hosts: T[], q: string): T[] {
  const ts = String(q ?? '').trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!ts.length) return hosts
  return hosts.filter(h => {
    const hay = [h.ip, h.hostname, h.os_guess, h.mac_vendor, h.mac, h.open_ports, h.segment].join(' ').toLowerCase()
    return ts.every(t => hay.includes(t))
  })
}

/** 勾選裡「目前畫面上看得到的」IP（批次動作只送這些；搜尋藏起來的不送——比舊頁收緊） */
export function visibleIps(sel: Record<string, boolean>, shown: { ip: string }[]): string[] {
  return shown.filter(h => sel[h.ip]).map(h => h.ip)
}
export function pruneIps(sel: Record<string, boolean>, shown: { ip: string }[]): Record<string, boolean> {
  const keep = new Set(shown.map(h => h.ip))
  const out: Record<string, boolean> = {}
  for (const [k, v] of Object.entries(sel)) if (v && keep.has(k)) out[k] = true
  return out
}

// ===== 改分類（舊頁 openMove／moveTargetChoices／submitMove 同一套；寫的是 os_type_override，只影響顯示與試連，不會去建帳號）=====
export interface MoveRow { ip: string; hostname?: string | null; asset_serial?: string | null; tab?: string | null; message?: string | null; os_override?: any }
export interface TabChoice { os_type: string; label: string; onboardable: boolean }
export interface CatTab { tab: string; label: string; movable: boolean; why_not: string | null; choices: TabChoice[] }
export interface MoveOpt { value: string; label: string; disabled: boolean; note: string }

/** 認不出是哪一台的搬不了：要「主機名＋IP」或「資產序號」（不然會套到所有未登記的位址上） */
export const canMove = (r: MoveRow) => Boolean((r.hostname && r.ip) || r.asset_serial)
export const WHY_CANNOT_MOVE = '這個 IP 資產庫沒有登記（沒有主機名也沒有序號）——認不出是哪一台，搬了會套到所有未登記的位址上。請先在「掃到未登記」把它登記起來。'

/** 選項＝每一個分類頁簽，一個不少、同順序；同一頁簽（只有一個子類別）標「目前在這」不給選；連線狀態列出但不能選 */
export function moveChoices(rows: MoveRow[], tabs: CatTab[]): MoveOpt[] {
  const here = new Set(rows.map(r => r.tab))
  const only = here.size === 1 ? [...here][0] : null
  const out: MoveOpt[] = []
  for (const t of tabs) {
    if (!t.movable) { out.push({ value: `${t.tab}|`, label: t.label, disabled: true, note: '（連線狀態，不能指定）' }); continue }
    for (const c of t.choices) {
      const multi = t.choices.length > 1
      const isHere = t.tab === only && !multi
      out.push({ value: `${t.tab}|${c.os_type}`, label: multi ? `${t.label} — ${c.label}` : t.label, disabled: isHere,
        note: isHere ? '（目前在這）' : (c.onboardable ? '（會去試連）' : '（另有納管方式，不試連）') })
    }
  }
  return out
}

/** POST /api/adopt/move-tab 的 body（舊頁 submitMove 一字不改）；restore＝還原成機器判定 */
export function moveBody(rows: MoveRow[], choice: string, reason: string, restore: boolean) {
  return {
    targets: rows.map(r => ({ ip: r.ip, hostname: r.hostname, asset_serial: r.asset_serial })),
    tab: restore ? null : choice.split('|')[0],
    os_type: restore ? null : (choice.split('|')[1] || null),
    reason: reason.trim() || null,
  }
}

/** 納管失敗清單的「改分類」：大類換成頁簽名，視窗才講得出「目前在哪」（舊頁 failMoveRow） */
export const FAIL_CLASS_TAB: Record<string, string> = { Linux: 'ssh', Windows: 'winrm', AIX: 'mainframe' }
export function failMoveRow(f: any): MoveRow {
  return { ip: f.ip, asset_serial: f.asset_serial, hostname: f.hostname, message: `納管失敗，目前作業系統大類：${f.os_class}`,
    tab: f.os_override?.tab || FAIL_CLASS_TAB[f.os_class] || '', os_override: f.os_override ? { ...f.os_override } : null }
}

/** 空位址（沒回應又沒登記＝那裡沒有機器）不列（舊頁 isEmptyAddr） */
export const isEmptyAddr = (r: { tab?: string; status?: string; registered?: number | boolean }) => r.tab === 'empty' || (r.status === 'import_only' && !r.registered)

// ===== 批次試密碼／預檢能勾哪幾台（舊頁 canProbe 一字不改；fail-closed）=====
// VMware／儲存／網路／其他不納管、AS400、上次被 VMware 天條擋下的，一律不給。這只是畫面這一道；
// 真正的鎖在後端（onboard_engine 探測到 VMware 就拒絕、預檢用同一份白名單下結論）。
export const NO_ONBOARD_TABS = ['esxi', 'storage', 'network', 'ocp', 'nok_other', 'import', 'agent']
export function canProbe(r: { status?: string; route?: string; guide?: any; tab?: string | null; mainframe_onboardable?: boolean; last_fail_short?: string | null }): boolean {
  if (r.status !== 'needs_credential' && r.status !== 'onboard_failed') return false
  if (r.route !== 'ssh' || r.guide) return false
  if (NO_ONBOARD_TABS.includes(r.tab || '')) return false
  if (r.tab === 'winrm') return false
  if (r.tab === 'mainframe' && !r.mainframe_onboardable) return false
  if (r.last_fail_short === 'VMware 不可納管') return false
  return true
}
/** 預檢會真的登入，一次最多 20 台（舊頁同一個上限） */
export const PRECHECK_MAX = 20

// ===== 6.1「已納管」：收集全部、改分類（批 23 第 7 片，舊頁「已納管主機」）=====
/** 舊頁 OS_TYPE_TAB 一字不改：已納管表沒有頁簽，把這台目前的 OS 類型換成頁簽名，改分類視窗才講得出「目前在哪一類」 */
export const OS_TYPE_TAB: Record<string, string> = {
  Linux: 'ssh', Windows: 'winrm', AIX: 'mainframe', 'IBM i': 'mainframe', 'Storage/SAN': 'storage',
  '網路設備': 'network', VMware: 'esxi', '設備': 'nok_other', 'OpenShift 節點': 'ocp',
  '管理韌體(BMC)': 'nok_other',
}
/** /api/manage-state 那一份（舊頁已納管表的資料來源）：OS 類型與人工指定，只讀 */
export interface ManagedInfo { asset_serial?: string | null; ip?: string | null; hostname?: string | null; os_type?: string | null; os_override?: any
  collect_checked_at?: string | null; state?: string | null; root_key?: boolean }
/** 序號優先、沒有序號才用 IP */
export function managedInfoOf(r: { serial?: string | null; ip?: string | null }, bySerial: Record<string, ManagedInfo>, byIp: Record<string, ManagedInfo>): ManagedInfo | null {
  return (r.serial && bySerial[r.serial]) || (r.ip && byIp[r.ip]) || null
}
/** 舊頁 openMoveManaged 同一個轉換。主機名只用 manage-state 的 hostname（V2 列的 name 可能是資產名稱，不能當主機名送） */
export function managedMoveRow(r: { ip?: string | null; serial?: string | null }, info: ManagedInfo | null): MoveRow {
  const os = info?.os_type || ''
  return { ip: r.ip || '', asset_serial: r.serial || info?.asset_serial || null, hostname: info?.hostname ?? null,
    message: `已納管主機，目前 OS 類型：${os || '未填'}`, tab: OS_TYPE_TAB[os] || '',
    os_override: info?.os_override ? { ...info.os_override, os_type: os, tab: OS_TYPE_TAB[os] || '' } : null }
}
/** 收集全部要送的資產序號＝這張表上勾了、而且有序號的（沒序號的收不了，不送） */
export function collectAllSerials(sel: Record<string, boolean>, rows: { key: string; serial?: string | null }[]): string[] {
  return rows.filter(r => sel[r.key] && r.serial).map(r => r.serial as string)
}
/** 表格重讀後，已經不在表上的勾選丟掉 */
export function pruneKeys(sel: Record<string, boolean>, rows: { key: string }[]): Record<string, boolean> {
  const have = new Set(rows.map(r => r.key))
  const out: Record<string, boolean> = {}
  for (const [k, v] of Object.entries(sel)) if (v && have.has(k)) out[k] = true
  return out
}
// ===== 「另有納管方式」的去處（舊頁 guideWhere；批 23 第 8 片）=====
// 後端 collect_dispatch._NOK_GUIDE 的 page 已經是 V2 網址（2.042.16 起）；萬一是舊網址（例 /san、/hmc），
// 照全站同一張舊→新對照表換成 V2，對照不到就不給連結（不把人帶回舊頁）。
export interface NokGuide { how?: string | null; page?: string | null; page_label?: string | null; note?: string | null }
export function guideHref(g: NokGuide | null | undefined): string | null {
  const p = g?.page
  if (!p) return null
  if (p.startsWith('/v2')) return p
  const [path, qs = ''] = p.split('?')
  const t = v2LegacyTarget(path, Object.fromEntries(new URLSearchParams(qs)))
  if (!t) return null
  const q = new URLSearchParams(t.query).toString()
  return q ? `${t.path}?${q}` : t.path
}
/** 畫面那一格的字：有去處＝「走 SAN 收集」；沒有＝「目前不收：原因」；不是另有納管方式＝空白 */
export function guideText(g: NokGuide | null | undefined): string {
  if (!g) return ''
  if (g.page && g.page_label) return `走 ${g.page_label}`
  return `目前不收${g.note ? `：${g.note}` : ''}`
}

/** 舊頁 collectAllSelected 的確認句，逐字 */
export function collectAllConfirm(n: number, skipped: number): string {
  return `對勾選的 ${n} 台各收一輪全部（硬體規格／服務／軟體／帳號／OS 重建／組態檢核）？\n\n`
    + '・唯讀收集，不會改動目標機。\n・背景逐台排隊跑，一台約數十秒到幾分鐘；可以離開這頁。'
    + (skipped > 0 ? `\n・另有 ${skipped} 台沒有資產序號，收不了，不會送。` : '')
}

// ===== 6.1「已納管」篩選（舊頁 managedShown 同一套；2.058.11）=====
export interface ManagedFilter { os: string; kw: string; tag: string }
/** OS（os_type，空的算「未填」）、標籤、關鍵字（IP／主機名稱／序號，不分大小寫、子字串）三個一起生效——
 *  規則跟舊頁 managedShown、後端 managed_export.filter_rows 一字不差 */
export function managedMatch(m: { ip?: string | null; hostname?: string | null; asset_serial?: string | null; os_type?: string | null; tags?: string[] }, f: ManagedFilter): boolean {
  const k = (f.kw || '').trim().toLowerCase()
  if (f.os && (m.os_type || '未填') !== f.os) return false
  if (f.tag && !(m.tags ?? []).includes(f.tag)) return false
  if (!k) return true
  return [m.ip, m.hostname, m.asset_serial].some((v) => (v || '').toLowerCase().includes(k))
}
/** 舊 Excel（managed_export.headers）在畫面放不下的欄：只在匯出時接在後面（同名、同順序；test_v2_port_b23_managed 釘住） */
export const MANAGED_EXPORT_ONLY = ['狀態', '機房', '環境別', '作業系統（資產登記）', '納管帳號',
  '硬體規格（最近結果）', '服務（最近結果）', '軟體（最近結果）', '帳號（最近結果）', '排程（最近結果）',
  'OS 重建資料（最近結果）', '組態檢核（最近結果）', '六樣收集時間']
/** 匯出那一份（export-rows）對回畫面上的列：序號優先、沒序號才用 IP */
export function joinExportRows<T extends { serial?: string | null; ip?: string | null }>(rows: T[], items: Record<string, any>[]): (T & Record<string, any>)[] {
  const bySerial: Record<string, any> = {}; const byIp: Record<string, any> = {}
  for (const it of items) { if (it['資產序號']) bySerial[it['資產序號']] = it; if (it['IP']) byIp[it['IP']] = it }
  return rows.map((r) => {
    const it = (r.serial && bySerial[r.serial]) || (r.ip && byIp[r.ip]) || {}
    const x: Record<string, any> = {}
    MANAGED_EXPORT_ONLY.forEach((h, i) => { x[`x${i}`] = it[h] ?? '' })
    return { ...r, ...x }
  })
}
