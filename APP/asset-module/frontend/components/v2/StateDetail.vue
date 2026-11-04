<script setup lang="ts">
import { partialGapNote, partialGapRows, serialsOf, type PartialGaps } from '~/utils/partialGaps'
// 9.2 數量對帳「納管狀態明細」頁籤（V37，2.048.4 起 V2 原生；舊版 /manage-state-detail 轉到這裡）。
// 清單＝/api/manage-state?state=<狀態>，後端 manage_state.summarize() 的代表筆（每台只算一次），跟首頁、帳實比對的數字同一份；這裡不另算。
// 條件在網址：?tab=state&state=<狀態>；已退役組成卡片 ?rp=<狀態|來源>／also／multi（點了篩下面的表，項次不重編）。
const { apiFetch } = useApi()
const route = useRoute()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())

const state = computed(() => q('state'))
const data = ref<any>(null)
const err = ref('')
const loading = ref(true)
const partialNote = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  partialNote.value = ''                       // 次要提示不可沿用上一個狀態的結果
  if (!state.value) { data.value = null; err.value = '沒有指定狀態'; loading.value = false; return }
  loading.value = true
  err.value = ''                               // 從「沒有指定狀態」自動轉到預設狀態時不閃錯誤
  try {
    if (state.value === 'partial_gaps') {      // 整台口徑的次要待辦（逐筆未涵蓋、整台判別的狀態）
      const pg = await apiFetch<PartialGaps>('/api/manage-state', { query: { state: 'partial_gaps' } })
      if (!isLatest()) return
      data.value = { state: '部分登記未涵蓋', total: pg.n, items: partialGapRows(pg) }
    } else {
      const res = await apiFetch<any>('/api/manage-state', { query: { state: state.value === '已退役' ? '已退役:breakdown' : state.value } })
      if (!isLatest()) return
      data.value = res
      if (state.value === '未涵蓋') {
        try {
          const note = partialGapNote(await apiFetch<PartialGaps>('/api/manage-state', { query: { state: 'partial_gaps' } }))
          if (isLatest()) partialNote.value = note
        } catch (e) { console.warn('次要待辦載入失敗（不影響主清單）', e) }
      }
    }
    err.value = ''
  } catch (e: any) {
    if (!isLatest()) return
    data.value = null; err.value = `載入失敗：${errMsg(e)}`   // 失敗不沿用上一個狀態的清單
  } finally { if (isLatest()) loading.value = false }
}
onMounted(load)
watch(state, load)

const u = (extra: Record<string, string | undefined> = {}) =>
  ({ path: '/v2/reconcile', query: Object.fromEntries(Object.entries({ tab: 'state', state: state.value, ...extra }).filter(([, v]) => v !== undefined && v !== '')) })
// 後端 manage_state 的狀態鍵名照舊，畫面用正式用語「退役但仍偵測到」（同 9.2 帳實比對）
const RETIRED_ALIVE = '退役但仍' + '\u5728\u7dda'
const shownState = computed(() => (state.value === 'partial_gaps' ? '部分登記未涵蓋' : state.value === RETIRED_ALIVE ? '退役但仍偵測到' : (state.value || '（未指定）')))
const isRetired = computed(() => state.value === '已退役' && !!data.value?.matrix)
// 項次照預設排序（主機名稱 A→Z，不分大小寫，再比 IP）編、固定不變；組成卡片只挑出其中幾筆，號碼不重編
const numbered = computed(() => {
  const items = [...(data.value?.items ?? [])]
  items.sort((a: any, b: any) => String(a.hostname ?? '').toLowerCase().localeCompare(String(b.hostname ?? '').toLowerCase())
    || String(a.ip ?? '').localeCompare(String(b.ip ?? '')))
  return items.map((r: any, i: number) => ({ ...r, __no: i + 1, rk: `${r.asset_serial ?? ''}|${r.hostname ?? ''}|${r.ip ?? ''}`,
    name: r.hostname || '(無主機名)', serials: serialsOf(r), serial_t: serialsOf(r).join('、') || '未登記', also_t: r.also_active ? '是' : '' }))
})
const rp = computed(() => q('rp'))
const rows = computed(() => {
  const p = rp.value
  if (!isRetired.value || !p) return numbered.value
  if (p === 'also') return numbered.value.filter((r: any) => r.also_active)
  if (p === 'multi') return numbered.value.filter((r: any) => (r.row_count ?? 1) > 1)
  const cut = p.lastIndexOf('|')             // 來源值不含 |；狀態值就算含 | 也切得對
  const st = p.slice(0, cut), src = p.slice(cut + 1)
  return numbered.value.filter((r: any) => r.statuses === st && r.source === src)
})
const cells = computed(() => (data.value?.matrix ?? []).map((c: any) => ({ ...c, key: `${c.status}|${c.source}` })))
const toggle = (k: string) => u({ rp: rp.value === k ? undefined : k })
const cols = computed(() => [
  { k: 'name', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true }, { k: 'serial_t', label: '資產序號', mono: true }, { k: 'os_type', label: '平台' },
  ...(isRetired.value ? [{ k: 'statuses', label: 'CIA 資產狀態' }, { k: 'source', label: '來源' }, { k: 'row_count', label: '登記筆數', mono: true }, { k: 'also_t', label: '另有現役登記' }] : []),
  { k: 'collect_checked_at', label: '最後確認時間', mono: true }, { k: 'collect_error', label: '錯誤訊息' },
])
</script>

<template>
  <div>
    <div class="bar">
      <b>狀態：{{ shownState }}</b>
      <span v-if="data" class="muted">{{ n(data.total) }} 台</span>
      <InfoNote>這張清單跟首頁「對帳明細」、本頁「帳實比對」用同一份計算（每台只算一次），台數保證一致。點資產序號到主機履歷；沒有序號＝那台還沒登記成資產。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading && !data" class="muted">載入中…</p>
    <template v-else-if="data">
      <p v-if="partialNote"><NuxtLink :to="{ path: '/v2/reconcile', query: { tab: 'state', state: 'partial_gaps' } }" class="lnk">{{ partialNote }} →</NuxtLink></p>
      <div v-if="isRetired" class="cards">
        <NuxtLink v-for="(c, i) in cells" :key="c.key" class="kc" :class="{ on: rp === c.key }" :to="toggle(c.key)">
          <span class="kno">R{{ i + 1 }}</span><b class="mono">{{ n(c.machines) }}</b> 台<span class="kl">{{ c.status }}｜{{ c.source }}</span><span class="muted">{{ n(c.rows) }} 筆登記</span>
        </NuxtLink>
        <NuxtLink class="kc" :class="{ on: rp === 'also' }" :to="toggle('also')">
          <span class="kno">R{{ cells.length + 1 }}</span><b class="mono">{{ n(data.also_active ?? 0) }}</b> 台<span class="kl">同一台另有現役登記</span><span class="muted">舊登記退役、新登記在用</span>
        </NuxtLink>
        <NuxtLink class="kc" :class="{ on: rp === 'multi' }" :to="toggle('multi')">
          <span class="kno">R{{ cells.length + 2 }}</span><b class="mono">{{ n(data.multi_row ?? 0) }}</b> 台<span class="kl">一台多筆退役登記</span>
        </NuxtLink>
        <NuxtLink class="kc" :to="{ path: '/v2/reconcile', query: { tab: 'state', state: RETIRED_ALIVE } }">
          <span class="kno">R{{ cells.length + 3 }}</span><b class="mono">{{ n(data.retired_alive ?? 0) }}</b> 台<span class="kl">退役但仍偵測到（另列）</span>
        </NuxtLink>
        <NuxtLink class="kc" to="/v2/datafix?tab=shells">
          <span class="kno">R{{ cells.length + 4 }}</span><b class="mono">{{ n(data.merged_shells ?? 0) }}</b> 筆<span class="kl">掃描空殼已合併（不算台數）</span>
        </NuxtLink>
        <NuxtLink class="kc" to="/v2/datafix?tab=cia">
          <span class="kno">R{{ cells.length + 5 }}</span><b class="mono">{{ n(data.pending_cia ?? 0) }}</b> 筆<span class="kl">待 CIA 異動（不改台數）</span>
        </NuxtLink>
        <InfoNote>
          已退役＝CIA 清冊資產狀態是 {{ (data.retired_values ?? []).join('／') }} 的機器（逐台，{{ n(data.rows ?? 0) }} 筆登記）。
          資產狀態只有 CIA 匯入可以寫：系統不會自己標退役；人知道下線的記成「待 CIA 異動」，不改台數。
          掃描空殼併回 CIA 正本後是「合併紀錄」，不算退役、不算任何台數。「閒置」目前算退役（改一行設定全站一起變）。
        </InfoNote>
      </div>
      <V2Table :rows="rows" prefix="S" row-key="rk" unit="台" :max="200" :empty="`目前沒有符合「${shownState}」的主機`" :cols="cols">
        <template #cell-serial_t="{ row }"><SerialLinks :serials="row.serials" /></template>
      </V2Table>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.lnk { color: var(--link); font-size: 13px; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-start; margin: 4px 0 12px; }
.kc { display: flex; flex-direction: column; gap: 2px; min-width: 150px; padding: 10px 12px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kc.on { background: var(--mint); }
.kc b { font-size: 20px; color: var(--brand-dark); }
.kno { font-size: 11px; color: var(--ink-aux); }
.kl { font-size: 13px; }
</style>
