<script setup lang="ts">
// 每日摘要信設定＋寄送紀錄（2.x 第四批從舊「今日變化」頁搬出來，只放在 ⚙ 系統設定「通知設定」頁籤；
// 舊頁的摘要信頁籤改成連到這裡，設定只有一個入口）。API 不變：/api/digest/settings、preview、test-mail、mail-log。
interface Settings {
  snapshot_time: string; retain_days: number; gap_days: number; count_change_pct: number
  count_change_min: number; uptime_days: number; mail_enabled: boolean; mail_time: string
  recipients: string; base_url: string; subject_prefix: string
}
interface SettingsOut { settings: Settings; corrupt: string | null; missing: string[]; can_edit: boolean }
interface LogRow { id: number; run_date: string; kind: string; sent_at: string; recipients: string; subject: string | null; status: string; detail: string | null; smtp_host: string | null }
const { apiFetch } = useApi()
const { showToast } = useToast()
// ── 寄信設定（限管理員改）──
const sv = ref<SettingsOut | null>(null)
const form = ref<Settings | null>(null)
const svError = ref('')
async function loadSettings() {
  svError.value = ''
  try {
    sv.value = await apiFetch<SettingsOut>('/api/digest/settings')
    form.value = { ...sv.value.settings }
  } catch (e: any) {
    svError.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}
const preview = ref<{ subject: string; body: string } | null>(null)
const logs = ref<LogRow[]>([])
const logError = ref('')
async function loadLogs() {
  logError.value = ''
  try {
    logs.value = (await apiFetch<{ items: LogRow[] }>('/api/digest/mail-log')).items
  } catch (e: any) {
    logError.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}
onMounted(() => { loadSettings(); loadLogs() })

const saving = ref(false)
async function save() {
  if (!form.value) return
  saving.value = true
  try {
    await apiFetch('/api/digest/settings', { method: 'PUT', body: form.value })
    showToast('已儲存', 'success')
    await loadSettings()
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? ''}`, 'error', 12000)
  } finally {
    saving.value = false
  }
}
async function doPreview() {
  try {
    preview.value = await apiFetch<{ subject: string; body: string }>('/api/digest/preview')
  } catch (e: any) {
    showToast(`預覽失敗：${e?.data?.detail ?? e?.message ?? ''}`, 'error', 12000)
  }
}
const testing = ref(false)
async function testMail() {
  testing.value = true
  try {
    const r = await apiFetch<{ ok: boolean; detail?: string; reason?: string }>('/api/digest/test-mail', { method: 'POST' })
    showToast(r.ok ? `測試信已送交 SMTP：${r.detail}` : `沒寄出：${r.reason ?? r.detail}`, r.ok ? 'success' : 'error', 12000)
  } catch (e: any) {
    showToast(`寄測試信失敗：${e?.data?.detail ?? e?.message ?? ''}`, 'error', 12000)
  } finally {
    testing.value = false
  }
}
const logRows = computed(() => logs.value.map((r, i) => ({ ...r, no: i + 1 })))
const logSort = useSort(logRows, 'no')
</script>

<template>
  <div>
    <!-- 摘要信設定 -->
    <div class="panel">
      <p v-if="svError" class="err">{{ svError }}</p>
      <p v-if="sv?.corrupt" class="err">{{ sv.corrupt }}</p>
      <div v-if="sv?.missing?.length" class="note">還缺：{{ sv.missing.join('、') }}
        <InfoNote>SMTP 主機／埠／寄件者沿用「系統設定 → 寄信設定」那一份（弱點到期寄信同一組），這裡不重填。</InfoNote>
      </div>
      <template v-if="form">
        <div class="grid">
          <label>寄信 <input v-model="form.mail_enabled" type="checkbox" :disabled="!sv?.can_edit"></label>
          <label>寄送時間 <input v-model="form.mail_time" class="in" :disabled="!sv?.can_edit"></label>
          <label class="wide">收件人 <input v-model="form.recipients" class="in wide-in" :disabled="!sv?.can_edit"></label>
          <label class="wide">系統網址 <input v-model="form.base_url" class="in wide-in" :disabled="!sv?.can_edit"></label>
          <label>主旨前綴 <input v-model="form.subject_prefix" class="in" :disabled="!sv?.can_edit"></label>
          <label>快照時間 <input v-model="form.snapshot_time" class="in" :disabled="!sv?.can_edit"></label>
          <label>快照保留（天） <input v-model.number="form.retain_days" type="number" class="num" :disabled="!sv?.can_edit"></label>
          <label>收集中斷（天） <input v-model.number="form.gap_days" type="number" class="num" :disabled="!sv?.can_edit"></label>
          <label>數量變化（%） <input v-model.number="form.count_change_pct" type="number" class="num" :disabled="!sv?.can_edit"></label>
          <label>數量變化（筆） <input v-model.number="form.count_change_min" type="number" class="num" :disabled="!sv?.can_edit"></label>
          <label>開機過久（天） <input v-model.number="form.uptime_days" type="number" class="num" :disabled="!sv?.can_edit"></label>
        </div>
        <InfoNote>收件人用逗號分隔。系統網址＝信裡連結的開頭（例：http://10.99.0.10:3000）。
          寄信預設關著；寄不成一小時後自己補寄，到 18:00 為止，每一次嘗試都記在「寄送紀錄」。只有管理員能改。</InfoNote>
        <div class="acts">
          <button v-if="sv?.can_edit" class="btn small" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button>
          <button class="btn small" @click="doPreview">預覽信的內容</button>
          <button v-if="sv?.can_edit" class="btn small" :disabled="testing" @click="testMail">{{ testing ? '寄送中…' : '立即寄一封測試' }}</button>
        </div>
        <div v-if="preview" class="pv"><b>{{ preview.subject }}</b><pre>{{ preview.body }}</pre></div>
      </template>
    </div>

    <h3 class="h3">寄送紀錄</h3>
    <div class="panel">
      <p v-if="logError" class="err">{{ logError }}</p>
      <p v-else-if="!logRows.length" class="dim">還沒有寄送紀錄。</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="no" :active="logSort.sortKey.value" :dir="logSort.sortDir.value" @sort="logSort.toggle">#</SortTh>
            <SortTh k="sent_at" :active="logSort.sortKey.value" :dir="logSort.sortDir.value" @sort="logSort.toggle">時間</SortTh>
            <SortTh k="kind" :active="logSort.sortKey.value" :dir="logSort.sortDir.value" @sort="logSort.toggle">種類</SortTh>
            <SortTh k="status" :active="logSort.sortKey.value" :dir="logSort.sortDir.value" @sort="logSort.toggle">結果</SortTh>
            <SortTh k="recipients" :active="logSort.sortKey.value" :dir="logSort.sortDir.value" @sort="logSort.toggle">收件人</SortTh>
            <SortTh k="detail" :active="logSort.sortKey.value" :dir="logSort.sortDir.value" @sort="logSort.toggle">SMTP 回應</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in logSort.sorted.value" :key="r.id">
              <td class="mono">{{ r.no }}</td>
              <td class="mono">{{ r.sent_at }}</td>
              <td>{{ r.kind === 'test' ? '測試' : '排程' }}</td>
              <td><span class="pill" :class="r.status === 'sent' ? 'ok' : 'bad'">{{ r.status === 'sent' ? '已送交' : '失敗' }}</span></td>
              <td class="ell" :title="r.recipients">{{ r.recipients || '—' }}</td>
              <td class="ell" :title="r.detail || ''">{{ r.detail || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</template>

<style scoped>
.h3 { margin: 16px 0 6px; font-size: 15px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: default; }
.tbl-wrap { overflow-x: auto; }
.tbl-wrap table { width: 100%; font-size: 13px; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 16px; font-size: 13px; color: var(--ink-soft); margin-bottom: 10px; }
.grid label { display: flex; align-items: center; gap: 6px; }
.grid .wide { grid-column: 1 / -1; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 120px; }
.wide-in { flex: 1; width: auto; }
.num { width: 70px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.pv { background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; font-size: 13px; }
.pv pre { white-space: pre-wrap; font-family: inherit; margin: 6px 0 0; }
.pill { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; border: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.pill.ok { color: var(--brand-dark); border-color: var(--brand); background: var(--mint); }
.pill.bad { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); }
.ell { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dim { color: var(--muted); }
.err { color: var(--bad); }
.note { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
</style>
