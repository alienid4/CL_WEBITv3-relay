<script setup lang="ts">
// 4.1「批次健檢」頁籤（V34，2.048.21 起 V2 原生；取代嵌入的舊頁 pages/health-check.vue）。⚠D2：會到目標機跑唯讀檢查。同一批 API，行為不變：
// GET /api/health-check/targets?cidr（整段帶入前先問一次）、POST /api/health-check/run（並發唯讀健檢；管理員，後端 ADMIN_WRITE）、
// POST /api/health-check/baseline/run（手動補一份健康基準，先確認；管理員，後端 ADMIN_WRITE）。
// 只打指定的已登記主機、並發上限 16（預設 8，開太大像掃描、可能觸發 SOC）；結果只在這次畫面上（POST 回應，不落地），所以篩選不放網址。
// 「沒問題／有問題／還沒查完／沒去成」是四件事：沒查完不降級成黃、沒去成（我們沒憑證）跟異常（那台機器）分開。方塊數字直接用後端 summary，不自己再算。
// 單台明細＝右側抽屜（V2HealthDetail，從舊頁原樣搬來），列表位置不動，可以連續看好幾台。
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

// 清乾淨輸入：值班常從 WhatsUp Gold 告警或瀏覽器複製，會帶 http://、結尾 /、:port、user@（不剝掉會變成「could not resolve hostname」的假紅）
function cleanHost(s: string): string {
  let h = (s || '').trim()
  h = h.replace(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//, '')
  h = h.split('/')[0]
  h = h.split('@').pop() || h
  h = h.replace(/:\d+$/, '')
  return h.trim()
}
const ipsText = ref('')
const cidr = ref('')
const concurrency = ref(8)
const running = ref(false)
const results = ref<any[]>([])
const summary = ref<any>(null)
const parsedIps = computed(() => [...new Set(ipsText.value.split(/[\s,]+/).map(cleanHost).filter(Boolean))])

async function loadSegment() {
  const c = cidr.value.trim()
  if (!c) { showToast('先輸入網段（例：10.99.1.0/24）', 'warn'); return }
  try {
    const r = await apiFetch<any>('/api/health-check/targets', { query: { cidr: c } })
    if (!r.count) { showToast('這段沒有已登記的 IP', 'warn'); return }
    if (!window.confirm(`這段有 ${r.count} 台已登記的主機，要全部帶入健檢清單嗎？`)) return
    ipsText.value = r.items.map((x: any) => x.ip).join('\n')
    showToast(`已帶入 ${r.count} 台`, 'success')
  } catch (e: any) { showToast(`查詢網段失敗：${errMsg(e)}`, 'error') }
}
async function run() {
  const ips = parsedIps.value
  if (!ips.length) { showToast('先輸入要檢查的 IP', 'warn'); return }
  running.value = true; results.value = []; summary.value = null; openIp.value = ''; clearFilters()
  try {
    const r = await apiFetch<any>('/api/health-check/run', { method: 'POST', body: { ips, concurrency: concurrency.value } })
    results.value = r.items ?? []; summary.value = r.summary ?? null
    pushRecent(ips)
    if (!results.value.length) showToast('沒有結果', 'warn')
  } catch (e: any) { showToast(`健檢失敗：${errMsg(e)}`, 'error') } finally { running.value = false }
}
// 健康基準：平常每週六排程自動收；這顆給第一次鋪底／值班想立刻更新基準用
const baselining = ref(false)
async function baselineRun() {
  if (!window.confirm('立即對所有已納管主機收一份「健康基準」？平常每週六會自動收，這是手動補一份。')) return
  baselining.value = true
  try {
    const r = await apiFetch<any>('/api/health-check/baseline/run', { method: 'POST' })
    showToast(`已建立基準：${r.saved}/${r.targets} 台`, 'success')
  } catch (e: any) { showToast(`建立基準失敗：${errMsg(e)}`, 'error') } finally { baselining.value = false }
}

// 最近查過的 IP（前 5 筆）：per-viewer 方便，存 localStorage；讀寫都包 try/catch（無痕／封鎖 storage 不能壞）
const RECENT_KEY = 'healthcheck_recent_ips'
const recentIps = ref<string[]>([])
onMounted(() => {
  try { recentIps.value = [...new Set((JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') as string[]).map(cleanHost).filter(Boolean))].slice(0, 5) }
  catch { recentIps.value = [] }        // 沒有「最近查過」清單是正確的降級，不是要通報的錯誤
})
function pushRecent(ips: string[]) {
  try {
    recentIps.value = [...new Set([...ips.map(cleanHost), ...recentIps.value])].filter(Boolean).slice(0, 5)
    localStorage.setItem(RECENT_KEY, JSON.stringify(recentIps.value))
  } catch { /* storage 不可用就算了，不影響檢查 */ }
}
function pickRecent(ip: string) { if (!parsedIps.value.includes(ip)) ipsText.value = (ipsText.value.trim() ? ipsText.value.trim() + '\n' : '') + ip }

const LIGHT: Record<string, string> = { red: '🔴 異常', yellow: '🟡 注意', green: '🟢 正常', skipped: '⏭ 跳過' }
const SEV: Record<string, number> = { red: 3, yellow: 2, green: 1, skipped: 0 }
const plain = (t: string) => (t || '').replace(/\*\*/g, '')
// 先攤成平面欄位再排；?? -1：null（這個平台沒有這種東西）不可以被當成 0%
const rows = computed(() => [...results.value].map(r => ({ ...r,
  sev: SEV[r.overall] ?? 0, diskPct: r.disks?.[0]?.use_pct ?? -1, inodeMax: Math.max(-1, ...(r.disks ?? []).map((d: any) => d.inode_pct ?? -1)),
  memPct: r.mem?.used_pct ?? -1, cpuPct: r.cpu?.usage_pct ?? -1, loadPer: r.load?.per_cpu ?? -1, failedN: (r.failed_units ?? []).length,
  portN: r.reachable ? (r.ports ?? []).length : -1,
  // 只算「連得到卻沒查完」——跟後端 summary.incomplete 同一個條件
  incomplete: r.reachable && r.complete === false && !r.not_probed ? 1 : 0,
  light_t: r.not_probed ? '⏭ 沒去成' : (LIGHT[r.overall] ?? r.overall),
  summary_t: !r.reachable ? (r.error || '連不上') : plain((r.notes ?? []).join('；')),
})).sort((a, b) => b.sev - a.sev || String(a.ip).localeCompare(String(b.ip), undefined, { numeric: true })).map((r, i) => ({ ...r, __no: i + 1 })))

// 點方塊只看那一類；「沒查完」「沒去成」是跟燈號不同的維度，另開篩選
const lightFilter = ref('')
const onlyIncomplete = ref(false)
const onlyNotProbed = ref(false)
function pickLight(k: string) { lightFilter.value = lightFilter.value === k ? '' : k }
function clearFilters() { lightFilter.value = ''; onlyIncomplete.value = false; onlyNotProbed.value = false }
const filtered = computed(() => !!(lightFilter.value || onlyIncomplete.value || onlyNotProbed.value))
const shown = computed(() => rows.value.filter(r => (!lightFilter.value || r.overall === lightFilter.value)
  && (!onlyIncomplete.value || r.incomplete === 1) && (!onlyNotProbed.value || !!r.not_probed)))
const filterText = computed(() => [lightFilter.value ? (LIGHT[lightFilter.value] || lightFilter.value) : '', onlyIncomplete.value ? '未完整檢查' : '', onlyNotProbed.value ? '沒去成' : ''].filter(Boolean).join('＋'))
const pctOr = (v: number | null | undefined, extra = '') => (v === null || v === undefined || v < 0 ? '—' : `${v}%${extra}`)
const cols = [
  { k: 'sev', label: '判定', text: (r: any) => r.light_t }, { k: 'ip', label: 'IP', mono: true },
  { k: 'diskPct', label: '磁碟', mono: true, text: (r: any) => pctOr(r.diskPct, r.inodeMax >= 80 ? ` i${r.inodeMax}%` : '') },
  { k: 'memPct', label: '記憶體', mono: true, text: (r: any) => pctOr(r.memPct, r.mem && r.mem.swap_pct >= 50 ? ` sw${r.mem.swap_pct}%` : '') },
  { k: 'cpuPct', label: 'CPU', mono: true, text: (r: any) => pctOr(r.cpuPct, r.cpu && r.cpu.iowait_pct >= 25 ? ` io${r.cpu.iowait_pct}%` : '') },
  { k: 'loadPer', label: '負載/核', mono: true, text: (r: any) => (r.load ? r.load.per_cpu : '—') },
  { k: 'failedN', label: '失敗服務', mono: true }, { k: 'portN', label: 'port', mono: true, text: (r: any) => (r.reachable ? r.portN : '—') },
  { k: 'incomplete', label: '完整度', mono: true, text: (r: any) => (r.coverage ? `${r.coverage.done}/${r.coverage.total}` : '—'),
    note: '只講查了幾項，不參與燈號。未完整的缺哪幾項、為什麼，點 IP 看。' },
  { k: 'summary_t', label: '摘要' }]

// 單台明細抽屜
const openIp = ref('')
const sel = computed(() => rows.value.find(x => x.ip === openIp.value) || null)
function closeDrawer() { openIp.value = '' }
const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') closeDrawer() }
onMounted(() => window.addEventListener('keydown', onEsc))
onBeforeUnmount(() => window.removeEventListener('keydown', onEsc))

// 匯出證據 CSV（舊頁那份，一台一列＋各面向關鍵值；照目前的篩選匯出）。表格上的「匯出」另外匯出畫面欄位。
function exportCsv() {
  const head = ['掃描時間', 'IP', '判定', '連得上', 'OS', '開機天數', '剛重開', '最滿磁碟%', '最滿掛載', '最滿inode%', '記憶體%', 'swap%', 'CPU%', 'iowait%', '每核負載',
    '失敗服務', '唯讀重掛', 'CLOSE_WAIT', '磁碟util%', 'port數', '殭屍', 'NTP同步', '說明']
  const esc = (v: any) => { const s = String(v ?? ''); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s }
  const lines = [head.join(',')]
  for (const r of shown.value) {
    lines.push([r.checked_at ?? '', r.ip, LIGHT[r.overall] ?? r.overall, r.reachable ? '是' : '否', r.os ?? '', r.uptime?.days ?? '', r.uptime?.just_rebooted ? '是' : '',
      r.disks?.[0]?.use_pct ?? '', r.disks?.[0]?.mount ?? '', r.inodeMax >= 0 ? r.inodeMax : '', r.mem?.used_pct ?? '', r.mem?.swap_pct ?? '', r.cpu?.usage_pct ?? '',
      r.cpu?.iowait_pct ?? '', r.load?.per_cpu ?? '', (r.failed_units ?? []).join('｜'), (r.readonly_mounts ?? []).map((m: any) => m.mount).join('｜'),
      r.tcp?.close_wait ?? '', r.io?.util ?? '', r.reachable ? (r.ports ?? []).length : '', r.zombies, r.ntp_synced == null ? '' : (r.ntp_synced ? '是' : '否'),
      (r.error || plain((r.notes ?? []).join('；')))].map(esc).join(','))
  }
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob); a.download = `主機自我檢查_${new Date().toISOString().slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div>
    <p class="muted">輸入告警那台的 IP 跑一輪唯讀健檢，產一份可回報的證據。
      <InfoNote>值班收到告警（WhatsUp Gold 說某台當機／異常）時用。檢查面向：①系統資源（CPU 使用率/iowait、記憶體/swap、磁碟空間/inode、load、剛重開機）②業務服務（systemd failed、監聽 port/服務、殭屍）③網路（TCP 連線狀態、iostat 磁碟 I/O、介面丟包、DNS）④安全/變更（最近登入、NTP）。唯讀檢查、不改設定；連不上／唯讀重掛／有 failed 服務＝紅。判定用絕對閾值不靠基準。只打指定的已登記主機、並發有上限，不是掃描。</InfoNote></p>

    <div class="box">
      <label class="row"><span class="lbl">要檢查的 IP<InfoNote>一行一個，或用逗號／空白分隔；貼整串網址也可以，會剝成純主機。</InfoNote></span>
        <textarea v-model="ipsText" class="ips" rows="3" placeholder="10.99.1.10&#10;10.99.1.11" aria-label="要檢查的 IP" /></label>
      <div v-if="recentIps.length" class="row"><span class="lbl">最近查過</span>
        <div class="chips"><button v-for="ip in recentIps" :key="ip" type="button" class="chip mono" @click="pickRecent(ip)">{{ ip }}</button></div></div>
      <div class="row"><span class="lbl">或整段帶入</span>
        <input v-model="cidr" class="inp" placeholder="10.99.1.0/24" aria-label="網段" @keyup.enter="loadSegment">
        <button type="button" class="btn2" @click="loadSegment">查這段已登記的</button></div>
      <div class="row">
        <label class="muted">並發 <input v-model.number="concurrency" class="inp num" type="number" min="1" max="16" aria-label="並發台數"> 台
          <InfoNote>同時檢查幾台，上限 16。開太大對一群主機同時 SSH 會像掃描/暴力、可能觸發 SOC——預設 8 是刻意保守。</InfoNote></label>
        <span v-if="parsedIps.length" class="muted">已輸入 {{ parsedIps.length }} 台</span>
        <template v-if="isAdmin">
          <button type="button" class="btn" :disabled="running || !parsedIps.length" @click="run">{{ running ? '檢查中…' : '執行自我檢查' }}</button>
          <button type="button" class="btn2" :disabled="baselining" @click="baselineRun">{{ baselining ? '建立中…' : '建立健康基準' }}</button>
          <InfoNote>建立健康基準：對所有已納管主機收一份「正常時的參照」。平常每週六自動收（離峰）；這顆是手動補一份／第一次鋪底用。事發時的即時健檢會跟最近一份基準比：少了 port/服務＝紅（可能掉了）、新增＝提示。</InfoNote>
        </template>
        <span v-else class="muted">執行健檢、建立基準限管理員</span>
      </div>
    </div>

    <template v-if="summary">
      <div class="cards">
        <button type="button" class="scard bad" :class="{ on: lightFilter === 'red' }" @click="pickLight('red')"><span class="sv">{{ summary.red }}</span><span class="sl">異常（含連不上 {{ summary.unreachable }}）</span></button>
        <button type="button" class="scard warn" :class="{ on: lightFilter === 'yellow' }" @click="pickLight('yellow')"><span class="sv">{{ summary.yellow }}</span><span class="sl">注意</span></button>
        <button type="button" class="scard good" :class="{ on: lightFilter === 'green' }" @click="pickLight('green')"><span class="sv">{{ summary.green }}</span><span class="sl">正常</span></button>
        <!-- 中性色：沒去成（主詞是我們：沒憑證／金鑰）、未完整檢查（尚未確認）都是我們的待辦，不是那台的狀態 -->
        <button v-if="summary.not_probed" type="button" class="scard plain" :class="{ on: onlyNotProbed }" @click="onlyNotProbed = !onlyNotProbed"><span class="sv">{{ summary.not_probed }}</span><span class="sl">沒去成（我們這邊）</span></button>
        <button v-if="summary.incomplete" type="button" class="scard plain" :class="{ on: onlyIncomplete }" @click="onlyIncomplete = !onlyIncomplete"><span class="sv">{{ summary.incomplete }}</span><span class="sl">未完整檢查</span></button>
        <button type="button" class="scard" :class="{ on: !filtered }" @click="clearFilters()"><span class="sv">{{ summary.total }}</span><span class="sl">全部<template v-if="summary.skipped">（跳過 {{ summary.skipped }}）</template></span></button>
        <button type="button" class="btn2 exp" :disabled="!shown.length" @click="exportCsv">匯出證據 CSV</button>
      </div>
      <p v-if="filtered" class="muted">目前只顯示「{{ filterText }}」{{ shown.length }} 台（共 {{ rows.length }} 台）
        <button type="button" class="lb" @click="clearFilters()">顯示全部</button><InfoNote>匯出會照這個篩選匯出，不是全部。</InfoNote></p>
      <V2Table :rows="shown" prefix="" row-key="ip" unit="台" :cols="cols" export-name="批次健檢"
               :empty="filtered ? `這次檢查的 ${rows.length} 台裡，沒有符合目前篩選的` : '沒有結果'">
        <template #cell-sev="{ row }"><span class="pill" :class="row.not_probed ? 'skipped' : row.overall">{{ row.light_t }}</span>
          <span v-if="row.incomplete === 1 && row.coverage" class="cover-tag">未完整檢查 {{ row.coverage.done }}/{{ row.coverage.total }}</span></template>
        <template #cell-ip="{ row }"><button type="button" class="lb mono" :class="{ on: openIp === row.ip }" @click="openIp = row.ip">{{ row.ip }}</button>
          <span v-if="row.uptime?.just_rebooted" class="reboot" title="開機不到 10 分鐘">剛重開</span></template>
        <!-- 數值格照舊依 level 上色（跟舊頁同一條：後端給的 level） -->
        <template #cell-diskPct="{ row }"><span :class="row.disks?.length ? 'lv-' + row.disks[0].level : 'dim'">{{ row.disks?.length ? row.disks[0].use_pct + '%' : '—' }}</span><span v-if="row.inodeMax >= 80" class="dim"> i{{ row.inodeMax }}%</span></template>
        <template #cell-memPct="{ row }"><span :class="row.mem ? 'lv-' + row.mem.level : 'dim'">{{ row.mem ? row.mem.used_pct + '%' : '—' }}</span><span v-if="row.mem && row.mem.swap_pct >= 50" class="dim"> sw{{ row.mem.swap_pct }}%</span></template>
        <template #cell-cpuPct="{ row }"><span :class="row.cpu ? 'lv-' + row.cpu.level : 'dim'">{{ row.cpu ? row.cpu.usage_pct + '%' : '—' }}</span><span v-if="row.cpu && row.cpu.iowait_pct >= 25" class="dim"> io{{ row.cpu.iowait_pct }}%</span></template>
        <template #cell-loadPer="{ row }"><span :class="row.load ? 'lv-' + row.load.level : 'dim'">{{ row.load ? row.load.per_cpu : '—' }}</span></template>
        <template #cell-failedN="{ row }"><span :class="row.failedN ? 'lv-red' : 'dim'">{{ row.failedN }}</span></template>
        <template #cell-incomplete="{ row }"><span v-if="!row.coverage" class="dim">—</span>
          <span v-else :class="row.incomplete === 1 ? 'cover-warn' : 'dim'" :title="row.incomplete === 1 ? '缺：' + row.coverage.missing.map((m: any) => m.name).join('、') + '（點 IP 看原因）' : undefined">{{ row.coverage.done }}/{{ row.coverage.total }}</span></template>
      </V2Table>
    </template>
    <p v-else-if="!running" class="muted">還沒有檢查結果。上面輸入 IP 後按「執行自我檢查」。</p>

    <div v-if="sel" class="mask" @click="closeDrawer" />
    <aside v-if="sel" class="drawer" role="dialog" aria-label="主機健檢明細">
      <header class="dhead">
        <span class="pill" :class="sel.overall">{{ LIGHT[sel.overall] || sel.overall }}</span>
        <b class="mono">{{ sel.ip }}</b><span v-if="sel.os" class="muted">{{ sel.os }}</span>
        <span class="sp" /><button type="button" class="btn2" @click="closeDrawer">關閉 (Esc)</button>
      </header>
      <div class="dbody"><V2HealthDetail :sel="sel" /></div>
    </aside>
  </div>
</template>

<style scoped>
.box { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 12px 14px; margin: 6px 0 12px; display: flex; flex-direction: column; gap: 10px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.lbl { min-width: 92px; color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; }
.ips { flex: 1; min-width: 260px; font-family: ui-monospace, monospace; font-size: 12.5px; padding: 8px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); resize: vertical; }
.inp { font-family: inherit; font-size: 13px; padding: 4px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); min-width: 180px; }
.inp.num { min-width: 0; width: 60px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--brand-dark); cursor: pointer; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin: 4px 0 10px; align-items: center; }
.scard { font-family: inherit; display: flex; flex-direction: column; gap: 2px; min-width: 110px; padding: 10px 14px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card); color: var(--ink); cursor: pointer; text-align: left; }
.scard:hover { border-color: var(--border-strong); }
.scard.on { outline: 2px solid var(--brand); outline-offset: -2px; }
.scard .sv { font-family: var(--disp); font-size: 22px; font-weight: 700; }
.scard .sl { font-size: 12px; color: var(--muted); }
.scard.bad { border-color: var(--bad); } .scard.bad .sv { color: var(--bad); }
.scard.warn { border-color: var(--warn); } .scard.warn .sv { color: var(--warn-text); }
.scard.good { border-color: var(--brand); } .scard.good .sv { color: var(--brand-dark); }
.scard.plain .sv { color: var(--ink-aux); }
.exp { margin-left: auto; }
.btn { font-family: inherit; font-size: 13px; padding: 4px 14px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .45; cursor: default; }
.lb { font-family: inherit; font-size: 12px; padding: 1px 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.lb.on { background: var(--mint); border-color: var(--brand); }
.mono { font-family: ui-monospace, monospace; }
.pill { font-size: 11.5px; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
.pill.red { background: var(--bad-soft); color: var(--bad); }
.pill.yellow { background: var(--warn-soft); color: var(--warn-text); }
.pill.green { background: var(--mint); color: var(--brand-dark); }
.pill.skipped { background: var(--sub); color: var(--muted); }
/* 「未完整」一律中性灰，不可以用黃色（會跟「注意」混在一起） */
.cover-tag { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 9px; font-size: 11px; color: var(--ink-aux); border: 1px solid var(--border); white-space: nowrap; }
.lv-red { color: var(--bad); font-weight: 700; }
.lv-yellow { color: var(--warn-text); font-weight: 700; }
.lv-green { color: var(--brand-dark); }
.dim { color: var(--muted); }
.cover-warn { color: var(--ink-aux); border-bottom: 1px dotted currentColor; cursor: help; }
.reboot { font-size: 10px; color: var(--warn-text); background: var(--warn-soft); padding: 0 5px; border-radius: 6px; margin-left: 6px; }
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .18); z-index: 40; }
.drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(980px, 82vw); background: var(--card); border-left: 1px solid var(--border-strong); z-index: 41; display: flex; flex-direction: column; box-shadow: var(--shadow); }
.dhead { display: flex; align-items: center; gap: 10px; padding: 10px 16px; border-bottom: 1px solid var(--border); flex: 0 0 auto; }
.dbody { overflow: auto; padding: 14px 16px; }
.sp { flex: 1; }
.muted { color: var(--muted); font-size: 13px; display: inline-flex; gap: 4px; align-items: center; flex-wrap: wrap; }
</style>
