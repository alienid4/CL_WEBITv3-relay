<script setup lang="ts">
// 10.1 月報（2.x 第四批）：月報匯出｜系統組報告。沿用既有畫面（同一份程式與 API），收掉舊頁頁首。
// 2.007.19：加「效能圖表」（NMON 逐分鐘、日／小時／原始切換、各指標、可複製連結）。
// 2.007.13：加「主機組月報」（V2HostMonthly，網頁即月報，每頁 16:9 可直接截圖）。
definePageMeta({ layout: 'v2' })
// 系統組報告（V49）：2.048.16 起 V2 原生（V2SystemGroup），不再嵌入舊頁
// 主機系統總覽（V50）：2.048.6 起 V2 原生（V2SystemOverview），不再嵌入舊頁
// 2.047.0（V47、V48）：報表列印、月報匯出改 V2 原生（V2HostSources、V2MonthlyExport），不再嵌入舊頁
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const TABS = [{ k: 'monthly', label: '月報匯出' }, { k: 'host', label: '主機組月報' }, { k: 'perf', label: '效能圖表' }, { k: 'group', label: '系統組報告' }, { k: 'overview', label: '主機系統總覽' }, { k: 'sources', label: '報表列印' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'monthly'), set: (v: string) => router.replace({ query: { tab: v } }) })

// 頁首結論：本月月報是否已凍結（同一支 /api/monthly-report/preview，只讀狀態，不改凍結規則）
const _now = new Date()
const thisYm = `${_now.getFullYear()}-${String(_now.getMonth() + 1).padStart(2, '0')}`
const head = ref<any>(null)
const headErr = ref('')
async function loadHead() {
  try { head.value = await apiFetch<any>('/api/monthly-report/preview', { query: { ym: thisYm } }); headErr.value = '' } catch (e: any) { headErr.value = `本月狀態載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(loadHead)
const failed = computed(() => (head.value?.sections ?? []).filter((s: any) => s.error).length)
const todo = computed(() => {
  const out: { k: string; text: string; to: any }[] = []
  if (!head.value) return out
  if (!head.value.snapshot) out.push({ k: 'freeze', text: `${thisYm} 月報還沒凍結`, to: { query: { tab: 'monthly', ym: thisYm } } })
  if (failed.value) out.push({ k: 'bad', text: `${failed.value} 個段落取得失敗`, to: { query: { tab: 'monthly', ym: thisYm, ms: 'bad' } } })
  return out
})
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">10.1 月報</h1></template>
    <template #conclusion>
      <p v-if="headErr" class="err">{{ headErr }}</p>
      <template v-else-if="head">
        <span data-report-concl>{{ thisYm }} {{ head.snapshot ? '已凍結' : '尚未凍結' }}<template v-if="head.snapshot">・{{ head.snapshot.generated_at }}</template><template v-if="failed">・取得失敗 {{ failed }} 段</template></span>
        <div class="todo" data-report-todo><b>要你處理</b>
          <div v-for="t in todo" :key="t.k" class="it"><span class="sp">{{ t.text }}</span><NuxtLink class="lnk" :to="t.to">查看</NuxtLink></div>
          <div v-if="!todo.length" class="it muted">沒有要你處理的事</div></div>
      </template>
    </template>
    <template #tab>
      <ClientOnly v-if="tab === 'host'"><V2HostMonthly /></ClientOnly>
      <ClientOnly v-else-if="tab === 'perf'">
        <V2NmonExplorer />
        <details class="more"><summary>資料覆蓋</summary><V2Coverage kind="perf" /></details>
      </ClientOnly>
      <ClientOnly v-else-if="tab === 'monthly'"><V2MonthlyExport /></ClientOnly>
      <ClientOnly v-else-if="tab === 'sources'"><V2HostSources /></ClientOnly>
      <ClientOnly v-else-if="tab === 'overview'"><V2SystemOverview /></ClientOnly>
      <ClientOnly v-else><V2SystemGroup /></ClientOnly>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.todo { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 12px; padding: 8px 12px; margin: 8px 0 0; font-size: 14px; }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); }
.todo .sp { flex: 1; }
.lnk { color: var(--link); }
.more { margin-top: 14px; font-size: 13px; }
.more summary { cursor: pointer; color: var(--ink-aux); }
</style>
