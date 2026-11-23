// 開發協作頁的「去識別」純函式（只在瀏覽器本機執行，不送到伺服器）。
// 把 IPv4、MAC、email 換成穩定代號：同一個值永遠換成同一個代號，所以開發者仍看得出「這兩列是同一台」。
// 刻意不做（頁面會要求使用者手動檢查）：主機名、網域、IQN、WWPN／WWN、設備序號、LUN 名稱、共享／掛載路徑、帳號、IPv6。
// IPv6 只偵測並計數（result.ipv6），不替換。

export interface SanitizeMaps {
  ip: Record<string, string>
  mac: Record<string, string>
  email: Record<string, string>
}
/** 需要人工確認的位址：像 IP 又可能是版本號。kept＝已原樣保留；replaced＝已替換但形似版本號 */
export interface ReviewItem { text: string; action: 'kept' | 'replaced'; reason: string }
export interface SanitizeResult {
  text: string
  maps: SanitizeMaps
  /** 這一次輸入裡不同的值各算一個：{ ip, mac, email }（累積的對照表列數看 maps） */
  distinct: { ip: number; mac: number; email: number }
  /** 文字中被換掉的處數（同一位址出現三次算三處） */
  hits: { ip: number; mac: number; email: number }
  /** 偵測到但未替換的 IPv6（不同的值） */
  ipv6: string[]
  /** 請人工確認清單 */
  review: ReviewItem[]
}

export function emptyMaps(): SanitizeMaps { return { ip: {}, mac: {}, email: {} } }

// 每段先抓 1～3 位數字，再在 callback 裡驗 ≤255、去前導零（前導零的寫法也涵蓋，正規化後再對照）；
// 前後不能緊貼數字或「.數字」：避開 5 段以上的版本號 / OID（如 5.5.0.0.6000236、1.3.6.1.4.1）
const IPV4_RE = /(?:(?<![0-9A-Fa-f:])::ffff:)?(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?!\d|\.\d)/gi
// IPv4-mapped IPv6：::ffff:a.b.c.d，整段處理（保留 ::ffff: 前綴，位址換代號）
const MAPPED_RE = /(?<![0-9A-Fa-f:])::ffff:(\d{1,3}(?:\.\d{1,3}){3})(?!\d|\.\d)/gi
// 6 組兩位十六進位（: 或 - 分隔）；前後不能再接十六進位組，所以 8 組的 WWPN 不會被當成 MAC，12:30:45 這種時間不夠組數
const MAC_COLON_RE = /(?<![0-9A-Fa-f:-])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![:-]?[0-9A-Fa-f])/g
// Cisco 寫法 aabb.ccdd.eeff
const MAC_DOT_RE = /(?<![0-9A-Fa-f.])[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}(?![.]?[0-9A-Fa-f])/g
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/g
// IPv6：含 :: 的寫法，或 8 組完整寫法（至少一組超過 2 位，免得把 8 組 2 位的 WWPN 算進去）
const IPV6_COMPRESSED_RE = /(?<![0-9A-Fa-f:])(?:(?:[0-9A-Fa-f]{1,4}:){1,7}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?|::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6}))(?![0-9A-Fa-f:])/g
const IPV6_FULL_RE = /(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])/g
// 只有前面有這些字才把四段數字當版本號保留（字邊界很重要：server 192.0.2.1 不能因為結尾是 "ver" 就漏換）
const VERSION_BEFORE_RE = /(?:\b(?:version|ver|build|release|firmware|fw|v)|版本)[\s:=_-]*$/i

const IP_POOLS = ['192.0.2', '198.51.100', '203.0.113']        // 文件專用位址（RFC 5737）

function ipCode(i: number): string {
  const pool = Math.floor(i / 254)
  return pool < IP_POOLS.length ? `${IP_POOLS[pool]}.${(i % 254) + 1}` : `IP-${i + 1}`
}
function macCode(i: number): string {
  const b = (n: number) => (n % 256).toString(16).toUpperCase().padStart(2, '0')
  return `02:00:00:${b(Math.floor(i / 65536))}:${b(Math.floor(i / 256))}:${b(i)}`   // 02 開頭＝本機自訂位址，不會跟真實設備撞
}
function emailCode(i: number): string { return `user${i + 1}@example.test` }

/** 255.255.255.0 這種子網路遮罩不是識別資訊，原樣保留（開發者要看遮罩長度）。 */
export function isNetmask(ip: string): boolean {
  const o = ip.split('.').map(Number)
  if (o.length !== 4) return false
  const n = ((o[0] << 24) | (o[1] << 16) | (o[2] << 8) | o[3]) >>> 0
  if (n === 0 || n === 0xffffffff) return false
  const inv = (~n) >>> 0
  return ((inv + 1) & inv) === 0 && o[0] === 255           // 連續的 1 後面全是 0
}

/** 形似版本號：結尾 .0.0，或四段都是個位數（1.2.3.4）。只用來提醒人工確認，不影響要不要替換。 */
function versionShaped(norm: string): boolean {
  return /\.0\.0$/.test(norm) || norm.split('.').every(o => Number(o) <= 9)
}

function normMac(raw: string): string {
  return raw.replace(/[^0-9A-Fa-f]/g, '').toLowerCase()
}

/** 正規化四段位址；有任何一段超過 255 回 null。 */
function normIp(m: string): string | null {
  const parts = m.split('.').map(s => parseInt(s, 10))
  return parts.every(n => n >= 0 && n <= 255) ? parts.join('.') : null
}

/**
 * 去識別。prev 可帶上一次的對照表，讓分批貼上的輸出仍使用同一組代號。
 * 不改動 prev（回傳新的 maps）。
 */
export function sanitize(input: string, prev?: SanitizeMaps): SanitizeResult {
  const maps: SanitizeMaps = {
    ip: { ...(prev?.ip ?? {}) }, mac: { ...(prev?.mac ?? {}) }, email: { ...(prev?.email ?? {}) },
  }
  const hits = { ip: 0, mac: 0, email: 0 }
  const seen = { ip: new Set<string>(), mac: new Set<string>(), email: new Set<string>() }   // 這一次輸入裡出現過的不同值
  const review: ReviewItem[] = []
  const reviewKeys = new Set<string>()
  const addReview = (text: string, action: ReviewItem['action'], reason: string) => {
    const k = `${action}|${text}`
    if (reviewKeys.has(k)) return
    reviewKeys.add(k)
    review.push({ text, action, reason })
  }

  // IPv6：先在原文偵測（把 ::ffff:a.b.c.d 這種整段處理的先拿掉，免得被算成 IPv6）
  const v6 = new Set<string>()
  const forV6 = input.replace(MAPPED_RE, ' ')
  for (const m of forV6.match(IPV6_COMPRESSED_RE) ?? []) if (/\d/.test(m) && m.length >= 3) v6.add(m)
  for (const m of forV6.match(IPV6_FULL_RE) ?? []) if (m.split(':').some(g => g.length > 2)) v6.add(m)

  let text = input

  // email 先換（避免 user@192.0.2.5 這種被 IP 規則拆開）
  text = text.replace(EMAIL_RE, (m) => {
    const k = m.toLowerCase()
    if (!(k in maps.email)) maps.email[k] = emailCode(Object.keys(maps.email).length)
    hits.email++
    seen.email.add(k)
    return maps.email[k]
  })

  const macRepl = (m: string) => {
    const k = normMac(m)
    if (!(k in maps.mac)) maps.mac[k] = macCode(Object.keys(maps.mac).length)
    hits.mac++
    seen.mac.add(k)
    return maps.mac[k]
  }
  text = text.replace(MAC_COLON_RE, macRepl).replace(MAC_DOT_RE, macRepl)

  const codeFor = (norm: string) => {
    if (!(norm in maps.ip)) maps.ip[norm] = ipCode(Object.keys(maps.ip).length)
    hits.ip++
    seen.ip.add(norm)
    if (versionShaped(norm)) addReview(norm, 'replaced', '形似版本號但前面沒有版本字樣，已當位址替換；若其實是版本號請手動還原')
    return maps.ip[norm]
  }

  // 換過 MAC / email 的代號（02:00:00…、example.test）不含 IPv4，不會被誤換。
  // IPv4-mapped IPv6（::ffff:a.b.c.d）在同一個規則裡整段處理：保留 ::ffff: 前綴、位址換代號
  text = text.replace(IPV4_RE, (m, offset: number, whole: string) => {
    const prefix = m.startsWith(':') ? m.slice(0, 7) : ''
    const ip = m.slice(prefix.length)
    const norm = normIp(ip)
    if (!norm) return m                                                   // 有一段超過 255，不是位址
    if (norm === '0.0.0.0' || norm === '255.255.255.255' || isNetmask(norm)) return m
    if (!prefix && VERSION_BEFORE_RE.test(whole.slice(Math.max(0, offset - 14), offset))) {
      addReview(m, 'kept', '前面有版本字樣，當版本號原樣保留；若其實是位址請手動替換')
      return m
    }
    return prefix + codeFor(norm)
  })

  return {
    text, maps, hits, review, ipv6: [...v6],
    distinct: { ip: seen.ip.size, mac: seen.mac.size, email: seen.email.size },
  }
}

/** 對照表轉成文字（原值 → 代號）；MAC 以冒號顯示。 */
export function mapsAsText(maps: SanitizeMaps): string {
  const lines: string[] = ['# 對照表：留在本機，不要寄出、不要貼到公開處', '']
  const add = (title: string, m: Record<string, string>, fmt: (k: string) => string = k => k) => {
    const e = Object.entries(m)
    if (!e.length) return
    lines.push(`## ${title}`)
    for (const [k, v] of e) lines.push(`${fmt(k)}\t->\t${v}`)
    lines.push('')
  }
  add('IP', maps.ip)
  add('MAC', maps.mac, k => k.match(/.{2}/g)?.join(':') ?? k)
  add('Email', maps.email)
  return lines.join('\n')
}

/** 整理後仍可能含的敏感資料類型（頁面在複製／下載前要使用者逐項檢查）。 */
export const UNHANDLED_KINDS = ['主機名', '網域', 'IQN', 'WWPN／WWN', '設備序號', 'LUN 名稱', '共享／掛載路徑', '帳號', 'IPv6']
