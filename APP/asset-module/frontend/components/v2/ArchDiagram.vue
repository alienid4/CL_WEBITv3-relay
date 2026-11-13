<script setup lang="ts">
// 3.2 架構圖（V19，2.047.3 V2 原生；取代嵌入的舊 pages/architecture.vue）。同一批 API：/api/arch/vcenter、/api/arch/ocp、/api/arch/ftp。
// 圖照舊（一張圖一頁、可列印／存 PDF），每張圖下面加一張 V2Table 清單（可排序、項次、匯出、數字點下去看明細）；
// 連結全部改到 V2：主機 → /v2/machine/:serial，OCP 細項 → 3.2「OCP 逐台明細」頁籤（/v2/arch?tab=ocpdetail&cluster=&role=&risk=）。
// 原本的註解：
// 架構圖：一張圖一頁、PowerPoint 風格、可列印/存 PDF 當簡報附件（2026-09-07 使用者定案）。
// - vCenter：從 CI 圖即時算，可點 cluster 下鑽到 ESXi，資料變自動更新。
// - FTP：成員清單人維護（存 app_settings），屬性用 hardware 真資料；系統查不到的
//   （FTP 帳號數、近月檔案、整併階段）一律不編——使用者定的：查不到就不寫。
interface Esxi { label: string; asset_serial: string | null; vm: number }
interface Cluster {
  cluster: string; site: string; env: string; vsan: boolean
  esxi_count: number; vm_count: number; datastore_count: number; esxi: Esxi[]
}
interface VcTotals { sites: number; clusters: number; esxi: number; vm: number; datastore: number; vsan_clusters: number; uat_clusters: number }
interface FtpHost { ip: string; zone: string; hostname: string | null; site: string | null; environment: string | null; asset_serial: string | null; registered: boolean }

const { apiFetch } = useApi()
// view：嵌在 2.x「3.2 架構圖」頁籤裡時由外層指定分頁，不顯示本頁自己的分頁列
const props = defineProps<{ view?: 'vcenter' | 'ocp' | 'ftp' }>()
const router = useRouter()
const url = (path: string, query: Record<string, string>) => { const q = new URLSearchParams(query).toString(); return q ? `${path}?${q}` : path }
const ocpLink = (q: Record<string, string>) => url('/v2/arch', { tab: 'ocpdetail', ...q })
const machine = (serial: string) => `/v2/machine/${encodeURIComponent(serial)}`
const tab = ref<'vcenter' | 'ocp' | 'ftp'>(props.view || 'vcenter')
watch(() => props.view, (v) => { if (v) tab.value = v })

const vc = ref<{ clusters: Cluster[]; totals: VcTotals } | null>(null)

// OCP：一座叢集＝一張卡，點開看每個節點的角色與狀態（使用者：「OCP 其實是一台大設備，
// 但由很多小的主機建立起來」——所以對外是一座，對內每個節點都要查得到）。
interface OcpNode {
  hostname: string | null; ip: string | null; asset_serial: string | null
  role: string; os: string; stage: string; stage_label: string; tone: string
  location: string | null; environment: string | null; api_id: string | null
  purpose: string | null; dup_count: number; dup_reason: string
  cluster_basis: string; cluster_by: string | null; cluster_at: string | null
}
interface OcpCluster {
  cluster: string; basis: string; node_count: number; nodes: OcpNode[]
  by_role: Record<string, number>; by_stage: Record<string, number>
  versions: Record<string, number>; locations: Record<string, number>
  environments: Record<string, number>
  primary_location: string; primary_env: string; spans: string[]; risks: string[]
  vips: string[]
  role_tiers: Record<string, { count: number; retired: number; ready: number; worst: string
    not_managed: boolean; by_stage: Record<string, number> }>
}
const ocp = ref<{
  clusters: OcpCluster[]
  totals: { clusters: number; nodes: number; ungrouped: number }
  cluster_names: string[]; note: string
  locations: string[]; environments: string[]
} | null>(null)

// 版面照使用者給的 FTP 架構圖：**機房＝欄、環境＝帶**（2026-09-20「架構圖分 機房*環境」
// 「像 FTP 的圖」）。一座叢集放在「它最多節點所在」的機房×環境；跨格的在卡片上標出來，
// 不偷偷歸成一格。
const ocpMatrix = computed(() => {
  const d = ocp.value
  if (!d) return []
  // 2026-09-21 使用者「測試要對齊」：原本每一欄各自堆疊，上面那格高度不一樣，
  // 下面的「測試」帶就歪掉了，讀起來不像同一列。改成「環境＝列」的方陣，
  // 交給 CSS grid 強制同一列等高。
  return d.environments.map((env) => ({
    env,
    cells: d.locations.map((loc) => ({
      location: loc,
      clusters: d.clusters.filter((c) => c.primary_location === loc && c.primary_env === env),
    })),
  }))
})
// 欄頭（機房名稱＋該機房節點數）單獨一列，跟下面的方陣共用同一組欄
const ocpCols = computed(() => {
  const d = ocp.value
  if (!d) return []
  return d.locations.map((loc) => ({
    location: loc,
    nodes: d.clusters.filter((c) => c.primary_location === loc)
      .reduce((s, c) => s + c.node_count, 0),
  }))
})
// 一台一個小方塊（使用者 2026-09-20）。四色，**紅色只給真的出事**：
// 綠＝資料齊全、黃＝納管中還缺資料、灰＝沒掃過／退役／豁免、紅＝失聯或退役卻還有回應。
// 「沒掃過」不是失敗，所以給灰不給黃——不然整面牌紅黃，真的出事那一台就沒人看見。
const NODE_TONE: Record<string, string> = {
  complete: 'ok', collected: 'ok',
  unregistered: 'mid', not_onboarded: 'mid', onboarded_stale: 'mid',
  no_facts: 'mid', no_services: 'mid', no_accounts: 'mid', conflict: 'mid',
  not_covered: 'none', no_ip: 'none', exempt: 'none',
  retired: 'gone',
  lost: 'bad', retired_alive: 'bad',
}
function squareTone(stage: string | null | undefined) {
  return NODE_TONE[stage || ''] ?? 'none'
}
// 現役排前、退役排後——一排小方塊看過去就是「這角色現在幾台、舊的還拖幾台」。
function nodesOfRole(c: OcpCluster, role: string) {
  const all = c.nodes.filter((n) => n.role === role)
  const gone = (n: OcpNode) => (n.stage === 'retired' || n.stage === 'exempt' ? 1 : 0)
  return [...all].sort((a, b) => gone(a) - gone(b))
}
// 全站摘要（圖下方那條，比照 FTP 圖的「整併摘要」）
const ocpSummary = computed(() => {
  const cs = ocp.value?.clusters ?? []
  return {
    mixed: cs.filter((c) => c.risks.some((r) => r.includes('種版本'))).length,
    dup: cs.filter((c) => c.risks.some((r) => r.includes('多筆登記'))).length,
    swap: cs.filter((c) => c.risks.some((r) => r.includes('汰換'))).length,
    style: cs.filter((c) => c.risks.some((r) => r.includes('寫法不同'))).length,
    span: cs.filter((c) => c.spans.length).length,
    roles: cs.reduce((acc, c) => {
      for (const [r, n] of Object.entries(c.by_role)) acc[r] = (acc[r] ?? 0) + n
      return acc
    }, {} as Record<string, number>),
  }
})
const route = useRoute()
const ftp = ref<{ hosts: FtpHost[]; totals: { hosts: number; registered: number } } | null>(null)
const loading = ref(true)
const openCluster = ref<string | null>(null)

async function load() {
  loading.value = true
  try {
    if (tab.value === 'vcenter' && !vc.value) vc.value = await apiFetch('/api/arch/vcenter')
    if (tab.value === 'ocp' && !ocp.value) ocp.value = await apiFetch('/api/arch/ocp')
    if (tab.value === 'ftp' && !ftp.value) ftp.value = await apiFetch('/api/arch/ftp')
  } finally {
    loading.value = false
  }
}
watch(tab, load, { immediate: true })

const SITES = ['內湖機房', '板橋機房', '敦南機房']
function bySite(env: string) {
  const out: Record<string, Cluster[]> = {}
  for (const s of SITES) out[s] = []
  for (const c of vc.value?.clusters ?? []) {
    if (c.env !== env) continue
    ;(out[c.site] ??= []).push(c)
  }
  return out
}
const prodBySite = computed(() => bySite('PROD'))
const uatBySite = computed(() => bySite('UAT'))

// FTP：依機房分欄。zone 是人畫圖的分區（DMZ/正式/測試），留著當標籤但不當系統事實。
const ftpBySite = computed(() => {
  const out: Record<string, FtpHost[]> = {}
  for (const h of ftp.value?.hosts ?? []) {
    const s = h.site || '未登記機房'
    ;(out[s] ??= []).push(h)
  }
  return out
})

function toggle(name: string) { openCluster.value = openCluster.value === name ? null : name }
// ---- 圖下面的清單（V2Table）；ESXi 台數點下去在下方列出（?vcc=叢集，可分享）
const vcRows = computed(() => (vc.value?.clusters ?? []).map((c, i) => ({ ...c, __no: i + 1, vsan_text: c.vsan ? 'vSAN' : '' })))
const vcc = computed(() => (typeof route.query.vcc === 'string' ? route.query.vcc : ''))
const openEsxi = (c: string | undefined) => router.replace({ query: { ...route.query, vcc: c } })
const esxiRows = computed(() => (vc.value?.clusters.find(c => c.cluster === vcc.value)?.esxi ?? []).map((e, i) => ({ ...e, __no: i + 1 })))
const ocpRows = computed(() => (ocp.value?.clusters ?? []).map((c, i) => ({ __no: i + 1, cluster: c.cluster, location: c.primary_location, environment: c.primary_env,
  nodes: c.node_count, roles: Object.entries(c.by_role).map(([r, n]) => `${r} ${n}`).join('、'), vips: c.vips.length, risks: c.risks.length + c.spans.length })))
const ftpRows = computed(() => (ftp.value?.hosts ?? []).map((h, i) => ({ ...h, __no: i + 1, reg: h.registered ? '已登記' : '未登記' })))
function printPage() { if (import.meta.client) window.print() }
</script>

<template>
  <div>

    <div class="bar">
      <button class="btn" type="button" @click="printPage">⎙ 列印／存 PDF</button>
    </div>

    <p v-if="loading" class="muted">載入中…</p>

    <!-- ===== OCP ===== -->
    <div v-else-if="tab === 'ocp' && ocp" class="sheet">
      <div class="sheet-title">
        OCP 叢集架構 · {{ ocp.totals.clusters }} 座、{{ ocp.totals.nodes }} 個節點
        <span v-if="ocp.totals.ungrouped" class="warnx">（{{ ocp.totals.ungrouped }} 台判不出叢集）</span>
      </div>
      <div class="sheet-body">
        <p class="ocp-note">{{ ocp.note }}</p>
        <div class="ocp-grid" :style="{ '--cols': ocpCols.length }">
          <div v-for="col in ocpCols" :key="col.location" class="col-hd">
            {{ col.location }} · {{ col.nodes }} 個節點
          </div>

          <template v-for="band in ocpMatrix" :key="band.env">
            <div v-for="cell in band.cells" :key="cell.location"
                 class="zone" :class="`z-${band.env}`">
              <div class="zone-hd">{{ band.env }}</div>
              <p v-if="!cell.clusters.length" class="dim small zone-empty">（這個機房沒有這個環境的叢集）</p>

              <div v-for="c in cell.clusters" :key="c.cluster" class="card ocp-card"
                   :class="{ ungrouped: c.cluster === '未分群' }">
                <div class="card-top">
                  <b class="cname">{{ c.cluster }}</b>
                  <span class="tag env">{{ c.node_count }} 台</span>
                </div>
                <!-- 第一層：照 OpenShift 部署拓樸圖的畫法（使用者 2026-09-20 選的那張）——
                     最上面一條對外入口 VIP，下面依角色分排。版本、分群依據、風險都收到第二層，
                     因為使用者看這張圖第一眼要知道的是「這座長什麼樣、哪一排沒資料」。 -->
                <div v-if="c.vips.length" class="tier tier-vip">
                  <span class="tier-n">入口 VIP</span>
                  <span v-for="v in c.vips.slice(0, 4)" :key="v" class="pill vip">{{ v }}</span>
                  <span v-if="c.vips.length > 4" class="dim small">+{{ c.vips.length - 4 }}</span>
                </div>
                <p v-else class="dim small tier-none">未查到對外 VIP（BIG-IP 清單沒對到這座，不代表沒有）</p>

                <div v-for="(tier, role) in c.role_tiers" :key="role" class="tier" :class="`tr-${role}`">
                  <!-- 鍵規定：每個數字都要點得進去看是哪幾台。
                       （使用者 2026-09-21：「未標示 2 了，我不能點進去看是哪兩個」） -->
                  <NuxtLink class="tier-n" :to="ocpLink({ cluster: c.cluster, role: String(role) })">
                    {{ role }}
                  </NuxtLink>
                  <NuxtLink class="tier-c" :to="ocpLink({ cluster: c.cluster, role: String(role) })">
                    {{ tier.count }}
                  </NuxtLink>
                  <span class="boxes">
                    <component :is="n.asset_serial ? 'NuxtLink' : 'i'"
                               v-for="n in nodesOfRole(c, role)"
                               :key="n.asset_serial || n.ip || n.hostname || ''"
                               class="nb" :class="`n-${squareTone(n.stage)}`"
                               :to="n.asset_serial ? machine(n.asset_serial) : undefined"
                               :title="`${n.hostname || n.ip || '—'}｜${n.stage_label}`" />
                  </span>
                  <!-- 使用者 2026-09-21：「0/10 又是什麼意思」——原本寫「0/10 資料齊全」太省。
                       改成白話＋直接講出剩下的停在哪一關，不要讓人猜。 -->
                  <span class="tier-b"
                        :title="`資料齊全＝納管漏斗最後一關（進得去、主機事實／服務／帳號都收到）。`
                          + `這排 ${tier.count} 台裡有 ${tier.ready} 台到位，其餘停在「${tier.worst}」。`">
                    <template v-if="tier.not_managed">第一期不佈收集帳號（見下方說明）</template>
                    <template v-else>
                      {{ tier.count }} 台裡 <b>{{ tier.ready }}</b> 台資料齊全
                      <template v-if="tier.ready < tier.count">，其餘停在「{{ tier.worst }}」</template>
                    </template>
                    <template v-if="tier.retired">　＋{{ tier.retired }} 台已退役</template>
                  </span>
                </div>

                <!-- 第二＋三層搬到 /ocp（2026-09-21 使用者：「東西很多 另開網頁展開」）。
                     卡片只留「長什麼樣」，版本／分群／風險／逐台狀態都在那一頁。 -->
                <NuxtLink class="ocp-more-link" :to="ocpLink({ cluster: c.cluster })">
                  看這座的版本／風險／{{ c.node_count }} 個節點 →
                  <span v-if="c.risks.length || c.spans.length" class="risk-n">{{ c.risks.length + c.spans.length }}</span>
                </NuxtLink>
              </div>
            </div>
          </template>
        </div>

        <!-- 底部說明帶：比照 FTP 那張圖的圖例／摘要 -->
        <div class="ocp-foot">
          <div class="foot-box">
            <div class="foot-h">為什麼 OCP 節點都是「非納管設備」</div>
            <p>RHCOS 系統區唯讀、本機帳號重佈就消失，要建帳號得改 MachineConfig（動整個節點池）。
              使用者 2026-09-21 拍板：**第一期不佈收集帳號**（改看「OCP 叢集明細」），這 151 台不再算進「要納管」的母體。</p>
            <p>第二期改向叢集 API 取資料（只需唯讀 token，不會動到叢集）。</p>
          </div>
          <div class="foot-box">
            <div class="foot-h">圖例</div>
            <p class="lg">
              <i class="nb n-ok" /> 資料齊全　<i class="nb n-mid" /> 納管中、還缺資料　
              <i class="nb n-none" /> 沒掃過／第一期不佈收集帳號　<i class="nb n-gone" /> 已退役（不算進台數）　
              <i class="nb n-bad" /> 失聯或退役卻還有回應
            </p>
            <p>一個小方塊＝一台，滑過去看主機名與狀態，點進去是該台的資產頁。
              角色來自用途／主機名；判不出來標「未標示」，不猜。</p>
            <p>欄＝機房，帶＝環境；一座叢集放在它最多節點所在的那一格。</p>
            <p>分群依據：FQDN ＞ 用途／名稱 ＞ 人工指定；判不出來標「未分群」，不用機房或網段硬湊。</p>
          </div>
          <div class="foot-box">
            <div class="foot-h">摘要</div>
            <!-- 鍵規定：每個數字都要點得進去看是哪幾筆
                 （使用者 2026-09-21：「這些要能點進去看細項」） -->
            <p>
              <NuxtLink :to="ocpLink({})">叢集 {{ ocp.totals.clusters }} 座</NuxtLink>　
              節點 {{ ocp.totals.nodes }} 個　
              <NuxtLink v-if="ocp.totals.ungrouped" :to="ocpLink({ cluster: '未分群' })">
                未分群 {{ ocp.totals.ungrouped }} 台
              </NuxtLink>
              <template v-else>未分群 0 台</template>
            </p>
            <p>
              角色：<NuxtLink v-for="(n, r) in ocpSummary.roles" :key="r"
                          class="lnk" :to="ocpLink({ role: String(r) })">{{ r }} {{ n }}　</NuxtLink>
            </p>
          </div>
          <div class="foot-box">
            <div class="foot-h">要注意的</div>
            <p><NuxtLink :to="ocpLink({ risk: 'mixed' })">同叢集混版：{{ ocpSummary.mixed }} 座</NuxtLink></p>
            <p><NuxtLink :to="ocpLink({ risk: 'dup' })">同一節點重複登記：{{ ocpSummary.dup }} 座</NuxtLink></p>
            <p><NuxtLink :to="ocpLink({ risk: 'span' })">跨機房或跨環境：{{ ocpSummary.span }} 座</NuxtLink></p>
            <p><NuxtLink :to="ocpLink({ risk: 'swap' })">疑似汰換：{{ ocpSummary.swap }} 座</NuxtLink></p>
            <p><NuxtLink :to="ocpLink({ risk: 'style' })">版本寫法不一致：{{ ocpSummary.style }} 座</NuxtLink></p>
          </div>
        </div>
      </div>
      <div class="sheet-foot">
        <div class="legend">
          節點判定用全站正典的 OS 類型（OpenShift 節點），狀態沿用納管漏斗的關卡——
          兩邊不會各說各話。這張圖是系統算的，不用人維護成員清單。
        </div>
        <div class="stat">
          叢集 {{ ocp.totals.clusters }}　節點 {{ ocp.totals.nodes }}　未分群 {{ ocp.totals.ungrouped }}
        </div>
      </div>
    </div>

    <section v-if="!loading && tab === 'ocp' && ocp" class="list no-print">
      <V2Table :rows="ocpRows" prefix="C" row-key="cluster" unit="座" export-name="架構圖_OCP叢集" empty="沒有叢集"
               :cols="[{ k: 'cluster', label: '叢集', link: (r: any) => ocpLink({ cluster: r.cluster }) }, { k: 'location', label: '主要機房' }, { k: 'environment', label: '環境' },
                       { k: 'nodes', label: '節點', mono: true, link: (r: any) => ocpLink({ cluster: r.cluster }) }, { k: 'roles', label: '角色' }, { k: 'vips', label: '入口 VIP', mono: true }, { k: 'risks', label: '要注意', mono: true }]" />
    </section>

    <!-- ===== vCenter ===== -->
    <div v-else-if="tab === 'vcenter' && vc" class="sheet">
      <div class="sheet-title">vCenter 虛擬化架構</div>

      <div class="sheet-body">
        <div class="band prod">PROD 正式環境</div>
        <div class="cols">
          <div v-for="s in SITES" :key="'p'+s" class="col">
            <div class="col-hd">{{ s }}</div>
            <div v-if="!prodBySite[s].length" class="none">—</div>
            <div v-for="c in prodBySite[s]" :key="c.cluster" class="card prodc" @click="toggle(c.cluster)">
              <div class="card-top">
                <span v-if="c.vsan" class="tag vsan">vSAN</span>
                <b class="cname">{{ c.cluster }}</b>
              </div>
              <div class="metrics">{{ c.esxi_count }} ESXi · {{ c.vm_count }} VM · {{ c.datastore_count }} datastore</div>
              <div v-if="openCluster === c.cluster" class="drill" @click.stop>
                <div v-for="e in c.esxi" :key="e.label" class="esxi-row">
                  <NuxtLink v-if="e.asset_serial" class="dl" :to="machine(e.asset_serial)">{{ e.label }}</NuxtLink>
                  <span v-else>{{ e.label }}</span>
                  <span class="vmn">{{ e.vm }} VM</span>
                </div>
              </div>
              <div v-else class="hint-open">點看 ESXi ▾</div>
            </div>
          </div>
        </div>

        <div class="band uat">UAT 測試環境</div>
        <div class="cols">
          <div v-for="s in SITES" :key="'u'+s" class="col">
            <div class="col-hd">{{ s }}</div>
            <div v-if="!uatBySite[s].length" class="none">無 UAT 叢集</div>
            <div v-for="c in uatBySite[s]" :key="c.cluster" class="card uatc" @click="toggle(c.cluster)">
              <div class="card-top">
                <span class="tag uat">UAT</span><b class="cname">{{ c.cluster }}</b>
              </div>
              <div class="metrics">{{ c.esxi_count }} ESXi · {{ c.vm_count }} VM · {{ c.datastore_count }} datastore</div>
              <div v-if="openCluster === c.cluster" class="drill" @click.stop>
                <div v-for="e in c.esxi" :key="e.label" class="esxi-row">
                  <NuxtLink v-if="e.asset_serial" class="dl" :to="machine(e.asset_serial)">{{ e.label }}</NuxtLink>
                  <span v-else>{{ e.label }}</span>
                  <span class="vmn">{{ e.vm }} VM</span>
                </div>
              </div>
              <div v-else class="hint-open">點看 ESXi ▾</div>
            </div>
          </div>
        </div>
      </div>

      <div class="sheet-foot">
        <div class="legend">
          <span class="tag vsan">vSAN</span> 超融合叢集
          <span class="sw prod"></span> PROD　<span class="sw uat"></span> UAT
        </div>
        <div class="stat">
          機房 {{ vc.totals.sites }}　叢集 {{ vc.totals.clusters }}　ESXi {{ vc.totals.esxi }}
          VM {{ vc.totals.vm.toLocaleString() }}　datastore {{ vc.totals.datastore }}
        </div>
      </div>
    </div>

    <section v-if="!loading && tab === 'vcenter' && vc" class="list no-print">
      <V2Table :rows="vcRows" prefix="V" row-key="cluster" unit="個叢集" export-name="架構圖_vCenter叢集" empty="沒有叢集"
               :cols="[{ k: 'site', label: '機房' }, { k: 'env', label: '環境' }, { k: 'cluster', label: '叢集' }, { k: 'vsan_text', label: 'vSAN' },
                       { k: 'esxi_count', label: 'ESXi', mono: true }, { k: 'vm_count', label: 'VM', mono: true }, { k: 'datastore_count', label: 'datastore', mono: true }]">
        <template #cell-esxi_count="{ row }"><button type="button" class="lbtn" data-testid="arch-esxi" @click="openEsxi(vcc === row.cluster ? undefined : row.cluster)">{{ row.esxi_count }}</button></template>
      </V2Table>
      <div v-if="vcc" class="drillbox" data-testid="arch-esxi-drill">
        <b>{{ vcc }} 的 ESXi {{ esxiRows.length }} 台</b> <button type="button" class="lbtn" @click="openEsxi(undefined)">關閉</button>
        <V2Table :rows="esxiRows" prefix="E" row-key="label" unit="台" :export-name="`架構圖_ESXi_${vcc}`" empty="沒有 ESXi"
                 :cols="[{ k: 'label', label: 'ESXi', link: (r: any) => (r.asset_serial ? machine(r.asset_serial) : null) }, { k: 'vm', label: 'VM', mono: true }]" />
      </div>
    </section>

    <!-- ===== FTP ===== -->
    <div v-else-if="tab === 'ftp' && ftp" class="sheet">
      <div class="sheet-title">FTP 主機架構</div>
      <div class="sheet-body">
        <div v-if="!ftp.hosts.length" class="empty-ftp">
          尚未設定 FTP 主機清單。<InfoNote>FTP 哪些主機是人維護的（系統收不到「這台是 FTP」），清單存在伺服器設定裡（<code>arch_ftp_hosts</code>）。設定後這裡會用資產庫的真資料填每台的主機名／機房／環境別。</InfoNote>
        </div>
        <div v-else class="cols ftpcols">
          <div v-for="(hosts, s) in ftpBySite" :key="s" class="col">
            <div class="col-hd">{{ s }}</div>
            <div v-for="h in hosts" :key="h.ip" class="card ftpc">
              <div class="card-top">
                <span v-if="h.environment" class="tag env">{{ h.environment }}</span>
                <span v-if="h.zone" class="tag zone">{{ h.zone }}</span>
                <b class="cname">
                  <NuxtLink v-if="h.asset_serial" class="dl" :to="machine(h.asset_serial)">{{ h.hostname || h.ip }}</NuxtLink>
                  <template v-else>{{ h.hostname || h.ip }}</template>
                </b>
              </div>
              <div class="metrics">{{ h.ip }}<span v-if="!h.registered" class="unreg"> · 未登記</span></div>
            </div>
          </div>
        </div>
      </div>
      <div class="sheet-foot">
        <div class="legend">此圖成員清單為人工維護；主機名／機房／環境別為資產庫真資料，其餘（帳號數等）系統查不到故不列。</div>
        <div class="stat">FTP 主機 {{ ftp.totals.hosts }}　已登記 {{ ftp.totals.registered }}</div>
      </div>
    </div>
    <section v-if="!loading && tab === 'ftp' && ftp && ftp.hosts.length" class="list no-print">
      <V2Table :rows="ftpRows" prefix="F" row-key="ip" unit="台" export-name="架構圖_FTP主機" empty="沒有 FTP 主機"
               :cols="[{ k: 'site', label: '機房' }, { k: 'zone', label: '分區' }, { k: 'environment', label: '環境' }, { k: 'hostname', label: '主機名稱', link: (r: any) => (r.asset_serial ? machine(r.asset_serial) : null), text: (r: any) => r.hostname || r.ip },
                       { k: 'ip', label: 'IP', mono: true }, { k: 'reg', label: '登記', tone: (r: any) => (r.registered ? '' : 'test') }]" />
    </section>
  </div>
</template>

<style scoped>
.list { margin-top: 14px; }
.drillbox { margin-top: 10px; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); font-size: 13px; }
.lbtn { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; }
@media print { .no-print { display: none; } }
.ocp-note { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
/* 機房＝欄、環境＝列。grid 預設同一列等高，所以「測試」一定跟「測試」齊。*/
.ocp-grid { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); gap: 8px; }
.ocp-grid > .col-hd { align-self: end; }
.zone { border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px; min-width: 0; }
.zone-hd { font-size: 12px; color: var(--ink-soft); text-align: right; margin-bottom: 4px; }
.zone-empty { margin: 2px 0; }
.zone.z-正式 { border-color: #15803d; }
.zone.z-正式 .zone-hd { color: #15803d; }
.zone.z-測試 { border-color: #6d28d9; }
.zone.z-測試 .zone-hd { color: #6d28d9; }
.zone.z-DMZ { border-color: #b91c1c; }
.zone.z-DMZ .zone-hd { color: #b91c1c; }
.tier { display: flex; align-items: center; gap: 6px; padding: 3px 6px; margin-top: 4px;
  border-left: 3px solid var(--border); border-radius: 0 4px 4px 0; background: var(--bg-soft, transparent); }
.tier-n { font-size: 12px; color: var(--ink-soft); min-width: 62px; }
.tier-c { font-size: 15px; font-weight: 600; }
a.tier-n, a.tier-c { text-decoration: none; }
a.tier-n:hover, a.tier-c:hover { text-decoration: underline; }
.tier-b { font-size: 12px; color: var(--ink-soft); }
.tier-none { margin: 4px 0 0; }
.tier-vip { border-left-color: #0ea5e9; }
.tier.tr-Master { border-left-color: #1d4ed8; }
.tier.tr-Infra { border-left-color: #7c3aed; }
.tier.tr-Worker { border-left-color: #0f766e; }
.tier.tr-Bootstrap { border-left-color: #a16207; }
.boxes { display: flex; flex-wrap: wrap; gap: 2px; align-items: center; }
.nb { width: 11px; height: 11px; border-radius: 2px; display: inline-block; border: 1px solid transparent; }
.nb.n-ok { background: #15803d; }
.nb.n-mid { background: #ca8a04; }
.nb.n-none { background: transparent; border-color: var(--border-strong, #9ca3af); }
.nb.n-bad { background: #b91c1c; }
.nb.n-gone { background: transparent; border-style: dashed; border-color: var(--muted, #9ca3af); opacity: .7; }
.lg .nb { vertical-align: -1px; }
.pill.vip { font-family: ui-monospace, monospace; }
.ocp-more-link { display: inline-flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 12px; }
.risk-n { display: inline-block; min-width: 16px; padding: 0 5px; border-radius: 9px;
  background: #fef3c7; color: #92400e; font-size: 11px; text-align: center; }
.ocp-foot { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
.foot-box { flex: 1 1 200px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.foot-h { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.foot-box p { font-size: 12px; color: var(--ink-soft); margin: 2px 0; line-height: 1.6; }
.ocp-card { cursor: default; }
.ocp-card.ungrouped { border-style: dashed; }
.ocp-card .card-top { cursor: pointer; }
.pill { font-size: 11.5px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border-strong); }
.pill.ver { color: var(--ink-soft); }
.pill.loc { color: var(--muted); }
.ocp-risk { margin: 6px 0 0; padding-left: 1.2em; font-size: 12.5px; color: #b45309; }
.ocp-nodes { margin-top: 8px; overflow-x: auto; }
.ocp-nodes table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.ocp-nodes th, .ocp-nodes td { padding: 4px 8px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
.ocp-nodes .mono { font-family: ui-monospace, monospace; }
.st { font-size: 11.5px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border-strong); }
.st.t-bad { color: #b91c1c; border-color: #b91c1c; }
.st.t-warn { color: #b45309; border-color: #b45309; }
.st.t-good { color: #15803d; border-color: #15803d; }
.dup { font-size: 10.5px; margin-left: 5px; padding: 0 5px; border-radius: 8px;
       background: var(--warn-soft); color: var(--warn-text); }
.mv select { font: inherit; font-size: 12px; padding: 1px 4px; }
.manual { font-style: normal; margin-left: 4px; color: var(--brand); }
.warnx { color: #b45309; font-size: 13px; }
.small { font-size: 11.5px; }
.section-divider { margin: 0 0 12px; font-size: 11px; color: var(--brand-dark); font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
.bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); }
.tab { padding: 8px 16px; font-size: 12.5px; color: var(--ink-soft); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tab.active { color: var(--brand-dark); font-weight: 700; border-bottom-color: var(--brand); }
.muted { opacity: .7; }

.sheet { background: #fff; border: 1px solid #d0d5dd; border-radius: 8px; overflow: hidden; color: #1a1a2e; }
.sheet-title { background: #1F3864; color: #fff; text-align: center; padding: 12px; font-size: 18px; font-weight: 600; letter-spacing: 1px; }
.sheet-body { padding: 12px; }
.band { color: #fff; font-size: 13px; font-weight: 600; padding: 5px 10px; border-radius: 5px; margin: 4px 0 8px; }
.band.prod { background: #2E7D46; }
.band.uat { background: #7B5EA7; margin-top: 14px; }
.cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.col-hd { text-align: center; font-size: 12px; color: #555; margin-bottom: 6px; }
.col { display: flex; flex-direction: column; }
.none { border: 1px dashed #c8c8d0; border-radius: 6px; padding: 7px; text-align: center; font-size: 11px; color: #aaa; }
.card { border: 1.5px solid #2E7D46; border-radius: 6px; padding: 7px 9px; margin-bottom: 6px; cursor: pointer; }
.card.uatc { border-color: #7B5EA7; }
.card.ftpc { border-color: #1F3864; cursor: default; }
.card-top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.cname { font-size: 12px; }
.metrics { font-size: 11px; color: #555; margin-top: 2px; }
.unreg { color: #c0392b; }
.hint-open { font-size: 10.5px; color: #9aa; margin-top: 3px; }
.drill { margin-top: 6px; border-top: 1px dashed #ccc; padding-top: 5px; }
.esxi-row { display: flex; justify-content: space-between; font-size: 11px; padding: 2px 0; gap: 8px; }
.esxi-row .vmn { color: #777; white-space: nowrap; }
.dl { color: #1F3864; text-decoration: none; }
.dl:hover { text-decoration: underline; }
.tag { font-size: 10px; padding: 1px 6px; border-radius: 4px; color: #fff; white-space: nowrap; }
.tag.vsan { background: #1D9E75; }
.tag.uat { background: #BA7517; }
.tag.env { background: #1F3864; }
.tag.zone { background: #888; }
.sheet-foot { display: flex; gap: 10px; padding: 10px 12px; border-top: 1px solid #d0d5dd; flex-wrap: wrap; align-items: center; }
.legend { flex: 1; min-width: 200px; font-size: 11px; color: #555; }
.sw { display: inline-block; width: 14px; height: 10px; border-radius: 2px; vertical-align: middle; }
.sw.prod { background: #2E7D46; } .sw.uat { background: #7B5EA7; }
.stat { background: #eef2f8; border: 1px solid #d0d5dd; border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.empty-ftp { padding: 24px; text-align: center; color: #777; font-size: 13px; }
.ftpcols { grid-template-columns: repeat(3, 1fr); }

@media print {
  .bar, .section-divider { display: none; }
  .sheet { border: none; }
}
</style>
