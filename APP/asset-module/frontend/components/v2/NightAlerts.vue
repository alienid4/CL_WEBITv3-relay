<script setup lang="ts">
// 夜間速查上方的「目前告警中」（AUD-03）。只讀本機快取：GET /api/night/alerts（不寫庫、不對外連線）。
// 「更新」＝人為呼叫既有的 WhatsUp 輪詢端點（POST /api/integrations/whatsup/poll，只有該端點允許的帳號會看到按鈕）；
// 本元件自己不排程、不自動重查。誠實規則：來源沒啟用／沒成功輪詢過＝「未取得」，過舊＝「資料過舊」，都不能顯示成沒有告警。
// Q15（2.066.x）：4.1 告警事件（中繼）有啟用時，後端直接用 1.1 今日概況那一份（同一個範圍：預設 Teams 通知範圍，網址 ?all=1 看全部），
// 首頁有數字這裡就有；那時沒有「更新」鈕與新鮮度門檻（新鮮度＝4.1 心跳）。
import { scopeOf, toggleScopeQuery } from '~/utils/alertScope'
import { FILTER_LABEL, demoData, durationText, filterRows, refreshByHand, viewKind } from '~/composables/nightAlerts'
import type { AlertFilter, NightAlertData, NightAlertRow } from '~/composables/nightAlerts'
import { ALERT_CONTACT_COLS } from '~/utils/alertContact'

const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const showAll = computed(() => scopeOf(route.query.all) === 'all')
function setScope(all: boolean) { router.replace({ query: toggleScopeQuery(route.query as any, all) }) }
const relay = computed(() => (view.value?.source as any)?.kind === 'relay')
const { showToast } = useToast()
const data = ref<NightAlertData | null>(null)
const failed = ref('')
const demo = ref(false)
const filter = ref<AlertFilter | ''>('all')          // 目前下鑽的磚；'' ＝收起清單
const busy = ref(false)
const freshInput = ref(30)
const savingCfg = ref(false)

const view = computed<NightAlertData | null>(() => (demo.value ? demoData() : data.value))
const kind = computed(() => viewKind(view.value))
const shown = computed(() => (view.value && filter.value ? filterRows(view.value.rows, filter.value) : []))
const tiles = computed(() => {
  const c = view.value?.counts
  return c ? ([['critical', c.critical], ['warning', c.warning], ['unmapped', c.unmapped]] as [AlertFilter, number][]) : []
})

async function load() {
  failed.value = ''
  try {
    data.value = await apiFetch<NightAlertData>('/api/night/alerts', { query: { scope: scopeOf(route.query.all) } })
    freshInput.value = data.value.fresh_min
  } catch (e: any) {
    data.value = null
    failed.value = e?.data?.detail ?? e?.message ?? '讀取失敗'
  }
}
onMounted(load)
watch(() => route.query.all, () => { load() })

function pick(f: AlertFilter) { filter.value = filter.value === f ? '' : f }

async function refresh() {
  if (busy.value) return
  busy.value = true
  try {
    const r = await refreshByHand(apiFetch, new Date(), (m) => window.confirm(m))
    if (!r.sent) return
    const st = r.res?.status
    showToast(st === 'failed' || st === 'busy' ? `更新沒有成功：${r.res?.message ?? ''}` : `已更新：${r.res?.message ?? ''}`, st === 'failed' || st === 'busy' ? 'error' : 'success')
    await load()
  } catch (e: any) {
    showToast(`更新失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { busy.value = false }
}

async function saveCfg() {
  savingCfg.value = true
  try {
    await apiFetch('/api/night/alerts/settings', { method: 'PUT', body: { fresh_min: Number(freshInput.value) } })
    showToast('已儲存新鮮度門檻', 'success')
    await load()
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { savingCfg.value = false }
}

const hostLink = (r: NightAlertRow) => (!demo.value && r.machine_key ? `/v2/machine/${encodeURIComponent(r.machine_key)}` : null)
const cols = [
  { k: 'severity', label: '嚴重度', tone: (r: NightAlertRow) => (r.severity === '嚴重' ? 'dr' : 'none') },
  { k: 'name', label: '裝置', link: hostLink, min: 110 },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'state', label: 'WhatsUp 狀態' },
  { k: 'categories', label: '類別' },
  { k: 'partitions', label: '分割區', text: (r: NightAlertRow) => (r.partitions === null ? '—' : r.partitions) },
  { k: 'problem', label: '問題原文', min: 140, text: (r: NightAlertRow) => r.problem || '未取得' },
  { k: 'since', label: '起始時間', mono: true, text: (r: NightAlertRow) => r.since || '未取得' },
  { k: 'duration_min', label: '已持續', text: (r: NightAlertRow) => durationText(r.duration_min) },
  { k: 'match_label', label: '對應資產', text: (r: NightAlertRow) => r.match_label + (r.match_note ? `：${r.match_note}` : '') },
  // 2.044.2（R15）：告警聯絡五欄（後端依對到的資產查 APID；對不到資產的寫「未提供」）。示範資料沒有這五欄也照樣顯示「未提供」
  ...ALERT_CONTACT_COLS.map(c => ({ ...c, text: (r: any) => r[c.k] ?? '未提供' })),
]
</script>

<template>
  <section class="card na" aria-label="目前告警中">
    <h2>
      <span>🚨 目前告警中</span>
      <span v-if="demo" class="demo">示範資料</span>
      <InfoNote>
        只讀本系統已存的 WhatsUp 告警，打開這頁不會去問 WhatsUp，也不會連任何主機。4.1 告警事件（中繼）有啟用時，這裡跟 1.1 今日概況「現在仍在告警」是同一份、同一個範圍（預設 Teams 通知範圍，可切「看全部」），嚴重＝分級 🔴 立即；
        沒有中繼的環境才讀 WhatsUp 輪詢快取（盤中靜默窗內不自動查）。
        「未取得」＝來源沒啟用或還沒成功輪詢過，不是沒有告警；「資料過舊」＝超過 {{ view?.fresh_min ?? 30 }} 分鐘沒更新，下面只是當時的樣子。
        嚴重＝WhatsUp 狀態為當機／嚴重／無回應／錯誤，其餘為警告。對應資產要 IP 與主機名都對得上才算；對不確定的列「待核」，不猜。
        分割區與類別由告警原文解析，抽不到就寫「未取得」，請看原文。真實告警文字格式尚未用公司端資料驗證。
        盤中（平日 08:30–15:00）按「更新」會先問你一次。
      </InfoNote>
      <button v-if="!demo" type="button" class="lnk" @click="demo = true">看示範</button>
      <button v-else type="button" class="lnk" @click="demo = false">結束示範</button>
    </h2>

    <div v-if="failed" class="alert bad" role="alert">讀取失敗：{{ failed }} <button type="button" class="retry" @click="load">再試一次</button></div>
    <div v-else-if="kind === 'loading'" class="skel" aria-busy="true" />
    <template v-else-if="view">
      <!-- 資料時間與新鮮度（查詢結果與資料時間分開顯示） -->
      <div v-if="kind === 'unavailable'" class="alert" role="status"><b>未取得</b>　{{ view.message }}</div>
      <div v-else-if="kind === 'stale'" class="alert" role="status"><b>{{ view.status_label }}</b>　{{ view.message }}</div>
      <div v-else-if="kind === 'empty'" class="okbox" role="status"><b>目前沒有告警</b>　資料時間 {{ (view.data_time || '').slice(11, 16) || '示範' }}</div>
      <div class="meta">
        <span>資料時間：{{ view.data_time || '—' }}<template v-if="view.age_min !== null">（{{ view.age_min }} 分鐘前）</template></span>
        <span v-if="!relay">門檻：{{ view.fresh_min }} 分鐘</span>
        <span v-else>來源：4.1 告警事件（跟今日概況同一份）</span>
        <template v-if="relay && view.scope">
          <span>{{ view.scope.mode === 'policy' ? '範圍：Teams 通知範圍' : '範圍：全部' }}<template v-if="view.scope.mode === 'policy' && view.scope.hidden">（另有 {{ view.scope.hidden }} 件不在範圍）</template></span>
          <button v-if="!showAll && view.scope.keywords.length" type="button" class="lnk" data-testid="night-scope-all" @click="setScope(true)">看全部</button>
          <button v-else-if="showAll && view.scope.keywords.length" type="button" class="lnk" data-testid="night-scope-policy" @click="setScope(false)">只看通知範圍</button>
        </template>
        <span v-if="view.last_failed && view.status === 'ok'" class="warnt">最近一次輪詢失敗，資料沒有更新到最新</span>
        <button v-if="!demo && view.can_refresh" type="button" class="btn" :disabled="busy" @click="refresh">{{ busy ? '更新中…' : '更新' }}</button>
      </div>

      <div v-if="kind === 'rows' || kind === 'stale'" class="tiles">
        <button v-for="[k, n] in tiles" :key="k" type="button" class="tile" :class="[k, { on: filter === k }]" :aria-pressed="filter === k" @click="pick(k)">
          <span class="n">{{ n }}</span><span class="l">{{ FILTER_LABEL[k] }}</span>
        </button>
        <button type="button" class="tile all" :class="{ on: filter === 'all' }" :aria-pressed="filter === 'all'" @click="pick('all')">
          <span class="n">{{ view.counts.total }}</span><span class="l">全部</span>
        </button>
      </div>
      <div v-if="filter && (kind === 'rows' || kind === 'stale')" class="tbl">
        <V2Table :rows="shown" :cols="cols" prefix="A" :max="10" :export-name="`夜間速查_目前告警_${FILTER_LABEL[filter as AlertFilter]}`" :empty="kind === 'stale' ? '此快取沒有符合的列，無法確認目前是否有告警' : '這一類目前沒有告警'" />
      </div>
    </template>

    <details v-if="!demo && data?.can_edit_settings" class="cfg">
      <summary>設定</summary>
      <label>資料新鮮度門檻 <input v-model.number="freshInput" type="number" min="5" max="1440" aria-label="資料新鮮度門檻（分鐘）"> 分鐘</label>
      <button type="button" class="btn" :disabled="savingCfg" @click="saveCfg">{{ savingCfg ? '儲存中…' : '儲存' }}</button>
    </details>
  </section>
</template>

<style scoped>
.na { margin-top: 12px; }
.demo { font-size: 13px; padding: 1px 10px; border-radius: 12px; border: 1px dashed var(--warn); color: var(--warn-text); background: var(--warn-soft); font-weight: 600; }
.lnk { font: inherit; font-size: 14px; background: none; border: 0; color: var(--link, var(--brand)); cursor: pointer; text-decoration: underline; padding: 4px; min-height: 32px; }
.alert { border-radius: 14px; padding: 12px 14px; margin-top: 8px; border: 1px solid var(--warn); color: var(--warn-text); background: var(--warn-soft); }
.alert.bad { border-color: var(--bad); color: var(--bad); background: var(--bad-soft); }
.okbox { border-radius: 14px; padding: 12px 14px; margin-top: 8px; border: 1px solid var(--good); color: var(--good); }
.meta { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin-top: 8px; color: var(--muted); font-size: 15px; }
.warnt { color: var(--warn-text); }
.btn { font: inherit; font-size: 15px; padding: 6px 16px; border-radius: 12px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink); cursor: pointer; min-height: 40px; }
.btn:disabled { opacity: .6; }
.tiles { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.tile { font: inherit; display: flex; flex-direction: column; align-items: center; min-width: 88px; padding: 8px 14px; border-radius: 14px; border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink); cursor: pointer; }
.tile .n { font-family: var(--disp); font-size: 28px; font-weight: 700; line-height: 1.1; }
.tile .l { font-size: 14px; color: var(--muted); }
.tile.critical .n { color: var(--bad); }
.tile.on { outline: 2px solid var(--brand); }
.tbl { margin-top: 10px; overflow-x: auto; }
.cfg { margin-top: 10px; font-size: 15px; color: var(--muted); }
.cfg input { width: 80px; font: inherit; padding: 4px 8px; margin: 0 4px; }
.skel { height: 52px; border-radius: 14px; background: var(--sub); margin-top: 8px; }
.retry { font: inherit; margin-left: 8px; }
</style>
