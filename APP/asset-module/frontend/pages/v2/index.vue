<script setup lang="ts">
// 1.1 今日概況（2.044.0，需求 R9，使用者 2026-11-04：「今日概況應該是哪台主機有報修、有報告警…這才是今日；保固到期每天都 30 台，
// 一年才做完，永遠放首頁不恰當」）。只放台北時間 00:00 到現在的事；長期待辦搬到 1.4 追蹤處理，這裡只留一行入口＋今天的變化。
// 資料＝/api/v2/today（v2_today.today，組裝既有計算）：
//   現在的告警＝wug_alert 告警事件（還開著）；今天的事件＝4.1 同一張表；今天的資產異動＝1.2 每日異動同一份比對；
//   開盤尖峰（2.045.0）＝跳板機收盤後送來的 Aria ESXi 檔（V2OpeningPeak）；今天還沒收到只寫「尚未匯入」，不放假數字。
// 0 件和「未收集／未啟用／資料中斷」分開顯示：n 是 null＝來源沒資料（寫原因），n 是 0＝真的 0 件。
// 舊網址：?tab=todo（原待處理事項）→ 1.4 追蹤處理；?tab=week（原本週異動）→ 1.2 異動與異常「本週異動」頁籤。
// 畫面重看（AI/畫面重看標準_2026-11-15.md，樣板 1.3）：頁首一行結論 →「要你處理」（沒事寫沒事）→ 可點的數字卡 →
// 表格每格一個粗體主值＋一行灰字；聯絡五欄、IP、分級收進「點監控項目看明細」與匯出；逐來源資料時間收進「資料更新於」。
import { NuxtLink } from '#components'
import { ALERT_CONTACT_COLS } from '~/utils/alertContact'
// Q9（2.066.x）：告警預設只列跟 Teams 同一個主機組政策範圍（?all=1 看全部）；同一分鐘＋同一來源收成一列，點件數（?ag=）列出那幾筆
import { scopeOf, toggleScopeQuery, groupQuery, groupRows, groupTitle, bucketQuery, attentionCount, attentionBlind } from '~/utils/alertScope'
// 2.066.x（使用者 11-13）：「需要注意」只列斷滿門檻、還沒恢復、不在例行時段的；其他收進摺疊的「例行／已自動恢復」（?ab= 下鑽）
import type { AlertGroup } from '~/utils/alertScope'
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
if (route.query.tab === 'todo') await navigateTo('/v2/track', { replace: true })
else if (route.query.tab === 'week') await navigateTo('/v2/changes?tab=week', { replace: true })

interface Block { status: string; label?: string | null; rows: any[]; n: number | null; as_of: string | null; [k: string]: any }
interface Today {
  now: string; since: string; date: string
  alerts: Block; incidents: Block; changes: Block; opening: Block
  track: { status: string; label?: string; total: number | null; high?: number; as_of: string | null; nodata?: string[]; changes?: { prev_day: string | null; new: number | null; resolved: number | null; reason: string | null } }
  sources: { key: string; label: string; as_of: string | null; state: string | null }[]
  can_notify?: boolean
}
const { apiFetch } = useApi()
const data = ref<Today | null>(null)
const err = ref('')
async function load() {
  try { data.value = await apiFetch<Today>('/api/v2/today', { query: { scope: scopeOf(route.query.all) } }); err.value = '' } catch (e: any) { err.value = `今日資料載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
await load()
watch(() => route.query.all, () => { load() })
// 1.5 每日檢查今天的狀態一行；讀不到就寫原因，不擋首頁
const dcheck = ref<{ state: string; text: string; prev?: any; lp_alert?: string; bad?: number; unconfirmed?: number } | null>(null)
const dcErr = ref('')
onMounted(async () => { try { dcheck.value = await apiFetch<any>('/api/daily-check/today') } catch (e: any) { dcErr.value = `讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } })

const hm = (t: string | null | undefined) => (t ? String(t).slice(11, 16) || String(t) : '')
const srcText = (s: Today['sources'][number]) => (s.state ? s.state : (s.as_of ? (s.as_of.slice(0, 10) === data.value?.date ? hm(s.as_of) : s.as_of.slice(0, 16)) : '—'))
const staleSrc = computed(() => (data.value?.sources ?? []).filter(s => !!s.state))
/** 區塊標題旁的件數：null＝來源沒資料（寫原因的短字），0＝真的 0 件 */
const countText = (b: Block | undefined) => (!b ? '—' : b.n == null ? ({ disabled: '未啟用', uncollected: '未收集', error: '無法計算', stale: '資料中斷' } as Record<string, string>)[b.status] || '未收集' : `${b.n} 件`)
/** 2.066.1：資產異動那行裡的「收集中斷 M 台」做成連結（到 1.2 每日異動的收集中斷分類），其餘文字照原樣 */
const gapParts = (b: any): { t: string; link?: string }[] => {
  const label = String(b?.label || '')
  const g = b?.collect_gap
  const hit = g?.n && g?.link ? `收集中斷 ${g.n} 台` : ''
  const i = hit ? label.indexOf(hit) : -1
  if (i < 0) return [{ t: label }]
  return [{ t: label.slice(0, i) }, { t: hit, link: g.link }, { t: label.slice(i + hit.length) }].filter(p => p.t)
}
const dur = (m: number | null | undefined) => { const v = Number(m || 0); return v < 120 ? `${v} 分` : `${Math.floor(v / 60)} 小時 ${v % 60} 分` }
const mlink = (r: any) => (r.machine_key ? `/v2/machine/${encodeURIComponent(r.machine_key)}` : r.ip ? `/v2/hosts?q=${encodeURIComponent(r.ip)}` : null)
// 告警兩段（2.044.3，GPT A2＋B1）：現在仍在告警（不管哪天開始，昨天開始的標「昨天起」）／今天發生過（今天 00:00 後開始，含已恢復）
const shape = (r: any) => ({ ...r, dur_text: dur(r.duration_min), start_text: String(r.started_at || '').slice(5, 16), end_text: r.ended_at ? String(r.ended_at).slice(5, 16) : '未恢復',
  sys_text: r.api_id ? `${r.api_id} ${r.system_name || ''}`.trim() : '未登記業務系統', since_text: r.since_note ? String(r.since_note).replace('昨日起', '昨天起') : '今天' })
const scope = computed(() => data.value?.alerts.scope)
const showAll = computed(() => scopeOf(route.query.all) === 'all')
function setScope(all: boolean) { router.replace({ query: toggleScopeQuery(route.query as any, all) }) }
const agKey = computed(() => String(route.query.ag ?? ''))
const att = computed(() => data.value?.alerts.attention ?? null)
const attGroups = computed<AlertGroup[]>(() => (att.value?.groups ?? []).map(shape))
const attRows = computed(() => (att.value?.rows ?? []).map(shape))
const agGroup = computed(() => attGroups.value.find(g => g.group === agKey.value))
const abKey = computed(() => String(route.query.ab ?? ''))
const bucketLink = (k: string) => router.resolve({ path: route.path, query: bucketQuery(route.query as any, k) }).fullPath
const abBucket = computed(() => (att.value?.buckets ?? []).find((b: any) => b.key === abKey.value && b.key !== 'longterm'))
// 使用者 11-14：斷超過 N 小時還沒恢復的移出需要注意，另收一區（摺疊、可下鑽）
const longB = computed(() => (att.value?.buckets ?? []).find((b: any) => b.key === 'longterm'))
const longRows = computed(() => (longB.value?.rows ?? []).map(shape))
const routineBuckets = computed(() => (att.value?.buckets ?? []).filter((b: any) => b.key !== 'longterm'))
const routineN = computed(() => routineBuckets.value.reduce((s: number, b: any) => s + b.n, 0))
const abRows = computed(() => (abBucket.value?.rows ?? []).map(shape))
const flapRows = computed(() => att.value?.flapping ?? [])
const sugRows = ref<any[] | null>(null)
const sugErr = ref('')
watch(abKey, async (k) => {
  if (k !== 'suggest' || sugRows.value) return
  try { sugRows.value = (await apiFetch<{ rows: any[] }>('/api/v2/routine/suggestions')).rows } catch (e: any) { sugErr.value = `讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}, { immediate: true })
const hostLinkOf = (r: any) => (String(r.host_key || '').startsWith('ip:') ? `/v2/hosts?q=${encodeURIComponent(String(r.host_key).slice(3))}` : r.host_key && !String(r.host_key).startsWith('wug:') ? `/v2/machine/${encodeURIComponent(r.host_key)}?tab=ops` : null)
const attCountText = computed(() => attentionCount(data.value?.alerts as any))
const agRows = computed(() => groupRows(attRows.value, attGroups.value, agKey.value))
const groupLink = (g: AlertGroup) => router.resolve({ path: route.path, query: groupQuery(route.query as any, g) }).fullPath
const closeDrill = computed(() => { const { ag: _g, ...rest } = route.query; return { path: route.path, query: rest } })
const todayAlertRows = computed(() => (data.value?.alerts.today?.rows ?? []).map(shape))

// ===== 表格欄位：畫面上只留主值；IP、業務系統是主機下面的灰字，分級與聯絡五欄在明細與匯出 =====
const hostText = (r: any) => [r.who, r.ip].filter(Boolean).join(' ')       // 排序＋搜尋（可以直接搜 IP）
const HOST_COL = { k: 'who', label: '主機', text: hostText }
const MON_COL = { k: 'monitor', label: '監控項目' }
const REASON_COL = { k: 'judge_reason', label: '判斷理由', min: 160 }
const START_COL = { k: 'start_text', label: '開始', mono: true }
const END_COL = { k: 'end_text', label: '恢復', mono: true }
const NOTIFY_COL = { k: 'notify', label: '通知', text: (r: any) => r.notified_text || '' }   // R16：按了先預覽收件人與內容，確認才送
const X_IP = { k: 'ip', label: 'IP' }
const X_TIER = { k: 'tier_label', label: '分級' }
const X_SINCE = { k: 'since_text', label: '從哪天開始' }
const X_DUR = { k: 'dur_text', label: '持續' }
const X_SYS = { k: 'sys_text', label: '業務系統' }
const X_CONTACT = ALERT_CONTACT_COLS                                             // R15：告警聯絡五欄（沒有的寫「未提供」）
// 需要注意（一組一列）：件數可點（下鑽＝這一組的那幾筆）；只有一筆的組照樣能直接按「通知」
const ATT_COLS = [HOST_COL, MON_COL, REASON_COL, { k: 'n', label: '件數', mono: true, link: groupLink }, START_COL, NOTIFY_COL]
const ATT_X = [X_IP, X_TIER, X_SINCE, X_DUR, X_SYS, ...X_CONTACT]
const AG_COLS = [HOST_COL, MON_COL, REASON_COL, START_COL, NOTIFY_COL]
const BUCKET_COLS = [HOST_COL, MON_COL, REASON_COL, START_COL, END_COL]
const BUCKET_X = [X_IP, X_DUR, X_SYS]
const TODAY_COLS = [HOST_COL, MON_COL, START_COL, END_COL, NOTIFY_COL]
const TODAY_X = [X_IP, X_TIER, X_DUR, X_SYS, ...X_CONTACT]
const FLAP_COLS = [{ k: 'who', label: '主機', text: hostText }, { k: 'n', label: '今天斷了幾次', mono: true }, { k: 'events', label: '告警筆數', mono: true }, { k: 'reason', label: '判斷理由', min: 160 }]
const SUG_COLS = [{ k: 'who', label: '主機', link: hostLinkOf }, { k: 'ip', label: 'IP', mono: true }, { k: 'start_hhmm', label: '建議開始', mono: true },
  { k: 'end_hhmm', label: '建議結束', mono: true }, { k: 'days', label: '14 天內幾天', mono: true }, { k: 'reason', label: '理由', min: 160 }]
const TWO_SLOTS = ['cell-who', 'cell-monitor', 'cell-start_text', 'cell-end_text']

// 一格＝粗體主值＋一行灰字（V2Table 的 cell-<欄> 插槽共用；監控項目可點，展開這一列的明細）
const Cell2 = (p: { row: any; k: string }, { emit }: { emit: (e: 'open') => void }) => {
  const r = p.row
  const sub = (t: string) => (t ? h('span', { class: 't11-sub' }, t) : null)
  if (p.k === 'who') {
    const to = mlink(r)
    const name = h('b', r.who || r.ip || '未知主機')
    return h('span', { class: 't11-c2' }, [to ? h(NuxtLink, { to }, () => name) : name, sub([r.ip, r.sys_text].filter(Boolean).join('・'))])
  }
  if (p.k === 'monitor') return h('button', { type: 'button', class: 't11-lb', title: '看明細', onClick: () => emit('open') }, r.monitor || '看明細')
  if (p.k === 'start_text') {
    return h('span', { class: 't11-c2' }, [h('b', [r.start_text || '—', r.since_note ? h('span', { class: 't11-tag' }, r.since_text) : null]),
      sub(r.ended_at ? '' : `已持續 ${r.dur_text}`)])
  }
  return h('span', { class: 't11-c2' }, [h('b', r.end_text), sub(r.ended_at ? `持續 ${r.dur_text}` : `已持續 ${r.dur_text}`)])
}
Cell2.props = ['row', 'k']
Cell2.emits = ['open']

// 點監控項目＝明細（判斷依據、聯絡人、下一步按鈕）；同一列再點一次收起
const detail = ref<{ at: string; row: any } | null>(null)
function openDetail(at: string, row: any) { detail.value = detail.value?.at === at && detail.value.row?.id === row?.id ? null : { at, row } }
watch(() => route.fullPath, () => { detail.value = null })
const hasContact = (r: any) => ALERT_CONTACT_COLS.some(c => r[c.k] && r[c.k] !== '未提供')
const detailItems = (r: any): [string, string][] => ([
  ['監控項目', r.monitor], ['IP', r.ip], ['分級', r.tier_label], ['開始', r.start_text ? `${r.start_text}${r.since_note ? `・${r.since_text}` : ''}` : ''],
  ['恢復', r.end_text], ['持續', r.dur_text], ['判斷理由', r.judge_reason], ['業務系統', r.sys_text],
  ...(hasContact(r) ? ALERT_CONTACT_COLS.map(c => [c.label, r[c.k] || '未提供'] as [string, string]) : [['聯絡資料', '沒有登記'] as [string, string]]),
  ['通知', r.notified_text || '還沒通知'],
] as [string, string][]).filter(([, v]) => v != null && v !== '')

const notifyId = ref<number | null>(null)
const track = computed(() => data.value?.track)

// ===== 頁首一行結論＋要你處理 =====
const worst = computed(() => attGroups.value.reduce<any>((a, g) => (!a || Number(g.duration_min || 0) > Number(a.duration_min || 0) ? g : a), null))
const headline = computed(() => {
  const d = data.value
  if (!d) return ''
  const a = att.value
  const parts: string[] = []
  if (!a) parts.push(`告警${attCountText.value}`)
  else if (a.n) parts.push(`需要注意的告警 ${a.n} 件${worst.value ? `，最久的是 ${worst.value.who || worst.value.ip} ${worst.value.monitor || ''}，已斷 ${worst.value.dur_text}` : ''}`)
  else parts.push(a.blind ? '監控看不到告警資料，0 件不代表沒事' : '目前沒有需要注意的告警')
  if (d.incidents.n) parts.push(`今天的事件 ${d.incidents.n} 件`)
  if (d.opening.status === 'ok' && d.opening.hot) parts.push(`開盤尖峰 ${d.opening.hot} 台超過 ${d.opening.hot_pct ?? 80}%`)
  return parts.join('；')
})
const anchor = (id: string) => ({ path: route.path, query: route.query, hash: `#${id}` })
interface Todo { text: string; to: any; label: string }
const todos = computed<Todo[]>(() => {
  const d = data.value
  if (!d) return []
  const out: Todo[] = []
  const a = att.value
  if (a?.blind) out.push({ text: '告警資料中斷，看不到新的斷線', to: '/v2/incident?tab=wug', label: '看告警事件' })
  if (a?.n) out.push({ text: `${a.n} 件告警還沒恢復，需要注意`, to: anchor('att'), label: `看這 ${a.n} 件` })
  const dc = dcheck.value
  if (dc?.state === 'open' && dc.unconfirmed) out.push({ text: `每日檢查還有 ${dc.unconfirmed} 列待確認`, to: '/v2/dailycheck', label: '去確認' })
  else if (dc?.bad) out.push({ text: `每日檢查有 ${dc.bad} 列異常`, to: '/v2/dailycheck', label: '看是哪幾台' })
  else if (dc?.state === 'none') out.push({ text: '今天的每日檢查還沒產生', to: '/v2/dailycheck', label: '去看' })
  if (dc?.lp_alert) out.push({ text: 'WhatsUp 輪循資料可能中斷，每日檢查的數字可能不完整', to: '/v2/dailycheck', label: '看原因' })
  const g = d.changes.collect_gap
  if (g?.n && g?.link) out.push({ text: `${g.n} 台收集中斷，今天的資產異動可能不完整`, to: g.link, label: '看是哪幾台' })
  if (d.opening.status === 'ok' && d.opening.hot) out.push({ text: `開盤尖峰 ${d.opening.hot} 台 ESXi 的 CPU 超過 ${d.opening.hot_pct ?? 80}%`, to: anchor('opening'), label: '看是哪幾台' })
  return out
})
// 數字卡：點了跳到下面那一區
const cards = computed(() => {
  const d = data.value
  if (!d) return []
  const a = att.value
  return [
    { k: 'att', label: '需要注意', v: a ? `${a.n} 件` : attCountText.value, tone: a?.n || a?.blind ? 'bad' : '' },
    { k: 'today', label: '今天發生過的告警', v: d.alerts.today?.n == null ? countText(d.alerts) : `${d.alerts.today.n} 件`, tone: '' },
    { k: 'incidents', label: '今天的事件', v: countText(d.incidents), tone: d.incidents.n ? 'warn' : '' },
    { k: 'changes', label: '今天的資產異動', v: d.changes.n == null ? countText(d.changes) : `${d.changes.n} 筆`, tone: '' },
    { k: 'opening', label: `開盤尖峰超過 ${d.opening.hot_pct ?? 80}%`, v: d.opening.status === 'ok' ? `${d.opening.hot ?? 0} 台` : '還沒有資料', tone: d.opening.hot ? 'warn' : '' },
  ]
})
const INCIDENT_COLS = [{ k: 'title', label: '事件', link: (r: any) => r.link }, { k: 'type', label: '類型' }, { k: 'mode_label', label: '正式／演練' },
  { k: 'what', label: '今天的變化', text: (r: any) => String(r.what || '').replace(/今日/g, '今天') }, { k: 'state', label: '狀態' }, { k: 'started_at', label: '開始', mono: true }]
</script>

<template>
  <V2Page>
    <template #title><h1 class="h1">1.1 今日概況</h1></template>
    <template #conclusion>
      <span v-if="data" data-testid="today-headline">{{ headline }}
        <InfoNote>只放今天（台北時間 00:00 到現在）發生或變化的事。保固、單點風險、逾期未重開機、容量預警這類長期待辦在「追蹤處理」。<br>「0 件」＝來源有資料、今天真的沒有；「未收集」「未啟用」「資料中斷」＝來源沒有資料，不代表沒事。</InfoNote>
      </span>
    </template>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="data">
      <details class="srcs" data-testid="today-sources">
        <summary>資料更新於 <b class="mono">{{ hm(data.now) }}</b><span v-if="staleSrc.length" class="tag warn">{{ staleSrc.length }} 個來源沒更新</span></summary>
        <div class="srcl">
          <span v-for="s in data.sources" :key="s.key" class="src" :class="{ warn: !!s.state }">{{ s.label }} <b class="mono">{{ srcText(s) }}</b></span>
        </div>
      </details>

      <div class="todo" :class="{ ok: !todos.length }" data-testid="today-todo">
        <b v-if="todos.length">要你處理（{{ todos.length }} 件）</b><b v-else>沒有要你處理的事</b>
        <div v-for="(t, i) in todos" :key="i" class="it">
          <span class="n mono">{{ i + 1 }}</span><span>{{ t.text }}</span><span class="sp" />
          <NuxtLink class="btn" :to="t.to">{{ t.label }}</NuxtLink>
        </div>
      </div>

      <div class="cards">
        <NuxtLink v-for="c in cards" :key="c.k" :to="anchor(c.k)" class="card" :class="c.tone" :data-testid="`today-card-${c.k}`">
          <b class="mono">{{ c.v }}</b><span>{{ c.label }}</span></NuxtLink>
      </div>

      <NuxtLink to="/v2/track" class="trackline">
        <b>長期待辦</b>
        <span v-if="track?.total != null">{{ track.total }} 項<template v-if="track.high">，其中高 {{ track.high }}</template></span>
        <span v-else class="warnt">{{ track?.label || '無法計算' }}</span>
        <template v-if="track?.changes">
          <span v-if="track.changes.new == null" class="chg">{{ track.changes.reason }}</span>
          <span v-else class="chg">今天新增 {{ track.changes.new }}、解除 {{ track.changes.resolved }}<span v-if="track.changes.prev_day" class="sub">　跟 {{ track.changes.prev_day }} 比</span></span>
        </template>
        <span class="go">›</span>
      </NuxtLink>
      <NuxtLink to="/v2/dailycheck" class="trackline" data-testid="dc-line">
        <b>每日檢查</b>
        <span :class="{ warnt: dcheck?.state === 'none' || !!dcErr }">{{ dcErr || dcheck?.text || '讀取中…' }}</span>
        <span v-if="!dcErr && dcheck?.state === 'none' && dcheck?.prev" class="prevnote" data-testid="dc-prev">{{ dcheck.prev.note }}：{{ dcheck.prev.text }}</span>
        <span v-if="!dcErr && dcheck?.lp_alert" class="badt" data-testid="dc-lp-alert">{{ dcheck.lp_alert }}</span>
        <InfoNote>各項主機當天的狀態（系統判定）；異常、無資料點進去看是哪幾台。</InfoNote>
        <span class="go">›</span>
      </NuxtLink>
      <NuxtLink v-if="track?.changes?.resolved" to="/v2/track?view=resolved" class="reslink">看今天解除的 {{ track.changes.resolved }} 件 ›</NuxtLink>

      <div class="cols">
        <div class="col">
          <section id="att" class="blk">
            <div class="bh"><h2>需要注意</h2><span class="cnt" :class="{ na: !att || data.alerts.status === 'stale', hot: att && att.n > 0 }" data-testid="att-count">{{ attCountText }}</span>
              <span v-if="attentionBlind(data.alerts as any)" class="tag bad" data-testid="att-blind-tag">監控看不到</span>
              <NuxtLink to="/v2/incident?tab=wug" class="more">全部告警 ›</NuxtLink>
              <InfoNote>還沒恢復、斷線超過 {{ att?.min ?? 10 }} 分（開盤時段 {{ att?.open_min ?? 5 }} 分），而且不在這台例行時段內的告警；進來時推一次 Email／Teams，恢復時再推一次。其他的收在下面「例行與已自動恢復」。點「監控項目」看判斷依據與聯絡人；門檻與例行時段在主機頁「事件與維運紀錄」設定。</InfoNote>
            </div>
            <p v-if="att?.blind" class="blind" role="alert" data-testid="att-blind">{{ att.blind }}</p>
            <p v-else-if="data.alerts.label" class="na-line">{{ data.alerts.label }}</p>
            <div v-if="scope" class="scope" data-testid="alert-scope">
              <span>{{ scope.mode === 'policy' ? '範圍：Teams 通知範圍' : '範圍：全部' }}</span>
              <span v-if="scope.mode === 'policy' && scope.hidden" class="sub">另有 {{ scope.hidden }} 件不在通知範圍</span>
              <button v-if="!showAll && scope.keywords.length" type="button" class="mini" data-testid="scope-all" @click="setScope(true)">看全部</button>
              <button v-else-if="showAll && scope.keywords.length" type="button" class="mini" data-testid="scope-policy" @click="setScope(false)">只看通知範圍</button>
              <InfoNote>{{ scope.label }}。通知範圍＝事件應變 › 告警事件 › 設定 的「只推這些動作政策」，Teams 只推這些；沒記錄有沒有掛政策的照樣算在範圍內（寧可多列）。「看全部」會把不在範圍的也列出來。</InfoNote>
            </div>
            <V2Table v-if="att" :rows="attGroups" prefix="A" :max="10" :empty="att.blind ? '監控看不到告警資料，0 件不代表沒事' : '目前沒有需要注意的告警'" :cols="ATT_COLS" :export-cols="ATT_X" export-name="今日概況_需要注意">
              <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('att', row)" /></template>
              <template #cell-notify="{ row }">
                <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify && row.n === 1" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
              </template>
            </V2Table>
            <div v-if="detail?.at === 'att'" class="pv" data-testid="alert-detail">
              <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
              <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
              <div class="acts">
                <button v-if="data.can_notify && detail.row.n === 1" type="button" class="btn" @click="notifyId = detail.row.id">{{ detail.row.notified_n ? '重送通知' : '通知負責人' }}</button>
                <NuxtLink v-if="detail.row.n > 1" class="btn ghost" :to="groupLink(detail.row)">看這 {{ detail.row.n }} 筆</NuxtLink>
                <NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink>
              </div>
            </div>
            <div v-if="agKey" class="drill" data-testid="alert-group-drill">
              <div class="bh"><b>{{ agGroup ? groupTitle(agGroup) : '這一組已經不在需要注意裡' }}</b>
                <NuxtLink :to="closeDrill" class="more">收起</NuxtLink></div>
              <V2Table v-if="agGroup" :rows="agRows" prefix="R" :max="20" empty="這一組目前沒有告警" :cols="AG_COLS" :export-cols="ATT_X" export-name="今日概況_同批告警">
                <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('ag', row)" /></template>
                <template #cell-notify="{ row }">
                  <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
                </template>
              </V2Table>
              <div v-if="detail?.at === 'ag'" class="pv">
                <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
                <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
                <div class="acts">
                  <button v-if="data.can_notify" type="button" class="btn" @click="notifyId = detail.row.id">{{ detail.row.notified_n ? '重送通知' : '通知負責人' }}</button>
                  <NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink>
                </div>
              </div>
            </div>
            <details v-if="att && longB" class="routine" :open="abKey === 'longterm'" data-testid="att-longterm">
              <summary>長期未恢復 <span class="sub" data-testid="att-long-n">{{ longB.n }} 件・斷超過 {{ att.long_h }} 小時</span>
                <InfoNote>斷超過 {{ att.long_h }} 小時還沒恢復的移到這裡，不算在需要注意、不再推；推過「發生」的恢復時照樣推恢復。每日彙整只寫一行「長期未恢復 N 台」。小時數由管理員在主機頁「事件與維運紀錄」設定。</InfoNote></summary>
              <V2Table :rows="longRows" prefix="L" :max="20" empty="沒有長期未恢復的告警" :cols="BUCKET_COLS" :export-cols="BUCKET_X" export-name="今日概況_長期未恢復">
                <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('long', row)" /></template>
              </V2Table>
              <div v-if="detail?.at === 'long'" class="pv">
                <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
                <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
                <div class="acts"><NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink></div>
              </div>
            </details>
            <details v-if="att" class="routine" :open="!!abKey && abKey !== 'longterm'" data-testid="att-routine">
              <summary>例行與已自動恢復 <span class="sub">{{ routineN }} 件；反覆斷線 {{ flapRows.length }} 台<template v-if="att.suggestions">；待確認的例行時段 {{ att.suggestions }} 筆</template></span></summary>
              <div class="tiles">
                <NuxtLink v-for="b in routineBuckets" :key="b.key" :to="bucketLink(b.key)" class="tile" :class="{ on: abKey === b.key }" :data-testid="`att-b-${b.key}`">
                  <b class="mono">{{ b.n }}</b><span>{{ b.label }}</span></NuxtLink>
                <NuxtLink :to="bucketLink('flap')" class="tile" :class="{ on: abKey === 'flap' }" data-testid="att-b-flap"><b class="mono">{{ flapRows.length }} 台</b><span>反覆斷線</span></NuxtLink>
                <NuxtLink :to="bucketLink('suggest')" class="tile" :class="{ on: abKey === 'suggest' }" data-testid="att-b-suggest"><b class="mono">{{ att.suggestions == null ? '—' : `${att.suggestions} 筆` }}</b><span>待確認的例行時段</span></NuxtLink>
                <InfoNote>未滿門檻仍斷：還在等，滿 {{ att.min }} 分沒恢復就會列進需要注意。監控明細讀不到：收集問題，不是設備故障。例行時段內：管理員登記（或確認建議）的每天固定時段，時段結束還沒恢復就列進需要注意。門檻內自動恢復、例行時段內恢復、曾需注意已恢復：今天恢復的。反覆斷線：今天同一台斷了 3 次以上，不列需要注意，每日彙整寫一行「今天斷了 N 次」。待確認的例行時段：最近 14 天同一台同一時刻（前後 15 分）有 10 天以上斷過，管理員確認才生效，到主機頁確認或拒絕。</InfoNote>
              </div>
              <template v-if="abBucket">
                <V2Table :rows="abRows" prefix="B" :max="20" empty="這一類目前沒有告警" :cols="BUCKET_COLS" :export-cols="BUCKET_X" :export-name="`今日概況_${abBucket.label}`">
                  <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('bucket', row)" /></template>
                </V2Table>
                <div v-if="detail?.at === 'bucket'" class="pv">
                  <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
                  <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
                  <div class="acts"><NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink></div>
                </div>
              </template>
              <V2Table v-else-if="abKey === 'flap'" :rows="flapRows" prefix="F" :max="20" empty="今天沒有反覆斷線的主機" :cols="FLAP_COLS" :export-cols="[X_IP]" export-name="今日概況_反覆斷線">
                <template #cell-who="{ row }"><span class="c2"><b>{{ row.who || row.ip }}</b><span class="sub">{{ row.ip }}</span></span></template>
              </V2Table>
              <template v-else-if="abKey === 'suggest'">
                <p v-if="sugErr" class="err">{{ sugErr }}</p>
                <V2Table v-else-if="sugRows" :rows="sugRows" prefix="S" :max="20" empty="目前沒有待確認的例行時段" :cols="SUG_COLS" export-name="今日概況_待確認的例行時段" />
                <p v-else class="sub">讀取中…</p>
              </template>
            </details>
          </section>

          <section id="today" class="blk">
            <div class="bh"><h2>今天發生過的告警</h2><span class="cnt" :class="{ na: data.alerts.today?.n == null }">{{ data.alerts.today?.n == null ? countText(data.alerts) : `${data.alerts.today.n} 件` }}</span>
              <span v-if="data.alerts.today?.n != null" class="sub">仍未恢復 {{ data.alerts.today.still_open }}・已恢復 {{ data.alerts.today.recovered }}</span>
              <InfoNote>今天 00:00 以後開始的告警，含已經恢復的。件數＝下面的列數。點「監控項目」看分級與聯絡人。</InfoNote>
            </div>
            <p v-if="data.alerts.today?.label" class="na-line">{{ data.alerts.today.label }}</p>
            <V2Table v-if="data.alerts.today?.n != null" :rows="todayAlertRows" prefix="D" :max="10" empty="今天沒有發生告警" :cols="TODAY_COLS" :export-cols="TODAY_X">
              <template v-for="s in TWO_SLOTS" #[s]="{ row }"><Cell2 :row="row" :k="s.slice(5)" @open="openDetail('today', row)" /></template>
              <template #cell-notify="{ row }">
                <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
              </template>
            </V2Table>
            <div v-if="detail?.at === 'today'" class="pv">
              <div class="pvh"><b>{{ detail.row.who || detail.row.ip || '未知主機' }}</b><button type="button" class="lb" @click="detail = null">收起</button></div>
              <dl><template v-for="[k, v] in detailItems(detail.row)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
              <div class="acts">
                <button v-if="data.can_notify" type="button" class="btn" @click="notifyId = detail.row.id">{{ detail.row.notified_n ? '重送通知' : '通知負責人' }}</button>
                <NuxtLink v-if="mlink(detail.row)" class="btn ghost" :to="mlink(detail.row)!">看這台主機</NuxtLink>
              </div>
            </div>
            <V2AlertNotify :event-id="notifyId" @close="notifyId = null" @sent="load" />
          </section>

          <section id="incidents" class="blk">
            <div class="bh"><h2>今天的事件</h2><span class="cnt" :class="{ na: data.incidents.n == null }">{{ countText(data.incidents) }}</span>
              <NuxtLink to="/v2/incident" class="more">事件應變 ›</NuxtLink>
              <InfoNote>事件應變裡今天開立或今天結案的事件。還沒結案的舊事件在「追蹤處理」的「進行中事件」。</InfoNote>
            </div>
            <p v-if="data.incidents.label" class="na-line">{{ data.incidents.label }}</p>
            <V2Table v-if="data.incidents.n != null" :rows="data.incidents.rows" prefix="E" :max="10" empty="今天沒有事件" :cols="INCIDENT_COLS" />
          </section>

          <section id="changes" class="blk">
            <div class="bh"><h2>今天的資產異動</h2><span class="cnt" :class="{ na: data.changes.n == null || data.changes.status === 'stale' }">{{ countText(data.changes) }}</span>
              <span v-if="data.changes.as_of" class="sub">快照 {{ data.changes.as_of }}<template v-if="data.changes.prev_date">・跟 {{ data.changes.prev_date }} 比</template></span>
              <NuxtLink to="/v2/changes?tab=daily" class="more">異動與異常 ›</NuxtLink>
              <InfoNote>今天那份每日快照跟前一份的比對（跟「異動與異常」的每日異動同一份計算，點分類看明細）。今天還沒產生快照寫「未收集」；兩份快照之間掃描、收集、匯入都沒有新資料時，比出來的數字不代表沒有異動，會標出來。</InfoNote>
            </div>
            <p v-if="data.changes.label" class="na-line"><template v-for="(p, i) in gapParts(data.changes)" :key="i"><NuxtLink v-if="p.link" :to="p.link" data-testid="chg-gap-link">{{ p.t }}</NuxtLink><template v-else>{{ p.t }}</template></template></p>
            <V2Table v-if="data.changes.n != null" :rows="data.changes.rows" prefix="C" :max="20" empty="今天沒有資產異動"
                     :cols="[{ k: 'label', label: '分類', link: (r: any) => r.link }, { k: 'n', label: '筆數', mono: true, link: (r: any) => r.link }]" />
          </section>
        </div>

        <div class="col">
          <section id="opening" class="blk">
            <div class="bh"><h2>{{ data.opening.status !== 'ok' && data.opening.prev ? `開盤尖峰 ${data.opening.prev.day}` : '今日開盤尖峰 09:00–09:30' }}</h2><span v-if="data.opening.status !== 'ok'" class="cnt na">{{ data.opening.status === 'pending' ? '尚未匯入' : data.opening.status === 'holiday' ? '休市' : '無法計算' }}</span>
              <span v-else class="sub">收盤後才有資料</span>
              <InfoNote>ESXi 主機開盤時段（09:00～09:30）的 CPU 使用率最高／平均／最低，依最高排序，與該主機近 20 個工作日比較。資料由跳板機收盤後從 Aria Operations 匯出送進來，所以收盤前這一區不放數字。<template v-if="data.opening.last_day">最近一次收到的是 {{ data.opening.last_day }}。</template></InfoNote>
            </div>
            <template v-if="data.opening.status !== 'ok' && data.opening.prev">
              <p class="prevnote" data-testid="op-prev">這是 {{ data.opening.prev.day }} 的資料，收到 {{ String(data.opening.prev.collected_at || '').slice(0, 16) || '—' }}；{{ data.opening.status === 'holiday' ? '今日休市' : data.opening.status === 'pending' ? '今日尚未匯入' : '今日無法計算' }}</p>
              <V2OpeningPeak :data="data.opening.prev" />
            </template>
            <V2OpeningPeak v-else :data="data.opening" />
          </section>
        </div>
      </div>

      <div class="quick">
        <NuxtLink to="/v2/incident" class="qb danger"><b>事件應變</b><span>停機影響評估、通報聯絡、檢核清單</span></NuxtLink>
        <NuxtLink to="/v2/onboard" class="qb"><b>納管作業</b><span>新設備納管與收集</span></NuxtLink>
        <NuxtLink to="/v2/datafix" class="qb"><b>待補資料</b><span>CIA 缺漏與待更正</span></NuxtLink>
      </div>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.err { color: var(--bad); }
.mono { font-family: var(--disp); }
.srcs { font-size: 13px; color: var(--ink-soft); margin-top: -8px; }
.srcs summary { cursor: pointer; color: var(--ink-aux); }
.srcl { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; margin-top: 6px; }
.src.warn b { color: var(--warn-text); }
.tag { display: inline-block; margin-left: 8px; padding: 0 8px; border-radius: 999px; font-size: 12px; white-space: nowrap; }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.tag.bad { background: var(--bad-soft); color: var(--bad); margin-left: 0; }
.todo { display: flex; flex-direction: column; gap: 4px; padding: 10px 14px; background: var(--card); border: 1px solid var(--border); border-left: 5px solid var(--warn); border-radius: var(--radius); font-size: 14px; }
.todo.ok { border-left-color: var(--good); }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 4px 0; border-top: 1px solid var(--line); }
.todo .n { font-weight: 700; white-space: nowrap; }
.todo .sp { flex: 1; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 14px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; display: inline-block; white-space: nowrap; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.card { display: flex; flex-direction: column; gap: 2px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; color: var(--ink); text-decoration: none; }
.card:hover { border-color: var(--brand); }
.card b { font-size: 22px; }
.card span { font-size: 13px; color: var(--ink-aux); }
.card.bad b { color: var(--bad); }
.card.warn b { color: var(--warn-text); }
.trackline { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; text-decoration: none; color: var(--ink); background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 16px; }
.trackline:hover { border-color: var(--brand); }
.trackline .chg { color: var(--ink-soft); font-size: 13px; }
.trackline .go { margin-left: auto; color: var(--brand); font-size: 18px; }
.warnt { color: var(--warn-text); }
.badt { color: var(--bad); font-size: 13px; }
.prevnote { margin: 0 0 6px; color: var(--warn-text); font-size: 13px; }
.cols { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; align-items: start; }
@media (max-width: 1100px) { .cols { grid-template-columns: 1fr; } }
.col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.blk { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; min-width: 0; scroll-margin-top: 16px; }
.bh { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.bh h2 { margin: 0; font-size: 16px; }
.cnt { font-family: var(--disp); font-weight: 700; color: var(--brand-dark); }
.cnt.na { color: var(--warn-text); font-weight: 600; font-family: inherit; }
.sub { font-size: 13px; color: var(--ink-aux); }
.more { margin-left: auto; font-size: 13px; color: var(--link); text-decoration: none; }
.na-line { margin: 0; padding: 8px 10px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.quick { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 760px) { .quick { grid-template-columns: 1fr; } }
.qb { text-decoration: none; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; color: var(--ink); }
.qb b { font-size: 16px; }
.qb span { font-size: 13px; color: var(--ink-aux); }
.qb:hover { border-color: var(--brand); }
.qb.danger { border-left: 4px solid var(--v2-danger); }
.reslink { font-size: 13px; color: var(--link); text-decoration: none; margin-top: -8px; }
.blind { margin: 0; padding: 8px 10px; border-radius: 8px; background: var(--bad-soft); color: var(--bad); font-weight: 600; font-size: 14px; }
.cnt.hot { color: var(--bad); }
.routine { border-top: 1px dashed var(--line); padding-top: 8px; }
.routine summary { cursor: pointer; font-weight: 600; font-size: 14px; color: var(--ink-soft); }
.tiles { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }
.tile { display: flex; flex-direction: column; align-items: center; min-width: 96px; padding: 6px 12px; border-radius: 12px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink); text-decoration: none; font-size: 13px; }
.tile b { font-size: 20px; }
.tile.on { outline: 2px solid var(--brand); }
.scope { margin: 0; display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; font-size: 13px; color: var(--ink-soft); }
.drill { border-top: 1px dashed var(--line); padding-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.nt { display: inline-flex; gap: 6px; align-items: center; }
.mini { font-family: inherit; font-size: 12px; padding: 1px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.c2 { display: flex; flex-direction: column; }
.c2 b { color: var(--ink); }
.pv { border: 1px solid var(--brand); border-radius: 12px; padding: 10px 14px; background: var(--card); display: flex; flex-direction: column; gap: 8px; }
.pvh { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.pv dl { display: grid; grid-template-columns: 110px 1fr; gap: 4px 12px; margin: 0; font-size: 13px; }
.pv dt { color: var(--ink-aux); }
.pv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font: inherit; font-size: 13px; padding: 0; text-decoration: underline; }
</style>

<style>
/* Cell2（render function）不吃 scoped 屬性，類名前綴 t11- 避免外溢 */
.t11-c2 { display: flex; flex-direction: column; gap: 1px; }
.t11-c2 b { color: var(--ink); font-weight: 700; }
.t11-sub { font-size: 12px; color: var(--ink-aux); }
.t11-tag { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 11.5px; font-weight: 600; }
.t11-lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-align: left; text-decoration: underline dotted; }
</style>
