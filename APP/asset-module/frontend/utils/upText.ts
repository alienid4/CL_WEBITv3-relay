// 機型、序號一律顯示成全大寫、空白換行整理成一個空白（使用者 2026-10-28）。
// 只統一「顯示」：資料庫裡 CIA 的原值不改，篩選／下鑽連結用的仍是原值。
export const upText = (x?: string | null) => (x || '').split(/\s+/).filter(Boolean).join(' ').toUpperCase()
