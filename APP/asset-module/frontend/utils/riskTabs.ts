// 1.3 設備風險頁籤（使用者 11-14「這也不用」）：track_uptime_enabled 關著（預設）時沒有「逾期未重開機」頁籤，預設頁籤改硬體異常。
export const RISK_TABS_BASE = ['hardware', 'spof', 'contract', 'hk'] as const

/** 這次可用的頁籤 key（uptimeOn＝後端 /api/risk 的 uptime_enabled；沒回這個欄位＝舊後端，當作開著） */
export function riskTabKeys(uptimeOn: boolean | undefined): string[] {
  return [...(uptimeOn !== false ? ['uptime'] : []), ...RISK_TABS_BASE]
}

/** 網址的 tab 不在可用清單（例如關掉後還帶 tab=uptime）→ 第一個可用的 */
export function pickRiskTab(q: unknown, uptimeOn: boolean | undefined): string {
  const keys = riskTabKeys(uptimeOn)
  const t = String(q ?? '')
  return keys.includes(t) ? t : keys[0]
}
