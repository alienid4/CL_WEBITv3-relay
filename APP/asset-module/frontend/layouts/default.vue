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
interface NavItem {
  no: string; to: string; icon: string; label: string
  mod?: string; match?: (p: string) => boolean
}
interface NavGroup extends NavItem { children: NavItem[] }

const NAV: NavGroup[] = [
  {
    no: '1', to: '/', icon: '◈', label: '儀表板',
    match: (p) => p === '/',
    children: [
      { no: '1-1', to: '/issues', icon: '⚑', label: '問題清單', mod: 'dashboard' },
      { no: '1-2', to: '/scan-results', icon: '◎', label: '掃描結果', mod: 'dashboard' },
    ],
  },
  {
    no: '2', to: '/assets', icon: '▤', label: '資產查詢', mod: 'assets',
    match: (p) => p.startsWith('/assets') && p !== '/assets/new',
    children: [
      { no: '2-1', to: '/assets/new', icon: '＋', label: '新增資產', mod: 'assets' },
      { no: '2-2', to: '/data-quality', icon: '◍', label: '資料品質', mod: 'data_quality' },
      { no: '2-3', to: '/adopt', icon: '＋', label: '納入管理', mod: 'adopt' },
      // 300 台的時候真正有用的視角：誰還沒進來、卡在哪一關、下一步做什麼
      { no: '2-4', to: '/pipeline', icon: '⧗', label: '納管漏斗', mod: 'pipeline' },
    ],
  },
  {
    no: '3', to: '/golive', icon: '☑', label: '上線前檢查', mod: 'golive',
    match: (p) => p.startsWith('/golive'),
    children: [
      { no: '3-1', to: '/drift', icon: '◬', label: '基線失效', mod: 'golive' },
    ],
  },
  {
    // 「盤點」是分類不是頁面（使用者 2026-08-15 指正：盤點作業與 EOS 都屬於盤點）。
    // 沒有自己的落地頁時 to 留空，第一層就只當可收合的分類標題，不做假的導向——
    // 點了跳到某個子頁會讓人以為那就是「盤點」的首頁。
    no: '4', to: '', icon: '☷', label: '盤點',
    match: () => false,
    children: [
      { no: '4-1', to: '/accounts', icon: '☖', label: '帳號儀表板', mod: 'accounts' },
      { no: '4-2', to: '/account-matrix', icon: '▦', label: '帳號合規表', mod: 'accounts' },
      { no: '4-3', to: '/services', icon: '⌁', label: '服務盤點', mod: 'services',
        match: (p) => p.startsWith('/services') },
      { no: '4-4', to: '/eos', icon: '◷', label: 'EOS 生命週期', mod: 'eos',
        match: (p) => p.startsWith('/eos') },
      { no: '4-5', to: '/account-ops', icon: '☰', label: '盤點作業', mod: 'accounts' },
    ],
  },
  {
    no: '5', to: '/topology', icon: '◇', label: '系統聯通圖', mod: 'topology',
    match: (p) => p.startsWith('/topology'),
    children: [],
  },
  {
    // MICS 重大異常事件指揮系統切片2（2026-08-18）。開關預設關閉，測完使用者
    // 手動開——見 schema.sql feature_flags 的 blast 那筆註解。
    no: '5b', to: '/blast', icon: '⌖', label: '影響範圍查詢', mod: 'blast',
    match: (p) => p.startsWith('/blast'),
    children: [],
  },
  {
    // 系統組月報（2026-08-21）。使用者每月要把三張表貼進部門報告，原本是人工統計。
    // 開關預設關閉，驗過內容再自己開——見 schema.sql feature_flags 的 report_system。
    no: '5c', to: '/reports/system-group', icon: '▥', label: '系統組報告', mod: 'report_system',
    match: (p) => p.startsWith('/reports'),
    children: [
      // 2026-08-25：部門報告圖表頁第二階段，使用者提供現有簡報頁要求「格式相同、
      // 數據是新的即可」。跟月報三張表同一個 report_system 開關，不用另開一個。
      { no: '5c-1', to: '/reports/physical-distribution', icon: '◔', label: '各環境實體機分布', mod: 'report_system' },
      { no: '5c-2', to: '/reports/system-overview', icon: '▦', label: '主機系統總覽', mod: 'report_system' },
      { no: '5c-3', to: '/reports/business-systems', icon: '☰', label: '業務系統排行', mod: 'report_system' },
      // 2026-08-26：頁B／頁C 的分色靠逐台的 system_category，而有 475 台只能人工判斷
      //（452 台管理員 Excel 沒涵蓋、23 台環境別與分類矛盾）。沒有這頁它們永遠是灰的。
      { no: '5c-4', to: '/reports/classify', icon: '⊞', label: '主機分類作業', mod: 'report_system' },
    ],
  },
  {
    no: '6', to: '/settings', icon: '⚙', label: '系統設定',
    match: (p) => p.startsWith('/settings'),
    children: [
      // 2026-08-26 使用者要求。掛在系統設定底下而不是資產底下：它講的是
      // 「這台系統本身」（誰在用、誰動了什麼），不是資產資料。
      { no: '6-1', to: '/activity', icon: '☷', label: '操作紀錄', mod: 'activity' },
    ],
  },
  {
    // 2026-08-25 使用者拍板：「資料匯入」是自己一類，不該散在別的分類底下——
    // 原本掛在「2 資產查詢」下面的 2-5 資料匯入（CIA/存活清單/RVTools 匯入樞紐）
    // 獨立成第一層。5c-3 業務系統排行頁裡的「業務系統分類對照表」上傳/下載也是
    // 匯入功能，使用者明確說「也算7.資料匯入」——那個元件本身留在 5c-3（報表頁
    // 要能自己完整運作，不能拆走），這裡加一個捷徑連過去，讓人從「資料匯入」
    // 這個入口也找得到它，不用先想到「這其實在報表頁裡」。
    no: '7', to: '/import', icon: '⇅', label: '資料匯入', mod: 'import',
    match: (p) => p.startsWith('/import'),
    children: [
      { no: '7-1', to: '/reports/business-systems', icon: '☰', label: '業務系統分類對照表', mod: 'report_system' },
      // 2026-08-26 使用者拍板：單據檔案室（原 2-2）與網段配置表（原 2-3）搬到這裡。
      // 兩個都是「先把外面的東西餵進來」才有內容的頁——掛在「資產查詢」底下要先
      // 想到它們是資產的附屬品才找得到。
      { no: '7-2', to: '/documents', icon: '▤', label: '單據檔案室', mod: 'documents' },
      { no: '7-3', to: '/segments', icon: '▩', label: '網段配置表', mod: 'segments' },
      // 2026-08-26 使用者：「這類別要放在 7. 的下面」。區塊本身留在 5c-4
      //（那頁要能自己完整運作，不能拆走），這裡放捷徑並帶 #seed 直接捲到定位——
      // 跟 7-1 業務系統分類對照表同一個做法。
      { no: '7-4', to: '/reports/classify#seed', icon: '⊞',
        label: '主機分類：盤點表帶入', mod: 'report_system' },
    ],
  },
]

const { isEnabled } = useFeatureFlags()
function navVisible(item: NavItem) {
  return !item.mod || isEnabled(item.mod)
}
// 整組的子項都被關掉、而且第一層自己也沒開（或本來就只是分類標題）時，整組不顯示——
// 只剩一個空標題掛在那裡沒有任何意義。
const visibleNav = computed(() =>
  NAV.map((g) => ({ ...g, children: g.children.filter(navVisible) }))
     .filter((g) => (g.to ? navVisible(g) : g.children.length > 0)),
)

function isOn(item: NavItem) {
  return item.match ? item.match(route.path) : route.path === item.to
}
// 展開狀態：預設「目前所在的群組展開、其餘收合」，但**使用者按過就以他按的為準**。
// 第一版寫成 `目前群組 || 手動展開`，結果目前所在的那一組永遠關不掉——
// 按了沒反應比沒有這顆按鈕還糟（2026-08-15 使用者回報「我無法收合」）。
// 用 null/true/false 三態：沒按過是 null（跟著路由自動），按過就記住他要的。
const userToggled = ref<Record<string, boolean>>({})
const activeGroup = computed(() =>
  visibleNav.value.find((g) => isOn(g) || g.children.some((c) => isOn(c)))?.no ?? '',
)
function groupOpen(g: NavGroup) {
  const manual = userToggled.value[g.no]
  return manual === undefined ? g.no === activeGroup.value : manual
}
// 收合後仍要看得出「你在這一組裡面」：不然使用者把目前所在的組收起來之後，
// 整個選單沒有任何地方是亮的，會不知道自己在哪一頁。
function hasActiveChild(g: NavGroup) {
  return g.children.some((c) => isOn(c))
}
function toggleGroup(g: NavGroup) {
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
            v-if="g.children.length" class="caret" type="button"
            :aria-label="`${groupOpen(g) ? '收合' : '展開'} ${g.label}`"
            :title="`${groupOpen(g) ? '收合' : '展開'} ${g.label}`"
            @click="toggleGroup(g)"
          >{{ groupOpen(g) ? '▾' : '▸' }}</button>
        </div>
        <template v-if="groupOpen(g)">
          <NuxtLink
            v-for="c in g.children" :key="c.no" :to="c.to"
            class="nav lv2" :class="{ on: isOn(c) }"
          >
            <span class="no">{{ c.no }}</span><span class="i">{{ c.icon }}</span>{{ c.label }}
          </NuxtLink>
        </template>
      </template>
    </nav>

    <div class="col">
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
