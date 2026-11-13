// 2.059.3（V65 收尾）：5.1 批次標記現場盤點結果的預覽（⚠D4）。只送目前清單看得到的勾選；預覽講清楚幾台、現在各是什麼、幾台不變。全假資料。
import { describe, expect, it } from 'vitest'
import { BULK_LABEL, bulkPreview, stateText } from '../utils/rackBulk'

const DEV: Record<string, any> = {
  a: { key: 'a', kind: 'rack', state: 'active', state_label: '在用', override: null },
  b: { key: 'b', kind: 'rack', state: 'off', state_label: '關機未下架', override: 'off' },
  c: { key: 'c', kind: 'removed', state: null, state_label: null, override: 'removed' },
  d: { key: 'd', kind: 'rack', state: 'pending', state_label: '待確認', override: null },
}

describe('批次標記預覽', () => {
  it('狀態文字跟清單同一個寫法', () => {
    expect(stateText(DEV.c)).toBe('已不在櫃上'); expect(stateText(DEV.a)).toBe('在用'); expect(stateText({})).toBe('—')
    expect(BULK_LABEL.removed).toBe('標記已不在櫃上')
  })
  it('標記關機未下架：算出現況分布、本來就是的幾台、已有人工標記的幾台', () => {
    const p = bulkPreview('off', ['a', 'b', 'c'], ['a', 'b', 'c', 'd'], DEV)
    expect(p.total).toBe(3)
    expect(p.keys).toEqual(['a', 'b', 'c'])
    expect(p.unchanged).toBe(1)          // b 本來就是 off
    expect(p.manual).toBe(2)             // b、c 有人工標記
    expect(p.byState).toEqual([{ label: '在用', n: 1 }, { label: '關機未下架', n: 1 }, { label: '已不在櫃上', n: 1 }])
  })
  it('清除標記：沒有人工標記的算不變；看不到（換了清單）的勾選不送；重複勾只算一次', () => {
    const p = bulkPreview('clear', ['a', 'a', 'd', 'b', 'zz'], ['a', 'b', 'd'], DEV)
    expect(p.keys).toEqual(['a', 'd', 'b'])
    expect(p.unchanged).toBe(2)
    expect(p.missing).toBe(0)
  })
  it('清單有、資料找不到的：算 missing 但仍照舊頁行為送出（後端判斷）', () => {
    const p = bulkPreview('removed', ['x'], ['x'], DEV)
    expect(p).toMatchObject({ total: 1, missing: 1, unchanged: 0 })
  })
})

describe('勾選一變，預覽就作廢（2.059.5 審查）', () => {
  it('預覽後多勾一台／取消一台：預覽清掉，要重新按標記', async () => {
    const { nextTick } = await import('vue')
    const { useBulkSelection } = await import('../utils/rackBulk')
    const s = useBulkSelection()
    s.picked.value = { a: true, b: true }
    await nextTick()
    s.prev.value = bulkPreview('off', s.keys.value, ['a', 'b', 'c'], DEV)
    expect(s.prev.value!.total).toBe(2)
    s.picked.value = { ...s.picked.value, c: true }
    await nextTick()
    expect(s.prev.value).toBeNull()
    s.prev.value = bulkPreview('off', s.keys.value, ['a', 'b', 'c'], DEV)
    s.picked.value = { ...s.picked.value, a: false }
    await nextTick()
    expect(s.prev.value).toBeNull()
  })
})
