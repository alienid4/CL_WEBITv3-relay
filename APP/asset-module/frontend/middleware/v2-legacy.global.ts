// 舊版網址一律轉到 V2 對應頁（2.042.20 起，使用者：「以後都不要再看到 V1 頁面」）。
// 以前只在「從 /v2 出發」時轉，書籤、手打網址、摘要信連結仍落在舊版、之後一路都是舊選單——那是主要漏洞。
// 現在不管從哪裡來都轉；對照表與例外（/login、/print/*）在 utils/v2LegacyMap.ts。
// 例外中的例外：網址帶 ?legacy=1 不轉——只給 V2 還沒移植、畫面上明確標「舊版」的功能用（docs/V2移植清單.md C 類）。
// 檔名排在 auth／features 之後，登入與功能開關照舊先檢查。
// 已移植完成的頁（isPorted）legacy=1 也不放行，直接轉到 V2（2.047.1，GPT 審查 P1-4）。
import { LEGACY_BYPASS, isPorted, v2LegacyTarget } from '~/utils/v2LegacyMap'

export default defineNuxtRouteMiddleware((to, from) => {
  if (to.path.startsWith('/v2')) return
  if (!isPorted(to.path)) {
    if (to.query[LEGACY_BYPASS] === '1') return
    // 已經用 legacy=1 開著的舊頁，在同一頁裡改篩選（router.replace 換 query、常把 legacy 洗掉）不要被轉走
    if (from && from.path === to.path && from.query[LEGACY_BYPASS] === '1') return
  }
  const t = v2LegacyTarget(to.path, to.query as any)
  if (t) return navigateTo({ path: t.path, query: t.query }, { replace: true })
})
