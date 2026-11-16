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

// 儲存設備 API（批 S1，Dell Unity）
export interface StorageDevice {
  id: number; name: string; vendor: string; vendor_label: string; host: string; port: number; credential: string
  ca_bundle: string; trust_self_signed: boolean; enabled: boolean; note: string
  last_test_at: string | null; last_test_ok: boolean | null; last_test_code: string | null; last_test_msg: string | null
  last_identity: { name?: string; model?: string; serial?: string } | null; last_tls_verified: boolean | null; __no: number
  last_collect?: StorageCollectSummary | null
  last_health?: StorageHealthSummary | null; health_schedule?: { included: boolean; reason: string; due: boolean }
}
export const STORAGE_CODE: Record<string, string> = {
  unauthorized: '帳密被拒', forbidden: '權限不足', redirect: '被重新導向', html: '收到登入頁', timeout: '逾時', tls: 'TLS 驗證失敗',
  unreachable: '連不上', credential: '憑證問題', config: '設定問題', http_error: 'Storage 回錯誤', bad_json: '格式不符', bad_encoding: '編碼損壞', bad_shape: '格式不符',
  bad_next: '分頁連結異常', page_loop: '分頁循環', page_limit: '分頁過多', too_big: '回應過大', unsupported: '品牌不支援',
  sections_failed: '各區段都失敗', run_timeout: '整次逾時', skipped: '略過',
}

// 儲存設備資料收集（批 S2）：人為按鈕、GET only；欄位依 Dell Unity 公開文件推定，未實機驗證
export interface StorageCollectSection { key: string; label: string; status: 'ok' | 'partial' | 'failed' | 'skipped'; rows: number; code: string | null; message: string }
export interface StorageCollectSummary {
  id: number; status: 'ok' | 'partial' | 'failed'; complete: boolean; code: string | null; message: string | null
  finished_at: string; item_count: number; review_count: number; sections: StorageCollectSection[] | null
}
export interface StorageCollectRun extends StorageCollectSummary {
  device_id: number; device_name: string; endpoint: string; started_at: string; collected_by: string | null; tls_verified: boolean | null
  identity: { name?: string; model?: string; serial?: string; os_version?: string; api_version?: string } | null
}
export interface StorageCollectRow {
  __no: number; kind: string; kind_label: string; obj_id: string; name: string; parent: string
  size_total: number | null; size_used: number | null; size_free: number | null; review: string; incomplete: boolean; extra: Record<string, any>
}
export interface StorageCollectDetail { device_id: number; run: StorageCollectRun | null; latest: StorageCollectRun | null; rows: StorageCollectRow[] }
export const SC_STATUS: Record<string, string> = { ok: '完整', partial: '不完整', failed: '失敗', skipped: '略過' }

// 儲存設備每日健康／告警檢查（批 S3）：每日 07:00（台北）自動一次＋admin 立即檢查；GET only；欄位未實機驗證
export const SH_STATUS: Record<string, string> = { ok: '完整', partial: '不完整', failed: '失敗', skipped: '略過', read: '已讀取', missing: '讀不到欄位' }
export const SH_TRIGGER: Record<string, string> = { auto: '自動', retry: '自動重試', manual: '人為' }
export const SH_CLASS: Record<string, string> = { effective: '有效', resolved: '已解除', uncertain: '不確定' }
export interface StorageHealthVerdict { level: 'complete' | 'incomplete' | 'failed' | 'none'; text: string; stale: boolean }
export interface StorageHealthSummary {
  id: number; status: 'ok' | 'partial' | 'failed'; complete: boolean; code: string | null; message: string | null; finished_at: string
  trigger: string; attempt_no: number; health_status: string; health_value: string | null; alert_status: string
  n_total: number | null; n_effective: number | null; n_resolved: number | null; n_uncertain: number | null; n_acked: number | null; n_manual: number | null
  verdict: StorageHealthVerdict
}
export interface StorageHealthBatch extends StorageHealthSummary {
  device_id: number; device_name: string; endpoint: string; day: string; started_at: string; checked_by: string | null; tls_verified: boolean | null
  os_version: string | null; health_at: string | null; health_code: string | null; health_msg: string | null; alert_at: string | null
  alert_code: string | null; alert_msg: string | null; health_detail: { value?: any; descriptions?: string[] | null } | null
}
export interface StorageHealthAlert {
  __no: number; alert_id: string; state_raw: string; state_class: 'effective' | 'resolved' | 'uncertain'; state_note: string; manual: boolean
  severity_raw: string; alert_time: string; acked: boolean | null; component: string; message: string; incomplete: boolean
}
export interface StorageHealthHistory { id: number; day: string; trigger: string; attempt_no: number; started_at: string; finished_at: string; status: string; code: string | null; message: string | null }
export interface StorageHealthDetail {
  device_id: number; batch: StorageHealthBatch | null; latest: StorageHealthBatch | null; alerts: StorageHealthAlert[]; history: StorageHealthHistory[]
  schedule: { included: boolean; reason: string; due: boolean }; daily_at: string; alerts_pruned: boolean
}
