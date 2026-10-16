<script setup lang="ts">
// OCP 叢集明細（2026-09-21 使用者：「東西很多，另開網頁展開」）。
//
// 架構圖（4-3）那張卡片只留第一層——名稱、入口 VIP、各角色一台一個小方塊。
// 版本、分群依據、風險、逐台節點這些「第二＋三層」全部搬到這一頁，
// 因為那些東西塞進卡片會把圖擠爛，而圖的價值就是一眼看完。
//
// 進來的方式有兩種，都要能用：
//   /ocp              → 全部叢集一張表（可排序、可下鑽）
//   /ocp?cluster=xxx  → 直接展開那一座
// 用 query 不用動態路由，是為了讓 smoke_pages 有一個固定網址可以每次部署都打。
interface OcpNode {
  hostname: string | null; ip: string | null; asset_serial: string | null
  role: string; os: string; stage: string; stage_label: string; tone: string
  location: string | null; environment: string | null; api_id: string | null
  purpose: string | null; dup_count: number; dup_reason: string
  cluster_basis: string; cluster_by: string | null; cluster_at: string | null
  role_basis: string
}
// 可以指定的角色（跟後端 ocp_cluster.ROLES 一致）。「未標示」不在選單裡——
// 那是「我們判不出來」的結果，不是一種可以被指定的角色；要回到自動判斷選「（自動）」。
const ROLES = ['Master', 'Infra', 'Worker', 'Bootstrap']
interface OcpCluster {
  cluster: string; basis: string; node_count: number; nodes: OcpNode[]
  by_role: Record<string, number>; by_stage: Record<string, number>
  versions: Record<string, number>; locations: Record<string, number>
  environments: Record<string, number>
  primary_location: string; primary_env: string; spans: string[]; risks: string[]
  vips: string[]
  role_tiers: Record<string, { count: number; retired: number; ready: number; worst: string; by_stage: Record<string, number> }>
}
type OcpData = {
  clusters: OcpCluster[]
  totals: { clusters: number; nodes: number; ungrouped: number }
  cluster_names: string[]; note: string
  locations: string[]; environments: string[]
}

const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()

const data = ref<OcpData | null>(null)
const loading = ref(true)
const err = ref('')

async function load() {
  loading.value = true
  try {
    data.value = await apiFetch<OcpData>('/api/arch/ocp')
  } catch (e: any) {
    // 不靜默吞：拿不到就講出來，不然畫面會是一片空白讓人以為「沒有叢集」
    err.value = e?.data?.detail || e?.message || '叢集資料拿不到'
  } finally {
    loading.value = false
  }
}
await load()

// 選哪一座：網址說了算，這樣「看整座 →」的連結可以直接分享給同事
const picked = computed(() => String(route.query.cluster ?? ''))
const one = computed(() => data.value?.clusters.find((c) => c.cluster === picked.value) ?? null)
function pick(name: string) {
  router.push(name ? { path: '/ocp', query: { cluster: name } } : { path: '/ocp' })
}

// ── 總表（沒指定叢集時）：每欄可排、每個數字點得進去
const RISK_LABEL: Record<string, string> = {
  mixed: '同叢集混版', dup: '同一節點重複登記', span: '跨機房或跨環境',
  swap: '疑似汰換', style: '版本寫法不一致',
}
// 架構圖底部「要注意的」帶過來的 ?risk=（2026-09-21：「這些要能點進去看細項」）。
// 數字點不進去，人只能選擇相信或不相信，不能查證。
const riskFilter = computed(() => String(route.query.risk ?? ''))
function riskHit(c: OcpCluster, kind: string) {
  const all = [...c.risks, ...c.spans]
  if (kind === 'mixed') return all.some((r) => r.includes('種版本'))
  if (kind === 'dup') return all.some((r) => r.includes('多筆登記'))
  if (kind === 'span') return c.spans.length > 0
  if (kind === 'swap') return all.some((r) => r.includes('汰換'))
  if (kind === 'style') return all.some((r) => r.includes('寫法不同'))
  return true
}
function clearRisk() {
  router.push({ path: '/ocp' })
}
const listRows = computed(() => (data.value?.clusters ?? [])
  .filter((c) => !riskFilter.value || riskHit(c, riskFilter.value))
  .map((c) => ({
  cluster: c.cluster,
  basis: c.basis,
  node_count: c.node_count,
  live: Object.values(c.role_tiers).reduce((s, t) => s + t.count, 0),
  retired: Object.values(c.role_tiers).reduce((s, t) => s + t.retired, 0),
  ready: Object.values(c.role_tiers).reduce((s, t) => s + t.ready, 0),
  location: c.primary_location,
  environment: c.primary_env,
  versions: Object.keys(c.versions).join('、'),
  vips: c.vips.length,
  risks: c.risks.length + c.spans.length,
})))
const { sortKey: lKey, sortDir: lDir, toggle: lToggle, sorted: listSorted } =
  useSort(listRows, 'node_count')

// ── 節點表（指定叢集時）：現役排前面，跟架構圖上小方塊的順序一致
// 架構圖上點「未標示 2」帶過來的 ?role=：直接只留那一種角色。
// （使用者 2026-09-21：「未標示 2 了，我不能點進去看是哪兩個」）
const roleFilter = computed(() => String(route.query.role ?? ''))
function clearRole() {
  router.push(picked.value ? { path: '/ocp', query: { cluster: picked.value } } : { path: '/ocp' })
}
const nodeRows = computed(() => {
  const gone = (n: OcpNode) => (n.stage === 'retired' || n.stage === 'exempt' ? 1 : 0)
  const all = (one.value?.nodes ?? [])
    .filter((n) => !roleFilter.value || n.role === roleFilter.value)
  return [...all].sort((a, b) => gone(a) - gone(b))
})
// 摘要那行「角色：Worker 65」不指定叢集，所以沒選叢集但有 role 時，
// 要跨叢集把那 65 台列出來（多一欄「叢集」），不是要人一座一座自己加。
const crossNodes = computed(() => {
  if (one.value || !roleFilter.value) return []
  const out: (OcpNode & { cluster: string })[] = []
  for (const c of data.value?.clusters ?? []) {
    for (const n of c.nodes) {
      if (n.role === roleFilter.value) out.push({ ...n, cluster: c.cluster })
    }
  }
  return out
})
const { sortKey: xKey, sortDir: xDir, toggle: xToggle, sorted: crossSorted } =
  useSort(crossNodes, 'cluster')
const { sortKey: nKey, sortDir: nDir, toggle: nToggle, sorted: nodesSorted } =
  useSort(nodeRows, '')
const nQuery = ref('')
const nodesShown = computed(() => {
  const q = nQuery.value.trim().toLowerCase()
  if (!q) return nodesSorted.value
  return nodesSorted.value.filter((n) => [n.hostname, n.ip, n.role, n.os, n.stage_label]
    .some((v) => (v || '').toString().toLowerCase().includes(q)))
})

const savingRole = ref<string | null>(null)
async function setRole(node: OcpNode, role: string) {
  if (!node.asset_serial) return
  savingRole.value = node.asset_serial
  try {
    await apiFetch('/api/arch/ocp/role', {
      method: 'PUT',
      body: { asset_serial: node.asset_serial, role, reason: 'OCP 明細頁人工指定' },
    })
    await load()
  } finally {
    savingRole.value = null
  }
}
const movingNode = ref<string | null>(null)
async function moveCluster(node: OcpNode, cluster: string) {
  if (!node.asset_serial) return
  movingNode.value = node.asset_serial
  try {
    await apiFetch('/api/arch/ocp/cluster', {
      method: 'PUT',
      body: { asset_serial: node.asset_serial, cluster, reason: 'OCP 明細頁人工指定' },
    })
    await load()   // 重算，不要自己在前端搬（兩邊會漂走）
  } finally {
    movingNode.value = null
  }
}
</script>

<template>
  <div class="page">
    <h1>
      OCP 叢集明細
      <NuxtLink class="back" to="/architecture?tab=ocp">← 回架構圖</NuxtLink>
    </h1>
    <p class="sub">
      架構圖那張卡片只放「長什麼樣」；版本、分群依據、風險、逐台狀態放這裡。
    </p>
    <!-- 2026-09-21 使用者：「OCP 我很不熟，我不太敢動他」。
         畫面不能讓人以為這 151 台是自己漏做——那會逬人去動不該動的東西。 -->
    <p class="why">OCP 節點跑的是 RHCOS（Red Hat CoreOS），跟一般 Linux 不同：系統區唯讀、不能裝代理程式、建收集帳號要改 MachineConfig（等於動整個節點池，而且叢集升版會被覆蓋）。所以這些台在納管漏斗裡顯示「未敀過」、「資料齊全 0」是**預期的**，不是漏做——這條路本來就不該走。要拿到版本、角色、Ready 狀態，正規做法是向叢集 API 要（只需唯讀 token，不會動到叢集）；在那之前，這頁的資料來自資產清單與掃描，角色判不出來可以在表上自己指定。</p>

    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>

    <template v-else-if="data">
      <div class="bar">
        <select :value="picked" class="pick" @change="pick(($event.target as HTMLSelectElement).value)">
          <option value="">全部叢集（{{ data.totals.clusters }} 座）</option>
          <option v-for="n in data.cluster_names" :key="n" :value="n">{{ n }}</option>
        </select>
        <span class="muted">
          共 {{ data.totals.clusters }} 座、{{ data.totals.nodes }} 個節點<template
            v-if="data.totals.ungrouped">，{{ data.totals.ungrouped }} 台判不出叢集</template>
        </span>
      </div>

      <div v-if="!one && (riskFilter || roleFilter)" class="bar">
        <button v-if="riskFilter" class="chip" @click="clearRisk">
          只看「{{ RISK_LABEL[riskFilter] || riskFilter }}」 ✕
        </button>
        <button v-if="roleFilter" class="chip" @click="clearRole">只看「{{ roleFilter }}」 ✕</button>
      </div>

      <!-- 沒選叢集、但指定了角色：跨叢集列那些節點 -->
      <table v-if="!one && roleFilter" class="tbl">
        <thead>
          <tr>
            <SortTh k="cluster" :active="xKey" :dir="xDir" @sort="xToggle">叢集</SortTh>
            <SortTh k="hostname" :active="xKey" :dir="xDir" @sort="xToggle">節點</SortTh>
            <SortTh k="ip" :active="xKey" :dir="xDir" @sort="xToggle">IP</SortTh>
            <SortTh k="os" :active="xKey" :dir="xDir" @sort="xToggle">版本</SortTh>
            <SortTh k="stage_label" :active="xKey" :dir="xDir" @sort="xToggle">狀態</SortTh>
            <SortTh k="location" :active="xKey" :dir="xDir" @sort="xToggle">機房</SortTh>
            <SortTh k="purpose" :active="xKey" :dir="xDir" @sort="xToggle">用途原文</SortTh>
          </tr>
        </thead>
        <tbody>
          <tr v-for="n in crossSorted" :key="(n.asset_serial || n.ip || n.hostname || '') + n.cluster">
            <td><a class="dl" @click="pick(n.cluster)">{{ n.cluster }}</a></td>
            <td>
              <NuxtLink v-if="n.asset_serial" class="dl" :to="`/assets/${n.asset_serial}`">{{ n.hostname || '—' }}</NuxtLink>
              <span v-else>{{ n.hostname || '—' }}</span>
            </td>
            <td class="mono">{{ n.ip || '—' }}</td>
            <td>{{ n.os }}</td>
            <td><span class="st" :class="`t-${n.tone}`">{{ n.stage_label }}</span></td>
            <td class="dim">{{ n.location || '—' }}</td>
            <td class="dim purpose" :title="n.purpose || ''">{{ n.purpose || '（用途沒填）' }}</td>
          </tr>
        </tbody>
      </table>

      <!-- 沒指定叢集：全部列出來 -->
      <table v-else-if="!one" class="tbl">
        <thead>
          <tr>
            <SortTh k="cluster" :active="lKey" :dir="lDir" @sort="lToggle">叢集</SortTh>
            <SortTh k="live" :active="lKey" :dir="lDir" class="r" @sort="lToggle">現役台數</SortTh>
            <SortTh k="retired" :active="lKey" :dir="lDir" class="r" @sort="lToggle">已退役</SortTh>
            <SortTh k="ready" :active="lKey" :dir="lDir" class="r" @sort="lToggle">資料齊全</SortTh>
            <SortTh k="vips" :active="lKey" :dir="lDir" class="r" @sort="lToggle">入口 VIP</SortTh>
            <SortTh k="location" :active="lKey" :dir="lDir" @sort="lToggle">主要機房</SortTh>
            <SortTh k="environment" :active="lKey" :dir="lDir" @sort="lToggle">環境</SortTh>
            <SortTh k="versions" :active="lKey" :dir="lDir" @sort="lToggle">版本</SortTh>
            <SortTh k="basis" :active="lKey" :dir="lDir" @sort="lToggle">分群依據</SortTh>
            <SortTh k="risks" :active="lKey" :dir="lDir" class="r" @sort="lToggle">要注意</SortTh>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in listSorted" :key="r.cluster">
            <td><a class="dl" @click="pick(r.cluster)">{{ r.cluster }}</a></td>
            <td class="r mono"><b>{{ r.live }}</b></td>
            <td class="r mono dim">{{ r.retired || '—' }}</td>
            <td class="r mono" :class="{ warnx: r.ready < r.live }">{{ r.ready }}</td>
            <td class="r mono dim">{{ r.vips || '—' }}</td>
            <td>{{ r.location }}</td>
            <td>{{ r.environment }}</td>
            <td class="dim">{{ r.versions }}</td>
            <td class="dim">{{ r.basis }}</td>
            <td class="r"><span v-if="r.risks" class="risk-n">{{ r.risks }}</span><span v-else class="dim">—</span></td>
          </tr>
        </tbody>
      </table>

      <!-- ── 指定了叢集：第二層（版本／分群／風險）＋ 第三層（節點表） ── -->
      <template v-else>
        <div class="head2">
          <h2>{{ one.cluster }}</h2>
          <span class="muted">
            {{ one.primary_location }} · {{ one.primary_env }} · 分群依據 {{ one.basis }}
          </span>
        </div>

        <div class="boxes2">
          <div class="box">
            <div class="box-h">入口 VIP</div>
            <p v-if="!one.vips.length" class="muted">未查到（BIG-IP 清單沒對到這座，不代表沒有）</p>
            <p v-for="v in one.vips" v-else :key="v" class="mono">{{ v }}</p>
          </div>
          <div class="box">
            <div class="box-h">版本分佈</div>
            <p v-for="(n, v) in one.versions" :key="v">{{ v }} × {{ n }}</p>
          </div>
          <div class="box">
            <div class="box-h">角色</div>
            <p v-for="(t, role) in one.role_tiers" :key="role">
              {{ role }} {{ t.count }}　<span class="dim">資料齊全 {{ t.ready }}<template
                v-if="t.retired">、另有 {{ t.retired }} 台已退役</template></span>
            </p>
          </div>
          <div class="box wide">
            <div class="box-h">要注意的</div>
            <p v-if="!one.risks.length && !one.spans.length" class="muted">沒偵測到混版、重複登記或跨機房。</p>
            <ul v-else class="risk-list">
              <li v-for="r in [...one.spans, ...one.risks]" :key="r">{{ r }}</li>
            </ul>
          </div>
        </div>

        <div class="bar">
          <input v-model="nQuery" class="q" placeholder="搜主機名／IP／角色／版本／狀態…">
          <button v-if="roleFilter" class="chip" @click="clearRole">只看「{{ roleFilter }}」 ✕</button>
          <span class="muted">{{ nodesShown.length }} / {{ one.node_count }}</span>
        </div>
        <table class="tbl">
          <thead>
            <tr>
              <SortTh k="hostname" :active="nKey" :dir="nDir" @sort="nToggle">節點</SortTh>
              <SortTh k="ip" :active="nKey" :dir="nDir" @sort="nToggle">IP</SortTh>
              <SortTh k="role" :active="nKey" :dir="nDir" @sort="nToggle">角色</SortTh>
              <SortTh k="purpose" :active="nKey" :dir="nDir" @sort="nToggle"
                      title="系統就是拿這欄在判角色。這欄空＝用途沒填；有寫卻還是未標示＝系統認不得那個詞。">用途原文</SortTh>
              <SortTh k="os" :active="nKey" :dir="nDir" @sort="nToggle">版本</SortTh>
              <SortTh k="stage_label" :active="nKey" :dir="nDir" @sort="nToggle">狀態</SortTh>
              <SortTh k="location" :active="nKey" :dir="nDir" @sort="nToggle">機房</SortTh>
              <SortTh k="cluster_basis" :active="nKey" :dir="nDir" @sort="nToggle">改叢集</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!nodesShown.length"><td colspan="8" class="muted">找不到符合「{{ nQuery }}」的節點</td></tr>
            <tr v-for="n in nodesShown" :key="n.asset_serial || n.ip || n.hostname || ''">
              <td>
                <NuxtLink v-if="n.asset_serial" class="dl" :to="`/assets/${n.asset_serial}`">{{ n.hostname || '—' }}</NuxtLink>
                <span v-else>{{ n.hostname || '—' }}</span>
                <span v-if="n.dup_count > 1" class="dup" :title="n.dup_reason">同台 {{ n.dup_count }} 筆</span>
              </td>
              <td class="mono">{{ n.ip || '—' }}</td>
              <td>
                <select :disabled="savingRole === n.asset_serial"
                        :value="ROLES.includes(n.role) ? n.role : ''"
                        :title="n.role_basis === '人工指定'
                          ? `人工指定（${n.cluster_by || '—'} ${n.cluster_at || ''}）`
                          : `自動判斷：${n.role_basis}`"
                        @change="setRole(n, ($event.target as HTMLSelectElement).value)">
                  <option value="">（自動）{{ ROLES.includes(n.role) ? '' : ' 未標示' }}</option>
                  <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
                </select>
                <i v-if="n.role_basis === '人工指定'" class="manual" title="人工指定">✎</i>
              </td>
              <td class="dim purpose" :title="n.purpose || ''">{{ n.purpose || '（用途沒填）' }}</td>
              <td>{{ n.os }}</td>
              <td><span class="st" :class="`t-${n.tone}`">{{ n.stage_label }}</span></td>
              <td class="dim">{{ n.location || '—' }}</td>
              <td>
                <select :disabled="movingNode === n.asset_serial"
                        :value="one.cluster === '未分群' ? '' : one.cluster"
                        :title="n.cluster_basis === '人工指定'
                          ? `人工指定（${n.cluster_by || '—'} ${n.cluster_at || ''}）`
                          : `自動判斷：${n.cluster_basis}`"
                        @change="moveCluster(n, ($event.target as HTMLSelectElement).value)">
                  <option value="">（未分群）</option>
                  <option v-for="name in data.cluster_names" :key="name" :value="name">{{ name }}</option>
                </select>
                <i v-if="n.cluster_basis === '人工指定'" class="manual" title="人工指定">✎</i>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="note">
          角色與叢集判錯的，直接在表上換（改的是整台，不是單一筆登記），改過的會標 ✎。
          角色自動判斷只看用途／主機名里的關鍵字（master／control／infra／worker／bootstrap
          與對應的中文）；認不出來標「未標示」，不猜。
          分群依據優先序：FQDN ＞ 用途／名稱 ＞ 人工指定；判不出來標「未分群」，不用機房或網段硬湊。
        </p>
      </template>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 16px 20px 40px; }
h1 { font-size: 20px; margin: 0 0 4px; display: flex; align-items: baseline; gap: 12px; }
.back { font-size: 13px; font-weight: 400; }
.sub { font-size: 12.5px; color: var(--muted); margin: 0 0 14px; }
.err { color: #b91c1c; font-size: 13px; }
.why { font-size: 12.5px; line-height: 1.8; color: var(--ink-soft); background: #f1f5f9;
  border-left: 3px solid #0ea5e9; border-radius: 0; padding: 8px 12px; margin: 0 0 14px; }
.bar { display: flex; align-items: center; gap: 10px; margin: 12px 0 6px; }
.pick, .q { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.q { min-width: 280px; }
.head2 { display: flex; align-items: baseline; gap: 12px; margin-top: 14px; }
.head2 h2 { font-size: 17px; margin: 0; }
.boxes2 { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 4px; }
.box { flex: 1 1 200px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.box.wide { flex-basis: 100%; }
.box-h { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.box p { font-size: 12.5px; margin: 2px 0; line-height: 1.6; }
.risk-list { margin: 4px 0 0; padding-left: 1.2em; font-size: 12.5px; color: #b45309; line-height: 1.7; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { padding: 5px 8px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
.tbl td.r, .tbl th.r { text-align: right; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.dim, .muted { color: var(--muted); }
.warnx { color: #b45309; }
.risk-n { display: inline-block; min-width: 18px; padding: 0 5px; border-radius: 9px;
  background: #fef3c7; color: #92400e; font-size: 12px; text-align: center; }
.purpose { max-width: 260px; overflow: hidden; text-overflow: ellipsis; }
.dup { margin-left: 6px; font-size: 11.5px; color: #b45309; }
.manual { margin-left: 4px; font-style: normal; color: var(--muted); }
.dl { cursor: pointer; }
.chip { border: 1px solid var(--border); border-radius: 12px; padding: 2px 10px; font-size: 12px;
  background: #eef2ff; color: #3730a3; cursor: pointer; }
.note { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.7; }
</style>
