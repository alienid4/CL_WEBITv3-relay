// 2.054.0（V20 第 3 批）：資產編輯的規則＝舊 pages/assets/[serial].vue startEdit／saveEdit（搬到 utils/assetEdit）。
import { describe, expect, it } from 'vitest'
import { buildDraft, editableKeys, saveFields, sections } from '../utils/assetEdit'

const fg = { hardware: { common: ['hostname', 'ip', 'asset_serial', 'asset_status', 'os'], advanced: ['remark', 'os'], people: ['custodian'], hardware_info: ['rack_no'], cia: ['confidentiality'] } }

describe('editableKeys', () => {
  it('五組聯集、去重、主鍵不給改', () => {
    expect(editableKeys(fg)).toEqual(['hostname', 'ip', 'asset_status', 'os', 'remark', 'custodian', 'rack_no', 'confidentiality'])
    expect(editableKeys(null)).toEqual([])
  })
})

describe('buildDraft／saveFields（資產狀態＝待 CIA 異動）', () => {
  const hw = { hostname: 'h1', ip: '10.99.1.1', asset_status: '使用中', pending_status: '停用', os: null }
  it('沒值帶空字串；有待異動帶待異動', () => {
    const d = buildDraft(hw, editableKeys(fg))
    expect(d.os).toBe('')
    expect(d.asset_status).toBe('停用')
    expect('asset_serial' in d).toBe(false)
  })
  it('資產狀態沒改就不送（不然會把待異動取消掉）', () => {
    const d = buildDraft(hw, editableKeys(fg))
    expect('asset_status' in saveFields(d, hw)).toBe(false)
    expect(saveFields({ ...d, asset_status: '報廢' }, hw).asset_status).toBe('報廢')
    expect(saveFields({ ...d, asset_status: '' }, { ...hw, pending_status: null, asset_status: null })).not.toHaveProperty('asset_status')
  })
  it('其他欄位照送（不改值也送，舊頁同一條）', () => {
    const d = buildDraft(hw, editableKeys(fg))
    expect(saveFields(d, hw)).toMatchObject({ hostname: 'h1', ip: '10.99.1.1', os: '', remark: '' })
  })
})

describe('sections（只影響排列）', () => {
  it('主機名稱／IP 在第一區、資產狀態另外放、每欄只出現一次、沒有主鍵', () => {
    const s = sections(fg)
    expect(s[0]).toMatchObject({ k: 'net', keys: ['hostname', 'ip'] })
    const all = s.flatMap(x => x.keys)
    expect(all).not.toContain('asset_status')
    expect(all).not.toContain('asset_serial')
    expect(new Set(all).size).toBe(all.length)
    // 每個可編輯欄位（資產狀態以外）都在某一區，不會有改不到的欄位
    expect(new Set(all)).toEqual(new Set(editableKeys(fg).filter(k => k !== 'asset_status')))
  })
})
