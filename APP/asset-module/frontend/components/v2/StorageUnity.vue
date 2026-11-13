<script setup lang="ts">
// 5.2 儲存容量的「Unity 陣列」頁籤（devkit D2/D3 的 WEBIT3 端）：中繼匯入的 Dell Unity 快照，唯讀。
// 資料：GET /api/v2/relay-import/unity（storage_relay.overview）。卡片數字＝下面清單筆數（同一份；verify_numbers 5.2 Unity 釘住）。
// 子檢視與篩選都放網址（uv＝檢視、ua＝陣列序號、up＝Pool、ul＝LUN、uh＝主機），可以分享；項次由後端在篩選前編好，篩過不變號。
// 2026-11-10 GPT 挑剔使用者審查採納：Pool 使用率預警卡、Pool 30 天成長與預估幾天後滿（≥7 天才估）、每個值可下鑽、
// 每日容量可到 Pool 層、每台陣列自己的資料時間與過期、「全部 X・篩選後 Y」、換檢視保留篩選、一鍵清除篩選。
const { apiFetch } = useApi()
const { isAdmin } = useAuth()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const data = ref<any>(null)
const err = ref('')
try { data.value = await apiFetch<any>('/api/v2/relay-import/unity') } catch (e: any) { err.value = `Unity 資料載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }

const VIEWS = ['arrays', 'hot', 'pools', 'luns', 'map', 'hosts', 'unmatched', 'daily'] as const
const view = computed(() => ((VIEWS as readonly string[]).includes(q('uv')) ? q('uv') : 'arrays'))
type F = { ua?: string; up?: string; ul?: string; uh?: string }
const FKEYS = ['ua', 'up', 'ul', 'uh'] as const
const cur = computed<F>(() => Object.fromEntries(FKEYS.map(k => [k, q(k)]).filter(([, v]) => v)))
const hasFilter = computed(() => Object.keys(cur.value).length > 0)
/** 連到 Unity 頁籤的某個檢視；f 是這個連結要套的篩選（不帶就是沒有篩選） */
function to(v: string, f: F = {}) {
  const p = new URLSearchParams({ tab: 'unity', uv: v })
  for (const k of FKEYS) if (f[k]) p.set(k, f[k] as string)
  return `/v2/storage?${p.toString()}`
}
function clear(k?: typeof FKEYS[number]) {
  const next: Record<string, any> = { ...route.query }
  for (const x of (k ? [k] : FKEYS)) next[x] = undefined
  router.replace({ query: next })
}
const RELAY_LOG = '/v2/settings?st=integrations&sub=relay&src=unity'
const accepting = ref(false)
/** 管理員：Unity 回報 0 個 Pool 被擋、確定真的清空了 → 下一次匯入接受這台的空快照（只一次） */
async function acceptEmpty(serial: string) {
  if (!window.confirm(`確定陣列 ${serial} 真的清空了？下一次匯入會用空的快照取代舊資料（只生效一次）。`)) return
  accepting.value = true
  try {
    await apiFetch('/api/v2/relay-import/unity/accept-empty', { method: 'POST', body: { serial } })
    showToast('已設定：下一次匯入會接受這台的空快照', 'success')
  } catch (e: any) { showToast(`設定失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { accepting.value = false }
}

function size(b: number | null | undefined) {
  if (b === null || b === undefined) return null
  const neg = b < 0
  const g = Math.abs(b) / 1024 ** 3
  const t = g >= 1024 ? `${(g / 1024).toFixed(2)} TiB` : `${g.toFixed(1)} GiB`
  return neg ? `-${t}` : t
}
const pct = (v: number | null | undefined) => (v === null || v === undefined ? null : `${v}%`)
const ago = (h: number | null | undefined) => (h === null || h === undefined ? null : h < 48 ? `${Math.round(h)} 小時前` : `${Math.floor(h / 24)} 天前`)
// 篩選只套在有那個欄位的列（例：陣列列沒有 lun_id，帶著 LUN 篩選換到陣列檢視不會變空）
const FCOL: Record<string, string> = { ua: 'serial', up: 'pool_id', ul: 'lun_id', uh: 'host_id' }
const fil = (xs: any[]) => xs.filter(x => FKEYS.every(k => !cur.value[k] || !(FCOL[k] in x) || x[FCOL[k]] === cur.value[k]))
const find = (list: string, k: string, v: string) => (data.value?.[list] ?? []).find((x: any) => x.serial === (q('ua') || x.serial) && x[k] === v)
const FLABEL = computed(() => ({
  ua: `陣列：${(data.value?.arrays ?? []).find((a: any) => a.serial === q('ua'))?.array ?? q('ua')}`,
  up: `Pool：${find('pools', 'pool_id', q('up'))?.name || q('up')}`,
  ul: `LUN：${find('luns', 'lun_id', q('ul'))?.name || q('ul')}`,
  uh: `主機：${find('hosts', 'host_id', q('uh'))?.name || q('uh')}`,
}))

const base = computed<any[]>(() => {
  const d = data.value
  if (!d) return []
  const m: Record<string, any[]> = { arrays: d.arrays, hot: d.pools_hot, pools: d.pools, luns: d.luns, map: d.maps, hosts: d.hosts, unmatched: d.unmatched,
    daily: q('up') ? d.pool_daily : d.daily }
  return m[view.value] ?? []
})
const rows = computed(() => fil(base.value))
const c = computed(() => data.value?.cards ?? {})
const warnPct = computed(() => data.value?.settings?.pool_warn_pct ?? 80)
const CHIPS = computed(() => [
  { v: 'arrays', label: '陣列', n: c.value.arrays }, { v: 'hot', label: `Pool 使用率≥${warnPct.value}%`, n: c.value.pools_hot },
  { v: 'pools', label: 'Pool', n: c.value.pools }, { v: 'luns', label: 'LUN', n: c.value.luns },
  { v: 'map', label: '主機↔LUN', n: c.value.maps }, { v: 'hosts', label: 'Unity 主機', n: c.value.hosts },
  { v: 'unmatched', label: '對不到', n: c.value.unmatched }, { v: 'daily', label: '每日容量', n: data.value?.daily?.length ?? 0 }])
const fresh = computed(() => data.value?.freshness ?? null)
const machine = (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)
const sz = (k: string) => (r: any) => size(r[k])
const poolDaily = (r: any) => to('daily', { ua: r.serial, up: r.pool_id })
const hostQ = (r: any) => (r.name ? `/v2/hosts?q=${encodeURIComponent(r.name)}` : null)

const POOL_COLS = [{ k: 'array', label: '陣列', link: (r: any) => to('arrays', { ua: r.serial }) }, { k: 'name', label: 'Pool', link: poolDaily },
  { k: 'size_total', label: '容量', mono: true, text: sz('size_total'), link: poolDaily },
  { k: 'size_used', label: '已用', mono: true, text: sz('size_used'), link: poolDaily },
  { k: 'size_free', label: '剩餘', mono: true, text: sz('size_free'), link: poolDaily },
  { k: 'used_pct', label: '使用率', mono: true, text: (r: any) => pct(r.used_pct), link: poolDaily, tone: (r: any) => (r.hot ? 'dr' : '') },
  { k: 'growth_30d', label: '近期成長', mono: true, text: (r: any) => (r.growth_30d === null ? r.trend_note : `近 ${r.growth_days} 天增加 ${size(r.growth_30d)}`), link: poolDaily },
  { k: 'days_to_full', label: '預估幾天後滿', mono: true, text: (r: any) => r.trend_note, link: poolDaily }]
const HOST_COLS = [{ k: 'array', label: '陣列', link: (r: any) => to('arrays', { ua: r.serial }) },
  { k: 'name', label: 'Unity 主機', link: (r: any) => to('map', { ua: r.serial, uh: r.host_id }) }, { k: 'type_label', label: '類型' },
  { k: 'match_label', label: '對應結果' }]
const COLS: Record<string, any[]> = {
  arrays: [{ k: 'array', label: '陣列', link: (r: any) => to('pools', { ua: r.serial }) }, { k: 'model', label: '型號' }, { k: 'serial', label: '序號', mono: true }, { k: 'management_host', label: '管理位址', mono: true },
    { k: 'size_total', label: '容量', mono: true, text: sz('size_total'), link: (r: any) => to('pools', { ua: r.serial }) },
    { k: 'size_used', label: '已用', mono: true, text: sz('size_used'), link: (r: any) => to('pools', { ua: r.serial }) },
    { k: 'size_free', label: '剩餘', mono: true, text: sz('size_free'), link: (r: any) => to('pools', { ua: r.serial }) },
    { k: 'used_pct', label: '使用率', mono: true, text: (r: any) => pct(r.used_pct), link: (r: any) => to('pools', { ua: r.serial }) },
    { k: 'pool_count', label: 'Pool', mono: true, link: (r: any) => to('pools', { ua: r.serial }) },
    { k: 'lun_count', label: 'LUN', mono: true, link: (r: any) => to('luns', { ua: r.serial }) },
    { k: 'host_count', label: '主機', mono: true, link: (r: any) => to('hosts', { ua: r.serial }) },
    { k: 'map_count', label: '主機↔LUN', mono: true, link: (r: any) => to('map', { ua: r.serial }) },
    { k: 'collected_text', label: '資料時間', mono: true, link: () => RELAY_LOG },
    { k: 'age_hours', label: '多久前', mono: true, text: (r: any) => ago(r.age_hours), link: () => RELAY_LOG },
    { k: 'stale', label: '狀態', text: (r: any) => (r.stale ? '資料過期' : '正常'), tone: (r: any) => (r.stale ? 'dr' : 'prod'), link: () => RELAY_LOG }],
  hot: POOL_COLS,
  pools: POOL_COLS,
  luns: [{ k: 'array', label: '陣列', link: (r: any) => to('arrays', { ua: r.serial }) },
    { k: 'name', label: 'LUN', link: (r: any) => to('map', { ua: r.serial, ul: r.lun_id }) },
    { k: 'pool', label: 'Pool', link: (r: any) => (r.pool_id ? poolDaily(r) : null) },
    { k: 'size_total', label: '容量', mono: true, text: sz('size_total') },
    { k: 'used', label: '已用', mono: true, text: (r: any) => (r.used === null ? '未收集' : size(r.used)) },
    { k: 'free', label: '剩餘', mono: true, text: (r: any) => (r.free === null ? '未收集' : size(r.free)) },
    { k: 'used_pct', label: '使用率', mono: true, text: (r: any) => (r.used_pct === null ? '未收集' : pct(r.used_pct)) },
    { k: 'host_count', label: '主機（台）', mono: true, link: (r: any) => to('map', { ua: r.serial, ul: r.lun_id }) },
    { k: 'hosts', label: '使用的主機' }],
  map: [{ k: 'array', label: '陣列', link: (r: any) => to('arrays', { ua: r.serial }) },
    { k: 'lun', label: 'LUN', link: (r: any) => to('luns', { ua: r.serial, ul: r.lun_id }) },
    { k: 'host', label: 'Unity 主機', link: (r: any) => to('hosts', { ua: r.serial, uh: r.host_id }) }, { k: 'access', label: '存取' },
    { k: 'asset_serial', label: 'WEBIT3 機器', mono: true, link: machine, text: (r: any) => r.asset_serial || (r.match_status === 'not_host' ? '非主機' : '對不到') }],
  hosts: [...HOST_COLS, { k: 'asset_serial', label: 'WEBIT3 機器', mono: true, link: machine },
    { k: 'candidates', label: '候選機器（台）', mono: true, link: hostQ },
    { k: 'lun_count', label: 'LUN（個）', mono: true, link: (r: any) => to('map', { ua: r.serial, uh: r.host_id }) }],
  unmatched: [...HOST_COLS, { k: 'candidates', label: '候選機器（台）', mono: true, link: hostQ },
    { k: 'name_q', label: '用主機名查', text: (r: any) => r.name, link: hostQ },
    { k: 'lun_count', label: 'LUN（個）', mono: true, link: (r: any) => to('map', { ua: r.serial, uh: r.host_id }) }],
}
const DAILY_ARR = [{ k: 'array', label: '陣列', link: (r: any) => to('arrays', { ua: r.serial }) }, { k: 'day', label: '日期', mono: true },
  { k: 'pools', label: 'Pool（個）', mono: true, link: (r: any) => to('pools', { ua: r.serial }) },
  { k: 'size_total', label: '容量', mono: true, text: sz('size_total') }, { k: 'size_used', label: '已用', mono: true, text: sz('size_used') },
  { k: 'size_free', label: '剩餘', mono: true, text: sz('size_free') }, { k: 'used_pct', label: '使用率', mono: true, text: (r: any) => pct(r.used_pct) }]
const DAILY_POOL = [{ k: 'array', label: '陣列', link: (r: any) => to('arrays', { ua: r.serial }) },
  { k: 'pool', label: 'Pool', link: (r: any) => to('pools', { ua: r.serial }) }, { k: 'day', label: '日期', mono: true },
  ...DAILY_ARR.slice(3)]
const cols = computed(() => (view.value === 'daily' ? (q('up') ? DAILY_POOL : DAILY_ARR) : COLS[view.value]))
const PREFIX: Record<string, string> = { arrays: 'U', hot: 'UP', pools: 'UP', luns: 'UL', map: 'UM', hosts: 'UH', unmatched: 'UH', daily: 'UD' }
const prefix = computed(() => (view.value === 'daily' && q('up') ? 'UDP' : PREFIX[view.value]))
</script>

<template>
  <div class="unity">
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else-if="data">
      <div class="bar">
        <NuxtLink class="muted" :to="RELAY_LOG">最後收集 {{ fresh?.last_collected_at || '—' }}</NuxtLink>
        <NuxtLink v-if="fresh && fresh.status !== 'ok'" class="stale" :to="RELAY_LOG">{{ fresh.status_label }}</NuxtLink>
        <NuxtLink v-if="c.stale_arrays" class="stale" :to="to('arrays')">過期陣列 {{ c.stale_arrays }}</NuxtLink>
        <NuxtLink v-if="data.empty_alert" class="stale alert" :to="RELAY_LOG">{{ data.empty_alert.text }}</NuxtLink>
        <button v-if="data.empty_alert?.serial && isAdmin" type="button" class="chip" :disabled="accepting" @click="acceptEmpty(data.empty_alert.serial)">接受空快照（{{ data.empty_alert.serial }}）</button>
        <NuxtLink v-if="c.arrays" class="muted" :to="to('arrays')">合計 {{ size(c.size_total) }}・已用 {{ size(c.size_used) }}（{{ pct(c.used_pct) ?? '—' }}）・剩餘 {{ size(c.size_free) }}</NuxtLink>
        <InfoNote>資料來源：中繼 PC 每天跑 unity_collect.ps1（開發協作 D3，只讀）→ 系統設定「中繼匯入」。每台陣列以最新一批為準，整份取代；每台陣列各自的資料時間在陣列表，超過 {{ data.settings?.stale_days ?? 3 }} 天沒更新標「資料過期」。容量單位是 Unity 回的位元組，換算成 GiB／TiB（1024 進位）。陣列容量＝它所有 Pool 的加總。LUN 的已用目前腳本沒有收，寫「未收集」，不是 0。機房篩選不適用這個頁籤。</InfoNote>
      </div>
      <div class="bar">
        <NuxtLink v-for="x in CHIPS" :key="x.v" class="chip" :class="{ on: view === x.v, hot: x.v === 'hot' && x.n }" :to="to(x.v, cur)">{{ x.label }} {{ x.n ?? 0 }}</NuxtLink>
        <InfoNote>Pool 使用率≥{{ warnPct }}%：已用 ÷ 容量達 {{ warnPct }}% 的 Pool（門檻是系統內建值 storage_relay.POOL_WARN_PCT）。預估幾天後滿：用目前剩餘 ÷ 最近 {{ data.settings?.trend_window_days ?? 30 }} 天的每日已用算出的平均每天成長；陣列資料過期寫「資料過期」、剩餘 0 寫「已滿」；每日資料少於 {{ data.settings?.trend_min_days ?? 7 }} 天寫「資料不足（N 天）」，沒有成長寫「未增加」。對不到＝Unity 上的主機名在資產庫找不到、對到多台不同機器，或主機名太通用（例：localhost）。對應只看主機名（短名、不分大小寫），同一台的判斷跟全站一樣；不會自動建立資產。「非主機」是 Unity 的子網路／網路群組存取設定。</InfoNote>
      </div>
      <div class="bar">
        <span class="muted cnt">全部 {{ base.length }}<template v-if="hasFilter">・篩選後 {{ rows.length }}</template></span>
        <button v-for="(v, k) in cur" :key="k" type="button" class="chip on" @click="clear(k as any)">{{ FLABEL[k as keyof typeof FLABEL] }} ✕</button>
        <button v-if="hasFilter" type="button" class="chip" @click="clear()">清除篩選</button>
      </div>
      <p v-if="!c.arrays" class="muted">還沒有 Unity 資料<InfoNote>下一步：在中繼 PC 照「開發協作 D3」跑 unity_collect.ps1，再由 relay_push 送到系統的中繼資料夾（系統設定 → 外部整合 → 中繼匯入）；匯入後這裡就有資料。被拒收的批次原因寫在中繼匯入的匯入紀錄。</InfoNote></p>
      <V2Table :key="view + q('up')" :rows="rows" :cols="cols" :prefix="prefix" :max="500"
               :empty="view === 'unmatched' ? '全部都對到了' : view === 'hot' ? `沒有使用率≥${warnPct}% 的 Pool` : '無資料'" />
    </template>
  </div>
</template>

<style scoped>
.unity { display: flex; flex-direction: column; gap: 8px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip.hot { border-color: var(--warn); color: var(--warn-text); }
.muted { color: var(--muted); font-size: 13px; }
.cnt { font-variant-numeric: tabular-nums; }
.err { color: var(--bad); }
.stale { padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 12px; text-decoration: none; }
</style>
