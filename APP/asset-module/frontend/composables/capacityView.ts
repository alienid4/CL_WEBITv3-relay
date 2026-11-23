// 容量分析（10.3）的純函式：格子文字、矩陣列、限制攤平、設定表單 ⇄ API 轉換。頁面只接線，數字口徑都在後端 capacity_analysis.py。
// 規矩：覆蓋不足／未收集／無資料「不是 0 也不是空白」，一定寫出來；沒有全天平均。
export interface CapCell { source?: string; kind?: string; asset_serial?: string | null; partial?: number; day_type: string; slot: string; slot_label: string; metric: string; samples: number; expected: number | null; days: number
  coverage_pct: number | null; priority?: boolean; peak_days?: CapPeakDay[]; period_max?: number | null; period_max_day?: string | null
  top5_max_median?: number | null; annotated_cmp?: any; max_gap_min?: number | null; p95_note?: string; bucket?: boolean; status: string; avg: number | null; p95: number | null; max: number | null; max_at: string | null }
export interface CapPeakDay { date: string; max: number | null; p95: number | null; avg: number | null; samples: number; annotated: string | null }
export interface CapLimit { code: string; text: string }
export interface CapHost { host: string; no: number; evidence: string; evidence_label: string; limits: CapLimit[]; cells: CapCell[]; data_days: number; span_days: number | null
  from: string | null; to: string | null; days_by_type: Record<string, number>; peak_cpu: any; context: any; interval_s: number | null }
export type CapStat = 'avg' | 'p95' | 'max' | 'top5'

// 容量判斷以尖峰日為準：預設看『全期最大』與『前 5 尖峰日最大值的中位數』，P95／平均只是輔助，不能拿平均掩蓋尖峰
export const CAP_STAT_LABEL: Record<CapStat, string> = { max: '全期最大', top5: '前5尖峰日中位數', p95: 'P95', avg: '平均' }
export const CAP_ANNOT_LABEL: Record<string, string> = { pre: '事前預告', post: '事後補標', 'pre+post': '事前預告＋事後補標', legacy: '舊清單標註' }
export const CAP_STATUS_LABEL: Record<string, string> = { ok: '正常', low_coverage: '覆蓋不足', few_days: '天數不足', no_count: '缺筆數（只留最大值）', coarse_interval: '間隔太粗無法評估', no_data: '無資料', not_collected: '未收集' }

export function capStat(s: any): CapStat { return s === 'p95' || s === 'avg' || s === 'top5' ? s : 'max' }

/** 一格要給的數字（覆蓋不足、未收集、無資料 → null，排序時視為沒有）。 */
export function capCellValue(c: CapCell | undefined, stat: CapStat): number | null {
  if (!c || c.status !== 'ok') return null
  return stat === 'top5' ? (c.top5_max_median ?? null) : c[stat]
}

/** 格子顯示文字。覆蓋不足會帶覆蓋率與「至少出現過」的最大值，讓人知道不是 0。 */
export function capCellText(c: CapCell | undefined, stat: CapStat, unit = '%'): string {
  if (!c) return '無資料'
  if (c.status === 'not_collected') return '未收集'
  if (c.status === 'no_data') return '無資料'
  if (c.status === 'coarse_interval') return c.priority ? '間隔太粗無法評估開盤尖峰' : '間隔太粗無法評估'
  if (c.status === 'no_count') return `缺筆數，平均不可得${c.max != null ? `（最大 ${c.max}${unit}）` : ''}`
  if (c.status === 'few_days') return `天數不足（只有 ${c.days} 天）${c.max != null ? `（至少出現 ${c.max}${unit}）` : ''}`
  if (c.status === 'low_coverage') return `覆蓋不足 ${c.coverage_pct ?? 0}%${c.max != null ? `（至少出現 ${c.max}${unit}）` : ''}`
  const v = stat === 'top5' ? c.top5_max_median : c[stat]
  if (v == null) return stat === 'p95' && c.p95_note ? 'P95 不可得（只有 avg／max）' : '無資料'
  return `${v}${unit}`
}

export function capFind(h: CapHost, dayType: string, slot: string, metric: string): CapCell | undefined {
  // 同一格可能有多個來源（逐日補缺、不混算）：矩陣取樣本最多的那個，來源在單機明細逐列列出
  const hits = h.cells.filter(c => c.day_type === dayType && c.slot === slot && c.metric === metric)
  return hits.length > 1 ? hits.reduce((a, b) => (b.samples > a.samples ? b : a)) : hits[0]
}

/** 矩陣：每台一列，每個時段一欄（欄代號＝時段 key；原值給排序、text 給顯示）。項次＝後端依主機名排好的固定編號。 */
export function capMatrixRows(hosts: CapHost[], slotKeys: string[], dayType: string, metric: string, stat: CapStat, unit = '%') {
  return hosts.map(h => {
    const r: Record<string, any> = { __no: h.no, host: h.host, evidence: h.evidence_label, ev_key: h.evidence, limits_n: h.limits.length,
      days: h.days_by_type?.[dayType] ?? 0, text: {} as Record<string, string> }
    for (const k of slotKeys) {
      const c = capFind(h, dayType, k, metric)
      r[`s_${k}`] = capCellValue(c, stat)
      r.text[k] = capCellText(c, stat, unit)
    }
    return r
  })
}

/** 全部主機的限制攤平成一張表（L1、L2…），給「限制與缺口」頁籤與下鑽用。 */
export function capLimitRows(hosts: CapHost[], staticLimits: CapLimit[] = []) {
  const rows: any[] = []
  for (const h of hosts) for (const l of h.limits) rows.push({ host: h.host, code: l.code, text: l.text, scope: staticLimits.some(s => s.code === l.code) ? '全部主機共通' : '這台' })
  return rows.map((r, i) => ({ ...r, __no: i + 1 }))
}

/** 單機明細：該日型的所有（時段 × 指標）格。 */
export function capDetailRows(h: CapHost | undefined, dayType: string, slotLabels: Record<string, string>, metricLabels: Record<string, string>) {
  if (!h) return []
  return h.cells.filter(c => c.day_type === dayType).map((c, i) => ({
    __no: i + 1, source: c.source ?? '', slot: slotLabels[c.slot] ?? c.slot_label, metric: metricLabels[c.metric] ?? c.metric, samples: c.samples, expected: c.expected,
    days: c.days, coverage: c.coverage_pct, gap: c.max_gap_min, avg: c.avg, p95: c.p95 ?? (c.p95_note ? '不可得' : null), max: c.max, max_at: c.max_at, status: CAP_STATUS_LABEL[c.status] ?? c.status,
    median: capMedianText(c), over: capOverText(c), over_sort: (c as any).over_lo ?? null,
  }))
}

/** 中位數：有值就顯示值；沒有就顯示後端寫的原因（不知道／不可得），不以平均或 P95 代替。 */
export function capMedianText(c: any): string {
  if (c?.median !== null && c?.median !== undefined) return String(c.median)
  const n = String(c?.median_note || '')
  return n.startsWith('不可得') ? '不可得' : '不知道'
}
/** 超標分鐘：後端已依來源取樣間隔換算好文字（精確值／範圍／不知道／不適用），畫面只顯示、不自己算。 */
export function capOverText(c: any): string {
  return String(c?.over_text || '不知道')
}

export function capSummary(hosts: CapHost[]) {
  const n = (e: string) => hosts.filter(h => h.evidence === e).length
  return { total: hosts.length, adequate: n('adequate'), limited: n('limited'), insufficient: n('insufficient') }
}

// ---- 設定表單 ⇄ API ----
export function capDatesToText(list: { date: string; note?: string }[]): string {
  return (list ?? []).map(x => (x.note ? `${x.date} ${x.note}` : x.date)).join('\n')
}
export function capTextToDates(t: string): { date: string; note: string }[] {
  return String(t || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean).map(s => {
    const m = s.match(/^(\S+)\s*(.*)$/)
    return { date: m ? m[1].replace(/\//g, '-') : s, note: m ? m[2] : '' }
  })
}

/** 單機的尖峰日排名（每個時段×指標前 5 天）：資料自己找尖峰，不靠事先定義大量交易日。P95 不可得就寫「不可得」。 */
export function capPeakRows(h: CapHost | undefined, dayType: string, metric: string, slotLabels: Record<string, string>) {
  if (!h) return []
  const rows: any[] = []
  for (const c of h.cells) {
    if (c.day_type !== dayType || c.metric !== metric || !c.peak_days?.length) continue
    c.peak_days.forEach((d, i) => rows.push({ slot: slotLabels[c.slot] ?? c.slot_label, source: c.source ?? '', rank: i + 1, date: d.date, max: d.max, p95: d.p95 ?? '不可得', avg: d.avg ?? '不可得',
      annotated: d.annotated ? (CAP_ANNOT_LABEL[d.annotated] ?? d.annotated) : '—', samples: d.samples }))
  }
  return rows.map((r, i) => ({ ...r, __no: i + 1 }))
}

/** 「標註日 vs 其他日」對照（有人工標註才有；事前預告與事後補標分開）。 */
export function capAnnotRows(h: CapHost | undefined, dayType: string, metric: string, slotLabels: Record<string, string>) {
  if (!h) return []
  const rows: any[] = []
  for (const c of h.cells) {
    const a = c.annotated_cmp
    if (c.day_type !== dayType || c.metric !== metric || !a) continue
    for (const [k, v] of Object.entries<any>(a.by_kind ?? {})) rows.push({ slot: slotLabels[c.slot] ?? c.slot_label, group: CAP_ANNOT_LABEL[k] ?? k, days: v.days, max: v.max })
    rows.push({ slot: slotLabels[c.slot] ?? c.slot_label, group: '其他交易日', days: a.other_days, max: a.other_max })
  }
  return rows.map((r, i) => ({ ...r, __no: i + 1 }))
}

// ---- 交易日情境標記 ----
export const MARKER_KIND_LABEL: Record<string, string> = { pre_announce: '事前預告', intraday: '盤中標記', post_fact: '事後補標' }
export function markerRanges(r: { ranges?: { start: string; end: string }[] }): string { return (r.ranges ?? []).map(x => `${x.start}–${x.end}`).join('、') }
/** 新增表單的來源預覽：與後端同一規則（伺服器時間判定；這裡只是預覽，使用者不能選）。 */
export function markerKindPreview(tradeDate: string, serverNow: string, todayKind: string): string {
  const today = serverNow.slice(0, 10)
  if (!tradeDate) return todayKind
  return tradeDate > today ? 'pre_announce' : tradeDate < today ? 'post_fact' : todayKind
}
export function markerRangesFromText(t: string): { start: string; end: string }[] {
  return String(t || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean).map(x => { const m = x.match(/^(\d{1,2}:\d{2})\s*[-–~]\s*(\d{1,2}:\d{2})$/); return m ? { start: m[1].padStart(5, '0'), end: m[2].padStart(5, '0') } : { start: x, end: '' } })
}
