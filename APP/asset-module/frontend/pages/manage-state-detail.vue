<script setup lang="ts">
// 首頁「對帳明細」死連結的下鑽頁（2026-10-18）。
//
// 背景：首頁「已納管」「已收集」兩列以前連到 /assets、/san 的整份清單，完全沒帶篩選——
// 點進去看到的筆數跟畫面上寫的數字對不上。這頁改成打 /api/manage-state?state=<state>，
// 那支後端回的是 manage_state.summarize() 的 rep_items[state]，跟首頁 stateCount()
// 讀的是同一份計算，所以這頁的 total 保證等於首頁寫的數字——不是另外重算一次。
interface Row {
  asset_serial: string | null; hostname: string | null; ip: string | null
  state: string; collect_checked_at: string | null; collect_error: string | null
  os_type: string | null
}
interface Out { state: string; scan_time: string | null; total: number; items: Row[] }

const route = useRoute()
const { apiFetch } = useApi()
const { showToast } = useToast()

const state = computed(() => String(route.query.state || ''))
const data = ref<Out | null>(null)
const loading = ref(true)
const loadError = ref('')

async function load() {
  if (!state.value) { loadError.value = '沒有指定狀態（網址缺少 state 參數）'; loading.value = false; return }
  loading.value = true
  loadError.value = ''
  try {
    data.value = await apiFetch<Out>('/api/manage-state', { params: { state: state.value } })
  } catch (e: any) {
    loadError.value = e?.data?.detail ?? e?.message ?? '載入失敗'
    showToast(`載入失敗：${loadError.value}`, 'error', 12000)
  } finally {
    loading.value = false
  }
}
await load()
watch(state, load)

const rows = computed(() => data.value?.items ?? [])
function keyOf(r: Row) { return `${r.asset_serial ?? ''}|${r.hostname ?? ''}|${r.ip ?? ''}` }
// 項次編號：以後端回來的順序為準、固定不變（天條：使用者重排表格後同一筆仍是同一號）
const rowNo = computed(() => {
  const m = new Map<string, number>()
  rows.value.forEach((r, i) => m.set(keyOf(r), i + 1))
  return m
})
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'hostname')
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>狀態：{{ state || '（未指定）' }}<template v-if="data"> （{{ data.total }} 台）</template></h1>
      <InfoNote>
        這張清單跟首頁「對帳明細」用<b>同一份計算</b>
        （<code>manage_state.summarize()</code> 的代表筆，每台只算一次），
        台數保證跟首頁寫的一致。<br><br>
        點資產序號可到該資產的詳細頁；查不到序號的代表那台還沒登記成資產。
      </InfoNote>
    </div>

    <p v-if="loading" class="dim">載入中…</p>
    <p v-else-if="loadError" class="dim">{{ loadError }}</p>
    <template v-else>
      <p v-if="!rows.length" class="dim">目前沒有符合「{{ state }}」的主機。</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th class="no">#</th>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
              <SortTh k="os_type" :active="sortKey" :dir="sortDir" @sort="toggle">平台</SortTh>
              <SortTh k="collect_checked_at" :active="sortKey" :dir="sortDir" @sort="toggle">最後確認時間</SortTh>
              <th>錯誤訊息</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sorted" :key="keyOf(r)">
              <td class="no mono">{{ rowNo.get(keyOf(r)) }}</td>
              <td>{{ r.hostname || '(無主機名)' }}</td>
              <td class="mono">{{ r.ip || '—' }}</td>
              <td class="mono">
                <NuxtLink v-if="r.asset_serial" :to="`/assets/${r.asset_serial}`" class="dl">{{ r.asset_serial }}</NuxtLink>
                <span v-else class="dim">未登記</span>
              </td>
              <td>{{ r.os_type || '—' }}</td>
              <td class="mono">{{ r.collect_checked_at || '—' }}</td>
              <td class="dim">{{ r.collect_error || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
