// 告警聯絡五欄（2.044.2，需求 R15）：後端 alert_contact.attach 在每一列告警加 c_ap_team／c_unit／c_system_name／c_sys_class／c_email
// （沒有值寫「未提供」）。首頁今日、4.1 告警事件、夜間速查都用這一組欄位定義，欄名與順序全站一致。
export const ALERT_CONTACT_COLS = [
  { k: 'c_ap_team', label: 'AP 團隊' },
  { k: 'c_unit', label: '單位' },
  { k: 'c_system_name', label: '系統名稱' },
  { k: 'c_sys_class', label: '系統分級' },
  { k: 'c_email', label: 'email' },
]

/** 一行文字版（手機卡片／夜間速查用）：AP 團隊・單位・系統名稱・分級・email */
export function contactLine(r: Record<string, any>): string {
  return ALERT_CONTACT_COLS.map(c => `${c.label} ${r[c.k] ?? '未提供'}`).join('・')
}

/** 通知結果的中文（後端 alert_notify.RESULT 的值原樣顯示；空字串＝還沒通知過） */
export function notifiedText(r: Record<string, any>): string {
  return r.notified_text || ''
}
