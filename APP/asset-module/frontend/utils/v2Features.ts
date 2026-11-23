// V2 功能查詢（2.043.10，V73：舊版 /features 搬過來）。
// 一份目錄三種來源，都不另抄頁面清單：
//   1. V2 選單（useNavV2 的 V2_NAV）＝「頁面」
//   2. 舊版功能目錄（useNav 的 featureCatalog：舊頁面＋藏在頁面裡的動作，含說明與別名）＝轉到它在 V2 的位置
//      （用跟 middleware 同一張轉址表 v2LegacyTarget，所以點下去就是舊網址會被送到的那一頁）
//   3. 全站搜尋框的常用頁籤（Search.vue 的額外功能）由呼叫端另外加
import { V2_NAV, v2ActiveTo } from '~/composables/useNavV2'
import type { FeatureRow } from '~/composables/useNav'
import { v2LegacyTarget } from '~/utils/v2LegacyMap'

export interface V2FeatureRow {
  kind: 'page' | 'action' | 'old'
  no: string; label: string; to: string; where: string; desc: string; kw: string[]
}

const navIndex = () => {
  const m = new Map<string, { no: string; label: string }>()
  for (const g of V2_NAV) for (const it of g.items) m.set(it.to, { no: it.no, label: it.label })
  return m
}

/** 舊網址（含 ?query）→ V2 網址字串；沒有 V2 對應（login／print）就回原網址 */
export function toV2Url(old: string): string {
  const [path, qs] = old.split('?')
  const q: Record<string, string> = {}
  new URLSearchParams(qs || '').forEach((v, k) => { q[k] = v })
  const t = v2LegacyTarget(path, q)
  if (!t) return old
  const s = new URLSearchParams(t.query).toString()
  return s ? `${t.path}?${s}` : t.path
}

/** V2 網址落在選單哪一項 →「6.1 納管作業」 */
export function whereOf(url: string): string {
  const to = v2ActiveTo(url.split('?')[0])
  const it = to ? navIndex().get(to) : undefined
  return it ? `${it.no} ${it.label}` : '—'
}

export function v2FeatureCatalog(old: FeatureRow[]): V2FeatureRow[] {
  const rows: V2FeatureRow[] = []
  for (const g of V2_NAV) for (const it of g.items) rows.push({ kind: 'page', no: it.no, label: it.label, to: it.to, where: g.label, desc: '', kw: [] })
  for (const r of old) {
    const url = toV2Url(r.to)
    if (!url.startsWith('/v2')) continue          // 沒有 V2 對應（登入、列印）不列
    rows.push({ kind: r.kind === 'action' ? 'action' : 'old', no: '', label: r.label, to: url, where: whereOf(url), desc: r.desc, kw: r.kw })
  }
  return rows
}

export function searchV2Features(q: string, rows: V2FeatureRow[]): V2FeatureRow[] {
  const k = q.trim().toLowerCase()
  if (!k) return rows
  return rows.filter(r => [r.no, r.label, r.desc, r.where, r.to, ...r.kw].some(v => (v || '').toLowerCase().includes(k)))
}
