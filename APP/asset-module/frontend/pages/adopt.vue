<script setup lang="ts">
// 納入管理＝把機器帶進系統的單一入口。頁面分兩段，順序就是實際工作順序：
//   ①「開始收集」（決策 C4）：貼網段或 IP 清單按一下，系統自己選路
//      （22→SSH／445→WinRM／活著但進不去→Push Agent／完全不通→只能匯入），
//      給一張分得出「收到了」跟「要人工處理」的結果表。
//   ②「登記成資產」：掃到了但沒登記的主機，補上業務欄位建成資產。
// 表單依 /api/assets/field-meta 動態分區（功能分類）＋標層：
//   tech(技術層) 有值→灰底唯讀自動帶；tech 沒值→可手動補；biz(業務層) 一律讓使用者填。
interface FieldMeta { label: string; category: string; layer: 'tech' | 'biz'; required?: boolean; options?: string[]; new?: boolean; help?: string }
interface Meta { categories: { key: string; label: string }[]; fields: Record<string, FieldMeta> }
interface ScanHost {
  ip: string; hostname: string | null; device_model: string | null; is_vm: number; segment: string | null
  // S16 被動指紋：讓人在納管前先認得出這台大概是什麼
  mac?: string | null; mac_vendor?: string | null; open_ports?: string | null; ttl?: number | null; os_guess?: string | null
}
interface DispatchRow {
  ip: string; alive: number; open_ports: string | null; route: string; status: string
  asset_serial: string | null; hostname: string | null; registered: number; message: string
}
interface DispatchOut {
  run?: { targets_raw: string | null; target_count: number; status: string; started_at: string | null; finished_at: string | null } | null
  total: number; collected: number; needs_action: number
  by_status: Record<string, number>; by_route: Record<string, number>
  results: DispatchRow[]
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const meta = ref<Meta | null>(null)
const hosts = ref<ScanHost[]>([])
const { sortKey, sortDir, toggle, sorted } = useSort(hosts, 'ip')
const selected = ref<ScanHost | null>(null)
const form = reactive<Record<string, any>>({})

// 一鍵納管用共用元件 OnboardModal，這裡只管開關與成功後移除該筆
const onboardHost = ref<ScanHost | null>(null)
function openOnboard(h: ScanHost) { onboardHost.value = h }
function onOnboarded() {
  const ip = onboardHost.value!.ip
  hosts.value = hosts.value.filter((x) => x.ip !== ip)
  // 從收集結果表點納管成功的，那一列要當場翻成「已收到」——
  // 不然畫面還停在「待佈身分」，人會以為沒成功又點一次
  const row = dispatch.value?.results.find((r) => r.ip === ip)
  if (row && dispatch.value) {
    row.status = 'collected'
    row.message = '已佈好收集帳號，現在收得到'
    dispatch.value.collected += 1
    dispatch.value.needs_action -= 1
  }
  onboardHost.value = null
  loadAudit()
}
const saving = ref(false)
const submitAttempted = ref(false)

function isMissing(key: string, required?: boolean) {
  return submitAttempted.value && required && !form[key]
}

// ===== 納管紀錄 =====
// 納管失敗時原本只看得到那一瞬間的 toast，關掉就沒了——但每一次嘗試其實都有寫進
// onboard_audit（含階段、訊息、腳本輸出），只是從來沒有畫面（使用者 2026-08-16 問
// 「失敗怎麼看 LOG」才發現）。沒有畫面等於要人 SSH 進去翻 DB 或 journalctl，
// 跟這個系統的定位相反。
interface OnboardAudit {
  id: number; target_ip: string; platform: string | null; login_user: string | null
  trigger: string; triggered_by: string | null; ok: number
  stage: string | null; message: string | null; output: string | null; created_at: string
}
const audits = ref<OnboardAudit[]>([])
const showAudit = ref(false)
const openOutput = ref<number | null>(null)
const { sortKey: aSortKey, sortDir: aSortDir, toggle: aToggle, sorted: aSorted } =
  useSort(audits, 'id')
async function loadAudit() {
  try { audits.value = await apiFetch<OnboardAudit[]>('/api/onboard/audit') } catch { /* 沒紀錄就空著 */ }
}
const lastFail = computed(() => audits.value.find((a) => !a.ok) || null)

// ===== ①收集入口（決策 C4）=====
const targets = ref('')
const dispatching = ref(false)
// 同「一鍵納管」那顆的問題：只變灰看不出在不在跑。這裡台數是算得出來的，
// 所以講得更具體——幾台、跑多久、每台大概多久。仍然不做假的百分比進度：
// 後端是一次呼叫，中途沒有真的可回報的節點。
const dElapsed = ref(0)
let dTicker: ReturnType<typeof setInterval> | null = null
// 目標數量：本地照 parse 規則粗估（CIDR 展開、a.b.c.d-e 範圍、單一 IP）。
// 只用來讓人心裡有數，真正的解析與上限在後端。
const targetCount = computed(() => {
  let n = 0
  for (const tok of targets.value.split(/[\s,;]+/)) {
    if (!tok) continue
    const cidr = tok.match(/^\d+\.\d+\.\d+\.\d+\/(\d+)$/)
    if (cidr) { const bits = 32 - Number(cidr[1]); n += Math.max(2 ** bits - 2, 1); continue }
    const range = tok.match(/^\d+\.\d+\.\d+\.(\d+)-(\d+)$/)
    if (range) { n += Math.max(Number(range[2]) - Number(range[1]) + 1, 0); continue }
    n += 1
  }
  return n
})
const dispatch = ref<DispatchOut | null>(null)
const dispatchRows = computed<DispatchRow[]>(() => dispatch.value?.results ?? [])
const { sortKey: dSortKey, sortDir: dSortDir, toggle: dToggle, sorted: dSorted } =
  useSort(dispatchRows, 'ip')

const ROUTE_LABEL: Record<string, string> = {
  ssh: 'SSH 收集', winrm: 'WinRM 收集', agent: 'Push Agent', import: '只能匯入',
}
const STATUS_LABEL: Record<string, string> = {
  collected: '已收到', needs_credential: '待佈身分', needs_agent: '待裝 Agent',
  import_only: '只能匯入', failed: '收集失敗',
}
// 綠＝這台現在收得到；黃＝人做一件事就能收到；灰＝這條路走不通，只能靠匯入
const STATUS_TONE: Record<string, string> = {
  collected: 'ok', needs_credential: 'warn', needs_agent: 'warn',
  failed: 'bad', import_only: 'off',
}

async function runDispatch() {
  if (dispatching.value) return
  if (!targets.value.trim()) { showToast('請先輸入網段或 IP 清單', 'warn'); return }
  dispatching.value = true
  dElapsed.value = 0
  dTicker = setInterval(() => { dElapsed.value += 1 }, 1000)
  showToast('開始收集：探測中，連得上的會直接進去收，可能要一段時間', 'info')
  try {
    dispatch.value = await apiFetch<DispatchOut>('/api/collect/dispatch', {
      method: 'POST', body: { targets: targets.value },
    })
    const d = dispatch.value
    if (d.needs_action === 0) showToast(`完成：${d.collected} 台全部收到`, 'success')
    else showToast(`完成：收到 ${d.collected} 台，${d.needs_action} 台要人工處理`, 'warn')
    await load()   // 有的機器狀態變了，候選清單要跟著更新
  } catch (e: any) {
    showToast(`收集失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  } finally {
    dispatching.value = false
    if (dTicker) { clearInterval(dTicker); dTicker = null }
  }
}
onBeforeUnmount(() => { if (dTicker) clearInterval(dTicker) })

// 要請人裝 agent 的那批：產安裝包給他帶過去
const pkg = ref<any>(null)
const pkgBusy = ref(false)
const pkgCopied = ref(false)
async function getPackage(ip: string) {
  if (pkgBusy.value) return
  pkgBusy.value = true
  try {
    pkg.value = await apiFetch<any>('/api/collect/agent-package', { method: 'POST', body: { ip } })
  } catch (e: any) {
    showToast(`取得安裝包失敗：${e?.data?.detail || '請稍後重試'}`, 'error')
  } finally {
    pkgBusy.value = false
  }
}
async function copyPkgScript() {
  try {
    await navigator.clipboard.writeText(pkg.value?.files?.['bootstrap_watcher.sh'] ?? '')
    pkgCopied.value = true
    setTimeout(() => (pkgCopied.value = false), 2000)
  } catch { showToast('複製失敗，請手動選取', 'warn') }
}

async function load() {
  ;[meta.value, hosts.value] = await Promise.all([
    apiFetch<Meta>('/api/field-meta'),
    apiFetch<ScanHost[]>('/api/scan/unregistered'),
  ])
}
await load()
// 上一次的結果還原回來——這動作要等數十秒，做完一重新整理就沒了等於白等
try { dispatch.value = await apiFetch<DispatchOut>('/api/collect/dispatch/latest') } catch { /* 沒跑過就空著 */ }
await loadAudit()
if (dispatch.value && !dispatch.value.results.length) dispatch.value = null

function fieldsOf(catKey: string) {
  if (!meta.value) return []
  return Object.entries(meta.value.fields)
    .filter(([, f]) => f.category === catKey)
    .map(([key, f]) => ({ key, ...f }))
}

function openAdopt(host: ScanHost) {
  selected.value = host
  Object.keys(form).forEach((k) => delete form[k])
  // 技術層：從掃描結果自動帶入現有的（其餘 OS/序號/MAC 待 facts 收集，先空著可手補）
  form.ip = host.ip
  form.hostname = host.hostname ?? ''
  form.subnet = host.segment ?? ''
  if (host.mac) form.mac = host.mac
  if (host.is_vm) form.is_vm = 'VM'
}

async function submitAdopt() {
  submitAttempted.value = true
  if (!form.asset_purpose || !form.environment) {
    showToast('請填紅框的必填欄位：資產用途、環境別', 'warn')
    return
  }
  saving.value = true
  try {
    await apiFetch('/api/assets/adopt', { method: 'POST', body: { fields: { ...form } } })
    showToast(`已納入管理：${selected.value?.ip}`, 'success')
    hosts.value = hosts.value.filter((h) => h.ip !== selected.value?.ip)
    selected.value = null
  } catch (err: any) {
    showToast(`納入失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> 資產盤點 → <b>納入管理</b></div>
    <h1 class="page-title">納入管理</h1>
    <p class="page-sub">把機器帶進系統。先「開始收集」讓系統自己找路進去，再把還沒登記的補成資產。</p>

    <!-- ===== ①收集入口：一個動作，系統自己選路 ===== -->
    <section v-if="!selected" class="card">
      <div class="sec-head"><span class="t">開始收集</span></div>
      <p class="hint">
        輸入要納進來的範圍，按一下就好——<b>不用自己判斷該走哪一條</b>。
        系統會逐台探測，能走 SSH 的走 SSH、只有 Windows 管道的走 WinRM、
        進不去但活著的產 Push Agent 安裝包、完全沒回應的列進「只能靠匯入」。
        <br>已經有資料在 Excel／RVTools 裡的走
        <NuxtLink class="dl" to="/import">資料匯入</NuxtLink>，那是既有資料進場，不是收集。
      </p>
      <textarea v-model="targets" class="targets" :disabled="dispatching"
                placeholder="每行一項，可混用：&#10;10.99.1.0/24&#10;10.99.2.10-20&#10;10.99.3.5" />
      <div class="actions">
        <button class="btn" :disabled="dispatching" @click="runDispatch">
          {{ dispatching ? `收集中… ${dElapsed}s` : '開始收集' }}
        </button>
        <span v-if="!dispatching" class="ahint">
          一次最多 1024 台；整個網段的例行掃描請走掃描排程。
          <template v-if="targetCount">目前輸入約 <b>{{ targetCount }}</b> 台。</template>
        </span>
      </div>
      <!-- 執行中：講得出「在做什麼、幾台、大概多久」，不做假的百分比 -->
      <div v-if="dispatching" class="working">
        <span class="dots"><i /><i /><i /></span>
        <div class="w-txt">
          正在逐台探測（{{ targetCount || '?' }} 台），能進去的會直接連進去收。
          <br>沒回應的每台約 5 秒（要等它逾時），所以全部沒回應時最久約
          <b>{{ Math.ceil((targetCount || 1) * 5 / 64) }} 秒</b>——同時跑 64 台，不是一台一台等。
          <br>做完會存下來，<b>中途關掉頁面也不會白跑</b>，回來還看得到結果。
        </div>
      </div>
    </section>

    <!-- 結果：一張表分得出「收到了」跟「要人工處理」 -->
    <section v-if="dispatch && dispatch.results.length && !selected" class="card">
      <div class="sec-head">
        <span class="t">收集結果</span>
        <span v-if="dispatch.run" class="muted small">
          {{ dispatch.run.finished_at || dispatch.run.started_at }}｜共 {{ dispatch.run.target_count }} 台
        </span>
      </div>
      <div class="tiles">
        <div class="tile"><div class="t-num mono">{{ dispatch.total }}</div><div class="t-lbl">這次處理</div></div>
        <div class="tile ok"><div class="t-num mono">{{ dispatch.collected }}</div><div class="t-lbl">已收到</div></div>
        <div class="tile warn"><div class="t-num mono">{{ dispatch.needs_action }}</div><div class="t-lbl">要人工處理</div></div>
        <div class="tile"><div class="t-num mono">{{ dispatch.by_status.needs_agent || 0 }}</div><div class="t-lbl">要請人裝 Agent</div></div>
        <div class="tile"><div class="t-num mono">{{ dispatch.by_status.import_only || 0 }}</div><div class="t-lbl">只能靠匯入</div></div>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="ip" :active="dSortKey" :dir="dSortDir" @sort="dToggle">IP</SortTh>
            <SortTh k="hostname" :active="dSortKey" :dir="dSortDir" @sort="dToggle">主機名稱</SortTh>
            <SortTh k="open_ports" :active="dSortKey" :dir="dSortDir" @sort="dToggle">開放埠</SortTh>
            <SortTh k="route" :active="dSortKey" :dir="dSortDir" @sort="dToggle">走哪條路</SortTh>
            <SortTh k="status" :active="dSortKey" :dir="dSortDir" @sort="dToggle">結果</SortTh>
            <SortTh k="message" :active="dSortKey" :dir="dSortDir" @sort="dToggle">下一步</SortTh>
            <th>操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in dSorted" :key="r.ip">
              <td class="mono">
                <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">{{ r.ip }}</NuxtLink>
                <template v-else>{{ r.ip }}</template>
              </td>
              <td>{{ r.hostname || (r.registered ? '—' : '(未登記)') }}</td>
              <!-- 天條二：埠點下去看得到「還有誰也在跑它」 -->
              <td class="mono small">
                <template v-if="r.open_ports">
                  <NuxtLink v-for="p in r.open_ports.split(',')" :key="p" class="dl port"
                            :to="`/services?port=${p}`">{{ p }}</NuxtLink>
                </template>
                <template v-else>—</template>
              </td>
              <td>{{ ROUTE_LABEL[r.route] || r.route }}</td>
              <td><span class="pill" :class="STATUS_TONE[r.status]">{{ STATUS_LABEL[r.status] || r.status }}</span></td>
              <td class="msg">{{ r.message }}</td>
              <td class="ops">
                <button v-if="r.status === 'needs_agent'" class="btn small primary"
                        :disabled="pkgBusy" @click="getPackage(r.ip)">取得安裝包</button>
                <button v-else-if="r.status === 'needs_credential' && r.route === 'ssh'"
                        class="btn small primary"
                        @click="openOnboard({ ip: r.ip, os_guess: null } as any)">⚡ 一鍵納管</button>
                <NuxtLink v-else-if="r.status === 'import_only'" class="btn small ghost" to="/import">去匯入</NuxtLink>
                <span v-else class="muted small">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Push Agent 安裝包：交給「要請人裝」的那位 -->
    <div v-if="pkg" class="modal-mask" @click.self="pkg = null">
      <div class="modal wide">
        <div class="mhead">Push Agent 安裝包 — {{ pkg.ip }}</div>
        <p class="mhint">
          這台系統進不去，改由主機自己每天回報，<b>collector 完全不主動連進去</b>——
          防火牆只需要開「主機 → collector」單向。
          <template v-if="pkg.created_asset"><br>已為這台建立資產 <code>{{ pkg.asset_serial }}</code>，業務欄位請稍後補上。</template>
        </p>
        <ol class="steps">
          <li>複製下方腳本，存成 <code>/tmp/bootstrap_watcher.sh</code> 交給該機管理者。</li>
          <li>請他以 root 執行一行：<code>{{ pkg.install_command }}</code></li>
          <li>裝完當天起每天自動回報到 <code>{{ pkg.collector_url }}</code>，之後不需要再用 root 登入這台。</li>
        </ol>
        <textarea class="cmdbox" readonly :value="pkg.files['bootstrap_watcher.sh']"
                  @focus="($event.target as HTMLTextAreaElement).select()" />
        <div class="macts">
          <button class="btn primary" @click="copyPkgScript">{{ pkgCopied ? '✓ 已複製' : '複製安裝腳本' }}</button>
          <button class="btn ghost" @click="pkg = null">關閉</button>
        </div>
      </div>
    </div>

    <!-- ===== 納管紀錄：失敗了要看得到為什麼，不用 SSH 進去翻 ===== -->
    <section v-if="!selected && audits.length" class="card">
      <div class="sec-head">
        <span class="t">納管紀錄</span>
        <button class="link-btn" type="button" @click="showAudit = !showAudit">
          {{ showAudit ? '收起' : `看全部（${audits.length} 筆）` }}
        </button>
      </div>

      <!-- 最近一次失敗直接攤開，不用先展開再找 -->
      <div v-if="lastFail && !showAudit" class="lastfail">
        <div class="lf-head">
          最近一次失敗 · {{ lastFail.created_at }} · <span class="mono">{{ lastFail.target_ip }}</span>
          <span class="pill bad">{{ lastFail.stage || '未知階段' }}</span>
        </div>
        <p class="lf-msg">{{ lastFail.message }}</p>
        <pre v-if="lastFail.output" class="outbox">{{ lastFail.output }}</pre>
        <p class="ahint">
          階段的意思：<b>connect</b> = 還沒進到目標機（帳密錯／連不上／缺收集金鑰）；
          <b>execute</b> = 進去了但腳本沒跑完（多半是權限或 sudo）。
        </p>
      </div>

      <div v-if="showAudit" class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="created_at" :active="aSortKey" :dir="aSortDir" @sort="aToggle">時間</SortTh>
            <SortTh k="target_ip" :active="aSortKey" :dir="aSortDir" @sort="aToggle">目標</SortTh>
            <SortTh k="platform" :active="aSortKey" :dir="aSortDir" @sort="aToggle">平台</SortTh>
            <SortTh k="login_user" :active="aSortKey" :dir="aSortDir" @sort="aToggle">登入帳號</SortTh>
            <SortTh k="trigger" :active="aSortKey" :dir="aSortDir" @sort="aToggle">觸發</SortTh>
            <SortTh k="ok" :active="aSortKey" :dir="aSortDir" @sort="aToggle">結果</SortTh>
            <SortTh k="stage" :active="aSortKey" :dir="aSortDir" @sort="aToggle">階段</SortTh>
            <SortTh k="message" :active="aSortKey" :dir="aSortDir" @sort="aToggle">訊息</SortTh>
            <th>輸出</th>
          </tr></thead>
          <tbody>
            <template v-for="a in aSorted" :key="a.id">
              <tr>
                <td class="small">{{ a.created_at }}</td>
                <td class="mono">{{ a.target_ip }}</td>
                <td>{{ a.platform || '—' }}</td>
                <td>{{ a.login_user || '—' }}</td>
                <td class="small">{{ a.trigger === 'auto' ? '排程' : (a.triggered_by || '手動') }}</td>
                <td><span class="pill" :class="a.ok ? 'ok' : 'bad'">{{ a.ok ? '成功' : '失敗' }}</span></td>
                <td class="small">{{ a.stage || '—' }}</td>
                <td class="msg">{{ a.message }}</td>
                <td>
                  <button v-if="a.output" class="link-btn" type="button"
                          @click="openOutput = openOutput === a.id ? null : a.id">
                    {{ openOutput === a.id ? '收起' : '看輸出' }}
                  </button>
                  <span v-else class="muted small">—</span>
                </td>
              </tr>
              <tr v-if="openOutput === a.id">
                <td colspan="9"><pre class="outbox">{{ a.output }}</pre></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <p class="ahint" style="margin-top:8px">
        這裡只留帳號名與成敗，<b>永遠不含密碼</b>。要更底層的伺服器日誌：
        <code>journalctl -u webit3-api -n 200</code>
      </p>
    </section>

    <div v-if="!selected" class="sec-head" style="margin-top:26px"><span class="t">登記成資產</span></div>
    <div v-if="hosts.length === 0 && !selected" class="empty">
      目前沒有「掃到了、CIA 沒登記」的主機——都納管完了，或還沒掃描。
    </div>

    <!-- 候選清單 -->
    <div v-if="!selected && hosts.length" class="tbl-wrap">
      <p class="hint">下方「研判」是<b>未登入主機、純從網路探測</b>推得的線索，用來認出這台大概是什麼；
        真正的主機名／序號要納管後由 facts 收集。</p>
      <table>
        <thead><tr>
          <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
          <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
          <SortTh k="os_guess" :active="sortKey" :dir="sortDir" @sort="toggle">研判（掃描指紋）</SortTh>
          <th>操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="h in sorted" :key="h.ip">
            <td class="mono">{{ h.ip }}</td>
            <td>{{ h.hostname || '(反解不到)' }}</td>
            <td>
              <div class="fp">
                <span v-if="h.is_vm" class="tag vm">VM</span>
                <span v-if="h.mac_vendor" class="tag vendor">{{ h.mac_vendor }}</span>
                <span v-if="h.os_guess" class="tag os">{{ h.os_guess }}</span>
                <span v-if="!h.mac_vendor && !h.os_guess && !h.is_vm" class="muted">— 線索不足 —</span>
              </div>
              <div class="fp-sub muted">
                <span v-if="h.open_ports">埠 {{ h.open_ports }}</span>
                <span v-if="h.mac" class="mono">{{ h.mac }}</span>
                <span v-if="h.segment">{{ h.segment }}</span>
              </div>
            </td>
            <td class="ops">
              <button class="btn small primary" @click="openOnboard(h)" title="系統自動進去建收集帳號">⚡ 一鍵納管</button>
              <button class="btn small ghost" @click="openAdopt(h)" title="只登記成資產、不建收集帳號">手動登記</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 一鍵納管：共用元件 -->
    <OnboardModal v-if="onboardHost" :ip="onboardHost.ip" :os-guess="onboardHost.os_guess"
                  @done="onOnboarded" @close="onboardHost = null" />

    <!-- 納入管理表單 -->
    <div v-if="selected && meta" class="form-card">
      <div class="form-head">
        <div><b>納入管理 — {{ selected.ip }}</b><span class="muted"> {{ selected.hostname ? '（' + selected.hostname + '）' : '' }}</span></div>
        <button class="btn ghost small" @click="selected = null">← 回清單</button>
      </div>

      <template v-for="cat in meta.categories" :key="cat.key">
        <div class="sec-head">
          <span class="t">{{ cat.label }}</span>
        </div>
        <div class="grid">
          <div v-for="f in fieldsOf(cat.key)" :key="f.key" class="f">
            <label :title="f.help || undefined">
              {{ f.label }}<span v-if="f.required" class="req">必填</span>
              <span class="layer" :class="f.layer">{{ f.layer === 'tech' ? '自動' : '手填' }}</span>
            </label>
            <!-- tech 有值→唯讀灰底 -->
            <div v-if="f.layer === 'tech' && form[f.key]" class="ro">{{ form[f.key] }}</div>
            <!-- tech 沒值→可手動補 -->
            <input v-else-if="f.layer === 'tech'" v-model="form[f.key]" class="in" placeholder="未取得（待 facts 收集，可手動補）" />
            <!-- biz select -->
            <select v-else-if="f.options" v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }">
              <option value="">—</option>
              <option v-for="o in f.options" :key="o" :value="o">{{ o }}</option>
            </select>
            <!-- biz input -->
            <input v-else v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }" />
          </div>
        </div>
      </template>

      <div class="actions">
        <button class="btn" :disabled="saving" @click="submitAdopt">{{ saving ? '登記中…' : '納入管理' }}</button>
        <button class="btn ghost" @click="selected = null">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.working { display: flex; gap: 10px; align-items: flex-start; margin-top: 10px;
  padding: 9px 11px; border: 1px solid var(--border-strong); background: var(--mint); }
.w-txt { font-size: 11px; color: var(--muted); line-height: 1.7; }
.w-txt b { color: var(--ink-soft); }
.dots { display: inline-flex; gap: 3px; padding-top: 5px; flex-shrink: 0; }
.dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--brand);
  animation: blink 1.2s infinite ease-in-out; }
.dots i:nth-child(2) { animation-delay: .2s; }
.dots i:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }

.link-btn { background: none; border: none; color: var(--brand-dark); font-family: inherit;
  font-size: 11.5px; cursor: pointer; padding: 0; text-decoration: underline; }
.lastfail { border: 1px solid var(--bad); background: var(--bad-soft); padding: 10px 12px; }
.lf-head { font-size: 12px; font-weight: 700; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.lf-msg { font-size: 12px; color: var(--ink); line-height: 1.6; margin: 6px 0; }
.outbox { font-family: ui-monospace, Consolas, monospace; font-size: 10.5px; line-height: 1.5;
  background: var(--card); border: 1px solid var(--border-strong); padding: 8px;
  max-height: 240px; overflow: auto; white-space: pre-wrap; word-break: break-all; margin: 6px 0; }

.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong); padding: 8px 14px;
  font-size: 12.5px; color: var(--ink-soft); display: flex; gap: 8px; align-items: center; margin-bottom: 14px; }
.breadcrumb-bar .pin, .breadcrumb-bar b { color: var(--brand-dark); }
.page-title { font-size: 17px; font-weight: 700; margin: 0 0 4px; }
.page-sub { font-size: 12px; color: var(--muted); margin: 0 0 16px; }
.empty { border: 1px solid var(--border); background: var(--card); padding: 30px; text-align: center; color: var(--muted); font-size: 13px; }
.hint { font-size: 11.5px; color: var(--muted); margin: 0 0 10px; line-height: 1.5; }
.ops { display: flex; gap: 6px; white-space: nowrap; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.small.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 380px; max-width: 92vw; background: var(--card-solid, #12211c);
  border: 1px solid var(--border-strong); border-radius: 12px; padding: 22px 24px; }
.modal .mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand-dark); }
.modal .mhint { font-size: 11.5px; color: var(--muted); line-height: 1.6; margin: 0 0 16px; }
.modal .mhint code { color: var(--brand-dark); }
.modal .mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
.modal .mf input, .modal .mf select { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.modal .mguess { font-size: 11px; color: var(--brand-dark); }
.modal .macts { display: flex; gap: 10px; margin-top: 6px; }
.hint b { color: var(--ink-soft); }
.fp { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.fp .tag { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 3px; white-space: nowrap; }
.fp .tag.vm { background: var(--warn-soft); color: var(--warn-text); }
.fp .tag.vendor { background: var(--mint-deep); color: var(--brand-dark); }
.fp .tag.os { background: var(--good-soft); color: var(--brand-dark); }
.fp-sub { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; margin-top: 4px; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 560px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.form-card { border: 1px solid var(--border); background: var(--card); padding: 18px 20px; }
.form-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.sec-head { margin: 16px 0 8px; }
.sec-head .t { font-size: 12px; font-weight: 700; color: var(--brand-dark); text-transform: uppercase; letter-spacing: .04em; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 14px; }
.f label { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
.f .req { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 2px; background: var(--bad); color: var(--ink); }
.f .in.missing { border: 2px solid var(--bad); background: var(--bad-soft); }
.f .layer { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 2px; }
.f .layer.tech { background: var(--good-soft); color: var(--brand-dark); }
.f .layer.biz { background: var(--warn-soft); color: var(--warn-text); }
.f .ro { font-size: 13px; font-weight: 700; background: var(--mint); border: 1px solid var(--border); padding: 7px 10px; }
.f .in { width: 100%; font-family: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.actions { display: flex; gap: 10px; margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px; border: none; background: var(--brand); color: #fff; cursor: pointer; }
.btn:hover:not(:disabled) { background: var(--brand-dark); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.small { padding: 5px 10px; font-size: 11.5px; }

/* ===== 收集入口 ===== */
.card { border: 1px solid var(--border); background: var(--card); padding: 16px 18px; margin-bottom: 18px; }
.card .sec-head { display: flex; justify-content: space-between; align-items: baseline; margin: 0 0 10px; }
.small { font-size: 11px; }
.dl { color: var(--brand-dark); text-decoration: none; border-bottom: 1px dotted var(--brand); }
.dl:hover { color: var(--brand-dark); }
.dl.port { margin-right: 6px; }
.targets { width: 100%; min-height: 84px; font-family: ui-monospace, Consolas, monospace;
  font-size: 12px; line-height: 1.6; padding: 9px 11px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); resize: vertical; }
.targets:disabled { opacity: .6; }
.card .actions { display: flex; gap: 12px; align-items: center; margin-top: 12px; padding-top: 0; border-top: none; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px; }
.tile { border: 1px solid var(--border); background: var(--mint); padding: 10px 12px; }
.tile.ok { border-color: var(--good); }
.tile.warn { border-color: var(--warn); }
.tile .t-num { font-size: 22px; font-weight: 700; line-height: 1.1; }
.tile.ok .t-num { color: var(--brand-dark); }
.tile.warn .t-num { color: var(--warn-text); }
.tile .t-lbl { font-size: 11px; color: var(--muted); margin-top: 2px; }
.pill { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; white-space: nowrap; }
.pill.ok { background: var(--good-soft); color: var(--brand-dark); }
.pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
.pill.off { background: var(--mint-deep); color: var(--muted); }
.msg { font-size: 11.5px; color: var(--ink-soft); line-height: 1.5; min-width: 260px; }
.modal.wide { width: 620px; }
.steps { font-size: 12px; color: var(--ink-soft); line-height: 1.8; margin: 0 0 12px; padding-left: 20px; }
.steps code { font-family: ui-monospace, Consolas, monospace; color: var(--brand-dark); }
.cmdbox { width: 100%; height: 180px; font-family: ui-monospace, Consolas, monospace;
  font-size: 10px; line-height: 1.4; padding: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); border-radius: 6px; resize: vertical; }
.ahint { font-size: 10.5px; color: var(--muted); }
</style>
