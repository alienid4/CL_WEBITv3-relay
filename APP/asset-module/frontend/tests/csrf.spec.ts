/**
 * Q91 CSRF：前端把 csrf_token cookie 抄進 X-CSRF-Token 標頭的規則（純函式，掛 happy-dom 的 document.cookie）。
 * 後端對應規則見 tests/asset_module/test_csrf_q91.py。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { csrfHeaders, csrfToken, isUnsafeMethod } from '../utils/csrf'

function setCookie(v: string) { document.cookie = v }

afterEach(() => { document.cookie = 'csrf_token=; max-age=0' })

describe('isUnsafeMethod', () => {
  it('GET／HEAD／OPTIONS 是安全方法，其餘會改東西', () => {
    expect(isUnsafeMethod('GET')).toBe(false)
    expect(isUnsafeMethod('get')).toBe(false)
    expect(isUnsafeMethod(undefined)).toBe(false)
    expect(isUnsafeMethod('OPTIONS')).toBe(false)
    for (const m of ['POST', 'PUT', 'PATCH', 'DELETE', 'post']) expect(isUnsafeMethod(m)).toBe(true)
  })
})

describe('csrfHeaders', () => {
  it('有 cookie 時，非 GET 帶 X-CSRF-Token，GET 不帶', () => {
    setCookie('csrf_token=abc123')
    expect(csrfToken()).toBe('abc123')
    expect(csrfHeaders('POST')).toEqual({ 'X-CSRF-Token': 'abc123' })
    expect(csrfHeaders('DELETE')).toEqual({ 'X-CSRF-Token': 'abc123' })
    expect(csrfHeaders('GET')).toEqual({})
  })
  it('沒有 cookie（沒登入／登入停用）時什麼都不帶，不丟錯', () => {
    expect(csrfToken()).toBe('')
    expect(csrfHeaders('POST')).toEqual({})
  })
})
