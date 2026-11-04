<script setup lang="ts">
import { partialGapNote, partialGapRows, serialsOf, type PartialGaps } from '~/utils/partialGaps'
import { makeLatest } from '~/utils/latestOnly'
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
  gap_serials?: string[]      // 整台口徑的缺口清單：這台每一筆缺口登記（各自連結）
  // 已退役組成（state=已退役 才有）
  statuses?: string; source?: string; row_count?: number; serials?: string[]; also_active?: boolean
}
interface Cell { status: string; source: string; machines: number; rows: number }
interface Out {
  state: string; scan_time: string | null; total: number; items: Row[]
  // 已退役組成（2026-10-20 使用者：「退役有 2,029，這一定是有算錯」）
  rows?: number; matrix?: Cell[]; also_active?: number; multi_row?: number
  retired_alive?: number; merged_shells?: number; pending_cia?: number; retired_values?: string[]
}

const route = useRoute()
const { apiFetch } = useApi()
const { showToast } = useToast()

const state = computed(() => String(route.query.state || ''))
const data = ref<Out | null>(null)
const loading = ref(true)
const loadError = ref('')
const partialNote = ref('')

const latest = makeLatest()
async function load() {
  const seq = latest.begin()
  partialNote.value = ''                          // 每次載入先清：次要提示不可沿用上一個頁面／上一次的結果
  if (!state.value) { loadError.value = '沒有指定狀態（網址缺少 state 參數）'; loading.value = false; return }
  loading.value = true
  loadError.value = ''
  try {
    // 已退役：改拿組成（同一份 summarize 的代表筆，多附狀態值×來源）
    if (state.value === 'partial_gaps') {                       // 整台口徑的次要待辦（逐筆未涵蓋、整台判別的狀態）
      const pg = await apiFetch<PartialGaps>('/api/manage-state', { params: { state: 'partial_gaps' } })
      if (!latest.isCurrent(seq)) return                        // 舊請求晚回來：丟掉
      data.value = { state: '部分登記未涵蓋', scan_time: null, total: pg.n, items: partialGapRows(pg) as any }
      return
    }
    const q = state.value === '已退役' ? '已退役:breakdown' : state.value
    const res = await apiFetch<Out>('/api/manage-state', { params: { state: q } })
    if (!latest.isCurrent(seq)) return
    data.value = res
    if (state.value === '未涵蓋') {                              // 次要提示：整台口徑才有；取不到不影響主清單
      try {
        const note = partialGapNote(await apiFetch<PartialGaps>('/api/manage-state', { params: { state: 'partial_gaps' } }))
        if (latest.isCurrent(seq)) partialNote.value = note
      } catch (e) { console.warn('次要待辦載入失敗（不影響主清單）', e) }
    }
  } catch (e: any) {
    if (!latest.isCurrent(seq)) return
    data.value = null                             // 失敗不沿用上一頁的清單
    loadError.value = e?.data?.detail ?? e?.message ?? '載入失敗'
    showToast(`載入失敗：${loadError.value}`, 'error', 12000)
  } finally {
    if (latest.isCurrent(seq)) loading.value = false
  }
}
await load()
watch(state, load)

function keyOf(r: Row) { return `${r.asset_serial ?? ''}|${r.hostname ?? ''}|${r.ip ?? ''}` }
// 項次編號：照預設排序（主機名稱 A→Z，不分大小寫）編、固定不變——使用者重排表格後同一筆仍是同一號。
// 原本照後端回來的順序編，跟畫面預設的主機名排序不同，看起來像亂跳（使用者 2026-10-18）。
const isRetired = computed(() => state.value === '已退役' && !!data.value?.matrix)
// 組成卡片點了就篩下面的表（再點一次取消）；「另有現役登記」也是一種篩選
const pick = ref<{ status?: string; source?: string; also?: boolean } | null>(null)
function pickCell(c: Cell) {
  pick.value = pick.value && pick.value.status === c.status && pick.value.source === c.source ? null
    : { status: c.status, source: c.source }
}
function pickAlso() { pick.value = pick.value?.also ? null : { also: true } }
function pickMulti() { pick.value = (pick.value as any)?.multi ? null : ({ multi: true } as any) }
// 項次編號先照預設排序編好（固定不變），篩選卡片只是挑出其中幾筆、號碼不重編
const numbered = computed(() => {
  const items = [...(data.value?.items ?? [])]
  items.sort((a, b) => String(a.hostname ?? '').toLowerCase().localeCompare(String(b.hostname ?? '').toLowerCase())
    || String(a.ip ?? '').localeCompare(String(b.ip ?? '')))
  return items.map((r, i) => ({ ...r, _no: i + 1 }))
})
const rows = computed(() => {
  let items = numbered.value
  const p: any = pick.value
  if (p?.also) items = items.filter((r) => r.also_active)
  else if (p?.multi) items = items.filter((r) => (r.row_count ?? 1) > 1)
  else if (p) items = items.filter((r) => r.statuses === p.status && r.source === p.source)
  return items
})
const { sortKey, sortDir, toggle, sorted } = useSort(rows, '_no')
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>狀態：{{ state === 'partial_gaps' ? '部分登記未涵蓋' : (state || '（未指定）') }}<template v-if="data"> （{{ data.total }} 台）</template></h1>
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
      <NuxtLink v-if="partialNote" :to="{ path: '/manage-state-detail', query: { state: 'partial_gaps' } }" class="dim">{{ partialNote }} →</NuxtLink>
      <template v-if="isRetired && data">
        <div class="cards">
          <button v-for="(c, i) in data.matrix" :key="c.status + c.source" class="kcard"
                  :class="{ on: pick && pick.status === c.status && pick.source === c.source }" @click="pickCell(c)">
            <span class="kno">R{{ i + 1 }}</span>
            <b class="mono">{{ c.machines.toLocaleString() }}</b> 台
            <span class="kl">{{ c.status }}｜{{ c.source }}</span>
            <span class="dim">{{ c.rows.toLocaleString() }} 筆登記</span>
          </button>
          <button class="kcard" :class="{ on: pick?.also }" @click="pickAlso">
            <span class="kno">R{{ (data.matrix?.length ?? 0) + 1 }}</span>
            <b class="mono">{{ (data.also_active ?? 0).toLocaleString() }}</b> 台
            <span class="kl">同一台另有現役登記</span>
            <span class="dim">舊登記退役、新登記在用</span>
          </button>
          <button class="kcard" :class="{ on: (pick as any)?.multi }" @click="pickMulti">
            <span class="kno">R{{ (data.matrix?.length ?? 0) + 2 }}</span>
            <b class="mono">{{ (data.multi_row ?? 0).toLocaleString() }}</b> 台
            <span class="kl">一台多筆退役登記</span>
          </button>
          <NuxtLink class="kcard" :to="{ path: '/manage-state-detail', query: { state: '退役但仍在線' } }">
            <span class="kno">R{{ (data.matrix?.length ?? 0) + 3 }}</span>
            <b class="mono">{{ (data.retired_alive ?? 0).toLocaleString() }}</b> 台
            <span class="kl">退役但仍在線（另列）</span>
          </NuxtLink>
          <NuxtLink class="kcard" to="/scan-shells">
            <span class="kno">R{{ (data.matrix?.length ?? 0) + 4 }}</span>
            <b class="mono">{{ (data.merged_shells ?? 0).toLocaleString() }}</b> 筆
            <span class="kl">掃描空殼已合併（不算台數）</span>
          </NuxtLink>
          <NuxtLink class="kcard" :to="{ path: '/assets', query: { open: 'cia' } }">
            <span class="kno">R{{ (data.matrix?.length ?? 0) + 5 }}</span>
            <b class="mono">{{ (data.pending_cia ?? 0).toLocaleString() }}</b> 筆
            <span class="kl">待 CIA 異動（不改台數）</span>
          </NuxtLink>
          <InfoNote>
            已退役＝CIA 清冊資產狀態是 {{ (data.retired_values ?? []).join('／') }} 的機器（逐台，{{ (data.rows ?? 0).toLocaleString() }} 筆登記）。
            資產狀態只有 CIA 匯入可以寫：系統不會自己標退役；人知道下線的記成「待 CIA 異動」，不改台數。
            掃描空殼併回 CIA 正本後是「合併紀錄」，不算退役、不算任何台數。<br>
            「閒置」目前算退役——要不要算，待你決定（改一行設定全站一起變）。
          </InfoNote>
        </div>
      </template>
      <p v-if="!rows.length" class="dim">目前沒有符合「{{ state }}」的主機。</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="_no" :active="sortKey" :dir="sortDir" @sort="toggle" class="no">#</SortTh>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
              <SortTh k="os_type" :active="sortKey" :dir="sortDir" @sort="toggle">平台</SortTh>
              <template v-if="isRetired">
                <SortTh k="statuses" :active="sortKey" :dir="sortDir" @sort="toggle">CIA 資產狀態</SortTh>
                <SortTh k="source" :active="sortKey" :dir="sortDir" @sort="toggle">來源</SortTh>
                <SortTh k="row_count" :active="sortKey" :dir="sortDir" @sort="toggle">登記筆數</SortTh>
                <SortTh k="also_active" :active="sortKey" :dir="sortDir" @sort="toggle">另有現役登記</SortTh>
              </template>
              <SortTh k="collect_checked_at" :active="sortKey" :dir="sortDir" @sort="toggle">最後確認時間</SortTh>
              <SortTh k="collect_error" :active="sortKey" :dir="sortDir" @sort="toggle">錯誤訊息</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sorted" :key="keyOf(r)">
              <td class="no mono">{{ r._no }}</td>
              <td>{{ r.hostname || '(無主機名)' }}</td>
              <td class="mono">{{ r.ip || '—' }}</td>
              <td class="mono">
                <SerialLinks :serials="serialsOf(r)" />
              </td>
              <td>{{ r.os_type || '—' }}</td>
              <template v-if="isRetired">
                <td>{{ r.statuses }}</td>
                <td>{{ r.source }}</td>
                <td class="mono" :title="(r.serials ?? []).join('、')">{{ r.row_count }}</td>
                <td>{{ r.also_active ? '是' : '' }}</td>
              </template>
              <td class="mono">{{ r.collect_checked_at || '—' }}</td>
              <td class="dim">{{ r.collect_error || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 已退役組成卡片（2026-10-20）：點卡片篩下面的表，再點一次取消 */
.cards { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-start; margin: 4px 0 14px; }
.kcard { display: flex; flex-direction: column; gap: 2px; min-width: 150px; padding: 10px 12px; text-align: left;
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer;
  color: var(--ink); text-decoration: none; font: inherit; }
.kcard:hover { border-color: var(--border-strong); }
.kcard.on { border-color: var(--brand); background: var(--mint); }
.kcard b { font-family: var(--disp); font-size: 20px; }
.kno { font-size: 11px; color: var(--ink-aux); }
.kl { font-size: 13px; }
</style>
