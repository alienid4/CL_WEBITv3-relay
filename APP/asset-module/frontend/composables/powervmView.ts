// PowerVM 可觀測架構圖（3.2 架構圖「PowerVM」頁籤）的純函式。
// 規則：null＝HMC 沒回報（寫「未取得」）；0＝HMC 回報 0（寫 0）。兩者不可混成同一個樣子。
export const NOT_GOT = '未取得'

export function cpuText(v: number | null | undefined): string {
  return v === null || v === undefined ? NOT_GOT : String(v)
}
export function vcpuText(v: number | null | undefined): string {
  return v === null || v === undefined ? NOT_GOT : String(v)
}
export function memText(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return NOT_GOT
  return `${Math.round((mb / 1024) * 100) / 100} GB`
}

export const STATUS_TONE: Record<string, string> = { fresh: 'good', stale: 'warn', unknown: 'warn', error: 'bad' }
export const STATUS_MARK: Record<string, string> = { fresh: '●', stale: '◐', unknown: '○', error: '✕' }

export type PvFilter = 'all' | 'stale' | 'cpu_unknown' | 'mem_unknown' | 'zero'
export const PV_FILTERS: PvFilter[] = ['all', 'stale', 'cpu_unknown', 'mem_unknown', 'zero']
export function pvFilter(v: unknown): PvFilter {
  return PV_FILTERS.includes(v as PvFilter) ? (v as PvFilter) : 'all'
}

/** 攤平成 LPAR 清單。項次依預設順序（HMC → 實體機 → LPAR）在篩選前編好，之後固定不變。 */
export function lparRows(consoles: any[]): any[] {
  const out: any[] = []
  for (const c of consoles ?? []) for (const s of c.systems ?? []) for (const l of s.lpars ?? []) {
    out.push({
      __no: out.length + 1, hmc: c.hostname || c.ip, hmc_ip: c.ip, frame: s.name, frame_serial: s.serial,
      name: l.name, lpar_id: l.lpar_id, env: l.env, state: l.state, os_version: l.os_version,
      proc_units: l.proc_units, procs: l.procs, mem_mb: l.mem_mb,
      status: c.status?.code, status_label: c.status?.label, collected_at: c.collected_at,
      machine_link: s.machine_link ?? null,
    })
  }
  return out
}

export function frameRows(consoles: any[]): any[] {
  const out: any[] = []
  for (const c of consoles ?? []) for (const s of c.systems ?? []) {
    out.push({
      __no: out.length + 1, hmc: c.hostname || c.ip, hmc_ip: c.ip, name: s.name, type_model: s.type_model, serial: s.serial,
      state: s.state, firmware: s.firmware, lpar_count: s.lpar_count,
      cpu_sum: s.cpu_sum, cpu_unknown: s.cpu_unknown, mem_sum_mb: s.mem_sum_mb, mem_unknown: s.mem_unknown,
      status: c.status?.code, status_label: c.status?.label, collected_at: c.collected_at, machine_link: s.machine_link ?? null,
    })
  }
  return out
}

export function filterLpars(rows: any[], f: PvFilter): any[] {
  switch (f) {
    case 'stale': return rows.filter(r => r.status === 'stale')
    case 'cpu_unknown': return rows.filter(r => r.proc_units === null || r.proc_units === undefined)
    case 'mem_unknown': return rows.filter(r => r.mem_mb === null || r.mem_mb === undefined)
    case 'zero': return rows.filter(r => r.proc_units === 0 || r.mem_mb === 0)
    default: return rows
  }
}

/** 架構圖的格子配置：每個實體機一欄；HMC 跨它底下所有實體機的欄。沒有實體機的 HMC 佔一欄（寫明沒資料）。 */
export function gridPlan(consoles: any[]): { cols: number; hmcs: { c: any; start: number; span: number }[]; frames: { c: any; s: any | null; col: number }[] } {
  const hmcs: { c: any; start: number; span: number }[] = []
  const frames: { c: any; s: any | null; col: number }[] = []
  let col = 1
  for (const c of consoles ?? []) {
    const list = c.systems?.length ? c.systems : [null]
    hmcs.push({ c, start: col, span: list.length })
    for (const s of list) frames.push({ c, s, col: col++ })
  }
  return { cols: col - 1, hmcs, frames }
}
