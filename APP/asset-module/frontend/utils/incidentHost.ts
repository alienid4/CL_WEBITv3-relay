// 單一事件「現場找機器」「機櫃圖」一列的最近測試狀態（2.043.11，GPT 審查 A4）。
// 一列＝一台實體機或 ESXi（VM 併到它所在的 ESXi）。check：'alive'＝通、'no_response'＝不通、空＝沒測。
export type HostStatus = 'up' | 'down' | 'untested'
const one = (c?: string | null): HostStatus => (c === 'no_response' ? 'down' : c === 'alive' ? 'up' : 'untested')

export function hostStatus(hostKey: string, machines: { key: string; host_key?: string | null; check?: string | null }[]): HostStatus {
  const self = machines.find(m => m.key === hostKey)
  if (self && self.check) return one(self.check)                 // 它自己在範圍裡、測過：用自己的
  const vms = machines.filter(m => m.host_key === hostKey && m.key !== hostKey).map(m => one(m.check))
  if (vms.includes('down')) return 'down'
  if (vms.includes('up')) return 'up'
  return 'untested'
}
