// 1.5 資料來源的 VMware：三個維度分開（今日收件／最近有效批次／最近一次執行），固定詞；登入失敗是「失敗」不是「只收到一半」。純函式，全假資料。
import { describe, expect, it } from 'vitest'
import { vmwareStatus, vmwareThree } from '../utils/dailycheckSources'

const lc = (o: any = {}) => ({ collected_at: '2026-11-19 06:00:00', age_min: 60, source: 'direct', ...o })

describe('vmwareThree', () => {
  it('今天窗內有完整批次：三欄都正常，並寫 1.5 採用哪一批', () => {
    const t = vmwareThree({ latest: { complete: true }, latest_complete: lc(), adopted: { batch_id: 'ariadc_direct_x' }, today: { complete: true, source: 'direct', collected_at: '2026-11-19 06:00:00' },
      direct: { enabled: true, last_run: { status: 'ok', at: '2026-11-19 06:03:00' } } })!
    expect(t.today.status).toBe('正常'); expect(t.today.text).toContain('ariadc_direct_x'); expect(t.fresh.status).toBe('正常'); expect(t.run.status).toBe('正常')
  })
  it('昨天有、今天沒有：今日收件「沒收到」、最近有效批次仍正常（兩個維度不混）', () => {
    const t = vmwareThree({ latest: { complete: true }, latest_complete: lc({ collected_at: '2026-11-18 06:00:00', age_min: 1500 }), today: { complete: false }, direct: { enabled: true, last_run: null } })!
    expect(t.today.status).toBe('沒收到'); expect(t.fresh.status).toBe('正常'); expect(t.run.status).toBe('沒收到')
  })
  it('登入被拒：最近一次執行是「失敗」；放棄當日寫「今天不會再自動重試」', () => {
    const t = vmwareThree({ latest: { complete: false, problems: ['vROps 登入失敗：x'] }, latest_complete: null, today: { complete: false },
      direct: { enabled: true, last_run: { status: 'incomplete', error_class: 'auth', at: '2026-11-19 06:00:00' }, auth_rejects_today: 3, auth_limit: 3, gave_up_today: { message: '登入被拒已達 3 次' } } })!
    expect(t.run.status).toBe('失敗'); expect(t.run.text).toContain('今天不會再自動重試'); expect(t.fresh.status).toBe('沒收到')
  })
  it('最近一次是登入被拒、之後關了開關：仍顯示「失敗」並另註開關關閉（不被「待設定」蓋掉）', () => {
    const t = vmwareThree({ latest: null, latest_complete: null, today: { complete: false },
      direct: { enabled: false, last_run: { status: 'incomplete', error_class: 'auth', at: '2026-11-19 06:00:00' } } })!
    expect(t.run.status).toBe('失敗'); expect(t.run.text).toContain('開關：關')
  })
  it('執行中是獨立旗標（不是第七種狀態詞）：看 direct.running，最後一列是上一輪成功也要標執行中', () => {
    const t = vmwareThree({ latest: null, latest_complete: null, today: { complete: false }, direct: { enabled: true, running: true, last_run: { status: 'ok', at: '2026-11-18 06:00:00' } } })!
    expect(t.running).toBe(true); expect(t.run.text).toContain('執行中'); expect(['正常', '過期', '失敗', '只收到一半', '沒收到', '待設定']).toContain(t.run.status)
    const t2 = vmwareThree({ latest: null, latest_complete: null, today: { complete: false }, direct: { enabled: true, running: false, last_run: { status: 'running', at: '2026-11-19 06:00:00' } } })!
    expect(t2.running).toBe(false)
  })
  it('今天的文字核對日期：昨天的不完整批次不能說成今天', () => {
    const t = vmwareThree({ latest: { complete: false, collected_at: '2026-11-18 06:00:00', problems: ['ESXi 主機：分頁不完整'] }, latest_complete: null, today: { complete: false, day: '2026-11-19' }, direct: { enabled: true } })!
    expect(t.today.text).not.toContain('今天只有不完整'); expect(t.today.text).toContain('沒有完整批次')
    const t2 = vmwareThree({ latest: { complete: false, collected_at: '2026-11-19 06:00:00', problems: ['ESXi 主機：分頁不完整'] }, latest_complete: null, today: { complete: false, day: '2026-11-19' }, direct: { enabled: true } })!
    expect(t2.today.text).toContain('今天只有不完整')
  })
  it('直抓沒開：最近一次執行「待設定」', () => {
    expect(vmwareThree({ latest: null, latest_complete: null, today: { complete: false }, direct: { enabled: false } })!.run.status).toBe('待設定')
  })
})

describe('vmwareStatus 07:45～08:00（直抓契約）', () => {
  const yday = { collected_at: '2026-11-18 06:00:00', age_min: 1500 }
  const mk = (direct: any) => ({ latest: { collected_at: yday.collected_at, complete: true, problems: [] }, latest_complete: yday, direct: { enabled: true, window: { cutoff: '08:00', last_start: '07:40' }, ...direct } })
  it('07:50 直抓還在執行：不標過期', () => {
    expect(vmwareStatus(mk({ running: true }), true, '2026-11-19', '07:50', true)!.status).toBe('正常')
  })
  it('07:50 還沒到採用截止：即使沒在跑也不標過期（舊的 07:45 線不再適用）', () => {
    expect(vmwareStatus(mk({}), true, '2026-11-19', '07:50', true)!.status).toBe('正常')
  })
  it('08:00 之後仍 running：不再抑制過期（running 只管採用截止之前）', () => {
    expect(vmwareStatus(mk({ running: true }), true, '2026-11-19', '09:00', true)!.status).toBe('過期')
    expect(vmwareStatus(mk({ running: true }), true, '2026-11-19', '08:00', true)!.status).toBe('過期')
    expect(vmwareStatus(mk({ running: true }), true, '2026-11-19', '07:59', true)!.status).toBe('正常')
  })
  it('08:00 之後仍沒有今天的批次且沒在跑：過期，並指向執行紀錄', () => {
    const s = vmwareStatus(mk({}), true, '2026-11-19', '08:00', true)!
    expect(s.status).toBe('過期'); expect(s.next).toContain('08:00'); expect(s.next).toContain('執行紀錄')
  })
  it('跳板機備援（直抓沒開）仍沿用 07:45 線', () => {
    expect(vmwareStatus(mk({ enabled: false }), true, '2026-11-19', '07:50', false)!.status).toBe('過期')
  })
})

describe('vmwareStatus', () => {
  it('登入失敗（沒有任何完整批次）＝失敗，不叫只收到一半', () => {
    const s = vmwareStatus({ latest: { collected_at: '2026-11-19 06:00:00', complete: false, problems: ['vROps 登入失敗：login failed'] }, latest_complete: null }, true)!
    expect(s.status).toBe('失敗')
  })
  it('其他不完整仍是只收到一半；直抓開啟時提示導向執行紀錄而不是跳板機', () => {
    const half = vmwareStatus({ latest: { collected_at: 'x', complete: false, problems: ['ESXi 主機：分頁不完整'] }, latest_complete: null }, true)!
    expect(half.status).toBe('只收到一半')
    const old = vmwareStatus({ latest: { collected_at: 'x', complete: true, problems: [] }, latest_complete: { collected_at: 'x', age_min: 3000 } }, true, '', '', true)!
    expect(old.status).toBe('過期'); expect(old.next).toContain('執行紀錄'); expect(old.next).not.toContain('跳板機')
  })
})
