/**
 * 功能模組開關對照（2.043.4，docs/V2移植清單.md N18／N28）：
 * V2 頁面也要吃同一組開關；關掉時送 V2「功能模組管理」，不再送舊 /settings。
 */
import { describe, expect, it } from 'vitest'
import { disabledTarget, moduleForRoute } from '../utils/featureRoutes'

describe('moduleForRoute', () => {
  it('N28：V2 頁面對到跟舊網址同一個開關', () => {
    expect(moduleForRoute('/v2')).toBe('dashboard')
    expect(moduleForRoute('/v2/hosts')).toBe('assets')
    expect(moduleForRoute('/v2/machine/SN1')).toBe('assets')
    expect(moduleForRoute('/v2/inventory/service')).toBe('services')
    expect(moduleForRoute('/v2/inventory/software')).toBe('services')
    expect(moduleForRoute('/v2/inventory/matrix')).toBe('accounts')
    expect(moduleForRoute('/v2/system/N-218')).toBe('topology')
    expect(moduleForRoute('/v2/systems')).toBe('topology')
    expect(moduleForRoute('/v2/onboard')).toBe('adopt')
    expect(moduleForRoute('/v2/eos')).toBe('eos')
  })

  it('N28：只有某個頁籤屬於開關的頁，其他頁籤不受影響', () => {
    expect(moduleForRoute('/v2/datafix', { tab: 'quality' })).toBe('data_quality')
    expect(moduleForRoute('/v2/datafix', { tab: 'units' })).toBeNull()
    expect(moduleForRoute('/v2/audit', { tab: 'drift' })).toBe('golive')
    expect(moduleForRoute('/v2/audit', { tab: 'config' })).toBeNull()
    expect(moduleForRoute('/v2/settings', { st: 'import' })).toBe('import')
  })

  it('切開關的畫面本身不攔（不然關掉就回不去）', () => {
    expect(moduleForRoute('/v2/settings')).toBeNull()
    expect(moduleForRoute('/v2/settings', { st: 'dev', sub: 'modules', disabled: 'assets' })).toBeNull()
    expect(moduleForRoute('/settings')).toBeNull()
    expect(moduleForRoute('/login')).toBeNull()
  })

  it('舊網址照舊對到開關', () => {
    expect(moduleForRoute('/')).toBe('dashboard')
    expect(moduleForRoute('/assets/SN1')).toBe('assets')
    expect(moduleForRoute('/software')).toBe('services')
  })

  it('/v2/systems 與 /v2/system/ 不互相誤判、/v2/hostsX 不算 /v2/hosts', () => {
    expect(moduleForRoute('/v2/hostsX')).toBeNull()
    expect(moduleForRoute('/v2/manual')).toBeNull()
  })
})

describe('6.1 納管作業依頁籤歸屬（GPT 審查 2）', () => {
  it('掃描結果歸 dashboard、掃描缺口歸 report_system，其他頁籤歸 adopt', () => {
    expect(moduleForRoute('/v2/onboard', { tab: 'scan' })).toBe('dashboard')
    expect(moduleForRoute('/v2/onboard', { tab: 'gaps', gk: 'fw' })).toBe('report_system')
    expect(moduleForRoute('/v2/onboard', { tab: 'pending' })).toBe('adopt')
    expect(moduleForRoute('/v2/onboard')).toBe('adopt')
    expect(moduleForRoute('/v2/onboard', { tab: 'funnel' })).toBe('adopt')
  })
})

describe('disabledTarget', () => {
  it('GPT 審查 3：非管理員送 V2 首頁帶 disabled（首頁被關就送手冊），不落在看不到的頁籤', () => {
    expect(disabledTarget('assets', false, true)).toEqual({ path: '/v2', query: { disabled: 'assets' } })
    expect(disabledTarget('assets', false, false)).toEqual({ path: '/v2/manual', query: { disabled: 'assets' } })
    expect(disabledTarget('dashboard', false, true)).toEqual({ path: '/v2/manual', query: { disabled: 'dashboard' } })
    expect(moduleForRoute('/v2/manual', { disabled: 'dashboard' })).toBeNull()   // 不會轉圈
  })
  it('N18：管理員送 V2 功能模組管理', () => {
    expect(disabledTarget('assets')).toEqual({ path: '/v2/settings', query: { st: 'dev', sub: 'modules', disabled: 'assets' } })
    expect(moduleForRoute(disabledTarget('assets').path, disabledTarget('assets').query)).toBeNull()   // 不會轉圈
  })
})
