// 1.3 設備風險：逾期未重開機頁籤跟著 track_uptime_enabled（使用者 11-14 預設關）
import { expect, it } from 'vitest'
import { pickRiskTab, riskTabKeys } from '../utils/riskTabs'

it('關著沒有逾期未重開機頁籤，預設頁籤改硬體異常', () => {
  expect(riskTabKeys(false)).toEqual(['hardware', 'spof', 'contract', 'hk'])
  expect(pickRiskTab('uptime', false)).toBe('hardware')
  expect(pickRiskTab('', false)).toBe('hardware')
  expect(pickRiskTab('spof', false)).toBe('spof')
})
it('開著（或舊後端沒回欄位）照舊', () => {
  expect(riskTabKeys(true)[0]).toBe('uptime')
  expect(pickRiskTab('', undefined)).toBe('uptime')
  expect(pickRiskTab('uptime', true)).toBe('uptime')
})
