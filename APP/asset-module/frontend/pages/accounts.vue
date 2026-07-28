<script setup lang="ts">
// 帳號儀表板：只放彙總數字框，每個框都可點下鑽到「帳號合規表」對應的資料。
// 表格獨立在 /account-matrix（使用者 2026-07-23 拍板：儀表板與表格分兩頁）。
// 點框框 → navigateTo('/account-matrix?…') 帶 query，合規表頁自動預選分頁＋套篩選。
const { apiFetch } = useApi()

interface Acc {
  pw_status: string | null; pw_expiry_status: string; uid: number; username: string
  is_sudoer: number; sudo_nopasswd: number; authorized_keys: number | null; never_logged_in: number
}
interface Summary {
  has_data: boolean
  fail_high?: number; fail_medium?: number; unknown?: number
  accounts?: number; privileged?: number; humans?: number
  run?: { started_at: string; host_count: number }
}

const accounts = ref<Acc[]>([])
const findingsCount = ref(0)
const summary = ref<Summary | null>(null)
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const [a, f] = await Promise.all([
      apiFetch<{ items: Acc[] }>('/api/accounts'),
      apiFetch<{ items: any[]; summary: Summary }>('/api/accounts/findings'),
    ])
    accounts.value = a.items
    findingsCount.value = f.items.length
    summary.value = f.summary
  } catch { /* 拿不到就留空 */ } finally {
    loading.value = false
  }
}
await load()

// passwd -S 狀態碼正規化（跟合規表 pwState 一致）：set/locked/empty/null。
function pwState(a: Acc): 'set' | 'locked' | 'empty' | null {
  const s = (a.pw_status || '').toUpperCase()
  if (s === '') return null
  if (s === 'LOCKED' || s === 'LK' || s === 'L') return 'locked'
  if (s === 'EMPTY' || s === 'NP') return 'empty'
  if (s === 'SET' || s === 'PS' || s === 'P') return 'set'
  return null
}
function cnt(fn: (a: Acc) => boolean) { return accounts.value.filter(fn).length }

// 上排：總量 / 稽核發現 / 風險 / 資料落差 / 帳號屬性。每個框都連到合規表對應視圖。
const kpis = computed(() => [
  { n: accounts.value.length, label: '受稽核帳號', to: '/account-matrix', tone: '' },
  { n: findingsCount.value, label: '稽核發現', hint: '看明細', to: '/account-matrix?tab=findings', tone: '' },
  { n: summary.value?.fail_high ?? 0, label: '高風險不合規', to: '/account-matrix?tab=findings&sev=high', tone: 'bad' },
  { n: summary.value?.fail_medium ?? 0, label: '中風險不合規', to: '/account-matrix?tab=findings&sev=medium', tone: 'warn' },
  { n: summary.value?.unknown ?? 0, label: '查不到（權限不足）', hint: '不等於合格', to: '/account-matrix?col=disabled&val=needroot', tone: 'warn' },
  { n: summary.value?.privileged ?? 0, label: '特權帳號', hint: `/ ${accounts.value.length} 全部`, to: '/account-matrix?sudoer=1', tone: '' },
  { n: summary.value?.humans ?? 0, label: '真人帳號', to: '/account-matrix?kind=human', tone: '' },
])

// 下排：各項合規檢查中招數。每個框連到合規表並套上該欄篩選（col+val）。
const checks = computed(() => [
  { n: cnt(a => a.pw_expiry_status === 'expired'), label: '密碼已過期', to: '/account-matrix?col=pwExpired&val=expired', tone: 'bad' },
  { n: cnt(a => a.pw_expiry_status === 'never'), label: '密碼永不過期', to: '/account-matrix?col=pwExpired&val=never', tone: 'bad' },
  { n: cnt(a => pwState(a) === 'empty'), label: '空密碼', to: '/account-matrix?col=empty&val=yes', tone: 'bad' },
  { n: cnt(a => a.uid === 0 && a.username !== 'root'), label: 'UID 0 非 root', to: '/account-matrix?col=uid0&val=yes', tone: 'bad' },
  { n: cnt(a => a.uid !== 0 && !!a.sudo_nopasswd), label: '免密碼 sudo', to: '/account-matrix?col=sudo&val=nopw', tone: 'bad' },
  { n: cnt(a => a.uid !== 0 && !a.sudo_nopasswd && !!a.is_sudoer), label: '有 sudo 權限', to: '/account-matrix?col=sudo&val=yes', tone: 'warn' },
  { n: cnt(a => (a.authorized_keys ?? 0) > 0), label: '免密碼金鑰', to: '/account-matrix?col=keys&val=has', tone: 'warn' },
  { n: cnt(a => !!a.never_logged_in), label: '從未登入', to: '/account-matrix?col=login&val=never', tone: 'warn' },
])

function go(to: string) { navigateTo(to) }
</script>

<template>
  <div>
    <div class="section-divider">帳號儀表板</div>
    <p class="lead">
      帳號盤點的彙總數字。<b>每個框都可以點</b>，點下去會帶你到
      <NuxtLink class="dl" to="/account-matrix">帳號合規表</NuxtLink> 看對應的實際帳號／發現。
      要收資料到 <NuxtLink class="dl" to="/account-ops">盤點作業</NuxtLink>。
    </p>

    <p v-if="loading" class="muted">載入中…</p>
    <p v-else-if="accounts.length === 0" class="muted">
      沒有帳號資料，到 <NuxtLink class="dl" to="/account-ops">盤點作業</NuxtLink> 收一輪。
    </p>
    <template v-else>
      <div class="tiles">
        <div v-for="k in kpis" :key="k.label" class="tile clickable" :class="[k.tone, { hot: k.tone && k.n > 0 }]"
             @click="go(k.to)">
          <div class="t-num">{{ k.n }}<small v-if="k.hint && k.hint.startsWith('/')" class="of">{{ k.hint }}</small></div>
          <div class="t-lbl">{{ k.label }}<span v-if="k.hint && !k.hint.startsWith('/')" class="hint">{{ k.hint }}</span></div>
          <span class="drill">看資料 →</span>
        </div>
      </div>

      <h3 class="dash-h">各項合規檢查中招數（點框看是哪些帳號）</h3>
      <div class="tiles checks">
        <div v-for="c in checks" :key="c.label" class="tile check clickable" :class="[c.tone, { zero: c.n === 0, hot: c.n > 0 }]"
             @click="c.n && go(c.to)">
          <div class="t-num">{{ c.n }}</div>
          <div class="t-lbl">{{ c.label }}</div>
          <span v-if="c.n > 0" class="drill">看帳號 →</span>
        </div>
      </div>

      <p v-if="summary?.run" class="when">
        最後盤點 · {{ summary.run.started_at }}（{{ summary.run.host_count }} 台）
      </p>
    </template>
  </div>
</template>

<style scoped>
.lead { color: var(--muted); margin: 0 0 18px; line-height: 1.7; }
.lead b { color: var(--ink, #dfeee9); }
.dl { color: var(--brand, #26a889); text-decoration: none; }
.dl:hover { text-decoration: underline; }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 8px; }
.tiles.checks { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
.tile { position: relative; background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px; }
.tile.clickable { cursor: pointer; transition: transform .12s, border-color .12s, box-shadow .12s; }
.tile.clickable:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(0,0,0,.28); border-color: rgba(47,214,172,.4); }
.tile.bad.hot { border-color: rgba(224,108,108,.5); }
.tile.warn.hot { border-color: rgba(230,170,60,.45); }
.t-num { font-size: 30px; font-weight: 700; color: var(--brand, #26a889); line-height: 1.1; font-variant-numeric: tabular-nums; }
.tile.bad.hot .t-num { color: #e06c6c; }
.tile.warn.hot .t-num { color: #d9a441; }
.t-num .of { font-size: 13px; color: var(--muted); font-weight: 500; margin-left: 2px; }
.t-lbl { font-size: 12.5px; color: var(--muted); margin-top: 5px; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .7; }
.tile.check.zero { opacity: .5; cursor: default; }
.tile.check.zero:hover { transform: none; box-shadow: none; border-color: var(--border); }
.tile.check.zero .t-num { color: var(--muted); }
.drill { position: absolute; right: 12px; bottom: 10px; font-size: 10px; color: var(--brand, #26a889);
  opacity: 0; transition: opacity .12s; }
.tile.clickable:hover .drill { opacity: .9; }
.dash-h { font-size: 14px; color: var(--muted); font-weight: 600; margin: 22px 0 10px; }
.when { font-size: 12px; color: var(--muted); margin-top: 16px; }
</style>
