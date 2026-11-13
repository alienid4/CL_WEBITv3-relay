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

const SUBS = [{ k: 'now', label: '目前告警' }, { k: 'list', label: '事件清單' }, { k: 'month', label: '每月彙總' }, { k: 'notice', label: '通知紀錄' }, { k: 'unmatched', label: '對不到的告警' }, { k: 'mute', label: '不通知清單' }, { k: 'settings', label: '設定' }]
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

const HB: Record<string, string> = { ok: '正常', down: '中斷', never: '從未收到' }
const durText = (m: number) => (m < 120 ? `${m} 分` : `${Math.floor(m / 60)} 小時 ${m % 60} 分`)
const VERDICT: Record<string, string> = { true: '真', false: '假' }
const evCols = [
  { k: 'tier_label', label: '分級' }, { k: 'who', label: '機器', link: whoLink }, { k: 'ip', label: 'IP', mono: true, link: ipLink },
  { k: 'monitor', label: '監控項目' }, { k: 'status_text', label: 'WhatsUp 狀態' }, { k: 'started_at', label: '開始', mono: true },
  { k: 'ended_at', label: '恢復', mono: true, text: (r: any) => r.ended_at || '未恢復' }, { k: 'duration_min', label: '持續', mono: true, text: (r: any) => durText(r.duration_min) },
  { k: 'system_name', label: '業務系統', text: (r: any) => (r.api_id ? `${r.api_id} ${r.system_name || ''}` : '未登記') }, ...ALERT_CONTACT_COLS, { k: 'owner', label: '負責人' },
  { k: 'market', label: '開盤' }, { k: 'policy', label: 'WhatsUp 動作政策', text: (r: any) => (r.policy == null ? '未取得' : r.policy || '未設定') },
  { k: 'tier_reason', label: '分級理由' }, { k: 'verdict', label: '真假', text: (r: any) => VERDICT[r.verdict] || '未回報' },
  { k: 'notify', label: '通知', text: (r: any) => r.notified_text || '' },
]
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
const scanText = computed(() => {
  const s = st.value?.last_scan
  if (!s) return '還沒有完整掃描'
  return `${s.snap_at.slice(5, 16)}・${s.complete ? '完整' : '不完整'}・看了 ${s.devices_seen} 台・有問題 ${s.problems} 台`
})
</script>

<template>
  <div class="box">
    <div v-if="st" class="hd">
      <span class="chip" :class="st.settings.enabled ? 'good' : 'warn'">{{ st.settings.enabled ? '已啟用' : '未啟用' }}</span>
      <span class="chip" :class="st.heartbeat === 'ok' ? 'good' : st.heartbeat === 'down' ? 'bad' : 'warn'">資料 {{ HB[st.heartbeat] }}</span>
      <span class="muted">最後收到 {{ st.last_received || '—' }}・最近掃描 {{ scanText }}・現在{{ st.market }}</span>
      <button type="button" class="num" @click="openList('open')">未恢復 <b>{{ st.open }}</b></button>
      <button type="button" class="num" @click="sub = 'now'">維護中 <b>{{ st.maintenance }}</b></button>
      <button type="button" class="num" @click="sub = 'unmatched'">對不到資產 <b>{{ st.unmatched }}</b></button>
      <button type="button" class="mini" @click="load">重新整理</button>
      <InfoNote>中繼 PC 每分鐘問一次 WhatsUp 群組狀態，有變化才找出是哪台、哪個監控項目（翻頁慢，約 3～5 分鐘）。分級：業務系統「第一類」的正式機（環境沒填也算）立即推 Teams；其他每小時一則摘要；IP 對不到資產的照設定（預設立即，不猜成不重要）；摘要裡的事件持續超過「升級」分鐘數就升成立即。一個事件只推發生、升級、恢復各一次。WhatsUp 上設成維護中的只記錄不推。超過心跳分鐘數沒收到資料會推一則「監控中斷」，代表這段時間看不到，不代表沒事。不完整的掃描（翻頁逾時）只會新增告警，不會把舊告警當成恢復。開盤／非開盤只改訊息上的標示。只讀 WhatsUp，不連任何主機，所以開盤時間也照跑（使用者 11-03 決定）。<br>每列帶告警聯絡資訊（AP 團隊、單位、系統名稱、系統分級、email；來源是系統設定 → 聯絡資訊 →「告警聯絡（APID）」，沒有的寫「未提供」）。「通知」按鈕先顯示收件人與內容，按確認才送（收件人是 Teams 通知設定的頻道，AP email 只寫在內容裡、不會直接寄給 AP）；通知過的再按是重送，要另外勾確認。只能看的帳號沒有這個按鈕。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-if="st?.settings_problem" class="err">{{ st.settings_problem }}</p>
    <p v-if="st && st.heartbeat === 'down'" class="warn">已超過 {{ st.settings.heartbeat_min }} 分鐘沒收到告警資料：下面是最後收到時的狀態，不代表現在沒事。</p>
    <div class="subs"><button v-for="s in SUBS.filter(x => x.k !== 'settings' || st?.can_edit)" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="sub = s.k">{{ s.label }}{{ s.k === 'now' ? ` ${nowRows.length}` : '' }}</button></div>

    <V2Table v-if="sub === 'now'" :rows="nowRows" :cols="evCols" prefix="A" empty="目前沒有未恢復的告警">
      <template v-if="!isViewer" #cell-verdict="{ row }">
        <span class="vd">{{ VERDICT[row.verdict] || '' }}
          <button type="button" class="mini" :disabled="busyId === row.id" @click="verdict(row, 'true')">真</button>
          <button type="button" class="mini" :disabled="busyId === row.id" @click="verdict(row, 'false')">假</button>
        </span>
      </template>
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
        <span v-if="f.mk || f.ip || f.dev" class="chip warn">只看 {{ f.mk || f.ip || ('WhatsUp 設備 ' + f.dev) }}{{ f.nomk === '1' ? '（對不到資產的）' : '' }}</span>
        <button type="submit" class="mini">套用</button><button type="button" class="mini" @click="clearFilter">清除</button>
      </form>
      <V2Table :rows="listRows" :cols="evCols" prefix="E" empty="沒有符合條件的告警事件">
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
      <p class="muted">在「目前告警」或「事件清單」那一列按「不通知」加入。符合的告警照樣記錄、畫面看得到，只是不推 Teams。<InfoNote>比對的是告警「開始時間」落在哪一天、幾點。時段起 &gt; 訖代表跨午夜（例：週六 22:00～06:00 包含週日凌晨）。只有管理員能加、能刪，每次都記操作紀錄。</InfoNote></p>
      <V2Table :rows="mutes" :cols="muteCols" prefix="X" empty="目前沒有不通知規則">
        <template #cell-act="{ row }"><button v-if="st?.can_edit" type="button" class="mini" @click="delMute(row)">刪除</button></template>
      </V2Table>
    </template>

    <section v-else-if="sub === 'settings' && st?.can_edit" class="set">
      <label><input v-model="form.enabled" type="checkbox"> 啟用告警事件</label>
      <label>心跳（分鐘）<input v-model="form.heartbeat_min" type="number" min="3" max="240"></label>
      <label>升級（分鐘）<input v-model="form.escalate_min" type="number" min="5" max="1440"></label>
      <label>摘要間隔（分鐘）<input v-model="form.digest_min" type="number" min="10" max="1440"></label>
      <label>IP 對不到資產<select v-model="form.unmatched_tier"><option value="red">立即</option><option value="yellow">每日彙整</option></select></label>
      <label>保存（天）<input v-model="form.retention_days" type="number" min="90" max="3650"></label>
      <label>斷線滿幾分鐘才發<input v-model="form.confirm_min" type="number" min="0" max="120"><InfoNote>非開盤時段的 🔴 告警要持續滿這麼多分鐘還沒恢復，才立即推 Teams（開盤時段一斷就推）。在這之前就恢復的（例如夜間排程重開機，多半 0～8 分鐘就回來）不立即推，改放進每天 08:00 的彙整。0＝不等，一斷就推。代價：真的故障會晚這麼多分鐘才收到。</InfoNote></label>
      <label>只推這些動作政策<input v-model="form.notify_policies" type="text" maxlength="500" placeholder="空白＝全部"><InfoNote>填 WhatsUp「動作政策」名稱裡的字，多個用逗號分開（例：主機組）。有填時，只推監控項目掛的動作政策名稱含這些字的告警；WhatsUp 沒掛政策、或掛別組的，照樣記錄、畫面看得到，只是不推 Teams。沒取得動作政策的（舊版收集端、整台設備層級）一律照推，寧可多推不漏推。</InfoNote></label>
      <button type="button" class="mini" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button>
      <InfoNote>心跳：超過這麼久沒收到中繼 PC 的告警資料就推「監控中斷」（中繼 PC 每 5 分鐘至少送一份）。升級：🟡 摘要裡的事件持續超過這麼久就升成立即並推一則。IP 對不到資產時無法判斷是否為第一類，只能選立即或摘要，不能更低。保存預設 2 年，每天清一次已恢復且超過期限的事件（未恢復的不清）。Teams 的送出方式（Workflow 或內部寄信）沿用「通知設定 → Teams 通知」；那邊沒啟用時這裡只記錄、不送。</InfoNote>
    </section>
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
