// ⚙ 資料匯入 V2（V36，2.059.1）共用的純函式：從舊 pages/import.vue 原樣抽出來（行為不變），讓 V2 元件與 vitest 共用。
import { httpReason } from '~/composables/httpReason'

/** 來源清單（總覽表的列與項次，順序固定）。link＝上傳不在這頁、只列筆數與時間的來源要去的地方。 */
export const IMPORT_SOURCES: { key: string; label: string; fmt: string; link?: string }[] = [
  { key: 'cia_excel', label: 'CIA 資產清冊（硬體／人員通訊錄／軟體）', fmt: '.xlsx（三分頁）' },
  { key: 'rvtools', label: 'RVTools（vCenter 虛擬機）', fmt: '.xlsx（可多檔）' },
  { key: 'dynassets', label: '存活清單（dynassets）', fmt: '.csv／.xlsx' },
  { key: 'business_system', label: '業務系統對照表', fmt: '.xlsx／.csv' },
  { key: 'sys_class', label: '系統類別對照表（第一／二／三類）', fmt: '.xlsx／.csv' },
  { key: 'syscat', label: '業務系統分類對照表', fmt: '.xlsx' },
  { key: 'segments', label: '網段配置表', fmt: '.xlsx／.csv／.tsv' },
  { key: 'ad', label: 'AD 人員名單', fmt: '.xlsx／.csv' },
  { key: 'account_audit', label: '帳號盤點', fmt: '.xlsx／.csv／dump' },
  { key: 'certs', label: '憑證調查表（7.5 憑證盤點）', fmt: '.xlsx' },
  { key: 'alert_contact', label: '告警聯絡資訊（APID）', fmt: '.xlsx／.csv' },
  { key: 'relocation', label: '機房搬遷盤點表（青埔）', fmt: '.xlsx（9 分頁）' },
  { key: 'documents', label: '單據（Word）', fmt: '.doc／.docx', link: '/v2/settings?st=documents' },
  { key: 'contracts', label: '合約檔（聯絡資訊）', fmt: '.xlsx／.csv', link: '/v2/settings?st=contacts&tab=import' },
  { key: 'roster', label: '值班電話名冊', fmt: '.xlsx／.csv', link: '/v2/settings?st=contacts&tab=oncall' },
  // 2.060.0：9.3 CIA 修正包，管理人勾好同意／不同意交回的那份（放最後，前面的項次不變）
  { key: 'cia_fix', label: 'CIA 修正包（管理人交回）', fmt: '.xlsx（系統產生的修正包）' },
  // P2 試辦：9.4 CIA 補登，負責人填好交回的那份（放最後，前面的項次不變）
  { key: 'cia_reg', label: 'CIA 補登（負責人交回）', fmt: '.xlsx（9.4 下載的補登檔）' },
  // 2.060.20（S10）：主機組的伺服器資料彙整表（參考來源，不改資產；放最後，前面的項次不變）
  { key: 'srv_sheet', label: '伺服器資料彙整表（參考）', fmt: '.xlsx（不加密；讀「伺服器 (主)」「已關機待處理」）' },
]

/** 檔名裡的日期（2.007.14 使用者：「上次的匯入版本」） */
export function dataDate(name?: string | null): string | null {
  const m = /(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])/.exec(name || '')
  return m ? `${m[1]}-${m[2]}-${m[3]}` : null
}
export function lastText(at?: string | null, date?: string | null): string {
  return at ? `${String(at).slice(0, 16)}${date ? `（資料 ${date}）` : ''}` : '—'
}

export interface CiaSummary { sheets: Record<string, { inserted: number; updated: number; skipped: number }>; errors: string[]; invalid_status?: { asset_serial: string; value: string }[] }
/** CIA 匯入結果三態（後端只要讀得開檔就回 200，一列都沒寫進去也是 200——畫面要講白）。舊頁 summarize 原樣。 */
export function ciaVerdict(s: CiaSummary) {
  const written = Object.values(s.sheets).reduce((n, x) => n + x.inserted + x.updated, 0)
  const skipped = Object.values(s.sheets).reduce((n, x) => n + x.skipped, 0)
  if (written === 0) {
    return { tone: 'error' as const, verdict: '沒有匯入任何資料',
      hint: s.errors.length > 0 ? '請看下方原因；最常見是分頁名稱不是「硬體／人員／軟體」，或表頭對不上欄位對應。' : '檔案讀得到，但沒有任何一列符合欄位對應，請確認表頭與分頁名稱。',
      toast: '匯入失敗：沒有任何資料被寫入' }
  }
  if (s.errors.length > 0 || skipped > 0) {
    return { tone: 'warn' as const, verdict: `部分匯入：成功 ${written} 筆，略過 ${skipped} 筆`,
      hint: '略過的資料請看下方原因，處理後可以再匯一次（重複匯入不會產生重複資料）。', toast: `部分匯入：成功 ${written} 筆、略過 ${skipped} 筆` }
  }
  return { tone: 'success' as const, verdict: `匯入成功：共 ${written} 筆`, hint: '', toast: `匯入成功，共 ${written} 筆` }
}

export interface RvSummary { total_vms: number; inserted: number; updated: number; pending_review: number; errors: string[]; extra_sheets?: Record<string, number>; vi_sdk_servers?: string[]; vc_moved?: { asset_serial: string; name: string; from: string; to: string }[] }
/** RVTools 一份檔的結果判定（舊頁 rvVerdict 原樣） */
export function rvVerdict(s: RvSummary) {
  const written = s.inserted + s.updated
  if (written === 0 && s.pending_review === 0) return { tone: 'error' as const, text: '沒有讀到任何 VM，請確認是 RVTools 匯出的檔（含 vInfo 分頁）' }
  if (s.pending_review > 0) return { tone: 'warn' as const, text: `收進 ${written} 台（新增 ${s.inserted}／更新 ${s.updated}），另有 ${s.pending_review} 台判不準、待人工確認` }
  return { tone: 'success' as const, text: `收進 ${written} 台 VM（新增 ${s.inserted}／更新 ${s.updated}）` }
}

/** 檔案最後修改時間＝這份匯出檔的資料時間（擋「不小心傳了舊檔」，後端 stale_import 用） */
export function fileDataAt(f: { lastModified: number }): string {
  const d = new Date(f.lastModified)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** GET 一支 API、把回應存成檔（帶登入 cookie；檔名照 Content-Disposition）——舊頁 downloadFile 原樣 */
export async function downloadVia(apiBase: string, path: string, fallbackName: string): Promise<void> {
  const res = await fetch(`${apiBase}${path}`, { credentials: 'include' })
  if (!res.ok) throw new Error(await httpReason(res))
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition') || ''
  const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = m ? decodeURIComponent(m[1]) : fallbackName
  a.click()
  URL.revokeObjectURL(url)
}
