// 舊版頁面網址 → 新版（V2）對應網址。
// 2.042.20 起（使用者：「以後都不要再看到 V1 頁面」）：middleware/v2-legacy.global.ts **一律**套這張表——
// 書籤、手打網址、信裡的連結、嵌在 V2 裡的舊頁自己的連結，只要舊網址有 V2 對應頁就轉過去。
// 清單與分類見 docs/V2移植清單.md（A/B/C 類有 V2 頁或頁籤的才在這裡）。
//
// 查詢參數：預設整包帶過去（嵌在 V2 頁籤裡的舊頁是讀同一個 route.query，帶過去它才會停在原本的篩選），
// 再蓋上 V2 頁籤參數。V1 的 tab 跟 V2 的 tab 意思不同時，先把 V1 的 tab 拿掉（見 drop）。
//
// 不轉的：/login（/print/labels、/print/rack-sheet 2.047.2 起搬到 /v2/print/，改成轉；/lifecycle、/features 2.043.10 起有 V2 對應），
// 以及網址帶 legacy=1 的（V2 上明確標「舊版」、V2 還沒移植的功能才用，見 LEGACY_BYPASS）。
export type LegacyQuery = Record<string, string | string[] | null | undefined>
export interface V2Target { path: string; query: Record<string, string> }

/** 網址帶這個參數＝刻意開舊版（只給 V2 還沒移植的功能用，畫面上要標「舊版」），middleware 不轉。 */
export const LEGACY_BYPASS = 'legacy'

/** 已移植完成（V2 原生、舊頁不再嵌入）的舊網址：legacy=1 也不開舊頁，一律轉到 V2（2.047.1，GPT 審查 P1-4）。
 *  之後每移植完一頁就加進來。結尾 /* 代表底下的動態路徑（如 /golive/:serial）。 */
export const PORTED = [
  '/activity', '/model-specs', '/reports/host-sources', '/reports/monthly', '/scan-results',
  '/golive', '/golive/*', '/blast/checklist/*',
  // 第 2 批（2.047.2）
  '/segments', '/documents', '/reports/physical-distribution', '/print/labels', '/print/rack-sheet',
  '/anomalies', '/architecture', '/ocp', '/contacts',      // 2.047.3
  '/account-matrix', '/accounts',                          // 2.048.0 V13＋V15
  '/topology',                                             // 2.048.1 V58
  '/existence', '/number-check', '/manage-state-detail',   // 2.048.4 V31／V39／V37
  '/reports/system-overview', '/reports/business-systems',   // 2.048.6 V50／V45
  '/drift', '/hmc',                                         // 2.048.8 V29／V35
  '/san', '/collect-account',                               // 2.048.12 V52／V24
  '/account-ops', '/batch-onboard',                         // 2.048.14 V14／V22
  '/relocation', '/scan-shells',                           // 2.048.10 V44／V54
  '/reports/system-group', '/reports/classify',             // 2.048.16 V49／V46
  '/data-quality', '/rack-diagram',                          // 2.048.18 V27／V42
]
export function isPorted(path: string): boolean {
  const p = path.length > 1 ? path.replace(/\/+$/, '') : path
  return PORTED.some(x => (x.endsWith('/*') ? p.startsWith(x.slice(0, -1)) && p.length > x.length - 1 : p === x))
}

/** 永遠不轉的舊網址（前綴）。 */
export const NEVER_REDIRECT = ['/login']

const flat = (q: LegacyQuery): Record<string, string> => {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(q)) {
    const s = Array.isArray(v) ? v[0] : v
    if (typeof s === 'string' && s !== '' && k !== LEGACY_BYPASS) out[k] = s
  }
  return out
}
const pick = (q: LegacyQuery, keys: string[]): Record<string, string> => {
  const all = flat(q)
  const out: Record<string, string> = {}
  for (const k of keys) if (k in all) out[k] = all[k]
  return out
}
const drop = (q: LegacyQuery, keys: string[]): Record<string, string> => {
  const out = flat(q)
  for (const k of keys) delete out[k]
  return out
}
const to = (path: string, query: Record<string, string> = {}): V2Target => ({ path, query })

// 舊網址 → (查詢參數) => V2 目標。key 是完整路徑（不含結尾斜線）。
const SIMPLE: Record<string, (q: LegacyQuery) => V2Target> = {
  // ── 9.2 數量對帳 ──
  '/existence': q => to('/v2/reconcile', { tab: 'existence', ...pick(q, ['bucket', 'level']) }),
  '/manage-state-detail': q => to('/v2/reconcile', { tab: 'state', ...pick(q, ['state']) }),
  '/count-bridge': () => to('/v2/reconcile'),
  '/number-check': () => to('/v2/reconcile', { tab: 'numcheck' }),
  '/machine-key': () => to('/v2/reconcile', { tab: 'rule' }),
  // ── 6.1 納管作業／6.2 設備收集 ──
  '/pipeline': q => to('/v2/onboard', { tab: 'funnel', ...pick(q, ['stage', 'env_group', 'os_type', 'drill']) }),
  '/scan-results': q => to('/v2/onboard', { tab: 'scan', ...pick(q, ['registered']) }),
  '/adopt': q => to('/v2/onboard', drop(q, ['tab'])),
  '/batch-onboard': q => to('/v2/onboard', drop(q, ['tab'])),
  '/lifecycle': () => to('/v2/onboard', { tab: 'life' }),      // V72（2.043.10）
  '/features': q => to('/v2/manual', { view: 'features', ...(flat(q).q ? { fq: flat(q).q } : {}) }),   // V73（2.043.10）
  '/reports/scan-gaps': q => to('/v2/onboard', { ...drop(q, ['tab']), tab: 'gaps' }),
  '/san': q => to('/v2/collect', { ...drop(q, ['tab']), tab: 'san' }),
  '/hmc': q => to('/v2/collect', { ...drop(q, ['tab']), tab: 'hmc' }),
  // ── 3 業務系統／架構圖 ──
  '/ocp': q => to('/v2/arch', { tab: 'ocpdetail', ...pick(q, ['cluster', 'risk', 'role']) }),
  '/architecture': q => to('/v2/arch', pick(q, ['tab'])),
  '/reports/business-systems': () => to('/v2/systems', { tab: 'rank' }),
  '/topology': q => {
    const s = flat(q).system
    return s ? to(`/v2/system/${encodeURIComponent(s)}`) : to('/v2/systems')
  },
  // ── 9.1 待補資料 ──
  '/data-quality': () => to('/v2/datafix', { tab: 'quality' }),
  '/corrections': () => to('/v2/datafix', { tab: 'mismatch' }),
  '/data-fix': q => to('/v2/datafix', { ...pick(q, ['unit']), ...(flat(q).tab === 'systems' ? { ub: 'sys' } : {}), tab: 'units' }),
  '/reports/classify': q => to('/v2/datafix', { ...drop(q, ['tab']), tab: 'classify' }),
  '/scan-shells': q => to('/v2/datafix', { ...drop(q, ['tab']), tab: 'shells' }),
  // ── 8 合規稽核 ──
  '/golive': () => to('/v2/audit', { tab: 'golive' }),
  '/config-audit': q => to('/v2/audit', { ...drop(q, ['tab']), tab: 'config' }),
  '/drift': q => to('/v2/audit', { ...drop(q, ['tab']), tab: 'drift' }),
  '/eos': q => to('/v2/eos', drop(q, ['tab'])),
  '/patch': () => to('/v2/patch'),
  // ── 1 今日概況 ──
  '/changes': (q) => {
    const f = flat(q)
    const t = f.tab
    if (t === 'mail-log' || t === 'settings') return to('/v2/settings', { st: 'notify', sub: 'digest' })
    return to('/v2/changes', { tab: 'daily', ...pick(q, ['date']), ...(t && t !== 'overview' ? { cat: t } : {}) })
  },
  '/anomalies': q => to('/v2/changes', { ...drop(q, ['tab']), tab: 'anomaly' }),
  '/issues': q => to('/v2/changes', { ...drop(q, ['tab']), tab: 'issues' }),
  '/risk': q => to('/v2/risk', pick(q, ['tab', 'view'])),   // N15：子清單 view 也帶
  '/oncall': q => to('/v2/night', pick(q, ['q'])),
  // ── 2 主機 ──
  '/hosts': q => to('/v2/hosts', pick(q, ['q'])),
  '/assets': (q) => {
    const f = flat(q)
    if (f.show === 'duplicates') return to('/v2/hosts', { tab: 'dups' })
    if (f.open === 'cia') return to('/v2/datafix', { tab: 'cia' })
    if (f.filter_field === 'api_id' && f.filter_value) return to(`/v2/system/${encodeURIComponent(f.filter_value)}`)
    const kw = f.q || f.filter_value
    return to('/v2/hosts', { ...pick(q, ['os_class']), ...(kw ? { q: kw } : {}) })
  },
  '/assets/new': () => to('/v2/hosts', { new: '1' }),
  // ── 4 事件應變 ──
  '/incident': () => to('/v2/incident'),
  '/health-check': q => to('/v2/incident', { ...drop(q, ['tab']), tab: 'health' }),
  '/blast': (q) => {
    const f = flat(q)
    // V62（2.043.10）：舊 /blast 的功能都在 4.1「影響範圍查詢」頁籤；帶 q＝直接查
    // A1（2.043.11）：view=map&system＝看某系統的上下游圖 → 業務系統頁；mode（probe＝也看依賴誰）、port 照帶；
    // 只有 system 沒有 q 時，用系統代號查（planned＝事前評估，跟 incident 同一份結果，不另帶）
    if (f.view === 'map' && f.system) return to(`/v2/system/${encodeURIComponent(f.system)}`, { tab: 'deps' })   // 系統頁的「上下游」頁籤
    const kw = f.q || f.system
    return to('/v2/incident', { tab: 'impact', ...(kw ? { iq: kw } : {}), ...(f.mode === 'probe' ? { mode: 'probe' } : {}), ...(f.port ? { port: f.port } : {}) })
  },
  // ── 5 機房管理 ──
  '/datacenter': q => to('/v2/datacenter', pick(q, ['room', 'rack'])),
  '/storage': q => to('/v2/storage', pick(q, ['room', 'tab'])),
  // ── 7 盤點作業 ──
  '/rack-audit': q => to('/v2/inventory/rack', drop(q, ['tab'])),
  '/rack-diagram': q => to('/v2/inventory/rack', { ...drop(q, ['tab']), tab: 'photo' }),
  '/relocation': q => to('/v2/inventory/rack', { ...drop(q, ['tab']), tab: 'relocate' }),
  '/accounts': q => to('/v2/inventory/matrix', { tab: 'dashboard', ...pick(q, ['os']) }),   // V15 帳號儀表板＝合規矩陣「儀表板」頁籤
  '/account-ops': q => to('/v2/inventory/account', { ...drop(q, ['tab']), tab: 'identity' }),
  '/account-matrix': q => to('/v2/inventory/matrix', flat(q)),
  '/software': q => to('/v2/inventory/software', { tab: 'list', ...pick(q, ['sw']) }),
  '/services': q => to('/v2/inventory/service', { tab: 'list', ...pick(q, ['port', 'ip', 'asset_serial']) }),
  // ── 10 報表 ──
  '/reports/monthly': () => to('/v2/report', { tab: 'monthly' }),
  '/reports/system-group': () => to('/v2/report', { tab: 'group' }),
  '/reports/system-overview': () => to('/v2/report', { tab: 'overview' }),
  '/reports/host-sources': () => to('/v2/report', { tab: 'sources' }),
  '/distribution': () => to('/v2/dist'),
  '/reports/physical-distribution': () => to('/v2/dist', { tab: 'phys' }),
  // ── 列印頁（純列印、沒有外框；V59／V60，2.047.2）──
  '/print/labels': q => to('/v2/print/labels', flat(q)),
  '/print/rack-sheet': q => to('/v2/print/rack-sheet', flat(q)),
  // ── ⚙ 系統設定（嵌入的舊頁讀自己的參數，所以整包帶過去，st 選頁籤）──
  '/import': q => to('/v2/settings', { ...flat(q), st: 'import' }),
  '/contacts': q => to('/v2/settings', { ...flat(q), st: 'contacts' }),
  '/model-specs': q => to('/v2/settings', { ...flat(q), st: 'specs' }),
  '/segments': q => to('/v2/settings', { ...flat(q), st: 'segments' }),
  '/documents': q => to('/v2/settings', { ...flat(q), st: 'documents' }),
  '/activity': q => to('/v2/settings', { ...flat(q), st: 'activity' }),
  '/nav-editor': () => to('/v2/settings', { st: 'nav' }),
  '/reset': () => to('/v2/settings', { st: 'reset' }),
  '/collect-account': () => to('/v2/settings', { st: 'identity', sub: 'migration' }),
  '/settings': (q) => {
    const f = flat(q)
    const t = f.tab
    if (f.disabled) return to('/v2/settings', { st: 'dev', sub: 'modules', disabled: f.disabled })
    if (t === 'backup') return to('/v2/settings', { st: 'backup' })
    if (t === 'mail') return to('/v2/settings', { st: 'notify', sub: 'mail' })
    if (t === 'credentials' || t === 'schedule' || t === 'autoonboard') return to('/v2/settings', { st: 'identity', sub: t })
    if (t === 'connections' || t === 'modules') return to('/v2/settings', { st: 'dev', sub: t })
    return to('/v2/settings')
  },
}

/** 舊資產明細 /assets/:serial 的頁籤 → 主機履歷 [分組 tab, 段落 sec]（段落 key 見 backend/v2_sections.py SECTIONS）。 */
export const ASSET_TAB_TO_MACHINE: Record<string, [string, string?]> = {
  advanced: ['overview'],
  hwinfo: ['hw', 'hw_spec'],
  san: ['hw', 'san'],
  software: ['sw', 'software'],
  services: ['sw', 'services'],
  schedule: ['sw', 'schedule'],
  accounts: ['sec', 'accounts'],
  vulns: ['sec', 'vulns'],
  audit: ['sec', 'audit'],
  health: ['sec', 'health'],
  history: ['chg', 'changes'],
  cia: ['raw', 'cia'],
  personnel: ['raw', 'personnel'],
  rebuild: ['rebuild'],
}

export function v2LegacyTarget(path: string, q: LegacyQuery = {}): V2Target | null {
  const p = path.length > 1 ? path.replace(/\/+$/, '') : path
  if (p === '/' || p.startsWith('/v2') || p.startsWith('/api/') || p.startsWith('/_nuxt')) return null
  if (NEVER_REDIRECT.some(n => p === n.replace(/\/$/, '') || p.startsWith(n.endsWith('/') ? n : n + '/'))) return null
  const rule = SIMPLE[p]
  if (rule) return rule(q)
  let m = /^\/golive\/([^/]+)$/.exec(p)
  if (m) return to(`/v2/golive/${m[1]}`)
  m = /^\/incident\/([^/]+)$/.exec(p)
  if (m) return to(`/v2/incident/${m[1]}`, pick(q, ['tab']))
  m = /^\/blast\/checklist\/([^/]+)$/.exec(p)
  if (m) return to(`/v2/checklist/${m[1]}`)
  m = /^\/assets\/([^/]+)$/.exec(p)
  if (m) {
    // 要編輯（?edit=1）去資產明細頁（舊頁嵌入，能編輯，舊頁籤 ?tab= 照帶，嵌入的舊頁自己讀）；
    // 其餘看資料去主機履歷，舊頁籤對到主機履歷的分組＋段落（N9：不再把 tab 丟掉）
    const f = flat(q)
    if (f.edit === '1') return to(`/v2/asset/${m[1]}`, { edit: '1', ...pick(q, ['tab']) })
    const s = f.tab ? ASSET_TAB_TO_MACHINE[f.tab] : undefined
    return to(`/v2/machine/${m[1]}`, s ? (s[1] ? { tab: s[0], sec: s[1] } : { tab: s[0] }) : {})
  }
  return null
}

/** 測試與守門用：這張表登記了哪些舊網址（不含 /golive/:x 這類動態路徑）。 */
export const LEGACY_PATHS = Object.keys(SIMPLE)

/** 共用元件（DataCell 等）要連「某欄位＝某值的資產」時用：跟舊 /assets?filter_field=&filter_value= 的轉址同一條規則（X3）。 */
export function assetFilterTarget(field: string, value: string): V2Target {
  return SIMPLE['/assets']({ filter_field: field, filter_value: value })
}
