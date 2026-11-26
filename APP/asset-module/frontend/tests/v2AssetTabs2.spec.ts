/**
 * 2.048.29 V20 第 2 批：資產明細的「軟體」「歷史」分頁搬成 V2 元件（資料由資產頁傳進來），實掛：
 *   - 軟體：照套件名排、項次固定；搜尋比對名稱／版本／供應商／架構／來源；0 筆講是哪一種；登記軟體有才顯示
 *   - 歷史：納管紀錄失敗也列、數失敗；收集診斷三種（沒紀錄／有紀錄／指令樣本）；組態差異照發現時間排
 *   - 舊頁的納管紀錄以前讀錯物件（detail 是 ref），永遠顯示「沒有任何納管紀錄」——修正後讀 detail.value
 */
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { computed, defineComponent, h, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssetSoftware from '../components/v2/AssetSoftware.vue'
import AssetHistory from '../components/v2/AssetHistory.vue'

beforeEach(() => { for (const [k, v] of Object.entries({ ref, computed })) vi.stubGlobal(k, v) })
afterEach(() => vi.unstubAllGlobals())
enableAutoUnmount(afterEach)

const V2Table = defineComponent({ props: ['rows', 'cols', 'empty', 'exportName'], setup(p, { slots }) {
  return () => h('div', { class: 'tbl', 'data-x': p.exportName }, (p.rows ?? []).length ? (p.rows ?? []).map((r: any) => h('div', { class: 'row', 'data-no': r.__no },
    (p.cols ?? []).map((c: any) => h('span', { class: `c-${c.k}` }, slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]!({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))))) : [h('i', { class: 'empty' }, p.empty)])
} })
const pass = defineComponent({ setup(_p, { slots }) { return () => h('span', slots.default?.()) } })
const opts = (props: any) => ({ props, global: { components: { V2Table, InfoNote: pass } } })
const EMPTY = { title: '軟體收集失敗', detail: '連不上', at: '2026-11-01' }

describe('軟體分頁', () => {
  const PK = [{ name: 'zlib', version: '1.2', vendor: 'Red Hat' }, { name: 'bash', version: '5.1', arch: 'x86_64' }, { name: 'openssl', version: '3.0', source: 'rpm' }]
  it('照套件名排、項次照這個順序；搜尋比對版本／架構；匯出檔名帶序號', () => {
    const w = mount(AssetSoftware, opts({ serial: 'S1', packages: PK, software: [], empty: EMPTY, lastCollected: 't1' }))
    expect(w.findAll('.c-name').map(x => x.text())).toEqual(['bash', 'openssl', 'zlib'])
    expect(w.findAll('.row').map(r => r.attributes('data-no'))).toEqual(['1', '2', '3'])
    expect(w.find('.tbl').attributes('data-x')).toBe('S1_軟體')
    expect(w.text()).toContain('最後收集：t1')
    return w.find('input.q').setValue('x86').then(() => {
      expect(w.findAll('.c-name').map(x => x.text())).toEqual(['bash'])
      expect(w.findAll('.row')[0].attributes('data-no')).toBe('1')
      expect(w.text()).toContain('1 / 3')
    })
  })
  it('0 筆：講資產頁算好的原因；登記軟體有資料才顯示', () => {
    let w = mount(AssetSoftware, opts({ serial: 'S1', packages: [], software: [], empty: EMPTY }))
    expect(w.text()).toContain('軟體收集失敗')
    expect(w.text()).toContain('最後一次收集嘗試：2026-11-01')
    expect(w.find('input.q').exists()).toBe(false)
    expect(w.text()).not.toContain('登記的資料庫／軟體')
    w = mount(AssetSoftware, opts({ serial: 'S1', packages: [], software: [{ asset_name: 'DB', handles_pii: 1 }, { asset_name: 'AP', handles_pii: 0 }], empty: EMPTY }))
    expect(w.text()).toContain('登記的資料庫／軟體')
    expect(w.findAll('.c-asset_name').map(x => x.text())).toEqual(['AP', 'DB'])
    expect(w.findAll('.c-handles_pii').map(x => x.text())).toEqual(['否', '是'])
  })
})

describe('歷史分頁', () => {
  it('納管紀錄失敗也列、標出失敗筆數；登入帳號空寫「未記錄」', () => {
    const w = mount(AssetHistory, opts({ serial: 'S1', trace: null, history: [], onboard: [
      { created_at: 't1', ok: 0, platform: 'aix', login_user: null, message: '金鑰不對' }, { created_at: 't2', ok: 1, login_user: 'root' }] }))
    expect(w.text()).toContain('納管紀錄 2')
    expect(w.text()).toContain('／失敗 1')
    expect(w.findAll('.c-ok').map(x => x.text())).toEqual(['失敗', '成功'])
    expect(w.findAll('.c-login_user').map(x => x.text())).toEqual(['未記錄', 'root'])
    expect(w.text()).toContain('還沒有收集連線紀錄')
  })
  it('沒有納管紀錄：照實講沒有；組態差異空寫「無歷史異常紀錄」', () => {
    const w = mount(AssetHistory, opts({ serial: 'S1', trace: null, history: [], onboard: [] }))
    expect(w.text()).toContain('沒有任何納管紀錄')
    expect(w.text()).toContain('無歷史異常紀錄')
  })
  it('收集診斷：結論、帳號、原始 SSH 訊息、指令樣本（離開碼 null＝沒回應、輸出空＝空）', () => {
    const w = mount(AssetHistory, opts({ serial: 'S1', onboard: [], history: [], trace: { verdict: 'no_ssh', text: '連不上', account: 'webit3sc', key_path: '/k',
      cmd_total: 2, cmd_ok: 1, cmd_empty: 1, cmd_failed: 0, collected_at: 't', transport_error: 'Permission denied',
      samples: [{ cmd: 'uname', exit_code: 0, out_len: 5, out_head: 'Linux', ms: 3 }, { cmd: 'lslpp', exit_code: null, out_len: 0, ms: 9 }] } }))
    expect(w.find('.verdict.v-no_ssh').text()).toBe('連不上')
    expect(w.text()).toContain('Permission denied')
    expect(w.text()).toContain('指令 2 條：有輸出 1、空 1、失敗 0')
    expect(w.findAll('.c-exit_code').map(x => x.text())).toEqual(['0', '沒回應'])
    expect(w.findAll('.c-out_len').map(x => x.text())).toEqual(['5 字元 · Linux', '空'])
  })
  it('組態差異照發現時間排、項次固定；已讀／未讀', () => {
    const w = mount(AssetHistory, opts({ serial: 'S1', onboard: [], trace: null, history: [
      { id: 1, detected_at: '2026-10-02', issue_type: 'B', is_read: 1 }, { id: 2, detected_at: '2026-10-01', issue_type: 'A', is_read: 0 }] }))
    const t = w.findAll('.tbl').at(-1)!
    expect(t.findAll('.c-issue_type').map(x => x.text())).toEqual(['A', 'B'])
    expect(t.findAll('.c-is_read').map(x => x.text())).toEqual(['未讀', '已讀'])
    expect(t.attributes('data-x')).toBe('S1_組態差異')
  })
})

describe('第 15 批審查補測', () => {
  const PK = [{ name: 'zlib', version: '1.2', vendor: 'Red Hat' }, { name: 'bash', version: '5.1', arch: 'x86_64' }, { name: 'openssl', version: '3.0', source: 'rpm' }]
  it('搜尋比對供應商、來源，不分大小寫；找不到講出搜尋字', async () => {
    const w = mount(AssetSoftware, opts({ serial: 'S1', packages: PK, software: [], empty: EMPTY }))
    const q = w.find('input.q')
    await q.setValue('RED HAT')
    expect(w.findAll('.c-name').map(x => x.text())).toEqual(['zlib'])
    await q.setValue('rpm')
    expect(w.findAll('.c-name').map(x => x.text())).toEqual(['openssl'])
    await q.setValue('OPENSSL')
    expect(w.findAll('.c-name').map(x => x.text())).toEqual(['openssl'])
    await q.setValue('zzz')
    expect(w.find('.empty').text()).toBe('找不到符合「zzz」的套件')
  })
  it('失敗筆數算的是失敗的（兩敗一成）', () => {
    const w = mount(AssetHistory, opts({ serial: 'S1', trace: null, history: [], onboard: [{ ok: 0 }, { ok: 0 }, { ok: 1 }] }))
    expect(w.text()).toContain('／失敗 2')
  })
  it('樣本：stderr 有值照列、空寫「—」；耗時帶 ms；離開碼 0 綠、非 0 紅；金鑰路徑', () => {
    const w = mount(AssetHistory, opts({ serial: 'S1', onboard: [], history: [], trace: { verdict: 'cmd_failed', text: 'x', key_path: '/k/id_rsa',
      samples: [{ cmd: 'a', exit_code: 0, out_len: 1, out_head: 'y', ms: 3, stderr: '' }, { cmd: 'b', exit_code: 2, out_len: 0, ms: 7, stderr: 'denied' }] } }))
    expect(w.findAll('.c-stderr').map(x => x.text())).toEqual(['', 'denied'])
    expect(w.findAll('.c-ms').map(x => x.text())).toEqual(['3 ms', '7 ms'])
    const ex = w.findAll('.c-exit_code')
    expect(ex[0].find('.good').exists()).toBe(true)
    expect(ex[1].find('.bad').exists()).toBe(true)
    expect(w.text()).toContain('/k/id_rsa')
  })
  it('舊頁接線：資料照傳、分頁標題算進納管筆數', async () => {
    const fs = await import('node:fs')
    const v = fs.readFileSync('pages/assets/[serial].vue', 'utf8')
    for (const t of [':onboard="onboardHistory"', ':history="detail.history"', ':trace="trace"', ':packages="detail.packages"', ':software="detail.software"', ':empty="softwareEmpty"',
      'detail.history.length + onboardHistory.length', 'v-if="onboardFailed"']) expect(v, t).toContain(t)
  })
})

it('舊頁接線：納管紀錄讀 detail.value（以前讀到 ref 本身，永遠是空的）；兩個分頁照舊在 v-else-if 鏈上', async () => {
  const fs = await import('node:fs')
  const v = fs.readFileSync('pages/assets/[serial].vue', 'utf8')
  expect(v).toContain('(detail.value as any)?.onboard_history')
  expect(v).not.toContain('(detail as any)?.onboard_history')
  expect(v).toMatch(/<V2AssetSoftware v-else-if="activeTab === 'software'"/)
  expect(v).toMatch(/<V2AssetHistory v-else-if="activeTab === 'history'"/)
})

it('兩個元件的 <style> 都編得過', async () => {
  const { parse, compileStyle } = await import('@vue/compiler-sfc')
  const fs = await import('node:fs')
  for (const f of ['AssetSoftware', 'AssetHistory']) {
    const d = parse(fs.readFileSync(`components/v2/${f}.vue`, 'utf8')).descriptor
    for (const s of d.styles) expect(compileStyle({ source: s.content, filename: f, id: 'data-v-x', scoped: true }).errors, f).toEqual([])
  }
})
