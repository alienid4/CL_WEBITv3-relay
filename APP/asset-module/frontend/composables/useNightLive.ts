// 夜間速查（/v2/night）「要不要自動即時檢測」與「呼叫端點」的判斷邏輯。
// 從 pages/v2/night.vue 抽出，讓 fail-closed、VMware 例外、不快取等行為可以用 vitest 互動驗證（不是讀檔字串）。
// 純函式、不依賴 Nuxt 自動匯入：apiFetch 由呼叫端傳入。

export type ApiFetch = <T = any>(url: string, opts?: any) => Promise<T>

// 自動即時檢測開關：每次查詢都重讀（管理員關掉立即生效，不快取）。
// 讀取失敗 fail-closed：不自動檢測（一份關閉的稽核設定不能被暫時性失敗繞過）。
export async function resolveAutoLive(
  fetchSettings: () => Promise<{ auto_live?: boolean }>,
): Promise<{ on: boolean; failed: boolean }> {
  try {
    const r = await fetchSettings()
    return { on: r.auto_live !== false, failed: false }
  } catch {
    return { on: false, failed: true }
  }
}

export type LiveDecision = { action: 'vmware' | 'manual' | 'run'; msg: string }

// 純判斷：VMware 絕不連線（天條，優先於開關）；手動按鈕一律執行；自動模式看設定。
export function shouldAutoCheck(p: {
  settings: { on: boolean; failed: boolean } | null
  isVmware: boolean
  mode: 'auto' | 'manual'
}): LiveDecision {
  if (p.isVmware) return { action: 'vmware', msg: '' }
  if (p.mode === 'manual') return { action: 'run', msg: '' }
  const s = p.settings
  if (!s || !s.on) return { action: 'manual', msg: !s || s.failed ? '無法讀取設定，請按立即檢測' : '' }
  return { action: 'run', msg: '' }
}

// 完整決策：VMware 與手動不讀設定；只有自動模式才（每次）重讀設定。
export async function decideLive(
  apiFetch: ApiFetch,
  p: { isVmware: boolean; manual: boolean },
): Promise<LiveDecision> {
  if (p.isVmware) return shouldAutoCheck({ settings: null, isVmware: true, mode: 'auto' })
  if (p.manual) return shouldAutoCheck({ settings: null, isVmware: false, mode: 'manual' })
  const settings = await resolveAutoLive(() => apiFetch<{ auto_live?: boolean }>('/api/night/settings'))
  return shouldAutoCheck({ settings, isVmware: false, mode: 'auto' })
}

// 唯一會連到目標機的呼叫（只讀探測）。
export function runLiveCheck(apiFetch: ApiFetch, ips: string[], timeoutMs: number) {
  return apiFetch<{ items: any[] }>('/api/health-check/run', { method: 'POST', body: { ips }, timeout: timeoutMs })
}

// 純查詢（不連目標機、不寫入）：值班電話、依賴下游（mode=probe 的語意＝只讀）。
export function fetchPhones(apiFetch: ApiFetch, query: Record<string, string>) {
  return apiFetch<any>('/api/contacts/card', { query })
}
export function fetchDownImpact(apiFetch: ApiFetch, nodeId: string) {
  return apiFetch<{ dependencies?: { node_id: string }[] }>('/api/blast/impact', { query: { node_id: nodeId, mode: 'probe', depth: 3 } })
}
