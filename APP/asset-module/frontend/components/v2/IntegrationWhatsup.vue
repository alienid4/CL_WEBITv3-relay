<script setup lang="ts">
// WhatsUp Gold API（Q93）：定期把 WhatsUp 的告警抓回來，值班不必再手動複製 IP。
// 設定（網址、CA 憑證檔、帳密、輪詢間隔）＋連線測試＋立即輪詢。告警清單在「4.1 事件應變」的「目前告警」頁籤。
// 狀態：未實機驗證（回應格式依官方文件推定，要在公司網路實測）。
import { RUN_STATUS, type WhatsupConfig } from '~/types/integrations'
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const cfg = ref<WhatsupConfig | null>(null)
const form = reactive({ url: '', ca_bundle: '', enabled: false, interval_min: 5, credential: '' })
const runs = ref<any[]>([])
const err = ref('')
const busy = ref<'' | 'save' | 'test' | 'poll'>('')
const testResult = ref<{ ok: boolean; message: string } | null>(null)

async function load() {
  err.value = ''
  try {
    const c = await apiFetch<WhatsupConfig>('/api/integrations/whatsup')
    cfg.value = c
    Object.assign(form, { url: c.url, ca_bundle: c.ca_bundle, enabled: c.enabled, interval_min: c.interval_min, credential: c.credential })
    runs.value = (await apiFetch<{ rows: any[] }>('/api/integrations/whatsup/runs')).rows
  } catch (e: any) { err.value = `WhatsUp Gold 設定載入失敗：${errMsg(e)}` }
}
const creds = ref<{ name: string; username: string }[]>([])
async function loadCreds() {
  try { creds.value = (await apiFetch<{ name: string; kind: string; username: string }[]>('/api/credentials')).filter(c => c.kind === 'whatsup') }
  catch (e: any) { creds.value = []; err.value = `收集憑證清單載入失敗：${errMsg(e)}` }
}
onMounted(() => { load(); loadCreds() })

async function save() {
  busy.value = 'save'
  try {
    await apiFetch('/api/integrations/whatsup', { method: 'PUT', body: { ...form, interval_min: Number(form.interval_min) } })
    showToast('已儲存設定', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
async function test() {
  busy.value = 'test'; testResult.value = null
  try { testResult.value = await apiFetch<{ ok: boolean; message: string }>('/api/integrations/whatsup/test', { method: 'POST' }) }
  catch (e: any) { testResult.value = { ok: false, message: errMsg(e) } } finally { busy.value = '' }
}
async function pollNow() {
  busy.value = 'poll'
  try {
    const r = await apiFetch<{ status: string; message: string }>('/api/integrations/whatsup/poll', { method: 'POST' })
    showToast(r.message, r.status === 'ok' ? 'success' : r.status === 'partial' ? 'warn' : 'error', 8000)
    await load()
  } catch (e: any) { showToast(`輪詢沒有完成：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
const rows = computed(() => runs.value.map(r => ({ ...r, st: RUN_STATUS[r.status] ?? r.status, trig: r.trigger === 'manual' ? '手動' : '排程' })))
const cols = [
  { k: 'at', label: '時間', mono: true }, { k: 'trig', label: '觸發' }, { k: 'st', label: '結果' }, { k: 'devices', label: '裝置數', mono: true },
  { k: 'alerts', label: '告警中', mono: true }, { k: 'new_count', label: '新增', mono: true }, { k: 'cleared_count', label: '解除', mono: true },
  { k: 'started_by', label: '操作者' }, { k: 'message', label: '說明' },
]
</script>

<template>
  <div class="box">
    <div class="hd">
      <b class="t">WhatsUp Gold 告警</b><span class="chip">未實機驗證</span>
      <NuxtLink class="lk" to="/v2/incident?tab=alerts">看目前告警 ›</NuxtLink>
      <InfoNote>硬體故障以 WhatsUp Gold 為唯一來源，本系統不另外掃硬體。這裡定期讀取 WhatsUp 的告警裝置，存成清單與變化歷史，值班在「4.1 事件應變」的「目前告警」頁籤點 IP 直接查詢。只讀取，不改 WhatsUp 的任何設定。回應格式依官方文件推定，需在公司網路實測後才算驗證。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <dl class="kv">
      <dt>API 網址</dt><dd><input v-model="form.url" class="in" placeholder="https://wug.example.local:9644" aria-label="API 網址"></dd>
      <dt>收集憑證</dt><dd>
        <select v-model="form.credential" class="in s" aria-label="收集憑證">
          <option value="">（自動：唯一一筆）</option>
          <option v-for="c in creds" :key="c.name" :value="c.name">{{ c.name }}（{{ c.username }}）</option>
        </select>
        <NuxtLink class="lk" to="/v2/settings?st=identity&sub=credentials">到收集憑證設定帳密 ›</NuxtLink>
        <InfoNote>WhatsUp Gold 的帳號密碼統一在「系統設定 → 收集憑證」新增（類型選 whatsup），這裡只挑用哪一筆。密碼加密存放，畫面與 API 都不會顯示。</InfoNote>
      </dd>
      <dt>CA 憑證檔</dt><dd><input v-model="form.ca_bundle" class="in" placeholder="伺服器上的路徑，留空＝系統預設信任清單" aria-label="CA 憑證檔"> <InfoNote>一律驗證 TLS 憑證，沒有關閉驗證的選項。公司內部自簽憑證請把根憑證放到伺服器，在這裡填檔案路徑。</InfoNote></dd>
      <dt>定期輪詢</dt><dd><label class="chk"><input v-model="form.enabled" type="checkbox"> 啟用，每 <input v-model="form.interval_min" type="number" min="1" class="in xs" aria-label="間隔分鐘"> 分鐘一次</label></dd>
    </dl>
    <div class="acts">
      <button type="button" class="btn" :disabled="!!busy" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存設定' }}</button>
      <button type="button" class="btn" :disabled="!!busy" @click="test">{{ busy === 'test' ? '測試中…' : '連線測試' }}</button>
      <button type="button" class="btn pri" :disabled="!!busy" @click="pollNow">{{ busy === 'poll' ? '輪詢中…' : '立即輪詢' }}</button>
      <span v-if="testResult" :class="testResult.ok ? 'ok' : 'err'">{{ testResult.ok ? '連線成功：' : '連線失敗：' }}{{ testResult.message }}</span>
    </div>
    <b class="sub">輪詢紀錄</b>
    <V2Table :rows="rows" :cols="cols" prefix="R" empty="還沒有輪詢紀錄" />
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 12px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.t { font-size: 16px; }
.lk { color: var(--link); font-size: 13px; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 8px 12px; margin: 0; align-items: center; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.in { font-family: inherit; font-size: 13.5px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: min(520px, 100%); background: var(--card); color: var(--ink); }
.in.s { width: 240px; } .in.xs { width: 70px; }
.chk { display: inline-flex; align-items: center; gap: 6px; }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 6px 14px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.btn:disabled { opacity: .6; cursor: default; }
.sub { margin-top: 6px; }
.ok { color: var(--good); } .err { color: var(--bad); }
</style>
