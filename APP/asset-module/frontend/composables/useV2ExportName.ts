// 2.x 表格匯出的檔名：頁名＋頁籤＋日期（2026-10-27 使用者：「查詢後想匯出，系統無此功能；我要說一次你才幫我加一次」
// → 每張表一律可匯出，檔名不用各頁自己想）。頁名取選單（useNavV2），不在選單的頁（主機履歷、系統履歷、單一事件）用路徑判。
import { V2_NAV } from '~/composables/useNavV2'

export function v2PageName(path: string, params: Record<string, any> = {}): string {
  for (const g of V2_NAV) {
    for (const it of g.items) {
      const ms = it.match ?? [it.to]
      if (ms.some(m => (m === '/v2' ? path === '/v2' || path === '/v2/' : path === m || path.startsWith(m + '/')))) {
        if (path.startsWith('/v2/machine/')) return `主機履歷 ${params.key ?? ''}`.trim()
        if (path.startsWith('/v2/system/')) return `系統履歷 ${params.id ?? ''}`.trim()
        return `${it.no} ${it.label}`
      }
    }
  }
  if (path.startsWith('/v2/incident/')) return `事件 ${params.id ?? ''}`.trim()
  return '匯出'
}

export function useV2ExportName() {
  const route = useRoute()
  const tab = inject<Ref<string> | null>('v2TabLabel', null)
  return (extra?: string) => {
    const d = new Date()
    const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
    return [v2PageName(route.path, route.params as any), tab?.value || '', extra || '', ymd].filter(Boolean).join('_').replace(/[\\/:*?"<>|\s]+/g, '_')
  }
}
