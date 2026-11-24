// 1.1 今日概況「焦點卡」的判斷（純函式，vitest 直接測）。
// 排序：分級（🔴 red ＞ 🟡 yellow）→ 第一類系統優先 → 斷線久（規格 v3 第 3 點）。分級一律看後端穩定欄位 tier，不看標籤文字。
// 狀態：只有「告警來源 ok、算得出來、n＝0、沒有監控盲區」才能是綠色；算不出來／未啟用／未收集不可轉成 0。

export type Tone = 'bad' | 'warn' | 'ok'

export const tierRank = (r: { tier?: string }): number => (r.tier === 'red' ? 0 : r.tier === 'yellow' ? 1 : 2)
export const tierTone = (r: { tier?: string }): '' | 'bad' | 'warn' => (tierRank(r) === 0 ? 'bad' : tierRank(r) === 1 ? 'warn' : '')
const classRank = (r: { c_sys_class?: string }): number => (String(r.c_sys_class || '').includes('第一') ? 0 : 1)
const mins = (r: { duration_min?: number | null }): number => (r.duration_min == null || Number.isNaN(Number(r.duration_min)) ? -1 : Number(r.duration_min))

export function sortFocus<T extends { tier?: string; c_sys_class?: string; duration_min?: number | null }>(groups: T[]): T[] {
  return [...groups].sort((x, y) => (tierRank(x) - tierRank(y)) || (classRank(x) - classRank(y)) || (mins(y) - mins(x)))
}

/** 焦點卡外框／大字顏色：有 🔴 就紅，只有 🟡 就黃；沒有項目時只有「確定查過且沒事」才綠 */
export function focusTone(groups: { tier?: string }[], alertsStatus: string | undefined, att: { blind?: string | null } | null | undefined): Tone {
  if (groups.length) return groups.some(g => g.tier === 'red') ? 'bad' : 'warn'
  return alertsStatus === 'ok' && !!att && !att.blind ? 'ok' : 'warn'
}

/** 資料來源的預期狀態（休市、尚未匯入）不算「沒更新」 */
export const EXPECTED_SOURCE_STATES = ['休市', '尚未匯入']
export const isStaleSource = (s: { state?: string | null }): boolean => !!s.state && !EXPECTED_SOURCE_STATES.includes(s.state)

/** 焦點卡標題旁的件數字：群組數與告警件數不同時要講清楚（組／件） */
export function focusChipText(groupCount: number, total: number | null | undefined): string {
  const t = total ?? groupCount
  const unit = t === groupCount ? '件' : '組'
  const head = groupCount > 3 ? `前 3 ${unit}・另有 ${groupCount - 3} ${unit}` : `${groupCount} ${unit}`
  return t === groupCount ? head : `${head}・共 ${t} 件`
}

// ===== 1.1 要顯示的「Teams 有沒有送出去」「各中繼來源新不新鮮」（原本藏在設定頁，H12／H14）=====
export interface TeamsView { settings?: { enabled?: boolean } | null; corrupt?: string | null; hold?: string | null; queue?: Record<string, number> | null }
export interface TeamsState { tone: 'ok' | 'warn' | 'bad' | 'dim' | 'wait'; text: string; todo: string | null }

export function teamsStatus(sv: TeamsView | null | undefined, err: string): TeamsState {
  if (err) return { tone: 'dim', text: '狀態讀不到', todo: null }
  if (!sv) return { tone: 'wait', text: '確認中…', todo: null }
  if (sv.corrupt) return { tone: 'bad', text: '設定檔有問題', todo: 'Teams 通知的設定檔有問題' }
  if (!sv.settings?.enabled) return { tone: 'dim', text: '未啟用', todo: null }
  const q = sv.queue ?? {}
  const pending = Number(q.pending ?? 0)
  const failed = Number(q.failed ?? 0)
  const uncertain = Number(q.uncertain ?? 0)
  if (sv.hold) return { tone: 'warn', text: `不會送出：${sv.hold}，待送 ${pending} 則`, todo: `Teams 通知目前不會送出：${sv.hold}，待送 ${pending} 則` }
  if (failed || uncertain) {
    return { tone: 'bad', text: `待送 ${pending}・重試中 ${failed}・不確定 ${uncertain}`, todo: `Teams 通知有 ${failed + uncertain} 則沒送出或不確定是否送達` }
  }
  return { tone: 'ok', text: `正常・待送 ${pending} 則`, todo: null }
}

export interface RelaySrc { source: string; label: string; implemented?: boolean; status: string; status_label?: string; last_collected_at?: string | null; last_batch?: string | null }
const RELAY_STATE: Record<string, string | null> = { ok: null, stale: '過期', partial: '只收到一半', never: '沒收到' }

/** 中繼匯入各來源 → 跟 1.1「資料來源」同一種格式（state 空＝正常） */
export function relayAsSources(view: { sources?: RelaySrc[] } | null | undefined): { key: string; label: string; as_of: string | null; state: string | null }[] {
  return (view?.sources ?? []).filter(s => s.implemented !== false).map(s => ({
    key: `relay:${s.source}`, label: `${s.label}・中繼`, as_of: s.last_collected_at ?? s.last_batch ?? null,
    state: s.status in RELAY_STATE ? RELAY_STATE[s.status] : (s.status_label || '沒收到'),
  }))
}
