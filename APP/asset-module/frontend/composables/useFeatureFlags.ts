// S13：功能模組開關（D28）。全域快取一份flags清單，layout側邊欄、gating middleware、
// 系統設定頁的管理UI三處都吃同一份狀態，改一處全部同步，不會兜不起來。
export interface FeatureFlag {
  module_key: string
  label: string
  enabled: number
  updated_at: string
}

export function useFeatureFlags() {
  const flags = useState<FeatureFlag[]>('feature-flags', () => [])
  const loaded = useState<boolean>('feature-flags-loaded', () => false)
  const { apiFetch } = useApi()

  async function ensureLoaded() {
    if (loaded.value) return
    await refresh()
  }

  async function refresh() {
    try {
      flags.value = await apiFetch<FeatureFlag[]>('/api/feature-flags')
      loaded.value = true
    } catch {
      // 抓不到（例如未登入）就維持空清單，isEnabled預設開啟不會因此把功能誤鎖死
    }
  }

  function isEnabled(moduleKey: string): boolean {
    const f = flags.value.find((x) => x.module_key === moduleKey)
    return f ? !!f.enabled : true
  }

  async function setEnabled(moduleKey: string, enabled: boolean) {
    const updated = await apiFetch<FeatureFlag>(`/api/feature-flags/${moduleKey}`, {
      method: 'PUT',
      body: { enabled },
    })
    const idx = flags.value.findIndex((x) => x.module_key === moduleKey)
    if (idx >= 0) flags.value[idx] = updated
    else flags.value.push(updated)
  }

  return { flags, loaded, ensureLoaded, refresh, isEnabled, setEnabled }
}
