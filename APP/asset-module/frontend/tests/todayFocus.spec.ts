/**
 * 1.1 今日概況焦點卡的判斷（utils/todayFocus.ts）。
 * 這些是 GPT 挑剔審核（2026-11-18）抓到的會誤判案例：標籤文字排序失效、黃燈畫成紅、
 * 算不出來卻顯示綠色「沒事」、休市／尚未匯入被算成「來源沒更新」。
 */
import { describe, expect, it } from 'vitest'
import { EXPECTED_SOURCE_STATES, focusChipText, focusTone, isStaleSource, relayAsSources, sortFocus, teamsStatus, tierRank, tierTone } from '../utils/todayFocus'

describe('分級排序', () => {
  it('🔴 red 排在 🟡 yellow 前面，不看標籤文字', () => {
    const rows = [{ tier: 'yellow', tier_label: '🟡 每日彙整', duration_min: 900 }, { tier: 'red', tier_label: '🔴 立即', duration_min: 10 }]
    expect(sortFocus(rows).map(r => r.tier)).toEqual(['red', 'yellow'])
  })
  it('同分級：第一類系統優先，再來斷線久的優先', () => {
    const rows = [
      { tier: 'red', c_sys_class: '第二類', duration_min: 500 },
      { tier: 'red', c_sys_class: '第一類', duration_min: 20 },
      { tier: 'red', c_sys_class: '第一類', duration_min: 90 },
    ]
    expect(sortFocus(rows).map(r => r.duration_min)).toEqual([90, 20, 500])
  })
  it('持續時間缺值排最後，不當成 0 分混進去', () => {
    const rows = [{ tier: 'red', duration_min: null }, { tier: 'red', duration_min: 5 }]
    expect(sortFocus(rows).map(r => r.duration_min)).toEqual([5, null])
  })
  it('分級顏色：red＝bad、yellow＝warn、其他不上色', () => {
    expect(tierTone({ tier: 'red' })).toBe('bad')
    expect(tierTone({ tier: 'yellow' })).toBe('warn')
    expect(tierTone({ tier: 'maint' })).toBe('')
    expect(tierRank({})).toBe(2)
  })
})

describe('焦點卡顏色（假綠燈）', () => {
  it('只有黃燈告警時不可畫成紅色', () => {
    expect(focusTone([{ tier: 'yellow' }], 'ok', { blind: null })).toBe('warn')
  })
  it('有 🔴 就紅', () => {
    expect(focusTone([{ tier: 'yellow' }, { tier: 'red' }], 'ok', { blind: null })).toBe('bad')
  })
  it('確定查過、0 件、沒有監控盲區才是綠', () => {
    expect(focusTone([], 'ok', { blind: null })).toBe('ok')
  })
  it('告警來源算不出來／未啟用／未收集時不可是綠', () => {
    for (const st of ['error', 'disabled', 'uncollected', 'stale']) expect(focusTone([], st, null)).toBe('warn')
  })
  it('監控中斷（blind）時不可是綠', () => {
    expect(focusTone([], 'ok', { blind: '監控資料中斷' })).toBe('warn')
  })
})

describe('資料來源狀態', () => {
  it('休市、尚未匯入是預期狀態，不算沒更新', () => {
    for (const s of EXPECTED_SOURCE_STATES) expect(isStaleSource({ state: s })).toBe(false)
    expect(isStaleSource({ state: null })).toBe(false)
  })
  it('資料中斷、來源未更新、無法計算算沒更新', () => {
    for (const s of ['資料中斷', '來源未更新', '無法計算', '未收集']) expect(isStaleSource({ state: s })).toBe(true)
  })
})

describe('焦點卡件數字', () => {
  it('群組數等於件數：只寫件', () => {
    expect(focusChipText(2, 2)).toBe('2 件')
    expect(focusChipText(4, 4)).toBe('前 3 件・另有 1 件')
  })
  it('群組數少於件數：改寫組，並講共幾件', () => {
    expect(focusChipText(2, 5)).toBe('2 組・共 5 件')
    expect(focusChipText(5, 9)).toBe('前 3 組・另有 2 組・共 9 件')
  })
})

describe('Teams 通知狀態（原本藏在設定頁）', () => {
  const on = { settings: { enabled: true } }
  it('讀取失敗：只標讀不到，不列待辦（可能是權限）', () => {
    const t = teamsStatus(null, '讀取失敗')
    expect(t.tone).toBe('dim'); expect(t.todo).toBeNull()
  })
  it('還沒讀完：確認中，不可顯示正常', () => {
    expect(teamsStatus(null, '').tone).toBe('wait')
  })
  it('未啟用不是問題', () => {
    expect(teamsStatus({ settings: { enabled: false } }, '').todo).toBeNull()
  })
  it('設定檔壞掉要列待辦', () => {
    expect(teamsStatus({ ...on, corrupt: '壞了' }, '').todo).toContain('設定檔有問題')
  })
  it('目前不會送出（hold）要列待辦並寫原因與待送數', () => {
    const t = teamsStatus({ ...on, hold: '對外送出尚未確認', queue: { pending: 4 } }, '')
    expect(t.tone).toBe('warn'); expect(t.todo).toContain('對外送出尚未確認'); expect(t.todo).toContain('待送 4 則')
  })
  it('有重試中或不確定的就是待辦，兩種數字加總', () => {
    const t = teamsStatus({ ...on, queue: { pending: 0, failed: 2, uncertain: 1 } }, '')
    expect(t.tone).toBe('bad'); expect(t.todo).toContain('3 則')
  })
  it('全部正常才是 ok，並帶待送數', () => {
    const t = teamsStatus({ ...on, queue: { pending: 0, failed: 0, uncertain: 0 } }, '')
    expect(t.tone).toBe('ok'); expect(t.todo).toBeNull(); expect(t.text).toContain('待送 0 則')
  })
})

describe('中繼匯入來源 → 1.1 資料來源', () => {
  const view = { sources: [
    { source: 'a', label: 'WhatsUp', implemented: true, status: 'ok', last_collected_at: '2026-11-18 08:30' },
    { source: 'b', label: 'Aria', implemented: true, status: 'stale', last_collected_at: '2026-11-15 16:10' },
    { source: 'c', label: '儲存', implemented: true, status: 'partial' },
    { source: 'd', label: '尚未支援', implemented: false, status: 'never' },
    { source: 'e', label: '從沒收到', implemented: true, status: 'never' },
  ] }
  it('只列已實作的來源，並用固定六種狀態用語', () => {
    const r = relayAsSources(view)
    expect(r.map(x => x.label)).toEqual(['WhatsUp・中繼', 'Aria・中繼', '儲存・中繼', '從沒收到・中繼'])
    expect(r.map(x => x.state)).toEqual([null, '過期', '只收到一半', '沒收到'])
  })
  it('過期、只收到一半、沒收到都算沒更新；正常不算', () => {
    expect(relayAsSources(view).filter(isStaleSource).length).toBe(3)
  })
  it('沒有資料時回空陣列', () => {
    expect(relayAsSources(null)).toEqual([])
  })
})
