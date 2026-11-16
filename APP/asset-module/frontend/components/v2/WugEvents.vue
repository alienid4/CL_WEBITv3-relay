<script setup lang="ts">
// 4.1 事件應變 →「告警事件」（2.043.0）：WhatsUp 告警經中繼 PC 每分鐘進來的事件表（一個設備＋監控項目的一次問題＝一筆）。
// 同一份資料也給 Teams 推播、夜間速查、主機履歷、月報（加總＝下鑽）。資料：/api/v2/wug-alerts*（wug_alert.py）。
// 子頁籤與篩選都寫在網址（wsub／wstate／wtier／wmonth／wq／wmk／wip），可以直接分享。
import { NIGHT_PATH, nightLink, machineLink } from '~/types/integrations'
// 2.044.2（R15）每列帶告警聯絡五欄；2.044.2（R16）「通知」按鈕（一般帳號以上，伺服器回 can_notify）→ V2AlertNotify 先預覽再送
import { ALERT_CONTACT_COLS } from '~/utils/alertContact'
const emit = defineEmits<{ count: [n: number] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const hasNight = computed(() => router.resolve(NIGHT_PATH).matched.length > 0)
const ipLink = (r: any) => (r.ip ? (hasNight.value ? nightLink(r.ip) : machineLink(r.ip)) : null)
const whoLink = (r: any) => (r.machine_key ? `/v2/machine/${encodeURIComponent(r.ip || r.hostname || r.machine_key)}` : null)

// 設定（心跳、升級、保存天數…）2.066.x 起不當子頁籤，收進最下面管理員才看得到的「更多」（操作／設定分層）
const SUBS = [{ k: 'now', label: '目前告警' }, { k: 'list', label: '事件清單' }, { k: 'month', label: '每月彙總' }, { k: 'notice', label: '通知紀錄' }, { k: 'unmatched', label: '對不到的告警' }, { k: 'mute', label: '不通知清單' }]
const qv = (k: string) => String(route.query[k] ?? '')
function setQ(patch: Record<string, string>) { router.replace({ query: { ...route.query, ...patch } }) }
const sub = computed({ get: () => (SUBS.some(s => s.k === qv('wsub')) ? qv('wsub') : 'now'), set: (v: string) => setQ({ wsub: v }) })
const thisMonth = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 7)
const f = reactive({ state: qv('wstate'), tier: qv('wtier'), month: qv('wmonth'), q: qv('wq'), mk: qv('wmk'), ip: qv('wip'), dev: qv('wdev'), nomk: qv('wnomk') })
const ym = ref(qv('wmonth') || thisMonth())

const st = ref<any>(null)
const nowRows = ref<any[]>([])
const listRows = ref<any[]>([])
const month = ref<any>(null)
const noticeRows = ref<any[]>([])
const unmatched = ref<any[]>([])
const err = ref('')
async function loadStatus() {
  st.value = await apiFetch('/api/v2/wug-alerts')
  Object.assign(form, st.value.settings)
}
const canNotify = ref(false)
const notifyId = ref<number | null>(null)
async function loadNow() { const r = await apiFetch<{ rows: any[]; can_notify?: boolean }>('/api/v2/wug-alerts/events', { query: { state: 'open' } }); nowRows.value = r.rows; canNotify.value = !!r.can_notify; emit('count', nowRows.value.filter(r => r.tier !== 'maint').length) }
async function loadList() {
  listRows.value = (await apiFetch<{ rows: any[] }>('/api/v2/wug-alerts/events', { query: { state: f.state, tier: f.tier, month: f.month, q: f.q, machine_key: f.mk, ip: f.ip, wug_device_id: f.dev, no_machine: f.nomk === '1' } })).rows
}
async function loadMonth() { month.value = await apiFetch('/api/v2/wug-alerts/month', { query: { ym: ym.value } }) }
async function loadNotice() { noticeRows.value = (await apiFetch<{ rows: any[] }>('/api/v2/wug-alerts/notices')).rows }
async function loadUnmatched() { unmatched.value = (await apiFetch<{ rows: any[] }>('/api/v2/relay-import/unmatched', { query: { source: 'whatsup_alert' } })).rows }
async function load() {
  err.value = ''
  try {
    await loadStatus()
    await loadNow()
    if (sub.value === 'list') await loadList()
    if (sub.value === 'month') await loadMonth()
    if (sub.value === 'notice') await loadNotice()
    if (sub.value === 'unmatched') await loadUnmatched()
    if (sub.value === 'mute') await loadMutes()
  } catch (e: any) { err.value = `告警事件載入失敗：${errMsg(e)}` }
}
onMounted(load)
watch(sub, () => { load() })
function applyFilter() { setQ({ wstate: f.state, wtier: f.tier, wmonth: f.month, wq: f.q, wmk: f.mk, wip: f.ip, wdev: f.dev, wnomk: f.nomk }); loadList().catch((e: any) => { err.value = `事件清單載入失敗：${errMsg(e)}` }) }
function clearFilter() { Object.assign(f, { state: '', tier: '', month: '', q: '', mk: '', ip: '', dev: '', nomk: '' }); applyFilter() }
// 每月彙總的一列 → 事件清單（同一個月、同一台；對不到資產的用 IP，連 IP 都沒有用 WhatsUp 設備 ID，且只算沒對到資產的）＝那幾筆
function drill(g: any) {
  const mk = g.machine_key || ''
  Object.assign(f, { state: '', tier: '', q: '', month: month.value?.month || ym.value, mk, ip: mk ? '' : (g.ip || ''), dev: mk || g.ip ? '' : (g.wug_device_id || ''), nomk: mk ? '' : '1' })
  setQ({ wsub: 'list', wstate: '', wtier: '', wq: '', wmonth: f.month, wmk: f.mk, wip: f.ip, wdev: f.dev, wnomk: f.nomk })
  loadList().catch((e: any) => { err.value = `事件清單載入失敗：${errMsg(e)}` })
}
function openList(state: string) { Object.assign(f, { state, tier: '', month: '', q: '', mk: '', ip: '', dev: '', nomk: '' }); setQ({ wsub: 'list', wstate: state, wtier: '', wmonth: '', wq: '', wmk: '', wip: '', wdev: '', wnomk: '' }); loadList() }

const durText = (m: number) => (m < 120 ? `${m} 分` : `${Math.floor(m / 60)} 小時 ${m % 60} 分`)
const VERDICT: Record<string, string> = { true: '真', false: '假' }
// 分級小標籤（立即＝紅、彙整＝黃、維護中＝灰）
const TIER: Record<string, { t: string; c: string }> = { red: { t: '立即', c: 'bad' }, yellow: { t: '彙整', c: 'warn' }, maint: { t: '維護中', c: 'plain' } }
const sysText = (r: any) => (r.api_id ? `${r.api_id} ${r.system_name || ''}` : '未登記')
const endText = (r: any) => r.ended_at || '未恢復'
const policyText = (r: any) => (r.policy == null ? '未取得' : r.policy || '未設定')
// 畫面 7 欄；其他（WhatsUp 狀態、恢復、告警聯絡、開盤、動作政策、分級理由、真假）點「監控項目」看明細，匯出照樣帶
const evCols = [
  { k: 'who', label: '機器', text: (r: any) => [r.who, r.ip].filter(Boolean).join(' ') },
  { k: 'monitor', label: '監控項目' }, { k: 'started_at', label: '開始', mono: true },
  { k: 'duration_min', label: '持續', mono: true, text: (r: any) => durText(r.duration_min) },
  { k: 'system_name', label: '業務系統', text: sysText }, { k: 'owner', label: '負責人' },
  { k: 'notify', label: '通知', text: (r: any) => r.notified_text || '' },
]
const evExport = [
  { k: 'tier_label', label: '分級' }, { k: 'status_text', label: 'WhatsUp 狀態' }, { k: 'ended_at', label: '恢復', text: endText },
  ...ALERT_CONTACT_COLS, { k: 'market', label: '開盤' }, { k: 'policy', label: '動作政策', text: policyText },
  { k: 'tier_reason', label: '分級理由' }, { k: 'verdict', label: '真假', text: (r: any) => VERDICT[r.verdict] || '未回報' },
]
// 點一列的「監控項目」＝在表格下方看這筆明細（同一時間一筆）
const pick = ref<any>(null)
function togglePick(r: any) { pick.value = pick.value?.id === r.id ? null : r }
watch(sub, () => { pick.value = null })
const monthCols = [
  { k: 'who', label: '機器' }, { k: 'ip', label: 'IP', mono: true }, { k: 'system_name', label: '業務系統', text: (r: any) => (r.api_id ? `${r.api_id} ${r.system_name || ''}` : '未登記') },
  { k: 'events', label: '次數', mono: true }, { k: 'red', label: '其中立即', mono: true }, { k: 'open', label: '未恢復', mono: true },
  { k: 'minutes', label: '總時長', mono: true, text: (r: any) => durText(r.minutes) }, { k: 'monitors', label: '監控項目' },
]
const NKIND: Record<string, string> = { open: '發生', escalate: '升級', brief: '短暫事故', recover: '恢復', digest: '🟡 摘要（併入每日彙整）', suspect: '疑似收集異常', heartbeat_down: '監控中斷', heartbeat_never: '監控未啟動', heartbeat_up: '監控恢復' }
const NSTATUS: Record<string, string> = { digest: '併入每日 08:00 彙整', queued: '已排進 Teams', recorded: '只記錄（Teams 未啟用）' }
const noticeCols = [
  { k: 'at', label: '時間', mono: true }, { k: 'kind', label: '種類', text: (r: any) => NKIND[r.kind] || r.kind }, { k: 'events', label: '筆數', mono: true },
  { k: 'title', label: '訊息第一行' }, { k: 'status', label: '處理', text: (r: any) => NSTATUS[r.status] || r.status },
  { k: 'delivery_status', label: 'Teams 投遞', text: (r: any) => r.delivery_status || '—' },
]
const umCols = [{ k: 'ident', label: 'IP', mono: true }, { k: 'name', label: 'WhatsUp 裝置名' }, { k: 'reason', label: '原因', text: (r: any) => (r.reason === 'ambiguous' ? `對到 ${r.candidates} 台` : '資產庫沒有') },
  { k: 'first_seen', label: '第一次', mono: true }, { k: 'last_seen', label: '最近', mono: true }]

// 2.060.18 不通知清單（使用者 11-11：「例外名單包含時段，可以自己加，例：週六日 SEC02 會 reboot」）：符合的照樣記錄、只是不推 Teams
const mutes = ref<any[]>([])
async function loadMutes() { mutes.value = (await apiFetch<{ items: any[] }>('/api/v2/wug-alerts/mutes')).items }
const WD = ['一', '二', '三', '四', '五', '六', '日']
const muteRow = ref<any>(null)
const mf = reactive<any>({ whole: true, days: [] as string[], from: '', to: '', note: '' })
function openMute(r: any) { muteRow.value = r; Object.assign(mf, { whole: true, days: [], from: '', to: '', note: '' }) }
const muteBusy = ref(false)
async function saveMute() {
  muteBusy.value = true
  try {
    const m = await apiFetch<any>('/api/v2/wug-alerts/mutes', { method: 'POST', body: { event_id: muteRow.value.id, whole_device: mf.whole, days: mf.days, time_from: mf.from, time_to: mf.to, note: mf.note } })
    showToast(`已加入不通知：${m.device_label}・${m.when_text}`, 'success'); muteRow.value = null
    if (sub.value === 'mute') await loadMutes()
  } catch (e: any) { showToast(`加入失敗：${errMsg(e)}`, 'error') } finally { muteBusy.value = false }
}
async function delMute(m: any) {
  if (!confirm(`刪掉這條不通知規則？
${m.device_label} ${m.monitor === '*' ? '全部監控項目' : m.monitor}・${m.when_text}`)) return
  try { await apiFetch(`/api/v2/wug-alerts/mutes/${m.id}`, { method: 'DELETE' }); showToast('已刪除', 'success'); await loadMutes() } catch (e: any) { showToast(`刪除失敗：${errMsg(e)}`, 'error') }
}
const muteCols = [{ k: 'device_label', label: '機器' }, { k: 'monitor', label: '監控項目', text: (r: any) => (r.monitor === '*' ? '全部監控項目' : r.monitor) },
  { k: 'when_text', label: '時段' }, { k: 'note', label: '原因' }, { k: 'created_by', label: '加入者' }, { k: 'created_at', label: '加入時間', mono: true }, { k: 'act', label: '' }]

const busyId = ref<number | null>(null)
async function verdict(r: any, v: string) {
  busyId.value = r.id
  try {
    const out = await apiFetch<any>(`/api/v2/wug-alerts/events/${r.id}/verdict`, { method: 'PUT', body: { verdict: v } })
    r.verdict = out.verdict
    showToast(`事件 #${r.id} 已回報：${VERDICT[v] || '清除'}`, 'success')
  } catch (e: any) { showToast(`回報失敗：${errMsg(e)}`, 'error') } finally { busyId.value = null }
}

const form = reactive<any>({ enabled: false, heartbeat_min: 10, escalate_min: 60, digest_min: 60, unmatched_tier: 'red', retention_days: 730, notify_policies: '', confirm_min: 10 })
const saving = ref(false)
async function save() {
  saving.value = true
  try {
    st.value = await apiFetch('/api/v2/wug-alerts/settings', { method: 'PUT', body: { ...form, heartbeat_min: Number(form.heartbeat_min), escalate_min: Number(form.escalate_min), digest_min: Number(form.digest_min), retention_days: Number(form.retention_days), confirm_min: Number(form.confirm_min) } })
    showToast('告警設定已儲存', 'success')
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { saving.value = false }
}
// 狀態只在出問題時講一行：未啟用／從未收到（中斷另有一行警告）／最近一次掃描沒看完
const stProblem = computed(() => {
  const s = st.value
  if (!s) return ''
  if (!s.settings.enabled) return '告警事件未啟用'
  if (s.heartbeat === 'never') return '還沒收到過告警資料'
  if (s.last_scan && !s.last_scan.complete) return `最近一次掃描（${s.last_scan.snap_at.slice(5, 16)}）沒看完，只會新增告警、不會判恢復`
  return ''
})
</script>

<template>
  <div class="box">
    <div v-if="st" class="hd">
      <button type="button" class="num" @click="openList('open')">未恢復 <b>{{ st.open }}</b></button>
      <button type="button" class="num" @click="sub = 'now'">維護中 <b>{{ st.maintenance }}</b></button>
      <button type="button" class="num" @click="sub = 'unmatched'">對不到資產 <b>{{ st.unmatched }}</b></button>
      <span class="muted">資料 {{ (st.last_received || '—').slice(5, 16) }}</span>
      <button type="button" class="mini" @click="load">重新整理</button>
      <InfoNote>第一類業務系統的正式機立即推 Teams，其他每小時一則摘要；摘要裡持續超過升級時間的改成立即。WhatsUp 設成維護中的只記錄不推。「通知」先預覽收件人與內容，確認才送。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-if="st?.settings_problem" class="err">{{ st.settings_problem }}</p>
    <p v-if="st && st.heartbeat === 'down'" class="warn">已超過 {{ st.settings.heartbeat_min }} 分鐘沒收到告警資料：下面是最後收到時的狀態，不代表現在沒事。</p>
    <p v-else-if="stProblem" class="warn" data-testid="wug-problem">{{ stProblem }}</p>
    <div class="subs"><button v-for="s in SUBS" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="sub = s.k">{{ s.label }}{{ s.k === 'now' ? ` ${nowRows.length}` : '' }}</button></div>

    <V2Table v-if="sub === 'now'" :rows="nowRows" :cols="evCols" :export-cols="evExport" prefix="A" empty="目前沒有未恢復的告警">
      <template #cell-who="{ row }"><span v-if="TIER[row.tier]" class="chip" :class="TIER[row.tier].c">{{ TIER[row.tier].t }}</span>
        <NuxtLink v-if="whoLink(row)" :to="whoLink(row)!"><b>{{ row.who || row.ip }}</b></NuxtLink><b v-else>{{ row.who || row.ip }}</b>
        <div v-if="row.ip" class="sm"><NuxtLink v-if="ipLink(row)" :to="ipLink(row)!">{{ row.ip }}</NuxtLink><template v-else>{{ row.ip }}</template></div></template>
      <template #cell-monitor="{ row }"><button type="button" class="pickb" :class="{ on: pick?.id === row.id }" @click="togglePick(row)">{{ row.monitor }}</button><div class="sm">{{ row.status_text }}</div></template>
      <template #cell-notify="{ row }">
        <span class="vd">{{ row.notified_text }}<button v-if="canNotify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button><button v-if="st?.can_edit" type="button" class="mini" @click="openMute(row)">不通知</button></span>
      </template>
    </V2Table>

    <template v-else-if="sub === 'list'">
      <form class="filters" @submit.prevent="applyFilter">
        <select v-model="f.state" aria-label="狀態"><option value="">全部狀態</option><option value="open">未恢復</option><option value="closed">已恢復</option></select>
        <select v-model="f.tier" aria-label="分級"><option value="">全部分級</option><option value="red">🔴 立即</option><option value="yellow">🟡 每日彙整</option><option value="maint">維護中</option></select>
        <input v-model="f.month" type="month" aria-label="開始月份">
        <input v-model="f.q" type="search" placeholder="機器、IP、監控項目、系統、負責人" aria-label="搜尋">
        <span v-if="f.mk || f.ip || f.dev" class="chip warn">只看 {{ f.mk || f.ip || ('WhatsUp 設備 ' + f.dev) }}{{ f.nomk === '1' ? '・對不到資產' : '' }}</span>
        <button type="submit" class="mini">套用</button><button type="button" class="mini" @click="clearFilter">清除</button>
      </form>
      <V2Table :rows="listRows" :cols="evCols" :export-cols="evExport" prefix="E" empty="沒有符合條件的告警事件">
        <template #cell-who="{ row }"><span v-if="TIER[row.tier]" class="chip" :class="TIER[row.tier].c">{{ TIER[row.tier].t }}</span>
          <NuxtLink v-if="whoLink(row)" :to="whoLink(row)!"><b>{{ row.who || row.ip }}</b></NuxtLink><b v-else>{{ row.who || row.ip }}</b>
          <div v-if="row.ip" class="sm"><NuxtLink v-if="ipLink(row)" :to="ipLink(row)!">{{ row.ip }}</NuxtLink><template v-else>{{ row.ip }}</template></div></template>
        <template #cell-monitor="{ row }"><button type="button" class="pickb" :class="{ on: pick?.id === row.id }" @click="togglePick(row)">{{ row.monitor }}</button><div class="sm">{{ row.ended_at ? `恢復 ${row.ended_at.slice(5, 16)}` : row.status_text }}</div></template>
        <template #cell-notify="{ row }">
          <span class="vd">{{ row.notified_text }}<button v-if="canNotify" type="button" class="mini" @click="notifyId = row.id">{{ row.notified_n ? '重送' : '通知' }}</button><button v-if="st?.can_edit" type="button" class="mini" @click="openMute(row)">不通知</button></span>
        </template>
      </V2Table>
    </template>

    <template v-else-if="sub === 'month'">
      <form class="filters" @submit.prevent="loadMonth"><input v-model="ym" type="month" aria-label="月份"><button type="submit" class="mini">查詢</button>
        <span v-if="month" class="muted">共 {{ month.events }} 筆、{{ month.machines.length }} 台、總時長 {{ durText(month.minutes) }}、未恢復 {{ month.open }}、對不到資產 {{ month.unmatched }}</span>
        <InfoNote>依「開始時間」算月份，跟月報的「告警事件」一段是同一份計算。點一列看那幾筆事件。</InfoNote></form>
      <V2Table :rows="month?.machines ?? []" :cols="monthCols" prefix="M" empty="這個月沒有告警事件">
        <template #cell-events="{ row }"><button type="button" class="link" @click="drill(row)">{{ row.events }}</button></template>
      </V2Table>
    </template>

    <V2Table v-else-if="sub === 'notice'" :rows="noticeRows" :cols="noticeCols" prefix="N" empty="還沒有發出任何通知" />
    <V2Table v-else-if="sub === 'unmatched'" :rows="unmatched" :cols="umCols" prefix="U" empty="沒有對不到資產的告警" />
    <template v-else-if="sub === 'mute'">
      <p class="muted">符合的告警照樣記錄，只是不推 Teams<InfoNote>在「目前告警」或「事件清單」那一列按「不通知」加入。比對告警開始時間；起 &gt; 訖＝跨午夜。只有管理員能加、能刪。</InfoNote></p>
      <V2Table :rows="mutes" :cols="muteCols" prefix="X" empty="目前沒有不通知規則">
        <template #cell-act="{ row }"><button v-if="st?.can_edit" type="button" class="mini" @click="delMute(row)">刪除</button></template>
      </V2Table>
    </template>

    <section v-if="pick && (sub === 'now' || sub === 'list')" class="pv" data-testid="wug-pick">
      <div class="pvh"><b>{{ pick.who || pick.ip }}・{{ pick.monitor }}</b><span class="sp" /><button type="button" class="mini" @click="pick = null">關閉</button></div>
      <dl class="kv">
        <dt>分級</dt><dd>{{ TIER[pick.tier]?.t || pick.tier_label }}<span v-if="pick.tier_reason" class="sm">　{{ pick.tier_reason }}</span></dd>
        <dt>WhatsUp 狀態</dt><dd>{{ pick.status_text || '—' }}</dd>
        <dt>開始／恢復</dt><dd>{{ pick.started_at }}　→　{{ endText(pick) }}</dd>
        <dt>開盤</dt><dd>{{ pick.market || '—' }}</dd>
        <dt>動作政策</dt><dd>{{ policyText(pick) }}</dd>
        <template v-for="c in ALERT_CONTACT_COLS" :key="c.k"><dt>{{ c.label }}</dt><dd>{{ pick[c.k] || '未提供' }}</dd></template>
        <dt>真假</dt><dd><span class="vd">{{ VERDICT[pick.verdict] || '未回報' }}
          <template v-if="!isViewer"><button type="button" class="mini" :disabled="busyId === pick.id" @click="verdict(pick, 'true')">真</button>
            <button type="button" class="mini" :disabled="busyId === pick.id" @click="verdict(pick, 'false')">假</button></template></span></dd>
      </dl>
    </section>

    <details v-if="st?.can_edit" class="more" data-testid="wug-more">
      <summary>更多</summary>
      <section class="set">
      <label><input v-model="form.enabled" type="checkbox"> 啟用告警事件</label>
      <label>心跳（分鐘）<input v-model="form.heartbeat_min" type="number" min="3" max="240"></label>
      <label>升級（分鐘）<input v-model="form.escalate_min" type="number" min="5" max="1440"></label>
      <label>摘要間隔（分鐘）<input v-model="form.digest_min" type="number" min="10" max="1440"></label>
      <label>IP 對不到資產<select v-model="form.unmatched_tier"><option value="red">立即</option><option value="yellow">每日彙整</option></select></label>
      <label>保存（天）<input v-model="form.retention_days" type="number" min="90" max="3650"></label>
      <label>斷線滿幾分鐘才發<input v-model="form.confirm_min" type="number" min="0" max="120"><InfoNote>非開盤時段的立即告警要持續滿這麼久才推；之前就恢復的併入 08:00 彙整。0＝一斷就推。</InfoNote></label>
      <label>只推這些動作政策<input v-model="form.notify_policies" type="text" maxlength="500" placeholder="空白＝全部"><InfoNote>填 WhatsUp 動作政策名稱裡的字，逗號分開。不符合的照樣記錄，只是不推；取不到政策的一律照推。</InfoNote></label>
      <button type="button" class="mini" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button>
      <InfoNote>心跳：多久沒收到資料就推「監控中斷」。升級：彙整裡的事件持續多久改成立即。保存：只清已恢復的事件。Teams 送出方式沿用「通知設定 → Teams 通知」。</InfoNote>
      </section>
    </details>
    <V2AlertNotify :event-id="notifyId" @close="notifyId = null" @sent="load" />
    <div v-if="muteRow" class="dlg-bg" @click.self="muteRow = null">
      <div class="dlg" role="dialog" aria-label="加入不通知">
        <h3>不通知：{{ muteRow.who || muteRow.ip }}（{{ muteRow.ip || '無 IP' }}）</h3>
        <label><input v-model="mf.whole" type="radio" :value="true"> 這台全部監控項目</label>
        <label><input v-model="mf.whole" type="radio" :value="false"> 只有「{{ muteRow.monitor }}」</label>
        <div class="row">星期（都不勾＝每天）：<label v-for="(d, i) in WD" :key="i"><input v-model="mf.days" type="checkbox" :value="String(i + 1)"> {{ d }}</label></div>
        <div class="row">時段（都不填＝全天）：<input v-model="mf.from" type="time" aria-label="起"> ～ <input v-model="mf.to" type="time" aria-label="訖"></div>
        <div class="row">原因：<input v-model="mf.note" type="text" maxlength="200" placeholder="例：週末例行 reboot"></div>
        <div class="row"><button type="button" class="mini" :disabled="muteBusy" @click="saveMute">{{ muteBusy ? '加入中…' : '加入' }}</button><button type="button" class="mini" @click="muteRow = null">取消</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 10px; }
.hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; }
.chip.good { background: var(--good-soft); color: var(--good); }
.chip.warn { background: var(--warn-soft); color: var(--warn-text); }
.chip.bad { background: var(--bad-soft); color: var(--bad); }
.chip.plain { background: var(--sub); color: var(--ink-aux); }
.hd .chip, td .chip { margin-right: 6px; }
.sm { color: var(--ink-aux); font-size: 12px; }
.pickb { border: 0; background: none; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; text-align: left; }
.pickb.on { font-weight: 700; }
.pv { border: 1px solid var(--brand); border-radius: 12px; background: var(--card); padding: 10px 12px; }
.pvh { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; } .sp { flex: 1; }
.kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 4px 12px; margin: 0; font-size: 13.5px; } .kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.more { border-top: 1px solid var(--line); padding-top: 8px; font-size: 13px; }
.more summary { cursor: pointer; color: var(--ink-aux); margin-bottom: 8px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); margin: 0; }
.warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); border-radius: 10px; padding: 8px 14px; font-size: 13.5px; margin: 0; }
.num { font-family: inherit; font-size: 13px; padding: 2px 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--ink); cursor: pointer; }
.num b { font-family: var(--disp); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.link { font-family: var(--disp); border: 0; background: none; color: var(--brand); text-decoration: underline; cursor: pointer; padding: 0; }
.subs { display: flex; gap: 6px; border-bottom: 1px solid var(--line); padding-bottom: 8px; flex-wrap: wrap; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--mint); color: var(--brand-dark); }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.filters select, .filters input { font-family: inherit; font-size: 13px; padding: 3px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); color: var(--ink); }
.set { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
.set input[type=number] { width: 80px; margin-left: 6px; }
.set select { margin-left: 6px; }
.vd { display: inline-flex; gap: 4px; align-items: center; }
.dlg-bg { position: fixed; inset: 0; background: rgba(0,0,0,.3); display: flex; align-items: center; justify-content: center; z-index: 50; }
.dlg { background: var(--card); border-radius: 12px; padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; min-width: 360px; max-width: 92vw; }
.dlg h3 { margin: 0; font-size: 15px; }
.dlg .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.dlg input[type=text] { flex: 1; min-width: 200px; }
</style>
