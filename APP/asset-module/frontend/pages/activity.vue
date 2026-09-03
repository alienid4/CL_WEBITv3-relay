<script setup lang="ts">
// 操作紀錄（2026-08-26 使用者要求：「我要知道誰在用，LOG 紀錄也要」）。
//
// 這頁要回答的兩個問題不一樣，所以分成兩塊：
//   · 上半：**誰在用這套系統**（最近 N 天各自做了幾次）——在線人數只看得到當下
//   · 下半：**誰做了什麼**（逐筆，可篩人／可篩動作）
//
// 只記會改東西的請求（非 GET）與登入相關事件；不記 request body。
// 定義與取捨全部寫在 backend/activity.py 檔頭，畫面上也講一次給使用者看，
// 免得有人以為「查不到某個動作」是系統壞了——那是刻意不記。
definePageMeta({ ssr: false })

interface Row {
  id: number; at: string; username: string | null; ip: string | null
  method: string; path: string; status: number | null
  duration_ms: number | null; action: string
}
interface ListResp { total: number; limit: number; offset: number; rows: Row[]; retain_days: number }
interface Summary {
  days: number
  by_user: { username: string; n: number; last_at: string }[]
  login_failed: number
  total: number
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const summary = ref<Summary | null>(null)
const data = ref<ListResp | null>(null)
const loading = ref(false)
const errorMessage = ref('')

const days = ref(7)
const fUser = ref('')
const fAction = ref('')
const offset = ref(0)
const PAGE = 200

const ACTIONS = [
  { v: '', label: '全部' },
  { v: 'change', label: '資料異動' },
  { v: 'login', label: '登入' },
  { v: 'logout', label: '登出' },
  { v: 'login_failed', label: '登入失敗' },
]

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const q = new URLSearchParams({ limit: String(PAGE), offset: String(offset.value) })
    if (fUser.value) q.set('username', fUser.value)
    if (fAction.value) q.set('action', fAction.value)
    const [s, d] = await Promise.all([
      apiFetch<Summary>(`/api/activity/summary?days=${days.value}`),
      apiFetch<ListResp>(`/api/activity?${q}`),
    ])
    summary.value = s
    data.value = d
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
await load()
watch([days, fUser, fAction], () => { offset.value = 0; load() })

function page(delta: number) {
  const next = offset.value + delta * PAGE
  if (next < 0 || next >= (data.value?.total ?? 0)) return
  offset.value = next
  load()
}

const { sortKey, sortDir, toggle, sorted } = useSort(computed(() => data.value?.rows ?? []), '')

function statusClass(s: number | null) {
  if (s === null) return ''
  if (s >= 500) return 'bad'
  if (s >= 400) return 'warn'
  return 'ok'
}
function actionLabel(a: string) {
  return ACTIONS.find((x) => x.v === a)?.label ?? a
}
</script>

<template>
  <div class="page">
    <header class="hd">
      <div>
        <h1>操作紀錄</h1>
        <p class="sub">誰在用這套系統、誰改了什麼。</p>
      </div>
      <button class="btn" :disabled="loading" @click="load">↻ 重新整理</button>
    </header>

    <p v-if="errorMessage" class="err">{{ errorMessage }}</p>

    <p class="scope">
      <b>記錄範圍：</b>只記<b>會改東西的請求</b>（新增／修改／刪除／匯入）與
      <b>登入、登出、登入失敗</b>。純查看（GET）刻意不記——那佔九成以上流量而稽核價值低，
      全記會把真正要查的東西淹掉。<b>不記你輸入的內容</b>（可能含密碼與真實資料），
      只記「誰、什麼時候、哪一支功能、結果如何、從哪個 IP」。
      紀錄保留 <b>{{ data?.retain_days ?? '—' }}</b> 天。
    </p>

    <!-- 誰在用 -->
    <section v-if="summary" class="who">
      <div class="who-hd">
        <h2>最近誰在用</h2>
        <select v-model.number="days" class="sel">
          <option :value="1">最近 1 天</option>
          <option :value="7">最近 7 天</option>
          <option :value="30">最近 30 天</option>
          <option :value="90">最近 90 天</option>
        </select>
        <span v-if="summary.login_failed" class="failed">
          ⚠ 期間有 <b>{{ summary.login_failed }}</b> 次登入失敗
        </span>
      </div>
      <div v-if="summary.by_user.length" class="who-list">
        <button
          v-for="u in summary.by_user" :key="u.username" class="who-card"
          :class="{ on: fUser === u.username }"
          @click="fUser = fUser === u.username ? '' : u.username"
        >
          <b>{{ u.username }}</b>
          <span class="n">{{ u.n }} 次</span>
          <span class="last">最後 {{ u.last_at }}</span>
        </button>
      </div>
      <p v-else class="empty-inline">
        這段期間沒有任何異動紀錄。<b>這不代表沒人用</b>——只看報表不改東西的人不會留下紀錄
        （純查看刻意不記）。要看「現在誰開著」請點左上角的線上人數。
      </p>
    </section>

    <!-- 逐筆 -->
    <section class="toolbar">
      <div class="tabs">
        <button
          v-for="a in ACTIONS" :key="a.v" :class="{ on: fAction === a.v }"
          @click="fAction = a.v"
        >{{ a.label }}</button>
      </div>
      <span v-if="fUser" class="chip">
        只看 <b>{{ fUser }}</b>
        <button class="x" @click="fUser = ''">✕</button>
      </span>
      <span class="total">共 {{ data?.total ?? 0 }} 筆</span>
    </section>

    <div class="tbl-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <SortTh k="at" :active="sortKey" :dir="sortDir" @sort="toggle">時間</SortTh>
            <SortTh k="username" :active="sortKey" :dir="sortDir" @sort="toggle">帳號</SortTh>
            <SortTh k="action" :active="sortKey" :dir="sortDir" @sort="toggle">動作</SortTh>
            <SortTh k="method" :active="sortKey" :dir="sortDir" @sort="toggle">方式</SortTh>
            <SortTh k="path" :active="sortKey" :dir="sortDir" @sort="toggle">功能</SortTh>
            <SortTh k="status" :active="sortKey" :dir="sortDir" @sort="toggle">結果</SortTh>
            <SortTh k="duration_ms" :active="sortKey" :dir="sortDir" @sort="toggle">耗時</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">來源 IP</SortTh>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="8" class="empty">載入中…</td></tr>
          <tr v-else-if="sorted.length === 0"><td colspan="8" class="empty">沒有符合的紀錄</td></tr>
          <tr v-for="r in sorted" :key="r.id" :class="{ danger: r.action === 'login_failed' }">
            <td class="mono">{{ r.at }}</td>
            <td>{{ r.username || '（未登入）' }}</td>
            <td>{{ actionLabel(r.action) }}</td>
            <td class="mono">{{ r.method }}</td>
            <td class="mono path">{{ r.path }}</td>
            <td><span class="st" :class="statusClass(r.status)">{{ r.status ?? '—' }}</span></td>
            <td class="mono num">{{ r.duration_ms !== null ? r.duration_ms + ' ms' : '—' }}</td>
            <td class="mono">{{ r.ip || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="data && data.total > PAGE" class="pager">
      <button class="btn" :disabled="offset === 0" @click="page(-1)">← 上一頁</button>
      <span>{{ offset + 1 }}–{{ Math.min(offset + PAGE, data.total) }} / {{ data.total }}</span>
      <button class="btn" :disabled="offset + PAGE >= data.total" @click="page(1)">下一頁 →</button>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 18px 22px 60px; }
.hd { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0; }
.sub { color: var(--muted); font-size: 13px; margin: 0; }
.err { color: var(--bad); }
.scope { font-size: 12px; color: var(--muted); line-height: 1.8; margin: 12px 0;
  border-left: 3px solid var(--line); padding-left: 10px; }

.who { margin: 16px 0; }
.who-hd { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.who-hd .failed { color: var(--bad); font-size: 12px; }
.who-list { display: flex; gap: 10px; flex-wrap: wrap; }
.who-card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; cursor: pointer;
  background: var(--card); text-align: left; }
.who-card.on { border-color: var(--brand); }
.who-card .n { font-size: 12px; color: var(--brand-dark); }
.who-card .last { font-size: 11px; color: var(--muted); }
.empty-inline { font-size: 12px; color: var(--muted); line-height: 1.8; }

.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.tabs { display: flex; gap: 6px; }
.tabs button { border: 1px solid var(--line); background: var(--card); border-radius: 6px;
  padding: 5px 12px; cursor: pointer; font-size: 13px; }
.tabs button.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.sel { padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; }
.chip { font-size: 12px; border: 1px solid var(--brand); border-radius: 12px; padding: 3px 10px; }
.chip .x { border: none; background: transparent; cursor: pointer; margin-left: 4px; }
.total { margin-left: auto; font-size: 12px; color: var(--muted); }

.tbl-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { padding: 5px 10px; border-bottom: 1px solid var(--line); text-align: left;
  white-space: nowrap; }
.tbl tbody tr.danger td { background: var(--bad-soft); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.path { max-width: 340px; overflow: hidden; text-overflow: ellipsis; }
.num { text-align: right; }
.empty { text-align: center; color: var(--muted); padding: 24px; }
.st { display: inline-block; min-width: 34px; text-align: center; border-radius: 4px;
  padding: 0 5px; font-size: 12px; }
.st.ok { background: var(--good-soft); }
.st.warn { background: var(--warn-soft); }
.st.bad { background: var(--bad-soft); }

.btn { border: 1px solid var(--line); background: var(--card); border-radius: 6px;
  padding: 5px 12px; cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.pager { display: flex; align-items: center; gap: 12px; justify-content: center;
  margin-top: 12px; font-size: 12px; color: var(--muted); }
</style>
