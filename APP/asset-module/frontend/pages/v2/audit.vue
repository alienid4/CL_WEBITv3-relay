<script setup lang="ts">
// 8.2 組態與上線檢核：組態檢核｜上線檢查｜基線偏移（同一份程式與 API：/api/config-audit、/api/golive、/api/drift）。
// 頁首一行結論＋要你處理：數字直接讀三支既有 API，跟各頁籤顯示的是同一份資料（不另算）。
// 資料覆蓋率（V2Coverage）屬診斷，收在頁籤底部「資料來源」，預設收合。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const TABS = [{ k: 'config', label: '組態檢核' }, { k: 'golive', label: '上線檢查' }, { k: 'drift', label: '基線偏移' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'config'), set: (v: string) => router.replace({ query: { tab: v } }) })

const nBad = ref<number | null>(null)      // 有不合規條目的主機
const nUntrust = ref<number | null>(null)  // 結果不完整的主機
const nDrift = ref<number | null>(null)    // 待處理的偏移
const nGolive = ref<number | null>(null)   // 上線檢查進行中
const err = ref('')
const why = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
onMounted(async () => {
  const fails: string[] = []
  await Promise.all([
    apiFetch<any>('/api/config-audit').then((r) => {
      nBad.value = (r.items ?? []).filter((x: any) => (x.non_compliant ?? 0) > 0).length
      nUntrust.value = r.summary?.untrustworthy ?? 0
    }).catch((e) => { fails.push(`組態檢核：${why(e)}`) }),
    apiFetch<any>('/api/drift', { query: { status: 'open' } }).then((r) => { nDrift.value = (r.drifts ?? []).length }).catch((e) => { fails.push(`基線偏移：${why(e)}`) }),
    apiFetch<any>('/api/golive', { query: { status: 'open' } }).then((r) => { nGolive.value = (r.checks ?? []).length }).catch((e) => { fails.push(`上線檢查：${why(e)}`) }),
  ])
  err.value = fails.length ? `部分數字讀不到（${fails.join('；')}）` : ''
})
const n = (v: number | null) => (v === null ? '—' : String(v))
const concl = computed(() => `${n(nBad.value)} 台不合規・${n(nDrift.value)} 項偏移待處理・${n(nGolive.value)} 台上線未完成`)
const todo = computed(() => {
  const out: { k: string; text: string; to: any }[] = []
  if (nBad.value) out.push({ k: 'bad', text: `${nBad.value} 台有不合規項目`, to: { query: { tab: 'config', ct: 'hosts' } } })
  if (nUntrust.value) out.push({ k: 'untrust', text: `${nUntrust.value} 台檢核結果不完整`, to: { query: { tab: 'config', ct: 'overview', cd: 'untrust' } } })
  if (nDrift.value) out.push({ k: 'drift', text: `${nDrift.value} 項設定跟上線時不同`, to: { query: { tab: 'drift', ds: 'open' } } })
  if (nGolive.value) out.push({ k: 'golive', text: `${nGolive.value} 台上線前檢查還沒完成`, to: { query: { tab: 'golive' } } })
  return out
})
const loaded = computed(() => nBad.value !== null || nDrift.value !== null || nGolive.value !== null)
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">8.2 組態與上線檢核</h1></template>
    <template #conclusion>
      <span v-if="loaded" data-testid="audit-concl">{{ concl }}</span>
      <span v-else-if="!err" class="muted">載入中…</span>
      <InfoNote>上線檢查通過的那一刻，自動檢查項目的結果即成為基線；之後每天回檢，與基線不同即列入基線偏移。新設備的上線進度另見 6.1 納管作業「新設備上線」。</InfoNote>
    </template>
    <template #tab>
      <p v-if="err" class="err">{{ err }}</p>
      <div v-if="loaded" class="todo" data-testid="audit-todo"><b>要你處理<template v-if="todo.length">（{{ todo.length }} 件事）</template></b>
        <div v-for="t in todo" :key="t.k" class="it"><span class="sp">{{ t.text }}</span><NuxtLink class="pbtn" :to="t.to">查看</NuxtLink></div>
        <div v-if="!todo.length" class="it muted">沒有要你處理的事</div></div>
      <V2GoliveList v-if="tab === 'golive'" />
      <ClientOnly v-else-if="tab === 'drift'"><V2Drift /></ClientOnly>
      <ClientOnly v-else><V2ConfigAudit /></ClientOnly>
      <details v-if="tab === 'config' || tab === 'drift'" class="more" data-testid="audit-source"><summary>資料來源</summary>
        <ClientOnly><V2Coverage :key="tab" :kind="tab" /></ClientOnly></details>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
.todo { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 12px; padding: 8px 12px; margin: 0 0 10px; font-size: 14px; }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); }
.todo .sp { flex: 1; }
.pbtn { font-size: 13px; padding: 3px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); text-decoration: none; }
.more { margin-top: 14px; font-size: 13px; color: var(--ink-aux); } .more summary { cursor: pointer; color: var(--link); margin-bottom: 6px; }
</style>
