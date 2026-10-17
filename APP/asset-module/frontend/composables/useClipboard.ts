/**
 * 複製到剪貼簿，**在 http 環境也要能用**。
 *
 * ## 為什麼需要這支
 *
 * `navigator.clipboard` 只在「安全環境」存在——HTTPS 或 localhost。
 * 這套系統實際是走 `http://<內網IP>:3000` 開的，所以那個 API **根本不存在**，
 * 直接呼叫會丟 TypeError，而且多數呼叫端沒有 catch → 按鈕按下去什麼都沒發生，
 * 沒有錯誤、沒有提示，使用者只會覺得「壞了」。
 *
 * 2026-09-08 使用者回報「加密金鑰點選不能複製」才發現，而且一查全站有六處
 * 都這樣寫——包括納管的「複製安裝腳本」「複製一行指令」。也就是說那些按鈕
 * 從上線到現在**在這個環境從來沒有成功過**。
 *
 * ## 兩層退路
 *
 * 1. `navigator.clipboard`（HTTPS／localhost 時走這條）
 * 2. 隱藏 textarea ＋ `document.execCommand('copy')`——老方法，但 http 可用
 *
 * 兩條都失敗就**回 false**，呼叫端要把內容顯示出來讓人自己選取複製，
 * 不可以假裝成功。
 */
export function useClipboard() {
  async function copy(text: string): Promise<boolean> {
    if (!text) return false

    // 1) 安全環境才有的正規做法
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        return true
      }
    } catch {
      // 權限被拒或環境不支援 → 往下走退路，不要在這裡放棄
    }

    // 2) http 環境的退路。execCommand 已標記為過時，但在非安全環境
    //    它是唯一還能用的路——過時總比按了沒反應好。
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      // 不能用 display:none／visibility:hidden——那樣選取不到，複製會失敗
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.top = '-1000px'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      ta.setSelectionRange(0, text.length)   // iOS 需要這一行才選得到
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }

  return { copy }
}
