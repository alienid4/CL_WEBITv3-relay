// V2 6.1「掃描缺口」頁籤（?gk=）的篩選（2.043.5，GPT 審查 1）。
// 跟後端 backend/scan_gaps.py 的 VIEWS 是同一個判斷：畫面列、頁籤數字、CSV 匯出（/api/reports/scan-gaps/export?view=）三者一致。
export type GapView = 'gap' | 'fw' | 'exp' | 'all'
/** 後端「掃描正常」的 kind（scan_gaps.OK） */
export const GAP_OK = '正常'

export function gapView(gk: string): GapView {
  return gk === 'fw' || gk === 'exp' || gk === 'all' ? gk : 'gap'
}

export function gapInView(r: { is_gap?: boolean; firewall_first?: boolean; expected_unreachable?: boolean; kind?: string }, v: GapView): boolean {
  if (v === 'all') return true
  if (v === 'fw') return !!r.firewall_first
  if (v === 'exp') return r.kind !== GAP_OK && !!r.expected_unreachable
  return !!r.is_gap
}

/** CSV 匯出的查詢參數：頁籤交給後端用同一個判斷篩，預期不通的不再另外排除（頁籤已經決定要不要列） */
export function gapCsvQuery(gk: string): Record<string, string> {
  return { view: gapView(gk), include_expected: '1' }
}
