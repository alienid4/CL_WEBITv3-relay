<script setup lang="ts">
// 外殼：白底側邊軌 + 白色 topbar + LIVE 狀態燈 + 版本。
// 2026-08-24 依「示範白綠」全站視覺規範改版（色一律取 main.css 變數，這裡不自己發明色）。
const { user, logout } = useAuth()
const { ensureLoaded } = useFeatureFlags()
const { toasts } = useToast()
const { apiFetch } = useApi()
const route = useRoute()

interface AppVersion { version: string; git_commit?: string | null; built_at?: string | null; started_at?: string | null }
const appVersion = ref<AppVersion | null>(null)

// 頂端原本是即時跳動的日期時鐘，但作業系統右下角就有時鐘了，戰情室重複顯示沒有意義。
// 使用者 2026-08-12 要的是「上次更版時間」——後端 /api/version 的 started_at 就是
// 這支服務行程真正啟動的時間，只有部署重啟過才會變，是「新程式碼有沒有生效」的
// 唯一可信依據（見 api.py version_endpoint 的說明），拿來顯示剛好回答「上次更版是何時」。

await ensureLoaded()

// 在線人數（2026-08-26 使用者要求：「在左上角顯示在線人數，我要知道誰在用」）。
// 「在線」＝最近 N 分鐘真的送過 request，不是「session 沒過期」——定義與理由寫在
// backend/activity.py 檔頭。這裡只負責顯示，並且**把統計視窗一起顯示出來**：
// 看到「3」而不知道是「幾分鐘內的 3」，這個數字沒辦法拿來做判斷。
interface OnlineUser {
  username: string; last_seen_at: string | null; sessions: number
  ip: string | null; idle_seconds: number | null
}
interface Online {
  count: number; window_minutes: number; users: OnlineUser[]
  last_activity_at: string | null; never_recorded: boolean
}
const online = ref<Online | null>(null)
const onlineOpen = ref(false)
let onlineTimer: ReturnType<typeof setInterval> | null = null

async function loadOnline() {
  online.value = await apiFetch<Online>('/api/online').catch(() => null)
}
function idleText(sec: number | null) {
  if (sec === null) return ''
  if (sec < 60) return '剛剛'
  return `${Math.floor(sec / 60)} 分鐘前`
}

onMounted(async () => {
  appVersion.value = await apiFetch<AppVersion>('/api/version').catch(() => null)
  if (isEnabled('activity')) {
    await loadOnline()
    // 60 秒更新一次：後端心跳本來就有 60 秒節流，拉得更快也只是拿到同一個數字
    onlineTimer = setInterval(loadOnline, 60_000)
  }
})
onBeforeUnmount(() => {
  if (onlineTimer) clearInterval(onlineTimer)
})


// 兩層選單（2026-08-15 使用者要求）：第一層本身就是功能頁，第二層是它底下的子功能。
// 編號是使用者指定的表達方式——「2 開頭就跟資產有關」，所以 2-1 新增資產、2-2 資料品質…（單據檔案室原本是 2-2，2026-08-26 搬到 7-2）
// 這讓人一眼看得出「這個功能屬於哪一塊」，18 個項目攤平時完全看不出來。
//
// 分群原則：**照使用者的心智模型分，不照後端模組分**。例如網段配置表在技術上是獨立
// 模組，但使用者是在「要登記/查一台資產」的情境下用它，所以掛在資產底下。
// mod = 這一項屬於哪個功能開關（feature_flags.module_key）。關掉後選單直接不顯示——
// 留在選單上但點了被踢回設定頁，使用者只會覺得系統壞了。沒標 mod 的一律永遠顯示。

const { isEnabled } = useFeatureFlags()
const { authDisabled } = useServerInfo()

// ===== 選單自訂（2026-09-11）：順序資料驅動、全站共用一份、限管理員改 =====
// 內建 NAV 是權威來源（預設順序＋每項的 icon/label/mod/match）；存到 app_settings 的
// nav_layout 只記「群與項目的順序、分群」（用路由當 key）。決策 B：系統管理的 6A/6B
// 子分區升成兩個獨立群，全站選單一律 2 層。NAV 資料與攤平/catalog 在 composables/useNav.ts。
const CATALOG = navCatalog()

// 存的項目：純路由字串（舊版），或 { to, label }（新版帶自訂名稱）。兩種都要吃。
type SavedItem = string | { to: string; label?: string }
interface SavedGroup { id?: string; label: string; icon?: string; to?: string; items: SavedItem[] }
const savedLayout = ref<SavedGroup[] | null>(null)
const canEditNav = ref(false)

function navVisible(item: NavItem) {
  if (item.adminOnly && !canEditNav.value) return false
  return !item.mod || isEnabled(item.mod)
}

// 合併預設＋存的順序 → 算編號 → 濾掉關掉的模組。存的版面沒放到的內建項目（新加的功能）
// 補回同名預設群，避免新功能因為某人存過舊版面就從此消失。
const visibleNav = computed(() => {
  const base = defaultGroups()
  const saved = savedLayout.value
  let groups: FlatGroup[]
  if (!saved || !saved.length) {
    groups = base.map((g) => ({ label: g.label, icon: g.icon, to: g.to, items: [...g.items] }))
  } else {
    const placed = new Set<string>()
    groups = saved.map((sg) => {
      const items = (sg.items || []).map((e) => {
        const to = typeof e === 'string' ? e : e.to
        const base = CATALOG[to]
        if (!base) return null
        // 有自訂名稱就蓋過內建 label；沒有就用內建
        const label = (typeof e === 'object' && e.label) ? e.label : base.label
        return { ...base, label }
      }).filter(Boolean) as NavItem[]
      items.forEach((it) => it.to && placed.add(it.to))
      return { label: sg.label, icon: sg.icon || '▸', to: sg.to || '', items }
    })
    for (const g of base) {
      for (const it of g.items) {
        if (!it.to || placed.has(it.to)) continue
        let grp = groups.find((x) => x.label === g.label)
        if (!grp) { grp = { label: g.label, icon: g.icon, to: g.to || '', items: [] }; groups.push(grp) }
        grp.items.push(it)
      }
    }
  }
  return groups
    .map((g, gi) => ({
      no: String(gi + 1), label: g.label, icon: g.icon, to: g.to,
      items: g.items.filter(navVisible).map((it, ii) => ({ ...it, no: `${gi + 1}-${ii + 1}` })),
    }))
    .filter((g) => (g.to ? true : g.items.length > 0))
})
type ShownGroup = { no: string; label: string; icon?: string; to?: string; items: NavItem[] }

async function loadNavLayout() {
  try {
    const r = await apiFetch<{ layout: SavedGroup[] | null; can_edit: boolean }>('/api/nav-layout')
    savedLayout.value = r.layout
    canEditNav.value = !!r.can_edit
  } catch { /* 讀不到就用內建預設，不讓選單掛掉 */ }
}
onMounted(loadNavLayout)

function isOn(item: { to?: string; match?: (p: string, tab: string) => boolean }) {
  if (!item.to) return false
  const tab = String(route.query.tab || '')
  return item.match ? item.match(route.path, tab) : route.path === item.to
}
function childOn(g: ShownGroup) { return g.items.some(isOn) }

// 展開狀態三態（null=跟路由、true/false=使用者按過的）。理由見舊註：目前所在的組要能收合。
const userToggled = ref<Record<string, boolean>>({})
const activeGroup = computed(() =>
  visibleNav.value.find((g) => isOn(g) || childOn(g))?.no ?? '')
function groupOpen(g: ShownGroup) {
  const manual = userToggled.value[g.no]
  return manual === undefined ? g.no === activeGroup.value : manual
}
function hasActiveChild(g: ShownGroup) { return childOn(g) }
function toggleGroup(g: ShownGroup) {
  userToggled.value = { ...userToggled.value, [g.no]: !groupOpen(g) }
}

async function handleLogout() {
  await logout()
  await navigateTo('/login')
}
</script>

<template>
  <div class="room">
    <nav class="rail">
      <NuxtLink to="/" class="mark">資</NuxtLink>
      <template v-for="g in visibleNav" :key="g.no">
        <div class="grow">
          <NuxtLink
            v-if="g.to" :to="g.to" class="nav lv1"
            :class="{ on: isOn(g), 'child-on': !groupOpen(g) && hasActiveChild(g) }"
          >
            <span class="no">{{ g.no }}</span><span class="i">{{ g.icon }}</span>{{ g.label }}
          </NuxtLink>
          <button
            v-else class="nav lv1 asheader" type="button"
            :class="{ 'child-on': !groupOpen(g) && hasActiveChild(g) }"
            @click="toggleGroup(g)"
          >
            <span class="no">{{ g.no }}</span><span class="i">{{ g.icon }}</span>{{ g.label }}
          </button>
          <button
            v-if="g.items.length" class="caret" type="button"
            :aria-label="`${groupOpen(g) ? '收合' : '展開'} ${g.label}`"
            :title="`${groupOpen(g) ? '收合' : '展開'} ${g.label}`"
            @click="toggleGroup(g)"
          >{{ groupOpen(g) ? '▾' : '▸' }}</button>
        </div>
        <template v-if="groupOpen(g)">
          <NuxtLink
            v-for="c in g.items" :key="c.to" :to="c.to!"
            class="nav lv2" :class="{ on: isOn(c) }"
          >
            <span class="no">{{ c.no }}</span><span class="i">{{ c.icon }}</span>{{ c.label }}
          </NuxtLink>
        </template>
      </template>
    </nav>

    <div class="col">
      <!-- 登入被關掉時的警告條。刻意做成**整條、紅色、關不掉**：
           這是「沒有存取控制」的狀態，不能讓人忘記它開著。
           2026-09-10 使用者為了測試方便要求可以關登入，這條是配套。 -->
      <div v-if="authDisabled" class="authoff">
        ⚠️ <b>登入已停用</b>——目前任何連得到這個網址的人都能看到全部資料。
        測試完請關掉（系統設定 → 安全性，或移除伺服器的 <code>WEBIT3_DISABLE_AUTH</code>）。
      </div>
      <header class="topbar">
        <div class="brand"><span class="logo">資</span><span class="bt">資訊戰情室<small>資產盤點</small></span></div>
        <div v-if="online" class="online-wrap">
          <button
            class="online-chip" type="button"
            :class="{ zero: online.count === 0, unknown: online.never_recorded }"
            :title="online.never_recorded
              ? '這台系統還沒記錄過任何活動——可能是剛升級。這不等於「沒有人在用」'
              : `最近 ${online.window_minutes} 分鐘內有動作的人`"
            @click="onlineOpen = !onlineOpen"
          >
            <span class="dot" />
            <template v-if="online.never_recorded">尚未開始記錄</template>
            <template v-else>線上 <b>{{ online.count }}</b></template>
          </button>
          <div v-if="onlineOpen" class="online-pop">
            <div class="op-hd">
              誰在線上
              <small>最近 {{ online.window_minutes }} 分鐘內有動作</small>
            </div>
            <ul v-if="online.users.length">
              <li v-for="u in online.users" :key="u.username">
                <b>{{ u.username }}</b>
                <span class="mono">{{ u.ip || '—' }}</span>
                <span class="ago">{{ idleText(u.idle_seconds) }}</span>
                <span v-if="u.sessions > 1" class="multi">{{ u.sessions }} 個連線</span>
              </li>
            </ul>
            <p v-else-if="online.never_recorded" class="op-empty">
              <b>尚未開始記錄。</b>這台系統還沒留下任何活動紀錄（可能是剛升級）。
              這<b>不等於</b>「沒有人在用」——分不清楚的話，這個 0 不能拿來做判斷。
            </p>
            <p v-else class="op-empty">
              目前沒有人在線上。最後一次活動：{{ online.last_activity_at || '（無紀錄）' }}
            </p>
            <NuxtLink class="op-more" to="/activity" @click="onlineOpen = false">
              查看操作紀錄 →
            </NuxtLink>
          </div>
        </div>
        <GlobalSearch />
        <div class="tright">
          <span v-if="appVersion?.started_at" class="clock mono" :title="'服務行程啟動時間，只有部署重啟過才會變'">
            上次更版 {{ appVersion.started_at }}
          </span>
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
/* 顏色一律取自 main.css 的變數，這裡不自己發明色（全站視覺規範 §1）。 */
.room { display: grid; grid-template-columns: 208px 1fr; min-height: 100vh; }

.rail { padding: 18px 12px; border-right: 1px solid var(--border); background: var(--sidebar); }
.mark { display: flex; align-items: center; justify-content: center; width: 42px; height: 42px; border-radius: 12px;
  background: var(--brand); color: #fff; font-weight: 700; font-size: 18px;
  text-decoration: none; margin: 4px 4px 18px; }
.rgrp { font-size: 10px; text-transform: uppercase; letter-spacing: 2px; color: var(--muted); padding: 14px 12px 6px; }
.nav { display: flex; align-items: center; gap: 11px; padding: 10px 12px; border-radius: 10px; font-size: 14px;
  color: var(--sidebar-text); text-decoration: none; margin-bottom: 2px; transition: background .15s, color .15s; }
.nav:hover { background: var(--sub); color: var(--ink); }
.nav.on { background: var(--sidebar-active-bg); color: var(--sidebar-active-text); font-weight: 600; }
.nav.dis { opacity: .4; }
.nav .i { width: 18px; text-align: center; opacity: .85; }
/* 兩層選單：編號讓人一眼看出歸屬，縮排＋左側線標示層級 */
.nav .no { font-family: var(--disp); font-size: 10px; color: var(--muted);
  min-width: 20px; letter-spacing: .5px; }
.nav.on .no { color: var(--sidebar-active-text); }
.nav.lv1 { font-weight: 600; flex: 1; min-width: 0; padding-right: 4px; }
.nav.lv1 > :last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nav.lv2 { margin-left: 14px; padding-left: 10px; font-size: 13px;
  border-left: 1px solid var(--border); border-radius: 0 10px 10px 0; }
.nav.lv2 .no { min-width: 26px; }
/* 子分區標題（5A/5B/5C）：不可點、小字、灰色，只當視覺分隔 */
.nav.subheader { margin-left: 14px; padding: 6px 10px 3px; font-size: 11px; font-weight: 600;
  color: var(--muted); border-left: 1px solid var(--border); border-radius: 0;
  letter-spacing: .04em; cursor: default; gap: 7px; }
.nav.subheader .no { min-width: 22px; }
/* 子分區底下的頁面（第三層）：再縮排一點，跟子分區標題對齊在它下方 */
.nav.lv3 { margin-left: 26px; padding-left: 10px; font-size: 12.5px;
  border-left: 1px solid var(--border); border-radius: 0 10px 10px 0; }
.grow { display: flex; align-items: center; gap: 0; min-width: 0; }
.caret { flex: none; width: 22px; height: 28px; background: none; border: none;
  border-radius: 8px; color: var(--muted); cursor: pointer; font-size: 10px;
  line-height: 1; padding: 0; display: flex; align-items: center; justify-content: center; }
.caret:hover { color: var(--brand-dark); background: var(--sub); }
.nav.asheader { font-family: inherit; background: none; border: none; cursor: pointer;
  text-align: left; width: 100%; font-size: 14px; }
/* 收合狀態下，這一組裡面有頁面正被開著：用一個小點標示，不然收起來就完全看不出人在哪 */
.nav.child-on { color: var(--sidebar-active-text); }
.nav.child-on .no { color: var(--sidebar-active-text); }
.nav.child-on::after { content: "●"; margin-left: auto; font-size: 7px; color: var(--brand-dark); }

.col { display: flex; flex-direction: column; min-width: 0; }
.online-wrap { position: relative; }
.online-chip { display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  background: transparent; border: 1px solid var(--border-strong); border-radius: 14px;
  padding: 3px 11px; font-size: 12px; color: var(--ink-aux); white-space: nowrap; }
.online-chip:hover { border-color: var(--brand); color: var(--brand-dark); }
.online-chip .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--good); }
.online-chip.zero .dot { background: var(--ink-aux); opacity: 0.5; }
.online-chip.unknown .dot { background: var(--warn); }
.online-chip b { font-size: 13px; color: var(--ink); }
.online-pop { position: absolute; top: 30px; left: 0; z-index: 60; min-width: 300px;
  background: var(--card-solid); border: 1px solid var(--border-strong); border-radius: 10px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.16); padding: 10px 12px; }
.online-pop .op-hd { font-size: 13px; font-weight: 700; margin-bottom: 6px; }
.online-pop .op-hd small { font-weight: 400; color: var(--ink-aux); margin-left: 6px; font-size: 11px; }
.online-pop ul { list-style: none; margin: 0; padding: 0; max-height: 260px; overflow-y: auto; }
.online-pop li { display: flex; align-items: baseline; gap: 8px; padding: 4px 0;
  border-bottom: 1px solid var(--border); font-size: 12px; }
.online-pop li:last-child { border-bottom: none; }
.online-pop .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--ink-aux); font-size: 11px; }
.online-pop .ago { margin-left: auto; color: var(--ink-aux); font-size: 11px; }
.online-pop .multi { color: var(--warn); font-size: 11px; }
.online-pop .op-empty { font-size: 12px; color: var(--ink-aux); line-height: 1.7; margin: 4px 0; }
.online-pop .op-more { display: block; margin-top: 8px; font-size: 12px; color: var(--brand-dark); }
.authoff { background: var(--bad, #c62828); color: #fff; font-size: 13px; line-height: 1.6;
  padding: 8px 30px; text-align: center; }
.authoff code { background: rgba(255,255,255,.18); padding: 1px 5px; border-radius: 3px; }
.topbar { display: flex; align-items: center; justify-content: space-between; padding: 14px 30px;
  background: var(--topbar); backdrop-filter: blur(8px); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 20; }
.brand { display: flex; align-items: center; gap: 11px; }
.brand .logo { width: 32px; height: 32px; border-radius: 9px; background: var(--brand);
  color: #fff; font-weight: 700; display: flex; align-items: center; justify-content: center; font-size: 15px; }
.brand .bt { font-size: 15px; font-weight: 700; color: var(--ink); line-height: 1.2; }
.brand .bt small { display: block; font-size: 10px; font-weight: 400; color: var(--muted); }
.tright { display: flex; align-items: center; gap: 18px; }
.clock { font-size: 12.5px; color: var(--ink-aux); font-variant-numeric: tabular-nums; letter-spacing: .5px; white-space: nowrap; }
.live { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; color: var(--muted);
  font-family: var(--disp); letter-spacing: 2px; }
/* 白底不加光暈（全站規範 §3 狀態燈），只用不透明度做心跳 */
.beat { width: 7px; height: 7px; border-radius: 50%; background: var(--good); animation: beat 1.8s infinite; }
@keyframes beat { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
.who { font-size: 12px; color: var(--muted); text-align: right; line-height: 1.4; }
.who b { color: var(--ink); }
.ver { display: block; font-size: 12.5px; color: var(--ink-aux); font-family: var(--disp); }
.logout { background: transparent; border: 1px solid var(--border-strong); color: var(--ink-aux);
  border-radius: 10px; padding: 7px 14px; font-size: 12px; cursor: pointer; }
.logout:hover { border-color: var(--brand); color: var(--brand-dark); }

/* 內容最大寬 1600px 置中（全站規範 §4） */
.content { padding: 26px 30px 44px; max-width: var(--maxw); width: 100%; margin: 0 auto; }

.toast-wrap { position: fixed; top: 18px; right: 18px; z-index: 1000; display: flex; flex-direction: column; gap: 8px; }
.toast { min-width: 220px; max-width: 360px; padding: 11px 15px; border-radius: 10px; font-size: 13px; font-weight: 600;
  color: #fff; box-shadow: 0 6px 18px rgba(16,40,34,.14); animation: tin .18s ease; }
@keyframes tin { from{transform:translateY(-6px);opacity:0} to{transform:translateY(0);opacity:1} }
.toast-success { background: var(--good); }
.toast-error { background: var(--bad); }
.toast-warn { background: var(--warn-text); }   /* 白字配 --warn 只有 4.28:1，用加深版才過 AA */
.toast-info { background: var(--ink-soft); }
</style>
