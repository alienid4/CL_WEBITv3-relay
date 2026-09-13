<script setup lang="ts">
// 問題清單（可嵌入元件）：儀表板的「異常消失／異常新增／漏登記」落點。
// 2026-09-12 從 pages/issues.vue 抽出來，讓「異常報告」頁能把它當一個分頁共用。
// ⚠️ 篩選改用**本地狀態**（不綁 URL、不 router.replace）——不然嵌在 /anomalies 裡點篩選會跳走。
// 深連結相容：standalone /issues 用 prop 把 ?type / ?show 帶進來當初始值。
interface Issue {
  id: number; hostname: string | null; ip: string | null
  issue_type: string; detected_at: string; is_read: number; handled_at: string | null
}
const props = defineProps<{ initialType?: string; initialShowAll?: boolean }>()

const ISSUE_TYPES = ['異常消失', '異常新增', '漏登記'] as const
const TYPE_HINT: Record<string, string> = {
  異常消失: '之前掃得到、這次掃不到。可能是關機、換 IP、或真的下線了。',
  異常新增: '之前沒看過、這次突然出現在網段上的主機。',
  漏登記: '掃描掃得到，但 CIA 沒有這台的登記資料。',
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const issues = ref<Issue[]>([])
const { sortKey, sortDir, toggle, sorted } = useSort(issues, 'detected_at')
const loading = ref(false)
const errorMessage = ref('')
const marking = ref<number | null>(null)
const activeType = ref(props.initialType && (ISSUE_TYPES as readonly string[]).includes(props.initialType) ? props.initialType : '')
const includeHandled = ref(!!props.initialShowAll)

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const query: Record<string, string> = {}
    if (activeType.value) query.issue_type = activeType.value
    if (!includeHandled.value) query.is_read = 'false'
    issues.value = await apiFetch<Issue[]>('/api/issues', { query })
  } catch {
    errorMessage.value = '問題清單載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch([activeType, includeHandled], load)

function setType(t: string) { activeType.value = t }
function toggleHandled() { includeHandled.value = !includeHandled.value }

async function markRead(issue: Issue) {
  marking.value = issue.id
  try {
    await apiFetch(`/api/issues/${issue.id}`, { method: 'PATCH', body: { is_read: true } })
    showToast(`已標記處理：${issue.hostname || issue.ip || `#${issue.id}`}`, 'success')
    await load()
  } catch (err: any) {
    showToast(err?.data?.detail ?? '標記失敗，請稍後再試', 'error')
  } finally {
    marking.value = null
  }
}
</script>

<template>
  <div>
    <div class="tabs">
      <button class="tab" :class="{ on: !activeType }" type="button" @click="setType('')">全部</button>
      <button v-for="t in ISSUE_TYPES" :key="t" class="tab" :class="{ on: activeType === t }" type="button" @click="setType(t)">{{ t }}</button>
      <button class="tab ghost" type="button" @click="toggleHandled">
        {{ includeHandled ? '只看待處理' : '含已處理' }}
      </button>
    </div>

    <p v-if="activeType" class="type-hint">{{ TYPE_HINT[activeType] }}</p>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <p v-else-if="issues.length === 0" class="muted">
      {{ includeHandled ? '沒有任何問題紀錄。' : '目前沒有待處理的問題 🎉' }}
    </p>

    <table v-else class="tbl">
      <thead>
        <tr>
          <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
          <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
          <SortTh k="issue_type" :active="sortKey" :dir="sortDir" @sort="toggle">類型</SortTh>
          <SortTh k="detected_at" :active="sortKey" :dir="sortDir" @sort="toggle">發現時間</SortTh>
          <SortTh k="is_read" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in sorted" :key="row.id">
          <td>{{ row.hostname || '—' }}</td>
          <td class="mono">
            <NuxtLink v-if="row.ip" class="dl" :to="{ path: '/assets', query: { q: row.ip } }" title="在資產清單中查這個 IP">{{ row.ip }}</NuxtLink>
            <template v-else>—</template>
          </td>
          <td><button class="linklike" type="button" title="只看這一類問題" @click="setType(row.issue_type)">{{ row.issue_type }}</button></td>
          <td class="mono dim">{{ row.detected_at }}</td>
          <td>
            <span v-if="row.is_read" class="pill done">已處理</span>
            <span v-else class="pill open">待處理</span>
          </td>
          <td class="right">
            <button v-if="!row.is_read" class="btn-sm" type="button" :disabled="marking === row.id" @click="markRead(row)">
              {{ marking === row.id ? '處理中…' : '標記已處理' }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.tab { background: transparent; border: 1px solid var(--border-strong); color: inherit;
  border-radius: 999px; padding: 5px 14px; font-size: 13px; cursor: pointer; }
.tab.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.tab.ghost { margin-left: auto; opacity: 0.75; }
.type-hint { font-size: 12.5px; color: var(--ink-soft); margin: 0 0 14px; }
.muted { color: var(--ink-soft); font-size: 14px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.tbl th { text-align: left; font-weight: 600; font-size: 12px; color: var(--ink-soft);
  padding: 8px 10px; border-bottom: 1px solid var(--border); }
.tbl td { padding: 10px; border-bottom: 1px solid var(--line); }
.tbl .right { text-align: right; }
.mono { font-variant-numeric: tabular-nums; }
.dl { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--brand); }
.dl:hover { color: var(--brand-dark); }
.linklike { background: none; border: none; padding: 0; color: var(--brand-dark); cursor: pointer;
  font: inherit; border-bottom: 1px dotted var(--brand); }
.dim { color: var(--ink-aux); }
.pill { font-size: 11.5px; padding: 2px 10px; border-radius: 999px; }
.pill.open { background: var(--warn-soft); color: var(--warn-text); }
.pill.done { background: var(--mint); color: var(--brand-dark); }
.btn-sm { background: transparent; border: 1px solid var(--border-strong); color: inherit;
  border-radius: 8px; padding: 4px 12px; font-size: 12px; cursor: pointer; }
.btn-sm:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.btn-sm:disabled { opacity: 0.5; cursor: default; }
</style>
