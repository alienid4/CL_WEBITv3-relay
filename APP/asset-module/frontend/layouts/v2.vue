<script setup lang="ts">
// 2.x 全站外框（決策 9／11）：最左一欄＝全站選單（10 組＋系統設定，所在頁反白）；右邊內容全寬，
// 最上方固定全站搜尋框（任務走查 1：不在首頁也能直接查 IP）。舊頁仍用舊外框（layouts/default.vue）。
import { V2_NAV, V2_PINNED, v2ActiveTo } from '~/composables/useNavV2'
import { openGroups, toggleGroup, setAllGroups } from '~/utils/navGroups'
const route = useRoute()
const { toasts } = useToast()
const { apiFetch } = useApi()
const active = computed(() => v2ActiveTo(route.path))
// Q91：目前帳號＋權限等級＋登出（登入停用時顯示「未登入（登入已停用）」，不顯示登出）
const { user, level, logout } = useAuth()
const { authDisabled, ensureLoaded: ensureServerInfo } = useServerInfo()
onMounted(ensureServerInfo)
const LEVEL_LABEL: Record<string, string> = { admin: '管理員', user: '一般', viewer: '只能看' }
const levelLabel = computed(() => (level.value ? LEVEL_LABEL[level.value] : ''))
async function handleLogout() {
  try { await logout() } catch (e: any) {
    useToast().showToast(`登出失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
    return
  }
  await navigateTo('/login')
}
const version = ref('')
const startedAt = ref('')
onMounted(async () => {
  try {
    const r = await apiFetch<{ version: string; started_at?: string }>('/api/version')
    version.value = r.version
    startedAt.value = r.started_at ? r.started_at.slice(5, 16) : ''   // 例：10-29 20:12（服務真正啟動＝新程式碼生效的時間）
  } catch { version.value = '' /* 版本讀不到不影響操作 */ }
})
const collapsed = ref(false)
// 夜間速查頁在手機上只留一條窄列（品牌＋速查），完整選單按「選單」才展開，不然要先捲過整串選單才看到輸入框。
const isNight = computed(() => active.value === V2_PINNED.to)
const menuOpen = ref(false)

// ===== 群組展開／收合（像 Excel 的大綱群組，2026-10-27）=====
// 預設全部收起，只有目前頁所在的群組打開；每人展開哪些群組會記住（登入開著存伺服器 /api/nav/v2-groups，
// 登入停用存這個瀏覽器）。目前頁的群組一律打開，手動收起只在這次有效。
const ALL = V2_NAV.map(g => g.no)
const activeGroup = computed(() => V2_NAV.find(g => g.items.some(it => it.to === active.value))?.no ?? null)
const saved = ref<string[]>([])
const closedActive = ref(false)
const storage = ref<'server' | 'local'>('local')
const LS = 'webit-v2-nav-open'
const openSet = computed(() => new Set(openGroups(saved.value, activeGroup.value, ALL, closedActive.value)))
watch(activeGroup, () => { closedActive.value = false })
onMounted(async () => {
  try {
    const r = await apiFetch<{ open: string[]; storage: 'server' | 'local' }>('/api/nav/v2-groups')
    storage.value = r.storage
    if (r.storage === 'server') { saved.value = r.open; return }
  } catch { storage.value = 'local' /* 舊版後端沒有這支：改存瀏覽器 */ }
  try { saved.value = JSON.parse(localStorage.getItem(LS) || '[]') } catch { saved.value = [] /* 瀏覽器不讓讀：當沒存過 */ }
})
let timer: ReturnType<typeof setTimeout> | null = null
function persist() {
  if (storage.value === 'local') {
    try { localStorage.setItem(LS, JSON.stringify(saved.value)) } catch { /* 存不了只是下次不記得 */ }
    return
  }
  if (timer) clearTimeout(timer)
  timer = setTimeout(async () => {
    try { await apiFetch('/api/nav/v2-groups', { method: 'PUT', body: { open: saved.value } }) } catch (e: any) {
      useToast().showToast(`選單展開狀態存不進去：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
    }
  }, 400)
}
function toggle(no: string) {
  const [s2, c2] = toggleGroup(saved.value, no, activeGroup.value, closedActive.value, ALL)
  saved.value = s2; closedActive.value = c2; persist()
}
function setAll(expand: boolean) {
  const [s2, c2] = setAllGroups(expand, ALL)
  saved.value = s2; closedActive.value = c2; persist()
}
const allOpen = computed(() => ALL.every(g => openSet.value.has(g)))
</script>

<template>
  <div class="v2shell" :class="{ collapsed, night: isNight, 'menu-open': menuOpen }">
    <nav class="v2nav" aria-label="全站選單">
      <div class="brand"><NuxtLink to="/v2">資訊戰情室</NuxtLink><button type="button" class="col" :title="collapsed ? '展開選單' : '收合選單'" @click="collapsed = !collapsed">{{ collapsed ? '›' : '‹' }}</button></div>
      <NuxtLink :to="V2_PINNED.to" class="pin" :class="{ on: active === V2_PINNED.to }" :title="`${V2_PINNED.label}：打 IP 或貼告警，直接看是誰、找誰、狀態、影響誰`">
        <span class="pinico">{{ V2_PINNED.no }}</span><span class="lab">{{ V2_PINNED.label }}</span>
      </NuxtLink>
      <button v-if="isNight" type="button" class="menubtn" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen">{{ menuOpen ? '收起選單' : '全部選單' }}</button>
      <div v-if="version" class="verline" title="目前執行的程式版本；更版時間＝服務上次重啟（新程式碼生效）的時間">v{{ version }}<template v-if="startedAt">・更版 {{ startedAt }}</template></div>
      <div class="outline"><button type="button" class="ol" :title="allOpen ? '全部收合' : '全部展開'" @click="setAll(!allOpen)">{{ allOpen ? '全部收合' : '全部展開' }}</button></div>
      <div v-for="g in V2_NAV" :key="g.no" class="grp">
        <button type="button" class="gh" :class="{ cur: activeGroup === g.no }" :aria-expanded="openSet.has(g.no)" :aria-controls="`v2g-${g.no}`"
                :title="`${g.no} ${g.label}（${g.items.length} 項）`" @click="toggle(g.no)">
          <span class="car">{{ openSet.has(g.no) ? '▾' : '▸' }}</span><span class="gno">{{ g.no }}</span><span class="glab">{{ g.label }}</span>
        </button>
        <div v-show="openSet.has(g.no)" :id="`v2g-${g.no}`" class="gitems">
        <NuxtLink v-for="it in g.items" :key="it.to" :to="it.to" class="it" :class="{ on: active === it.to, old: it.old }">
          <span class="ino">{{ it.no }}</span><span class="lab">{{ it.label }}</span><span v-if="it.old" class="oldtag">（舊版）</span>
        </NuxtLink>
        </div>
      </div>
      <div class="foot"><span v-if="version" class="ver">v{{ version }}</span></div>
    </nav>
    <div class="v2main">
      <header class="topbar"><V2Search compact /><span class="who" data-testid="v2-who">
          <template v-if="authDisabled">未登入（登入已停用）</template>
          <template v-else-if="user"><b>{{ user }}</b><span v-if="levelLabel" class="lvl" :class="`lvl-${level}`">{{ levelLabel }}</span></template>
        </span>
        <button v-if="!authDisabled && user" type="button" class="logout2" @click="handleLogout">登出</button>
        <span class="vbadge" title="目前是新版（V2）介面">V2 新版</span></header>
      <main class="content"><slot /></main>
    </div>
    <div class="toast-wrap">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`toast-${t.type}`">{{ t.message }}</div>
    </div>
  </div>
</template>

<style scoped>
.v2shell { display: grid; grid-template-columns: 220px minmax(0, 1fr); min-height: 100vh; background: var(--paper); color: var(--ink);
  font-family: 'Noto Sans TC', 'Microsoft JhengHei', system-ui, sans-serif; }
.v2shell.collapsed { grid-template-columns: 56px minmax(0, 1fr); }
.v2nav { background: var(--card); border-right: 1px solid var(--border); padding: 12px 8px; position: sticky; top: 0; height: 100vh; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.who { margin-left: auto; font-size: 13px; color: var(--ink-soft); display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.who b { color: var(--ink); }
.lvl { border: 1px solid var(--border-strong); border-radius: 999px; padding: 1px 8px; font-size: 12px; }
.lvl-viewer { color: var(--warn-text); }
.logout2 { border: 1px solid var(--border-strong); background: transparent; color: var(--ink-aux); border-radius: 8px; padding: 3px 10px; font: inherit; font-size: 13px; cursor: pointer; }
.logout2:hover { border-color: var(--brand); color: var(--brand-dark); }
.vbadge { margin-left: 0; background: var(--brand); color: var(--type-on); font-weight: 700; font-size: 13px; padding: 3px 12px; border-radius: 999px; white-space: nowrap; }
.brand { display: flex; align-items: center; justify-content: space-between; padding: 4px 8px 8px; border-bottom: 1px solid var(--line); }
.brand a { font-weight: 700; color: var(--brand-dark); text-decoration: none; font-size: 15px; white-space: nowrap; overflow: hidden; }
.col { border: 0; background: none; cursor: pointer; color: var(--ink-aux); font-size: 16px; }
.grp { display: flex; flex-direction: column; }
.outline { display: flex; justify-content: flex-end; padding: 2px 4px; }
.ol { border: 1px solid var(--border); background: var(--card); border-radius: 6px; font-size: 11.5px; padding: 1px 8px; cursor: pointer; color: var(--ink-aux); font-family: inherit; }
.ol:hover { border-color: var(--brand); color: var(--brand-dark); }
.gh { display: flex; align-items: center; gap: 2px; width: 100%; border: 0; background: none; text-align: left; font-family: inherit;
  font-size: 12.5px; color: var(--ink-soft); padding: 5px 8px; border-radius: 6px; cursor: pointer; white-space: nowrap; }
.gh:hover { background: var(--sub); }
.gh:focus-visible { outline: 2px solid var(--brand); }
.gh.cur { color: var(--brand-dark); font-weight: 600; }
.car { width: 14px; color: var(--ink-aux); }
.gcnt { color: var(--muted); font-size: 11.5px; margin-left: 2px; }
.gno { font-family: var(--disp); margin-right: 6px; }
.it { display: flex; align-items: center; gap: 6px; padding: 5px 8px 5px 14px; border-radius: 8px; color: var(--ink); text-decoration: none; font-size: 13.5px; white-space: nowrap; }
.it:hover { background: var(--sub); }
.it.on { background: var(--brand); color: var(--type-on); }
.it.old .lab { color: var(--ink-soft); }
.it.on.old .lab { color: var(--type-on); }
.ino { font-family: var(--disp); font-size: 12px; min-width: 28px; color: inherit; opacity: .8; }
.oldtag { font-size: 11px; color: var(--muted); }
.it.on .oldtag { color: var(--type-on); }
.foot { margin-top: auto; display: flex; justify-content: space-between; align-items: center; padding: 8px; border-top: 1px solid var(--line); font-size: 12px; }
.ver { color: var(--muted); font-family: var(--disp); }
.pin { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 10px; text-decoration: none; font-weight: 700; font-size: 15px;
  color: var(--brand-dark); background: var(--mint); border: 1px solid var(--brand); margin: 2px 4px 4px; white-space: nowrap; }
.pin:hover { background: var(--mint-deep); }
.pin.on { background: var(--brand); color: var(--type-on); }
.pinico { font-size: 18px; }
.menubtn { display: none; }
.collapsed .pin { justify-content: center; padding: 10px 0; margin: 2px 0 4px; }
.collapsed .pin .lab { display: none; }
.collapsed .glab, .collapsed .gcnt, .collapsed .car, .collapsed .outline, .collapsed .lab, .collapsed .oldtag, .collapsed .foot, .collapsed .brand a { display: none; }
.v2main { min-width: 0; display: flex; flex-direction: column; }
.topbar { display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 50; background: var(--paper); border-bottom: 1px solid var(--border); padding: 10px 24px; }
.topbar > :first-child { flex: 1; min-width: 0; }
.content { padding: 20px 24px 40px; min-width: 0; }
@media (max-width: 860px) {
  .v2shell { grid-template-columns: 1fr; grid-template-rows: auto 1fr; } .v2nav { position: static; height: auto; }
  .v2shell.night .menubtn { display: block; border: 1px solid var(--border); background: var(--card); color: var(--ink-aux); border-radius: 8px; padding: 8px 12px; font: inherit; font-size: 14px; cursor: pointer; }
  .v2shell.night:not(.menu-open) .grp, .v2shell.night:not(.menu-open) .outline, .v2shell.night:not(.menu-open) .foot, .v2shell.night:not(.menu-open) .verline { display: none; }
  .v2shell.night .topbar { display: none; }
  .v2shell.night .content { padding-left: 14px; padding-right: 14px; padding-top: 10px; }
  .v2shell.night .v2nav { flex-direction: row; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 14px; }
  .v2shell.night .v2nav .brand, .v2shell.night:not(.menu-open) .brand { display: none; }
  .v2shell.night.menu-open .v2nav .brand { display: flex; flex-basis: 100%; }
  .v2shell.night .pin { flex: 1; margin: 0; }
  .v2shell.night .grp, .v2shell.night .outline, .v2shell.night .verline { flex-basis: 100%; }
}
.toast-wrap { position: fixed; top: 18px; right: 18px; z-index: 1000; display: flex; flex-direction: column; gap: 8px; }
.toast { min-width: 220px; max-width: 360px; padding: 11px 15px; border-radius: 10px; font-size: 13px; font-weight: 600; color: var(--type-on); box-shadow: var(--shadow); }
.toast-success { background: var(--good); } .toast-error { background: var(--bad); } .toast-warn { background: var(--warn-text); } .toast-info { background: var(--ink-soft); }
.verline { font-family: var(--disp); font-size: 13px; font-weight: 600; color: var(--brand-dark); background: var(--mint); border: 1px solid var(--brand); border-radius: 8px; padding: 3px 10px; margin: 2px 8px 4px; white-space: nowrap; }
</style>
