<script setup lang="ts">
// Aria Operations API 匯入（Q92）：取代「RVTools 資料夾監看」的建議來源。一個 Aria Operations 涵蓋所有 vCenter。
// 設定（網址、驗證來源、CA 憑證檔、帳密、排程）＋連線測試＋立即匯入＋執行紀錄。密碼只送出、不回顯；留空＝沿用已存的。
// 狀態：未實機驗證（回應格式依官方文件推定，要在公司網路實測）。
import { onBeforeUnmount } from 'vue'
import { RUN_STATUS, TRIGGER, type VropsConfig, type VropsDcStatus, type VropsRun } from '~/types/integrations'
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const cfg = ref<VropsConfig | null>(null)
const form = reactive({ url: '', auth_source: '', ca_bundle: '', enabled: false, interval_hours: 24, credential: '' })
const runs = ref<VropsRun[]>([])
const err = ref('')
const runsErr = ref('')
const busy = ref<'' | 'save' | 'test' | 'run'>('')
const testResult = ref<{ ok: boolean; message: string } | null>(null)
const openRun = ref<VropsRun | null>(null)

async function load() {
  err.value = ''
  try {
    const c = await apiFetch<VropsConfig>('/api/integrations/vrops')
    cfg.value = c
    Object.assign(form, { url: c.url, auth_source: c.auth_source, ca_bundle: c.ca_bundle, enabled: c.enabled, interval_hours: c.interval_hours, credential: c.credential })
    runsErr.value = ''
    try { runs.value = (await apiFetch<{ runs: VropsRun[] }>('/api/integrations/vrops/runs')).runs } catch (e2: any) { runs.value = []; runsErr.value = errMsg(e2) }
  } catch (e: any) { err.value = `Aria Operations 設定載入失敗：${errMsg(e)}` }
}
const creds = ref<{ name: string; username: string }[]>([])
async function loadCreds() {
  try { creds.value = (await apiFetch<{ name: string; kind: string; username: string }[]>('/api/credentials')).filter(c => c.kind === 'vrops') }
  catch (e: any) { creds.value = []; err.value = `收集憑證清單載入失敗：${errMsg(e)}` }
}
onMounted(() => { load(); loadCreds(); loadDc() })

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
// 三層：最新一批有沒有進來＝操作層（預設可見）；紀錄表與錯誤明細＝診斷層（點了才展開）
// ===== 每日 VMware 快照（系統直抓，供 1.5 每日檢查）：開關＋立即抓一次＋最近一次狀態；原因與批次號在收合區 =====
const dc = ref<VropsDcStatus | null>(null)
const dcOn = ref(false)
const dcBusy = ref(false)
const dcErr = ref('')
let dcTimer: ReturnType<typeof setTimeout> | null = null
const DC_STATUS: Record<string, string> = { ok: '成功', incomplete: '不完整', failed: '失敗', deferred: '延後（盤中靜默）', busy: '進行中' }
let dcFails = 0
async function loadDc(polling = false) {
  try {
    const r = await apiFetch<VropsDcStatus>('/api/integrations/vrops/dailycheck')
    dc.value = r && typeof r === 'object' && !Array.isArray(r) ? r : null
    dcOn.value = !!dc.value?.enabled
    dcErr.value = ''; dcFails = 0
    if (dc.value?.running) dcPoll()
  } catch (e: any) {
    // 輪詢中遇到暫時性錯誤：保留上一份狀態、有限次退避重試，不永久停止；非輪詢（第一次載入）才直接顯示讀取失敗
    if (polling && dcFails < 5) { dcFails++; dcPoll(); return }
    dc.value = null; dcErr.value = errMsg(e)
  }
}
function dcPoll() {
  if (dcTimer) return
  dcTimer = setTimeout(async () => { dcTimer = null; await loadDc(true) }, 5000 * (1 + dcFails))
}
onBeforeUnmount(() => { if (dcTimer) clearTimeout(dcTimer) })
async function saveDc() {
  dcBusy.value = true
  try {
    dc.value = await apiFetch<VropsDcStatus>('/api/integrations/vrops/dailycheck', { method: 'PUT', body: { enabled: dcOn.value } })
    showToast(dcOn.value ? '已開啟每日自動抓取' : '已關閉每日自動抓取', 'success')
  } catch (e: any) { dcOn.value = !dcOn.value; showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { dcBusy.value = false }
}
async function runDc() {
  dcBusy.value = true
  try {
    const r = await apiFetch<{ status: string; message: string }>('/api/integrations/vrops/dailycheck/run', { method: 'POST' })
    showToast(r.message || DC_STATUS[r.status] || r.status, r.status === 'ok' || r.status === 'started' ? 'success' : r.status === 'incomplete' ? 'warn' : 'error', 8000)
    await loadDc()
    if (r.status === 'started') dcPoll()   // 執行緒才剛開始，立刻讀到的狀態可能還沒標 running：保證會輪詢到完成
  } catch (e: any) { showToast(`抓取沒有完成：${errMsg(e)}`, 'error') } finally { dcBusy.value = false }
}
const dcLast = computed(() => (dc.value?.last && (dc.value.last as any).status ? dc.value.last : null))
const dcSrc = (s: string | null | undefined) => (s === 'direct' ? '系統直抓' : s === 'relay' ? '中繼批次' : '—')
const dcBad = computed(() => !dc.value || !dc.value.today?.complete)
const histOpen = ref(false)
const latest = computed(() => [...runs.value].sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)))[0] ?? null)
function show(r: any) { openRun.value = runs.value.find(x => x.id === r.id) ?? null }
</script>

<template>
  <div class="box">
    <div class="hd">
      <b class="t">Aria Operations 匯入</b><span class="chip rec">建議來源</span>
      <InfoNote>一個 Aria Operations API 涵蓋所有 vCenter，系統不直接連個別 vCenter、也不連 ESXi。匯入的內容與 RVTools 檔案匯入完全同一份格式，主機清單、儲存容量、影響範圍都不用改。舊的「RVTools 資料夾監看」仍保留可用；兩種來源請擇一，不要同時開。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <dl class="kv">
      <dt>API 網址</dt><dd><input v-model="form.url" class="in" placeholder="https://aria.example.local" aria-label="API 網址"></dd>
      <dt>收集憑證</dt><dd>
        <select v-model="form.credential" class="in s" aria-label="收集憑證">
          <option value="">（自動：唯一一筆）</option>
          <option v-for="c in creds" :key="c.name" :value="c.name">{{ c.name }}（{{ c.username }}）</option>
        </select>
        <NuxtLink class="lk" to="/v2/settings?st=cred">到收集憑證設定帳密 ›</NuxtLink>
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
    <p class="stat" :class="{ bad: !latest || latest.status !== 'ok' }" data-test="vrops-latest">
      最近一次匯入：<template v-if="runsErr">匯入紀錄讀取失敗（{{ runsErr }}）</template><template v-else-if="latest">{{ RUN_STATUS[latest.status] ?? latest.status }}　{{ latest.started_at }}　VM {{ latest.vms ?? '—' }}／主機 {{ latest.hosts ?? '—' }}<template v-if="latest.error_count">　錯誤 {{ latest.error_count }}</template></template><template v-else>沒收到（還沒有匯入紀錄）</template>
    </p>
    <div class="dc" data-test="vrops-dc">
      <div class="acts">
        <b>每日 VMware 快照</b>
        <InfoNote>供 1.5 每日檢查的 VMware 列使用。系統在工作日台北時間 06:00～08:00 自己讀 Aria Operations（最晚 07:40 開始，每 20 分鐘試一次，窗內已有完整批次就不再讀；過了 07:40 不再啟動，請走跳板機備援），結果走跟跳板機腳本同一條匯入與完整性檢查。只做讀取，不連 ESXi／vCenter。跳板機腳本保留當備援，開啟後要停掉跳板機排程。關閉開關會中止進行中的抓取；開關關著時不能按「立即抓一次」。開啟前需先設好上面的網址與收集憑證。</InfoNote>
        <label class="chk"><input v-model="dcOn" type="checkbox" :disabled="!isAdmin || dcBusy" aria-label="每日 VMware 自動抓取" @change="saveDc"> 啟用每日自動抓取</label>
        <button v-if="isAdmin" type="button" class="btn" :disabled="dcBusy || !!busy || !!dc?.running || !dc?.enabled" data-test="vrops-dc-run" @click="runDc">{{ dc?.running ? '抓取中…' : '立即抓一次' }}</button>
      </div>
      <p class="stat" :class="{ bad: dcBad }" data-test="vrops-dc-latest">
        最近一次每日快照：<template v-if="dcErr">狀態讀取失敗（{{ dcErr }}）</template><template v-else-if="dcLast">{{ DC_STATUS[dcLast.status ?? ''] ?? dcLast.status }}　{{ dcLast.at }}</template><template v-else>沒收到（還沒有抓取紀錄）</template>　今天：{{ dc?.today?.complete ? `已收到完整批次（${dcSrc(dc.today.source)}）` : '沒收到' }}<template v-if="dc?.window?.used_default">　時間窗用預設 {{ dc.window.from }}～{{ dc.window.cutoff }}</template><template v-if="dc?.gave_up_today">　今天不會再自動重試</template><template v-else-if="dc?.next_try">　下次可試 {{ String(dc.next_try).slice(11, 16) }}</template><template v-if="dc?.auth_rejects_today">　登入被拒 {{ dc.auth_rejects_today }}／{{ dc.auth_limit }} 次</template>
      </p>
      <details v-if="dcLast || dc?.last_ok" class="adv" data-test="vrops-dc-adv">
        <summary>每日快照的原因與批次（診斷）</summary>
        <p v-if="dcLast?.message" class="muted">{{ dcLast.message }}</p>
        <ol v-if="dcLast?.problems?.length"><li v-for="(p, i) in dcLast.problems" :key="i">{{ p }}</li></ol>
        <p v-if="dc?.gave_up_today" class="muted" data-test="vrops-dc-gaveup">{{ dc.gave_up_today.message }}</p>
        <ul v-if="dc?.audit?.length" class="muted"><li v-for="a in dc.audit" :key="a.id">{{ a.at }}　{{ a.by || '—' }}：{{ a.key }} {{ a.before }} → {{ a.after }}</li></ul>
        <p class="muted">最後一次系統直抓成功：{{ dc?.last_ok?.collected_at ?? '沒有' }}<template v-if="dc?.last_ok">（{{ dc.last_ok.batch_id }}）</template>　呼叫 {{ dcLast?.calls ?? '—' }} 次、{{ dcLast?.elapsed_s ?? '—' }} 秒</p>
      </details>
    </div>
    <details class="adv" :open="histOpen" data-test="vrops-hist" @toggle="histOpen = ($event.target as HTMLDetailsElement).open">
      <summary>匯入紀錄與錯誤明細（診斷，{{ runs.length }} 筆）</summary>
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
    </details>
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
.stat { margin: 0; font-size: 13px; padding: 6px 12px; border-radius: 10px; border: 1px solid var(--border); background: var(--sub); color: var(--ink-soft); }
.stat.bad { border-color: var(--warn); background: var(--warn-soft); color: var(--warn-text); }
.dc { display: flex; flex-direction: column; gap: 8px; }
.dc ol { margin: 4px 0 0; padding-left: 20px; line-height: 1.7; }
.adv { font-size: 13px; }
.adv > summary { cursor: pointer; color: var(--brand-dark); padding: 4px 0; }
.ok { color: var(--good); } .err { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; }
.pick { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini.on { background: var(--mint); border-color: var(--brand); }
.detail { background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.detail ol { margin: 6px 0 0; padding-left: 20px; line-height: 1.7; }
</style>
