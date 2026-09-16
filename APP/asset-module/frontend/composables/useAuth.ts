// S6：登入狀態管理。session本體是後端httponly cookie，前端讀不到也不用讀，
// 這裡只快取「目前是誰登入」這件事，實際驗證永遠以後端 /api/auth/me 回應為準。
interface MeResponse {
  username: string
}

export function useAuth() {
  const user = useState<string | null>('auth-user', () => null)
  const checked = useState<boolean>('auth-checked', () => false)
  const config = useRuntimeConfig()

  async function fetchMe() {
    try {
      const headers = import.meta.server ? useRequestHeaders(['cookie']) : undefined
      const res = await $fetch<MeResponse>('/api/auth/me', {
        baseURL: config.public.apiBase,
        credentials: 'include',
        headers,
      })
      user.value = res.username
    } catch {
      user.value = null
    } finally {
      checked.value = true
    }
  }

  async function login(username: string, password: string) {
    const res = await $fetch<MeResponse>('/api/auth/login', {
      baseURL: config.public.apiBase,
      method: 'POST',
      credentials: 'include',
      body: { username, password },
    })
    user.value = res.username
    checked.value = true
  }

  async function logout() {
    await $fetch('/api/auth/logout', {
      baseURL: config.public.apiBase,
      method: 'POST',
      credentials: 'include',
    })
    user.value = null
  }

  return { user, checked, fetchMe, login, logout }
}
