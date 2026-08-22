// S13：功能模組開關的路由層攔截（D28）。跑在auth.global.ts之後（檔名字母序a<f，
// Nuxt照字母序跑全域middleware），所以能進到這裡的都已經是登入使用者。
// 「系統設定」路徑刻意不攔截——那是切換開關的畫面本身，見schema.sql註解的自鎖死防呆。
// 注意順序：底下用 startsWith 比對，'/' 那條特例只比完全相等，其餘先寫先贏。
const ROUTE_MODULE_MAP: Array<[string, string]> = [
  ['/', 'dashboard'],
  // 儀表板磚塊點進去的下鑽頁，歸在 dashboard 底下——關掉儀表板卻還能從網址列進到
  // 它的細項頁，等於開關沒關乾淨。
  ['/issues', 'dashboard'],
  ['/scan-results', 'dashboard'],
  ['/assets', 'assets'],
  ['/import', 'import'],
  ['/topology', 'topology'],
  ['/services', 'services'],
  ['/accounts', 'accounts'],
  ['/account-matrix', 'accounts'],
  ['/account-ops', 'accounts'],
  // 2026-08-15 新增的模組。基線失效跟上線前檢查是同一件事的兩半，共用一個開關——
  // 只關掉其中一半，另一半會列出一堆沒有來源的資料，比全開全關都糟。
  ['/reports', 'report_system'],
  ['/golive', 'golive'],
  ['/drift', 'golive'],
  ['/documents', 'documents'],
  ['/segments', 'segments'],
  ['/data-quality', 'data_quality'],
  ['/eos', 'eos'],
  ['/adopt', 'adopt'],
  ['/blast', 'blast'],
]

export default defineNuxtRouteMiddleware(async (to) => {
  if (to.path === '/login' || to.path.startsWith('/settings')) return

  const match = ROUTE_MODULE_MAP.find(([path]) =>
    path === '/' ? to.path === '/' : to.path.startsWith(path)
  )
  if (!match) return
  const [, moduleKey] = match

  const { isEnabled, ensureLoaded } = useFeatureFlags()
  await ensureLoaded()

  if (!isEnabled(moduleKey)) {
    return navigateTo(`/settings?disabled=${moduleKey}`)
  }
})
