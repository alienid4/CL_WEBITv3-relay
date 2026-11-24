// 1.5 每日檢查：容量成長與估計滿載的畫面欄位（純函式；數字全部來自後端 daily_check_trend.analyze，前端不重算）。
// 規則：查不到＝「—」（不是 0、不是正常）；「待累積」「樣本不足，不估算」「容量有變，不估算」等文字用後端的 trend_note。
// 容量等級（第一級／需處理／正常／未收集／已知例外）跟「資料來源狀態」六種詞是兩回事，欄名叫「容量等級」。
// 排序：已滿 → 已在第一級 → 到第一級天數少的 → 到滿載天數少的 → 使用率高的；「下降中」只是說明，不取消容量優先級。
// 歷史長度：Pool 與 Unity 檔案系統（kind＝檔案系統，同樣是 trend 列、同一組欄位）最多 40 天、硬碟（WhatsUp 上次輪循）最多約 30 天。

export interface TrendCol { k: string; label: string; mono?: boolean; text?: (r: any) => any; tone?: (r: any) => string }

const num = (v: any) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export const fmtPct = (v: any) => (num(v) === null ? '—' : `${v}%`)
export const fmtDelta = (v: any) => (num(v) === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)} 點`)
export const fmtGrowth = (v: any) => (num(v) === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)} 點／天`)
/** 到第一級：0＝已在第一級；沒有＝—（不估） */
export const fmtDaysFirst = (v: any) => (num(v) === null ? '—' : v === 0 ? '已在第一級' : `${v} 天`)
/** 到滿載：0＝已滿；沒有＝— */
export const fmtDaysFull = (v: any) => (num(v) === null ? '—' : v === 0 ? '已滿' : `${v} 天`)

/** 容量等級 → V2Table tone：第一級紅、需處理黃、正常綠、其他（未收集、已知例外）灰 */
export const TREND_TONE: Record<string, string> = { 第一級: 'test', 需處理: 'dr', 正常: 'prod', 已知例外: 'none', 未收集: 'none' }

/** 趨勢欄位（Pool 與逐顆硬碟共用，同一組欄位同一個順序） */
export function trendCols(): TrendCol[] {
  return [
    { k: 'pct', label: '使用率', mono: true, text: (r) => fmtPct(r.pct) },
    { k: 'prev', label: '前次', mono: true, text: (r) => fmtPct(r.prev) },
    { k: 'delta', label: '較前次', mono: true, text: (r) => fmtDelta(r.delta) },
    { k: 'growth', label: '每日成長', mono: true, text: (r) => fmtGrowth(r.growth) },
    { k: 'samples', label: '樣本數', mono: true, text: (r) => (num(r.samples) === null ? '—' : `${r.samples} 天`) },
    { k: 'days_to_first', label: '到第一級', text: (r) => fmtDaysFirst(r.days_to_first) },
    { k: 'days_to_full', label: '到滿載', text: (r) => fmtDaysFull(r.days_to_full) },
    { k: 'trend_note', label: '趨勢說明', text: (r) => r.trend_note || '—' },
  ]
}

/** 這一列明細有沒有趨勢欄位（Storage 的 Pool 列有；其他列沒有） */
export const hasTrend = (detail: any[] | undefined) => (detail ?? []).some((x) => x && x.trend)

/** 這一列是不是「上次輪循的硬碟」：每台設備都帶 disks（可以是空陣列）。0 顆也要顯示逐顆硬碟的空狀態，不能整個不出現 */
export const isDiskPanel = (rule: string | undefined, detail: any[] | undefined) => rule === 'disk' && (detail ?? []).some((x) => x && Array.isArray(x.disks))

/** Storage 明細欄位：名稱、容量等級、趨勢欄位、內容、資料時間 */
export function storageDetailCols(): TrendCol[] {
  return [{ k: 'name', label: '名稱' }, { k: 'state', label: '容量等級', tone: (r) => TREND_TONE[r.state] ?? '' }, ...trendCols(),
    { k: 'what', label: '內容' }, { k: 'since', label: '採集時間', mono: true }]
}

/** 硬碟明細（逐顆）欄位 */
export function diskDetailCols(): TrendCol[] {
  return [{ k: 'name', label: '主機' }, { k: 'ip', label: 'IP', mono: true }, { k: 'item', label: '磁碟' },
    { k: 'state', label: '容量等級', tone: (r) => TREND_TONE[r.state] ?? '' }, ...trendCols()]
}

/** 排序鍵（跟後端 daily_check_trend.sort_key 同一個順序）：tier、到第一級天數、到滿載天數、使用率高到低 */
export function trendOrder(a: any, b: any): number {
  const t = (x: any) => num(x.tier) ?? 4
  const f = (x: any) => num(x.days_to_full) ?? 1e9
  return t(a) - t(b) || (num(a.days_to_first) ?? 0) - (num(b.days_to_first) ?? 0) || f(a) - f(b) || (num(b.pct) ?? -1) - (num(a.pct) ?? -1)
}

/** 把每台設備的 disks 攤平成逐顆硬碟列（項次固定：照上面的順序編，之後使用者怎麼排都不變）。沒有 disks 的設備不產生列。 */
export function flattenDisks(detail: any[] | undefined): any[] {
  const out: any[] = []
  for (const d of detail ?? []) {
    for (const k of d?.disks ?? []) out.push({ ...k, name: d.name, ip: d.ip, serial: d.serial, device_id: d.device_id })
  }
  out.sort(trendOrder)
  return out.map((r, i) => ({ ...r, __no: i + 1 }))
}
