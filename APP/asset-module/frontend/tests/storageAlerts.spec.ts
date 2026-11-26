/** 5.2 容量預警臨時門檻（2.043.6，V71）：跟後端 storage_capacity.alerts 同一個判斷。 */
import { describe, expect, it } from 'vitest'
import { diskLevel, nearFullByRoom, parseThreshold, physLevelText, physListed, storageAlerts, usedFromFree } from '../utils/storageAlerts'

describe('storageAlerts', () => {
  const vols = [{ n: 'a', free_pct: 5 }, { n: 'b', free_pct: 20 }, { n: 'c', free_pct: null }]
  const phys = [{ n: 'p1', by: 'mount', free_pct: 8 }, { n: 'p2', by: 'vg', free_pct: 1 }, { n: 'p3', by: 'mount', free_pct: 30 }]
  it('Datastore 剩餘 < 門檻；實體主機只看檔案系統（VG 未配置空間不列）', () => {
    const r = storageAlerts(vols, phys, 15)
    expect(r.volumes.map(v => v.n)).toEqual(['a'])
    expect(r.physical.map(p => p.n)).toEqual(['p1'])
  })
  it('門檻調高只多列 Datastore；實體主機固定看使用率 80／90（R17）', () => {
    const r = storageAlerts(vols, phys, 40)
    expect(r.volumes.map(v => v.n)).toEqual(['a', 'b'])
    expect(r.physical.map(p => p.n)).toEqual(['p1'])
  })
  it('依機房的預警格＝同一門檻點進去的 Datastore 筆數（GPT 審查 1）', () => {
    const vs = [{ room: '甲', free_pct: 5 }, { room: '甲', free_pct: 25 }, { room: '乙', free_pct: 10 }, { room: '乙', free_pct: null }]
    expect(nearFullByRoom(vs, 15)).toEqual({ 甲: 1, 乙: 1 })
    expect(nearFullByRoom(vs, 30)).toEqual({ 甲: 2, 乙: 1 })
    const low = storageAlerts(vs, [], 30).volumes
    expect(low.filter(v => v.room === '甲').length).toBe(nearFullByRoom(vs, 30)['甲'])
  })
  it('磁碟分級邊界：79.9 不列、80.0 處理、90.0 高、查不到是未收集（R17）', () => {
    expect(diskLevel(79.9)).toBeNull()
    expect(diskLevel(80)).toBe('handle')
    expect(diskLevel(89.9)).toBe('handle')
    expect(diskLevel(90)).toBe('high')
    expect(diskLevel(null)).toBe('uncollected')
    expect(usedFromFree(20.1)).toBe(79.9)
    expect(usedFromFree(20)).toBe(80)
    expect(usedFromFree(null)).toBeNull()
    const r = storageAlerts([], [{ n: 'a', by: 'mount', free_pct: 20.1 }, { n: 'b', by: 'mount', free_pct: 20 }, { n: 'c', by: 'mount', free_pct: null }], 15)
    expect(r.physical.map(p => p.n)).toEqual(['b'])
  })
  it('實體主機磁碟頁的標示也用同一把尺（2.044.3 GPT A4）：不看 Datastore 門檻、VG 不判', () => {
    expect(physLevelText({ by: 'mount', free_pct: 20 })).toBe('處理（≥80%）')
    expect(physLevelText({ by: 'mount', free_pct: 10 })).toBe('高（≥90%）')
    expect(physLevelText({ by: 'mount', free_pct: 20.1 })).toBe('')
    expect(physLevelText({ by: 'mount', free_pct: null })).toBe('未收集')
    expect(physLevelText({ by: 'vg', free_pct: 1 })).toBe('')
    expect(physListed({ by: 'mount', free_pct: 20 })).toBe(true)
    expect(physListed({ by: 'mount', free_pct: 30 })).toBe(false)
  })
  it('門檻參數只收 1～99', () => {
    expect(parseThreshold('20')).toBe(20)
    expect(parseThreshold('0')).toBeNull()
    expect(parseThreshold('100')).toBeNull()
    expect(parseThreshold(undefined)).toBeNull()
    expect(parseThreshold('abc')).toBeNull()
  })
})
