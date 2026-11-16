/**
 * 作業系統 → 圖示（2026-10-21 使用者：「已納管後，主機名後面放 OS 的圖」）。
 *
 * - 只看**實測** OS（SSH／WinRM 登入讀到的），不看 CIA 登記——兩者可能不同
 *   （例：CIA 寫 Windows Server 2019，實測是 RedHat 9.4）。
 * - 沒納管 → 不顯示；已納管但實測 OS 不知道 → 「?」圖示＋「作業系統不知道」。
 * - 使用者定的分法：Red Hat、Oracle Linux、OCP（OpenShift／RHCOS 節點）各有自己的圖；
 *   Debian、CentOS、Rocky、Ubuntu、SUSE 及其他 Linux 一律企鵝；Windows、AIX 各自一個。
 *   提示一律寫實測的完整 OS 名稱＋版本，所以同是企鵝也分得出來。
 * - 圖示是元件內嵌 SVG（OsIcon.vue），公司機沒有網路也看得到。
 */
export type OsIconKey = 'rhel' | 'oracle' | 'openshift' | 'linux' | 'windows' | 'aix' | 'unknown'
  | 'cisco' | 'hp' | 'f5' | 'network'

/** OS 字樣 → 圖示代號。認不出來的回 unknown。順序有意義：RHCOS／Oracle 的字樣可能也帶 Red Hat。 */
export function osIconKey(os: string | null | undefined): OsIconKey {
  const s = String(os ?? '').trim().toLowerCase()
  if (!s) return 'unknown'
  if (/\baix\b/.test(s)) return 'aix'
  if (s.includes('windows')) return 'windows'
  if (s.includes('openshift') || s.includes('coreos') || /\brhcos\b/.test(s) || /\bocp\b/.test(s)) return 'openshift'
  if (s.includes('oracle') || /\bol\s?\d/.test(s)) return 'oracle'
  if (s.includes('centos') || s.includes('rocky')) return 'linux'
  if (s.includes('red hat') || s.includes('redhat') || /\brhel\b/.test(s)) return 'rhel'
  if (/linux|debian|ubuntu|suse|\bsles\b|alma|fedora|amazon/.test(s)) return 'linux'
  return 'unknown'
}

export interface OsIconInfo { key: OsIconKey; title: string }

/** 主機名後面要不要放圖、放哪一個。managed＝已納管；measuredOs＝實測 OS（不知道給 null）。 */
export function osIconFor(managed: boolean, measuredOs: string | null | undefined): OsIconInfo | null {
  if (!managed) return null
  const os = String(measuredOs ?? '').trim()
  if (!os) return { key: 'unknown', title: '作業系統不知道' }
  const key = osIconKey(os)
  return { key, title: key === 'unknown' ? `${os}（沒有對應的圖示）` : os }
}

// ---------------------------------------------------------------------
// 網路設備：主機名後面放「廠牌」圖示（2026-10-21）。網路設備不會像伺服器那樣納管（不建帳號、不收集），
// 所以不看納管狀態，一律顯示。廠牌由後端 host_kind.net_vendor_view 判（資產登記的廠牌字樣＝確定，
// MAC 廠商＝推測），提示寫出依據。
// ---------------------------------------------------------------------
export interface NetVendor { vendor: string | null; confidence_label: string | null; evidence: string[] }

/** 廠牌字樣 → 圖示代號：Cisco／HP（HPE、Aruba、H3C）／F5（BIG-IP）；其他或不知道＝一般網路設備。 */
export function netVendorKey(vendor: string | null | undefined): OsIconKey {
  const s = String(vendor ?? '').trim().toLowerCase()
  if (s.includes('cisco')) return 'cisco'
  if (/\bf5\b|big-?ip/.test(s)) return 'f5'
  if (/\bhpe?\b|hewlett|aruba|h3c|procurve/.test(s)) return 'hp'
  return 'network'
}

export function netIconFor(nv: NetVendor | null | undefined, model?: string | null): OsIconInfo | null {
  if (!nv) return null
  const how = nv.evidence?.[0] ? `（依據：${nv.evidence[0]}）` : ''
  if (!nv.vendor) {
    const m = String(model ?? '').trim()
    return { key: 'network', title: `網路設備・廠牌不知道${m ? `（登記機型：${m}）` : ''}` }
  }
  const conf = nv.confidence_label ? `・${nv.confidence_label}` : ''
  return { key: netVendorKey(nv.vendor), title: `網路設備・${nv.vendor}${conf}${how}` }
}
