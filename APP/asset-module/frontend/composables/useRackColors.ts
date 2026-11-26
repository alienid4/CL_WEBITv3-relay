// 4-1 機房平面圖與機櫃正面共用的「顏色依」分類（後端 rack_layout.MACHINE_TYPES／SYS_LEVELS 同一組 key）。
// 不明／未分級一律灰色，不猜。
export type ColorBy = 'type' | 'level'
export interface RackCat { k: string; label: string; color: string }

export const TYPE_CATS: RackCat[] = [
  { k: 'esxi', label: 'VM 主機（ESXi）', color: 'var(--chart-2)' },
  { k: 'linux', label: '實體 Linux', color: 'var(--chart-1)' },
  { k: 'windows', label: '實體 Windows', color: 'var(--chart-5)' },
  { k: 'power', label: 'AIX／Power', color: 'var(--chart-4)' },
  { k: 'oracle', label: 'Oracle 一體機', color: 'var(--chart-6)' },
  { k: 'storage', label: '儲存', color: 'var(--chart-3)' },
  { k: 'san', label: 'SAN 交換器', color: 'var(--chart-8)' },
  { k: 'network', label: '網路設備', color: 'var(--chart-7)' },
  { k: 'unknown', label: '不明', color: 'var(--chart-gray)' },
]
export const LEVEL_CATS: RackCat[] = [
  { k: 'core', label: '核心', color: 'var(--chart-6)' },
  { k: 'noncore', label: '非核心', color: 'var(--chart-2)' },
  { k: 'unclassified', label: '未分級', color: 'var(--chart-gray)' },
]

export function rackCats(by: ColorBy): RackCat[] {
  return by === 'level' ? LEVEL_CATS : TYPE_CATS
}

export function rackCatOf(by: ColorBy, d: { machine_type?: string | null; sys_level?: string | null } | null | undefined): string {
  if (!d) return by === 'level' ? 'unclassified' : 'unknown'
  return (by === 'level' ? d.sys_level : d.machine_type) || (by === 'level' ? 'unclassified' : 'unknown')
}

export function rackCatColor(by: ColorBy, k: string): string {
  return rackCats(by).find(c => c.k === k)?.color ?? 'var(--chart-gray)'
}

export interface IpSeg { ip: string; cidr: string | null; purpose: string | null; vlan: string | null; env: string | null }
export function ipSegText(x: IpSeg): string {
  if (!x.cidr) return `${x.ip}（不在網段表）`
  return `${x.ip}（${x.cidr}${x.purpose ? ' ' + x.purpose : ''}${x.vlan ? ' VLAN ' + x.vlan : ''}${x.env ? ' ' + x.env : ''}）`
}
