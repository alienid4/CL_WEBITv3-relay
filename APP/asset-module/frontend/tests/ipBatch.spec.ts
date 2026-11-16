import { describe, expect, it } from 'vitest'
import { BATCH_MAX, filterBatchRows, overLimitText, previewLines, selectAllFiltered, selectedKeys } from '../utils/ipBatch'

const R = (k: string, who: string, why: string, code: string) => ({ item_key: k, who, why, mach: 'db-1、db-2', signals: [{ code }] })
const rows = [R('ip:192.0.2.1', '192.0.2.1', '同一個 IP 出現在多台', 'shared_ip'), R('mk:a', '', '型號是 F5', 'lb_model'), R('ip:192.0.2.9', '192.0.2.9', 'VIP', 'vip_shared')]

describe('filterBatchRows', () => {
  it('無條件＝全部', () => expect(filterBatchRows(rows, { text: '', signal: '' })).toHaveLength(3))
  it('依訊號', () => expect(filterBatchRows(rows, { text: '', signal: 'lb_model' }).map(r => r.item_key)).toEqual(['mk:a']))
  it('依文字不分大小寫', () => expect(filterBatchRows(rows, { text: 'F5', signal: '' })).toHaveLength(1))
  it('文字＋訊號是且', () => expect(filterBatchRows(rows, { text: '192.0.2.9', signal: 'shared_ip' })).toHaveLength(0))
})

describe('選取', () => {
  it('全選目前篩選結果＝篩選後全部，保留原有勾選', () => {
    const f = filterBatchRows(rows, { text: '', signal: 'shared_ip' })
    expect(selectAllFiltered(f, { 'mk:a': true })).toEqual({ 'mk:a': true, 'ip:192.0.2.1': true })
  })
  it('只回仍在清單內且有勾的', () => {
    expect(selectedKeys({ 'mk:a': true, 'ip:gone': true, 'ip:192.0.2.1': false }, rows)).toEqual(['mk:a'])
  })
})

describe('換篩選不留隱藏勾選', () => {
  it('篩選 A 全選 → 切到篩選 B：送出的只含 B 可見且被勾選者', () => {
    const a = filterBatchRows(rows, { text: '', signal: 'shared_ip' })
    const sel = selectAllFiltered(a)
    const b = filterBatchRows(rows, { text: '', signal: 'lb_model' })
    expect(selectedKeys(sel, b)).toEqual([])                    // 舊勾選在 B 裡全看不到 → 不送
    const sel2 = selectAllFiltered(b, sel)                     // 就算沒清掉，再按 B 全選
    expect(selectedKeys(sel2, b)).toEqual(['mk:a'])             // 仍只有 B 的可見項，不含 A 的隱藏勾選
  })
})

describe('上限', () => {
  it('500 以內沒提示、超過有提示', () => {
    expect(overLimitText(BATCH_MAX)).toBe('')
    expect(overLimitText(501)).toContain('500')
  })
})

describe('previewLines', () => {
  it('列出將判定、覆蓋、略過', () => {
    const t = previewLines({
      will_apply: 10, will_overwrite: 2, reopened: 1, skipped: 3, skipped_by_reason: { already_judged: 3 },
      skipped_reason_label: { already_judged: '已判定過（未勾覆蓋）' }, will_overwrite_by_verdict: { 資料錯誤: 2 },
    })
    expect(t).toEqual(['將判定 10 筆', '其中覆蓋已判定 2 筆（資料錯誤 2）', '其中重新出現的 1 筆', '略過 已判定過（未勾覆蓋） 3 筆'])
  })
})
