// 舊版頁面網址 → 新版（V2）對應網址。
// 用途：嵌在 V2 頁籤裡的舊頁（功能、API 不改），裡面的連結與 router.push 還指著舊網址；
// middleware/v2-legacy.global.ts 在「人正在 V2 頁面時」把這些舊網址換成對應的 V2 網址，
// 讓人不會被帶出 V2 外框。舊頁本身、直接開舊網址（書籤）完全不受影響。
// 沒有對應 V2 頁的舊網址（例：/assets 資產清單）不在這裡，維持原樣。
export type LegacyQuery = Record<string, string | string[] | null | undefined>
export interface V2Target { path: string; query: Record<string, string> }

const pick = (q: LegacyQuery, keys: string[]): Record<string, string> => {
  const out: Record<string, string> = {}
  for (const k of keys) {
    const v = q[k]
    const s = Array.isArray(v) ? v[0] : v
    if (typeof s === 'string' && s !== '') out[k] = s
  }
  return out
}

export function v2LegacyTarget(path: string, q: LegacyQuery = {}): V2Target | null {
  const p = path.length > 1 ? path.replace(/\/+$/, '') : path
  if (p === '/existence') return { path: '/v2/reconcile', query: { tab: 'existence', ...pick(q, ['bucket', 'level']) } }
  if (p === '/manage-state-detail') return { path: '/v2/reconcile', query: { tab: 'state', ...pick(q, ['state']) } }
  if (p === '/count-bridge') return { path: '/v2/reconcile', query: {} }
  if (p === '/number-check') return { path: '/v2/reconcile', query: { tab: 'numcheck' } }
  if (p === '/pipeline') return { path: '/v2/onboard', query: { tab: 'funnel', ...pick(q, ['stage', 'env_group', 'os_type', 'drill']) } }
  if (p === '/scan-results') return { path: '/v2/onboard', query: { tab: 'scan', ...pick(q, ['registered']) } }
  if (p === '/ocp') return { path: '/v2/arch', query: { tab: 'ocpdetail', ...pick(q, ['cluster', 'risk', 'role']) } }
  if (p === '/architecture') return { path: '/v2/arch', query: pick(q, ['tab']) }
  if (p === '/data-quality') return { path: '/v2/datafix', query: { tab: 'quality' } }
  if (p === '/corrections') return { path: '/v2/datafix', query: { tab: 'mismatch' } }
  if (p === '/reports/business-systems') return { path: '/v2/systems', query: { tab: 'rank' } }
  if (p === '/golive') return { path: '/v2/audit', query: { tab: 'golive' } }
  let m = /^\/golive\/([^/]+)$/.exec(p)
  if (m) return { path: `/v2/golive/${m[1]}`, query: {} }
  m = /^\/assets\/([^/]+)$/.exec(p)
  if (m && m[1] !== 'new') {
    // 要編輯（?edit=1）去資產明細頁（舊頁嵌入，能編輯）；其餘看資料一律去主機履歷
    return q.edit === '1' ? { path: `/v2/asset/${m[1]}`, query: { edit: '1' } } : { path: `/v2/machine/${m[1]}`, query: {} }
  }
  return null
}
