// 7.1 機櫃盤點「各櫃進度」的數字下鑽到「差異處理」（2.043.5，GPT 審查 4／5）：
// 網址帶 rk＝機櫃（"機房 機櫃"，跟差異列的 host 同一個字串）＋dk＝哪一格，差異頁只列那一櫃、那一類。
// 種類名稱＝backend/rack_inventory.py DIFF_LABEL。
export type RackDiffKey = 'gone' | 'bad' | 'extra' | 'open'
export const RACK_DIFF_LABELS: Record<Exclude<RackDiffKey, 'open'>, string[]> = {
  gone: ['不在'],
  bad: ['U 位不符', '型號不符', '序號不符'],
  extra: ['櫃上有但沒登記'],
}
export const rackKey = (room: string, rack: string) => `${room} ${rack}`

/** 各櫃進度那一格 → 差異頁網址參數。open＝這櫃待處理的全部種類；其他＝這櫃該種類（不分處理狀態，跟格子的數字同口徑） */
export function rackDiffQuery(task: string | number, room: string, rack: string, dk: RackDiffKey): Record<string, string> {
  return { tab: 'diffs', task: String(task), rk: rackKey(room, rack), dk }
}

/** 差異列是否落在網址指定的櫃＋種類（沒帶 rk／dk 就不篩） */
export function inRackDiff(d: { host?: string; kind?: string }, rk: string, dk: string): boolean {
  if (rk && d.host !== rk) return false
  const labels = (RACK_DIFF_LABELS as Record<string, string[]>)[dk]
  if (labels && !labels.includes(d.kind || '')) return false
  return true
}

/** 帶 dk 進差異頁時，處理狀態的預設篩選：open＝待處理；其他種類＝全部（格子數字不分狀態） */
export const rackDiffStatus = (dk: string): string => (dk === 'open' ? '待處理' : dk ? '' : '待處理')
