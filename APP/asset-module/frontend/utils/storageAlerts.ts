// 5.2 儲存容量「容量預警」換門檻（2.043.6，V71：舊版 /storage 的門檻切換搬過來）。
// 跟後端 backend/storage_capacity.py alerts() 同一個判斷：datastore 剩餘 % < 門檻＋只看檔案系統（by=mount）的實體主機。
// 沒換門檻時畫面直接用後端 alerts（首頁、1.4 追蹤處理同一份）；換了門檻（?th=）才用這裡在瀏覽器重算。
// 2.044.0（R17）：實體主機檔案系統改用全站磁碟一把尺（backend/disk_level.py）：使用率＝100−剩餘 %（一位小數），
// ≥80% 列「處理」、≥90% 列「高」；臨時門檻只換 Datastore，不影響實體主機。
export interface AlertVol { free_pct: number | null }
export interface AlertPhys { by?: string; free_pct: number | null }

export const DISK_HANDLE_PCT = 80
export const DISK_HIGH_PCT = 90

/** 剩餘 % → 使用率 %（一位小數）；查不到回 null（跟後端 disk_level.used_from_free 同一算法） */
export function usedFromFree(free: number | null | undefined): number | null {
  if (free === null || free === undefined || !Number.isFinite(Number(free))) return null
  return Math.round((100 - Math.round(Number(free) * 10) / 10) * 10) / 10
}

/** 使用率 → 'high'｜'handle'｜null（不列）｜'uncollected'（查不到） */
export function diskLevel(used: number | null | undefined): 'high' | 'handle' | 'uncollected' | null {
  if (used === null || used === undefined || !Number.isFinite(Number(used))) return 'uncollected'
  const p = Math.round(Number(used) * 10) / 10
  return p >= DISK_HIGH_PCT ? 'high' : p >= DISK_HANDLE_PCT ? 'handle' : null
}

/** 實體主機一列的磁碟等級文字（v2 與舊版儲存容量頁「實體主機磁碟」共用，2.044.3 GPT A4）：
 *  只有檔案系統（by=mount）才判；VG／磁碟的剩餘是未配置空間，不判（空字串）。 */
export function physLevelText(p: { by?: string; free_pct: number | null }): string {
  if (p.by !== 'mount') return ''
  const lv = diskLevel(usedFromFree(p.free_pct))
  return lv === 'high' ? '高（≥90%）' : lv === 'handle' ? '處理（≥80%）' : lv === 'uncollected' ? '未收集' : ''
}

/** 要不要標成快滿（處理或高） */
export function physListed(p: { by?: string; free_pct: number | null }): boolean {
  if (p.by !== 'mount') return false
  const lv = diskLevel(usedFromFree(p.free_pct))
  return lv === 'high' || lv === 'handle'
}

export function parseThreshold(v: unknown): number | null {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 && n < 100 ? n : null
}

/** 「依機房」的 Datastore 預警格（2.043.7，GPT 審查 1）：有臨時門檻時，每個機房的格子用同一份重算結果，
 *  跟點進去（容量預警頁籤、同一門檻）一致。room＝卷宗所在桶（跟後端 rooms() 的分桶同一欄）。 */
export function nearFullByRoom(volumes: { room?: string; free_pct: number | null }[], th: number): Record<string, number> {
  const out: Record<string, number> = {}
  for (const v of storageAlerts(volumes, [], th).volumes) out[v.room ?? ''] = (out[v.room ?? ''] ?? 0) + 1
  return out
}

export function storageAlerts<V extends AlertVol, P extends AlertPhys>(volumes: V[], physical: P[], th: number): { volumes: V[]; physical: P[] } {
  return {
    volumes: volumes.filter(v => v.free_pct !== null && v.free_pct !== undefined && v.free_pct < th),
    physical: physical.filter(p => { const lv = diskLevel(usedFromFree(p.free_pct)); return p.by === 'mount' && (lv === 'high' || lv === 'handle') }),
  }
}
