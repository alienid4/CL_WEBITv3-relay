// 外部系統整合（Q92 Aria Operations、Q93 WhatsUp Gold）前端共用常數。
// 夜間速查頁（/v2/night）由另一條分支在做：這裡只把連結目標寫成常數，不依賴那頁已經存在；
// 頁面不存在（router 解析不到）時，IP 改連主機履歷頁。
export const NIGHT_PATH = '/v2/night'
export const nightLink = (ip: string) => `${NIGHT_PATH}?q=${encodeURIComponent(ip)}`
export const machineLink = (ip: string) => `/v2/machine/${encodeURIComponent(ip)}`

export interface VropsConfig {
  url: string; auth_source: string; ca_bundle: string; enabled: boolean; interval_hours: number
  credential: string; username: string; has_password: boolean; last_run: VropsRun | null
}
export interface VropsRun {
  id: number; started_at: string; finished_at: string | null; trigger: string; started_by: string | null; status: string
  vms: number | null; hosts: number | null; clusters: number | null; datastores: number | null
  inserted: number | null; updated: number | null; pending_review: number | null; error_count: number; message: string | null
  detail?: { errors?: string[]; write_errors?: string[] }
}
export interface WhatsupConfig {
  url: string; ca_bundle: string; enabled: boolean; interval_min: number; credential: string; username: string; has_password: boolean
  last_run: { at: string; status: string; message: string | null } | null
}
export const RUN_STATUS: Record<string, string> = { ok: '成功', partial: '部分成功', failed: '失敗', running: '進行中' }
export const TRIGGER: Record<string, string> = { manual: '手動', schedule: '排程' }
export const EVENT_LABEL: Record<string, string> = { new: '新增', changed: '狀態改變', cleared: '解除' }
