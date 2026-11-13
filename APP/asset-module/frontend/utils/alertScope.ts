// 1.1 今日概況／夜間速查「現在仍在告警」的範圍與收合（Q9／Q15）。純函式，vitest 直接測。
// 範圍：預設只列跟 Teams 同一個主機組政策範圍（4.1 notify_policies，後端 v2_today.scope_filter）；網址 ?all=1＝看全部。
// 收合：後端 groups（同一分鐘開始＋同一個來源設備收成一列，n＝件數、ids＝那幾筆）；點件數＝網址 ?ag=<組>，下面列出那幾筆。

export interface AlertGroup { group: string; n: number; ids: number[]; [k: string]: any }
export interface AlertScope { mode: 'policy' | 'all'; requested: string; keywords: string[]; n_all: number; hidden: number; label: string }

/** 網址的 all 參數 → 後端 scope */
export const scopeOf = (q: unknown): 'all' | 'policy' => (String(q ?? '') === '1' ? 'all' : 'policy')

/** 切換範圍的網址 query（保留其他參數，切換時收起下鑽） */
export function toggleScopeQuery(query: Record<string, any>, toAll: boolean): Record<string, any> {
  const { all: _a, ag: _g, ...rest } = query
  return toAll ? { ...rest, all: '1' } : rest
}

/** 某一組的下鑽網址 query（同一組再點一次＝收起） */
export function groupQuery(query: Record<string, any>, g: AlertGroup): Record<string, any> {
  const { ag, ...rest } = query
  return ag === g.group ? rest : { ...rest, ag: g.group }
}

/** 下鑽：這一組的那幾筆（照告警列原本的順序與項次）。組不存在＝空清單。 */
export function groupRows<T extends { id: number }>(rows: T[], groups: AlertGroup[], key: unknown): T[] {
  const g = groups.find(x => x.group === String(key ?? ''))
  if (!g) return []
  const ids = new Set(g.ids)
  return rows.filter(r => ids.has(r.id))
}

/** 加總＝下鑽：各組件數加總要等於列數 */
export const groupsTotal = (groups: AlertGroup[]): number => groups.reduce((s, g) => s + (g.n || 0), 0)

/** 組的開始時間（到分）＋來源的短字，畫面標題用 */
export const groupTitle = (g: AlertGroup | undefined): string =>
  (g ? `${String(g.started_at || '').slice(5, 16)} 開始・${g.who || g.ip || '未知來源'}・${g.n} 件` : '')

/** 摺疊區某一類的下鑽網址 query（?ab=<類別>；同一類再點一次＝收起；切換時收起組的下鑽） */
export function bucketQuery(query: Record<string, any>, key: string): Record<string, any> {
  const { ab, ag: _g, ...rest } = query
  return ab === key ? rest : { ...rest, ab: key }
}

/** 「需要注意」標題的件數：告警沒啟用／沒收到資料寫原因；心跳壞了在數字後標「（盲區）」，不讓 0 被讀成沒事 */
export function attentionCount(alerts: { status?: string; attention?: { n: number; blind?: string | null } | null } | null | undefined): string {
  if (!alerts) return '—'
  const a = alerts.attention
  if (!a) return ({ disabled: '未啟用', uncollected: '未收集', error: '無法計算' } as Record<string, string>)[alerts.status || ''] || '未收集'
  return a.blind ? `${a.n} 件（監控盲區）` : `${a.n} 件`
}
