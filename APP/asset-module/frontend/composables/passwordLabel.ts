/**
 * 密碼「代號」（v1.468.6，2026-10-19 使用者：「譬如我測試過 A 密碼，失敗」）。
 *
 * 一次納管時可以替這組密碼取個代號（A／B／PAM-1），帳密被拒的機器會自動貼標籤「試過A失敗」，
 * 下次就知道那幾台要換哪一組。**只記代號，絕不存密碼**——所以這裡擋掉看起來像密碼的代號。
 */
export const LABEL_MAX = 12

/** 「帳密被拒」才貼「試過<代號>失敗」——比自動停止用的判斷窄（只認登入被拒，不認「需要 sudo 密碼」這類） */
export const AUTH_REJECT_RE = /登入被拒|帳號或密碼不對|Permission denied|Authentication failed|authentication failure/i

/** 代號有問題回錯誤訊息，沒問題回 null。空字串＝不用代號（合法）。 */
export function validateLabel(label: string, password: string): string | null {
  const l = (label || '').trim()
  if (!l) return null
  // 先擋「是密碼／像密碼」，再擋長度與字元——錯誤訊息要講出真正的問題
  const pw = password || ''
  if (pw && (l === pw || (l.length >= 4 && pw.includes(l)) || (pw.length >= 4 && l.includes(pw)))) {
    return '代號不可以是密碼或密碼的一部分——只記代號（例：A、B、PAM-1），不存密碼'
  }
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9\p{L}]/u].filter((re) => re.test(l)).length
  if (l.length >= 6 && classes >= 3) return '這個代號看起來像密碼——請用簡短代號（例：A、B、PAM-1）'
  if (l.length > LABEL_MAX) return `代號最多 ${LABEL_MAX} 個字（例：A、B、PAM-1）`
  if (!/^[\p{L}\p{N}_\-.]+$/u.test(l)) return '代號只能用中英文、數字、- _ .'
  return null
}

/** 帳密被拒的機器要貼的標籤與備註（備註只有時間＋短原因，不含任何帳密）。 */
export function authFailTag(label: string, reason: string, now = new Date()): { tag: string; note: string } {
  const p = (n: number) => String(n).padStart(2, '0')
  const at = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}`
  return { tag: `試過${label.trim()}失敗`, note: `${at} ${reason}` }
}

/** 失敗訊息 → 標籤用的短原因（給「把失敗的加標籤」預填）。 */
export function shortFailReason(message: string): string {
  const m = message || ''
  if (/登入被拒|帳號或密碼|Permission denied|authentication/i.test(m)) return '帳密被拒'
  if (/sudo/i.test(m)) return '沒有 sudo'
  if (/逾時|timed? ?out|連不上|無法連線|No route|refused/i.test(m)) return '連不上'
  if (/Windows/i.test(m)) return '是 Windows'
  return '納管失敗'
}
