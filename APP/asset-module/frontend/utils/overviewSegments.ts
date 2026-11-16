// 系統總覽「全環境系統架構分布」圓環的分段（W221-014，Codex 32 號）：
// 圓環中心＝分段加總，所以分段必須含「未分類」，中心才會等於全環境總量；否則尚無分類表時三組全 0、
// 中心顯示 0，而同頁總量是 2535。純函式，vitest 直接測。
export interface OverviewCounts { total: number; core: number; noncore: number; test: number; uncategorized: number }
export interface Seg { name: string; count: number; color: string }

export const GROUP_COLOR: Record<string, string> = {
  核心交易: 'var(--brand-dark)', 非核心: 'var(--chart-2)', 測試: 'var(--warn-text)', 未分類: 'var(--chart-gray)',
}
export const GROUP_BUCKET: Record<string, string> = { 核心交易: 'core', 非核心: 'noncore', 測試: 'test', 未分類: 'uncategorized' }

export function groupSegments(d: OverviewCounts | null | undefined): Seg[] {
  if (!d) return []
  return [
    { name: '核心交易', count: d.core, color: GROUP_COLOR['核心交易'] },
    { name: '非核心', count: d.noncore, color: GROUP_COLOR['非核心'] },
    { name: '測試', count: d.test, color: GROUP_COLOR['測試'] },
    { name: '未分類', count: d.uncategorized ?? 0, color: GROUP_COLOR['未分類'] },
  ]
}

export function segmentsSum(segs: Seg[]): number {
  return segs.reduce((a, s) => a + (s.count || 0), 0)
}
