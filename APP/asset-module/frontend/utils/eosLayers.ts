// EOS 項目底下機器的構成（Codex 66／004）：把對帳橋角色整理成使用者看得懂的四類，不顯示技術欄名。
// 逐台各自一台、各自查自己的 EOS；構成是規則推定（機櫃規則＋人工合併決定），無法判斷標「未能判斷」。
export const LAYER_GROUPS: { key: string; label: string; roles: string[] }[] = [
  { key: 'machine', label: '整機', roles: ['phys_server', 'phys_storage', 'phys_network', 'phys_other', 'esxi', 'endpoint', 'frame_suspect'] },
  { key: 'virtual', label: '虛擬機／分割區', roles: ['vm', 'lpar'] },
  { key: 'component', label: '元件', roles: ['appliance', 'frame_multi'] },
  { key: 'mgmt', label: '管理口', roles: ['mgmt_attached', 'mgmt_orphan'] },
]
export const LAYER_UNKNOWN_LABEL = '未能判斷'

export function layerParts(layers?: Record<string, number> | null): { label: string; n: number }[] {
  if (!layers) return []
  const out: { label: string; n: number }[] = []
  let known = 0
  for (const g of LAYER_GROUPS) {
    const n = g.roles.reduce((s, r) => s + (layers[r] || 0), 0)
    known += n
    if (n > 0) out.push({ label: g.label, n })
  }
  const total = Object.values(layers).reduce((s, n) => s + (n || 0), 0)
  if (total - known > 0) out.push({ label: LAYER_UNKNOWN_LABEL, n: total - known })
  return out
}

/** 只有「不是全部都是整機」才值得顯示（全是整機＝沒有額外資訊，不干擾畫面）。 */
export function layerText(layers?: Record<string, number> | null): string {
  const parts = layerParts(layers)
  if (parts.length === 0 || (parts.length === 1 && parts[0].label === '整機')) return ''
  return parts.map(p => `${p.label} ${p.n}`).join('／')
}
