// 2.1 主機查詢「偵測存活」頁籤的純函式與型別（2.041.97）。計數與下鑽走同一份（加總＝下鑽）。
export interface AliveRow {
  asset_serial: string | null; hostname: string | null; ip: string | null
  state: 'alive' | 'no_response' | 'unknown' | 'excluded' | 'skipped'; state_label: string; evidence: string; verdict: string
  icmp_sent: number | null; icmp_recv: number | null; avg_ms: number | null; open_ports: string
}
export interface AliveReport {
  run_id: number; scope: string; started_at: string; finished_at: string | null; created_by: string | null
  ports: string; requested: number; deadline_hit: boolean; still_running?: number; reused?: number; counts: Record<string, number>; rows: AliveRow[]
}
export interface AliveRun {
  run_id: number; started_at: string; created_by: string | null; scope: string; total: number
  alive: number; no_response: number; unknown: number; excluded: number; skipped: number; from_v2: boolean
}

export const ALIVE_ORDER = ['alive', 'no_response', 'unknown', 'excluded', 'skipped'] as const
export const ALIVE_LABEL: Record<string, string> = {
  alive: '存活', no_response: '連不上', unknown: '無法判斷', excluded: '已排除（VMware 設備）', skipped: '略過',
}

export function aliveCounts(rows: AliveRow[]): Record<string, number> {
  const c: Record<string, number> = { alive: 0, no_response: 0, unknown: 0, excluded: 0, skipped: 0 }
  for (const r of rows) c[r.state] = (c[r.state] ?? 0) + 1
  return c
}

export function aliveFilter(rows: AliveRow[], state: string): AliveRow[] {
  return state === 'all' ? rows : rows.filter(r => r.state === state)
}

/** 「標記下線」只帶連不上、且有資產序號的列（已排除、略過、無法判斷、存活都不帶） */
export function offlineSerials(rows: AliveRow[]): string[] {
  return [...new Set(rows.filter(r => r.state === 'no_response' && r.asset_serial).map(r => r.asset_serial as string))]
}
