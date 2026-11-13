<script setup lang="ts">
// WhatsUp 磁碟使用率 → Teams（⚙ 系統設定 → 通知設定 → Teams 磁碟告警）：準備／緊急兩級門檻、暴增條件、啟用、「用最近一筆資料送範例」、告警歷史（唯讀，最近 200 筆）。
// API：/api/teams-notify/disk-alert（GET）、/disk-alert/settings（PUT，admin）、/disk-alert/sample（POST，admin，真的會送）。說明文字收進 InfoNote。
interface Hist { id: number; __no: number; at: string; kind: string; batch_id: string | null; threshold: number | null; hosts: number; items: number
  status: string; delivery_key: string | null; delivery_status: string | null; summary: string; tier?: string | null }
interface Settings { enabled: boolean; warn: number; critical: number; surge_hours: number; surge_rise: number; surge_floor: number; exclude: string[] }
interface Out { settings: Settings; corrupt: string | null; teams_enabled: boolean; latest_day: string | null; history: Hist[]; can_edit: boolean; digest_time: string }
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : e?.message ?? '未知錯誤' }

const sv = ref<Out | null>(null)
const form = ref<Settings>({ enabled: false, warn: 80, critical: 90, surge_hours: 5, surge_rise: 15, surge_floor: 85, exclude: [] })
// 2.060.19（S6）：假磁碟排除（一行一段）、非緊急每日彙整時間（空白＝立即發）
const excludeText = ref('')
const digestTime = ref('08:00')
const digestOn = ref(true)
const err = ref('')
const busy = ref<'' | 'save' | 'sample'>('')
async function load() {
  err.value = ''
  try {
    sv.value = await apiFetch<Out>('/api/teams-notify/disk-alert')
    form.value = { ...sv.value.settings }
    excludeText.value = (sv.value.settings.exclude ?? []).join('\n')
    digestTime.value = sv.value.digest_time || '08:00'
    digestOn.value = !!sv.value.digest_time
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)
const can = computed(() => !!sv.value?.can_edit)

// 2.060.x 記憶體／SWAP 告警（預設關閉）：/api/teams-notify/mem-alert（GET）、/mem-alert/settings（PUT，admin）
const mem = ref<any>(null)
const mform = ref({ mem_enabled: false, mem_threshold: 90, swap_enabled: false, swap_threshold: 50 })
const merr = ref('')
async function loadMem() {
  merr.value = ''
  try { mem.value = await apiFetch<any>('/api/teams-notify/mem-alert'); mform.value = { ...mem.value.settings } } catch (e: any) { merr.value = `載入失敗：${errMsg(e)}` }
}
onMounted(loadMem)
async function saveMem() {
  try {
    const f = mform.value
    await apiFetch('/api/teams-notify/mem-alert/settings', { method: 'PUT', body: { mem_enabled: !!f.mem_enabled, mem_threshold: Number(f.mem_threshold), swap_enabled: !!f.swap_enabled, swap_threshold: Number(f.swap_threshold) } })
    showToast('已儲存', 'success'); await loadMem()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error', 12000) }
}
const memCols = [{ k: 'day', label: '資料日', mono: true }, { k: 'kind', label: '種類', text: (r: any) => (r.kind === 'swap' ? 'SWAP' : '記憶體') },
  { k: 'threshold', label: '門檻', mono: true, text: (r: any) => `${r.threshold}%` }, { k: 'hosts', label: '台數', mono: true },
  { k: 'status', label: '處理', text: (r: any) => (r.status === 'queued' ? '已排入 Teams' : '只記錄（Teams 未啟用）') },
  { k: 'summary', label: '內容', brief: (r: any) => (r.summary || '').split('\n')[0] }]

async function save() {
  busy.value = 'save'
  try {
    const f = form.value
    if (!(Number(f.warn) < Number(f.critical))) { showToast('準備門檻要小於緊急門檻', 'error', 8000); return }
    await apiFetch('/api/teams-notify/disk-alert/settings', { method: 'PUT', body: {
      enabled: f.enabled, warn: Number(f.warn), critical: Number(f.critical),
      surge_hours: Number(f.surge_hours), surge_rise: Number(f.surge_rise), surge_floor: Number(f.surge_floor),
      exclude: excludeText.value.split('\n').map(x => x.trim()).filter(Boolean), digest_time: digestOn.value ? digestTime.value.trim() : '' } })
    showToast('已儲存', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = '' }
}
async function sample() {
  if (!confirm(`這會真的送一則【測試】訊息到 Teams 頻道。\n內容用最近一天（${sv.value?.latest_day ?? '—'}）匯入的 WhatsUp 磁碟資料組成，分緊急／準備／暴增列出（未達門檻的也列）。\n確定送出？`)) return
  busy.value = 'sample'
  try {
    const r = await apiFetch<{ ok: boolean; outcome: string; http: number | null; reason: string }>('/api/teams-notify/disk-alert/sample', { method: 'POST', body: { confirm: true } })
    const code = r.http != null ? `HTTP ${r.http}` : '無 HTTP 回應'
    if (r.outcome === 'sent') showToast(`範例已送出（${code}），請到 Teams 頻道確認。`, 'success', 12000)
    else if (r.outcome === 'uncertain') showToast(`結果不確定（${code}）：可能已送達，請到頻道確認；不會自動重送。`, 'warn', 15000)
    else showToast(`送出失敗（${code}）：${r.reason || '未知原因'}`, 'error', 15000)
    await load()
  } catch (e: any) { showToast(`沒有送出：${errMsg(e)}`, 'error', 12000) } finally { busy.value = '' }
}

const KIND: Record<string, string> = { alert: '告警', sample: '範例（測試）', stale: '補匯入的舊資料' }
const TIER: Record<string, string> = { critical: '緊急', warn: '準備', surge: '暴增' }
const STATUS: Record<string, string> = { digest: '併入每日彙整', queued: '已排入 Teams', recorded: '只記錄（Teams 未啟用）', sample: '範例', stale: '只記錄（兩天前的資料不發）' }
const DELIV: Record<string, string> = { pending: '待送', sending: '送出中', sent: '已送出', failed: '重試中', dead: '放棄', expired: '過期未送', uncertain: '不確定', skipped: '不需送', cancelled: '已取消' }
const cols = [
  { k: 'at', label: '時間', mono: true },
  { k: 'kind', label: '種類', text: (r: Hist) => KIND[r.kind] ?? r.kind },
  { k: 'tier', label: '級別', text: (r: Hist) => (r.tier ? TIER[r.tier] ?? r.tier : '—') },
  { k: 'threshold', label: '門檻', mono: true, text: (r: Hist) => (r.threshold == null ? '—' : `${r.threshold}%`) },
  { k: 'hosts', label: '台數', mono: true },
  { k: 'status', label: '處理', text: (r: Hist) => STATUS[r.status] ?? r.status },
  { k: 'delivery_status', label: 'Teams 結果', text: (r: Hist) => (r.delivery_status ? DELIV[r.delivery_status] ?? r.delivery_status : '—') },
  { k: 'summary', label: '內容', brief: (r: Hist) => (r.summary || '').split('\n')[0] },
  { k: 'batch_id', label: '匯入批次', mono: true },
]
</script>

<template>
  <div>
    <div class="panel">
      <div class="hd"><b>WhatsUp 磁碟使用率告警</b>
        <InfoNote>每天的 WhatsUp 中繼匯入成功後自動檢查：任一台、任一顆磁碟當天最高使用率達「緊急」門檻列緊急、達「準備」門檻列準備，每一級彙整成一則 Teams 訊息（緊急先送；一台一行，最多 5 行，其餘寫「另有 N 台」）。同一台同一顆磁碟同一天每一級只通知一次；當天已通知「準備」、之後升到「緊急」會再通知一次。「暴增」：在設定的小時數內上升達設定的百分點、且升到下限以上，另送一則（一顆一行，寫從幾 % 到幾 %、台北時間幾點到幾點），同一顆同一天一次。送出沿用「Teams 通知」的網址與開關；Teams 通知沒啟用時只記錄在下方歷史、不送。只送 Teams、不連任何主機，所以盤中也會照常處理。主機名用資產 IP 對應，對不到顯示 IP。CPU 目前不告警；記憶體／SWAP 在下面另外設定。</InfoNote>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-if="sv?.corrupt" class="err">{{ sv.corrupt }}</p>
      <div v-if="sv && form.enabled && !sv.teams_enabled" class="note">Teams 通知目前未啟用：超標只會記錄，不會送出。</div>
      <template v-if="sv">
        <div class="grid">
          <label>啟用 <input v-model="form.enabled" type="checkbox" :disabled="!can"></label>
          <label>準備（%） <input v-model.number="form.warn" type="number" min="50" max="100" step="1" class="num" :disabled="!can" aria-label="準備門檻"></label>
          <label>緊急（%） <input v-model.number="form.critical" type="number" min="50" max="100" step="1" class="num" :disabled="!can" aria-label="緊急門檻"></label>
          <label>最近資料 <span class="mono">{{ sv.latest_day ?? '—' }}</span></label>
        </div>
        <div class="grid">
          <label>暴增：時間窗（小時） <input v-model.number="form.surge_hours" type="number" min="0.5" max="24" step="0.5" class="num" :disabled="!can" aria-label="暴增時間窗"></label>
          <label>上升（百分點） <input v-model.number="form.surge_rise" type="number" min="1" max="100" step="1" class="num" :disabled="!can" aria-label="暴增上升幅度"></label>
          <label>升到（%）以上 <input v-model.number="form.surge_floor" type="number" min="50" max="100" step="1" class="num" :disabled="!can" aria-label="暴增下限"></label>
          <InfoNote>暴增：同一顆磁碟在「時間窗」內上升達「上升」設定的百分點，而且上升終點達「升到」設定的使用率，就通知。用 WhatsUp 每次輪詢的最高值（沒有才用平均）。終點達緊急門檻時以緊急送出。</InfoNote>
        </div>
        <div class="grid">
          <label><input v-model="digestOn" type="checkbox" :disabled="!can" data-testid="digest-on"> 非緊急改每日彙整</label>
          <label>彙整時間 <input v-model="digestTime" type="time" class="num" :disabled="!can || !digestOn" aria-label="每日彙整時間" data-testid="digest-time"></label>
          <InfoNote>勾選：準備級、沒到緊急的暴增、WhatsUp 🟡 告警不立即發，把「上一次彙整之後」累積的，在這個時間（台北時間，預設 08:00，每天含假日）合成一則，送到「Teams 通知」設定的同一個地方。緊急、🔴 斷線、恢復照舊立即發。不勾＝全部立即發（舊做法）。</InfoNote>
          <label class="wide">排除的假磁碟（磁碟名稱含這段字就不算，一行一個） <textarea v-model="excludeText" rows="2" class="ta" :disabled="!can" aria-label="排除的假磁碟" data-testid="disk-exclude"></textarea></label>
          <InfoNote>磁碟名稱含其中任一段文字（不分大小寫）就不算、不告警，每日檢查的磁碟列也一起排除。預設排除交換器的 devfs 與 /packages/mnt/（永遠 100%）。</InfoNote>
        </div>
        <div class="acts">
          <button v-if="can" class="btn small" :disabled="busy === 'save'" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存' }}</button>
          <button v-if="can" class="btn small danger" :disabled="busy === 'sample' || !sv.latest_day" @click="sample">{{ busy === 'sample' ? '送出中…' : '用最近一筆資料送範例' }}</button>
          <InfoNote>範例會真的送到 Teams：用最近一天匯入的資料組成正式告警的格式，標題寫【測試】，依緊急／準備／暴增分段列出（未達門檻的也列，只為了看格式），不記去重。條件同「送測試訊息」：要先在 Teams 通知勾「對外送出已確認」並儲存；每 60 秒最多一次（與送測試訊息共用）。</InfoNote>
        </div>
      </template>
    </div>
    <div class="panel mt" data-testid="mem-alert">
      <div class="hd"><b>WhatsUp 記憶體／SWAP 告警</b>
        <InfoNote>每天 WhatsUp 中繼匯入成功後，看前一天（台北時間）的資料：記憶體＝每台最高使用率達門檻；SWAP＝名稱像 swap 的那顆磁碟最高使用率達門檻（同一行附這台的記憶體最高值）。每一種一天最多一則，一台一行最多 5 行、其餘寫「另有 N 台」。預設兩個都關閉；送出沿用「Teams 通知」的網址與開關。</InfoNote></div>
      <p v-if="merr" class="err">{{ merr }}</p>
      <div v-if="mem" class="grid">
        <label>記憶體告警 <input v-model="mform.mem_enabled" type="checkbox" :disabled="!mem.can_edit"></label>
        <label>記憶體門檻（%） <input v-model.number="mform.mem_threshold" type="number" min="1" max="100" step="1" class="num" :disabled="!mem.can_edit" aria-label="記憶體門檻"></label>
        <label>SWAP 告警 <input v-model="mform.swap_enabled" type="checkbox" :disabled="!mem.can_edit"></label>
        <label>SWAP 門檻（%） <input v-model.number="mform.swap_threshold" type="number" min="1" max="100" step="1" class="num" :disabled="!mem.can_edit" aria-label="SWAP 門檻"></label>
      </div>
      <div v-if="mem?.can_edit" class="acts"><button class="btn small" @click="saveMem">儲存</button></div>
      <V2Table v-if="mem" :rows="mem.history ?? []" :cols="memCols" prefix="M" empty="還沒有記憶體／SWAP 告警。" />
    </div>
    <h3 class="h3">告警歷史</h3>
    <div class="panel">
      <V2Table :rows="sv?.history ?? []" :cols="cols" prefix="H" empty="還沒有磁碟告警。WhatsUp 資料匯入後若有磁碟達門檻，會列在這裡。" />
    </div>
  </div>
</template>

<style scoped>
.h3 { margin: 16px 0 6px; font-size: 15px; }
.mt { margin-top: 12px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px 16px; font-size: 13px; color: var(--ink-soft); margin-bottom: 10px; }
.grid label { display: flex; align-items: center; gap: 6px; }
.num { width: 70px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.grid .wide { grid-column: span 2; align-items: flex-start; }
.ta { flex: 1; min-width: 160px; font-family: var(--disp); font-size: 13px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; }
.mono { font-family: var(--disp); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; align-items: center; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: default; }
.btn.danger { border-color: var(--warn); color: var(--warn-text); }
.err { color: var(--bad); }
.note { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
</style>
