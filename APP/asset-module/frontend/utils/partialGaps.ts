// 整台口徑下的次要待辦（Codex 74／007）：逐筆登記未涵蓋、但整台判成別的狀態的機器。
// 代表筆口徑（預設）下 applies=false，什麼都不顯示。
export interface PartialGap { asset_serial: string | null; hostname: string | null; ip: string | null; machine_state: string; gap_rows: string[]; row_states: string[] }
export interface PartialGaps { n: number; items: PartialGap[]; applies: boolean }

/** 只有整台口徑且有缺口才回提示文字；代表筆口徑回空字串（不顯示）。 */
export function partialGapNote(pg?: PartialGaps | null): string {
  if (!pg || !pg.applies || !pg.n) return ''
  return `另有 ${pg.n} 台：部分登記未涵蓋，但整台已判為其他狀態（例如失聯）。這些缺口仍要處理`
}

/** 轉成清單頁既有的列格式（狀態欄寫整台狀態與缺口登記，讓人看得出為什麼在這裡）。 */
export function partialGapRows(pg?: PartialGaps | null) {
  return (pg?.items ?? []).map(x => ({
    // asset_serial 只用於排序／列識別（第一筆缺口登記）；畫面上每筆缺口各自連結，見 gap_serials（不合成不存在的序號）
    asset_serial: x.gap_rows[0] ?? x.asset_serial, gap_serials: [...x.gap_rows], hostname: x.hostname, ip: x.ip,
    state: `${x.machine_state}（缺口登記：${x.gap_rows.length} 筆）`, collect_checked_at: null, collect_error: null, os_type: null,
  }))
}

/** 一列要連到哪些資產：缺口列＝每筆缺口登記各一個；一般列＝自己的序號（沒有序號＝空，畫面顯示未登記）。 */
export function serialsOf(r: { asset_serial?: string | null; gap_serials?: string[] }): string[] {
  if (r.gap_serials?.length) return r.gap_serials
  return r.asset_serial ? [r.asset_serial] : []
}
