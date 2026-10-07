<script setup lang="ts">
// HMC（IBM Power／AIX／IBM i 管理主控台）收集頁。2026-09-13。
// 同 SAN 的 A 版：帳密當場打、只在記憶體、收完即清、不儲存；只跑唯讀查詢指令。
// ⚠️ parser 依 HMC 官方格式寫、尚未用實機驗過，第一次收可能要對格式微調。
definePageMeta({ ssr: false })
const { apiFetch } = useApi()
const { showToast } = useToast()

interface Con {
  hostname: string; ip: string; device_model?: string; physical_location?: string
  collected_at: string | null; system_count: number | null; lpar_count: number | null
  hmc_version: string | null; collected: boolean
}

const items = ref<Con[]>([])
const total = ref(0)
const done = ref(0)
const loading = ref(true)
const manualIp = ref('')

async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ items: Con[]; total: number; collected: number }>('/api/hmc/consoles')
    items.value = r.items; total.value = r.total; done.value = r.collected
  } catch (e: any) {
    showToast(`清單載入失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
  } finally { loading.value = false }
}
onMounted(load)

// ---- 憑證（當場打、不留存）＋收集 ----
const credOpen = ref(false)
const cUser = ref(''); const cPass = ref(''); const cTargets = ref<string[]>([])
const running = ref(false); const prog = ref({ done: 0, total: 0, cur: '' })
const rowStatus = ref<Record<string, string>>({})

function askSingle(ip: string) { cTargets.value = [ip]; credOpen.value = true }
function askManual() {
  const ip = manualIp.value.trim()
  if (!ip) { showToast('先輸入 HMC 的 IP', 'warn'); return }
  cTargets.value = [ip]; credOpen.value = true
}
async function runCollect() {
  if (!cUser.value || !cPass.value) { showToast('帳號、密碼都要填', 'warn'); return }
  credOpen.value = false; running.value = true
  prog.value = { done: 0, total: cTargets.value.length, cur: '' }
  for (const ip of cTargets.value) {
    prog.value.cur = ip; rowStatus.value = { ...rowStatus.value, [ip]: 'running' }
    try {
      const r = await apiFetch<any>('/api/hmc/collect', {
        method: 'POST', body: { ip, username: cUser.value, password: cPass.value },
      })
      rowStatus.value = { ...rowStatus.value, [ip]: `ok:${r.system_count}/${r.lpar_count}` }
    } catch (e: any) {
      rowStatus.value = { ...rowStatus.value, [ip]: 'err:' + (e?.data?.detail ?? e?.message ?? '失敗') }
    }
    prog.value.done++
  }
  cPass.value = ''; running.value = false; manualIp.value = ''
  await load(); showToast('收集完成', 'success')
}
function cancelCred() { credOpen.value = false; cPass.value = '' }

// ---- 明細 ----
const detOpen = ref(false); const detIp = ref(''); const detData = ref<any>(null)
async function openDetail(ip: string) {
  detIp.value = ip; detOpen.value = true; detData.value = null
  try { detData.value = (await apiFetch<any>(`/api/hmc/consoles/${encodeURIComponent(ip)}`)).data }
  catch (e: any) { showToast(`明細載入失敗：${e?.data?.detail ?? e?.message}`, 'error'); detOpen.value = false }
}
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>HMC 收集（IBM Power）</h1>
      <InfoNote>收 HMC 底下的 managed system（frame）與 LPAR：序號、韌體、CPU/記憶體、OS。帳號密碼<b>當場輸入、只在記憶體、收完即清、不儲存</b>；只跑唯讀查詢指令（lssyscfg／lshwres／lshmc），<b>不改 HMC 任何設定</b>。<br>⚠️ 這支 parser 依官方格式撰寫、尚未用實機驗過，第一次收若欄位對不上跟我說，我依實際輸出調。</InfoNote>
    </div>

    <div class="manual">
      HMC 常沒登記在資產庫——可直接輸入 IP 收集：
      <input v-model="manualIp" class="in" placeholder="HMC 管理 IP" @keyup.enter="askManual">
      <button class="btn primary" :disabled="running" @click="askManual">收集此 IP</button>
    </div>

    <p v-if="loading" class="dim">載入中…</p>
    <template v-else>
      <div class="prog">已收集 <b>{{ done }}</b> ／ 清單 <b>{{ total }}</b> 台（含手動收過的）</div>
      <p v-if="running" class="runbar">收集中… {{ prog.done }}/{{ prog.total }}　目前：{{ prog.cur }}</p>

      <table class="dtable">
        <thead><tr>
          <th class="txt">主機名</th><th class="txt">IP</th><th class="txt">型號</th>
          <th class="txt">狀態</th><th class="num">系統</th><th class="num">LPAR</th>
          <th class="txt">上次收集</th><th class="txt">操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="c in items" :key="c.ip">
            <td class="txt rowh">{{ c.hostname || '—' }}</td>
            <td class="txt mono">{{ c.ip }}</td>
            <td class="txt">{{ c.device_model || '—' }}</td>
            <td class="txt">
              <span v-if="rowStatus[c.ip] === 'running'" class="pill run">收集中…</span>
              <span v-else-if="(rowStatus[c.ip] || '').startsWith('ok')" class="pill ok">✓ 剛收到 {{ rowStatus[c.ip].split(':')[1] }}</span>
              <span v-else-if="(rowStatus[c.ip] || '').startsWith('err')" class="pill bad" :title="rowStatus[c.ip].slice(4)">✕ 失敗</span>
              <span v-else-if="c.collected" class="pill done">已收集</span>
              <span v-else class="pill none">未收集</span>
            </td>
            <td class="num">{{ c.system_count ?? '—' }}</td>
            <td class="num">{{ c.lpar_count ?? '—' }}</td>
            <td class="txt mono dim">{{ c.collected_at || '—' }}</td>
            <td class="txt">
              <button class="mini" :disabled="running" @click="askSingle(c.ip)">收集</button>
              <button v-if="c.collected" class="mini" @click="openDetail(c.ip)">明細</button>
            </td>
          </tr>
          <tr v-if="items.length === 0"><td colspan="8" class="dim">資產庫沒有登記 HMC——用上面手動輸入 IP 收集。</td></tr>
        </tbody>
      </table>
    </template>

    <!-- 憑證 -->
    <div v-if="credOpen" class="mask" @click="cancelCred" />
    <div v-if="credOpen" class="modal cred">
      <h3>登入 HMC（{{ cTargets.join('、') }}）</h3>
      <p class="dim sm">帳密只用這一次、收完即清，不會存起來。</p>
      <label>帳號<input v-model="cUser" class="in" autocomplete="off"></label>
      <label>密碼<input v-model="cPass" class="in" type="password" autocomplete="off" @keyup.enter="runCollect"></label>
      <div class="acts">
        <button class="btn primary" @click="runCollect">開始收集</button>
        <button class="btn" @click="cancelCred">取消</button>
      </div>
    </div>

    <!-- 明細 -->
    <div v-if="detOpen" class="mask" @click="detOpen = false" />
    <div v-if="detOpen" class="modal det">
      <div class="dhd">
        <b>{{ detIp }}</b>
        <span class="dim">　HMC {{ detData?.hmc_version || '?' }}　·　{{ detData?.system_count || 0 }} 系統／{{ detData?.lpar_count || 0 }} LPAR</span>
        <button class="mini" @click="detOpen = false">關閉</button>
      </div>
      <div class="dwrap">
        <div v-for="sys in (detData?.systems || [])" :key="sys.name" class="sysblk">
          <div class="sysh"><b>{{ sys.name }}</b>　<span class="dim">{{ sys.type_model }}　SN {{ sys.serial || '—' }}　FW {{ sys.firmware || '—' }}　{{ sys.state }}</span></div>
          <table class="dtable">
            <thead><tr><th class="txt">LPAR</th><th class="txt">ID</th><th class="txt">類型</th><th class="txt">狀態</th><th class="txt">OS</th><th class="num">CPU</th><th class="num">RAM(MB)</th></tr></thead>
            <tbody>
              <tr v-for="lp in sys.lpars" :key="lp.lpar_id + lp.name">
                <td class="txt rowh">{{ lp.name }}</td>
                <td class="txt">{{ lp.lpar_id }}</td>
                <td class="txt">{{ lp.env }}</td>
                <td class="txt">{{ lp.state }}</td>
                <td class="txt">{{ lp.os_version || '—' }}</td>
                <td class="num">{{ lp.proc_units ?? '—' }}</td>
                <td class="num">{{ lp.mem_mb ?? '—' }}</td>
              </tr>
              <tr v-if="!sys.lpars || sys.lpars.length === 0"><td colspan="7" class="dim">無 LPAR</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 18px 22px 60px; }
.hd { display: flex; align-items: center; gap: 8px; }
h1 { font-size: 19px; margin: 0; }
.manual { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 14px 0;
  font-size: 13px; color: var(--ink-soft); background: var(--sub); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 10px 14px; }
.prog { font-size: 14px; color: var(--ink-soft); margin: 6px 0 10px; }
.prog b { color: var(--brand-dark); font-family: var(--disp); }
.runbar { font-size: 13px; color: var(--warn-text); margin: 0 0 10px; }
.btn { padding: 6px 14px; border-radius: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.mini { margin-right: 6px; padding: 3px 10px; font-size: 12px; border-radius: 6px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .5; }
.pill { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.pill.done { background: var(--mint); color: var(--brand-dark); }
.pill.none { background: var(--sub); color: var(--muted); }
.pill.run { background: var(--warn-soft); color: var(--warn-text); }
.pill.ok { background: var(--good-soft); color: var(--brand-dark); }
.pill.bad { background: var(--bad-soft); color: var(--bad); cursor: help; }
.dim { color: var(--ink-soft); } .sm { font-size: 12px; }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 39; }
.modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%,-50%);
  background: var(--card); border: 1px solid var(--border-strong); border-radius: var(--radius);
  box-shadow: 0 12px 40px rgba(0,0,0,.25); z-index: 40; padding: 18px 20px; }
.modal.cred { width: 360px; }
.modal.cred h3 { margin: 0 0 6px; font-size: 15px; }
.modal.cred label { display: block; font-size: 12px; color: var(--muted); margin-top: 10px; }
.in { padding: 7px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: #fff; color: var(--ink); }
.modal.cred .in { width: 100%; margin-top: 3px; }
.manual .in { min-width: 200px; }
.acts { display: flex; gap: 8px; margin-top: 16px; }
.modal.det { width: min(960px, 94vw); max-height: 82vh; display: flex; flex-direction: column; padding: 0; }
.dhd { padding: 11px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }
.dhd .mini { margin-left: auto; }
.dwrap { overflow: auto; padding: 12px 16px 16px; }
.sysblk { margin-bottom: 18px; }
.sysh { font-size: 13px; margin-bottom: 6px; }
</style>
