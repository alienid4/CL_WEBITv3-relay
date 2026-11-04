<script setup lang="ts">
// 10.1 效能圖表（2.007.19）：NB 交辦「顆粒太粗」「nmon 只有 cpu 不夠」「要給連結」。
// 資料：上傳的 NMON 檔（AIX；Linux 不用 NMON）。每分鐘一點全部留。
// 狀態全在網址（host／month／組別 c／圖 k／顆粒 grain／某天 day／時間點 at／Y 軸 y／展開的卡 open）：複製網址打開就是同一畫面、上一頁也回得去。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
const qs = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '')

function url(p: Record<string, any>) {
  const base: Record<string, any> = { ...route.query, tab: 'perf', ...p }
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(base)) if (v !== null && v !== undefined && v !== '') sp.set(k, String(v))
  return `/v2/report?${sp.toString()}`
}
const go = (p: Record<string, any>) => router.push(url(p))
async function copyLink(p: Record<string, any> = {}) {
  const full = `${window.location.origin}${url(p)}`
  try { await navigator.clipboard.writeText(full); showToast('已複製連結，貼給別人打開就是這個畫面', 'success') } catch { showToast(`瀏覽器不允許複製，請手動複製：${full}`, 'warn') }
}

// ===== 主機與月份 =====
const hostsData = ref<any>(null)
const hostsError = ref('')
async function loadHosts() {
  try { hostsData.value = await apiFetch<any>('/api/v2/nmon/hosts', { query: qs('month') ? { month: qs('month') } : {} }); hostsError.value = '' } catch (e: any) { hostsError.value = `載入失敗：${errMsg(e)}` }
}
const months = computed<string[]>(() => hostsData.value?.months ?? [])
const month = computed(() => qs('month') || months.value[0] || '')
const hostList = computed<any[]>(() => hostsData.value?.hosts ?? [])
const host = computed(() => qs('host') || hostList.value[0]?.host || '')
const hostInfo = computed(() => hostList.value.find(h => h.host === host.value))
const files = computed<any[]>(() => (hostsData.value?.files ?? []).filter((f: any) => f.host === host.value))

// ===== 摘要（卡片）與圖 =====
const sum = ref<any>(null)
const sumError = ref('')
const grain = computed(() => (['day', 'hour', 'raw'].includes(qs('grain')) ? qs('grain') : (qs('day') ? 'raw' : 'hour')) as 'day' | 'hour' | 'raw')
const day = computed(() => qs('day'))
const ymode = computed(() => (qs('y') === '100' ? '100' : 'auto') as 'auto' | '100')
const CPU_ORDER = ['cpu_busy', 'cpu_user', 'cpu_sys', 'cpu_wait', 'cpu_idle']
const CHARTS: Record<string, { id: string; label: string; unit: string; pick: (k: string) => boolean; def?: string[] }[]> = {
  cpu: [{ id: 'pct', label: '使用率 %', unit: '%', pick: k => CPU_ORDER.includes(k), def: ['cpu_busy'] },
    { id: 'runq', label: '排隊數', unit: '', pick: k => k === 'runq' }],
  mem: [{ id: 'pct', label: '使用率', unit: '%', pick: k => ['mem_used', 'swap_used'].includes(k) },
    { id: 'pg', label: '換頁', unit: '/s', pick: k => k.startsWith('pg'), def: ['pgsin', 'pgsout'] }],
  disk: [{ id: 'busy', label: '忙碌 %', unit: '%', pick: k => k.startsWith('disk:') && k.endsWith(':busy') },
    { id: 'rd', label: '讀 KB/s', unit: 'KB/s', pick: k => k.startsWith('disk:') && k.endsWith(':rd') },
    { id: 'wr', label: '寫 KB/s', unit: 'KB/s', pick: k => k.startsWith('disk:') && k.endsWith(':wr') }],
  net: [{ id: 'rx', label: '收 KB/s', unit: 'KB/s', pick: k => k.endsWith(':rx') && k.startsWith('nic:') },
    { id: 'tx', label: '送 KB/s', unit: 'KB/s', pick: k => k.endsWith(':tx') && k.startsWith('nic:') },
    { id: 'err', label: '錯誤數', unit: '', pick: k => k.endsWith(':err') && k.startsWith('nic:') }],
  fs: [{ id: 'fs', label: '使用率 %', unit: '%', pick: k => k.startsWith('fs:') }],
}
const GROUP_LABEL: Record<string, string> = { cpu: 'CPU', mem: '記憶體與換頁', disk: '磁碟', net: '網路', fs: '檔案系統' }
const groupIds = Object.keys(GROUP_LABEL)
const items = computed<any[]>(() => sum.value?.items ?? [])
const collected = computed<string[]>(() => sum.value?.collected_groups ?? [])
const gid = computed(() => (groupIds.includes(qs('c')) ? qs('c') : 'cpu'))
const charts = computed(() => CHARTS[gid.value])
const chart = computed(() => charts.value.find(c => c.id === qs('k')) ?? charts.value[0])
// 卡片與圖例的順序：CPU 照 忙碌→user→sys→wait→idle，其餘照名稱
const chartItems = computed(() => items.value.filter(i => chart.value.pick(i.key)).sort((a, b) => (CPU_ORDER.indexOf(a.key) < 0 ? 99 : CPU_ORDER.indexOf(a.key)) - (CPU_ORDER.indexOf(b.key) < 0 ? 99 : CPU_ORDER.indexOf(b.key)) || a.key.localeCompare(b.key)))
const COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)', 'var(--chart-gray)']
const off = ref<Record<string, boolean>>({})
watch([gid, () => chart.value.id, host], () => { off.value = {} })
const isOn = (key: string) => (chart.value.def ? chart.value.def.includes(key) !== !!off.value[key] : !off.value[key])
const toggle = (key: string) => { off.value = { ...off.value, [key]: !off.value[key] } }

const ser = ref<any>(null)
const serError = ref('')
async function loadSum() {
  sum.value = null
  if (!host.value || !month.value) return
  try { sum.value = await apiFetch<any>('/api/v2/nmon/summary', { query: { host: host.value, month: month.value } }); sumError.value = '' } catch (e: any) { sumError.value = `摘要載入失敗：${errMsg(e)}` }
}
async function loadSeries() {
  ser.value = null
  if (!host.value || !month.value || !sum.value) return
  const keys = chartItems.value.map(i => i.key)
  if (!keys.length) return
  try {
    ser.value = await apiFetch<any>('/api/v2/nmon/series', { query: { host: host.value, month: month.value, grain: grain.value, ...(grain.value === 'raw' && day.value ? { day: day.value } : {}), keys: keys.join(',') } })
    serError.value = ''
  } catch (e: any) { serError.value = `圖表載入失敗：${errMsg(e)}` }
}
const drawn = computed(() => chartItems.value.filter(i => isOn(i.key) && ser.value?.series?.[i.key]).map((i, n) => ({ key: i.key, label: i.label, color: COLORS[chartItems.value.indexOf(i) % COLORS.length], pts: ser.value.series[i.key] })))
const thr = computed(() => { const t = chartItems.value.find(i => isOn(i.key) && i.threshold != null)?.threshold; return t ?? null })

// ===== 時間點：最吃 CPU 的程式 =====
const cpuPeak = computed(() => items.value.find(i => i.key === 'cpu_busy'))
const at = computed(() => qs('at') || cpuPeak.value?.peak_at || '')
const topData = ref<any>(null)
const topError = ref('')
async function loadTop() {
  topData.value = null
  if (!host.value || !at.value) return
  try { topData.value = await apiFetch<any>('/api/v2/nmon/top', { query: { host: host.value, ts: at.value } }); topError.value = '' } catch (e: any) { topError.value = `TOP 載入失敗：${errMsg(e)}` }
}

// ===== 載入時機 =====
onMounted(loadHosts)
watch(() => qs('month'), loadHosts)
watch([host, month], async () => { await loadSum() }, { immediate: false })
watch(hostsData, async () => { await loadSum() })
watch(sum, () => { loadSeries(); loadTop() })
watch([() => chart.value.id, gid, grain, day], loadSeries)
watch(at, loadTop)

// ===== 互動 =====
function pick(x: string, atTs: string) {
  if (grain.value === 'raw') { go({ at: atTs }); return }
  go({ day: x.slice(0, 10), grain: 'raw', at: atTs, open: null })
}
const drill = computed(() => items.value.find(i => i.key === qs('open')))
const drillRows = computed(() => (drill.value?.over_runs ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, minutes_text: `${r.minutes} 分`, peak_text: `${r.peak}${drill.value.unit}`, where: r.peak_at, link: url({ day: r.from.slice(0, 10), grain: 'raw', at: r.peak_at, open: null }) })))
const fmt = (v: number | null | undefined, u: string) => (v === null || v === undefined ? '—' : `${+v.toFixed(2)}${u}`)
const tone = (i: any) => (i.over ? 'warn' : '')
const MONTH_OPTS = computed(() => [...new Set([month.value, ...months.value].filter(Boolean))])
const dayStep = (d: number) => {
  const t = new Date(`${day.value}T00:00:00`); t.setDate(t.getDate() + d)
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`
}
</script>

<template>
  <div class="ne">
    <p v-if="hostsError" class="err">{{ hostsError }}</p>
    <p v-else-if="!hostsData" class="muted">載入中…</p>
    <p v-else-if="!hostList.length" class="muted">這個月還沒有逐分鐘的 NMON 資料。到「主機組月報」按上方「上傳 NMON 檔」（純文字、.gz、AIX 的 .Z 都可以）。</p>
    <template v-else>
      <div class="tool">
        <label>主機 <select :value="host" @change="go({ host: ($event.target as HTMLSelectElement).value, day: null, at: null, open: null, grain: null })"><option v-for="h in hostList" :key="h.host" :value="h.host">{{ h.host }}</option></select></label>
        <label>月份 <select :value="month" @change="go({ month: ($event.target as HTMLSelectElement).value, day: null, at: null, open: null, grain: null })"><option v-for="m in MONTH_OPTS" :key="m" :value="m">{{ m }}</option></select></label>
        <span class="seg"><button v-for="g in [['day', '日'], ['hour', '小時'], ['raw', '原始']]" :key="g[0]" type="button" :class="{ on: grain === g[0] }" @click="g[0] === 'raw' ? go({ grain: 'raw', day: day || (cpuPeak ? cpuPeak.peak_at.slice(0, 10) : null) }) : go({ grain: g[0], day: null })">{{ g[1] }}</button></span>
        <span v-if="chart.unit === '%'" class="seg"><button type="button" :class="{ on: ymode === 'auto' }" @click="go({ y: null })">Y 軸自動</button><button type="button" :class="{ on: ymode === '100' }" @click="go({ y: '100' })">0–100%</button></span>
        <button type="button" class="btn2" @click="copyLink()">複製連結</button>
        <InfoNote>{{ hostInfo?.host }}：{{ hostInfo?.days }} 天、{{ hostInfo?.samples?.toLocaleString() }} 個取樣點（{{ hostInfo?.from }}～{{ hostInfo?.to }}），取樣間隔 {{ hostInfo?.interval_s ? `${hostInfo.interval_s} 秒` : '未註明' }}，每個取樣點都留著。
          日＝每天一點、小時＝每小時一點（實線平均、細線尖峰）、原始＝取樣間隔的每一點（要選某一天：在日或小時圖上點一下）。
          尖峰的時間點可以點，下方會列出當時最吃 CPU 的程式（NMON 只在程式夠忙時才記，所以取最接近的快照，並標出實際時間）。
          門檻：CPU 忙碌 {{ hostsData?.thresholds?.cpu_busy }}%、記憶體 {{ hostsData?.thresholds?.mem_used }}%、換頁空間 {{ hostsData?.thresholds?.swap_used }}%、檔案系統 {{ hostsData?.thresholds?.fs }}%（另算 {{ hostsData?.thresholds?.fs_crit }}%）、磁碟忙碌 {{ hostsData?.thresholds?.disk_busy }}%。
          沒收集的項目寫「未收集」，不會顯示成 0。AIX 的格式已用真檔驗證；Linux 不用 NMON。</InfoNote>
      </div>
      <div v-if="files.length" class="muted">原始 NMON：<template v-for="(f, i) in files" :key="f.filename"><template v-if="i">、</template><a :href="`${apiBase}/api/v2/nmon/raw?host=${encodeURIComponent(f.host)}&filename=${encodeURIComponent(f.filename)}`">{{ f.filename }}</a></template>（僅管理員可下載）</div>

      <p v-if="sumError" class="err">{{ sumError }}</p>
      <template v-else-if="sum">
        <div class="groups">
          <button v-for="g in groupIds" :key="g" type="button" :class="{ on: gid === g, none: !collected.includes(g) }" @click="go({ c: g, k: null, open: null })">{{ GROUP_LABEL[g] }}<small v-if="!collected.includes(g)">未收集</small></button>
        </div>
        <p v-if="!collected.includes(gid)" class="msg">這台沒有收集到「{{ GROUP_LABEL[gid] }}」的資料：NMON 檔裡沒有這個區段，所以不顯示（不是 0）。</p>
        <template v-else>
          <div v-if="charts.length > 1" class="seg sub"><button v-for="c in charts" :key="c.id" type="button" :class="{ on: chart.id === c.id }" :disabled="!items.some(i => c.pick(i.key))" @click="go({ k: c.id, open: null })">{{ c.label }}{{ items.some(i => c.pick(i.key)) ? '' : '（未收集）' }}</button></div>
          <div class="cards">
            <div v-for="i in chartItems" :key="i.key" class="card" :class="{ off: !isOn(i.key) }">
              <button type="button" class="lg" @click="toggle(i.key)"><i :style="{ background: COLORS[chartItems.indexOf(i) % COLORS.length] }" />{{ i.label }}</button>
              <span class="kv">平均 <b>{{ fmt(i.avg, i.unit) }}</b></span>
              <span class="kv">尖峰 <b><NuxtLink :to="url({ day: i.peak_at.slice(0, 10), grain: 'raw', at: i.peak_at })">{{ fmt(i.peak, i.unit) }}</NuxtLink></b><small>{{ i.peak_at.slice(5, 16) }}</small></span>
              <span v-if="i.over !== null" class="kv">超過 {{ i.threshold }}{{ i.unit }} <b :class="tone(i)"><NuxtLink v-if="i.over" :to="url({ open: qs('open') === i.key ? null : i.key })">{{ i.over }} 次</NuxtLink><template v-else>0 次</template></b></span>
            </div>
          </div>
          <div class="head">
            <h3 class="h3">{{ GROUP_LABEL[gid] }}{{ charts.length > 1 || chart.label !== GROUP_LABEL[gid] ? `・${chart.label}` : '' }}<small>　{{ grain === 'raw' ? `原始（${day}）` : grain === 'hour' ? '每小時' : '每天' }}</small></h3>
            <span v-if="grain === 'raw'" class="daynav"><NuxtLink :to="url({ day: dayStep(-1), at: null })">‹ 前一天</NuxtLink><NuxtLink :to="url({ day: dayStep(1), at: null })">後一天 ›</NuxtLink><NuxtLink :to="url({ grain: 'hour', day: null, at: null })">回整月</NuxtLink></span>
            <button type="button" class="btn2" @click="copyLink()">複製這張圖的連結</button>
          </div>
          <p v-if="serError" class="err">{{ serError }}</p>
          <p v-else-if="!ser" class="muted">載入中…</p>
          <V2NmonChart v-else :series="drawn" :grain="grain" :unit="chart.unit" :ymode="ymode" :threshold="thr" :mark="grain === 'raw' ? qs('at') || null : null" @pick="pick" />
          <p v-if="grain !== 'raw'" class="hint">點圖上任一點：展開那一天（每分鐘）。</p>

          <template v-if="drill">
            <h3 class="h3">{{ drill.label }} 超過 {{ drill.threshold }}{{ drill.unit }} 的時段（{{ drill.over }} 個取樣點、{{ drillRows.length }} 段）</h3>
            <V2Table :rows="drillRows" prefix="T" row-key="from" unit="段" empty="沒有超標時段"
                     :cols="[{ k: 'from', label: '開始', mono: true, link: (r: any) => r.link }, { k: 'to', label: '結束', mono: true }, { k: 'minutes_text', label: '持續', mono: true }, { k: 'peak', label: `尖峰（${drill.unit}）`, mono: true }, { k: 'peak_at', label: '尖峰時間', mono: true }]" />
          </template>

          <h3 class="h3">最吃 CPU 的程式<small>　{{ at ? `時間 ${at}` : '' }}</small></h3>
          <p v-if="topError" class="err">{{ topError }}</p>
          <template v-else-if="topData">
            <p v-if="!topData.procs.length" class="muted">這台這天沒有收 TOP 資料（NMON 要用 -T 或 -I 選項才會記），所以沒有程式清單。</p>
            <template v-else>
              <p class="muted">取最接近的快照 {{ topData.at }}{{ topData.exact ? '' : '（不是同一秒，NMON 只在程式夠忙時才記）' }}</p>
              <V2Table :rows="topData.procs" prefix="P" row-key="pid" unit="個" :cols="[{ k: 'cmd', label: '程式' }, { k: 'pid', label: 'PID', mono: true }, { k: 'cpu', label: '%CPU', mono: true }]" />
            </template>
          </template>
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.ne { display: flex; flex-direction: column; gap: 12px; }
.tool { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.tool select { font: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: 8px; overflow: hidden; }
.seg button { font: inherit; font-size: 13px; padding: 4px 12px; border: 0; background: var(--card); color: var(--ink); cursor: pointer; }
.seg button + button { border-left: 1px solid var(--border-strong); }
.seg button.on { background: var(--brand); color: var(--type-on); }
.seg button:disabled { opacity: .45; cursor: not-allowed; }
.seg.sub { align-self: flex-start; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--link); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.muted { color: var(--ink-aux); font-size: 13px; margin: 0; }
.err { color: var(--bad); margin: 0; }
.msg { margin: 0; color: var(--ink-aux); font-size: 14px; padding: 18px; border: 1px dashed var(--border-strong); border-radius: 10px; }
.groups { display: flex; gap: 8px; flex-wrap: wrap; }
.groups button { font: inherit; font-size: 14px; padding: 6px 16px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; display: inline-flex; gap: 6px; align-items: baseline; }
.groups button.on { background: var(--brand); color: var(--type-on); border-color: var(--brand); }
.groups button.none { color: var(--ink-aux); border-style: dashed; }
.groups small { font-size: 11px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; }
.card { border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; background: var(--sub); display: flex; flex-direction: column; gap: 3px; }
.card.off { opacity: .5; }
.lg { font: inherit; font-weight: 600; font-size: 13px; border: 0; background: none; text-align: left; cursor: pointer; padding: 0; color: var(--brand-dark); }
.lg i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.kv { font-size: 12.5px; color: var(--ink-soft); display: flex; gap: 6px; align-items: baseline; }
.kv b { font-family: var(--disp); font-size: 16px; color: var(--ink); }
.kv b.warn, .kv b.warn a { color: var(--warn-text); }
.kv small { color: var(--ink-aux); font-size: 11px; }
.head { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
.h3 { margin: 0; font-size: 15px; font-weight: 700; color: var(--brand-dark); }
.h3 small { font-weight: 400; color: var(--ink-aux); font-size: 12.5px; }
.daynav { display: inline-flex; gap: 12px; font-size: 13px; }
.hint { margin: 0; font-size: 12px; color: var(--ink-aux); }
</style>
