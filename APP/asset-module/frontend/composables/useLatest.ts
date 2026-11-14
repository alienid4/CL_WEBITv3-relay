/**
 * 只套用「最新那一次」請求的結果（2.047.2，GPT 複核：快速切條件時，舊請求晚回來會把新結果蓋掉，
 * 畫面上的條件跟數字就對不起來）。
 *
 * 用法：
 *   const begin = useLatest()
 *   async function load() {
 *     const isLatest = begin()
 *     const r = await apiFetch(...)
 *     if (!isLatest()) return        // 已經有更新的請求出去了，這次的結果丟掉（錯誤也一樣丟掉）
 *     data.value = r
 *   }
 * 新寫的頁面，凡是「條件一改就重抓」的都照這個做。
 */
export function useLatest() {
  let seq = 0
  return function begin(): () => boolean {
    const mine = ++seq
    return () => mine === seq
  }
}
