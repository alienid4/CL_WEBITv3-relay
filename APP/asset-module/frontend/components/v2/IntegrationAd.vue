<script setup lang="ts">
// AD 人員名單（LDAPS 每日同步，2.068.0）：系統自己唯讀拉 AD 名單，取代每天人工匯出 CSV／中繼。
// 寫入跟 6 資料匯入 → AD 人員名單同一條（整份取代、少兩成整批不收）。只走 LDAPS 636、一律驗證憑證（沒有關閉驗證的選項）。
// 盤中靜默窗內按「測試連線／立即同步」由 useApi 統一跳二次確認。管理員專用（後端每支 API 都再擋）。
import { AD_RUN_STATUS, TRIGGER, type AdConfig, type AdRun } from '~/types/integrations'
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const cfg = ref<AdConfig | null>(null)
const form = reactive({ hosts: '', base_dn: '', ca_bundle: '', enabled: false, run_at: '07:00', credential: '' })
const runs = ref<AdRun[]>([])
const err = ref('')
const busy = ref<'' | 'save' | 'test' | 'run'>('')
const testResult = ref<{ ok: boolean; message: string; warning?: string } | null>(null)
const saved = ref('')
const dirty = computed(() => saved.value !== '' && JSON.stringify(form) !== saved.value)
const openRun = ref<AdRun | null>(null)
const hostsText = (hs: { host: string; cert_name: string }[]) => hs.map(h => (h.cert_name ? `${h.host} ${h.cert_name}` : h.host)).join('\n')

async function load() {
  err.value = ''
  try {
    const c = await apiFetch<AdConfig>('/api/integrations/ad')
    cfg.value = c
    Object.assign(form, { hosts: hostsText(c.hosts), base_dn: c.base_dn, ca_bundle: c.ca_bundle, enabled: c.enabled, run_at: c.run_at, credential: c.credential })
    saved.value = JSON.stringify(form)
    runs.value = (await apiFetch<{ runs: AdRun[] }>('/api/integrations/ad/runs')).runs
  } catch (e: any) { err.value = `AD 同步設定載入失敗：${errMsg(e)}` }
}
const creds = ref<{ name: string; username: string }[]>([])
async function loadCreds() {
  try { creds.value = (await apiFetch<{ name: string; kind: string; username: string }[]>('/api/credentials')).filter(c => c.kind === 'ldap') }
  catch (e: any) { creds.value = []; err.value = `收集憑證清單載入失敗：${errMsg(e)}` }
}
onMounted(() => { load(); loadCreds() })

async function save() {
  busy.value = 'save'
  try {
    await apiFetch('/api/integrations/ad', { method: 'PUT', body: { ...form } })
    showToast('已儲存設定', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
async function test() {
  busy.value = 'test'; testResult.value = null
  try { testResult.value = await apiFetch<{ ok: boolean; message: string; warning?: string }>('/api/integrations/ad/test', { method: 'POST' }) }
  catch (e: any) { testResult.value = { ok: false, message: errMsg(e) } } finally { busy.value = '' }
}
async function runNow() {
  busy.value = 'run'
  try {
    const r = await apiFetch<{ status: string; message: string }>('/api/integrations/ad/run', { method: 'POST' })
    showToast(r.message, r.status === 'ok' ? 'success' : r.status === 'refused' ? 'warn' : 'error', 8000)
    await load()
  } catch (e: any) { showToast(`同步沒有完成：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}

const LAMP_CLASS: Record<string, string> = { ok: 'ok', stale: 'stale', failed: 'bad', never: 'never', unset: 'never' }
const fr = computed(() => cfg.value?.freshness ?? null)
const src = computed(() => cfg.value?.list?.batch ?? null)
const rows = computed(() => runs.value.map(r => ({ ...r, st: AD_RUN_STATUS[r.status] ?? r.status, trig: TRIGGER[r.trigger] ?? r.trigger,
  mgr: r.mgr_ok == null ? null : `${r.mgr_ok}／${r.mgr_fail}／${r.mgr_dupname}` })))
const cols = [
  { k: 'started_at', label: '開始時間', mono: true }, { k: 'trig', label: '觸發' }, { k: 'st', label: '結果' }, { k: 'dc', label: 'DC', mono: true },
  { k: 'entries', label: 'AD 筆數', mono: true }, { k: 'rows', label: '寫入人數', mono: true }, { k: 'prev_total', label: '原人數', mono: true },
  { k: 'enabled_n', label: '啟用', mono: true }, { k: 'disabled_n', label: '停用', mono: true },
  { k: 'mgr', label: '主管 對上／對不上／同名', mono: true }, { k: 'started_by', label: '操作者' }, { k: 'message', label: '說明' },
]
function show(r: AdRun) { openRun.value = openRun.value?.id === r.id ? null : r }
</script>

<template>
  <div class="box">
    <div class="hd">
      <b class="t">AD 人員名單同步</b><span class="chip">未實機驗證</span>
      <InfoNote>每天從網域控制站唯讀讀一次全體人員帳號（LDAPS 636），整份取代「6 資料匯入 → AD 人員名單」；新名單比現有少超過兩成會整批不收、名單不動，要真的縮小請到資料匯入手動上傳。讀到的名單只寫進資料庫，不存檔、不寫 log。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <div v-if="fr" class="status">
      <span class="lamp" :class="LAMP_CLASS[fr.status]" data-test="lamp">● {{ fr.label }}</span>
      <span class="muted">上次成功 {{ fr.last_ok_at ?? '—' }}<template v-if="fr.age_hours != null">（{{ fr.age_hours }} 小時前）</template></span>
      <InfoNote>超過 {{ fr.stale_days }} 天沒有成功同步＝過期；從沒成功過＝沒收到；最近一次失敗或被拒收、或服務帳號被暫停使用＝失敗；DC、Base DN、CA、憑證缺一＝待設定。盤中延後不算失敗。</InfoNote>
      <NuxtLink v-if="cfg?.list" class="lk" to="/v2/settings?st=import&src=ad" data-test="list-link">目前名單 {{ cfg.list.total }} 人 ›</NuxtLink>
      <span class="muted" data-test="list-source">生效名單來源：{{ src ? `${src.file_name || '—'}・${src.imported_at || '—'}・${src.imported_by || '—'}` : '還沒有名單' }}</span>
    </div>
    <p v-if="fr?.blocked" class="err" data-test="blocked">{{ fr.blocked }}</p>

    <dl class="kv">
      <dt>網域控制站</dt><dd>
        <textarea v-model="form.hosts" class="in ta" rows="3" placeholder="10.99.0.1 dc01.corp.example&#10;10.99.0.2 dc02.corp.example" aria-label="網域控制站" />
        <InfoNote>一行一台，依序嘗試；連不上、憑證驗證失敗或查詢途中斷線才換下一台；帳密一旦送出，被拒或結果不明都不換台（避免把服務帳號鎖住）。用 IP 連時，同一行空一格寫上 DC 憑證上的主機名（必填），系統只認憑證上的這個名稱；不接受 *。</InfoNote>
      </dd>
      <dt>連接埠</dt><dd><span class="mono" data-test="port">636（LDAPS）</span> <InfoNote>只走 LDAPS 636，不提供 389 明文或 StartTLS。</InfoNote></dd>
      <dt>Base DN</dt><dd><input v-model="form.base_dn" class="in" placeholder="DC=corp,DC=example" aria-label="Base DN"></dd>
      <dt>收集憑證</dt><dd>
        <select v-model="form.credential" class="in s" aria-label="收集憑證">
          <option value="" disabled>（請選擇）</option>
          <option v-for="c in creds" :key="c.name" :value="c.name">{{ c.name }}（{{ c.username }}）</option>
        </select>
        <NuxtLink class="lk" to="/v2/settings?st=cred">到收集憑證設定帳密 ›</NuxtLink>
        <InfoNote>服務帳號在「系統設定 → 收集憑證」新增（類型選 ldap），帳號寫成 帳號@網域、網域\帳號 或完整 DN 都可以；密碼不可空白。這裡只挑用哪一筆，密碼加密存放，畫面與 API 都不會顯示。</InfoNote>
      </dd>
      <dt>CA 憑證檔</dt><dd><input v-model="form.ca_bundle" class="in" placeholder="伺服器上公司根 CA 的路徑（必填）" aria-label="CA 憑證檔"> <InfoNote>一律驗證 DC 的 TLS 憑證，本系統不提供關閉驗證，也不使用系統預設信任清單（避免有公開憑證的外部主機也通過）。請把公司根 CA 放到伺服器，在這裡填檔案路徑。</InfoNote></dd>
      <dt>每日同步</dt><dd><label class="chk"><input v-model="form.enabled" type="checkbox" aria-label="啟用每日同步"> 啟用，每天 <input v-model="form.run_at" type="time" class="in xs" aria-label="同步時間"></label>
        <InfoNote>時間不可落在盤中靜默時段（平日開盤時間）。失敗會隔 60 分鐘再試，一天最多 3 次；被「少兩成」擋下的不重試，要人看過。</InfoNote></dd>
    </dl>
    <div class="acts">
      <button type="button" class="btn" :disabled="!!busy" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存設定' }}</button>
      <button type="button" class="btn" :disabled="!!busy" @click="test">{{ busy === 'test' ? '測試中…' : '測試連線' }}</button>
      <button type="button" class="btn pri" :disabled="!!busy" @click="runNow">{{ busy === 'run' ? '同步中…' : '立即同步' }}</button>
      <InfoNote>測試連線只登入並抽查第一頁（最多 20 筆），不寫任何資料；用的是已儲存的設定。</InfoNote>
      <span v-if="dirty" class="warn" data-test="dirty">有未儲存的變更：測試連線與立即同步用的是已儲存的設定</span>
      <span v-if="testResult" :class="testResult.ok ? 'ok' : 'err'" data-test="test-result">{{ testResult.ok ? '連線成功：' : '連線失敗：' }}{{ testResult.message }}</span>
      <span v-if="testResult?.warning" class="warn" data-test="test-warning">⚠ {{ testResult.warning }}</span>
    </div>

    <b class="sub">同步紀錄</b>
    <V2Table :rows="rows" :cols="cols" prefix="R" empty="還沒有同步紀錄" />
    <div v-if="runs.length" class="pick"><span class="muted">看某一次的明細：</span>
      <button v-for="r in runs.slice(0, 8)" :key="r.id" type="button" class="mini" :class="{ on: openRun?.id === r.id }" @click="show(r)">R{{ r.id }} {{ r.started_at.slice(5, 16) }}（{{ AD_RUN_STATUS[r.status] ?? r.status }}）</button>
    </div>
    <div v-if="openRun" class="detail" data-test="run-detail">
      <b>R{{ openRun.id }} {{ openRun.started_at }}：{{ openRun.message || '—' }}</b>
      <ol v-if="openRun.detail?.errors?.length"><li v-for="(m, i) in openRun.detail.errors" :key="i">{{ m }}</li></ol>
      <span v-else class="muted">這次沒有 DC 錯誤。</span>
    </div>
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 12px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.t { font-size: 16px; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.status { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 14px; }
.lamp { font-size: 13px; font-weight: 700; }
.lamp.ok { color: var(--st-online); } .lamp.stale { color: var(--warn-text); } .lamp.bad { color: var(--bad); } .lamp.never { color: var(--st-unknown); }
.kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 8px 12px; margin: 0; align-items: center; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.in { font-family: inherit; font-size: 13.5px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: min(520px, 100%); background: var(--card); color: var(--ink); }
.in.ta { font-family: var(--mono, monospace); resize: vertical; }
.in.s { width: 240px; } .in.xs { width: 110px; }
.mono { font-family: var(--mono, monospace); }
.chk { display: inline-flex; align-items: center; gap: 6px; }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 6px 14px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.btn:disabled { opacity: .6; cursor: default; }
.lk { color: var(--brand); font-size: 13px; text-decoration: none; }
.sub { margin-top: 6px; }
.ok { color: var(--good); } .err { color: var(--bad); } .warn { color: var(--warn-text); }
.muted { color: var(--muted); font-size: 13px; }
.pick { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini.on { background: var(--mint); border-color: var(--brand); }
.detail { background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.detail ol { margin: 6px 0 0; padding-left: 20px; line-height: 1.7; }
</style>
