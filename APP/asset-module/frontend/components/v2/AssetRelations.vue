<script setup lang="ts">
// 資產明細與操作（V20，2.059.2）「對外入口與關聯」頁籤：舊 /assets/[serial] 的對外入口（VIP）、OCP 同叢集節點、虛擬化位置、SAN 原樣搬來（唯讀）。
//   VIP：/api/assets/{序號} 帶的 vip_pools（後端 vip_view.host_vips 算好，前端不另算）
//   同叢集：GET /api/arch/ocp/siblings?asset_serial=　虛擬化位置：GET /api/arch/vm-placement/{序號}
//   SAN：detail.san_collected 為 false 就不問；有的話 GET /api/san/switches/{ip}（明細在主機履歷「硬體與位置」→ SAN）
const props = defineProps<{ detail: any; serial: string }>()
const { apiFetch } = useApi()
const enc = encodeURIComponent
const why = (e: any) => e?.data?.detail ?? e?.message ?? e
const hw = computed<any>(() => props.detail?.hardware ?? {})

const vipRows = computed(() => (props.detail?.vip_pools?.vips ?? []).map((v: any, i: number) => ({
  __no: i + 1, vip: v.vip, kind: v.kind === 'cluster' ? '疑似叢集' : 'VIP', services: (v.services_here ?? []).map((s: any) => s.name).join('、'),
  pool: (v.pool ?? []).map((m: any) => (m.is_self ? `${m.hostname}（這台）` : m.hostname || m.ip)).join('、'),
  verdict: v.pool_size <= 1 ? 'CIA 登記上只有這台' : v.kind === 'cluster' ? `疑似叢集，同叢集 ${v.pool_size} 台` : `同一入口後面 ${v.pool_size} 台`,
  retired: v.retired_also ? `另有 ${v.retired_also} 台已退役` : '', raw: (v.raw_values ?? []).filter((x: string) => x !== v.vip).join('、'),
})))
const vipCols = [
  { k: 'vip', label: '入口', mono: true, link: (r: any) => `/v2/dist?tab=vip&vip=${enc(r.vip)}` }, { k: 'kind', label: '判斷' }, { k: 'services', label: '這台上的服務' },
  { k: 'pool', label: '同一入口後面的主機' }, { k: 'verdict', label: '判讀' }, { k: 'retired', label: '退役' }, { k: 'raw', label: '登記值' },
]
const misfiled = computed(() => (props.detail?.vip_pools?.misfiled ?? []).map((m: any, i: number) => ({ ...m, __no: i + 1 })))

const sib = ref<any>(null)
const sibErr = ref('')
const placement = ref<any>(null)
const san = ref<any>(null)
onMounted(async () => {
  apiFetch<any>(`/api/arch/ocp/siblings?asset_serial=${enc(props.serial)}`).then(v => { sib.value = v }).catch((e: any) => { sibErr.value = `同叢集節點載入失敗：${why(e)}` })
  apiFetch<any>(`/api/arch/vm-placement/${enc(props.serial)}`).then(v => { placement.value = v }).catch(() => { placement.value = { found: false, error: true } })
  if (hw.value.ip && props.detail?.san_collected !== false) apiFetch<any>(`/api/san/switches/${enc(hw.value.ip)}`).then(v => { san.value = v }).catch(() => { san.value = null /* 404＝這台沒收過 SAN，不顯示 */ })
})
const masters = computed(() => (sib.value?.siblings ?? []).filter((s: any) => s.role === 'Master').slice(0, 5).map((s: any, i: number) => ({ ...s, __no: i + 1, name: s.hostname || s.ip })))
const machine = (k: string) => `/v2/machine/${enc(k)}`
</script>

<template>
  <div>
    <h3 class="h3">對外入口（VIP）{{ vipRows.length }} 個<InfoNote>每個入口：這台上跑哪些服務、同一入口後面還有哪些主機。「CIA 登記上只有這台」代表清冊裡這個入口只登記在這台——不等於單點故障，F5 實際設定沒有驗證，而且 VIP 會浮動。已報廢的主機不算在同池裡。點入口看 10.2「入口（VIP）」後面全部是哪幾台。</InfoNote></h3>
    <p v-if="detail?.vip_pools?.error" class="err">入口資料載入失敗：{{ detail.vip_pools.error }}</p>
    <V2Table :rows="vipRows" :cols="vipCols" prefix="V" unit="個入口" empty="CIA 沒有登記這台的 VIP" />
    <template v-if="misfiled.length">
      <h4 class="h4">VIP 欄填的不是 IP（{{ misfiled.length }}）<InfoNote>多半填錯欄位，建議回頭修 CIA。</InfoNote></h4>
      <V2Table :rows="misfiled" :cols="[{ k: 'value', label: '填的值', mono: true }, { k: 'kind_label', label: '判斷' }, { k: 'asset_serial', label: '登記', mono: true, link: (r: any) => machine(r.asset_serial) }]" prefix="X" />
    </template>

    <h3 class="h3">虛擬化位置</h3>
    <p v-if="!placement" class="muted">載入中…</p>
    <p v-else-if="placement.error" class="err">讀不到虛擬化位置</p>
    <p v-else-if="!placement.found" class="muted">不是 VM，或 vCenter 盤點資料（RVTools）裡沒有這台</p>
    <dl v-else class="kv">
      <dt>vCenter</dt><dd>{{ placement.esxi_vc || '—' }}</dd>
      <dt>叢集</dt><dd>{{ placement.cluster || '—' }}</dd>
      <dt>ESXi</dt><dd><NuxtLink v-if="placement.esxi_serial" :to="machine(placement.esxi_serial)">{{ placement.esxi }}</NuxtLink><template v-else>{{ placement.esxi || '—' }}</template></dd>
      <dt>ESXi IP</dt><dd class="mono">{{ placement.esxi_ip || '—' }}</dd>
      <dt>機房（推導自 ESXi）<InfoNote>推導值：VM 跑在該 ESXi 上、ESXi 登記在這機房。不是 VM 自己的登記機房。</InfoNote></dt><dd>{{ placement.esxi_location || 'ESXi 未登記機房' }}</dd>
    </dl>

    <h3 class="h3">OCP 同叢集節點</h3>
    <p v-if="sibErr" class="err">{{ sibErr }}</p>
    <p v-else-if="!sib" class="muted">載入中…</p>
    <p v-else-if="!sib.is_ocp" class="muted">不是 OpenShift 節點</p>
    <template v-else>
      <p class="row">叢集 <b>{{ sib.cluster }}</b>・全叢集 {{ sib.node_count }} 個節點、資料齊全 {{ sib.healthy }} 台<span v-if="sib.me">・這台：{{ sib.me.role }}</span>
        <NuxtLink :to="`/v2/arch?tab=ocpdetail&cluster=${encodeURIComponent(sib.cluster || '')}`">看整座（逐台狀態）</NuxtLink>
        <InfoNote>分群依據：{{ sib.basis }}。各角色：{{ Object.entries(sib.by_role || {}).map(([r, n]) => `${r} ${n}`).join('、') }}</InfoNote></p>
      <ul v-if="sib.risks?.length" class="risk"><li v-for="r in sib.risks" :key="r">{{ r }}</li></ul>
      <V2Table :rows="masters" :cols="[{ k: 'name', label: 'Master', link: (r: any) => (r.asset_serial ? machine(r.asset_serial) : null) }, { k: 'role', label: '角色' }, { k: 'os', label: 'OS' }, { k: 'stage_label', label: '狀態' }, { k: 'location', label: '機房' }]" prefix="M" empty="沒有 Master 資料" />
    </template>

    <template v-if="san">
      <h3 class="h3">SAN</h3>
      <p class="row">{{ san.switch_name || hw.ip }}・port {{ san.port_count ?? '—' }}・zone {{ san.zone_count ?? '—' }}・WWPN {{ san.wwpn_count ?? '—' }}・收集 {{ san.collected_at || '—' }}
        <NuxtLink :to="`/v2/machine/${encodeURIComponent(serial)}?tab=hw&sec=san`">看 SAN 明細</NuxtLink>
        <NuxtLink to="/v2/collect?tab=san">6.2 SAN 收集</NuxtLink></p>
    </template>
  </div>
</template>

<style scoped>
.h3 { font-size: 15px; margin: 16px 0 6px; display: flex; align-items: center; gap: 6px; }
.h4 { font-size: 14px; margin: 10px 0 6px; display: flex; align-items: center; gap: 6px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; margin: 4px 0; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
.mono { font-family: var(--disp); }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; font-size: 13px; margin: 0; }
.kv dt { color: var(--ink-aux); display: flex; gap: 4px; align-items: center; }
.kv dd { margin: 0; color: var(--ink); }
.risk { margin: 4px 0; padding-left: 18px; color: var(--warn-text); font-size: 13px; }
</style>
