/**
 * useSort：表格排序（天條：只要是表格，每一欄都要能排）。
 *
 * ⚠️ 這份同時是**給 Codex 的樣板**——補測試時照這個形狀寫：
 *   1. 測試名稱講「守什麼」，不是講「測什麼函式」
 *   2. 每個 assert 旁邊寫「為什麼這條重要」，尤其是踩過的坑
 *   3. **不准出現真實 IP／主機名／公司名**。例子一律用 10.99.x、SECSVRxxx-nn
 *      這份檔案會經過 email 離開公司，這條是硬規則
 *   4. 測「錯的時候會怎樣」，不是只測快樂路徑
 */
import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { useSort } from '../composables/useSort'

interface Row { hostname: string | null; ip: string | null; n: number | null }

const rows = (v: Row[]) => ref(v)

describe('useSort', () => {
  it('沒指定排序欄時，原順序不動', () => {
    // 表格第一次載入時常常是後端已經排好的順序，前端不該擅自重排
    const src = rows([
      { hostname: 'b', ip: null, n: 1 },
      { hostname: 'a', ip: null, n: 2 },
    ])
    const { sorted } = useSort(src)
    expect(sorted.value.map((r) => r.hostname)).toEqual(['b', 'a'])
  })

  it('IP 要按數值排，不是按字串排', () => {
    // 字串排序會把 10.99.1.110 排在 10.99.1.9 前面（因為 '1' < '9'），
    // 看網段用量時整個順序都是錯的
    //
    // ⚠️ 2026-08-26 抽驗紀錄（照「沒看過它紅過的測試不算測試」那條做的）：
    // 把 useSort 裡那段專門處理 IP 的分支整個拿掉，**這條測試照樣是綠的**。
    // 原因是 localeCompare 的 numeric:true 對點分四段本來就排得對，
    // 所以那段 IP 分支在正確性上是**多餘的**（它比較快、比較明確，但不是唯一防線）。
    // 要兩條路都斷（IP 分支拿掉 + numeric:true 拿掉）這條才會紅。
    //
    // 所以這條守的是**行為**（IP 要排得對），不是守某一段實作。這樣沒問題，
    // 但不要以為它在保護那段 IP 程式碼——它沒有。
    const src = rows([
      { hostname: 'c', ip: '10.99.1.110', n: null },
      { hostname: 'a', ip: '10.99.1.9', n: null },
      { hostname: 'b', ip: '10.99.1.20', n: null },
    ])
    const { toggle, sorted } = useSort(src)
    toggle('ip')
    expect(sorted.value.map((r) => r.ip)).toEqual(['10.99.1.9', '10.99.1.20', '10.99.1.110'])
  })

  it('空值一律排最後，升冪降冪都一樣', () => {
    // 空白浮到最上面沒有任何資訊價值——降冪時「最大的」應該是有值的那些，
    // 不是一堆空白。這條刻意不隨方向翻面。
    const src = rows([
      { hostname: 'a', ip: null, n: 2 },
      { hostname: 'b', ip: null, n: null },
      { hostname: 'c', ip: null, n: 1 },
    ])
    const { toggle, sorted } = useSort(src)
    toggle('n')
    expect(sorted.value.map((r) => r.n)).toEqual([1, 2, null])
    toggle('n')                                   // 轉降冪
    expect(sorted.value.map((r) => r.n)).toEqual([2, 1, null])
  })

  it('同一欄再點一次換方向，換一欄則回到升冪', () => {
    const src = rows([{ hostname: 'a', ip: null, n: 1 }])
    const { sortKey, sortDir, toggle } = useSort(src)
    toggle('hostname')
    expect([sortKey.value, sortDir.value]).toEqual(['hostname', 'asc'])
    toggle('hostname')
    expect(sortDir.value).toBe('desc')
    toggle('n')
    expect([sortKey.value, sortDir.value]).toEqual(['n', 'asc'])   // 換欄要回升冪
  })

  it('中文帶數字要排得對（第2項在第10項前面）', () => {
    // localeCompare 的 numeric:true。少了它「第10項」會排在「第2項」前面
    const src = rows([
      { hostname: '第10項', ip: null, n: null },
      { hostname: '第2項', ip: null, n: null },
    ])
    const { toggle, sorted } = useSort(src)
    toggle('hostname')
    expect(sorted.value.map((r) => r.hostname)).toEqual(['第2項', '第10項'])
  })

  it('不會改動傳進來的陣列', () => {
    // sorted 是 computed，如果就地 sort 會把來源順序也改掉，
    // 那「取消排序回到原順序」就永遠做不到了
    const original = [
      { hostname: 'b', ip: null, n: 2 },
      { hostname: 'a', ip: null, n: 1 },
    ]
    const src = ref(original)
    const { toggle, sorted } = useSort(src)
    toggle('hostname')
    void sorted.value
    expect(original.map((r) => r.hostname)).toEqual(['b', 'a'])
  })
})
