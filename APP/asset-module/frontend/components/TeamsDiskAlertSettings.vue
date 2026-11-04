<script setup lang="ts">
// WhatsUp 磁碟使用率 → Teams（⚙ 系統設定 → 通知設定 → Teams 磁碟告警）：門檻、啟用、「用最近一筆資料送範例」、告警歷史（唯讀，最近 200 筆）。
// API：/api/teams-notify/disk-alert（GET）、/disk-alert/settings（PUT，admin）、/disk-alert/sample（POST，admin，真的會送）。說明文字收進 InfoNote。
interface Hist { id: number; __no: number; at: string; kind: string; batch_id: string | null; threshold: number | null; hosts: number; items: number
  status: string; delivery_key: string | null; delivery_status: string | null; summary: string }
interface Out { settings: { enabled: boolean; threshold: number }; corrupt: string | null; teams_enabled: boolean; latest_day: string | null; history: Hist[]; can_edit: boolean }
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : e?.message ?? '未知錯誤' }

const sv = ref<Out | null>(null)
const form = ref({ enabled: false, threshold: 90 })
const err = ref('')
const busy = ref<'' | 'save' | 'sample'>('')
async function load() {
  err.value = ''
  try {
    sv.value = await apiFetch<Out>('/api/teams-notify/disk-alert')
    form.value = { ...sv.value.settings }
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)
const can = computed(() => !!sv.value?.can_edit)

async function save() {
  busy.value = 'save'
  try {
    await apiFetch('/api/teams-notify/disk-alert/settings', { method: 'PUT', body: { enabled: form.value.enabled, threshold: Number(form.value.threshold) } })
    showToast('已儲存', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = '' }
}
async function sample() {
  if (!confirm(`這會真的送一則【測試】訊息到 Teams 頻道。\n內容用最近一天（${sv.value?.latest_day ?? '—'}）匯入的 WhatsUp 磁碟資料組成，不套門檻。\n確定送出？`)) return
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

const KIND: Record<string, string> = { alert: '告警', sample: '範例（測試）' }
const STATUS: Record<string, string> = { queued: '已排入 Teams', recorded: '只記錄（Teams 未啟用）', sample: '範例' }
const DELIV: Record<string, string> = { pending: '待送', sending: '送出中', sent: '已送出', failed: '重試中', dead: '放棄', expired: '過期未送', uncertain: '不確定', skipped: '不需送', cancelled: '已取消' }
const cols = [
  { k: 'at', label: '時間', mono: true },
  { k: 'kind', label: '種類', text: (r: Hist) => KIND[r.kind] ?? r.kind },
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
        <InfoNote>每天的 WhatsUp 中繼匯入成功後自動檢查：任一台、任一顆磁碟當天最高使用率達門檻，就彙整成一則 Teams 訊息（一台一行，最多 30 行，其餘寫「另有 N 台」）。同一台同一顆磁碟同一天只通知一次。送出沿用「Teams 通知」的網址與開關；Teams 通知沒啟用時只記錄在下方歷史、不送。只送 Teams、不連任何主機，所以盤中也會照常處理。主機名用資產 IP 對應，對不到顯示 IP。CPU／記憶體目前不告警。</InfoNote>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-if="sv?.corrupt" class="err">{{ sv.corrupt }}</p>
      <div v-if="sv && form.enabled && !sv.teams_enabled" class="note">Teams 通知目前未啟用：超標只會記錄，不會送出。</div>
      <template v-if="sv">
        <div class="grid">
          <label>啟用 <input v-model="form.enabled" type="checkbox" :disabled="!can"></label>
          <label>門檻（%） <input v-model.number="form.threshold" type="number" min="50" max="100" step="1" class="num" :disabled="!can" aria-label="門檻"></label>
          <label>最近資料 <span class="mono">{{ sv.latest_day ?? '—' }}</span></label>
        </div>
        <div class="acts">
          <button v-if="can" class="btn small" :disabled="busy === 'save'" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存' }}</button>
          <button v-if="can" class="btn small danger" :disabled="busy === 'sample' || !sv.latest_day" @click="sample">{{ busy === 'sample' ? '送出中…' : '用最近一筆資料送範例' }}</button>
          <InfoNote>範例會真的送到 Teams：用最近一天匯入的資料組成正式告警的格式，標題寫【測試】、不套門檻、不記去重。條件同「送測試訊息」：要先在 Teams 通知勾「對外送出已確認」並儲存；每 60 秒最多一次（與送測試訊息共用）。</InfoNote>
        </div>
      </template>
    </div>
    <h3 class="h3">告警歷史</h3>
    <div class="panel">
      <V2Table :rows="sv?.history ?? []" :cols="cols" prefix="H" empty="還沒有磁碟告警。WhatsUp 資料匯入後若有磁碟達門檻，會列在這裡。" />
    </div>
  </div>
</template>

<style scoped>
.h3 { margin: 16px 0 6px; font-size: 15px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px 16px; font-size: 13px; color: var(--ink-soft); margin-bottom: 10px; }
.grid label { display: flex; align-items: center; gap: 6px; }
.num { width: 70px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.mono { font-family: var(--disp); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; align-items: center; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: default; }
.btn.danger { border-color: var(--warn); color: var(--warn-text); }
.err { color: var(--bad); }
.note { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
</style>
