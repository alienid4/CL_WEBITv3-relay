<script setup lang="ts">
// ACT-03 Teams 單向通知設定（⚙ 系統設定 → 通知設定 → Teams 通知）：啟用／門檻／摘要時間／靜默時段／重試、
// Workflow 網址（密鑰：只能設定或清除，畫面永不回顯）、預覽（不送）、測試送出（只限本機模擬接收端）、最近投遞紀錄。
// API：/api/teams-notify/*。說明文字一律收進 InfoNote。
interface Settings {
  enabled: boolean; min_severity: string; send_recovery: boolean; send_incidents: boolean; summary_daily: boolean; summary_weekly: boolean
  summary_time: string; weekly_day: number; quiet_start: string; quiet_end: string; max_attempts: number; base_backoff_sec: number
  live_confirmed: boolean; dns_guard: boolean; base_url: string; webhook_set: boolean; channel: 'webhook' | 'email'; mail_to: string
}
interface SettingsOut { settings: Settings; corrupt: string | null; missing: string[]; can_edit: boolean; auth_disabled?: boolean; hold?: string | null; queue?: Record<string, number>; webhook_host?: string | null }
interface Delivery { id: number; __no: number; kind: string; source_ref: string; severity: string; title: string; status: string; status_label: string
  attempts: number; last_http: number | null; last_error: string | null; created_at: string; sent_at: string | null; next_try_at: string | null }
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : e?.message ?? '未知錯誤' }

const sv = ref<SettingsOut | null>(null)
const form = ref<Settings | null>(null)
const err = ref('')
const newUrl = ref('')
const busy = ref<'' | 'save' | 'url' | 'test' | 'live'>('')
async function load() {
  err.value = ''
  try {
    sv.value = await apiFetch<SettingsOut>('/api/teams-notify/settings')
    form.value = { ...sv.value.settings }
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
const logs = ref<Delivery[]>([])
const logErr = ref('')
async function loadLogs() {
  logErr.value = ''
  try { logs.value = (await apiFetch<{ items: Delivery[] }>('/api/teams-notify/log')).items } catch (e: any) { logErr.value = `投遞紀錄載入失敗：${errMsg(e)}` }
}
onMounted(() => { load(); loadLogs() })

async function save() {
  if (!form.value) return
  busy.value = 'save'
  try {
    const { webhook_set, ...body } = form.value
    await apiFetch('/api/teams-notify/settings', { method: 'PUT', body })
    showToast('已儲存', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = '' }
}
async function setUrl() {
  busy.value = 'url'
  try {
    await apiFetch('/api/teams-notify/webhook', { method: 'PUT', body: { url: newUrl.value } })
    newUrl.value = ''
    showToast('已設定 Workflow 網址（加密存放）', 'success')
    await load()
  } catch (e: any) { showToast(`設定失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = '' }
}
async function clearUrl() {
  if (!confirm('清除 Workflow 網址？清除後不會再送任何通知。')) return
  busy.value = 'url'
  try { await apiFetch('/api/teams-notify/webhook', { method: 'DELETE' }); showToast('已清除', 'success'); await load() }
  catch (e: any) { showToast(`清除失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = '' }
}

const KINDS = [{ k: 'alert', label: '嚴重告警' }, { k: 'recovery', label: '恢復' }, { k: 'daily', label: '每日摘要' }, { k: 'weekly', label: '每週摘要' }]
const pvKind = ref('alert')
const pv = ref<any>(null)
async function doPreview() {
  pv.value = null
  try { pv.value = await apiFetch(`/api/teams-notify/preview`, { query: { kind: pvKind.value } }) }
  catch (e: any) { showToast(`預覽失敗：${errMsg(e)}`, 'error', 12000) }
}
const pvFacts = computed(() => (pv.value?.card?.attachments?.[0]?.content?.body ?? []).find((b: any) => b.type === 'FactSet')?.facts ?? [])
async function testSend() {
  busy.value = 'test'
  try {
    const r = await apiFetch<{ ok: boolean; reason?: string; http?: number }>('/api/teams-notify/test', { method: 'POST' })
    showToast(r.ok ? `測試已送到本機模擬接收端（HTTP ${r.http}）` : `沒送出：${r.reason || ('HTTP ' + r.http)}`, r.ok ? 'success' : 'error', 12000)
    await loadLogs()
  } catch (e: any) { showToast(`測試失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = '' }
}
// 人為主動「送測試訊息到已設定的網址」：真的會送到 Teams 頻道，所以先二次確認（只顯示網域，不顯示完整網址）
// 目的地已設定：webhook 看網址、email 看收件信箱（以已儲存的設定為準）
const targetSet = computed(() => sv.value?.settings.channel === 'email' ? !!sv.value?.settings.mail_to : !!sv.value?.settings.webhook_set)
const liveOk = computed(() => !!sv.value?.settings.live_confirmed && targetSet.value)
async function liveTest() {
  const mail = sv.value?.settings.channel === 'email'
  const host = sv.value?.webhook_host || (mail ? '（已設定的收件信箱）' : '（已設定的 Workflow 網域）')
  if (!confirm(`這會真的送一則測試訊息${mail ? '（寄信，由收件人的流程轉成 Teams 私訊）' : '到 Teams 頻道'}。\n將送到${mail ? '信箱' : '網域'}：${host}\n內容固定為「測試訊息（IT 戰情室）」，不含真實告警資料。\n確定送出？`)) return
  busy.value = 'live'
  try {
    const r = await apiFetch<{ ok: boolean; outcome: string; http: number | null; reason: string }>('/api/teams-notify/test-live', { method: 'POST', body: { confirm: true } })
    const code = r.http != null ? `HTTP ${r.http}` : '無 HTTP 回應'
    if (r.outcome === 'sent') showToast(`測試訊息已送出（${code}）。請到 Teams 頻道確認有收到、格式正常。`, 'success', 12000)
    else if (r.outcome === 'uncertain') showToast(`結果不確定（${code}）：請求可能已送達，請到 Teams 頻道確認；系統不會自動重送。`, 'warn', 15000)
    else showToast(`送出失敗（${code}）：${r.reason || '未知原因'}`, 'error', 15000)
    await loadLogs()
  } catch (e: any) { showToast(`沒有送出：${errMsg(e)}`, 'error', 12000); await loadLogs() } finally { busy.value = '' }
}
async function retry(r: Delivery) {
  if (!confirm(r.status === 'uncertain'
    ? '這則的請求可能已經送達 Teams，重送可能造成重複訊息（卡片尾端的事件ID 可辨識）。確定重送？'
    : '重送這則通知？若對方其實已收到，會出現重複訊息。')) return
  try { await apiFetch(`/api/teams-notify/log/${r.id}/retry`, { method: 'POST' }); showToast('已重新排隊', 'success'); await loadLogs() }
  catch (e: any) { showToast(`重送失敗：${errMsg(e)}`, 'error', 12000) }
}

const KIND_LABEL: Record<string, string> = { alert: '告警', recovery: '恢復', incident: '事件開立', incident_closed: '事件結案', summary_daily: '每日摘要', summary_weekly: '每週摘要', test: '測試' }
const SEV_LABEL: Record<string, string> = { critical: '嚴重', warning: '警告', info: '輕微' }
const cols = [
  { k: 'created_at', label: '時間', mono: true },
  { k: 'kind', label: '種類', text: (r: Delivery) => KIND_LABEL[r.kind] ?? r.kind },
  { k: 'severity', label: '嚴重度', text: (r: Delivery) => SEV_LABEL[r.severity] ?? r.severity },
  { k: 'title', label: '內容' },
  { k: 'source_ref', label: '事件ID', mono: true },
  { k: 'status_label', label: '結果' },
  { k: 'attempts', label: '嘗試次數', mono: true },
  { k: 'last_http', label: 'HTTP', mono: true },
  { k: 'last_error', label: '錯誤摘要' },
  { k: 'act', label: '操作', text: () => '' },
]
const can = computed(() => !!sv.value?.can_edit)
</script>

<template>
  <div>
    <div class="panel">
      <div class="hd"><b>Teams 單向通知</b><span class="chip">未實機驗證</span>
        <InfoNote>重大告警、恢復與定期摘要，單向送到 Teams 頻道（經 Workflows 的「收到 Teams webhook 要求時」流程，Office 365 連接器已退役不使用）。預設關閉。告警來源是 WhatsUp 告警變化與正式事件，不另建資料。Workflow 網址等同密碼：加密存放、畫面與紀錄都不會顯示（貼上後只能設定或清除）。「對外送出已確認」沒勾之前，非本機的網址一則都不會送。預覽不送也不留紀錄；「測試送出」只限本機模擬接收端；「送測試訊息到 Teams」是管理員人為按下、真的送到頻道的連線測試（需已勾對外送出已確認、每 60 秒最多一次、內容固定、會留稽核）。時間用台北時間。投遞是「至少一次」：每則卡片尾端有事件ID，萬一重複收到可辨識；請求已送出卻沒收到回應的會標「不確定（可能已送達）」，不自動重送，需要時由管理員按「重送」。恢復／結案通知要等對應的告警／開立真的送達才會送。停用後再啟用不會補發停用期間的事件。</InfoNote>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-if="sv?.corrupt" class="err">{{ sv.corrupt }}</p>
      <div v-if="sv?.auth_disabled" class="note">登入停用中：為安全起見，Teams 通知的設定、測試與重送全部關閉（沒有真實身分可核對）。請先啟用登入。</div>
      <div v-if="sv?.missing?.length" class="note">還缺：{{ sv.missing.join('、') }}</div>
      <div v-if="sv?.hold" class="note">目前不會送出：{{ sv.hold }}。待送 {{ sv.queue?.pending ?? 0 }} 則、重試中 {{ sv.queue?.failed ?? 0 }} 則會留在佇列，條件解除後才送。</div>
      <div v-if="sv?.queue?.uncertain" class="note">有 {{ sv.queue.uncertain }} 則「不確定（可能已送達）」：請求送出後沒收到回應，系統不會自動重送，請到下方紀錄確認後再決定是否重送。</div>
      <template v-if="form">
        <div class="grid">
          <label class="wide">投遞方式
            <select v-model="form.channel" class="in" :disabled="!can" aria-label="投遞方式">
              <option value="webhook">Workflow 網址（直接連 Teams）</option><option value="email">內部寄信（轉 Teams 私訊）</option>
            </select>
            <InfoNote>正式區連不到外網時選「內部寄信」：用「寄信設定（SMTP）」那一頁的郵件主機寄到收件信箱，主旨開頭固定為 WEBIT3-ALERT；收件人在 Teams 工作流程建「收到主旨含 WEBIT3-ALERT 的信 → 私訊我」的流程，就會在 Teams 收到通知。</InfoNote></label>
          <label v-if="form.channel === 'email'" class="wide">收件信箱 <input v-model="form.mail_to" class="in wide-in" placeholder="name@公司網域（多個用逗號分隔）" :disabled="!can" aria-label="收件信箱"></label>
          <label v-if="form.channel !== 'email'" class="wide">Workflow 網址
            <span class="pill" :class="form.webhook_set ? 'ok' : 'bad'">{{ form.webhook_set ? '已設定' : '未設定' }}</span>
            <input v-if="can" v-model="newUrl" class="in wide-in" type="password" autocomplete="off" placeholder="貼上新網址（只寫入、不回顯）" aria-label="Workflow 網址">
            <button v-if="can" class="btn small" :disabled="!newUrl || busy === 'url'" @click="setUrl">設定</button>
            <button v-if="can && form.webhook_set" class="btn small" :disabled="busy === 'url'" @click="clearUrl">清除</button>
          </label>
          <label>啟用 <input v-model="form.enabled" type="checkbox" :disabled="!can"></label>
          <label>嚴重度門檻
            <select v-model="form.min_severity" class="in" :disabled="!can" aria-label="嚴重度門檻">
              <option value="critical">只送嚴重</option><option value="warning">警告以上</option><option value="info">全部</option>
            </select></label>
          <label>送恢復通知 <input v-model="form.send_recovery" type="checkbox" :disabled="!can"></label>
          <label>送正式事件開立／結案 <input v-model="form.send_incidents" type="checkbox" :disabled="!can"></label>
          <label>每日摘要 <input v-model="form.summary_daily" type="checkbox" :disabled="!can"></label>
          <label>每週摘要 <input v-model="form.summary_weekly" type="checkbox" :disabled="!can"></label>
          <label>摘要時間 <input v-model="form.summary_time" class="in" :disabled="!can" aria-label="摘要時間"></label>
          <label>每週摘要星期
            <select v-model.number="form.weekly_day" class="in" :disabled="!can" aria-label="每週摘要星期">
              <option v-for="(d, i) in ['週一','週二','週三','週四','週五','週六','週日']" :key="i" :value="i">{{ d }}</option>
            </select></label>
          <label>靜默開始 <input v-model="form.quiet_start" class="in" placeholder="22:00" :disabled="!can" aria-label="靜默開始"></label>
          <label>靜默結束 <input v-model="form.quiet_end" class="in" placeholder="07:00" :disabled="!can" aria-label="靜默結束"></label>
          <label>重試上限（次） <input v-model.number="form.max_attempts" type="number" class="num" :disabled="!can"></label>
          <label>退避起始（秒） <input v-model.number="form.base_backoff_sec" type="number" class="num" :disabled="!can"></label>
          <label>擋內部位址（DNS 檢查） <input v-model="form.dns_guard" type="checkbox" :disabled="!can">
            <InfoNote>送出前解析網址的 DNS，解析到內部或保留位址就拒絕，並直接連驗證過的位址（防 DNS 改指）。公司若必須經內部 proxy 才能出站才關閉；關閉動作會留稽核。</InfoNote></label>
          <label class="wide">系統網址 <input v-model="form.base_url" class="in wide-in" placeholder="http://10.99.0.10:3000" :disabled="!can" aria-label="系統網址"></label>
          <label class="wide">對外送出已確認 <input v-model="form.live_confirmed" type="checkbox" :disabled="!can">
            <InfoNote>管理員確認公司頻道與 Workflow 後才勾。沒勾時，真實 Teams 網址的通知會留在「待送」，不會對外送出。靜默時段內只有「嚴重」會送，其餘等時段結束。</InfoNote></label>
        </div>
        <div class="acts">
          <button v-if="can" class="btn small" :disabled="busy === 'save'" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存' }}</button>
          <select v-model="pvKind" class="in" aria-label="預覽種類"><option v-for="k in KINDS" :key="k.k" :value="k.k">{{ k.label }}</option></select>
          <button class="btn small" @click="doPreview">預覽（不送出）</button>
          <button v-if="can" class="btn small" :disabled="busy === 'test'" @click="testSend">測試送出（僅本機模擬）</button>
          <button v-if="can && targetSet" class="btn small danger" :disabled="busy === 'live' || !liveOk" :title="liveOk ? '' : '需先勾「對外送出已確認」並儲存'" @click="liveTest">{{ busy === 'live' ? '送出中…' : '送測試訊息到 Teams（真的會送出）' }}</button>
          <span v-if="can && targetSet && !liveOk" class="dim">需先勾「對外送出已確認」並按「儲存」</span>
        </div>
        <div v-if="pv" class="pv"><b>{{ pv.title }}</b>
          <span v-if="pv.demo" class="pill">示範資料</span>
          <ul><li v-for="f in pvFacts" :key="f.title">{{ f.title }}：{{ f.value }}</li></ul>
        </div>
      </template>
    </div>

    <h3 class="h3">最近投遞紀錄</h3>
    <div class="panel">
      <p v-if="logErr" class="err">{{ logErr }}</p>
      <V2Table v-else :rows="logs" :cols="cols" prefix="T" empty="還沒有投遞紀錄。啟用並設定網址後，告警、事件與摘要會出現在這裡；先按「預覽」可看卡片長相。">
        <template #cell-act="{ row }">
          <button v-if="can && row.kind !== 'test' && (row.status === 'dead' || row.status === 'expired' || row.status === 'uncertain')" class="btn small" @click="retry(row)">重送</button>
          <span v-else>—</span>
        </template>
      </V2Table>
    </div>
  </div>
</template>

<style scoped>
.h3 { margin: 16px 0 6px; font-size: 15px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: default; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px 16px; font-size: 13px; color: var(--ink-soft); margin-bottom: 10px; }
.grid label { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.grid .wide { grid-column: 1 / -1; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 120px; background: var(--card); color: var(--ink); }
.wide-in { flex: 1; width: auto; min-width: 200px; }
.num { width: 70px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; align-items: center; }
.pv { background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; font-size: 13px; }
.pv ul { margin: 6px 0 0; padding-left: 18px; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; border: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.pill.ok { color: var(--brand-dark); border-color: var(--brand); background: var(--mint); }
.pill.bad { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); }
.err { color: var(--bad); }
.dim { color: var(--muted); font-size: 12.5px; }
.btn.danger { border-color: var(--warn); color: var(--warn-text); }
.note { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
</style>
