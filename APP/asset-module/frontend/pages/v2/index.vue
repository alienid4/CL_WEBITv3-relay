<script setup lang="ts">
// 1.1 今日概況（2.044.0，需求 R9，使用者 2026-11-04：「今日概況應該是哪台主機有報修、有報告警…這才是今日；保固到期每天都 30 台，
// 一年才做完，永遠放首頁不恰當」）。只放台北時間 00:00 到現在的事；長期待辦搬到 1.4 追蹤處理，這裡只留一行入口＋今天的變化。
// 資料＝/api/v2/today（v2_today.today，組裝既有計算）：
//   現在的告警＝wug_alert 告警事件（還開著）；今天的事件＝4.1 同一張表；今天的資產異動＝1.2 每日異動同一份比對；
//   開盤尖峰（2.045.0）＝跳板機收盤後送來的 Aria ESXi 檔（V2OpeningPeak）；今天還沒收到只寫「尚未匯入」，不放假數字。
// 0 件和「未收集／未啟用／資料中斷」分開顯示：n 是 null＝來源沒資料（寫原因），n 是 0＝真的 0 件。
// 舊網址：?tab=todo（原待處理事項）→ 1.4 追蹤處理；?tab=week（原本週異動）→ 1.2 異動與異常「本週異動」頁籤。
import { ALERT_CONTACT_COLS } from '~/utils/alertContact'
// Q9（2.066.x）：告警預設只列跟 Teams 同一個主機組政策範圍（?all=1 看全部）；同一分鐘＋同一來源收成一列，點件數（?ag=）列出那幾筆
import { scopeOf, toggleScopeQuery, groupQuery, groupRows, groupTitle, bucketQuery, attentionCount } from '~/utils/alertScope'
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
// 1.5 每日檢查（試辦）今天的狀態一行；讀不到就寫原因，不擋首頁
const dcheck = ref<{ state: string; text: string; prev?: any; lp_alert?: string } | null>(null)
const dcErr = ref('')
onMounted(async () => { try { dcheck.value = await apiFetch<any>('/api/daily-check/today') } catch (e: any) { dcErr.value = `讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } })

const hm = (t: string | null | undefined) => (t ? String(t).slice(11, 16) || String(t) : '')
const srcText = (s: Today['sources'][number]) => (s.state ? s.state : (s.as_of ? (s.as_of.slice(0, 10) === data.value?.date ? hm(s.as_of) : s.as_of.slice(0, 16)) : '—'))
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
// 告警兩段（2.044.3，GPT A2＋B1）：現在仍在告警（不管哪天開始，昨天開始的標「昨日起」）／今天發生過（今天 00:00 後開始，含已恢復）
const shape = (r: any) => ({ ...r, dur_text: dur(r.duration_min), start_text: String(r.started_at || '').slice(5, 16), end_text: r.ended_at ? String(r.ended_at).slice(5, 16) : '未恢復',
  sys_text: r.api_id ? `${r.api_id} ${r.system_name || ''}`.trim() : '未登記業務系統', since_text: r.since_note || '今日' })
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
const TAIL_COLS = [
  { k: 'sys_text', label: '業務系統' },
  ...ALERT_CONTACT_COLS,                                                       // R15：告警聯絡五欄（沒有的寫「未提供」）
  { k: 'notify', label: '通知', text: (r: any) => r.notified_text || '' },      // R16：按了先預覽收件人與內容，確認才送
]
const ALERT_COLS = [
  { k: 'who', label: '主機', link: mlink }, { k: 'ip', label: 'IP', mono: true }, { k: 'monitor', label: '監控項目' },
  { k: 'tier_label', label: '分級' }, { k: 'start_text', label: '開始', mono: true }, { k: 'since_text', label: '起於' }, { k: 'dur_text', label: '持續', mono: true },
  ...TAIL_COLS,
]
// 收合後一組一列：件數可點（下鑽＝這一組的那幾筆）；只有一筆的組照樣能直接按「通知」
const GROUP_COLS = [
  { k: 'who', label: '主機', link: mlink }, { k: 'ip', label: 'IP', mono: true }, { k: 'monitor', label: '監控項目' },
  { k: 'n', label: '件數', mono: true, link: groupLink }, { k: 'tier_label', label: '分級' }, { k: 'start_text', label: '開始', mono: true },
  { k: 'since_text', label: '起於' }, { k: 'dur_text', label: '持續', mono: true },
  ...TAIL_COLS,
]
const REASON_COL = { k: 'judge_reason', label: '判斷理由', min: 160 }
const ATT_COLS = [GROUP_COLS[0], GROUP_COLS[1], GROUP_COLS[2], GROUP_COLS[3], REASON_COL, ...GROUP_COLS.slice(4)]
const BUCKET_COLS = [
  { k: 'who', label: '主機', link: mlink }, { k: 'ip', label: 'IP', mono: true }, { k: 'monitor', label: '監控項目' }, REASON_COL,
  { k: 'start_text', label: '開始', mono: true }, { k: 'end_text', label: '恢復', mono: true }, { k: 'dur_text', label: '持續', mono: true }, { k: 'sys_text', label: '業務系統' },
]
const FLAP_COLS = [{ k: 'who', label: '主機' }, { k: 'ip', label: 'IP', mono: true }, { k: 'n', label: '今天斷了幾次', mono: true }, { k: 'events', label: '告警筆數', mono: true }, { k: 'reason', label: '判斷理由', min: 160 }]
const SUG_COLS = [{ k: 'who', label: '主機', link: hostLinkOf }, { k: 'ip', label: 'IP', mono: true }, { k: 'start_hhmm', label: '建議開始', mono: true },
  { k: 'end_hhmm', label: '建議結束', mono: true }, { k: 'days', label: '14 天內幾天', mono: true }, { k: 'reason', label: '理由', min: 160 }]
const TODAY_COLS = [
  { k: 'who', label: '主機', link: mlink }, { k: 'ip', label: 'IP', mono: true }, { k: 'monitor', label: '監控項目' },
  { k: 'tier_label', label: '分級' }, { k: 'start_text', label: '開始', mono: true }, { k: 'end_text', label: '恢復', mono: true }, { k: 'dur_text', label: '持續', mono: true },
  ...TAIL_COLS,
]
const notifyId = ref<number | null>(null)
const track = computed(() => data.value?.track)
const trackChange = computed(() => {
  const c = track.value?.changes
  if (!c) return ''
  if (c.new == null) return c.reason || ''
  return `今日新增 ${c.new}・今日解除 ${c.resolved}（對比 ${c.prev_day}）`
})
</script>

<template>
  <V2Page>
    <template #title><h1 class="h1">1.1 今日概況</h1></template>
    <template #conclusion>
      <span v-if="data">台北時間 {{ data.since }}～{{ hm(data.now) }}
        <InfoNote>只放今天（台北時間 00:00 到現在）發生或變化的事。保固、單點風險、逾期未重開機、容量預警這類長期待辦在 1.4 追蹤處理，這裡只留一行入口與今天的變化。<br>「0 件」＝來源有資料、今天真的沒有；「未收集」「未啟用」「資料中斷」＝來源沒有資料，不代表沒事。</InfoNote>
      </span>
    </template>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="data">
      <div class="srcs">
        <span class="st">資料時間</span>
        <span v-for="s in data.sources" :key="s.key" class="src" :class="{ warn: !!s.state }">{{ s.label }} <b class="mono">{{ srcText(s) }}</b></span>
      </div>

      <NuxtLink to="/v2/track" class="trackline">
        <b>長期待辦：1.4 追蹤處理</b>
        <span v-if="track?.total != null">共 {{ track.total }} 項<template v-if="track.high">（高 {{ track.high }}）</template></span>
        <span v-else class="warnt">{{ track?.label || '無法計算' }}</span>
        <span class="chg">{{ trackChange }}</span>
        <span class="go">›</span>
      </NuxtLink>
      <NuxtLink to="/v2/dailycheck" class="trackline" data-testid="dc-line">
        <b>每日檢查：1.5</b>
        <span :class="{ warnt: dcheck?.state === 'none' || !!dcErr }">{{ dcErr || dcheck?.text || '讀取中…' }}</span>
        <span v-if="!dcErr && dcheck?.state === 'none' && dcheck?.prev" class="prevnote" data-testid="dc-prev">{{ dcheck.prev.note }}：{{ dcheck.prev.text }}</span>
        <span v-if="!dcErr && dcheck?.lp_alert" class="badt" data-testid="dc-lp-alert">{{ dcheck.lp_alert }}</span>
        <InfoNote>試辦中：跟現行手填的 xServer 每日檢查紀錄並行 5 個工作天，試辦期間以手填表為準。系統預填、由人逐列確認後送出。</InfoNote>
        <span class="go">›</span>
      </NuxtLink>
      <NuxtLink v-if="track?.changes?.resolved" to="/v2/track?view=resolved" class="reslink">看今日解除的 {{ track.changes.resolved }} 件 ›</NuxtLink>

      <div class="cols">
        <div class="col">
          <section class="blk">
            <div class="bh"><h2>需要注意</h2><span class="cnt" :class="{ na: !att || data.alerts.status === 'stale', hot: att && att.n > 0 }" data-testid="att-count">{{ attCountText }}</span>
              <NuxtLink to="/v2/incident?tab=wug" class="more">全部告警 ›</NuxtLink>
              <InfoNote>只列「斷線滿門檻（非開盤 {{ att?.min ?? 10 }} 分、開盤 {{ att?.open_min ?? 5 }} 分）還沒恢復、而且不在這台例行時段內」的告警；例行時段只管時段開始之後才斷的，時段結束還沒恢復，那一刻起就列進來；已推過通知的一直留到恢復。設備不是 Down、只是讀不到監控明細的先當收集問題，超過 60 分也列進來。Email／Teams 也只在進入這裡時推一次、恢復時推一次。其他（未滿門檻、例行時段內、門檻內自動恢復、反覆斷線）收在下面「例行／已自動恢復」，件數可點，資料都還在。每一列的「判斷理由」寫為什麼列在這裡。同一分鐘、同一台的多個監控項目收成一列，「件數」點下去列出是哪幾筆。門檻與例行時段由管理員在主機頁「事件與維運紀錄」設定。</InfoNote>
            </div>
            <p v-if="att?.blind" class="blind" role="alert" data-testid="att-blind">{{ att.blind }}</p>
            <p v-else-if="data.alerts.label" class="na-line">{{ data.alerts.label }}</p>
            <div v-if="scope" class="scope" data-testid="alert-scope">
              <span>{{ scope.mode === 'policy' ? '範圍：Teams 通知範圍' : '範圍：全部' }}</span>
              <span v-if="scope.mode === 'policy' && scope.hidden" class="sub">另有 {{ scope.hidden }} 件不在通知範圍</span>
              <button v-if="!showAll && scope.keywords.length" type="button" class="mini" data-testid="scope-all" @click="setScope(true)">看全部</button>
              <button v-else-if="showAll && scope.keywords.length" type="button" class="mini" data-testid="scope-policy" @click="setScope(false)">只看通知範圍</button>
              <InfoNote>{{ scope.label }}。通知範圍＝4.1 事件應變「告警事件 → 設定」的「只推這些動作政策」，Teams 只推這些；沒記錄有沒有掛政策的照樣算在範圍內（寧可多列）。「看全部」會把不在範圍的也列出來，網址帶 all=1 可以直接分享。</InfoNote>
            </div>
            <V2Table v-if="att" :rows="attGroups" prefix="A" :max="10" :empty="att.blind ? '監控盲區：看不到不代表沒事' : '目前沒有需要注意的告警'" :cols="ATT_COLS" export-name="今日概況_需要注意">
              <template #cell-notify="{ row }">
                <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify && row.n === 1" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
              </template>
            </V2Table>
            <div v-if="agKey" class="drill" data-testid="alert-group-drill">
              <div class="bh"><b>{{ agGroup ? groupTitle(agGroup) : '這一組已經不在需要注意裡' }}</b>
                <NuxtLink :to="closeDrill" class="more">收起</NuxtLink></div>
              <V2Table v-if="agGroup" :rows="agRows" prefix="R" :max="20" empty="無" :cols="[...ALERT_COLS.slice(0, 3), REASON_COL, ...ALERT_COLS.slice(3)]" export-name="今日概況_同批告警">
                <template #cell-notify="{ row }">
                  <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
                </template>
              </V2Table>
            </div>
            <details v-if="att && longB" class="routine" :open="abKey === 'longterm'" data-testid="att-longterm">
              <summary>長期未恢復（超過 {{ att.long_h }} 小時） <span class="sub" data-testid="att-long-n">{{ longB.n }} 件</span>
                <InfoNote>斷超過 {{ att.long_h }} 小時還沒恢復的移到這裡，不算在需要注意、不再推；推過「發生」的恢復時照樣推恢復。每日彙整只寫一行「長期未恢復 N 台」。小時數由管理員在主機頁「事件與維運紀錄」設定。</InfoNote></summary>
              <V2Table :rows="longRows" prefix="L" :max="20" empty="沒有長期未恢復的告警" :cols="BUCKET_COLS" export-name="今日概況_長期未恢復" />
            </details>
            <details v-if="att" class="routine" :open="!!abKey && abKey !== 'longterm'" data-testid="att-routine">
              <summary>例行／已自動恢復 <span class="sub">{{ routineBuckets.reduce((s: number, b: any) => s + b.n, 0) }} 件・反覆斷線 {{ flapRows.length }} 台<template v-if="att.suggestions">・建議例行 {{ att.suggestions }} 筆</template></span></summary>
              <div class="tiles">
                <NuxtLink v-for="b in routineBuckets" :key="b.key" :to="bucketLink(b.key)" class="tile" :class="{ on: abKey === b.key }" :data-testid="`att-b-${b.key}`">
                  <b class="mono">{{ b.n }}</b><span>{{ b.label }}</span></NuxtLink>
                <NuxtLink :to="bucketLink('flap')" class="tile" :class="{ on: abKey === 'flap' }" data-testid="att-b-flap"><b class="mono">{{ flapRows.length }}</b><span>反覆斷線（台）</span></NuxtLink>
                <NuxtLink :to="bucketLink('suggest')" class="tile" :class="{ on: abKey === 'suggest' }" data-testid="att-b-suggest"><b class="mono">{{ att.suggestions ?? '—' }}</b><span>建議例行（待確認）</span></NuxtLink>
                <InfoNote>未滿門檻仍斷：還在等，滿 {{ att.min }} 分沒恢復就會列進需要注意。監控明細讀不到：收集問題，不是設備故障。例行時段內：管理員登記（或確認建議）的每天固定時段，時段結束還沒恢復就列進需要注意。門檻內自動恢復、例行時段內恢復、曾需注意已恢復：今天恢復的。反覆斷線：今天同一台斷了 3 次以上，不列需要注意，每日彙整寫一行「今天斷了 N 次」。建議例行：最近 14 天同一台同一時刻（±15 分）有 10 天以上斷過，管理員確認才生效，到主機頁確認或拒絕。</InfoNote>
              </div>
              <template v-if="abBucket">
                <V2Table :rows="abRows" prefix="B" :max="20" empty="無" :cols="BUCKET_COLS" :export-name="`今日概況_${abBucket.label}`" />
              </template>
              <V2Table v-else-if="abKey === 'flap'" :rows="flapRows" prefix="F" :max="20" empty="今天沒有反覆斷線的主機" :cols="FLAP_COLS" export-name="今日概況_反覆斷線" />
              <template v-else-if="abKey === 'suggest'">
                <p v-if="sugErr" class="err">{{ sugErr }}</p>
                <V2Table v-else-if="sugRows" :rows="sugRows" prefix="S" :max="20" empty="目前沒有建議" :cols="SUG_COLS" export-name="今日概況_建議例行" />
                <p v-else class="sub">讀取中…</p>
              </template>
            </details>
          </section>

          <section class="blk">
            <div class="bh"><h2>今天發生過的告警</h2><span class="cnt" :class="{ na: data.alerts.today?.n == null }">{{ data.alerts.today?.n == null ? countText(data.alerts) : `${data.alerts.today.n} 件` }}</span>
              <span v-if="data.alerts.today?.n != null" class="sub">仍未恢復 {{ data.alerts.today.still_open }}・已恢復 {{ data.alerts.today.recovered }}</span>
              <InfoNote>今天 00:00 以後開始的告警，含已經恢復的，列開始、恢復時間與持續多久。件數＝下面的列數。</InfoNote>
            </div>
            <p v-if="data.alerts.today?.label" class="na-line">{{ data.alerts.today.label }}</p>
            <V2Table v-if="data.alerts.today?.n != null" :rows="todayAlertRows" prefix="D" :max="10" empty="今天沒有發生告警" :cols="TODAY_COLS">
              <template #cell-notify="{ row }">
                <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
              </template>
            </V2Table>
            <V2AlertNotify :event-id="notifyId" @close="notifyId = null" @sent="load" />
          </section>

          <section class="blk">
            <div class="bh"><h2>今天的事件</h2><span class="cnt" :class="{ na: data.incidents.n == null }">{{ countText(data.incidents) }}</span>
              <NuxtLink to="/v2/incident" class="more">4.1 事件應變 ›</NuxtLink>
              <InfoNote>4.1 事件應變裡今天開立或今天結案的事件。還沒結案的舊事件在 1.4 追蹤處理「進行中事件」。</InfoNote>
            </div>
            <p v-if="data.incidents.label" class="na-line">{{ data.incidents.label }}</p>
            <V2Table v-if="data.incidents.n != null" :rows="data.incidents.rows" prefix="E" :max="10" empty="今天沒有事件"
                     :cols="[{ k: 'title', label: '事件', link: (r: any) => r.link }, { k: 'type', label: '類型' }, { k: 'mode_label', label: '性質' }, { k: 'what', label: '今日' }, { k: 'state', label: '狀態' }, { k: 'started_at', label: '開始', mono: true }]" />
          </section>

          <section class="blk">
            <div class="bh"><h2>今天的資產異動</h2><span class="cnt" :class="{ na: data.changes.n == null || data.changes.status === 'stale' }">{{ countText(data.changes) }}</span>
              <span v-if="data.changes.as_of" class="sub">快照 {{ data.changes.as_of }}<template v-if="data.changes.prev_date">・對比 {{ data.changes.prev_date }}</template></span>
              <NuxtLink to="/v2/changes?tab=daily" class="more">1.2 異動與異常 ›</NuxtLink>
              <InfoNote>今天那份每日快照跟前一份的比對（1.2「每日異動」同一份計算，點分類看明細）。今天還沒產生快照寫「未收集」；兩份快照之間掃描、收集、匯入都沒有新資料時，比出來的數字不代表沒有異動，會標出來。</InfoNote>
            </div>
            <p v-if="data.changes.label" class="na-line"><template v-for="(p, i) in gapParts(data.changes)" :key="i"><NuxtLink v-if="p.link" :to="p.link" data-testid="chg-gap-link">{{ p.t }}</NuxtLink><template v-else>{{ p.t }}</template></template></p>
            <V2Table v-if="data.changes.n != null" :rows="data.changes.rows" prefix="C" :max="20" empty="今天沒有資產異動"
                     :cols="[{ k: 'label', label: '分類', link: (r: any) => r.link }, { k: 'n', label: '筆數', mono: true, link: (r: any) => r.link }]" />
          </section>
        </div>

        <div class="col">
          <section class="blk">
            <div class="bh"><h2>{{ data.opening.status !== 'ok' && data.opening.prev ? `開盤尖峰（${data.opening.prev.day}）` : '今日開盤尖峰（09:00～09:30，收盤後回補）' }}</h2><span v-if="data.opening.status !== 'ok'" class="cnt na">{{ data.opening.status === 'pending' ? '尚未匯入' : data.opening.status === 'holiday' ? '休市' : '無法計算' }}</span>
              <InfoNote>ESXi 主機開盤時段（09:00～09:30）的 CPU 使用率 Max／Avg／Min，依 Max 排序，與該主機近 20 個工作日比較。資料由跳板機收盤後從 Aria Operations（vCOps）匯出、經中繼送進來，所以收盤前這一區不放數字。<template v-if="data.opening.last_day">最近一次收到的是 {{ data.opening.last_day }}。</template></InfoNote>
            </div>
            <template v-if="data.opening.status !== 'ok' && data.opening.prev">
              <p class="prevnote" data-testid="op-prev">這是 {{ data.opening.prev.day }} 的資料（收到 {{ String(data.opening.prev.collected_at || '').slice(0, 16) || '—' }}）；{{ data.opening.status === 'holiday' ? '今日休市' : data.opening.status === 'pending' ? '今日尚未匯入' : '今日無法計算' }}</p>
              <V2OpeningPeak :data="data.opening.prev" />
            </template>
            <V2OpeningPeak v-else :data="data.opening" />
          </section>
        </div>
      </div>

      <div class="quick">
        <NuxtLink to="/v2/incident" class="qb danger"><b>4.1 事件應變</b><span>停機影響評估、通報聯絡、檢核清單</span></NuxtLink>
        <NuxtLink to="/v2/onboard" class="qb"><b>6.1 納管作業</b><span>新設備納管與收集</span></NuxtLink>
        <NuxtLink to="/v2/datafix" class="qb"><b>9.1 待補資料</b><span>CIA 缺漏與待更正</span></NuxtLink>
      </div>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.err { color: var(--bad); }
.mono { font-family: var(--disp); }
.srcs { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; font-size: 13px; color: var(--ink-soft); }
.srcs .st { color: var(--ink-aux); }
.src.warn b { color: var(--warn-text); }
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
.blk { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
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
</style>
