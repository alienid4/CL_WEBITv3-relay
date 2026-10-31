// EOS 行事曆（ICS）匯出的資料挑選（W221-006，Codex 41 號）：只納入「可信、完整日期」的項目；
// 來源未核對／被拒／格式錯／無單一日期／月精度（沒有完整日）一律排除並回報數量。純函式，vitest 直接測。
export interface IcsItem { name: string; count: number; status: string; eos_date?: string | null; trusted?: boolean }
export interface IcsRow { name: string; date: string; count: number; status: string }

export function icsDate(s: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s).trim())
  return m ? `${m[1]}${m[2]}${m[3]}` : null
}

export function selectIcsRows(buckets: IcsItem[][]): { rows: IcsRow[]; skippedUntrusted: number; skippedNoDate: number } {
  const seen = new Set<string>()
  const rows: IcsRow[] = []
  let skippedUntrusted = 0
  let skippedNoDate = 0
  for (const items of buckets) {
    for (const it of items) {
      if (it.status !== 'expired' && it.status !== 'upcoming') continue
      if (it.trusted !== true) { skippedUntrusted += 1; continue }      // 舊 API 沒有 trusted 也一律排除（寧可不匯出，不冒稱官方）
      const d = it.eos_date ? icsDate(it.eos_date) : null
      if (!d) { skippedNoDate += 1; continue }                           // 月精度（2030-04）等沒有完整日
      const k = it.name + '|' + d
      if (seen.has(k)) continue
      seen.add(k)
      rows.push({ name: it.name, date: d, count: it.count, status: it.status })
    }
  }
  return { rows, skippedUntrusted, skippedNoDate }
}
