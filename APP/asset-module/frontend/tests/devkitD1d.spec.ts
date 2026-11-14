// D1d（備用的 WhatsUp SQL 探測）的查詢必須對正式 WhatsUp 資料庫友善：不掃整表、不鎖表、不傳回全部欄位、限日期與少量裝置。
import { describe, expect, it } from 'vitest'
import { DEVKIT_TASKS } from '../content/devkit'

const d1c = DEVKIT_TASKS.find(t => t.id === 'D1d')!

describe('D1d 查詢對正式庫友善', () => {
  it('任何一條都沒有 COUNT(*)（掃整表）；筆數由第 2 條的 partition 統計提供', () => {
    for (const c of d1c.commands) expect(c.text).not.toMatch(/COUNT\s*\(\s*\*\s*\)/i)
  })
  it('讀到使用者資料表的查詢一律 WITH (NOLOCK) 並先 SET LOCK_TIMEOUT 5000', () => {
    const userTable = d1c.commands.filter(c => /PerfTableName/.test(c.text))
    expect(userTable.length).toBe(2)
    for (const c of userTable) {
      expect(c.text).toMatch(/^SET LOCK_TIMEOUT 5000;/)
      expect(c.text).toMatch(/PerfTableName WITH \(NOLOCK\)/)
    }
  })
  it('抽樣那條：明確欄位清單（不用 *）、限單一裝置、限最近幾天、最多 20 筆', () => {
    const c = d1c.commands[3].text
    expect(c).not.toMatch(/SELECT\s+(TOP\s+\d+\s+)?\*/i)
    expect(c).toMatch(/TOP 20 /)
    expect(c).toMatch(/DeviceIdColumn = 123/)
    expect(c).toMatch(/DATEADD\(day, -3, GETDATE\(\)\)/)
  })
  it('改了指令文字，任務版本號有加（舊進度會顯示指令已更新）', () => {
    expect(d1c.ver).toBeGreaterThanOrEqual(2)
  })
})
