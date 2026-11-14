/**
 * 密碼代號（v1.468.6）：只記代號，絕不存密碼。例子一律假值。
 */
import { describe, expect, it } from 'vitest'
import { authFailTag, shortFailReason, validateLabel } from '../composables/passwordLabel'

describe('validateLabel', () => {
  it('簡短代號可以；空的＝不用代號', () => {
    expect(validateLabel('A', 'Xy7$abcd')).toBeNull()
    expect(validateLabel('PAM-1', 'Xy7$abcd')).toBeNull()
    expect(validateLabel('', 'Xy7$abcd')).toBeNull()
  })
  it('等於密碼、是密碼的一部分 → 擋', () => {
    expect(validateLabel('Xy7$abcd', 'Xy7$abcd')).toMatch(/不可以是密碼/)
    expect(validateLabel('abcd', 'Xy7$abcd')).toMatch(/不可以是密碼/)
  })
  it('看起來像密碼（長、混大小寫數字符號）→ 擋；太長 → 擋', () => {
    expect(validateLabel('Pa55w0rd!', '')).toMatch(/看起來像密碼/)
    expect(validateLabel('ABCDEFGHIJKLM', '')).toMatch(/最多 12/)
  })
})

describe('authFailTag', () => {
  it('標籤＝試過<代號>失敗；備註只有時間＋原因', () => {
    const t = authFailTag('A', '帳密被拒', new Date(2026, 9, 19, 21, 5))
    expect(t).toEqual({ tag: '試過A失敗', note: '2026-10-19 21:05 帳密被拒' })
  })
})

describe('shortFailReason', () => {
  it('後端訊息 → 短原因', () => {
    expect(shortFailReason('登入被拒——帳號或密碼不對')).toBe('帳密被拒')
    expect(shortFailReason('沒有 sudo 權限')).toBe('沒有 sudo')
    expect(shortFailReason('其他')).toBe('納管失敗')
  })
})
