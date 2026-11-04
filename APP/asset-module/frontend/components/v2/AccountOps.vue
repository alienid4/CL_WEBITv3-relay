<script setup lang="ts">
// 7.2 帳號盤點「收集身分」頁籤（V14，2.048.14 起 V2 原生；取代嵌入的舊頁 pages/account-ops.vue）。同一批 API，行為不變（⚠D2）：
// GET /api/accounts/findings、/api/accounts/hosts、/api/accounts/sudo-rules（一般帳號以上）、/api/accounts/collect-config（管理員讀）；
// POST /api/accounts/collect、PUT /api/accounts/collect-config、/api/accounts/exclude（管理員；後端 ADMIN_WRITE）；
// GET /api/accounts/diagnose（收集健檢：對一台即時跑每條收集指令，回去識別化結果；後端登入即可、有 manual_diag 靜默窗閘門）。
// 網址：?tab=identity&ax=1（展開排除清單）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const { copy } = useClipboard()
const route = useRoute()
const { isAdmin, canWrite } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const summary = ref<any>(null)
const thresholds = ref<Record<string, number>>({})
const sumErr = ref('')
async function loadSummary() {
  try { const r = await apiFetch<any>('/api/accounts/findings'); summary.value = r.summary; thresholds.value = r.thresholds ?? {}; sumErr.value = '' }
  catch (e: any) { sumErr.value = `盤點摘要載入失敗：${errMsg(e)}` }
}
const collHosts = ref<any[] | null>(null)
const hostErr = ref('')
async function loadHosts() {
  try { collHosts.value = (await apiFetch<any>('/api/accounts/hosts')).hosts ?? []; hostErr.value = '' }
  catch (e: any) { hostErr.value = `可收集主機清單載入失敗：${errMsg(e)}` }
}
// 收集身分設定是管理員才能讀（後端 ADMIN_READ）；一般帳號不去打，畫面直說限管理員
const cfg = ref<any>(null)
const cfgErr = ref('')
async function loadCfg() {
  try { cfg.value = await apiFetch<any>('/api/accounts/collect-config'); cfgErr.value = '' } catch (e: any) { cfgErr.value = `收集身分設定載入失敗：${errMsg(e)}` }
}
// 盤點摘要與主機清單是一般帳號以上能讀（後端 USER_READ）；只能看的帳號不去打，畫面直說
onMounted(() => { if (canWrite.value) { loadSummary(); loadHosts() } if (isAdmin.value) loadCfg() })

// ---- 立即盤點 ----
const collecting = ref(false)
async function collect() {
  if (collecting.value) return
  collecting.value = true
  showToast('開始盤點帳號，連線中…', 'info')
  try {
    const r = await apiFetch<any>('/api/accounts/collect', { method: 'POST' })
    if (r.candidates === 0) showToast('沒有已納管的 Linux 主機可收', 'warn')
    else {
      const m = `完成：${r.candidates} 台、${r.accounts} 個帳號、${r.findings} 條稽核發現`
      showToast(r.needs_root_hosts ? `${m}（${r.needs_root_hosts} 台權限不足只收到一半）` : m, r.needs_root_hosts ? 'warn' : 'success')
    }
    await loadSummary(); await loadHosts()
  } catch (e: any) { showToast(`盤點失敗：${errMsg(e)}`, 'error') } finally { collecting.value = false }
}

// ---- 收集身分、收集器位址 ----
// 我們自己佈的唯讀收集帳號（新名＋舊名）：選它們不需要另外授權收集公鑰
const COLLECTOR_NAMES = ['webit3sc', 'webit3scan']
const showProvision = ref(false)
async function setAccount(account: string) {
  try {
    await apiFetch('/api/accounts/collect-config', { method: 'PUT', body: { account } })
    if (cfg.value) cfg.value.account = account
    if (!COLLECTOR_NAMES.includes(account)) showProvision.value = true
    showToast(`收集身分已設為 ${account}`, 'success')
  } catch (e: any) { showToast(`設定失敗：${errMsg(e)}`, 'error') }
}
const ipEdit = ref('')
const savingIp = ref(false)
async function saveIp() {
  if (savingIp.value || !cfg.value) return
  savingIp.value = true
  try {
    const r = await apiFetch<any>('/api/accounts/collect-config', { method: 'PUT', body: { account: cfg.value.account, collector_ip: ipEdit.value } })
    await loadCfg(); showToast(`收集器位址已設為 ${r.collector_ip}`, 'success')
  } catch (e: any) { showToast(`設定失敗：${errMsg(e)}`, 'error') } finally { savingIp.value = false }
}

// ---- 排除主機（非標準受管、天生收不全）；網址 ax=1 展開 ----
const ax = computed(() => route.query.ax === '1')
const u = (extra: Record<string, string | undefined> = {}) => ({ path: '/v2/inventory/account', query: Object.fromEntries(Object.entries({ tab: 'identity', ...extra }).filter(([, v]) => v)) })
const exRows = computed(() => (collHosts.value ?? []).map((h: any, i: number) => ({ ...h, __no: i + 1, host: h.hostname || h.asset_serial, st: h.excluded ? '已排除' : '納入稽核' })))
async function toggleExclude(serial: string, exclude: boolean) {
  try {
    await apiFetch('/api/accounts/exclude', { method: 'PUT', body: { asset_serial: serial, exclude } })
    showToast(exclude ? '已排除，不再納入稽核' : '已納回稽核', 'success')
    await loadHosts(); await loadSummary()
  } catch (e: any) { showToast(`操作失敗：${errMsg(e)}`, 'error') }
}

// ---- 權限缺口：sudo 白名單解法 ----
const sudoRules = ref('')
const showSudo = ref(false)
const sudoErr = ref('')
async function loadSudo() {
  showSudo.value = true
  if (sudoRules.value) return
  try { sudoRules.value = (await apiFetch<any>('/api/accounts/sudo-rules')).rules; sudoErr.value = '' } catch (e: any) { sudoErr.value = `sudo 白名單載入失敗：${errMsg(e)}` }
}

// ---- 收集健檢（debug）----
const hostOpts = computed(() => (collHosts.value ?? []).map((h: any) => ({ serial: h.asset_serial, label: `${h.hostname || h.ip}（${h.ip}）` })))
const probeSerial = ref('')
const probe = ref<any>(null)
const probing = ref(false)
async function runProbe() {
  if (!probeSerial.value || probing.value) return
  probing.value = true; probe.value = null
  try { probe.value = await apiFetch('/api/accounts/diagnose', { query: { asset_serial: probeSerial.value } }) }
  catch (e: any) { showToast(`健檢失敗：${errMsg(e)}`, 'error') } finally { probing.value = false }
}
async function copyProbe() {
  try {
    const ok = await copy(JSON.stringify(probe.value, null, 2))
    if (!ok) { showToast('複製失敗，請直接選取內容複製', 'warn', 8000); return }
    showToast('已複製（去識別化），可貼給開發者分析', 'success')
  } catch (e) { console.warn(e); showToast('複製失敗，請手動選取', 'warn') }
}
const VERDICT: Record<string, string> = { ok: '正常', empty: '空', permission: '權限被擋', command_missing: '指令不存在', unreachable: '連不上', error: '錯誤' }
const cmdRows = computed(() => (probe.value?.commands ?? []).map((c: any, i: number) => ({ ...c, __no: i + 1, vd: VERDICT[c.verdict] || c.verdict, na: c.has_non_ascii ? '是' : '' })))
</script>

<template>
  <div>
    <div class="bar">
      <button v-if="isAdmin" type="button" class="btn" :disabled="collecting" @click="collect">{{ collecting ? '盤點中…' : '立即盤點' }}</button>
      <span v-if="summary?.run" class="muted">上次盤點 {{ summary.run.started_at }}（{{ summary.run.host_count }} 台）</span>
      <label v-if="cfg" class="lbl">收集身分
        <select :value="cfg.account" class="in" aria-label="收集身分" @change="setAccount(($event.target as HTMLSelectElement).value)">
          <option v-for="o in cfg.options" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select></label>
      <span v-if="!isAdmin" class="muted" data-test="admin-only">立即盤點、收集身分、排除主機限管理員</span>
      <span class="sp" />
      <span v-if="thresholds.acct_pw_max_days != null" class="muted">門檻：密碼 {{ thresholds.acct_pw_max_days }} 天、閒置 {{ thresholds.acct_idle_days }} 天</span>
      <InfoNote>帳號盤點的操作面：設定收集身分、觸發盤點、排除非標準主機、出問題時跑健檢。看稽核結果在本頁其他頁籤。</InfoNote>
    </div>
    <p v-if="!canWrite" class="muted" data-test="viewer">盤點摘要、排除清單與收集健檢限一般帳號以上</p>
    <p v-if="sumErr" class="err">{{ sumErr }}</p>
    <p v-if="cfgErr" class="err">{{ cfgErr }}</p>

    <div v-if="cfg" class="box">
      <div class="bar">
        <b>收集器位址</b><code class="val">{{ cfg.collector_ip }}</code><span class="muted">（{{ cfg.collector_ip_source }}）</span>
        <InfoNote>系統自動偵測，一般不用改；多網卡／NAT／對外用 DNS 名才需指定。納管時會把它寫進目標主機 authorized_keys 的 from= 來源限制。
          填錯不會有錯誤訊息——金鑰會被目標主機拒絕，但納管仍顯示成功、之後永遠收不到。目前偵測到 {{ cfg.collector_ip_detected }}。留空儲存＝清掉指定，退回自動偵測。</InfoNote>
        <input v-model="ipEdit" class="in" :placeholder="cfg.collector_ip" aria-label="收集器位址" :disabled="savingIp" @keyup.enter="saveIp">
        <button type="button" class="btn2" :disabled="savingIp" @click="saveIp">{{ savingIp ? '儲存中…' : '儲存' }}</button>
      </div>
      <div class="bar"><b>帳號備註</b><code class="val">{{ cfg.account_comment }}</code>
        <NuxtLink :to="{ path: '/v2/settings', query: { st: 'identity', sub: 'credentials' } }">到系統設定修改</NuxtLink></div>
      <div v-if="!COLLECTOR_NAMES.includes(cfg.account)" class="bar">
        <b>收集身分：{{ cfg.account }}</b><span class="muted">（標準管理帳號，可拿到需 root 的欄位）</span>
        <button type="button" class="lb" @click="showProvision = !showProvision">{{ showProvision ? '收起' : '看授權步驟' }}</button>
      </div>
      <template v-if="showProvision && !COLLECTOR_NAMES.includes(cfg.account)">
        <p class="muted">{{ cfg.provision_hint }}</p>
        <p class="muted">把這把 webit3 收集公鑰加進各機的 <code>~sysinfra/.ssh/authorized_keys</code>：</p>
        <pre class="pre">{{ cfg.pubkey }}</pre>
      </template>
    </div>

    <div v-if="(summary?.failed_count || 0) > 0" class="gap bad" data-test="failed">
      <b>{{ summary.failed_count }}/{{ summary.host_count }} 台主機收集失敗（連不上或認證失敗）</b>
      <InfoNote>這些主機這次完全沒被收到，稽核數字只反映收得到的那幾台。最常見原因：收集身分的公鑰還沒授權進那些主機。用下方「收集健檢」挑一台失敗的跑一次，就會告訴你確切原因。</InfoNote>
      <div v-if="summary.run_error" class="mono">{{ summary.run_error }}</div>
    </div>
    <div class="bar">
      <span v-if="(summary?.excluded?.length || 0) > 0">已排除 <b class="mono">{{ summary.excluded.length }}</b> 台不納入稽核：<span class="mono">{{ summary.excluded.join('、') }}</span></span>
      <NuxtLink :to="u({ ax: ax ? undefined : '1' })">{{ ax ? '收起排除清單' : '管理排除清單' }}</NuxtLink>
    </div>
    <template v-if="ax">
      <p class="muted">排除會清掉它上次收到的帳號資料，隨時可納回。<InfoNote>把非標準受管（沒佈標準管理帳號、天生收不全）的主機排除，不納入稽核、也不算失敗。</InfoNote></p>
      <p v-if="hostErr" class="err">{{ hostErr }}</p>
      <p v-else-if="!collHosts" class="muted">載入中…</p>
      <V2Table v-else :rows="exRows" prefix="X" row-key="asset_serial" unit="台" :max="200" export-name="帳號盤點_排除清單" empty="沒有可收集的主機"
               :cols="[{ k: 'host', label: '主機', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'ip', label: 'IP', mono: true },
                       { k: 'st', label: '狀態', tone: (r: any) => (r.excluded ? 'none' : 'dr') }, { k: 'act', label: '操作' }]">
        <template #cell-act="{ row }">
          <button v-if="isAdmin" type="button" class="lb" @click="toggleExclude(row.asset_serial, !row.excluded)">{{ row.excluded ? '納回' : '排除' }}</button>
          <span v-else class="muted">—</span>
        </template>
      </V2Table>
    </template>

    <div v-if="(summary?.hosts_needing_root || 0) > 0" class="gap" data-test="needroot">
      <b>{{ summary.hosts_needing_root }} 台主機只收到一半</b>
      <InfoNote>收集帳號 webit3sc 是唯讀非 root，拿不到密碼到期、空密碼、sudo 明細、authorized_keys——這些正是稽核最看重的欄位。
        不要手動貼 sudo 白名單——重跑一次「納管腳本」就會佈好：它會先把現況備份到 /var/backups/webit3/&lt;時間&gt;/ 並產生 restore.sh（執行就還原成納管前），
        再放一支內容固定、只有 root 能改、不接受任何參數的唯讀輔助程式，sudo 只准「不帶參數」執行它。為什麼不直接開 chage -l *／cat /home/*/.ssh/authorized_keys：
        sudoers 的 * 會吃空白，那種規則能被拿去順便讀 /etc/shadow。輔助程式只給密碼狀態碼、效期日期、sudoers 內容、金鑰數量，不吐密碼雜湊也不吐金鑰內容。
        這是擴權動作，要不要佈由你決定，系統不會自己動。</InfoNote>
      <button type="button" class="lb" @click="loadSudo">看解法（sudo 白名單）</button>
      <p v-if="sudoErr" class="err">{{ sudoErr }}</p>
      <pre v-else-if="showSudo" class="pre">{{ sudoRules }}</pre>
    </div>

    <h3 class="h3">收集健檢（debug）</h3>
    <div class="bar">
      <select v-model="probeSerial" class="in" aria-label="健檢主機">
        <option value="">選一台主機…</option>
        <option v-for="h in hostOpts" :key="h.serial" :value="h.serial">{{ h.label }}</option>
      </select>
      <button type="button" class="btn" :disabled="!probeSerial || probing" @click="runProbe">{{ probing ? '健檢中…' : '執行健檢' }}</button>
      <button v-if="probe" type="button" class="btn2" @click="copyProbe">複製結果</button>
      <InfoNote>對一台主機即時跑每條收集指令，回報「為什麼收不到」（權限／指令缺／語系／連不上）。不含任何原始輸出，去識別化後可安全複製貼給開發者分析。</InfoNote>
    </div>
    <template v-if="probe">
      <div class="bar">
        <span :class="probe.reachable ? 'ok' : 'err'">{{ probe.reachable ? `連得上（身分：${probe.identity}）` : '連不上' }}</span>
        <span v-if="probe.reachable" :class="probe.sudo_n ? 'ok' : 'err'">sudo -n {{ probe.sudo_n ? '可用' : '不可用' }}</span>
        <span v-if="probe.os" class="muted">{{ probe.os.id }} {{ probe.os.version }}（{{ probe.os.family }}）</span>
        <span v-if="probe.locale" class="muted mono">{{ probe.locale }}</span>
      </div>
      <V2Table v-if="probe.commands?.length" :rows="cmdRows" prefix="C" row-key="name" unit="條" export-name="帳號收集健檢"
               :cols="[{ k: 'name', label: '指令', mono: true }, { k: 'vd', label: '判定', tone: (r: any) => (r.verdict === 'ok' ? 'dr' : r.verdict === 'empty' || r.verdict === 'command_missing' ? 'none' : 'test') },
                       { k: 'rc', label: 'rc', mono: true }, { k: 'stdout_lines', label: '行數', mono: true }, { k: 'na', label: '非ASCII' }, { k: 'stderr', label: 'stderr', mono: true }]" />
      <ul class="hints"><li v-for="(h, i) in probe.hints" :key="i">{{ h }}</li></ul>
      <p class="muted">殘留掃描：{{ probe._residual_scan }}</p>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.sp { flex: 1; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.box { border: 1px solid var(--border); background: var(--card); border-radius: var(--radius); padding: 10px 14px; margin-bottom: 12px; }
.val { font-family: var(--disp); color: var(--brand-dark); font-weight: 700; }
.lbl { display: inline-flex; gap: 6px; align-items: center; }
.in { padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); font: inherit; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0 6px 0 0; }
.gap { margin: 0 0 12px; padding: 10px 14px; border-radius: 10px; background: var(--warn-soft); border: 1px solid var(--warn); color: var(--warn-text); font-size: 13px; }
.gap.bad { background: var(--bad-soft); border-color: var(--bad); color: var(--bad); }
.pre { background: var(--sub); padding: 10px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.hints { font-size: 13px; margin: 6px 0 6px 18px; padding: 0; }
.ok { color: var(--good); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
code { background: var(--sub); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
</style>
