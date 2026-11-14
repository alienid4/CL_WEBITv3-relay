// 目錄清理「容量占用候選」（2.046.0，R18）：後端 dir_housekeeping.candidates 的結果攤成表格列。只是候選，請資料擁有者判斷處理方式。
export interface HkCand { mount: string; pct: number | null; flags: string[]; items: { path: string; size: string; kb: number; reasons: string[]; biggest: { kind: string; path: string; size: string } | null }[] }

export function candRows(cs: HkCand[] | null | undefined) {
  const out: Record<string, any>[] = []
  for (const c of cs ?? []) {
    if (!c.items.length) {
      out.push({ key: `${c.mount}#0`, mount: c.mount, pct: c.pct, path: '', size: '', kb: 0, biggest: '', reasons: '沒有找到 1 GB 以上的目錄', flags: c.flags.join('；') })
      continue
    }
    c.items.forEach((it, i) => out.push({
      key: `${c.mount}#${i}`, mount: c.mount, pct: c.pct, path: it.path, size: it.size, kb: it.kb,
      biggest: it.biggest ? `${it.biggest.kind === 'file' ? '檔案' : '目錄'} ${it.biggest.path}（${it.biggest.size}）` : '',
      reasons: it.reasons.join('、'), flags: c.flags.join('；'),
    }))
  }
  return out.map((r, i) => ({ ...r, __no: i + 1 }))
}

/** 夜間自動開關的確認文字（打開前讓管理員看清楚會做什麼） */
export function nightConfirmText(on: boolean): string {
  return on
    ? '打開夜間自動掃描？\n每天 19:00～07:00 自動挑檔案系統使用率 ≥80% 的主機，一次一台、每台每晚最多一次、主機忙就跳過；07:00 後不開新的一台、08:30 前結束。唯讀，不會刪改任何檔案。'
    : '關閉夜間自動掃描？手動掃描不受影響。'
}
