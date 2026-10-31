// 共用API client：baseURL/credentials/SSR cookie轉送這三件事每個呼叫後端的頁面都要做，
// 抽出來避免S7-S9每頁重複同樣的樣板（D29 API優先設計自然需要的整合點）。
//
// 另外統一處理 401（session 過期）：session TTL 是 8 小時，早上登入的人下午就會過期，
// 這在日常使用中非常常見。沒有統一處理的話，每一頁只會顯示自己的「資料載入失敗，
// 請稍後再試」，使用者看不出是登入過期，只會一直重試然後以為系統壞了——
// 這也是「偶發載入失敗」這類回報的來源之一。正確行為是直接請他重新登入。

// 模組級旗標：一次載入可能同時發好幾支 API（資產頁一次打 5 支），過期時它們會一起 401。
// 沒有這個閘門就會連續觸發多次導轉與多則提示。
let redirectingToLogin = false

// Q91：後端用 403 + code=csrf 表示「安全檢查 token 不符」（例如升版前就登入、cookie 還沒有 token）。
// 處理：抓一次 /api/auth/me（後端會順便補發 csrf cookie），帶新 token 重送一次；仍失敗才把錯誤丟出去。
function isCsrfRejected(err: any): boolean {
  return (err?.statusCode === 403 || err?.response?.status === 403) && err?.data?.code === 'csrf'
}
// 權限不足（等級不夠）：後端的訊息已經是白話（「您的帳號權限是…」），直接跳提示，避免各頁各寫各的「載入失敗」
function isLevelRefused(err: any): boolean {
  return (err?.statusCode === 403 || err?.response?.status === 403)
    && typeof err?.data?.detail === 'string' && err.data.detail.startsWith('您的帳號權限是')
}

export function useApi() {
  const config = useRuntimeConfig()

  function isUnauthorized(err: any): boolean {
    return err?.statusCode === 401 || err?.response?.status === 401
  }

  async function handleSessionExpired() {
    if (redirectingToLogin) return
    redirectingToLogin = true

    // 清掉快取的登入身分，免得 layout 還顯示著使用者名稱
    const user = useState<string | null>('auth-user', () => null)
    const checked = useState<boolean>('auth-checked', () => false)
    user.value = null
    useState<string | null>('auth-level', () => null).value = null
    checked.value = true

    if (import.meta.client) {
      const route = useRoute()
      if (route.path !== '/login') {
        // 講清楚是「登入過期」而不是「系統壞了」，使用者才知道下一步該做什麼
        useToast().showToast('登入已過期，請重新登入', 'warn')
        await navigateTo('/login')
      }
    }
    // 導轉完成後解除閘門，之後真的再過期一次仍要能提示
    redirectingToLogin = false
  }

  async function apiFetch<T>(path: string, opts: Record<string, any> = {}, retried = false): Promise<T> {
    const headers = import.meta.server ? useRequestHeaders(['cookie']) : undefined
    try {
      return await $fetch<T>(path, {
        baseURL: config.public.apiBase,
        credentials: 'include',
        ...opts,
        // CSRF（Q91）：非 GET 一律帶 X-CSRF-Token；呼叫端自己給的 headers 會合併進來
        headers: { ...headers, ...csrfHeaders(opts.method), ...(opts.headers || {}) },
      })
    } catch (err: any) {
      if (isUnauthorized(err)) {
        await handleSessionExpired()
      }
      if (!retried && import.meta.client && isCsrfRejected(err)) {
        try {
          await $fetch('/api/auth/me', { baseURL: config.public.apiBase, credentials: 'include' })
        } catch { /* 連 /me 都失敗（例如登入過期）：沿用下面的錯誤處理 */ }
        return await apiFetch<T>(path, opts, true)
      }
      if (import.meta.client && isLevelRefused(err)) {
        useToast().showToast(err.data.detail, 'warn')
      }
      // 仍然往上拋：呼叫端可能要收掉自己的 loading 狀態，不能把錯誤吞掉
      throw err
    }
  }

  /** 同 apiFetch，但連同回應標頭一起回傳。分頁需要讀 X-Total-Count 才算得出總頁數，
   *  而 $fetch 只給 body。 */
  async function apiFetchWithHeaders<T>(
    path: string, opts: Record<string, any> = {},
  ): Promise<{ data: T; headers: Headers }> {
    const headers = import.meta.server ? useRequestHeaders(['cookie']) : undefined
    try {
      const res = await $fetch.raw<T>(path, {
        baseURL: config.public.apiBase,
        credentials: 'include',
        ...opts,
        headers: { ...headers, ...csrfHeaders(opts.method), ...(opts.headers || {}) },
      })
      return { data: res._data as T, headers: res.headers }
    } catch (err: any) {
      if (isUnauthorized(err)) {
        await handleSessionExpired()
      }
      throw err
    }
  }

  return { apiFetch, apiFetchWithHeaders }
}
