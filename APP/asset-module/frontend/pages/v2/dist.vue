<script setup lang="ts">
// 10.2 分布統計（2.x 第四批；設計筆記 docs/ui-v2/批次4設計_2026-10-28.md）：機房分布｜作業系統｜業務系統排行｜環境別。
// 只放台數（用電與空間只在 5.1，機房列給連結）。資料：/api/v2/dist（v2_dist.py）——所在地／作業系統／環境別＝2.1 主機查詢
// 同一份 v2_hosts.machines（每個維度加總＝2.1「全部登記台數」）；業務系統排行＝3.1 同一份 v2_systems.listing。
// 每格可點，清單＝/api/v2/dist/drill（同一份計算，列數＝格子數字；verify_numbers check_v2_dist 釘住）。
// 2.051.0（V66，批 16）：加「機房×環境」（同一份 machines 交叉計數；格子＝drill(site, 所在地, env)，網址 ?ms=&me=）
// 與「快速查詢」（?lq=，/api/v2/dist/lookup：打主機名／IP／序號／業務系統，看這台落在哪一格，每個值點下去＝那一格的清單）。
// 2.059.0（V66 收尾）：舊 /distribution 剩下的「基礎設施」「AP 系統」（含改分級，V2DistSystems）、「OS×環境納管」（V2DistOnboard）、
// 「入口（VIP）」（V2DistVip）搬成 V2 原生，都用舊頁同一支 API（⚠D3 不寫新的計算）；舊版入口拿掉、/distribution 加進 PORTED。
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const data = ref<any>(null)
const err = ref('')
try { data.value = await apiFetch('/api/v2/dist') } catch (e: any) { err.value = `分布統計載入失敗：${errMsg(e)}` }
const TABS = [{ k: 'site', label: '機房分布' }, { k: 'matrix', label: '機房×環境' }, { k: 'os', label: '作業系統' }, { k: 'osver', label: '作業系統版本' }, { k: 'systems', label: '業務系統排行' }, { k: 'env', label: '環境別' }, { k: 'phys', label: '實體機分布' }, { k: 'infra', label: '基礎設施' }, { k: 'ap', label: 'AP 系統' }, { k: 'onboard', label: 'OS×環境納管' }, { k: 'vip', label: '入口（VIP）' }, { k: 'lookup', label: '快速查詢' }]
const tab = computed({ get: () => (TABS.some(t => t.k === q('tab')) ? q('tab') : 'site'), set: (v: string) => router.replace({ query: { tab: v } }) })
const DIM_LABEL: Record<string, string> = { site: '所在地', os: '作業系統大類', osver: '作業系統版本', env: '環境別' }
const DIM_TABS = ['site', 'os', 'osver', 'env']
const dcRooms = computed(() => new Set<string>(data.value?.dc_rooms ?? []))
const enc = encodeURIComponent
const drillUrl = (dim: string, value: string, kind: string) => `/v2/dist?tab=${dim}&dv=${enc(value)}&dk=${kind}`
const rows = computed(() => (data.value?.dims?.[tab.value] ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, power: dcRooms.value.has(r.value) ? '用電／空間' : '' })))
const totals = computed(() => {
  const xs = data.value?.dims?.[tab.value] ?? []
  return ['all', 'phys', 'vm', 'online'].reduce((o: any, k) => ({ ...o, [k]: xs.reduce((a: number, r: any) => a + r[k], 0) }), {})
})
const sysRows = computed(() => (data.value?.systems ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1 })))
const sysUrl = (r: any) => `/v2/system/${enc(r.api_id)}`

// 機房×環境：一列一個所在地、一欄一個環境別。格子／列合計／欄合計都點得下去（同一支 drill）。
const mxUrl = (site: string | null, env: string | null) => `/v2/dist?tab=matrix${site !== null ? `&ms=${enc(site)}` : ''}${env !== null ? `&me=${enc(env)}` : ''}`
const mxEnvs = computed<string[]>(() => data.value?.matrix?.envs ?? [])
const mxRows = computed(() => (data.value?.matrix?.rows ?? []).map((r: any, i: number) => {
  const o: any = { site: r.site, all: r.all, __no: i + 1 }
  mxEnvs.value.forEach((e, j) => { o[`e${j}`] = r.by_env?.[e] ?? 0 })
  return o
}))
const mxCols = computed(() => [
  { k: 'site', label: '所在地' },
  ...mxEnvs.value.map((e, j) => ({ k: `e${j}`, label: `${e}（台）`, mono: true, link: (r: any) => (r[`e${j}`] ? mxUrl(r.site, e) : null) })),
  { k: 'all', label: '合計（台）', mono: true, link: (r: any) => mxUrl(r.site, null) },
])

// 下鑽清單（維度頁籤：?dv=&dk=；機房×環境：?ms=&me=，只給一個＝列合計或欄合計）
const drillQuery = computed<Record<string, string> | null>(() => {
  if (DIM_TABS.includes(tab.value) && q('dv') && q('dk')) return { dim: tab.value, value: q('dv'), kind: q('dk') }
  if (tab.value === 'matrix') {
    if (q('ms') && q('me')) return { dim: 'site', value: q('ms'), kind: 'all', env: q('me') }
    if (q('ms')) return { dim: 'site', value: q('ms'), kind: 'all' }
    if (q('me')) return { dim: 'env', value: q('me'), kind: 'all' }
  }
  return null
})
const drill = ref<any>(null)
const drillErr = ref('')
let drillSeq = 0
watch(drillQuery, async (dq) => {
  const seq = ++drillSeq
  drill.value = null; drillErr.value = ''
  if (!dq) return
  try {
    const r = await apiFetch('/api/v2/dist/drill', { query: dq })
    if (seq === drillSeq) drill.value = r
  } catch (e: any) { if (seq === drillSeq) drillErr.value = `清單載入失敗：${errMsg(e)}` }
}, { immediate: true })
const host = (r: any) => `/v2/machine/${enc(r.key)}`
const cols = computed(() => [
  { k: 'value', label: DIM_LABEL[tab.value] || '值' },
  { k: 'all', label: '全部（台）', mono: true, link: (r: any) => drillUrl(tab.value, r.value, 'all') },
  { k: 'phys', label: '非虛擬機（台）', mono: true, link: (r: any) => (r.phys ? drillUrl(tab.value, r.value, 'phys') : null) },
  { k: 'vm', label: '虛擬機（台）', mono: true, link: (r: any) => (r.vm ? drillUrl(tab.value, r.value, 'vm') : null) },
  { k: 'online', label: '運作中（台）', mono: true, link: (r: any) => (r.online ? drillUrl(tab.value, r.value, 'online') : null) },
  ...(tab.value === 'osver' ? [{ k: 'measured', label: '其中實測（台）', mono: true }] : []),
  ...(tab.value === 'site' ? [{ k: 'power', label: '5.1 機房平面圖', link: (r: any) => (r.power ? `/v2/datacenter?room=${enc(r.value)}&tab=power` : null) }] : []),
])
const backUrl = computed(() => `/v2/dist?tab=${tab.value}`)

// 快速查詢（?lq=）：網址就是查詢條件，可以分享；每個值點下去＝那一格的清單（同一支 drill）
const lqInput = ref(q('lq'))
const lookup = ref<any>(null)
const lookupErr = ref('')
let lookupSeq = 0
watch(() => (tab.value === 'lookup' ? q('lq') : ''), async (v) => {
  const seq = ++lookupSeq
  lookup.value = null; lookupErr.value = ''
  lqInput.value = v
  if (!v.trim()) return
  try {
    const r = await apiFetch('/api/v2/dist/lookup', { query: { q: v } })
    if (seq === lookupSeq) lookup.value = r
  } catch (e: any) { if (seq === lookupSeq) lookupErr.value = `查詢失敗：${errMsg(e)}` }
}, { immediate: true })
function doLookup() { router.replace({ query: { tab: 'lookup', ...(lqInput.value.trim() ? { lq: lqInput.value.trim() } : {}) } }) }
const lookupRows = computed(() => (lookup.value?.rows ?? []).map((r: any, i: number) => ({ ...r, cell: `${r.site}×${r.env}`, __no: i + 1 })))
const lookupCols = [
  { k: 'name', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'system', label: '業務系統' },
  { k: 'site', label: '所在地', link: (r: any) => drillUrl('site', r.site, 'all') },
  { k: 'env', label: '環境別', link: (r: any) => drillUrl('env', r.env, 'all') },
  { k: 'cell', label: '機房×環境', link: (r: any) => mxUrl(r.site, r.env) },
  { k: 'os', label: '作業系統大類', link: (r: any) => drillUrl('os', r.os, 'all') },
  { k: 'osver', label: '作業系統版本', link: (r: any) => drillUrl('osver', r.osver, 'all') },
  { k: 'vm', label: '虛擬機／非虛擬機' }, { k: 'status_label', label: '偵測狀態' },
]
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">10.2 分布統計</h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <span v-else-if="data">全部登記台數 <NuxtLink to="/v2/hosts">{{ data.total.toLocaleString() }} 台</NuxtLink>
        <InfoNote>一台一列（同一台的多筆登記算一台、退役不算），與 2.1 主機查詢「全部登記台數」同一個數字；每個維度的「全部」加總都等於它。
          虛擬機＝2.1「虛擬機」頁籤同一個定義（各頁籤加總都等於它）；非虛擬機＝全部－虛擬機。2.1／9.2 的「實體設備」是依機身／框算的單位數，不是一台一列，所以不在這裡細分。所在地是登記的實體位置，5.1 機房平面圖的機房台數只算在機櫃上的實體設備，兩者不同。
          用電與剩餘空間只在 5.1。</InfoNote></span>
    </template>
    <template #tab>
      <!-- 各環境實體機分布（V57，2.047.2）：V2 原生（V2PhysDist），同一批 API，不再嵌入舊元件 -->
      <ClientOnly v-if="tab === 'phys'"><V2PhysDist /></ClientOnly>
      <!-- 基礎設施／AP 系統／OS×環境納管／入口（VIP）（V66 收尾，2.059.0） -->
      <ClientOnly v-else-if="tab === 'infra' || tab === 'ap'"><V2DistSystems :key="tab" :kind="tab === 'ap' ? 'ap' : 'infra'" /></ClientOnly>
      <ClientOnly v-else-if="tab === 'onboard'"><V2DistOnboard /></ClientOnly>
      <ClientOnly v-else-if="tab === 'vip'"><V2DistVip /></ClientOnly>
      <!-- 快速查詢（V66，2.051.0） -->
      <template v-else-if="tab === 'lookup'">
        <form class="bar" @submit.prevent="doLookup">
          <input v-model="lqInput" class="inp" placeholder="主機名／IP／序號／業務系統" aria-label="快速查詢" />
          <button type="submit" class="btn">查詢</button>
          <InfoNote>一台一列（跟各頁籤同一份清單）。名稱、IP、序號、業務系統任一欄包含輸入的字就列出；每個值點下去＝那一格的清單，台數跟格子上的數字一樣。最多列 200 台。</InfoNote>
        </form>
        <p v-if="lookupErr" class="err">{{ lookupErr }}</p>
        <template v-else-if="lookup">
          <p class="muted">符合 {{ lookup.total.toLocaleString() }} 台<span v-if="lookup.total > lookup.rows.length">，顯示前 {{ lookup.rows.length }} 台</span></p>
          <V2Table :rows="lookupRows" :cols="lookupCols" prefix="Q" unit="台" empty="沒有符合的主機" />
        </template>
        <p v-else-if="q('lq')" class="muted">查詢中…</p>
        <p v-else class="muted">輸入主機名、IP、序號或業務系統，按查詢。</p>
      </template>
      <template v-else-if="data && drillQuery">
        <div class="bar"><NuxtLink :to="backUrl">‹ 返回</NuxtLink><b>{{ drill?.label || '' }}</b><span v-if="drill" class="muted">{{ drill.rows.length }} 台</span></div>
        <p v-if="drillErr" class="err">{{ drillErr }}</p>
        <V2Table v-else-if="drill" :rows="drill.rows" prefix="D" unit="台" empty="沒有符合的主機"
                 :cols="[{ k: 'name', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'type', label: '類型' }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }, { k: 'env', label: '環境別' }, { k: 'status_label', label: '偵測狀態' }, ...(tab === 'osver' ? [{ k: 'os_text', label: '作業系統（原始）' }, { k: 'os_src', label: '來源' }] : [])]" />
        <p v-else class="muted">載入中…</p>
      </template>
      <template v-else-if="data && tab === 'matrix'">
        <V2Table :rows="mxRows" :cols="mxCols" prefix="M" :max="500" empty="無資料" />
        <p class="muted sum">合計：
          <template v-for="e in mxEnvs" :key="e"><NuxtLink :to="mxUrl(null, e)">{{ e }} {{ (data.matrix.env_totals[e] ?? 0).toLocaleString() }}</NuxtLink>・</template>
          全部 <NuxtLink to="/v2/hosts">{{ data.matrix.total.toLocaleString() }}</NuxtLink>（台）
          <InfoNote>所在地與環境別都用 CIA 登記值，跟「機房分布」「環境別」兩個頁籤同一個判斷：每列合計＝機房分布的「全部」，每欄合計＝環境別的「全部」，總和＝全部登記台數。點格子看是哪幾台。</InfoNote></p>
      </template>
      <template v-else-if="data && tab !== 'systems'">
        <V2Table :rows="rows" prefix="G" :max="500" empty="無資料" :cols="cols" />
        <p v-if="tab === 'osver'" class="muted sum">AIX、Windows 分版本，Linux 不分發行版只算一行；網路、儲存、虛擬化平台等歸「其他」、沒有 OS 資料的歸「未知」，所以合計＝全部登記台數。
          <InfoNote>來源＝「實測」代表這台已納管、系統收集到它自己回報的 OS，版本以它為準（CIA 登記的版本常不準）；「登記」＝還沒收集到，暫用 CIA 登記的 OS 欄。點台數可以看是哪幾台、原始寫法與來源。Windows Server 2012 與 2012 R2 分開算。</InfoNote></p>
        <p class="muted sum">合計：全部 {{ totals.all.toLocaleString() }}・非虛擬機 {{ totals.phys.toLocaleString() }}・虛擬機 {{ totals.vm.toLocaleString() }}・運作中 {{ totals.online.toLocaleString() }}（台）</p>
      </template>
      <template v-else-if="data">
        <p class="muted">依主機台數排序<InfoNote>台數＝3.1 業務系統、系統履歷同一個數字。一台可屬多個業務系統，所以排行的台數加總會大於全部登記台數，不列合計。</InfoNote></p>
        <V2Table :rows="sysRows" prefix="S" :max="500" empty="無資料"
                 :cols="[{ k: 'api_id', label: '代號', mono: true, link: sysUrl }, { k: 'name', label: '業務系統', link: sysUrl }, { k: 'class_group', label: '分級' }, { k: 'severity', label: '重要度' }, { k: 'hosts', label: '主機（台）', mono: true, link: (r: any) => `${sysUrl(r)}?tab=hosts` }, { k: 'records', label: '登記（筆）', mono: true }, { k: 'unit', label: '使用單位' }]" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.sum { margin-top: 8px; }
.err { color: var(--bad); }
.inp { padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px; min-width: 260px; font-family: inherit; background: var(--card); color: var(--ink); }
.btn { padding: 6px 14px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; }
.btn:hover { background: var(--brand-hover); }
</style>
