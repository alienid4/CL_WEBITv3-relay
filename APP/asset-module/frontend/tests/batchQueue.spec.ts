/**
 * 勾選多台一次納管：自動停止之後不可以卡在「等待中」（v1.468.4）。
 *
 * 2026-10-19 使用者（公司機）：「失敗會卡住」——7 台，前 4 台登入被拒、系統自動停下，
 * 剩下 3 台一直寫「等待中」、計數寫「等待 3」，看起來像當掉。
 * 例子 IP 一律 10.99.x。
 */
import { describe, expect, it } from 'vitest'
import { runQueue, isAuthFail, type QRow } from '../composables/batchQueue'

interface Row extends QRow { ip: string }
const mk = (n: number): Row[] =>
  Array.from({ length: n }, (_, i) => ({ ip: `10.99.7.${i + 1}`, state: 'waiting', message: '', stage: '' }))
const REJECT = '登入被拒——帳號或密碼不對，或該帳號不允許 SSH 登入'

describe('runQueue', () => {
  it('前 4 台帳密被拒 → 自動停止，剩下 3 台標「未執行（已停止）」，沒有任何一列還在等待／進行中', async () => {
    const rows = mk(7)
    const res = await runQueue(rows, async (r) => { r.state = 'failed'; r.message = REJECT }, 4)
    expect(res.autoStopped).toBe(true)
    expect(res.authFailed).toBe(4)
    expect(res.notRun.map((r) => r.ip)).toEqual(['10.99.7.5', '10.99.7.6', '10.99.7.7'])
    expect(rows.filter((r) => r.state === 'stopped')).toHaveLength(3)
    // 這是使用者踩到的坑：停了卻寫「等待中」
    expect(rows.some((r) => r.state === 'waiting' || r.state === 'running')).toBe(false)
  })

  it('有一台成功過就不自動停（密碼是對的，失敗是那台自己的問題）', async () => {
    const rows = mk(6)
    const res = await runQueue(rows, async (r) => {
      if (r.ip === '10.99.7.1') { r.state = 'ok'; return }
      r.state = 'failed'; r.message = REJECT
    }, 1)
    expect(res.autoStopped).toBe(false)
    expect(rows.filter((r) => r.state === 'failed')).toHaveLength(5)
  })

  it('某一台丟例外／沒有改狀態：那台標失敗，整輪照樣結束', async () => {
    const rows = mk(3)
    const res = await runQueue(rows, async (r) => {
      if (r.ip === '10.99.7.1') throw new Error('network down')
      if (r.ip === '10.99.7.2') return            // 沒改狀態（不該發生，但不能因此卡住）
      r.state = 'ok'
    }, 2)
    expect(res.autoStopped).toBe(false)
    expect(rows.map((r) => r.state)).toEqual(['failed', 'failed', 'ok'])
  })

  it('非帳密的失敗（沒有 sudo）不觸發自動停止', async () => {
    const rows = mk(5)
    const res = await runQueue(rows, async (r) => { r.state = 'failed'; r.message = '沒有 sudo 權限' }, 2)
    expect(res.autoStopped).toBe(false)
    expect(rows.every((r) => r.state === 'failed')).toBe(true)
  })

  it('後端的「登入被拒」字樣算帳密錯', () => {
    expect(isAuthFail({ state: 'failed', message: REJECT })).toBe(true)
    expect(isAuthFail({ state: 'failed', message: 'Permission denied (publickey,password)' })).toBe(true)
    expect(isAuthFail({ state: 'ok', message: REJECT })).toBe(false)
  })
})
