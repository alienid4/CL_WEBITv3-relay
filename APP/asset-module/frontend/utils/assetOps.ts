// 資產明細與操作 V2（V20，2.059.2）的判斷：從舊 pages/assets/[serial].vue 原樣抽出，另外把 VMware 擋得比舊頁更嚴（天條）。
// 純函式，vitest 釘住。

// 字樣對齊 os_class_groups.json「虛擬化平台」（後端以那份為準），跟舊頁 auditPlatform 同一串
const VMWARE_RE = /esxi|vmware esx|vsphere hypervisor|photon|vcenter|vcsa|vm vc/

/** 這台是不是 VMware 設備（ESXi／vCenter）：後端判定（onboard_block.kind＝esxi）或 CIA 登記／實測 OS 字樣，任一個說是就是（fail-closed）。 */
export function isVmwareAsset(hw: any, osView?: any): boolean {
  if (hw?.onboard_block?.kind === 'esxi') return true
  if (VMWARE_RE.test(String(hw?.os ?? '').toLowerCase())) return true
  return VMWARE_RE.test(String(osView?.effective ?? '').toLowerCase())
}

/** 組態檢核要用哪個平台的腳本（舊頁 auditPlatform 原樣：VMware 先看登記值、其餘實測優先）。 */
export function auditPlatformOf(hw: any, osView?: any): 'linux' | 'aix' | 'windows' | 'vmware' | 'auto' {
  const os = String(hw?.os ?? '').toLowerCase()
  if (VMWARE_RE.test(os) || hw?.onboard_block?.kind === 'esxi') return 'vmware'
  const eff = String(osView?.effective ?? '').toLowerCase()
  if (eff && osView?.basis !== 'cia') {
    if (VMWARE_RE.test(eff)) return 'vmware'
    if (/\baix\b|^\d{4}-\d{2}-\d{2}-\d{4}$/.test(eff)) return 'aix'
    if (/windows|microsoft/.test(eff)) return 'windows'
    return 'linux'
  }
  if (/\baix\b|^\d{4}-\d{2}-\d{2}-\d{4}$/.test(os)) return 'aix'
  if (/windows|microsoft/.test(os)) return 'windows'
  if (os) return 'linux'
  return 'auto'
}
export const AUDIT_PLATFORM_LABEL: Record<string, string> = { linux: 'Linux', aix: 'AIX', windows: 'Windows', vmware: 'VMware', auto: '自動判斷平台' }

export interface ActionFlags { onboard: boolean; reonboard: boolean; collect: boolean; revoke: boolean; offline: boolean; exempt: boolean; unexempt: boolean }
/** 頁首哪些按鈕可以出現。舊頁規則：一鍵納管＝有 IP、還沒收得到、後端沒擋、沒豁免；重新納管／收集全部／收集規格／取消納管＝已納管（collect_ok＝1）。
 *  V2 多兩條：VMware 一律不出現納管與收集（取消納管照舊），只能看的帳號什麼寫入按鈕都沒有。 */
export function actionFlags(hw: any, opts: { vmware: boolean; viewer: boolean }): ActionFlags {
  const none = { onboard: false, reonboard: false, collect: false, revoke: false, offline: false, exempt: false, unexempt: false }
  if (!hw || opts.viewer) return none
  const onboarded = hw.collect_ok === 1
  const exempt = !!hw.onboard_exempt
  return {
    onboard: !opts.vmware && !!hw.ip && !onboarded && !hw.onboard_block && !exempt,
    reonboard: !opts.vmware && onboarded && !!hw.ip,
    collect: !opts.vmware && onboarded,
    revoke: onboarded && !!hw.ip,
    offline: !exempt,
    exempt: !exempt,
    unexempt: exempt,
  }
}

/** 收集結果的記號：✓ 只給真的存到資料且每部分都收到；部分權限不足＝⚠；沒收到＝✕；平台不做＝略過（舊頁 chipMark 原樣） */
export function chipMark(r: { ok: boolean | null; warn?: boolean }): string {
  return r.ok === true ? (r.warn ? '⚠' : '✓') : (r.ok === false ? '✕' : '略過')
}

/** 取消納管要用哪一套（舊頁同一個判斷） */
export function revokePlatform(os?: string | null): 'aix' | 'windows' | 'linux' {
  return os && /aix/i.test(os) ? 'aix' : os && /windows/i.test(os) ? 'windows' : 'linux'
}
