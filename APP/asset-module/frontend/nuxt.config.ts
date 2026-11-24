// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2026-07-17',
  devtools: { enabled: true },
  css: ['~/assets/css/main.css'],
  runtimeConfig: {
    // 伺服器端（SSR）專用的後端位置，環境變數 NUXT_API_BASE_SERVER 覆蓋；空字串＝跟 public.apiBase 一樣（預設，行為不變）。
    // 正式機可設成 http://127.0.0.1:8000：SSR 不必繞 nginx＋TLS 再打回自己（量測首頁 TTFB 約 2.8 秒，其中每支 SSR API 都多一趟）。
    // 只給 SSR 用（import.meta.server）；瀏覽器端一律照舊用 public.apiBase。
    apiBaseServer: '',
    public: {
      // 後端API位置，環境變數 NUXT_PUBLIC_API_BASE 可覆蓋（部署到221時指向實際位址）
      apiBase: 'http://localhost:8000',
    },
  },
})
