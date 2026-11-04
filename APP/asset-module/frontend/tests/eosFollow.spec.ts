/**
 * EOS 追蹤表單的即時提示：保留依據缺什麼就講什麼（後端才是強制方），重新檢視日建議值只是預填。
 */
import { describe, expect, it } from 'vitest'
import { holdMissing, notifyText, ownerText, statusTone, suggestReviewDate, validDate } from '../utils/eosFollow'

const classes = [
  { name: '原廠或廠商相容性', need_note: false },
  { name: '業務單位不同意', need_note: true },
]
const ok = { hold_reason_class: '原廠或廠商相容性', hold_basis_text: '廠商只支援此版', hold_evidence_ref: '信件 10/1', hold_review_date: '2027-04-30' }

describe('holdMissing 保留依據缺什麼', () => {
  it('齊全 → 空陣列', () => expect(holdMissing(ok, classes)).toEqual([]))
  it('逐項缺：類別／說明／證據／重新檢視日', () => {
    expect(holdMissing({ ...ok, hold_reason_class: '' }, classes)).toEqual(['阻礙來源類別'])
    expect(holdMissing({ ...ok, hold_basis_text: '  ' }, classes)).toEqual(['依據說明'])
    expect(holdMissing({ ...ok, hold_evidence_ref: null }, classes)).toEqual(['證據參照'])
    expect(holdMissing({ ...ok, hold_review_date: '2027-02-30' }, classes)).toEqual(['重新檢視日'])
  })
  it('需說明的類別沒說明；未登記的類別會指名而不是默默歸其他', () => {
    expect(holdMissing({ ...ok, hold_reason_class: '業務單位不同意' }, classes)[0]).toContain('需填說明')
    expect(holdMissing({ ...ok, hold_reason_class: '天氣不好' }, classes)[0]).toContain('不在類別清單')
  })
  it('全空 → 四項都列出（記錄人／日期由伺服器自動記）', () => expect(holdMissing({}, classes)).toHaveLength(4))
})

describe('日期', () => {
  it('validDate 只收真的存在的 YYYY-MM-DD', () => {
    expect(validDate('2026-11-01')).toBe(true)
    for (const bad of ['2026-13-01', '2026-02-30', '2026/11/01', '', null, undefined]) expect(validDate(bad as any)).toBe(false)
  })
  it('建議值＝N 個月後（月底不溢位），只是預填', () => {
    expect(suggestReviewDate('2026-11-01', 6)).toBe('2027-05-01')
    expect(suggestReviewDate('2026-08-31', 6)).toBe('2027-02-28')
    expect(suggestReviewDate('2026-11-01', 12)).toBe('2027-11-01')
  })
})

describe('顯示文字', () => {
  it('通知三態分開', () => {
    expect(notifyText({ notified: true, notified_at: '2026-10-12 08:00:00' })).toBe('已通知 2026-10-12')
    expect(notifyText({ notified: false, last_send_failed: true })).toContain('寄送失敗')
    expect(notifyText({ notified: true, notified_at: '2026-10-12 08:00:00', last_send_failed: true })).toBe('已通知 2026-10-12（最近一次寄送失敗）')   // 失敗與已通知並存
    expect(notifyText({ notified: false, notify_map: 'unmapped' })).toBe('無法對應通知紀錄')
    expect(notifyText({ notified: false, notify_map: 'recipient_unverified' })).toBe('收件地址無法確認（待核對）')
    expect(notifyText({ notified: true, notified_at: '2026-10-12 08:00:00', recipient_unrecorded: true })).toBe('已通知 2026-10-12・收件地址未記錄（舊資料）')
    expect(notifyText({ notified: false })).toBe('尚未通知')
  })
  it('待指派不是空白', () => {
    expect(ownerText({ owner_ref: '待指派' })).toContain('沒有負責人')
    expect(ownerText({ owner_ref: '王小明', owner_role: 'AP' })).toBe('王小明（AP）')
  })
  it('狀態語氣：完成綠、待辦橘、其餘灰', () => {
    expect(statusTone(['done'])).toBe('prod')
    expect(statusTone(['unreplied'])).toBe('test')
    expect(statusTone(['hold_expiring'])).toBe('none')
    expect(statusTone(['voided'])).toBe('none')
  })
})
