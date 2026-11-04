// 2.1 主機查詢的「批次查詢」與「重複登記」純函式（2.041.84）。資料＝/api/v2/hosts/batch-lookup、/api/v2/hosts/duplicates（後端 host_lookup，唯讀）。
// 數字磚、表格、匯出都用同一批 rows：磚的數字＝rows 依狀態的筆數，點磚＝同一份 rows 篩出來（加總＝下鑽）。
export type LookupStatus = 'one' | 'many' | 'partial' | 'none'
export interface LookupCand { key: string; name: string; ip: string | null; retired: boolean }
export interface LookupRow {
  no: number; term: string; input_count: number; status: LookupStatus; status_label: string; matched_by: string | null
  partial: boolean; count: number; hint: string | null; key: string | null; name: string | null; ip: string | null
  type: string | null; system: string | null; where: string | null; detect: string | null; records: number | null
  retired: boolean | null; candidates: LookupCand[]; candidates_text: string
}
export interface LookupData {
  mode: 'exact' | 'partial'; mode_label: string; rows: LookupRow[]
  summary: { raw_lines: number; blank: number; unique: number; duplicates_merged: number } & Record<LookupStatus, number>
  limits: { max_raw_lines: number; max_term_len: number; max_total_chars: number; max_unique: number }; notes: string[]
}
export const LOOKUP_LABEL: Record<LookupStatus, string> = { one: '找到', many: '多筆', partial: '部分命中', none: '查無' }
export const LOOKUP_TONE: Record<LookupStatus, string> = { one: 'prod', many: 'dr', partial: 'test', none: 'none' }

/** 貼上的文字 → 項目清單：換行、Tab、逗號、分號、空白都當分隔；空白項略過（後端另外依原始行數／長度／總量把關）。 */
export function parseTerms(text: string): string[] {
  return (text || '').split(/[\s,;，；、]+/).map(s => s.trim()).filter(Boolean)
}
export function lookupCounts(rows: LookupRow[]): Record<LookupStatus, number> {
  const c: Record<LookupStatus, number> = { one: 0, many: 0, partial: 0, none: 0 }
  for (const r of rows) c[r.status] += 1
  return c
}
export function lookupFilter(rows: LookupRow[], f: LookupStatus | 'all'): LookupRow[] {
  return f === 'all' ? rows : rows.filter(r => r.status === f)
}

export interface DupGroup {
  no: number; key: string; hostname: string | null; ip: string | null; count: number; extra_rows: number; kind: string; kind_label: string
  identical: boolean; diff_fields: string[]; serials: string[]; systems: string[]; origin_text: string
}
export interface DupMember {
  group_no: number; group_key: string; kind: string; kind_label: string; serial: string; hostname: string | null; ip: string | null
  api_id: string | null; how: string | null; created_at: string | null; updated_at: string | null; from_import: string; strong_ids: string
  diff: Record<string, string>
}
export interface DupData {
  groups: DupGroup[]; members: DupMember[]; by_kind: Record<string, number>; kind_labels: Record<string, string>; kind_order: string[]
  extra_rows: number; dismissed_groups: number | null
}
const FIELD_LABEL: Record<string, string> = {
  hostname: '主機名稱', ip: 'IP', api_id: '業務系統', big_ip_vip: 'VIP', asset_purpose: '用途', asset_name: '資產名稱', os: '作業系統',
  asset_status: '資產狀態', environment: '環境別', physical_location: '機房', rack_no: '機櫃', owner: '擁有者', custodian: '保管人',
  device_model: '設備型號', user_name: '使用者', usage_unit: '使用單位', availability: '可用性',
}
export const fieldLabel = (f: string) => FIELD_LABEL[f] || f
export const diffText = (m: DupMember) => Object.entries(m.diff).map(([k, v]) => `${fieldLabel(k)}：${v || '（空白）'}`).join('；') || '無差異'
export function dupFilter<T extends { kind: string }>(rows: T[], kind: string): T[] {
  return kind === 'all' ? rows : rows.filter(r => r.kind === kind)
}

// ===== 重複登記「釋放序號」與還原（2.041.97；管理員限定；後端 host_release.py）=====
export interface ReleaseItem {
  serial: string; hostname: string | null; ip: string | null; api_id: string | null; asset_status: string | null
  from_cia: boolean; counts: Record<string, number>; total_rows: number; kept_refs: Record<string, number>
}
export interface ReleasePreview {
  key: string; hostname: string | null; ip: string | null; kind: string; kind_label: string
  current: string[]; keep: string[]; release: ReleaseItem[]
  totals: { serials: number; rows: number; by_table: Record<string, number> }
  cia_serials: string[]; warnings: string[]; blockers: string[]; blocked: boolean; count_before: number; count_after: number
  confirm_word: string; fingerprint: string
}
export interface ReleaseRecord {
  id: number; merged_at: string; actor: string | null; hostname: string | null; ip: string | null; serial: string
  kept_serials: string | null; batch_id: string | null; from_cia: string; state: string; reason: string | null
  restorable: boolean; restored_at: string | null; restored_by: string | null; rows: number | null; counts: Record<string, number>
}
export interface ReleaseList { items: ReleaseRecord[]; restorable: number }

/** 勾選的明細（rowKey＝serial）→ 它們所屬的組（machine_key）。一次只能釋放同一組。 */
export function selectedRelease(members: DupMember[], sel: Record<string, boolean>): { keys: string[]; serials: string[] } {
  const picked = members.filter(m => sel[m.serial])
  return { keys: [...new Set(picked.map(m => m.group_key))], serials: picked.map(m => m.serial) }
}
/** 確認文字：輸入「釋放」或任一要釋放的序號才算數。 */
export function confirmOk(text: string, word: string, serials: string[]): boolean {
  const t = (text || '').trim()
  return !!t && (t === word || serials.includes(t))
}
const TABLE_LABEL: Record<string, string> = {
  hardware: '資產主檔', software: '軟體登記', personnel: '人員', host_account: '主機帳號', host_package: '已安裝套件',
  host_service: '服務／連接埠', host_winservice: 'Windows 服務', host_spec: '硬體規格', host_iis: 'IIS 站台', host_connection: '連線紀錄',
  host_api_key: '收集金鑰', host_metric_latest: '最新效能數值', host_rebuild: '重建資料', asset_nic: '網卡', asset_hba: 'HBA 卡',
  account_finding: '帳號盤點發現', baseline_drift: '基線偏離', checklist_item: '上線檢查項目', doc_archive: '文件綁定',
  golive_check: '上線檢核', provision_request: '開通申請', ci_node: '關聯圖節點', cia_pending_change: 'CIA 待同步變更',
  rack_position_override: '機櫃位置', os_type_override: '作業系統類型覆寫', onboard_exempt: '納管豁免', source_record: '來源紀錄',
}
export const tableLabel = (t: string) => {
  const link = t.endsWith('（接回用）')
  const name = t.replace('（接回用）', '').split('.')[0]
  return (TABLE_LABEL[name] ?? name) + (link ? '（接回用）' : '')
}
/** 各表筆數 → 一行文字（0 筆的不列）。 */
export function countsText(c: Record<string, number>): string {
  return Object.entries(c).filter(([, n]) => n > 0).map(([k, n]) => `${tableLabel(k)} ${n}`).join('、') || '—'
}
