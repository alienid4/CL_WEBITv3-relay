// 6.1 納管漏斗的篩選與勾選（2.054.1，批 18 審查 P1：要釘住「送出去的是哪幾台」，不只釘端點）。
// components/v2/Funnel.vue 只呼叫這些純函式；vitest funnel.spec.ts 釘住：
//   · 篩選＝舊 pages/pipeline.vue 同一套（只看待處理、關卡、下鑽的關卡組、環境 → 機房 → OS、關鍵字）
//   · 能勾的＝目前篩選結果裡可納管的；送給依序納管視窗與偵測存活的 IP＝目前篩選結果裡、已勾的那幾台
//   · 換篩選時，看不到的勾選一律丟掉；表頭取消勾選＝全部清掉
export interface FunnelRow {
  ip: string | null; hostname: string | null; asset_serial: string | null
  environment: string | null; env_group: string; physical_location: string | null
  os: string | null; os_type: string; stage: string; stage_label: string
  collect_ok: number | null; onboard_block: unknown | null; onboard_exempt: boolean
  [k: string]: any
}
export interface FunnelFilter {
  todoStages: string[]; onlyTodo: boolean; stage: string; stageSet: string[]
  env: string[]; loc: string[]; os: string[]; kw: string
}
export const locOf = (r: FunnelRow) => (r.physical_location || '').trim() || '(未填)'

/** 只套「這一欄以外」的條件（環境 → 機房 → OS 層層收斂；舊頁 rowsBefore 同一套） */
export function rowsBefore(items: FunnelRow[], f: FunnelFilter, stopAt: 'env' | 'loc' | 'os' | 'all'): FunnelRow[] {
  let rows = items
  if (f.onlyTodo && f.todoStages.length) rows = rows.filter(r => f.todoStages.includes(r.stage))
  if (f.stage) rows = rows.filter(r => r.stage === f.stage)
  if (f.stageSet.length) rows = rows.filter(r => f.stageSet.includes(r.stage))
  if (stopAt !== 'env' && f.env.length) rows = rows.filter(r => f.env.includes(r.env_group))
  if ((stopAt === 'os' || stopAt === 'all') && f.loc.length) rows = rows.filter(r => f.loc.includes(locOf(r)))
  return rows
}

export function filterFunnel(items: FunnelRow[], f: FunnelFilter): FunnelRow[] {
  let rows = rowsBefore(items, f, 'all')
  if (f.os.length) rows = rows.filter(r => f.os.includes(r.os_type))
  const kw = f.kw.trim().toLowerCase()
  if (kw) rows = rows.filter(r => [r.ip, r.hostname, r.asset_serial, r.os, r.stage_label, r.physical_location, r.environment].some(v => (v || '').toString().toLowerCase().includes(kw)))
  return rows
}

/** 能不能勾進依序納管（舊頁同一條判準） */
export function canBatchOnboard(r: FunnelRow): boolean {
  return !!r.ip && r.collect_ok !== 1 && !r.onboard_block && !r.onboard_exempt && r.stage !== 'retired'
}
/** 能勾的＝目前篩選結果裡可納管的 */
export const selectableRows = (filtered: FunnelRow[]) => filtered.filter(canBatchOnboard)

/** 換篩選後：只留目前篩選結果裡、能勾、而且原本有勾的 */
export function pruneSelection(sel: Record<string, boolean>, filtered: FunnelRow[]): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  for (const r of selectableRows(filtered)) if (sel[r.ip!]) out[r.ip!] = true
  return out
}
/** 送出去的 IP（依序納管視窗、偵測存活）＝目前篩選結果裡已勾的，順序照清單 */
export function ipsToSend(sel: Record<string, boolean>, filtered: FunnelRow[]): string[] {
  return selectableRows(filtered).filter(r => sel[r.ip!]).map(r => r.ip!)
}
/** 「勾選這關 N 台」：把目前篩選結果裡能勾的全部勾上 */
export function selectAll(sel: Record<string, boolean>, filtered: FunnelRow[]): Record<string, boolean> {
  const out = { ...sel }
  for (const r of selectableRows(filtered)) out[r.ip!] = true
  return out
}
// 表頭取消勾選＝全部清掉：2.055.1 起由 V2Table 的 header-clears-all 明確做（表頭取消時直接送 {}），
// 不再用「一次少了幾台」去猜（第 1 頁 1 台＋第 2 頁 5 台時猜不到；批 19 審查 P1-1）。

/** 點關卡卡片要寫進網址的變動（舊頁 pickStage 同一套＋2.054.1 修正）：
 *  · 再點同一張＝取消關卡；換卡片＝清掉下鑽（drill）
 *  · 下鑽帶的 OS 條件留著（網址還沒有 os_type 時，把下鑽的 OS 寫進網址）——舊頁 pickStage 不清 osFilter
 *  · 不在「還需要處理」的關卡（已收集／已退役／非納管…）一律關掉「只看還需要處理的」，清單才對得上卡片 */
export function pickStagePatch(o: { current: string; k: string; osInUrl: boolean; drillOs: string[]; todoStages: string[] }): Record<string, string | undefined> {
  const on = o.current === o.k
  const keepOs = !o.osInUrl && o.drillOs.length ? { os_type: o.drillOs.join(',') } : {}
  return { stage: on ? undefined : o.k, drill: undefined, ...keepOs, ...(!on && !o.todoStages.includes(o.k) ? { ft: '0' } : {}) }
}

/** 選了關卡又有其他篩選時，清單台數可能跟卡片不同；回 { gap, card }，gap＝要不要在畫面講出來 */
export function cardGap(o: { stage: string; counts: Record<string, number>; filtered: number; others: boolean }): { gap: boolean; card: number } {
  const card = o.stage ? o.counts[o.stage] ?? 0 : 0
  return { gap: !!o.stage && (o.others || o.filtered !== card), card }
}

/** 「清除其他篩選」：只留關卡（不在待處理的關卡照樣關掉「只看還需要處理的」） */
export function stageOnlyQuery(stage: string, todoStages: string[]): Record<string, string> {
  return { tab: 'funnel', stage, ...(todoStages.includes(stage) ? {} : { ft: '0' }) }
}
