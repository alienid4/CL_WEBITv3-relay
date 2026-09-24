<script setup lang="ts">
// 主機自我檢查（值班即時健檢，2026-09-21起）：驗證 WhatsUp Gold 告警是誤報還是真故障。
// 輸入一批 IP（或整段帶入、二次確認）→ 並發唯讀健檢 → 紅黃綠一覽 → 展開看單台
// 完整證據（照使用者 2026-09-22 的 4 維度：系統資源／業務服務／網路／安全變更）→ 匯出。
// 批次 1：免提權；log(dmesg/OOM/journal/爆破) 需 sudo＝批次 3。
// inode_pct／iowait_pct 可能是 null：**那代表這個平台沒有這個概念**（NTFS 沒有
// inode、Windows 沒有 iowait），不是 0。JS 的 `null >= 0` 會是 true，
// 直接拿去比大小會把「沒有這種東西」顯示成「0%，很好」。
interface Disk { mount: string; fs: string; size_kb: number; used_kb: number; avail_kb: number; use_pct: number; inode_pct: number | null; level: string }
interface Cpu { usage_pct: number; iowait_pct: number | null; ncpu: number; level: string }
interface Load { load1: number; load5: number; load15: number; ncpu: number; per_cpu: number; proc_total: number; level: string }
interface Mem { total_kb: number; avail_kb: number; used_pct: number; swap_total_kb: number; swap_pct: number; level: string }
interface Uptime { uptime_sec: number; days: number; just_rebooted: boolean; level: string }
interface Tcp { counts: Record<string, number>; estab: number; time_wait: number; close_wait: number; syn_recv: number; level: string }
interface Io { device: string; util: number; await: number | null; level: string }
interface Net { total_errs: number; total_drops: number; ifaces: { iface: string; errs: number; drops: number }[]; level: string }
interface Proc { pid: number; comm: string; cpu: number; mem: number }
interface Port { port: number; bind: string; process: string | null; service_guess: string | null; is_infra: number }
// 失敗服務的判讀。oneshot（timer 觸發的批次）跟常駐服務的 failed 意思完全不同，
// 畫面一定要分得出來——不然值班會對批次去按重啟，或把服務死掉當成批次沒跑而放著。
interface SshFails {
  fails: number; distinct_ips: number
  top_sources: { ip: string; count: number }[]
  level: string; note: string
}
interface KernelErrors {
  total: number; oom: number; io_error: number; samples: string[]; level: string
  // 已查證的開機告知：line ＋ 一句白話解釋。**兩個都要有**——
  // 只給 line 等於丟一句看不懂的話給值班自己害怕（使用者 2026-09-23）。
  notices?: { line: string; why: string }[]
  notice_note?: string | null
  // 還沒查證過的：照實說還沒判讀，不算錯誤也不消失。
  unreviewed?: string[]
  unreviewed_note?: string | null
}
interface FailedUnit {
  unit: string; type: string | null; result: string | null; exit_status: string | null
  triggered_by: string | null; oneshot: boolean
  verdict: string; reason_hint: string | null; last_active: string | null
  inspect: string[]; act: string[]
}
interface Result {
  ip: string; platform: string; reachable: boolean; error: string | null; unresolved: boolean
  os: string | null; kernel: string | null; uptime: Uptime | null
  cpu: Cpu | null; load: Load | null; mem: Mem | null
  disks: Disk[]; readonly_mounts: { mount: string; fs: string; dev: string }[]; failed_units: string[]
  failed_detail: FailedUnit[]
  checked_at: string | null; took_ms: number | null
  // 批次 3：**null ＝沒查到**（沒 sudo 授權或讀不到），不是「沒問題」。
  ssh_fails: SshFails | null; kernel_errors: KernelErrors | null
  sudo_log_authorized: boolean | null
  net: Net | null; tcp: Tcp | null; io: Io | null; zombies: number
  ntp_synced: boolean | null; dns_ok: boolean | null; logins: number | null; recent_logins: string[]
  top_cpu: Proc[]; top_mem: Proc[]; ports: Port[]; process_visible: boolean
  overall: string; notes: string[]
  // 「沒問題」「有問題」「還沒查完」是**三件事**，不可以擠進兩個顏色。
  // 沒查完不降級成黃——那是「把沒查到當成綠」的鏡像：假的警訊。
  // 八台 AIX 第一版只做得出部分維度就會全黃，值班點進去發現其實沒事，
  // 下次就不會再點了（使用者原話：「這資訊看了我會覺得可怕」）。
  complete?: boolean
  coverage?: Coverage | null
  // 有值＝**我們**根本沒去探測（沒憑證／沒金鑰／平台不支援），
  // 不是那台機器沒回應。主詞不同，值班的下一步也完全不同。
  not_probed?: string | null
  // 批次 2：跟最近基準比的方向性 diff（消失=紅、新增=提示）。null=尚無基準。
  diff: HealthDiff | null
}
interface Coverage {
  done: number; total: number
  // why **一定有值**：缺了卻不說為什麼，跟顯示 0 是同一種病。
  missing: { key: string; name: string; why: string }[]
  text: string
}
interface HealthDiff {
  baseline_at: string; baseline_source: string; level: string
  missing_ports: number[]; new_ports: number[]
  disk_jumps: { mount: string; from: number; to: number; delta: number }[]
}
interface Summary { total: number; red: number; yellow: number; green: number; skipped: number; unreachable: number; incomplete?: number; not_probed?: number }

const { apiFetch } = useApi()
const { showToast } = useToast()

// 指令做成「點一下複製」，**刻意不做成會執行的按鈕**。
// 按鈕會讓人跳過判斷，而這是金融業主機——系統不替人按重啟。
async function copyCmd(cmd: string) {
  try {
    await navigator.clipboard.writeText(cmd)
    showToast('已複製：' + cmd.slice(0, 60))
  } catch {
    showToast('複製失敗，請手動選取', 'error')
  }
}

// 清乾淨輸入：值班常從 WhatsUp Gold 告警或瀏覽器複製，會帶 http://、結尾 /、:port、user@。
// 把它剝成純主機／IP，不然整串拿去 SSH 會噴「could not resolve hostname」的假紅
//（使用者 2026-09-22：貼 http://YOUR_SERVER_IP/ 被說成當機）。
function cleanHost(s: string): string {
  let h = (s || '').trim()
  h = h.replace(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//, '')   // 剝 scheme：http:// https:// ssh://
  h = h.split('/')[0]                                    // 剝路徑／結尾 /
  h = h.split('@').pop() || h                            // 剝 user@
  h = h.replace(/:\d+$/, '')                             // 剝 :port（健檢一律走 SSH 22）
  return h.trim()
}

const ipsText = ref('')
const cidr = ref('')
const concurrency = ref(8)
const running = ref(false)
const results = ref<Result[]>([])
const summary = ref<Summary | null>(null)
const openIp = ref('')

const parsedIps = computed(() =>
  [...new Set(ipsText.value.split(/[\s,]+/).map(cleanHost).filter(Boolean))])

async function loadSegment() {
  const c = cidr.value.trim()
  if (!c) { showToast('先輸入網段（例：10.99.1.0/24）', 'warn'); return }
  try {
    const r = await apiFetch<{ count: number; items: { ip: string }[] }>(
      '/api/health-check/targets', { query: { cidr: c } })
    if (!r.count) { showToast('這段沒有已登記的 IP', 'warn'); return }
    if (!confirm(`這段有 ${r.count} 台已登記的主機，要全部帶入健檢清單嗎？`)) return
    ipsText.value = r.items.map((x) => x.ip).join('\n')
    showToast(`已帶入 ${r.count} 台`, 'success')
  } catch (e: any) {
    showToast(e?.data?.detail ?? '查詢網段失敗', 'error')
  }
}

async function run() {
  const ips = parsedIps.value
  if (!ips.length) { showToast('先輸入要檢查的 IP', 'warn'); return }
  running.value = true
  results.value = []
  summary.value = null
  try {
    const r = await apiFetch<{ items: Result[]; summary: Summary }>(
      '/api/health-check/run', { method: 'POST', body: { ips, concurrency: concurrency.value } })
    results.value = r.items
    summary.value = r.summary
    pushRecent(ips)                    // 記住這次查的 IP，下次可點選不用打
    if (!r.items.length) showToast('沒有結果', 'warn')
  } catch (e: any) {
    showToast(e?.data?.detail ?? '健檢失敗，請稍後再試', 'error')
  } finally {
    running.value = false
  }
}

// 批次 2：健康基準。平常每週六排程自動收；這顆給第一次鋪底／值班想立刻更新基準用。
// 基準是「正常時的參照」，事發時的即時健檢會跟最近一份比（方向性 diff）。
const baselining = ref(false)
async function baselineRun() {
  if (!confirm('立即對所有已納管主機收一份「健康基準」？平常每週六會自動收，這是手動補一份。')) return
  baselining.value = true
  try {
    const r = await apiFetch<{ targets: number; saved: number }>(
      '/api/health-check/baseline/run', { method: 'POST' })
    showToast(`已建立基準：${r.saved}/${r.targets} 台`, 'success')
  } catch (e: any) {
    showToast(e?.data?.detail ?? '建立基準失敗', 'error')
  } finally {
    baselining.value = false
  }
}

// 天條：每欄可排。要排的是算出來的值 → 先攤成平面欄位再交給 useSort。
// 第一層：點狀態方塊就只看那一類。值班要的是「哪幾台有問題」，
// 不是把數字看完再自己去表裡找。
const lightFilter = ref<string>('')
function pickLight(k: string) { lightFilter.value = lightFilter.value === k ? '' : k }
function clearFilters() {
  lightFilter.value = ''
  onlyIncomplete.value = false
  onlyNotProbed.value = false
}

const sevOrder: Record<string, number> = { red: 3, yellow: 2, green: 1, skipped: 0 }
const rows = computed(() => results.value.map((r) => ({
  ...r,
  sev: sevOrder[r.overall] ?? 0,
  diskPct: r.disks[0]?.use_pct ?? -1,
  // ?? -1：null 進 Math.max 會被當成 0，於是「沒有 inode 這回事」變成「inode 0%」。
  inodeMax: Math.max(-1, ...r.disks.map((d) => d.inode_pct ?? -1)),
  memPct: r.mem?.used_pct ?? -1,
  swapPct: r.mem?.swap_pct ?? -1,
  cpuPct: r.cpu?.usage_pct ?? -1,
  iowaitPct: r.cpu?.iowait_pct ?? -1,
  loadPer: r.load?.per_cpu ?? -1,
  failedN: r.failed_units.length,
  portN: r.reachable ? r.ports.length : -1,
  // 排序用：沒查完的排前面（1 在前），同樣完整度再比做了幾項。
  // **只算「連得到卻沒查完」**。連不上的那台，列上已經寫著「連不上」了，
  // 再標一次「未完整檢查 0/9」只是把同一件事說兩遍，把真正要看的那批稀釋掉。
  incomplete: r.reachable && r.complete === false && !r.not_probed ? 1 : 0,
  coverDone: r.coverage?.done ?? -1,
  coverTotal: r.coverage?.total ?? -1,
})))
// 「沒查完」是**跟燈號不同的一個維度**，所以另開一個篩選而不是塞進 lightFilter。
// 值班要能把「檢查完整而且綠」跟「沒查完」一眼分開——那是兩種完全不同的處置。
const onlyIncomplete = ref(false)
const onlyNotProbed = ref(false)
// 方塊上的數字**直接用後端的 summary.incomplete**，不自己再算一份。
// 2026-09-23 原本是前端自己算：後端那時把連不上的也算進去，兩邊差一個數。
// 正解不是各算各的（那就變成兩份定義），是**把後端的語意收斂成同一個**——
// 現在後端也只算「連得到卻沒查完」，兩邊指的是同一批機器。
// 前端的 r.incomplete 只拿來做篩選與排序，判斷條件跟後端那一行必須一致。
const shown = computed(() => {
  let out = lightFilter.value
    ? rows.value.filter((r) => r.overall === lightFilter.value)
    : rows.value
  if (onlyIncomplete.value) out = out.filter((r) => r.incomplete === 1)
  if (onlyNotProbed.value) out = out.filter((r) => !!r.not_probed)
  return out
})
const { sortKey, sortDir, toggle, sorted } = useSort(shown, 'sev', 'desc')

// 後端的 notes 用 **粗體** 的寫法，但這頁是純文字渲染——星號會原樣印出來給值班看。
// 刻意**剝掉**而不是轉成 <b>：notes 內含主機名與 SSH 錯誤原文，
// 用 v-html 等於替後端字串開一個注入口，為了一點排版不值得。
// 說明文字要看平台講話。**在 Windows 上講 systemd／sudo／iostat 不只是用詞不精確，
// 是給使用者錯誤指示**——照著做不會有任何效果，而且會讓人以為系統壞了。
const isWin = computed(() => sel.value?.platform === 'windows')

function plain(t: string) { return (t || '').replace(/\*\*/g, '') }

function gib(kb: number) { return (kb / 1024 / 1024).toFixed(1) }
const LIGHT: Record<string, string> = { red: '🔴 異常', yellow: '🟡 注意', green: '🟢 正常', skipped: '⏭ 跳過' }

// 最近查過的 IP（前 5 筆，可點選不用打）。存 localStorage：per-viewer 方便、不影響別人，
// 讀寫都包 try/catch（無痕視窗／封鎖 storage 也不能壞）。
const RECENT_KEY = 'healthcheck_recent_ips'
const recentIps = ref<string[]>([])
function loadRecent() {
  // 舊資料也 cleanHost 一遍：之前存過的 http://… 壞值在這裡就被洗乾淨
  try { recentIps.value = [...new Set((JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') as string[]).map(cleanHost).filter(Boolean))].slice(0, 5) }
  // localStorage 無痕/被封鎖/內容壞掉：沒有「最近查過」清單是正確的降級，不是要通報的錯誤
  catch { recentIps.value = [] }
}
function pushRecent(ips: string[]) {
  try {
    recentIps.value = [...new Set([...ips.map(cleanHost), ...recentIps.value])].filter(Boolean).slice(0, 5)
    localStorage.setItem(RECENT_KEY, JSON.stringify(recentIps.value))
  } catch { /* storage 不可用就算了，不影響檢查 */ }
}
function pickRecent(ip: string) {
  if (!parsedIps.value.includes(ip)) {
    ipsText.value = (ipsText.value.trim() ? ipsText.value.trim() + '\n' : '') + ip
  }
}
onMounted(loadRecent)

// port 分類（回應使用者：「內建服務 vs 外面建起來的服務」）：
//   系統/基礎＝is_infra（22 SSH／DNS／NTP／RPC…，OS 內建那類）
//   應用服務＝對照得到名字的（Grafana／Mongo／uvicorn…，多半是你部署的）
//   不明＝自訂 port 又沒 root 拿不到行程名——真名要 sudo ss -tlnp（批 3）
type PortT = { port: number; bind: string; process: string | null; service_guess: string | null; is_infra: number }
function portBuckets(ports: PortT[]) {
  return {
    infra: ports.filter((p) => p.is_infra),
    app: ports.filter((p) => !p.is_infra && p.service_guess),
    unknown: ports.filter((p) => !p.is_infra && !p.service_guess),
  }
}
function toggleOpen(ip: string) { openIp.value = openIp.value === ip ? '' : ip }

// 第三層＝右側抽屜。原本是 inline 展開：四個維度很高，一台展開後列表被推走，
// 看完一台要捲回去找下一台。抽屜讓**列表位置不動**，可以連續看好幾台。
const sel = computed(() => rows.value.find((x) => x.ip === openIp.value) || null)
function closeDrawer() { openIp.value = '' }
onMounted(() => {
  const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') closeDrawer() }
  window.addEventListener('keydown', esc)
  onUnmounted(() => window.removeEventListener('keydown', esc))
})

// 匯出證據：一台一列＋各面向關鍵值，UTF-8 BOM 讓 Excel 開繁中不亂碼
function exportCsv() {
  const head = ['掃描時間', 'IP', '判定', '連得上', 'OS', '開機天數', '剛重開',
    '最滿磁碟%', '最滿掛載', '最滿inode%', '記憶體%', 'swap%', 'CPU%', 'iowait%', '每核負載',
    '失敗服務', '唯讀重掛', 'CLOSE_WAIT', '磁碟util%', 'port數', '殭屍', 'NTP同步', '說明']
  const esc = (v: any) => { const s = String(v ?? ''); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s }
  const lines = [head.join(',')]
  for (const r of sorted.value) {
    lines.push([r.checked_at ?? '', r.ip, LIGHT[r.overall] ?? r.overall, r.reachable ? '是' : '否', r.os ?? '',
      r.uptime?.days ?? '', r.uptime?.just_rebooted ? '是' : '',
      r.disks[0]?.use_pct ?? '', r.disks[0]?.mount ?? '', r.inodeMax >= 0 ? r.inodeMax : '',
      r.mem?.used_pct ?? '', r.mem?.swap_pct ?? '', r.cpu?.usage_pct ?? '', r.cpu?.iowait_pct ?? '',
      r.load?.per_cpu ?? '', r.failed_units.join('｜'), r.readonly_mounts.map((m) => m.mount).join('｜'),
      r.tcp?.close_wait ?? '', r.io?.util ?? '', r.reachable ? r.ports.length : '', r.zombies,
      r.ntp_synced === null ? '' : (r.ntp_synced ? '是' : '否'), (r.error || plain(r.notes.join('；')))].map(esc).join(','))
  }
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `主機自我檢查_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div>
    <div class="section-divider">值班診斷</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>主機自我檢查</b>
      <InfoNote>值班收到告警（WhatsUp Gold 說某台當機／異常）時，輸入那台 IP 跑一輪，按維運排查標準一次掃過該看的面向，產一份可回報的證據。<br><br><b>檢查面向</b>：①系統資源（CPU 使用率/iowait、記憶體/swap、磁碟空間/inode、load、剛重開機）②業務服務（systemd failed、監聽 port/服務、殭屍）③網路（TCP 連線狀態、iostat 磁碟 I/O、介面丟包、DNS）④安全/變更（最近登入、NTP）。<br><br>唯讀檢查、不改設定；<b>連不上／唯讀重掛／有 failed 服務＝紅</b>。判定用絕對閾值不靠基準。<b>核心與系統日誌（dmesg OOM/I/O error、journalctl、爆破）需 root，在後續版本加。</b>只打指定的已登記主機、並發有上限，不是掃描。批次 1 只支援 Linux。</InfoNote>
    </div>

    <div class="card">
      <div class="in-row">
        <label class="in-lbl">要檢查的 IP<span class="hint">一行一個，或用逗號/空白分隔</span></label>
        <textarea v-model="ipsText" class="ips" rows="3" placeholder="10.99.1.10&#10;10.99.1.11"></textarea>
      </div>
      <div class="in-row rec" v-if="recentIps.length">
        <label class="in-lbl">最近查過<span class="hint">點一下加入，不用重打</span></label>
        <div class="recchips">
          <button v-for="ip in recentIps" :key="ip" type="button" class="recchip mono" @click="pickRecent(ip)">{{ ip }}</button>
        </div>
      </div>
      <div class="in-row seg">
        <label class="in-lbl">或整段帶入</label>
        <input v-model="cidr" class="cidr" placeholder="10.99.1.0/24" @keyup.enter="loadSegment" />
        <button class="btn ghost" type="button" @click="loadSegment">查這段已登記的</button>
      </div>
      <div class="in-row go">
        <label class="conc">並發 <input v-model.number="concurrency" type="number" min="1" max="16" /> 台
          <InfoNote>同時檢查幾台，上限 16。開太大對一群主機同時 SSH 會像掃描/暴力、可能觸發 SOC——預設 8 是刻意保守。</InfoNote>
        </label>
        <span class="pcount" v-if="parsedIps.length">已輸入 {{ parsedIps.length }} 台</span>
        <button class="btn" type="button" :disabled="running || !parsedIps.length" @click="run">
          {{ running ? '檢查中…' : '執行自我檢查' }}
        </button>
        <button class="btn ghost" type="button" :disabled="baselining" @click="baselineRun">
          {{ baselining ? '建立中…' : '建立健康基準' }}
          <InfoNote>對所有已納管主機收一份「正常時的參照」。平常每週六自動收（離峰）；這顆是手動補一份／第一次鋪底用。事發時的即時健檢會跟最近一份基準比：<b>少了 port/服務＝紅（可能掉了）、新增＝提示</b>。</InfoNote>
        </button>
      </div>
    </div>

    <p v-if="lightFilter || onlyIncomplete || onlyNotProbed" class="filter-note">
      目前只顯示「{{ [lightFilter ? (LIGHT[lightFilter] || lightFilter) : '', onlyIncomplete ? '未完整檢查' : '', onlyNotProbed ? '沒去成' : ''].filter(Boolean).join('＋') }}」{{ sorted.length }} 台（共 {{ rows.length }} 台）
      <button type="button" class="lnk" @click="clearFilters()">顯示全部</button>
      <span class="dim sm">・匯出 CSV 會照這個篩選匯出，不是全部</span>
    </p>
    <template v-if="summary">
      <div class="tiles">
        <button type="button" class="tile bad" :class="{ on: lightFilter === 'red' }" @click="pickLight('red')"><b>{{ summary.red }}</b><span>異常（含連不上 {{ summary.unreachable }}）</span></button>
        <button type="button" class="tile warn" :class="{ on: lightFilter === 'yellow' }" @click="pickLight('yellow')"><b>{{ summary.yellow }}</b><span>注意</span></button>
        <button type="button" class="tile good" :class="{ on: lightFilter === 'green' }" @click="pickLight('green')"><b>{{ summary.green }}</b><span>正常</span></button>
        <!-- 中性色（不是黃）：沒查完不是警訊，是「還不知道」。
             用警示色會把它跟真的有問題混在一起，值班就分不出該先看哪一邊。 -->
        <!-- 「沒去成」跟「異常」分開。前者的主詞是我們（沒有憑證／金鑰），
             後者是那台機器。混在一起，值班會跑去機房看一台好好的機器，
             而正確的下一步是走三步到設定頁補一個帳號。
             用中性色，因為它跟「未完整檢查」同一族：**那是我們的待辦，
             不是那台的狀態**。 -->
        <button v-if="summary.not_probed" type="button" class="tile plain"
                :class="{ on: onlyNotProbed }" @click="onlyNotProbed = !onlyNotProbed">
          <b>{{ summary.not_probed }}</b><span>沒去成（我們這邊）</span>
        </button>
        <button v-if="summary.incomplete" type="button" class="tile plain"
                :class="{ on: onlyIncomplete }" @click="onlyIncomplete = !onlyIncomplete">
          <b>{{ summary.incomplete }}</b><span>未完整檢查</span>
        </button>
        <button type="button" class="tile" :class="{ on: !lightFilter && !onlyIncomplete }" @click="clearFilters()"><b>{{ summary.total }}</b><span>全部<template v-if="summary.skipped">（跳過 {{ summary.skipped }}）</template></span></button>
        <button class="btn ghost exp" type="button" :disabled="!sorted.length" @click="exportCsv">⬇ 匯出證據 CSV</button>
      </div>

      <div class="card">
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <SortTh k="sev" :active="sortKey" :dir="sortDir" @sort="toggle">判定</SortTh>
                <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
                <SortTh k="diskPct" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">磁碟</SortTh>
                <SortTh k="memPct" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">記憶體</SortTh>
                <SortTh k="cpuPct" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">CPU</SortTh>
                <SortTh k="loadPer" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">負載/核</SortTh>
                <SortTh k="failedN" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">失敗服務</SortTh>
                <SortTh k="portN" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">port</SortTh>
                <SortTh k="incomplete" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">完整度</SortTh>
                <th>摘要</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <template v-for="r in sorted" :key="r.ip">
                <tr class="row" :class="{ on: openIp === r.ip }" @click="toggleOpen(r.ip)">
                  <td>
                    <span class="pill" :class="r.overall">{{ r.not_probed ? '⏭ 沒去成' : (LIGHT[r.overall] ?? r.overall) }}</span>
                    <!-- 燈號**不因為沒查完而改變**，只是旁邊多一句實話。 -->
                    <span v-if="r.incomplete === 1 && r.coverage" class="cover-tag">未完整檢查 {{ r.coverage.done }}/{{ r.coverage.total }}</span>
                  </td>
                  <td class="mono">{{ r.ip }}<span v-if="r.uptime?.just_rebooted" class="reboot" title="開機不到 10 分鐘">剛重開</span></td>
                  <td class="num">
                    <span v-if="r.disks.length" :class="'lv-' + r.disks[0].level">{{ r.disks[0].use_pct }}%</span>
                    <span v-else class="dim">—</span>
                    <span v-if="r.inodeMax >= 80" class="dim sm"> i{{ r.inodeMax }}%</span>
                  </td>
                  <td class="num"><span v-if="r.mem" :class="'lv-' + r.mem.level">{{ r.mem.used_pct }}%</span><span v-else class="dim">—</span><span v-if="r.mem && r.mem.swap_pct >= 50" class="dim sm"> sw{{ r.mem.swap_pct }}%</span></td>
                  <td class="num"><span v-if="r.cpu" :class="'lv-' + r.cpu.level">{{ r.cpu.usage_pct }}%</span><span v-else class="dim">—</span><span v-if="r.cpu && r.cpu.iowait_pct >= 25" class="dim sm"> io{{ r.cpu.iowait_pct }}%</span></td>
                  <td class="num"><span v-if="r.load" :class="'lv-' + r.load.level">{{ r.load.per_cpu }}</span><span v-else class="dim">—</span></td>
                  <td class="num"><span v-if="r.failed_units.length" class="lv-red">{{ r.failed_units.length }}</span><span v-else class="dim">0</span></td>
                  <td class="num">{{ r.reachable ? r.ports.length : '—' }}</td>
                  <!-- **顏色不動**：這一格只講「查了幾項」，不參與燈號。 -->
                  <td class="num">
                    <span v-if="!r.coverage" class="dim">—</span>
                    <span v-else-if="r.incomplete === 1" class="cover-warn"
                          :title="'缺：' + r.coverage.missing.map((m) => m.name).join('、') + '（點開看原因）'">
                      {{ r.coverage.done }}/{{ r.coverage.total }}
                    </span>
                    <span v-else class="dim">{{ r.coverage.done }}/{{ r.coverage.total }}</span>
                  </td>
                  <td class="sm">
                    <span v-if="!r.reachable" class="bad-txt">{{ r.error || '連不上' }}</span>
                    <span v-else-if="r.notes.length" class="dim">{{ plain(r.notes.join('；')) }}</span>
                    <span v-else class="dim">—</span>
                  </td>
                  <td class="caret">{{ openIp === r.ip ? '▾' : '▸' }}</td>
                </tr>
              </template>
              <!-- 篩選後 0 台不可以只留空白表格——那看起來像壞掉。
                   要講清楚是「篩掉了」還是「本來就沒有」。 -->
              <tr v-if="!sorted.length">
                <td colspan="11" class="dim empty-row">
                  <template v-if="lightFilter || onlyIncomplete || onlyNotProbed">
                    這次檢查的 {{ rows.length }} 台裡，沒有符合目前篩選的。
                    <button type="button" class="lnk" @click="clearFilters()">顯示全部</button>
                  </template>
                  <template v-else>還沒有檢查結果。上面輸入 IP 後按「開始健檢」。</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
    <!-- 第三層：右側抽屜。列表位置不動，可以連續看好幾台。 -->
    <div v-if="sel" class="drawer-mask" @click="closeDrawer" />
    <aside v-if="sel" class="drawer" role="dialog" aria-label="主機健檢明細">
      <header class="dhead">
        <span class="light" :class="'lv-' + sel.overall">{{ LIGHT[sel.overall] || sel.overall }}</span>
        <b class="mono">{{ sel.ip }}</b>
        <span v-if="sel.os" class="dim sm">{{ sel.os }}</span>
        <div class="spacer" />
        <button type="button" class="btn small ghost" @click="closeDrawer">關閉 (Esc)</button>
      </header>
      <div class="dbody">
                    <!-- 放在最上面：值班點進來第一件事要知道「這份結論有多完整」。
                         放在下面等於要人自己捲到底才發現有八項沒查。
                         **這一塊不影響燈號**——沒查完不是警訊，是還不知道。 -->
                    <div v-if="sel.coverage && sel.complete === false && sel.reachable" class="cover-box">
                      <b>未完整檢查 {{ sel.coverage.done }}/{{ sel.coverage.total }} 項</b>
                      <span class="dim sm">・燈號只反映<b>查到的那幾項</b>；沒查完不等於有問題，也不等於沒問題</span>
                      <ul class="cover-list">
                        <li v-for="m in sel.coverage.missing" :key="m.key">
                          <b>{{ m.name }}</b>：{{ m.why }}
                        </li>
                      </ul>
                    </div>
                    <p v-else-if="sel.coverage && sel.reachable" class="cover-ok dim sm">
                      完整檢查 {{ sel.coverage.done }}/{{ sel.coverage.total }} 項——這一輪該查的都查到了。
                    </p>
                    <div v-if="!sel.reachable" class="bad-txt big">
                      <template v-if="sel.unresolved">⚠ 主機名無法解析：{{ sel.error }}
                        <div class="dim sm">多半是輸入帶了 http://／打錯／DNS 查不到——<b>不是機器當機</b>。確認輸入的是純 IP 或正確主機名。</div>
                      </template>
                      <template v-else>✕ 連不上這台：{{ sel.error }}
                        <div class="dim sm">這通常就是告警屬實的證據——機器關機/當機，或整段被防火牆擋。</div>
                      </template>
                    </div>
                    <div v-else class="dims">
                      <!-- 掃描時間：使用者 2026-09-22「掃描時間要顯示出來」。
                           放最上面而不是角落——沒有時間的數字沒人敢用，
                           三分鐘前跟三天前的綠燈意義完全不同。 -->
                      <p class="scan-at">
                        掃描時間 <b>{{ sel.checked_at || '—' }}</b>
                        <span v-if="sel.took_ms !== null" class="dim sm">・耗時 {{ (sel.took_ms / 1000).toFixed(1) }} 秒</span>
                        <span class="dim sm">・這些數字是那個時點量到的，不是現在</span>
                      </p>

                      <!-- 批次 2：跟最近基準比（方向性：消失=紅、新增=提示）。 -->
                      <div v-if="sel.diff" class="basecmp" :class="'bc-' + sel.diff.level">
                        <b>跟基準比</b> <span class="dim sm">基準取自 {{ sel.diff.baseline_at }}（{{ sel.diff.baseline_source }}）</span>
                        <div v-if="sel.diff.missing_ports.length" class="bad-txt">🔴 少了監聽 port：{{ sel.diff.missing_ports.join('、') }}（服務可能掉了）</div>
                        <div v-if="sel.diff.new_ports.length" class="dim">＋ 新增 port：{{ sel.diff.new_ports.join('、') }}（多半是合法上線）</div>
                        <div v-if="sel.diff.disk_jumps.length" class="dim">磁碟漲幅：{{ sel.diff.disk_jumps.map(j => `${j.mount} ${j.from}→${j.to}%`).join('、') }}</div>
                        <div v-if="!sel.diff.missing_ports.length && !sel.diff.new_ports.length && !sel.diff.disk_jumps.length" class="dim">跟基準一致，沒有 port／服務消失</div>
                      </div>
                      <p v-else class="basecmp dim sm">尚無健康基準可比——每週六自動建立，或按上方「建立健康基準」先鋪一份。</p>

                      <!-- 維度 1：系統資源 -->
                      <section class="dim-sec">
                        <div class="dh">① 系統資源 <span class="dim sm">{{ sel.os }} · {{ sel.kernel }} · 開機 {{ sel.uptime?.days }} 天<template v-if="sel.uptime?.just_rebooted"> ⚠剛重開</template></span></div>
                        <div class="kvs">
                          <p class="kv"><span>CPU</span><b v-if="sel.cpu" :class="'lv-' + sel.cpu.level">{{ sel.cpu.usage_pct }}%</b><em v-if="sel.cpu" class="dim"><template v-if="sel.cpu.iowait_pct != null"> iowait {{ sel.cpu.iowait_pct }}%</template><template v-else> 這個平台沒有 iowait 這個指標</template>（{{ sel.cpu.ncpu }} 核）</em></p>
                          <p class="kv"><span>負載</span><b v-if="sel.load" :class="'lv-' + sel.load.level">{{ sel.load.per_cpu }}/核</b><em v-if="sel.load" class="dim"> load {{ sel.load.load1 }}/{{ sel.load.load5 }}/{{ sel.load.load15 }} · {{ sel.load.proc_total }} 支進程</em><em v-else class="dim">{{ isWin ? '這個平台沒有 load average 這個指標' : '沒有收到負載資料' }}</em></p>
                          <p class="kv"><span>記憶體</span><b v-if="sel.mem" :class="'lv-' + sel.mem.level">{{ sel.mem.used_pct }}%</b><em v-if="sel.mem" class="dim"> 用 {{ gib(sel.mem.total_kb - sel.mem.avail_kb) }}/{{ gib(sel.mem.total_kb) }}G<template v-if="sel.mem.swap_total_kb"> · swap {{ sel.mem.swap_pct }}%</template></em></p>
                        </div>
                        <div class="two">
                          <div>
                            <div class="th2">磁碟／掛載</div>
                            <table class="inner">
                              <thead><tr><th>掛載點</th><th class="num">空間</th><th class="num">inode</th><th class="num">已用/總</th></tr></thead>
                              <tbody>
                                <tr v-for="d in sel.disks" :key="d.mount">
                                  <td class="mono">{{ d.mount }}</td>
                                  <td class="num"><span :class="'lv-' + d.level">{{ d.use_pct }}%</span></td>
                                  <!-- null（這個平台沒有 inode）要顯示「—」而不是 0%。
                                       `null >= 0` 在 JS 是 true，所以一定要先判 null。 -->
                                  <td class="num"><span :class="(d.inode_pct ?? -1) >= 80 ? 'lv-red' : ''">{{ d.inode_pct == null ? '—' : d.inode_pct + '%' }}</span></td>
                                  <td class="num mono dim">{{ gib(d.used_kb) }}/{{ gib(d.size_kb) }}G</td>
                                </tr>
                                <tr v-if="!sel.disks.length"><td colspan="4" class="dim">沒收到磁碟資料</td></tr>
                              </tbody>
                            </table>
                            <p v-if="sel.readonly_mounts.length" class="ro-warn">⚠ 唯讀重掛（磁碟故障徵兆）：{{ sel.readonly_mounts.map(m => m.mount).join('、') }}</p>
                          </div>
                          <div>
                            <div class="th2">吃資源前 5（CPU｜記憶體）</div>
                            <table class="inner">
                              <thead><tr><th>行程</th><th class="num">CPU%</th><th class="num">MEM%</th></tr></thead>
                              <tbody>
                                <tr v-for="(p, i) in sel.top_cpu" :key="'c'+i"><td class="mono">{{ p.comm }}</td><td class="num">{{ p.cpu }}</td><td class="num dim">{{ p.mem }}</td></tr>
                              </tbody>
                            </table>
                          </div>
                        </div>
                      </section>
    
                      <!-- 維度 2：業務服務 -->
                      <section class="dim-sec">
                        <div class="dh">② 業務服務 <span class="dim sm">殭屍 {{ sel.zombies }} · 監聽 {{ sel.ports.length }} port<template v-if="!sel.process_visible"> · 行程名需 root</template></span></div>
                        <template v-if="sel.failed_detail?.length">
                          <p class="fail-warn">🔴 {{ isWin ? '設為自動啟動、卻沒在跑的服務' : 'systemd 失敗服務' }}（{{ sel.failed_detail.length }}）</p>
                          <div v-for="f in sel.failed_detail" :key="f.unit" class="fu">
                            <div class="fu-head">
                              <b class="mono">{{ f.unit }}</b>
                              <span class="tag" :class="f.oneshot ? 'batch' : 'daemon'">
                                {{ f.oneshot ? '批次型（timer 觸發）' : '常駐服務' }}
                              </span>
                              <span v-if="f.result" class="dim sm">Result={{ f.result }}<template v-if="f.exit_status"> · exit {{ f.exit_status }}</template></span>
                            </div>
                            <!-- 後端擋住 markdown 是治本，這層 plain() 是第二道：
                                 relay 與舊版後端仍可能送含星號的字串過來。 -->
                            <p class="fu-verdict">{{ plain(f.verdict) }}</p>
                            <p v-if="f.reason_hint" class="dim sm">失敗原因：{{ plain(f.reason_hint) }}</p>
    
                            <div class="cmds">
                              <div class="cmd-grp">
                                <div class="cmd-h ok">先查（唯讀，現在就可以跑）</div>
                                <code v-for="c in f.inspect" :key="c" class="cmd" @click="copyCmd(c)" :title="'點一下複製'">{{ c }}</code>
                              </div>
                              <div class="cmd-grp danger">
                                <div class="cmd-h warn">再處理 ⚠ 先看懂上面的輸出再做，這些會改變狀態</div>
                                <code v-for="c in f.act" :key="c" class="cmd act" @click="copyCmd(c)" :title="'點一下複製'">{{ c }}</code>
                              </div>
                            </div>
                          </div>
                        </template>
                        <p v-else-if="sel.failed_units.length" class="fail-warn">
                          🔴 {{ isWin ? '設為自動啟動、卻沒在跑的服務' : 'systemd 失敗服務' }}（{{ sel.failed_units.length }}）：{{ sel.failed_units.join('、') }}
                          <!-- 原本這句一律說「舊版收集的，重跑就會有」。Windows 根本沒有
                               逐項判讀這個東西，講「重跑一次就會有」是**給一個做了也沒用的
                               指示**——比不講更糟，人會照做然後以為系統壞了。 -->
                          <span class="dim sm">{{ isWin ? '（Windows 這一版只列出服務名稱，沒有逐項判讀）' : '（這份結果是舊版收集的，沒有判讀資料，重跑一次就會有）' }}</span>
                        </p>
                        <p v-else class="ok-line">✓ 沒有 failed 服務</p>
                        <template v-if="sel.ports.length">
                          <div class="pgrp">
                            <span class="pglbl">系統/基礎</span>
                            <template v-for="p in portBuckets(sel.ports).infra" :key="p.bind + ':' + p.port">
                              <span class="pchip infra">{{ p.port }}<i v-if="p.service_guess">{{ p.service_guess }}</i></span>
                            </template>
                            <span v-if="!portBuckets(sel.ports).infra.length" class="dim sm">無</span>
                          </div>
                          <div class="pgrp">
                            <span class="pglbl">應用服務</span>
                            <template v-for="p in portBuckets(sel.ports).app" :key="p.bind + ':' + p.port">
                              <span class="pchip app">{{ p.port }}<i>{{ p.service_guess }}</i></span>
                            </template>
                            <span v-if="!portBuckets(sel.ports).app.length" class="dim sm">無</span>
                          </div>
                          <div class="pgrp" v-if="portBuckets(sel.ports).unknown.length">
                            <span class="pglbl">不明<InfoNote>自訂 port，沒有 root 拿不到行程名所以認不出。要真名：批 3 的 <code>sudo ss -tlnp</code> 佈上去後這裡就會顯示實際服務。</InfoNote></span>
                            <span v-for="p in portBuckets(sel.ports).unknown" :key="p.bind + ':' + p.port" class="pchip">{{ p.port }}</span>
                          </div>
                        </template>
                        <div v-else class="ports"><span class="dim">沒收到監聽 port</span></div>
                      </section>
    
                      <!-- 維度 3：網路 -->
                      <section class="dim-sec">
                        <div class="dh">③ 網路</div>
                        <div class="kvs">
                          <p class="kv"><span>TCP</span><b v-if="sel.tcp" :class="'lv-' + sel.tcp.level">ESTAB {{ sel.tcp.estab }}</b><em v-if="sel.tcp" class="dim"> TIME_WAIT {{ sel.tcp.time_wait }} · CLOSE_WAIT {{ sel.tcp.close_wait }} · SYN_RECV {{ sel.tcp.syn_recv }}</em></p>
                          <p class="kv"><span>磁碟 I/O</span><b v-if="sel.io" :class="'lv-' + sel.io.level">{{ sel.io.util }}%</b><em v-if="sel.io" class="dim"> {{ sel.io.device }} await {{ sel.io.await }}ms</em><span v-else class="dim">{{ isWin ? '這個平台沒有收這個指標' : 'iostat 未裝／無資料' }}</span></p>
                          <p class="kv"><span>介面</span><b v-if="sel.net" :class="'lv-' + sel.net.level">errs {{ sel.net.total_errs }} · drops {{ sel.net.total_drops }}</b><em v-if="sel.net && sel.net.ifaces.length" class="dim"> {{ sel.net.ifaces.map(i => i.iface).join('、') }}</em></p>
                          <p class="kv"><span>DNS</span><b :class="sel.dns_ok === false ? 'lv-yellow' : ''">{{ sel.dns_ok === null ? '未測' : sel.dns_ok ? '解析正常' : '解析失敗' }}</b></p>
                        </div>
                      </section>
    
                      <!-- 維度 4：安全/變更 -->
                      <section class="dim-sec">
                        <div class="dh">④ 安全／變更 <span class="dim sm">目前登入 {{ sel.logins ?? '—' }} 人 · NTP {{ sel.ntp_synced === null ? '未知' : sel.ntp_synced ? '已同步' : '未同步' }}</span></div>
                        <div class="logins">
                          <div class="th2">最近登入</div>
                          <div v-for="(l, i) in sel.recent_logins" :key="i" class="login mono">{{ l }}</div>
                          <div v-if="!sel.recent_logins.length" class="dim sm">沒有最近登入紀錄</div>
                        </div>
                        <!-- SSH 爆破與核心錯誤：批次 3 已實作（sudo 窄白名單，唯讀）。
                             原本這裡寫「需 root，屬後續版本」——做好了就要把話改掉，
                             留著會讓人以為還沒做。 -->
                        <!-- Windows 上講 sudo／sudoers 是**給一個做了也沒用的指示**：
                             那台沒有 sudoers，而且登入失敗那一項是使用者 2026-09-23
                             拍板「不開這個權限」，重新納管一百次也不會變。
                             錯誤指示比不講更糟——人會照做，然後以為系統壞了。 -->
                        <div v-if="isWin" class="no-sudo">
                          ⚠ <b>登入失敗偵測沒有查</b>，不是「沒有異常」。
                          未授權讀取安全性記錄檔（2026-09-23 決定不開這個權限）。
                          <div class="dim sm">要做的話，收集帳號需加入各機的 Event Log Readers 群組（唯讀群組）；目前刻意不開。</div>
                        </div>
                        <div v-else-if="sel.sudo_log_authorized === false" class="no-sudo">
                          ⚠ <b>這兩項沒有查到</b>，不是「沒有異常」。
                          收集帳號還沒拿到 sudo 授權，所以 SSH 爆破與核心錯誤都是未知。
                          <div class="dim sm">納管腳本會佈 /etc/sudoers.d/webit3scan；重新納管一次即可開通。</div>
                        </div>
    
                        <template v-else>
                          <!-- sudo 開通了、登入紀錄卻讀不到：**這一列不可以就這樣消失**，
                               看起來會像系統根本沒有這個檢查項目。RHEL 系的紀錄在
                               /var/log/secure，Debian／Ubuntu 在 /var/log/auth.log，
                               兩個都試過還是讀不到就照實講——而且要講明「不是權限問題」，
                               否則人會再去重新納管一次，結果還是一樣。 -->
                          <p v-if="!sel.ssh_fails" class="kv">
                            <span>SSH 登入失敗</span>
                            <b class="dim">沒有查到</b>
                            <em class="dim">・/var/log/secure 與 /var/log/auth.log 兩個路徑都試過，
                              都讀不到。不是「沒有異常」，也不是權限沒開</em>
                          </p>
                          <p class="kv" v-if="sel.ssh_fails">
                            <span>SSH 登入失敗</span>
                            <b :class="'lv-' + sel.ssh_fails.level">{{ sel.ssh_fails.fails }} 次</b>
                            <em class="dim">・來源 {{ sel.ssh_fails.distinct_ips }} 個 IP・{{ sel.ssh_fails.note }}</em>
                          </p>
                          <div v-if="sel.ssh_fails?.top_sources.length" class="srcs">
                            <span v-for="t in sel.ssh_fails.top_sources" :key="t.ip" class="srcchip">
                              {{ t.ip }} <i>{{ t.count }} 次</i>
                            </span>
                          </div>
    
                          <p class="kv" v-if="sel.kernel_errors">
                            <span>核心錯誤</span>
                            <b :class="'lv-' + sel.kernel_errors.level">{{ sel.kernel_errors.total }} 筆</b>
                            <em class="dim">・OOM {{ sel.kernel_errors.oom }}・I/O 錯誤 {{ sel.kernel_errors.io_error }}</em>
                          </p>
                          <div v-if="sel.kernel_errors?.samples.length" class="ksamples">
                            <code v-for="(k, i) in sel.kernel_errors.samples" :key="i">{{ k }}</code>
                          </div>

                          <!-- 已查證的開機告知：**每一筆都附一句白話**。
                               使用者 2026-09-23：「會讓人本來沒事更害怕嗎」——
                               列一句看不懂的核心訊息而不解釋，看的人只能腦補最壞的。 -->
                          <template v-if="sel.kernel_errors?.notices?.length">
                            <p class="kv">
                              <span>開機時的告知</span>
                              <b class="lv-green">{{ sel.kernel_errors.notices.length }} 筆</b>
                              <em class="dim">・不影響燈號</em>
                            </p>
                            <p class="dim sm">{{ sel.kernel_errors.notice_note }}</p>
                            <div class="ksamples">
                              <div v-for="(n, i) in sel.kernel_errors.notices" :key="i" class="knote">
                                <code>{{ n.line }}</code>
                                <div class="dim sm">→ {{ n.why }}</div>
                              </div>
                            </div>
                          </template>

                          <!-- 還沒判讀過的：不裝沒事，也不嚇人。 -->
                          <template v-if="sel.kernel_errors?.unreviewed?.length">
                            <p class="kv">
                              <span>還沒判讀</span>
                              <b class="lv-green">{{ sel.kernel_errors.unreviewed.length }} 筆</b>
                            </p>
                            <p class="dim sm">{{ sel.kernel_errors.unreviewed_note }}</p>
                            <div class="ksamples">
                              <code v-for="(k, i) in sel.kernel_errors.unreviewed" :key="i">{{ k }}</code>
                            </div>
                          </template>
                        </template>
                      </section>
                    </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.section-divider { margin: 0 0 16px; font-size: 11px; color: var(--brand-dark); font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong); padding: 8px 14px; font-size: 12.5px; color: var(--ink-soft); display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
.breadcrumb-bar b { color: var(--brand-dark); }
.card { border: 1px solid var(--border); background: var(--card); padding: 16px; margin-bottom: 16px; border-radius: var(--radius, 14px); }
.in-row { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
.in-row.seg, .in-row.go { align-items: center; }
.in-lbl { font-size: 12.5px; color: var(--ink-soft); min-width: 92px; padding-top: 6px; }
.in-lbl .hint { display: block; font-size: 11px; color: var(--muted); }
.ips { flex: 1; font-family: ui-monospace, monospace; font-size: 12.5px; padding: 8px 10px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); resize: vertical; }
.cidr { font-family: ui-monospace, monospace; font-size: 12.5px; padding: 6px 10px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 180px; }
.go { justify-content: flex-start; gap: 16px; }
.conc { font-size: 12.5px; color: var(--ink-soft); display: inline-flex; align-items: center; gap: 4px; }
.conc input { width: 54px; padding: 4px 6px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.pcount { font-size: 12px; color: var(--muted); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 8px 18px; border: none; background: var(--brand); color: #fff; cursor: pointer; border-radius: 8px; }
.btn:hover:not(:disabled) { background: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.ghost:hover { border-color: var(--brand); color: var(--brand-dark); }
.tiles { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius, 14px); padding: 10px 18px; text-align: center; min-width: 96px; }
.tile b { display: block; font-size: 24px; font-weight: 700; color: var(--ink); font-family: var(--disp, monospace); }
.tile span { font-size: 11.5px; color: var(--muted); }
.tile.bad { border-color: var(--bad); } .tile.bad b { color: var(--bad); }
.tile.warn { border-color: var(--warn); } .tile.warn b { color: var(--warn-text); }
.tile.good { border-color: var(--brand); } .tile.good b { color: var(--brand-dark); }
.exp { margin-left: auto; }
.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); white-space: nowrap; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.mono { font-family: ui-monospace, monospace; }
.row { cursor: pointer; }
.row:hover td, .row.on td { background: var(--mint); }
.caret { color: var(--brand); width: 22px; }
.reboot { font-size: 10px; color: var(--warn-text); background: var(--warn-soft); padding: 0 5px; border-radius: 6px; margin-left: 6px; }
.pill { font-size: 11.5px; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
.pill.red { background: var(--bad-soft); color: var(--bad); }
.pill.yellow { background: var(--warn-soft); color: var(--warn-text); }
.pill.green { background: var(--mint); color: var(--brand-dark); }
.pill.skipped { background: var(--sub); color: var(--muted); }
.lv-red { color: var(--bad); font-weight: 700; }
.lv-yellow { color: var(--warn-text); font-weight: 700; }
.lv-green { color: var(--brand-dark); }
.dim { color: var(--muted); } .sm { font-size: 11px; }
.bad-txt { color: var(--bad); } .bad-txt.big { font-size: 13px; font-weight: 600; padding: 6px 0; }
.drill td { background: rgba(0,0,0,.02); }
.dims { display: flex; flex-direction: column; gap: 14px; padding: 4px 0; }
.dim-sec { border-left: 3px solid var(--brand); padding: 4px 0 4px 12px; }
.dh { font-size: 12.5px; font-weight: 700; color: var(--brand-dark); margin-bottom: 6px; }
.kvs { display: flex; flex-wrap: wrap; gap: 6px 22px; }
.kv { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; margin: 2px 0; }
.kv span:first-child { min-width: 56px; color: var(--ink-soft); }
.two { display: flex; gap: 22px; flex-wrap: wrap; margin-top: 8px; }
.two > div { flex: 1; min-width: 240px; }
.th2 { font-size: 11.5px; font-weight: 600; color: var(--ink-soft); margin: 4px 0; }
table.inner { font-size: 12px; }
table.inner th, table.inner td { padding: 4px 8px; }
.ro-warn { color: var(--bad); font-size: 12px; margin: 6px 0 0; font-weight: 600; }
.fail-warn { color: var(--bad); font-size: 12.5px; font-weight: 600; margin: 2px 0 6px; }
.ok-line { color: var(--brand-dark); font-size: 12px; margin: 2px 0 6px; }
.ports { display: flex; flex-wrap: wrap; gap: 6px; }
.pgrp { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 4px 0; }
.pglbl { font-size: 11px; color: var(--ink-soft); min-width: 62px; font-weight: 600; }
.pchip { font-size: 11.5px; padding: 2px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); }
.pchip.infra { color: var(--muted); border-style: dashed; }
.pchip.app { border-color: var(--brand); color: var(--brand-dark); }
.pchip i { font-style: normal; color: var(--muted); margin-left: 4px; font-size: 10.5px; }
.pchip.app i { color: var(--brand); }
.recchips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.recchip { font-size: 12px; padding: 3px 10px; border: 1px solid var(--border-strong); border-radius: 999px;
  background: var(--card); color: var(--brand-dark); cursor: pointer; }
.recchip:hover { border-color: var(--brand); background: var(--mint); }
.logins { margin: 4px 0; }
.login { font-size: 11.5px; color: var(--ink-soft); padding: 1px 0; }
.logins { margin: 4px 0; }
.login { font-size: 11.5px; color: var(--ink-soft); padding: 1px 0; }
.scan-at { margin: 0 0 10px; font-size: 12.5px; padding: 4px 10px;
           background: var(--sub); border-radius: 6px; display: inline-block; }
.fu { border: 1px solid var(--border-strong); border-radius: 8px;
      padding: 8px 12px; margin: 6px 0; background: var(--card); }
.fu-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.fu-verdict { margin: 4px 0; font-size: 12.5px; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; }
.tag.batch { background: var(--warn-soft); color: var(--warn-text); }
.tag.daemon { background: var(--danger-soft, rgba(180,35,24,.12)); color: var(--danger-text, #b42318); }
.cmds { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 6px; }
.cmd-grp { flex: 1; min-width: 280px; }
.cmd-grp.danger { border-left: 3px solid var(--warn-text); padding-left: 10px; border-radius: 0; }
.cmd-h { font-size: 11.5px; margin-bottom: 3px; }
.cmd-h.ok { color: var(--ok-text, #1a7f37); }
.cmd-h.warn { color: var(--warn-text, #b54708); font-weight: 600; }
.cmd { display: block; font-family: ui-monospace, monospace; font-size: 12px;
       padding: 3px 8px; margin: 2px 0; background: var(--sub); border-radius: 4px;
       cursor: pointer; white-space: pre-wrap; word-break: break-all; }
.cmd:hover { outline: 1px solid var(--border-strong); }
.cmd.act { background: var(--warn-soft); }
.tile { cursor: pointer; border: 1px solid transparent; font: inherit; text-align: left; }
.tile:hover { border-color: var(--border-strong); }
.tile.on { outline: 2px solid var(--brand); outline-offset: -2px; }
/* 「未完整」一律中性灰。**不可以用黃色**：那會跟「注意」混在一起，
   值班就分不出「有問題」與「還沒查完」——那正是這個功能要解決的事。 */
.cover-tag {
  display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 9px;
  font-size: 11px; color: var(--text-dim, #666);
  border: 1px solid var(--border, #d4d4d4); background: transparent; white-space: nowrap;
}
.cover-warn { color: var(--text-dim, #666); border-bottom: 1px dotted currentColor; cursor: help; }
.cover-box {
  margin: 0 0 12px; padding: 10px 12px; border-radius: 6px;
  border: 1px solid var(--border, #d4d4d4); background: var(--surface-2, #fafafa);
}
.cover-list { margin: 6px 0 0; padding-left: 18px; font-size: 12.5px; line-height: 1.7; }
.cover-ok { margin: 0 0 10px; }
.tile.plain b { color: var(--text-dim, #666); }

.filter-note { font-size: 12.5px; margin: 6px 0; padding: 4px 10px;
               background: var(--warn-soft); border-radius: 6px; display: inline-block; }
.filter-note .lnk { background: none; border: none; color: var(--brand);
                    cursor: pointer; font: inherit; text-decoration: underline; padding: 0 4px; }
.no-sudo { background: var(--warn-soft); color: var(--warn-text);
           padding: 8px 12px; border-radius: 6px; font-size: 12.5px; }
.srcs { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 8px; }
.srcchip { font-size: 11.5px; padding: 1px 8px; border-radius: 10px;
           background: var(--sub); font-family: ui-monospace, monospace; }
.srcchip i { color: var(--muted); font-style: normal; margin-left: 4px; }
.ksamples code { display: block; font-size: 11.5px; padding: 2px 8px; margin: 2px 0;
                 background: var(--sub); border-radius: 4px; word-break: break-all; }
/* 開機告知：解釋緊貼在那一行底下，不要讓人還要自己對應哪句配哪句 */
.knote { margin: 6px 0; }
.knote .dim { padding: 2px 8px 0; line-height: 1.6; }
.basecmp { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; margin: 8px 0 12px;
  font-size: 12.5px; line-height: 1.7; background: var(--sub); }
.basecmp.bc-red { border-color: var(--bad); background: var(--bad-soft); }
.basecmp > div { margin-top: 2px; }
.drawer-mask { position: fixed; inset: 0; background: rgba(0,0,0,.18); z-index: 40; }
.drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(980px, 82vw);
          background: var(--card); border-left: 1px solid var(--border-strong);
          z-index: 41; display: flex; flex-direction: column;
          box-shadow: -2px 0 12px rgba(0,0,0,.12); }
.dhead { display: flex; align-items: center; gap: 10px; padding: 10px 16px;
         border-bottom: 1px solid var(--border); flex: 0 0 auto; }
.dbody { overflow: auto; padding: 14px 16px; }
</style>
