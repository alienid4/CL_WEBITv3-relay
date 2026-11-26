<script setup lang="ts">
// 10.2 分布統計「入口（VIP）」頁籤（V66，2.059.0）：舊 components/VipEntries.vue 搬成 V2。
// 同一支 GET /api/stats/vip-entries／…/detail（⚠D3 不寫新的計算）；來源是 CIA 的「BIG IP/VIP」欄，未經 F5 驗證。
// 網址：?tab=vip &vq=搜尋 &vk=vip|cluster（判斷）&vip=某個入口或某個非 IP 值（看後面是哪幾台）
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const n = (v: number | null | undefined) => (v ?? 0).toLocaleString()
const enc = encodeURIComponent

const data = ref<any>(null)
const err = ref('')
onMounted(async () => {
  try { data.value = await apiFetch('/api/stats/vip-entries') } catch (e: any) { err.value = `入口資料載入失敗：${errMsg(e)}` }
})
const url = (extra: Record<string, string>) => {
  const qs = new URLSearchParams({ tab: 'vip' })
  for (const [k, v] of Object.entries({ vq: q('vq'), vk: q('vk'), ...extra })) if (v) qs.set(k, v)
  return `/v2/dist?${qs.toString()}`
}
const detailUrl = (v: string) => url({ vip: v })
const vqInput = ref(q('vq'))
watch(() => q('vq'), v => { vqInput.value = v })
function search() { const v = vqInput.value.trim(); router.replace({ query: { tab: 'vip', ...(v ? { vq: v } : {}), ...(q('vk') ? { vk: q('vk') } : {}) } }) }
function setKind(k: string) { const { vk, vip, ...rest } = route.query as Record<string, any>; router.replace({ query: { ...rest, tab: 'vip', ...(k ? { vk: k } : {}) } }) }

// 項次照後端給的順序固定（篩選後同一個入口還是同一號）
const all = computed(() => (data.value?.entries ?? []).map((e: any, i: number) => ({ ...e, __no: i + 1, raw_text: (e.raw_values ?? []).join('、') })))
const entries = computed(() => {
  const kw = q('vq'); const k = q('vk')
  return all.value.filter((e: any) => (!k || e.kind === k) && (!kw || e.vip.includes(kw) || (e.raw_values ?? []).some((r: string) => r.includes(kw))))
})
const vipCount = computed(() => all.value.filter((e: any) => e.kind === 'vip').length)
const clusterCount = computed(() => all.value.filter((e: any) => e.kind === 'cluster').length)
const entryCols = [
  { k: 'vip', label: '入口（VIP）', mono: true, link: (r: any) => detailUrl(r.vip) }, { k: 'kind_label', label: '判斷' },
  { k: 'hosts', label: '後面幾台', mono: true, link: (r: any) => detailUrl(r.vip) },
  { k: 'systems', label: '影響幾個系統', mono: true, link: (r: any) => detailUrl(r.vip) },
  { k: 'rows', label: '登記（筆）', mono: true, link: (r: any) => detailUrl(r.vip) }, { k: 'raw_text', label: 'CIA 原始寫法' },
]
const others = computed(() => (data.value?.others ?? []).map((o: any, i: number) => ({ ...o, __no: i + 1 })))
const otherCols = [
  { k: 'value', label: '填的值', mono: true }, { k: 'kind_label', label: '判斷' },
  { k: 'hosts', label: '台數', mono: true, link: (r: any) => detailUrl(r.value) }, { k: 'rows', label: '登記（筆）', mono: true, link: (r: any) => detailUrl(r.value) },
]

// 明細：這個入口後面是哪幾台
const detail = ref<any>(null)
const detailErr = ref('')
let seq = 0
watch(() => q('vip'), async (v) => {
  const my = ++seq
  detail.value = null; detailErr.value = ''
  if (!v) return
  try { const r = await apiFetch('/api/stats/vip-entries/detail', { query: { value: v } }); if (my === seq) detail.value = r } catch (e: any) { if (my === seq) detailErr.value = `明細載入失敗：${errMsg(e)}` }
}, { immediate: true })
const dSys = computed(() => (detail.value?.systems ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1, api_text: s.api_id ?? '（沒填 AP ID）' })))
const dSysCols = [
  { k: 'api_text', label: 'AP ID', mono: true, link: (r: any) => (r.api_id ? `/v2/system/${enc(r.api_id)}` : null) }, { k: 'name', label: '系統' },
  { k: 'hosts', label: '台數', mono: true }, { k: 'rows', label: '登記（筆）', mono: true },
]
const dHosts = computed(() => (detail.value?.machines ?? []).map((m: any, i: number) => ({ ...m, __no: i + 1, name: m.hostname || '（無主機名）' })))
const dHostCols = [
  { k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${enc(r.asset_serial)}` }, { k: 'ip', label: 'IP', mono: true },
  { k: 'environment', label: '環境' }, { k: 'location', label: '機房' }, { k: 'systems_text', label: '掛的系統' },
  { k: 'purposes_text', label: '用途' }, { k: 'row_count', label: '登記（筆）', mono: true },
]
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else-if="data && q('vip')">
      <div class="bar"><NuxtLink :to="url({})">‹ 返回入口清單</NuxtLink><b class="mono">{{ q('vip') }}</b>
        <span v-if="detail" class="muted">後面 {{ n(detail.hosts) }} 台（{{ n(detail.rows) }} 筆登記）</span>
        <InfoNote v-if="detail">{{ detail.basis }}</InfoNote></div>
      <p v-if="detailErr" class="err">{{ detailErr }}</p>
      <template v-else-if="detail">
        <h3 class="h3">影響的系統</h3>
        <V2Table :rows="dSys" :cols="dSysCols" prefix="Y" unit="個系統" empty="沒有系統" />
        <h3 class="h3">主機</h3>
        <V2Table :rows="dHosts" :cols="dHostCols" prefix="H" unit="台" empty="沒有主機" />
      </template>
      <p v-else class="muted">載入中…</p>
    </template>
    <template v-else-if="data">
      <p class="muted">共 {{ n(all.length) }} 個入口（VIP {{ n(vipCount) }}・疑似叢集 IP {{ n(clusterCount) }}）・VIP 欄空白 {{ n(data.empty_rows) }} 筆／全部 {{ n(data.total_rows) }} 筆
        <InfoNote>VIP 是服務對外的入口（F5 上的虛擬 IP），不是一台機器；一個 VIP 後面可以接好幾台，一台也可以站在好幾個 VIP 後面。
          來源是 CIA 登記的「BIG IP/VIP」欄，未經 F5 設定驗證，而且 VIP 會浮動——只代表登記當時怎麼填。「無」「N/A」視同空白；IP 後面帶 cluster／msdtc 的標「疑似叢集 IP」（多半是 Windows 叢集，是推論）。{{ data.basis }}</InfoNote></p>
      <form class="bar" @submit.prevent="search">
        <input v-model="vqInput" class="inp" placeholder="搜尋 VIP 或 CIA 原始寫法" aria-label="搜尋 VIP" />
        <button type="submit" class="btn">搜尋</button>
        <button type="button" class="seg" :class="{ on: !q('vk') }" @click="setKind('')">全部</button>
        <button type="button" class="seg" :class="{ on: q('vk') === 'vip' }" @click="setKind('vip')">VIP {{ n(vipCount) }}</button>
        <button type="button" class="seg" :class="{ on: q('vk') === 'cluster' }" @click="setKind('cluster')">疑似叢集 IP {{ n(clusterCount) }}</button>
      </form>
      <V2Table :rows="entries" :cols="entryCols" prefix="V" unit="個入口" empty="沒有符合的入口" />
      <template v-if="others.length">
        <h3 class="h3">VIP 欄填的不是 IP（{{ others.length }} 種）<InfoNote>這些值放在 VIP 欄但不是 IP（常見是填成 AP ID 或主機名），沒有算進入口清單，也沒有丟掉；要修是去改 CIA 登記。</InfoNote></h3>
        <V2Table :rows="others" :cols="otherCols" prefix="X" unit="種" empty="沒有" />
      </template>
    </template>
    <p v-else class="muted">載入中…</p>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.h3 { font-size: 15px; margin: 16px 0 6px; display: flex; align-items: center; gap: 6px; }
.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: var(--disp); }
.err { color: var(--bad); }
.inp { padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px; min-width: 240px; font-family: inherit; background: var(--card); color: var(--ink); }
.btn { padding: 6px 14px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; }
.btn:hover { background: var(--brand-hover); }
.seg { font-family: inherit; font-size: 12px; padding: 3px 10px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; cursor: pointer; }
.seg.on { background: var(--brand); border-color: var(--brand); color: var(--card); }
</style>
