// 資產編輯（V20 第 3 批，2.054.0）：從舊 pages/assets/[serial].vue 的 startEdit／saveEdit 原樣搬出來的純函式，
// 給 components/v2/AssetEdit.vue 用；vitest（assetEdit.spec.ts）釘住跟舊頁同一套規則。
// 舊頁在 V20 全部搬完之前還留著同樣的常數（舊頁只剩檢視，編輯入口改連 V2）。

export interface FieldGroups { hardware: { common: string[]; advanced: string[]; people?: string[]; hardware_info?: string[]; cia?: string[] } }

/** 主鍵不給改：改序號等於換一台，會弄丟 personnel/software 的關聯（舊頁同一份） */
export const LOCKED = new Set(['asset_serial', 'id', 'created_at', 'updated_at'])
/** 人名欄：可搜尋選單（打字出候選、查無可新增） */
export const PERSON_FIELDS = new Set(['user_name', 'custodian'])
export const FIELD_LABELS: Record<string, string> = {
  asset_purpose: '資產用途', environment: '環境別', custodian: '保管者', usage_unit: '使用單位',
  group_name: '群組名稱', api_id: 'API ID', rack_no: '設備櫃位', request_no: '申請單編號',
  confidentiality: '機密性', availability: '可用性', integrity: '完整性',
  inventory_division: '盤點單位-處別', inventory_department: '盤點單位-部門',
  owner: '擁有者', remark: '附加說明', hardware_no: '硬體編號', big_ip_vip: 'BIG IP/VIP',
  asset_name: '資產名稱', infra_type: '整體基礎架構', physical_location: '機房地點',
  quantity: '數量', os: '作業系統', user_name: '使用者', owning_company: '所屬公司',
  asset_status: '資產狀態', device_model: '設備機型', hw_serial: '設備序號', mac: 'MAC 位址',
  hostname: '主機名稱', ip: 'IP',
}
export const fieldLabel = (k: string) => FIELD_LABELS[k] ?? k

/** 可編輯欄位＝五組欄位的聯集（去重、排除主鍵），舊頁 editableKeys 同一份 */
export function editableKeys(fg: FieldGroups | null | undefined): string[] {
  const h = fg?.hardware
  if (!h) return []
  return [...new Set([...(h.common ?? []), ...(h.advanced ?? []), ...(h.people ?? []), ...(h.hardware_info ?? []), ...(h.cia ?? [])])].filter(k => !LOCKED.has(k))
}

/** 開始編輯：每個可編輯欄位帶目前值（沒有就空字串）；資產狀態下拉＝待 CIA 異動（有待異動就帶待異動那個值） */
export function buildDraft(hw: Record<string, any>, keys: string[]): Record<string, any> {
  const d: Record<string, any> = {}
  for (const k of keys) d[k] = hw[k] ?? ''
  if ('asset_status' in d) d.asset_status = hw.pending_status || hw.asset_status || ''
  return d
}

/** 送出的 fields：資產狀態只在真的改了才送——不然存別的欄位會把待 CIA 異動當成「改回 CIA 的值」取消掉 */
export function saveFields(draft: Record<string, any>, hw: Record<string, any>): Record<string, any> {
  const f: Record<string, any> = { ...draft }
  if ('asset_status' in f && (f.asset_status || '') === (hw.pending_status || hw.asset_status || '')) delete f.asset_status
  return f
}

/** 畫面分區（只影響排列，不影響送出的欄位）：每個欄位只出現在第一個含它的區。
 *  主機名稱／IP 一律在第一區（舊頁在「網路／環境」框就地改，不管它在哪一組）；資產狀態另外放（待 CIA 異動下拉）。 */
export function sections(fg: FieldGroups | null | undefined): { k: string; label: string; keys: string[] }[] {
  const h = fg?.hardware
  if (!h) return []
  const seen = new Set<string>(['asset_status'])
  const take = (xs: string[] | undefined) => (xs ?? []).filter(k => !LOCKED.has(k) && !seen.has(k) && (seen.add(k), true))
  return [
    { k: 'net', label: '網路／環境', keys: take(['hostname', 'ip']) },
    { k: 'common', label: '基本欄位', keys: take(h.common) },
    { k: 'people', label: '人員與單位', keys: take(h.people) },
    { k: 'hwinfo', label: '硬體資訊', keys: take(h.hardware_info) },
    { k: 'cia', label: '資安分級（CIA）', keys: take(h.cia) },
    { k: 'advanced', label: '進階欄位', keys: take(h.advanced) },
  ].filter(s => s.keys.length)
}
