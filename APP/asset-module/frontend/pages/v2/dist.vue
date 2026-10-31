<script setup lang="ts">
// 10.2 分布統計（2.x 第四批；設計筆記 docs/ui-v2/批次4設計_2026-10-28.md）：機房分布｜作業系統｜業務系統排行｜環境別。
// 只放台數（用電與空間只在 5.1，機房列給連結）。資料：/api/v2/dist（v2_dist.py）——所在地／作業系統／環境別＝2.1 主機查詢
// 同一份 v2_hosts.machines（每個維度加總＝2.1「全部登記台數」）；業務系統排行＝3.1 同一份 v2_systems.listing。
// 每格可點，清單＝/api/v2/dist/drill（同一份計算，列數＝格子數字；verify_numbers check_v2_dist 釘住）。
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const data = ref<any>(null)
const err = ref('')
try { data.value = await apiFetch('/api/v2/dist') } catch (e: any) { err.value = `分布統計載入失敗：${errMsg(e)}` }
const TABS = [{ k: 'site', label: '機房分布' }, { k: 'os', label: '作業系統' }, { k: 'osver', label: '作業系統版本' }, { k: 'systems', label: '業務系統排行' }, { k: 'env', label: '環境別' }, { k: 'phys', label: '實體機分布' }]
const tab = computed({ get: () => (TABS.some(t => t.k === q('tab')) ? q('tab') : 'site'), set: (v: string) => router.replace({ query: { tab: v } }) })
const DIM_LABEL: Record<string, string> = { site: '所在地', os: '作業系統大類', osver: '作業系統版本', env: '環境別' }
const dcRooms = computed(() => new Set<string>(data.value?.dc_rooms ?? []))
const drillUrl = (dim: string, value: string, kind: string) => `/v2/dist?tab=${dim}&dv=${encodeURIComponent(value)}&dk=${kind}`
const rows = computed(() => (data.value?.dims?.[tab.value] ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, power: dcRooms.value.has(r.value) ? '用電／空間' : '' })))
const totals = computed(() => {
  const xs = data.value?.dims?.[tab.value] ?? []
  return ['all', 'phys', 'vm', 'online'].reduce((o: any, k) => ({ ...o, [k]: xs.reduce((a: number, r: any) => a + r[k], 0) }), {})
})
const sysRows = computed(() => (data.value?.systems ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1 })))
const sysUrl = (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}`

// 下鑽清單
const drill = ref<any>(null)
const drillErr = ref('')
watch(() => [q('dv'), q('dk'), q('tab')], async ([v, k]) => {
  drill.value = null; drillErr.value = ''
  if (!v || !k || tab.value === 'systems') return
  try { drill.value = await apiFetch('/api/v2/dist/drill', { query: { dim: tab.value, value: v, kind: k } }) } catch (e: any) { drillErr.value = `清單載入失敗：${errMsg(e)}` }
}, { immediate: true })
const host = (r: any) => `/v2/machine/${encodeURIComponent(r.key)}`
const cols = computed(() => [
  { k: 'value', label: DIM_LABEL[tab.value] || '值' },
  { k: 'all', label: '全部（台）', mono: true, link: (r: any) => drillUrl(tab.value, r.value, 'all') },
  { k: 'phys', label: '非虛擬機（台）', mono: true, link: (r: any) => (r.phys ? drillUrl(tab.value, r.value, 'phys') : null) },
  { k: 'vm', label: '虛擬機（台）', mono: true, link: (r: any) => (r.vm ? drillUrl(tab.value, r.value, 'vm') : null) },
  { k: 'online', label: '運作中（台）', mono: true, link: (r: any) => (r.online ? drillUrl(tab.value, r.value, 'online') : null) },
  ...(tab.value === 'osver' ? [{ k: 'measured', label: '其中實測（台）', mono: true }] : []),
  ...(tab.value === 'site' ? [{ k: 'power', label: '5.1 機房平面圖', link: (r: any) => (r.power ? `/v2/datacenter?room=${encodeURIComponent(r.value)}&tab=power` : null) }] : []),
])
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
      <!-- 各環境實體機分布（舊版 /reports/physical-distribution，2.019.1）：直接嵌入同一個元件，API 不變 -->
      <ClientOnly v-if="tab === 'phys'"><V2Embed><PhysicalDist /></V2Embed></ClientOnly>
      <template v-else-if="data && tab !== 'systems'">
        <template v-if="q('dv')">
          <div class="bar"><NuxtLink :to="`/v2/dist?tab=${tab}`">‹ 返回</NuxtLink><b>{{ drill?.label || '' }}</b><span v-if="drill" class="muted">{{ drill.rows.length }} 台</span></div>
          <p v-if="drillErr" class="err">{{ drillErr }}</p>
          <V2Table v-else-if="drill" :rows="drill.rows" prefix="D" unit="台" empty="沒有符合的主機"
                   :cols="[{ k: 'name', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'type', label: '類型' }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }, { k: 'env', label: '環境別' }, { k: 'status_label', label: '偵測狀態' }, ...(tab === 'osver' ? [{ k: 'os_text', label: '作業系統（原始）' }, { k: 'os_src', label: '來源' }] : [])]" />
          <p v-else class="muted">載入中…</p>
        </template>
        <template v-else>
          <V2Table :rows="rows" prefix="G" :max="500" empty="無資料" :cols="cols" />
          <p v-if="tab === 'osver'" class="muted sum">AIX、Windows 分版本，Linux 不分發行版只算一行；網路、儲存、虛擬化平台等歸「其他」、沒有 OS 資料的歸「未知」，所以合計＝全部登記台數。
            <InfoNote>來源＝「實測」代表這台已納管、系統收集到它自己回報的 OS，版本以它為準（CIA 登記的版本常不準）；「登記」＝還沒收集到，暫用 CIA 登記的 OS 欄。點台數可以看是哪幾台、原始寫法與來源。Windows Server 2012 與 2012 R2 分開算。</InfoNote></p>
          <p class="muted sum">合計：全部 {{ totals.all.toLocaleString() }}・非虛擬機 {{ totals.phys.toLocaleString() }}・虛擬機 {{ totals.vm.toLocaleString() }}・運作中 {{ totals.online.toLocaleString() }}（台）</p>
        </template>
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
</style>
