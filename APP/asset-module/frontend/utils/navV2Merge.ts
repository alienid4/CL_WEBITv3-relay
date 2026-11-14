// 2.x 選單：程式內建預設（V2_NAV，唯一的正本）＋ 管理員存的覆寫（/api/v2/nav-layout）→ 畫面上的選單。
// 覆寫只記「群組順序」與「每組放哪些項目（用網址當 key）」，名稱、編號一律取程式預設：
//   * 覆寫裡有、程式已拿掉的群組／項目 → 丟掉（dropped 回報給編輯畫面）
//   * 程式新增、覆寫裡沒有的項目 → 自動放回它在預設裡的群組最後（added）
//   * 覆寫裡沒有的群組 → 照預設順序接在最後
//   * 合併後空掉的群組不顯示
// 項目編號（例 2.1）不跟著搬家重編：手冊與各頁寫的「2.1 主機查詢」要對得上。
export interface NavItemLike { to: string; [k: string]: any }
export interface NavGroupLike<I extends NavItemLike = NavItemLike> { no: string; items: I[]; [k: string]: any }
export interface NavOverrideGroup { no: string; items: string[] }
export interface NavMergeResult<G> { groups: G[]; dropped: string[]; added: string[] }

export function mergeNavV2<I extends NavItemLike, G extends NavGroupLike<I>>(
  defaults: G[], override: NavOverrideGroup[] | null | undefined,
): NavMergeResult<G> {
  if (!Array.isArray(override) || !override.length) return { groups: defaults.map(g => ({ ...g, items: [...g.items] })), dropped: [], added: [] }
  const groupByNo = new Map(defaults.map(g => [g.no, g]))
  const itemByTo = new Map<string, I>()
  const homeOf = new Map<string, string>()
  for (const g of defaults) for (const it of g.items) { itemByTo.set(it.to, it); homeOf.set(it.to, g.no) }

  const placed = new Set<string>()
  const dropped: string[] = []
  const out: G[] = []
  const seenGroups = new Set<string>()
  for (const og of override) {
    if (!og || typeof og.no !== 'string') continue
    const base = groupByNo.get(og.no)
    if (!base || seenGroups.has(og.no)) { dropped.push(`群組 ${og?.no}`); for (const t of og?.items ?? []) if (!itemByTo.has(t)) dropped.push(t); continue }
    seenGroups.add(og.no)
    const items: I[] = []
    for (const t of Array.isArray(og.items) ? og.items : []) {
      const it = itemByTo.get(t)
      if (!it) { dropped.push(t); continue }
      if (placed.has(t)) continue
      placed.add(t); items.push(it)
    }
    out.push({ ...base, items })
  }
  for (const g of defaults) if (!seenGroups.has(g.no)) out.push({ ...g, items: [] })
  // 被丟掉群組裡、但程式還在的項目，跟程式新增的項目一樣：放回預設群組
  const added: string[] = []
  for (const g of defaults) {
    for (const it of g.items) {
      if (placed.has(it.to)) continue
      const home = out.find(x => x.no === homeOf.get(it.to))!
      home.items.push(it); placed.add(it.to)
      if (seenGroups.has(g.no)) added.push(it.to)       // 覆寫裡有這組卻沒有這項＝程式新加的
    }
  }
  return { groups: out.filter(g => g.items.length), dropped, added }
}

/** 兩份覆寫（目前 vs 要匯入／還原的）各自跟程式預設合併後，比出「哪些項目會換組或換位置、群組順序變了沒」。
 *  匯入、還原前先給管理員看這份差異（驗收 G2）。只回有變的項目；位置是組內第幾個（1 起算）。 */
export interface NavDiffRow { no: string; label: string; to: string; from: string; to_group: string; from_pos: number | null; to_pos: number | null; change: string }
export interface NavDiff { rows: NavDiffRow[]; groupOrderBefore: string[]; groupOrderAfter: string[]; groupOrderChanged: boolean; dropped: string[] }
export function diffNavV2<I extends NavItemLike, G extends NavGroupLike<I>>(
  defaults: G[], before: NavOverrideGroup[] | null | undefined, after: NavOverrideGroup[] | null | undefined,
): NavDiff {
  const a = mergeNavV2(defaults, before)
  const b = mergeNavV2(defaults, after)
  const where = (gs: G[]) => {
    const m = new Map<string, { g: G; pos: number }>()
    gs.forEach(g => g.items.forEach((it, i) => m.set(it.to, { g, pos: i + 1 })))
    return m
  }
  const wa = where(a.groups); const wb = where(b.groups)
  const gname = (g?: G) => (g ? `${g.no} ${g.label ?? ''}`.trim() : '—')
  const rows: NavDiffRow[] = []
  for (const g of b.groups) {
    // 組內順序只比「前後都在這組」的項目彼此的先後，別組搬進搬出造成的位置位移不算（不然搬一項會冒出一整串）
    const stayB = g.items.filter(it => wa.get(it.to)?.g.no === g.no).map(it => it.to)
    const stayA = [...stayB].sort((p, q) => wa.get(p)!.pos - wa.get(q)!.pos)
    for (const it of g.items) {
      const x = wa.get(it.to); const y = wb.get(it.to)!
      const moved = !x || x.g.no !== y.g.no
      if (!moved && stayA.indexOf(it.to) === stayB.indexOf(it.to)) continue
      rows.push({ no: String((it as any).no ?? ''), label: String((it as any).label ?? it.to), to: it.to,
        from: gname(x?.g), to_group: gname(y.g), from_pos: x?.pos ?? null, to_pos: y.pos,
        change: moved ? '換組' : '組內換位置' })
    }
  }
  const groupOrderBefore = a.groups.map(g => gname(g))
  const groupOrderAfter = b.groups.map(g => gname(g))
  return { rows, groupOrderBefore, groupOrderAfter,
    groupOrderChanged: groupOrderBefore.join('|') !== groupOrderAfter.join('|'), dropped: b.dropped }
}

/** 畫面上的選單 → 要存的覆寫（只留群組編號與項目網址）。 */
export function toNavOverride(groups: NavGroupLike[]): NavOverrideGroup[] {
  return groups.map(g => ({ no: g.no, items: g.items.map(it => it.to) }))
}
