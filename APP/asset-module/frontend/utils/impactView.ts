// 4.1「影響範圍查詢」的畫面計算（2.043.11，GPT 審查 A2／A3／B1／B2）。純函式，方便測試；
// 資料都是 /api/blast/impact（或快照裡存的同一份 result）原樣，這裡只整理成畫面要的樣子，不另算影響範圍。

/** 事故規模一行：受影響節點、其中主機（host＋ESXi 節點）、業務系統、通知人數。 */
export function impactScale(result: any): { nodes: number; hosts: number; systems: number; notify: number } {
  const s = result?.summary ?? {}
  const c = s.counts ?? {}
  return {
    nodes: (result?.dependents ?? []).length,
    hosts: (c.host ?? 0) + (c.esxi ?? 0),
    systems: (s.by_biz_system ?? []).length,
    notify: (s.notify ?? []).length,
  }
}

/** 通知對象 → 為哪幾套受影響系統找他（從 by_biz_system 裡每台主機的負責人反查）。 */
export function notifySystems(summary: any): Record<string, string[]> {
  const out: Record<string, Set<string>> = {}
  for (const b of summary?.by_biz_system ?? []) {
    for (const a of b.assets ?? []) {
      for (const o of a.owners ?? []) {
        if (!o?.name) continue
        ;(out[o.name] ??= new Set()).add(b.api_id)
      }
    }
  }
  return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, [...v].sort()]))
}

/** 網址上的快照屬不屬於目前查詢對象（A3：不是就忽略，避免上面查 A、下面明細與 CSV 卻是 B）。 */
export const snapshotBelongs = (snap: any, nodeId: string): boolean => !!snap && snap.node_id === nodeId

/** 看快照時「連線推論沒採用」的下鑽（A2）：只用快照裡存的那一份（自動判定管理連線、模稜兩可的來源 IP 摘要），
 *  不去查現在的連線列；排除清單快照裡沒有逐列明細，回 null（畫面寫明）。 */
export function snapshotObsRows(result: any, bucket: string): any[] | null {
  const o = result?.observed ?? {}
  if (bucket === 'admin_ssh_auto') return (o.auto_admin_ssh ?? []).map((a: any) => ({ ip: a.ip, rows: a.rows, apis: (a.apis ?? []).join('、') || '—', extra: `連進 ${a.targets} 個系統` }))
  if (bucket === 'ambiguous') return (o.ambiguous ?? []).map((a: any) => ({ ip: a.ip, rows: a.rows, apis: (a.apis ?? []).join('、') || '—', extra: '' }))
  return null
}
