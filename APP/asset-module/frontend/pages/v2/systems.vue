<script setup lang="ts">
// 3.1 業務系統（2.x 第四批）：一個業務系統一列，可用中文名稱、代號、使用單位、保管人、主機資產名稱搜尋（藍圖任務 9）。
// 點系統進「系統履歷」（不在選單）。台數＝系統履歷同一套（/api/v2/systems，v2_systems.py）。
// 「排行與分類匯入」頁籤（V45）：2.048.6 起 V2 原生（components/v2/BizRank.vue，含系統分類對照匯入，API 原樣不動），不再嵌入舊頁。
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const TABS = [{ k: 'list', label: '業務系統清單' }, { k: 'rank', label: '排行與分類匯入' }]
const tab = computed({ get: () => (route.query.tab === 'rank' ? 'rank' : 'list'), set: (v: string) => router.replace({ query: { tab: v === 'list' ? undefined : v } }) })
const data = ref<any>(null)
const err = ref('')
try { data.value = await apiFetch('/api/v2/systems') } catch (e: any) { err.value = `業務系統清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
const q = ref(typeof route.query.q === 'string' ? route.query.q : '')
watch(q, (v) => router.replace({ query: { ...route.query, q: v || undefined } }))
const cls = computed(() => (typeof route.query.cls === 'string' ? route.query.cls : ''))
// #50（2026-11-12）：上下游狀態（有關聯／沒有關聯／查不到資料），數字＝篩出來的列數；查不到資料＝缺口清單
const dep = computed(() => (typeof route.query.dep === 'string' ? route.query.dep : ''))
const DEP_STATES = [{ k: 'has', l: '有關聯' }, { k: 'none', l: '沒有關聯' }, { k: 'partial', l: '部分查不到' }, { k: 'no_data', l: '查不到資料' }]
const GROUPS = ['第一類', '第二類', '第三類', '未分類']
const rows = computed(() => {
  const terms = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  return (data.value?.rows ?? []).filter((r: any) => (!cls.value || r.class_group === cls.value) && (!dep.value || r.deps_state === dep.value) && terms.every((t: string) => r.search.includes(t)))
})
const cnt = (g: string) => (data.value?.rows ?? []).filter((r: any) => r.class_group === g).length
const url = (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}`
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">3.1 業務系統</h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <span v-else-if="data">共 {{ data.total }} 個業務系統</span>
    </template>
    <template #tab>
    <ClientOnly v-if="tab === 'rank'"><V2BizRank /></ClientOnly>
    <template v-else>
    <div v-if="data" class="bar">
      <input v-model="q" class="kw" type="search" placeholder="搜尋：系統中文名稱、代號、使用單位、保管人">
      <NuxtLink class="chip" :class="{ on: !cls }" :to="{ query: { ...route.query, cls: undefined } }">全部 {{ data.total }}</NuxtLink>
      <NuxtLink v-for="g in GROUPS" :key="g" class="chip" :class="{ on: cls === g }" :to="{ query: { ...route.query, cls: g } }">{{ g }} {{ cnt(g) }}</NuxtLink>
      <span class="muted">符合 {{ rows.length }} 個</span>
    </div>
    <div v-if="data" class="bar">
      <span class="muted">上下游</span>
      <NuxtLink class="chip" :class="{ on: !dep }" :to="{ query: { ...route.query, dep: undefined } }">全部 {{ data.total }}</NuxtLink>
      <NuxtLink v-for="d in DEP_STATES" :key="d.k" class="chip" :class="{ on: dep === d.k }" :to="{ query: { ...route.query, dep: d.k } }">{{ d.l }} {{ data.deps_by_state?.[d.k] ?? 0 }}</NuxtLink>
      <InfoNote>有關聯＝有人工登記或連線推論的上下游（筆數＝系統履歷「上下游」頁籤的筆數）。沒有關聯＝沒有上下游，而且每一台主機近期都有連線觀察（全查過、沒看到跨系統連線）。部分查不到＝沒有上下游、但有主機沒有連線觀察（那幾台可能有關聯）。查不到資料＝沒有登記、主機也都沒有連線觀察。後兩者不能當成「沒有」，就是缺口清單；「未觀察（台）」點下去看是哪幾台。</InfoNote>
      <InfoNote>主機（台）＝該系統代號底下的主機，同一台多筆登記算一台、退役不算，與系統履歷同一個數字。登記（筆）＝CIA 與其他來源的登記筆數（含退役）。多個關鍵字以空白分隔，全部符合才列出。</InfoNote>
    </div>
    <V2Table v-if="data" :rows="rows" prefix="S" :max="1000" empty="沒有符合的業務系統"
             :cols="[{ k: 'api_id', label: '代號', mono: true, link: url }, { k: 'name', label: '系統名稱', link: url }, { k: 'class_group', label: '分級' }, { k: 'severity', label: '重要度' }, { k: 'hosts', label: '主機（台）', mono: true, link: (r: any) => `${url(r)}?tab=hosts` }, { k: 'records', label: '登記（筆）', mono: true }, { k: 'deps_total', label: '上下游（筆）', mono: true, link: (r: any) => `${url(r)}?tab=deps` }, { k: 'deps_state_label', label: '上下游狀態' }, { k: 'deps_unobserved_count', label: '未觀察（台）', mono: true, link: (r: any) => (r.deps_unobserved_count ? `${url(r)}?tab=deps` : null) }, { k: 'rooms', label: '所在地' }, { k: 'unit', label: '使用單位' }, { k: 'custodian', label: '保管人' }, { k: 'note', label: '備註' }]" />
    </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.kw { font-family: inherit; font-size: 14px; padding: 6px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: 340px; max-width: 100%; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
