<script setup lang="ts">
// 帳號儀表板：只放彙總數字框，每個框都可點下鑽到「帳號合規表」對應的資料。
// 表格獨立在 /account-matrix（使用者 2026-07-23 拍板：儀表板與表格分兩頁）。
// 點框框 → navigateTo('/account-matrix?…') 帶 query，合規表頁自動預選分頁＋套篩選。
const { apiFetch } = useApi()

interface Acc {
  pw_status: string | null; pw_expiry_status: string; uid: number; username: string
  is_sudoer: number; sudo_nopasswd: number; authorized_keys: number | null; never_logged_in: number
}
interface Summary {
  has_data: boolean
  fail_high?: number; fail_medium?: number; unknown?: number
  accounts?: number; privileged?: number; humans?: number
  run?: { started_at: string; host_count: number }
}

// 盤點到哪幾台（2026-09-16 使用者：「我只知道四台? 不知道哪四台，應該是先列出哪幾台，
// 我再進去點後才是那一台的全部帳號資訊」，並補「每個盤點都是這概念」）。
// 彙總數字對得起來也沒用——人要做的事是「去那台上面改」，所以主機清單要在最前面。
interface HostRow {
  ip: string; asset_serial: string | null; hostname: string | null
  os: string | null; environment: string | null; physical_location: string | null
  department: string | null; contact: string | null; custodian: string | null
  accounts: number; human: number; sudoers: number; never_login: number
  findings: number; findings_high: number; collected_at: string | null; excluded: boolean
}
const hosts = ref<HostRow[]>([])
const hostsError = ref('')
const hostKw = ref('')
const rt = useRuntimeConfig()
const shownHosts = computed(() => {
  const q = hostKw.value.trim().toLowerCase()
  if (!q) return hosts.value
  return hosts.value.filter((h) => [h.ip, h.hostname, h.asset_serial, h.os,
                                    h.environment, h.physical_location,
                                    h.department, h.contact, h.custodian]
    .some((v) => (v || '').toString().toLowerCase().includes(q)))
})
// 預設照「高風險條數」排——這頁的用途是決定先看哪一台，不是按 IP 唸名冊
const { sortKey: hKey, sortDir: hDir, toggle: hToggle, sorted: hSorted } =
  useSort(shownHosts, 'findings_high', 'desc')

const accounts = ref<Acc[]>([])
const findingsCount = ref(0)
const summary = ref<Summary | null>(null)
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const [a, f, h] = await Promise.all([
      apiFetch<{ items: Acc[] }>('/api/accounts'),
      apiFetch<{ items: any[]; summary: Summary }>('/api/accounts/findings'),
      // ⚠️ 不可以 .catch(() => ({items:[]}))：2026-09-17 踩到——SQL 寫錯欄位名讓這支 500，
      // 錯誤被吞掉之後畫面顯示「0 台／這一輪沒有收到任何主機的帳號資料」，
      // 但實際上有 7 台。**程式壞掉被顯示成「沒有資料」是最糟的失敗方式。**
      apiFetch<{ items: HostRow[] }>('/api/accounts/inventoried-hosts')
        .catch((e: any) => { hostsError.value = e?.data?.detail ?? e?.message ?? '載入失敗'; return { items: [] } }),
    ])
    accounts.value = a.items
    findingsCount.value = f.items.length
    summary.value = f.summary
    hosts.value = h.items ?? []
  } catch { /* 拿不到就留空 */ } finally {
    loading.value = false
  }
}
onMounted(load)

// 特權帳號趨勢（2026-09-19）：每次採集後存一筆現值，這裡畫成折線。
// 資料是採集後才開始累積的——點數少代表剛開始收，不是沒有特權帳號。
interface PrivSnap {
  taken_at: string; total_accounts: number; privileged: number
  sudoers: number; uid0: number; nopasswd: number; mgmt: number; human: number
}
const trend = ref<PrivSnap[]>([])
onMounted(async () => {
  try { trend.value = (await apiFetch<{ items: PrivSnap[] }>('/api/accounts/privileged-trend')).items }
  catch { /* 輔助小圖，載不到不擋整頁 */ }
})
const latestSnap = computed(() => trend.value[trend.value.length - 1] ?? null)
// SVG 折線：把 privileged 序列映射到 0..100 × 0..30 的座標。只有 1 點畫不出線，改標「剛開始累積」。
const trendPath = computed(() => {
  const xs = trend.value
  if (xs.length < 2) return ''
  const vals = xs.map((s) => s.privileged)
  const max = Math.max(...vals, 1), min = Math.min(...vals, 0)
  const span = max - min || 1
  return xs.map((s, i) => {
    const x = (i / (xs.length - 1)) * 100
    const y = 30 - ((s.privileged - min) / span) * 28 - 1
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})
const trendDelta = computed(() => {
  if (trend.value.length < 2) return null
  return trend.value[trend.value.length - 1].privileged - trend.value[0].privileged
})

// passwd -S 狀態碼正規化（跟合規表 pwState 一致）：set/locked/empty/null。
function pwState(a: Acc): 'set' | 'locked' | 'empty' | null {
  const s = (a.pw_status || '').toUpperCase()
  if (s === '') return null
  if (s === 'LOCKED' || s === 'LK' || s === 'L') return 'locked'
  if (s === 'EMPTY' || s === 'NP') return 'empty'
  if (s === 'SET' || s === 'PS' || s === 'P') return 'set'
  return null
}
function cnt(fn: (a: Acc) => boolean) { return accounts.value.filter(fn).length }

// 上排：總量 / 稽核發現 / 風險 / 資料落差 / 帳號屬性。每個框都連到合規表對應視圖。
const kpis = computed(() => [
  { n: accounts.value.length, label: '受稽核帳號', to: '/account-matrix?tab=matrix', tone: '' },
  { n: findingsCount.value, label: '稽核發現', hint: '看明細', to: '/account-matrix?tab=findings', tone: '' },
  { n: summary.value?.fail_high ?? 0, label: '高風險不合規', to: '/account-matrix?tab=findings&sev=high', tone: 'bad' },
  { n: summary.value?.fail_medium ?? 0, label: '中風險不合規', to: '/account-matrix?tab=findings&sev=medium', tone: 'warn' },
  { n: summary.value?.unknown ?? 0, label: '查不到（權限不足）', hint: '不等於合格', to: '/account-matrix?col=disabled&val=needroot', tone: 'warn' },
  { n: summary.value?.privileged ?? 0, label: '特權帳號', hint: `/ ${accounts.value.length} 全部`, to: '/account-matrix?sudoer=1', tone: '' },
  { n: summary.value?.humans ?? 0, label: '真人帳號', to: '/account-matrix?kind=human', tone: '' },
])

// 下排：各項合規檢查中招數。每個框連到合規表並套上該欄篩選（col+val）。
const checks = computed(() => [
  { n: cnt(a => a.pw_expiry_status === 'expired'), label: '密碼已過期', to: '/account-matrix?col=pwExpired&val=expired', tone: 'bad' },
  { n: cnt(a => a.pw_expiry_status === 'never'), label: '密碼永不過期', to: '/account-matrix?col=pwExpired&val=never', tone: 'bad' },
  { n: cnt(a => pwState(a) === 'empty'), label: '空密碼', to: '/account-matrix?col=empty&val=yes', tone: 'bad' },
  { n: cnt(a => a.uid === 0 && a.username !== 'root'), label: 'UID 0 非 root', to: '/account-matrix?col=uid0&val=yes', tone: 'bad' },
  { n: cnt(a => a.uid !== 0 && !!a.sudo_nopasswd), label: '免密碼 sudo', to: '/account-matrix?col=sudo&val=nopw', tone: 'bad' },
  { n: cnt(a => a.uid !== 0 && !a.sudo_nopasswd && !!a.is_sudoer), label: '有 sudo 權限', to: '/account-matrix?col=sudo&val=yes', tone: 'warn' },
  { n: cnt(a => (a.authorized_keys ?? 0) > 0), label: '免密碼金鑰', to: '/account-matrix?col=keys&val=has', tone: 'warn' },
  { n: cnt(a => !!a.never_logged_in), label: '從未登入', to: '/account-matrix?col=login&val=never', tone: 'warn' },
])

function go(to: string) { navigateTo(to) }
</script>

<template>
  <div>
    <p class="lead">帳號盤點的彙總數字
      <InfoNote><b>每個框都可以點</b>，點下去會帶你到「帳號合規表」看對應的實際帳號／發現。要收資料到「盤點作業」。</InfoNote></p>

    <p v-if="loading" class="muted">載入中…</p>
    <p v-else-if="accounts.length === 0" class="muted">
      沒有帳號資料，到 <NuxtLink class="dl" to="/account-ops">盤點作業</NuxtLink> 收一輪。
    </p>
    <template v-else>
      <!-- 先列出「是哪幾台」（2026-09-16 使用者），點進去才是那一台的帳號明細。
           彙總數字往下移、也縮小——那些是結果，主機清單才是可以動手的東西。 -->
      <section class="hostsec">
        <div class="hhd">
          盤點到的主機 <b>{{ hosts.length }}</b> 台
          <InfoNote>這一輪實際收到帳號資料的是這幾台。<b>點主機名或 IP</b> 進去看那一台的完整帳號清單。<br><br>沒出現在這裡的機器代表這一輪沒收到（可能還沒納管、或被排除），到「盤點作業」看原因。</InfoNote>
          <span class="spacer" />
          <input v-model="hostKw" class="hkw" placeholder="搜 IP／主機名／機房／OS／部門／窗口" />
          <a class="btn small" :href="`${rt.public.apiBase}/api/accounts/inventoried-hosts/export`">⬇ 匯出 Excel</a>
        </div>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <SortTh k="ip" :active="hKey" :dir="hDir" @sort="hToggle">IP</SortTh>
                <SortTh k="hostname" :active="hKey" :dir="hDir" @sort="hToggle">主機名稱</SortTh>
                <SortTh k="physical_location" :active="hKey" :dir="hDir" @sort="hToggle">機房</SortTh>
                <SortTh k="environment" :active="hKey" :dir="hDir" @sort="hToggle">環境</SortTh>
                <SortTh k="os" :active="hKey" :dir="hDir" @sort="hToggle">作業系統</SortTh>
                <!-- 部門與窗口（2026-09-17 使用者：「沒這個以後怎麼找窗口盤點」）——
                     盤點的下一步是去找人，沒有窗口這份清單只能看不能用。 -->
                <SortTh k="department" :active="hKey" :dir="hDir" @sort="hToggle">使用單位（部門）</SortTh>
                <SortTh k="contact" :active="hKey" :dir="hDir" @sort="hToggle">窗口</SortTh>
                <SortTh k="accounts" :active="hKey" :dir="hDir" class="num" @sort="hToggle">帳號數</SortTh>
                <SortTh k="human" :active="hKey" :dir="hDir" class="num" @sort="hToggle">真人</SortTh>
                <SortTh k="sudoers" :active="hKey" :dir="hDir" class="num" @sort="hToggle">有 sudo</SortTh>
                <SortTh k="never_login" :active="hKey" :dir="hDir" class="num" @sort="hToggle">從未登入</SortTh>
                <SortTh k="findings" :active="hKey" :dir="hDir" class="num" @sort="hToggle">稽核發現</SortTh>
                <SortTh k="collected_at" :active="hKey" :dir="hDir" @sort="hToggle">盤點時間</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="h in hSorted" :key="h.ip">
                <td class="mono">
                  <NuxtLink class="dl" :to="`/account-matrix?ip=${encodeURIComponent(h.ip)}`"
                            title="看這台的完整帳號清單">{{ h.ip }}</NuxtLink>
                </td>
                <td>
                  <NuxtLink v-if="h.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(h.asset_serial)}`"
                            title="看這台的資產詳細頁">{{ h.hostname || '—' }}</NuxtLink>
                  <template v-else>{{ h.hostname || '—' }}</template>
                </td>
                <td>{{ h.physical_location || '—' }}</td>
                <td>{{ h.environment || '—' }}</td>
                <td class="ell" :title="h.os || ''">{{ h.os || '—' }}</td>
                <td class="ell" :title="h.department || ''">{{ h.department || '—' }}</td>
                <td class="ell" :title="`使用者：${h.contact || '—'}　保管者：${h.custodian || '—'}`">
                  {{ h.contact || '—' }}
                  <span v-if="h.custodian && h.custodian !== h.contact" class="dim sm">／{{ h.custodian }}</span>
                </td>
                <td class="num">
                  <NuxtLink class="dl" :to="`/account-matrix?ip=${encodeURIComponent(h.ip)}`">{{ h.accounts }}</NuxtLink>
                </td>
                <td class="num">{{ h.human }}</td>
                <td class="num">{{ h.sudoers }}</td>
                <td class="num">{{ h.never_login }}</td>
                <td class="num">
                  <NuxtLink v-if="h.findings" class="dl"
                            :class="{ badn: h.findings_high }"
                            :to="`/account-matrix?tab=findings&ip=${encodeURIComponent(h.ip)}`"
                            :title="h.findings_high ? `其中 ${h.findings_high} 條高風險` : ''">{{ h.findings }}</NuxtLink>
                  <span v-else class="muted">0</span>
                </td>
                <td class="mono muted sm">{{ h.collected_at || '—' }}</td>
              </tr>
              <tr v-if="!hSorted.length">
                <td colspan="13" :class="hostsError ? 'err-cell' : 'muted'">
                  <template v-if="hostsError">
                    ✕ 主機清單載入失敗：{{ hostsError }}
                    <div class="sm">這是<b>程式或連線出錯</b>，不是「沒有資料」——請把這行訊息貼給開發者。</div>
                  </template>
                  <template v-else-if="hostKw">沒有符合「{{ hostKw }}」的主機</template>
                  <template v-else>這一輪沒有收到任何主機的帳號資料</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <p class="sechd">彙總數字
        <InfoNote>這些是上面那批主機加總起來的結果。<b>每個框都可以點</b>，會帶你到「帳號合規表」看對應的實際帳號／發現。</InfoNote>
      </p>
      <div class="tiles">
        <div v-for="k in kpis" :key="k.label" class="tile clickable" :class="[k.tone, { hot: k.tone && k.n > 0 }]"
             @click="go(k.to)">
          <div class="t-num">{{ k.n }}<small v-if="k.hint && k.hint.startsWith('/')" class="of">{{ k.hint }}</small></div>
          <div class="t-lbl">{{ k.label }}<span v-if="k.hint && !k.hint.startsWith('/')" class="hint">{{ k.hint }}</span></div>
          <span class="drill">看資料 →</span>
        </div>
      </div>

      <!-- 特權帳號趨勢：稽核最看「特權帳號是變多還變少」。每次採集後累積一點。 -->
      <section class="trendsec">
        <p class="sechd">特權帳號趨勢
          <InfoNote>特權＝有 sudo／UID 0／標準管理帳號。每次採集後存一筆現值。<b>這是採集後才開始累積的</b>——點少代表剛開始收，不是沒有特權帳號。稽核最看的是這條線在變多還變少。</InfoNote>
        </p>
        <div class="trend-card">
          <div v-if="latestSnap" class="trend-now">
            <div class="tn-num">{{ latestSnap.privileged }}</div>
            <div class="tn-lbl">目前特權帳號
              <span v-if="trendDelta !== null" class="tn-delta" :class="trendDelta > 0 ? 'up' : trendDelta < 0 ? 'down' : ''">
                {{ trendDelta > 0 ? '▲ +' + trendDelta : trendDelta < 0 ? '▼ ' + trendDelta : '＝ 持平' }}<span v-if="trendDelta !== 0"> 自首筆</span>
              </span>
            </div>
            <div class="tn-break">sudo {{ latestSnap.sudoers }}・UID0 {{ latestSnap.uid0 }}・免密碼 {{ latestSnap.nopasswd }}・管理帳號 {{ latestSnap.mgmt }}</div>
          </div>
          <div class="trend-chart">
            <svg v-if="trendPath" viewBox="0 0 100 30" preserveAspectRatio="none" class="spark">
              <path :d="trendPath" fill="none" stroke="var(--brand)" stroke-width="1.2" vector-effect="non-scaling-stroke" />
            </svg>
            <p v-else class="muted sm">
              {{ trend.length === 1 ? '已有第一筆，再採集一輪就畫得出趨勢線。' : '還沒有快照——收一輪帳號後開始累積。' }}
            </p>
          </div>
        </div>
      </section>

      <h3 class="dash-h">各項合規檢查中招數（點框看是哪些帳號）</h3>
      <div class="tiles checks">
        <div v-for="c in checks" :key="c.label" class="tile check clickable" :class="[c.tone, { zero: c.n === 0, hot: c.n > 0 }]"
             @click="c.n && go(c.to)">
          <div class="t-num">{{ c.n }}</div>
          <div class="t-lbl">{{ c.label }}</div>
          <span v-if="c.n > 0" class="drill">看帳號 →</span>
        </div>
      </div>

      <p v-if="summary?.run" class="when">
        最後盤點 · {{ summary.run.started_at }}（{{ summary.run.host_count }} 台）
      </p>
    </template>
  </div>
</template>

<style scoped>
.err-cell { color: var(--bad); }
.err-cell .sm { font-size: 11.5px; color: var(--ink-soft); margin-top: 3px; }
.hostsec { margin-bottom: 18px; }
.hhd { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 600; margin-bottom: 8px; }
.hhd .spacer { flex: 1; }
.hkw { padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
       background: var(--card); color: var(--ink); font-size: 13px; min-width: 200px; font-weight: 400; }
.sechd { font-size: 13px; font-weight: 600; color: var(--ink-soft); margin: 14px 0 6px;
         display: flex; align-items: center; gap: 6px; }
.ell { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.badn { color: var(--bad); font-weight: 700; }
.sm { font-size: 11.5px; }
.trendsec { margin: 6px 0 14px; }
.trend-card { display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
  border: 1px solid var(--border); border-radius: var(--radius, 14px); background: var(--card); padding: 14px 18px; }
.trend-now { min-width: 160px; }
.tn-num { font-size: 32px; font-weight: 700; color: var(--brand-dark); line-height: 1; font-family: var(--disp, monospace); }
.tn-lbl { font-size: 12px; color: var(--ink-soft); margin-top: 4px; }
.tn-delta { margin-left: 6px; font-weight: 600; }
.tn-delta.up { color: var(--bad); }
.tn-delta.down { color: var(--good, var(--brand)); }
.tn-break { font-size: 11px; color: var(--muted); margin-top: 3px; }
.trend-chart { flex: 1; min-width: 200px; }
.spark { width: 100%; height: 60px; display: block; }
.mono { font-family: ui-monospace, monospace; }
.lead { color: var(--muted); margin: 0 0 18px; line-height: 1.7; }
.lead b { color: var(--ink, var(--ink)); }
.dl { color: var(--brand-dark); text-decoration: none; }
.dl:hover { text-decoration: underline; }

/* 卡片縮小（2026-09-16 使用者：「卡片太大，小一點」）——
   它們是結果、不是主角，主機清單才是可以動手的東西。 */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: 8px; margin-bottom: 8px; }
.tiles.checks { grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); }
.tile { position: relative; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 9px 11px; }
.tile.clickable { cursor: pointer; transition: transform .12s, border-color .12s, box-shadow .12s; }
.tile.clickable:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(0,0,0,.28); border-color: rgba(0,145,66,.4); }
.tile.bad.hot { border-color: rgba(224,108,108,.5); }
.tile.warn.hot { border-color: rgba(230,170,60,.45); }
.t-num { font-size: 20px; font-weight: 700; color: var(--brand-dark); line-height: 1.1; font-variant-numeric: tabular-nums; }
.tile.bad.hot .t-num { color: var(--bad); }
.tile.warn.hot .t-num { color: var(--warn-text); }
.t-num .of { font-size: 13px; color: var(--muted); font-weight: 500; margin-left: 2px; }
.t-lbl { font-size: 11.5px; color: var(--muted); margin-top: 3px; line-height: 1.4; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .7; }
.tile.check.zero { opacity: .5; cursor: default; }
.tile.check.zero:hover { transform: none; box-shadow: none; border-color: var(--border); }
.tile.check.zero .t-num { color: var(--muted); }
.drill { position: absolute; right: 12px; bottom: 10px; font-size: 10px; color: var(--brand-dark);
  opacity: 0; transition: opacity .12s; }
.tile.clickable:hover .drill { opacity: .9; }
.dash-h { font-size: 14px; color: var(--muted); font-weight: 600; margin: 22px 0 10px; }
.when { font-size: 12px; color: var(--muted); margin-top: 16px; }
</style>
