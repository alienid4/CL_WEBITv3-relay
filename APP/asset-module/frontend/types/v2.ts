// 2.x 共用型別（類型標記、狀態）。後端 v2_views.py 產出，前端只畫。
// logo：作業系統圖示代號（composables/v2Logos.ts）；null＝畫文字方塊（tile），AIX／A400／設備／不知道都是。
export interface V2Badge {
  code: string; label: string; vm?: boolean; guessed?: boolean; os_class?: string
  logo?: string | null; tile?: string; version_label?: string; note?: string | null
}
export interface V2Status { key: 'online' | 'stale' | 'missing' | 'unknown' | string; label: string }
