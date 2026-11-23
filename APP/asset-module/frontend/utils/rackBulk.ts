// 5.1 機房平面圖：批次標記現場盤點結果（V65，2.059.3，⚠D4）的預覽計算。純函式，vitest 釘住。
import { computed, ref, watch } from 'vue'
// 送出仍是舊 /datacenter 同一支 PUT /api/racks/state/bulk（state＝off／removed／null 清除，note＝現場盤點），後端不動。
export type BulkKind = 'off' | 'removed' | 'clear'
export const BULK_LABEL: Record<BulkKind, string> = { off: '標記已關機未下架', removed: '標記已不在櫃上', clear: '清除標記' }

/** 一台目前的狀態文字（跟清單「狀態」欄同一個寫法） */
export function stateText(d: any): string {
  return d?.kind === 'removed' ? '已不在櫃上' : (d?.state_label || '—')
}

export interface BulkPreview { kind: BulkKind; keys: string[]; total: number; byState: { label: string; n: number }[]; unchanged: number; manual: number; missing: number }
/** 按下去之前先算：這次會送幾台、它們現在各是什麼狀態、有幾台本來就是這個標記（送了也不變）、幾台已經有人工標記、幾台清單裡找不到。
 *  keys 只留目前清單看得到的（visible），看不到的不會被送出。 */
export function bulkPreview(kind: BulkKind, picked: string[], visible: string[], devices: Record<string, any>): BulkPreview {
  const vis = new Set(visible)
  const keys = [...new Set(picked)].filter(k => vis.has(k))
  const by = new Map<string, number>()
  let unchanged = 0, manual = 0, missing = 0
  for (const k of keys) {
    const d = devices[k]
    if (!d) { missing++; continue }
    const s = stateText(d)
    by.set(s, (by.get(s) ?? 0) + 1)
    if (d.override) manual++
    if ((kind === 'clear' && !d.override) || (kind !== 'clear' && d.override === kind)) unchanged++
  }
  return { kind, keys, total: keys.length, byState: [...by.entries()].map(([label, n]) => ({ label, n })).sort((a, b) => b.n - a.n), unchanged, manual, missing }
}

/** 勾選＋預覽的狀態：勾選一變（多勾、少勾、換一批），手上的預覽就作廢——確認送出的一定是畫面上現在勾的那幾台（2.059.5 審查）。 */
export function useBulkSelection() {
  const picked = ref<Record<string, boolean>>({})
  const prev = ref<BulkPreview | null>(null)
  const keys = computed(() => Object.keys(picked.value).filter(k => picked.value[k]))
  watch(() => keys.value.join('|'), () => { prev.value = null })
  return { picked, prev, keys }
}
