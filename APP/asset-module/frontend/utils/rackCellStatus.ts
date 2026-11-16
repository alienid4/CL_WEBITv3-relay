// 機櫃正面圖每一格的狀態標示（2.043.13，docs/ui-v2/機櫃差異待辦_規格_2026-11-04.md 第一階段第 3 點）。
// 固定一組字：一致／Excel 有系統無／系統有 Excel 無／Excel 尚未更新／下架待確認／關機，系統設備與 Excel 層格子共用。
// Excel 對照的原始狀態碼（後端 rack_excel_import）：OK、POSONLY（系統面板在同一格，名稱寫法不同）、ABSENT（系統說在這櫃、Excel 沒寫）、
// MISSING（Excel 有、系統沒有）、NOPOS／MOVED／OTHERROOM／BLOCKED（系統有對到但位置待處理）。STALE＝已處理掉、下一版 Excel 仍寫著（第二階段）。
export const CELL_TEXT: Record<string, string> = {
  OK: '一致', POSONLY: '一致（名稱寫法不同）', ABSENT: '系統有・Excel 無', MISSING: 'Excel 有・系統無', STALE: 'Excel 尚未更新',
  NOPOS: '系統未登位置', MOVED: '位置不同', OTHERROOM: '系統登在別間', BLOCKED: '待人工確認',
  off: '關機', pending: '下架待確認',
}

/** 系統設備那一格：關機／下架待確認（機櫃狀態）優先，再來是跟 Excel 對的結果；沒有 Excel 對照時不寫 */
export function sysCellStatus(state: string | null | undefined, exStatus: string | null | undefined): { code: string; label: string } {
  if (state === 'off' || state === 'pending') return { code: state, label: CELL_TEXT[state] }
  if (!exStatus) return { code: '', label: '' }
  return { code: exStatus, label: CELL_TEXT[exStatus] ?? exStatus }
}

/** Excel 層那一格 */
export function xlCellStatus(status: string): { code: string; label: string } {
  return { code: status, label: CELL_TEXT[status] ?? status }
}

// 狀態格（第 7 格）只畫圖示＋顏色、不寫字（2.044.8，使用者：「待確認可以圖示或顏色，不要寫字」），字放 title。
export const CELL_ICON: Record<string, string> = {
  OK: '✓', POSONLY: '✓', ABSENT: '△', MISSING: '✕', STALE: '↻',
  NOPOS: '?', BLOCKED: '?', pending: '?', MOVED: '⇄', OTHERROOM: '⇄', off: '⏻',
}
/** 系統面板「要人注意的事」（RackElevation 的 sysFlag）對應的圖示 */
export const FLAG_ICON: Record<string, string> = {
  現場更正: '✎', 疑似登記錯誤: '!', 重疊: '⧉', '管理介面・找不到本體': '⚙',
}
/** 狀態格要畫的圖示：先標記、再狀態；都沒有就空白 */
export function cellIcons(flag: string | null | undefined, code: string | null | undefined): string {
  return [flag ? FLAG_ICON[flag] ?? '' : '', code ? CELL_ICON[code] ?? '' : ''].join('')
}
/** 圖例「圖示說明」：每個圖示代表什麼（同一個圖示多種狀態合併成一行） */
export const ICON_LEGEND: { icon: string; label: string }[] = [
  { icon: '✓', label: '一致' }, { icon: '△', label: '系統有・Excel 無' }, { icon: '✕', label: 'Excel 有・系統無' },
  { icon: '↻', label: 'Excel 尚未更新' }, { icon: '?', label: '待確認（未登位置／待人工確認／下架待確認）' },
  { icon: '⇄', label: '位置不同／登在別間' }, { icon: '⏻', label: '關機' },
  { icon: '✎', label: '現場更正' }, { icon: '!', label: '疑似登記錯誤' }, { icon: '⧉', label: '重疊' }, { icon: '⚙', label: '管理介面・找不到本體' },
]
