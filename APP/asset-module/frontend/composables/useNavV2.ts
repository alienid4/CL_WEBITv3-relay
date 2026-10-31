// 2.x 全站選單（藍圖定稿 2026-10-27、決策 13：10 組＋系統設定）。新版外框 layouts/v2.vue 用。
// 名稱與編號照藍圖，不可自己改；主機履歷／系統履歷／單一事件不放選單（從清單、搜尋、連結進入）。
// 還沒做新版的頁先連到舊頁、標「（舊版）」，做好一頁就把 to 改成新路由、拿掉 old。
export interface V2NavItem { no: string; label: string; to: string; old?: boolean; match?: string[] }
export interface V2NavGroup { no: string; label: string; items: V2NavItem[] }

// 釘在選單最上面、不屬於任何一組、永遠看得到（不收合）。夜間速查：半夜迷迷糊糊，直接點出來最快，不要放在某一層裡面（2026-10-29）。
export const V2_PINNED: V2NavItem = { no: '🌙', label: '夜間速查', to: '/v2/night' }

export const V2_NAV: V2NavGroup[] = [
  { no: '1', label: '今日概況', items: [
    { no: '1.1', label: '今日概況', to: '/v2', match: ['/v2'] },
    { no: '1.2', label: '異動與異常', to: '/v2/changes' },
    { no: '1.3', label: '設備風險', to: '/v2/risk' },
  ] },
  { no: '2', label: '主機', items: [
    { no: '2.1', label: '主機查詢', to: '/v2/hosts', match: ['/v2/hosts', '/v2/machine', '/v2/asset'] },
  ] },
  { no: '3', label: '業務系統', items: [
    { no: '3.1', label: '業務系統', to: '/v2/systems', match: ['/v2/systems', '/v2/system'] },
    { no: '3.2', label: '架構圖', to: '/v2/arch' },
  ] },
  { no: '4', label: '事件應變', items: [
    { no: '4.1', label: '事件應變', to: '/v2/incident' },
  ] },
  { no: '5', label: '機房管理', items: [
    { no: '5.1', label: '機房平面圖', to: '/v2/datacenter' },
    { no: '5.2', label: '儲存容量', to: '/v2/storage' },
  ] },
  { no: '6', label: '納管作業', items: [
    { no: '6.1', label: '納管作業', to: '/v2/onboard' },
    { no: '6.2', label: '設備收集', to: '/v2/collect' },
  ] },
  { no: '7', label: '盤點作業', items: [
    { no: '7.1', label: '機櫃盤點', to: '/v2/inventory/rack' },
    { no: '7.2', label: '帳號盤點', to: '/v2/inventory/account', match: ['/v2/inventory/account', '/v2/inventory/matrix'] },
    { no: '7.3', label: '軟體盤點', to: '/v2/inventory/software' },
    { no: '7.4', label: '服務盤點', to: '/v2/inventory/service' },
  ] },
  { no: '8', label: '合規稽核', items: [
    { no: '8.1', label: '弱點管理', to: '/v2/patch' },
    { no: '8.2', label: '組態與上線檢核', to: '/v2/audit', match: ['/v2/audit', '/v2/golive'] },
    { no: '8.3', label: '生命週期', to: '/v2/eos' },
  ] },
  { no: '9', label: '資料治理', items: [
    { no: '9.1', label: '待補資料', to: '/v2/datafix' },
    { no: '9.2', label: '數量對帳', to: '/v2/reconcile' },
  ] },
  { no: '10', label: '報表', items: [
    { no: '10.1', label: '月報', to: '/v2/report' },
    { no: '10.2', label: '分布統計', to: '/v2/dist' },
  ] },
  { no: '⚙', label: '系統設定', items: [
    { no: '⚙', label: '系統設定', to: '/v2/settings' },
  ] },
]

/** 目前路徑落在哪一項（最長前綴；/v2 只比完全相同，不然每一頁都會亮 1.1）。 */
export function v2ActiveTo(path: string): string | null {
  let best: string | null = null
  let len = -1
  if (path === V2_PINNED.to || path.startsWith(V2_PINNED.to + '/')) return V2_PINNED.to
  for (const g of V2_NAV) {
    for (const it of g.items) {
      for (const m of it.match ?? [it.to]) {
        const hit = m === '/v2' ? (path === '/v2' || path === '/v2/') : (path === m || path.startsWith(m + '/'))
        if (hit && m.length > len) { best = it.to; len = m.length }
      }
    }
  }
  return best
}
