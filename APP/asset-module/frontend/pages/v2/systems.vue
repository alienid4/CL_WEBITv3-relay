<script setup lang="ts">
// 3.1 業務系統（2.x 第四批）：一個業務系統一列，可用中文名稱、代號、使用單位、保管人、主機資產名稱搜尋（藍圖任務 9）。
// 點系統進「系統履歷」（不在選單）。台數＝系統履歷同一套（/api/v2/systems，v2_systems.py）。
// 「排行」頁籤（V45）：2.048.6 起 V2 原生（components/v2/BizRank.vue，含系統分類對照匯入，API 原樣不動），不再嵌入舊頁。
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const TABS = [{ k: 'list', label: '業務系統清單' }, { k: 'rank', label: '排行' }]
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
// 2026-11-15 畫面重看：一行結論＋要你處理；表格 6 欄，其餘只在匯出
const st = (k: string) => data.value?.deps_by_state?.[k] ?? 0
const concl = computed(() => [`共 ${data.value?.total ?? 0} 個業務系統`, st('no_data') ? `${st('no_data')} 個查不到上下游` : '', cnt('未分類') ? `${cnt('未分類')} 個未分類` : ''].filter(Boolean).join('・'))
const todo = computed(() => [
  st('no_data') ? { k: 'nd', text: `${st('no_data')} 個查不到上下游`, to: { query: { ...route.query, dep: 'no_data', cls: undefined } } } : null,
  st('partial') ? { k: 'pt', text: `${st('partial')} 個部分主機沒有連線資料`, to: { query: { ...route.query, dep: 'partial', cls: undefined } } } : null,
  cnt('未分類') ? { k: 'uc', text: `${cnt('未分類')} 個未分類`, to: { query: { ...route.query, cls: '未分類', dep: undefined } } } : null,
].filter(Boolean) as { k: string, text: string, to: any }[])
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">3.1 業務系統</h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <span v-else-if="data" data-testid="systems-concl">{{ concl }}</span>
    </template>
    <template #tab>
    <ClientOnly v-if="tab === 'rank'"><V2BizRank /></ClientOnly>
    <template v-else>
    <div v-if="data" class="todo" data-testid="systems-todo"><b>要你處理</b>
      <div v-for="t in todo" :key="t.k" class="it"><span class="sp">{{ t.text }}</span><NuxtLink class="pbtn" :to="t.to">查看</NuxtLink></div>
      <div v-if="!todo.length" class="it muted">沒有要你處理的事</div>
    </div>
    <div v-if="data" class="bar">
      <input v-model="q" class="kw" type="search" placeholder="搜尋：系統名稱、代號、使用單位、保管人">
      <NuxtLink class="chip" :class="{ on: !cls }" :to="{ query: { ...route.query, cls: undefined } }">全部 {{ data.total }}</NuxtLink>
      <NuxtLink v-for="g in GROUPS" :key="g" class="chip" :class="{ on: cls === g }" :to="{ query: { ...route.query, cls: g } }">{{ g }} {{ cnt(g) }}</NuxtLink>
      <span class="muted">符合 {{ rows.length }} 個</span>
    </div>
    <div v-if="data" class="bar">
      <span class="muted">上下游</span>
      <NuxtLink class="chip" :class="{ on: !dep }" :to="{ query: { ...route.query, dep: undefined } }">全部 {{ data.total }}</NuxtLink>
      <NuxtLink v-for="d in DEP_STATES" :key="d.k" class="chip" :class="{ on: dep === d.k }" :to="{ query: { ...route.query, dep: d.k } }">{{ d.l }} {{ data.deps_by_state?.[d.k] ?? 0 }}</NuxtLink>
      <InfoNote>查不到＝主機沒有連線資料，不代表沒有上下游。</InfoNote>
    </div>
    <V2Table v-if="data" :rows="rows" prefix="S" :max="1000" unit="個" empty="沒有符合的業務系統"
             :cols="[{ k: 'name', label: '系統' }, { k: 'class_group', label: '分級' }, { k: 'hosts', label: '主機', mono: true }, { k: 'deps_state_label', label: '上下游' }, { k: 'unit', label: '使用單位' }, { k: 'custodian', label: '保管人' }]"
             :export-cols="[{ k: 'api_id', label: '代號' }, { k: 'severity', label: '重要度' }, { k: 'records', label: '登記筆數' }, { k: 'deps_total', label: '上下游筆數' }, { k: 'deps_unobserved_count', label: '沒有連線資料台數' }, { k: 'rooms', label: '所在地' }, { k: 'note', label: '備註' }]">
      <template #cell-name="{ row }"><NuxtLink :to="url(row)"><b>{{ row.name || row.api_id }}</b></NuxtLink><div class="sm">{{ row.api_id }}<span v-if="row.note" class="tg bad">代號有誤</span></div></template>
      <template #cell-class_group="{ row }">{{ row.class_group }}<div v-if="row.severity === '重大'" class="sm">重大</div></template>
      <template #cell-hosts="{ row }"><NuxtLink :to="`${url(row)}?tab=hosts`"><b>{{ row.hosts }} 台</b></NuxtLink><div v-if="row.rooms" class="sm">{{ row.rooms }}</div></template>
      <template #cell-deps_state_label="{ row }"><NuxtLink :to="`${url(row)}?tab=deps`"><span class="tg" :class="{ bad: row.deps_state === 'no_data', warn: row.deps_state === 'partial' }">{{ row.deps_state_label }}</span></NuxtLink><div v-if="row.deps_total || row.deps_unobserved_count" class="sm"><template v-if="row.deps_total">{{ row.deps_total }} 筆</template><template v-if="row.deps_unobserved_count"> {{ row.deps_unobserved_count }} 台沒資料</template></div></template>
    </V2Table>
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
.todo { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 12px; padding: 8px 12px; margin: 0 0 10px; font-size: 14px; }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); }
.todo .sp { flex: 1; }
.pbtn { font-size: 13px; padding: 3px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); text-decoration: none; }
.sm { font-size: 12px; color: var(--ink-aux); }
.tg { display: inline-block; padding: 0 8px; border-radius: 10px; font-size: 12px; background: var(--sub); color: var(--ink-soft); margin-left: 4px; }
.tg.bad { background: var(--bad-soft); color: var(--bad); } .tg.warn { background: var(--warn-soft); color: var(--warn-text); }
</style>
