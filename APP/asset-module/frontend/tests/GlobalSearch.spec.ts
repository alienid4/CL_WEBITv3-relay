/**
 * GlobalSearch：搜尋結果要「說得出來源」。
 *
 * 2026-08-27 使用者的批評：「全文代表 DB 我都可以查，不是我說一個案例多一個功能，
 * 沒說就查不到」。後端改成通用掃表之後，**畫面也要跟上**——搜不到的時候
 * 要看得出是哪個字沒命中，不然使用者分不出「沒有這個東西」跟「我打錯字」。
 *
 * ⚠️ 這個元件用了 Nuxt 自動匯入（useApi／useRouter），沒辦法整個 mount。
 * 所以這裡測的是**同一份判斷邏輯**：把後端回傳套進去，確認算出來的
 * deadTerms／expanded 是對的。
 *
 * 這是刻意的取捨，也是限制：它守得住邏輯，守不住「有沒有真的顯示在畫面上」。
 * 要守後者得把那段抽成 composable——已列進建議，還沒做。
 */
import { describe, expect, it } from 'vitest'

interface Term { term: string; match: string[]; hits: number }

/** 跟 GlobalSearch.vue 裡的 computed 同一份判斷。 */
const deadTerms = (terms: Term[]) => terms.filter((t) => t.hits === 0)
const expanded = (terms: Term[]) => terms.filter((t) => t.match.length > 1)

describe('搜尋結果的可信度資訊', () => {
  it('完全沒命中的關鍵字要被挑出來', () => {
    // 使用者實際問過的例子：「測試區 vc」。改成同義詞之後測試區找得到，
    // 但如果哪天真的打了一個不存在的詞，要能立刻看出是那個詞的問題
    const terms: Term[] = [
      { term: '不存在的東西', match: ['不存在的東西'], hits: 0 },
      { term: 'vc', match: ['vc'], hits: 685 },
    ]
    expect(deadTerms(terms).map((t) => t.term)).toEqual(['不存在的東西'])
  })

  it('每個字都有命中但總數為 0 時，不可以說成「某個字沒命中」', () => {
    // 這是完全不同的情況：兩個條件都存在，只是沒有同時符合的資料。
    // 前者「改個字再試」，後者「少打一個字」——給錯建議會讓人白繞
    const terms: Term[] = [
      { term: '板橋', match: ['板橋'], hits: 1505 },
      { term: 'hmc', match: ['hmc'], hits: 12 },
    ]
    expect(deadTerms(terms)).toHaveLength(0)
  })

  it('有展開同義詞的要標出來，而且不含只有自己一個詞的', () => {
    // 不可以安靜地擴大搜尋範圍：多出來的結果一定要說得出來源，
    // 否則使用者搜「測試」卻看到一堆 uat 的東西，會以為系統亂撈
    const terms: Term[] = [
      { term: '測試', match: ['測試', '測試區', 'test', 'uat'], hits: 2133 },
      { term: 'vc', match: ['vc'], hits: 685 },
    ]
    const e = expanded(terms)
    expect(e.map((t) => t.term)).toEqual(['測試'])
    expect(e[0].match.slice(1)).toEqual(['測試區', 'test', 'uat'])
  })

  it('沒有 terms 時不會炸（後端舊版或搜尋失敗）', () => {
    // 後端可能是舊版、或這次請求失敗回了空物件。
    // 前端不可以因此整個下拉壞掉——搜尋框壞掉比少一行說明嚴重得多
    expect(deadTerms([])).toEqual([])
    expect(expanded([])).toEqual([])
  })
})
