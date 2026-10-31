<script setup lang="ts">
// 1.1 今日概況（2.x 藍圖，2026-10-27）：頁籤「待處理事項｜本週異動」（2026-10-28 找碴 C25：近期查詢移到頂端搜尋框下拉）。上方三個常用作業按鈕（全站唯一允許的卡片）。
// 待處理事項＝/api/v2/home（進行中事件、設備風險、容量預警、每日異動、掃描異常；每一項都跟它連去的頁同一份計算）；
// 全部是 0 才顯示綠底「今日無待處理事項」。本週異動＝/api/v2/home/week。
definePageMeta({ layout: 'v2' })
interface Todo { key: string; label: string; n: number | null; tone: string; link: string; note: string | null }
interface Home { todo: Todo[]; as_of: string | null; date: string | null; physical: number | null; hosts: number | null }
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const home = ref<Home | null>(null)
const err = ref('')
try { home.value = await apiFetch<Home>('/api/v2/home') } catch (e: any) { err.value = `待處理事項載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
const TABS = [{ k: 'todo', label: '待處理事項' }, { k: 'week', label: '本週異動' }]
const tab = computed({ get: () => (route.query.tab === 'week' ? 'week' : 'todo'), set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })
const todoRows = computed(() => (home.value?.todo ?? []).map(t => ({ ...t, n_text: t.n == null ? '無資料' : t.n.toLocaleString(), note: t.note || '', level: t.tone === 'bad' ? '高' : t.tone === 'warn' ? '中' : '一般' })))
// 本週異動＝/api/v2/home/week（最近 7 天每天的每日異動筆數，1.2 同一份計算；沒收集的日子寫無資料）
const week = ref<any>(null)
const weekErr = ref('')
async function loadWeek() { if (week.value) return; try { week.value = await apiFetch('/api/v2/home/week') } catch (e: any) { weekErr.value = `本週異動載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } }
if (tab.value === 'week') await loadWeek()
watch(tab, t => { if (t === 'week') loadWeek() })
const weekRows = computed(() => (week.value?.rows ?? []).map((r: any) => ({ ...r, n_text: r.n == null ? '無資料' : r.n })))
const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString())
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">1.1 今日概況</h1></template>
    <template #conclusion>
      <span v-if="home">待處理 {{ home.todo.length }} 項・資料時間 {{ home.as_of || '—' }}・實體設備 <NuxtLink to="/v2/hosts?tab=physical">{{ fmt(home.physical) }} 台</NuxtLink>・作業系統主機 <NuxtLink to="/v2/reconcile?trial=hosts">{{ fmt(home.hosts) }} 台</NuxtLink>
        <InfoNote>實體設備＝機身或機框（伺服器、儲存、網路設備等；LPAR、一體機元件依機身／框算一台），點數字看 2.1「實體設備」頁籤（同一個數字）。作業系統主機＝作業系統實例（含虛擬機、LPAR），算法在 9.2 數量對帳（點數字看組成）。2.1「全部登記台數」是資產庫裡每一台登記（含帳外待確認、端點等），所以比這兩個數字大，不能相加比較。</InfoNote></span>
    </template>
    <div class="quick">
      <NuxtLink to="/v2/incident" class="qb danger"><b>4.1 事件應變</b><span>停機影響評估、通報聯絡、檢核清單</span></NuxtLink>
      <NuxtLink to="/v2/onboard" class="qb"><b>6.1 納管作業</b><span>新設備納管與收集</span></NuxtLink>
      <NuxtLink to="/v2/datafix" class="qb"><b>9.1 待補資料</b><span>CIA 缺漏與待更正</span></NuxtLink>
    </div>
    <template #tab>
      <template v-if="tab === 'todo'">
        <p v-if="err" class="err">{{ err }}</p>
        <p v-else-if="home && !home.todo.length" class="ok">今日無待處理事項。</p>
        <V2Table v-else :rows="todoRows" prefix="T" empty="今日無待處理事項"
                 :cols="[{ k: 'label', label: '事項', link: (r: any) => r.link }, { k: 'n', label: '數量', mono: true, text: (r: any) => r.n_text, link: (r: any) => r.link }, { k: 'level', label: '優先度' }, { k: 'note', label: '範圍' }]" />
      </template>
      <template v-else>
        <p v-if="weekErr" class="err">{{ weekErr }}</p>
        <p v-if="week?.freshness?.reason" class="stale">{{ week.freshness.reason }}</p>
        <V2Table v-if="week" :rows="weekRows" prefix="W" empty="無資料"
                 :cols="[{ k: 'date', label: '日期', mono: true, link: (r: any) => r.link }, { k: 'n', label: '異動筆數', mono: true, text: (r: any) => r.n_text, link: (r: any) => r.link }, { k: 'status', label: '比對' }, { k: 'detail', label: '分類' }]" />
        <p v-else-if="!weekErr" class="muted">載入中…</p>
        <InfoNote>每天的數字＝1.2 異動與異常「每日異動」同一份比對（點日期看明細）。那天沒有快照寫「無資料（未收集）」；兩份快照之間掃描、收集、匯入都沒有新資料又比出 0 筆，寫「無資料（資料來源未更新）」，不當成沒有異動。</InfoNote>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.quick { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 760px) { .quick { grid-template-columns: 1fr; } }
.qb { text-decoration: none; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; color: var(--ink); }
.qb b { font-size: 16px; }
.qb span { font-size: 13px; color: var(--ink-aux); }
.qb:hover { border-color: var(--brand); }
.qb.danger { border-left: 4px solid var(--v2-danger); }
.ok { margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--st-online-bg); color: var(--st-online); }
.err { color: var(--bad); }
.stale { margin: 0 0 8px; padding: 6px 10px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
</style>
