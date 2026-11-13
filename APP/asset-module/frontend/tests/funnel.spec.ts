// 2.054.1（批 18 審查 P1）：6.1 納管漏斗——釘住「送出去的是哪幾台」與篩選規則（utils/funnel）。
import { describe, expect, it } from 'vitest'
import { cardGap, filterFunnel, ipsToSend, pickStagePatch, pruneSelection, rowsBefore, selectAll, selectableRows, stageOnlyQuery, type FunnelFilter, type FunnelRow } from '../utils/funnel'

const row = (ip: string | null, stage: string, extra: Partial<FunnelRow> = {}): FunnelRow => ({
  ip, hostname: `h-${ip}`, asset_serial: `S-${ip}`, environment: '正式', env_group: 'prod', physical_location: '甲機房',
  os: 'RHEL 9', os_type: 'Linux', stage, stage_label: stage, collect_ok: 0, onboard_block: null, onboard_exempt: false, ...extra,
})
const items: FunnelRow[] = [
  row('10.99.0.1', 'unonboarded'), row('10.99.0.2', 'unonboarded', { os_type: 'AIX' }), row('10.99.0.3', 'lost'),
  row('10.99.0.4', 'complete', { collect_ok: 1 }), row('10.99.0.5', 'retired'), row(null, 'unonboarded'),
  row('10.99.0.6', 'unonboarded', { env_group: 'test', physical_location: '乙機房' }),
]
const base: FunnelFilter = { todoStages: ['unonboarded', 'lost'], onlyTodo: true, stage: '', stageSet: [], env: [], loc: [], os: [], kw: '' }
const ips = (rs: FunnelRow[]) => rs.map(r => r.ip)

describe('filterFunnel（舊頁同一套）', () => {
  it('預設只看還需要處理的', () => {
    expect(ips(filterFunnel(items, base))).toEqual(['10.99.0.1', '10.99.0.2', '10.99.0.3', null, '10.99.0.6'])
  })
  it('下鑽的關卡組有套上（一個數字跨好幾關）', () => {
    const f = { ...base, onlyTodo: false, stageSet: ['complete', 'retired'] }
    expect(ips(filterFunnel(items, f))).toEqual(['10.99.0.4', '10.99.0.5'])
  })
  it('關卡、環境、機房、OS、關鍵字都會收斂', () => {
    expect(ips(filterFunnel(items, { ...base, stage: 'lost' }))).toEqual(['10.99.0.3'])
    expect(ips(filterFunnel(items, { ...base, env: ['test'] }))).toEqual(['10.99.0.6'])
    expect(ips(filterFunnel(items, { ...base, loc: ['乙機房'] }))).toEqual(['10.99.0.6'])
    expect(ips(filterFunnel(items, { ...base, os: ['AIX'] }))).toEqual(['10.99.0.2'])
    expect(ips(filterFunnel(items, { ...base, kw: '99.0.3' }))).toEqual(['10.99.0.3'])
  })
  it('rowsBefore 只套這一欄以外的條件', () => {
    expect(ips(rowsBefore(items, { ...base, env: ['test'] }, 'env')).length).toBe(5)
    expect(ips(rowsBefore(items, { ...base, loc: ['乙機房'] }, 'loc')).length).toBe(5)
  })
})

describe('勾選與送出', () => {
  const filtered = filterFunnel(items, { ...base, stage: 'unonboarded' })   // 0.1 0.2 null 0.6
  it('能勾的＝目前篩選結果裡可納管的（不是全部）', () => {
    expect(ips(selectableRows(filtered))).toEqual(['10.99.0.1', '10.99.0.2', '10.99.0.6'])
    expect(ips(selectableRows(filtered))).not.toContain('10.99.0.3')
  })
  it('勾選這關 N 台＝把篩選結果裡能勾的全勾上，送出的就是那 N 台', () => {
    const s = selectAll({}, filtered)
    expect(ipsToSend(s, filtered)).toEqual(['10.99.0.1', '10.99.0.2', '10.99.0.6'])
  })
  it('送出去的＝已勾、而且在目前篩選結果裡的（換了篩選，看不到的不送）', () => {
    const s = { '10.99.0.1': true, '10.99.0.3': true, '10.99.0.6': true }
    expect(ipsToSend(s, filtered)).toEqual(['10.99.0.1', '10.99.0.6'])
    expect(ipsToSend({ '10.99.0.4': true }, filterFunnel(items, { ...base, onlyTodo: false }))).toEqual([])   // 已收得到的不能送
  })
  it('換篩選：看不到的勾選丟掉', () => {
    const s = selectAll({}, filtered)
    const narrowed = filterFunnel(items, { ...base, stage: 'unonboarded', os: ['AIX'] })
    expect(pruneSelection(s, narrowed)).toEqual({ '10.99.0.2': true })
  })
})

describe('點關卡卡片（pickStagePatch）', () => {
  const base2 = { current: '', osInUrl: false, drillOs: [] as string[], todoStages: ['unonboarded', 'lost'] }
  it('下鑽帶的 OS 條件會保留（網址還沒有 os_type 時寫進網址）；網址已有就不動', () => {
    expect(pickStagePatch({ ...base2, k: 'lost', drillOs: ['AIX'] })).toEqual({ stage: 'lost', drill: undefined, os_type: 'AIX' })
    expect(pickStagePatch({ ...base2, k: 'lost', drillOs: ['AIX'], osInUrl: true })).toEqual({ stage: 'lost', drill: undefined })
  })
  it('不在待處理的關卡會關掉「只看還需要處理的」；再點同一張＝取消', () => {
    expect(pickStagePatch({ ...base2, k: 'retired' })).toMatchObject({ stage: 'retired', ft: '0' })
    expect(pickStagePatch({ ...base2, k: 'lost' })).not.toHaveProperty('ft')
    expect(pickStagePatch({ ...base2, current: 'retired', k: 'retired' })).toEqual({ stage: undefined, drill: undefined })
  })
})

describe('卡片數字 vs 清單（cardGap／stageOnlyQuery）', () => {
  it('有其他篩選或台數不同就講出來；沒有就不講', () => {
    expect(cardGap({ stage: 'complete', counts: { complete: 9 }, filtered: 3, others: true })).toEqual({ gap: true, card: 9 })
    expect(cardGap({ stage: 'complete', counts: { complete: 9 }, filtered: 9, others: true })).toEqual({ gap: true, card: 9 })
    expect(cardGap({ stage: 'complete', counts: { complete: 9 }, filtered: 0, others: false })).toEqual({ gap: true, card: 9 })
    expect(cardGap({ stage: 'complete', counts: { complete: 9 }, filtered: 9, others: false }).gap).toBe(false)
    expect(cardGap({ stage: '', counts: {}, filtered: 3, others: true }).gap).toBe(false)
  })
  it('清除其他篩選＝只留關卡（不在待處理的關卡帶 ft=0）', () => {
    expect(stageOnlyQuery('complete', ['lost'])).toEqual({ tab: 'funnel', stage: 'complete', ft: '0' })
    expect(stageOnlyQuery('lost', ['lost'])).toEqual({ tab: 'funnel', stage: 'lost' })
  })
})
