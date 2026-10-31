<script setup lang="ts">
// Aria Operations API 匯入（Q92）：取代「RVTools 資料夾監看」的建議來源。一個 Aria Operations 涵蓋所有 vCenter。
// 設定（網址、驗證來源、CA 憑證檔、帳密、排程）＋連線測試＋立即匯入＋執行紀錄。密碼只送出、不回顯；留空＝沿用已存的。
// 狀態：未實機驗證（回應格式依官方文件推定，要在公司網路實測）。
import { RUN_STATUS, TRIGGER, type VropsConfig, type VropsRun } from '~/types/integrations'
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const cfg = ref<VropsConfig | null>(null)
const form = reactive({ url: '', auth_source: '', ca_bundle: '', enabled: false, interval_hours: 24, credential: '' })
const runs = ref<VropsRun[]>([])
const err = ref('')
const busy = ref<'' | 'save' | 'test' | 'run'>('')
const testResult = ref<{ ok: boolean; message: string } | null>(null)
const openRun = ref<VropsRun | null>(null)

async function load() {
  err.value = ''
  try {
    const c = await apiFetch<VropsConfig>('/api/integrations/vrops')
    cfg.value = c
    Object.assign(form, { url: c.url, auth_source: c.auth_source, ca_bundle: c.ca_bundle, enabled: c.enabled, interval_hours: c.interval_hours, credential: c.credential })
    runs.value = (await apiFetch<{ runs: VropsRun[] }>('/api/integrations/vrops/runs')).runs
  } catch (e: any) { err.value = `Aria Operations 設定載入失敗：${errMsg(e)}` }
}
const creds = ref<{ name: string; username: string }[]>([])
async function loadCreds() {
  try { creds.value = (await apiFetch<{ name: string; kind: string; username: string }[]>('/api/credentials')).filter(c => c.kind === 'vrops') }
  catch (e: any) { creds.value = []; err.value = `收集憑證清單載入失敗：${errMsg(e)}` }
}
onMounted(() => { load(); loadCreds() })

async function save() {
  busy.value = 'save'
  try {
    await apiFetch('/api/integrations/vrops', { method: 'PUT', body: { ...form, interval_hours: Number(form.interval_hours) } })
    showToast('已儲存設定', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
async function test() {
  busy.value = 'test'; testResult.value = null
  try { testResult.value = await apiFetch<{ ok: boolean; message: string }>('/api/integrations/vrops/test', { method: 'POST' }) }
  catch (e: any) { testResult.value = { ok: false, message: errMsg(e) } } finally { busy.value = '' }
}
async function runNow() {
  busy.value = 'run'
  try {
    const r = await apiFetch<{ status: string; message: string }>('/api/integrations/vrops/run', { method: 'POST' })
    showToast(r.message, r.status === 'ok' ? 'success' : r.status === 'partial' ? 'warn' : 'error', 8000)
    await load()
  } catch (e: any) { showToast(`匯入沒有完成：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
const rows = computed(() => runs.value.map(r => ({ ...r, st: RUN_STATUS[r.status] ?? r.status, trig: TRIGGER[r.trigger] ?? r.trigger,
  newupd: r.inserted == null ? null : `${r.inserted}／${r.updated}／${r.pending_review}` })))
const cols = [
  { k: 'started_at', label: '開始時間', mono: true }, { k: 'trig', label: '觸發' }, { k: 'st', label: '結果' },
  { k: 'vms', label: 'VM', mono: true }, { k: 'hosts', label: '主機', mono: true }, { k: 'clusters', label: '叢集', mono: true }, { k: 'datastores', label: 'Datastore', mono: true },
  { k: 'newupd', label: '新增／更新／待確認', mono: true }, { k: 'error_count', label: '錯誤數', mono: true }, { k: 'started_by', label: '操作者' }, { k: 'message', label: '說明' },
]
function show(r: any) { openRun.value = runs.value.find(x => x.id === r.id) ?? null }
</script>

<template>
  <div class="box">
    <div class="hd">
      <b class="t">Aria Operations 匯入</b><span class="chip">未實機驗證</span><span class="chip rec">建議來源</span>
      <InfoNote>一個 Aria Operations API 涵蓋所有 vCenter，系統不直接連個別 vCenter、也不連 ESXi。匯入的內容與 RVTools 檔案匯入完全同一份格式，主機清單、儲存容量、影響範圍都不用改。舊的「RVTools 資料夾監看」仍保留可用；兩種來源請擇一，不要同時開。回應格式依官方文件推定，需在公司網路實測後才算驗證。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <dl class="kv">
      <dt>API 網址</dt><dd><input v-model="form.url" class="in" placeholder="https://aria.example.local" aria-label="API 網址"></dd>
      <dt>收集憑證</dt><dd>
        <select v-model="form.credential" class="in s" aria-label="收集憑證">
          <option value="">（自動：唯一一筆）</option>
          <option v-for="c in creds" :key="c.name" :value="c.name">{{ c.name }}（{{ c.username }}）</option>
        </select>
        <NuxtLink class="lk" to="/v2/settings?st=identity&sub=credentials">到收集憑證設定帳密 ›</NuxtLink>
        <InfoNote>Aria Operations 的帳號密碼統一在「系統設定 → 收集憑證」新增（類型選 vrops），這裡只挑用哪一筆。密碼加密存放，畫面與 API 都不會顯示。</InfoNote>
      </dd>
      <dt>驗證來源</dt><dd><input v-model="form.auth_source" class="in s" placeholder="例：LOCAL，留空＝預設" aria-label="驗證來源"> <InfoNote>Aria Operations 登入時的驗證來源名稱（本機帳號通常是 LOCAL；網域帳號填該來源在 Aria 裡的名稱）。留空就不送這個欄位。</InfoNote></dd>
      <dt>CA 憑證檔</dt><dd><input v-model="form.ca_bundle" class="in" placeholder="伺服器上的路徑，留空＝系統預設信任清單" aria-label="CA 憑證檔"> <InfoNote>一律驗證 TLS 憑證，沒有關閉驗證的選項。公司內部自簽憑證請把根憑證放到伺服器，在這裡填檔案路徑。</InfoNote></dd>
      <dt>排程匯入</dt><dd><label class="chk"><input v-model="form.enabled" type="checkbox"> 啟用，每 <input v-model="form.interval_hours" type="number" min="1" class="in xs" aria-label="間隔小時"> 小時一次</label></dd>
    </dl>
    <div class="acts">
      <button type="button" class="btn" :disabled="!!busy" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存設定' }}</button>
      <button type="button" class="btn" :disabled="!!busy" @click="test">{{ busy === 'test' ? '測試中…' : '連線測試' }}</button>
      <button type="button" class="btn pri" :disabled="!!busy" @click="runNow">{{ busy === 'run' ? '匯入中…（資料多時需要幾分鐘）' : '立即匯入' }}</button>
      <span v-if="testResult" :class="testResult.ok ? 'ok' : 'err'">{{ testResult.ok ? '連線成功：' : '連線失敗：' }}{{ testResult.message }}</span>
    </div>
    <b class="sub">匯入紀錄</b>
    <V2Table :rows="rows" :cols="cols" prefix="R" empty="還沒有匯入紀錄" />
    <div class="pick"><span class="muted">看某一次的錯誤明細：</span>
      <button v-for="r in runs.slice(0, 8)" :key="r.id" type="button" class="mini" :class="{ on: openRun?.id === r.id }" @click="show(r)">{{ r.started_at.slice(5, 16) }}（{{ RUN_STATUS[r.status] ?? r.status }}）</button>
    </div>
    <div v-if="openRun" class="detail">
      <b>{{ openRun.started_at }}：{{ openRun.message || '—' }}</b>
      <ol v-if="(openRun.detail?.errors?.length || 0) + (openRun.detail?.write_errors?.length || 0)">
        <li v-for="(m, i) in [...(openRun.detail?.errors ?? []), ...(openRun.detail?.write_errors ?? [])]" :key="i">{{ m }}</li>
      </ol>
      <span v-else class="muted">這次沒有錯誤。</span>
    </div>
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 12px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.t { font-size: 16px; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.chip.rec { background: var(--mint); color: var(--brand-dark); box-shadow: inset 0 0 0 1px var(--brand); }
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
.muted { color: var(--muted); font-size: 13px; }
.pick { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini.on { background: var(--mint); border-color: var(--brand); }
.detail { background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.detail ol { margin: 6px 0 0; padding-left: 20px; line-height: 1.7; }
</style>
