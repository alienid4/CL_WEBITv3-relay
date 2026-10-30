/**
 * 帳號儀表板 OS 分頁（2026-10-21）：卡片集合依 OS、數字＝合規表點進去的筆數、查不到不顯示 0。
 */
import { describe, expect, it } from 'vitest'
import { ACCOUNT_CELLS, dashboardChecks, defaultOsTab, mNeverLogin, osTabs, privBreakdown } from '../composables/accountCells'
import type { CellAccount } from '../composables/accountCells'

const base: CellAccount = {
  username: 'u', uid: 1000, pw_status: 'set', pw_expiry_status: 'valid', is_sudoer: 0,
  sudo_nopasswd: 0, authorized_keys: 0, never_logged_in: 0, os_family: 'rhel', os_bucket: 'linux',
}
const lx = (o: Partial<CellAccount>): CellAccount => ({ ...base, ...o })
const win = (o: Partial<CellAccount>): CellAccount => ({
  ...base, uid: null, authorized_keys: null, os_family: 'windows', os_bucket: 'windows',
  login_source: 'samr', can_login: 1, ...o,
})

const rows: CellAccount[] = [
  lx({ username: 'root', uid: 0 }),
  lx({ username: 'bd', uid: 0 }),                         // UID 0 非 root
  lx({ username: 'ops', is_sudoer: 1, sudo_nopasswd: 1 }),
  lx({ username: 'dev', is_sudoer: 1, authorized_keys: 2 }),
  win({ username: 'Administrator', is_sudoer: 1 }),
  win({ username: 'old', pw_status: 'locked', can_login: 0 }),
  win({ username: 'svc', login_source: null }),           // 沒走 SAMR：曾登入查不到
]
const keys = (cs: { key: string }[]) => cs.map((c) => c.key)

describe('dashboardChecks 依 OS 分頁擺卡', () => {
  it('Windows 分頁：沒有 sudo／UID 0／金鑰卡，有本機管理員與已停用', () => {
    const w = rows.filter((r) => r.os_bucket === 'windows')
    const cs = dashboardChecks(w, 'windows')
    expect(keys(cs)).not.toContain('uid0:yes')
    expect(keys(cs)).not.toContain('sudo:nopw')
    expect(keys(cs)).not.toContain('sudo:yes')
    expect(keys(cs)).not.toContain('keys:has')
    expect(cs.map((c) => c.label)).not.toContain('有 sudo 權限')
    const by = Object.fromEntries(cs.map((c) => [c.key, c]))
    expect(by['sudo:admin'].n).toBe(1)
    expect(by['disabled:locked'].n).toBe(1)
    expect(by['sudo:admin'].to).toBe('/account-matrix?col=sudo&val=admin&os=windows')
  })

  it('Linux 分頁：沒有 Windows 專屬卡；數字跟合規表欄位判定一致', () => {
    const l = rows.filter((r) => r.os_bucket === 'linux')
    const cs = dashboardChecks(l, 'linux')
    expect(keys(cs)).not.toContain('sudo:admin')
    expect(keys(cs)).not.toContain('disabled:locked')
    for (const c of cs) {
      const [col, val] = c.key.split(':')
      expect(c.n).toBe(l.filter((r) => ACCOUNT_CELLS[col](r).k === val).length)
      expect(c.to).toContain('&os=linux')
    }
    const by = Object.fromEntries(cs.map((c) => [c.key, c]))
    expect(by['uid0:yes'].n).toBe(1)
    expect(by['sudo:nopw'].n).toBe(1)
    expect(by['sudo:yes'].n).toBe(1)
    expect(by['keys:has'].n).toBe(1)
  })

  it('全部分頁：OS 專屬卡標範圍，且只數那些 OS 的帳號', () => {
    const by = Object.fromEntries(dashboardChecks(rows, 'all').map((c) => [c.key, c]))
    expect(by['uid0:yes'].scope).toBe('不含 Windows')
    expect(by['sudo:admin'].scope).toBe('僅 Windows')
    expect(by['sudo:admin'].to).toContain('&os=windows')
    expect(by['disabled:locked'].n).toBe(1)               // 只數 Windows 那支，不混 Linux
    expect(by['pwExpired:expired'].scope).toBe('')
  })

  it('全部查不到 → n=null（畫面顯示「查不到」不是 0）；部分查不到另外計數', () => {
    const allUnk = [lx({ username: 'a', authorized_keys: null }), lx({ username: 'b', authorized_keys: null })]
    const k = dashboardChecks(allUnk, 'linux').find((c) => c.key === 'keys:has')!
    expect(k.n).toBeNull()
    expect(k.unknownTo).toBe('/account-matrix?col=keys&val=needroot&os=linux')
    const login = dashboardChecks(rows.filter((r) => r.os_bucket === 'windows'), 'windows')
      .find((c) => c.key === 'login:never')!
    expect(login.n).toBe(0)
    expect(login.unknown).toBe(1)
  })
})

describe('Windows 曾登入', () => {
  it('沒走 SAMR＝查不到，不可以寫成「有」', () => {
    expect(mNeverLogin(win({ login_source: null })).k).toBe('needroot')
    expect(mNeverLogin(win({})).k).toBe('yes')
    expect(mNeverLogin(lx({})).k).toBe('yes')
  })
})

describe('OS 分頁列與預設', () => {
  it('AIX／OS 未知有主機才出現；全部＝加總', () => {
    expect(osTabs({ linux: 5, windows: 2 }).map((t) => t.key)).toEqual(['linux', 'windows', 'all'])
    const t = osTabs({ linux: 5, windows: 2, aix: 1, unknown: 3 })
    expect(t.map((x) => x.key)).toEqual(['linux', 'windows', 'aix', 'unknown', 'all'])
    expect(t.find((x) => x.key === 'all')!.n).toBe(11)
  })
  it('預設主機最多的 OS；記住的分頁還在就用；記住的不存在就退回', () => {
    expect(defaultOsTab({ linux: 200, windows: 40 })).toBe('linux')
    expect(defaultOsTab({ linux: 200, windows: 40 }, 'windows')).toBe('windows')
    expect(defaultOsTab({ linux: 200, windows: 40 }, 'aix')).toBe('linux')
    expect(defaultOsTab({}, null)).toBe('all')
  })
})

describe('特權組成', () => {
  it('Windows 只有本機管理員＋標準管理帳號，沒有 sudo／UID 0', () => {
    const p = privBreakdown(rows.filter((r) => r.os_bucket === 'windows'), 'windows').map((x) => x.label)
    expect(p).toEqual(['本機管理員', '標準管理帳號'])
  })
  it('Linux 互斥分類：UID 0（含 root）／免密碼 sudo／有 sudo', () => {
    const p = Object.fromEntries(privBreakdown(rows.filter((r) => r.os_bucket === 'linux'), 'linux').map((x) => [x.label, x.n]))
    expect(p).toEqual({ 'UID 0': 2, '免密碼 sudo': 1, '有 sudo': 1, '標準管理帳號': 0 })
  })
})
