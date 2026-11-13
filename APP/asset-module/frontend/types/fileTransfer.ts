// 檔案傳送（Q94）共用型別。後端 transfer_service.py／routers/transfer.py 產出，前端只畫。
export interface FtHost {
  ip: string; serial: string; hostname: string; platform: string; os_label: string
  box: 'windows' | 'linux'; env: string; room: string; __no: number
}
export interface FtFile {
  id: number; name: string; size: number; sha256: string; source: string; from_host: string | null
  uploaded_by: string; uploaded_at: string; now_ok: boolean; why: string; next_at: string | null; __no: number
}
export interface FtVerdict { ok: boolean; why: string; next_at: string | null; now: string }
export const fmtSize = (n: number): string => (n >= 1073741824 ? `${(n / 1073741824).toFixed(1)} GB`
  : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`)
export const JOB_STATUS: Record<string, string> = {
  queued: '排隊中', running: '進行中', waiting_window: '等待允許時段', done: '成功', partial: '部分失敗', failed: '失敗', stopped: '已停止',
}
export const HOST_STATUS: Record<string, string> = { queued: '排隊中', running: '傳送中', ok: '成功', failed: '失敗', stopped: '已停止' }
