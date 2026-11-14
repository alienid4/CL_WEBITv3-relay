// Q91 CSRF 的最後一道網：全站有 20 幾處直接用 fetch() 呼叫後端（下載、上傳檔案、匯出），不經 useApi。
// 這裡把 window.fetch 包一層：只要是往本站後端（apiBase 或 /api/ 開頭）的非 GET 請求，沒帶 X-CSRF-Token 就自動補上。
// 這樣新寫的 fetch() 也不會漏；useApi／useAuth／useTableExport 另外也明確帶（雙保險，標頭重複設定是無害的）。
import { csrfHeaders } from '~/utils/csrf'

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  const original = window.fetch.bind(window)
  const apiBase = String(config.public.apiBase || '')

  function toBackend(url: string): boolean {
    if (url.startsWith('/api/')) return true
    return !!apiBase && url.startsWith(apiBase)
  }

  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    try {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      const method = init?.method ?? (typeof input === 'object' && 'method' in input ? (input as Request).method : 'GET')
      if (toBackend(url)) {
        const extra = csrfHeaders(method)
        if (Object.keys(extra).length) {
          const headers = new Headers(init?.headers ?? (typeof input === 'object' && 'headers' in input ? (input as Request).headers : undefined))
          for (const [k, v] of Object.entries(extra)) if (!headers.has(k)) headers.set(k, v)
          return original(input, { ...init, headers })
        }
      }
    } catch {
      // 包裝本身出任何問題都不能讓請求送不出去：退回原樣送（最壞就是被後端 403，畫面會顯示原因）
    }
    return original(input, init)
  }
})
