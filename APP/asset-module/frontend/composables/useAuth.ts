// S6：登入狀態管理。session本體是後端httponly cookie，前端讀不到也不用讀，
// 這裡只快取「目前是誰登入」這件事，實際驗證永遠以後端 /api/auth/me 回應為準。
//
// Q91：多快取一個「權限等級」(level)：admin 管理員／user 一般／viewer 只能看。
// 前端只用它決定「藏掉或停用做不到的按鈕」；真正擋人的是後端每支 API（access_policy）。
// 登入停用時後端回 level=admin（既有功能照舊全開）。
export type AccessLevel = 'admin' | 'user' | 'viewer'

interface MeResponse {
  username: string
  level?: AccessLevel
}

const RANK: Record<AccessLevel, number> = { viewer: 0, user: 1, admin: 2 }

export function useAuth() {
  const user = useState<string | null>('auth-user', () => null)
  const level = useState<AccessLevel | null>('auth-level', () => null)
  const checked = useState<boolean>('auth-checked', () => false)
  const config = useRuntimeConfig()

  /** 目前帳號的等級是否至少是 need。沒查到等級（舊後端／未登入）時：只放行 viewer 能做的，不亂放大權限。 */
  function can(need: AccessLevel): boolean {
    const have = level.value ?? 'viewer'
    return RANK[have] >= RANK[need]
  }
  const isAdmin = computed(() => can('admin'))
  const canWrite = computed(() => can('user'))
  const isViewer = computed(() => level.value === 'viewer')

  async function fetchMe() {
    try {
      const headers = import.meta.server ? useRequestHeaders(['cookie']) : undefined
      const res = await $fetch<MeResponse>('/api/auth/me', {
        baseURL: config.public.apiBase,
        credentials: 'include',
        headers,
      })
      user.value = res.username
      level.value = res.level ?? null
    } catch {
      user.value = null   // 讀不到 /me（未登入或 session 過期）＝視為未登入，這正是本檢查的目的
      level.value = null
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
    level.value = res.level ?? null
    checked.value = true
  }

  async function logout() {
    await $fetch('/api/auth/logout', {
      baseURL: config.public.apiBase,
      method: 'POST',
      credentials: 'include',
      headers: csrfHeaders('POST'),   // Q91：登出也要帶 CSRF token
    })
    user.value = null
    level.value = null
  }

  return { user, level, checked, fetchMe, login, logout, can, isAdmin, canWrite, isViewer }
}
