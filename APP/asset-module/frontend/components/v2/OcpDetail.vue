<script setup lang="ts">
// 3.2「OCP 逐台明細」頁籤（V40，2.047.3 V2 原生；取代嵌入的舊 pages/ocp.vue）。同一批 API：GET /api/arch/ocp、PUT /api/arch/ocp/role、PUT /api/arch/ocp/cluster。
// 三種看法都在網址上（可分享）：全部叢集（?risk= 只看某種要注意的）｜指定一座（?cluster=，含 VIP／版本／角色／要注意＋逐台節點，可改角色與叢集）｜
// 跨叢集看某種角色（?role=，沒指定叢集時）。改角色／叢集限一般帳號以上（後端另擋），改完重新向後端拿，不在前端自己搬。
interface OcpNode { hostname: string | null; ip: string | null; asset_serial: string | null; role: string; os: string; stage: string; stage_label: string; tone: string
  location: string | null; environment: string | null; api_id: string | null; purpose: string | null; dup_count: number; dup_reason: string
  cluster_basis: string; cluster_by: string | null; cluster_at: string | null; role_basis: string }
interface OcpCluster { cluster: string; basis: string; node_count: number; nodes: OcpNode[]; by_role: Record<string, number>; by_stage: Record<string, number>
  versions: Record<string, number>; locations: Record<string, number>; environments: Record<string, number>; primary_location: string; primary_env: string
  spans: string[]; risks: string[]; vips: string[]; role_tiers: Record<string, { count: number; retired: number; ready: number; worst: string; by_stage: Record<string, number> }> }
interface OcpData { clusters: OcpCluster[]; totals: { clusters: number; nodes: number; ungrouped: number }; cluster_names: string[]; note: string }
// 可以指定的角色（跟後端 ocp_cluster.ROLES 一致）；「未標示」是判不出來的結果，不是可指定的角色
const ROLES = ['Master', 'Infra', 'Worker', 'Bootstrap']
const RISK_LABEL: Record<string, string> = { mixed: '同叢集混版', dup: '同一節點重複登記', span: '跨機房或跨環境', swap: '疑似汰換', style: '版本寫法不一致' }
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const data = ref<OcpData | null>(null)
const loading = ref(true)
const err = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  try { const r = await apiFetch<OcpData>('/api/arch/ocp'); if (!isLatest()) return; data.value = r; err.value = '' } catch (e: any) { if (isLatest()) err.value = `叢集資料載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { if (isLatest()) loading.value = false }
}
onMounted(load)
const picked = computed(() => qs('cluster'))
const riskFilter = computed(() => qs('risk'))
const roleFilter = computed(() => qs('role'))
const one = computed(() => data.value?.clusters.find(c => c.cluster === picked.value) ?? null)
// 換看法用 push（上一頁回得去）；換一座叢集時清掉 risk（risk 只用在全部叢集那張表）
const go = (patch: Record<string, string | undefined>) => router.push({ query: { ...route.query, ...patch } })
const pick = (name: string) => go({ cluster: name || undefined, risk: undefined })

function riskHit(c: OcpCluster, kind: string) {
  const all = [...c.risks, ...c.spans]
  if (kind === 'mixed') return all.some(r => r.includes('種版本'))
  if (kind === 'dup') return all.some(r => r.includes('多筆登記'))
  if (kind === 'span') return c.spans.length > 0
  if (kind === 'swap') return all.some(r => r.includes('汰換'))
  if (kind === 'style') return all.some(r => r.includes('寫法不同'))
  return true
}
const sumTier = (c: OcpCluster, k: 'count' | 'retired' | 'ready') => Object.values(c.role_tiers).reduce((s, t) => s + t[k], 0)
const listRows = computed(() => (data.value?.clusters ?? []).filter(c => !riskFilter.value || riskHit(c, riskFilter.value)).map((c, i) => ({
  __no: i + 1, cluster: c.cluster, basis: c.basis, live: sumTier(c, 'count'), retired: sumTier(c, 'retired'), ready: sumTier(c, 'ready'),
  location: c.primary_location, environment: c.primary_env, versions: Object.keys(c.versions).join('、'), vips: c.vips.length, risks: c.risks.length + c.spans.length,
})))
const machineLink = (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)
const nodeText = (r: any) => (r.hostname || '—') + (r.dup_count > 1 ? `（同台 ${r.dup_count} 筆）` : '')
const crossRows = computed(() => {
  if (one.value || !roleFilter.value) return []
  const out: any[] = []
  for (const c of data.value?.clusters ?? []) for (const n of c.nodes) if (n.role === roleFilter.value) out.push({ ...n, cluster: c.cluster, __no: out.length + 1, key: `${c.cluster}|${n.asset_serial || n.ip || n.hostname}|${out.length}` })
  return out
})
// 指定叢集的節點：現役排前面（跟架構圖上小方塊的順序一致）
const nodeRows = computed(() => {
  const gone = (n: OcpNode) => (n.stage === 'retired' || n.stage === 'exempt' ? 1 : 0)
  return [...(one.value?.nodes ?? []).filter(n => !roleFilter.value || n.role === roleFilter.value)].sort((a, b) => gone(a) - gone(b))
    .map((n, i) => ({ ...n, __no: i + 1, key: `${n.asset_serial || n.ip || n.hostname}|${i}`, role_text: (ROLES.includes(n.role) ? n.role : '未標示') + (n.role_basis === '人工指定' ? ' ✎' : '') }))
})
const nq = ref('')
const nodesShown = computed(() => { const q = nq.value.trim().toLowerCase(); return q ? nodeRows.value.filter(n => [n.hostname, n.ip, n.role, n.os, n.stage_label].some(v => (v || '').toString().toLowerCase().includes(q))) : nodeRows.value })
const saving = ref('')
async function put(url: string, n: OcpNode, body: Record<string, string>, what: string) {
  if (!n.asset_serial) return
  saving.value = n.asset_serial
  try { await guardedFetch(url, { method: 'PUT', body: { asset_serial: n.asset_serial, ...body, reason: 'OCP 明細頁人工指定' } }); showToast(`已改${what}：${n.hostname || n.asset_serial}`, 'success'); await load() } catch (e: any) { showToast(`改${what}失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { saving.value = '' }
}
const setRole = (n: OcpNode, role: string) => put('/api/arch/ocp/role', n, { role }, '角色')
const moveCluster = (n: OcpNode, cluster: string) => put('/api/arch/ocp/cluster', n, { cluster }, '叢集')
const stageTone = (r: any) => (r.tone === 'bad' ? 'prod' : r.tone === 'warn' ? 'test' : '')
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <template v-else-if="data">
      <div class="bar">
        <select :value="picked" aria-label="選叢集" @change="pick(($event.target as HTMLSelectElement).value)">
          <option value="">全部叢集（{{ data.totals.clusters }} 座）</option>
          <option v-for="c in data.cluster_names" :key="c" :value="c">{{ c }}</option>
        </select>
        <span class="muted">共 {{ data.totals.clusters }} 座、{{ data.totals.nodes }} 個節點<template v-if="data.totals.ungrouped">，{{ data.totals.ungrouped }} 台判不出叢集</template></span>
        <span v-if="!one && riskFilter" class="chip on">只看「{{ RISK_LABEL[riskFilter] || riskFilter }}」<button type="button" class="x" aria-label="清除" @click="go({ risk: undefined })">✕</button></span>
        <span v-if="roleFilter" class="chip on">只看「{{ roleFilter }}」<button type="button" class="x" aria-label="清除" @click="go({ role: undefined })">✕</button></span>
        <InfoNote>OCP 節點跑的是 RHCOS（Red Hat CoreOS）：系統區唯讀、不能裝代理程式、建收集帳號要改 MachineConfig（等於動整個節點池，叢集升版會被覆蓋）。所以這些台在納管漏斗裡顯示「未做過」、「資料齊全 0」是預期的，不是漏做。正規做法是向叢集 API 要（只需唯讀 token）；在那之前資料來自資產清單與掃描，角色判不出來可以在表上自己指定。角色自動判斷只看用途／主機名裡的關鍵字（master／control／infra／worker／bootstrap 與對應中文），認不出來標「未標示」不猜；分群依據優先序 FQDN＞用途／名稱＞人工指定，判不出來標「未分群」。</InfoNote>
      </div>

      <V2Table v-if="!one && roleFilter" :rows="crossRows" prefix="X" row-key="key" unit="台" :export-name="`OCP節點_${roleFilter}`" empty="沒有這種角色的節點"
               :cols="[{ k: 'cluster', label: '叢集' }, { k: 'hostname', label: '節點', link: machineLink, text: nodeText }, { k: 'ip', label: 'IP', mono: true }, { k: 'os', label: '版本' },
                       { k: 'stage_label', label: '狀態', tone: stageTone }, { k: 'location', label: '機房' }, { k: 'purpose', label: '用途原文' }]">
        <template #cell-cluster="{ row }"><button type="button" class="lb" @click="pick(row.cluster)">{{ row.cluster }}</button></template>
      </V2Table>

      <V2Table v-else-if="!one" :rows="listRows" prefix="C" row-key="cluster" unit="座" export-name="OCP叢集清單" empty="沒有叢集"
               :cols="[{ k: 'cluster', label: '叢集' }, { k: 'live', label: '現役台數', mono: true }, { k: 'retired', label: '已退役', mono: true }, { k: 'ready', label: '資料齊全', mono: true },
                       { k: 'vips', label: '入口 VIP', mono: true }, { k: 'location', label: '主要機房' }, { k: 'environment', label: '環境' }, { k: 'versions', label: '版本' },
                       { k: 'basis', label: '分群依據' }, { k: 'risks', label: '要注意', mono: true }]">
        <template #cell-cluster="{ row }"><button type="button" class="lb" data-testid="ocp-pick" @click="pick(row.cluster)">{{ row.cluster }}</button></template>
      </V2Table>

      <template v-else>
        <h3 class="h3">{{ one.cluster }}<span class="muted">{{ one.primary_location }}・{{ one.primary_env }}・分群依據 {{ one.basis }}</span>
          <button type="button" class="lb" @click="pick('')">‹ 全部叢集</button></h3>
        <div class="boxes">
          <div class="box"><b>入口 VIP</b>
            <p v-if="!one.vips.length" class="muted">未查到（BIG-IP 清單沒對到這座，不代表沒有）</p>
            <p v-for="v in one.vips" v-else :key="v" class="mono">{{ v }}</p></div>
          <div class="box"><b>版本分佈</b><p v-for="(n, v) in one.versions" :key="v">{{ v }} × {{ n }}</p></div>
          <div class="box"><b>角色</b>
            <p v-for="(t, role) in one.role_tiers" :key="role"><button type="button" class="lb" @click="go({ role: roleFilter === String(role) ? undefined : String(role) })">{{ role }} {{ t.count }}</button>
              <span class="muted">資料齊全 {{ t.ready }}<template v-if="t.retired">、另有 {{ t.retired }} 台已退役</template></span></p></div>
          <div class="box wide"><b>要注意的</b>
            <p v-if="!one.risks.length && !one.spans.length" class="muted">沒偵測到混版、重複登記或跨機房。</p>
            <ul v-else><li v-for="r in [...one.spans, ...one.risks]" :key="r">{{ r }}</li></ul></div>
        </div>
        <div class="bar"><input v-model="nq" class="in" type="search" placeholder="搜主機名／IP／角色／版本／狀態…" aria-label="搜尋節點"><span class="muted">{{ nodesShown.length }}／{{ one.node_count }}</span></div>
        <V2Table :rows="nodesShown" prefix="N" row-key="key" unit="台" :export-name="`OCP節點_${one.cluster}`" :empty="nq ? `找不到符合「${nq}」的節點` : '沒有節點'"
                 :cols="[{ k: 'hostname', label: '節點', link: machineLink, text: nodeText }, { k: 'ip', label: 'IP', mono: true }, { k: 'role_text', label: '角色' },
                         { k: 'purpose', label: '用途原文' }, { k: 'os', label: '版本' }, { k: 'stage_label', label: '狀態', tone: stageTone }, { k: 'location', label: '機房' },
                         { k: 'cluster_basis', label: '叢集', text: () => one!.cluster }]">
          <template #cell-role_text="{ row }">
            <select v-if="!isViewer && row.asset_serial" :value="ROLES.includes(row.role) ? row.role : ''" :disabled="saving === row.asset_serial" aria-label="改角色"
                    :title="row.role_basis === '人工指定' ? `人工指定（${row.cluster_by || '—'} ${row.cluster_at || ''}）` : `自動判斷：${row.role_basis}`"
                    @change="setRole(row, ($event.target as HTMLSelectElement).value)">
              <option value="">（自動）{{ ROLES.includes(row.role) ? '' : ' 未標示' }}</option>
              <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
            </select>
            <span v-else>{{ row.role_text }}</span>
          </template>
          <template #cell-cluster_basis="{ row }">
            <select v-if="!isViewer && row.asset_serial" :value="one.cluster === '未分群' ? '' : one.cluster" :disabled="saving === row.asset_serial" aria-label="改叢集"
                    :title="row.cluster_basis === '人工指定' ? `人工指定（${row.cluster_by || '—'} ${row.cluster_at || ''}）` : `自動判斷：${row.cluster_basis}`"
                    @change="moveCluster(row, ($event.target as HTMLSelectElement).value)">
              <option value="">（未分群）</option>
              <option v-for="c in data.cluster_names" :key="c" :value="c">{{ c }}</option>
            </select>
            <span v-else>{{ one.cluster }}</span>
          </template>
        </V2Table>
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 4px 0 8px; font-size: 16px; display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.boxes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 10px; }
@media (max-width: 900px) { .boxes { grid-template-columns: 1fr; } }
.box { padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); font-size: 13px; }
.box p { margin: 4px 0; }
.box.wide { grid-column: 1 / -1; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.x { border: 0; background: none; cursor: pointer; font-family: inherit; }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; min-width: 280px; border: 1px solid var(--border-strong); border-radius: 8px; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
