<script setup lang="ts">
// SAN switch（Brocade）收集頁。2026-09-13 機房搬遷盤點用。
// A 版：帳密當場打、只在記憶體、收完清掉、**不儲存憑證**；只跑後端唯讀白名單指令。
// 目的：看得出「哪些收過、哪些沒、上次何時收」，測試區可勾一批一次收（同一組帳密）。
definePageMeta({ ssr: false })
const { apiFetch } = useApi()
const { showToast } = useToast()

interface Sw {
  asset_serial: string; hostname: string; ip: string; device_model: string; asset_status?: string | null
  asset_name: string; physical_location: string; environment: string; os: string
  collected_at: string | null; wwpn_count: number | null; zone_count: number | null
  port_count: number | null; switch_name: string | null; collected: boolean
}

const items = ref<Sw[]>([])
const total = ref(0)
const done = ref(0)
const loading = ref(true)
const envFilter = ref<'all' | 'prod' | 'test' | 'other'>('all')

// 收集端環境自我檢查（paramiko 能不能真的用）——壞就在畫面黃底講清楚，不用去跑指令
const envchk = ref<{ ok: boolean; reason?: string; fix?: string; paramiko_version?: string
  paramiko_file?: string; cwd?: string; broken_paths?: string[] } | null>(null)
async function loadEnvChk() {
  try {
    envchk.value = await apiFetch('/api/system/collector-selfcheck')
  } catch (e: any) {
    // ⚠️ 以前這裡是 envchk = null → 黃底消失，看起來像「好了」。
    // 2026-09-15 公司機就是黃底不見、收集照樣失敗。自我檢查本身失敗也要講出來。
    envchk.value = {
      ok: false,
      reason: '自我檢查本身失敗：' + (e?.data?.detail ?? e?.message ?? '原因不明'),
      fix: '按下方「收集紀錄 → 下載分析檔」，整份傳給開發者',
    }
  }
}

// 收集紀錄：共用元件 CollectLog（見 components/CollectLog.vue）
const logRef = ref<{ reload: () => void } | null>(null)

// 一鍵修復：把蓋住 paramiko 的空目錄改名備份（不刪除），修完重驗。
// 為什麼放畫面上：patch 只掃兩個固定位置，空目錄不在那就永遠修不好，
// 每查一次要重出一包 patch 太慢（使用者 9/14：不想再手動貼指令來回）。
const healing = ref(false)
async function healEnv() {
  healing.value = true
  try {
    const r = await apiFetch<{ ok: boolean; message: string }>(
      '/api/system/collector-selfheal', { method: 'POST' })
    showToast(r.message, r.ok ? 'success' : 'warn', 15000)
    await loadEnvChk()
    logRef.value?.reload()
  } catch (e: any) {
    showToast(`修復失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
  } finally {
    healing.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ items: Sw[]; total: number; collected: number }>('/api/san/switches')
    items.value = r.items
    total.value = r.total
    done.value = r.collected
  } catch (e: any) {
    showToast(`清單載入失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
  } finally {
    loading.value = false
  }
}
onMounted(() => { load(); loadEnvChk() })

// 只有「真的連不到」才建議防火牆/離線匯入；內部錯誤（SSHException/協商失敗/程式錯）不亂猜
function isConnFail(status: string) {
  const m = (status || '').slice(4)
  return /timed out|timeout|refused|no route|resolve|unreachable|connect to host|無法連線|連不到/i.test(m)
}
function isTest(e: string) { return e.includes('測試') || e.toUpperCase().includes('UAT') || e.toUpperCase().includes('DEV') }
function isProd(e: string) { return e.includes('正式') }
const filtered = computed(() => items.value.filter((i) => {
  const e = i.environment || ''
  if (envFilter.value === 'prod') return isProd(e)
  if (envFilter.value === 'test') return isTest(e)
  if (envFilter.value === 'other') return !isProd(e) && !isTest(e)
  return true
}))
// 鐵規則：表格每欄可排（2026-09-15 使用者抓到這張沒排序）。狀態攤成文字才排得動
const listRows = computed(() => filtered.value.map((s) => ({ ...s, status_text: s.collected ? '已收集' : '未收集' })))
const { sortKey: lsKey, sortDir: lsDir, toggle: lsToggle, sorted: listSorted } = useSort(listRows)

// ---- 多選 ----
const sel = ref<Set<string>>(new Set())
function toggle(ip: string) {
  const s = new Set(sel.value); s.has(ip) ? s.delete(ip) : s.add(ip); sel.value = s
}
const allSel = computed(() => filtered.value.length > 0 && filtered.value.every((i) => sel.value.has(i.ip)))
function toggleAll() {
  const s = new Set(sel.value)
  if (allSel.value) filtered.value.forEach((i) => s.delete(i.ip))
  else filtered.value.forEach((i) => s.add(i.ip))
  sel.value = s
}

// ---- 憑證輸入（當場打、不留存）＋收集 ----
const credOpen = ref(false)
const cUser = ref('')
const cPass = ref('')
const cTargets = ref<string[]>([])
const running = ref(false)
const prog = ref({ done: 0, total: 0, cur: '' })
const rowStatus = ref<Record<string, string>>({})   // ip -> 'running' | 'ok:N' | 'err:msg'

function askSingle(ip: string) { cTargets.value = [ip]; credOpen.value = true }
function askBatch() {
  if (!sel.value.size) { showToast('先勾選要收集的 switch', 'warn'); return }
  cTargets.value = [...sel.value]; credOpen.value = true
}
async function runCollect() {
  if (!cUser.value || !cPass.value) { showToast('帳號、密碼都要填', 'warn'); return }
  credOpen.value = false
  running.value = true
  prog.value = { done: 0, total: cTargets.value.length, cur: '' }
  for (const ip of cTargets.value) {
    prog.value.cur = ip
    rowStatus.value = { ...rowStatus.value, [ip]: 'running' }
    try {
      const r = await apiFetch<any>('/api/san/collect', {
        method: 'POST', body: { ip, username: cUser.value, password: cPass.value },
      })
      rowStatus.value = { ...rowStatus.value, [ip]: `ok:${r.wwpn_count}` }
    } catch (e: any) {
      rowStatus.value = { ...rowStatus.value, [ip]: 'err:' + (e?.data?.detail ?? e?.message ?? '失敗') }
    }
    prog.value.done++
  }
  cPass.value = ''            // 收完立刻清掉密碼（不留存）
  running.value = false
  await load()               // 重抓「上次收集時間／筆數」
  logRef.value?.reload()     // 這一輪每台的收集紀錄（含失敗當下的環境）
  showToast('收集完成', 'success')
}
function cancelCred() { credOpen.value = false; cPass.value = '' }

// ---- 離線匯入（防火牆還沒開通時：貼上自己跑的指令畫面）----
const impOpen = ref(false)
const impIp = ref('')
const impText = ref('')
const impBusy = ref(false)
// 標準指令清單（後端 san_collector.READONLY_COMMANDS，線上收集也跑這份）。
// 2026-09-15 使用者：「你要放在離線版，讓他去 COPY 出來的格式才會統一，你才好分析」
const guide = ref<{ commands: string[]; min_required: string[]; steps: string[] } | null>(null)
async function openImport() {
  impOpen.value = true
  if (!guide.value) {
    // 離線指引是輔助說明，載不到就不顯示指引區（guide=null → v-if 收起），不擋匯入視窗
    try { guide.value = await apiFetch('/api/san/offline-guide') } catch { guide.value = null }
  }
}
async function copyCmds() {
  const text = (guide.value?.commands ?? []).join('\n')
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // http 內網頁 clipboard API 可能被瀏覽器擋——退回傳統選取複製
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  showToast(`已複製 ${guide.value?.commands.length ?? 0} 個指令，到 switch 上一次貼一行`, 'success')
}
async function runImport(force = false) {
  if (!impIp.value.trim() || !impText.value.trim()) { showToast('IP 和貼上的內容都要填', 'warn'); return }
  impBusy.value = true
  try {
    const r = await apiFetch<any>('/api/san/import', {
      method: 'POST', body: { ip: impIp.value.trim(), transcript: impText.value, force },
    })
    const miss = (r.missing_cmds || []) as string[]
    if (r.ip_check === 'unverified') showToast('提醒：這份畫面沒有 fabricshow，無法確認是不是這個 IP 的 switch', 'warn', 10000)
    const filled = (r.asset_serial_filled || []).length
    const conflicts = (r.asset_serial_conflicts || []) as any[]
    if (conflicts.length) {
      showToast(`序號沒有寫進資產：資產上是 ${conflicts[0].asset_has}，switch 回報 ${conflicts[0].switch_says}——請確認哪個對`, 'warn', 15000)
    } else if (filled) {
      showToast(`已把 switch 回報的機箱序號寫進 ${filled} 筆資產的「設備序號」`, 'success', 8000)
    }
    showToast(`匯入成功：${r.switch_name || impIp.value}，${r.wwpn_count} 筆 WWPN（認到 ${(r.imported_cmds || []).length} 個指令`
      + (miss.length ? `；缺 ${miss.join('、')}` : '，清單全部都有') + '）', miss.length ? 'warn' : 'success', 12000)
    logRef.value?.reload()
    impOpen.value = false; impText.value = ''; impIp.value = ''
    await load()
  } catch (e: any) {
    const status = e?.statusCode ?? e?.response?.status
    // IP 防呆：switch 自己回報的 IP 跟填的不一樣 → 問人，確認才強制匯入（原文已存檔）
    if (status === 409 && !force) {
      impBusy.value = false
      logRef.value?.reload()
      if (confirm(`${e?.data?.detail ?? 'IP 對不上'}\n\n按「確定」仍匯入到你填的 IP；按「取消」回去檢查。`)) {
        return runImport(true)
      }
      return
    }
    showToast(`匯入失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
    logRef.value?.reload()
  } finally { impBusy.value = false }
}

// ---- 拖放 PuTTY LOG 檔（2026-09-16 使用者：「我一般會用 PUTTY LOG，把 PUTTY LOG 拖進來就好」）----
// 只讀純文字，**在瀏覽器裡讀完直接填進文字框**，不另外上傳檔案：
// 送到後端的還是同一段文字、走同一支 parser，判讀行為完全一致（也不用管檔案上傳的安全面）。
const dragOver = ref(false)
const TEXT_EXT = ['.log', '.txt', '.out', '.text']
const MAX_MB = 10

async function readLogFile(f: File | null | undefined) {
  if (!f) return
  const name = f.name.toLowerCase()
  if (!TEXT_EXT.some((e) => name.endsWith(e))) {
    showToast(`只收純文字紀錄檔（${TEXT_EXT.join('／')}）——PuTTY 的 session log 就是這種`, 'warn', 9000)
    return
  }
  if (f.size > MAX_MB * 1024 * 1024) {
    showToast(`檔案 ${(f.size / 1048576).toFixed(1)} MB，超過 ${MAX_MB} MB。請只留這台 switch 那一段`, 'warn', 9000)
    return
  }
  try {
    impText.value = await f.text()
    showToast(`已讀入 ${f.name}（${(f.size / 1024).toFixed(0)} KB）；確認 IP 後按「匯入判讀」`, 'success')
    // PuTTY log 的檔名常常就帶著 IP，順手帶出來讓人確認——不自動送出
    const m = f.name.match(/(\d{1,3}(?:\.\d{1,3}){3})/)
    if (m && !impIp.value) impIp.value = m[1]
  } catch (e: any) {
    showToast(`讀檔失敗：${e?.message ?? '請改用複製貼上'}`, 'error')
  }
}

function onDrop(ev: DragEvent) {
  dragOver.value = false
  readLogFile(ev.dataTransfer?.files?.[0])
}

// ---- 標記下線（共用 OfflineMarkModal，走 v1.185.0 的 batch-status）----
const offlineTarget = ref<{ serials: string[]; who: string; reason: string } | null>(null)
function askOffline(s: Sw, reason = '') {
  if (!s.asset_serial) { showToast('這台在資產庫裡查不到序號，無法改狀態', 'warn'); return }
  offlineTarget.value = { serials: [s.asset_serial], who: `${s.hostname || s.ip}（${s.ip}）`, reason }
}

// ---- 明細 ----
const detOpen = ref(false)
const detIp = ref('')
const detData = ref<any>(null)
const detRows = computed(() => ((detData.value?.rows || []) as any[]).map((r) => ({
  ...r, port_text: r.port ? `${r.switch}/${r.port}` : '', zones_text: (r.zones || []).join('、'),
})))
const { sortKey: dtKey, sortDir: dtDir, toggle: dtToggle, sorted: detSorted } = useSort(detRows)
async function openDetail(ip: string) {
  detIp.value = ip; detOpen.value = true; detData.value = null
  try {
    const r = await apiFetch<any>(`/api/san/switches/${encodeURIComponent(ip)}`)
    detData.value = r.data
  } catch (e: any) {
    showToast(`明細載入失敗：${e?.data?.detail ?? e?.message}`, 'error')
    detOpen.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>SAN 收集（Brocade）</h1>
      <InfoNote>帳號密碼<b>當場輸入、只在記憶體、收完即清、不會存起來</b>；系統只跑唯讀指令（switchshow／fabricshow／nscamshow／cfgactvshow／alishow），<b>不會改 switch 任何設定</b>。測試區可勾一批、用同一組帳密一次收。</InfoNote>
    </div>

    <!-- 收集端環境自我檢查：paramiko 壞掉的話即時收集一定失敗，直接黃底講清楚＋怎麼修 -->
    <div v-if="envchk && !envchk.ok" class="envwarn">
      ⚠ <b>這台收集端環境有問題，即時收集會失敗</b>：{{ envchk.reason }}
      <!-- 壞在哪裡要直接寫出來：patch 只掃固定兩個位置，不在那就永遠修不好（2026-09-15 公司機） -->
      <div v-if="envchk.broken_paths && envchk.broken_paths.length" class="fix">
        蓋住 paramiko 的空目錄：<code v-for="p in envchk.broken_paths" :key="p" class="bad">{{ p }}</code>
      </div>
      <div class="fix">{{ envchk.fix }}</div>
      <div class="acts">
        <button class="btn primary" :disabled="healing" @click="healEnv">
          {{ healing ? '修復中…' : '🔧 一鍵修復' }}
        </button>
        <span class="dim sm">把空目錄改名備份（不刪除），修完自動重驗</span>
      </div>
    </div>

    <p v-if="loading" class="dim">載入中…</p>
    <template v-else>
      <div class="bar">
        <div class="prog">已收集 <b>{{ done }}</b> ／ 共 <b>{{ total }}</b> 台</div>
        <div class="ftabs">
          <button class="ft" :class="{ on: envFilter === 'all' }" @click="envFilter = 'all'">全部</button>
          <button class="ft" :class="{ on: envFilter === 'prod' }" @click="envFilter = 'prod'">正式區</button>
          <button class="ft" :class="{ on: envFilter === 'test' }" @click="envFilter = 'test'">測試區</button>
          <button class="ft" :class="{ on: envFilter === 'other' }" @click="envFilter = 'other'">其他</button>
        </div>
        <button class="btn" :disabled="running" @click="openImport" title="防火牆還沒開通時：照指令清單在 switch 上跑，把畫面貼進來">離線匯入</button>
        <button class="btn primary" :disabled="running || !sel.size" @click="askBatch">
          收集選取（{{ sel.size }}）
        </button>
      </div>

      <p v-if="running" class="runbar">收集中… {{ prog.done }}/{{ prog.total }}　目前：{{ prog.cur }}</p>

      <table class="dtable">
        <thead><tr>
          <th style="width:34px"><input type="checkbox" :checked="allSel" @change="toggleAll"></th>
          <SortTh k="hostname" :active="lsKey" :dir="lsDir" class="txt" @sort="lsToggle">主機名</SortTh>
          <SortTh k="ip" :active="lsKey" :dir="lsDir" class="txt" @sort="lsToggle">IP</SortTh>
          <SortTh k="device_model" :active="lsKey" :dir="lsDir" class="txt" @sort="lsToggle">型號</SortTh>
          <SortTh k="physical_location" :active="lsKey" :dir="lsDir" class="txt" @sort="lsToggle">機房</SortTh>
          <SortTh k="environment" :active="lsKey" :dir="lsDir" class="txt" @sort="lsToggle">環境</SortTh>
          <SortTh k="status_text" :active="lsKey" :dir="lsDir" class="txt" @sort="lsToggle">狀態</SortTh>
          <SortTh k="wwpn_count" :active="lsKey" :dir="lsDir" class="num" @sort="lsToggle">WWPN</SortTh>
          <SortTh k="collected_at" :active="lsKey" :dir="lsDir" class="txt" @sort="lsToggle">上次收集</SortTh>
          <th class="txt">操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="s in listSorted" :key="s.ip">
            <td><input type="checkbox" :checked="sel.has(s.ip)" @change="toggle(s.ip)"></td>
            <td class="txt rowh">
              <NuxtLink v-if="s.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(s.asset_serial)}`"
                        title="看這台的資產詳細頁（含 SAN 分頁）">{{ s.hostname || '—' }}</NuxtLink>
              <template v-else>{{ s.hostname || '—' }}</template>
            </td>
            <td class="txt mono">
              <NuxtLink v-if="s.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(s.asset_serial)}`"
                        title="看這台的資產詳細頁（含 SAN 分頁）">{{ s.ip }}</NuxtLink>
              <template v-else>{{ s.ip }}</template>
            </td>
            <td class="txt">{{ s.device_model || '—' }}</td>
            <td class="txt">{{ s.physical_location || '—' }}</td>
            <td class="txt">{{ s.environment || '—' }}</td>
            <td class="txt">
              <span v-if="rowStatus[s.ip] === 'running'" class="pill run">收集中…</span>
              <span v-else-if="(rowStatus[s.ip] || '').startsWith('ok')" class="pill ok">✓ 剛收到 {{ rowStatus[s.ip].split(':')[1] }} 筆</span>
              <template v-else-if="(rowStatus[s.ip] || '').startsWith('err')">
                <span class="pill bad">✕ 失敗</span>
                <div class="failmsg" :title="rowStatus[s.ip].slice(4)">{{ rowStatus[s.ip].slice(4) }}</div>
                <div v-if="isConnFail(rowStatus[s.ip])" class="failhint">連不到多半是防火牆還沒開通 → 可改用上方「離線匯入」貼畫面</div>
                <div v-else class="failhint">這是系統端／連線協商的錯誤（不是防火牆）。把完整訊息貼給我看。</div>
              </template>
              <span v-else-if="s.collected" class="pill done">已收集</span>
              <span v-else class="pill none">未收集</span>
            </td>
            <td class="num">
              <button v-if="s.collected && s.wwpn_count != null" type="button" class="linkbtn mono"
                      title="看是哪幾筆 WWPN" @click="openDetail(s.ip)">{{ s.wwpn_count }}</button>
              <template v-else>{{ s.wwpn_count ?? '—' }}</template>
            </td>
            <td class="txt mono dim">{{ s.collected_at || '—' }}</td>
            <td class="txt acts">
              <AliveCheck :ip="s.ip" :can-offline="!!s.asset_serial" @offline="(r) => askOffline(s, r)" />
              <button class="mini" :disabled="!s.asset_serial" title="標記成停用／報廢／閒置（原因必填，會記進 CIA 待異動）"
                      @click="askOffline(s)">下線</button>
              <button class="mini" :disabled="running" @click="askSingle(s.ip)">收集</button>
              <button v-if="s.collected" class="mini" @click="openDetail(s.ip)">明細</button>
            </td>
          </tr>
          <tr v-if="filtered.length === 0"><td colspan="10" class="dim">這個範圍沒有 SAN switch</td></tr>
        </tbody>
      </table>
    </template>

    <!-- 憑證輸入 -->
    <div v-if="credOpen" class="mask" @click="cancelCred" />
    <div v-if="credOpen" class="modal cred">
      <h3>登入 SAN switch（{{ cTargets.length }} 台）</h3>
      <p class="dim sm">帳密只用這一次、收完即清，不會存起來。這 {{ cTargets.length }} 台會用同一組帳密依序收集。</p>
      <label>帳號<input v-model="cUser" class="in" autocomplete="off"></label>
      <label>密碼<input v-model="cPass" class="in" type="password" autocomplete="off" @keyup.enter="runCollect"></label>
      <div class="acts">
        <button class="btn primary" @click="runCollect">開始收集</button>
        <button class="btn" @click="cancelCred">取消</button>
      </div>
    </div>

    <!-- 收集紀錄：每次收集／自我檢查不通過／修復都留下當下完整環境，一次看清楚卡在哪 -->
    <CollectLog ref="logRef" :kinds="['san_collect', 'san_import', 'selfcheck', 'selfheal']" :start-open="true" />

    <!-- 離線匯入 -->
    <div v-if="impOpen" class="mask" @click="impOpen = false" />
    <div v-if="impOpen" class="modal imp">
      <h3>離線匯入 SAN switch</h3>
      <p class="dim sm">防火牆還沒開通、或系統連不到 switch 時用這個。<b>請照下面同一份指令清單跑</b>——大家格式一致，系統才判讀得準。</p>
      <ol v-if="guide" class="guide-steps">
        <li v-for="(st, i) in guide.steps" :key="i">{{ st }}</li>
      </ol>
      <div class="cmd-head">
        <b>要跑的唯讀指令（{{ guide?.commands.length ?? 0 }} 個，一次貼一行）</b>
        <button class="mini" type="button" :disabled="!guide" @click="copyCmds">📋 複製全部指令</button>
      </div>
      <pre class="cmd-list">{{ (guide?.commands ?? []).join('\n') }}</pre>
      <p v-if="guide" class="dim sm">至少要有：<code>{{ guide.min_required.join(' / ') }}</code>（缺了照樣匯入，但判讀會不完整）</p>
      <label>這台 switch 的管理 IP<input v-model="impIp" class="in" placeholder="例如 192.0.2.10"></label>
      <label>貼上整段畫面，或把 PuTTY 紀錄檔拖進來
        <div class="droparea" :class="{ over: dragOver }"
             @dragover.prevent="dragOver = true" @dragleave.prevent="dragOver = false" @drop.prevent="onDrop">
          <textarea v-model="impText" class="in ta" rows="12" placeholder="IBM_8969_F24:admin> switchshow&#10;...&#10;IBM_8969_F24:admin> nscamshow&#10;...&#10;&#10;（也可以直接把 PuTTY 的 .log 檔拖到這個框裡）"></textarea>
          <div v-if="dragOver" class="dropmsg">放開就讀進來</div>
        </div>
      </label>
      <p class="dim sm">
        檔案不會被上傳——瀏覽器讀成文字填進上面的框，送出的跟你自己貼的完全一樣。
        <label class="filepick">或選擇檔案
          <input type="file" accept=".log,.txt,.out,.text,text/plain" @change="readLogFile(($event.target as HTMLInputElement).files?.[0])">
        </label>
      </p>
      <div class="acts">
        <button class="btn primary" :disabled="impBusy" @click="runImport()">{{ impBusy ? '匯入中…' : '匯入判讀' }}</button>
        <button class="btn" @click="impOpen = false">取消</button>
      </div>
    </div>

    <OfflineMarkModal v-if="offlineTarget" :serials="offlineTarget.serials" :who="offlineTarget.who"
                      :default-reason="offlineTarget.reason"
                      @close="offlineTarget = null" @done="offlineTarget = null; load()" />

    <!-- 明細 -->
    <div v-if="detOpen" class="mask" @click="detOpen = false" />
    <div v-if="detOpen" class="modal det">
      <div class="dhd">
        <b>{{ detData?.switch || detIp }}</b>
        <span class="dim">　cfg: {{ detData?.zoning_cfg || '—' }}　·　{{ detData?.rows?.length || 0 }} 筆 WWPN</span>
        <button class="mini" @click="detOpen = false">關閉</button>
      </div>
      <div class="dwrap">
        <table class="dtable">
          <thead><tr>
            <SortTh k="wwpn" :active="dtKey" :dir="dtDir" class="txt" @sort="dtToggle">WWPN</SortTh>
            <SortTh k="alias" :active="dtKey" :dir="dtDir" class="txt" @sort="dtToggle">別名</SortTh>
            <SortTh k="port_text" :active="dtKey" :dir="dtDir" class="txt" @sort="dtToggle">switch/port</SortTh>
            <SortTh k="zones_text" :active="dtKey" :dir="dtDir" class="txt" @sort="dtToggle">zones</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in detSorted" :key="r.wwpn">
              <td class="txt mono">{{ r.wwpn }}</td>
              <td class="txt">{{ r.alias || '—' }}</td>
              <td class="txt">{{ r.port_text || '—' }}</td>
              <td class="txt">{{ r.zones_text || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.acts { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.droparea { position: relative; }
.droparea.over .ta { outline: 2px dashed var(--brand); outline-offset: 2px; }
.dropmsg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
           background: rgba(0,128,106,.08); color: var(--brand-dark); font-size: 14px;
           border-radius: 6px; pointer-events: none; }
.filepick input { display: none; }
.filepick { text-decoration: underline; cursor: pointer; }
.linkbtn { background: none; border: none; padding: 0; color: var(--brand-dark); text-decoration: underline;
           cursor: pointer; font-size: inherit; }
.guide-steps { margin: 6px 0 8px 18px; padding: 0; font-size: 12px; line-height: 1.7; }
.cmd-head { display: flex; align-items: center; gap: 8px; font-size: 13px; margin-top: 6px; }
.cmd-list { margin: 6px 0; padding: 8px 10px; font-size: 12px; line-height: 1.6; max-height: 230px; overflow: auto;
            background: rgba(0,0,0,.04); border-radius: 5px; font-family: ui-monospace, monospace; }

.envwarn .bad { display: inline-block; margin-right: 6px; padding: 1px 6px;
                border-radius: 4px; background: rgba(176,106,0,.14); }
.envwarn .acts { margin-top: 8px; display: flex; align-items: center; gap: 8px; }
.page { padding: 18px 22px 60px; }
.hd { display: flex; align-items: center; gap: 8px; }
h1 { font-size: 19px; margin: 0; }
.bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin: 14px 0 10px; }
.prog { font-size: 14px; color: var(--ink-soft); }
.prog b { color: var(--brand-dark); font-family: var(--disp); }
.ftabs { display: flex; gap: 6px; }
.ft { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft);
  border-radius: 999px; padding: 5px 14px; font-size: 13px; cursor: pointer; }
.ft.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); font-weight: 600; }
.btn { padding: 6px 14px; border-radius: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn.primary { margin-left: auto; }
.runbar { font-size: 13px; color: var(--warn-text); margin: 0 0 10px; }
.mini { margin-right: 6px; padding: 3px 10px; font-size: 12px; border-radius: 6px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .5; }
.pill { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.pill.done { background: var(--mint); color: var(--brand-dark); }
.pill.none { background: var(--sub); color: var(--muted); }
.pill.run { background: var(--warn-soft); color: var(--warn-text); }
.pill.ok { background: var(--good-soft); color: var(--brand-dark); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
/* 失敗原因直接顯示在畫面上（不只 tooltip）：錯誤是「不看會出事」，該留在畫面 */
.failmsg { margin-top: 4px; color: var(--bad); font-size: 11.5px; line-height: 1.4;
  max-width: 320px; white-space: normal; word-break: break-word; }
.failhint { margin-top: 2px; color: var(--ink-soft); font-size: 11px; max-width: 320px; }
.envwarn { margin: 12px 0; padding: 12px 14px; border-radius: 10px;
  background: var(--warn-soft); border: 1px solid var(--warn); color: var(--warn-text);
  font-size: 13px; line-height: 1.6; }
.envwarn .fix { margin-top: 6px; color: var(--ink-soft); }
.dim { color: var(--ink-soft); } .sm { font-size: 12px; }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 39; }
.modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%,-50%);
  background: var(--card); border: 1px solid var(--border-strong); border-radius: var(--radius);
  box-shadow: 0 12px 40px rgba(0,0,0,.25); z-index: 40; padding: 18px 20px; }
.modal.cred { width: 360px; }
.modal.imp { width: min(720px, 92vw); }
.modal.imp h3 { margin: 0 0 6px; font-size: 15px; }
.modal.imp label { display: block; font-size: 12px; color: var(--muted); margin-top: 10px; }
.modal.imp .in { width: 100%; margin-top: 3px; }
.modal.imp .ta { font-family: ui-monospace, Consolas, monospace; font-size: 12px; resize: vertical; }
.modal.imp code { background: var(--sub); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }
.modal.cred h3 { margin: 0 0 6px; font-size: 15px; }
.modal.cred label { display: block; font-size: 12px; color: var(--muted); margin-top: 10px; }
.in { width: 100%; padding: 7px 10px; margin-top: 3px; border: 1px solid var(--border-strong);
  border-radius: 8px; background: #fff; color: var(--ink); }
.acts { display: flex; gap: 8px; margin-top: 16px; }
.acts .btn.primary { margin-left: 0; }
.modal.det { width: min(920px, 92vw); max-height: 80vh; display: flex; flex-direction: column; padding: 0; }
.dhd { padding: 11px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }
.dhd .mini { margin-left: auto; }
.dwrap { overflow: auto; padding: 8px 16px 16px; }
</style>
