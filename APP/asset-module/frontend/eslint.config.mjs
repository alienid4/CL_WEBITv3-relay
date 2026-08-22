// 前端靜態檢查（2026-08-15 建立）。
//
// 為什麼是 ESLint 而不是 vue-tsc：`nuxt typecheck` 需要 vue-tsc，但 v2/v3 在這台的
// Node 24 都是 ERR_PACKAGE_PATH_NOT_EXPORTED 裝不起來。ESLint 是純 JS 沒有那個問題。
//
// 這份設定只開**一條真正重要的規則**：vue/no-undef-properties——樣板裡引用了
// script 沒定義的東西就報錯。那正是「改 A 死 B」最常見的形態：把某段程式碼搬走／刪掉，
// 樣板裡的殘存參考沒清乾淨，`nuxt build` 一路綠燈，實際打開頁面才炸
// （2026-08-15 vCenter 從系統設定搬到匯入頁時就是這樣）。
//
// 刻意不開整包 recommended：這是既有專案，一次噴幾百條風格警告只會讓人把整個檢查關掉。
// 寧可先守住一條會害頁面掛掉的規則，之後要再加再說。
import vue from 'eslint-plugin-vue'
import vueParser from 'vue-eslint-parser'
import tsParser from '@typescript-eslint/parser'

export default [
  // 全域忽略要獨立一個物件：寫在下面那個設定裡只對該設定生效，
  // 結果建置產物（.output）會被掃進來噴一堆無關的錯（實測踩到）。
  { ignores: ['.nuxt/**', '.output/**', 'node_modules/**', 'dist/**', 'public/**'] },
  {
    files: ['**/*.vue'],
    plugins: { vue },
    // 樣板常寫 ($event.target as HTMLInputElement)，這些是瀏覽器內建型別不是變數。
    // 不宣告的話會噴一堆假警報，一旦有假警報，真警報就會被當成雜訊忽略掉。
    linterOptions: { reportUnusedDisableDirectives: true },
    languageOptions: {
      parser: vueParser,
      ecmaVersion: 'latest',
      sourceType: 'module',
      // <script setup lang="ts"> 要有 TS 解析器，否則 interface 會被當成保留字
      parserOptions: { parser: tsParser, ecmaFeatures: { jsx: false } },
      globals: {
        HTMLInputElement: 'readonly', HTMLSelectElement: 'readonly',
        HTMLTextAreaElement: 'readonly', HTMLElement: 'readonly',
        Event: 'readonly', KeyboardEvent: 'readonly', MouseEvent: 'readonly',
        File: 'readonly', FormData: 'readonly',
      },
    },
    rules: {
      // 樣板引用了不存在的變數／屬性 → 錯誤（不是警告，警告會被忽略）
      'vue/no-undef-properties': 'error',
      // 元件用了沒註冊的元件名，同樣是搬檔案後常見的殘留
      'vue/no-undef-components': ['error', {
        // Nuxt 自動匯入這些，ESLint 看不到註冊處，列白名單免得整片假警報
        ignorePatterns: [
          'NuxtLink', 'NuxtPage', 'NuxtLayout', 'ClientOnly', 'Transition',
          'SortTh', 'DataCell', 'GlobalSearch', 'OnboardModal',
          'SearchableSelect', 'VcenterAutoImport',
        ],
      }],
    },
  },
]
