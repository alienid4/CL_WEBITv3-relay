<script setup lang="ts">
// S4 未結案總覽（2.061.3）：9.3「未結案總覽」頁籤。一頁看完 9.3 修正包、9.4 補登、同機多筆、實體機補登、機櫃編號、彙整表比對的未結案。
// 卡片（全部／新案／逾期／例外到期）＝下面清單的列數，同一份計算（verify_numbers 釘住）；條件放網址（ov＝哪一張卡、cv=1＝涵蓋範圍）。
// 匯出＝目前清單（含事件紀錄：誰、哪天、做了什麼），給稽核。
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const route = useRoute()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())
const u = (extra: Record<string, string> = {}) => '/v2/ciafix?' + new URLSearchParams({ tab: 'open', ...extra }).toString()
const view = computed(() => (['new', 'overdue', 'exc_due'].includes(q('ov')) ? q('ov') : 'all'))
const showCov = computed(() => q('cv') === '1')
const sum = ref<any>(null); const items = ref<any[] | null>(null); const cov = ref<any>(null); const err = ref('')
async function load() {
  try {
    // 清單＋卡片同一次計算（後端 items 一起回 summary）；涵蓋範圍頁才另外拿卡片數字
    if (showCov.value) { [sum.value, cov.value] = await Promise.all([apiFetch<any>('/api/cia-open/summary'), apiFetch<any>('/api/cia-open/coverage')]) }
    else { const r = await apiFetch<any>('/api/cia-open/items', { query: { view: view.value } }); items.value = r.items; sum.value = r.summary }
    err.value = ''
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)
watch(() => [view.value, showCov.value], load)
const CARDS = computed(() => [
  { k: 'all', label: '全部未結案', tone: '' }, { k: 'new', label: `新案（${sum.value?.new_days ?? 7} 天內）`, tone: '' },
  { k: 'overdue', label: `逾期（交出超過 ${sum.value?.overdue_days ?? 14} 天）`, tone: 'warn' }, { k: 'exc_due', label: '例外到期（要重審）', tone: 'warn' },
].map(x => ({ ...x, v: sum.value?.counts?.[x.k], to: u(x.k === 'all' ? {} : { ov: x.k }), on: !showCov.value && view.value === x.k })))
const rows = computed(() => (items.value ?? []).map((r: any) => ({ ...r, wait: r.wait_days ?? '—', exc: r.exception_until ?? '—', since: r.waiting_since ?? '—' })))
const cols = [
  { k: 'source', label: '來源' }, { k: 'ref', label: '編號', mono: true, link: (r: any) => r.link }, { k: 'name', label: '名稱' }, { k: 'status', label: '狀態' },
  { k: 'first_seen', label: '第一次出現', mono: true }, { k: 'since', label: '交出／分派', mono: true }, { k: 'wait', label: '等了幾天', mono: true },
  { k: 'exc', label: '例外到期', mono: true }, { k: 'trail', label: '紀錄' },
]
const srcRows = computed(() => Object.entries(sum.value?.by_source ?? {}).map(([k, v], i) => ({ __no: i + 1, source: k, n: v })))
const covRows = computed(() => (cov.value?.sources ?? []).map((r: any) => ({ ...r, date2: r.date ?? '未取得', c: r.covered ?? '—', uc: r.uncovered ?? '—', um: r.unmatched ?? '—', nt: r.note ?? '' })))
const covCols = [{ k: 'source', label: '來源' }, { k: 'date2', label: '最後資料日期', mono: true }, { k: 'c', label: '涵蓋台數', mono: true },
  { k: 'uc', label: '未涵蓋台數', mono: true }, { k: 'um', label: '無法比對', mono: true }, { k: 'nt', label: '備註' }]
const od = ref<number | null>(null); const odBusy = ref(false)
watch(sum, (s) => { if (s && od.value === null) od.value = s.overdue_days }, { immediate: true })
async function saveOd() {
  odBusy.value = true
  try { await guardedFetch<any>('/api/cia-open/settings', { method: 'PUT', body: { overdue_days: od.value } }); showToast('已儲存', 'success'); await load() } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { odBusy.value = false }
}
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="cards">
      <NuxtLink v-for="k in CARDS" :key="k.k" class="kc" :class="[{ on: k.on }, k.tone]" :to="k.to">
        <span class="kl">{{ k.label }}</span><b class="kv mono">{{ n(k.v) }}<small> 筆</small></b>
      </NuxtLink>
    </div>
    <div class="bar">
      <NuxtLink class="chip" :class="{ on: !showCov }" :to="u()">未結案清單</NuxtLink>
      <NuxtLink class="chip" :class="{ on: showCov }" :to="u({ cv: '1' })">掃描涵蓋範圍</NuxtLink>
      <span class="muted">截點 {{ sum?.as_of ?? '—' }}</span>
      <span class="sp" />
      <template v-if="isAdmin">
        <label class="muted">逾期天數 <input v-model.number="od" type="number" min="1" max="365" class="num" aria-label="逾期天數"></label>
        <button type="button" class="mini" :disabled="odBusy || od === sum?.overdue_days" @click="saveOd">儲存</button>
      </template>
      <InfoNote>來源：9.3 修正包（待決定、同意等套用）、9.4 補登（未結案）、同機多筆、實體機補登、機櫃編號（待確認、已確認、已交出）、彙整表比對（表有 CIA 無、欄位不一致）。
        新案＝第一次出現在最近 7 天內；逾期＝交出去（修正包產包、補登分派或退回、案件交出）超過設定天數還沒回，退回時重算；例外到期＝標了例外而到期日已過（系統自動回到待確認，處理之前列在這裡要重審）。
        涵蓋範圍的分母固定＝2.1 全部登記台數，看得出「沒有差異」是真的沒有、還是沒掃到；WhatsUp 沒有逐台清單，只列最後一次輪詢。
        「紀錄」欄是每一筆的事件（日期、動作、誰），匯出即稽核紀錄。</InfoNote>
    </div>
    <template v-if="showCov">
      <p class="muted">分母（2.1 全部登記台數）<b class="mono">{{ n(cov?.total) }}</b> 台</p>
      <V2Table :rows="covRows" :cols="covCols" prefix="C" row-key="source" unit="個來源" />
    </template>
    <template v-else>
      <V2Table v-if="srcRows.length" :rows="srcRows" :cols="[{ k: 'source', label: '來源' }, { k: 'n', label: '未結案', mono: true }]" prefix="S" row-key="source" unit="個來源" :search="false" />
      <V2Table :rows="rows" :cols="cols" prefix="O" row-key="__no" unit="筆" :max="200" empty="沒有未結案" />
    </template>
  </div>
</template>

<style scoped>
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 8px; }
@media (max-width: 900px) { .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.kc { display: flex; flex-direction: column; gap: 2px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kl { font-size: 12px; color: var(--ink-soft); }
.kv { font-size: 24px; color: var(--brand-dark); }
.kv small { font-size: 12px; color: var(--muted); }
.kc.warn .kv { color: var(--warn-text); }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.sp { flex: 1; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.num { width: 64px; font-family: inherit; font-size: 13px; padding: 2px 6px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
</style>
