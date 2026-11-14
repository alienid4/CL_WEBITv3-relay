// 相機掃條碼／QR（2.044.6，GPT B4）：用瀏覽器內建的 BarcodeDetector，不另外載入程式庫。
// 不支援（沒有 BarcodeDetector、沒有相機 API、或不是 https／localhost 的安全連線）就不顯示掃描按鈕——不顯示一個按了不會動的按鈕。
export function canScan(w: any = typeof window !== 'undefined' ? window : undefined): boolean {
  if (!w) return false
  return 'BarcodeDetector' in w && !!w.navigator?.mediaDevices?.getUserMedia && w.isSecureContext !== false
}

/** 掃到的字整理成可以放進欄位的樣子：去掉前後空白與換行；QR 內容是「key=value;…」時取 SN／序號那一段 */
export function scannedText(raw: string): string {
  const t = (raw || '').replace(/[\r\n]+/g, ' ').trim()
  const m = t.match(/(?:^|[;,&\s])(?:SN|S\/N|SERIAL|序號)\s*[:=]\s*([^;,&\s]+)/i)
  return m ? m[1] : t
}

/** 只認最新一次（2.044.7，GPT 複核 P1）：打字搜尋候選時，舊請求比新請求晚回來，不可以蓋掉新結果。 */
export function latestOnly() {
  let seq = 0
  return { next: () => ++seq, isLatest: (n: number) => n === seq }
}
