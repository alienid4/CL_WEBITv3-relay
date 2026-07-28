<script setup lang="ts">
// 儀表板：戰情室 command-center hero（HTML_Mock_戰情室風_v2 為視覺基準）。
// 全部餵真資料——hero 徑向儀表/venn 來自 /api/dashboard/stats，即時清單來自 /api/issues。
// D31精神：不放系統做不到的假資料（歷史趨勢時間序列後端還沒有，故不畫）。
interface DashboardStats {
  environment: string
  ica_count: number
  scanned_count: number
  overlap_count: number
  ica_only_count: number
  scan_only_count: number
  total_ica_count: number
  total_overlap_count: number
  last_scan_time: string | null
  last_scan_ok: boolean
  failed_segments: string[]
  issue_counts: Record<string, number>
}
interface IssueRow {
  id: number
  detected_at: string
  hostname: string | null
  ip: string | null
  issue_type: string
}

const { apiFetch } = useApi()

const ENV_OPTIONS = [
  { value: '正式', label: '正式' },
  { value: '正式+測試', label: '正式＋測試' },
  { value: '全部', label: '全部（含備援）' },
]

const environment = ref('正式')
const stats = ref<DashboardStats | null>(null)
const issues = ref<IssueRow[]>([])
const loading = ref(false)
const errorMessage = ref('')
const { showToast } = useToast()
const scanning = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

async function loadStats() {
  loading.value = true
  errorMessage.value = ''
  try {
    stats.value = await apiFetch<DashboardStats>('/api/dashboard/stats', {
      params: { environment: environment.value },
    })
  } catch {
    errorMessage.value = '儀表板資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
async function loadIssues() {
  try {
    issues.value = await apiFetch<IssueRow[]>('/api/issues', { params: { is_read: false } })
  } catch {
    issues.value = []
  }
}
async function reloadAll() {
  await Promise.all([loadStats(), loadIssues()])
}

await reloadAll()
watch(environment, loadStats)

// 重新掃描：四態（Idle→Pending→Success/Error），背景跑、輪詢狀態、完成刷新儀表板。
function pollScan(startTs: number) {
  const TIMEOUT = 120000
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    if (Date.now() - startTs > TIMEOUT) {
      if (pollTimer) clearInterval(pollTimer)
      scanning.value = false
      showToast('掃描逾時，請稍後再查看結果', 'error')
      return
    }
    let s: any
    try {
      s = await apiFetch<any>('/api/scan/status')
    } catch {
      return
    }
    if (s.running) return
    if (pollTimer) clearInterval(pollTimer)
    scanning.value = false
    if (s.status === 'ok') {
      showToast(`已重新掃描，找到 ${s.found_count} 台活著的主機`, 'success')
      await reloadAll()
    } else if (s.status === 'failed') {
      showToast(`掃描失敗：${s.error ?? '未知原因，請稍後重試'}`, 'error')
    }
  }, 1500)
}

async function triggerRescan() {
  if (scanning.value) return
  scanning.value = true
  try {
    await apiFetch('/api/scan/run', { method: 'POST' })
  } catch (err: any) {
    scanning.value = false
    const detail = err?.data?.detail
    showToast(detail ?? '啟動掃描失敗，請稍後重試', detail ? 'warn' : 'error')
    return
  }
  pollScan(Date.now())
}

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

const _init = await apiFetch<any>('/api/scan/status').catch(() => null)
if (_init?.running) {
  scanning.value = true
  pollScan(Date.now())
}

const totalIssueCount = computed(() =>
  stats.value ? Object.values(stats.value.issue_counts).reduce((s, n) => s + n, 0) : 0,
)
// 一致率＝相符 ÷（登記的 ∪ 網路上掃到的）。徑向儀表 r=86 → 圓周 540。
//
// ⚠️ 分母不能只用「登記數」。舊版是 overlap / ica_count，只回答「我登記的東西還在不在」，
// 完全不問「網路上有多少東西我沒登記」——結果是**登記越少分數越高，一台都不登記就是滿分 100%**。
// 對資產盤點系統語意剛好相反，而且這是頭條數字。實測：網路 6 台、只登記 2 台、4 台沒納管，
// 舊公式顯示 100%。改成聯集後同一份資料是 33%，才對得起「盤點完整度」這個意思。
//
// 用 total_* 是刻意的：那些「掃到卻沒登記」的機器不屬於任何環境，跟環境篩選過的登記數混用
// 會讓分數隨下拉選單跳動，但實際狀況沒變。詳見 api.py dashboard_stats 的註解。
const consistency = computed(() => {
  const s = stats.value
  if (!s) return 0
  const denom = s.total_ica_count + s.scan_only_count
  if (!denom) return 0
  return Math.min(100, (s.total_overlap_count / denom) * 100)
})
const dashOffset = computed(() => 540 * (1 - consistency.value / 100))
// ⚠️ hero 兩框一律用**全站**數字，不吃環境下拉——跟一致率同一個理由：
// 頭條數字被環境偷偷過濾，但標籤沒說，人會直接誤讀。實測踩過：掃描結果頁明明 8 台已登記，
// 儀表板「目前資產」卻寫 2（因為預設只看「正式」，另外 6 台登記在「測試」）。
// 環境篩選仍然作用在下方的明細與下鑽，那裡是分析用途、有標明範圍。
const totalAssetCount = computed(() => stats.value?.total_ica_count ?? 0)

// 納管四態（互斥且窮盡：每台機器剛好落在一格，加總＝所有知道的機器）。
// 比「資產數／異常數」更有用的原因：每一格都直接對應一個明確動作。
interface ManageState {
  counts: Record<string, number>
  total_known: number
  next_action: Record<string, string>
  scan_time: string | null
}
const mstate = ref<ManageState | null>(null)
try {
  mstate.value = await apiFetch<ManageState>('/api/manage-state')
} catch { /* 拿不到就退回不顯示這一區，不擋整頁 */ }

const STATE_ORDER = ['未登記', '未納管', '已納管', '失聯'] as const
const STATE_SUB: Record<string, string> = {
  未登記: '掃到了但沒登記',
  未納管: '登記了但連不進去',
  已納管: '收得到主機名/OS/序號',
  失聯: '登記在案但這次掃不到',
}
const STATE_LINK: Record<string, string> = {
  未登記: '/adopt',
  未納管: '/assets',
  已納管: '/assets',
  失聯: '/issues',
}
function stateCount(k: string) { return mstate.value?.counts?.[k] ?? 0 }

// 組成統計：儀表板該回答「我的機器長什麼樣子」（幾台 Windows／虛實／環境），
// 那是統計；「兩邊相符／登記卻掃不到」是對帳細節，屬於小功能，不該佔戰情室頭條。
interface Composition {
  total: number
  by_platform: Record<string, number>
  by_environment: Record<string, number>
  by_virtualization: Record<string, number>
  by_status: Record<string, number>
  os_from_facts: number
  os_guessed: number
}
const comp = ref<Composition | null>(null)
try {
  comp.value = await apiFetch<Composition>('/api/dashboard/composition')
} catch { /* 拿不到就不顯示這一區，不擋整頁 */ }

const PLATFORM_COLOR: Record<string, string> = {
  Windows: '#7fb3ea', Linux: '#4fe3c0', 'AIX/Unix': '#c9a6ff',
  網路設備: '#ffb867', 未知: '#5f7d72',
}
function segments(m: Record<string, number> | undefined, colors?: Record<string, string>) {
  const e = Object.entries(m ?? {}).sort((a, b) => b[1] - a[1])
  const total = e.reduce((s, [, n]) => s + n, 0) || 1
  return e.map(([k, n]) => ({
    key: k, n, pct: (n / total) * 100,
    color: colors?.[k] ?? (k === '虛擬機' || k === '正式' ? '#4fe3c0' : '#7fb3ea'),
  }))
}
// 異常資產＝登記卻掃不到 ＋ 掃到卻沒登記。刻意不含「兩邊相符」——那些是正常的。
// 登記卻掃不到（全站）＝ 全站登記數 − 全站相符數。
const abnormalCount = computed(() => {
  const s = stats.value
  if (!s) return 0
  return (s.total_ica_count - s.total_overlap_count) + s.scan_only_count
})
const ic = (k: string) => stats.value?.issue_counts?.[k] ?? 0

// 每個磚塊/數字都要能點進去看是哪幾台——只看得到數量卻不知道是誰，等於還是要自己去查。
// 環境選擇（正式／正式＋測試／全部）一併帶進下鑽，後端 /api/assets 認得同一組 preset，
// 所以點進去的筆數會跟磚塊上的數字一致（有 test_dashboard_drilldown.py 守著）。
const drill = computed(() => {
  const env = environment.value
  return {
    allAssets: { path: '/assets', query: { environment: env } },
    overlap: { path: '/assets', query: { scan_status: 'overlap', environment: env } },
    icaOnly: { path: '/assets', query: { scan_status: 'ica_only', environment: env } },
    // 「掃到卻沒登記」＝納入管理的候選清單，本來就有專頁
    scanOnly: { path: '/adopt' },
    // 掃描側的事實（這次網路上活著的機器），與 ICA 登記側不同，另有專頁
    scanned: { path: '/scan-results' },
    issue: (t: string) => ({ path: '/issues', query: { type: t } }),
  }
})

function dotColor(t: string): string {
  return t === '異常消失' ? '#ff6b6b' : t === '漏登記' ? '#7fb3ea' : '#ffb867'
}
function typeColor(t: string): string {
  return t === '異常消失' ? '#ff6b6b' : t === '漏登記' ? '#7fb3ea' : '#ffb867'
}
function fmtTime(s: string): string {
  return s ? s.replace(/^\d{4}-/, '').slice(0, 14) : ''
}
</script>

<template>
  <div class="dash">
    <div class="head">
      <div class="title">
        <div class="ey">ASSET · WAR ROOM</div>
        <h1>資產盤點戰情室</h1>
      </div>
      <div class="right">
        <label class="envwrap">環境
          <select v-model="environment">
            <option v-for="opt in ENV_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <button class="scanbtn" :disabled="scanning" @click="triggerRescan">
          <span v-if="scanning" class="spin" />{{ scanning ? '掃描中…' : '↻ 重新掃描' }}
        </button>
      </div>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="stats">
      <div class="lastscan">
        最後掃描 · {{ stats.last_scan_time ?? '尚未執行過' }}
        <span class="hbadge" :class="{ warn: !stats.last_scan_ok }">
          {{ stats.last_scan_ok ? '所有網段完成' : `${stats.failed_segments.length} 個網段掃描失敗` }}
        </span>
      </div>

      <div class="stage">
        <!-- HERO：招牌是「我管多少台」——公司 3000+ 台，這才是重點 -->
        <div class="glass hero glow-teal">
          <div class="herotext">
            <!-- 總數當招牌 -->
            <div class="headline">
              <NuxtLink to="/assets" class="bignum" title="看全部登記資產">
                <div class="hn">{{ totalAssetCount.toLocaleString() }}</div>
                <div class="hl">資產總數 · 已登記在案</div>
              </NuxtLink>
              <div class="netside">
                <div class="ns-row">網路上發現 <b>{{ mstate ? mstate.total_known : totalAssetCount }}</b> 台</div>
                <!-- 未登記只講這一次（原本四格＋警示框＋漏登記共講三次，重複）-->
                <NuxtLink v-if="mstate && stateCount('未登記') > 0" to="/adopt" class="ns-alert">
                  ⚠ {{ stateCount('未登記') }} 台在網路上但沒登記 · 納入管理 →
                </NuxtLink>
                <div v-else class="ns-ok">✓ 網路上沒有未登記的主機</div>
              </div>
            </div>

            <!-- 納管進度：四態縮成一條，不再是四個大方塊 -->
            <div v-if="mstate" class="progress">
              <div class="pgk">納管進度</div>
              <div class="pgbar">
                <NuxtLink v-for="k in STATE_ORDER.filter((x) => x !== '未登記')" :key="k"
                          :to="STATE_LINK[k]" class="pgseg" :class="`st-${k}`"
                          :style="{ flexGrow: stateCount(k) || 0.001 }"
                          :title="`${k} ${stateCount(k)} 台：${mstate.next_action?.[k]}`" />
              </div>
              <div class="pgleg">
                <NuxtLink v-for="k in STATE_ORDER.filter((x) => x !== '未登記')" :key="k"
                          :to="STATE_LINK[k]" class="pgi" :class="`st-${k}`"
                          :title="mstate.next_action?.[k]">
                  {{ k }}<b>{{ stateCount(k) }}</b>
                </NuxtLink>
              </div>
            </div>

            <!-- 組成統計＝「素材」：我的機器長什麼樣子（每段可點）-->
            <template v-if="comp">
              <div class="compsec">
                <div class="ck">作業系統平台</div>
                <div class="bar">
                  <span v-for="g in segments(comp.by_platform, PLATFORM_COLOR)" :key="g.key"
                        class="seg" :style="{ width: g.pct + '%', background: g.color }"
                        :title="`${g.key} ${g.n} 台`" />
                </div>
                <div class="cleg">
                  <NuxtLink v-for="g in segments(comp.by_platform, PLATFORM_COLOR)" :key="g.key"
                            :to="{ path: '/assets', query: { filter_field: 'os', filter_value: '' } }"
                            class="ci" :title="`看 ${g.key} 的資產`">
                    <span class="sw" :style="{ background: g.color }" />{{ g.key }}<b>{{ g.n }}</b>
                  </NuxtLink>
                </div>
              </div>
              <div class="compsec">
                <div class="ck">虛擬 / 實體</div>
                <div class="cleg">
                  <NuxtLink v-for="g in segments(comp.by_virtualization)" :key="g.key"
                            :to="{ path: '/assets', query: { virtual: g.key === '虛擬機' ? 'yes' : 'no' } }"
                            class="ci" :title="`看${g.key}是哪幾台`">
                    <span class="sw" :style="{ background: g.color }" />{{ g.key }}<b>{{ g.n }}</b>
                  </NuxtLink>
                </div>
              </div>
              <div class="compsec">
                <div class="ck">環境別</div>
                <div class="cleg">
                  <NuxtLink v-for="g in segments(comp.by_environment)" :key="g.key"
                            :to="{ path: '/assets', query: { filter_field: 'environment', filter_value: g.key } }"
                            class="ci">
                    <span class="sw" :style="{ background: g.color }" />{{ g.key }}<b>{{ g.n }}</b>
                  </NuxtLink>
                </div>
              </div>
              <div class="osnote">
                OS 來源：{{ comp.os_from_facts }} 台實際收集、{{ comp.os_guessed }} 台由掃描推測
              </div>
            </template>
          </div>
        </div>

        <!-- 右側：異動指標（未登記已在 hero 講過，這裡不重複，只放「跟上次比的變化」）-->
        <div class="stack">
          <div class="mini">
            <NuxtLink :to="drill.issue('異常新增')" class="glass stat"><div class="n amber">{{ ic('異常新增') }}</div><div class="lab">異常新增（這次多出來的）</div></NuxtLink>
            <NuxtLink :to="drill.issue('異常消失')" class="glass stat"><div class="n red">{{ ic('異常消失') }}</div><div class="lab">異常消失（登記卻掃不到）</div></NuxtLink>
            <NuxtLink :to="drill.scanned" class="glass stat"><div class="n teal">{{ stats.scanned_count }}</div><div class="lab">本次掃到存活</div></NuxtLink>
            <NuxtLink to="/assets" class="glass stat"><div class="n blue">{{ comp ? comp.os_from_facts : '—' }}</div><div class="lab">已收到真實資料</div></NuxtLink>
          </div>
        </div>
      </div>

      <!-- 問題即時清單 -->
      <div class="glass feed">
        <h3>
          <span class="beat" />問題即時清單 · {{ totalIssueCount }} 筆待處理
          <NuxtLink to="/issues" class="h3link">全部問題 →</NuxtLink>
        </h3>
        <div v-if="issues.length === 0" class="empty">目前沒有待處理的問題 🎉</div>
        <NuxtLink
          v-for="row in issues.slice(0, 12)"
          :key="row.id"
          :to="drill.issue(row.issue_type)"
          class="frow"
        >
          <span class="dot" :style="{ background: dotColor(row.issue_type) }" />
          <span class="host">{{ row.hostname || '—' }}</span>
          <span class="ip">{{ row.ip || '—' }}</span>
          <span class="type" :style="{ color: typeColor(row.issue_type) }">{{ row.issue_type }}</span>
          <span class="time">{{ fmtTime(row.detected_at) }}</span>
        </NuxtLink>
        <!-- 原本連到 /assets——問題不是資產，連錯地方了；/issues 才是問題清單 -->
        <NuxtLink v-if="issues.length > 12" to="/issues" class="morelink">查看全部 {{ issues.length }} 筆 →</NuxtLink>
      </div>
    </template>
    <p v-else-if="loading" class="loading">載入中…</p>
  </div>
</template>

<style scoped>
.dash { font-family: 'Microsoft JhengHei', sans-serif; }
.head { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 24px; flex-wrap: wrap; gap: 14px; }
.title .ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: #4fe3c0; text-transform: uppercase; }
.title h1 { font-family: var(--disp); font-size: 26px; font-weight: 600; margin: 4px 0 0; color: #fff; letter-spacing: -.5px; }
.right { display: flex; align-items: center; gap: 14px; }
.envwrap { font-size: 12.5px; color: var(--muted); display: inline-flex; align-items: center; gap: 8px; }
.envwrap select { font-family: inherit; font-size: 12.5px; padding: 7px 10px; }
.scanbtn { font-family: inherit; font-size: 13px; font-weight: 700; padding: 9px 18px; border: none; border-radius: 10px;
  background: linear-gradient(135deg,#2fd6ac,#1e8a6f); color: #04120e; cursor: pointer; box-shadow: 0 6px 20px rgba(47,214,172,.3);
  display: inline-flex; align-items: center; gap: 8px; }
.scanbtn:disabled { opacity: .7; cursor: not-allowed; }
.spin { width: 13px; height: 13px; border: 2px solid rgba(4,18,14,.4); border-top-color: #04120e; border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.lastscan { font-family: var(--disp); font-size: 12px; color: var(--muted); margin-bottom: 18px; display: flex; align-items: center; gap: 12px; }
.hbadge { font-size: 11px; padding: 3px 10px; border-radius: 20px; background: rgba(47,214,172,.14); color: #4fe3c0; font-family: 'Microsoft JhengHei'; }
.hbadge.warn { background: rgba(255,184,103,.16); color: #ffb867; }
.error-text { color: var(--bad); font-size: 13px; margin-bottom: 14px; }
.loading { color: var(--muted); }

.glass { background: rgba(255,255,255,.035); border: 1px solid rgba(255,255,255,.07); border-radius: 20px; backdrop-filter: blur(10px); }
.glow-teal { box-shadow: 0 0 20px rgba(47,214,172,.15); }
.stage { display: grid; grid-template-columns: 1.15fr 1fr; gap: 22px; align-items: stretch; }

.hero { padding: 26px 30px; display: block; }
.herotext { width: 100%; }
/* 招牌：資產總數（公司 3000+ 台，這才是重點）*/
.headline { display: flex; align-items: center; gap: 28px; flex-wrap: wrap;
  padding-bottom: 18px; margin-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,.08); }
.bignum { text-decoration: none; color: inherit; }
.bignum .hn { font-family: var(--disp); font-size: 64px; font-weight: 700; line-height: 1;
  color: #fff; letter-spacing: -3px; text-shadow: 0 0 30px rgba(47,214,172,.35); }
.bignum:hover .hn { color: #4fe3c0; }
.bignum .hl { font-size: 12px; color: #7fa89b; letter-spacing: 1px; margin-top: 6px; }
.netside { display: flex; flex-direction: column; gap: 8px; }
.netside .ns-row { font-size: 13px; color: #9fc4b8; }
.netside .ns-row b { font-family: var(--disp); font-size: 18px; color: #fff; }
.netside .ns-alert { display: inline-block; font-size: 12.5px; font-weight: 700; color: #ffb867;
  background: rgba(255,184,103,.12); border: 1px solid rgba(255,184,103,.3);
  padding: 5px 12px; border-radius: 999px; text-decoration: none; }
.netside .ns-alert:hover { background: rgba(255,184,103,.2); }
.netside .ns-ok { font-size: 12.5px; color: #4fe3c0; }
/* 納管進度：一條，不是四個大方塊 */
.progress { margin-bottom: 16px; }
.progress .pgk { font-size: 10.5px; color: #7fa89b; letter-spacing: 1px; margin-bottom: 5px; }
.pgbar { display: flex; height: 10px; border-radius: 999px; overflow: hidden;
  background: rgba(255,255,255,.06); margin-bottom: 6px; }
.pgseg { display: block; }
.pgseg.st-已納管 { background: #4fe3c0; }
.pgseg.st-未納管 { background: #7fb3ea; }
.pgseg.st-失聯 { background: #ff8f8f; }
.pgleg { display: flex; flex-wrap: wrap; gap: 4px 14px; }
.pgi { display: flex; align-items: center; gap: 4px; font-size: 12px; text-decoration: none; color: #9fc4b8; }
.pgi:hover { color: #fff; }
.pgi b { font-family: var(--disp); font-size: 14px; margin-left: 2px; }
.pgi.st-已納管 b { color: #4fe3c0; }
.pgi.st-未納管 b { color: #7fb3ea; }
.pgi.st-失聯 b { color: #ff8f8f; }
.hero_legacy { padding: 28px 30px; display: flex; align-items: center; gap: 30px; }
.gauge { position: relative; width: 200px; height: 200px; flex-shrink: 0; }
.gauge .pct { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.gauge .pct b { font-family: var(--disp); font-size: 44px; font-weight: 700; color: #fff; letter-spacing: -2px; line-height: 1; text-shadow: 0 0 24px rgba(47,214,172,.4); }
.gauge .pct span { font-size: 11px; color: #7fa89b; letter-spacing: 2px; margin-top: 6px; }
.herotext .k { font-size: 12px; color: #7fa89b; letter-spacing: 1px; }
/* 兩個框：目前資產 / 異常資產（取代原本的「N vs M」，那個對比看不出要做什麼） */
.twobox { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
/* 納管四態：互斥且窮盡，每格對應一個動作 */
.fourbox { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 8px; }
.fourbox .box { padding: 10px 12px; }
.fourbox .bn { font-size: 28px; }
.fourbox .st-未登記 .bn { color: #ffb867; }
.fourbox .st-未納管 .bn { color: #7fb3ea; }
.fourbox .st-已納管 .bn { color: #4fe3c0; }
.fourbox .st-失聯 .bn { color: #ff8f8f; }
.fourbox .box.zero .bn { color: #5f7d72; }
.totals { font-size: 11px; color: #5f7d72; margin-bottom: 14px; }
.compsec { margin-bottom: 12px; }
.compsec .ck { font-size: 10.5px; color: #7fa89b; letter-spacing: 1px; margin-bottom: 5px; }
.bar { display: flex; height: 8px; border-radius: 999px; overflow: hidden; background: rgba(255,255,255,.06); margin-bottom: 6px; }
.bar .seg { height: 100%; }
.cleg { display: flex; flex-wrap: wrap; gap: 4px 14px; }
.cleg .ci { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #9fc4b8; text-decoration: none; }
.cleg .ci:hover { color: #fff; }
.cleg .ci b { color: #fff; font-family: var(--disp); font-size: 14px; margin-left: 2px; }
.cleg .sw { width: 8px; height: 8px; border-radius: 2px; }
.osnote { font-size: 10.5px; color: #5f7d72; margin-top: 8px; }
.totals b { color: #9fc4b8; font-weight: 700; }
.twobox .box {
  display: block; text-decoration: none; padding: 12px 14px; border-radius: 14px;
  background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.08);
  transition: border-color .2s, background .2s;
}
.twobox .box:hover { border-color: rgba(47,214,172,.5); background: rgba(47,214,172,.07); }
.twobox .bk { font-size: 11px; color: #7fa89b; letter-spacing: 1px; }
.twobox .bn { font-family: var(--disp); font-size: 34px; font-weight: 700; color: #fff; line-height: 1.15; letter-spacing: -1px; }
.twobox .bs { font-size: 10.5px; color: #5f7d72; }
.twobox .box.bad .bn { color: #ff8f8f; }
.twobox .box.bad:hover { border-color: rgba(255,107,107,.5); background: rgba(255,107,107,.07); }
.twobox .box.bad.zero .bn { color: #4fe3c0; }
/* 明細的範圍標示：hero 兩框是全站、下面是環境篩選過的，不標會被當成同一把尺 */
.scope { font-size: 10.5px; color: #5f7d72; letter-spacing: .5px; margin-bottom: 5px; }
.twobox .box.bad.zero:hover { border-color: rgba(47,214,172,.5); background: rgba(47,214,172,.07); }
/* ===== 下鑽：每個數字都是連結，但不能長得像一堆藍色底線把戰情室弄髒 =====
   做法是保留原本的排版，只加「可點」的訊號：hover 時提亮 + 左移一點 + 出現箭頭。 */
.legend { display: flex; flex-direction: column; gap: 10px; font-size: 13px; }
.legend .row {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #b8ccc4;
  text-decoration: none;
  padding: 3px 6px;
  margin: -3px -6px;
  border-radius: 7px;
  transition: background .16s ease, color .16s ease;
}
.legend .row:hover { background: rgba(255, 255, 255, .06); color: #eaf5f1; }
.legend .row::after {
  content: '→';
  opacity: 0;
  margin-left: 4px;
  font-size: 11px;
  transition: opacity .16s ease;
}
.legend .row:hover::after { opacity: .65; }

/* hero 大數字（ICA vs 掃描） */
.numlink {
  color: inherit;
  text-decoration: none;
  border-bottom: 1px dashed transparent;
  transition: color .16s ease, border-color .16s ease;
}
.numlink:hover { color: #4fe3c0; border-bottom-color: rgba(79, 227, 192, .5); }

/* 圓環中央的一致率 */
a.pct { text-decoration: none; color: inherit; cursor: pointer; }
a.pct:hover b { color: #4fe3c0; transition: color .16s ease; }

/* 右側四格指標：整格可點 */
a.stat { display: block; text-decoration: none; color: inherit; cursor: pointer; position: relative; }
a.stat::after {
  content: '→';
  position: absolute;
  top: 14px;
  right: 14px;
  font-size: 12px;
  opacity: 0;
  transition: opacity .16s ease;
}
a.stat:hover::after { opacity: .55; }
a.stat:hover { border-color: rgba(38, 168, 137, .45); }
.legend .sw { width: 9px; height: 9px; border-radius: 3px; }
.legend b { font-family: var(--disp); color: #fff; margin-left: auto; font-variant-numeric: tabular-nums; }

.stack { display: flex; flex-direction: column; gap: 14px; }
.alert { padding: 20px 22px; border-radius: 20px; background: linear-gradient(135deg, rgba(232,124,7,.16), rgba(232,124,7,.05));
  border: 1px solid rgba(232,124,7,.35); display: flex; align-items: center; justify-content: space-between; }
.alert.ok { background: linear-gradient(135deg, rgba(47,214,172,.12), rgba(47,214,172,.03)); border-color: rgba(47,214,172,.3); }
.alert .n { font-family: var(--disp); font-size: 40px; font-weight: 700; color: #ffb867; letter-spacing: -1.5px; line-height: 1; }
.alert .lab { font-size: 12px; color: #d9b48a; margin-top: 4px; }
.alert.ok .lab { color: #8fbfae; }
.alert .go { color: #ffb867; border: 1px solid rgba(255,184,103,.5); border-radius: 9px; padding: 8px 14px; font-size: 12px; font-weight: 700; text-decoration: none; white-space: nowrap; }
.alert .go:hover { background: rgba(255,184,103,.12); }
.mini { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; flex: 1; }
.stat { padding: 16px 18px; }
.stat .n { font-family: var(--disp); font-size: 30px; font-weight: 600; letter-spacing: -1px; line-height: 1; }
.stat .lab { font-size: 11.5px; color: #8fa89f; margin-top: 6px; }
.n.teal { color: #4fe3c0; } .n.red { color: #ff6b6b; } .n.amber { color: #ffb867; } .n.blue { color: #7fb3ea; }

.feed { margin-top: 22px; padding: 22px 26px; }
.feed h3 { font-family: var(--disp); font-size: 13px; letter-spacing: 1px; text-transform: uppercase; color: #7fa89b; margin: 0 0 14px; display: flex; align-items: center; gap: 8px; }
.feed .beat { width: 7px; height: 7px; border-radius: 50%; background: #2fd6ac; animation: beat 1.8s infinite; }
@keyframes beat { 0%{box-shadow:0 0 0 0 rgba(47,214,172,.55)} 70%{box-shadow:0 0 0 8px rgba(47,214,172,0)} 100%{box-shadow:0 0 0 0 rgba(47,214,172,0)} }
.empty { color: #7fa89b; font-size: 13px; padding: 10px 0; }
.frow { display: flex; align-items: center; gap: 14px; padding: 11px 0; border-bottom: 1px solid rgba(255,255,255,.05); font-size: 13px; text-decoration: none; color: inherit; }
.frow:hover { background: rgba(255,255,255,.04); }
.frow:last-child { border-bottom: none; }
.feed h3 .h3link { margin-left: auto; font-size: 11.5px; letter-spacing: 0; text-transform: none; color: #26a889; text-decoration: none; }
.feed h3 .h3link:hover { text-decoration: underline; }
.frow .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.frow .host { font-family: var(--disp); color: #dfeee9; width: 150px; }
.frow .ip { font-family: var(--disp); color: #8fa89f; width: 140px; font-size: 12px; }
.frow .type { flex: 1; }
.frow .time { margin-left: auto; color: #6f8880; font-size: 12px; font-family: var(--disp); }
.morelink { display: inline-block; margin-top: 14px; font-size: 12.5px; color: #4fe3c0; text-decoration: none; }
.morelink:hover { text-decoration: underline; }

@media (max-width: 900px) { .stage { grid-template-columns: 1fr; } .hero { flex-direction: column; text-align: center; } }
</style>
