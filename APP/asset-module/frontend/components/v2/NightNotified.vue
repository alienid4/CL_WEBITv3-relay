<script setup lang="ts">
// 夜間速查・今晚通知清單（N3）：只列 Teams 實際通知過你的（GET /api/v2/night/notified），未恢復在上、已恢復淡色在下。
// 點主機或「什麼事」進緊急檢查（同一頁 ?host=<machine_key>，網址可分享）。「我在處理」寫進主機履歷（POST /api/v2/night/handle）。
// 舊告警（掛超過 1 天）收成一行，展開後是原本的「目前告警中」（元件 V2NightAlerts，功能不刪）。
import { fetchNotified, headline, hhmm, impactText, handleState, handleText, needsYou, nowText, platformLabel, postHandle, sortNotified, writeErr } from '~/composables/nightNotified'
import type { NotifiedData, NotifiedItem } from '~/composables/nightNotified'

const { apiFetch } = useApi()
const { isViewer } = useAuth()     // 只能看的帳號不出「我在處理」（後端另外擋）
const { showToast } = useToast()
const route = useRoute()

const data = ref<NotifiedData | null>(null)
const err = ref('')
const loading = ref(false)
async function load() {
  loading.value = true
  try { data.value = await fetchNotified(apiFetch); err.value = '' } catch (e: any) { err.value = writeErr(e) } finally { loading.value = false }
}
onMounted(load)

const now = computed(() => data.value?.now ?? null)
const rows = computed(() => sortNotified(data.value?.items ?? []).map((x, i) => ({
  ...x, __no: i + 1,
  at_t: hhmm(x.notified_at, now.value),
  host_t: x.name || x.ip || '—',
  now_t: nowText(x, now.value).tag,
  impact_t: impactText(x).main,
  handle_t: handleText(x.handling, now.value).main,
})))
const todo = computed(() => needsYou(rows.value as any) as (NotifiedItem & { __no: number })[])
const concl = computed(() => headline(data.value?.summary))
const hostTo = (x: NotifiedItem) => ({ path: '/v2/night', query: { host: x.machine_key || x.ip || '' } })
const cols = [
  { k: 'at_t', label: '通知時間', mono: true },
  { k: 'host_t', label: '主機', min: 140 },
  { k: 'title', label: '什麼事', min: 160 },
  { k: 'now_t', label: '它現在' },
  { k: 'impact_t', label: '影響' },
  { k: 'handle_t', label: '處理' },
]
const exportCols = [
  { k: 'notified_at', label: '通知時間（完整）' },
  { k: 'ip', label: 'IP' },
  { k: 'platform', label: '平台', text: (r: any) => platformLabel(r.platform) },
  { k: 'detail', label: '說明' },
  { k: 'recovered_at', label: '恢復時間' },
  { k: 'times_tonight', label: '今晚次數' },
]

const olderOpen = ref(route.query.view === 'older')
const busy = ref<Record<string, boolean>>({})
async function take(x: NotifiedItem) {
  if (!x.machine_key && !x.ip) { showToast('這筆沒有對應到主機，不能登記處理', 'error'); return }
  busy.value = { ...busy.value, [x.id]: true }
  try {
    const r = await postHandle(apiFetch, x.id, x.machine_key || x.ip || '')
    const it = data.value?.items.find(y => y.id === x.id)
    if (it) it.handling = { id: r.id, by: r.by, at: r.at, result: null, done_at: null }
    showToast('已登記你在處理', 'success')
  } catch (e: any) {
    if (e?.cancelled) return
    showToast(`登記失敗：${writeErr(e)}`, 'error')
  } finally { busy.value = { ...busy.value, [x.id]: false } }
}
</script>

<template>
  <section class="nn" data-testid="night-notified">
    <div class="nhd">
      <div class="concl" data-testid="night-concl">{{ err ? '讀不到今晚的通知' : (data ? concl : '讀取中…') }}</div>
      <span v-if="data" class="sm">最近 {{ data.hours }} 小時・資料時間 {{ hhmm(data.data_at, data.now) }}</span>
      <button type="button" class="b2" :disabled="loading" @click="load">{{ loading ? '更新中…' : '重新整理' }}</button>
      <InfoNote>只列 Teams 有通知你的；同一台今晚多次通知合併成一列。點主機看緊急檢查（找誰、跟平常比、影響範圍）。</InfoNote>
    </div>
    <div v-if="err" class="alert bad" role="alert">{{ err }} <button type="button" class="b2" @click="load">再試一次</button></div>

    <template v-else-if="data">
      <div class="todo" data-testid="night-todo"><b>要你處理<template v-if="todo.length">（{{ todo.length }} 件）</template></b>
        <div v-for="x in todo" :key="x.id" class="it"><span class="mono">N{{ x.__no }}</span><b>{{ x.name || x.ip }}</b><span>{{ x.title }}</span><span class="sp" />
          <NuxtLink class="b2" :to="hostTo(x)">查看</NuxtLink></div>
        <div v-if="!todo.length" class="it muted">沒有要你處理的事</div>
      </div>

      <V2Table :rows="rows" :cols="cols" :export-cols="exportCols" prefix="N" row-key="id" unit="件" empty="今晚沒有通知你" export-name="夜間速查_今晚通知">
        <template #cell-at_t="{ row }"><span :class="{ dim: !row.open }" class="mono">{{ row.at_t }}</span></template>
        <template #cell-host_t="{ row }"><div :class="{ dim: !row.open }"><NuxtLink :to="hostTo(row)" data-testid="night-host-link"><b>{{ row.host_t }}</b></NuxtLink>
          <div class="sm">{{ row.ip || '無 IP' }}・{{ platformLabel(row.platform) }}</div></div></template>
        <template #cell-title="{ row }"><div :class="{ dim: !row.open }"><NuxtLink :to="hostTo(row)"><b>{{ row.title }}</b></NuxtLink><div v-if="row.detail" class="sm">{{ row.detail }}</div></div></template>
        <template #cell-now_t="{ row }"><div :class="{ dim: !row.open }"><span class="tg" :class="nowText(row, now).tone">{{ row.now_t }}</span><div v-if="nowText(row, now).sub" class="sm">{{ nowText(row, now).sub }}</div></div></template>
        <template #cell-impact_t="{ row }"><div :class="{ dim: !row.open, red: impactText(row).big }"><b>{{ row.impact_t }}</b><div v-if="impactText(row).sub" class="sm">{{ impactText(row).sub }}</div></div></template>
        <template #cell-handle_t="{ row }"><div :class="{ dim: !row.open }">
          <button v-if="handleState(row.handling) === 'none' && row.open && !isViewer" type="button" class="b1" :disabled="busy[row.id]" data-testid="night-take" @click="take(row)">{{ busy[row.id] ? '登記中…' : '我在處理' }}</button>
          <template v-else><b>{{ row.handle_t }}</b><div v-if="handleText(row.handling, now).sub" class="sm">{{ handleText(row.handling, now).sub }}</div></template></div></template>
      </V2Table>

    </template>

    <details class="older" :open="olderOpen" @toggle="olderOpen = ($event.target as HTMLDetailsElement).open">
      <summary data-testid="night-older">{{ data?.older ? (data.older.count ? `另有 ${data.older.count} 件舊告警・掛超過 1 天` : '沒有掛超過 1 天的舊告警') : '目前告警中・含舊告警' }}</summary>
      <V2NightAlerts v-if="olderOpen" />
    </details>
  </section>
</template>

<style scoped>
.nn { margin-top: 12px; }
.nhd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.concl { font-family: var(--disp); font-size: 18px; font-weight: 700; color: var(--ink); }
.sm { color: var(--ink-aux); font-size: 12px; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); }
.dim { opacity: .55; }
.red, .red b { color: var(--bad); }
.tg { display: inline-block; font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; white-space: nowrap; }
.tg.bad { background: var(--bad-soft); color: var(--bad); }
.tg.good { background: var(--good-soft); color: var(--good); }
.b1 { font: inherit; font-size: 13px; border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 10px; cursor: pointer; white-space: nowrap; }
.b1:disabled { opacity: .6; }
.b2 { font: inherit; font-size: 13px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 3px 10px; cursor: pointer; text-decoration: none; }
.todo { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 12px; padding: 8px 12px; margin: 0 0 10px; font-size: 14px; }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); flex-wrap: wrap; }
.todo .sp { flex: 1; }
.alert { border-radius: 12px; padding: 10px 14px; margin-top: 8px; border: 1px solid var(--warn); color: var(--warn-text); background: var(--warn-soft); }
.alert.bad { border-color: var(--bad); color: var(--bad); background: var(--bad-soft); }
.older { margin-top: 12px; background: var(--sub); border-radius: 12px; padding: 10px 14px; }
.older summary { cursor: pointer; color: var(--ink-soft); }
</style>
