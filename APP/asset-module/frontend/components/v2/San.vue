<script setup lang="ts">
// 6.2 設備收集「SAN 交換器」頁籤（V52，2.048.12 起 V2 原生；取代嵌入的舊頁 pages/san.vue）。同一批 API，行為不變（⚠D2）：
// GET /api/san/switches、GET /api/san/switches/{ip}、GET /api/san/offline-guide、GET /api/system/collector-selfcheck；
// POST /api/san/collect、/api/san/import、/api/system/collector-selfheal（管理員；後端 ADMIN_WRITE）；
// 下線走共用 OfflineMarkModal（/api/assets/batch-status，一般帳號以上）、偵測存活走共用 AliveCheck（一般帳號以上）。
// A 版安全模式照舊：帳密當場輸入、只在記憶體、收完即清、不儲存（或選「收集憑證」裡 storage 類型的一筆）；只跑後端唯讀白名單指令。
// 網址：?tab=san&se=<prod|test|other>（環境篩選）、&sd=<switch IP>（WWPN 明細）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const { isAdmin, canWrite } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const items = ref<any[]>([])
const total = ref(0)
const done = ref(0)
const loading = ref(true)
const err = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const r = await apiFetch<any>('/api/san/switches')
    if (!isLatest()) return
    items.value = r.items ?? []; total.value = r.total; done.value = r.collected; err.value = ''
  } catch (e: any) { if (isLatest()) err.value = `SAN 清單載入失敗：${errMsg(e)}` } finally { if (isLatest()) loading.value = false }
}

// ---- 收集端環境自我檢查（paramiko 能不能用）；自我檢查本身失敗也要講出來，不能讓黃底消失看起來像好了 ----
const envchk = ref<any>(null)
async function loadEnvChk() {
  try { envchk.value = await apiFetch('/api/system/collector-selfcheck') }
  catch (e: any) { envchk.value = { ok: false, reason: `自我檢查本身失敗：${errMsg(e)}`, fix: '按下方「收集紀錄 → 下載分析檔」，整份傳給開發者' } }
}
const logRef = ref<{ reload: () => void } | null>(null)
const healing = ref(false)
async function healEnv() {
  healing.value = true
  try {
    const r = await apiFetch<any>('/api/system/collector-selfheal', { method: 'POST' })
    showToast(r.message, r.ok ? 'success' : 'warn', 15000)
    await loadEnvChk(); logRef.value?.reload()
  } catch (e: any) { showToast(`修復失敗：${errMsg(e)}`, 'error', 12000) } finally { healing.value = false }
}
onMounted(() => { load(); loadEnvChk() })

// ---- 環境篩選（網址 se）----
const ENVS = [{ k: '', label: '全部' }, { k: 'prod', label: '正式區' }, { k: 'test', label: '測試區' }, { k: 'other', label: '其他' }]
const se = computed(() => (['prod', 'test', 'other'].includes(String(route.query.se)) ? String(route.query.se) : ''))
const isTest = (e: string) => e.includes('測試') || e.toUpperCase().includes('UAT') || e.toUpperCase().includes('DEV')
const isProd = (e: string) => e.includes('正式')
const u = (extra: Record<string, string | undefined> = {}) => ({ path: '/v2/collect', query: Object.fromEntries(Object.entries({ tab: 'san', se: se.value, ...extra }).filter(([, v]) => v)) })

// ---- 憑證（當場打、不留存）＋收集 ----
const storageCreds = ref<any[]>([])
async function loadCreds() {
  try { storageCreds.value = (await apiFetch<any[]>('/api/credentials')).filter((c: any) => c.kind === 'storage') }
  catch (e) { storageCreds.value = []; console.warn('收集憑證清單載不到，只剩當場輸入（不擋收集）', e) }
}
onMounted(() => { if (isAdmin.value) loadCreds() })
const picked = ref<Record<string, boolean>>({})
const pickedIps = computed(() => Object.keys(picked.value).filter(k => picked.value[k]))
const credOpen = ref(false)
const cCred = ref('')
const cUser = ref('')
const cPass = ref('')
const cTargets = ref<string[]>([])
const running = ref(false)
const prog = ref({ done: 0, total: 0, cur: '' })
const rowStatus = ref<Record<string, string>>({})   // ip -> 'running' | 'ok:N' | 'err:msg'
function ask(ips: string[]) {
  if (!ips.length) { showToast('先勾選要收集的 switch', 'warn'); return }
  cTargets.value = ips; credOpen.value = true
}
function cancelCred() { credOpen.value = false; cPass.value = '' }
async function runCollect() {
  if (!cCred.value && (!cUser.value || !cPass.value)) { showToast('帳號、密碼都要填，或改選一組已設定的憑證', 'warn'); return }
  credOpen.value = false; running.value = true
  prog.value = { done: 0, total: cTargets.value.length, cur: '' }
  for (const ip of cTargets.value) {
    prog.value.cur = ip; rowStatus.value = { ...rowStatus.value, [ip]: 'running' }
    try {
      const r = await apiFetch<any>('/api/san/collect', { method: 'POST', body: cCred.value ? { ip, credential: cCred.value } : { ip, username: cUser.value, password: cPass.value } })
      rowStatus.value = { ...rowStatus.value, [ip]: `ok:${r.wwpn_count}` }
    } catch (e: any) { rowStatus.value = { ...rowStatus.value, [ip]: `err:${errMsg(e)}` } }
    prog.value.done++
  }
  cPass.value = ''; running.value = false      // 收完即清
  await load(); logRef.value?.reload(); showToast('收集完成', 'success')
}
// 只有「真的連不到」才建議防火牆／離線匯入；內部錯誤不亂猜
const isConnFail = (s: string) => /timed out|timeout|refused|no route|resolve|unreachable|connect to host|無法連線|連不到/i.test(s.slice(4))
const stText = (r: any) => {
  const s = rowStatus.value[r.ip] || ''
  if (s === 'running') return '收集中…'
  if (s.startsWith('ok')) return `剛收到 ${s.split(':')[1]} 筆`
  if (s.startsWith('err')) return '失敗'
  return r.collected ? '已收集' : '未收集'
}
const rows = computed(() => items.value.map((s: any, i: number) => ({ ...s, __no: i + 1 })).filter((s: any) => {
  const e = s.environment || ''
  if (se.value === 'prod') return isProd(e)
  if (se.value === 'test') return isTest(e)
  if (se.value === 'other') return !isProd(e) && !isTest(e)
  return true
}).map((s: any) => ({ ...s, st: stText(s) })))
const machine = (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)

// ---- 離線匯入（防火牆還沒開通時：照標準指令清單跑，把畫面貼進來或拖 PuTTY 紀錄檔）----
const impOpen = ref(false)
const impIp = ref('')
const impText = ref('')
const impBusy = ref(false)
const guide = ref<any>(null)
async function openImport() {
  impOpen.value = true
  if (!guide.value) {
    try { guide.value = await apiFetch('/api/san/offline-guide') }
    catch (e) { guide.value = null; console.warn('離線指引載不到，只收起指引區（不擋匯入）', e) }
  }
}
async function copyCmds() {
  const text = (guide.value?.commands ?? []).join('\n')
  try { await navigator.clipboard.writeText(text) } catch {
    // http 內網頁 clipboard API 可能被瀏覽器擋——退回傳統選取複製（不是吞錯：下面照樣提示）
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove()
  }
  showToast(`已複製 ${guide.value?.commands?.length ?? 0} 個指令，到 switch 上一次貼一行`, 'success')
}
async function runImport(force = false) {
  if (!impIp.value.trim() || !impText.value.trim()) { showToast('IP 和貼上的內容都要填', 'warn'); return }
  impBusy.value = true
  try {
    const r = await apiFetch<any>('/api/san/import', { method: 'POST', body: { ip: impIp.value.trim(), transcript: impText.value, force } })
    const miss = (r.missing_cmds || []) as string[]
    if (r.ip_check === 'unverified') showToast('提醒：這份畫面沒有 fabricshow，無法確認是不是這個 IP 的 switch', 'warn', 10000)
    const filled = (r.asset_serial_filled || []).length
    const conflicts = (r.asset_serial_conflicts || []) as any[]
    if (conflicts.length) showToast(`序號沒有寫進資產：資產上是 ${conflicts[0].asset_has}，switch 回報 ${conflicts[0].switch_says}——請確認哪個對`, 'warn', 15000)
    else if (filled) showToast(`已把 switch 回報的機箱序號寫進 ${filled} 筆資產的「設備序號」`, 'success', 8000)
    showToast(`匯入成功：${r.switch_name || impIp.value}，${r.wwpn_count} 筆 WWPN（認到 ${(r.imported_cmds || []).length} 個指令`
      + (miss.length ? `；缺 ${miss.join('、')}` : '，清單全部都有') + '）', miss.length ? 'warn' : 'success', 12000)
    logRef.value?.reload()
    impOpen.value = false; impText.value = ''; impIp.value = ''
    await load()
  } catch (e: any) {
    const status = e?.statusCode ?? e?.response?.status
    // IP 防呆：switch 自己回報的 IP 跟填的不一樣 → 問人，確認才強制匯入
    if (status === 409 && !force) {
      impBusy.value = false; logRef.value?.reload()
      if (window.confirm(`${errMsg(e)}\n\n按「確定」仍匯入到你填的 IP；按「取消」回去檢查。`)) return runImport(true)
      return
    }
    showToast(`匯入失敗：${errMsg(e)}`, 'error', 12000); logRef.value?.reload()
  } finally { impBusy.value = false }
}
const dragOver = ref(false)
const TEXT_EXT = ['.log', '.txt', '.out', '.text']
const MAX_MB = 10
async function readLogFile(f: File | null | undefined) {
  if (!f) return
  if (!TEXT_EXT.some(e => f.name.toLowerCase().endsWith(e))) { showToast(`只收純文字紀錄檔（${TEXT_EXT.join('／')}）——PuTTY 的 session log 就是這種`, 'warn', 9000); return }
  if (f.size > MAX_MB * 1024 * 1024) { showToast(`檔案 ${(f.size / 1048576).toFixed(1)} MB，超過 ${MAX_MB} MB。請只留這台 switch 那一段`, 'warn', 9000); return }
  try {
    impText.value = await f.text()          // 只在瀏覽器讀成文字填進框，不另外上傳檔案
    showToast(`已讀入 ${f.name}（${(f.size / 1024).toFixed(0)} KB）；確認 IP 後按「匯入判讀」`, 'success')
    const m = f.name.match(/(\d{1,3}(?:\.\d{1,3}){3})/)
    if (m && !impIp.value) impIp.value = m[1]
  } catch (e: any) { showToast(`讀檔失敗：${e?.message ?? '請改用複製貼上'}`, 'error') }
}
function onDrop(ev: DragEvent) { dragOver.value = false; readLogFile(ev.dataTransfer?.files?.[0]) }

// ---- 標記下線（共用 OfflineMarkModal）----
const offlineTarget = ref<{ serials: string[]; who: string; reason: string } | null>(null)
function askOffline(s: any, reason = '') {
  if (!s.asset_serial) { showToast('這台在資產庫裡查不到序號，無法改狀態', 'warn'); return }
  offlineTarget.value = { serials: [s.asset_serial], who: `${s.hostname || s.ip}（${s.ip}）`, reason }
}

// ---- WWPN 明細（網址 sd）----
const sd = computed(() => (typeof route.query.sd === 'string' ? route.query.sd : ''))
const det = ref<any>(null)
const detErr = ref('')
const beginDet = useLatest()
watch(sd, async (ip) => {
  const isLatest = beginDet()
  det.value = null; detErr.value = ''
  if (!ip) return
  try { const r = await apiFetch<any>(`/api/san/switches/${encodeURIComponent(ip)}`); if (isLatest()) det.value = r.data } catch (e: any) { if (isLatest()) detErr.value = `明細載入失敗：${errMsg(e)}` }
}, { immediate: true })
const detRows = computed(() => ((det.value?.rows ?? []) as any[]).map((r: any, i: number) => ({ ...r, __no: i + 1, port_text: r.port ? `${r.switch}/${r.port}` : '', zones_text: (r.zones || []).join('、') })))
</script>

<template>
  <div>
    <div v-if="envchk && !envchk.ok" class="envwarn" data-test="envwarn">
      ⚠ <b>這台收集端環境有問題，即時收集會失敗</b>：{{ envchk.reason }}
      <div v-if="envchk.broken_paths && envchk.broken_paths.length" class="fix">蓋住 paramiko 的空目錄：<code v-for="p in envchk.broken_paths" :key="p" class="badp">{{ p }}</code></div>
      <div class="fix">{{ envchk.fix }}</div>
      <div v-if="isAdmin" class="bar"><button type="button" class="btn" :disabled="healing" @click="healEnv">{{ healing ? '修復中…' : '一鍵修復' }}</button>
        <InfoNote>把蓋住 paramiko 的空目錄改名備份（不刪除），修完自動重驗。</InfoNote></div>
    </div>
    <p v-if="running" class="warn">收集中… {{ prog.done }}/{{ prog.total }}　目前：{{ prog.cur }}</p>

    <template v-if="sd">
      <div class="bar"><NuxtLink :to="u()">‹ 返回</NuxtLink><b>{{ det?.switch || sd }}</b>
        <span v-if="det" class="muted">cfg：{{ det.zoning_cfg || '—' }}・{{ detRows.length }} 筆 WWPN</span></div>
      <p v-if="detErr" class="err">{{ detErr }}</p>
      <p v-else-if="!det" class="muted">載入中…</p>
      <V2Table v-else :rows="detRows" prefix="W" row-key="wwpn" unit="筆" :max="200" :export-name="`SAN_${sd}_WWPN`" empty="沒有 WWPN"
               :cols="[{ k: 'wwpn', label: 'WWPN', mono: true }, { k: 'alias', label: '別名' }, { k: 'port_text', label: 'switch/port' }, { k: 'zones_text', label: 'zones' }]" />
    </template>
    <template v-else>
      <div class="bar">
        <span>已收集 <b class="mono">{{ done }}</b>／共 <b class="mono">{{ total }}</b> 台</span>
        <InfoNote>帳號密碼當場輸入、只在記憶體、收完即清、不會存起來；系統只跑唯讀指令（switchshow／fabricshow／nscamshow／cfgactvshow／alishow），
          不會改 switch 任何設定。可勾一批、用同一組帳密一次收。防火牆還沒開通時用「離線匯入」：照同一份指令清單在 switch 上跑，把畫面貼進來或拖 PuTTY 紀錄檔。</InfoNote>
        <NuxtLink v-for="e in ENVS" :key="e.k" :to="u({ se: e.k || undefined })" class="chip" :class="{ on: se === e.k }">{{ e.label }}</NuxtLink>
        <template v-if="isAdmin">
          <button type="button" class="btn2" :disabled="running" @click="openImport">離線匯入</button>
          <button type="button" class="btn" :disabled="running || !pickedIps.length" @click="ask(pickedIps)">收集選取（{{ pickedIps.length }}）</button>
        </template>
        <span v-else class="muted" data-test="admin-only">收集、離線匯入限管理員</span>
      </div>
      <p v-if="err" class="err" data-test="err">{{ err }}</p>
      <p v-if="loading && !items.length && !err" class="muted">載入中…</p>
      <V2Table v-if="items.length || (!loading && !err)" v-model:selected="picked" :selectable="isAdmin" :rows="rows" prefix="S" row-key="ip" unit="台" export-name="SAN 收集" empty="這個範圍沒有 SAN switch"
               :cols="[{ k: 'hostname', label: '主機名', link: machine }, { k: 'ip', label: 'IP', mono: true, link: machine }, { k: 'device_model', label: '型號' },
                       { k: 'physical_location', label: '機房' }, { k: 'environment', label: '環境' }, { k: 'st', label: '狀態' },
                       { k: 'wwpn_count', label: 'WWPN', mono: true, link: (r: any) => (r.collected && r.wwpn_count != null ? u({ sd: r.ip }) : null) },
                       { k: 'collected_at', label: '上次收集', mono: true }, { k: 'act', label: '操作' }]">
        <template #cell-st="{ row }">
          <span class="pill" :class="row.st === '收集中…' ? 'run' : row.st.startsWith('剛收到') ? 'ok' : row.st === '失敗' ? 'bad' : row.collected ? 'done' : 'none'">{{ row.st }}</span>
          <template v-if="row.st === '失敗'">
            <div class="failmsg">{{ rowStatus[row.ip].slice(4) }}</div>
            <div class="failhint">{{ isConnFail(rowStatus[row.ip]) ? '連不到多半是防火牆還沒開通 → 可改用「離線匯入」貼畫面' : '這是系統端／連線協商的錯誤（不是防火牆）。把完整訊息貼給開發者。' }}</div>
          </template>
        </template>
        <template #cell-act="{ row }">
          <span class="acts">
            <AliveCheck v-if="canWrite" :ip="row.ip" :can-offline="!!row.asset_serial" @offline="(r: string) => askOffline(row, r)" />
            <button v-if="canWrite" type="button" class="lb" :disabled="!row.asset_serial" title="標記成停用／報廢／閒置（原因必填，會記進 CIA 待異動）" @click="askOffline(row)">下線</button>
            <button v-if="isAdmin" type="button" class="lb" :disabled="running" @click="ask([row.ip])">收集</button>
            <NuxtLink v-if="row.collected" :to="u({ sd: row.ip })" class="lb">明細</NuxtLink>
          </span>
        </template>
      </V2Table>
    </template>

    <CollectLog ref="logRef" :kinds="['san_collect', 'san_import', 'selfcheck', 'selfheal']" :start-open="true" />

    <div v-if="credOpen" class="mask" @click="cancelCred" />
    <div v-if="credOpen" class="modal" role="dialog" aria-label="登入 SAN switch">
      <h3 class="h3">登入 SAN switch（{{ cTargets.length }} 台）</h3>
      <p class="muted">帳密只用這一次、收完即清，不會存起來；這 {{ cTargets.length }} 台用同一組帳密依序收集。</p>
      <label>憑證
        <select v-model="cCred" class="in" aria-label="憑證">
          <option value="">當場輸入（不留存）</option>
          <option v-for="c in storageCreds" :key="c.name" :value="c.name">{{ c.name }}（{{ c.username }}）</option>
        </select></label>
      <label>帳號<input v-model="cUser" class="in" autocomplete="off" :disabled="!!cCred"></label>
      <label>密碼<input v-model="cPass" class="in" type="password" autocomplete="off" :disabled="!!cCred" @keyup.enter="runCollect"></label>
      <div class="bar"><button type="button" class="btn" @click="runCollect">開始收集</button><button type="button" class="btn2" @click="cancelCred">取消</button></div>
    </div>

    <div v-if="impOpen" class="mask" @click="impOpen = false" />
    <div v-if="impOpen" class="modal imp" role="dialog" aria-label="離線匯入 SAN switch">
      <h3 class="h3">離線匯入 SAN switch</h3>
      <ol v-if="guide" class="steps"><li v-for="(st, i) in guide.steps" :key="i">{{ st }}</li></ol>
      <div class="bar"><b>要跑的唯讀指令（{{ guide?.commands?.length ?? 0 }} 個，一次貼一行）</b>
        <button type="button" class="btn2" :disabled="!guide" @click="copyCmds">複製全部指令</button></div>
      <pre class="cmds">{{ (guide?.commands ?? []).join('\n') }}</pre>
      <p v-if="guide" class="muted">至少要有：<code>{{ guide.min_required.join(' / ') }}</code></p>
      <label>這台 switch 的管理 IP<input v-model="impIp" class="in" placeholder="例如 10.99.0.10"></label>
      <label>貼上整段畫面，或把 PuTTY 紀錄檔拖進來
        <div class="drop" :class="{ over: dragOver }" @dragover.prevent="dragOver = true" @dragleave.prevent="dragOver = false" @drop.prevent="onDrop">
          <textarea v-model="impText" class="in ta" rows="10" aria-label="貼上畫面" />
        </div></label>
      <p class="muted">檔案不會被上傳——瀏覽器讀成文字填進上面的框。
        <label class="pick">或選擇檔案<input type="file" accept=".log,.txt,.out,.text,text/plain" @change="readLogFile(($event.target as HTMLInputElement).files?.[0])"></label></p>
      <div class="bar"><button type="button" class="btn" :disabled="impBusy" @click="runImport()">{{ impBusy ? '匯入中…' : '匯入判讀' }}</button>
        <button type="button" class="btn2" @click="impOpen = false">取消</button></div>
    </div>

    <OfflineMarkModal v-if="offlineTarget" :serials="offlineTarget.serials" :who="offlineTarget.who" :default-reason="offlineTarget.reason"
                      @close="offlineTarget = null" @done="offlineTarget = null; load()" />
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 0 0 6px; font-size: 15px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.in { padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); font: inherit; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled, .lb:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0 6px 0 0; text-decoration: none; }
.acts { display: inline-flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.chip { border: 1px solid var(--border); border-radius: 999px; padding: 3px 12px; color: var(--ink-soft); text-decoration: none; font-size: 12px; }
.chip.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); font-weight: 600; }
.pill { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.pill.done { background: var(--mint); color: var(--brand-dark); }
.pill.none { background: var(--sub); color: var(--muted); }
.pill.run { background: var(--warn-soft); color: var(--warn-text); }
.pill.ok { background: var(--good-soft); color: var(--brand-dark); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
.failmsg { margin-top: 4px; color: var(--bad); font-size: 11.5px; max-width: 320px; white-space: normal; word-break: break-word; }
.failhint { margin-top: 2px; color: var(--ink-soft); font-size: 11px; max-width: 320px; white-space: normal; }
.envwarn { margin: 0 0 12px; padding: 12px 14px; border-radius: 10px; background: var(--warn-soft); border: 1px solid var(--warn); color: var(--warn-text); font-size: 13px; line-height: 1.6; }
.envwarn .fix { margin-top: 6px; color: var(--ink-soft); }
.badp { display: inline-block; margin-right: 6px; padding: 1px 6px; border-radius: 4px; background: var(--warn-soft); }
.warn { color: var(--warn-text); font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 39; }
.modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 360px; max-width: 94vw; max-height: 90vh; overflow: auto; background: var(--card); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); z-index: 40; padding: 18px 20px; }
.modal.imp { width: 720px; }
.modal label { display: block; font-size: 12px; color: var(--muted); margin-top: 10px; }
.modal .in { width: 100%; margin-top: 3px; }
.ta { font-family: ui-monospace, Consolas, monospace; font-size: 12px; resize: vertical; }
.drop.over .ta { outline: 2px dashed var(--brand); outline-offset: 2px; }
.steps { margin: 6px 0 8px 18px; padding: 0; font-size: 12px; line-height: 1.7; }
.cmds { margin: 6px 0; padding: 8px 10px; font-size: 12px; line-height: 1.6; max-height: 200px; overflow: auto; background: var(--sub); border-radius: 6px; }
.pick { display: inline; text-decoration: underline; cursor: pointer; }
.pick input { display: none; }
code { background: var(--sub); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }
</style>
