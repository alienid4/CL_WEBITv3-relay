<script setup lang="ts">
// 盤點作業（帳號盤點的「操作面」）。
//
// 刻意跟「帳號盤點」檢視頁分開：檢視頁是每天在看的稽核結果，該乾淨；
// 這裡是偶爾才動的設定/觸發/除錯——收集身分、立即盤點、排除主機、健檢 debug。
// 使用者 2026-07-23 拍板：操作全搬出檢視頁，連立即盤點也移過來。
interface Summary {
  has_data: boolean
  fail_high?: number; fail_medium?: number; fail_low?: number; unknown?: number
  accounts?: number; privileged?: number; humans?: number
  hosts_needing_root?: number
  failed_count?: number; host_count?: number; run_error?: string | null
  excluded?: string[]
  run?: { started_at: string; host_count: number; needs_root_count: number }
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const summary = ref<Summary | null>(null)
const thresholds = ref<Record<string, number>>({})

async function loadSummary() {
  try {
    const r = await apiFetch<{ summary: Summary; thresholds: any }>(
      '/api/accounts/findings')
    summary.value = r.summary
    thresholds.value = r.thresholds
  } catch { /* 拿不到就留空，不擋畫面 */ }
}
await loadSummary()

const collecting = ref(false)
async function collect() {
  if (collecting.value) return
  collecting.value = true
  showToast('開始盤點帳號，連線中…', 'info')
  try {
    const r = await apiFetch<any>('/api/accounts/collect', { method: 'POST' })
    if (r.candidates === 0) {
      showToast('沒有已納管的 Linux 主機可收', 'warn')
    } else {
      const msg = `完成：${r.candidates} 台、${r.accounts} 個帳號、${r.findings} 條稽核發現`
      showToast(r.needs_root_hosts ? `${msg}（${r.needs_root_hosts} 台權限不足只收到一半）` : msg,
        r.needs_root_hosts ? 'warn' : 'success')
    }
    await loadSummary()
    await loadHosts()
  } catch (e: any) {
    showToast(`盤點失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  } finally {
    collecting.value = false
  }
}

const sudoRules = ref('')
const showSudo = ref(false)
async function loadSudoRules() {
  showSudo.value = true
  if (sudoRules.value) return
  const r = await apiFetch<{ rules: string }>('/api/accounts/sudo-rules')
  sudoRules.value = r.rules
}

// 收集身分：webit3scan(唯讀) vs sysinfra(標準管理帳號，完整資料)
interface CollectConfig {
  account: string
  options: { value: string; label: string; note: string }[]
  pubkey: string
  provision_hint: string
  // 收集器自己的位址：會被寫進目標主機 authorized_keys 的 from= 來源限制。
  // 填錯不會報錯，只會讓金鑰永遠被拒、而納管照樣顯示成功——所以要看得到
  // 它現在是什麼、從哪來的（公司主機 2026-08-16 就是踩這個）。
  collector_ip: string
  collector_ip_source: string
  collector_ip_detected: string
}
const collectCfg = ref<CollectConfig | null>(null)
const showProvision = ref(false)
async function loadCollectCfg() {
  collectCfg.value = await apiFetch<CollectConfig>('/api/accounts/collect-config')
}
await loadCollectCfg()
const ipEdit = ref('')
const savingIp = ref(false)
async function saveCollectorIp() {
  if (savingIp.value || !collectCfg.value) return
  savingIp.value = true
  try {
    const r = await apiFetch<{ collector_ip: string }>('/api/accounts/collect-config', {
      method: 'PUT', body: { account: collectCfg.value.account, collector_ip: ipEdit.value },
    })
    await loadCollectCfg()
    showToast(`收集器位址已設為 ${r.collector_ip}`, 'success')
  } catch (e: any) {
    showToast(`設定失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  } finally {
    savingIp.value = false
  }
}

async function setCollectAccount(account: string) {
  try {
    await apiFetch('/api/accounts/collect-config', { method: 'PUT', body: { account } })
    if (collectCfg.value) collectCfg.value.account = account
    if (account !== 'webit3scan') showProvision.value = true
    showToast(`收集身分已設為 ${account}`, 'success')
  } catch (e: any) {
    showToast(`設定失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  }
}

// 可收集主機清單（含排除狀態）：給健檢選單與排除管理共用
interface CollHost { asset_serial: string; hostname: string | null; ip: string; excluded: boolean }
const collHosts = ref<CollHost[]>([])
async function loadHosts() {
  try {
    const r = await apiFetch<{ hosts: CollHost[] }>('/api/accounts/hosts')
    collHosts.value = r.hosts
  } catch { /* 拿不到就空，不擋畫面 */ }
}
await loadHosts()
const hosts = computed(() =>
  collHosts.value.map(h => ({ serial: h.asset_serial, label: `${h.hostname || h.ip}（${h.ip}）` })))

// 排除主機不納入稽核（非標準受管、天生收不全的機器）。透明、可還原。
const showExclude = ref(false)
async function toggleExclude(serial: string, exclude: boolean) {
  try {
    await apiFetch('/api/accounts/exclude', { method: 'PUT', body: { asset_serial: serial, exclude } })
    showToast(exclude ? '已排除，不再納入稽核' : '已納回稽核', 'success')
    await loadHosts()
    await loadSummary()
  } catch (e: any) {
    showToast(`操作失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  }
}

// 收集健檢（debug）
const probeSerial = ref('')
const probeResult = ref<any>(null)
const probing = ref(false)
const showProbe = ref(false)
async function runProbe() {
  if (!probeSerial.value || probing.value) return
  probing.value = true
  probeResult.value = null
  try {
    probeResult.value = await apiFetch('/api/accounts/diagnose',
      { query: { asset_serial: probeSerial.value } })
  } catch (e: any) {
    showToast(`健檢失敗：${e?.data?.detail || '未知錯誤'}`, 'error')
  } finally {
    probing.value = false
  }
}
async function copyProbe() {
  try {
    await navigator.clipboard.writeText(JSON.stringify(probeResult.value, null, 2))
    showToast('已複製（去識別化），可貼給開發者分析', 'success')
  } catch {
    showToast('複製失敗，請手動選取', 'warn')
  }
}
const VERDICT: Record<string, { t: string; c: string }> = {
  ok: { t: '正常', c: 'ok' }, empty: { t: '空', c: 'dim' },
  permission: { t: '權限被擋', c: 'bad' }, command_missing: { t: '指令不存在', c: 'warn' },
  unreachable: { t: '連不上', c: 'bad' }, error: { t: '錯誤', c: 'bad' },
}
</script>

<template>
  <div>
    <div class="section-divider">盤點作業</div>
    <p class="lead">
      帳號盤點的操作面：設定收集身分、觸發盤點、排除非標準主機、出問題時跑健檢。
      看稽核結果請到 <NuxtLink class="dl" to="/accounts">帳號盤點</NuxtLink>。
    </p>

    <div class="bar">
      <button class="btn primary" type="button" :disabled="collecting" @click="collect">
        {{ collecting ? '盤點中…' : '立即盤點' }}
      </button>
      <span v-if="summary?.run" class="when">
        上次盤點 · {{ summary.run.started_at }}（{{ summary.run.host_count }} 台）
      </span>
      <label v-if="collectCfg" class="acct-sel">
        收集身分
        <select :value="collectCfg.account" @change="setCollectAccount(($event.target as HTMLSelectElement).value)">
          <option v-for="o in collectCfg.options" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </label>
      <div class="spacer"></div>
      <span class="th-note">
        門檻：密碼 {{ thresholds.acct_pw_max_days }} 天、閒置 {{ thresholds.acct_idle_days }} 天
      </span>
    </div>

    <!-- 收集器自己的位址：納管腳本會把它寫進目標主機的 from= 來源限制 -->
    <div v-if="collectCfg" class="collector-ip">
      <div class="ci-head">
        <b>收集器位址</b>
        <code class="ci-val">{{ collectCfg.collector_ip }}</code>
        <span class="ci-src">（{{ collectCfg.collector_ip_source }}）</span>
      </div>
      <p class="pv-note">
        納管時會把它寫進目標主機 <code>authorized_keys</code> 的 <code>from=</code> 來源限制。
        <b>填錯不會有錯誤訊息</b>——金鑰會被目標主機拒絕，但納管仍顯示成功、之後永遠收不到。
        系統預設自動偵測（目前偵測到 <code>{{ collectCfg.collector_ip_detected }}</code>）；
        多網卡、走 NAT、或對外要用 DNS 名時才需要在這裡指定。
      </p>
      <div class="ci-edit">
        <input v-model="ipEdit" class="ci-in" :placeholder="collectCfg.collector_ip"
               :disabled="savingIp" @keyup.enter="saveCollectorIp" />
        <button class="link-btn" type="button" :disabled="savingIp" @click="saveCollectorIp">
          {{ savingIp ? '儲存中…' : '儲存' }}
        </button>
        <span class="pv-note">留空儲存＝清掉指定，退回自動偵測</span>
      </div>
    </div>

    <!-- 切成管理帳號時的一次性授權說明 -->
    <div v-if="collectCfg && collectCfg.account !== 'webit3scan'" class="provision">
      <div class="pv-head">
        <b>收集身分：{{ collectCfg.account }}</b>（標準管理帳號，可拿到需 root 的欄位）
        <button class="link-btn" type="button" @click="showProvision = !showProvision">
          {{ showProvision ? '收起' : '看授權步驟' }}
        </button>
      </div>
      <template v-if="showProvision">
        <p class="pv-note">{{ collectCfg.provision_hint }}</p>
        <p class="pv-note">把這把 webit3 收集公鑰加進各機的 <code>~sysinfra/.ssh/authorized_keys</code>：</p>
        <pre class="sudo">{{ collectCfg.pubkey }}</pre>
      </template>
    </div>

    <!-- 收集失敗：這些主機根本沒被收到，不能被 unknown=0 蓋掉 -->
    <div v-if="(summary?.failed_count || 0) > 0" class="gap fail">
      <b>✗ {{ summary?.failed_count }}/{{ summary?.host_count }} 台主機收集失敗（連不上或認證失敗）</b>
      這些主機這次<b>完全沒被收到</b>，稽核數字只反映收得到的那幾台。最常見原因：收集身分（{{ collectCfg?.account }}）的公鑰還沒授權進那些主機。
      用下方「🩺 收集健檢」挑一台失敗的跑一次，就會告訴你確切原因。
      <div v-if="summary?.run_error" class="mono fail-err">{{ summary.run_error }}</div>
      <button class="link-btn" type="button" @click="showExclude = !showExclude">
        非標準受管主機 → 管理排除清單
      </button>
    </div>

    <!-- 已排除主機：透明呈現 -->
    <div v-if="(summary?.excluded?.length || 0) > 0" class="excluded-note">
      已排除 <b>{{ summary?.excluded?.length }}</b> 台不納入稽核（非標準受管）：
      <span class="mono">{{ summary?.excluded?.join('、') }}</span>
      <button class="link-btn" type="button" @click="showExclude = !showExclude">管理</button>
    </div>

    <!-- 排除清單管理 -->
    <div v-if="showExclude" class="exclude-box">
      <p class="probe-lead">
        把<b>非標準受管</b>（沒佈標準管理帳號、天生收不全）的主機排除，不納入稽核、也不算失敗。
        排除會<b>清掉它上次收到的帳號資料</b>，隨時可納回。
      </p>
      <table class="tbl">
        <thead><tr><th>主機</th><th>IP</th><th>狀態</th><th></th></tr></thead>
        <tbody>
          <tr v-for="h in collHosts" :key="h.asset_serial">
            <td>{{ h.hostname || h.asset_serial }}</td>
            <td class="mono dim">{{ h.ip }}</td>
            <td><span :class="h.excluded ? 'vd dim' : 'vd ok'">{{ h.excluded ? '已排除' : '納入稽核' }}</span></td>
            <td class="right">
              <button class="btn small" type="button" @click="toggleExclude(h.asset_serial, !h.excluded)">
                {{ h.excluded ? '納回' : '排除' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 權限缺口：webit3scan 唯讀拿不到需 root 欄位時的解法 -->
    <div v-if="(summary?.hosts_needing_root || 0) > 0" class="gap">
      <b>⚠ {{ summary?.hosts_needing_root }} 台主機只收到一半</b>
      收集帳號 <code>webit3scan</code> 是唯讀非 root，拿不到密碼到期、空密碼、sudo 明細、
      authorized_keys——這些正是稽核最看重的欄位。
      <button class="link-btn" type="button" @click="loadSudoRules">看解法（sudo 白名單）</button>
      <pre v-if="showSudo" class="sudo">{{ sudoRules }}</pre>
      <p v-if="showSudo" class="sudo-note">
        存成 <code>/etc/sudoers.d/webit3scan-audit</code>（chmod 440，visudo -cf 驗證）。
        刻意<b>不含 <code>cat /etc/shadow</code></b>：<code>chage -l</code>／<code>passwd -S</code>
        拿得到同樣的稽核結論卻不吐密碼雜湊，能用小權限達成就不該要大的。
        這是擴權動作，要不要佈由你決定，系統不會自己動。
      </p>
    </div>

    <!-- 收集健檢（debug）：某台收不全時，挑它跑一次，去識別化結果可複製給開發者 -->
    <div class="probe-box">
      <button class="probe-toggle" type="button" @click="showProbe = !showProbe">
        🩺 收集健檢（debug）{{ showProbe ? '▲' : '▼' }}
      </button>
      <template v-if="showProbe">
        <p class="probe-lead">
          對一台主機即時跑每條收集指令，回報「為什麼收不到」（權限／指令缺／語系／連不上）。
          <b>不含任何原始輸出</b>，去識別化後可安全複製貼給開發者分析。
        </p>
        <div class="bar">
          <select v-model="probeSerial" class="sel">
            <option value="">選一台主機…</option>
            <option v-for="h in hosts" :key="h.serial" :value="h.serial">{{ h.label }}</option>
          </select>
          <button class="btn primary" type="button" :disabled="!probeSerial || probing" @click="runProbe">
            {{ probing ? '健檢中…' : '執行健檢' }}
          </button>
          <button v-if="probeResult" class="btn" type="button" @click="copyProbe">複製結果</button>
        </div>

        <div v-if="probeResult" class="probe-result">
          <div class="pr-head">
            <span :class="probeResult.reachable ? 'ok' : 'bad'">
              {{ probeResult.reachable ? `✓ 連得上（身分：${probeResult.identity}）` : '✗ 連不上' }}
            </span>
            <span v-if="probeResult.reachable" :class="probeResult.sudo_n ? 'ok' : 'bad'">
              sudo -n {{ probeResult.sudo_n ? '可用' : '不可用' }}
            </span>
            <span v-if="probeResult.os" class="dim">
              {{ probeResult.os.id }} {{ probeResult.os.version }}（{{ probeResult.os.family }}）
            </span>
            <span v-if="probeResult.locale" class="dim mono">{{ probeResult.locale }}</span>
          </div>
          <table v-if="probeResult.commands?.length" class="tbl">
            <thead><tr><th>指令</th><th>判定</th><th>rc</th><th>行數</th><th>非ASCII</th><th>stderr</th></tr></thead>
            <tbody>
              <tr v-for="c in probeResult.commands" :key="c.name">
                <td class="mono">{{ c.name }}</td>
                <td><span class="vd" :class="VERDICT[c.verdict]?.c">{{ VERDICT[c.verdict]?.t || c.verdict }}</span></td>
                <td class="mono dim">{{ c.rc }}</td>
                <td class="mono dim">{{ c.stdout_lines }}</td>
                <td>{{ c.has_non_ascii ? '⚠ 是' : '' }}</td>
                <td class="mono dim stderr">{{ c.stderr }}</td>
              </tr>
            </tbody>
          </table>
          <ul class="hints">
            <li v-for="(h, i) in probeResult.hints" :key="i">{{ h }}</li>
          </ul>
          <p class="dim resid">殘留掃描：{{ probeResult._residual_scan }}</p>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.collector-ip { border: 1px solid var(--border); background: var(--card); padding: 12px 14px; margin-bottom: 14px; }
.ci-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.ci-val { font-family: ui-monospace, Consolas, monospace; color: var(--brand); font-weight: 700; }
.ci-src { font-size: 11px; color: var(--muted); }
.ci-edit { display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.ci-in { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; padding: 6px 9px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); min-width: 190px; }

.lead { color: var(--muted); margin: 0 0 16px; line-height: 1.7; }
.dl { color: var(--brand, #009142); text-decoration: none; }
.dl:hover { text-decoration: underline; }

.bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.spacer { flex: 1; }
.btn { border-radius: 9px; border: 1px solid var(--border); background: transparent; color: inherit;
  padding: 7px 14px; cursor: pointer; font-size: 13px; font-family: inherit; }
.btn.primary { background: var(--brand, #009142); border-color: transparent; color: #04120e; font-weight: 600; }
.btn:disabled { opacity: .55; cursor: progress; }
.btn.small { padding: 3px 10px; font-size: 12px; }
.sel { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 9px; padding: 6px 10px; font-size: 13px; }
.when, .th-note { font-size: 12px; color: var(--muted); }
.right { text-align: right; }

.acct-sel { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
.acct-sel select { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 8px; padding: 5px 8px; font-size: 12px; font-family: inherit; }

.provision { background: rgba(120,150,220,.08); border: 1px solid rgba(120,150,220,.3);
  border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; font-size: 12px;
  color: var(--muted); line-height: 1.7; }
.provision b { color: #8ea6dd; }
.pv-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pv-note { margin: 6px 0 0; }
.provision code { background: rgba(15,23,42,.07); padding: 1px 5px; border-radius: 4px; }

.gap { background: rgba(230,170,60,.08); border: 1px solid rgba(230,170,60,.35);
  border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; font-size: 12px;
  color: var(--muted); line-height: 1.75; }
.gap b { color: #d9a441; }
.gap.fail { background: rgba(224,108,108,.08); border-color: rgba(224,108,108,.4); }
.gap.fail b { color: #e06c6c; }
.fail-err { margin-top: 6px; font-size: 11px; color: var(--muted); }
.gap code { background: rgba(15,23,42,.07); padding: 1px 5px; border-radius: 4px; }
.excluded-note { font-size: 12px; color: var(--muted); margin-bottom: 14px; padding: 8px 12px; border: 1px dashed var(--border); border-radius: 8px; }
.excluded-note b { color: var(--ink, inherit); }
.exclude-box { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; }
.link-btn { background: none; border: none; color: var(--brand, #009142); cursor: pointer;
  font-size: 12px; padding: 0 4px; font-family: inherit; text-decoration: underline; }
.sudo { background: rgba(0,0,0,.35); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; margin: 8px 0 4px; font-size: 11px; overflow-x: auto; white-space: pre; }
.sudo-note { margin: 0; font-size: 11px; line-height: 1.7; }

.probe-box { border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; margin-bottom: 16px; }
.probe-toggle { background: none; border: none; color: inherit; cursor: pointer; font-size: 13px; font-family: inherit; padding: 2px 0; }
.probe-lead { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 8px 0; }
.probe-lead b { color: #8ea6dd; }
.probe-result { margin-top: 10px; }
.pr-head { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; margin-bottom: 8px; align-items: center; }
.pr-head .ok { color: #009142; }
.pr-head .bad { color: #e06c6c; }
.vd { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.vd.ok { background: rgba(0,145,66,.16); color: #009142; }
.vd.bad { background: rgba(224,108,108,.16); color: #e06c6c; }
.vd.warn { background: rgba(230,170,60,.16); color: #d9a441; }
.vd.dim { color: var(--muted); }
.stderr { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hints { margin: 10px 0 0; padding-left: 18px; font-size: 12px; color: var(--muted); line-height: 1.8; }
.resid { font-size: 11px; margin-top: 8px; }

.tbl { width: 100%; border-collapse: collapse; }
.tbl th, .tbl td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; }
.mono { font-family: var(--mono, ui-monospace, monospace); }
.dim { color: var(--muted); }
</style>
