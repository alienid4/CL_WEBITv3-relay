/**
 * 帳號合規狀態欄（3-8 帳號合規表）與帳號儀表板（3-7）共用的判定。
 *
 * 儀表板的「中招數」跟合規表點進去的筆數必須是同一份計算（加總＝下鑽），
 * 所以兩頁都用這裡的 cell 函式；儀表板數字＝ rows.filter(r => CELL[col](r).k === val).length，
 * 下鑽就是合規表 `?col=<col>&val=<val>&os=<桶>`，合規表用同一個函式篩。
 *
 * Windows 沒有 sudo／UID／SSH 金鑰／shell（2026-10-21 使用者）：sudo 對應「本機管理員」，其餘標不適用、不判紅。
 */

export interface CellAccount {
  username: string
  uid: number | null
  pw_status: string | null
  pw_expiry_status: string
  is_sudoer: number
  sudo_nopasswd: number
  authorized_keys: number | null
  never_logged_in: number
  can_login?: number | null
  os_family?: string | null
  login_source?: string | null
  os_bucket?: string | null
}

// c: bad(紅=有問題) / warn(黃=注意) / ok(綠=正常) / dim(灰=無/不適用) / needroot(藍=查不到)
export type Cell = { t: string; c: string; k: string }

export const isWin = (a: { os_family?: string | null }) => a.os_family === 'windows'

// passwd -S 狀態碼跨發行版有 set/PS/P、locked/LK/L、empty/NP 多種寫法，
// 這裡收斂成 set/locked/empty/null，前端才不會把 LK 當成「沒鎖」。
export function pwState(a: { pw_status: string | null }): 'set' | 'locked' | 'empty' | null {
  const s = (a.pw_status || '').toUpperCase()
  if (s === '') return null
  if (s === 'LOCKED' || s === 'LK' || s === 'L') return 'locked'
  if (s === 'EMPTY' || s === 'NP') return 'empty'
  if (s === 'SET' || s === 'PS' || s === 'P') return 'set'
  return null // 未知碼＝查不到，寧可標需 root 也不謊報啟用中
}

export function mPwExpired(a: CellAccount): Cell {
  const s = a.pw_expiry_status
  if (s === 'expired') return { t: '已過期', c: 'bad', k: 'expired' }
  if (s === 'never') return { t: '永不過期', c: 'bad', k: 'never' }
  if (s === 'valid') return { t: '未過期', c: 'ok', k: 'valid' }
  if (s === 'unknown') return { t: '需 root', c: 'needroot', k: 'needroot' }
  return { t: '—', c: 'dim', k: 'na' }
}
export function mDisabled(a: CellAccount): Cell {
  if (isWin(a) && pwState(a) !== 'locked') return a.can_login ? { t: '啟用中', c: '', k: 'active' } : { t: '已停用', c: 'ok', k: 'locked' }
  const s = pwState(a)
  if (s === 'locked') return { t: '已停用', c: 'ok', k: 'locked' }
  if (s === null) return { t: '需 root', c: 'needroot', k: 'needroot' }
  return { t: '啟用中', c: '', k: 'active' }
}
export function mSudo(a: CellAccount): Cell {
  if (isWin(a)) return a.is_sudoer ? { t: '本機管理員', c: 'warn', k: 'admin' } : { t: '否', c: 'dim', k: 'no' }
  if (a.uid === 0) return { t: 'UID 0', c: 'bad', k: 'uid0' }
  if (a.sudo_nopasswd) return { t: '是·免密碼', c: 'bad', k: 'nopw' }
  if (a.is_sudoer) return { t: '是', c: 'warn', k: 'yes' }
  return { t: '否', c: 'dim', k: 'no' }
}
export function mEmpty(a: CellAccount): Cell {
  if (isWin(a) && pwState(a) === null) return { t: '不適用', c: 'dim', k: 'na' }
  const s = pwState(a)
  if (s === null) return { t: '需 root', c: 'needroot', k: 'needroot' }
  if (s === 'empty') return { t: '是', c: 'bad', k: 'yes' }
  return { t: '否', c: 'dim', k: 'no' }
}
export function mUid0(a: CellAccount): Cell {
  if (isWin(a)) return { t: '不適用', c: 'dim', k: 'na' }
  if (a.uid === 0 && a.username !== 'root') return { t: '是', c: 'bad', k: 'yes' }
  if (a.uid === 0) return { t: 'root', c: 'dim', k: 'root' }
  return { t: '否', c: 'dim', k: 'no' }
}
export function mKeys(a: CellAccount): Cell {
  if (isWin(a)) return { t: '不適用', c: 'dim', k: 'na' }
  if (a.authorized_keys === null || a.authorized_keys === undefined) return { t: '需 root', c: 'needroot', k: 'needroot' }
  if (a.authorized_keys > 0) return { t: `${a.authorized_keys} 把`, c: 'warn', k: 'has' }
  return { t: '無', c: 'dim', k: 'none' }
}
export function mNeverLogin(a: CellAccount): Cell {
  if (a.never_logged_in) return { t: '從未登入', c: 'warn', k: 'never' }
  // Windows 只有走 SAMR 才知道最後登入；沒走到＝查不到，不可以寫成「有」（後端 _cell_login 同字）
  if (isWin(a) && (a.login_source || '') !== 'samr') return { t: '查不到', c: 'needroot', k: 'needroot' }
  return { t: '有', c: 'dim', k: 'yes' }
}

export const ACCOUNT_CELLS: Record<string, (a: CellAccount) => Cell> = {
  pwExpired: mPwExpired, disabled: mDisabled, sudo: mSudo, empty: mEmpty,
  uid0: mUid0, keys: mKeys, login: mNeverLogin,
}

// ===== 帳號儀表板 OS 分頁（2026-10-21 使用者：「3-7 帳號儀表板 這要分 WINDOWS 跟 LINUX」）=====
// 桶由後端 account_inventory.host_os_buckets 決定（收集時機器回報的 os_family 優先，退回資產庫 OS）。
export type OsBucket = 'linux' | 'windows' | 'aix' | 'ocp' | 'unknown'
export type OsTab = OsBucket | 'all'
export const OS_BUCKETS: OsBucket[] = ['linux', 'windows', 'aix', 'ocp', 'unknown']
export const OS_LABEL: Record<OsTab, string> = {
  linux: 'Linux', windows: 'Windows', aix: 'AIX', ocp: 'OCP', unknown: 'OS 未知', all: '全部',
}

/** 分頁列：Linux／Windows 固定出現；AIX、OCP、OS 未知有主機才出現（照樣計數，不默默丟）；最後是全部。
 *  OCP 的台數＝資產庫的 OCP 節點數（ocpNodes，跟 OCP 叢集頁同一份），不是有帳號資料的台數——
 *  OCP 第一期不納管、沒帳密，有帳號資料的通常是 0；「全部」只加有帳號資料的（counts.ocp）。 */
export function osTabs(counts: Partial<Record<OsBucket, number>>, ocpNodes = 0): { key: OsTab; label: string; n: number }[] {
  const n = (b: OsBucket) => counts[b] ?? 0
  const tabs: { key: OsTab; label: string; n: number }[] = [
    { key: 'linux', label: OS_LABEL.linux, n: n('linux') },
    { key: 'windows', label: OS_LABEL.windows, n: n('windows') },
  ]
  if (n('aix') > 0) tabs.push({ key: 'aix', label: OS_LABEL.aix, n: n('aix') })
  if (ocpNodes > 0 || n('ocp') > 0) tabs.push({ key: 'ocp', label: OS_LABEL.ocp, n: Math.max(ocpNodes, n('ocp')) })
  if (n('unknown') > 0) tabs.push({ key: 'unknown', label: OS_LABEL.unknown, n: n('unknown') })
  tabs.push({ key: 'all', label: OS_LABEL.all, n: OS_BUCKETS.reduce((s, b) => s + n(b), 0) })
  return tabs
}

/** 預設分頁：記住的那個還在就用；不然主機最多的 OS（使用者環境是 Linux 最多）；都沒有就全部。 */
export function defaultOsTab(counts: Partial<Record<OsBucket, number>>, remembered?: string | null, ocpNodes = 0): OsTab {
  const tabs = osTabs(counts, ocpNodes)
  if (remembered && tabs.some((t) => t.key === remembered)) return remembered as OsTab
  const best = tabs.filter((t) => t.key !== 'all' && t.key !== 'ocp').sort((a, b) => b.n - a.n)[0]
  return best && best.n > 0 ? best.key : 'all'
}

interface CheckDef {
  col: string; val: string; label: string; tone: string
  // unix＝Linux／AIX 才有的概念（Windows 不顯示）；win＝Windows 才有；空＝都適用
  only?: 'unix' | 'win'
}
// 順序就是畫面順序。sudo／UID 0／金鑰在 Windows 不存在 → only:'unix'；本機管理員／已停用 → only:'win'
// （已停用是 Windows 帳號盤點的重點；Linux 分頁照舊不擺，避免改動既有 Linux 畫面）
export const CHECK_DEFS: CheckDef[] = [
  { col: 'pwExpired', val: 'expired', label: '密碼已過期', tone: 'bad' },
  { col: 'pwExpired', val: 'never', label: '密碼永不過期', tone: 'bad' },
  { col: 'empty', val: 'yes', label: '空密碼', tone: 'bad' },
  { col: 'uid0', val: 'yes', label: 'UID 0 非 root', tone: 'bad', only: 'unix' },
  { col: 'sudo', val: 'nopw', label: '免密碼 sudo', tone: 'bad', only: 'unix' },
  { col: 'sudo', val: 'yes', label: '有 sudo 權限', tone: 'warn', only: 'unix' },
  { col: 'keys', val: 'has', label: '免密碼金鑰', tone: 'warn', only: 'unix' },
  { col: 'sudo', val: 'admin', label: '本機管理員', tone: 'warn', only: 'win' },
  { col: 'disabled', val: 'locked', label: '已停用', tone: '', only: 'win' },
  { col: 'login', val: 'never', label: '從未登入', tone: 'warn' },
]

export interface CheckCard {
  key: string; label: string; scope: string; tone: string
  n: number | null           // null＝這批帳號全部查不到，畫面顯示「查不到」不是 0
  unknown: number            // 查不到（需 root／Windows 沒走 SAMR）幾個
  to: string; unknownTo: string
}

/**
 * 某個分頁要顯示哪些合規中招卡、各幾個。
 * - Linux／AIX／OS 未知：不出現 Windows 專屬卡（本機管理員、已停用）。
 *   OS 未知桶不會有 Windows 帳號（Windows 收集器一定標 os_family=windows），所以照 Linux 判
 * - Windows：不出現 sudo／UID 0／金鑰卡（不適用就不擺，不擺 0）
 * - 全部：都擺，但 OS 專屬卡標「不含 Windows」「僅 Windows」，且只數那些 OS 的帳號
 */
export function dashboardChecks(rows: CellAccount[], tab: OsTab): CheckCard[] {
  const mixed = tab === 'all'
  const out: CheckCard[] = []
  for (const d of CHECK_DEFS) {
    if (d.only === 'unix' && tab === 'windows') continue
    if (d.only === 'win' && tab !== 'windows' && !mixed) continue
    let pool = rows
    let scope = ''
    let osQ = tab === 'all' ? '' : tab
    if (mixed && d.only === 'unix') { pool = rows.filter((r) => !isWin(r)); scope = '不含 Windows' }
    if (mixed && d.only === 'win') {
      pool = rows.filter((r) => isWin(r)); scope = '僅 Windows'
      if (tab === 'all') osQ = 'windows'
    }
    const fn = ACCOUNT_CELLS[d.col]
    let hit = 0, unk = 0
    for (const r of pool) {
      const k = fn(r).k
      if (k === d.val) hit++
      else if (k === 'needroot') unk++
    }
    const os = osQ ? `&os=${osQ}` : ''
    out.push({
      key: `${d.col}:${d.val}`, label: d.label, scope, tone: d.tone,
      n: pool.length > 0 && unk === pool.length ? null : hit,
      unknown: unk,
      to: `/account-matrix?col=${d.col}&val=${d.val}${os}`,
      unknownTo: `/account-matrix?col=${d.col}&val=needroot${os}`,
    })
  }
  return out
}

/**
 * 特權帳號組成（現值，可下鑽）。用合規表 sudo 欄的分類（UID 0／免密碼 sudo／有 sudo／本機管理員互斥），
 * 數字＝點進去的筆數。Windows 只有本機管理員＋標準管理帳號。
 */
export function privBreakdown(rows: (CellAccount & { kind?: string | null })[], tab: OsTab) {
  const os = tab === 'all' ? '' : `&os=${tab}`
  const cnt = (val: string) => rows.filter((r) => mSudo(r).k === val).length
  const items: { label: string; n: number; to: string }[] = []
  if (tab !== 'windows') {
    items.push({ label: 'UID 0', n: cnt('uid0'), to: `/account-matrix?col=sudo&val=uid0${os}` })
    items.push({ label: '免密碼 sudo', n: cnt('nopw'), to: `/account-matrix?col=sudo&val=nopw${os}` })
    items.push({ label: '有 sudo', n: cnt('yes'), to: `/account-matrix?col=sudo&val=yes${os}` })
  }
  if (tab === 'windows' || tab === 'all') {
    items.push({ label: '本機管理員', n: cnt('admin'), to: `/account-matrix?col=sudo&val=admin${tab === 'all' ? '&os=windows' : os}` })
  }
  items.push({ label: '標準管理帳號', n: rows.filter((r) => r.kind === 'mgmt').length, to: `/account-matrix?tab=accounts&kind=mgmt${os}` })
  return items
}
