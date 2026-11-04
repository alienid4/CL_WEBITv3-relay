// 憑證到期探測（2.042.02）：清單多「最後探測」「探測比對」兩欄（可排序＝後端 SORTERS 有同名鍵）、
// 「匯出探測清單」只在管理員才看得到的「設定」頁籤、詳情頁把 probe 觀測標成「中繼探測（只供比對）」、開發協作有 D5 腳本。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DEVKIT_TASKS } from '../content/devkit'

const idx = readFileSync(resolve(process.cwd(), 'pages/v2/certs/index.vue'), 'utf-8')
const det = readFileSync(resolve(process.cwd(), 'pages/v2/certs/[id].vue'), 'utf-8')
const be = readFileSync(resolve(process.cwd(), '../backend/certs.py'), 'utf-8')

describe('憑證清單：探測欄位', () => {
  it('有「最後探測」「探測比對」欄，且後端排序鍵存在', () => {
    expect(idx).toMatch(/\{ k: 'probe_at', label: '最後探測'/)
    expect(idx).toMatch(/\{ k: 'probe_diff', label: '探測比對'/)
    expect(be).toMatch(/"probe_at": lambda r:/)
    expect(be).toMatch(/"probe_diff": lambda r:/)
  })
  it('探測不一致會附探測到的到期日；失敗附原因；未探測明講', () => {
    expect(idx).toContain("`${r.probe_diff_label}（探測 ${r.probe_not_after_local ?? '—'}）`")
    expect(idx).toContain('`探測失敗：${r.probe_error || \'—\'}`')
    expect(idx).toContain("if (!r.probe_diff) return '未探測'")
  })
  it('匯出探測清單：只在設定頁籤（管理員），走 GET 下載，結果告訴使用者略過幾筆', () => {
    const settings = idx.slice(idx.indexOf("<template v-else-if=\"tab === 'settings' && isAdmin\">"))
    expect(settings).toContain('@click="exportProbeList">匯出探測清單</button>')
    expect(idx).toContain('/api/certs/probe-list.txt')
    expect(idx).toContain("res.headers.get('X-Probe-Skipped')")
    expect(idx).not.toMatch(/開始探測|探測一次|立即探測/)
  })
  it('頁首說明不再說「沒有自動探測」，並講明探測不覆蓋人工值', () => {
    expect(idx).not.toContain('目前沒有自動探測')
    expect(idx).toContain('不會覆蓋人工值')
  })
  it('詳情頁：探測紀錄是獨立頁籤、唯讀（沒有作廢鈕），觀測歷史不混入探測', () => {
    expect(det).toContain("{ k: 'probe', label: `探測紀錄 ${d.value?.probe_observations?.length ?? 0}` }")
    const probeTab = det.slice(det.indexOf('v-else-if="d && tab === ' + "'probe'"), det.indexOf('v-else-if="d && tab === ' + "'obs'"))
    expect(probeTab).toContain(':rows="probeRows"')
    expect(probeTab).not.toContain('<button')
    expect(probeTab).not.toContain('#cell-ops')
    expect(det).not.toContain("o.source === 'probe'")
  })
})

describe('開發協作 D5：憑證探測腳本', () => {
  it('有 D5、可下載 cert_collect.ps1、權限寫唯讀且不需帳密', () => {
    const t = DEVKIT_TASKS.find(x => x.id === 'D5')!
    expect(t).toBeTruthy()
    expect(t.commands[0].file).toBe('cert_collect.ps1')
    expect(t.perm).toContain('唯讀')
    expect(t.perm).toContain('不需要任何帳號密碼')
    expect(t.commands[0].text.startsWith('# CERT-RELAY-COLLECT-SCRIPT v1')).toBe(true)
  })
})
