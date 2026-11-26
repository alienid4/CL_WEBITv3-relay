/**
 * 1.2 掃描異常「異常消失」的掃描品質提示（純函式，給 changes.vue 與 vitest 共用）。
 * 重點：讀不到 ≠ 完整。API 失敗時要明講「本次消失數字不可確認」，不可靜默當成沒問題。
 */
export interface ScanQuality {
  scan_time: string
  incomplete: boolean
  global_incomplete: boolean
  mode: 'ok' | 'segments' | 'all' | 'pending'
  judgement_pending?: boolean
  latest_scan_time?: string | null
  bad_segments: string[]
  reason: string
  baseline_events: string[]
  confirm: number
  drop_pct: number
  rule_version: string
  autoclose_apply: boolean
  invalid_settings: string[]
}

export interface QualityState {
  loaded: boolean            // 曾成功取得（含成功但回 null＝沒有任何掃描紀錄）
  error: string              // 最近一次讀取失敗的原因（空字串＝沒失敗）
  data: ScanQuality | null
}

export interface QualityNotice {
  kind: 'unknown' | 'pending' | 'none' | 'ok' | 'segments' | 'all'
  text: string
  segments: string[]         // 完整清單（不截斷）
}

export function qualityNotice(s: QualityState): QualityNotice {
  if (!s.loaded) {
    return { kind: 'unknown', text: '掃描品質讀不到，本次消失數字不可確認', segments: [] }
  }
  const d = s.data
  if (!d) return { kind: 'none', text: '', segments: [] }
  const when = (d.scan_time || '').slice(0, 16).replace('T', ' ')
  if (d.mode === 'pending' || d.judgement_pending) {
    return { kind: 'pending', text: `最新一批判定未完成（最新掃描 ${(d.latest_scan_time || '').slice(0, 16).replace('T', ' ')}，品質停在 ${when}），本次消失數字不可確認`, segments: [] }
  }
  if (d.mode === 'all') return { kind: 'all', text: `本次掃描不完整，整批未採用於消失判定（${when}）`, segments: d.bad_segments }
  if (d.mode === 'segments') {
    return { kind: 'segments', text: `本次有 ${d.bad_segments.length} 個網段未採用，其餘網段照常判定（${when}）`, segments: d.bad_segments }
  }
  return { kind: 'ok', text: '', segments: [] }
}

/** 「異常消失」類型說明：門檻取自後端回傳的有效設定，不寫死 */
export function goneHint(confirm?: number | null): string {
  const n = confirm && confirm > 0 ? String(confirm) : 'N'
  return `連續 ${n} 次完整掃描都掃不到才記（門檻可在系統設定調整；若先經過「持續縮減確認」會更久）。掃描不完整的網段那次不計入，vCOps 近期看到開機中的不算。`
}

/** 「異常消失」分組：miss_count 與門檻都來自後端；任一未知（NULL／API 還沒回來）＝歷史次數未知，不當成 0／首次 */
export function goneGroup(missCount: number | null | undefined, confirm: number | null | undefined): 'repeat' | 'below' | 'unknown' {
  if (missCount == null || confirm == null) return 'unknown'
  return missCount >= confirm ? 'repeat' : 'below'
}

export interface IssueLike { is_read: number; handled_at?: string | null; auto_closed?: number; close_reason?: string | null }

/** 處理狀態：系統自動關閉與人工處理分開顯示 */
export function issueState(i: IssueLike): string {
  if (!i.is_read) return '未處理'
  if (i.auto_closed) return `系統自動關閉${i.close_reason ? `：${i.close_reason}` : ''}`
  return `已處理 ${i.handled_at?.slice(0, 16) || ''}`
}
