// V20 收尾：舊頁的存在可信度／資料可信度徽章、同 IP 多筆／實測不符小標籤、網卡 MAC 表搬到 V2。
// 釘住「分組與顏色照舊頁」：same_machine／off_book 是後端給的，前端只分組不判斷。全假資料（10.99.x）。
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { computed, defineComponent, h } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as sig from '../utils/assetSignals'

enableAutoUnmount(afterEach)
vi.stubGlobal('computed', computed)
vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '/x' } }))
for (const [k, v] of Object.entries(sig)) vi.stubGlobal(k, v)
const Table = defineComponent({ props: ['rows', 'cols', 'prefix', 'unit', 'empty'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl', 'data-empty': p.empty }, (p.rows ?? []).map((r: any) => h('div', { class: 'row' },
    (p.cols ?? []).map((c: any) => (slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : h('span', String(c.text ? c.text(r) : r[c.k])))))))
} })
const stubs = { V2Table: Table, NuxtLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, InfoNote: { template: '<i class="note"><slot /></i>' } }

describe('徽章顏色與說明（舊頁同一組規則）', () => {
  it('資料可信度 ≥90 綠、50–89 黃、<50 紅', () => {
    expect(sig.trustTone(90)).toBe('good'); expect(sig.trustTone(89)).toBe('warn')
    expect(sig.trustTone(50)).toBe('warn'); expect(sig.trustTone(49)).toBe('bad'); expect(sig.trustTone(null)).toBe('bad')
  })
  it('存在可信度：計入＝綠、退役＝黃、其餘疑似下線＝紅，下鑽 bucket 同一個判斷', () => {
    expect([sig.existTone({ counted: true }), sig.existBucket({ counted: true })]).toEqual(['good', 'counted'])
    expect([sig.existTone({ retired: true }), sig.existBucket({ retired: true })]).toEqual(['warn', 'retired'])
    expect([sig.existTone({}), sig.existBucket({})]).toEqual(['bad', 'suspect_offline'])
    const lines = sig.existLines({ level_label: '高', evidence: ['onboarded', 'inv:vcenter'], stale_evidence: ['vcenter'], counted: true, has_cia: false })
    expect(lines.join('\n')).toContain('依據：已納管、vCenter（來源過期）')
    expect(lines.join('\n')).toContain('計入台數・CIA 待補登')
  })
  it('資料可信度說明列出三個來源與機器證據', () => {
    const t = sig.trustLines({ kind: 'vm', source_score: 70, sources: { dynassets: true, rvtools: null, cia: false }, evidence: 'managed' }).join('\n')
    expect(t).toContain('dynassets 有、RVTools 不適用、CIA 沒有')
    expect(t).toContain('已納管 +30')
  })
})

describe('同 IP 多筆／實測不符小標籤', () => {
  const detail = {
    collect_status: {
      self: { off_book: false },
      same_ip: [
        { asset_serial: 'AUTO-1', same_machine: true, off_book: true },
        { asset_serial: 'S-2', same_machine: false, off_book: false, hostname: 'other' },
        { asset_serial: 'S-3', same_machine: false, off_book: false, hostname: '' },
      ],
      observed: { seen_at: '2026-11-01', fields: [{ key: 'hostname', label: '主機名', observed: 'h-new', registered: 'h-old', mismatch: true }] },
    },
  }
  it('分組照後端的 same_machine／off_book，不自己判斷同一台', () => {
    const g = sig.splitSameIp(detail.collect_status)
    expect(g.shellTwins.map(r => r.asset_serial)).toEqual(['AUTO-1'])
    expect(g.otherOnIp.map(r => r.asset_serial)).toEqual(['S-2', 'S-3'])
    expect(g.ciaTwins).toEqual([])
  })
  it('實測不符：observed 欄位或 os_view.mismatch／ip_view／hostname_view／ip_error_view 任一就算', () => {
    expect(sig.mismatchOf({}).any).toBe(false)
    expect(sig.mismatchOf({ os_view: { mismatch: true } }).any).toBe(true)
    expect(sig.mismatchOf({ ip_view: { cia: '10.99.0.1' } }).any).toBe(true)
    expect(sig.mismatchOf({ ip_error_view: {} }).any).toBe(true)
    expect(sig.mismatchOf(detail).fields.length).toBe(1)
  })
  it('畫面出現「另有掃描紀錄」「同 IP 另 2 筆」「實測與 CIA 不符」與匯出', async () => {
    const C = (await import('../components/v2/AssetSignals.vue')).default
    const w = mount(C, { props: { detail, serial: 'S-1' }, global: { stubs } })
    const t = w.text()
    expect(t).toContain('另有掃描紀錄'); expect(t).toContain('同 IP 另 2 筆'); expect(t).toContain('實測與 CIA 不符')
    expect(t).toContain('主機名實測')
    expect(w.find('a[href="/x/api/measured-mismatch/export?asset_serial=S-1"]').exists()).toBe(true)
  })
  it('沒有同 IP、沒有實測就整排不出現', async () => {
    const C = (await import('../components/v2/AssetSignals.vue')).default
    const w = mount(C, { props: { detail: { collect_status: { same_ip: [] } }, serial: 'S-1' }, global: { stubs } })
    expect(w.find('.dupchips').exists()).toBe(false)
  })
})

describe('網卡 MAC 表', () => {
  const detail = {
    nics: [
      { nic_name: 'eth0', nic_type: 'physical', mac: 'aa:bb:cc:00:00:01', ips: '10.99.0.5', subnet: '24', speed_cur: 1000, speed_max: 10000, downgraded: true, model: 'X', driver: 'ixgbe' },
      { nic_name: 'bond0', bond: 'active-backup', mac: 'aa:bb:cc:00:00:02', speed_cur: 10000 },
    ],
    mac_view: { cia: 'aa:bb:cc:99:99:99', measured: 'aa:bb:cc:00:00:01', nic: 'eth0', at: '2026-11-01' },
  }
  it('全部 MAC 都列、項次固定、降速與系統校正標出', () => {
    const rows = sig.nicRows(detail)
    expect(rows.map(r => r.__no)).toEqual([1, 2])
    expect(rows[0].speed_text).toBe('1G（額定 10G，降速）')
    expect(rows[0].ips_text).toBe('10.99.0.5/24')
    expect(rows[0].chip).toBe('X (ixgbe)')
    expect(rows[0].mac_fixed).toBe(true); expect(rows[1].mac_fixed).toBe(false)
    expect(rows[1].iface).toBe('bond0 · active-backup')
    expect(sig.nicRows({ ...detail, mac_view: { error: 'x', measured: 'aa:bb:cc:00:00:01' } })[0].mac_fixed).toBe(false)
  })
  it('元件把每張網卡畫出來；沒收集時顯示收集說明', async () => {
    const C = (await import('../components/v2/AssetNics.vue')).default
    const w = mount(C, { props: { detail }, global: { stubs } })
    expect(w.findAll('.row').length).toBe(2)
    expect(w.text()).toContain('aa:bb:cc:00:00:02'); expect(w.text()).toContain('系統校正')
    const e = mount(C, { props: { detail: { host_spec: { spec_json: { net_notes: { nics: '這台沒有 ip 指令' } } } } }, global: { stubs } })
    expect(e.find('.tbl').attributes('data-empty')).toBe('這台沒有 ip 指令')
  })
})
