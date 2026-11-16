// 開發協作頁的「個人進度」：每條指令各自記「已執行」「已取得輸出」與備註；任務狀態由各條推導。
// 只存在使用者這個瀏覽器的 localStorage（頁面負責讀寫）；這裡是純函式：遷移舊格式、推導狀態、匯出／匯入。
import type { DevkitTask } from '../content/devkit'

export interface CmdProg { ran: boolean; got: boolean; note: string }
export interface TaskProg { ver: number; cmds: Record<string, CmdProg> }
export interface Progress { v: 2; updated: string; tasks: Record<string, TaskProg> }

export type TaskStatusKey = '待收集' | '進行中' | '已收集'
export interface TaskState {
  status: string
  got: number
  total: number
  /** 還沒取得輸出的指令（1 起算的序號） */
  missing: number[]
  /** 本機記的是舊版指令（task.ver 不同）：進度不採用，提示重新確認 */
  stale: boolean
}

export const NOTE_MAX = 200

export function emptyProgress(): Progress { return { v: 2, updated: '', tasks: {} } }

const asObj = (x: unknown): Record<string, unknown> | null => (x && typeof x === 'object' && !Array.isArray(x) ? x as Record<string, unknown> : null)

function cleanCmd(x: unknown): CmdProg {
  const o = asObj(x) ?? {}
  return { ran: o.ran === true || o.got === true, got: o.got === true, note: typeof o.note === 'string' ? o.note.slice(0, NOTE_MAX) : '' }
}

/**
 * 把本機讀到的任何東西整理成目前格式；絕不丟例外。
 * 舊格式（單一勾選）：{ "D1": true }，視為該任務目前每條指令都已取得（ver 記 1，指令若已改版會顯示「指令已更新」）。
 */
export function migrateProgress(raw: unknown, tasks: DevkitTask[]): Progress {
  const o = asObj(raw)
  if (!o) return emptyProgress()
  const out = emptyProgress()
  if (o.v === 2 && asObj(o.tasks)) {
    out.updated = typeof o.updated === 'string' ? o.updated : ''
    for (const [id, tp] of Object.entries(o.tasks as Record<string, unknown>)) {
      const t = asObj(tp)
      if (!t) continue
      const cmds: Record<string, CmdProg> = {}
      for (const [k, c] of Object.entries(asObj(t.cmds) ?? {})) if (/^\d+$/.test(k)) cmds[k] = cleanCmd(c)
      out.tasks[id] = { ver: Number.isInteger(t.ver) ? Number(t.ver) : 1, cmds }
    }
    return out
  }
  for (const [id, val] of Object.entries(o)) {              // 舊格式：{ D1: true }
    if (val !== true) continue
    const t = tasks.find(x => x.id === id)
    if (!t || t.placeholder) continue
    const cmds: Record<string, CmdProg> = {}
    t.commands.forEach((_, i) => { cmds[String(i)] = { ran: true, got: true, note: '舊版進度（單一勾選）' } })
    out.tasks[id] = { ver: 1, cmds }
  }
  return out
}

export function taskState(t: DevkitTask, p: Progress): TaskState {
  if (t.placeholder) return { status: t.waiting || '待開發', got: 0, total: 0, missing: [], stale: false }
  const total = t.commands.length
  const tp = p.tasks[t.id]
  const stale = !!tp && tp.ver !== t.ver && Object.values(tp.cmds).some(c => c.ran || c.got)
  const cmds = stale || !tp ? {} : tp.cmds
  const got = t.commands.filter((_, i) => cmds[String(i)]?.got).length
  const ran = t.commands.filter((_, i) => cmds[String(i)]?.ran || cmds[String(i)]?.got).length
  const missing = t.commands.map((_, i) => i + 1).filter(n => !cmds[String(n - 1)]?.got)
  const status = got === total && total > 0 ? '已收集' : ran > 0 ? '進行中' : '待收集'
  return { status, got, total, missing, stale }
}

export function cmdProg(t: DevkitTask, p: Progress, i: number): CmdProg {
  const tp = p.tasks[t.id]
  if (!tp || tp.ver !== t.ver) return { ran: false, got: false, note: '' }
  return tp.cmds[String(i)] ?? { ran: false, got: false, note: '' }
}

function touch(p: Progress, t: DevkitTask, now: string): { next: Progress; tp: TaskProg } {
  const cur = p.tasks[t.id]
  const tp: TaskProg = cur && cur.ver === t.ver ? { ver: cur.ver, cmds: { ...cur.cmds } } : { ver: t.ver, cmds: {} }   // 指令已更新：從頭記
  return { next: { v: 2, updated: now, tasks: { ...p.tasks, [t.id]: tp } }, tp }
}

/** 改某一條指令的狀態（不改動傳入的 p）。「已取得輸出」一定也算「已執行」；取消「已執行」會一併取消「已取得輸出」。 */
export function setCmd(p: Progress, t: DevkitTask, i: number, patch: Partial<CmdProg>, now: string): Progress {
  const { next, tp } = touch(p, t, now)
  const old = tp.cmds[String(i)] ?? { ran: false, got: false, note: '' }
  const m: CmdProg = { ...old, ...patch }
  if (patch.got === true) m.ran = true
  if (patch.ran === false) m.got = false
  m.note = String(m.note ?? '').slice(0, NOTE_MAX)
  tp.cmds[String(i)] = m
  return next
}

/** 整個任務全部勾選或全部取消（備註保留）。 */
export function setAll(p: Progress, t: DevkitTask, on: boolean, now: string): Progress {
  const { next, tp } = touch(p, t, now)
  t.commands.forEach((_, i) => {
    const old = tp.cmds[String(i)] ?? { ran: false, got: false, note: '' }
    tp.cmds[String(i)] = { ...old, ran: on, got: on }
  })
  return next
}

export function exportProgress(p: Progress): string { return JSON.stringify(p, null, 2) }

/** 匯入檔解析：格式不對回 { ok:false, err }；合法就整理成目前格式（也接受舊格式）。 */
export function parseImport(text: string, tasks: DevkitTask[]): { ok: true; p: Progress } | { ok: false; err: string } {
  let j: unknown
  try { j = JSON.parse(text.replace(/^﻿/, '')) } catch { return { ok: false, err: '不是有效的 JSON 檔' } }
  const o = asObj(j)
  if (!o) return { ok: false, err: '檔案內容不是進度資料' }
  const looksV2 = o.v === 2 && asObj(o.tasks)
  const looksLegacy = !('v' in o) && Object.values(o).every(v => typeof v === 'boolean')
  if (!looksV2 && !looksLegacy) return { ok: false, err: '不是開發協作匯出的進度檔' }
  return { ok: true, p: migrateProgress(o, tasks) }
}

/** 匯入合併：以匯入檔裡有的任務為準，其他任務保留本機的。 */
export function mergeProgress(base: Progress, incoming: Progress, now: string): Progress {
  return { v: 2, updated: now, tasks: { ...base.tasks, ...incoming.tasks } }
}
