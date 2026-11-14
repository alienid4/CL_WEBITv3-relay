// 主機關聯探索（工作包 C）的圖狀態：純函式，不碰畫面，方便測試。
// 規格：docs/host-relations/規格_主機關聯探索_v1.0_2026-11-06.md 第 2 章（探索狀態與競態）。
//
// 核心規則：
//  · 一次只展開一跳；同一個 key（節點＋方向＋snapshot）只會有一個請求在飛。
//  · 回應要通過 epoch／requestId／snapshotId 三道核對才合併；過時的回應丟掉、計數。
//  · 已在圖上的節點位置不動；新節點排在被展開節點的左欄（入站）／右欄（出站）；一欄放不下就分組（見 groupSide）。
//  · 復原只拿掉「那一次動作獨有」的邊，以及因此變成孤立的新節點；別的動作還在用的共享節點保留。
//  · 全圖預算 100 節點／300 條邊：這一批放進去會超過，就整批不放（不隨機放一部分）。

export type Direction = 'outbound' | 'inbound' | 'both'
export interface RelNode { node_id: string; entity_type: 'host' | 'endpoint'; display_name: string | null; addresses: string[]; management: { status: string; reason_code: string; detail: string | null }; identity: { status: string; candidate_count: number }; coverage: any; health: any; machine_key: string | null }
export interface RelEdge { edge_id: string; source_id: string; target_id: string; protocol: string; destination_port: number | null; service: { name: string | null; basis: string | null }; direction_status: string; direction_basis: string; observations: number; source_refs_total: number; source_refs: any[]; first_seen: any; last_seen: any; observation_sides: string; freshness: string; partial: boolean }
export interface Action { id: number; nodeId: string; direction: Direction; addedNodes: string[]; addedEdges: string[]; groupIds?: string[] }
/** 一組（2026-11-08 跟 GPT 討論兩輪＋使用者定）：一欄放不下時，清冊沒有的 IP 依 Port 組合收、已納管主機依業務系統收。 */
export interface Group { id: string; kind: 'port' | 'system'; center: string; side: 'left' | 'right'; members: string[]; title: string; sub: string }
export interface Expansion { status: 'loading' | 'success' | 'partial' | 'empty' | 'error'; cursor: string | null; hasMore: boolean; loadedPages: number; requestId: number; total: number; error?: string }
export interface GraphState {
  rootId: string; selectedId: string | null
  nodes: Record<string, RelNode>; edges: Record<string, RelEdge>; pos: Record<string, { x: number; y: number }>
  snapshotId: string | null; epoch: number; history: Action[]; exp: Record<string, Expansion>
  nextId: number; dropped: number
  groups: Record<string, Group>; groupOf: Record<string, string>   // 成員 → 組（拉出來的成員不在 groupOf 裡）
}

export const BUDGET = { nodes: 100, edges: 300 }
export const COL_X = 300          // 欄與欄的水平距離
export const ROW_Y = 64           // 同一欄上下間距
export const MAX_SLOTS = 8        // 一欄超過這麼多格才分組（≈畫布高度放得下的數量）

export function newState(rootId: string): GraphState {
  return { rootId, selectedId: rootId, nodes: {}, edges: {}, pos: { [rootId]: { x: 0, y: 0 } }, snapshotId: null, epoch: 0, history: [], exp: {}, nextId: 1, dropped: 0, groups: {}, groupOf: {} }
}

export const expKey = (nodeId: string, dir: Direction, snap: string | null) => `${nodeId}|${dir}|${snap ?? ''}`

/** 開始一個展開請求；同 key 正在載入就回 null（不重複送）。 */
export function beginExpand(s: GraphState, nodeId: string, dir: Direction): { key: string; requestId: number; epoch: number } | null {
  const key = expKey(nodeId, dir, s.snapshotId)
  if (s.exp[key]?.status === 'loading') return null
  const requestId = s.nextId++
  s.exp[key] = { ...(s.exp[key] ?? { cursor: null, hasMore: false, loadedPages: 0, total: 0 }), status: 'loading', requestId }
  return { key, requestId, epoch: s.epoch }
}

/** 這個節點跟中心之間的連線，目的埠的集合（排序後）。 */
function portsBetween(edges: RelEdge[], center: string, id: string): number[] {
  return [...new Set(edges.filter(x => (x.source_id === center && x.target_id === id) || (x.target_id === center && x.source_id === id))
    .map(x => x.destination_port).filter((p): p is number => p != null))].sort((a, b) => a - b)
}

/** 一欄放不下（超過 MAX_SLOTS 格）才分組：清冊沒有的 IP 依 Port 組合、已納管且只掛一個業務系統的主機依系統；
 *  其他（納管失敗、狀態未知、多系統、身分不明）一律單獨成框。同一鍵只有 1 個就不收。回傳 [格子, 新組]。 */
export function groupSide(s: GraphState, center: string, side: 'left' | 'right', ids: string[], edges: RelEdge[]): { slots: string[]; groups: Group[] } {
  if (ids.length <= MAX_SLOTS) return { slots: ids, groups: [] }
  const keyOf = (id: string): string | null => {
    const n = s.nodes[id]
    if (!n) return null
    if (n.entity_type === 'endpoint' && n.management.status === 'unmanaged' && n.identity.status === 'unresolved') {
      const ps = portsBetween(edges, center, id)
      return ps.length ? `port:${ps.join('+')}` : null
    }
    const sys = (n as any).systems as string[] | undefined
    if (n.management.status === 'managed' && sys?.length === 1) return `system:${sys[0]}`
    return null
  }
  const buckets = new Map<string, string[]>()
  const order: string[] = []
  for (const id of ids) {
    const k = keyOf(id)
    if (!k) { order.push(id); continue }
    if (!buckets.has(k)) { buckets.set(k, []); order.push(`@${k}`) }
    buckets.get(k)!.push(id)
  }
  const slots: string[] = []
  const groups: Group[] = []
  for (const o of order) {
    if (!o.startsWith('@')) { slots.push(o); continue }
    const k = o.slice(1); const members = buckets.get(k)!
    if (members.length < 2) { slots.push(members[0]); continue }
    const id = `g:${center}|${side}|${k}`
    const [kind, val] = k.split(/:(.*)/s) as ['port' | 'system', string]
    const g: Group = kind === 'port'
      ? { id, kind, center, side, members, title: `TCP ${val.replace(/\+/g, '＋')}・${members.length} 個端點`, sub: '清冊沒有' }
      : { id, kind, center, side, members, title: `業務系統 ${val}`, sub: `${members.length} 台・點開看清單` }
    groups.push(g); slots.push(id)
  }
  return { slots, groups }
}

/** 一欄直排：以中心的高度為中線上下排開；位置被佔用就往下找空位（已在圖上的不動）。 */
function stack(s: GraphState, x: number, cy: number, ids: string[]) {
  const taken = Object.values(s.pos)
  const free = (p: { x: number; y: number }) => taken.every(q => Math.abs(q.x - p.x) > 150 || Math.abs(q.y - p.y) > ROW_Y - 8)
  const y0 = cy - ((ids.length - 1) * ROW_Y) / 2
  ids.forEach((id, i) => {
    let p = { x, y: y0 + i * ROW_Y }
    for (let k = 0; k < 200 && !free(p); k++) p = { x, y: p.y + ROW_Y }
    s.pos[id] = p
    taken.push(p)
  })
}

/** 新節點的位置：入站放中心左邊一欄、出站放右邊一欄（使用者 11-08 定：左入右出、整齊直排）。
 *  一欄放不下就分組，組只佔一格。已在圖上的節點位置不動。回傳這次建立的組。 */
export function placeColumns(s: GraphState, centerId: string, ids: string[], edges: RelEdge[]): Group[] {
  const c = s.pos[centerId] ?? { x: 0, y: 0 }
  const fresh = ids.filter(id => !s.pos[id])
  const isLeft = (id: string) => edges.some(x => x.source_id === id && x.target_id === centerId) && !edges.some(x => x.source_id === centerId && x.target_id === id)
  const left = fresh.filter(isLeft)
  const right = fresh.filter(id => !isLeft(id))
  const made: Group[] = []
  for (const [side, list, dx] of [['left', left, -COL_X], ['right', right, COL_X]] as const) {
    const { slots, groups } = groupSide(s, centerId, side, list, edges)
    stack(s, c.x + dx, c.y, slots)
    for (const g of groups) {
      s.groups[g.id] = g
      for (const m of g.members) { s.groupOf[m] = g.id; s.pos[m] = { ...s.pos[g.id] } }
      made.push(g)
    }
  }
  return made
}

/** 把一個成員從組裡拉出來，變成單獨的節點（放在組的旁邊）。 */
export function pullOut(s: GraphState, memberId: string) {
  const gid = s.groupOf[memberId]
  if (!gid) return
  delete s.groupOf[memberId]
  const g = s.groups[gid]
  g.members = g.members.filter(m => m !== memberId)
  const gp = s.pos[gid]
  delete s.pos[memberId]
  stack(s, gp.x + (g.side === 'right' ? 220 : -220), gp.y, [memberId])
  if (g.members.length === 0) { delete s.groups[gid]; delete s.pos[gid] }
  else {
    g.title = g.kind === 'port' ? g.title.replace(/\d+ 個端點/, `${g.members.length} 個端點`) : g.title
    g.sub = g.kind === 'system' ? `${g.members.length} 台・點開看清單` : g.sub
  }
}

/** 畫面要畫的東西：組的成員收起來（組只畫一格、連線合成一條）；「只看已納管」時未納管不畫、不算。 */
export interface ViewNode { id: string; group?: Group; node?: RelNode }
export interface ViewEdge { id: string; source: string; target: string; edge?: RelEdge; count?: number; uncertain?: boolean }
export function view(s: GraphState, onlyManaged: boolean): { nodes: ViewNode[]; edges: ViewEdge[] } {
  const keep = (id: string) => {
    const n = s.nodes[id]
    if (id === s.rootId) return true
    return !onlyManaged || n?.management.status === 'managed'
  }
  const nodes: ViewNode[] = []
  for (const id of Object.keys(s.nodes)) if (!s.groupOf[id] && keep(id)) nodes.push({ id, node: s.nodes[id] })
  for (const g of Object.values(s.groups)) {
    if (onlyManaged && g.kind === 'port') continue
    const members = g.members.filter(keep)
    if (members.length) nodes.push({ id: g.id, group: { ...g, members } })
  }
  const shown = new Set(nodes.map(n => n.id))
  const edges: ViewEdge[] = []
  const agg = new Map<string, ViewEdge>()
  for (const x of Object.values(s.edges)) {
    const a = s.groupOf[x.source_id] ?? x.source_id
    const b = s.groupOf[x.target_id] ?? x.target_id
    if (!shown.has(a) || !shown.has(b) || !keep(x.source_id) || !keep(x.target_id)) continue
    if (a === x.source_id && b === x.target_id) { edges.push({ id: x.edge_id, source: a, target: b, edge: x, uncertain: x.direction_status === 'uncertain' }); continue }
    const k = `agg:${a}>${b}`
    const e = agg.get(k) ?? { id: k, source: a, target: b, count: 0 }
    e.count = (e.count ?? 0) + 1
    agg.set(k, e)
  }
  return { nodes, edges: [...edges, ...agg.values()] }
}

/** 重新排版（只有使用者按才做）：照探索順序重排全部位置。回傳舊位置，給「復原排版」用。 */
export function relayout(s: GraphState): Record<string, { x: number; y: number }> {
  const old = JSON.parse(JSON.stringify(s.pos))
  const keepGroups = { groups: s.groups, groupOf: s.groupOf }
  s.pos = { [s.rootId]: { x: 0, y: 0 } }
  const all = Object.values(s.edges)
  for (const a of s.history) {
    const ids = a.addedNodes.filter(id => !keepGroups.groupOf[id] && !s.pos[id])
    const isLeft = (id: string) => all.some(x => x.source_id === id && x.target_id === a.nodeId)
    const c = s.pos[a.nodeId] ?? { x: 0, y: 0 }
    const gids = (a.groupIds ?? []).filter(g => keepGroups.groups[g])
    const left = [...ids.filter(isLeft), ...gids.filter(g => keepGroups.groups[g].side === 'left')]
    const right = [...ids.filter(id => !isLeft(id)), ...gids.filter(g => keepGroups.groups[g].side === 'right')]
    stack(s, c.x - COL_X, c.y, left)
    stack(s, c.x + COL_X, c.y, right)
  }
  for (const [m, g] of Object.entries(keepGroups.groupOf)) if (s.pos[g]) s.pos[m] = { ...s.pos[g] }
  for (const id of Object.keys(s.nodes)) if (!s.pos[id]) s.pos[id] = old[id] ?? { x: 0, y: 0 }
  return old
}

/** 回應合併（原子）：三道核對都過才動狀態。回傳 'stale'｜'budget'｜'ok'。 */
export function applyPage(s: GraphState, key: string, requestId: number, epoch: number, res: any, nodeId: string, dir: Direction, more = false): 'stale' | 'budget' | 'ok' {
  const e = s.exp[key]
  if (epoch !== s.epoch || !e || e.requestId !== requestId || (s.snapshotId && res.snapshot_id !== s.snapshotId)) {
    s.dropped++
    settle(s, key, requestId)
    return 'stale'
  }
  const newNodes = (res.nodes as RelNode[]).filter(n => !s.nodes[n.node_id])
  const newEdges = (res.edges as RelEdge[]).filter(x => !s.edges[x.edge_id])
  if (Object.keys(s.nodes).length + newNodes.length > BUDGET.nodes || Object.keys(s.edges).length + newEdges.length > BUDGET.edges) {
    s.exp[key] = { ...e, status: 'error', error: 'budget' }
    return 'budget'
  }
  if (!s.snapshotId) {                 // 第一次拿到版本：把這個展開的 key 換成帶版本的
    s.snapshotId = res.snapshot_id
    delete s.exp[key]
    key = expKey(nodeId, dir, s.snapshotId)
  }
  for (const n of res.nodes as RelNode[]) s.nodes[n.node_id] = n
  for (const x of newEdges) s.edges[x.edge_id] = x
  const made = placeColumns(s, nodeId, newNodes.map(n => n.node_id).filter(id => id !== nodeId), res.edges as RelEdge[])
  // 每一頁都是自己的動作：復原「載入更多」只拿掉那一頁，不會連第一頁一起拿掉
  s.history.push({ id: s.nextId++, nodeId, direction: dir, addedNodes: newNodes.map(n => n.node_id).filter(id => id !== s.rootId), addedEdges: newEdges.map(x => x.edge_id), groupIds: made.map(g => g.id) })
  const status = res.edges.length === 0 && !more ? (res.completeness?.status === 'partial' ? 'partial' : 'empty') : (res.completeness?.status === 'partial' ? 'partial' : 'success')
  s.exp[key] = { status, cursor: res.next_cursor, hasMore: !!res.has_more, loadedPages: e.loadedPages + 1, requestId, total: res.total_edges ?? 0 }
  return 'ok'
}

/** 被取消或過時的請求：同一個請求還掛著「載入中」就清掉，按鈕才不會一直灰著。
 *  之前成功過的頁數、游標保留（回到請求前的狀態）；從沒成功過的整筆拿掉。 */
export function settle(s: GraphState, key: string, requestId: number) {
  const e = s.exp[key]
  if (!e || e.requestId !== requestId || e.status !== 'loading') return
  if (e.loadedPages > 0) s.exp[key] = { ...e, status: 'success' }
  else delete s.exp[key]
}

export function failExpand(s: GraphState, key: string, requestId: number, epoch: number, msg: string) {
  const e = s.exp[key]
  if (epoch !== s.epoch || !e || e.requestId !== requestId) { s.dropped++; settle(s, key, requestId); return }
  s.exp[key] = { ...e, status: 'error', error: msg }
}

/** 復原上一個動作：只拿掉那次獨有的邊，與因此沒有任何邊相連的新節點（起點永遠保留）。 */
export function undo(s: GraphState): boolean {
  if (s.history.length <= 1) return false
  const act = s.history.pop()!
  s.epoch++
  for (const id of act.addedEdges) delete s.edges[id]
  const used = new Set<string>([s.rootId])
  for (const x of Object.values(s.edges)) { used.add(x.source_id); used.add(x.target_id) }
  for (const id of act.addedNodes) if (!used.has(id)) { delete s.nodes[id]; delete s.pos[id] }
  for (const g of act.groupIds ?? []) { for (const m of s.groups[g]?.members ?? []) delete s.groupOf[m]; delete s.groups[g]; delete s.pos[g] }
  for (const k of Object.keys(s.exp)) if (k.startsWith(`${act.nodeId}|${act.direction}|`)) delete s.exp[k]
  if (s.selectedId && !s.nodes[s.selectedId]) s.selectedId = s.rootId
  return true
}

/** 重設：回到第一次成功的畫面（同一個資料版本）。 */
export function resetToFirst(s: GraphState) {
  while (s.history.length > 1) undo(s)
  s.epoch++
  s.selectedId = s.rootId
}

/** 重新讀取：清空全部、換新版本（之後由畫面重做第一次展開）。 */
export function reload(s: GraphState) {
  const root = s.rootId
  Object.assign(s, newState(root), { epoch: s.epoch + 1, nextId: s.nextId, dropped: s.dropped })
}

/** 邊的標籤：「TCP 443・HTTPS」；沒目的埠就是「TCP・方向未知」，沒服務名稱就是「未知服務」。 */
export function edgeLabel(x: RelEdge): string {
  const proto = (x.protocol || 'tcp').toUpperCase()
  if (x.destination_port == null) return `${proto}・目的埠未知`
  return `${proto} ${x.destination_port}・${x.service?.name || '未知服務'}`
}

export function nodeLabel(n: RelNode | undefined, id: string): string {
  if (!n) return id
  if (n.entity_type === 'host') return n.display_name || n.addresses[0] || '未命名主機'
  return n.addresses[0] || id
}

/** 時間：確定是台北時間的顯示「(UTC+08:00)」；時區沒查證的照原文加「時區未確認」。 */
export function tsText(t: any): string {
  if (!t) return '—'
  return t.time_quality === 'unknown' ? `${t.raw}（時區未確認）` : `${t.raw}（UTC+08:00）`
}
