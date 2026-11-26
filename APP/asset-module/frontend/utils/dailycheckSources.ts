// 1.5 每日檢查「資料來源」頁籤：每一種資料有沒有進來、多久前、要不要處理（純函式，測試用 node 直接載入）。
// 狀態固定六種（CLAUDE.md「每一頁分三層」）：正常／過期／失敗／只收到一半／沒收到／待設定。
// 只用後端已經回的資料推（/api/daily-check 的逐列、/lastpoll、/vmware），不改判定與台數（D3）。
// 逐列推出來的來源以「這次快照」為準（快照當下的判定）；上次輪循、VMware 用即時的批次狀態。

export type SrcStatus = '正常' | '過期' | '失敗' | '只收到一半' | '沒收到' | '待設定'
export const SRC_STATUSES: SrcStatus[] = ['正常', '過期', '失敗', '只收到一半', '沒收到', '待設定']
/** V2Table tone：prod＝綠、test＝紅、dr＝黃 */
export const SRC_TONE: Record<SrcStatus, string> = { 正常: 'prod', 過期: 'test', 失敗: 'test', 只收到一半: 'dr', 沒收到: 'test', 待設定: 'dr' }
/** 會出現在「要你處理」的狀態（待設定另外處理：核准／設定 vCenter 有自己的按鈕） */
export const SRC_NEEDS_LOOK: SrcStatus[] = ['過期', '失敗', '只收到一半', '沒收到']

export const SRC_DEFS: { key: string; name: string }[] = [
  { key: 'alert', name: 'WhatsUp 告警' },
  { key: 'lastpoll', name: 'WhatsUp 上次輪循' },
  { key: 'perf', name: 'WhatsUp 前一日效能與磁碟' },
  { key: 'vmware', name: 'VMware（vROps）' },
  { key: 'storage', name: 'Unity 儲存' },
  { key: 'adtime', name: 'AD 對時' },
]

export interface SrcRow {
  __no: number; key: string; name: string; status: SrcStatus; tone: string
  at: string; next: string; codes: string[]; act: '' | 'approve' | 'setvm' | 'settings'; sub: string
}

/** 每一列的「來源」文字 → 資料來源代號；對不到（人工列）回 '' */
export function srcKeyOf(source: string | null | undefined): string {
  const s = String(source || '')
  if (/告警快照/.test(s)) return 'alert'
  if (/上次輪循/.test(s)) return 'lastpoll'
  if (/vROps|VMware/.test(s)) return 'vmware'
  if (/Unity/.test(s)) return 'storage'
  if (/AD 對時/.test(s)) return 'adtime'
  if (/效能|磁碟|健康檢查/.test(s)) return 'perf'
  return ''
}

const METRIC_NAME: Record<string, string> = { cpu: 'CPU', mem: '記憶體', disk: '硬碟' }
/** 收集端的英文原因翻成白話（只收到一半時顯示；認不得就不顯示原文以外的猜測） */
export function partialWhy(err?: string | null): string {
  const e = String(err || '')
  if (!e) return ''
  if (/report cap/i.test(e)) return 'WhatsUp 一次最多給 100 台，逐台補問沒補完'
  if (/per-device cpu/i.test(e)) return '逐台補問時 WhatsUp 沒回應'
  if (/time box/i.test(e)) return '超過收集時限'
  if (/call budget|MaxCalls/i.test(e)) return '呼叫次數用完'
  if (/page cap|pages read|more pages/i.test(e)) return '報表頁數上限不夠，沒讀完'
  if (/HTTP\s*5\d\d|timed? ?out|connection/i.test(e)) return 'WhatsUp 沒回應'
  if (/HTTP\s*4\d\d/i.test(e)) return 'WhatsUp 拒絕查詢'
  return '收集沒讀完'                      // 11-15 使用者：回報講中文；原文在資料來源的收集紀錄裡
}
const RANK: Record<SrcStatus, number> = { 失敗: 5, 沒收到: 4, 過期: 3, 只收到一半: 2, 待設定: 1, 正常: 0 }
const worst = (a: SrcStatus, b: SrcStatus): SrcStatus => (RANK[b] > RANK[a] ? b : a)
const cut = (s: string, n = 90) => (s.length > n ? `${s.slice(0, n)}…` : s)

/** 一列「無資料」的原因 → 狀態 */
export function reasonStatus(reason: string | null | undefined, hasTime: boolean): SrcStatus {
  const r = String(reason || '')
  if (/太舊|過期/.test(r)) return '過期'
  if (/失敗|錯誤|拒收/.test(r)) return '失敗'
  if (/不完整|沒讀完|不確定|未知/.test(r)) return '只收到一半'
  if (/未設定|未手填/.test(r)) return '待設定'
  return hasTime ? '失敗' : '沒收到'
}

/** 這次快照裡屬於同一個來源的列（人工列不算）→ 狀態、最後收到、下一步 */
export function rowsStatus(rows: any[]): { status: SrcStatus; at: string; next: string; codes: string[] } | null {
  const rs = (rows || []).filter(r => r && r.verdict !== 'manual')
  if (!rs.length) return null
  const at = rs.map(r => String(r.data_at || '')).filter(Boolean).sort().pop() || ''
  const nod = rs.filter(r => r.verdict === 'nodata')
  if (!nod.length) return { status: '正常', at, next: '不用處理', codes: [] }
  let st: SrcStatus = '正常'
  for (const r of nod) st = worst(st, reasonStatus(r.reason, !!r.data_at))
  const codes = nod.map(r => String(r.code || '')).filter(Boolean)
  const why = String(nod[0].reason || '—')
  const withData = rs.length - nod.length
  if (withData > 0 && st !== '待設定') return { status: '只收到一半', at, next: cut(`缺 ${codes.join('、')} 的資料：${why}`), codes }
  return { status: st, at, next: cut(`${codes.join('、')}：${why}`), codes }
}

const hm = (t: string | null | undefined) => String(t || '').slice(11, 16)

/** WhatsUp 上次輪循：用即時的批次狀態（/api/daily-check/lastpoll）。nowHM＝伺服器現在幾點（HH:MM），收集時段外不算過期。 */
export function lastpollStatus(lpst: any, freshMin: number, nowHM: string, lpAlert?: string | null): { status: SrcStatus; at: string; next: string; act: SrcRow['act'] } | null {
  if (!lpst) return null
  const latest = lpst.latest
  if (!latest) return { status: '沒收到', at: '', next: '還沒收到資料，請確認中繼機收集排程', act: '' }
  const at = String(latest.collected_at || '')
  const lc = lpst.latest_complete
  const groups: any[] = lpst.groups || []
  const failed = groups.filter(g => !g.ok)
  if (failed.length) return { status: '失敗', at, next: cut(`群組「${failed[0].path}」收集失敗：${failed[0].error || '—'}${failed.length > 1 ? `（共 ${failed.length} 個群組）` : ''}`), act: '' }
  if (lpAlert) return { status: '過期', at, next: cut(String(lpAlert)), act: '' }
  if (!lc) return { status: '只收到一半', at, next: cut(`沒有完整的一批：${(latest.problems || [])[0] || '—'}`), act: '' }
  const inWindow = nowHM >= '05:30' && nowHM <= '18:00'
  const fresh = Number(lpst.fresh_min ?? freshMin ?? 35)
  if (inWindow && Number(lc.age_min) > fresh) return { status: '過期', at, next: `${lc.age_min} 分鐘沒更新，請確認收集排程`, act: '' }
  if (!latest.complete) return { status: '只收到一半', at, next: cut(`最新一批不完整（${(latest.problems || [])[0] || '—'}）；判定用 ${hm(lc.collected_at)} 那批完整的`), act: '' }
  const part = groups.filter(g => (g.partial || []).length)
  if (part.length) {
    const g = part[0]
    const k = g.partial[0]
    const why = partialWhy(g.partial_why?.[k]?.error)
    const got = g.partial_why?.[k]?.devices
    const miss = got != null && g.members ? `讀到 ${got}／${g.members} 台` : ''
    return { status: '只收到一半', at, next: cut(`「${g.path.split('>').pop()?.trim()}」${METRIC_NAME[k] ?? k} ${[miss, why].filter(Boolean).join('，')}；下一輪會自動再收`), act: '' }
  }
  const unscoped = groups.filter(g => g.ok && !g.scope_ok)
  if (unscoped.length) return { status: '待設定', at, next: `${unscoped.length} 個群組成員變了（範圍未確認），要核准`, act: 'approve' }
  return { status: '正常', at, next: '不用處理', act: '' }
}

/** VMware（vROps）：一天收一次（06:00、06:20、07:30）。超過 26 小時沒有完整批次＝過期；
 *  而且過了 VM_DUE_HM（最後一輪 07:30 之後）還沒有「今天」的完整批次也算過期——不然早上兩輪都失敗時，
 *  昨天 07:30 那批到中午前都還不到 26 小時，這頁會顯示正常，跟 VC 列的「無資料（太舊）」對不上（11-14 獨立審查 P2）。
 *  nowYMD／nowHM＝伺服器現在的日期與時間（/api/daily-check 的 now）；沒給就只用 26 小時那條。沒設定 vCenter 清單＝待設定。 */
export const VM_DAILY_MAX_MIN = 26 * 60
export const VM_DUE_HM = '07:45'
export function vmwareStatus(vmst: any, vmConfigured: boolean, nowYMD = '', nowHM = '', direct = false): { status: SrcStatus; at: string; next: string; act: SrcRow['act'] } | null {
  if (!vmst) return null
  const latest = vmst.latest
  if (!latest) return { status: '沒收到', at: '', next: direct ? '還沒收到任何一批（系統直抓：看「系統設定 › 外部整合 › Aria Operations」的執行紀錄）' : '還沒收到任何一批（跳板機每天 06:00 起讀 vROps）', act: '' }
  const at = String(latest.collected_at || '')
  const lc = vmst.latest_complete
  if (!lc) {
    const p0 = String((latest.problems || [])[0] || '')
    if (p0.startsWith('vROps 登入失敗')) return { status: '失敗', at, next: cut(`${p0}；本次沒有取得任何資料，VC 列會是無資料`), act: '' }
    return { status: '只收到一半', at, next: cut(`沒有完整的一批：${p0 || '—'}，VC 列會是無資料`), act: '' }
  }
  if (Number(lc.age_min) > VM_DAILY_MAX_MIN) return { status: '過期', at, next: `最新完整一批是 ${Math.round(Number(lc.age_min) / 60)} 小時前，${direct ? '請看系統設定 › 外部整合 › Aria Operations 的執行紀錄與憑證' : '請檢查跳板機的排程'}`, act: '' }
  // 系統直抓：到期線是後端回報的採用截止（預設 08:00），不是舊排程的 07:45；採用截止前直抓還在執行（或還有時槽）就不標過期
  const dueHM = direct ? String(vmst.direct?.window?.cutoff || '08:00') : VM_DUE_HM
  const lastStart = direct ? String(vmst.direct?.window?.last_start || '07:40') : ''
  // running 只能在採用截止（dueHM）之前抑制「過期」；截止後今天還沒收到就是過期（執行中另外放在三欄的「最近一次執行」）
  const stillTrying = direct && !vmst.direct?.gave_up_today && nowHM < dueHM && (!!vmst.direct?.running || (!!lastStart && nowHM <= lastStart))
  if (nowYMD && nowHM >= dueHM && !stillTrying && String(lc.collected_at || '').slice(0, 10) < nowYMD) return { status: '過期', at, next: `今天 ${dueHM} 前沒收到完整的一批（最新是 ${String(lc.collected_at || '').slice(0, 16)}），${direct ? '請看系統設定 › 外部整合 › Aria Operations 的執行紀錄與憑證' : '請檢查跳板機的排程'}`, act: '' }
  if (!latest.complete) return { status: '只收到一半', at, next: cut(`最新一批不完整（${(latest.problems || [])[0] || '—'}）；判定用 ${hm(lc.collected_at)} 那批完整的`), act: '' }
  if (!vmConfigured) return { status: '待設定', at, next: '資料已收到，還沒設定 vCenter 清單（VC 列暫時人工）', act: 'setvm' }
  return { status: '正常', at, next: '不用處理', act: '' }
}

/** VMware 三個維度分開看（固定詞：正常／過期／失敗／只收到一半／沒收到／待設定）：今日收件、最近有效批次的新鮮度、最近一次執行結果。 */
export interface VmThree { today: { status: SrcStatus; text: string }; fresh: { status: SrcStatus; text: string }; run: { status: SrcStatus; text: string }; running: boolean }
export function vmwareThree(vmst: any): VmThree | null {
  if (!vmst) return null
  const t = vmst.today, lc = vmst.latest_complete, ad = vmst.adopted, d = vmst.direct || {}
  const src = (s: any) => (s === 'direct' ? '系統直抓' : s === 'relay' ? '中繼批次' : '—')
  const today: VmThree['today'] = t?.complete
    ? { status: '正常', text: `今天窗內已收到完整批次（${src(t.source)}・${String(t.collected_at || '').slice(11, 16)}）；1.5 採用 ${ad?.batch_id ?? '—'}` }
    : { status: '沒收到', text: vmst.latest && !vmst.latest.complete && String(vmst.latest.collected_at || '').slice(0, 10) === String(t?.day || '') ? `今天只有不完整的批次：${String((vmst.latest.problems || [])[0] || '—').slice(0, 60)}` : '今天窗內沒有完整批次' }
  const fresh: VmThree['fresh'] = !lc ? { status: '沒收到', text: '從沒收到完整批次' }
    : Number(lc.age_min) > VM_DAILY_MAX_MIN ? { status: '過期', text: `最近有效批次 ${String(lc.collected_at).slice(0, 16)}（${Math.round(Number(lc.age_min) / 60)} 小時前）` }
    : { status: '正常', text: `最近有效批次 ${String(lc.collected_at).slice(0, 16)}（${src(lc.source)}）` }
  const r = d.last_run
  let run: VmThree['run']
  if (d.gave_up_today) run = { status: '失敗', text: `今天不會再自動重試：${String(d.gave_up_today.message || '').slice(0, 80)}` }
  else if (!r) run = d.enabled ? { status: '沒收到', text: '還沒有執行紀錄' } : { status: '待設定', text: '系統直抓未開啟（目前走跳板機備援）' }
  else if (r.status === 'running') run = { status: '沒收到', text: `${String(r.at).slice(5, 16)} 開始（還沒有完成紀錄）` }
  else if (r.status === 'ok') run = { status: '正常', text: `${String(r.at).slice(5, 16)} 成功` }
  else if (r.status === 'incomplete') run = { status: r.error_class === 'auth' ? '失敗' : '只收到一半', text: `${String(r.at).slice(5, 16)} 不完整${r.error_class ? `（${r.error_class}）` : ''}` }
  else run = { status: '失敗', text: `${String(r.at).slice(5, 16)} ${r.status}${r.error_class ? `（${r.error_class}）` : ''}` }
  if (!d.enabled && r) run.text += '；開關：關'
  if (d.next_try && run.status !== '正常') run.text += `；下次可試 ${String(d.next_try).slice(11, 16)}`
  if (d.auth_limit && d.auth_rejects_today) run.text += `；登入被拒 ${d.auth_rejects_today}／${d.auth_limit} 次`
  // 執行中以後端的 running 旗標為準（不看最後一列的狀態）；狀態詞照最近一筆結果，不當成正常或成功
  const running = !!d.running
  if (running) run.text = `執行中；上一次：${run.text}`
  return { today, fresh, run, running }
}

export interface SrcInput {
  rows: any[]; lpst?: any; vmst?: any; perfSource?: string; vmConfigured: boolean
  lpFreshMin?: number; nowHM?: string; nowYMD?: string; lpAlert?: string | null
}

/** 全部資料來源（固定順序、固定項次 D1～D6；這次沒用到的來源不列） */
export function buildSources(i: SrcInput): SrcRow[] {
  const byKey: Record<string, any[]> = {}
  for (const r of i.rows || []) { const k = srcKeyOf(r?.source); if (k) (byKey[k] ||= []).push(r) }
  const out: SrcRow[] = []
  SRC_DEFS.forEach((def, idx) => {
    let s: { status: SrcStatus; at: string; next: string; codes?: string[]; act?: SrcRow['act'] } | null = null
    if (def.key === 'lastpoll') s = i.perfSource === 'w3' ? null : lastpollStatus(i.lpst, Number(i.lpFreshMin ?? 35), String(i.nowHM || '12:00'), i.lpAlert)
    else if (def.key === 'vmware') s = vmwareStatus(i.vmst, i.vmConfigured, String(i.nowYMD || ''), String(i.nowHM || ''), !!i.vmst?.direct?.enabled)
    else s = rowsStatus(byKey[def.key] || [])
    if (!s) return
    const codes = s.codes ?? (byKey[def.key] || []).filter(r => r.verdict === 'nodata').map(r => String(r.code || ''))
    const act: SrcRow['act'] = s.act || (s.status === '待設定' ? 'settings' : '')
    out.push({ __no: idx + 1, key: def.key, name: def.name, status: s.status, tone: SRC_TONE[s.status], at: s.at, next: s.next,
      codes, act, sub: def.key === 'vmware' ? 'vm' : def.key === 'lastpoll' ? 'wug' : 'list' })
  })
  return out
}

/** 每日檢查的提示文字（run.warnings／warnings_now）→ 去設定的哪個子頁籤 */
export function warningSub(w: string): string {
  if (/VMware|vCenter/.test(w)) return 'vm'
  if (/上次輪循|W3|群組/.test(w)) return 'wug'
  return 'list'
}
