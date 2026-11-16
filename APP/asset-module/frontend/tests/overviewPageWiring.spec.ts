import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// W221-014：總覽頁用到的函式必須真的有匯入或是 auto-import 的名稱（曾因 groupSegmentsOf 未定義，整頁空白）
describe('主機系統總覽頁接線', () => {
  it('groupSegmentsOf 有明確匯入且來源檔真的有 groupSegments', () => {
    const page = readFileSync('pages/reports/system-overview.vue', 'utf-8')
    const util = readFileSync('utils/overviewSegments.ts', 'utf-8')
    if (page.includes('groupSegmentsOf(')) {
      expect(page).toMatch(/import\s*\{[^}]*groupSegments\s+as\s+groupSegmentsOf[^}]*\}\s*from\s*'~\/utils\/overviewSegments'/)
      expect(util).toMatch(/export function groupSegments\b/)
    }
  })
})
