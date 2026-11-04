// 功能模組開關：哪個網址歸哪個開關（從 middleware/features.global.ts 抽出來，2.043.4，方便測試）。
// 「系統設定」路徑刻意不攔截——那是切換開關的畫面本身，見schema.sql註解的自鎖死防呆。
// 被關掉時送到 V2 功能模組管理（disabledTarget）；V2 路徑的對照在下面的 V2_MODULE_MAP。
// 注意順序：底下用 startsWith 比對，'/' 那條特例只比完全相等，其餘先寫先贏。
export const ROUTE_MODULE_MAP: Array<[string, string]> = [
  ['/', 'dashboard'],
  // 儀表板磚塊點進去的下鑽頁，歸在 dashboard 底下——關掉儀表板卻還能從網址列進到
  // 它的細項頁，等於開關沒關乾淨。
  ['/issues', 'dashboard'],
  ['/scan-results', 'dashboard'],
  ['/anomalies', 'dashboard'],
  ['/distribution', 'dashboard'],
  ['/assets', 'assets'],
  ['/import', 'import'],
  ['/topology', 'topology'],
  ['/services', 'services'],
  ['/software', 'services'],   // 軟體盤點跟服務盤點共用開關（同樣是「進去問機器」的唯讀採集）
  ['/accounts', 'accounts'],
  ['/account-matrix', 'accounts'],
  ['/account-ops', 'accounts'],
  // 2026-08-15 新增的模組。基線失效跟上線前檢查是同一件事的兩半，共用一個開關——
  // 只關掉其中一半，另一半會列出一堆沒有來源的資料，比全開全關都糟。
  ['/reports', 'report_system'],
  ['/activity', 'activity'],
  ['/golive', 'golive'],
  ['/drift', 'golive'],
  ['/documents', 'documents'],
  ['/segments', 'segments'],
  ['/data-quality', 'data_quality'],
  ['/eos', 'eos'],
  ['/adopt', 'adopt'],
  ['/blast', 'blast'],
  ['/ocp', 'topology'],
  ['/datacenter', 'topology'],
  ['/storage', 'topology'],
  ['/print/', 'topology'],
  ['/patch', 'report_system'],   // 弱點彙總跟報告同一個開關   // OCP 明細跟架構圖同一個開關，不該只關一半
]

// N28（2.043.4）：V2 頁面也要吃同一組開關。V2 外框用寫死的 useNavV2，不另讀旗標，
// 所以在路由層比對 V2 路徑（先寫先贏；'/v2' 只比完全相等；結尾 '/' ＝底下的動態頁）。
// 第三欄＝只有該頁籤歸這個開關（例：9.1 待補資料只有「資料品質」頁籤屬 data_quality；8.2 只有上線前檢查／基線失效屬 golive）。
// ⚙ 系統設定本身不攔（切開關的畫面），但嵌在裡面的匯入／單據／網段／操作紀錄照各自開關。
const V2_MODULE_MAP: Array<[string, string, { key: string; vals: string[] }?]> = [
  ['/v2', 'dashboard'],
  ['/v2/changes', 'dashboard'],
  ['/v2/dist', 'dashboard'],
  ['/v2/hosts', 'assets'],
  ['/v2/machine', 'assets'],
  ['/v2/asset/', 'assets'],
  ['/v2/systems', 'topology'],
  ['/v2/system/', 'topology'],
  ['/v2/arch', 'topology'],
  ['/v2/datacenter', 'topology'],
  ['/v2/storage', 'topology'],
  ['/v2/inventory/service', 'services'],
  ['/v2/inventory/software', 'services'],
  ['/v2/inventory/account', 'accounts'],
  ['/v2/inventory/matrix', 'accounts'],
  ['/v2/report', 'report_system'],
  ['/v2/patch', 'report_system'],
  ['/v2/golive/', 'golive'],
  ['/v2/audit', 'golive', { key: 'tab', vals: ['golive', 'drift'] }],
  ['/v2/datafix', 'data_quality', { key: 'tab', vals: ['quality'] }],
  ['/v2/eos', 'eos'],
  // 6.1 納管作業裡嵌的舊頁照舊網址的開關（GPT 審查 2）：掃描結果＝舊 /scan-results（dashboard）、掃描缺口＝舊 /reports/scan-gaps（report_system）
  ['/v2/onboard', 'dashboard', { key: 'tab', vals: ['scan'] }],
  ['/v2/onboard', 'report_system', { key: 'tab', vals: ['gaps'] }],
  ['/v2/onboard', 'adopt'],
  ['/v2/checklist/', 'blast'],
  ['/v2/print/', 'topology'],      // V59／V60（2.047.1）：列印頁搬到 /v2/print/，跟舊 /print/ 同一個開關
  ['/v2/incident', 'blast', { key: 'tab', vals: ['impact'] }],   // V62：舊 /blast 搬到 4.1 影響範圍查詢頁籤
  ['/v2/settings', 'import', { key: 'st', vals: ['import'] }],
  ['/v2/settings', 'documents', { key: 'st', vals: ['documents'] }],
  ['/v2/settings', 'segments', { key: 'st', vals: ['segments'] }],
  ['/v2/settings', 'activity', { key: 'st', vals: ['activity'] }],
]

const first = (v: any): string => String(Array.isArray(v) ? (v[0] ?? '') : (v ?? ''))

/** 某個路徑＋查詢參數歸哪個功能開關（null＝不受開關管）。匯出給測試用。 */
export function moduleForRoute(path: string, query: Record<string, any> = {}): string | null {
  if (path === '/login') return null
  if (path === '/v2' || path.startsWith('/v2/')) {
    const p = path.length > 3 ? path.replace(/\/+$/, '') : path
    const hit = V2_MODULE_MAP.find(([pre, , only]) => {
      const okPath = pre === '/v2' ? p === '/v2' : (pre.endsWith('/') ? p.startsWith(pre) : (p === pre || p.startsWith(pre + '/')))
      return okPath && (!only || only.vals.includes(first(query[only.key])))
    })
    return hit ? hit[1] : null
  }
  if (path.startsWith('/settings')) return null
  const match = ROUTE_MODULE_MAP.find(([pre]) => (pre === '/' ? path === '/' : path.startsWith(pre)))
  return match ? match[1] : null
}

/** 功能被關掉時送去的地方（N18／GPT 審查 3）：
 *  管理員 → V2「開發工具 › 功能模組管理」（能直接打開）；
 *  其他人看不到那個頁籤 → V2 首頁（首頁本身也被關時改手冊頁），都帶 ?disabled=，外框跳「功能已停用」提示。 */
export function disabledTarget(moduleKey: string, isAdmin = true, dashboardOn = true): { path: string; query: Record<string, string> } {
  if (isAdmin) return { path: '/v2/settings', query: { st: 'dev', sub: 'modules', disabled: moduleKey } }
  return { path: dashboardOn && moduleKey !== 'dashboard' ? '/v2' : '/v2/manual', query: { disabled: moduleKey } }
}
