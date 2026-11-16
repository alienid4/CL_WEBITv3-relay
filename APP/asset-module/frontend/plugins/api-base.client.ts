// apiBase 寫死的主機名（不管是 127.0.0.1、localhost、還是某個固定 IP）只要跟瀏覽器
// 實際載入頁面的主機名不是同一個字串，Cookie 的 SameSite=Lax 保護就會判定成「跨站」，
// 登入時發的 session cookie 不會被帶到後續 API 請求上——即使兩者其實是同一台機器
// （例如頁面是 localhost:3000、API 寫死 127.0.0.1:8000，瀏覽器眼中兩者是不同网站）。
// 症狀是登入 API 本身回 200 成功，但緊接著的其他 API 全部 401，畫面顯示「載入失敗」，
// 而且看不出是這個原因——這正是 2026-08-11 實際踩到的狀況。
//
// 修法：只要 apiBase 的主機名跟頁面的主機名不同，就把 apiBase 的主機名換成頁面自己的，
// 埠號不變。正式環境（NUXT_PUBLIC_API_BASE 已指定跟前端同主機的實際位址）本來就會相同，
// 這裡不會有動作；本機開發、SSH port-forward、雲端環境轉發埠…這些「其實是同一台機器，
// 只是名稱寫法對不上」的情況，都靠這條規則自動校正。
export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  const apiBase = config.public.apiBase
  if (!apiBase) return

  let apiUrl: URL
  try {
    apiUrl = new URL(apiBase)
  } catch {
    return
  }

  const pageHost = window.location.hostname
  if (apiUrl.hostname !== pageHost) {
    apiUrl.hostname = pageHost
    config.public.apiBase = apiUrl.toString().replace(/\/$/, '')
  }
})
