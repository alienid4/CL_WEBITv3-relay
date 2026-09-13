// 後端的基本狀態（版本、登入有沒有被關掉）。
//
// 為什麼獨立一支 composable：`/api/version` 是**唯一免登入**的端點，
// 而「登入有沒有被關掉」這件事必須在守衛跑之前就知道——放在需要登入的地方拿，
// 會變成先被導去登入頁才拿得到，永遠繞不出來。
//
// 只抓一次並共用結果：守衛每次換頁都會呼叫，每次都打一支 API 沒有必要。
export function useServerInfo() {
  const authDisabled = useState<boolean>('server-auth-disabled', () => false)
  const version = useState<string>('server-version', () => '')
  const loaded = useState<boolean>('server-info-loaded', () => false)
  const { apiFetch } = useApi()

  async function ensureLoaded(force = false) {
    if (loaded.value && !force) return
    try {
      const r = await apiFetch<any>('/api/version')
      authDisabled.value = r?.auth_disabled === true
      version.value = r?.version ?? ''
    } catch {
      // 拿不到就當作「登入正常運作」——**寧可多擋一次，不要少擋一次**。
      // 這個預設值是安全側的：後端掛了的時候不該變成人人可進。
      authDisabled.value = false
    } finally {
      loaded.value = true
    }
  }

  return { authDisabled, version, loaded, ensureLoaded }
}
