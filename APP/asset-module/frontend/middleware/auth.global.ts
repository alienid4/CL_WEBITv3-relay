// 全域路由守衛：除了 /login，任何頁面都要有效session才能進，過期/未登入一律導回登入頁。
export default defineNuxtRouteMiddleware(async (to) => {
  const { user, checked, fetchMe } = useAuth()

  if (!checked.value) {
    await fetchMe()
  }

  if (!user.value && to.path !== '/login') {
    return navigateTo('/login')
  }
  if (user.value && to.path === '/login') {
    return navigateTo('/')
  }
})
