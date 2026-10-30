// Q91 CSRF：後端登入時發一顆 JS 讀得到的 csrf_token cookie；每個會改東西的請求（POST/PUT/PATCH/DELETE）
// 要把它抄進 X-CSRF-Token 標頭，後端比對不符就回 403（code=csrf）。
// 用 cookie 的值而不是記在變數裡：重新整理頁面、多個分頁、/api/auth/me 補發新值之後都還是最新的。
export const CSRF_COOKIE = 'csrf_token'
export const CSRF_HEADER = 'X-CSRF-Token'
const SAFE = new Set(['GET', 'HEAD', 'OPTIONS'])

/** 目前的 csrf token（瀏覽器端才讀得到 cookie；伺服器端渲染時回空字串）。 */
export function csrfToken(): string {
  if (typeof document === 'undefined') return ''
  for (const part of document.cookie.split(';')) {
    const i = part.indexOf('=')
    if (i > 0 && part.slice(0, i).trim() === CSRF_COOKIE) {
      try { return decodeURIComponent(part.slice(i + 1).trim()) } catch { return part.slice(i + 1).trim() }
    }
  }
  return ''
}

export function isUnsafeMethod(method?: string): boolean {
  return !SAFE.has(String(method || 'GET').toUpperCase())
}

/** 要加進請求標頭的 CSRF 欄位；安全方法或沒有 token 時回空物件。 */
export function csrfHeaders(method?: string): Record<string, string> {
  if (!isUnsafeMethod(method)) return {}
  const t = csrfToken()
  return t ? { [CSRF_HEADER]: t } : {}
}
