// 機櫃差異待辦（2.043.17，R4–R8）：前端共用的字與表單規則。字跟後端 rack_diff.py 同一組（清單 API 也會回傳，以後端為準）。
export type DiffSubject = 'sys' | 'xl'
export const REASONS: Record<DiffSubject, Record<string, string>> = {
  sys: { moved: '搬到別的位置了', removed: '這台不在這格（整台拿走了）', off: '還在，但關機或拔電', dup: '看起來重複登記', unsure: '不確定，請管理員看' },
  xl: { absent: '這格其實是空的', wrongpos: '位置寫錯了', bind: '這格就是系統裡的某一台' },
}
export const STATUS: Record<string, string> = {
  pending: '待確認', matched: '已匹配', cia: '待補登 CIA', excel: '待退回 Excel', retire_wait: '待資產負責人核准退役', closed: '已結案',
}
// 結案動作要另外填的東西
export const ACTION_NEEDS: Record<string, ('note' | 'target' | 'bind' | 'approver')[]> = {
  reject: ['note'], move: ['target'], bind: ['bind'], approve_retire: ['approver'],
}

export const needsTarget = (s: DiffSubject, reason: string) => (s === 'sys' && reason === 'moved') || (s === 'xl' && reason === 'wrongpos')
export const needsBind = (s: DiffSubject, reason: string) => s === 'xl' && reason === 'bind'

/** 回報表單還缺什麼（空字串＝可以送出） */
export function reportProblem(s: DiffSubject, f: { reason: string; note: string; target_rack: string; target_u_start: string | number; bind_q: string }): string {
  if (!f.reason) return '請選原因'
  if (f.reason === 'unsure' && !f.note.trim()) return '選「不確定」要寫備註（看到什麼）'
  if (s === 'xl' && f.reason === 'wrongpos' && (!f.target_rack.trim() || !Number(f.target_u_start))) return '要填實際在哪（哪一櫃、從第幾 U）'
  if (needsTarget(s, f.reason) && f.target_rack.trim() && !Number(f.target_u_start)) return '「從第幾 U」要填數字'
  if (needsBind(s, f.reason) && !f.bind_q.trim()) return '要寫是哪一台（主機名、IP 或資產編號）'
  return ''
}

/** 結案動作還缺什麼（空字串＝可以送出） */
export function actProblem(action: string, f: { note: string; target_rack: string; target_u_start: string | number; bind_q: string; approver: string; basis: string }, item: { target_rack?: string | null; target_u_start?: number | null; bind_key?: string | null }): string {
  const need = ACTION_NEEDS[action] ?? []
  if (need.includes('note') && !f.note.trim()) return '駁回要寫原因'
  if (need.includes('target') && !((f.target_rack.trim() && Number(f.target_u_start)) || (item.target_rack && item.target_u_start))) return '要填新位置（櫃、起 U）'
  if (need.includes('bind') && !f.bind_q.trim() && !item.bind_key) return '要寫綁到系統哪一台'
  if (need.includes('approver') && (!f.approver.trim() || !f.basis.trim())) return '退役要登錄核准人與依據'
  return ''
}

/** 送出正式資料動作前的確認摘要（GPT B3）：會改哪台、改成什麼、能不能還原。null＝這個動作不改機櫃圖／資產（只改待辦狀態）。 */
export function actSummary(action: string, item: { subject: string; room: string; rack: string; u_text: string; who: string; target_room?: string | null; target_rack?: string | null; target_u_start?: number | null; target_u_end?: number | null },
  f: { target_rack: string; target_u_start: string | number; target_u_end: string | number; bind_q: string; approver: string; basis: string }): { lines: string[]; undo: string } | null {
  const here = `${item.room} ${item.rack} ${item.u_text}`
  const uu = (a: any, b: any) => (Number(a) && Number(b) && Number(b) !== Number(a) ? `${a}-${b}U` : `${a}U`)
  const tgt = f.target_rack ? `${f.target_rack} ${uu(f.target_u_start, f.target_u_end || f.target_u_start)}` : (item.target_rack ? `${item.target_rack} ${uu(item.target_u_start, item.target_u_end)}` : '')
  const undo = '可以還原（結案後那台又被別人改過就不能）'
  switch (action) {
    case 'confirm_off': return { lines: [`${item.who}（${here}）→ 標「關機未下架」`, '保留這段 U、用電算 0、不算在用台數'], undo }
    case 'confirm_removed': return { lines: [`${item.who}（${here}）→ 標「已不在櫃上」`, '從機櫃圖拿掉、不算台數；資產本身不改'], undo }
    case 'move': return { lines: [`${item.who}：${here} → ${tgt || '（新位置）'}`, '寫位置更正（會進 CIA 待更正）'], undo }
    case 'bind': return { lines: [`系統「${f.bind_q || '回報時指定的那台'}」→ 畫到 ${here}`, '寫位置更正（會進 CIA 待更正）'], undo }
    case 'approve_retire': return { lines: [`${item.who}（${here}）→ 記「CIA 待異動：報廢」＋已不在櫃上`, `核准人 ${f.approver || '—'}；依據 ${f.basis || '—'}`, 'CIA 資產狀態不會直接改（CIA 匯入改了才是真的）'], undo }
    case 'undo': return { lines: [`把這筆待辦還原到上一個狀態`, '結案時改過的機櫃狀態／位置／待異動一起改回去'], undo: '還原本身會留紀錄' }
    default: return null
  }
}
