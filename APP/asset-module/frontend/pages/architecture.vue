<script setup lang="ts">
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
const tab = ref<'vcenter' | 'ftp'>('vcenter')

const vc = ref<{ clusters: Cluster[]; totals: VcTotals } | null>(null)
const ftp = ref<{ hosts: FtpHost[]; totals: { hosts: number; registered: number } } | null>(null)
const loading = ref(true)
const openCluster = ref<string | null>(null)

async function load() {
  loading.value = true
  try {
    if (tab.value === 'vcenter' && !vc.value) vc.value = await apiFetch('/api/arch/vcenter')
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
function printPage() { if (import.meta.client) window.print() }
</script>

<template>
  <div>
    <div class="section-divider">架構圖</div>

    <div class="bar">
      <div class="tabs">
        <div class="tab" :class="{ active: tab === 'vcenter' }" @click="tab = 'vcenter'">vCenter 虛擬化</div>
        <div class="tab" :class="{ active: tab === 'ftp' }" @click="tab = 'ftp'">FTP 主機</div>
      </div>
      <button class="btn" type="button" @click="printPage">⎙ 列印／存 PDF</button>
    </div>

    <p v-if="loading" class="muted">載入中…</p>

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
                  <NuxtLink v-if="e.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(e.asset_serial)}`">{{ e.label }}</NuxtLink>
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
                  <NuxtLink v-if="e.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(e.asset_serial)}`">{{ e.label }}</NuxtLink>
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
                  <NuxtLink v-if="h.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(h.asset_serial)}`">{{ h.hostname || h.ip }}</NuxtLink>
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
  </div>
</template>

<style scoped>
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
