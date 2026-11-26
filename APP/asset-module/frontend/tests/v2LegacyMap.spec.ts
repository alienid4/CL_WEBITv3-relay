/**
 * 舊網址 → V2（2.042.20 起 middleware 一律轉；使用者：「以後都不要再看到 V1 頁面」）。
 * 只測純函式 v2LegacyTarget；「V2 頁不准寫 V1 網址」的守門在 tests/asset_module/test_v2_no_v1_links.py。
 */
import { describe, expect, it } from 'vitest'
import { ASSET_TAB_TO_MACHINE, LEGACY_BYPASS, LEGACY_PATHS, v2LegacyTarget } from '../utils/v2LegacyMap'

describe('v2LegacyTarget', () => {
  it('摘要信的每日變化連結帶到 V2 每日異動的同一分類', () => {
    expect(v2LegacyTarget('/changes', { date: '2026-10-23', tab: 'added' }))
      .toEqual({ path: '/v2/changes', query: { tab: 'daily', date: '2026-10-23', cat: 'added' } })
    expect(v2LegacyTarget('/changes', { tab: 'mail-log' })).toEqual({ path: '/v2/settings', query: { st: 'notify', sub: 'digest' } })
  })

  it('資產清單：關鍵字、系統代號、重複登記各自帶到對的 V2 頁', () => {
    expect(v2LegacyTarget('/assets', { q: 'web01' })).toEqual({ path: '/v2/hosts', query: { q: 'web01' } })
    expect(v2LegacyTarget('/assets', { filter_field: 'api_id', filter_value: 'N-218' })).toEqual({ path: '/v2/system/N-218', query: {} })
    expect(v2LegacyTarget('/assets', { show: 'duplicates' })).toEqual({ path: '/v2/hosts', query: { tab: 'dups' } })
    expect(v2LegacyTarget('/assets/new')).toEqual({ path: '/v2/hosts', query: { new: '1' } })
    expect(v2LegacyTarget('/assets/SN1')).toEqual({ path: '/v2/machine/SN1', query: {} })
    expect(v2LegacyTarget('/assets/SN1', { edit: '1' })).toEqual({ path: '/v2/asset/SN1', query: { edit: '1' } })
  })

  it('N9：舊資產明細的頁籤不丟——看資料對到主機履歷的分組＋段落，編輯照帶 tab', () => {
    expect(v2LegacyTarget('/assets/SN1', { tab: 'accounts' })).toEqual({ path: '/v2/machine/SN1', query: { tab: 'sec', sec: 'accounts' } })
    expect(v2LegacyTarget('/assets/SN1', { tab: 'history' })).toEqual({ path: '/v2/machine/SN1', query: { tab: 'chg', sec: 'changes' } })
    expect(v2LegacyTarget('/assets/SN1', { tab: 'hwinfo' })).toEqual({ path: '/v2/machine/SN1', query: { tab: 'hw', sec: 'hw_spec' } })
    expect(v2LegacyTarget('/assets/SN1', { tab: 'advanced' })).toEqual({ path: '/v2/machine/SN1', query: { tab: 'overview' } })
    expect(v2LegacyTarget('/assets/SN1', { tab: 'nope' })).toEqual({ path: '/v2/machine/SN1', query: {} })
    expect(v2LegacyTarget('/assets/SN1', { edit: '1', tab: 'cia' })).toEqual({ path: '/v2/asset/SN1', query: { edit: '1', tab: 'cia' } })
  })

  it('N9：每個舊資產頁籤都有主機履歷對應', () => {
    const OLD = ['advanced', 'hwinfo', 'cia', 'personnel', 'software', 'services', 'accounts', 'vulns', 'audit', 'health', 'san', 'rebuild', 'schedule', 'history']
    for (const t of OLD) expect(ASSET_TAB_TO_MACHINE[t], t).toBeTruthy()
  })

  it('N23：/issues?show=all 帶著 show 到 V2 掃描異常', () => {
    expect(v2LegacyTarget('/issues', { show: 'all', type: '漏登記' })).toEqual({ path: '/v2/changes', query: { show: 'all', type: '漏登記', tab: 'issues' } })
  })

  it('N21：機櫃圖「開始盤點這櫃」的參數帶到 V2 機櫃盤點', () => {
    expect(v2LegacyTarget('/rack-audit', { start: '1', room: 'A', rack: '01' })).toEqual({ path: '/v2/inventory/rack', query: { start: '1', room: 'A', rack: '01' } })
  })

  it('嵌在 V2 設定裡的舊頁：參數整包帶過去，st 選頁籤', () => {
    expect(v2LegacyTarget('/contacts', { tab: 'todo', api: 'N-1' })).toEqual({ path: '/v2/settings', query: { tab: 'todo', api: 'N-1', st: 'contacts' } })
    expect(v2LegacyTarget('/settings', { tab: 'backup' })).toEqual({ path: '/v2/settings', query: { st: 'backup' } })
    expect(v2LegacyTarget('/settings', { disabled: 'eos' })).toEqual({ path: '/v2/settings', query: { st: 'dev', sub: 'modules', disabled: 'eos' } })
  })

  it('V1 的 tab 跟 V2 的 tab 意思不同時，以 V2 頁籤為準', () => {
    expect(v2LegacyTarget('/drift', { tab: 'x' })).toEqual({ path: '/v2/audit', query: { tab: 'drift' } })
    expect(v2LegacyTarget('/san')).toEqual({ path: '/v2/collect', query: { tab: 'san' } })
  })

  it('動態路徑、結尾斜線', () => {
    expect(v2LegacyTarget('/incident/12/')).toEqual({ path: '/v2/incident/12', query: {} })
    expect(v2LegacyTarget('/blast/checklist/7')).toEqual({ path: '/v2/checklist/7', query: {} })
    expect(v2LegacyTarget('/topology', { system: 'N-218' })).toEqual({ path: '/v2/system/N-218', query: {} })
    expect(v2LegacyTarget('/lifecycle')).toEqual({ path: '/v2/onboard', query: { tab: 'life' } })
    expect(v2LegacyTarget('/features', { q: 'ping' })).toEqual({ path: '/v2/manual', query: { view: 'features', fq: 'ping' } })
    expect(v2LegacyTarget('/data-fix', { tab: 'systems' })).toEqual({ path: '/v2/datafix', query: { ub: 'sys', tab: 'units' } })
    expect(v2LegacyTarget('/blast', { q: '10.99.1.1', mode: 'incident' })).toEqual({ path: '/v2/incident', query: { tab: 'impact', iq: '10.99.1.1' } })
    // A1（2.043.11）：mode／port／view／system 不丟
    expect(v2LegacyTarget('/blast', { q: 'web01', mode: 'probe', port: '1521' })).toEqual({ path: '/v2/incident', query: { tab: 'impact', iq: 'web01', mode: 'probe', port: '1521' } })
    expect(v2LegacyTarget('/blast', { view: 'map', system: 'N-218' })).toEqual({ path: '/v2/system/N-218', query: { tab: 'deps' } })
    expect(v2LegacyTarget('/blast', { system: 'N-218', mode: 'planned' })).toEqual({ path: '/v2/incident', query: { tab: 'impact', iq: 'N-218' } })
  })

  it('不轉的：V2 本身、登入、API、沒有 V2 對應的', () => {
    for (const p of ['/v2', '/v2/hosts', '/v2/print/labels', '/login', '/api/assets', '/'])
      expect(v2LegacyTarget(p)).toBeNull()
  })

  it('列印頁（V59／V60，2.047.2）搬到 /v2/print/，查詢參數照帶', () => {
    expect(v2LegacyTarget('/print/labels', { room: 'R1', rack: 'A01' })).toEqual({ path: '/v2/print/labels', query: { room: 'R1', rack: 'A01' } })
    expect(v2LegacyTarget('/print/rack-sheet', { room: 'R1' })).toEqual({ path: '/v2/print/rack-sheet', query: { room: 'R1' } })
  })

  it('legacy 參數不會被帶進 V2 網址', () => {
    expect(v2LegacyTarget('/account-matrix', { col: 'sudo', [LEGACY_BYPASS]: '1' })).toEqual({ path: '/v2/inventory/matrix', query: { col: 'sudo' } })
  })

  it('表裡每一條都轉到 /v2', () => {
    for (const p of LEGACY_PATHS) expect(v2LegacyTarget(p)?.path.startsWith('/v2')).toBe(true)
  })
})
