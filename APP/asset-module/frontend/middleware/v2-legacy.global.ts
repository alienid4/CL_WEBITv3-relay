// 人在 V2（/v2/...）頁面時，點到（或程式 router.push 到）已有 V2 對應頁的舊網址，改送到 V2 對應頁。
// 為什麼要有：V2 頁籤裡嵌了既有舊頁（資料品質、納管漏斗、OCP 明細…），舊頁裡的連結還是舊網址，
// 不攔的話按一下就被帶出 V2 外框。對照表在 utils/v2LegacyMap.ts。
// 只在「從 /v2 出發」時生效：直接開舊網址、舊版頁面之間互點完全不變。檔名排在 auth／features 之後，登入與功能開關照舊先檢查。
import { v2LegacyTarget } from '~/utils/v2LegacyMap'

export default defineNuxtRouteMiddleware((to, from) => {
  if (!from || !from.path.startsWith('/v2') || to.path.startsWith('/v2')) return
  const t = v2LegacyTarget(to.path, to.query as any)
  if (t) return navigateTo({ path: t.path, query: t.query })
})
