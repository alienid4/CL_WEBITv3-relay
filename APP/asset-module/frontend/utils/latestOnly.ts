// 「最新一次請求才算數」：route 快速切換時，舊請求晚回來不可覆蓋新畫面（Codex 79 併檢的失效保護）。
export function makeLatest() {
  let seq = 0
  return { begin: () => ++seq, isCurrent: (n: number) => n === seq }
}
