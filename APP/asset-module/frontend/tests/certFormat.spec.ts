/**
 * 憑證管理（9.3）前端純函式與頁面守則。
 * 例子一律虛構（example.test、虛構人名）。
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  CERT_STATUS_KEYS, certChangeSummary, certDecisions, certFmtUtc8, certLocalToApi, certStatusRank, certStatusTone, certUnresolved,
} from '../composables/certFormat'

describe('時間：UTC 存、UTC+8 顯示並明示時區', () => {
  it('到期當日 00:10 UTC 顯示為 08:10 (UTC+8)，跨日也對', () => {
    expect(certFmtUtc8('2026-11-01T00:10:00Z')).toBe('2026-11-01 08:10 (UTC+8)')
    expect(certFmtUtc8('2026-12-31T16:30:00Z')).toBe('2027-01-01 00:30 (UTC+8)')
  })
  it('空值與壞值回空字串，不丟例外', () => {
    expect(certFmtUtc8(null)).toBe('')
    expect(certFmtUtc8('')).toBe('')
    expect(certFmtUtc8('不是時間')).toBe('')
  })
  it('datetime-local 轉後端格式', () => {
    expect(certLocalToApi('2026-11-30T23:59')).toBe('2026-11-30 23:59')
    expect(certLocalToApi('')).toBe('')
  })
})

describe('狀態排序與顏色', () => {
  it('依嚴重度排：已過期最前、無資料最後', () => {
    const ks = [...CERT_STATUS_KEYS].sort((a, b) => certStatusRank(b) - certStatusRank(a))
    expect(ks[0]).toBe('none')
    expect(certStatusRank('expired')).toBe(0)
    expect(certStatusRank('不認得')).toBe(CERT_STATUS_KEYS.length)
  })
  it('顏色語意', () => {
    expect(certStatusTone('expired')).toBe('bad')
    expect(certStatusTone('d7')).toBe('bad')
    expect(certStatusTone('d30')).toBe('warn')
    expect(certStatusTone('d60')).toBe('warn')
    expect(certStatusTone('ok')).toBe('ok')
    expect(certStatusTone('none')).toBe('none')
  })
})

describe('匯入預覽：變更摘要不外洩人名', () => {
  it('人名欄位只寫「有更新」，其他欄位顯示新舊值', () => {
    const s = certChangeSummary([
      { field: 'req_unit', old: '營運部', new: '資訊處' },
      { field: 'req_contact', old: '洪小明', new: '林大華' },
      { field: 'planned_text', old: '', new: '2026-12-05 22:00' },
      { field: 'planned_at_utc', old: null, new: '2026-12-05T14:00:00Z' },
    ])
    expect(s).toContain('申請單位：營運部 → 資訊處')
    expect(s).toContain('申請窗口：有更新')
    expect(s).not.toContain('洪小明')
    expect(s).not.toContain('林大華')
    expect(s).toContain('預排更換時間：（空） → 2026-12-05 22:00')
    expect(s).not.toContain('結構化')
  })
})

describe('匯入衝突的選擇', () => {
  const rows = [
    { row_no: 3, status: 'new' },
    { row_no: 4, status: 'conflict', candidates: [{ id: 'aaa' }, { id: 'bbb' }] },
    { row_no: 5, status: 'conflict', candidates: [] },
    { row_no: 6, status: 'update' },
  ]
  it('沒選的衝突列算未決', () => {
    expect(certUnresolved(rows, {})).toBe(2)
    expect(certUnresolved(rows, { '4': 'skip' })).toBe(1)
    expect(certUnresolved(rows, { '4': 'skip', '5': 'new' })).toBe(0)
  })
  it('decisions 只收衝突列、且選項必須合法（skip／new／該列候選 id）', () => {
    const out = certDecisions(rows, { '3': 'new', '4': 'bbb', '5': 'aaa', '6': 'skip' })
    expect(out).toEqual({ '4': 'bbb' })        // 3、6 不是衝突列；5 的 aaa 不是它的候選
    expect(certDecisions(rows, { '4': 'skip', '5': 'new' })).toEqual({ '4': 'skip', '5': 'new' })
  })
})

describe('頁面守則（讀原始碼）', () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf-8')
  const idx = read('../pages/v2/certs/index.vue')
  const det = read('../pages/v2/certs/[id].vue')
  it('沒有任何更換／探測按鈕（第一階段只管理與查詢）', () => {
    for (const src of [idx, det]) {
      expect(src).not.toMatch(/一鍵更換|自動更換|立即更換|開始探測|探測一次/)
    }
  })
  it('頁首固定標示：不寄通知、不建待辦、不保證即時探測', () => {
    expect(idx).toContain('本系統不寄送通知、不建立待辦、不保證即時探測；請依既有作業追蹤更換。')
    expect(det).toContain('本系統不寄送通知、不建立待辦、不保證即時探測；請依既有作業追蹤更換。')
  })
  it('清單用共用 V2Table（可排序、有項次、可匯出），欄位不含人名欄', () => {
    expect(idx).toContain(':rows="rows" :cols="cols"')
    expect(idx).toContain(':export-all="exportAllRows"')                     // 匯出＝目前篩選結果（全部頁）
    const colsBlock = idx.slice(idx.indexOf('const cols = computed'), idx.indexOf('// 清單上直接改更換進度'))
    for (const k of ['req_contact', 'ap_contact', 'infra_owner', 'ap_tester']) expect(colsBlock).not.toContain(k)
  })
  it('不用「提醒天數」這個名字（它不會提醒）', () => {
    expect(idx + det).not.toContain('提醒天數')
  })
})
