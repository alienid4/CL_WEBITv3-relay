<script setup lang="ts">
// 掃描結果頁：儀表板「本次掃描 / 本次掃到存活」磚塊點進來的落點。
// 這裡看的是「掃描側」的事實（這次網路上活著的主機），跟資產查詢（CIA 登記側）不同；
// 每筆標註有沒有登記，未登記的可以直接跳去納入管理。
interface ScanItem {
  ip: string | null
  hostname: string | null
  device_model: string | null
  segment: string | null
  is_vm: number | null
  registered: boolean
  asset_serial?: string | null
  // S16 指紋：比 device_model 的「(網路探測)」內部標記有意義得多
  mac?: string | null
  mac_vendor?: string | null
  open_ports?: string | null
  os_guess?: string | null
}
interface ScanResults {
  scan_time: string | null
  items: ScanItem[]
}

const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()

const data = ref<ScanResults | null>(null)
const items = computed(() => data.value?.items ?? [])
const { sortKey, sortDir, toggle, sorted } = useSort(items, 'ip')
const loading = ref(false)
const errorMessage = ref('')

// ?registered=yes|no；不帶就是全部
const filter = computed(() => {
  const v = route.query.registered
  return v === 'yes' || v === 'no' ? v : ''
})

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const query: Record<string, string> = {}
    if (filter.value) query.registered = filter.value
    data.value = await apiFetch<ScanResults>('/api/scan/results', { query })
  } catch {
    errorMessage.value = '掃描結果載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
await load()
watch(() => route.query, load)

async function setFilter(v: string) {
  await router.replace({ path: '/scan-results', query: v ? { registered: v } : {} })
}
</script>

<template>
  <div>
    <div class="section-divider">掃描結果</div>

    <p class="lead">
      最新一次掃描中「有回應（存活）」的主機。這是網路上的實況，不是 CIA 登記清單。
      <span v-if="data?.scan_time" class="when">掃描時間 · {{ data.scan_time }}</span>
    </p>

    <div class="tabs">
      <button class="tab" :class="{ on: !filter }" type="button" @click="setFilter('')">全部</button>
      <button class="tab" :class="{ on: filter === 'yes' }" type="button" @click="setFilter('yes')">
        已登記
      </button>
      <button class="tab" :class="{ on: filter === 'no' }" type="button" @click="setFilter('no')">
        未登記
      </button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <p v-else-if="!data || data.items.length === 0" class="muted">
      {{ data?.scan_time ? '這個條件下沒有主機。' : '還沒有執行過掃描。' }}
    </p>

    <table v-else class="tbl">
      <thead>
        <tr>
          <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
          <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
          <SortTh k="segment" :active="sortKey" :dir="sortDir" @sort="toggle">網段</SortTh>
          <SortTh k="os_guess" :active="sortKey" :dir="sortDir" @sort="toggle">研判（掃描指紋）</SortTh>
          <SortTh k="registered" :active="sortKey" :dir="sortDir" @sort="toggle">登記狀態</SortTh>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in sorted" :key="`${row.ip}-${row.hostname}-${i}`">
          <td>
            <NuxtLink v-if="row.registered && row.asset_serial" class="dl" :to="`/assets/${row.asset_serial}`">{{ row.hostname || '(反解不到)' }}</NuxtLink>
            <template v-else>{{ row.hostname || '—' }}</template>
          </td>
          <td class="mono">
            <NuxtLink v-if="row.registered && row.asset_serial" class="dl" :to="`/assets/${row.asset_serial}`">{{ row.ip }}</NuxtLink>
            <template v-else>{{ row.ip || '—' }}</template>
          </td>
          <td class="mono dim">{{ row.segment || '—' }}</td>
          <td>
            <div class="fp">
              <span v-if="row.is_vm" class="tag vm">VM</span>
              <span v-if="row.mac_vendor" class="tag vendor">{{ row.mac_vendor }}</span>
              <span v-if="row.os_guess" class="tag os">{{ row.os_guess }}</span>
            </div>
            <div v-if="row.open_ports" class="fp-sub dim">埠 {{ row.open_ports }}</div>
            <span v-if="!row.is_vm && !row.mac_vendor && !row.os_guess && !row.open_ports" class="dim">—</span>
          </td>
          <td>
            <span v-if="row.registered" class="pill done">已登記</span>
            <span v-else class="pill open">未登記</span>
          </td>
          <td class="right">
            <NuxtLink
              v-if="row.registered && row.asset_serial"
              class="link"
              :to="`/assets/${row.asset_serial}`"
            >
              看資產 →
            </NuxtLink>
            <NuxtLink v-else-if="!row.registered" class="link" to="/adopt">納入管理 →</NuxtLink>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.lead { font-size: 13px; opacity: 0.75; margin: 0 0 14px; line-height: 1.6; }
.lead .when { display: block; margin-top: 3px; font-variant-numeric: tabular-nums; opacity: 0.8; }
.tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.tab {
  background: transparent;
  border: 1px solid rgba(15,23,42,0.16);
  color: inherit;
  border-radius: 999px;
  padding: 5px 14px;
  font-size: 13px;
  cursor: pointer;
}
.tab.on { border-color: #009142; color: var(--brand-dark); background: rgba(0,145,66,0.1); }
.muted { opacity: 0.7; font-size: 14px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.tbl th {
  text-align: left;
  font-weight: 600;
  font-size: 12px;
  opacity: 0.65;
  padding: 8px 10px;
  border-bottom: 1px solid rgba(15,23,42,0.1);
}
.tbl td { padding: 10px; border-bottom: 1px solid rgba(15,23,42,0.05); }
.tbl .right { text-align: right; }
.mono { font-variant-numeric: tabular-nums; }
.dim { opacity: 0.65; }
.pill { font-size: 11.5px; padding: 2px 10px; border-radius: 999px; }
.pill.open { background: rgba(255, 184, 103, 0.16); color: var(--warn-text); }
.pill.done { background: rgba(0,145,66,0.14); color: var(--brand-dark); }
.fp { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.fp .tag { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 3px; white-space: nowrap; }
.fp .tag.vm { background: rgba(255, 184, 103, 0.16); color: var(--warn-text); }
.fp .tag.vendor { background: rgba(0,145,66,0.14); color: var(--brand-dark); }
.fp .tag.os { background: rgba(0,145,66,0.14); color: var(--brand-dark); }
.fp-sub { font-size: 11px; margin-top: 3px; }
.dl { color: inherit; text-decoration: none; border-bottom: 1px dotted rgba(0,145,66,.45); }
.dl:hover { color: var(--brand-dark); border-bottom-color: #009142; }
.link { font-size: 12.5px; color: var(--brand-dark); text-decoration: none; }
.link:hover { text-decoration: underline; }
</style>
