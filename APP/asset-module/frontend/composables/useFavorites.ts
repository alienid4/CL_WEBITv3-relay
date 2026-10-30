// 我的最愛＋最近用過（2026-10-23）。每人一份：登入開著時存伺服器（/api/nav/favorites，以帳號為 key）；
// 登入停用時沒有身分可分，改存這個瀏覽器（localStorage，讀寫都包 try/catch，讀不到就用預設）。
const DEFAULT_FAVORITES = ['/changes', '/oncall', '/assets', '/datacenter', '/topology']
const LS_FAV = 'webit3.nav.favorites'
const LS_RECENT = 'webit3.nav.recent'

function lsGet(key: string): string[] | null {
  try {
    const v = localStorage.getItem(key)
    return v ? JSON.parse(v) : null
  } catch { return null } // 私密視窗／被擋：當沒有存過
}
function lsSet(key: string, v: string[]) {
  try { localStorage.setItem(key, JSON.stringify(v)) } catch { /* 存不了只是下次不記得，不影響使用 */ }
}

export function useFavorites() {
  const { apiFetch } = useApi()
  const favorites = useState<string[]>('nav-favorites', () => [...DEFAULT_FAVORITES])
  const recent = useState<string[]>('nav-recent', () => [])
  const storage = useState<'server' | 'local'>('nav-fav-storage', () => 'server')
  const loaded = useState<boolean>('nav-fav-loaded', () => false)

  async function load() {
    try {
      const r = await apiFetch<{ items: string[]; recent: string[]; storage: 'server' | 'local' }>('/api/nav/favorites')
      storage.value = r.storage
      if (r.storage === 'local') {
        favorites.value = lsGet(LS_FAV) ?? [...DEFAULT_FAVORITES]
        recent.value = lsGet(LS_RECENT) ?? []
      } else {
        favorites.value = r.items
        recent.value = r.recent
      }
    } catch {
      // 伺服器讀不到（舊版後端）：退回瀏覽器存
      storage.value = 'local'
      favorites.value = lsGet(LS_FAV) ?? [...DEFAULT_FAVORITES]
      recent.value = lsGet(LS_RECENT) ?? []
    }
    loaded.value = true
  }

  let saveTimer: ReturnType<typeof setTimeout> | null = null
  function save() {
    if (storage.value === 'local') {
      lsSet(LS_FAV, favorites.value)
      lsSet(LS_RECENT, recent.value)
      return
    }
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(async () => {
      try {
        await apiFetch('/api/nav/favorites', { method: 'PUT', body: { items: favorites.value, recent: recent.value } })
      } catch (e: any) {
        useToast().showToast(`我的最愛存不進去：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
      }
    }, 400)
  }

  function isFav(to: string) { return favorites.value.includes(to) }
  function toggleFav(to: string) {
    favorites.value = isFav(to) ? favorites.value.filter(x => x !== to) : [...favorites.value, to]
    save()
  }
  function moveFav(from: number, to: number) {
    const arr = [...favorites.value]
    const [x] = arr.splice(from, 1)
    arr.splice(to, 0, x)
    favorites.value = arr
    save()
  }
  function pushRecent(to: string) {
    if (!loaded.value || !to) return
    const next = [to, ...recent.value.filter(x => x !== to)].slice(0, 5)
    if (next.join('|') === recent.value.join('|')) return
    recent.value = next
    save()
  }
  return { favorites, recent, storage, load, isFav, toggleFav, moveFav, pushRecent }
}
