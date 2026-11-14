// 我的最愛拖拉排序（Codex 1223）：側欄只畫「目錄裡找得到」的那幾個，保存的清單可能還夾著目錄沒有的路徑
// （功能下架、權限看不到）。拖拉要用「路徑」在完整清單裡找位置，不能拿畫面上的第幾個去切完整清單——
// 以前 [/隱藏, /a, /b] 把畫面第 2 個（/b）拖到第 1 個（/a），完整清單裡動的是 /a，結果順序沒變。
// 看不到的路徑原地保留（刪不刪不在這裡決定）。
export function moveFavByRoute(list: string[], fromTo: string, toTo: string): string[] {
  const from = list.indexOf(fromTo)
  const to = list.indexOf(toTo)
  if (from < 0 || to < 0 || from === to) return list
  const arr = [...list]
  const [x] = arr.splice(from, 1)
  arr.splice(to, 0, x)
  return arr
}
