import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

/**
 * 前端測試設定。
 *
 * 2026-08-26 補的。在這之前**前端零測試**——後端 837 個測試守著計算與口徑，
 * 前端一行都沒有，而今天實際踩到的坑有一半在前端（進度條沒有、批次上傳整包送、
 * 「建議排除掃描 127」沒有單位、匯入結果算好了卻沒顯示出來）。
 *
 * ## 為什麼用 happy-dom 而不是 jsdom
 * 快很多，而且我們要測的東西（composable 的排序邏輯、元件的條件顯示）用不到
 * jsdom 那些冷門 API。真的踩到再換。
 *
 * ## .vue 掛得起來，Nuxt 自動匯入掛不起來
 *
 * `plugins: [vue()]` 讓單一元件（`components/*.vue`）可以用 @vue/test-utils
 * `mount()` 起來測真實 DOM 與事件。
 *
 * ## 為什麼不用 @nuxt/test-utils
 * 那套要起一個真的 Nuxt 環境，慢而且相依複雜。**大部分值得測的東西不需要它**：
 * 排序、格式化、條件判斷都是純函式或單一元件。等真的需要測「整頁載入」再說。
 *
 * ⚠️ 這代表用到 Nuxt 自動匯入（useApi／useToast／definePageMeta）的**頁面元件
 * 沒辦法直接掛載**。要測那種頁面，先把邏輯抽成 composable 或純函式再測——
 * 那本來就是比較好的寫法。
 */
export default defineConfig({
  // ⚠️ 2026-08-27 補上。第一版漏了這行，結果**任何 .vue 都掛不起來**——
  // 直接 import 會噴 "Failed to parse source ... Install @vitejs/plugin-vue"。
  // 是協作 AI 補測試時撞到才發現的：它沒辦法掛載元件，只好退而求其次去比對
  // 原始碼字串，交回來一批「讀檔案內容 toContain(...)」的假測試。
  //
  // 教訓：**基礎建設架好之後要自己先寫一個目標型別的測試驗過**。
  // 我只驗了 composable（純 TS），沒驗 .vue，就宣稱「前端測試環境好了」。
  plugins: [vue()],
  test: {
    environment: 'happy-dom',
    include: ['tests/**/*.spec.ts'],
    globals: true,
  },
  resolve: {
    alias: {
      '~': fileURLToPath(new URL('.', import.meta.url)),
      '@': fileURLToPath(new URL('.', import.meta.url)),
    },
  },
})
