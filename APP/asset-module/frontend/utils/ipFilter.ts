// 6.1 待納管的「IP／網段」篩選（2.007.48，使用者：「我想找 10.99.3 網段」）。
// 以前的搜尋是「包含」比對：打 10.99.3 會連 10.99.30～39 一起抓進來。這裡改成「依八位元邊界」比：
//   10.99.3          → 10.99.3.0 ～ 10.99.3.255（整段開頭）
//   10.99.3.         → 同上
//   10.99.3.0/24     → CIDR
//   10.99.3.10-50    → 同網段的範圍；10.99.3.10-10.99.4.5 也行
//   10.99.3.18       → 完整 IP（只這一台）
// 可以用空白、逗號、換行混著寫多段（任一段符合就算）。不像 IP 的字（SECSVR19、web）當作名稱，不分大小寫「包含」。
// 回傳的 match 只看 IP；name 的比對交給呼叫端。

const OCT = /^\d{1,3}$/

function ipNum(s: string): number | null {
  const p = s.split('.')
  if (p.length !== 4 || !p.every(x => OCT.test(x) && Number(x) <= 255)) return null
  return ((Number(p[0]) * 256 + Number(p[1])) * 256 + Number(p[2])) * 256 + Number(p[3])
}

/** 一段輸入 → IP 範圍 [起, 迄]；不是 IP 寫法回 null。 */
export function parseIpTerm(raw: string): [number, number] | null {
  const t = raw.trim()
  if (!t) return null
  const cidr = t.match(/^(\d{1,3}(?:\.\d{1,3}){3})\/(\d{1,2})$/)
  if (cidr) {
    const base = ipNum(cidr[1]); const bits = Number(cidr[2])
    if (base === null || bits > 32) return null
    const size = 2 ** (32 - bits)
    const start = Math.floor(base / size) * size
    return [start, start + size - 1]
  }
  const range = t.match(/^(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3}(?:\.\d{1,3}){0,3})$/)
  if (range) {
    const a = ipNum(range[1])
    if (a === null) return null
    const tail = range[2].split('.')
    const head = range[1].split('.')
    const b = ipNum([...head.slice(0, 4 - tail.length), ...tail].join('.'))   // 10.99.3.10-50 → 10.99.3.50
    if (b === null || b < a) return null
    return [a, b]
  }
  const pre = t.replace(/\.$/, '').split('.')
  if (pre.length >= 1 && pre.length <= 4 && pre.every(x => OCT.test(x) && Number(x) <= 255)) {
    const fill = (v: number) => [...pre.map(Number), ...Array(4 - pre.length).fill(v)]
    const lo = fill(0); const hi = fill(255)
    return [((lo[0] * 256 + lo[1]) * 256 + lo[2]) * 256 + lo[3], ((hi[0] * 256 + hi[1]) * 256 + hi[2]) * 256 + hi[3]]
  }
  return null
}

export interface IpFilter {
  /** 有沒有輸入任何東西 */
  active: boolean
  /** 這個 IP 在不在輸入的任一段裡 */
  ip: (ip: string | null | undefined) => boolean
  /** 輸入裡不像 IP 的那些字（要比名稱用） */
  words: string[]
  /** 輸入裡有幾段是 IP 寫法 */
  ranges: number
}

export function ipFilter(text: string): IpFilter {
  const terms = (text || '').split(/[\s,，、;；]+/).map(s => s.trim()).filter(Boolean)
  const ranges: [number, number][] = []
  const words: string[] = []
  for (const t of terms) {
    const r = parseIpTerm(t)
    if (r) ranges.push(r)
    else words.push(t.toLowerCase())
  }
  return {
    active: terms.length > 0,
    ranges: ranges.length,
    words,
    ip: (ip) => {
      const n = ip ? ipNum(ip) : null
      return n !== null && ranges.some(([a, b]) => n >= a && n <= b)
    },
  }
}

/** 一列符不符合：有 IP 段就依 IP 段，有文字就找名稱；兩種都寫＝任何一種符合（例：10.99.3 secsvr）。 */
export function rowMatches(f: IpFilter, ip: string | null | undefined, nameText: string): boolean {
  if (!f.active) return true
  if (f.ranges && f.ip(ip)) return true
  const t = (nameText || '').toLowerCase()
  return f.words.some(w => t.includes(w))
}
