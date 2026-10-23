// 畫面上這張表（篩選、排序後）原樣匯出成 Excel（2026-10-22 使用者：「每個頁面都要有」搜尋＋匯出）。
// 後端 /api/export/table-xlsx 只轉格式、不查資料庫，所以匯出的就是使用者正在看的那幾列。
export function useTableExport() {
  const cfg = useRuntimeConfig()
  const busy = ref(false)
  async function exportTable(title: string, headers: string[], rows: unknown[][]) {
    if (busy.value) return
    busy.value = true
    try {
      const res = await fetch(`${cfg.public.apiBase}/api/export/table-xlsx`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, headers, rows }),
      })
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${title}.xlsx`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      busy.value = false
    }
  }
  return { exportTable, busy }
}
