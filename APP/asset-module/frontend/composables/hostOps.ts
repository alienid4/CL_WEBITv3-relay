// 單機維運記錄本（主機履歷「事件與維運紀錄」）的純函式：篩選、列整形、示範資料。
// 計次與下鑽是同一份：counts[*].event_ids 就是事件明細要顯示的 id（後端 host_ops.count_events）。
export interface OpsEvent {
  id: string; __no: number; source: string; source_label: string; category: string; category_label: string; partition: string; title: string
  first_at: string; last_at: string | null; recovered_at: string | null; status: string; partial_recover?: boolean
  match_label: string; countable: boolean; metric_text: string; evidence: { k: string; v: string }[]; recovered_label?: string | null; closed_at?: string | null; closed_label?: string | null
  handling_text: Record<'action' | 'root_cause' | 'outcome', string>; notes: any[]
}
export interface OpsCount { key: string; __no: number; category: string; category_label: string; partition: string; source: string; source_label: string; count: number; ongoing: number; event_ids: string[] }

export const PERIOD_OPTS = [
  { k: 'today', label: '今天' }, { k: 'week', label: '本週' }, { k: '7d', label: '最近 7 天' }, { k: '30d', label: '最近 30 天' },
  { k: '90d', label: '最近 90 天' }, { k: 'next30', label: '未來 30 天' },
]
export const STATUS_LABEL: Record<string, string> = { ongoing: '持續中', recovered: '已恢復', closed: '已結案', unknown: '結束時間未知' }
export const KIND_LABEL: Record<string, string> = { action: '處理措施', root_cause: '已確認根因', outcome: '改善結果' }
export const SRC_STATE: Record<string, string> = { has_data: '有資料', no_data: '無資料', 'n/a': '不適用／無此來源', error: '讀取失敗', no_machine_data: '此機未取得', none_recorded: '無紀錄' }

export interface Filter { label: string; ids: Set<string> | null }

/** 卡片／計次點下去要看的事件 id 集合；null＝全部。 */
export function filterFor(kind: 'all' | 'ongoing' | 'disk' | 'memory' | 'count', d: { counts: OpsCount[]; ongoing_ids: string[] }, count?: OpsCount): Filter {
  if (kind === 'all') return { label: '', ids: null }
  if (kind === 'ongoing') return { label: '持續中', ids: new Set(d.ongoing_ids) }
  if (kind === 'count' && count) return { label: `${count.category_label}${count.partition ? ' ' + count.partition : ''}（${count.source_label}）`, ids: new Set(count.event_ids) }
  const cat = kind
  return { label: kind === 'disk' ? '磁碟' : '記憶體', ids: new Set(d.counts.filter(c => c.category === cat).flatMap(c => c.event_ids)) }
}

export function recoveredText(e: Pick<OpsEvent, 'recovered_at' | 'status' | 'partial_recover'> & { recovered_label?: string | null; closed_at?: string | null; closed_label?: string | null }): string {
  if (e.recovered_at) return e.recovered_at + (e.partial_recover ? '（僅此項消失，裝置仍告警）' : '')
  if (e.closed_at) return `結案 ${e.closed_at}（${e.closed_label || '結案時間，非單機恢復時間'}）`
  if (e.status === 'ongoing') return '尚未恢復'
  return e.recovered_label || '未取得'
}

/** 事件／次數卡片的顯示：沒有事件、而且所有事件來源對這台都沒有資料 → 「未取得」，不能被讀成 0＝沒有異常。 */
export function cardValue(n: number, d: { evidence_any?: boolean; totals: { events: number } }): string | number {
  return d.totals.events === 0 && d.evidence_any === false ? '未取得' : n
}

export function eventRows(events: OpsEvent[], f: Filter) {
  return events.filter(e => !f.ids || f.ids.has(e.id)).map(e => ({
    ...e, st: STATUS_LABEL[e.status] ?? e.status, rec: recoveredText(e), last: e.last_at || '未取得',
    h_action: e.handling_text.action, h_root: e.handling_text.root_cause, h_outcome: e.handling_text.outcome,
  }))
}

/** 示範資料（前端自己產生、不入庫、畫面明標「示範」）：讓還沒有真實事件的人看得到頁面長相。 */
export function demoData() {
  const ev = (n: number, over: Partial<OpsEvent>): OpsEvent => ({
    id: `DEMO-${n}`, __no: n, source: 'whatsup', source_label: 'WhatsUp Gold 告警', category: 'disk', category_label: '磁碟', partition: '/var', title: '磁碟 /var',
    first_at: '2026-10-20 08:00:00', last_at: '2026-10-20 09:00:00', recovered_at: '2026-10-20 10:00:00', status: 'recovered', match_label: 'IP＋主機名一致', countable: true,
    metric_text: '未取得（WhatsUp 只提供告警文字，沒有量測數值）', evidence: [{ k: '告警原文', v: 'Disk Free Space - /var' }],
    handling_text: { action: '清除舊日誌', root_cause: '未確認', outcome: '未填' }, notes: [], ...over,
  })
  const events: OpsEvent[] = [
    ev(1, { id: 'DEMO-3', first_at: '2026-10-30 23:58:00', last_at: '2026-10-31 00:01:00', recovered_at: '2026-10-31 00:02:00', source: 'nmon', source_label: 'NMON 門檻超標',
      metric_text: '峰值 91%（2026-10-31 00:00:00；門檻 80%）', match_label: '主機名符合' }),
    ev(2, { id: 'DEMO-2', category: 'memory', category_label: '記憶體', partition: '', title: '記憶體', first_at: '2026-10-25 08:00:00', recovered_at: null, status: 'ongoing',
      handling_text: { action: '未填', root_cause: '未確認', outcome: '未填' } }),
    ev(3, { id: 'DEMO-1' }),
  ]
  events.forEach((e, i) => { e.__no = i + 1 })
  const counts: OpsCount[] = [
    { key: 'disk|/var|nmon', __no: 1, category: 'disk', category_label: '磁碟', partition: '/var', source: 'nmon', source_label: 'NMON 門檻超標', count: 1, ongoing: 0, event_ids: ['DEMO-3'] },
    { key: 'disk|/var|whatsup', __no: 2, category: 'disk', category_label: '磁碟', partition: '/var', source: 'whatsup', source_label: 'WhatsUp Gold 告警', count: 1, ongoing: 0, event_ids: ['DEMO-1'] },
    { key: 'memory||whatsup', __no: 3, category: 'memory', category_label: '記憶體', partition: '', source: 'whatsup', source_label: 'WhatsUp Gold 告警', count: 1, ongoing: 1, event_ids: ['DEMO-2'] },
  ]
  return {
    machine: { key: 'DEMO', name: '示範主機', ips: ['10.99.0.1'], serials: ['DEMO'] }, window: { period: '30d', label: '最近 30 天', start: '2026-10-02 12:00:00', end: '2026-11-01 12:00:00', tz: 'Asia/Taipei', future: false },
    batch: 'demo00000000', evidence_any: true, generated_at: '2026-11-01 12:00:00', events, counts, ongoing_ids: ['DEMO-2'],
    totals: { events: 3, countable: 3, ongoing: 1, unknown_end: 0, disk: 2, memory: 1 }, coverage: [], upcoming_coverage: [], upcoming: [], pending: [], orphan_notes: [], demo: true,
  }
}
