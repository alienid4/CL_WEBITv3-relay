// 2.058.14 審查 P2-2：批次試密碼的摘要——不是 IPv4、VMware 擋下的不算「兩組都不對」。
import { describe, expect, it } from 'vitest'
import { probeLabel, probeSummary } from '../utils/batchProbeSummary'

describe('批次試密碼摘要', () => {
  it('全部都是無效輸入：兩組都不對＝0，另外列出不是 IPv4 的台數', () => {
    const s = probeSummary([
      { matched_password_index: null, invalid_ip: true },
      { matched_password_index: null, invalid_ip: true },
    ])
    expect(s).toEqual({ total: 2, matched: 0, unmatched: 0, invalid: 2, vmware: 0 })
  })
  it('混在一起：各算各的', () => {
    const s = probeSummary([
      { matched_password_index: 0 }, { matched_password_index: null },
      { matched_password_index: null, vmware: true }, { matched_password_index: null, invalid_ip: true },
    ])
    expect(s).toEqual({ total: 4, matched: 1, unmatched: 1, invalid: 1, vmware: 1 })
  })
  it('每列的標籤', () => {
    expect(probeLabel({ matched_password_index: null, invalid_ip: true })).toBe('只接受 IPv4')
    expect(probeLabel({ matched_password_index: null, vmware: true })).toBe('VMware 不登入')
    expect(probeLabel({ matched_password_index: 1 })).toBe('密碼 B')
    expect(probeLabel({ matched_password_index: null })).toBe('都不對')
  })
})
