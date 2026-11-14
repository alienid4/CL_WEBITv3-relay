<script setup lang="ts">
// ⚙ 操作紀錄（V16，2.047.0 V2 原生；取代舊 pages/activity.vue）。同一批 API：/api/activity/summary、/api/activity。
// 上半「最近誰在用」（帳號卡＝篩選＝下鑽）、下半逐筆（V2Table 可排序、項次、匯出全部符合條件的筆數）。
// 伺服器一次給 200 筆（跟舊頁一樣分頁）；表格在這一頁裡排序。網址：?st=activity&au=帳號&aa=動作&ad=天數（可分享）。
// 2.047.1（GPT 審查 P1-3）：清單帶跟卡片一樣的 days；點帳號卡清掉動作篩選——卡片上的次數＝點下去的筆數。
interface Row { id: number; at: string; username: string | null; ip: string | null; method: string; path: string; status: number | null; duration_ms: number | null; action: string }
interface ListResp { total: number; limit: number; offset: number; rows: Row[]; retain_days: number }
interface Summary { days: number; by_user: { username: string; n: number; last_at: string }[]; login_failed: number; total: number }
const PAGE = 200
const ACTIONS = [{ v: '', label: '全部' }, { v: 'change', label: '資料異動' }, { v: 'login', label: '登入' }, { v: 'logout', label: '登出' }, { v: 'login_failed', label: '登入失敗' }]
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const days = computed(() => ([1, 7, 30, 90].includes(Number(q('ad'))) ? Number(q('ad')) : 7))
const fUser = computed(() => q('au'))
const fAction = computed(() => (ACTIONS.some(a => a.v === q('aa')) ? q('aa') : ''))
const offset = ref(0)
const set = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
const summary = ref<Summary | null>(null)
const data = ref<ListResp | null>(null)
const loading = ref(false)
const err = ref('')
function listUrl(off: number, limit = PAGE) {
  const p = new URLSearchParams({ limit: String(limit), offset: String(off), days: String(days.value) })
  if (fUser.value) p.set('username', fUser.value)
  if (fAction.value) p.set('action', fAction.value)
  return `/api/activity?${p}`
}
const begin = useLatest()      // 2.047.2（GPT 複核）：快速切期間／帳號／動作時只套用最新那一次
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const [s, d] = await Promise.all([apiFetch<Summary>(`/api/activity/summary?days=${days.value}`), apiFetch<ListResp>(listUrl(offset.value))])
    if (!isLatest()) return
    summary.value = s
    data.value = d
    err.value = ''
  } catch (e: any) { if (isLatest()) err.value = `操作紀錄載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { if (isLatest()) loading.value = false }
}
watch([days, fUser, fAction], () => { offset.value = 0; load() }, { immediate: true })
function page(delta: number) {
  const next = offset.value + delta * PAGE
  if (next < 0 || next >= (data.value?.total ?? 0)) return
  offset.value = next
  load()
}
const actionLabel = (a: string) => ACTIONS.find(x => x.v === a)?.label ?? a
const shape = (r: Row, i: number) => ({ ...r, __no: offset.value + i + 1, who: r.username || '（未登入）', act: actionLabel(r.action),
  ms: r.duration_ms !== null ? `${r.duration_ms} ms` : '' })
const rows = computed(() => (data.value?.rows ?? []).map(shape))
// 匯出＝目前篩選條件的全部筆數（不只這一頁）
async function exportAll() {
  const out: Row[] = []
  const total = data.value?.total ?? 0
  for (let off = 0; off < total; off += 1000) out.push(...(await apiFetch<ListResp>(listUrl(off, 1000))).rows)
  return out.map((r, i) => ({ ...r, __no: i + 1, who: r.username || '（未登入）', act: actionLabel(r.action), ms: r.duration_ms !== null ? `${r.duration_ms} ms` : '' }))
}
const COLS = [
  { k: 'at', label: '時間', mono: true }, { k: 'who', label: '帳號' }, { k: 'act', label: '動作' }, { k: 'method', label: '方式', mono: true },
  { k: 'path', label: '功能', mono: true }, { k: 'status', label: '結果', mono: true, tone: (r: any) => (r.status >= 500 ? 'prod' : r.status >= 400 ? 'test' : '') },
  { k: 'duration_ms', label: '耗時', mono: true, text: (r: any) => r.ms }, { k: 'ip', label: '來源 IP', mono: true },
]
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else>
      <div class="bar">
        <b class="h3">最近誰在用</b>
        <select :value="days" aria-label="期間" @change="set({ ad: ($event.target as HTMLSelectElement).value })">
          <option v-for="d in [1, 7, 30, 90]" :key="d" :value="d">最近 {{ d }} 天</option>
        </select>
        <span v-if="summary?.login_failed" class="bad">⚠ 期間有 {{ summary.login_failed }} 次登入失敗
          <button type="button" class="lb" @click="set({ aa: 'login_failed', au: undefined })">看是哪幾次</button></span>
        <InfoNote>只記會改東西的請求（新增／修改／刪除／匯入）與登入、登出、登入失敗；純查看（GET）刻意不記。不記輸入的內容（可能含密碼與真實資料），只記誰、什麼時候、哪一支功能、結果、從哪個 IP。紀錄保留 {{ data?.retain_days ?? '—' }} 天。只看報表不改東西的人不會留下紀錄，「沒有紀錄」不代表沒人用。</InfoNote>
        <button type="button" class="btn2" :disabled="loading" @click="load">重新整理</button>
      </div>
      <div v-if="summary?.by_user.length" class="cards" data-testid="activity-users">
        <button v-for="u in summary.by_user" :key="u.username" type="button" class="card" :class="{ on: fUser === u.username }"
                @click="set({ au: fUser === u.username ? undefined : u.username, aa: undefined })">
          <b>{{ u.username }}</b><span class="n">{{ u.n }} 次</span><span class="muted">最後 {{ u.last_at }}</span>
        </button>
      </div>
      <p v-else-if="summary" class="muted">這段期間沒有任何異動紀錄</p>
      <div class="bar">
        <button v-for="a in ACTIONS" :key="a.v" type="button" class="chip" :class="{ on: fAction === a.v }" @click="set({ aa: a.v || undefined })">{{ a.label }}</button>
        <span v-if="fUser" class="chip on">只看 {{ fUser }} <button type="button" class="x" aria-label="清除帳號篩選" @click="set({ au: undefined })">✕</button></span>
        <span class="muted">共 {{ data?.total ?? 0 }} 筆</span>
      </div>
      <V2Table :rows="rows" prefix="L" row-key="id" :max="PAGE" :cols="COLS" :export-all="exportAll" export-name="操作紀錄"
               :empty="loading ? '載入中…' : '沒有符合的紀錄'" />
      <div v-if="data && data.total > PAGE" class="bar pager">
        <button type="button" class="btn2" :disabled="offset === 0" @click="page(-1)">‹ 上一頁</button>
        <span>{{ offset + 1 }}–{{ Math.min(offset + PAGE, data.total) }}／{{ data.total }}</span>
        <button type="button" class="btn2" :disabled="offset + PAGE >= data.total" @click="page(1)">下一頁 ›</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { font-size: 15px; }
.cards { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; background: var(--card); font-family: inherit; font-size: 13px; cursor: pointer; text-align: left; }
.card.on { border-color: var(--brand); background: var(--mint); }
.card .n { color: var(--brand-dark); font-size: 12px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.x { border: 0; background: none; cursor: pointer; font-family: inherit; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2:disabled { opacity: .5; }
.lb { border: 0; background: none; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.pager { justify-content: center; }
.muted { color: var(--muted); font-size: 12px; }
.bad { color: var(--bad); font-size: 12px; }
.err { color: var(--bad); }
</style>
