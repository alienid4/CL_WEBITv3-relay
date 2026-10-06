// 全域路由守衛：除了 /login，任何頁面都要有效session才能進，過期/未登入一律導回登入頁。
//
// 例外：後端把登入整個關掉時（測試階段，使用者 2026-09-10「我現在測試階段，
// 用不到登入」），不要再導去登入頁——導過去也沒用，因為此時每支 API 本來就放行，
// 登入頁只會變成一道假關卡。
//
// ⚠️ 判斷一定要問後端（`/api/version` 的 `auth_disabled`），**不可以只在前端加旗標**：
// 真正決定放不放行的是後端。前端自己關掉守衛而後端沒關，畫面會變成滿頁 401；
// 反過來後端關了前端沒關，只是多一道沒必要的登入頁。一律以後端為準。
export default defineNuxtRouteMiddleware(async (to) => {
  const { user, checked, fetchMe } = useAuth()
  const { authDisabled, ensureLoaded } = useServerInfo()

  await ensureLoaded()

  if (!checked.value) {
    await fetchMe()
  }

  if (authDisabled.value) {
    // 登入已停用：登入頁沒有意義，進去了就把人送回首頁
    if (to.path === '/login') return navigateTo('/')
    return
  }

  if (!user.value && to.path !== '/login') {
    return navigateTo('/login')
  }
  if (user.value && to.path === '/login') {
    return navigateTo('/')
  }
})
