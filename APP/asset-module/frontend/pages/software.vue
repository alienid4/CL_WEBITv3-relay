<script setup lang="ts">
// 軟體盤點（2026-09-11）——「哪幾台還在跑舊版 OpenSSL」「誰裝了 7-Zip」。
//
// 三個分頁回答三個問題：
//   軟體總覽＝這個軟體有幾個版本、各幾台（點下去看是哪幾台）
//   依主機  ＝這台裝了什麼、資料是哪天收的（看得出哪台舊了）
//   異動紀錄＝哪天多了、少了什麼（每台第一次盤點不算異動）
// 表格一律可排序＋數字可下鑽（鐵規則）。
interface SwRow {
  name: string; version_count: number; host_count: number
  versions: string[]; sources: string[]
}
interface VerRow { version: string; host_count: number; archs: string | null }
interface HostRow {
  ip: string; asset_serial: string | null; hostname: string | null; version: string
  arch: string; installed_at: string | null; first_seen: string | null; last_seen: string | null
  source: string
}
interface ByHost {
  ip: string; asset_serial: string | null; hostname: string | null; package_count: number
  last_seen: string | null; first_seen: string | null; sources: string | null
}
interface Pkg {
  id: number; name: string; version: string; arch: string; vendor: string | null
  installed_at: string | null; first_seen: string | null; last_seen: string | null; gone_at: string | null
}
interface Change {
  id: number; ip: string; hostname: string | null; name: string; version: string
  arch: string; change: 'added' | 'removed'; detected_at: string
}
interface Summary {
  hosts: number; live: number; names: number; changes_30d: number
  last_run: {
    started_at: string; status: string; trigger: string; host_count: number | null
    ok_count: number | null; failed_count: number | null; unsupported_count: number | null
    error: string | null
  } | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const tab = ref<'overview' | 'hosts' | 'changes'>('overview')
const summary = ref<Summary | null>(null)
const items = ref<SwRow[]>([])
const byHost = ref<ByHost[]>([])
const changes = ref<Change[]>([])
const keyword = ref('')
const loading = ref(false)
const errorMessage = ref('')

const SOURCE_LABEL: Record<string, string> = { ssh_rpm: 'Linux rpm', winrm_registry: 'Windows（未驗證）' }

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [a, b, c] = await Promise.all([
      apiFetch<{ items: SwRow[]; summary: Summary }>('/api/software/packages'),
      apiFetch<{ items: ByHost[] }>('/api/software/by-host'),
      apiFetch<{ items: Change[] }>('/api/software/changes', { query: { days: 90 } }),
    ])
    items.value = a.items
    summary.value = a.summary
    byHost.value = b.items
    changes.value = c.items
  } catch {
    errorMessage.value = '軟體清單載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
onMounted(load)

// 關鍵字：總覽比對軟體名稱；依主機比對主機名／IP；異動比對軟體或主機
const kw = computed(() => keyword.value.trim().toLowerCase())
const has = (...v: (string | null | undefined)[]) => v.some((x) => (x || '').toLowerCase().includes(kw.value))
const fItems = computed(() => (kw.value ? items.value.filter((r) => has(r.name)) : items.value))
const fHosts = computed(() => (kw.value ? byHost.value.filter((r) => has(r.hostname, r.ip)) : byHost.value))
const fChanges = computed(() => (kw.value ? changes.value.filter((r) => has(r.name, r.hostname, r.ip)) : changes.value))

const { sortKey: oKey, sortDir: oDir, toggle: oToggle, sorted: oSorted } = useSort(fItems, 'host_count')
const { sortKey: hKey, sortDir: hDir, toggle: hToggle, sorted: hSorted } = useSort(fHosts, 'hostname')
const { sortKey: cKey, sortDir: cDir, toggle: cToggle, sorted: cSorted } = useSort(fChanges, 'detected_at')

// ---- 下鑽：軟體 → 各版本 → 主機 ----
const drillName = ref('')
const drillVersion = ref<string | null>(null)
const versions = ref<VerRow[]>([])
const drillHosts = ref<HostRow[]>([])
const { sortKey: vKey, sortDir: vDir, toggle: vToggle, sorted: vSorted } = useSort(versions, 'host_count')
const { sortKey: dKey, sortDir: dDir, toggle: dToggle, sorted: dSorted } = useSort(drillHosts, 'hostname')

async function openSoftware(name: string, version: string | null = null) {
  drillName.value = name
  drillVersion.value = version
  const [v, h] = await Promise.all([
    apiFetch<{ items: VerRow[] }>('/api/software/versions', { query: { name } }),
    apiFetch<{ items: HostRow[] }>('/api/software/hosts',
      { query: { name, version: version ?? undefined } }),
  ])
  versions.value = v.items
  drillHosts.value = h.items
}
async function pickVersion(version: string | null) {
  await openSoftware(drillName.value, version)
}
function closeDrill() { drillName.value = ''; drillVersion.value = null }

// ---- 下鑽：主機 → 這台裝了什麼 ----
const hostIp = ref('')
const hostLabel = ref('')
const hostPkgs = ref<Pkg[]>([])
const { sortKey: pKey, sortDir: pDir, toggle: pToggle, sorted: pSorted } = useSort(hostPkgs, 'name')
async function openHost(r: { ip: string; hostname: string | null }) {
  hostIp.value = r.ip
  hostLabel.value = r.hostname || r.ip
  hostPkgs.value = (await apiFetch<{ items: Pkg[] }>('/api/software/host', { query: { ip: r.ip } })).items
}

// ---- 採集：會真的連出去，三態回饋 ----
const collecting = ref(false)
async function collect() {
  if (collecting.value) return
  collecting.value = true
  showToast('開始軟體盤點，連線中…', 'info')
  try {
    const r = await apiFetch<any>('/api/software/collect', { method: 'POST' })
    const baseline = r.hosts.filter((h: any) => h.baseline).length
    const added = r.hosts.reduce((n: number, h: any) => n + (h.added || 0), 0)
    const removed = r.hosts.reduce((n: number, h: any) => n + (h.removed || 0), 0)
    if (r.candidates === 0) {
      showToast('沒有已納管的主機可收——先到「納入管理」把主機納管', 'warn')
    } else {
      const parts = [`收到 ${r.hosts.length}／${r.candidates} 台、${r.packages} 個套件`]
      if (baseline) parts.push(`${baseline} 台是第一次盤點（不算異動）`)
      if (added || removed) parts.push(`新裝 ${added}、移除 ${removed}`)
      if (r.unsupported.length) parts.push(`${r.unsupported.length} 台不支援`)
      if (r.failed.length) parts.push(`${r.failed.length} 台失敗`)
      showToast(parts.join('；'), r.failed.length ? 'warn' : 'success', 10000)
    }
    await load()
  } catch (e: any) {
    showToast(`軟體盤點失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  } finally {
    collecting.value = false
  }
}
</script>

<template>
  <div>
    <div class="section-divider">軟體盤點</div>

    <p class="lead">每台主機裝了哪些軟體、各是什麼版本
      <InfoNote><b>Linux</b>：<code>rpm -qa</code>（一般權限就讀得到，不需要 sudo）。<br><b>Windows</b>：「程式和功能」清單（登錄檔 Uninstall 機碼，走 WinRM）——<b>還沒在真機驗證過</b>。<br><br><b>看不到的</b>：手動解壓縮的、pip／npm 裝的、容器裡的、Windows 只裝給單一使用者的、免安裝版（例如綠色版 7-Zip）。清單上沒有≠沒裝。<br>Debian／Ubuntu 沒有 rpm，會標「不支援」，不是「沒裝軟體」。<br><br><b>異動紀錄從每台第一次盤點之後才開始</b>：第一次收到的全部算「本來就在」，那之前裝過、移除過什麼，系統不知道。</InfoNote>
    </p>

    <!-- 資料新鮮度放最上面：稽核要先知道這份清單是哪天的 -->
    <p v-if="summary?.last_run" class="last-run">
      最新盤點：<b>{{ summary.last_run.started_at }}</b>
      <span class="lr-meta">（{{ summary.last_run.trigger === 'schedule' ? '每晚排程' : '手動' }}，
        收到 {{ summary.last_run.ok_count ?? 0 }}／{{ summary.last_run.host_count ?? 0 }} 台<template v-if="summary.last_run.unsupported_count">，{{ summary.last_run.unsupported_count }} 台不支援</template><template v-if="summary.last_run.failed_count">，<span class="bad">{{ summary.last_run.failed_count }} 台失敗</span></template>）</span>
    </p>
    <p v-else-if="summary" class="last-run">最新盤點：尚未盤點</p>

    <div v-if="summary" class="tiles">
      <div class="tile"><div class="t-num mono">{{ summary.hosts }}</div><div class="t-lbl">有軟體清單的主機</div></div>
      <div class="tile"><div class="t-num mono">{{ summary.names }}</div><div class="t-lbl">軟體種類</div></div>
      <div class="tile"><div class="t-num mono">{{ summary.live }}</div><div class="t-lbl">安裝筆數（主機×套件）</div></div>
      <div class="tile"><div class="t-num mono">{{ summary.changes_30d }}</div><div class="t-lbl">近 30 天異動</div></div>
    </div>

    <div class="bar">
      <button class="btn primary" type="button" :disabled="collecting" @click="collect">
        {{ collecting ? '盤點中…' : '立即盤點' }}
      </button>
      <div class="tabs">
        <div class="tab" :class="{ active: tab === 'overview' }" @click="tab = 'overview'">軟體總覽 {{ items.length }}</div>
        <div class="tab" :class="{ active: tab === 'hosts' }" @click="tab = 'hosts'">依主機 {{ byHost.length }}</div>
        <div class="tab" :class="{ active: tab === 'changes' }" @click="tab = 'changes'">異動紀錄 {{ changes.length }}</div>
      </div>
      <div class="spacer" />
      <input v-model="keyword" class="kw" type="search"
             :placeholder="tab === 'hosts' ? '搜尋主機／IP' : '搜尋軟體，例如 openssh、openssl、7-zip'" />
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <p v-else-if="summary && !summary.last_run" class="muted">還沒盤點過。按「立即盤點」對已納管的主機收一輪。</p>

    <!-- 軟體總覽 -->
    <template v-else-if="tab === 'overview'">
      <div class="card">
        <div class="tbl-wrap">
          <table class="tbl">
            <thead><tr>
              <SortTh k="name" :active="oKey" :dir="oDir" @sort="oToggle">軟體</SortTh>
              <SortTh k="version_count" :active="oKey" :dir="oDir" class="num" @sort="oToggle">版本數</SortTh>
              <SortTh k="host_count" :active="oKey" :dir="oDir" class="num" @sort="oToggle">台數</SortTh>
              <th>版本</th>
            </tr></thead>
            <tbody>
              <tr v-for="r in oSorted" :key="r.name" :class="{ sel: r.name === drillName }">
                <td><a class="dl" @click="openSoftware(r.name)">{{ r.name }}</a>
                  <span v-if="r.sources.includes('winrm_registry')" class="tag">Windows</span></td>
                <td class="num mono"><a class="dl" @click="openSoftware(r.name)">{{ r.version_count }}</a></td>
                <td class="num mono"><a class="dl" @click="openSoftware(r.name)">{{ r.host_count }}</a></td>
                <td class="vers">
                  <a v-for="v in r.versions.slice(0, 4)" :key="v" class="chip mono" @click="openSoftware(r.name, v)">{{ v || '（無版本）' }}</a>
                  <span v-if="r.versions.length > 4" class="dim">…等 {{ r.versions.length }} 個</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="drillName" class="card">
        <div class="card-head">
          <b>{{ drillName }}</b>
          <span class="dim">{{ drillVersion === null ? '全部版本' : `版本 ${drillVersion || '（無版本）'}` }}</span>
          <div class="spacer" />
          <button v-if="drillVersion !== null" class="btn small" type="button" @click="pickVersion(null)">看全部版本</button>
          <button class="btn small" type="button" @click="closeDrill">關閉</button>
        </div>
        <div class="split">
          <div class="tbl-wrap">
            <table class="tbl">
              <thead><tr>
                <SortTh k="version" :active="vKey" :dir="vDir" @sort="vToggle">版本</SortTh>
                <SortTh k="archs" :active="vKey" :dir="vDir" @sort="vToggle">架構</SortTh>
                <SortTh k="host_count" :active="vKey" :dir="vDir" class="num" @sort="vToggle">台數</SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="v in vSorted" :key="v.version" :class="{ sel: v.version === drillVersion }">
                  <td class="mono">{{ v.version || '（無版本）' }}</td>
                  <td class="dim">{{ v.archs }}</td>
                  <td class="num mono"><a class="dl" @click="pickVersion(v.version)">{{ v.host_count }}</a></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="tbl-wrap">
            <table class="tbl">
              <thead><tr>
                <SortTh k="hostname" :active="dKey" :dir="dDir" @sort="dToggle">主機</SortTh>
                <SortTh k="ip" :active="dKey" :dir="dDir" @sort="dToggle">IP</SortTh>
                <SortTh k="version" :active="dKey" :dir="dDir" @sort="dToggle">版本</SortTh>
                <SortTh k="installed_at" :active="dKey" :dir="dDir" @sort="dToggle">安裝時間</SortTh>
                <SortTh k="last_seen" :active="dKey" :dir="dDir" @sort="dToggle">最後看到</SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="h in dSorted" :key="h.ip + h.version + h.arch">
                  <td>
                    <NuxtLink v-if="h.asset_serial" class="dl" :to="`/assets/${h.asset_serial}`">{{ h.hostname || h.asset_serial }}</NuxtLink>
                    <span v-else class="dim">（未登記）</span>
                  </td>
                  <td class="mono">{{ h.ip }}</td>
                  <td class="mono">{{ h.version || '—' }}</td>
                  <td class="mono dim">{{ h.installed_at || '不知道' }}</td>
                  <td class="mono dim">{{ h.last_seen }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </template>

    <!-- 依主機 -->
    <template v-else-if="tab === 'hosts'">
      <div class="card">
        <div class="tbl-wrap">
          <table class="tbl">
            <thead><tr>
              <SortTh k="hostname" :active="hKey" :dir="hDir" @sort="hToggle">主機</SortTh>
              <SortTh k="ip" :active="hKey" :dir="hDir" @sort="hToggle">IP</SortTh>
              <SortTh k="package_count" :active="hKey" :dir="hDir" class="num" @sort="hToggle">套件數</SortTh>
              <SortTh k="last_seen" :active="hKey" :dir="hDir" @sort="hToggle">最後收到</SortTh>
              <SortTh k="first_seen" :active="hKey" :dir="hDir" @sort="hToggle">第一次盤點</SortTh>
              <SortTh k="sources" :active="hKey" :dir="hDir" @sort="hToggle">來源</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="r in hSorted" :key="r.ip" :class="{ sel: r.ip === hostIp }">
                <td>
                  <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">{{ r.hostname || r.asset_serial }}</NuxtLink>
                  <span v-else class="dim">（未登記）</span>
                </td>
                <td class="mono">{{ r.ip }}</td>
                <td class="num mono"><a class="dl" @click="openHost(r)">{{ r.package_count }}</a></td>
                <td class="mono dim">{{ r.last_seen }}</td>
                <td class="mono dim">{{ r.first_seen }}</td>
                <td class="dim">{{ (r.sources || '').split(',').map((s) => SOURCE_LABEL[s] || s).join('、') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="hostIp" class="card">
        <div class="card-head">
          <b>{{ hostLabel }}</b><span class="dim mono">{{ hostIp }}</span>
          <span class="dim">目前 {{ hostPkgs.length }} 個套件</span>
          <div class="spacer" />
          <button class="btn small" type="button" @click="hostIp = ''">關閉</button>
        </div>
        <div class="tbl-wrap">
          <table class="tbl">
            <thead><tr>
              <SortTh k="name" :active="pKey" :dir="pDir" @sort="pToggle">軟體</SortTh>
              <SortTh k="version" :active="pKey" :dir="pDir" @sort="pToggle">版本</SortTh>
              <SortTh k="arch" :active="pKey" :dir="pDir" @sort="pToggle">架構</SortTh>
              <SortTh k="vendor" :active="pKey" :dir="pDir" @sort="pToggle">廠商</SortTh>
              <SortTh k="installed_at" :active="pKey" :dir="pDir" @sort="pToggle">安裝時間</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="p in pSorted" :key="p.id">
                <td><a class="dl" @click="tab = 'overview'; openSoftware(p.name)">{{ p.name }}</a></td>
                <td class="mono">{{ p.version || '—' }}</td>
                <td class="dim">{{ p.arch || '—' }}</td>
                <td class="dim">{{ p.vendor || '—' }}</td>
                <td class="mono dim">{{ p.installed_at || '不知道' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- 異動紀錄 -->
    <template v-else>
      <div class="card">
        <p class="note">近 90 天。每台主機<b>第一次盤點</b>收到的套件算「本來就在」，不列在這裡。「時間」是哪一輪盤點發現的，不是實際安裝的時間。</p>
        <p v-if="!cSorted.length" class="muted">這段期間沒有異動。</p>
        <div v-else class="tbl-wrap">
          <table class="tbl">
            <thead><tr>
              <SortTh k="detected_at" :active="cKey" :dir="cDir" @sort="cToggle">發現時間</SortTh>
              <SortTh k="hostname" :active="cKey" :dir="cDir" @sort="cToggle">主機</SortTh>
              <SortTh k="ip" :active="cKey" :dir="cDir" @sort="cToggle">IP</SortTh>
              <SortTh k="name" :active="cKey" :dir="cDir" @sort="cToggle">軟體</SortTh>
              <SortTh k="version" :active="cKey" :dir="cDir" @sort="cToggle">版本</SortTh>
              <SortTh k="change" :active="cKey" :dir="cDir" @sort="cToggle">異動</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="c in cSorted" :key="c.id">
                <td class="mono dim">{{ c.detected_at }}</td>
                <td>{{ c.hostname || '（未登記）' }}</td>
                <td class="mono">{{ c.ip }}</td>
                <td><a class="dl" @click="tab = 'overview'; openSoftware(c.name)">{{ c.name }}</a></td>
                <td class="mono">{{ c.version || '—' }}</td>
                <td><span class="pill" :class="c.change">{{ c.change === 'added' ? '新裝' : '移除' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lead { color: var(--muted); margin: 0 0 12px; line-height: 1.7; }
.last-run { margin: 0 0 14px; font-size: 13px; color: var(--ink); }
.lr-meta { color: var(--ink-soft); }
.bad { color: var(--bad); }
.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; }
.t-num { font-size: 26px; font-weight: 700; color: var(--brand-dark); line-height: 1.1; }
.t-lbl { font-size: 12px; color: var(--muted); margin-top: 4px; }

.bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.spacer { flex: 1; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft);
  border-radius: 999px; padding: 6px 16px; font-size: 13px; cursor: pointer; }
.tab:hover { border-color: var(--border-strong); }
.tab.active { border-color: var(--brand); background: var(--mint); color: var(--ink); font-weight: 600; }
.btn { border-radius: 9px; border: 1px solid var(--border); background: transparent; color: inherit;
  padding: 7px 14px; cursor: pointer; font-size: 13px; }
.btn.primary { background: var(--brand); border-color: transparent; color: #fff; font-weight: 600; }
.btn.small { padding: 3px 10px; font-size: 12px; }
.btn:disabled { opacity: .55; cursor: progress; }
.kw { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 9px; padding: 6px 10px; font-size: 13px; min-width: 280px; }

.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 12px 14px; margin-bottom: 16px; }
.card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 14px; }
.split { display: grid; grid-template-columns: minmax(260px, 1fr) 2fr; gap: 16px; align-items: start; }
@media (max-width: 900px) { .split { grid-template-columns: 1fr; } }
.tbl-wrap { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { padding: 7px 10px; border-bottom: 1px solid var(--border); text-align: left; }
.tbl .num { text-align: right; }
.tbl tr.sel td { background: var(--mint); }
.dl { cursor: pointer; color: var(--link); }
.vers { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: 11.5px; padding: 1px 7px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; color: var(--ink-soft); }
.chip:hover { border-color: var(--brand); color: var(--ink); }
.tag { font-size: 10px; padding: 1px 6px; border-radius: 6px; margin-left: 6px;
  border: 1px solid var(--border); color: var(--muted); }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
.pill.added { background: rgba(0,145,66,.16); color: var(--brand-dark); }
.pill.removed { background: rgba(224,108,108,.16); color: var(--bad); }
.note { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
.dim { color: var(--muted); }
</style>
