// S13：功能模組開關的路由層攔截（D28）。跑在auth.global.ts之後（檔名字母序a<f，
// Nuxt照字母序跑全域middleware），所以能進到這裡的都已經是登入使用者。
// 對照表（舊網址＋V2 網址）與停用導向在 utils/featureRoutes.ts。
import { disabledTarget, moduleForRoute } from '~/utils/featureRoutes'

export default defineNuxtRouteMiddleware(async (to) => {
  const moduleKey = moduleForRoute(to.path, to.query as any)
  if (!moduleKey) return

  const { isEnabled, ensureLoaded } = useFeatureFlags()
  await ensureLoaded()

  if (!isEnabled(moduleKey)) {
    const { isAdmin } = useAuth()
    return navigateTo(disabledTarget(moduleKey, isAdmin.value, isEnabled('dashboard')))
  }
})
