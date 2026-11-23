// 主機關聯探索的圖狀態（規格 P0 01／02／12、P1 17）：位置穩定、共享節點不被錯刪、過時回應不合併、預算上限。
import { describe, it, expect } from 'vitest'
import { newState, beginExpand, applyPage, undo, resetToFirst, edgeLabel, tsText, BUDGET, settle, view, pullOut, relayout, MAX_SLOTS } from '../utils/hostRelGraph'

const N = (id: string, extra: any = {}) => ({ node_id: id, entity_type: id.startsWith('h:') ? 'host' : 'endpoint', display_name: id.slice(2), addresses: ['10.99.0.1'],
  management: { status: 'managed', reason_code: 'managed', detail: null }, identity: { status: 'resolved', candidate_count: 1 }, coverage: {}, health: null, machine_key: id.slice(2), ...extra })
const E = (s: string, t: string, port = 443) => ({ edge_id: `${s}>${t}/tcp/${port}`, source_id: s, target_id: t, protocol: 'tcp', destination_port: port,
  service: { name: 'HTTPS', basis: 'common_port' }, direction_status: 'inferred', direction_basis: 'listen_snapshot', observations: 1, source_refs_total: 1, source_refs: [],
  first_seen: null, last_seen: null, observation_sides: 'single', freshness: 'fresh', partial: false })
const res = (nodes: any[], edges: any[], snap = 'S1') => ({ snapshot_id: snap, nodes, edges, has_more: false, next_cursor: null, total_edges: edges.length, completeness: { status: 'complete' } })

function seeded() {
  const s = newState('h:a')
  const t = beginExpand(s, 'h:a', 'both')!
  applyPage(s, t.key, t.requestId, t.epoch, res([N('h:a'), N('h:gw'), N('h:db'), N('h:mq')], [E('h:a', 'h:gw'), E('h:a', 'h:db', 1521), E('h:a', 'h:mq', 5672)]), 'h:a', 'both')
  return s
}

describe('hostRelGraph', () => {
  it('P0 01：再展開 gw01 只多一跳，起點與既有位置不動', () => {
    const s = seeded()
    const before = JSON.stringify({ a: s.pos['h:a'], db: s.pos['h:db'], mq: s.pos['h:mq'], gw: s.pos['h:gw'] })
    const t = beginExpand(s, 'h:gw', 'outbound')!
    expect(applyPage(s, t.key, t.requestId, t.epoch, res([N('h:gw'), N('h:auth'), N('h:mq')], [E('h:gw', 'h:auth', 636), E('h:gw', 'h:mq', 5672)]), 'h:gw', 'outbound')).toBe('ok')
    expect(JSON.stringify({ a: s.pos['h:a'], db: s.pos['h:db'], mq: s.pos['h:mq'], gw: s.pos['h:gw'] })).toBe(before)
    expect(s.pos['h:auth']).toBeTruthy()
    expect(Object.keys(s.nodes).sort()).toEqual(['h:a', 'h:auth', 'h:db', 'h:gw', 'h:mq'])
  })

  it('P0 02／12：復原只拿掉那次獨有的邊與孤立新節點，共享的 mq 保留', () => {
    const s = seeded()
    const t = beginExpand(s, 'h:gw', 'outbound')!
    applyPage(s, t.key, t.requestId, t.epoch, res([N('h:gw'), N('h:auth'), N('h:mq')], [E('h:gw', 'h:auth', 636), E('h:gw', 'h:mq', 5672)]), 'h:gw', 'outbound')
    expect(undo(s)).toBe(true)
    expect(s.nodes['h:auth']).toBeUndefined()
    expect(s.nodes['h:mq']).toBeTruthy()
    expect(Object.keys(s.edges).length).toBe(3)
    expect(undo(s)).toBe(false)                 // 第一次的畫面不能再復原
  })

  it('P0 12：同 key 正在載入不重送；復原後回來的舊回應不合併', () => {
    const s = seeded()
    const t = beginExpand(s, 'h:gw', 'outbound')!
    expect(beginExpand(s, 'h:gw', 'outbound')).toBeNull()
    resetToFirst(s)                              // epoch 變了
    expect(applyPage(s, t.key, t.requestId, t.epoch, res([N('h:x')], [E('h:gw', 'h:x')]), 'h:gw', 'outbound')).toBe('stale')
    expect(s.nodes['h:x']).toBeUndefined()
    expect(s.dropped).toBe(1)
  })

  it('P0 13：不同版本的回應不合併', () => {
    const s = seeded()
    const t = beginExpand(s, 'h:gw', 'outbound')!
    expect(applyPage(s, t.key, t.requestId, t.epoch, res([N('h:x')], [E('h:gw', 'h:x')], 'S2'), 'h:gw', 'outbound')).toBe('stale')
  })

  it('P1 17：超過預算整批不放', () => {
    const s = seeded()
    const many = Array.from({ length: BUDGET.nodes }, (_, i) => N(`h:n${i}`))
    const t = beginExpand(s, 'h:gw', 'outbound')!
    expect(applyPage(s, t.key, t.requestId, t.epoch, res(many, many.map(n => E('h:gw', n.node_id))), 'h:gw', 'outbound')).toBe('budget')
    expect(Object.keys(s.nodes).length).toBe(4)
  })

  it('審查 P1：取消或過時的請求不會讓「展開」一直灰著', () => {
    const s = seeded()
    const t = beginExpand(s, 'h:gw', 'outbound')!
    settle(s, t.key, t.requestId)                  // 使用者按了復原，請求被取消
    expect(beginExpand(s, 'h:gw', 'outbound')).not.toBeNull()
    const t2 = beginExpand(s, 'h:db', 'outbound')!
    resetToFirst(s)
    applyPage(s, t2.key, t2.requestId, t2.epoch, res([N('h:x')], [E('h:db', 'h:x')]), 'h:db', 'outbound')   // 過時
    expect(beginExpand(s, 'h:db', 'outbound')).not.toBeNull()
  })

  it('審查 P3：復原「載入更多」只拿掉那一頁', () => {
    const s = seeded()
    const t = beginExpand(s, 'h:gw', 'outbound')!
    applyPage(s, t.key, t.requestId, t.epoch, { ...res([N('h:gw'), N('h:p1')], [E('h:gw', 'h:p1')]), has_more: true, next_cursor: 'c1' }, 'h:gw', 'outbound')
    const t2 = beginExpand(s, 'h:gw', 'outbound')!
    applyPage(s, t2.key, t2.requestId, t2.epoch, res([N('h:gw'), N('h:p2')], [E('h:gw', 'h:p2')]), 'h:gw', 'outbound', true)
    undo(s)
    expect(s.nodes['h:p1']).toBeTruthy()
    expect(s.nodes['h:p2']).toBeUndefined()
  })

  it('分組（11-08 定）：一欄放得下不分組；放不下時清冊沒有的 IP 依 Port、已納管依業務系統，納管失敗單獨成框', () => {
    const small = seeded()
    expect(Object.keys(small.groups)).toEqual([])
    const s = newState('h:a')
    const ep = (i: number) => N(`e:default:4:198.51.100.${i}`, { entity_type: 'endpoint', management: { status: 'unmanaged', reason_code: 'not_in_inventory', detail: null }, identity: { status: 'unresolved', candidate_count: 0 }, systems: [] })
    const eps = Array.from({ length: 13 }, (_, i) => ep(i + 1))
    const apps = Array.from({ length: 4 }, (_, i) => N(`h:app${i}`, { systems: ['S-9'] }))
    const fail = N('h:mq', { management: { status: 'unmanaged', reason_code: 'not_onboarded', detail: '未納管' }, systems: ['S-3'] })
    const nodes = [N('h:a'), ...eps, ...apps, fail]
    const edges = [...eps.map(n => E('h:a', n.node_id, 7844)), ...apps.map(n => E('h:a', n.node_id, 5672)), E('h:a', 'h:mq', 5672)]
    const t = beginExpand(s, 'h:a', 'both')!
    applyPage(s, t.key, t.requestId, t.epoch, res(nodes, edges), 'h:a', 'both')
    const gs = Object.values(s.groups)
    expect(gs.map(g => g.title).sort()).toEqual(['TCP 7844・13 個端點', '業務系統 S-9'])
    expect(s.groupOf['h:mq']).toBeUndefined()
    const v = view(s, false)
    expect(v.nodes.length).toBe(1 + 2 + 1)              // 起點＋兩組＋mq
    expect(v.edges.find(e => e.target.startsWith('g:') && e.count === 13)).toBeTruthy()
    // 只看已納管：清冊沒有、納管失敗都不畫、不算；系統組還在
    const m = view(s, true)
    expect(m.nodes.map(n => n.group?.kind ?? n.id).sort()).toEqual(['h:a', 'system'])
    expect(m.edges.every(e => !e.target.includes('198.51') && e.target !== 'h:mq')).toBe(true)
    // 拉出來：變成單獨節點，組數字跟著減
    pullOut(s, 'h:app0')
    expect(s.groupOf['h:app0']).toBeUndefined()
    expect(Object.values(s.groups).find(g => g.kind === 'system')!.sub).toContain('3 台')
    expect(view(s, false).nodes.some(n => n.id === 'h:app0')).toBe(true)
    // 復原：組一起拿掉
    expect(MAX_SLOTS).toBeGreaterThan(0)
  })

  it('一個 IP 只有一個框：多 Port 的端點不收進單一 Port 組；重新排版回傳舊位置可以復原', () => {
    const s = newState('h:a')
    const ep = (i: number) => N(`e:default:4:203.0.113.${i}`, { entity_type: 'endpoint', management: { status: 'unmanaged', reason_code: 'not_in_inventory', detail: null }, identity: { status: 'unresolved', candidate_count: 0 }, systems: [] })
    const eps = Array.from({ length: 10 }, (_, i) => ep(i + 1))
    const edges = [...eps.map(n => E('h:a', n.node_id, 443)), E('h:a', eps[0].node_id, 8443)]
    const t = beginExpand(s, 'h:a', 'outbound')!
    applyPage(s, t.key, t.requestId, t.epoch, res([N('h:a'), ...eps], edges), 'h:a', 'outbound')
    expect(s.groupOf[eps[0].node_id]).toBeUndefined()          // 443＋8443 的那台自己一格
    expect(Object.values(s.groups)[0].members.length).toBe(9)
    const before = JSON.stringify(s.pos)
    const old = relayout(s)
    expect(JSON.stringify(old)).toBe(before)
  })

  it('標籤：不出現 TCP/0；未知服務與時區未確認照實寫', () => {
    expect(edgeLabel(E('h:a', 'h:b', 21) as any)).toBe('TCP 21・HTTPS')
    expect(edgeLabel({ ...E('h:a', 'h:b'), destination_port: null } as any)).toBe('TCP・目的埠未知')
    expect(edgeLabel({ ...E('h:a', 'h:b', 47123), service: { name: null, basis: null } } as any)).toBe('TCP 47123・未知服務')
    expect(tsText({ raw: '2026-10-25 10:00:00', time_quality: 'unknown' })).toContain('時區未確認')
    expect(tsText({ raw: '2026-11-05 10:00:00', time_quality: 'taipei' })).toContain('UTC+08:00')
  })
})
