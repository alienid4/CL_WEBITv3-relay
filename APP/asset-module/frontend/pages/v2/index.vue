<script setup lang="ts">
// 1.1 今日概況（2.044.0，需求 R9，使用者 2026-11-04：「今日概況應該是哪台主機有報修、有報告警…這才是今日；保固到期每天都 30 台，
// 一年才做完，永遠放首頁不恰當」）。只放台北時間 00:00 到現在的事；長期待辦搬到 1.4 追蹤處理，這裡只留一行入口＋今天的變化。
// 資料＝/api/v2/today（v2_today.today，組裝既有計算）：
//   現在的告警＝wug_alert 告警事件（還開著）；今天的事件＝4.1 同一張表；今天的資產異動＝1.2 每日異動同一份比對；
//   開盤尖峰（2.045.0）＝跳板機收盤後送來的 Aria ESXi 檔（V2OpeningPeak）；今天還沒收到只寫「尚未匯入」，不放假數字。
// 0 件和「未收集／未啟用／資料中斷」分開顯示：n 是 null＝來源沒資料（寫原因），n 是 0＝真的 0 件。
// 舊網址：?tab=todo（原待處理事項）→ 1.4 追蹤處理；?tab=week（原本週異動）→ 1.2 異動與異常「本週異動」頁籤。
import { ALERT_CONTACT_COLS } from '~/utils/alertContact'
definePageMeta({ layout: 'v2' })
const route = useRoute()
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
  try { data.value = await apiFetch<Today>('/api/v2/today'); err.value = '' } catch (e: any) { err.value = `今日資料載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
await load()

const hm = (t: string | null | undefined) => (t ? String(t).slice(11, 16) || String(t) : '')
const srcText = (s: Today['sources'][number]) => (s.state ? s.state : (s.as_of ? (s.as_of.slice(0, 10) === data.value?.date ? hm(s.as_of) : s.as_of.slice(0, 16)) : '—'))
/** 區塊標題旁的件數：null＝來源沒資料（寫原因的短字），0＝真的 0 件 */
const countText = (b: Block | undefined) => (!b ? '—' : b.n == null ? ({ disabled: '未啟用', uncollected: '未收集', error: '無法計算', stale: '資料中斷' } as Record<string, string>)[b.status] || '未收集' : `${b.n} 件`)
const dur = (m: number | null | undefined) => { const v = Number(m || 0); return v < 120 ? `${v} 分` : `${Math.floor(v / 60)} 小時 ${v % 60} 分` }
const mlink = (r: any) => (r.machine_key ? `/v2/machine/${encodeURIComponent(r.machine_key)}` : r.ip ? `/v2/hosts?q=${encodeURIComponent(r.ip)}` : null)
// 告警兩段（2.044.3，GPT A2＋B1）：現在仍在告警（不管哪天開始，昨天開始的標「昨日起」）／今天發生過（今天 00:00 後開始，含已恢復）
const shape = (r: any) => ({ ...r, dur_text: dur(r.duration_min), start_text: String(r.started_at || '').slice(5, 16), end_text: r.ended_at ? String(r.ended_at).slice(5, 16) : '未恢復',
  sys_text: r.api_id ? `${r.api_id} ${r.system_name || ''}`.trim() : '未登記業務系統', since_text: r.since_note || '今日' })
const alertRows = computed(() => (data.value?.alerts.rows ?? []).map(shape))
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
      <NuxtLink v-if="track?.changes?.resolved" to="/v2/track?view=resolved" class="reslink">看今日解除的 {{ track.changes.resolved }} 件 ›</NuxtLink>

      <div class="cols">
        <div class="col">
          <section class="blk">
            <div class="bh"><h2>現在仍在告警</h2><span class="cnt" :class="{ na: data.alerts.n == null || data.alerts.status === 'stale' }">{{ countText(data.alerts) }}</span>
              <NuxtLink to="/v2/incident?tab=wug" class="more">全部告警 ›</NuxtLink>
              <InfoNote>WhatsUp 告警事件裡現在還沒恢復的（不含 WhatsUp 設為維護中的），不管哪天開始都列；昨天開始的標「昨日起」，更早的標日期。今天發生、已經恢復的在下一區「今天發生過」。告警功能沒啟用寫「告警未啟用」；啟用了但還沒收到資料寫「未收集」；太久沒收到新資料寫「資料中斷」，這時下面是中斷前的狀態。</InfoNote>
            </div>
            <p v-if="data.alerts.label" class="na-line">{{ data.alerts.label }}</p>
            <V2Table v-if="data.alerts.n != null || data.alerts.rows.length" :rows="alertRows" prefix="A" :max="10" empty="目前沒有告警" :cols="ALERT_COLS">
              <template #cell-notify="{ row }">
                <span class="nt">{{ row.notified_text }}<button v-if="data.can_notify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button></span>
              </template>
            </V2Table>
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
            <p v-if="data.changes.label" class="na-line">{{ data.changes.label }}</p>
            <V2Table v-if="data.changes.n != null" :rows="data.changes.rows" prefix="C" :max="20" empty="今天沒有資產異動"
                     :cols="[{ k: 'label', label: '分類', link: (r: any) => r.link }, { k: 'n', label: '筆數', mono: true, link: (r: any) => r.link }]" />
          </section>
        </div>

        <div class="col">
          <section class="blk">
            <div class="bh"><h2>今日開盤尖峰（09:00～09:30，收盤後回補）</h2><span v-if="data.opening.status !== 'ok'" class="cnt na">{{ data.opening.status === 'pending' ? '尚未匯入' : data.opening.status === 'holiday' ? '休市' : '無法計算' }}</span>
              <InfoNote>ESXi 主機開盤時段（09:00～09:30）的 CPU 使用率 Max／Avg／Min，依 Max 排序，與該主機近 20 個工作日比較。資料由跳板機收盤後從 Aria Operations（vCOps）匯出、經中繼送進來，所以收盤前這一區不放數字。<template v-if="data.opening.last_day">最近一次收到的是 {{ data.opening.last_day }}。</template></InfoNote>
            </div>
            <V2OpeningPeak :data="data.opening" />
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
.nt { display: inline-flex; gap: 6px; align-items: center; }
.mini { font-family: inherit; font-size: 12px; padding: 1px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
</style>
