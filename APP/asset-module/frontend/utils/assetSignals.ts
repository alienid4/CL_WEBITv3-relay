// V20 收尾（舊 /assets/[serial] 頁首的徽章與小標籤搬到 V2 /v2/asset/…）：純顯示，不算數。
// 資料來源跟舊頁同一份：
//   存在可信度 GET /api/existence/asset/{序號}（existence 算好的 score／counted／retired）
//   資料可信度 GET /api/trust/asset/{序號}（trust_score 算好的 score）
//   同 IP 多筆 GET /api/assets/{序號} 的 collect_status.same_ip（後端 machine_scope 算好 same_machine／off_book，不在前端判斷同一台）
//   實測不符 同一支的 collect_status.observed＋os_view／ip_view／hostname_view／ip_error_view
//   網卡 同一支的 nics＋mac_view
// 這裡只做「舊頁畫面上怎麼分組、上什麼顏色」，規則照抄舊頁，不另訂。

export type Tone = 'good' | 'warn' | 'bad'

/** 資料可信度徽章顏色：綠 ≥90、黃 50–89、紅 <50（舊頁同一組門檻） */
export function trustTone(score: number | null | undefined): Tone {
  const s = Number(score ?? 0)
  return s >= 90 ? 'good' : s >= 50 ? 'warn' : 'bad'
}

/** 存在可信度：計入台數＝綠、退役＝黃、其餘（疑似下線）＝紅；bucket 是 3-x 存在可信度頁的篩選鍵 */
export function existBucket(e: any): 'counted' | 'retired' | 'suspect_offline' {
  return e?.counted ? 'counted' : e?.retired ? 'retired' : 'suspect_offline'
}
export function existTone(e: any): Tone {
  return e?.counted ? 'good' : e?.retired ? 'warn' : 'bad'
}

const EV: Record<string, string> = { onboarded: '已納管', 'inv:dynassets': 'DY', 'inv:vcenter': 'vCenter',
  collect_stale: '收集帳號（超過 7 天）', scan: '網路掃描' }

/** 存在可信度的說明文字（舊頁 existTip 照抄） */
export function existLines(e: any): string[] {
  if (!e) return []
  const st: string[] = e.stale_evidence || []
  const ev = (e.evidence || []).map((x: string) => (EV[x] || x) + (st.includes(x.replace('inv:', '')) ? '（來源過期）' : '')).join('、')
  return [
    e.level_label || '',
    `依據：${ev || '沒有任何證據'}`,
    `最後看到：${e.last_seen_any || '—'}`,
    `${e.retired ? '已退役（備查，不計入台數）' : e.counted ? '計入台數' : '疑似下線（不計入台數）'}${e.has_cia ? '' : '・CIA 待補登'}`,
  ].filter(Boolean)
}

/** 資料可信度的說明文字（舊頁 trustTip 照抄） */
export function trustLines(t: any): string[] {
  if (!t) return []
  const s = (v: boolean | null | undefined) => (v === null || v === undefined ? '不適用' : v ? '有' : '沒有')
  const kind: Record<string, string> = { vm: 'VM', physical: '實體機', esxi: 'ESXi 主機', unknown: '型態不明（照 VM 算）' }
  const src = t.sources || {}
  const evidence = t.evidence === 'managed' ? '已納管 +30'
    : t.evidence === 'collected' ? '已收集（設備）+30'
      : t.evidence === 'alive'
        ? (t.alive_basis === 'dynassets'
          ? `網路通 +20（依據 dynassets 存活清單，匯入 ${String(t.dynassets_imported_at || '').slice(0, 10)}）`
          : '網路通 +20（我們掃描看到）')
        : '沒有'
  return [
    `型態：${kind[t.kind] ?? t.kind ?? '—'}`,
    `來源分 ${t.source_score ?? '—'}／70：dynassets ${s(src.dynassets)}、RVTools ${s(src.rvtools)}、CIA ${s(src.cia)}`,
    `機器證據：${evidence}`,
    '欄位對不上的警示還沒做：第一版只看「來源裡有沒有這台」',
  ]
}

/** 同 IP 多筆分組（舊頁 ciaTwins／shellTwins／otherOnIp）：same_machine／off_book 是後端 machine_scope 算的，這裡只分組 */
export function splitSameIp(collectStatus: any) {
  const rows: any[] = collectStatus?.same_ip ?? []
  return {
    rows,
    selfShell: !!collectStatus?.self?.off_book,
    ciaTwins: rows.filter((r) => r.same_machine && !r.off_book),
    shellTwins: rows.filter((r) => r.same_machine && r.off_book),
    otherOnIp: rows.filter((r) => !r.same_machine),
  }
}

/** 實測 vs CIA（舊頁 observedMismatch／anyMismatch 同一個條件） */
export function mismatchOf(detail: any) {
  const observed = detail?.collect_status?.observed ?? null
  const fields: any[] = (observed?.fields ?? []).filter((f: any) => f.mismatch)
  const any = fields.length > 0 || !!detail?.os_view?.mismatch || !!detail?.ip_view || !!detail?.hostname_view || !!detail?.ip_error_view
  return { observed, fields, any }
}

/** 網卡速率：NIC 存 Mb/s（舊頁 fmtMb） */
export function fmtMb(mb: number | null | undefined): string {
  if (!mb) return '—'
  return mb >= 1000 ? `${+(mb / 1000).toFixed(mb % 1000 ? 1 : 0)}G` : `${mb}M`
}

/** 網卡列：全部 MAC 都列（實體／虛擬／teaming），項次照後端給的順序固定（舊頁 nicRows） */
export function nicRows(detail: any): any[] {
  const mv = detail?.mac_view
  return ((detail?.nics ?? []) as any[]).map((n, i) => ({
    ...n, __no: i + 1,
    iface: n.bond ? `${n.nic_name} · ${n.bond}` : n.nic_name,
    type_text: n.nic_type || '—',
    chip: n.model && n.driver ? `${n.model} (${n.driver})` : (n.model || n.driver || ''),
    ips_text: (n.ips || n.ip || '') + (n.subnet && (n.ips || n.ip) ? `/${n.subnet}` : ''),
    speed_text: n.speed_cur ? (n.downgraded ? `${fmtMb(n.speed_cur)}（額定 ${fmtMb(n.speed_max)}，降速）` : fmtMb(n.speed_cur)) : '',
    mac_fixed: !!(mv && !mv.error && n.mac && n.mac === mv.measured),
  }))
}
