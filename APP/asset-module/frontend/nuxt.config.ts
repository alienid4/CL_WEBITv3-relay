// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2026-07-17',
  devtools: { enabled: true },
  css: ['~/assets/css/main.css'],
  runtimeConfig: {
    public: {
      // 後端API位置，環境變數 NUXT_PUBLIC_API_BASE 可覆蓋（部署到221時指向實際位址）
      apiBase: 'http://localhost:8000',
    },
  },
})
