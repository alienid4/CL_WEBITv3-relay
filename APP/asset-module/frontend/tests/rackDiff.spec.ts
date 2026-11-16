/** 機櫃差異待辦（2.043.17）：回報與結案表單缺什麼就擋。 */
import { describe, expect, it } from 'vitest'
import { REASONS, STATUS, actProblem, actSummary, needsBind, needsTarget, reportProblem } from '../utils/rackDiff'

const R = { reason: '', note: '', target_rack: '', target_u_start: '', bind_q: '' }
const A = { note: '', target_rack: '', target_u_start: '', bind_q: '', approver: '', basis: '' }

describe('rackDiff', () => {
  it('六種狀態、兩組原因', () => {
    expect(Object.keys(STATUS)).toEqual(['pending', 'matched', 'cia', 'excel', 'retire_wait', 'closed'])
    expect(Object.keys(REASONS.sys)).toContain('off')
    expect(Object.keys(REASONS.xl)).toEqual(['absent', 'wrongpos', 'bind'])
    expect(REASONS.sys.removed).toContain('這台不在這格')
    expect(REASONS.xl.absent).toBe('這格其實是空的')
    expect(REASONS.xl.wrongpos).toBe('位置寫錯了')
  })
  it('回報：沒選原因、不確定沒備註、Excel 位置不對沒填位置、綁定沒寫哪台都擋', () => {
    expect(reportProblem('sys', R)).toBe('請選原因')
    expect(reportProblem('sys', { ...R, reason: 'unsure' })).toContain('備註')
    expect(reportProblem('sys', { ...R, reason: 'unsure', note: '看到空的' })).toBe('')
    expect(reportProblem('xl', { ...R, reason: 'wrongpos' })).toContain('實際在哪')
    expect(reportProblem('xl', { ...R, reason: 'wrongpos', target_rack: 'B8', target_u_start: '7' })).toBe('')
    expect(reportProblem('xl', { ...R, reason: 'bind' })).toContain('哪一台')
    expect(reportProblem('sys', { ...R, reason: 'moved' })).toBe('')
    expect(reportProblem('sys', { ...R, reason: 'moved', target_rack: 'B8' })).toContain('第幾 U')
    expect(needsTarget('sys', 'moved') && needsTarget('xl', 'wrongpos') && !needsTarget('xl', 'absent')).toBe(true)
    expect(needsBind('xl', 'bind') && !needsBind('sys', 'bind')).toBe(true)
  })
  it('結案：駁回要原因、退役要核准人與依據、搬移要位置（回報時填過就不用）', () => {
    expect(actProblem('reject', A, {})).toContain('原因')
    expect(actProblem('approve_retire', { ...A, approver: '甲' }, {})).toContain('核准人')
    expect(actProblem('approve_retire', { ...A, approver: '甲', basis: '簽呈' }, {})).toBe('')
    expect(actProblem('move', A, {})).toContain('新位置')
    expect(actProblem('move', A, { target_rack: 'B8', target_u_start: 3 })).toBe('')
    expect(actProblem('bind', A, { bind_key: 'k' })).toBe('')
    expect(actProblem('confirm_off', A, {})).toBe('')
  })
  it('正式資料動作送出前有確認摘要（會改哪台、改成什麼、能不能還原）；只改待辦狀態的不用', () => {
    const it0 = { subject: 'sys', room: '內湖機房', rack: 'B8', u_text: '5U', who: 'moved2' }
    const F = { target_rack: '', target_u_start: '', target_u_end: '', bind_q: '', approver: '', basis: '' }
    const off = actSummary('confirm_off', it0, F)!
    expect(off.lines[0]).toContain('moved2')
    expect(off.lines.join('')).toContain('保留這段 U')
    expect(off.undo).toContain('可以還原')
    expect(actSummary('move', it0, { ...F, target_rack: 'C2', target_u_start: '7', target_u_end: '8' })!.lines[0]).toContain('C2 7-8U')
    expect(actSummary('approve_retire', it0, { ...F, approver: '甲', basis: '簽呈' })!.lines.join('')).toContain('核准人 甲')
    expect(actSummary('to_excel', it0, F)).toBeNull()
    expect(actSummary('reject', it0, F)).toBeNull()
  })
})
