// 機櫃「前後各一台」（2026-11-01）：兩台背對背共用同一段 U（一台在正面、一台在背面）。
// 後端 rack_detail 給每台 side（rear 背面／front 配對的正面／沒有＝一般設備）。純函式，vitest 直接測。
export type RackSide = 'front' | 'rear'
export interface SideDev { key: string; side?: 'front' | 'rear' | null; fr_with?: string[] }

// 這櫃有沒有任何背面設備：沒有就不顯示「正面／背面」切換鈕
export function hasRear(devs: { side?: string | null }[]): boolean {
  return devs.some(d => d.side === 'rear')
}

// 正面只畫正面設備（含沒有標記的一般設備）；背面只畫背面設備
export function onSide(d: { side?: string | null }, view: RackSide): boolean {
  return view === 'rear' ? d.side === 'rear' : d.side !== 'rear'
}

// 另一面的設備只畫淡淡的佔位（正面視圖看得到背面設備佔的 U、背面視圖看得到正面的），避免把有東西的 U 當成空的
export function isGhost(d: { side?: string | null }, view: RackSide): boolean {
  return !onSide(d, view)
}

// 佔位的滑過提示：只有真的配對的設備才標「正面／背面設備」，一般設備不特別標
export function ghostTitle(d: { name: string; side?: string | null }, uText: string): string {
  const tag = d.side === 'rear' ? '，背面設備' : d.side === 'front' ? '，正面設備' : ''
  return `${d.name}（${uText}${tag}）`
}

// 「前＋後」標籤：這台有對面的設備（共用同一段 U）
export function sharesU(d: SideDev): boolean {
  return (d.fr_with ?? []).length > 0
}

// 點開「前＋後」要看到的兩台：自己＋對面（依序：正面在前、背面在後）
export function pairOf<T extends SideDev>(d: T, all: T[]): T[] {
  const keys = new Set([d.key, ...(d.fr_with ?? [])])
  return all.filter(x => keys.has(x.key)).sort((a, b) => (a.side === 'rear' ? 1 : 0) - (b.side === 'rear' ? 1 : 0))
}

export const SIDE_TEXT: Record<RackSide, string> = { front: '正面', rear: '背面' }
