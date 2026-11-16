<script setup lang="ts">
// 8.3 生命週期：作業系統｜硬體｜軟體（V2EosLife，同一批 API）｜追蹤（含通知對象）｜證據與覆蓋｜依業務系統。
// 頁首結論的數字讀既有 API：台數＝/api/eos/summary 六桶 by_status 加總（跟作業系統頁籤上方四格同一份計算）；
// 回覆＝/api/eos/follow/overview 的 tiles（跟追蹤頁籤的磚同一份）。舊網址 ?tab=notify 照樣能開（落在追蹤頁籤的「通知對象」）。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const TABS = [{ k: 'os', label: '作業系統' }, { k: 'hw', label: '硬體' }, { k: 'sw', label: '軟體' }, { k: 'follow', label: '追蹤' }, { k: 'evidence', label: '證據與覆蓋' }, { k: 'system', label: '依業務系統' }]
const tab = computed({
  get: () => (route.query.tab === 'notify' ? 'follow' : TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'os'),
  set: (v: string) => router.replace({ query: { tab: v } }),
})

const BUCKETS = ['host_os', 'firmware', 'software', 'insufficient', 'other', 'hardware']
const sum = ref<any>(null)
const ov = ref<any>(null)
const err = ref('')
const why = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
onMounted(async () => {
  const fails: string[] = []
  await Promise.all([
    apiFetch<any>('/api/eos/summary').then((r) => { sum.value = r }).catch((e) => { fails.push(`EOS 清單：${why(e)}`) }),
    apiFetch<any>('/api/eos/follow/overview', { query: { demo: 'false' } }).then((r) => { ov.value = r }).catch((e) => { fails.push(`追蹤：${why(e)}`) }),
  ])
  err.value = fails.length ? `部分數字讀不到（${fails.join('；')}）` : ''
})
const st = (s: string) => (sum.value ? BUCKETS.reduce((n, k) => n + (sum.value[k]?.by_status?.[s] ?? 0), 0) : null)
const tile = (k: string) => (ov.value?.tiles ?? []).find((t: any) => t.key === k)?.count ?? null
const n = (v: number | null) => (v === null ? '—' : String(v))
const concl = computed(() => `已過 EOS ${n(st('expired'))} 台・一年內到期 ${n(st('upcoming'))} 台・${n(tile('unreplied'))} 筆沒人回覆`)
const todo = computed(() => {
  const out: { k: string; text: string; to: any }[] = []
  const ex = st('expired'), unrep = tile('unreplied'), unas = tile('unassigned'), fail = tile('delivery_failed'), unno = tile('unnotified')
  if (ex) out.push({ k: 'expired', text: `${ex} 台已過 EOS`, to: { query: { tab: 'os', est: 'expired' } } })
  if (unrep) out.push({ k: 'unreplied', text: `${unrep} 筆已通知、還沒回覆`, to: { query: { tab: 'follow', ef: 'unreplied' } } })
  if (unas) out.push({ k: 'unassigned', text: `${unas} 筆沒有負責人`, to: { query: { tab: 'follow', ef: 'unassigned' } } })
  if (fail) out.push({ k: 'failed', text: `${fail} 筆通知寄送失敗`, to: { query: { tab: 'follow', ef: 'delivery_failed' } } })
  if (unno) out.push({ k: 'unnotified', text: `${unno} 筆還沒通知`, to: { query: { tab: 'follow', ef: 'unnotified' } } })
  return out
})
const loaded = computed(() => !!sum.value || !!ov.value)
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">8.3 生命週期EOS</h1></template>
    <template #conclusion>
      <span v-if="loaded" data-testid="eos-concl">{{ concl }}</span>
      <span v-else-if="!err" class="muted">載入中…</span>
      <InfoNote>日期一律來自官方公告，查不到的標「未公佈」。台數為逐台（同一台多筆登記只算一次），分頁內的數字為版本或型號的種類數。</InfoNote>
    </template>
    <template #tab>
      <p v-if="err" class="err">{{ err }}</p>
      <div v-if="loaded" class="todo" data-testid="eos-todo"><b>要你處理<template v-if="todo.length">（{{ todo.length }} 件事）</template></b>
        <div v-for="t in todo" :key="t.k" class="it"><span class="sp">{{ t.text }}</span><NuxtLink class="pbtn" :to="t.to">查看</NuxtLink></div>
        <div v-if="!todo.length" class="it muted">沒有要你處理的事</div></div>
      <V2EosFollow v-if="tab === 'follow'" />
      <V2EosEvidence v-else-if="tab === 'evidence'" />
      <V2EosBySystem v-else-if="tab === 'system'" />
      <ClientOnly v-else><V2EosLife :key="tab" :view="(tab as any)" /></ClientOnly>
      <details v-if="tab === 'sw'" class="more" data-testid="eos-source"><summary>資料來源</summary>
        <ClientOnly><V2Coverage kind="eos_sw" /></ClientOnly></details>
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
