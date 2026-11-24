// 管理員限定頁面的直接網址也要擋（選單隱藏不等於進不去）。跑在 auth.global.ts 之後（檔名字母序 auth. < authz）。
// 名單＝useNavV2 裡標 admin: true 的項目；登入停用時後端視為 admin，照舊放行（以後端為準，見 auth.global.ts）。
import { V2_NAV, type V2NavItem } from '~/composables/useNavV2'

export default defineNuxtRouteMiddleware((to) => {
  const adminOnly = V2_NAV.flatMap(g => g.items).filter((it: V2NavItem) => it.admin).map(it => it.to)
  const path = to.path.toLowerCase()   // 路由比對不分大小寫，/V2/DevKit 也要擋
  if (!adminOnly.some(p => path === p || path.startsWith(p + '/'))) return
  const { isAdmin } = useAuth()
  if (!isAdmin.value) return navigateTo('/v2')
})
