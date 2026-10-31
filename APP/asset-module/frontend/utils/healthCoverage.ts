/**
 * 體檢涵蓋文案（W221-013／Codex 37 號）：沒有發現問題 ≠ 十項都驗過。
 * 只認後端 check_states（passed／found／not_checked／not_applicable 四態）；
 * 舊 API 沒有 check_states 時不默認全驗，回「資料不足，無法判斷涵蓋」。
 */
export type CheckState = 'passed' | 'found' | 'not_checked' | 'not_applicable'
export interface CheckStateItem { state: CheckState; reason: string }
export interface HealthLike {
  checks_total?: number
  check_states?: Record<string, CheckStateItem> | null
}

export const CHECK_LABEL: Record<string, string> = {
  lost: '失聯', collect: '收集', eos: 'EOS', drift: '基線', account: '帳號',
  identity: '身分', required: '必填欄位', duplicate: '重複登記', os_guessed: 'OS 來源', doc: '單據',
}

export interface Coverage {
  known: boolean
  passed: number
  found: number
  notChecked: number
  notApplicable: number
  total: number
  gaps: { key: string; label: string; state: 'not_checked' | 'not_applicable'; reason: string }[]
}

export function coverageOf(h: HealthLike | null | undefined): Coverage {
  const cs = h?.check_states
  if (!cs || typeof cs !== 'object' || !Object.keys(cs).length) {
    return { known: false, passed: 0, found: 0, notChecked: 0, notApplicable: 0, total: h?.checks_total ?? 0, gaps: [] }
  }
  const c: Coverage = { known: true, passed: 0, found: 0, notChecked: 0, notApplicable: 0, total: Object.keys(cs).length, gaps: [] }
  for (const [key, v] of Object.entries(cs)) {
    const label = CHECK_LABEL[key] ?? key
    if (v.state === 'passed') c.passed++
    else if (v.state === 'found') c.found++
    else if (v.state === 'not_applicable') {
      c.notApplicable++
      c.gaps.push({ key, label, state: 'not_applicable', reason: v.reason })
    } else {
      // not_checked 與任何不認得的狀態一律當未驗，絕不默認通過
      c.notChecked++
      c.gaps.push({ key, label, state: 'not_checked', reason: v.reason || '狀態不明' })
    }
  }
  return c
}

export function coverageText(h: HealthLike | null | undefined): string {
  const c = coverageOf(h)
  if (!c.known) return '資料不足，無法判斷涵蓋'
  return `已驗通過 ${c.passed}／已驗有發現 ${c.found}／未驗 ${c.notChecked}／不適用 ${c.notApplicable}（共 ${c.total} 項）`
}
