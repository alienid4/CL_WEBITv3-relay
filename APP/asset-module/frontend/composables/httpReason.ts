// 直接用 fetch() 下載檔案時（apiFetch 管不到 blob），失敗要把後端的「為什麼」讀出來。
//
// 2026-09-18 公司機：「匯出失敗：HTTP 400」。後端其實回了完整原因
// （「還沒設定收件人公鑰，無法匯出……」），前端卻寫成 throw new Error(`HTTP ${res.status}`)，
// 把答案丟掉。全前端有 8 處同樣寫法，統一走這裡。
export async function httpReason(res: Response): Promise<string> {
  try {
    const j = await res.clone().json()
    if (j?.detail) return typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    if (j?.message) return String(j.message)
  } catch { /* 回應不是 JSON：退回顯示狀態碼 */ }
  return `HTTP ${res.status}`
}
