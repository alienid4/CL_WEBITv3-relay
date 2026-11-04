// 開發協作：逐指令進度（推導、舊格式遷移、匯出入）與任務篩選。純函式，無 DOM。
import { describe, expect, it } from 'vitest'
import { DEVKIT_TASKS, commandsAsText, filterTasks } from '../content/devkit'
import { cmdProg, emptyProgress, exportProgress, mergeProgress, migrateProgress, parseImport, setAll, setCmd, taskState } from '../utils/devkitProgress'

const D1 = DEVKIT_TASKS.find(t => t.id === 'D1')!
const D1b = DEVKIT_TASKS.find(t => t.id === 'D1b')!
const D2 = DEVKIT_TASKS.find(t => t.id === 'D2')!
const T0 = '2026-11-01T10:00:00.000Z'

describe('任務狀態由各條指令推導', () => {
  it('什麼都沒勾＝待收集，三條都缺', () => {
    expect(taskState(D1, emptyProgress())).toMatchObject({ status: '待收集', got: 0, total: 3, missing: [1, 2, 3], stale: false })
  })
  it('只勾已執行或只取得一部分＝進行中，並指出缺哪幾條', () => {
    let p = setCmd(emptyProgress(), D1, 0, { got: true }, T0)
    expect(taskState(D1, p)).toMatchObject({ status: '進行中', got: 1, missing: [2, 3] })
    p = setCmd(emptyProgress(), D1, 1, { ran: true }, T0)          // 只執行還沒取得輸出
    expect(taskState(D1, p)).toMatchObject({ status: '進行中', got: 0, missing: [1, 2, 3] })
  })
  it('全部取得輸出＝已收集；取消其中一條又回到進行中', () => {
    let p = setAll(emptyProgress(), D1, true, T0)
    expect(taskState(D1, p)).toMatchObject({ status: '已收集', got: 3, missing: [] })
    p = setCmd(p, D1, 2, { got: false }, T0)
    expect(taskState(D1, p)).toMatchObject({ status: '進行中', missing: [3] })
  })
  it('已取得輸出必然也算已執行；取消已執行會連已取得一起取消', () => {
    let p = setCmd(emptyProgress(), D1, 0, { got: true }, T0)
    expect(cmdProg(D1, p, 0)).toMatchObject({ ran: true, got: true })
    p = setCmd(p, D1, 0, { ran: false }, T0)
    expect(cmdProg(D1, p, 0)).toMatchObject({ ran: false, got: false })
  })
  it('備註存起來、截斷到 200 字，不改動傳入的 progress', () => {
    const p0 = emptyProgress()
    const p1 = setCmd(p0, D1, 1, { note: 'x'.repeat(300) }, T0)
    expect(p0.tasks).toEqual({})
    expect(cmdProg(D1, p1, 1).note).toHaveLength(200)
    expect(p1.updated).toBe(T0)
  })
  it('placeholder 任務沒有進度，狀態字用 waiting', () => {
    expect(taskState(D1b, emptyProgress())).toMatchObject({ status: '等第一步結果', total: 0 })
  })
})

describe('本機儲存格式遷移', () => {
  it('舊格式（單一勾選）遷成每條都已取得；不存在的任務與 false 忽略', () => {
    const p = migrateProgress({ D1: true, D2: false, ZZ: true, D1b: true }, DEVKIT_TASKS)
    expect(taskState(D1, p)).toMatchObject({ status: '已收集', got: 3 })
    expect(cmdProg(D1, p, 0).note).toContain('舊版進度')
    expect(p.tasks.D2).toBeUndefined()
    expect(p.tasks.ZZ).toBeUndefined()
    expect(p.tasks.D1b).toBeUndefined()
  })
  it('舊版勾過、但指令後來改版（D2 ver 2）：進度不採用，標 stale', () => {
    const p = migrateProgress({ D2: true }, DEVKIT_TASKS)
    expect(taskState(D2, p)).toMatchObject({ status: '待收集', got: 0, stale: true })
    // 使用者重新勾一條：從頭記，不帶舊的
    const p2 = setCmd(p, D2, 0, { got: true }, T0)
    expect(taskState(D2, p2)).toMatchObject({ status: '進行中', got: 1, stale: false })
  })
  it('新格式往返不變', () => {
    const p = setCmd(setAll(emptyProgress(), D1, true, T0), D2, 3, { ran: true, note: '45KB 10:31' }, T0)
    expect(migrateProgress(JSON.parse(JSON.stringify(p)), DEVKIT_TASKS)).toEqual(p)
  })
  it('壞資料（null、字串、陣列、型別不對）一律回空進度、不丟例外', () => {
    for (const bad of [null, undefined, 'x', 5, [], { v: 2, tasks: 'no' }, { v: 2, tasks: { D1: 7, D2: { ver: 'a', cmds: { x: 1, 0: { ran: 'yes' } } } } }]) {
      expect(() => migrateProgress(bad, DEVKIT_TASKS)).not.toThrow()
    }
    expect(migrateProgress(null, DEVKIT_TASKS)).toEqual(emptyProgress())
    const odd = migrateProgress({ v: 2, tasks: { D2: { ver: 'a', cmds: { x: 1, 0: { ran: 'yes', note: 5 } } } } }, DEVKIT_TASKS)
    expect(odd.tasks.D2.cmds).toEqual({ 0: { ran: false, got: false, note: '' } })
  })
})

describe('進度匯出／匯入', () => {
  it('匯出再匯入得到同樣進度（含 BOM 開頭的檔也能讀）', () => {
    const p = setCmd(emptyProgress(), D1, 0, { got: true, note: 'a.csv 3KB' }, T0)
    const text = exportProgress(p)
    const r = parseImport(text, DEVKIT_TASKS)
    expect(r.ok && r.p).toEqual(p)
    expect(parseImport('﻿' + text, DEVKIT_TASKS).ok).toBe(true)
  })
  it('舊格式檔可匯入；不是進度檔的會被拒絕並說明', () => {
    expect(parseImport('{"D1":true}', DEVKIT_TASKS).ok).toBe(true)
    for (const bad of ['not json', '[]', '{"hello":"world"}', '"x"']) {
      const r = parseImport(bad, DEVKIT_TASKS)
      expect(r.ok).toBe(false)
    }
    expect((parseImport('not json', DEVKIT_TASKS) as { err: string }).err).toContain('JSON')
  })
  it('合併：匯入檔裡有的任務以檔案為準，其他保留本機', () => {
    const local = setCmd(setCmd(emptyProgress(), D1, 0, { got: true }, T0), D2, 0, { got: true }, T0)
    const incoming = setAll(emptyProgress(), D1, true, T0)
    const m = mergeProgress(local, incoming, '2026-11-02T00:00:00.000Z')
    expect(taskState(D1, m).status).toBe('已收集')
    expect(taskState(D2, m).got).toBe(1)
    expect(m.updated).toBe('2026-11-02T00:00:00.000Z')
  })
})

describe('收集任務篩選', () => {
  const none = () => '待收集'
  it('無條件＝全部，順序（項次）不變', () => {
    expect(filterTasks(DEVKIT_TASKS, {}, none).map(t => t.id)).toEqual(['D1', 'D1b', 'D2', 'D1c', 'D1d', 'D1e', 'D3', 'D4', 'D5', 'D6', 'D7'])
  })
  it('依標籤', () => {
    expect(filterTasks(DEVKIT_TASKS, { tag: 'Unity' }, none).map(t => t.id)).toEqual(['D2', 'D3'])
    expect(filterTasks(DEVKIT_TASKS, { tag: 'WhatsUp' }, none).map(t => t.id)).toEqual(['D1', 'D1b', 'D1c', 'D1d', 'D4', 'D6', 'D7'])
    expect(filterTasks(DEVKIT_TASKS, { tag: '全部' }, none)).toHaveLength(11)
  })
  it('搜尋會看指令（含連線前檢查）與編號，不分大小寫', () => {
    expect(filterTasks(DEVKIT_TASKS, { q: 'UEMCLI' }, none).map(t => t.id)).toEqual(['D2'])
    expect(filterTasks(DEVKIT_TASKS, { q: '-version' }, none).map(t => t.id)).toEqual(['D2'])
    expect(filterTasks(DEVKIT_TASKS, { q: 'activemonitortype' }, none).map(t => t.id)).toEqual(['D1'])
    expect(filterTasks(DEVKIT_TASKS, { q: 'd1b' }, none).map(t => t.id)).toEqual(['D1b'])
    expect(filterTasks(DEVKIT_TASKS, { q: '不存在的字' }, none)).toEqual([])
  })
  it('依狀態：用傳進來的推導函式', () => {
    const p = setAll(emptyProgress(), D2, true, T0)
    const statusOf = (t: typeof D1) => (t.placeholder ? '待開發' : taskState(t, p).status)
    expect(filterTasks(DEVKIT_TASKS, { status: '已收集' }, statusOf).map(t => t.id)).toEqual(['D2'])
    expect(filterTasks(DEVKIT_TASKS, { status: '待收集' }, statusOf).map(t => t.id)).toEqual(['D1', 'D1c', 'D1d', 'D1e', 'D3', 'D4', 'D5', 'D6', 'D7'])
    expect(filterTasks(DEVKIT_TASKS, { status: '待開發' }, statusOf).map(t => t.id)).toEqual(['D1b'])
  })
  it('下載用文字含編號、位置、權限、連線前檢查與每條指令', () => {
    const t = commandsAsText(DEVKIT_TASKS)
    expect(t).toContain('=== D1 ')
    expect(t).toContain('=== D2 ')
    expect(t).not.toContain('=== D1b')
    expect(t).toContain('【連線前檢查 1】')
    expect(t).toContain('uemcli -output csv /sys/general show -detail')
  })
})
