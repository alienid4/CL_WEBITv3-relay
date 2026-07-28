<script setup lang="ts">
// 資訊戰情室 command-center 外殼：深色側邊軌 + 玻璃 topbar + LIVE 脈動 + 版本。
const { user, logout } = useAuth()
const { ensureLoaded } = useFeatureFlags()
const { toasts } = useToast()
const { apiFetch } = useApi()
const route = useRoute()

const appVersion = ref<{ version: string; git_commit?: string | null; built_at?: string | null } | null>(null)

// 頂端即時時鐘（戰情室該一眼看到現在幾點）。只在 client 跑，避免 SSR 對不上。
const clock = ref('')
let clockTimer: ReturnType<typeof setInterval> | undefined
function tick() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  const wd = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
  clock.value = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}（${wd}）`
    + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// ⚠️ 生命週期 hook 一定要在任何 top-level await（下面的 ensureLoaded）之前同步註冊，
// 否則 await 之後 Vue 的 active instance 已失效，onMounted/onBeforeUnmount 掛不穩，
// 時鐘的 setInterval 在 Suspense 重掛時被清掉就再也不跳——會停在「上板時間」。
onMounted(() => {
  tick()
  clearInterval(clockTimer)          // 保險：重掛時先清掉舊的再起新的
  clockTimer = setInterval(tick, 1000)
})
onBeforeUnmount(() => clearInterval(clockTimer))

await ensureLoaded()

onMounted(async () => {
  appVersion.value = await apiFetch('/api/version').catch(() => null)
})

async function handleLogout() {
  await logout()
  await navigateTo('/login')
}
</script>

<template>
  <div class="room">
    <nav class="rail">
      <NuxtLink to="/" class="mark">資</NuxtLink>
      <div class="rgrp">總覽</div>
      <NuxtLink to="/" class="nav" :class="{ on: route.path === '/' }"><span class="i">◈</span>儀表板</NuxtLink>
      <NuxtLink to="/issues" class="nav" :class="{ on: route.path.startsWith('/issues') }"><span class="i">⚑</span>問題清單</NuxtLink>
      <NuxtLink to="/scan-results" class="nav" :class="{ on: route.path.startsWith('/scan-results') }"><span class="i">◎</span>掃描結果</NuxtLink>
      <NuxtLink to="/assets" class="nav" :class="{ on: route.path.startsWith('/assets') }"><span class="i">▤</span>資產查詢</NuxtLink>
      <NuxtLink to="/topology" class="nav" :class="{ on: route.path.startsWith('/topology') }"><span class="i">◇</span>系統聯通圖</NuxtLink>
      <NuxtLink to="/services" class="nav" :class="{ on: route.path.startsWith('/services') }"><span class="i">⌁</span>服務盤點</NuxtLink>
      <NuxtLink to="/accounts" class="nav" :class="{ on: route.path === '/accounts' }"><span class="i">☖</span>帳號儀表板</NuxtLink>
      <NuxtLink to="/account-matrix" class="nav" :class="{ on: route.path === '/account-matrix' }"><span class="i">▦</span>帳號合規表</NuxtLink>
      <div class="rgrp">作業</div>
      <NuxtLink to="/account-ops" class="nav" :class="{ on: route.path === '/account-ops' }"><span class="i">☷</span>盤點作業</NuxtLink>
      <NuxtLink to="/adopt" class="nav" :class="{ on: route.path === '/adopt' }"><span class="i">＋</span>納入管理</NuxtLink>
      <NuxtLink to="/import" class="nav" :class="{ on: route.path === '/import' }"><span class="i">⇅</span>資料匯入</NuxtLink>
      <NuxtLink to="/settings" class="nav" :class="{ on: route.path.startsWith('/settings') }"><span class="i">⚙</span>系統設定</NuxtLink>
    </nav>

    <div class="col">
      <header class="topbar">
        <div class="brand"><span class="logo">資</span><span class="bt">資訊戰情室<small>資產盤點</small></span></div>
        <GlobalSearch />
        <div class="tright">
          <span class="clock mono">{{ clock }}</span>
          <span class="live"><span class="beat"></span>LIVE</span>
          <div class="who">
            <b>{{ user }}</b>　資訊部
            <span v-if="appVersion" class="ver">v{{ appVersion.version }} · {{ appVersion.git_commit }}</span>
          </div>
          <button class="logout" type="button" @click="handleLogout">登出</button>
        </div>
      </header>
      <main class="content"><slot /></main>
    </div>

    <div class="toast-wrap">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`toast-${t.type}`">{{ t.message }}</div>
    </div>
  </div>
</template>

<style scoped>
.room { display: grid; grid-template-columns: 208px 1fr; min-height: 100vh; }

.rail { padding: 18px 12px; border-right: 1px solid var(--border); }
.mark { display: flex; align-items: center; justify-content: center; width: 42px; height: 42px; border-radius: 12px;
  background: linear-gradient(135deg, #2fd6ac, #1e8a6f); color: #04120e; font-weight: 800; font-size: 18px;
  text-decoration: none; box-shadow: 0 0 22px rgba(47,214,172,.45); margin: 4px 4px 18px; }
.rgrp { font-size: 10px; text-transform: uppercase; letter-spacing: 1.4px; color: #5f7d72; padding: 14px 12px 6px; }
.nav { display: flex; align-items: center; gap: 11px; padding: 10px 12px; border-radius: 10px; font-size: 14px;
  color: var(--sidebar-text); text-decoration: none; margin-bottom: 2px; transition: background .15s, color .15s; }
.nav:hover { background: rgba(255,255,255,.05); color: #e4efe9; }
.nav.on { background: var(--sidebar-active-bg); color: var(--sidebar-active-text); font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(47,214,172,.35); }
.nav.dis { opacity: .4; }
.nav .i { width: 18px; text-align: center; opacity: .9; }

.col { display: flex; flex-direction: column; min-width: 0; }
.topbar { display: flex; align-items: center; justify-content: space-between; padding: 14px 30px;
  background: var(--topbar); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 20; }
.brand { display: flex; align-items: center; gap: 11px; }
.brand .logo { width: 32px; height: 32px; border-radius: 9px; background: linear-gradient(135deg,#2fd6ac,#1e8a6f);
  color: #04120e; font-weight: 800; display: flex; align-items: center; justify-content: center; font-size: 15px; }
.brand .bt { font-size: 15px; font-weight: 700; color: #fff; line-height: 1.2; }
.brand .bt small { display: block; font-size: 10px; font-weight: 400; color: var(--muted); }
.tright { display: flex; align-items: center; gap: 18px; }
.clock { font-size: 12.5px; color: #dfeee9; font-variant-numeric: tabular-nums; letter-spacing: .5px; white-space: nowrap; }
.live { display: inline-flex; align-items: center; gap: 7px; font-size: 11.5px; color: var(--muted); font-family: var(--disp); letter-spacing: 1px; }
.beat { width: 8px; height: 8px; border-radius: 50%; background: #2fd6ac; animation: beat 1.8s infinite; }
@keyframes beat { 0%{box-shadow:0 0 0 0 rgba(47,214,172,.55)} 70%{box-shadow:0 0 0 8px rgba(47,214,172,0)} 100%{box-shadow:0 0 0 0 rgba(47,214,172,0)} }
.who { font-size: 12px; color: var(--muted); text-align: right; line-height: 1.4; }
.who b { color: #dfeee9; }
.ver { display: block; font-size: 10px; color: #5f7d72; font-family: var(--disp); }
.logout { background: rgba(255,255,255,.05); border: 1px solid var(--border); color: var(--ink-soft);
  border-radius: 8px; padding: 7px 14px; font-size: 12px; cursor: pointer; }
.logout:hover { border-color: var(--brand); color: var(--brand); }

.content { padding: 26px 30px 44px; }

.toast-wrap { position: fixed; top: 18px; right: 18px; z-index: 1000; display: flex; flex-direction: column; gap: 8px; }
.toast { min-width: 220px; max-width: 360px; padding: 11px 15px; border-radius: 10px; font-size: 13px; font-weight: 700;
  color: #04120e; box-shadow: 0 8px 24px rgba(0,0,0,.35); animation: tin .18s ease; }
@keyframes tin { from{transform:translateY(-6px);opacity:0} to{transform:translateY(0);opacity:1} }
.toast-success { background: #2fd6ac; }
.toast-error { background: #ff6b6b; color: #fff; }
.toast-warn { background: #ffb867; }
.toast-info { background: #cfe0da; }
</style>
