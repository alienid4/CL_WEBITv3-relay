// 2.1 主機頁直接納管（2.069.3）：「這台現在能做哪些動作、為什麼」的純規則，加 6.1 待納管篩不到時的唯讀查詢。
// 視窗、帳密、納管 API 一律沿用 6.1 的元件（OnboardModal／WinOnboardModal；盤中靜默由後端 quiet_deps 擋），這裡不碰密碼。
// 原則：fail-closed——任何一項資料讀不到／缺欄位，一律「納管狀態查不到」、動作全部停用，不猜。

export type Phase = 'blocked' | 'loading' | 'unknown' | 'onboarded' | 'stale' | 'failed' | 'not_onboarded'

export interface Load<T = any> { st: 'loading' | 'ok' | 'error'; data?: T; err?: string }
export interface LastAttempt { ip?: string; ok?: number; platform?: string | null; serial?: string | null; fail?: { at?: string; short?: string; message?: string } | null }
export interface TargetCheck { ok: boolean; why?: string | null; serial?: string | null }

/** 納管入口只擋停用／報廢（2026-11-22：「閒置」可能是待啟用的新機，不擋；與後端 onboard_eligibility.LOGIN_BLOCKED_STATUS 同一組）。 */
export const RETIRED_STATUS = ['停用', '報廢']

export interface PlanInput {
  // 主機本身（來自 /api/v2/machine）
  vmware?: boolean | null            // 後端 is_vmware（納管同一個入口）
  osClass?: string | null            // badge.os_class；「虛擬化平台」＝ESXi／vCenter
  badgeCode?: string | null          // badge.code；ESX＝VMware 設備
  retired?: boolean | null           // who_is.retired（key 以 ret: 開頭）
  assetStatus?: string | null
  lpar?: boolean                     // 明確標示 LPAR（count_bridge 角色 lpar）；不是「任何 AIX」
  onboarded?: boolean | null         // alive.onboarded（＝7 天內收集成功）
  collect?: 'fresh' | 'stale' | null | undefined   // alive.collect.status；undefined＝後端沒給這個欄位
  ips: string[]
  serials: string[]
  hostname?: string | null
  os?: string | null                 // CIA 登記 OS（最後才參考）
  measuredOs?: string | null         // 系統實測 OS（優先於登記）
  isAdmin: boolean
  // 非同步資料
  attempts: Load<Record<string, LastAttempt>>
  win: Load<Record<string, { opted_in?: boolean }>>
  check: Load<Record<string, TargetCheck>>
  pickedIp: string                   // 多 IP 時使用者明確選的那個；單 IP 不用
}

export interface HostPlan {
  phase: Phase
  mgmt: string                       // 管理關係：已納管／未納管／不納管／納管狀態查不到／讀取中…
  collectText: string                // 最近收集結果（全站六態詞：正常／過期／失敗／只收到一半／沒收到／待設定）
  reason: string                     // 動作被停用／不給的原因（有動作時為空）
  canOnboard: boolean                // 未納管：「納管」
  canRetry: boolean                  // 納管失敗：「重新納管」
  canReonboard: boolean              // 已納管但過期：次要的「重新納管」
  canRecollect: boolean              // 已納管（含過期）：「收集全部」
  canReload: boolean                 // 查不到時：重新讀取
  needPick: boolean                  // 多 IP 還沒選
  targetIp: string
  platform: 'windows' | 'ssh' | null
  platformBasis: string
  optedIn: boolean                   // Windows 視窗用：真實的 Windows 納管狀態（未納管＝false）
  fail: { ip: string; short: string; at: string; platform: string | null } | null
  recollectSerial: string | null
  recollectNote: string
}

const WIN = /windows/i
const clean = (x: any) => String(x ?? '').trim()

/** 跨 IP 取「最新一次失敗」（依時間，不是 IP 陣列第一筆）。後來成功（fail 為 null）的不算。 */
export function latestFailure(ips: string[], attempts: Record<string, LastAttempt> | null | undefined): HostPlan['fail'] {
  let best: HostPlan['fail'] = null
  let bestAt = ''
  for (const ip of ips) {
    const a = attempts?.[ip]
    if (!a || a.ok || !a.fail) continue
    const at = clean(a.fail.at)
    if (!best || at > bestAt) { best = { ip, short: a.fail.short || a.fail.message || '原因不明', at: at.slice(0, 16), platform: a.platform ?? null }; bestAt = at }
  }
  return best
}

export function isWindowsHost(os: string | null | undefined, lastPlatform?: string | null): boolean {
  return WIN.test(clean(os)) || WIN.test(clean(lastPlatform))
}

/** 這台走哪個視窗，依據排序：Windows 納管狀態（6.1 同一支 /api/onboard/windows/state）→ 系統實測 OS → 上次納管紀錄的平台 → CIA 登記 OS。
 *  全部都沒有就回 null（不猜）。 */
export function decidePlatform(i: { ip: string; winOptedIn: boolean; measuredOs?: string | null; lastPlatform?: string | null; os?: string | null }):
  { platform: 'windows' | 'ssh' | null; basis: string } {
  if (i.winOptedIn) return { platform: 'windows', basis: '已是 Windows 納管（WinRM）' }
  if (clean(i.measuredOs)) return WIN.test(clean(i.measuredOs)) ? { platform: 'windows', basis: `系統實測 OS：${clean(i.measuredOs)}` } : { platform: 'ssh', basis: `系統實測 OS：${clean(i.measuredOs)}（SSH）` }
  if (clean(i.lastPlatform)) return WIN.test(clean(i.lastPlatform)) ? { platform: 'windows', basis: '上次納管紀錄：Windows' } : { platform: 'ssh', basis: `上次納管紀錄：${clean(i.lastPlatform)}（SSH）` }
  if (clean(i.os)) return WIN.test(clean(i.os)) ? { platform: 'windows', basis: `CIA 登記 OS：${clean(i.os)}（未實測）` } : { platform: 'ssh', basis: `CIA 登記 OS：${clean(i.os)}（未實測，SSH）` }
  return { platform: null, basis: '' }
}

const NONE: Omit<HostPlan, 'phase' | 'mgmt' | 'collectText' | 'reason'> = {
  canOnboard: false, canRetry: false, canReonboard: false, canRecollect: false, canReload: false, needPick: false, targetIp: '', platform: null,
  platformBasis: '', optedIn: false, fail: null, recollectSerial: null, recollectNote: '',
}
const out = (phase: Phase, mgmt: string, collectText: string, reason: string, extra: Partial<HostPlan> = {}): HostPlan => ({ ...NONE, phase, mgmt, collectText, reason, ...extra })

/** 重新收集用哪個資產序號：這個 IP 掛的登記（check.serial，納管全站同一把尺）且在這台的登記清單裡；
 *  對不上才退回第一個，並註明（多筆登記時）。 */
export function pickRecollectSerial(serials: string[], ips: string[], pickedIp: string, check: Load<Record<string, TargetCheck>>): { serial: string | null; note: string } {
  const list = serials.filter(Boolean)
  if (!list.length) return { serial: null, note: '' }
  const order = [...(pickedIp ? [pickedIp] : []), ...ips.filter(x => x !== pickedIp)]
  if (check.st === 'ok') for (const ip of order) { const s = check.data?.[ip]?.serial; if (s && list.includes(s)) return { serial: s, note: '' } }
  if (list.length === 1) return { serial: list[0], note: '' }
  return { serial: list[0], note: `此台有多筆登記，收集使用序號 ${list[0]}` }
}

/** 一定不給動作的情況（天條：VMware 不納管、退役不納管）。回原因或 null。元件開頭用它決定要不要去讀納管資料。 */
export function preBlock(i: Pick<PlanInput, 'vmware' | 'osClass' | 'badgeCode' | 'retired' | 'assetStatus'>): string | null {
  const status = clean(i.assetStatus)
  if (i.vmware === true || i.osClass === '虛擬化平台' || i.badgeCode === 'ESX') return 'VMware 設備（ESXi／vCenter）不納管，資料取自 vCenter'
  if (i.retired || RETIRED_STATUS.includes(status)) return `${status || '退役'}的主機不納管、不收集`
  return null
}

/** 選定（或唯一）的 IP → 能不能納管、走哪個視窗；讀不到的一律不給。 */
function resolveTarget(i: PlanInput, ips: string[]):
  { ok: true; targetIp: string; platform: 'windows' | 'ssh'; basis: string; opted: boolean } | { ok: false; kind: 'pick' | 'unknown' | 'blocked' | 'noplatform'; reason: string; targetIp: string } {
  const targetIp = ips.length === 1 ? ips[0] : (ips.includes(i.pickedIp) ? i.pickedIp : '')
  if (!targetIp) return { ok: false, kind: 'pick', reason: '有多個 IP，請先選要納管的那一個', targetIp }
  const chk = i.check.data?.[targetIp]
  if (!chk) return { ok: false, kind: 'unknown', reason: `讀不到 ${targetIp} 的納管資格`, targetIp }
  if (!chk.ok) return { ok: false, kind: 'blocked', reason: chk.why || '伺服器判定這個位址不可納管', targetIp }
  const opted = i.win.data?.[targetIp]?.opted_in === true
  const pf = decidePlatform({ ip: targetIp, winOptedIn: opted, measuredOs: i.measuredOs, lastPlatform: i.attempts.data?.[targetIp]?.platform, os: i.os })
  if (!pf.platform) return { ok: false, kind: 'noplatform', reason: '查不到作業系統，判不出走 SSH 還是 Windows，請到 6.1 納管作業處理', targetIp }
  return { ok: true, targetIp, platform: pf.platform, basis: pf.basis, opted }
}

/** 主機頁納管欄的唯一判斷（天條：VMware 不納管、退役不納管；查不到一律停用）。 */
export function planHost(i: PlanInput): HostPlan {
  // 1. 不納管（天條）——先於一切；任何一個訊號命中就擋（m.vmware、OS 大類、類型標記、退役）
  const pb = preBlock(i)
  if (pb) return out('blocked', '不納管', '', pb)
  // 2. 查不到＝fail-closed
  if (i.vmware === undefined || i.vmware === null) return out('unknown', '納管狀態查不到', '', '讀不到是不是 VMware 設備（資料缺欄位），先不給動作', { canReload: true })
  if (typeof i.onboarded !== 'boolean' || i.collect === undefined) return out('unknown', '納管狀態查不到', '', '讀不到納管狀態（資料缺欄位），先不給動作', { canReload: true })
  const ips = [...new Set((i.ips ?? []).map(clean).filter(Boolean))]
  const stale = !i.onboarded && i.collect === 'stale'
  const rc = pickRecollectSerial(i.serials ?? [], ips, i.pickedIp, i.check)
  const lpar = i.lpar === true

  // 3. 已納管（含過期）：主動作是「收集全部」，不是納管
  if (i.onboarded || stale) {
    const waitSerial = i.check.st === 'loading' && (i.serials ?? []).filter(Boolean).length > 1   // 多筆登記要等 IP→序號對照，不先用第一筆
    const can = i.isAdmin && !!rc.serial && !waitSerial
    const reason = !i.isAdmin ? '收集需管理員' : !rc.serial ? '這台沒有資產序號，收不了' : waitSerial ? '讀取中' : ''
    // 過期時次要的「重新納管」：三份資料都讀到、資格通過、判得出平台才給（納管流程不是主動作）
    let reon: Partial<HostPlan> = {}
    if (stale && i.isAdmin && !lpar && [i.attempts, i.win, i.check].every(l => l.st === 'ok')) {
      const t = resolveTarget(i, ips)
      if (t.ok) reon = { canReonboard: true, targetIp: t.targetIp, platform: t.platform, platformBasis: t.basis, optedIn: t.opted }
    }
    return out(stale ? 'stale' : 'onboarded', '已納管', stale ? '過期（已納管，超過 7 天沒連上）' : '正常（7 天內有連線）', reason,
      { recollectSerial: rc.serial, recollectNote: rc.note, canRecollect: can, ...reon })
  }

  // 4. 未納管（或納管失敗）：LPAR 不給；其餘要三份資料都讀得到才給動作
  if (lpar) return out('blocked', '不納管', '', 'LPAR 由 HMC 管理，不走主機納管')
  const loads = [i.attempts, i.win, i.check]
  if (loads.some(l => l.st === 'loading')) return out('loading', '讀取中…', '', '讀取中')
  const bad = loads.find(l => l.st === 'error')
  if (bad) return out('unknown', '納管狀態查不到', '', bad.err || '讀取失敗', { canReload: true })
  if (!ips.length) return out('unknown', '納管狀態查不到', '', '這台沒有 IP，不能納管')

  const fail = latestFailure(ips, i.attempts.data)
  const phase: Phase = fail ? 'failed' : 'not_onboarded'
  const collectText = fail ? `失敗：${fail.short}${fail.at ? `（${fail.at}）` : ''}` : '沒收到'
  const base = { fail, recollectSerial: rc.serial, recollectNote: rc.note }
  if (!i.isAdmin) return out(phase, '未納管', collectText, '納管需管理員', base)

  const t = resolveTarget(i, ips)
  if (!t.ok) {
    if (t.kind === 'pick') return out(phase, '未納管', collectText, t.reason, { ...base, needPick: true })
    if (t.kind === 'unknown') return out('unknown', '納管狀態查不到', '', t.reason, { ...base, canReload: true, targetIp: t.targetIp })
    if (t.kind === 'blocked') return out('blocked', '不納管', '', t.reason, { ...base, targetIp: t.targetIp })
    return out(phase, '未納管', collectText, t.reason, { ...base, targetIp: t.targetIp })
  }
  const ex = { ...base, targetIp: t.targetIp, platform: t.platform, platformBasis: t.basis, optedIn: t.opted }
  return out(phase, '未納管', collectText, '', phase === 'failed' ? { ...ex, canRetry: true } : { ...ex, canOnboard: true })
}

/** 重新收集的確認句：寫出主機名、IP、資產序號、六項內容。 */
export function recollectConfirm(host: string, ip: string, serial: string): string {
  return `對這台收一輪全部（硬體規格／服務／軟體／帳號／OS 重建／組態檢核）？\n\n主機：${host || '—'}\nIP：${ip || '—'}\n資產序號：${serial}\n\n`
    + '・唯讀收集，不會改動目標機。\n・背景排隊跑，約數十秒到幾分鐘；可以離開這頁。'
}

/** post-collect-batch 的回應要真的排進去才算成功：missing 不空、或 added+already 為 0 都是失敗。 */
export function recollectOutcome(r: any): { ok: boolean; msg: string } {
  const missing: string[] = Array.isArray(r?.missing) ? r.missing : []
  const n = Number(r?.added ?? 0) + Number(r?.already ?? 0)
  if (missing.length) return { ok: false, msg: `沒有排入：資料庫查不到資產序號 ${missing.join('、')}` }
  if (n < 1) return { ok: false, msg: '沒有排入任何一台（伺服器回 0 台）' }
  return { ok: true, msg: `已排入 ${r.added ?? 0} 台${r.already ? `（${r.already} 台本來就在排隊）` : ''}` }
}

// ===== 6.1 待納管篩選為空時的提示 =====
export type MissState = 'onboarded' | 'stale' | 'failed' | 'candidates' | 'none' | 'error'
export interface Miss { state: MissState; name?: string; key?: string; reason?: string; n?: number; names?: string[]; err?: string }

/** 已納管：GET /api/v2/machine?q=（同 2.1，alive.onboarded／alive.collect.status）；失敗：GET /api/onboard/failures（同 6.1 納管失敗分頁，未解決的）。
 *  cache.failures 在同一頁面生命週期內只抓一次。查詢出錯回 error（不是 none），讓畫面分得出「真的沒有」與「查不到」。 */
export async function lookupPendingMiss(apiFetch: (u: string, o?: any) => Promise<any>, qText: string, cache: { failures?: any } = {}): Promise<Miss> {
  const text = qText.trim()
  if (!text) return { state: 'none' }
  const errs: string[] = []
  let cand: Miss | null = null
  try {
    const m = await apiFetch('/api/v2/machine', { query: { q: text } })
    if (m?.mode === 'machine') {
      if (m.alive?.onboarded) return { state: 'onboarded', name: m.name, key: m.key }
      if (m.alive?.collect?.status === 'stale') return { state: 'stale', name: m.name, key: m.key }
    } else if (m?.mode === 'candidates' && (m.candidates?.length ?? 0) > 0) {
      cand = { state: 'candidates', n: m.total ?? m.candidates.length, names: m.candidates.slice(0, 5).map((c: any) => c.hostname || c.ip || c.key) }
    }
  } catch (e: any) { errs.push(`主機查詢：${e?.data?.detail ?? e?.message ?? '失敗'}`) }
  try {
    if (!cache.failures) cache.failures = await apiFetch('/api/onboard/failures')
    const low = text.toLowerCase()
    const hit = (cache.failures?.items ?? []).find((r: any) => !r.resolved && (r.ip === text || (r.hostname && String(r.hostname).toLowerCase() === low)))
    if (hit) return { state: 'failed', name: hit.hostname || hit.ip, reason: hit.reason }
  } catch (e: any) { errs.push(`納管失敗清單：${e?.data?.detail ?? e?.message ?? '失敗'}`) }
  if (cand) return cand
  if (errs.length) return { state: 'error', err: errs.join('；') }
  return { state: 'none' }
}
