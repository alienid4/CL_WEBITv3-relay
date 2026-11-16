// 2.x 左側選單「像 Excel 的群組」（2026-10-27 使用者：「平常我不需要全部展開，我需要的時候再展開」）。
// 純函式（不碰 Vue／瀏覽器），測試直接用 node 跑（tests/asset_module/test_v2_nav_groups.py）。
//   saved：使用者存的展開群組；active：目前頁所在群組；closedActive：這次手動把目前群組收起來（不存）。
//   預設（沒存過）＝全部收起，只有目前頁所在的群組打開。

export function openGroups(saved: string[] | null | undefined, active: string | null, all: string[], closedActive = false): string[] {
  const set = new Set((saved ?? []).filter(g => all.includes(g)))
  if (active && all.includes(active) && !closedActive) set.add(active)
  return all.filter(g => set.has(g))
}

/** 點群組標題：回 [新的 saved, 新的 closedActive]。目前頁所在群組的收合只在這次有效，不寫進 saved。 */
export function toggleGroup(saved: string[], g: string, active: string | null, closedActive: boolean, all: string[]): [string[], boolean] {
  if (g === active) {
    const isOpen = !closedActive
    return [saved.filter(x => x !== g || !isOpen), isOpen]
  }
  const next = saved.includes(g) ? saved.filter(x => x !== g) : [...saved, g]
  return [all.filter(x => next.includes(x)), closedActive]
}

/** 全部展開／全部收合（Excel 大綱層級鈕）。 */
export function setAllGroups(expand: boolean, all: string[]): [string[], boolean] {
  return expand ? [[...all], false] : [[], true]
}
