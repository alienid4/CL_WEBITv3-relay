<script setup lang="ts">
// 10.2 分布統計「OS×環境納管」頁籤（V66，2.059.0）：舊 components/OnboardMatrix.vue 搬成 V2Table。
// 同一支 GET /api/stats/onboard-matrix（⚠D3 不寫新的計算）；格子點下去＝6.1 納管漏斗帶同樣條件（os_type、env_group），
// 跟舊頁一樣（漏斗跟這張表是同一份資料）。格子顯示哪個數字在網址 ?om=needs_action|onboarded|total。
const { apiFetch } = useApi()
const { isAdmin } = useAuth()
const rt = useRuntimeConfig()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const METRICS = [{ k: 'needs_action', label: '還要處理' }, { k: 'onboarded', label: '已納管' }, { k: 'total', label: '總台數' }] as const
type Metric = typeof METRICS[number]['k']
const metric = computed<Metric>(() => (METRICS.some(m => m.k === route.query.om) ? route.query.om as Metric : 'needs_action'))
function setMetric(k: Metric) { router.replace({ query: { ...route.query, om: k } }) }

const m = ref<any>(null)
const err = ref('')
onMounted(async () => {
  try { m.value = await apiFetch('/api/stats/onboard-matrix') } catch (e: any) { err.value = `交叉表載入失敗：${errMsg(e)}` }
})
// 跟舊頁同一個口徑：已納管＝onboarded＋collected（設備是「已收集」）
const main = (c: any) => (!c ? 0 : metric.value === 'needs_action' ? c.needs_action : metric.value === 'onboarded' ? c.onboarded + c.collected : c.total)
const show = (c: any) => (!c || !c.total ? '—' : `${main(c).toLocaleString()}／${c.total.toLocaleString()}${c.coverage === null || c.coverage === undefined ? '' : `・${c.coverage}%`}`)
const drillTo = (os: string | null, env: string | null) => {
  const qs = new URLSearchParams({ tab: 'funnel' })
  if (os) qs.set('os_type', os)
  if (env) qs.set('env_group', env)
  return `/v2/onboard?${qs.toString()}`
}
const cols = computed(() => [
  { k: 'os', label: 'OS 類型' },
  ...(m.value?.cols ?? []).map((c: any, j: number) => ({ k: `c${j}`, label: c.label, mono: true, text: (r: any) => r[`t${j}`], link: (r: any) => (r[`n${j}`] ? drillTo(r.os, c.key) : null) })),
  { k: 'tot', label: '合計', mono: true, text: (r: any) => r.tot_t, link: (r: any) => drillTo(r.os, null) },
])
// 每格的排序值＝主要數字（點標題照數字排），畫面文字＝「主要數字／總台數・納管率」
const rows = computed(() => (m.value?.rows ?? []).map((os: string, i: number) => {
  const o: any = { os, __no: i + 1 }
  ;(m.value.cols ?? []).forEach((c: any, j: number) => {
    const cell = m.value.cells?.[os]?.[c.key] ?? null
    o[`c${j}`] = main(cell); o[`t${j}`] = show(cell); o[`n${j}`] = cell?.total ?? 0
  })
  const rt_ = m.value.row_totals?.[os]
  o.tot = main(rt_); o.tot_t = show(rt_)
  return o
}))
const metricLabel = computed(() => METRICS.find(x => x.k === metric.value)?.label ?? '')
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else-if="m">
      <div class="bar">
        <span class="muted">格子顯示</span>
        <button v-for="x in METRICS" :key="x.k" type="button" class="seg" :class="{ on: metric === x.k }" @click="setMetric(x.k)">{{ x.label }}</button>
        <span class="muted">最近掃描 {{ m.scan_time || '—' }}<template v-if="m.duplicates_merged">・已收起 {{ m.duplicates_merged }} 筆重複登記</template></span>
        <a v-if="isAdmin" class="lnk" :href="`${rt.public.apiBase}/api/stats/onboard-matrix/export`">完整 Excel</a>
        <InfoNote>列是 OS 類型、欄是環境；格子＝「{{ metricLabel }}／該格總台數・納管率」。補佈納管按 OS 分批（Linux／Windows／AIX 腳本不同），能不能動看環境，所以「Linux × 測試 還要處理幾台」可以直接排進行程。
          納管率的分母不含已退役與非納管設備。台數照主機算（同主機名＋IP 的重複登記收成一台），跟 6.1 納管漏斗同一份資料，點格子會帶同樣條件到漏斗看是哪幾台。</InfoNote>
      </div>
      <V2Table :rows="rows" :cols="cols" prefix="O" unit="列" :max="200" empty="無資料" />
      <p class="muted sum">合計（{{ metricLabel }}／總台數）：
        <template v-for="c in m.cols" :key="c.key"><NuxtLink :to="drillTo(null, c.key)">{{ c.label }} {{ show(m.col_totals?.[c.key]) }}</NuxtLink>・</template>
        全部 <NuxtLink :to="drillTo(null, null)">{{ show(m.grand) }}</NuxtLink></p>
    </template>
    <p v-else class="muted">載入中…</p>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.seg { font-family: inherit; font-size: 12px; padding: 3px 10px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; cursor: pointer; }
.seg.on { background: var(--brand); border-color: var(--brand); color: var(--card); }
.lnk { color: var(--link); font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.sum { margin-top: 6px; }
.err { color: var(--bad); }
</style>
